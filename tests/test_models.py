"""数据模型测试。"""

from __future__ import annotations

import unittest

from qq_signin.core.models import (
    KIND_FRIEND,
    KIND_GROUP,
    AppState,
    AutoSignConfig,
    Target,
    Task,
    normalize_time,
)


class TargetTests(unittest.TestCase):
    def test_key_and_display(self) -> None:
        target = Target(kind=KIND_GROUP, id=123, name="测试群")
        self.assertEqual(target.key, "group:123")
        self.assertIn("测试群", target.display)

    def test_reject_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            Target(kind="channel", id=1, name="x")

    def test_reject_bad_id(self) -> None:
        with self.assertRaises(ValueError):
            Target(kind=KIND_FRIEND, id=0, name="x")


class TimeTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize_time("8:05"), "08:05")
        self.assertEqual(normalize_time("23:59"), "23:59")

    def test_invalid(self) -> None:
        for value in ("24:00", "12:60", "abc", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_time(value)


class TaskTests(unittest.TestCase):
    def _task(self, **kwargs: object) -> Task:
        data = {
            "name": "早安",
            "time": "8:00",
            "message": "早上好",
            "targets": [Target(kind=KIND_GROUP, id=1, name="群")],
        }
        data.update(kwargs)
        return Task(**data)  # type: ignore[arg-type]

    def test_time_is_normalized_and_targets_deduped(self) -> None:
        task = self._task(
            targets=[
                Target(kind=KIND_GROUP, id=1, name="群"),
                Target(kind=KIND_GROUP, id=1, name="群"),
            ]
        )
        self.assertEqual(task.time, "08:00")
        self.assertEqual(len(task.targets), 1)

    def test_validate_ok(self) -> None:
        self.assertEqual(self._task().validate(), [])

    def test_validate_empty_message(self) -> None:
        errors = self._task(message="  ").validate()
        self.assertTrue(any("文案" in e for e in errors))

    def test_validate_once_needs_date(self) -> None:
        errors = self._task(repeat="once", date="").validate()
        self.assertTrue(any("日期" in e for e in errors))
        self.assertFalse(self._task(repeat="once", date="2026-10-27").validate())

    def test_validate_bad_time(self) -> None:
        errors = self._task(time="25:00").validate()
        self.assertTrue(any("时间" in e for e in errors))

    def test_roundtrip(self) -> None:
        task = self._task(repeat="once", date="2026-10-27")
        restored = Task.from_dict(task.to_dict())
        self.assertEqual(restored.id, task.id)
        self.assertEqual(restored.time, "08:00")
        self.assertEqual(restored.targets, task.targets)

    def test_appstate_roundtrip(self) -> None:
        state = AppState()
        state.tasks.append(self._task())
        restored = AppState.from_dict(state.to_dict())
        self.assertEqual(len(restored.tasks), 1)
        self.assertEqual(restored.tasks[0].name, "早安")

    def test_sign_config_filters_friends(self) -> None:
        config = AutoSignConfig(
            enabled=True,
            time="8:00",
            groups=[
                Target(kind=KIND_GROUP, id=1, name="群"),
                Target(kind=KIND_FRIEND, id=2, name="好友"),
            ],
        )
        self.assertEqual(len(config.groups), 1)
        self.assertEqual(config.time, "08:00")
        self.assertEqual(config.validate(), [])

    def test_personal_sign_config_roundtrip(self) -> None:
        state = AppState()
        state.config.personal_sign.enabled = True
        state.config.personal_sign.time = "8:10"
        restored = AppState.from_dict(state.to_dict())
        self.assertTrue(restored.config.personal_sign.enabled)
        self.assertEqual(restored.config.personal_sign.time, "08:10")

    def test_old_state_without_personal_sign_uses_default(self) -> None:
        raw = AppState().to_dict()
        raw["config"].pop("personal_sign")
        restored = AppState.from_dict(raw)
        self.assertFalse(restored.config.personal_sign.enabled)
        self.assertEqual(restored.config.personal_sign.time, "08:10")

    def test_safety_stats_roundtrip(self) -> None:
        state = AppState()
        state.safety.date = "2026-10-28"
        state.safety.messages = 12
        state.safety.sign_groups = 3
        restored = AppState.from_dict(state.to_dict())
        self.assertEqual(restored.safety.messages, 12)
        self.assertEqual(restored.safety.sign_groups, 3)

    def test_send_interval_is_clamped_up(self) -> None:
        from qq_signin.core.models import AppConfig

        self.assertEqual(AppConfig(send_interval=1.5).send_interval, 5.0)
        self.assertEqual(AppConfig(send_interval=100).send_interval, 60.0)


if __name__ == "__main__":
    unittest.main()
