"""JSON 状态文件读写。

设计目标：
1. 任何 UI 线程和后台线程都可以安全调用；
2. 写入采用“临时文件 + 原子替换”，避免程序崩溃把 state.json 写坏；
3. 配置文件损坏时自动备份，然后回退到默认值，不阻止程序启动。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from .models import AppState

T = TypeVar("T")
LOGGER = logging.getLogger("qq_signin.storage")


class StateStore:
    """线程安全的 ``AppState`` 容器。"""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "state.json"
        self._lock = threading.RLock()
        self._state = self._load()

    # ------------------------------------------------------------------ 读取
    def read(self) -> AppState:
        """返回深拷贝，调用方随便改，不会影响内部状态。"""
        with self._lock:
            return copy.deepcopy(self._state)

    # ------------------------------------------------------------------ 修改
    def update(self, mutator: Callable[[AppState], T]) -> T:
        """在锁内修改并立即落盘。

        ``mutator`` 应该只做内存操作，不要在里面发网络请求或睡等待，
        否则会阻塞 UI 读取状态。
        """
        with self._lock:
            result = mutator(self._state)
            self._save_locked()
            return copy.deepcopy(result)

    # ------------------------------------------------------------------ 落盘
    def _save_locked(self) -> None:
        payload = json.dumps(
            self._state.to_dict(), ensure_ascii=False, indent=2, sort_keys=False
        )
        temp_path = self.path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(payload, encoding="utf-8")
            # Windows 上杀毒软件/索引器偶尔会占用文件，重试几次比直接崩溃好
            last_error: OSError | None = None
            for attempt in range(4):
                try:
                    os.replace(temp_path, self.path)
                    return
                except OSError as exc:  # pragma: no cover - 依赖运行环境
                    last_error = exc
                    time.sleep(0.08 * (attempt + 1))
            raise last_error  # type: ignore[misc]
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ 加载
    def _load(self) -> AppState:
        if not self.path.exists():
            LOGGER.info("未找到状态文件，使用默认配置：%s", self.path)
            return AppState()
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state.json 顶层必须是 JSON 对象")
            return AppState.from_dict(raw)
        except Exception as exc:
            backup = self.data_dir / (
                "state.corrupt-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
            )
            try:
                shutil.copy2(self.path, backup)
                LOGGER.error("状态文件损坏，已备份到 %s：%s", backup, exc)
            except OSError:
                LOGGER.error("状态文件损坏且备份失败：%s", exc)
            return AppState()
