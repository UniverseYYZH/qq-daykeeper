"""QQ 表情消息段测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qq_signin.core.message import build_onebot_message, display_message


class MessageTests(unittest.TestCase):
    def test_plain_text_is_unchanged(self) -> None:
        self.assertEqual(build_onebot_message("早上好"), "早上好")

    def test_face_only(self) -> None:
        self.assertEqual(
            build_onebot_message("[face:12]"),
            [{"type": "face", "data": {"id": "12"}}],
        )

    def test_text_and_face(self) -> None:
        message = build_onebot_message("早上好[face:12]")
        self.assertEqual(
            message,
            [
                {"type": "text", "data": {"text": "早上好"}},
                {"type": "face", "data": {"id": "12"}},
            ],
        )

    def test_display_message(self) -> None:
        self.assertEqual(display_message("你好[face:12]"), "你好[调皮]")

    def test_image_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "small.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            message = build_onebot_message(f"[image:{path}]")
            self.assertEqual(len(message), 1)
            self.assertEqual(message[0]["type"], "image")
            self.assertTrue(message[0]["data"]["file"].startswith("file://"))
        self.assertEqual(display_message("[image:C:\\a.png]"), "[图片]")

    def test_missing_image_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_onebot_message("[image:C:\\definitely-missing.png]")


if __name__ == "__main__":
    unittest.main()
