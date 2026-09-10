"""用户管理：用户 CRUD + 专家派遣。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import UserCreate, UserExpertAssign, UserUpdate
from .auth import get_current_admin, hash_password

router = APIRouter(prefix="/api/users", tags=["users"],
                   dependencies=[Depends(get_current_admin)])


def _user_to_dict(user: models.User, db: Session) -> dict:
    bindings = models.get_user_expert_bindings(db, user.id)
    experts = []
    for e in user.experts:
        b = bindings.get(e.id, {"feishu_app_id": "", "feishu_app_secret": ""})
        experts.append({
            "id": e.id,
            "name": e.name,
            "profile_name": e.profile_name,
            "feishu_app_id": b["feishu_app_id"],
            "feishu_app_secret": b["feishu_app_secret"],
            "has_feishu_bound": bool(b["feishu_app_id"] and b["feishu_app_secret"]),
        })
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "expert_ids": [e["id"] for e in experts],
        "experts": experts,
        "feishu_bound_count": sum(1 for e in experts if e["has_feishu_bound"]),
        "created_at": user.created_at.isoformat() if user.created_at else "",
        "updated_at": user.updated_at.isoformat() if user.updated_at else "",
    }


@router.get("")
def list_users(db: Session = Depends(get_db)):
    users = db.query(models.User).order_by(models.User.id).all()
    return [_user_to_dict(u, db) for u in users]


@router.get("/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _user_to_dict(user, db)


@router.post("")
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    exists = db.query(models.User).filter(models.User.email == payload.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="邮箱已存在")

    user = models.User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"user": _user_to_dict(user, db)}


@router.put("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    data = payload.model_dump(exclude_unset=True)
    if "email" in data and data["email"] != user.email:
        dup = db.query(models.User).filter(
            models.User.email == data["email"], models.User.id != user_id
        ).first()
        if dup:
            raise HTTPException(status_code=400, detail="邮箱已存在")

    password = data.pop("password", None)
    if password:
        user.password_hash = hash_password(password)
    for k, v in data.items():
        setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return {"user": _user_to_dict(user, db)}


@router.put("/{user_id}/experts")
def assign_experts(user_id: int, payload: UserExpertAssign, db: Session = Depends(get_db)):
    """派遣专家：把已创建的专家分配给该用户。"""
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.experts = (
        db.query(models.Expert).filter(models.Expert.id.in_(payload.expert_ids)).all()
        if payload.expert_ids else []
    )
    db.commit()
    db.refresh(user)
    return {"user": _user_to_dict(user, db)}


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(user)
    db.commit()
    return {"ok": True}
