"""日志配置。

界面日志和文件日志共用同一套格式。``QueueLogHandler`` 把后台线程的
日志放进队列，Tk 主线程再取出显示，避免跨线程操作控件。
"""

from __future__ import annotations

import logging
import queue
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class QueueLogHandler(logging.Handler):
    """把日志记录放入 ``queue.Queue``，供 UI 轮询。"""

    def __init__(self, log_queue: queue.Queue[tuple[str, str]]) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - 格式化失败时不要影响主流程
            self.handleError(record)
            return
        self.log_queue.put((record.levelname, message))


def setup_logging(
    data_dir: Path | str,
    level: int = logging.INFO,
) -> tuple[logging.Logger, queue.Queue[tuple[str, str]], Path]:
    """初始化日志，返回 ``(logger, ui_queue, log_path)``。"""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "app.log"

    logger = logging.getLogger("qq_signin")
    logger.setLevel(level)
    logger.propagate = False

    # 重复初始化时（例如测试）先移除旧 handler
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    queue_handler = QueueLogHandler(log_queue)
    queue_handler.setFormatter(formatter)
    logger.addHandler(queue_handler)

    return logger, log_queue, log_path
