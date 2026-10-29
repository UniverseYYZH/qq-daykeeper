"""主窗口：侧边栏导航、页面容器、事件泵。"""

from __future__ import annotations

import queue
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from ..core.models import LoginInfo, Target, Task
from ..core.service import AppService
from ..core.storage import StateStore
from .dialogs import TaskDialog
from .tabs import (
    ContactsTab,
    LogTab,
    PersonalSignTab,
    RemoteTab,
    SettingsTab,
    CheckinTab,
    SparkTab,
    TasksTab,
)
from .theme import (
    ACCENT,
    ACCENT_DARK,
    BG,
    BORDER,
    CARD,
    DANGER,
    FONT,
    MUTED,
    SIDEBAR,
    SIDEBAR_ACTIVE,
    SIDEBAR_HOVER,
    SUBTLE,
    SUCCESS,
    TEXT,
    WARNING,
)

# key, 导航文字, 页面标题, 页面副标题
PAGES = (
    ("tasks", "✉  消息任务", "消息任务", "按天定时把文案发送到指定群聊或好友"),
    ("checkin", "✓  群打卡", "群打卡", "群聊 + → 打卡，每天定时自动执行"),
    ("spark", "✧  火花守护", "好友火花守护", "18:00 后随机检查，23:50 最终检查"),
    ("personal", "☀  QQ 打卡", "QQ 打卡（日签卡）", "通过 NapCat Cookie 完成手机 QQ 日签卡"),
    ("remote", "☎  手机控制", "手机控制", "在手机 QQ 给自己发“菜单”"),
    ("contacts", "👥  联系人", "联系人", "同步并查看当前 QQ 的群聊和好友"),
    ("settings", "⚙  设置", "设置", "OneBot 连接、环境检查与维护"),
    ("logs", "▤  日志", "运行日志", "查看发送、打卡和错误详情"),
)


class _NavItem:
    """侧边栏的一项：左侧高亮条 + 扁平按钮。"""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: object,
    ) -> None:
        self.frame = tk.Frame(parent, bg=SIDEBAR)
        self.frame.pack(fill=tk.X, pady=1)
        self.indicator = tk.Frame(self.frame, bg=SIDEBAR, width=3)
        self.indicator.pack(side=tk.LEFT, fill=tk.Y)
        self.button = tk.Button(
            self.frame,
            text=text,
            command=command,  # type: ignore[arg-type]
            bg=SIDEBAR,
            fg="#C7D2E5",
            activebackground=SIDEBAR_HOVER,
            activeforeground="#FFFFFF",
            bd=0,
            anchor=tk.W,
            padx=16,
            pady=9,
            font=(FONT, 10),
            cursor="hand2",
            highlightthickness=0,
        )
        self.button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._active = False
        self.button.bind("<Enter>", self._on_enter)
        self.button.bind("<Leave>", self._on_leave)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.indicator.configure(bg=ACCENT if active else SIDEBAR)
        self.button.configure(
            bg=SIDEBAR_ACTIVE if active else SIDEBAR,
            fg="#FFFFFF" if active else "#C7D2E5",
        )

    def _on_enter(self, _event: object) -> None:
        if not self._active:
            self.button.configure(bg=SIDEBAR_HOVER)

    def _on_leave(self, _event: object) -> None:
        if not self._active:
            self.button.configure(bg=SIDEBAR)


class MainWindow:
    """把 service 的事件队列翻译成界面更新。"""

    def __init__(
        self,
        root: tk.Tk,
        service: AppService,
        store: StateStore,
        data_dir: Path,
        log_queue: queue.Queue[tuple[str, str]],
        log_path: Path,
    ) -> None:
        self.root = root
        self.service = service
        self.store = store
        self.data_dir = data_dir
        self.log_queue = log_queue
        self.log_path = log_path
        self._progress_running = False
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_items: dict[str, _NavItem] = {}

        self._configure_root()
        self._configure_style()
        self._build_sidebar()
        self._build_main()
        self._load_state()
        self.log_tab.load_initial()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._poll)

    # ------------------------------------------------------------------ 窗口
    def _configure_root(self) -> None:
        self.root.title("QQ 签到助手 - OneBot / NapCat")
        self.root.configure(bg=BG)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1220, max(980, screen_w - 80))
        height = min(800, max(640, screen_h - 100))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(980, 640)
        try:
            self.root.tk.call("tk", "scaling", 1.2)
        except tk.TclError:
            pass

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(font_name).configure(family=FONT, size=9)
            except tk.TclError:
                pass

        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure(
            "TButton",
            background="#F9FAFB",
            foreground=TEXT,
            padding=(10, 6),
            borderwidth=1,
            relief="solid",
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.map(
            "TButton",
            background=[("active", "#EAF2FF"), ("pressed", "#DCE8FF")],
            foreground=[("disabled", SUBTLE)],
        )
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#FFFFFF",
            padding=(12, 7),
            borderwidth=0,
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)],
            foreground=[("disabled", "#FFFFFF")],
        )
        style.configure("Danger.TButton", foreground=DANGER)
        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            rowheight=29,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#F9FAFB",
            foreground=MUTED,
            relief="flat",
            font=(FONT, 9, "bold"),
            padding=(6, 6),
        )
        style.map(
            "Treeview",
            background=[("selected", "#DBEAFE")],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "TLabelframe",
            background=BG,
            bordercolor=BORDER,
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=BG,
            foreground=TEXT,
            font=(FONT, 10, "bold"),
        )
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER, padding=4)
        style.configure("TSpinbox", fieldbackground=CARD, padding=4)
        style.configure("TCombobox", fieldbackground=CARD, padding=4)
        style.configure(
            "Accent.Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=BORDER,
            thickness=4,
        )

    # ------------------------------------------------------------------ 侧边栏
    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self.root, bg=SIDEBAR, width=214)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(fill=tk.X, padx=18, pady=(22, 18))
        tk.Label(
            brand,
            text="QQ 签到助手",
            bg=SIDEBAR,
            fg="#FFFFFF",
            font=(FONT, 15, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            brand,
            text="OneBot 11 · NapCatQQ",
            bg=SIDEBAR,
            fg="#8FA3BF",
            font=(FONT, 8),
        ).pack(anchor=tk.W, pady=(4, 0))

        tk.Frame(sidebar, bg="#1F2937", height=1).pack(fill=tk.X, padx=16)

        nav = tk.Frame(sidebar, bg=SIDEBAR)
        nav.pack(fill=tk.X, pady=(10, 0))
        for key, label, _title, _subtitle in PAGES:
            item = _NavItem(
                nav,
                label,
                lambda page_key=key: self.show_page(page_key),
            )
            self.nav_items[key] = item

        bottom = tk.Frame(sidebar, bg=SIDEBAR)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(0, 16))
        tk.Frame(bottom, bg="#1F2937", height=1).pack(fill=tk.X, pady=(0, 14))

        self.conn_label = tk.Label(
            bottom,
            text="● 未连接",
            bg=SIDEBAR,
            fg=SUBTLE,
            font=(FONT, 9, "bold"),
            anchor=tk.W,
        )
        self.conn_label.pack(fill=tk.X)
        self.account_label = tk.Label(
            bottom,
            text="先连接 NapCat",
            bg=SIDEBAR,
            fg="#8FA3BF",
            font=(FONT, 8),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=180,
        )
        self.account_label.pack(fill=tk.X, pady=(4, 10))

        self.connect_button = tk.Button(
            bottom,
            text="连接并同步",
            command=self._connect,
            bg=ACCENT,
            fg="#FFFFFF",
            activebackground=ACCENT_DARK,
            activeforeground="#FFFFFF",
            bd=0,
            padx=12,
            pady=7,
            font=(FONT, 9, "bold"),
            cursor="hand2",
            highlightthickness=0,
        )
        self.connect_button.pack(fill=tk.X)
        tk.Label(
            bottom,
            text="连接后请先到“联系人”刷新",
            bg=SIDEBAR,
            fg="#6B7C96",
            font=(FONT, 7),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(6, 0))

    # ------------------------------------------------------------------ 主区域
    def _build_main(self) -> None:
        main = tk.Frame(self.root, bg=BG)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        topbar = tk.Frame(main, bg=BG, height=88)
        topbar.pack(fill=tk.X, padx=22, pady=(16, 0))
        topbar.pack_propagate(False)

        title_box = tk.Frame(topbar, bg=BG)
        title_box.pack(side=tk.LEFT, fill=tk.Y)
        self.page_title_var = tk.StringVar(value="消息任务")
        self.page_subtitle_var = tk.StringVar(value="")
        tk.Label(
            title_box,
            textvariable=self.page_title_var,
            bg=BG,
            fg=TEXT,
            font=(FONT, 18, "bold"),
        ).pack(anchor=tk.W, pady=(6, 2))
        tk.Label(
            title_box,
            textvariable=self.page_subtitle_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor=tk.W)

        actions = tk.Frame(topbar, bg=BG)
        actions.pack(side=tk.RIGHT, pady=(14, 0))
        ttk.Button(
            actions, text="刷新联系人", command=self._refresh_contacts
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            actions, text="连接并同步", style="Accent.TButton", command=self._connect
        ).pack(side=tk.LEFT)

        # 先放底部状态栏，再放会 expand 的内容区，避免状态栏被挤掉
        self._build_statusbar(main)

        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 0))
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self.tasks_tab = TasksTab(self.content, self)
        self.checkin_tab = CheckinTab(self.content, self)
        self.spark_tab = SparkTab(self.content, self)
        self.personal_tab = PersonalSignTab(self.content, self)
        self.remote_tab = RemoteTab(self.content, self)
        self.contacts_tab = ContactsTab(self.content, self)
        self.settings_tab = SettingsTab(self.content, self)
        self.log_tab = LogTab(self.content, self)

        page_widgets = {
            "tasks": self.tasks_tab,
            "checkin": self.checkin_tab,
            "spark": self.spark_tab,
            "personal": self.personal_tab,
            "remote": self.remote_tab,
            "contacts": self.contacts_tab,
            "settings": self.settings_tab,
            "logs": self.log_tab,
        }
        for key, widget in page_widgets.items():
            widget.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = widget

        self.show_page("tasks")

    def _build_statusbar(self, parent: tk.Misc) -> None:
        bar = tk.Frame(parent, bg=BG, height=34)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=22, pady=(0, 6))
        bar.pack_propagate(False)

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = tk.Label(
            bar,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor=tk.W,
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress = ttk.Progressbar(
            bar,
            mode="indeterminate",
            length=100,
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.pack(side=tk.RIGHT, padx=(10, 12), pady=12)
        self.clock_var = tk.StringVar()
        tk.Label(
            bar,
            textvariable=self.clock_var,
            bg=BG,
            fg=SUBTLE,
            font=(FONT, 8),
        ).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ 页面切换
    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        page.tkraise()
        for nav_key, item in self.nav_items.items():
            item.set_active(nav_key == key)
        for page_key, _label, title, subtitle in PAGES:
            if page_key == key:
                self.page_title_var.set(title)
                self.page_subtitle_var.set(subtitle)
                break

    # ------------------------------------------------------------------ 初始数据
    def _load_state(self) -> None:
        state = self.store.read()
        self.contacts_tab.reload(preserve=False)
        self.checkin_tab.set_groups(state.contacts.groups)
        self.checkin_tab.load_config()
        self.spark_tab.set_friends(state.contacts.friends)
        self.spark_tab.load_config()
        self.personal_tab.load_config()
        self.remote_tab.refresh_status()
        self.tasks_tab.refresh()
        self.settings_tab.load()
        self._update_connected(self.service.login_info)
        self._update_account_summary(
            len(state.contacts.groups), len(state.contacts.friends), cached=True
        )

    # ------------------------------------------------------------------ 操作
    def _connect(self) -> None:
        self.service.connect_async()
        self.set_status("正在连接 OneBot 并同步联系人…", "busy")

    def _refresh_contacts(self) -> None:
        self.service.refresh_contacts_async()
        self.set_status("正在刷新联系人…", "busy")

    def open_task_dialog(
        self,
        task: Task | None = None,
        preset_targets: list[Target] | None = None,
    ) -> None:
        all_targets = self.store.read().contacts.all
        if not all_targets:
            messagebox.showinfo(
                "没有联系人",
                "请先连接 NapCat，点击“刷新联系人”同步群聊和好友。",
                parent=self.root,
            )
            return
        dialog = TaskDialog(
            self.root,
            all_targets=all_targets,
            task=task,
            preset_targets=preset_targets,
        )
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        try:
            self.service.save_task(dialog.result)
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self.tasks_tab.refresh()
        self.set_status(
            f"任务已保存：{dialog.result.name}（{dialog.result.time}）", "ok"
        )

    # ------------------------------------------------------------------ 状态
    def set_status(self, text: str, level: str = "info") -> None:
        color = {
            "ok": SUCCESS,
            "warning": WARNING,
            "error": DANGER,
            "busy": ACCENT,
        }.get(level, MUTED)
        self.status_var.set(text)
        self.status_label.configure(fg=color)

    def _update_connected(self, info: LoginInfo | None) -> None:
        if info:
            self.conn_label.configure(text="● 已连接", fg=SUCCESS)
            self.account_label.configure(text=f"{info.nickname}\n{info.user_id}")
            self.settings_tab.update_account(info)
        else:
            self.conn_label.configure(text="● 未连接", fg=SUBTLE)
            self.account_label.configure(text="先连接 NapCat")
            self.settings_tab.update_account(None)

    def _update_account_summary(
        self, groups: int, friends: int, *, cached: bool = False
    ) -> None:
        prefix = "缓存" if cached else "已同步"
        self.account_label.configure(
            text=f"{prefix}：群 {groups} · 好友 {friends}"
        )

    # ------------------------------------------------------------------ 事件泵
    def _poll(self) -> None:
        for _ in range(400):
            try:
                level, message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_tab.append(level, message)

        for _ in range(200):
            try:
                event = self.service.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)

        self.clock_var.set(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.root.after(200, self._poll)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "connection":
            if event.get("ok"):
                info = event.get("login")
                self._update_connected(
                    info if isinstance(info, LoginInfo) else self.service.login_info
                )
                if event.get("groups") is not None:
                    self._update_account_summary(
                        int(event.get("groups", 0)),
                        int(event.get("friends", 0)),
                    )
                    self.set_status(
                        f"已连接：群 {event.get('groups', 0)} 个，好友 {event.get('friends', 0)} 个",
                        "ok",
                    )
                else:
                    self.set_status("连接正常", "ok")
            else:
                self._update_connected(None)
                message = str(event.get("message", "连接失败"))
                self.set_status(f"连接失败：{message}", "error")
        elif event_type == "contacts_changed":
            self.contacts_tab.reload(preserve=True)
            contacts = self.store.read().contacts
            self.checkin_tab.set_groups(contacts.groups)
            self.spark_tab.set_friends(contacts.friends)
            self._update_account_summary(
                len(contacts.groups), len(contacts.friends)
            )
        elif event_type == "state_changed":
            self.tasks_tab.refresh()
            self.checkin_tab.refresh_status()
            self.personal_tab.refresh_status()
            self.spark_tab.refresh_status()
        elif event_type == "remote_status":
            self.remote_tab.refresh_status()
        elif event_type == "config_changed":
            self.settings_tab.load()
        elif event_type == "busy":
            busy = bool(event.get("busy"))
            if busy:
                self.progress.start(12)
                self._progress_running = True
                self.set_status(f"正在执行：{event.get('label', '')}…", "busy")
            else:
                if self._progress_running:
                    self.progress.stop()
                    self._progress_running = False
        elif event_type == "toast":
            level = str(event.get("level", "info"))
            self.set_status(str(event.get("message", "")), level)
        elif event_type == "error":
            self.set_status(f"错误：{event.get('message', '')}", "error")

    # ------------------------------------------------------------------ 关闭
    def _on_close(self) -> None:
        if self.service.busy:
            if not messagebox.askyesno(
                "任务执行中",
                "后台仍在执行任务，确定退出吗？未完成的目标将不会继续发送。",
                parent=self.root,
            ):
                return
        try:
            self.service.shutdown()
        finally:
            self.root.destroy()
