"""MCP Server CRUD。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import MCPServerCreate, MCPServerUpdate
from ..services import render_affected_experts
from .auth import get_current_admin

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"],
                   dependencies=[Depends(get_current_admin)])


def _to_dict(m: models.MCPServer) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "transport": m.transport,
        "config_template": m.config_template or "{}",
        "tools_filter": m.tools_filter or "[]",
        "created_at": m.created_at.isoformat() if m.created_at else "",
        "updated_at": m.updated_at.isoformat() if m.updated_at else "",
    }


@router.get("")
def list_mcp(db: Session = Depends(get_db)):
    return [_to_dict(m) for m in db.query(models.MCPServer).order_by(models.MCPServer.id).all()]


@router.get("/{mcp_id}")
def get_mcp(mcp_id: int, db: Session = Depends(get_db)):
    m = db.get(models.MCPServer, mcp_id)
    if m is None:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    return _to_dict(m)


@router.post("")
def create_mcp(payload: MCPServerCreate, db: Session = Depends(get_db)):
    m = models.MCPServer(**payload.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _to_dict(m)


@router.put("/{mcp_id}")
def update_mcp(mcp_id: int, payload: MCPServerUpdate, db: Session = Depends(get_db)):
    m = db.get(models.MCPServer, mcp_id)
    if m is None:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    affected = render_affected_experts(mcp_id=mcp_id)
    return {"mcp": _to_dict(m), "re_rendered_experts": affected}


@router.delete("/{mcp_id}")
def delete_mcp(mcp_id: int, db: Session = Depends(get_db)):
    m = db.get(models.MCPServer, mcp_id)
    if m is None:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    db.delete(m)
    db.commit()
    return {"ok": True}
