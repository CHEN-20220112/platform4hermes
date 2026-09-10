"""Pydantic 请求/响应模型（API 用）。"""
from typing import List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------
class SkillCreate(BaseModel):
    name: str
    version: str = "1.0.0"
    category: str = ""
    description: str = ""
    tags: str = ""
    content: str = ""


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    content: Optional[str] = None


# ---------------------------------------------------------------------------
# MCPServer
# ---------------------------------------------------------------------------
class MCPServerCreate(BaseModel):
    name: str
    transport: str = "http"  # http | stdio
    config_template: str = "{}"  # JSON
    tools_filter: str = "[]"  # JSON array


class MCPServerUpdate(BaseModel):
    name: Optional[str] = None
    transport: Optional[str] = None
    config_template: Optional[str] = None
    tools_filter: Optional[str] = None


# ---------------------------------------------------------------------------
# Expert
# ---------------------------------------------------------------------------
class ExpertCreate(BaseModel):
    name: str
    system_prompt: str = ""
    profile_name: str
    model: str = "deepseek-v4-flash-free"
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    skill_ids: List[int] = []
    mcp_server_ids: List[int] = []


class ExpertUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    profile_name: Optional[str] = None
    model: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    skill_ids: Optional[List[int]] = None
    mcp_server_ids: Optional[List[int]] = None


# ---------------------------------------------------------------------------
# FeishuApp
# ---------------------------------------------------------------------------
class FeishuAppCreate(BaseModel):
    app_id: str
    app_name: str = ""
    mode: str = "A"
    enabled: bool = False


class FeishuAppUpdate(BaseModel):
    app_id: Optional[str] = None
    app_name: Optional[str] = None
    mode: Optional[str] = None
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class SettingsUpdate(BaseModel):
    hermes_api_url: Optional[str] = None
    hermes_api_key: Optional[str] = None
    hermes_dashboard_url: Optional[str] = None
    hermes_dashboard_username: Optional[str] = None
    hermes_dashboard_password: Optional[str] = None
    hermes_model_provider: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_mode: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    default_model: Optional[str] = None
    hermes_profiles_dir: Optional[str] = None
    hermes_mode: Optional[str] = None
    hermes_bin: Optional[str] = None
    hermes_local_api_url: Optional[str] = None
    hermes_local_api_key: Optional[str] = None
