"""可复用的 Tkinter 控件。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Iterable

from ..core.models import KIND_GROUP, Target


class TargetPicker(ttk.Frame):
    """带搜索、全选、反选的“群聊/好友”勾选列表。

    交互：单击任意一行即可勾选/取消，行首显示 ☑ / ☐。
    ``show_type=False`` 时用于只展示群聊的打卡面板。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        show_type: bool = True,
        height: int = 10,
        id_heading: str = "群号",
        name_heading: str = "群名",
    ) -> None:
        super().__init__(master)
        self.show_type = show_type
        self._targets: list[Target] = []
        self._by_key: dict[str, Target] = {}
        self._checked: set[str] = set()
        self._filter = ""

        self.search_var = tk.StringVar()
        self.count_var = tk.StringVar(value="已选 0 / 共 0")

        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(toolbar, text="搜索").pack(side=tk.LEFT, padx=(0, 4))
        search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=18)
        search_entry.pack(side=tk.LEFT, padx=(0, 6))
        self.search_var.trace_add("write", self._on_filter_changed)

        ttk.Button(toolbar, text="全选", width=6, command=self._select_all).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="清空", width=6, command=self._clear_all).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="反选", width=6, command=self._invert).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Label(toolbar, textvariable=self.count_var).pack(side=tk.RIGHT)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)

        if show_type:
            columns = ("check", "type", "id", "name")
            self.tree = ttk.Treeview(
                body,
                columns=columns,
                show="headings",
                selectmode="browse",
                height=height,
            )
            self.tree.heading("check", text="选")
            self.tree.heading("type", text="类型")
            self.tree.heading("id", text="号码")
            self.tree.heading("name", text="名称")
            self.tree.column("check", width=42, minwidth=42, anchor=tk.CENTER, stretch=False)
            self.tree.column("type", width=54, minwidth=54, anchor=tk.CENTER, stretch=False)
            self.tree.column("id", width=105, minwidth=80, anchor=tk.CENTER, stretch=False)
            self.tree.column("name", width=300, minwidth=160, anchor=tk.W, stretch=True)
        else:
            columns = ("check", "id", "name")
            self.tree = ttk.Treeview(
                body,
                columns=columns,
                show="headings",
                selectmode="browse",
                height=height,
            )
            self.tree.heading("check", text="选")
            self.tree.heading("id", text=id_heading)
            self.tree.heading("name", text=name_heading)
            self.tree.column("check", width=42, minwidth=42, anchor=tk.CENTER, stretch=False)
            self.tree.column("id", width=110, minwidth=80, anchor=tk.CENTER, stretch=False)
            self.tree.column("name", width=300, minwidth=160, anchor=tk.W, stretch=True)

        self.tree.tag_configure("group", background="#f4f8fd")
        self.tree.tag_configure("friend", background="#ffffff")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ------------------------------------------------------------------ 对外 API
    def set_targets(
        self,
        targets: Iterable[Target],
        *,
        checked_keys: Iterable[str] | None = None,
        preserve: bool = True,
    ) -> None:
        """替换列表内容。

        ``checked_keys`` 不为空时用它设置勾选；否则 ``preserve=True``
        会保留仍然存在的旧勾选。
        """
        unique: dict[str, Target] = {}
        for target in targets:
            unique.setdefault(target.key, target)
        self._targets = sorted(
            unique.values(),
            key=lambda t: (0 if t.kind == KIND_GROUP else 1, t.name.casefold(), t.id),
        )
        self._by_key = {t.key: t for t in self._targets}
        if checked_keys is not None:
            self._checked = {k for k in checked_keys if k in self._by_key}
        elif preserve:
            self._checked &= set(self._by_key)
        else:
            self._checked.clear()
        self._render()

    def get_checked_targets(self) -> list[Target]:
        return [t for t in self._targets if t.key in self._checked]

    def set_checked_targets(self, targets: Iterable[Target]) -> None:
        self._checked = {t.key for t in targets if t.key in self._by_key}
        self._render()

    def check_all(self) -> None:
        self._select_all()

    def clear_checks(self) -> None:
        self._clear_all()

    @property
    def checked_count(self) -> int:
        return len(self._checked)

    # ------------------------------------------------------------------ 渲染
    def _filtered_targets(self) -> list[Target]:
        needle = self._filter.strip().casefold()
        if not needle:
            return list(self._targets)
        result = []
        for target in self._targets:
            haystack = f"{target.name} {target.id} {target.kind_label}".casefold()
            if needle in haystack:
                result.append(target)
        return result

    def _render(self) -> None:
        # 记住滚动位置，刷新勾选时不跳回顶部
        yview = self.tree.yview()
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for target in self._filtered_targets():
            mark = "☑" if target.key in self._checked else "☐"
            if self.show_type:
                values = (mark, target.kind_label, target.id, target.name)
            else:
                values = (mark, target.id, target.name)
            tag = "group" if target.kind == KIND_GROUP else "friend"
            self.tree.insert("", tk.END, iid=target.key, values=values, tags=(tag,))
        if selected:
            existing = [iid for iid in selected if self.tree.exists(iid)]
            if existing:
                self.tree.selection_set(existing)
        if yview:
            self.tree.yview_moveto(yview[0])
        self._update_count()

    def _update_count(self) -> None:
        self.count_var.set(f"已选 {len(self._checked)} / 共 {len(self._targets)}")

    # ------------------------------------------------------------------ 交互
    def _on_filter_changed(self, *_args: object) -> None:
        self._filter = self.search_var.get()
        self._render()

    def _on_click(self, event: tk.Event) -> str | None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        iid = self.tree.identify_row(event.y)
        if not iid:
            return None
        self.tree.selection_set(iid)
        self._toggle(iid)
        return "break"

    def _on_space(self, _event: tk.Event) -> str:
        for iid in self.tree.selection():
            self._toggle(iid)
        return "break"

    def _toggle(self, key: str) -> None:
        if key not in self._by_key:
            return
        if key in self._checked:
            self._checked.remove(key)
        else:
            self._checked.add(key)
        self._render()

    def _select_all(self) -> None:
        for target in self._filtered_targets():
            self._checked.add(target.key)
        self._render()

    def _clear_all(self) -> None:
        self._checked.clear()
        self._render()

    def _invert(self) -> None:
        for target in self._filtered_targets():
            if target.key in self._checked:
                self._checked.remove(target.key)
            else:
                self._checked.add(target.key)
        self._render()


class ReadOnlyText(tk.Text):
    """只读日志文本框，支持按级别着色和限制最大行数。"""

    def __init__(self, master: tk.Misc, **kwargs: object) -> None:
        super().__init__(master, **kwargs)
        self.configure(state=tk.DISABLED, wrap=tk.WORD)
        self.tag_configure("ERROR", foreground="#b42318")
        self.tag_configure("WARNING", foreground="#b54708")
        self.tag_configure("INFO", foreground="#344054")
        self.tag_configure("DEBUG", foreground="#667085")
        self._max_lines = 3000

    def append(self, message: str, level: str = "INFO") -> None:
        self.configure(state=tk.NORMAL)
        self.insert(tk.END, message.rstrip() + "\n", level if level in {"ERROR", "WARNING", "INFO", "DEBUG"} else "INFO")
        # 超过上限时从头部删除，避免长时间运行内存膨胀
        line_count = int(self.index("end-1c").split(".")[0])
        if line_count > self._max_lines:
            self.delete("1.0", f"{line_count - self._max_lines}.0")
        self.see(tk.END)
        self.configure(state=tk.DISABLED)

    def clear(self) -> None:
        self.configure(state=tk.NORMAL)
        self.delete("1.0", tk.END)
        self.configure(state=tk.DISABLED)
