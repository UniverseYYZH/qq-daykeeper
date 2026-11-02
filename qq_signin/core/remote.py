"""手机 QQ 远程控制。

NapCat 的 HTTP 上报（httpClients）把消息事件 POST 到本机，只有
``self_id == user_id`` 的私聊消息才会被当成命令；机器人自己的回复带前缀，
不会再次触发命令。这样不需要公网地址，也不接受别人发来的命令。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PREFIX = "【QQ签到助手】"

MENU_TEXT = f"""{PREFIX}菜单
查询：查看各模块开关和今日状态
任务开 / 任务关：所有消息任务
群打卡开 / 群打卡关
火花开 / 火花关
QQ打卡开 / QQ打卡关
全部开 / 全部关
菜单 / 帮助：显示本菜单"""

# 用户可能带空格、全角符号或不同说法；统一成规范命令
_ALIASES = {
    "菜单": "menu",
    "帮助": "menu",
    "help": "menu",
    "?": "menu",
    "？": "menu",
    "menu": "menu",
    "查询": "query",
    "状态": "query",
    "status": "query",
    "query": "query",
    "任务开": "tasks_on",
    "开启任务": "tasks_on",
    "打开任务": "tasks_on",
    "任务开启": "tasks_on",
    "任务关": "tasks_off",
    "关闭任务": "tasks_off",
    "任务关闭": "tasks_off",
    "群打卡开": "checkin_on",
    "开启群打卡": "checkin_on",
    "打开群打卡": "checkin_on",
    "打卡开": "checkin_on",
    "群打卡关": "checkin_off",
    "关闭群打卡": "checkin_off",
    "打卡关": "checkin_off",
    "火花开": "spark_on",
    "开启火花": "spark_on",
    "打开火花": "spark_on",
    "火花关": "spark_off",
    "关闭火花": "spark_off",
    "qq打卡开": "personal_on",
    "开启qq打卡": "personal_on",
    "打开qq打卡": "personal_on",
    "qq打卡关": "personal_off",
    "关闭qq打卡": "personal_off",
    "全部开": "all_on",
    "全部开启": "all_on",
    "全部关": "all_off",
    "全部关闭": "all_off",
}


def parse_command(text: str) -> str | None:
    normalized = "".join(str(text).split()).strip().lower()
    return _ALIASES.get(normalized)


def extract_text(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, list):
        parts: list[str] = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") == "text":
                data = segment.get("data")
                if isinstance(data, dict):
                    parts.append(str(data.get("text", "")))
        if parts:
            return "".join(parts)
    if isinstance(message, str):
        return message
    raw = payload.get("raw_message")
    return raw if isinstance(raw, str) else ""


class RemoteControl:
    """解析事件并交给服务线程执行，避免在 HTTP 线程里操作 QQ。"""

    def __init__(self, service: Any, logger: logging.Logger | None = None) -> None:
        self.service = service
        self.logger = logger or logging.getLogger("qq_signin.remote")
        self._seen: deque[str] = deque(maxlen=300)
        self._seen_set: set[str] = set()
        self._lock = threading.Lock()

    def handle_event(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        post_type = str(payload.get("post_type", ""))
        if post_type not in {"message", "message_sent"}:
            return False
        if str(payload.get("message_type", "")) != "private":
            return False

        try:
            self_id = int(payload.get("self_id") or 0)
            user_id = int(payload.get("user_id") or 0)
        except (TypeError, ValueError):
            return False
        if not self_id or user_id != self_id:
            return False
        if payload.get("target_id") is not None:
            try:
                if int(payload["target_id"]) != self_id:
                    return False
            except (TypeError, ValueError):
                return False

        text = extract_text(payload).strip()
        if not text or text.startswith(PREFIX):
            return False
        self.logger.info(
            "手机事件：post_type=%s message_type=%s self_id=%s "
            "user_id=%s target_id=%s text=%r",
            post_type,
            payload.get("message_type"),
            self_id,
            user_id,
            payload.get("target_id"),
            text,
        )
        command = parse_command(text)
        if command is None:
            return False

        message_id = str(payload.get("message_id") or "")
        if not message_id:
            message_id = f"{self_id}:{user_id}:{text}"
        with self._lock:
            if message_id in self._seen_set:
                return False
            if len(self._seen) == self._seen.maxlen:
                old = self._seen[0]
                self._seen_set.discard(old)
            self._seen.append(message_id)
            self._seen_set.add(message_id)

        self.service.enqueue_remote_command(command, self_id)
        return True


class RemotePoller(threading.Thread):
    """兜底轮询最近联系人。

    NapCat 的 httpClients 有时需要在写入配置后重启才生效；这个轮询直接
    使用已经可用的 OneBot HTTP 接口读取“最近联系人”，发现新的自聊命令
    就交给同一个 ``RemoteControl``，所以不依赖 HTTP 上报是否配置成功。
    """

    def __init__(
        self,
        service: Any,
        control: RemoteControl,
        *,
        interval: float = 10.0,
    ) -> None:
        super().__init__(name="qq-signin-remote-poller", daemon=True)
        self.service = service
        self.control = control
        self.interval = max(5.0, float(interval))
        self._stop_event = threading.Event()
        self._seen: set[str] = set()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        started_at = time.time() - 2
        while not self._stop_event.is_set():
            try:
                login = self.service.login_info
                if not self.service.connected or login is None:
                    self._stop_event.wait(self.interval)
                    continue
                self_id = int(login.user_id)
                client = self.service._client_or_raise()
                contacts = client.get_recent_contact(count=30)
                for contact in contacts:
                    message = contact.get("lastestMsg")
                    if not isinstance(message, dict):
                        continue
                    if str(message.get("post_type", "")) not in {
                        "message",
                        "message_sent",
                    }:
                        continue
                    if str(message.get("message_type", "")) != "private":
                        continue
                    try:
                        if int(message.get("user_id") or 0) != self_id:
                            continue
                    except (TypeError, ValueError):
                        continue
                    try:
                        timestamp = float(
                            message.get("time")
                            or message.get("timestamp")
                            or 0
                        )
                    except (TypeError, ValueError):
                        timestamp = 0
                    if timestamp and timestamp < started_at:
                        continue
                    message_id = str(
                        message.get("message_id")
                        or contact.get("msgId")
                        or ""
                    )
                    key = message_id or f"{timestamp}:{message.get('raw_message', '')}"
                    if key in self._seen:
                        continue
                    payload = dict(message)
                    if message_id:
                        payload["message_id"] = message_id
                    text = extract_text(payload).strip()
                    if parse_command(text) is None:
                        continue
                    # 先记已见，避免事件上报已处理时轮询再次执行
                    self._seen.add(key)
                    self.control.handle_event(payload)
            except Exception as exc:
                self.control.logger.debug("手机指令轮询失败：%s", exc)
            self._stop_event.wait(self.interval)


class _Handler(BaseHTTPRequestHandler):
    server: "_HTTPServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/", "/event"}:
            self.send_error(404)
            return
        expected = self.server.remote_token
        if expected:
            supplied = self.headers.get("Authorization", "")
            if supplied not in {expected, f"Bearer {expected}"}:
                self.send_error(401)
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                self.send_error(400)
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return

        self.server.control.handle_event(payload)
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], control: RemoteControl, token: str):
        self.control = control
        self.remote_token = token
        super().__init__(address, _Handler)


class RemoteEventServer(threading.Thread):
    """监听 127.0.0.1:port，接收 NapCat 的 HTTP 事件上报。"""

    def __init__(self, control: RemoteControl, port: int, token: str = "") -> None:
        super().__init__(name="qq-signin-remote", daemon=True)
        self._httpd = _HTTPServer(("127.0.0.1", port), control, token)
        self.port = port

    def run(self) -> None:
        try:
            self._httpd.serve_forever(poll_interval=0.5)
        finally:
            self._httpd.server_close()

    def stop(self) -> None:
        self._httpd.shutdown()
