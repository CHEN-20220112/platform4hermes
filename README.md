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
│   ├── models.py            # 10 张表 + 4 张多对多关联表
│   ├── settings_store.py    # DEFAULTS + get/update settings
│   ├── schemas.py           # Pydantic 请求模型
│   ├── services.py          # ProfileRenderer + HermesExecutor
│   ├── hermes_client.py     # HermesClient.call_api（SSE 流式 + profile 路由）
│   ├── feishu_adapter.py    # lark-oapi WebSocket + 消息路由 + 进度卡片
│   └── routers\
│       ├── auth.py          # 管理员登录
│       ├── experts.py       # 专家 CRUD + 渲染 Profile + 查看文件
│       ├── users.py         # 用户 CRUD + 专家派遣
│       ├── user_auth.py     # 用户登录门户 + 飞书 App 绑定
│       ├── skills.py        # Skill CRUD
│       ├── mcp_servers.py   # MCP Server CRUD
│       ├── plugins.py       # 插件管理（委托 hermes plugins：扫描/安装/启停/移除）
│       ├── platform_tools.py # 平台工具（Hermes toolsets 列表/启停，走 Dashboard API）
│       ├── feishu.py        # 飞书 App 注册表 + 连接启停
│       └── settings.py      # 平台设置 + Hermes 连接测试 + 审计日志
├── frontend\
│   ├── index.html           # 管理员单页前端（登录 + 专家/用户/Skill/MCP/插件/工具/设置/日志）
│   └── user.html            # 用户门户（邮箱登录 + 我的专家 + 绑定飞书 App）
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

- **Hermes 接入**：在「平台设置」配置运行目标（远端 / 本机，见下），并填对应
  的 API URL / API Key，点「测试连接」验证。
- **Profile 下发**：创建/编辑专家时自动渲染到 `data/profiles/{profile_name}/`，
  然后按运行目标发布：远端模式走 Dashboard API，本机模式直接写入本机
  `~/.hermes/profiles/{profile_name}/`（或 `%LOCALAPPDATA%\hermes\profiles`）。
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

## Hermes 运行目标（远端 / 本机切换）

平台把「配置下发」与「推理执行」统一抽象成一个运行目标，可在「平台设置 → Hermes 运行目标」切换：

| 模式 | 下发路径 | 执行路径 | 适用场景 |
| --- | --- | --- | --- |
| `remote`（默认） | Dashboard HTTP API（`hermes_dashboard_url`） | 远端 API Server（`hermes_api_url`） | 远端 Docker 集中部署 |
| `local` | 本机 profiles 目录（`hermes_profiles_dir`）+ `hermes` CLI | 本机 multiplex 网关（`hermes_local_api_url`） | 本机已安装 Hermes |

本机模式（`hermes_mode = local`）的关键行为：

1. 渲染产物直接写入 `~/.hermes/profiles/{profile}/`（`SOUL.md`、`config.yaml`、`skills/`），
   首次会尝试 `hermes profile create {profile}` 初始化完整结构。
2. `config.yaml` 采用**合并写入**，只覆盖平台管理的 `model.default` 与
   `gateway.multiplex_profiles`，保留 Hermes 自身写下的其它键。
3. MCP 采用**声明式对账**直接写 `config.yaml` 的 `mcp_servers`：绑定新增、
   **解绑自动清理**（不依赖 `hermes` CLI，因此 MCP 下发不要求 CLI 可用）。
4. 执行统一走 `/p/{profile}/v1/chat/completions`（multiplex 共享网关路由）。

MCP 解绑清理在两种模式下都生效：本地模式直接对账 `config.yaml`；远端模式通过
`DELETE /api/mcp/servers/{name}?profile={profile}` 删除已解绑的 MCP。所谓「清理」指
**以专家当前绑定的 MCP 集合为准**，profile 里多余（未绑定）的 MCP 会被移除。

Web 后台提供「本地网关控制」按钮，一键启动/停止本机 `hermes gateway run`（OpenAI
兼容 `api_server` 平台随网关一起跑），自动注入 `GATEWAY_MULTIPLEX_PROFILES=true`、
`API_SERVER_ENABLED=true`、`API_SERVER_PORT` / `API_SERVER_HOST` / `API_SERVER_KEY`
并把 `HERMES_HOME` 指向 profiles 根目录的上一级。若未填本地 API Key，平台会自动生成
一个并回写到「本机 Hermes 接入 → 本地 API Key」，保证网关与执行端使用同一把 key。

你也可以不托管，自己手动启动：

```bash
GATEWAY_MULTIPLEX_PROFILES=true API_SERVER_ENABLED=true \
API_SERVER_PORT=8642 API_SERVER_HOST=127.0.0.1 API_SERVER_KEY=<你的key> \
hermes gateway run
```

## 多 profile 复用（multiplex）

平台采用 Hermes 官方的多 profile 复用方案：渲染 profile 时写入
`gateway.multiplex_profiles: true`，由 Hermes 的**单个共享网关**通过
`/p/{profile}/v1/chat/completions` 路由到对应 profile，不为每个专家分配独立端口。

在 Docker 下，如果 Hermes 启动时忽略 config.yaml 里的 `gateway.multiplex_profiles`
（见 [issue #94813](https://github.com/NousResearch/hermes-agent/issues/94813)），
请在 Hermes 容器上设置环境变量强制开启：

```bash
GATEWAY_MULTIPLEX_PROFILES=true
```
