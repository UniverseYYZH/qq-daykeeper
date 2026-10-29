"""QQ 打卡（日签卡）测试。"""

from __future__ import annotations

import unittest

from qq_signin.core.onebot import OneBotClient
from qq_signin.core.qq_daily import (
    PersonalSignError,
    QQDailySigner,
    cookie_uin,
    cookie_value,
)
from tools.fake_onebot import FakeOneBotServer
from tools.fake_qq_daily import FakeQQDailyServer


class CookieTests(unittest.TestCase):
    def test_cookie_value_and_uin(self) -> None:
        cookies = "uin=o10001; p_skey=@abc; skey=xyz"
        self.assertEqual(cookie_value(cookies, "p_skey"), "@abc")
        self.assertEqual(cookie_uin(cookies), "10001")

    def test_quoted_cookie(self) -> None:
        self.assertEqual(
            cookie_value('p_skey="abc%40def"', "p_skey"), "abc@def"
        )


class QQDailySignerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.onebot = FakeOneBotServer(0).start()
        self.addCleanup(self.onebot.stop)
        self.daily = FakeQQDailyServer(0).start()
        self.addCleanup(self.daily.stop)
        self.client = OneBotClient(self.onebot.url, timeout=5)

    def _signer(self) -> QQDailySigner:
        return QQDailySigner(self.client, sign_url=self.daily.sign_url)

    def test_sign_success(self) -> None:
        result = self._signer().sign(fallback_uin=10001)
        self.assertTrue(result.success)
        self.assertFalse(result.already)
        self.assertEqual(result.code, 0)
        self.assertEqual(result.days, 25)
        request = self.daily.requests[0]
        self.assertEqual(request["body"]["uin"], 10001)
        self.assertEqual(request["body"]["type"], 1)
        self.assertEqual(request["body"]["sId"], "")
        self.assertIn("p_skey=@fake_p_skey", request["cookie"])
        self.assertIn("mpExtend", request["body"])

    def test_already_signed_is_success(self) -> None:
        self.daily.mode = "already"
        result = self._signer().sign()
        self.assertTrue(result.success)
        self.assertTrue(result.already)
        self.assertEqual(result.code, 1)

    def test_error_code_raises(self) -> None:
        self.daily.mode = "error"
        with self.assertRaises(PersonalSignError) as ctx:
            self._signer().sign()
        self.assertIn("-3000", str(ctx.exception))

    def test_html_login_page_raises(self) -> None:
        self.daily.mode = "html"
        with self.assertRaises(PersonalSignError) as ctx:
            self._signer().sign()
        self.assertIn("登录页", str(ctx.exception))

    def test_missing_p_skey(self) -> None:
        class StubOneBot:
            def get_cookies(self, domain: str) -> dict[str, str]:
                return {"cookies": "uin=o10001; skey=xyz"}

        with self.assertRaises(PersonalSignError) as ctx:
            QQDailySigner(StubOneBot()).sign()  # type: ignore[arg-type]
        self.assertIn("p_skey", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
