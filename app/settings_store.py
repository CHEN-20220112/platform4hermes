"""平台键值配置：DEFAULTS + get / update。"""
from sqlalchemy.orm import Session

from . import models

# 平台可配置键及其默认值
DEFAULTS = {
    "hermes_api_url": "http://172.16.98.47:18003",
    "hermes_api_key": "",            # Hermes API Server 的 API_SERVER_KEY
    # 下发走 Dashboard HTTP API（架构 B）
    "hermes_dashboard_url": "http://172.16.98.47:18004",
    "hermes_dashboard_username": "admin",
    "hermes_dashboard_password": "",
    "hermes_model_provider": "opencode-free",
    "feishu_app_id": "",             # 模式 A 单机器人全局 App
    "feishu_app_secret": "",
    "feishu_mode": "A",              # A = 单机器人路由；B = 一专家一机器人
    "deepseek_api_key": "",          # 可选：渲染进 profile config.yaml 供 Hermes 使用
    "default_model": "deepseek-v4-flash-free",
    # Hermes profiles 根目录；留空 = 自动探测（%LOCALAPPDATA%\hermes\profiles 或 ~/.hermes/profiles）
    "hermes_profiles_dir": "",
    # Hermes 运行目标：
    #   remote = 远端 Docker（Dashboard API 下发 profile + 远端 API Server 执行，现状默认）
    #   local  = 本机 hermes CLI（直接写本地 profiles 目录 + 本地 multiplex 网关执行）
    "hermes_mode": "remote",
    "hermes_bin": "hermes",                       # 本机 hermes CLI（可为绝对路径）
    "hermes_local_api_url": "http://127.0.0.1:8642",  # 本机网关地址
    "hermes_local_api_key": "",                   # 本机网关 API_SERVER_KEY（可选）
}

SETTING_KEYS = set(DEFAULTS.keys())


def get_settings(db: Session) -> dict:
    """返回合并了默认值的完整配置。"""
    data = dict(DEFAULTS)
    rows = db.query(models.Setting).all()
    for row in rows:
        if row.key in SETTING_KEYS:
            data[row.key] = row.value
    return data


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    if row is not None:
        return row.value
    return DEFAULTS.get(key, default)


def update_settings(db: Session, updates: dict) -> dict:
    """只接受已知键，其余忽略。"""
    for key, value in updates.items():
        if key not in SETTING_KEYS:
            continue
        row = db.query(models.Setting).filter(models.Setting.key == key).first()
        if row is not None:
            row.value = "" if value is None else str(value)
        else:
            db.add(models.Setting(key=key, value="" if value is None else str(value)))
    db.commit()
    return get_settings(db)
