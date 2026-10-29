"""调度器测试：不启动线程，直接测“到点判断”这个纯逻辑。"""

from __future__ import annotations

import unittest
from datetime import datetime

from qq_signin.core.models import (
    KIND_GROUP,
    AppState,
    AutoSignConfig,
    Target,
    Task,
)
from qq_signin.core.scheduler import collect_due_jobs

NOW = datetime(2026, 10, 27, 8, 0, 0)


def make_task(**kwargs: object) -> Task:
    data = {
        "name": "任务",
        "time": "08:00",
        "message": "hello",
        "targets": [Target(kind=KIND_GROUP, id=1, name="群")],
    }
    data.update(kwargs)
    return Task(**data)  # type: ignore[arg-type]


class SchedulerTests(unittest.TestCase):
    def test_daily_task_due_once(self) -> None:
        state = AppState(tasks=[make_task()])
        due = collect_due_jobs(state, NOW)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].kind, "task")
        self.assertEqual(state.tasks[0].last_run, "2026-10-27")
        self.assertEqual(collect_due_jobs(state, NOW), [])

    def test_wrong_minute(self) -> None:
        state = AppState(tasks=[make_task(time="08:01")])
        self.assertEqual(collect_due_jobs(state, NOW), [])

    def test_disabled(self) -> None:
        state = AppState(tasks=[make_task(enabled=False)])
        self.assertEqual(collect_due_jobs(state, NOW), [])

    def test_once_wrong_date(self) -> None:
        state = AppState(tasks=[make_task(repeat="once", date="2026-10-28")])
        self.assertEqual(collect_due_jobs(state, NOW), [])

    def test_once_right_date(self) -> None:
        state = AppState(tasks=[make_task(repeat="once", date="2026-10-27")])
        self.assertEqual(len(collect_due_jobs(state, NOW)), 1)

    def test_checkin(self) -> None:
        group = Target(kind=KIND_GROUP, id=1, name="群")
        state = AppState()
        state.config.group_checkin = AutoSignConfig(
            enabled=True, time="08:00", groups=[group]
        )
        due = collect_due_jobs(state, NOW)
        self.assertEqual([d.kind for d in due], ["checkin"])
        self.assertEqual(
            state.config.group_checkin.last_run, "2026-10-27"
        )
        self.assertEqual(collect_due_jobs(state, NOW), [])

    def test_checkin_disabled_without_groups(self) -> None:
        state = AppState()
        state.config.group_checkin = AutoSignConfig(
            enabled=True, time="08:00", groups=[]
        )
        self.assertEqual(collect_due_jobs(state, NOW), [])

    def test_personal_sign_due(self) -> None:
        state = AppState()
        state.config.personal_sign.enabled = True
        state.config.personal_sign.time = "08:00"
        due = collect_due_jobs(state, NOW)
        self.assertEqual([d.kind for d in due], ["personal"])
        self.assertEqual(state.config.personal_sign.last_run, "2026-10-27")
        self.assertEqual(collect_due_jobs(state, NOW), [])


if __name__ == "__main__":
    unittest.main()
