"""插件管理：委托 hermes plugins CLI（scan / install / enable / disable / remove）。"""
import json
import logging
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..plugin_descriptions import PLUGIN_DESCRIPTION_ZH
from ..schemas import PluginInstallRequest, PluginToggleRequest
from ..settings_store import get_settings
from .auth import get_current_admin

logger = logging.getLogger("plugins")

router = APIRouter(prefix="/api/plugins", tags=["plugins"],
                   dependencies=[Depends(get_current_admin)])

_PLUGINS_DIR_HINT = "~/.hermes/plugins"


def _resolve_bin(db: Session) -> str:
    settings = get_settings(db)
    return (settings.get("hermes_bin") or "hermes").strip() or "hermes"


def _run(bin_: str, args: list, timeout: int = 600) -> tuple:
    cmd = [bin_, "plugins", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"未找到 hermes 可执行文件：{bin_}"
    except subprocess.TimeoutExpired:
        return 124, "", "命令执行超时"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _list_from_cli(db: Session) -> list:
    """运行 ``hermes plugins list --json`` 并解析，返回 [{name, status, version, description, source}]。"""
    code, out, err = _run(_resolve_bin(db), ["list", "--json"])
    if code != 0:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _to_dict(p: models.Plugin) -> dict:
    # 内置插件优先显示中文描述；未命中的（用户自装 git 插件）回退显示 manifest 原文。
    desc = PLUGIN_DESCRIPTION_ZH.get(p.name) or (p.description or "")
    return {
        "id": p.id,
        "name": p.name,
        "version": p.version,
        "description": desc,
        "enabled": bool(p.enabled),
        "source": p.source or "",
        "created_at": p.created_at.isoformat() if p.created_at else "",
        "updated_at": p.updated_at.isoformat() if p.updated_at else "",
    }


def _sync(db: Session) -> list:
    """扫描磁盘（hermes plugins list --json）并 upsert 到 DB，返回 DB 插件列表。

    Hermes 会按能力分组重复列出同名插件（例如 deepinfra 同时出现在图像/视频后端），
    故按 name 去重，一个插件目录只对应一行。
    """
    items = _list_from_cli(db)
    seen = set()
    for it in items:
        name = (it.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        p = db.query(models.Plugin).filter(models.Plugin.name == name).first()
        if p is None:
            p = models.Plugin(name=name)
            db.add(p)
        p.version = it.get("version") or p.version or "1.0.0"
        p.description = it.get("description") or ""
        p.enabled = (it.get("status") or "").strip().lower() in ("enabled", "active", "on")
        p.source = (it.get("source") or p.source or "").strip()
    db.commit()
    return [_to_dict(p) for p in db.query(models.Plugin).order_by(models.Plugin.name).all()]


@router.get("")
def list_plugins(db: Session = Depends(get_db)):
    return [_to_dict(p) for p in db.query(models.Plugin).order_by(models.Plugin.name).all()]


@router.get("/{plugin_id}")
def get_plugin(plugin_id: int, db: Session = Depends(get_db)):
    p = db.get(models.Plugin, plugin_id)
    if p is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    return _to_dict(p)


@router.post("/scan")
def scan(db: Session = Depends(get_db)):
    """扫描磁盘，使列表载入新 manifest（与 Hermes「扫描」一致）。"""
    plugins = _sync(db)
    return {"plugins": plugins, "count": len(plugins)}


@router.post("/install")
def install_plugin(payload: PluginInstallRequest, db: Session = Depends(get_db)):
    identifier = (payload.source or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="请填写 Git 地址或 owner/repo")

    args = ["install", identifier]
    if payload.force:
        args.append("--force")
    args.append("--enable" if payload.enable else "--no-enable")

    code, out, err = _run(_resolve_bin(db), args)
    if code != 0:
        detail = (err or out or "安装失败").strip()
        raise HTTPException(status_code=400, detail=detail[:800])

    # 安装成功后重新扫描，把新插件载入 DB
    plugins = _sync(db)
    return {"ok": True, "stdout": out.strip()[:2000], "plugins": plugins}


@router.put("/{plugin_id}")
def toggle_plugin(plugin_id: int, payload: PluginToggleRequest, db: Session = Depends(get_db)):
    p = db.get(models.Plugin, plugin_id)
    if p is None:
        raise HTTPException(status_code=404, detail="插件不存在")

    args = ["enable" if payload.enabled else "disable"]
    if payload.enabled:
        args.append("--no-allow-tool-override")  # 跳过授权覆盖内置工具的交互确认
    args.append(p.name)

    code, out, err = _run(_resolve_bin(db), args)
    if code != 0:
        detail = (err or out or "操作失败").strip()
        raise HTTPException(status_code=400, detail=detail[:800])

    p.enabled = payload.enabled
    db.commit()
    db.refresh(p)
    return {"plugin": _to_dict(p)}


@router.delete("/{plugin_id}")
def remove_plugin(plugin_id: int, db: Session = Depends(get_db)):
    p = db.get(models.Plugin, plugin_id)
    if p is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    if (p.source or "").strip().lower() == "bundled":
        raise HTTPException(
            status_code=400,
            detail=f"仅可移除用户安装在 {_PLUGINS_DIR_HINT} 下的插件（内置插件不可移除）",
        )

    code, out, err = _run(_resolve_bin(db), ["remove", p.name])
    if code != 0:
        detail = (err or out or "移除失败").strip()
        raise HTTPException(status_code=400, detail=detail[:800])

    db.delete(p)
    db.commit()
    return {"ok": True}
