"""账号安全限制测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from qq_signin.core.models import AppState
from qq_signin.core.safety import (
    MAX_MESSAGES_PER_DAY,
    MAX_SIGN_GROUPS_PER_DAY,
    SafetyError,
    check_pause,
    is_risk_error,
    pause_for_risk,
    reserve_messages,
    reserve_sign_groups,
)


class SafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = AppState()
        self.now = datetime(2026, 10, 28, 12, 0)

    def test_limit_targets_per_task(self) -> None:
        with self.assertRaises(SafetyError):
            reserve_messages(self.state, 21, self.now)
        reserve_messages(self.state, 20, self.now)
        self.assertEqual(self.state.safety.messages, 20)

    def test_daily_message_cap(self) -> None:
        for _ in range(MAX_MESSAGES_PER_DAY // 20):
            reserve_messages(self.state, 20, self.now)
        with self.assertRaises(SafetyError):
            reserve_messages(self.state, 1, self.now)

    def test_sign_run_and_daily_cap(self) -> None:
        with self.assertRaises(SafetyError):
            reserve_sign_groups(self.state, 21, self.now)
        reserve_sign_groups(self.state, 20, self.now)
        reserve_sign_groups(self.state, 20, self.now)
        reserve_sign_groups(self.state, 10, self.now)
        with self.assertRaises(SafetyError):
            reserve_sign_groups(self.state, 1, self.now)
        self.assertEqual(
            self.state.safety.sign_groups, MAX_SIGN_GROUPS_PER_DAY
        )

    def test_new_day_resets_counts(self) -> None:
        reserve_messages(self.state, 20, self.now)
        reserve_messages(self.state, 20, self.now)
        reserve_messages(self.state, 10, self.now)
        self.assertEqual(self.state.safety.messages, 50)
        tomorrow = self.now + timedelta(days=1)
        reserve_messages(self.state, 20, tomorrow)
        self.assertEqual(self.state.safety.messages, 20)

    def test_risk_pause(self) -> None:
        pause_for_risk(self.state, self.now, "操作频繁")
        with self.assertRaises(SafetyError):
            check_pause(self.state, self.now)
        check_pause(self.state, self.now + timedelta(hours=3))

    def test_risk_keyword_detection(self) -> None:
        self.assertTrue(is_risk_error(RuntimeError("操作频繁，请稍后再试")))
        self.assertTrue(is_risk_error(RuntimeError("too many requests")))
        self.assertFalse(is_risk_error(RuntimeError("群不存在")))


if __name__ == "__main__":
    unittest.main()
