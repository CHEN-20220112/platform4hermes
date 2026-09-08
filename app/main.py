"""FastAPI 入口 + startup（init_db + seed + 飞书自动启动）。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import BASE_DIR, Base, SessionLocal, engine
from . import models
from .feishu_adapter import FeishuAdapter
from .routers import auth, experts, feishu, mcp_servers, settings, skills
from .routers.auth import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")


def init_db():
    """建表 + 种子管理员账号。"""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        admin = db.query(models.Admin).filter(models.Admin.username == "admin").first()
        if admin is None:
            db.add(models.Admin(username="admin", password_hash=hash_password("admin123")))
            db.commit()
            logger.info("已创建默认管理员 admin / admin123")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        FeishuAdapter.instance().auto_start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("飞书自动启动失败: %s", exc)
    yield
    try:
        FeishuAdapter.instance().stop()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="Hermes 专家配置与飞书服务平台", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 路由
for r in (
    auth.router,
    experts.router,
    skills.router,
    mcp_servers.router,
    feishu.router,
    settings.router,
    settings.logs_router,
):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 单页前端（静态资源挂在最后，避免拦截 /api）
frontend_dir = BASE_DIR / "frontend"
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
