"""程序入口。"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import tkinter as tk
from pathlib import Path

from . import __version__
from .core.logging_setup import setup_logging
from .core.service import AppService
from .core.storage import StateStore
from .ui.main_window import MainWindow


def _enable_dpi_awareness() -> None:
    """Windows 高分屏下让 Tk 字体清晰；失败不影响启动。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


def _default_data_dir() -> Path:
    env = os.environ.get("QQ_SIGNIN_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "data"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qq_signin",
        description="QQ 群打卡与定时消息助手（OneBot 11 + NapCat）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="数据目录，默认是项目下的 data/；也可用环境变量 QQ_SIGNIN_DATA_DIR",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else _default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    logger, log_queue, log_path = setup_logging(data_dir)
    logger.info("=" * 60)
    logger.info("QQ 签到助手 v%s 启动", __version__)
    logger.info("Python %s，数据目录：%s", sys.version.split()[0], data_dir)

    _enable_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        logger.error("无法创建 Tk 窗口：%s", exc)
        print(f"无法创建窗口：{exc}", file=sys.stderr)
        return 1

    store = StateStore(data_dir)
    # 仅用于本地联调：把个人打卡指向假 QQ 打卡接口
    personal_sign_url = os.environ.get("QQ_SIGNIN_DAILY_SIGN_URL", "").strip() or None
    service = AppService(store, logger, personal_sign_url=personal_sign_url)
    service.start()

    try:
        MainWindow(root, service, store, data_dir, log_queue, log_path)

        def auto_connect_loop() -> None:
            config = store.read().config
            if config.auto_connect and not service.connected and not service.busy:
                service.connect_async()
            root.after(15000, auto_connect_loop)

        if store.read().config.auto_connect:
            root.after(900, auto_connect_loop)
        root.mainloop()
    except Exception:
        logger.exception("程序异常退出")
        raise
    finally:
        service.shutdown()
        logger.info("程序退出")
        logging.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
