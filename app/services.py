"""核心业务：ProfileRenderer（下发渲染）+ HermesExecutor（转发执行）。"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import requests
import yaml
from sqlalchemy.orm import Session

logger = logging.getLogger("services")

from . import models
from .database import PROFILES_DIR, SessionLocal
from .hermes_client import HermesClient
from .settings_store import get_settings


def _resolve_hermes_profiles_dir(settings: dict) -> Path:
    """解析 Hermes profiles 根目录（可配置，默认自动探测）。"""
    configured = (settings.get("hermes_profiles_dir") or "").strip()
    if configured:
        return Path(configured).expanduser()

    candidates: list = []
    if os.name == "nt":
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            candidates.append(Path(localappdata) / "hermes" / "profiles")
    candidates.append(Path.home() / ".hermes" / "profiles")

    for c in candidates:
        if c.exists():
            return c
    return candidates[0] if candidates else Path.home() / ".hermes" / "profiles"


def _slugify(name: str) -> str:
    """把名称转成安全的目录名（保留中文）。"""
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name.strip(), flags=re.UNICODE).strip("-")
    return s or "skill"


def _parse_json_list(raw: str) -> list:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return val
        return []
    except json.JSONDecodeError:
        # 兜底：逗号分隔
        return [x.strip() for x in raw.split(",") if x.strip()]


def _extract_mcp_inner(cfg: dict, name: str) -> dict:
    """从 config_template 里提取真正的 MCP server 配置。

    兼容两种常见写法：
    1. 标准 MCP 客户端格式：{"mcpServers": {"<inner>": {"command"/"url", ...}}}
    2. 扁平格式：{"command"/"url", ...}
    """
    servers_map = cfg.get("mcpServers")
    if isinstance(servers_map, dict) and servers_map:
        if isinstance(servers_map.get(name), dict):
            return servers_map[name]
        first = next(iter(servers_map.values()))
        if isinstance(first, dict):
            return first
    return cfg


# ---------------------------------------------------------------------------
# 进程内 MCP 注册状态（前端「配置中」等待界面轮询用）
# {profile_lower: {"status": "idle"|"registering"|"done"|"error", "results": [...], "total": N}}
# ---------------------------------------------------------------------------
_MCP_REGISTER_STATUS: dict = {}
_MCP_REGISTER_LOCK = threading.Lock()


def get_mcp_register_status(profile_lower: str) -> dict:
    """返回某 profile 的 MCP 注册状态快照（供前端轮询）。"""
    with _MCP_REGISTER_LOCK:
        snap = _MCP_REGISTER_STATUS.get(profile_lower)
        if snap is None:
            return {"status": "idle", "results": [], "total": 0}
        return {
            "status": snap.get("status", "idle"),
            "results": list(snap.get("results", [])),
            "total": snap.get("total", 0),
        }


def resolve_hermes_target(settings: dict) -> tuple:
    """按 hermes_mode 解析执行目标，返回 (base_url, api_key, mode)。

    local 模式走本机 multiplex 网关；remote 模式走远端 API Server。
    """
    mode = (settings.get("hermes_mode") or "remote").strip().lower()
    if mode == "local":
        return (
            (settings.get("hermes_local_api_url") or "http://127.0.0.1:8642").strip(),
            (settings.get("hermes_local_api_key") or "").strip(),
            "local",
        )
    return (
        (settings.get("hermes_api_url") or "").strip(),
        (settings.get("hermes_api_key") or "").strip(),
        "remote",
    )


class ProfileRenderer:
    """把专家配置渲染成 Hermes Profile 目录结构并同步到 Hermes 端。"""

    def __init__(self, db: Optional[Session] = None):
        self._owns_db = db is None
        self.db = db or SessionLocal()

    def close(self):
        if self._owns_db:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    def render(self, expert: models.Expert) -> dict:
        profile_name = expert.profile_name
        local_dir = PROFILES_DIR / profile_name
        local_dir.mkdir(parents=True, exist_ok=True)

        skills = list(expert.skills or [])
        mcp_servers = list(expert.mcp_servers or [])

        # 1. SOUL.md
        (local_dir / "SOUL.md").write_text(
            self._render_soul(expert, skills), encoding="utf-8"
        )

        # 2. config.yaml
        config = self._render_config(expert, mcp_servers)
        (local_dir / "config.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        # 3. skills/
        skills_dir = local_dir / "skills"
        if skills_dir.exists():
            shutil.rmtree(skills_dir)
        skills_dir.mkdir(exist_ok=True)
        for skill in skills:
            skill_dir = skills_dir / _slugify(skill.name)
            skill_dir.mkdir(exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                self._render_skill_md(skill), encoding="utf-8"
            )

        # 4. 同步到 Hermes 端（走 Dashboard HTTP API 下发 profile/skill/MCP）
        sync = self._sync_to_hermes(profile_name, local_dir, skills, mcp_servers)

        return {
            "profile_name": profile_name,
            "local_dir": str(local_dir),
            "files": ["SOUL.md", "config.yaml"]
            + [f"skills/{_slugify(s.name)}/SKILL.md" for s in skills],
            "sync": sync,
        }

    # ------------------------------------------------------------------
    def _render_soul(self, expert: models.Expert, skills: list) -> str:
        lines = [f"# {expert.name}", ""]
        if expert.system_prompt:
            lines += [expert.system_prompt.strip(), ""]
        if skills:
            lines += ["## 绑定的技能 (Skills)", ""]
            for s in skills:
                lines += [
                    f"### Skill: {s.name} (v{s.version or '1.0.0'})",
                    "",
                    s.content.strip() if s.content else "_（无内容）_",
                    "",
                    "---",
                    "",
                ]
        return "\n".join(lines).strip() + "\n"

    def _render_skill_md(self, skill: models.Skill) -> str:
        frontmatter = {
            "name": self._skill_slug(skill),
            "version": skill.version or "1.0.0",
            "category": skill.category or "",
            "description": skill.description or "",
            "tags": [t.strip() for t in (skill.tags or "").split(",") if t.strip()],
        }
        fm = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n\n{skill.content or ''}".strip() + "\n"

    def _render_config(self, expert: models.Expert, mcp_servers: list) -> dict:
        """只渲染 model + gateway 复用开关；MCP 交给 ``hermes mcp add``（职责分离）。

        model 用嵌套结构 model.default，provider/base_url/plugins 等保留 Hermes 自己的值。
        gateway.multiplex_profiles=true 让 Hermes 用一个共享网关复用多个 profile
        （走 /p/{profile}/... 路由），而不是为每个 profile 起独立网关/端口。
        """
        settings = get_settings(self.db)
        model_default = expert.model or settings.get("default_model", "deepseek-chat")
        return {
            "model": {"default": model_default},
            "gateway": {"multiplex_profiles": True},
        }

    # ------------------------------------------------------------------
    def _sync_to_hermes(self, profile_name: str, local_dir: Path, skills: list, mcp_servers: list) -> dict:
        """按 hermes_mode 选择下发目标：local 写本机 profiles 目录，remote 走 Dashboard API。"""
        settings = get_settings(self.db)
        mode = (settings.get("hermes_mode") or "remote").strip().lower()
        if mode == "local":
            return self._sync_to_local(profile_name, local_dir, skills, mcp_servers)
        return self._sync_to_remote(profile_name, local_dir, skills, mcp_servers)

    # ------------------------------------------------------------------
    def _sync_to_local(self, profile_name: str, local_dir: Path, skills: list, mcp_servers: list) -> dict:
        """本地模式：把渲染产物直接写入本机 Hermes profiles 目录（数据面/控制面都在本机）。

        与远端 Dashboard 架构对称：
        - SOUL.md / config.yaml / skills/ 直接落到 ``hermes_profiles_dir/{profile}/``；
        - MCP 直接对账写 config.yaml 的 ``mcp_servers``（绑定新增、解绑清理），
          不依赖 hermes CLI（CLI 仅用于首次 ``profile create``）。
        """
        settings = get_settings(self.db)
        profiles_root = _resolve_hermes_profiles_dir(settings)
        profile_lower = profile_name.lower()
        target_dir = profiles_root / profile_lower
        hermes_bin = self._resolve_hermes_bin(settings.get("hermes_bin") or "hermes")

        # 1. 首次创建 profile（优先用 hermes CLI 初始化完整结构，失败则退化 mkdir）
        if not (target_dir / "SOUL.md").exists() and not (target_dir / "config.yaml").exists():
            self._ensure_local_profile(hermes_bin, profile_lower, target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # 2. SOUL.md
        soul_src = local_dir / "SOUL.md"
        if soul_src.exists():
            shutil.copy2(soul_src, target_dir / "SOUL.md")

        # 3. config.yaml（合并写入，保留 Hermes 自身其它键）
        self._merge_platform_config(local_dir / "config.yaml", target_dir / "config.yaml")

        # 4. 可选：DeepSeek Key 透传写进 profile 的 .env
        ds_key = (settings.get("deepseek_api_key") or "").strip()
        if ds_key:
            self._set_env_key(target_dir, "DEEPSEEK_API_KEY", ds_key)

        # 5. skills/（同名整体覆盖）
        skills_src = local_dir / "skills"
        if skills_src.exists():
            skills_dst = target_dir / "skills"
            if skills_dst.exists():
                shutil.rmtree(skills_dst)
            shutil.copytree(skills_src, skills_dst)

        # 6. MCP 对账：绑定新增、解绑清理（直接写 config.yaml 的 mcp_servers）
        mcp_results = self._reconcile_local_mcp(target_dir, mcp_servers)

        return {
            "status": "synced",
            "mode": "local",
            "target": str(target_dir),
            "hermes_bin": hermes_bin,
            "cli_available": self._hermes_cli_available(hermes_bin),
            "skills": [{"name": s.name, "status": "ok"} for s in skills],
            "mcp": mcp_results,
        }

    # ------------------------------------------------------------------
    def _sync_to_remote(self, profile_name: str, local_dir: Path, skills: list, mcp_servers: list) -> dict:
        """远端模式：通过 Dashboard HTTP API 下发 profile（SOUL / 模型 / 技能 / MCP）。

        架构 B：平台跑本机，远端 Hermes 不共享文件系统，改用 Dashboard 的 REST API：
        - POST /auth/password-login             登录拿会话 cookie
        - POST /api/profiles                    建 profile
        - PUT  /api/profiles/{name}/soul        写 SOUL.md
        - PUT  /api/profiles/{name}/model       写 provider + model
        - POST /api/skills?profile={name}       写技能
        - POST /api/mcp/servers?profile={name}  注册 MCP
        """
        settings = get_settings(self.db)
        dash_url = (settings.get("hermes_dashboard_url") or "").strip().rstrip("/")
        dash_user = (settings.get("hermes_dashboard_username") or "admin").strip()
        dash_pass = settings.get("hermes_dashboard_password") or ""
        provider = (settings.get("hermes_model_provider") or "opencode-free").strip()

        if not dash_url:
            return {"status": "skipped", "reason": "未配置 Dashboard URL"}
        if not dash_pass:
            return {"status": "skipped", "reason": "未配置 Dashboard 密码"}

        # 1. 登录拿会话 cookie（requests.Session 自动保存 Set-Cookie）
        session = requests.Session()
        try:
            resp = session.post(
                f"{dash_url}/auth/password-login",
                json={"provider": "basic", "username": dash_user, "password": dash_pass, "next": ""},
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": f"Dashboard 登录异常: {exc}"}
        if resp.status_code >= 400:
            return {"status": "error", "reason": f"Dashboard 登录失败: HTTP {resp.status_code} {resp.text[:200]}"}

        profile_lower = profile_name.lower()
        pq = quote(profile_lower)

        # 2. 确保 profile 存在
        existing = self._dash_list_names(session, f"{dash_url}/api/profiles")
        if profile_lower not in existing and profile_name not in existing:
            try:
                r = session.post(
                    f"{dash_url}/api/profiles",
                    json={"name": profile_lower, "clone_from": None, "description": ""},
                    timeout=30,
                )
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "reason": f"创建 profile 异常: {exc}"}
            if r.status_code >= 400 and not self._looks_like_exists(r):
                return {"status": "error", "reason": f"创建 profile 失败: HTTP {r.status_code} {r.text[:200]}"}

        # 3. 下发 SOUL
        soul_text = ""
        soul_file = local_dir / "SOUL.md"
        if soul_file.exists():
            soul_text = soul_file.read_text(encoding="utf-8")
        try:
            r = session.put(f"{dash_url}/api/profiles/{pq}/soul", json={"content": soul_text}, timeout=30)
            if r.status_code >= 400:
                return {"status": "error", "reason": f"下发 SOUL 失败: HTTP {r.status_code} {r.text[:200]}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": f"下发 SOUL 异常: {exc}"}

        # 4. 下发模型（provider + model）
        model = self._model_from_config(local_dir) or (settings.get("default_model") or "").strip()
        try:
            r = session.put(
                f"{dash_url}/api/profiles/{pq}/model",
                json={"provider": provider, "model": model},
                timeout=30,
            )
            if r.status_code >= 400:
                return {"status": "error", "reason": f"下发模型失败: HTTP {r.status_code} {r.text[:200]}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": f"下发模型异常: {exc}"}

        # 5. 下发技能（同名已存在则跳过）
        skill_results = []
        existing_skills = self._dash_list_names(session, f"{dash_url}/api/tools/toolsets?profile={pq}")
        for skill in skills:
            slug = self._skill_slug(skill)
            if slug in existing_skills:
                skill_results.append({"name": skill.name, "status": "exists"})
                continue
            content = self._render_skill_md(skill)
            try:
                r = session.post(
                    f"{dash_url}/api/skills?profile={pq}",
                    json={"name": slug, "content": content,
                          "category": skill.category or "", "profile": profile_lower},
                    timeout=30,
                )
                skill_results.append({
                    "name": skill.name,
                    "status": "ok" if r.status_code < 400 else "error",
                    "detail": "" if r.status_code < 400 else f"HTTP {r.status_code} {r.text[:120]}",
                })
            except Exception as exc:  # noqa: BLE001
                skill_results.append({"name": skill.name, "status": "error", "detail": str(exc)})

        # 6. 注册 MCP（同名已存在则跳过）
        mcp_results = []
        existing_mcp = self._dash_list_names(session, f"{dash_url}/api/mcp/servers?profile={pq}")
        for mcp in mcp_servers:
            if mcp.name in existing_mcp:
                mcp_results.append({"name": mcp.name, "status": "exists"})
                continue
            url = self._mcp_url(mcp)
            if not url:
                mcp_results.append({"name": mcp.name, "status": "error", "reason": "http MCP 缺少 url"})
                continue
            try:
                r = session.post(
                    f"{dash_url}/api/mcp/servers?profile={pq}",
                    json={"name": mcp.name, "url": url},
                    timeout=30,
                )
                mcp_results.append({
                    "name": mcp.name,
                    "status": "ok" if r.status_code < 400 else "error",
                    "detail": "" if r.status_code < 400 else f"HTTP {r.status_code} {r.text[:120]}",
                })
            except Exception as exc:  # noqa: BLE001
                mcp_results.append({"name": mcp.name, "status": "error", "detail": str(exc)})

        # 7. 解绑清理：删除 Dashboard 上已注册、但已不在绑定集合中的 MCP
        bound_names = {mcp.name for mcp in mcp_servers}
        for name in sorted(existing_mcp - bound_names):
            try:
                r = session.delete(
                    f"{dash_url}/api/mcp/servers/{quote(name)}?profile={pq}",
                    timeout=30,
                )
                mcp_results.append({
                    "name": name,
                    "status": "removed" if r.status_code < 400 else "error",
                    "detail": "" if r.status_code < 400 else f"HTTP {r.status_code} {r.text[:120]}",
                })
            except Exception as exc:  # noqa: BLE001
                mcp_results.append({"name": name, "status": "error", "detail": str(exc)})

        return {
            "status": "synced",
            "mode": "remote",
            "target": f"{dash_url}/api/profiles/{profile_lower}",
            "skills": skill_results,
            "mcp": mcp_results,
        }

    @staticmethod
    def _looks_like_exists(resp) -> bool:
        text = (resp.text or "").lower()
        return resp.status_code == 409 or any(
            k in text for k in ("already exist", "already_exists", "duplicate", "conflict")
        )

    @staticmethod
    def _dash_list_names(session, url: str) -> set:
        """GET 列表接口，防御性地把响应里的所有 name 收进集合。"""
        names: set = set()
        try:
            r = session.get(url, timeout=30)
            if r.status_code >= 400:
                return names
            data = r.json()
        except Exception:  # noqa: BLE001
            return names

        def _collect(obj):
            if isinstance(obj, list):
                for it in obj:
                    if isinstance(it, dict) and isinstance(it.get("name"), str):
                        names.add(it["name"])
                    elif isinstance(it, str):
                        names.add(it)
            elif isinstance(obj, dict):
                for key in ("profiles", "items", "skills", "toolsets", "servers", "results", "data"):
                    _collect(obj.get(key))

        _collect(data)
        return names

    @staticmethod
    def _model_from_config(local_dir: Path) -> str:
        cfg_file = local_dir / "config.yaml"
        try:
            cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            return ""
        m = cfg.get("model")
        if isinstance(m, dict):
            return str(m.get("default") or "")
        if isinstance(m, str):
            return m
        return ""

    @staticmethod
    def _skill_slug(skill) -> str:
        """生成 Dashboard 可接受的技能标识（仅小写字母/数字/连字符/点/下划线）。"""
        name = (skill.name or "").strip()
        if name and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name.lower()):
            return name.lower()
        return f"skill-{skill.id}"

    @staticmethod
    def _mcp_url(mcp) -> str:
        try:
            cfg = json.loads(mcp.config_template or "{}")
        except Exception:  # noqa: BLE001
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        inner = _extract_mcp_inner(cfg, mcp.name)
        return str(inner.get("url") or "").strip() if isinstance(inner, dict) else ""

    @staticmethod
    def _read_target_config(target_dir: Path) -> dict:
        """读取本地 profile 的 config.yaml（不存在/损坏返回空 dict）。"""
        try:
            cfg_path = target_dir / "config.yaml"
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _write_target_config(target_dir: Path, cfg: dict) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "config.yaml").write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    @staticmethod
    def _mcp_config_entry(mcp) -> Optional[dict]:
        """把平台 MCP 配置转成 Hermes config.yaml 的 ``mcp_servers.<name>`` 条目。

        http/sse → ``{url, headers?, auth?}``；stdio → ``{command, args?, env?}``。
        与 Hermes Dashboard 的 ``_normalize_mcp_server_create`` 输出形态一致。
        """
        try:
            cfg = json.loads(mcp.config_template or "{}")
        except Exception:  # noqa: BLE001
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        inner = _extract_mcp_inner(cfg, mcp.name)
        if not isinstance(inner, dict):
            return None

        if mcp.transport == "stdio":
            command = str(inner.get("command", "")).strip()
            if not command:
                return None
            entry: dict = {"command": command}
            args = inner.get("args") or []
            if args:
                entry["args"] = [str(a) for a in args]
            env = inner.get("env")
            if isinstance(env, dict) and env:
                entry["env"] = {str(k): str(v) for k, v in env.items()}
            return entry

        # http / sse
        url = str(inner.get("url", "")).strip()
        if not url:
            return None
        entry = {"url": url}
        headers = inner.get("headers")
        if isinstance(headers, dict) and headers:
            entry["headers"] = {str(k): str(v) for k, v in headers.items()}
        if inner.get("auth"):
            entry["auth"] = str(inner["auth"])
        return entry

    def _reconcile_local_mcp(self, target_dir: Path, mcp_servers: list) -> list:
        """对账本地 profile 的 mcp_servers：绑定新增、解绑清理（声明式）。"""
        cfg = self._read_target_config(target_dir)
        servers = cfg.get("mcp_servers")
        servers = servers if isinstance(servers, dict) else {}
        bound_names = {mcp.name for mcp in mcp_servers}
        results = []

        # 绑定：新增缺失的
        for mcp in mcp_servers:
            if mcp.name in servers:
                results.append({"name": mcp.name, "status": "exists"})
                continue
            entry = self._mcp_config_entry(mcp)
            if entry is None:
                results.append({"name": mcp.name, "status": "error",
                                "reason": "缺少 url/command"})
                continue
            servers[mcp.name] = entry
            results.append({"name": mcp.name, "status": "ok"})

        # 解绑：移除已注册但不在绑定集合中的
        for name in sorted(set(servers) - bound_names):
            servers.pop(name, None)
            results.append({"name": name, "status": "removed"})

        if servers:
            cfg["mcp_servers"] = servers
        else:
            cfg.pop("mcp_servers", None)
        self._write_target_config(target_dir, cfg)
        return results

    @staticmethod
    def _resolve_hermes_bin(configured: str) -> str:
        """解析本机 hermes CLI 路径：绝对/带分隔符路径原样返回，否则走 PATH。"""
        configured = (configured or "hermes").strip()
        if os.path.sep in configured or (os.name == "nt" and ("\\" in configured or "/" in configured)):
            return configured
        return shutil.which(configured) or configured

    @staticmethod
    def _hermes_cli_available(hermes_bin: str) -> bool:
        """判断本机 hermes CLI 是否可用（不真正执行，只查 PATH / 文件存在性）。"""
        if os.path.sep in hermes_bin or (os.name == "nt" and ("\\" in hermes_bin or "/" in hermes_bin)):
            return os.path.isfile(hermes_bin)
        return shutil.which(hermes_bin) is not None

    @staticmethod
    def _ensure_local_profile(hermes_bin: str, profile_lower: str, target_dir: Path) -> None:
        """首次创建 profile：优先用 ``hermes profile create`` 初始化，失败则退化为 mkdir。"""
        try:
            subprocess.run(
                [hermes_bin, "profile", "create", profile_lower],
                capture_output=True, timeout=60, text=True,
            )
        except Exception:  # noqa: BLE001
            pass
        target_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _merge_platform_config(local_config: Path, target_config: Path) -> None:
        """把平台渲染的 model.default + gateway 配置合并进 Hermes 现有 config.yaml，不覆盖其它键。"""
        try:
            local_cfg = yaml.safe_load(local_config.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            local_cfg = {}
        if not isinstance(local_cfg, dict):
            local_cfg = {}

        existing: dict = {}
        if target_config.exists():
            try:
                loaded = yaml.safe_load(target_config.read_text(encoding="utf-8"))
                existing = loaded if isinstance(loaded, dict) else {}
            except Exception:  # noqa: BLE001
                existing = {}

        # 只更新 model.default
        if isinstance(local_cfg.get("model"), dict) and local_cfg["model"].get("default"):
            existing.setdefault("model", {})
            if isinstance(existing["model"], dict):
                existing["model"]["default"] = local_cfg["model"]["default"]

        # gateway 配置（multiplex_profiles 等）整体合并
        if isinstance(local_cfg.get("gateway"), dict):
            existing.setdefault("gateway", {})
            if isinstance(existing["gateway"], dict):
                for k, v in local_cfg["gateway"].items():
                    existing["gateway"][k] = v

        target_config.parent.mkdir(parents=True, exist_ok=True)
        target_config.write_text(
            yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    @staticmethod
    def _set_env_key(target: Path, key: str, value: str) -> None:
        """把 ``key=value`` 写入/更新到 profile 的 .env（保留其它行不变）。"""
        env_path = target / ".env"
        lines = []
        if env_path.exists():
            try:
                lines = env_path.read_text(encoding="utf-8").splitlines()
            except Exception:  # noqa: BLE001
                lines = []

        new_lines = []
        found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(key + "=") or stripped.startswith(key + " ="):
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}")

        env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


class HermesExecutor:
    """把任务转发到本地 Hermes API Server，记录审计日志。"""

    def __init__(self, db: Optional[Session] = None):
        self._owns_db = db is None
        self.db = db or SessionLocal()

    def close(self):
        if self._owns_db:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def run(
        self,
        expert: models.Expert,
        task: str,
        open_id: str = "",
        channel: str = "feishu",
        session_id: str = "",
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        start = time.time()
        profile_name = expert.profile_name

        # 1. 确保 Profile 已下发
        local_soul = PROFILES_DIR / profile_name / "SOUL.md"
        if not local_soul.exists():
            with ProfileRenderer(self.db) as renderer:
                renderer.render(expert)

        # 2. 读取 Hermes 接入配置（按 hermes_mode 解析执行目标）
        settings = get_settings(self.db)
        base_url, api_key, hermes_mode = resolve_hermes_target(settings)

        # 3. 未配置 → 引导错误（本机网关允许不设 API Key）
        if not base_url or (hermes_mode != "local" and not api_key):
            target_hint = "本机网关（本地 API URL）" if hermes_mode == "local" else "远端 API Server（API URL / API Key）"
            err = f"请先在平台设置中配置 Hermes Agent 接入（{target_hint}）"
            latency = int((time.time() - start) * 1000)
            self._log(expert, open_id, channel, task, err, 0, latency, "error")
            return {
                "response": err,
                "tool_calls": [],
                "session_id": "",
                "tokens": 0,
                "latency_ms": latency,
                "error": err,
            }

        session_id = session_id or f"{open_id}_{expert.id}"

        # 4. 调 HermesClient 流式调用
        try:
            result = HermesClient.call_api(
                base_url=base_url,
                api_key=api_key,
                session_id=session_id,
                message=task,
                system_prompt=expert.system_prompt or "",
                profile_name=profile_name,
                on_progress=on_progress,
                timeout=300,
            )
            latency = int((time.time() - start) * 1000)
            self._log(
                expert, open_id, channel, task,
                result.get("response", ""), result.get("tokens", 0), latency, "success",
            )
            result["latency_ms"] = latency
            return result
        except Exception as exc:  # noqa: BLE001
            latency = int((time.time() - start) * 1000)
            err = f"调用 Hermes 失败: {exc}"
            self._log(expert, open_id, channel, task, err, 0, latency, "error")
            return {
                "response": err,
                "tool_calls": [],
                "session_id": session_id,
                "tokens": 0,
                "latency_ms": latency,
                "error": err,
            }

    def _log(self, expert, open_id, channel, message, response, tokens, latency, status):
        try:
            self.db.add(
                models.CallLog(
                    expert_id=expert.id,
                    user_open_id=open_id,
                    channel=channel,
                    message=message,
                    response=response[:8000],
                    tokens=tokens,
                    latency_ms=latency,
                    status=status,
                )
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()


def render_expert(expert_id: int, db: Optional[Session] = None) -> dict:
    """按 id 渲染专家 profile。"""
    own = db is None
    session = db or SessionLocal()
    try:
        expert = session.get(models.Expert, expert_id)
        if expert is None:
            return {"error": "专家不存在"}
        with ProfileRenderer(session) as renderer:
            return renderer.render(expert)
    finally:
        if own:
            session.close()


def render_affected_experts(skill_id: Optional[int] = None, mcp_id: Optional[int] = None) -> int:
    """Skill / MCP 变更后，重渲染所有引用它的专家。返回重渲染数量。"""
    db = SessionLocal()
    count = 0
    try:
        experts = []
        if skill_id is not None:
            skill = db.get(models.Skill, skill_id)
            if skill:
                experts.extend(skill.experts)
        if mcp_id is not None:
            mcp = db.get(models.MCPServer, mcp_id)
            if mcp:
                experts.extend(mcp.experts)
        seen = set()
        with ProfileRenderer(db) as renderer:
            for expert in experts:
                if expert.id in seen:
                    continue
                seen.add(expert.id)
                try:
                    renderer.render(expert)
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
    finally:
        db.close()
    return count
