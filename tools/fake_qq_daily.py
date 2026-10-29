"""开发用假 QQ 打卡（日签卡）接口。

配合 ``tools/fake_onebot.py`` 可以完整测试个人打卡流程，不会访问腾讯接口：

    python tools/fake_qq_daily.py --port 18081

测试中通过 ``QQDailySigner(..., sign_url=FakeQQDailyServer.sign_url)`` 注入。
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeQQDailyServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int = 18081, mode: str = "ok") -> None:
        super().__init__(("127.0.0.1", port), FakeQQDailyHandler)
        self.mode = mode
        self.requests: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def sign_url(self) -> str:
        return f"{self.url}/hybrid-h5/api/json/daily_attendance/SignIn"

    def record(self, request: dict[str, Any]) -> None:
        self.requests.append(request)

    def start(self) -> "FakeQQDailyServer":
        self._thread = threading.Thread(
            target=self.serve_forever, name="fake-qq-daily", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


class FakeQQDailyHandler(BaseHTTPRequestHandler):
    server: FakeQQDailyServer

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"raw": raw.decode("utf-8", errors="replace")}
        self.server.record(
            {
                "path": self.path,
                "body": body,
                "cookie": self.headers.get("Cookie", ""),
                "user_agent": self.headers.get("User-Agent", ""),
            }
        )
        print(f"[fake-qq-daily] {json.dumps(body, ensure_ascii=False)}")

        if self.server.mode == "html":
            self._send(200, b"<html>login required</html>", "text/html")
            return
        if self.server.mode == "already":
            payload = {"retCode": 1, "ret": 1, "msg": "今日已打卡", "totalDays": 24}
        elif self.server.mode == "error":
            payload = {"retCode": -3000, "ret": -3000, "msg": "cookie invalid"}
        else:
            payload = {"retCode": 0, "ret": 0, "msg": "打卡成功", "totalDays": 25}
        self._send(
            200,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="假 QQ 打卡（日签卡）接口")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument(
        "--mode",
        choices=("ok", "already", "error", "html"),
        default="ok",
        help="模拟成功/已打卡/失败/登录页",
    )
    args = parser.parse_args()
    server = FakeQQDailyServer(args.port, args.mode)
    print(f"Fake QQ 打卡已启动：{server.sign_url}，mode={args.mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
