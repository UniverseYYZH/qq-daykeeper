"""持久化测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from qq_signin.core.models import KIND_GROUP, Target, Task
from qq_signin.core.storage import StateStore
from tests.support import make_temp_dir, remove_temp_dir


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = make_temp_dir()
        self.addCleanup(remove_temp_dir, self.temp)

    def test_roundtrip(self) -> None:
        store = StateStore(self.temp)
        task = Task(
            name="测试",
            time="09:00",
            message="hello",
            targets=[Target(kind=KIND_GROUP, id=1, name="群")],
        )
        store.update(lambda state: state.tasks.append(task))
        self.assertTrue((self.temp / "state.json").exists())

        reloaded = StateStore(self.temp).read()
        self.assertEqual(len(reloaded.tasks), 1)
        self.assertEqual(reloaded.tasks[0].message, "hello")

    def test_corrupt_file_is_backed_up(self) -> None:
        path = self.temp / "state.json"
        path.write_text("{ not valid json", encoding="utf-8")
        store = StateStore(self.temp)
        self.assertEqual(store.read().tasks, [])
        backups = list(self.temp.glob("state.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn("not valid json", backups[0].read_text(encoding="utf-8"))

    def test_read_is_a_deep_copy(self) -> None:
        store = StateStore(self.temp)
        snapshot = store.read()
        snapshot.tasks.append(Task(name="x", time="10:00", message="x", targets=[]))
        self.assertEqual(store.read().tasks, [])

    def test_json_is_human_readable(self) -> None:
        store = StateStore(self.temp)
        store.update(lambda state: setattr(state.config, "token", "abc"))
        raw = json.loads((self.temp / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["config"]["token"], "abc")


if __name__ == "__main__":
    unittest.main()
