"""执行服务测试：直接调用同步的 _do_* 方法，避免等待线程。"""

from __future__ import annotations

import queue
import time
import unittest
from collections.abc import Callable
from datetime import date
from typing import Any

from qq_signin.core.models import KIND_FRIEND, KIND_GROUP, Target, Task
from qq_signin.core.safety import SafetyError
from qq_signin.core.service import AppService
from qq_signin.core.storage import StateStore
from tests.support import make_temp_dir, remove_temp_dir
from tools.fake_onebot import FakeOneBotServer
from tools.fake_qq_daily import FakeQQDailyServer


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = make_temp_dir()
        self.addCleanup(remove_temp_dir, self.temp)
        self.server = FakeOneBotServer(0).start()
        self.addCleanup(self.server.stop)
        self.daily = FakeQQDailyServer(0).start()
        self.addCleanup(self.daily.stop)
        self.store = StateStore(self.temp)
        self.service = AppService(
            self.store, personal_sign_url=self.daily.sign_url
        )
        # 测试里不等待生产环境的 8 秒安全间隔
        self.service._send_interval = lambda: 0.01  # type: ignore[method-assign]
        self.addCleanup(self.service.shutdown)
        self.service.save_config(self.server.url, "", 0.2, "")
        self.group = Target(kind=KIND_GROUP, id=10001, name="测试群")
        self.friend = Target(kind=KIND_FRIEND, id=20001, name="小明")

    def test_connect_syncs_contacts(self) -> None:
        self.service._do_connect()
        state = self.store.read()
        self.assertEqual(len(state.contacts.groups), 3)
        self.assertEqual(len(state.contacts.friends), 3)
        self.assertTrue(self.service.connected)
        assert self.service.login_info is not None
        self.assertEqual(self.service.login_info.user_id, 10001)

    def test_task_sends_to_group_and_friend(self) -> None:
        task = Task(
            name="测试任务",
            time="09:00",
            message="hello",
            targets=[self.group, self.friend],
        )
        self.service.save_task(task)
        self.service._do_task(task.id, manual=True)

        self.assertEqual(len(self.server.calls_for("send_group_msg")), 1)
        self.assertEqual(len(self.server.calls_for("send_private_msg")), 1)
        saved = self.store.read().find_task(task.id)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertIn("成功 2", saved.last_result)
        self.assertEqual(saved.last_run, "")  # 手动执行不占用每日计划

    def test_task_with_qq_face(self) -> None:
        task = Task(
            name="表情任务",
            time="09:00",
            message="[face:12]",
            targets=[self.group],
        )
        self.service.save_task(task)
        self.service._do_task(task.id, manual=True)
        call = self.server.calls_for("send_group_msg")[0]
        self.assertEqual(
            call["params"]["message"],
            [{"type": "face", "data": {"id": "12"}}],
        )

    def test_task_with_too_many_targets_is_blocked(self) -> None:
        targets = [
            Target(kind=KIND_GROUP, id=10000 + index, name=f"群{index}")
            for index in range(21)
        ]
        task = Task(
            name="超量任务",
            time="09:00",
            message="hello",
            targets=targets,
        )
        self.service.save_task(task)
        with self.assertRaises(SafetyError):
            self.service._do_task(task.id, manual=True)
        self.assertEqual(self.server.calls_for("send_group_msg"), [])

    def test_scheduled_task_marks_last_run(self) -> None:
        task = Task(
            name="测试任务",
            time="09:00",
            message="hello",
            targets=[self.group],
        )
        self.service.save_task(task)
        self.service._do_task(task.id, manual=False)
        saved = self.store.read().find_task(task.id)
        assert saved is not None
        self.assertNotEqual(saved.last_run, "")

    def test_checkin(self) -> None:
        self.service.save_checkin_config(True, "08:05", [self.group])
        self.service._do_checkin(None, manual=True)
        self.assertEqual(len(self.server.calls_for("send_group_sign")), 1)
        config = self.store.read().config
        self.assertIn("成功 1", config.group_checkin.last_result)
        self.assertEqual(config.group_checkin.last_run, "")

    def test_personal_sign(self) -> None:
        self.service.save_personal_sign_config(True, "08:10")
        self.service._do_personal_sign(manual=True)
        self.assertEqual(len(self.daily.requests), 1)
        request = self.daily.requests[0]
        self.assertEqual(request["body"]["uin"], 10001)
        config = self.store.read().config
        self.assertIn("打卡成功", config.personal_sign.last_result)
        self.assertEqual(config.personal_sign.last_run, "")

    def test_personal_sign_error_is_recorded(self) -> None:
        self.daily.mode = "error"
        self.service.save_personal_sign_config(True, "08:10")
        self.service._do_personal_sign(manual=True)
        config = self.store.read().config
        self.assertIn("失败", config.personal_sign.last_result)
        self.assertIn("-3000", config.personal_sign.last_result)

    def test_spark_sends_reminder_when_no_incoming(self) -> None:
        self.server.history = []
        self.service.save_spark_config(True, [self.friend], ["火花提醒"])
        self.service._do_spark_check(manual=True)
        calls = self.server.calls_for("send_private_msg")
        self.assertEqual(len(calls), 1)
        config = self.store.read().config.spark
        self.assertIn("未续上", config.last_result)
        self.assertEqual(
            config.statuses[self.friend.key].reminders_today, 1
        )
        self.assertTrue(config.next_check_at)

    def test_spark_stops_when_contact_sent_today(self) -> None:
        self.server.history = [
            {
                "time": int(time.time()),
                "sender": {"user_id": self.friend.id},
            }
        ]
        self.service.save_spark_config(True, [self.friend], ["火花提醒"])
        self.service._do_spark_check(manual=True)
        self.assertEqual(self.server.calls_for("send_private_msg"), [])
        config = self.store.read().config.spark
        self.assertEqual(
            config.statuses[self.friend.key].confirmed_date,
            date.today().isoformat(),
        )

    def test_spark_checks_three_friends(self) -> None:
        self.server.history = []
        second = Target(kind=KIND_FRIEND, id=20002, name="小红")
        third = Target(kind=KIND_FRIEND, id=20003, name="小刚")
        self.service.save_spark_config(
            True, [self.friend, second, third], ["火花提醒"]
        )
        self.service._do_spark_check(manual=True)
        self.assertEqual(len(self.server.calls_for("send_private_msg")), 3)
        config = self.store.read().config.spark
        self.assertEqual(len(config.friends), 3)
        for friend in (self.friend, second, third):
            self.assertEqual(
                config.statuses[friend.key].reminders_today, 1
            )

    def test_remote_query_and_toggle(self) -> None:
        self.service.save_checkin_config(True, "08:05", [self.group])
        self.service._do_remote_command("query", 10001)
        replies = self.server.calls_for("send_private_msg")
        self.assertEqual(len(replies), 1)
        self.assertIn("状态", replies[0]["params"]["message"])

        self.service._do_remote_command("checkin_off", 10001)
        self.assertFalse(self.store.read().config.group_checkin.enabled)
        self.assertIn(
            "群打卡：已关闭",
            self.server.calls_for("send_private_msg")[-1]["params"]["message"],
        )

    def test_task_validation(self) -> None:
        task = Task(name="", time="99:99", message="", targets=[])
        with self.assertRaises(ValueError):
            self.service.save_task(task)

    def test_async_worker_connect_and_send(self) -> None:
        self.service.start()
        self.service.connect_async()
        event = self._wait_event(
            lambda e: e.get("type") == "connection" and e.get("ok"), timeout=5
        )
        self.assertEqual(event["groups"], 3)
        self.assertEqual(len(self.store.read().contacts.groups), 3)

        task = Task(
            name="异步测试",
            time="09:00",
            message="async",
            targets=[self.group],
        )
        self.service.save_task(task)
        self.service.run_task_async(task.id)
        self._wait_event(
            lambda e: e.get("type") == "toast" and "异步测试" in str(e.get("message")),
            timeout=5,
        )
        self.assertEqual(len(self.server.calls_for("send_group_msg")), 1)

    def _wait_event(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.service.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if predicate(event):
                return event
        self.fail("等待服务事件超时")

    def test_reset_today_marks(self) -> None:
        task = Task(
            name="测试",
            time="09:00",
            message="x",
            targets=[self.group],
            last_run=date.today().isoformat(),
        )
        self.service.save_task(task)
        count = self.service.reset_today_marks()
        self.assertGreaterEqual(count, 1)
        saved = self.store.read().find_task(task.id)
        assert saved is not None
        self.assertEqual(saved.last_run, "")


if __name__ == "__main__":
    unittest.main()
