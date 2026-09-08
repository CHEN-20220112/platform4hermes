"""飞书适配器：lark-oapi WebSocket 长连接 + 消息路由 + 进度卡片。

设计说明：
- 每个飞书 App（模式 A 全局 App，或模式 B 每个专家的独立 App）对应一个
  ``lark.ws.Client``，其 ``start()`` 会阻塞当前线程，因此每个 client 跑在
  一个独立 daemon 线程里，由 ``FeishuAdapter`` 统一编排（启停/状态）。
- 消息按 ``sender open_id`` 路由到「当前选定专家」。
- 进度卡片：先回复「思考中」卡片拿到 message_id，后续 on_progress 用 PATCH
  实时更新，完成后 PATCH 成终态卡片。
"""
import asyncio
import json
import logging
import threading
from typing import Optional

from sqlalchemy.orm import Session

from . import models
from .database import SessionLocal
from .services import HermesExecutor
from .settings_store import get_settings

logger = logging.getLogger("feishu")

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        P2ImMessageReceiveV1,
        CreateMessageRequest,
        CreateMessageRequestBody,
        PatchMessageRequest,
        PatchMessageRequestBody,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTrigger,
        P2CardActionTriggerResponse,
        CallBackToast,
    )
    LARK_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    logger.warning("lark-oapi 导入失败: %s", exc)
    LARK_AVAILABLE = False
    lark = None


# ---------------------------------------------------------------------------
# 进度事件 → 卡片文案
# ---------------------------------------------------------------------------
def _progress_detail(evt: dict) -> str:
    kind = evt.get("event") or evt.get("type") or evt.get("kind") or ""
    if kind == "iter_start":
        return "🔁 开始推理迭代…"
    if kind == "tool_call":
        tool = evt.get("name") or evt.get("tool") or evt.get("tool_name") or ""
        return f"🔧 调用工具：{tool}"
    if kind == "tool_result":
        return "✅ 工具执行完成"
    if kind == "final":
        return "📝 生成最终回复…"
    return ""


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（内容过长已截断）"


class _FeishuBot:
    """单个飞书 App 的 WebSocket 长连接客户端。"""

    def __init__(self, app_id: str, app_secret: str, adapter: "FeishuAdapter",
                 expert_id: Optional[int] = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.adapter = adapter
        self.expert_id = expert_id  # None → 模式 A 路由器
        self._client = None
        self._rest = None
        self._thread = None

    # ------------------------------------------------------------------
    def start(self):
        if not LARK_AVAILABLE:
            logger.warning("lark-oapi 未安装，无法启动飞书连接")
            return

        # REST client（发消息 / 回复 / PATCH 卡片）
        self._rest = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .log_level(lark.LogLevel.ERROR)
            .build()
        )

        builder = lark.EventDispatcherHandler.builder("", "")
        builder.register_p2_im_message_receive_v1(self._on_p2_message)
        builder.register_p2_card_action_trigger(self._on_card_action)

        handler = builder.build()

        self._client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        self._thread = threading.Thread(
            target=self._run_ws, daemon=True, name=f"feishu-{self.app_id}"
        )
        self._thread.start()
        logger.info("飞书 bot 已启动 app_id=%s expert_id=%s", self.app_id, self.expert_id)

    def _run_ws(self):
        """在独立线程里跑 ws.Client，并给每个 client 分配独立 event loop。

        lark-oapi 的 ``ws.Client`` 在模块级共享一个 ``loop``，默认同一进程只能
        跑一个长连接；多专家（模式 B）需要每个 client 一个 loop，这里替换模块级
        ``loop`` 变量来实现。
        """
        import lark_oapi.ws.client as _ws_module

        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        _ws_module.loop = new_loop
        try:
            self._client.start()
        except Exception as exc:  # noqa: BLE001
            logger.error("飞书 WS 连接失败 app_id=%s: %s", self.app_id, exc)

    def stop(self):
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:  # noqa: BLE001
                pass
        self._client = None

    # ------------------------------------------------------------------
    # 事件回调
    # ------------------------------------------------------------------
    def _on_p2_message(self, data: "P2ImMessageReceiveV1") -> None:
        self._handle_receive(getattr(data, "event", None))

    def _handle_receive(self, event) -> None:
        if event is None:
            return
        # 结构：event.message = EventMessage；event.sender = EventSender
        message = getattr(event, "message", None)
        if message is None:
            return
        message_id = getattr(message, "message_id", "") or ""
        text = self._extract_text(message)
        open_id = self._extract_open_id(event)
        if not text.strip() or not open_id:
            return

        # 去重：飞书 WS 是 at-least-once 投递，断线重连会重发相同 message_id。
        if message_id and self.adapter.is_duplicate_message(message_id):
            logger.info("忽略重复投递 message_id=%s text=%s", message_id, text[:30])
            return

        logger.info("收到消息 message_id=%s open_id=%s text=%s", message_id, open_id, text[:50])
        # 独立线程处理，避免阻塞 WS 事件循环（否则心跳超时 → 断线重连 → 重投递）
        threading.Thread(
            target=self.adapter.handle_message,
            args=(open_id, text.strip(), message_id, self.expert_id, self),
            daemon=True,
            name=f"feishu-task-{message_id[:12] if message_id else 'noid'}",
        ).start()

    def _on_card_action(self, data: "P2CardActionTrigger"):
        """卡片按钮回调（card.action.trigger），返回 toast 响应。"""
        try:
            evt = getattr(data, "event", None)
            if evt is None:
                return None
            operator = getattr(evt, "operator", None)
            open_id = (
                getattr(operator, "open_id", None)
                or getattr(operator, "user_id", None)
                or ""
            )
            action = getattr(evt, "action", None)
            value = getattr(action, "value", None)
            ctx = getattr(evt, "context", None)
            message_id = getattr(ctx, "open_message_id", None) if ctx else None
            if not open_id:
                return None
            return self.adapter.handle_card_action(
                open_id=open_id, value=value, message_id=message_id, bot=self
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("卡片回调处理失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 提取
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_text(message) -> str:
        msg_type = getattr(message, "message_type", "") or ""
        content = getattr(message, "content", "") or "{}"
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            data = {}
        if msg_type == "text":
            return data.get("text", "") or ""
        if msg_type == "post":
            parts = []
            try:
                body = data.get("content", {}).get("post", {})
                for lang in ("zh_cn", "en_us"):
                    for block in body.get(lang, {}).get("content", []):
                        for seg in block:
                            parts.append(seg.get("text", ""))
            except Exception:  # noqa: BLE001
                pass
            return "".join(parts)
        return ""

    @staticmethod
    def _extract_open_id(event) -> str:
        # 结构：event.sender = EventSender，其 sender_id 是 UserId 对象
        # （含 open_id / user_id / union_id 三个字段）。
        sender = getattr(event, "sender", None)
        sender_id = getattr(sender, "sender_id", None)
        if sender_id is None:
            return ""
        if isinstance(sender_id, str):
            return sender_id.strip()
        return (
            getattr(sender_id, "open_id", None)
            or getattr(sender_id, "user_id", None)
            or getattr(sender_id, "union_id", None)
            or ""
        ).strip()

    # ------------------------------------------------------------------
    # 卡片 / 消息发送
    # ------------------------------------------------------------------
    @staticmethod
    def _resp_detail(resp) -> str:
        code = getattr(resp, "code", None)
        msg = getattr(resp, "msg", None)
        return f"code={code} msg={msg}"

    def reply_card(self, message_id: str, card: dict) -> str:
        """回复一张卡片，返回新消息 message_id。"""
        if self._rest is None:
            logger.warning("reply_card: REST client 未初始化")
            return ""
        try:
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = self._rest.im.v1.message.reply(req)
            if resp.success() and resp.data is not None:
                mid = getattr(resp.data, "message_id", "") or ""
                logger.info("回复卡片成功 message_id=%s", mid)
                return mid
            logger.warning("回复卡片失败 %s", self._resp_detail(resp))
        except Exception as exc:  # noqa: BLE001
            logger.exception("回复卡片异常: %s", exc)
        return ""

    def send_card(self, open_id: str, card: dict) -> str:
        if self._rest is None:
            logger.warning("send_card: REST client 未初始化")
            return ""
        try:
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("interactive")
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = self._rest.im.v1.message.create(req)
            if resp.success() and resp.data is not None:
                mid = getattr(resp.data, "message_id", "") or ""
                logger.info("发送卡片成功 message_id=%s", mid)
                return mid
            logger.warning("发送卡片失败 %s", self._resp_detail(resp))
        except Exception as exc:  # noqa: BLE001
            logger.exception("发送卡片异常: %s", exc)
        return ""

    def patch_card(self, message_id: str, card: dict) -> bool:
        if self._rest is None or not message_id:
            return False
        try:
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = self._rest.im.v1.message.patch(req)
            if resp.success():
                return True
            logger.warning("更新卡片失败 %s", self._resp_detail(resp))
            return False
        except Exception as exc:  # noqa: BLE001
            logger.exception("更新卡片异常: %s", exc)
            return False

    def reply_text(self, message_id: str, text: str) -> bool:
        if self._rest is None:
            logger.warning("reply_text: REST client 未初始化")
            return False
        try:
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("text")
                    .content(json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = self._rest.im.v1.message.reply(req)
            if resp.success():
                return True
            logger.warning("回复文本失败 %s", self._resp_detail(resp))
            return False
        except Exception as exc:  # noqa: BLE001
            logger.exception("回复文本异常: %s", exc)
            return False


class FeishuAdapter:
    """飞书连接编排器（单例）。"""

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "FeishuAdapter":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._bots: dict = {}
        self._selection: dict = {}  # open_id -> expert_id（模式 A）
        self._lock = threading.RLock()
        self._running = False
        self._processed_messages: set = set()  # 已处理 message_id（去重）

    def is_duplicate_message(self, message_id: str) -> bool:
        """判断是否重复投递；首次见到会标记为已处理并返回 False。"""
        with self._lock:
            if message_id in self._processed_messages:
                return True
            self._processed_messages.add(message_id)
            # 限制缓存大小，只覆盖重投递窗口（飞书重投通常在秒级~分钟级）
            if len(self._processed_messages) > 5000:
                self._processed_messages = set(list(self._processed_messages)[2500:])
            return False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def _load_bots(self) -> dict:
        """根据设置 + 专家配置组装 bot 列表。返回 {key: _FeishuBot}。"""
        db = SessionLocal()
        bots: dict = {}
        try:
            settings = get_settings(db)
            mode = settings.get("feishu_mode", "A").upper()

            if mode == "B":
                experts = db.query(models.Expert).all()
                for e in experts:
                    if e.feishu_app_id and e.feishu_app_secret:
                        bots[f"B:{e.id}"] = _FeishuBot(
                            e.feishu_app_id, e.feishu_app_secret, self, expert_id=e.id
                        )
            else:  # 模式 A
                app_id = settings.get("feishu_app_id", "").strip()
                app_secret = settings.get("feishu_app_secret", "").strip()
                if app_id and app_secret:
                    bots["A:global"] = _FeishuBot(app_id, app_secret, self, expert_id=None)
        finally:
            db.close()
        return bots

    def start(self):
        with self._lock:
            if self._running:
                return {"running": True, "detail": "已运行"}
            self._running = True
        try:
            self._bots = self._load_bots()
            for bot in self._bots.values():
                try:
                    bot.start()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("bot 启动失败: %s", exc)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._running = False
            raise exc
        return self.status()

    def stop(self):
        with self._lock:
            self._running = False
            bots = list(self._bots.values())
            self._bots = {}
        for bot in bots:
            try:
                bot.stop()
            except Exception:  # noqa: BLE001
                pass
        return {"running": False}

    def restart(self):
        self.stop()
        return self.start()

    def auto_start(self):
        """应用启动时：若已配置飞书则自动建立连接。"""
        db = SessionLocal()
        try:
            settings = get_settings(db)
            mode = settings.get("feishu_mode", "A").upper()
            configured = False
            if mode == "B":
                configured = db.query(models.Expert).filter(
                    models.Expert.feishu_app_id != "",
                    models.Expert.feishu_app_secret != "",
                ).first() is not None
            else:
                configured = bool(settings.get("feishu_app_id") and settings.get("feishu_app_secret"))
        finally:
            db.close()
        if configured:
            try:
                self.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("飞书自动启动失败: %s", exc)

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "lark_available": LARK_AVAILABLE,
                "bots": [
                    {"app_id": b.app_id, "expert_id": b.expert_id, "key": k}
                    for k, b in self._bots.items()
                ],
            }

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    def _db(self) -> Session:
        return SessionLocal()

    def _list_experts(self, db: Session):
        return db.query(models.Expert).order_by(models.Expert.id).all()

    def handle_message(self, open_id, text, message_id, bot_expert_id=None, bot=None):
        db = self._db()
        try:
            # 模式 B：固定专家
            if bot_expert_id is not None:
                expert = db.get(models.Expert, bot_expert_id)
                if expert is None:
                    self._reply_text(bot, message_id, "专家不存在")
                    return
                self._run_task(db, bot, expert, text, open_id, message_id)
                return

            # 模式 A：路由
            lowered = text.strip().lower()
            if lowered in ("专家", "选择专家", "菜单", "列表", "help", "帮助", "hi", "你好"):
                self._send_select_card(db, bot, open_id, message_id)
                return

            if text.strip().isdigit():
                idx = int(text.strip())
                experts = self._list_experts(db)
                if 1 <= idx <= len(experts):
                    expert = experts[idx - 1]
                    with self._lock:
                        self._selection[open_id] = expert.id
                    self._reply_text(bot, message_id, f"已选择专家：{expert.name}")
                    return

            with self._lock:
                expert_id = self._selection.get(open_id)
            expert = db.get(models.Expert, expert_id) if expert_id else None
            if expert is None:
                self._send_select_card(db, bot, open_id, message_id)
                return
            self._run_task(db, bot, expert, text, open_id, message_id)
        finally:
            db.close()

    def handle_card_action(self, open_id, value, message_id, bot=None):
        """处理卡片按钮回调，返回 P2CardActionTriggerResponse（toast）。"""
        # 飞书可能把按钮 value 序列化成 JSON 字符串，这里做兼容。
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = {"value": value}
        value = value or {}

        action = value.get("action")
        if action == "select_expert":
            expert_id = value.get("expert_id")
            name = "未知专家"
            db = self._db()
            try:
                expert = db.get(models.Expert, expert_id)
                name = expert.name if expert else "未知专家"
                with self._lock:
                    self._selection[open_id] = int(expert_id)
            finally:
                db.close()
            return self._toast(f"已选择专家：{name}，现在可直接发送任务")
        return self._toast("操作成功")

    @staticmethod
    def _toast(content: str):
        """构造飞书卡片回调的 toast 响应。"""
        if not LARK_AVAILABLE:
            return None
        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        toast.type = "success"
        toast.content = content
        resp.toast = toast
        return resp

    # ------------------------------------------------------------------
    # 执行任务 + 进度卡片
    # ------------------------------------------------------------------
    def _run_task(self, db, bot, expert, text, open_id, message_id):
        logger.info("开始处理任务 expert=%s open_id=%s text=%s", expert.name, open_id, text[:50])
        # 1. 发送「思考中」进度卡片，拿到 card 的 message_id
        card_msg_id = bot.reply_card(
            message_id, self._progress_card("🤔 思考中…", "")
        )
        logger.info("思考中卡片 card_msg_id=%s", card_msg_id or "(空)")

        # 2. on_progress → PATCH 卡片
        def on_progress(evt: dict):
            if not card_msg_id:
                return
            detail = _progress_detail(evt)
            if detail:
                bot.patch_card(card_msg_id, self._progress_card("⚙️ 处理中…", detail))

        # 3. 调 HermesExecutor
        try:
            with HermesExecutor(db) as executor:
                result = executor.run(
                    expert, text, open_id=open_id, channel="feishu", on_progress=on_progress
                )
            logger.info(
                "HermesExecutor 返回 latency_ms=%s tokens=%s error=%s",
                result.get("latency_ms"), result.get("tokens"), result.get("error"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("HermesExecutor 异常: %s", exc)
            result = {"response": f"执行异常: {exc}", "tool_calls": [], "tokens": 0, "latency_ms": 0, "error": str(exc)}

        # 4. 终态卡片
        final = self._final_card(expert, result)
        if card_msg_id:
            bot.patch_card(card_msg_id, final)
        else:
            bot.reply_card(message_id, final)

    # ------------------------------------------------------------------
    # 卡片构造
    # ------------------------------------------------------------------
    def _progress_card(self, title: str, detail: str) -> dict:
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": title}},
        ]
        if detail:
            elements.append({"tag": "hr"})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": detail}})
        return {
            "schema": "2.0",
            "config": {"update_multi": True, "width_mode": "fill"},
            "header": {
                "title": {"tag": "plain_text", "content": "Hermes 专家"},
                "template": "blue",
            },
            "body": {"elements": elements},
        }

    def _final_card(self, expert, result: dict) -> dict:
        response = result.get("response") or ""
        tool_calls = result.get("tool_calls") or []
        lines = [f"**{expert.name}** 已完成任务\n", "---", "**回复**\n", _truncate(response)]
        if tool_calls:
            lines += ["\n---", "**工具调用链路**"]
            for i, tc in enumerate(tool_calls, 1):
                name = tc.get("name") or tc.get("tool") or tc.get("tool_name") or "?"
                lines.append(f"{i}. {name}")
        lines += [
            "\n---",
            f"⏱️ {result.get('latency_ms', 0)} ms · tokens {result.get('tokens', 0)}",
        ]
        return {
            "schema": "2.0",
            "config": {"update_multi": True, "width_mode": "fill"},
            "header": {
                "title": {"tag": "plain_text", "content": "✅ 已完成"},
                "template": "green",
            },
            "body": {"elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]},
        }

    def _select_card(self, experts) -> dict:
        actions = []
        for i, e in enumerate(experts, 1):
            actions.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"{i}. {e.name}"},
                    "value": {"action": "select_expert", "expert_id": e.id},
                    "type": "primary",
                }
            )
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": "**请选择专家**（点按钮或回复数字）："}},
            {"tag": "action", "actions": actions},
        ]
        return {
            "schema": "2.0",
            "config": {"update_multi": True, "width_mode": "fill"},
            "header": {"title": {"tag": "plain_text", "content": "专家列表"}, "template": "wathet"},
            "body": {"elements": elements},
        }

    def _send_select_card(self, db, bot, open_id, message_id):
        experts = self._list_experts(db)
        if not experts:
            self._reply_text(bot, message_id, "暂无可用专家，请先在平台创建专家。")
            return
        bot.reply_card(message_id, self._select_card(experts))

    def _reply_text(self, bot, message_id, text):
        if bot is not None:
            bot.reply_text(message_id, text)
