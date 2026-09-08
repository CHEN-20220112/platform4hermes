"""平台键值配置：DEFAULTS + get / update。"""
from sqlalchemy.orm import Session

from . import models

# 平台可配置键及其默认值
DEFAULTS = {
    "hermes_api_url": "http://localhost:8642",
    "hermes_api_key": "",            # Hermes API Server 的 API_SERVER_KEY
    "feishu_app_id": "",             # 模式 A 单机器人全局 App
    "feishu_app_secret": "",
    "feishu_mode": "A",              # A = 单机器人路由；B = 一专家一机器人
    "deepseek_api_key": "",          # 可选：渲染进 profile config.yaml 供 Hermes 使用
    "default_model": "deepseek-chat",
    # Hermes profiles 根目录；留空 = 自动探测（%LOCALAPPDATA%\hermes\profiles 或 ~/.hermes/profiles）
    "hermes_profiles_dir": "",
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
