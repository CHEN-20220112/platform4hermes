"""FastAPI 入口 + startup（init_db + seed + 飞书自动启动）。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import BASE_DIR, Base, SessionLocal, engine
from . import models
from .feishu_adapter import FeishuAdapter
from .routers import auth, experts, feishu, mcp_servers, platform_tools, plugins, settings, skills, user_auth, users
from .routers.auth import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")


def _migrate_plugin_schema():
    """插件表 schema 变更迁移：旧表含 config 列（name/version/description/config），
    且早期无数据，直接删除重建（含关联表）。"""
    from sqlalchemy import inspect

    insp = inspect(engine)
    if "plugins" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("plugins")}
    if "enabled" not in cols and "config" in cols:
        Base.metadata.tables["expert_plugins"].drop(bind=engine, checkfirst=True)
        Base.metadata.tables["plugins"].drop(bind=engine, checkfirst=True)
        logger.info("已重建插件表（schema 升级）")


def _migrate_user_expert_feishu():
    """user_experts 关联表增加「用户 × 专家」各自的飞书 App 凭据列（增量 ADD COLUMN）。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "user_experts" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("user_experts")}
    with engine.begin() as conn:
        if "feishu_app_id" not in cols:
            conn.execute(text("ALTER TABLE user_experts ADD COLUMN feishu_app_id VARCHAR(128) DEFAULT ''"))
        if "feishu_app_secret" not in cols:
            conn.execute(text("ALTER TABLE user_experts ADD COLUMN feishu_app_secret VARCHAR(256) DEFAULT ''"))


def init_db():
    """建表 + 种子管理员账号。"""
    _migrate_plugin_schema()
    Base.metadata.create_all(bind=engine)
    _migrate_user_expert_feishu()
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
    plugins.router,
    platform_tools.router,
    feishu.router,
    users.router,
    user_auth.router,
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
