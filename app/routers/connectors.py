"""连接器（应用市场）CRUD + 启停。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import ConnectorCreate, ConnectorUpdate
from .auth import get_current_admin

router = APIRouter(prefix="/api/connectors", tags=["connectors"],
                   dependencies=[Depends(get_current_admin)])


def _to_dict(c: models.Connector) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "icon": c.icon or "",
        "category": c.category or "",
        "description": c.description or "",
        "enabled": bool(c.enabled),
        "config": c.config or "{}",
        "created_at": c.created_at.isoformat() if c.created_at else "",
        "updated_at": c.updated_at.isoformat() if c.updated_at else "",
    }


@router.get("")
def list_connectors(db: Session = Depends(get_db)):
    return [_to_dict(c) for c in db.query(models.Connector).order_by(models.Connector.id).all()]


@router.post("")
def create_connector(payload: ConnectorCreate, db: Session = Depends(get_db)):
    exists = db.query(models.Connector).filter(models.Connector.name == payload.name).first()
    if exists:
        raise HTTPException(status_code=400, detail="连接器名称已存在")
    c = models.Connector(**payload.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return _to_dict(c)


@router.put("/{connector_id}")
def update_connector(connector_id: int, payload: ConnectorUpdate, db: Session = Depends(get_db)):
    c = db.get(models.Connector, connector_id)
    if c is None:
        raise HTTPException(status_code=404, detail="连接器不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != c.name:
        dup = db.query(models.Connector).filter(
            models.Connector.name == data["name"], models.Connector.id != connector_id
        ).first()
        if dup:
            raise HTTPException(status_code=400, detail="连接器名称已存在")
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return _to_dict(c)


@router.delete("/{connector_id}")
def delete_connector(connector_id: int, db: Session = Depends(get_db)):
    c = db.get(models.Connector, connector_id)
    if c is None:
        raise HTTPException(status_code=404, detail="连接器不存在")
    db.delete(c)
    db.commit()
    return {"ok": True}
