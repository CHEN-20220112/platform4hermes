"""管理员登录 + 认证依赖。"""
import hashlib
import secrets
import threading

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 进程内 token 存储（单管理员平台，重启即失效）
_TOKENS: dict = {}
_lock = threading.Lock()

_SALT = "hermes-platform-v1"


def hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), _SALT.encode("utf-8"), 100_000
    ).hex()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash


def _issue_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _TOKENS[token] = username
    return token


def get_current_admin(authorization: str = Header(default="")) -> str:
    token = authorization.replace("Bearer", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    with _lock:
        username = _TOKENS.get(token)
    if not username:
        raise HTTPException(status_code=401, detail="登录已失效")
    return username


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    admin = (
        db.query(models.Admin).filter(models.Admin.username == payload.username).first()
    )
    if admin is None or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": _issue_token(admin.username), "username": admin.username}


@router.get("/me")
def me(username: str = Depends(get_current_admin)):
    return {"username": username}
