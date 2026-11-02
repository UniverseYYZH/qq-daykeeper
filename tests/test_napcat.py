"""NapCat 配置读取/写入测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qq_signin.core.napcat import (
    NapCatConfig,
    has_remote_client,
    launcher_path,
    quick_login_accounts,
    write_remote_client,
)


class NapCatConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "NapCat.Shell"
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        (root / "launcher.bat").write_text("@echo off\n", encoding="utf-8")
        config_dir = root / "config"
        config_dir.mkdir(parents=True)
        self.path = config_dir / "onebot11_123456.json"
        self.path.write_text(
            json.dumps(
                {
                    "network": {
                        "httpServers": [
                            {
                                "enable": True,
                                "host": "127.0.0.1",
                                "port": 3000,
                                "token": "secret",
                            }
                        ],
                        "httpClients": [],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.config = NapCatConfig(
            path=self.path,
            url="http://127.0.0.1:3000",
            token="secret",
            host="127.0.0.1",
            port=3000,
            server_name="test",
            enabled=True,
        )

    def test_quick_login_helpers(self) -> None:
        self.assertEqual(quick_login_accounts(self.config), ["123456"])
        self.assertEqual(
            launcher_path(self.config), self.root / "launcher.bat"
        )

    def test_write_remote_client(self) -> None:
        url = "http://127.0.0.1:18080/event"
        self.assertFalse(has_remote_client(self.config, url))
        backup = write_remote_client(self.config, url)
        self.assertTrue(backup.is_file())
        self.assertTrue(has_remote_client(self.config, url))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        client = raw["network"]["httpClients"][0]
        self.assertEqual(client["url"], url)
        self.assertTrue(client["reportSelfMessage"])

    def test_write_is_idempotent(self) -> None:
        url = "http://127.0.0.1:18080/event"
        write_remote_client(self.config, url)
        write_remote_client(self.config, url)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["network"]["httpClients"]), 1)


if __name__ == "__main__":
    unittest.main()
