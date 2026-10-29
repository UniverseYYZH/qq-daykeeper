"""OneBot HTTP 客户端测试（使用本地假服务，不需要 QQ）。"""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qq_signin.core.onebot import OneBotClient, OneBotError, normalize_base_url
from tools.fake_onebot import FakeOneBotServer


class OneBotClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeOneBotServer(0).start()
        self.addCleanup(self.server.stop)
        self.client = OneBotClient(self.server.url, timeout=5)

    def test_login_info(self) -> None:
        info = self.client.get_login_info()
        self.assertEqual(info.user_id, 10001)
        self.assertEqual(info.nickname, "测试账号")

    def test_group_and_friend_lists(self) -> None:
        groups = self.client.get_group_list()
        friends = self.client.get_friend_list()
        self.assertEqual(len(groups), 3)
        self.assertEqual(len(friends), 3)
        self.assertEqual(groups[0].kind, "group")
        self.assertEqual(friends[0].kind, "friend")

    def test_send_messages(self) -> None:
        self.client.send_group_msg(10001, "群消息")
        self.client.send_private_msg(20001, "私聊消息")
        self.assertEqual(
            self.server.calls_for("send_group_msg")[0]["params"]["message"], "群消息"
        )
        self.assertEqual(
            self.server.calls_for("send_private_msg")[0]["params"]["message"],
            "私聊消息",
        )

    def test_checkin_api(self) -> None:
        self.client.send_group_sign(10001)
        self.assertEqual(len(self.server.calls_for("send_group_sign")), 1)

    def test_get_cookies(self) -> None:
        data = self.client.get_cookies("qzone.qq.com")
        self.assertIn("p_skey", data["cookies"])
        self.assertEqual(data["bkn"], "123456789")

    def test_friend_msg_history(self) -> None:
        self.server.history = [
            {"time": 1700000000, "sender": {"user_id": 20001}}
        ]
        messages = self.client.get_friend_msg_history(20001, count=10)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["sender"]["user_id"], 20001)

    def test_retcode_error(self) -> None:
        with self.assertRaises(OneBotError) as ctx:
            self.client.call("not_implemented")
        self.assertIn("调用失败", str(ctx.exception))

    def test_token_required(self) -> None:
        token_server = FakeOneBotServer(0, token="secret").start()
        self.addCleanup(token_server.stop)
        time.sleep(0.1)

        last_error: OneBotError | None = None
        for _ in range(3):
            try:
                OneBotClient(
                    token_server.url, token="", timeout=5
                ).get_login_info()
            except OneBotError as exc:
                last_error = exc
                if "HTTP 401" in str(exc):
                    break
                time.sleep(0.1)
            else:
                self.fail("没有 token 时不应该成功")
        assert last_error is not None
        self.assertIn("HTTP 401", str(last_error))

        info = OneBotClient(token_server.url, token="secret", timeout=5).get_login_info()
        self.assertEqual(info.user_id, 10001)


class UrlTests(unittest.TestCase):
    def test_adds_scheme_and_slash(self) -> None:
        self.assertEqual(
            normalize_base_url("127.0.0.1:3000"), "http://127.0.0.1:3000/"
        )
        self.assertEqual(
            normalize_base_url("http://127.0.0.1:3000/onebot"),
            "http://127.0.0.1:3000/onebot/",
        )

    def test_rejects_bad_values(self) -> None:
        for value in ("", "ftp://host", "http://", "http://host?x=1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_base_url(value)


class _NapCat4Handler(BaseHTTPRequestHandler):
    """模拟 NapCat 4：根路径返回健康检查，action 放在 URL 路径上。"""

    paths: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        type(self).paths.append(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/":
            payload = {
                "status": "ok",
                "retcode": 0,
                "data": {},
                "message": "NapCat4 Is Running",
            }
        elif self.path == "/get_login_info":
            payload = {
                "status": "ok",
                "retcode": 0,
                "data": {"user_id": 10001, "nickname": "NapCat4测试号"},
            }
        else:
            payload = {
                "status": "ok",
                "retcode": 0,
                "data": [],
            }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class NapCat4StyleTests(unittest.TestCase):
    def setUp(self) -> None:
        _NapCat4Handler.paths = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _NapCat4Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        host, port = self.server.server_address[:2]
        self.url = f"http://{host}:{port}"
        self.client = OneBotClient(self.url, timeout=5)

    def test_auto_detect_path_style(self) -> None:
        info = self.client.get_login_info()
        self.assertEqual(info.user_id, 10001)
        self.assertEqual(info.nickname, "NapCat4测试号")
        # 第一次探测根路径，随后实际请求走 /get_login_info
        self.assertIn("/", _NapCat4Handler.paths)
        self.assertIn("/get_login_info", _NapCat4Handler.paths)

    def test_path_style_cached(self) -> None:
        self.client.get_login_info()
        _NapCat4Handler.paths.clear()
        self.client.get_group_list()
        self.assertEqual(_NapCat4Handler.paths, ["/get_group_list"])


if __name__ == "__main__":
    unittest.main()
