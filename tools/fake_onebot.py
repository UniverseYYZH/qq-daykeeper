"""开发用假 OneBot 服务。

没有 QQ / NapCat 时，用它验证界面、任务和签到流程：

    python tools/fake_onebot.py --port 18082

然后在软件设置里填 http://127.0.0.1:18082，点“连接并同步”。
它不会真的发 QQ 消息，只会记录收到的调用并返回成功。
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeOneBotServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int = 18082, token: str = "") -> None:
        super().__init__(("127.0.0.1", port), FakeOneBotHandler)
        self.token = token.strip()
        self.calls: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def record(self, action: str, params: dict[str, Any]) -> None:
        with self._lock:
            self.calls.append({"action": action, "params": params})

    def calls_for(self, action: str) -> list[dict[str, Any]]:
        with self._lock:
            return [c for c in self.calls if c["action"] == action]

    def start(self) -> "FakeOneBotServer":
        self._thread = threading.Thread(
            target=self.serve_forever, name="fake-onebot", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


class FakeOneBotHandler(BaseHTTPRequestHandler):
    server: FakeOneBotServer

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的命名约定
        if self.path not in {"/", ""}:
            self._send_json(404, {"status": "failed", "retcode": 404, "message": "not found"})
            return

        if self.server.token:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {self.server.token}":
                self._send_json(
                    401,
                    {"status": "failed", "retcode": 401, "message": "invalid token"},
                )
                return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            action = str(payload.get("action", ""))
            params = payload.get("params", {}) or {}
            if not isinstance(params, dict):
                raise ValueError("params 必须是对象")
        except Exception as exc:
            self._send_json(
                400,
                {"status": "failed", "retcode": 400, "message": f"bad request: {exc}"},
            )
            return

        self.server.record(action, params)
        print(f"[fake-onebot] {action} {json.dumps(params, ensure_ascii=False)}")

        try:
            data = self._dispatch(action, params)
        except ValueError as exc:
            self._send_json(
                200,
                {"status": "failed", "retcode": 1404, "message": str(exc), "data": None},
            )
            return
        self._send_json(200, {"status": "ok", "retcode": 0, "data": data})

    def _dispatch(self, action: str, params: dict[str, Any]) -> Any:
        if action == "get_login_info":
            return {"user_id": 10001, "nickname": "测试账号"}
        if action == "get_status":
            return {"online": True, "good": True}
        if action == "get_group_list":
            return [
                {"group_id": 10001, "group_name": "测试群 · 摸鱼"},
                {"group_id": 10002, "group_name": "测试群 · 通知"},
                {"group_id": 10003, "group_name": "测试群 · 打卡"},
            ]
        if action == "get_friend_list":
            return [
                {"user_id": 20001, "nickname": "小明", "remark": "小明"},
                {"user_id": 20002, "nickname": "小红", "remark": ""},
                {"user_id": 20003, "nickname": "文件传输助手", "remark": ""},
            ]
        if action == "get_cookies":
            # 模拟 NapCat 返回的 QQ 空间 Cookie，供个人签到测试
            domain = str(params.get("domain", ""))
            return {
                "cookies": (
                    "uin=o10001; skey=@fake_skey; p_skey=@fake_p_skey; "
                    f"domain={domain}"
                ),
                "bkn": "123456789",
            }
        if action == "get_friend_msg_history":
            return {"messages": list(self.server.history)}
        if action in {"send_group_msg", "send_private_msg"}:
            return {"message_id": len(self.server.calls)}
        if action == "send_group_sign":
            return None
        raise ValueError(f"fake server 未实现接口：{action}")

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        # 默认的访问日志太吵，调用日志已经单独打印
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="假 OneBot 11 HTTP 服务")
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--token", default="", help="要求 Bearer token；留空则不校验")
    args = parser.parse_args()

    server = FakeOneBotServer(args.port, args.token)
    print(f"Fake OneBot 已启动：{server.url}")
    print("在软件设置里填这个地址，然后点“连接并同步”。Ctrl+C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
