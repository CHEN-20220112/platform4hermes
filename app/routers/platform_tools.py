"""平台工具（Hermes toolsets）管理：委托 Hermes Dashboard API。

Hermes 的「平台工具」即 toolsets（工具集），按平台（CLI / Feishu / Telegram 等）
启用或禁用。这里通过 Dashboard 的 /api/tools/toolsets 读取与切换，与平台现有
profile/skill/MCP 的远端下发走同一套 Dashboard 登录。
"""
import logging

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ToolsetToggleRequest
from ..settings_store import get_settings
from .auth import get_current_admin

logger = logging.getLogger("platform_tools")

router = APIRouter(prefix="/api/platform-tools", tags=["platform-tools"],
                   dependencies=[Depends(get_current_admin)])


def _dashboard_session(db: Session) -> tuple:
    """登录 Hermes Dashboard，返回 (session, dash_url)。"""
    settings = get_settings(db)
    dash_url = (settings.get("hermes_dashboard_url") or "").strip().rstrip("/")
    user = (settings.get("hermes_dashboard_username") or "admin").strip()
    pwd = settings.get("hermes_dashboard_password") or ""

    if not dash_url:
        raise HTTPException(status_code=400, detail="未配置 Dashboard URL（平台设置 → Profile 下发）")
    if not pwd:
        raise HTTPException(status_code=400, detail="未配置 Dashboard 密码")

    session = requests.Session()
    try:
        resp = session.post(
            f"{dash_url}/auth/password-login",
            json={"provider": "basic", "username": user, "password": pwd, "next": ""},
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Dashboard 登录异常: {exc}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Dashboard 登录失败: HTTP {resp.status_code}")
    return session, dash_url


@router.get("")
def list_toolsets(db: Session = Depends(get_db)):
    session, dash_url = _dashboard_session(db)
    try:
        resp = session.get(f"{dash_url}/api/tools/toolsets", timeout=30)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取工具列表异常: {exc}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"获取工具列表失败: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Dashboard 返回非 JSON 数据")
    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="Dashboard 工具列表格式异常")
    return data


@router.put("/{name}")
def toggle_toolset(name: str, payload: ToolsetToggleRequest, db: Session = Depends(get_db)):
    session, dash_url = _dashboard_session(db)
    try:
        resp = session.put(
            f"{dash_url}/api/tools/toolsets/{name}",
            json={"enabled": payload.enabled},
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"切换工具状态异常: {exc}")
    if resp.status_code >= 400:
        detail = f"切换失败: HTTP {resp.status_code}"
        try:
            d = resp.json()
            if isinstance(d, dict) and d.get("detail"):
                detail = str(d["detail"])
        except ValueError:
            pass
        raise HTTPException(status_code=400, detail=detail[:800])
    try:
        return resp.json()
    except ValueError:
        return {"ok": True, "name": name, "enabled": payload.enabled}
