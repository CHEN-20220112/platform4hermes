"""用户登录门户 + 飞书 App 绑定（用户侧自服务，按「用户 × 专家」各自绑定）。"""
import secrets
import threading

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import UserFeishuBindRequest, UserLoginRequest
from .auth import verify_password

router = APIRouter(prefix="/api/user-auth", tags=["user-auth"])

# 进程内用户 token 存储（重启即失效），与管理员 token 隔离
_USER_TOKENS: dict = {}
_user_lock = threading.Lock()


def _issue_user_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _user_lock:
        _USER_TOKENS[token] = user_id
    return token


def get_current_user(authorization: str = Header(default=""),
                     db: Session = Depends(get_db)) -> models.User:
    """用户鉴权依赖：解析 Bearer token → User 模型。"""
    token = authorization.replace("Bearer", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    with _user_lock:
        user_id = _USER_TOKENS.get(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="登录已失效")
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def _user_me(user: models.User, db: Session) -> dict:
    bindings = models.get_user_expert_bindings(db, user.id)
    experts = []
    for e in user.experts:
        b = bindings.get(e.id, {"feishu_app_id": "", "feishu_app_secret": ""})
        experts.append({
            "id": e.id,
            "name": e.name,
            "profile_name": e.profile_name,
            "model": e.model,
            "feishu_app_id": b["feishu_app_id"],
            "feishu_app_secret": b["feishu_app_secret"],
            "has_feishu_bound": bool(b["feishu_app_id"] and b["feishu_app_secret"]),
        })
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "experts": experts,
    }


@router.post("/login")
def login(payload: UserLoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    return {"token": _issue_user_token(user.id), "user": _user_me(user, db)}


@router.get("/me")
def me(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _user_me(user, db)


@router.put("/experts/{expert_id}/feishu")
def bind_expert_feishu(expert_id: int, payload: UserFeishuBindRequest,
                       user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """为某个已分配的专家绑定/更新该用户自己的飞书 App 凭据（不同专家可绑不同 App）。"""
    if not any(e.id == expert_id for e in user.experts):
        raise HTTPException(status_code=404, detail="该专家未分配给你")

    db.execute(
        models.user_experts.update()
        .where(models.user_experts.c.user_id == user.id,
               models.user_experts.c.expert_id == expert_id)
        .values(
            feishu_app_id=(payload.feishu_app_id or "").strip(),
            feishu_app_secret=(payload.feishu_app_secret or "").strip(),
        )
    )
    db.commit()
    return {"user": _user_me(user, db)}


@router.get("/catalog")
def catalog(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """平台全部专家列表（含是否已添加到当前用户的「我的专家」）。"""
    my_ids = {e.id for e in user.experts}
    experts = db.query(models.Expert).order_by(models.Expert.id).all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "profile_name": e.profile_name,
            "model": e.model,
            "system_prompt": (e.system_prompt or "").strip(),
            "added": e.id in my_ids,
        }
        for e in experts
    ]


@router.post("/catalog/{expert_id}/summon")
def summon_expert(expert_id: int, user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """把平台专家「召唤」到当前用户的「我的专家」（新增关联，不影响已绑定的飞书）。"""
    expert = db.get(models.Expert, expert_id)
    if expert is None:
        raise HTTPException(status_code=404, detail="专家不存在")
    if any(e.id == expert_id for e in user.experts):
        return {"user": _user_me(user, db), "already": True}

    db.execute(
        models.user_experts.insert().values(
            user_id=user.id,
            expert_id=expert_id,
            feishu_app_id="",
            feishu_app_secret="",
        )
    )
    db.commit()
    return {"user": _user_me(user, db), "already": False}
