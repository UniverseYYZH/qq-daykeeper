"""手机指令解析与自聊消息过滤测试。"""

from __future__ import annotations

import json
import time
import unittest
import urllib.request

from qq_signin.core.remote import (
    PREFIX,
    RemoteControl,
    RemoteEventServer,
    parse_command,
)


class CommandTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(parse_command("菜单"), "menu")
        self.assertEqual(parse_command(" 查询 "), "query")
        self.assertEqual(parse_command("开启群打卡"), "checkin_on")
        self.assertEqual(parse_command("QQ打卡关"), "personal_off")
        self.assertEqual(parse_command("全部开启"), "all_on")
        self.assertIsNone(parse_command("随便说点什么"))


class _Service:
    def __init__(self) -> None:
        self.commands: list[tuple[str, int]] = []

    def enqueue_remote_command(self, command: str, self_id: int) -> None:
        self.commands.append((command, self_id))


class EventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _Service()
        self.control = RemoteControl(self.service)

    def event(self, **overrides):
        payload = {
            "post_type": "message_sent",
            "message_type": "private",
            "self_id": 10001,
            "user_id": 10001,
            "target_id": 10001,
            "message_id": 1,
            "message": [{"type": "text", "data": {"text": "菜单"}}],
        }
        payload.update(overrides)
        return payload

    def test_self_message_is_queued(self) -> None:
        self.assertTrue(self.control.handle_event(self.event()))
        self.assertEqual(self.service.commands, [("menu", 10001)])

    def test_other_user_is_ignored(self) -> None:
        self.assertFalse(self.control.handle_event(self.event(user_id=20001)))

    def test_group_is_ignored(self) -> None:
        self.assertFalse(
            self.control.handle_event(self.event(message_type="group"))
        )

    def test_bot_reply_prefix_is_ignored(self) -> None:
        self.assertFalse(
            self.control.handle_event(
                self.event(
                    message=[
                        {"type": "text", "data": {"text": PREFIX + "菜单"}}
                    ]
                )
            )
        )

    def test_duplicate_message_id_is_ignored(self) -> None:
        self.assertTrue(self.control.handle_event(self.event()))
        self.assertFalse(self.control.handle_event(self.event()))

    def test_unknown_text_is_ignored(self) -> None:
        self.assertFalse(
            self.control.handle_event(
                self.event(
                    message=[{"type": "text", "data": {"text": "普通聊天"}}]
                )
            )
        )


class ServerTests(unittest.TestCase):
    def test_http_post_is_accepted(self) -> None:
        service = _Service()
        control = RemoteControl(service)
        server = RemoteEventServer(control, 0)
        self.addCleanup(server.stop)
        server.start()
        port = server._httpd.server_address[1]
        payload = {
            "post_type": "message_sent",
            "message_type": "private",
            "self_id": 10001,
            "user_id": 10001,
            "target_id": 10001,
            "message_id": 99,
            "message": [{"type": "text", "data": {"text": "查询"}}],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/event",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
        deadline = time.time() + 2
        while not service.commands and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(service.commands, [("query", 10001)])


if __name__ == "__main__":
    unittest.main()
