"""专家 CRUD + 渲染 Profile + 查看下发文件。"""
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import PROFILES_DIR, get_db
from .. import models
from ..schemas import ExpertCreate, ExpertUpdate
from ..services import ProfileRenderer, render_expert, get_mcp_register_status
from .auth import get_current_admin

router = APIRouter(prefix="/api/experts", tags=["experts"],
                   dependencies=[Depends(get_current_admin)])


def _restart_feishu_async():
    """专家增删改后，后台重启飞书连接（模式 B 下会按新专家列表重建 bot）。"""
    from ..feishu_adapter import FeishuAdapter

    def _do():
        try:
            FeishuAdapter.instance().restart()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_do, daemon=True, name="feishu-restart").start()


def _expert_to_dict(expert: models.Expert) -> dict:
    return {
        "id": expert.id,
        "name": expert.name,
        "system_prompt": expert.system_prompt or "",
        "profile_name": expert.profile_name,
        "model": expert.model,
        "feishu_app_id": expert.feishu_app_id or "",
        "feishu_app_secret": expert.feishu_app_secret or "",
        "skill_ids": [s.id for s in expert.skills],
        "mcp_server_ids": [m.id for m in expert.mcp_servers],
        "skills": [
            {"id": s.id, "name": s.name, "version": s.version, "category": s.category}
            for s in expert.skills
        ],
        "mcp_servers": [
            {"id": m.id, "name": m.name, "transport": m.transport}
            for m in expert.mcp_servers
        ],
        "created_at": expert.created_at.isoformat() if expert.created_at else "",
        "updated_at": expert.updated_at.isoformat() if expert.updated_at else "",
    }


def _apply_relations(db: Session, expert: models.Expert,
                     skill_ids, mcp_server_ids) -> None:
    if skill_ids is not None:
        expert.skills = (
            db.query(models.Skill).filter(models.Skill.id.in_(skill_ids)).all()
            if skill_ids else []
        )
    if mcp_server_ids is not None:
        expert.mcp_servers = (
            db.query(models.MCPServer).filter(models.MCPServer.id.in_(mcp_server_ids)).all()
            if mcp_server_ids else []
        )


@router.get("")
def list_experts(db: Session = Depends(get_db)):
    experts = db.query(models.Expert).order_by(models.Expert.id).all()
    return [_expert_to_dict(e) for e in experts]


@router.get("/{expert_id}")
def get_expert(expert_id: int, db: Session = Depends(get_db)):
    expert = db.get(models.Expert, expert_id)
    if expert is None:
        raise HTTPException(status_code=404, detail="专家不存在")
    return _expert_to_dict(expert)


@router.post("")
def create_expert(payload: ExpertCreate, db: Session = Depends(get_db)):
    exists = db.query(models.Expert).filter(
        models.Expert.profile_name == payload.profile_name
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="profile_name 已存在")

    expert = models.Expert(
        name=payload.name,
        system_prompt=payload.system_prompt,
        profile_name=payload.profile_name,
        model=payload.model,
        feishu_app_id=payload.feishu_app_id,
        feishu_app_secret=payload.feishu_app_secret,
    )
    db.add(expert)
    db.flush()
    _apply_relations(db, expert, payload.skill_ids, payload.mcp_server_ids)
    db.commit()
    db.refresh(expert)

    render_result = render_expert(expert.id, db)
    _restart_feishu_async()
    return {"expert": _expert_to_dict(expert), "render": render_result}


@router.put("/{expert_id}")
def update_expert(expert_id: int, payload: ExpertUpdate, db: Session = Depends(get_db)):
    expert = db.get(models.Expert, expert_id)
    if expert is None:
        raise HTTPException(status_code=404, detail="专家不存在")

    data = payload.model_dump(exclude_unset=True)
    if "profile_name" in data and data["profile_name"] != expert.profile_name:
        dup = db.query(models.Expert).filter(
            models.Expert.profile_name == data["profile_name"],
            models.Expert.id != expert_id,
        ).first()
        if dup:
            raise HTTPException(status_code=400, detail="profile_name 已存在")

    skill_ids = data.pop("skill_ids", None)
    mcp_ids = data.pop("mcp_server_ids", None)
    for k, v in data.items():
        setattr(expert, k, v)
    _apply_relations(db, expert, skill_ids, mcp_ids)
    db.commit()
    db.refresh(expert)

    render_result = render_expert(expert.id, db)
    _restart_feishu_async()
    return {"expert": _expert_to_dict(expert), "render": render_result}


@router.delete("/{expert_id}")
def delete_expert(expert_id: int, db: Session = Depends(get_db)):
    expert = db.get(models.Expert, expert_id)
    if expert is None:
        raise HTTPException(status_code=404, detail="专家不存在")
    db.delete(expert)
    db.commit()
    _restart_feishu_async()
    return {"ok": True}


@router.post("/{expert_id}/render")
def render(expert_id: int, db: Session = Depends(get_db)):
    """手动渲染并下发 profile。"""
    result = render_expert(expert_id, db)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{expert_id}/mcp-sync-status")
def mcp_sync_status(expert_id: int, db: Session = Depends(get_db)):
    """查询该专家的 MCP 注册状态（供前端「配置中」等待界面轮询）。"""
    expert = db.get(models.Expert, expert_id)
    if expert is None:
        raise HTTPException(status_code=404, detail="专家不存在")
    return get_mcp_register_status(expert.profile_name.lower())


@router.get("/{expert_id}/files")
def view_files(expert_id: int, db: Session = Depends(get_db)):
    """查看本地下发的 profile 文件内容。"""
    expert = db.get(models.Expert, expert_id)
    if expert is None:
        raise HTTPException(status_code=404, detail="专家不存在")
    base = PROFILES_DIR / expert.profile_name
    if not base.exists():
        return {"profile_name": expert.profile_name, "files": []}
    files = []
    for path in sorted(base.rglob("*")):
        if path.is_file():
            rel = path.relative_to(base).as_posix()
            files.append({"path": rel, "content": path.read_text(encoding="utf-8", errors="replace")})
    return {"profile_name": expert.profile_name, "local_dir": str(base), "files": files}
