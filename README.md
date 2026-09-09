# Hermes 专家配置与飞书服务平台

介于飞书用户和 Hermes Agent 之间的**配置层 + 路由层**。平台不内置任何 Agent 引擎，
所有推理 / 上下文 / 记忆 / Skill / MCP 调用均由本地 Hermes Agent 完成。

平台只做三件事：

1. **管理**：专家 / Skill / MCP Server 的配置 CRUD
2. **下发**：把配置渲染成 Hermes Profile（`SOUL.md` + `config.yaml` + `skills/`）
3. **路由**：飞书消息 → 选定专家 → 调 Hermes API Server → 回传结果

## 目录结构

```
d:\platform4hermes\
├── app\
│   ├── main.py              # FastAPI 入口 + startup（init_db + seed + 飞书自动启动）
│   ├── database.py          # SQLAlchemy engine + SessionLocal + Base
│   ├── models.py            # 8 张表 + 2 张多对多关联表
│   ├── settings_store.py    # DEFAULTS + get/update settings
│   ├── schemas.py           # Pydantic 请求模型
│   ├── services.py          # ProfileRenderer + HermesExecutor
│   ├── hermes_client.py     # HermesClient.call_api（SSE 流式 + profile 路由）
│   ├── feishu_adapter.py    # lark-oapi WebSocket + 消息路由 + 进度卡片
│   └── routers\
│       ├── auth.py          # 管理员登录
│       ├── experts.py       # 专家 CRUD + 渲染 Profile + 查看文件
│       ├── skills.py        # Skill CRUD
│       ├── mcp_servers.py   # MCP Server CRUD
│       ├── feishu.py        # 飞书 App 注册表 + 连接启停
│       └── settings.py      # 平台设置 + Hermes 连接测试 + 审计日志
├── frontend\
│   └── index.html           # 单页前端（登录 + 专家/Skill/MCP/设置/日志）
├── data\                    # 运行时自动创建（hermes.db + profiles/）
└── requirements.txt
```

## 快速开始

```bash
# 1. 安装依赖（Python 3.12+）
pip install -r requirements.txt

# 2. 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. 打开前端
# http://localhost:8000/
```

**默认管理员**：`admin` / `admin123`（首次启动自动创建，建议登录后修改）。

## 关键约定

- **Hermes 接入**：在「平台设置」配置 `hermes_api_url`（如 `http://localhost:8642`）
  与 `hermes_api_key`（Hermes API Server 的 API_SERVER_KEY），点「测试连接」验证。
- **Profile 下发**：创建/编辑专家时自动渲染到 `data/profiles/{profile_name}/`，
  并同步到 `~/.hermes/profiles/{profile_name}/`（目录不存在时先执行
  `hermes profile create {name}`；Hermes 未安装则静默跳过）。
- **会话隔离**：每个「用户 × 专家」可开多个会话，落在 `conversations` 表；每次
  「新对话 / 重置」都生成新的 `session_key`（即 Hermes 的 `X-Hermes-Session-Id`），
  换 session 即清空上下文，不同会话历史互相独立。
- **飞书会话命令**（模式 A / B 均支持）：`新对话`（开新会话）、`重置`（清空当前
  会话上下文）、`会话`（列出/切换/删除会话）；`专家`/`菜单` 打开菜单卡片（含快捷按钮）。
- **飞书模式**：
  - 模式 A（单机器人路由）：平台设置里配置全局 App ID/Secret；用户发「专家」选专家。
  - 模式 B（一专家一机器人）：每个专家的 `feishu_app_id`/`feishu_app_secret` 独立配置。

## 责任边界

平台只做配置/下发/路由，以下由 Hermes 负责：LLM 推理、ReAct 循环、MCP Client、
跨任务上下文（state.db）、跨会话记忆（memory/）、Skill 加载执行、自进化。
