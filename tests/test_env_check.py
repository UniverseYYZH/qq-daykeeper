"""运行环境检查测试。"""

from __future__ import annotations

import unittest

from qq_signin.core.env_check import (
    check_local,
    check_onebot,
    format_results,
    has_failure,
)
from tests.support import make_temp_dir, remove_temp_dir
from tools.fake_onebot import FakeOneBotServer


class EnvCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = make_temp_dir()
        self.addCleanup(remove_temp_dir, self.temp)
        self.server = FakeOneBotServer(0).start()
        self.addCleanup(self.server.stop)

    def test_local_checks_pass(self) -> None:
        results = check_local(self.temp)
        self.assertFalse(has_failure(results))
        self.assertTrue(any(item.name == "Tkinter" and item.ok for item in results))
        self.assertTrue(any(item.name == "数据目录可写" and item.ok for item in results))

    def test_onebot_checks(self) -> None:
        results = check_onebot(self.server.url, timeout=5)
        self.assertFalse(has_failure(results))
        text = format_results(results)
        self.assertIn("测试账号", text)
        self.assertIn("3 个群", text)
        self.assertIn("p_skey", text)

    def test_onebot_failure(self) -> None:
        results = check_onebot("http://127.0.0.1:1", timeout=1)
        self.assertTrue(has_failure(results))
        self.assertIn("OneBot 连接", format_results(results))


if __name__ == "__main__":
    unittest.main()
