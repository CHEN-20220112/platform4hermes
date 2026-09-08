"""Skill CRUD。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import SkillCreate, SkillUpdate
from ..services import render_affected_experts
from .auth import get_current_admin

router = APIRouter(prefix="/api/skills", tags=["skills"],
                   dependencies=[Depends(get_current_admin)])


def _to_dict(s: models.Skill) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "version": s.version,
        "category": s.category or "",
        "description": s.description or "",
        "tags": s.tags or "",
        "content": s.content or "",
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
    }


@router.get("")
def list_skills(db: Session = Depends(get_db)):
    return [_to_dict(s) for s in db.query(models.Skill).order_by(models.Skill.id).all()]


@router.get("/{skill_id}")
def get_skill(skill_id: int, db: Session = Depends(get_db)):
    s = db.get(models.Skill, skill_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return _to_dict(s)


@router.post("")
def create_skill(payload: SkillCreate, db: Session = Depends(get_db)):
    s = models.Skill(**payload.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_dict(s)


@router.put("/{skill_id}")
def update_skill(skill_id: int, payload: SkillUpdate, db: Session = Depends(get_db)):
    s = db.get(models.Skill, skill_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    affected = render_affected_experts(skill_id=skill_id)
    return {"skill": _to_dict(s), "re_rendered_experts": affected}


@router.delete("/{skill_id}")
def delete_skill(skill_id: int, db: Session = Depends(get_db)):
    s = db.get(models.Skill, skill_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}
