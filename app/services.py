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

        # 4. 同步到 Hermes 端（含 hermes mcp add 注册 MCP）
        sync = self._sync_to_hermes(profile_name, local_dir, mcp_servers)

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
            "name": skill.name,
            "version": skill.version or "1.0.0",
            "category": skill.category or "",
            "description": skill.description or "",
            "tags": [t.strip() for t in (skill.tags or "").split(",") if t.strip()],
        }
        fm = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n\n{skill.content or ''}".strip() + "\n"

    def _render_config(self, expert: models.Expert, mcp_servers: list) -> dict:
        """只渲染 model；MCP 交给 ``hermes mcp add``（职责分离）。

        model 用嵌套结构 model.default，provider/base_url/plugins 等保留 Hermes 自己的值。
        """
        settings = get_settings(self.db)
        model_default = expert.model or settings.get("default_model", "deepseek-chat")
        return {"model": {"default": model_default}}

    # ------------------------------------------------------------------
    def _sync_to_hermes(self, profile_name: str, local_dir: Path, mcp_servers: list) -> dict:
        """同步 SOUL/config/skills 到 Hermes profiles，并通过 ``hermes mcp add`` 注册 MCP。

        Hermes 会把 profile 名规范成小写，且其实际 profiles 目录随安装方式
        而变（如 %LOCALAPPDATA%\\hermes\\profiles），故用可配置+自动探测。
        """
        hermes_bin = shutil.which("hermes")
        if not hermes_bin:
            return {"status": "skipped", "reason": "hermes 未安装"}

        profile_lower = profile_name.lower()
        profiles_root = _resolve_hermes_profiles_dir(get_settings(self.db))
        target = profiles_root / profile_lower

        # 目录不存在 → 调 hermes profile create 初始化完整结构
        if not target.exists():
            try:
                proc = subprocess.run(
                    [hermes_bin, "profile", "create", profile_lower],
                    capture_output=True,
                    timeout=60,
                )
                if proc.returncode != 0:
                    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
                    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
                    return {
                        "status": "error",
                        "reason": f"profile create 失败: {(stderr or stdout or 'exit %d' % proc.returncode)[:300]}",
                    }
            except FileNotFoundError:
                return {"status": "skipped", "reason": "hermes 未安装"}
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "reason": f"profile create 失败: {exc}"}

        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_dir / "SOUL.md", target / "SOUL.md")

            # 只合并 model.default（provider/base_url/plugins 等保留 Hermes 的）
            self._merge_platform_config(local_dir / "config.yaml", target / "config.yaml")

            skills_dst = target / "skills"
            if skills_dst.exists():
                shutil.rmtree(skills_dst)
            shutil.copytree(local_dir / "skills", skills_dst, dirs_exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": str(exc)}

        # MCP 交给 hermes mcp add 管理，放后台线程执行（探活可能较慢，避免阻塞前端保存）
        mcp_infos = [
            {
                "name": mcp.name,
                "transport": mcp.transport or "http",
                "config_template": mcp.config_template or "{}",
            }
            for mcp in mcp_servers
        ]

        def _register_mcp():
            for info in mcp_infos:
                try:
                    result = self._hermes_mcp_add(hermes_bin, profile_lower, info)
                    logger.info("hermes mcp add %s: %s", info["name"], result)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("hermes mcp add %s 异常: %s", info["name"], exc)

        if mcp_infos:
            threading.Thread(target=_register_mcp, daemon=True, name="hermes-mcp-add").start()

        return {
            "status": "synced",
            "target": str(target),
            "mcp": "后台注册中（%d 个）" % len(mcp_infos) if mcp_infos else [],
        }

    def _hermes_mcp_add(self, hermes_bin: str, profile_lower: str, mcp_info: dict) -> dict:
        """调 ``hermes -p <profile> mcp add`` 注册一个 MCP server（管道喂 y 非交互）。"""
        name = mcp_info["name"]
        try:
            cfg = json.loads(mcp_info["config_template"] or "{}")
        except json.JSONDecodeError:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        inner = _extract_mcp_inner(cfg, name)

        cmd = [hermes_bin, "-p", profile_lower, "mcp", "add", name, "--connect-timeout", "15"]
        if mcp_info["transport"] == "stdio":
            command = str(inner.get("command", "")).strip()
            if not command:
                return {"name": name, "status": "error", "reason": "stdio 缺少 command"}
            cmd += ["--command", command]
            args = inner.get("args") or []
            if args:
                cmd += ["--args"] + [str(a) for a in args]
        else:  # http / sse
            url = str(inner.get("url", "")).strip()
            if not url:
                return {"name": name, "status": "error", "reason": "http 缺少 url"}
            cmd += ["--url", url]

        try:
            proc = subprocess.run(
                cmd,
                input="y\ny\ny\n",  # 覆盖确认 / 失败仍保存 / 启用全部工具
                capture_output=True,
                timeout=120,
                text=True,
            )
            tail = (proc.stdout or "").strip().splitlines()
            return {
                "name": name,
                "status": "ok" if proc.returncode == 0 else "error",
                "exit": proc.returncode,
                "detail": " | ".join(tail[-3:]) if tail else "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"name": name, "status": "error", "reason": str(exc)}

    @staticmethod
    def _merge_platform_config(local_config: Path, target_config: Path) -> None:
        """把本地渲染的 model.default 合并进 Hermes 现有 config.yaml，不覆盖其它键。"""
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

        target_config.write_text(
            yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


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
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        start = time.time()
        profile_name = expert.profile_name

        # 1. 确保 Profile 已下发
        local_soul = PROFILES_DIR / profile_name / "SOUL.md"
        if not local_soul.exists():
            with ProfileRenderer(self.db) as renderer:
                renderer.render(expert)

        # 2. 读取 Hermes 接入配置
        settings = get_settings(self.db)
        base_url = (settings.get("hermes_api_url") or "").strip()
        api_key = (settings.get("hermes_api_key") or "").strip()

        # 3. 未配置 → 引导错误
        if not base_url or not api_key:
            err = "请先在平台设置中配置 Hermes Agent 接入（API URL / API Key）"
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

        session_id = f"{open_id}_{expert.id}"

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
