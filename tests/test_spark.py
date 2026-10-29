"""火花守护时间与历史判断测试。"""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta

from qq_signin.core.models import (
    KIND_FRIEND,
    KIND_GROUP,
    SparkWatchConfig,
    Target,
)
from qq_signin.core.spark import (
    contact_sent_today,
    compute_next_check,
    latest_message_time,
)

NOW = datetime(2026, 10, 28, 18, 0)


class SparkScheduleTests(unittest.TestCase):
    def test_first_check_between_1845_and_1915(self) -> None:
        result = compute_next_check(NOW, rng=random.Random(1))
        self.assertGreaterEqual(result, datetime(2026, 10, 28, 18, 45))
        self.assertLessEqual(result, datetime(2026, 10, 28, 19, 15))

    def test_late_random_uses_2350(self) -> None:
        result = compute_next_check(
            datetime(2026, 10, 28, 22, 50),
            rng=random.Random(4),
        )
        self.assertEqual(result, datetime(2026, 10, 28, 23, 50))

    def test_after_final_schedules_tomorrow(self) -> None:
        result = compute_next_check(
            datetime(2026, 10, 28, 23, 55),
            rng=random.Random(1),
        )
        self.assertEqual(result.date(), datetime(2026, 10, 29).date())
        self.assertGreaterEqual(result.time(), datetime(2026, 10, 29, 18, 45).time())

    def test_min_gap(self) -> None:
        last = datetime(2026, 10, 28, 21, 0)
        result = compute_next_check(
            datetime(2026, 10, 28, 21, 1),
            last_check_at=last,
            rng=random.Random(1),
        )
        self.assertGreaterEqual(result, last + timedelta(minutes=30))


class SparkHistoryTests(unittest.TestCase):
    def test_incoming_today(self) -> None:
        now = datetime(2026, 10, 28, 20, 0)
        messages = [
            {
                "time": int(datetime(2026, 10, 28, 19, 0).timestamp()),
                "sender": {"user_id": 20001},
            },
            {
                "time": int(datetime(2026, 10, 28, 18, 0).timestamp()),
                "sender": {"user_id": 10001},
            },
        ]
        self.assertTrue(contact_sent_today(messages, 20001, now))
        self.assertFalse(contact_sent_today(messages, 30001, now))
        self.assertEqual(
            latest_message_time(messages, 20001),
            datetime(2026, 10, 28, 19, 0),
        )

    def test_yesterday_does_not_count(self) -> None:
        now = datetime(2026, 10, 28, 20, 0)
        messages = [
            {
                "time": int(datetime(2026, 10, 27, 23, 0).timestamp()),
                "sender": {"user_id": 20001},
            }
        ]
        self.assertFalse(contact_sent_today(messages, 20001, now))


class SparkConfigTests(unittest.TestCase):
    def test_validate_requires_friend_and_message(self) -> None:
        config = SparkWatchConfig(enabled=True, friends=[], messages=[])
        errors = config.validate()
        self.assertTrue(any("好友" in e for e in errors))
        self.assertTrue(any("文案" in e for e in errors))

    def test_only_friend_allowed(self) -> None:
        with self.assertRaises(ValueError):
            SparkWatchConfig(
                friends=[Target(kind=KIND_GROUP, id=1, name="群")]
            )

    def test_at_most_three_friends(self) -> None:
        friends = [
            Target(kind=KIND_FRIEND, id=20000 + index, name=f"好友{index}")
            for index in range(4)
        ]
        config = SparkWatchConfig(enabled=True, friends=friends)
        self.assertEqual(len(config.friends), 3)


if __name__ == "__main__":
    unittest.main()
