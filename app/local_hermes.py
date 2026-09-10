"""本机 Hermes 网关托管：以 multiplex 单共享网关方式启动/停止本地 OpenAI 兼容 API Server。

对应 Hermes 官方「多 profile 复用」方案（单共享网关 + /p/{profile} 路由）：
  1. 渲染 profile 时写入 ``gateway.multiplex_profiles: true``；
  2. 启动一个 ``hermes gateway run``（OpenAI 兼容 api_server 平台随网关一起跑），
     并注入 ``GATEWAY_MULTIPLEX_PROFILES=true`` + ``API_SERVER_ENABLED=true``；
  3. 平台通过 ``/p/{profile}/v1/chat/completions`` 路由到对应 profile。

说明：这是「可选」能力 —— 平台把 profile 落到本地目录后，你也可以自己手动启动
``hermes gateway run``。本模块只是提供一个从 Web 后台一键启停的托管入口。

Hermes 版本差异：``api_server`` 是 gateway 的一个 platform，受这些环境变量控制：
``API_SERVER_ENABLED`` / ``API_SERVER_KEY``（必填）/ ``API_SERVER_PORT``（默认 8642）
/ ``API_SERVER_HOST``（默认 127.0.0.1）。
"""
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

from .database import DATA_DIR

logger = logging.getLogger("local_hermes")

_DEFAULT_PORT = 8642
_LOG_FILE = DATA_DIR / "local_hermes_gateway.log"


def _resolve_bin(configured: str) -> str:
    configured = (configured or "hermes").strip()
    if os.path.sep in configured or (os.name == "nt" and ("\\" in configured or "/" in configured)):
        return configured
    return shutil.which(configured) or configured


def _parse_url(url: str) -> tuple:
    p = urlparse((url or "").strip() or f"http://127.0.0.1:{_DEFAULT_PORT}")
    host = p.hostname or "127.0.0.1"
    try:
        port = p.port or _DEFAULT_PORT
    except ValueError:
        port = _DEFAULT_PORT
    return host, port


class LocalHermesGateway:
    """单例：管理本机 multiplex 网关子进程的生命周期。"""

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "LocalHermesGateway":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._proc = None
        self._cmd = None

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        return {
            "running": self._alive(),
            "pid": self._proc.pid if self._alive() else None,
            "cmd": self._cmd,
            "log_file": str(_LOG_FILE),
        }

    def start(self, hermes_bin: str = "hermes", api_url: str = "",
              profiles_root: str = "", api_key: str = "") -> dict:
        with self._lock:
            if self._alive():
                return {**self.status(), "detail": "已运行"}

            bin_path = _resolve_bin(hermes_bin)
            host, port = _parse_url(api_url)
            cmd = [bin_path, "gateway", "run"]

            env = dict(os.environ)
            env["GATEWAY_MULTIPLEX_PROFILES"] = "true"
            env["API_SERVER_ENABLED"] = "true"
            env["API_SERVER_HOST"] = host
            env["API_SERVER_PORT"] = str(port)
            if api_key:
                env["API_SERVER_KEY"] = api_key
            if profiles_root:
                # HERMES_HOME 指向 profiles 根目录的上一级，网关按 $HERMES_HOME/profiles 发现 profile
                env["HERMES_HOME"] = str(Path(profiles_root).parent)

            try:
                log_fh = open(_LOG_FILE, "ab")
            except Exception:  # noqa: BLE001
                log_fh = subprocess.DEVNULL
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=(os.name != "nt"),
                )
                self._cmd = " ".join(cmd)
            except Exception as exc:  # noqa: BLE001
                return {"running": False, "error": f"启动失败: {exc}", "cmd": " ".join(cmd)}

        return {**self.status(), "detail": "已启动"}

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._proc = None
                return {"running": False, "detail": "已停止"}
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            except Exception as exc:  # noqa: BLE001
                return {"running": True, "error": str(exc)}
            self._proc = None
            self._cmd = None
        return {"running": False, "detail": "已停止"}
