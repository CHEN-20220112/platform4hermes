"""FastAPI 入口 + startup（init_db + seed + 飞书自动启动）。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import BASE_DIR, Base, SessionLocal, engine
from . import models
from .feishu_adapter import FeishuAdapter
from .routers import auth, connectors, experts, feishu, mcp_servers, platform_tools, plugins, settings, skills, user_auth, users
from .routers.auth import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# 应用市场「连接器」初始数据
SEED_CONNECTORS = [
    {"name": "通达信", "icon": "📈", "category": "金融行情",
     "description": "股票行情与交易软件，行情数据接入。",
     "config": '{}'},
    {"name": "腾讯自选股", "icon": "📊", "category": "金融行情",
     "description": "腾讯自选股行情与自选股数据。",
     "config": '{"token": ""}'},
    {"name": "腾讯文档", "icon": "📄", "category": "办公协同",
     "description": "腾讯在线文档的读写与协作。",
     "config": '{"app_id": "", "app_secret": ""}'},
    {"name": "腾讯会议", "icon": "🎥", "category": "办公协同",
     "description": "腾讯会议的视频会议与日程。",
     "config": '{"app_id": "", "secret_id": "", "secret_key": ""}'},
    {"name": "钉钉", "icon": "📌", "category": "即时通讯",
     "description": "连接钉钉，收发消息、考勤、审批等。",
     "config": '{"app_key": "", "app_secret": ""}'},
    {"name": "微云", "icon": "☁️", "category": "云存储",
     "description": "腾讯微云云盘文件存储与管理。",
     "config": '{"access_token": ""}'},
    {"name": "金山文档", "icon": "📝", "category": "办公协同",
     "description": "金山/WPS 在线文档读写。",
     "config": '{"app_id": "", "app_secret": ""}'},
    {"name": "企查查", "icon": "🏢", "category": "企业信息",
     "description": "企业工商信息、股权、风险查询。",
     "config": '{"api_key": ""}'},
    {"name": "天眼查", "icon": "👁️", "category": "企业信息",
     "description": "企业信息、股东、司法风险查询。",
     "config": '{"token": ""}'},
    {"name": "百度网盘", "icon": "💾", "category": "云存储",
     "description": "百度网盘文件上传下载与管理。",
     "config": '{"access_token": "", "app_id": "", "secret_key": ""}'},
    {"name": "新华财经咨询MCP", "icon": "📰", "category": "金融资讯",
     "description": "新华财经资讯与咨询数据（MCP 服务）。",
     "config": '{"mcp_url": "", "api_key": ""}'},
]


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


def _migrate_mcp_category():
    """mcp_servers 表增加 category 列（增量 ADD COLUMN）。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "mcp_servers" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("mcp_servers")}
    with engine.begin() as conn:
        if "category" not in cols:
            conn.execute(text("ALTER TABLE mcp_servers ADD COLUMN category VARCHAR(64) DEFAULT ''"))


def _seed_connectors():
    """初始化应用市场连接器（表为空时写入常用连接器）。"""
    db = SessionLocal()
    try:
        if db.query(models.Connector).first() is None:
            for item in SEED_CONNECTORS:
                db.add(models.Connector(**item))
            db.commit()
            logger.info("已初始化 %d 个应用市场连接器", len(SEED_CONNECTORS))
    finally:
        db.close()


def init_db():
    """建表 + 种子管理员账号 + 种子连接器。"""
    _migrate_plugin_schema()
    Base.metadata.create_all(bind=engine)
    _migrate_user_expert_feishu()
    _migrate_mcp_category()
    db = SessionLocal()
    try:
        admin = db.query(models.Admin).filter(models.Admin.username == "admin").first()
        if admin is None:
            db.add(models.Admin(username="admin", password_hash=hash_password("admin123")))
            db.commit()
            logger.info("已创建默认管理员 admin / admin123")
    finally:
        db.close()
    _seed_connectors()


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
    connectors.router,
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
