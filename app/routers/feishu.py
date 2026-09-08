"""飞书 App 注册表 CRUD + 连接启停控制。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import FeishuAppCreate, FeishuAppUpdate
from ..feishu_adapter import FeishuAdapter
from .auth import get_current_admin

router = APIRouter(prefix="/api/feishu", tags=["feishu"],
                   dependencies=[Depends(get_current_admin)])


def _to_dict(a: models.FeishuApp) -> dict:
    return {
        "id": a.id,
        "app_id": a.app_id,
        "app_name": a.app_name or "",
        "mode": a.mode,
        "enabled": a.enabled,
        "created_at": a.created_at.isoformat() if a.created_at else "",
        "updated_at": a.updated_at.isoformat() if a.updated_at else "",
    }


# ---------------------------------------------------------------------------
# FeishuApp 注册表
# ---------------------------------------------------------------------------
@router.get("/apps")
def list_apps(db: Session = Depends(get_db)):
    return [_to_dict(a) for a in db.query(models.FeishuApp).order_by(models.FeishuApp.id).all()]


@router.post("/apps")
def create_app(payload: FeishuAppCreate, db: Session = Depends(get_db)):
    a = models.FeishuApp(**payload.model_dump())
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_dict(a)


@router.put("/apps/{app_id}")
def update_app(app_id: int, payload: FeishuAppUpdate, db: Session = Depends(get_db)):
    a = db.get(models.FeishuApp, app_id)
    if a is None:
        raise HTTPException(status_code=404, detail="飞书 App 不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return _to_dict(a)


@router.delete("/apps/{app_id}")
def delete_app(app_id: int, db: Session = Depends(get_db)):
    a = db.get(models.FeishuApp, app_id)
    if a is None:
        raise HTTPException(status_code=404, detail="飞书 App 不存在")
    db.delete(a)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 连接控制
# ---------------------------------------------------------------------------
@router.get("/status")
def status():
    return FeishuAdapter.instance().status()


@router.post("/start")
def start():
    try:
        return FeishuAdapter.instance().start()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"启动失败: {exc}")


@router.post("/stop")
def stop():
    return FeishuAdapter.instance().stop()


@router.post("/restart")
def restart():
    try:
        return FeishuAdapter.instance().restart()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"重启失败: {exc}")
