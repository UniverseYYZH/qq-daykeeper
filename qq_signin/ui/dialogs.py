"""新建/编辑定时消息任务的对话框。"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Iterable

from ..core.message import QQ_FACES
from ..core.napcat import find_qq_emoji_image
from ..core.models import (
    REPEAT_DAILY,
    REPEAT_ONCE,
    Target,
    Task,
)
from ..core.safety import MAX_TARGETS_PER_TASK
from .widgets import TargetPicker

REPEAT_TO_TEXT = {REPEAT_DAILY: "每天", REPEAT_ONCE: "仅一次"}
TEXT_TO_REPEAT = {v: k for k, v in REPEAT_TO_TEXT.items()}


class TaskDialog(tk.Toplevel):
    """编辑一条任务；关闭后从 ``result`` 取结果（取消为 None）。"""

    def __init__(
        self,
        parent: tk.Misc,
        all_targets: Iterable[Target],
        task: Task | None = None,
        preset_targets: Iterable[Target] | None = None,
    ) -> None:
        super().__init__(parent)
        self.result: Task | None = None
        self._task = task
        self._all_targets = list(all_targets)

        self.title("编辑任务" if task else "新建任务")
        screen_w = parent.winfo_screenwidth()
        screen_h = parent.winfo_screenheight()
        width = min(860, max(760, screen_w - 80))
        height = min(760, max(620, screen_h - 120))
        self.geometry(f"{width}x{height}")
        self.minsize(740, 600)
        self.transient(parent)
        self.resizable(True, True)

        self.name_var = tk.StringVar(value=task.name if task else "新任务")
        self.time_var = tk.StringVar(
            value=task.time if task else _default_time()
        )
        self.repeat_var = tk.StringVar(
            value=REPEAT_TO_TEXT.get(task.repeat, "每天") if task else "每天"
        )
        self.date_var = tk.StringVar(
            value=task.date if task and task.date else datetime.now().date().isoformat()
        )
        self.enabled_var = tk.BooleanVar(value=task.enabled if task else True)

        self._build()
        self._fill_targets(preset_targets)
        self._on_repeat_changed()

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-Return>", lambda _e: self._save())

        self.update_idletasks()
        self._center_over_parent(parent)
        self.grab_set()
        self.name_entry.focus_set()

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=3)
        container.rowconfigure(3, weight=2)

        basic = ttk.Frame(container)
        basic.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        basic.columnconfigure(1, weight=1)

        ttk.Label(basic, text="任务名称").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.name_entry = ttk.Entry(basic, textvariable=self.name_var)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Checkbutton(
            basic, text="启用", variable=self.enabled_var
        ).grid(row=0, column=2, sticky=tk.E)

        timing = ttk.Frame(container)
        timing.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(timing, text="执行时间").pack(side=tk.LEFT)
        ttk.Entry(timing, textvariable=self.time_var, width=8).pack(
            side=tk.LEFT, padx=(6, 16)
        )
        ttk.Label(timing, text="重复").pack(side=tk.LEFT)
        self.repeat_box = ttk.Combobox(
            timing,
            textvariable=self.repeat_var,
            values=("每天", "仅一次"),
            state="readonly",
            width=8,
        )
        self.repeat_box.pack(side=tk.LEFT, padx=(6, 16))
        self.repeat_box.bind("<<ComboboxSelected>>", self._on_repeat_changed)

        ttk.Label(timing, text="日期").pack(side=tk.LEFT)
        self.date_entry = ttk.Entry(timing, textvariable=self.date_var, width=13)
        self.date_entry.pack(side=tk.LEFT, padx=(6, 8))
        ttk.Label(timing, text="（仅一次时生效，YYYY-MM-DD）").pack(side=tk.LEFT)

        target_frame = ttk.LabelFrame(
            container,
            text=f"发送目标（最多 {MAX_TARGETS_PER_TASK} 个；单击行勾选，可搜索）",
            padding=6,
        )
        target_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        target_frame.rowconfigure(0, weight=1)
        target_frame.columnconfigure(0, weight=1)
        self.picker = TargetPicker(target_frame, show_type=True, height=10)
        self.picker.grid(row=0, column=0, sticky="nsew")

        message_frame = ttk.LabelFrame(container, text="发送文案", padding=6)
        message_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        message_frame.rowconfigure(1, weight=1)
        message_frame.columnconfigure(0, weight=1)

        emoji_bar = ttk.Frame(message_frame)
        emoji_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(emoji_bar, text="QQ 普通表情").pack(side=tk.LEFT)
        self._face_map = {
            f"{name} ({face_id})": face_id for face_id, name in QQ_FACES
        }
        self.face_var = tk.StringVar(value="调皮 (12)")
        self.face_box = ttk.Combobox(
            emoji_bar,
            textvariable=self.face_var,
            values=list(self._face_map),
            state="readonly",
            width=20,
        )
        self.face_box.pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(
            emoji_bar, text="插入", width=6, command=self._insert_face
        ).pack(side=tk.LEFT)
        ttk.Button(
            emoji_bar,
            text="插入小版图片",
            width=12,
            command=self._insert_small_image,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(
            emoji_bar,
            text="  超级表情只支持图片，普通表情用“插入”",
            foreground="#667085",
        ).pack(side=tk.LEFT, padx=(6, 0))

        self.message_text = tk.Text(
            message_frame,
            height=6,
            wrap=tk.WORD,
            undo=True,
            font=("Microsoft YaHei UI", 10),
        )
        message_scroll = ttk.Scrollbar(
            message_frame, orient=tk.VERTICAL, command=self.message_text.yview
        )
        self.message_text.configure(yscrollcommand=message_scroll.set)
        self.message_text.grid(row=1, column=0, sticky="nsew")
        message_scroll.grid(row=1, column=1, sticky="ns")
        if self._task:
            self.message_text.insert("1.0", self._task.message)

        buttons = ttk.Frame(container)
        buttons.grid(row=4, column=0, sticky="e")
        ttk.Button(buttons, text="取消", width=10, command=self._cancel).pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        ttk.Button(buttons, text="保存", width=10, command=self._save).pack(
            side=tk.RIGHT
        )
        ttk.Label(
            buttons,
            text="Ctrl+Enter 保存",
            foreground="#667085",
        ).pack(side=tk.LEFT, padx=(0, 12))

    def _fill_targets(self, preset_targets: Iterable[Target] | None) -> None:
        self.picker.set_targets(self._all_targets)
        if self._task is not None:
            self.picker.set_checked_targets(self._task.targets)
        elif preset_targets is not None:
            self.picker.set_checked_targets(preset_targets)

    # ------------------------------------------------------------------ 事件
    def _on_repeat_changed(self, _event: object | None = None) -> None:
        is_once = TEXT_TO_REPEAT.get(self.repeat_var.get(), REPEAT_DAILY) == REPEAT_ONCE
        self.date_entry.configure(state=tk.NORMAL if is_once else tk.DISABLED)

    def _insert_face(self) -> None:
        face_id = self._face_map.get(self.face_var.get(), 12)
        if face_id == 324:
            messagebox.showinfo(
                "这是超级表情",
                "“吃糖”在 QQ 里是超级表情，用 face 发送会是大版。\n\n"
                "要小版请点“插入小版图片”。",
                parent=self,
            )
            return
        self.message_text.insert(tk.INSERT, f"[face:{face_id}]")
        self.message_text.focus_set()

    def _insert_small_image(self) -> None:
        face_id = self._face_map.get(self.face_var.get(), 12)
        image = find_qq_emoji_image(face_id)
        if image is None:
            selected = filedialog.askopenfilename(
                parent=self,
                title="选择要发送的小版表情图片",
                filetypes=[
                    ("图片", "*.png *.jpg *.jpeg *.gif *.webp"),
                    ("所有文件", "*.*"),
                ],
            )
            if not selected:
                return
            image = Path(selected)
        self.message_text.insert(tk.INSERT, f"[image:{image}]")
        self.message_text.focus_set()

    def _save(self) -> None:
        repeat = TEXT_TO_REPEAT.get(self.repeat_var.get(), REPEAT_DAILY)
        targets = self.picker.get_checked_targets()
        if len(targets) > MAX_TARGETS_PER_TASK:
            messagebox.showerror(
                "目标太多",
                f"安全限制：单个任务最多 {MAX_TARGETS_PER_TASK} 个目标，"
                f"当前选了 {len(targets)} 个。\n\n请拆成多个任务，降低账号风险。",
                parent=self,
            )
            return
        task = Task(
            id=self._task.id if self._task else "",
            name=self.name_var.get(),
            time=self.time_var.get(),
            message=self.message_text.get("1.0", "end-1c"),
            targets=targets,
            repeat=repeat,
            date=self.date_var.get() if repeat == REPEAT_ONCE else "",
            enabled=self.enabled_var.get(),
            last_run=self._task.last_run if self._task else "",
            last_result=self._task.last_result if self._task else "",
            created_at=self._task.created_at if self._task else "",
        )
        errors = task.validate()
        if errors:
            messagebox.showerror("无法保存", "\n".join(f"• {e}" for e in errors), parent=self)
            return
        self.result = task
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _center_over_parent(self, parent: tk.Misc) -> None:
        try:
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            parent_w = parent.winfo_width()
            parent_h = parent.winfo_height()
            width = self.winfo_width()
            height = self.winfo_height()
            x = max(0, parent_x + (parent_w - width) // 2)
            y = max(0, parent_y + (parent_h - height) // 2)
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass


def _default_time() -> str:
    """默认给当前时间 +5 分钟，方便快速新建。"""
    return (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
