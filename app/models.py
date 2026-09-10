"""8 张表 + 2 张多对多关联表。"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow() -> datetime:
    """返回 naive UTC 时间（SQLite 存储友好）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 多对多关联表
# ---------------------------------------------------------------------------
expert_skills = Table(
    "expert_skills",
    Base.metadata,
    Column("expert_id", Integer, ForeignKey("experts.id"), primary_key=True),
    Column("skill_id", Integer, ForeignKey("skills.id"), primary_key=True),
)

expert_mcp_servers = Table(
    "expert_mcp_servers",
    Base.metadata,
    Column("expert_id", Integer, ForeignKey("experts.id"), primary_key=True),
    Column("mcp_server_id", Integer, ForeignKey("mcp_servers.id"), primary_key=True),
)


# ---------------------------------------------------------------------------
# 1. Admin 管理员账号
# ---------------------------------------------------------------------------
class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    created_at = Column(DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# 2. Expert 专家
# ---------------------------------------------------------------------------
class Expert(Base):
    __tablename__ = "experts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    system_prompt = Column(Text, default="")
    profile_name = Column(String(128), unique=True, nullable=False, index=True)
    model = Column(String(128), default="upstage/solar-pro4:free")
    # 模式 B（一专家一机器人）时使用的独立飞书 App 凭据
    feishu_app_id = Column(String(128), default="")
    feishu_app_secret = Column(String(256), default="")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    skills = relationship(
        "Skill", secondary=expert_skills, back_populates="experts", lazy="selectin"
    )
    mcp_servers = relationship(
        "MCPServer", secondary=expert_mcp_servers, back_populates="experts", lazy="selectin"
    )


# ---------------------------------------------------------------------------
# 2b. Conversation 会话（飞书用户 × 专家 的多会话 / 重置）
# ---------------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    open_id = Column(String(128), nullable=False, index=True)
    expert_id = Column(Integer, ForeignKey("experts.id"), nullable=True, index=True)
    # 传给 Hermes 的 X-Hermes-Session-Id；「新对话 / 重置」换新值 = 清空上下文
    session_key = Column(String(64), unique=True, nullable=False, index=True)
    title = Column(String(256), default="")  # 首条消息摘要，会话列表展示用
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# 3. Skill 技能
# ---------------------------------------------------------------------------
class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    version = Column(String(32), default="1.0.0")
    category = Column(String(64), default="")
    description = Column(Text, default="")
    tags = Column(String(256), default="")  # 逗号分隔
    content = Column(Text, default="")  # SKILL.md 正文
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    experts = relationship("Expert", secondary=expert_skills, back_populates="skills")


# ---------------------------------------------------------------------------
# 4. MCPServer MCP Server
# ---------------------------------------------------------------------------
class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    transport = Column(String(16), default="http")  # http | stdio
    config_template = Column(Text, default="{}")  # JSON
    tools_filter = Column(Text, default="[]")  # JSON array 或逗号分隔
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    experts = relationship("Expert", secondary=expert_mcp_servers, back_populates="mcp_servers")


# ---------------------------------------------------------------------------
# 5. FeishuApp 飞书 App（注册表）
# ---------------------------------------------------------------------------
class FeishuApp(Base):
    __tablename__ = "feishu_apps"

    id = Column(Integer, primary_key=True, index=True)
    app_id = Column(String(128), nullable=False)
    app_name = Column(String(128), default="")
    mode = Column(String(8), default="A")  # A | B
    enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# 6. Setting 平台键值配置
# ---------------------------------------------------------------------------
class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(128), unique=True, nullable=False, index=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# 7. CallLog 审计日志
# ---------------------------------------------------------------------------
class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    expert_id = Column(Integer, ForeignKey("experts.id"), nullable=True)
    user_open_id = Column(String(128), default="", index=True)
    channel = Column(String(32), default="feishu")
    message = Column(Text, default="")
    response = Column(Text, default="")
    tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    status = Column(String(32), default="success")  # success | error
    created_at = Column(DateTime, default=utcnow, index=True)
