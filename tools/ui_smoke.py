"""界面冒烟测试：创建窗口、切换所有页面、销毁，不进入主循环。

    python -m tools.ui_smoke

它不会连接 NapCat，只验证 Tk 控件和布局能否正常创建。
"""

from __future__ import annotations

import queue
import shutil
import tkinter as tk
from pathlib import Path

from qq_signin.core.models import KIND_GROUP, Target
from qq_signin.core.service import AppService
from qq_signin.core.storage import StateStore
from qq_signin.ui.dialogs import TaskDialog
from qq_signin.ui.main_window import MainWindow


def main() -> None:
    data_dir = Path(".tmp") / "ui-smoke"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    root = tk.Tk()
    store = StateStore(data_dir)
    service = AppService(store)
    log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    window = MainWindow(
        root,
        service,
        store,
        data_dir,
        log_queue,
        data_dir / "app.log",
    )

    for key in window.pages:
        window.show_page(key)
        root.update_idletasks()
        root.update()

    # 个人签到页保存配置（不发起网络请求）
    window.personal_tab.enabled_var.set(True)
    window.personal_tab.time_var.set("08:10")
    window.personal_tab._save()
    assert store.read().config.personal_sign.enabled
    print("PersonalSignTab OK")

    # 打开一次任务对话框并直接保存，验证校验逻辑和控件创建
    group = Target(kind=KIND_GROUP, id=10001, name="测试群")
    dialog = TaskDialog(root, [group], preset_targets=[group])
    root.update_idletasks()
    root.update()
    dialog.message_text.insert("1.0", "冒烟测试")
    dialog._insert_face()
    dialog._save()
    assert dialog.result is not None
    assert "[face:12]" in dialog.result.message
    print("TaskDialog OK:", dialog.result.name, dialog.result.time)

    root.update_idletasks()
    print("UI smoke OK:", root.winfo_width(), "x", root.winfo_height())
    print("pages:", list(window.pages))
    service.shutdown()
    root.destroy()
    shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
