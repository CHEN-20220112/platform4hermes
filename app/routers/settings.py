"""平台设置（Hermes 接入配置）+ 连接测试 + 审计日志查询。"""
import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from .. import models
from ..settings_store import get_settings, update_settings
from ..schemas import SettingsUpdate
from .auth import get_current_admin

router = APIRouter(prefix="/api/settings", tags=["settings"],
                   dependencies=[Depends(get_current_admin)])

logs_router = APIRouter(prefix="/api/call-logs", tags=["call-logs"],
                        dependencies=[Depends(get_current_admin)])


class TestHermesRequest(BaseModel):
    hermes_api_url: Optional[str] = None
    hermes_api_key: Optional[str] = None


@router.get("")
def read_settings(db: Session = Depends(get_db)):
    return get_settings(db)


@router.put("")
def write_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    updates = payload.model_dump(exclude_unset=True)
    return update_settings(db, updates)


@router.post("/test-hermes")
def test_hermes(payload: TestHermesRequest, db: Session = Depends(get_db)):
    settings = get_settings(db)
    base_url = (payload.hermes_api_url or settings.get("hermes_api_url") or "").strip().rstrip("/")
    api_key = payload.hermes_api_key or settings.get("hermes_api_key") or ""

    if not base_url:
        return {"ok": False, "detail": "未配置 Hermes API URL"}
    if not api_key:
        return {"ok": False, "detail": "未配置 Hermes API Key"}

    headers = {"Authorization": f"Bearer {api_key}"}
    candidates = [
        f"{base_url}/v1/models",
        f"{base_url}/health",
        base_url,
    ]
    last_err = ""
    for url in candidates:
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                return {"ok": True, "status_code": resp.status_code, "url": url,
                        "body": (resp.text or "")[:500]}
            last_err = f"{url} -> HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_err = f"{url} -> {exc}"
    return {"ok": False, "detail": last_err or "连接失败"}


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------
@logs_router.get("")
def list_logs(
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
    expert_id: Optional[int] = None,
    status: Optional[str] = None,
):
    q = db.query(models.CallLog)
    if expert_id is not None:
        q = q.filter(models.CallLog.expert_id == expert_id)
    if status:
        q = q.filter(models.CallLog.status == status)
    total = q.count()
    rows = (
        q.order_by(models.CallLog.id.desc())
        .offset(offset)
        .limit(min(limit, 500))
        .all()
    )
    expert_names = {
        e.id: e.name for e in db.query(models.Expert).all()
    }
    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "expert_id": r.expert_id,
            "expert_name": expert_names.get(r.expert_id, ""),
            "user_open_id": r.user_open_id,
            "channel": r.channel,
            "message": r.message,
            "response": r.response,
            "tokens": r.tokens,
            "latency_ms": r.latency_ms,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })
    return {"total": total, "items": items}
