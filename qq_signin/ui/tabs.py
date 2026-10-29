"""主窗口里的各个标签页。

界面代码刻意保持直白：每个 Tab 只做“展示 + 收集用户输入 + 调 service”，
不在这里发网络请求。这样调试时可以先看 service 日志，再定位 UI 问题。
"""

from __future__ import annotations

import os
import socket
import subprocess
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Iterable

from ..core.env_check import check_local, format_results
from ..core.message import display_message
from ..core.models import (
    KIND_FRIEND,
    KIND_GROUP,
    AppConfig,
    AutoSignConfig,
    DEFAULT_SPARK_MESSAGES,
    LoginInfo,
    Target,
    Task,
)
from ..core.napcat import (
    best_config,
    find_onebot_configs,
    has_remote_client,
    write_remote_client,
)
from ..core.safety import MAX_SIGN_GROUPS_PER_RUN
from ..core.service import AppService
from ..core.storage import StateStore
from .theme import (
    ACCENT,
    BG,
    BORDER,
    CARD,
    DANGER,
    FONT,
    INFO_BG,
    MUTED,
    SUCCESS,
    TEXT,
)
from .widgets import ReadOnlyText, TargetPicker

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow


class TasksTab(ttk.Frame):
    """定时消息任务列表。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=10)
        self.app = app
        self.service: AppService = app.service
        self.store: StateStore = app.store
        self._build()

    def _build(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Button(toolbar, text="新建任务", command=self._new_task).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(toolbar, text="编辑", command=self._edit_task).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(toolbar, text="删除", command=self._delete_task).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(toolbar, text="启用/停用", command=self._toggle_task).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(toolbar, text="立即执行", command=self._run_task).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Label(
            toolbar,
            text="每天到点自动发送；双击任务可编辑",
            foreground="#667085",
        ).pack(side=tk.RIGHT)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        columns = (
            "enabled",
            "name",
            "time",
            "repeat",
            "targets",
            "last_run",
            "last_result",
            "message",
        )
        self.tree = ttk.Treeview(
            body, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "enabled": "状态",
            "name": "名称",
            "time": "时间",
            "repeat": "重复",
            "targets": "目标",
            "last_run": "最近自动执行",
            "last_result": "最近结果",
            "message": "文案",
        }
        widths = {
            "enabled": 50,
            "name": 150,
            "time": 62,
            "repeat": 112,
            "targets": 82,
            "last_run": 95,
            "last_result": 185,
            "message": 330,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            anchor = tk.CENTER if column in {"enabled", "time", "targets", "last_run"} else tk.W
            self.tree.column(
                column,
                width=widths[column],
                minwidth=45 if column == "enabled" else 60,
                anchor=anchor,
                stretch=(column == "message"),
            )
        self.tree.tag_configure("disabled", foreground="#98a2b3")

        vscroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview)
        hscroll = ttk.Scrollbar(body, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(
            yscrollcommand=vscroll.set,
            xscrollcommand=hscroll.set,
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")

        self.tree.bind("<Double-1>", lambda _e: self._edit_task())

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        state = self.store.read()
        for task in sorted(state.tasks, key=lambda t: (t.time, t.name.casefold())):
            values = (
                "✓" if task.enabled else "✗",
                task.name,
                task.time,
                task.repeat_label,
                task.target_summary,
                task.last_run or "—",
                task.last_result or "—",
                _one_line(task.message, 48),
            )
            tags = () if task.enabled else ("disabled",)
            self.tree.insert("", tk.END, iid=task.id, values=values, tags=tags)
        existing = [iid for iid in selected if self.tree.exists(iid)]
        if existing:
            self.tree.selection_set(existing)

    # ------------------------------------------------------------------ 操作
    def _new_task(self) -> None:
        contacts = self.store.read().contacts
        if not contacts.all:
            messagebox.showinfo(
                "没有联系人",
                "请先连接 NapCat 并点击“刷新联系人”，同步到群聊/好友后再新建任务。",
                parent=self,
            )
            return
        self.app.open_task_dialog()

    def _edit_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        self.app.open_task_dialog(task=task)

    def _delete_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        if not messagebox.askyesno(
            "删除任务",
            f"确定删除任务“{task.name}”吗？此操作不可撤销。",
            parent=self,
        ):
            return
        self.service.delete_task(task.id)
        self.refresh()

    def _toggle_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        self.service.set_task_enabled(task.id, not task.enabled)
        self.refresh()

    def _run_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        preview = _one_line(task.message, 80)
        if not messagebox.askyesno(
            "立即执行",
            f"将立即向 {len(task.targets)} 个目标发送：\n\n{preview}\n\n"
            "手动执行不会改变“最近自动执行”日期，定时计划仍会照常执行。",
            parent=self,
        ):
            return
        self.service.run_task_async(task.id)
        self.app.set_status(f"已加入队列：{task.name}", "info")

    def _selected_task(self) -> Task | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("请选择任务", "先在列表里选中一条任务。", parent=self)
            return None
        return self.store.read().find_task(selection[0])


class ContactsTab(ttk.Frame):
    """联系人浏览与勾选，可把勾选结果带入新建任务。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=10)
        self.app = app
        self.service: AppService = app.service
        self.store: StateStore = app.store
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(top, text="刷新联系人", command=self._refresh).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(
            top, text="用选中项新建任务", command=self._new_task_from_selection
        ).pack(side=tk.LEFT, padx=4)
        self.info_var = tk.StringVar(value="尚未同步")
        ttk.Label(top, textvariable=self.info_var, foreground="#667085").pack(
            side=tk.RIGHT
        )

        ttk.Label(
            self,
            text="单击行勾选目标；这里勾选的目标用于“新建任务”。群打卡在对应标签页单独选择。",
            foreground="#667085",
        ).pack(anchor=tk.W, pady=(0, 6))

        self.picker = TargetPicker(self, show_type=True, height=18)
        self.picker.pack(fill=tk.BOTH, expand=True)

    def reload(self, preserve: bool = True) -> None:
        contacts = self.store.read().contacts
        self.picker.set_targets(contacts.all, preserve=preserve)
        updated = contacts.updated_at.replace("T", " ") if contacts.updated_at else "从未"
        self.info_var.set(
            f"群 {len(contacts.groups)} 个 / 好友 {len(contacts.friends)} 个 · 更新于 {updated}"
        )

    def _refresh(self) -> None:
        self.service.refresh_contacts_async()
        self.app.set_status("正在刷新联系人…", "info")

    def _new_task_from_selection(self) -> None:
        targets = self.picker.get_checked_targets()
        if not targets:
            messagebox.showinfo("未选择目标", "先勾选至少一个群聊或好友。", parent=self)
            return
        self.app.open_task_dialog(preset_targets=targets)


class CheckinPanel(ttk.LabelFrame):
    """群打卡面板。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, text="群打卡", padding=8)
        self.app = app
        self.service: AppService = app.service
        self.store: StateStore = app.store
        self.title_text = "群打卡"
        self._all_groups: list[Target] = []

        self.enabled_var = tk.BooleanVar(value=False)
        self.time_var = tk.StringVar(value="08:05")
        self.status_var = tk.StringVar(value="尚未执行")
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Checkbutton(
            header,
            text="启用每日自动执行",
            variable=self.enabled_var,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=(FONT, 9),
            bd=0,
            highlightthickness=0,
        ).pack(side=tk.LEFT)
        ttk.Label(header, text="时间").pack(side=tk.LEFT, padx=(16, 4))
        ttk.Entry(header, textvariable=self.time_var, width=8).pack(side=tk.LEFT)
        ttk.Label(header, text="HH:MM").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            header, text="OneBot: send_group_sign", foreground="#667085"
        ).pack(side=tk.RIGHT)

        ttk.Label(
            self,
            text="选择要执行操作的群（单击勾选）：",
            foreground="#667085",
        ).grid(row=1, column=0, sticky=tk.W, pady=(2, 4))

        self.picker = TargetPicker(self, show_type=False, height=9)
        self.picker.grid(row=2, column=0, sticky="nsew")

        footer = ttk.Frame(self)
        footer.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(
            footer,
            textvariable=self.status_var,
            foreground="#475467",
            wraplength=420,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(footer, text="立即执行", command=self._run_now).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(footer, text="保存设置", command=self._save).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ 数据
    def load_config(self) -> None:
        state = self.store.read()
        config = self._config(state.config)
        self.enabled_var.set(config.enabled)
        self.time_var.set(config.time)
        self.picker.set_targets(
            self._all_groups,
            checked_keys=[g.key for g in config.groups],
            preserve=False,
        )
        self.refresh_status()

    def set_groups(self, groups: Iterable[Target]) -> None:
        self._all_groups = [g for g in groups if g.kind == KIND_GROUP]
        # 保留用户已经勾选的群；联系人刷新后不会把选择清掉
        self.picker.set_targets(self._all_groups, preserve=True)

    def refresh_status(self) -> None:
        config = self._config(self.store.read().config)
        self.status_var.set(config.last_result or "尚未执行")

    def _config(self, config: AppConfig) -> AutoSignConfig:
        return config.group_checkin

    # ------------------------------------------------------------------ 操作
    def _save(self) -> None:
        groups = self.picker.get_checked_targets()
        if len(groups) > MAX_SIGN_GROUPS_PER_RUN:
            messagebox.showerror(
                "群太多",
                f"安全限制：单次最多操作 {MAX_SIGN_GROUPS_PER_RUN} 个群，"
                f"当前选了 {len(groups)} 个。\n\n请分成多天或减少群，降低账号风险。",
                parent=self,
            )
            return
        try:
            self.service.save_checkin_config(
                self.enabled_var.get(),
                self.time_var.get(),
                groups,
            )
        except ValueError as exc:
            messagebox.showerror("无法保存", str(exc), parent=self)
            return
        self.refresh_status()
        self.app.set_status(f"{self.title_text}设置已保存", "info")

    def _run_now(self) -> None:
        groups = self.picker.get_checked_targets()
        if not groups:
            messagebox.showinfo("未选择群", "先勾选至少一个群。", parent=self)
            return
        if len(groups) > MAX_SIGN_GROUPS_PER_RUN:
            messagebox.showerror(
                "群太多",
                f"安全限制：单次最多操作 {MAX_SIGN_GROUPS_PER_RUN} 个群，"
                f"当前选了 {len(groups)} 个。",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "立即执行",
            f"将对 {len(groups)} 个群执行“{self.title_text}”。\n\n"
            "手动执行不会改变每日自动执行标记。",
            parent=self,
        ):
            return
        self.service.run_checkin_async(groups, manual=True)
        self.app.set_status(f"{self.title_text}已加入队列", "info")


class CheckinTab(ttk.Frame):
    """群打卡。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=10)
        self.app = app
        # 面板先放进一个专用容器，避免和顶部说明标签混用 pack/grid
        self._panels = ttk.Frame(self)
        self.checkin_panel = CheckinPanel(self._panels, app)
        self._build()

    def _build(self) -> None:
        ttk.Label(
            self,
            text="群打卡就是群聊 + → 打卡。安全限制：单次最多 20 个群，"
            "每日最多 50 个群。保存设置后到点自动执行，建议先手动执行一次确认。",
            foreground="#667085",
            wraplength=1040,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        self._panels.pack(fill=tk.BOTH, expand=True)
        self._panels.columnconfigure(0, weight=1)
        self._panels.rowconfigure(0, weight=1)
        self.checkin_panel.grid(row=0, column=0, sticky="nsew")

    def load_config(self) -> None:
        self.checkin_panel.load_config()

    def set_groups(self, groups: Iterable[Target]) -> None:
        self.checkin_panel.set_groups(groups)

    def refresh_status(self) -> None:
        self.checkin_panel.refresh_status()


class PersonalSignTab(ttk.Frame):
    """QQ 打卡（日签卡）。

    OneBot 没有 QQ 打卡接口，这里通过 NapCat 的 ``get_cookies`` 获取
    ``ti.qq.com`` Cookie，再调用手机 QQ 的 ``daily_attendance/SignIn``。
    """

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=0)
        self.app = app
        self.service: AppService = app.service
        self.store: StateStore = app.store

        self.enabled_var = tk.BooleanVar(value=False)
        self.time_var = tk.StringVar(value="08:10")
        self.status_var = tk.StringVar(value="尚未执行")
        self.detail_var = tk.StringVar(value="启用后会在每天指定时间自动打卡。")
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        hero = tk.Frame(
            self,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        hero.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(0, weight=1)

        top = tk.Frame(hero, bg=CARD)
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        top.columnconfigure(0, weight=1)

        title_row = tk.Frame(top, bg=CARD)
        title_row.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_row,
            text="☀",
            bg=CARD,
            fg=ACCENT,
            font=(FONT, 24),
        ).pack(side=tk.LEFT, padx=(0, 10))
        title_box = tk.Frame(title_row, bg=CARD)
        title_box.pack(side=tk.LEFT)
        tk.Label(
            title_box,
            text="QQ 打卡（日签卡）",
            bg=CARD,
            fg=TEXT,
            font=(FONT, 15, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            title_box,
            text="通过 NapCat 获取 ti.qq.com Cookie，点击手机 QQ 的“日签卡”。",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor=tk.W, pady=(3, 0))
        tk.Label(
            title_row,
            text="日签卡",
            bg="#EAF2FF",
            fg=ACCENT,
            font=(FONT, 8, "bold"),
            padx=8,
            pady=3,
        ).pack(side=tk.LEFT, padx=(14, 0), pady=(4, 0))

        status_box = tk.Frame(hero, bg=CARD)
        status_box.grid(row=1, column=0, sticky="ew", padx=24, pady=(8, 0))
        status_box.columnconfigure(0, weight=1)
        tk.Label(
            status_box,
            text="打卡状态",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).grid(row=0, column=0, sticky="w")
        self.status_label = tk.Label(
            status_box,
            textvariable=self.status_var,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 13, "bold"),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=760,
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        tk.Label(
            status_box,
            textvariable=self.detail_var,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=760,
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))

        controls = tk.Frame(hero, bg=CARD)
        controls.grid(row=2, column=0, sticky="ew", padx=24, pady=(16, 18))
        controls.columnconfigure(3, weight=1)

        tk.Checkbutton(
            controls,
            text="启用每日自动执行",
            variable=self.enabled_var,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=(FONT, 10),
            bd=0,
            highlightthickness=0,
            anchor=tk.W,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            controls, text="时间", bg=CARD, fg=MUTED, font=(FONT, 9)
        ).grid(row=0, column=1, padx=(18, 4))
        ttk.Entry(controls, textvariable=self.time_var, width=8).grid(
            row=0, column=2, sticky="w"
        )
        tk.Label(
            controls, text="HH:MM", bg=CARD, fg=MUTED, font=(FONT, 8)
        ).grid(row=0, column=3, sticky="w", padx=(4, 0))

        ttk.Button(
            controls,
            text="保存设置",
            command=self._save,
        ).grid(row=0, column=4, padx=(10, 0))
        ttk.Button(
            controls,
            text="立即打卡",
            style="Accent.TButton",
            command=self._run_now,
        ).grid(row=0, column=5, padx=(6, 0))

        note = tk.Frame(hero, bg=INFO_BG)
        note.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 22))
        tk.Label(
            note,
            text=(
                "前置条件：NapCat 已登录；ti.qq.com Cookie 有效。"
                "第一次使用请先在手机 QQ 点左上角“已打卡”进入“QQ打卡”页面，"
                "确认能正常显示日签卡。"
            ),
            bg=INFO_BG,
            fg=TEXT,
            font=(FONT, 9),
            justify=tk.LEFT,
            wraplength=790,
        ).pack(side=tk.LEFT, padx=12, pady=10)
        ttk.Button(
            note,
            text="打开 QQ 打卡页",
            command=lambda: webbrowser.open(
                "https://ti.qq.com/signin/public/index.html"
            ),
        ).pack(side=tk.RIGHT, padx=10, pady=8)

        help_card = tk.Frame(
            self,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        help_card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        tk.Label(
            help_card,
            text="它是怎么工作的？",
            bg=CARD,
            fg=TEXT,
            font=(FONT, 11, "bold"),
        ).pack(anchor=tk.W, padx=24, pady=(18, 6))
        tk.Label(
            help_card,
            text=(
                "1. NapCat 的 get_cookies 接口读取当前 QQ 在 ti.qq.com 的 p_skey；\n"
                "2. 调用 daily_attendance/SignIn，提交 type=1（日签卡）；\n"
                "3. 返回 retCode=0 表示打卡成功，retCode=1 表示今天已经打过卡。\n"
                "这是手机 QQ 网页接口，不是腾讯官方 API，接口变化或风控时会在日志里显示原因。"
            ),
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            justify=tk.LEFT,
            wraplength=860,
        ).pack(anchor=tk.W, padx=24, pady=(0, 20))

    # ------------------------------------------------------------------ 数据
    def load_config(self) -> None:
        config = self.store.read().config.personal_sign
        self.enabled_var.set(config.enabled)
        self.time_var.set(config.time)
        self.refresh_status()

    def refresh_status(self) -> None:
        config = self.store.read().config.personal_sign
        result = config.last_result.strip()
        if not result:
            self.status_var.set("尚未执行")
            self.status_label.configure(fg=MUTED)
            self.detail_var.set("启用后会在每天指定时间自动打卡。")
            return

        self.status_var.set(result)
        if "失败" in result or "异常" in result:
            self.status_label.configure(fg=DANGER)
        elif "成功" in result or "已打卡" in result:
            self.status_label.configure(fg=SUCCESS)
        else:
            self.status_label.configure(fg=MUTED)

        if config.last_run:
            self.detail_var.set(f"最近自动执行：{config.last_run}")
        else:
            self.detail_var.set("最近一次为手动执行，不影响每日计划。")

    # ------------------------------------------------------------------ 操作
    def _save(self) -> None:
        try:
            self.service.save_personal_sign_config(
                self.enabled_var.get(),
                self.time_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("无法保存", str(exc), parent=self)
            return
        self.refresh_status()
        self.app.set_status("QQ 打卡设置已保存", "info")

    def _run_now(self) -> None:
        if not messagebox.askyesno(
            "立即打卡",
            "将通过当前 NapCat 登录的 QQ 执行一次 QQ 打卡（日签卡）。\n\n"
            "如果今天已经打卡过，接口通常会返回“已打卡”，不会重复奖励。",
            parent=self,
        ):
            return
        self.service.run_personal_sign_async()
        self.app.set_status("QQ 打卡已加入队列", "info")


class SparkTab(ttk.Frame):
    """好友火花守护。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=10)
        self.app = app
        self.service: AppService = app.service
        self.store: StateStore = app.store
        self._all_friends: list[Target] = []
        self.enabled_var = tk.BooleanVar(value=False)
        self.next_var = tk.StringVar(value="尚未安排")
        self.result_var = tk.StringVar(value="尚未检查")
        self.reminder_var = tk.StringVar(value="今日提醒：0/3")
        self.preset_vars: dict[str, tk.BooleanVar] = {}
        self._build()

    def _build(self) -> None:
        ttk.Label(
            self,
            text=(
                "每天 18:00 后随机每 45–75 分钟检查一次；23:20 后不再插入随机检查，"
                "23:50 做最终检查。与上一次检查尽量间隔 30 分钟以上。"
                "最多选 3 个好友；已续上的好友当天停止检查，"
                "未续上才随机提醒，每个人每天最多 3 条。"
            ),
            foreground="#667085",
            wraplength=1040,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=4)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="检查对象（最多 3 个好友）", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        tk.Checkbutton(
            left,
            text="启用火花守护",
            variable=self.enabled_var,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=(FONT, 10, "bold"),
            bd=0,
            highlightthickness=0,
            anchor=tk.W,
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.picker = TargetPicker(
            left,
            show_type=False,
            height=10,
            id_heading="QQ号",
            name_heading="好友",
        )
        self.picker.grid(row=1, column=0, sticky="nsew")
        tk.Label(
            left,
            textvariable=self.next_var,
            bg=BG,
            fg=ACCENT,
            font=(FONT, 9, "bold"),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(row=2, column=0, sticky="w", pady=(8, 2))
        tk.Label(
            left,
            textvariable=self.reminder_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor=tk.W,
        ).grid(row=3, column=0, sticky="w")
        tk.Label(
            left,
            textvariable=self.result_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=360,
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))

        right = ttk.LabelFrame(body, text="提醒文案（勾选后随机发送）", padding=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=1)

        presets = tk.Frame(right, bg=BG)
        presets.grid(row=0, column=0, sticky="ew")
        for index, message in enumerate(DEFAULT_SPARK_MESSAGES):
            var = tk.BooleanVar(value=True)
            self.preset_vars[message] = var
            tk.Checkbutton(
                presets,
                text=message,
                variable=var,
                bg=BG,
                fg=TEXT,
                activebackground=BG,
                activeforeground=TEXT,
                selectcolor=CARD,
                font=(FONT, 9),
                bd=0,
                highlightthickness=0,
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=480,
            ).grid(row=index, column=0, sticky="w", pady=1)

        tk.Label(
            right,
            text="自定义文案（每行一条，会和勾选的预设一起随机）：",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor=tk.W,
        ).grid(row=2, column=0, sticky="w", pady=(8, 4))
        custom_frame = ttk.Frame(right)
        custom_frame.grid(row=3, column=0, sticky="nsew")
        custom_frame.rowconfigure(0, weight=1)
        custom_frame.columnconfigure(0, weight=1)
        self.custom_text = tk.Text(
            custom_frame,
            height=5,
            wrap=tk.WORD,
            undo=True,
            font=("Microsoft YaHei UI", 9),
        )
        custom_scroll = ttk.Scrollbar(
            custom_frame, orient=tk.VERTICAL, command=self.custom_text.yview
        )
        self.custom_text.configure(yscrollcommand=custom_scroll.set)
        self.custom_text.grid(row=0, column=0, sticky="nsew")
        custom_scroll.grid(row=0, column=1, sticky="ns")
        tk.Label(
            right,
            text="提示：提醒也会占用每日消息额度，默认每天最多 3 条。",
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor=tk.W,
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="保存设置", command=self._save).pack(
            side=tk.RIGHT
        )
        ttk.Button(
            buttons, text="立即检查", style="Accent.TButton", command=self._run_now
        ).pack(side=tk.RIGHT, padx=(0, 6))

    # ------------------------------------------------------------------ 数据
    def load_config(self) -> None:
        config = self.store.read().config.spark
        self.enabled_var.set(config.enabled)
        self.picker.set_targets(
            self._all_friends,
            checked_keys=[friend.key for friend in config.friends],
            preserve=False,
        )
        for message, var in self.preset_vars.items():
            var.set(message in config.messages)
        custom = [m for m in config.messages if m not in DEFAULT_SPARK_MESSAGES]
        self.custom_text.delete("1.0", tk.END)
        self.custom_text.insert("1.0", "\n".join(custom))
        self.refresh_status()

    def set_friends(self, friends: Iterable[Target]) -> None:
        self._all_friends = [f for f in friends if f.kind == KIND_FRIEND]
        self.picker.set_targets(self._all_friends, preserve=True)

    def refresh_status(self) -> None:
        config = self.store.read().config.spark
        today = datetime.now().date().isoformat()
        selected = len(config.friends)
        if not config.enabled:
            self.next_var.set("火花守护未启用")
        elif selected == 0:
            self.next_var.set("火花守护未选择好友")
        elif not config.pending(today):
            self.next_var.set("今日全部已续上，已停止检查")
        elif config.next_check_at:
            self.next_var.set(
                "下次检查：" + config.next_check_at.replace("T", " ")[:16]
            )
        else:
            self.next_var.set("等待安排下一次检查")
        lines: list[str] = []
        for friend in config.friends:
            state = config.statuses.get(friend.key)
            if state is None:
                lines.append(f"{friend.name}：尚未检查")
            elif state.confirmed_date == today:
                lines.append(f"{friend.name}：已续上")
            else:
                lines.append(
                    f"{friend.name}：未确认，今日提醒 "
                    f"{state.reminders_today}/3"
                )
        self.result_var.set("\n".join(lines) if lines else "尚未检查")
        self.reminder_var.set(f"已选 {selected}/3 人")

    # ------------------------------------------------------------------ 操作
    def _collect_messages(self) -> list[str]:
        messages: list[str] = []
        for message, var in self.preset_vars.items():
            if var.get():
                messages.append(message)
        for line in self.custom_text.get("1.0", "end-1c").splitlines():
            text = line.strip()
            if text and text not in messages:
                messages.append(text)
        return messages

    def _save(self) -> None:
        targets = self.picker.get_checked_targets()
        if not 1 <= len(targets) <= 3:
            messagebox.showinfo(
                "选择好友",
                "火花守护最多选择 3 个好友，请选择 1–3 个。",
                parent=self,
            )
            return
        messages = self._collect_messages()
        if not messages:
            messagebox.showinfo(
                "没有文案",
                "至少勾选一条预设，或写一条自定义提醒。",
                parent=self,
            )
            return
        try:
            self.service.save_spark_config(
                self.enabled_var.get(), targets, messages
            )
        except ValueError as exc:
            messagebox.showerror("无法保存", str(exc), parent=self)
            return
        self.refresh_status()
        self.app.set_status("火花守护设置已保存", "info")

    def _run_now(self) -> None:
        targets = self.picker.get_checked_targets()
        if not 1 <= len(targets) <= 3:
            messagebox.showinfo(
                "选择好友", "先勾选 1–3 个好友。", parent=self
            )
            return
        names = "、".join(target.name for target in targets)
        if not messagebox.askyesno(
            "立即检查",
            f"现在检查 {names} 今天有没有发消息？\n\n"
            "未续上的好友会按当前文案随机发送一条提醒。",
            parent=self,
        ):
            return
        self.service.run_spark_check_async()
        self.app.set_status("火花检查已加入队列", "info")


class RemoteTab(ttk.Frame):
    """手机 QQ 控制说明与监听状态。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=10)
        self.app = app
        self.service: AppService = app.service
        self.store: StateStore = app.store
        self.status_var = tk.StringVar(value="未启动")
        self.address_var = tk.StringVar(value="")
        self._build()

    def _build(self) -> None:
        ttk.Label(
            self,
            text=(
                "在手机 QQ 里给自己发送纯文本“菜单”，即可开关模块和查询状态。"
                "电脑上的本软件和 NapCat 必须保持运行。"
            ),
            foreground="#667085",
            wraplength=1040,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        status = ttk.LabelFrame(self, text="监听状态", padding=10)
        status.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            status,
            textvariable=self.status_var,
            bg=CARD,
            fg=SUCCESS,
            font=(FONT, 10, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X)
        row = tk.Frame(status, bg=CARD)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row, text="上报地址：").pack(side=tk.LEFT)
        ttk.Entry(
            row, textvariable=self.address_var, state="readonly", width=48
        ).pack(side=tk.LEFT)
        ttk.Button(row, text="复制", command=self._copy).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(
            status,
            text="一键写入 NapCat 上报配置",
            command=self._write_remote_config,
        ).pack(anchor=tk.W, pady=(8, 0))
        tk.Label(
            status,
            text="写入前请先退出 NapCat；写入后重启 NapCat 生效。",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 8),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(4, 0))

        steps = ttk.LabelFrame(self, text="NapCat 设置（只做一次）", padding=10)
        steps.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        text = (
            "1. 打开 NapCat WebUI（默认 http://127.0.0.1:6099/webui）。\n"
            "2. 进入“网络配置” → “HTTP 客户端” → 添加。\n"
            "3. 上报地址填下面这个地址，消息格式选 array。\n"
            "4. 打开“上报自身消息”（reportSelfMessage）——不开这个，"
            "手机发给自己的消息不会上报。\n"
            "5. Token 留空；保存后重启 NapCat。\n"
            "6. 在手机 QQ 里给自己发送“菜单”。\n\n"
            "命令：\n"
            "  菜单 / 帮助\n"
            "  查询 / 状态\n"
            "  任务开 / 任务关\n"
            "  群打卡开 / 群打卡关\n"
            "  火花开 / 火花关\n"
            "  QQ打卡开 / QQ打卡关\n"
            "  全部开 / 全部关"
        )
        tk.Label(
            steps,
            text=text,
            bg=CARD,
            fg=TEXT,
            font=("Microsoft YaHei UI", 9),
            justify=tk.LEFT,
            anchor=tk.NW,
            wraplength=980,
        ).pack(fill=tk.BOTH, expand=True)

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.address_var.get())
        self.app.set_status("上报地址已复制", "info")

    def _write_remote_config(self) -> None:
        config = best_config()
        if config is None:
            messagebox.showinfo(
                "没有找到 NapCat",
                "先启动一次 NapCat，或先到设置页点“一键检测并填入”。",
                parent=self,
            )
            return
        url = self.address_var.get().strip()
        already = has_remote_client(config, url)
        if not messagebox.askyesno(
            "写入 NapCat 配置",
            f"将修改：\n{config.path}\n\n"
            f"写入上报地址：{url}\n"
            "并打开 reportSelfMessage。\n\n"
            "请先退出 NapCat，否则它退出时可能覆盖修改。\n"
            f"{'已有相同配置，会更新。' if already else '当前没有相同配置。'}\n\n"
            "继续？",
            parent=self,
        ):
            return
        try:
            backup = write_remote_client(config, url)
        except (OSError, ValueError) as exc:
            messagebox.showerror("写入失败", str(exc), parent=self)
            return
        messagebox.showinfo(
            "已写入",
            f"已写入 NapCat 配置。\n\n备份：\n{backup}\n\n"
            "现在重启 NapCat，然后在手机 QQ 给自己发“菜单”。",
            parent=self,
        )

    def refresh_status(self) -> None:
        config = self.store.read().config.remote
        port = config.port
        self.address_var.set(f"http://127.0.0.1:{port}/event")
        if config.enabled:
            self.status_var.set(self.service.remote_status)
        else:
            self.status_var.set("未启用（可在 data/state.json 的 remote.enabled 打开）")


class SettingsTab(ttk.Frame):
    """连接参数与维护操作。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=14)
        self.app = app
        self.service: AppService = app.service
        self.store: StateStore = app.store

        self.url_var = tk.StringVar()
        self.token_var = tk.StringVar()
        self.webui_var = tk.StringVar()
        self.interval_var = tk.StringVar()
        self.napcat_config_var = tk.StringVar()
        self.napcat_info_var = tk.StringVar(value="尚未检测 NapCat 配置")
        self.auto_connect_var = tk.BooleanVar(value=True)
        self.show_token_var = tk.BooleanVar(value=False)
        self.account_var = tk.StringVar(value="未连接")
        self._build()

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="OneBot API 地址").grid(
            row=0, column=0, sticky=tk.W, pady=6, padx=(0, 10)
        )
        ttk.Entry(self, textvariable=self.url_var).grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Label(
            self,
            text="NapCat 的 HTTP 服务根地址，例如 http://127.0.0.1:3000",
            foreground="#667085",
        ).grid(row=1, column=1, sticky=tk.W, pady=(0, 8))

        ttk.Label(self, text="Access Token").grid(
            row=2, column=0, sticky=tk.W, pady=6, padx=(0, 10)
        )
        token_row = ttk.Frame(self)
        token_row.grid(row=2, column=1, sticky="ew", pady=6)
        token_row.columnconfigure(0, weight=1)
        self.token_entry = ttk.Entry(
            token_row, textvariable=self.token_var, show="*"
        )
        self.token_entry.grid(row=0, column=0, sticky="ew")
        tk.Checkbutton(
            token_row,
            text="显示",
            variable=self.show_token_var,
            command=self._toggle_token_visibility,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=(FONT, 9),
            bd=0,
            highlightthickness=0,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            self,
            text="与 NapCat 的 HTTP 服务 token 一致；没有设置则留空。token 会以明文保存在 data/state.json。",
            foreground="#667085",
        ).grid(row=3, column=1, sticky=tk.W, pady=(0, 8))

        ttk.Label(self, text="发送间隔（秒）").grid(
            row=4, column=0, sticky=tk.W, pady=6, padx=(0, 10)
        )
        ttk.Spinbox(
            self,
            from_=5.0,
            to=60.0,
            increment=1.0,
            textvariable=self.interval_var,
            width=8,
        ).grid(row=4, column=1, sticky=tk.W, pady=6)
        ttk.Label(
            self,
            text=(
                "建议 8–15 秒，最低 5 秒。安全限制：单任务≤20 个目标，"
                "每日≤100 条消息、≤50 个群打卡。"
            ),
            foreground="#667085",
            wraplength=760,
            justify=tk.LEFT,
        ).grid(row=5, column=1, sticky=tk.W, pady=(0, 8))

        ttk.Label(self, text="NapCat WebUI").grid(
            row=6, column=0, sticky=tk.W, pady=6, padx=(0, 10)
        )
        webui_row = ttk.Frame(self)
        webui_row.grid(row=6, column=1, sticky="ew", pady=6)
        webui_row.columnconfigure(0, weight=1)
        ttk.Entry(webui_row, textvariable=self.webui_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(
            webui_row, text="打开 WebUI", command=self._open_webui
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            self,
            text="用于扫码登录 QQ（NapCat 的 WebUI 地址，默认 http://127.0.0.1:6099）。",
            foreground="#667085",
        ).grid(row=7, column=1, sticky=tk.W, pady=(0, 12))

        napcat_box = ttk.LabelFrame(self, text="NapCat 配置（自动检测）", padding=8)
        napcat_box.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        napcat_box.columnconfigure(0, weight=1)
        ttk.Label(
            napcat_box,
            textvariable=self.napcat_info_var,
            foreground="#667085",
            wraplength=780,
            justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Button(
            napcat_box, text="一键检测并填入", command=self._detect_napcat
        ).grid(row=1, column=0, sticky="w")
        ttk.Button(
            napcat_box, text="打开 NapCat 目录", command=self._open_napcat_dir
        ).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Button(
            napcat_box, text="打开 OneBot 配置", command=self._open_onebot_config
        ).grid(row=1, column=2, sticky="w")
        tk.Checkbutton(
            napcat_box,
            text="启动软件时自动连接（不必每次点“连接并同步”）",
            variable=self.auto_connect_var,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=(FONT, 9),
            bd=0,
            highlightthickness=0,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(self)
        buttons.grid(row=9, column=0, columnspan=2, sticky=tk.W, pady=(8, 12))
        ttk.Button(buttons, text="保存设置", command=self._save).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(buttons, text="测试连接", command=self._test).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(buttons, text="刷新联系人", command=self._refresh).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(buttons, text="打开数据目录", command=self._open_data).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(
            buttons, text="运行环境检查", command=self._check_env
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            buttons, text="重置今日执行标记", command=self._reset_marks
        ).pack(side=tk.LEFT, padx=6)

        ttk.Separator(self, orient=tk.HORIZONTAL).grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=10
        )
        ttk.Label(self, text="当前账号").grid(
            row=11, column=0, sticky=tk.W, padx=(0, 10)
        )
        ttk.Label(self, textvariable=self.account_var).grid(
            row=11, column=1, sticky=tk.W
        )

        note = (
            "说明：本工具通过第三方协议端 NapCatQQ 的 OneBot 接口操作 QQ，"
            "不是腾讯官方 API。频繁发送、批量打卡可能触发风控，建议使用小号，"
            "并遵守 QQ 用户协议。群打卡和 QQ 打卡（日签卡）都是非官方接口，"
            "接口或风控变化时可能失败；QQ 会员/签到中心等其他签到未包含。"
            "安全限制已开启：最低 5 秒间隔、单任务 20 个目标、"
            "每日 100 条消息/50 个群打卡，风控自动暂停 2 小时。"
        )
        ttk.Label(
            self,
            text=note,
            foreground="#667085",
            wraplength=820,
            justify=tk.LEFT,
        ).grid(row=12, column=0, columnspan=2, sticky="ew", pady=(14, 0))

    # ------------------------------------------------------------------ 数据
    def load(self) -> None:
        config = self.store.read().config
        self.url_var.set(config.onebot_url)
        self.token_var.set(config.token)
        self.webui_var.set(config.napcat_webui)
        self.interval_var.set(f"{config.send_interval:g}")
        self.napcat_config_var.set(config.napcat_config)
        self.auto_connect_var.set(config.auto_connect)
        if config.napcat_config:
            self.napcat_info_var.set(f"已保存配置：{config.napcat_config}")
        else:
            self.napcat_info_var.set("尚未检测 NapCat 配置，点下方按钮自动读取")
        self.update_account(self.service.login_info)

    def update_account(self, info: LoginInfo | None) -> None:
        self.account_var.set(info.display if info else "未连接")

    def _toggle_token_visibility(self) -> None:
        self.token_entry.configure(show="" if self.show_token_var.get() else "*")

    def _save(self) -> bool:
        try:
            interval = float(self.interval_var.get())
            self.service.save_config(
                self.url_var.get(),
                self.token_var.get(),
                interval,
                self.webui_var.get(),
                napcat_config=self.napcat_config_var.get(),
                auto_connect=self.auto_connect_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("无法保存", str(exc), parent=self)
            return False
        self.app.set_status("连接设置已保存", "info")
        return True

    # ------------------------------------------------------------------ 操作
    def _test(self) -> None:
        if not self._save():
            return
        self.service.test_connection_async()
        self.app.set_status("正在测试连接…", "info")

    def _refresh(self) -> None:
        if not self._save():
            return
        self.service.refresh_contacts_async()
        self.app.set_status("正在刷新联系人…", "info")

    def _open_data(self) -> None:
        try:
            os.startfile(self.app.data_dir)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self)

    def _open_webui(self) -> None:
        url = self.webui_var.get().strip()
        if not url:
            messagebox.showinfo("未配置", "先填写 NapCat WebUI 地址。", parent=self)
            return
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        webbrowser.open(url)

    def _detect_napcat(self) -> None:
        configs = find_onebot_configs()
        if not configs:
            messagebox.showinfo(
                "没有找到 NapCat",
                "没有在常见目录找到 NapCat 的 config\\onebot11_*.json。\n\n"
                "请先启动一次 NapCat，或手动填写 OneBot 地址和 Token。",
                parent=self,
            )
            return
        chosen = next((item for item in configs if item.enabled), configs[0])
        self.url_var.set(chosen.url)
        self.token_var.set(chosen.token)
        self.napcat_config_var.set(str(chosen.path))
        state = "已启用" if chosen.enabled else "未启用"
        self.napcat_info_var.set(
            f"已读取：{chosen.path}\nHTTP 服务 {state}：{chosen.url}"
        )
        self._save()
        reachable = self._port_open(chosen.host, chosen.port)
        messagebox.showinfo(
            "已读取 NapCat 配置",
            f"配置文件：\n{chosen.path}\n\n"
            f"OneBot 地址：{chosen.url}\n"
            f"Token：{'已自动填入' if chosen.token else '空'}\n\n"
            + (
                "OneBot 端口已监听，可以点“测试连接”。"
                if reachable
                else "注意：这个端口现在没有监听。请先启动 NapCat 并完成登录，"
                "软件会自动重试连接。"
            ),
            parent=self,
        )

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.8):
                return True
        except OSError:
            return False

    def _napcat_config_path(self) -> Path | None:
        raw = self.napcat_config_var.get().strip()
        if raw:
            path = Path(raw)
            if path.is_file():
                return path
        configs = find_onebot_configs()
        if configs:
            chosen = next((item for item in configs if item.enabled), configs[0])
            return chosen.path
        return None

    def _open_napcat_dir(self) -> None:
        path = self._napcat_config_path()
        if path is None:
            messagebox.showinfo(
                "没有找到 NapCat",
                "先启动一次 NapCat，或手动填写 OneBot 地址。",
                parent=self,
            )
            return
        try:
            os.startfile(path.parent.parent)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self)

    def _open_onebot_config(self) -> None:
        path = self._napcat_config_path()
        if path is None:
            messagebox.showinfo(
                "没有找到配置",
                "先点“一键检测并填入”，或手动填写 OneBot 地址。",
                parent=self,
            )
            return
        try:
            subprocess.Popen(
                ["notepad.exe", str(path)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror("无法打开配置", str(exc), parent=self)

    def _check_env(self) -> None:
        results = check_local(self.app.data_dir)
        text = format_results(results)
        text += (
            "\n\n提示：OneBot 连接和 QQ 打卡 Cookie 需要先启动 NapCat。"
            "\n可以点“测试连接”，或在命令行运行："
            "\npython tools/check_env.py --url "
            + self.url_var.get().strip()
        )
        messagebox.showinfo("本机环境检查", text, parent=self)

    def _reset_marks(self) -> None:
        if not messagebox.askyesno(
            "重置今日标记",
            "将清除今天所有任务/打卡的“已执行”标记，使它们今天可以再次自动触发。\n\n"
            "只建议在确认上一次执行失败时使用。",
            parent=self,
        ):
            return
        count = self.service.reset_today_marks()
        self.app.set_status(f"已重置 {count} 条今日标记", "info")


class LogTab(ttk.Frame):
    """实时日志 + 文件日志入口。"""

    def __init__(self, master: tk.Misc, app: "MainWindow") -> None:
        super().__init__(master, padding=10)
        self.app = app
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(top, text="清空显示", command=self._clear).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(top, text="打开日志文件", command=self._open_log).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Label(
            top,
            text=f"日志文件：{self.app.log_path}",
            foreground="#667085",
        ).pack(side=tk.RIGHT)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)
        self.text = ReadOnlyText(
            body,
            font=("Consolas", 9),
            background="#ffffff",
            foreground="#344054",
            insertbackground="#344054",
        )
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def load_initial(self) -> None:
        path = Path(self.app.log_path)
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for line in lines[-400:]:
            level = "ERROR" if "[ERROR]" in line else "WARNING" if "[WARNING]" in line else "INFO"
            self.text.append(line, level)

    def append(self, level: str, message: str) -> None:
        self.text.append(message, level)

    def _clear(self) -> None:
        self.text.clear()

    def _open_log(self) -> None:
        try:
            os.startfile(self.app.log_path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开日志", str(exc), parent=self)


def _one_line(text: str, limit: int) -> str:
    """把多行文案压成一行，用于表格预览。"""
    compact = " ".join(display_message(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"
