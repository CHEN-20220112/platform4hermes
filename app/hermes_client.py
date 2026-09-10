"""HermesClient：HTTP 调用本地 Hermes API Server（SSE 流式）。"""
import json
import logging
from typing import Callable, Optional

import requests

ProgressHandler = Optional[Callable[[dict], None]]

logger = logging.getLogger("hermes")


class HermesClient:
    """调用 Hermes API Server 的 /v1/chat/completions（OpenAI 兼容 + SSE）。"""

    @staticmethod
    def call_api(
        base_url: str,
        api_key: str,
        session_id: str,
        message: str,
        system_prompt: str = "",
        profile_name: str = "",
        on_progress: ProgressHandler = None,
        timeout: int = 300,
    ) -> dict:
        """
        SSE 流式调用 Hermes。

        优先使用多 profile 路由 /p/{profile_name}/v1/chat/completions；
        仅当该路由本身不存在（404/405 且非「Unknown or unconfigured profile」，
        即服务器不支持多 profile 路由）时才回退到标准路由 /v1/chat/completions。
        若返回的是「profile 未配置/网关未起」这类真实故障，则直接抛错，绝不
        静默回退到 default（否则会把不同专家/模型的请求全部打到 default）。

        返回:
            {response, tool_calls, session_id, tokens}
        """
        base = base_url.rstrip("/")

        candidates = []
        if profile_name:
            # Hermes 会把 profile 名规范成小写，这里统一转小写
            candidates.append(f"{base}/p/{profile_name.lower()}/v1/chat/completions")
        candidates.append(f"{base}/v1/chat/completions")

        last_error: Optional[Exception] = None
        for url in candidates:
            try:
                return HermesClient._stream_call(
                    url, api_key, session_id, message, system_prompt, on_progress, timeout
                )
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                body = ""
                if exc.response is not None:
                    try:
                        body = (exc.response.text or "").lower()
                    except Exception:  # noqa: BLE001
                        body = ""

                # 「Unknown or unconfigured profile」表示 profile 路由本身存在，
                # 但该 profile 的网关没有起来 / 未配置 —— 这是真实故障，必须抛错，
                # 不能回退到 /v1/chat/completions，否则所有专家都会静默落到 default，
                # 用错误的模型出结果（这正是「全部打到 default」的根因）。
                profile_missing = "unknown or unconfigured profile" in body or "unconfigured profile" in body

                # 仅当「多 profile 路由本身不存在」时才回退：404/405 且响应体不是
                # 「profile 未配置」（例如旧版 Hermes 根本不支持 /p/{profile} 路由）。
                if url != candidates[-1] and status in (404, 405) and not profile_missing:
                    logger.warning(
                        "Hermes 路由 %s 返回 HTTP %s，回退标准路由", url, status
                    )
                    last_error = exc
                    continue
                raise
        if last_error is not None:
            raise last_error
        # 理论上到不了这里（candidates 至少有一个标准路由）
        raise RuntimeError("HermesClient: 无可用 URL")

    @staticmethod
    def _stream_call(
        url: str,
        api_key: str,
        session_id: str,
        message: str,
        system_prompt: str,
        on_progress: ProgressHandler,
        timeout: int,
    ) -> dict:
        headers = {
            "X-Hermes-Session-Id": session_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        # 本机网关可能不设 API_SERVER_KEY，此时不发送 Authorization，避免空 Bearer 被拒
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})

        body = {
            "model": "hermes-agent",
            "messages": messages,
            "stream": True,
        }

        tool_calls: list = []
        response_parts: list = []
        tokens = 0
        error_msgs: list = []

        with requests.post(
            url, headers=headers, json=body, stream=True, timeout=timeout
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue

                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                HermesClient._handle_event(
                    data, tool_calls, response_parts, on_progress
                )
                tokens = data.get("usage", {}).get("total_tokens", tokens) if isinstance(
                    data.get("usage"), dict
                ) else tokens

                # Hermes 把「模型不存在 / 工具失败」这类真实故障也包装成
                # HTTP 200 的 SSE 流，仅通过 finish_reason=error 表达。若不主动
                # 捕获，平台会把空回复当成「成功」，导致飞书显示「已完成」却无内容。
                errored = any(
                    choice.get("finish_reason") == "error"
                    for choice in (data.get("choices") or [])
                )
                if errored:
                    error_msgs.append(
                        HermesClient._stream_error(data) or "Hermes 执行出错（无错误详情）"
                    )

        response_text = "".join(response_parts).strip()
        if error_msgs and not response_text:
            raise RuntimeError("; ".join(dict.fromkeys(error_msgs)))

        result = {
            "response": response_text,
            "tool_calls": tool_calls,
            "session_id": session_id,
            "tokens": int(tokens or 0),
        }
        if error_msgs:
            result["error"] = "; ".join(dict.fromkeys(error_msgs))
        return result

    @staticmethod
    def _stream_error(data: dict) -> str:
        """从 SSE 事件里提取错误信息（error 字段可能是 dict 或 str）。"""
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or "").strip()
        if isinstance(err, str):
            return err.strip()
        return ""

    @staticmethod
    def _handle_event(data: dict, tool_calls: list, response_parts: list, on_progress) -> None:
        event_type = data.get("type") or data.get("event")

        # hermes.tool.progress —— 进度事件（iter_start / tool_call / tool_result / final）
        if event_type == "hermes.tool.progress" or "progress" in str(event_type or "").lower():
            inner = data.get("data") or data.get("payload") or data
            if isinstance(inner, dict):
                kind = (
                    inner.get("event")
                    or inner.get("type")
                    or inner.get("kind")
                    or inner.get("name")
                )
                # 工具调用链路收集
                if kind in ("tool_call", "tool_result", "iter_start"):
                    tool_calls.append(inner)
                if on_progress:
                    on_progress(inner)
            return

        # chat.completion.chunk —— 累积最终回复
        if event_type in (None, "", "chat.completion.chunk", "message.delta"):
            choices = data.get("choices") or []
            for choice in choices:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str):
                    response_parts.append(content)
                # OpenAI 风格 tool_calls delta（兜底）
                for tc in delta.get("tool_calls") or []:
                    tool_calls.append(tc)
            return

        # 兼容某些实现直接把内容放顶层
        if isinstance(data.get("content"), str):
            response_parts.append(data["content"])


def call_api(*args, **kwargs) -> dict:
    """模块级便捷函数。"""
    return HermesClient.call_api(*args, **kwargs)
