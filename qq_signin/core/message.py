"""消息段构建。

QQ 的内置“小黄脸”不是普通文字。复制到剪贴板时经常会变成 ``n`` 之类的
单字符，直接发送只会发一个字母。正确做法是发送 OneBot 的 ``face`` 消息段：

    {"type": "face", "data": {"id": "12"}}

为了让用户仍然可以在文本框里编辑，约定用 ``[face:12]`` 表示调皮表情。
这个模块把带标记的文本转换成 OneBot 消息数组。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 常用 QQ 内置表情；第一个“调皮”就是截图里吐舌头的那个。
QQ_FACES: tuple[tuple[int, str], ...] = (
    (12, "调皮"),
    (13, "呲牙"),
    (14, "惊讶"),
    (20, "偷笑"),
    (21, "可爱"),
    (34, "晕"),
    (324, "吃糖（超级）"),
    (27, "流汗"),
    (28, "憨笑"),
    (42, "鼓掌"),
    (63, "玫瑰"),
    (66, "爱心"),
    (76, "太阳"),
    (78, "拥抱"),
    (79, "强"),
    (89, "OK"),
    (90, "爱情"),
    (91, "飞吻"),
)

FACE_NAMES = {face_id: name for face_id, name in QQ_FACES}
FACE_TOKEN_RE = re.compile(r"\[face:(\d{1,4})\]")
TOKEN_RE = re.compile(r"\[face:(\d{1,4})\]|\[image:([^\]]+)\]")


def build_onebot_message(text: str) -> str | list[dict[str, Any]]:
    """把 ``[face:12]`` / ``[image:path]`` 转换成 OneBot 消息段数组。

    没有标记时返回原始字符串，保持和旧版本完全一致。
    """
    if not TOKEN_RE.search(text):
        return text

    message: list[dict[str, Any]] = []
    position = 0
    for match in TOKEN_RE.finditer(text):
        if match.start() > position:
            message.append(
                {"type": "text", "data": {"text": text[position : match.start()]}}
            )
        face_id = match.group(1)
        image_path = match.group(2)
        if face_id:
            message.append({"type": "face", "data": {"id": face_id}})
        elif image_path:
            path = Path(image_path).expanduser()
            if not path.is_file():
                raise ValueError(f"图片不存在：{image_path}")
            message.append(
                {"type": "image", "data": {"file": path.resolve().as_uri()}}
            )
        position = match.end()
    if position < len(text):
        message.append({"type": "text", "data": {"text": text[position:]}})
    return message or text


def display_message(text: str) -> str:
    """把 ``[face:12]`` 换成 ``[调皮]``，把图片换成 ``[图片]``。"""
    text = FACE_TOKEN_RE.sub(
        lambda match: f"[{FACE_NAMES.get(int(match.group(1)), '表情')}]",
        text,
    )
    return re.sub(r"\[image:[^\]]+\]", "[图片]", text)
