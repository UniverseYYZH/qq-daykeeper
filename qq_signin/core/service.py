"""业务执行服务。

线程模型：
- Tk 主线程：只处理界面；
- worker 线程：串行执行所有网络操作（连接、发送、打卡），避免并发写日志；
- scheduler 线程：每 5 秒检查一次是否到点，只负责入队。

所有跨线程通知都通过 ``events`` 队列发送，界面用 ``after`` 轮询消费。
"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
from datetime import datetime
from typing import Any

from .message import build_onebot_message
from .models import (
    KIND_FRIEND,
    KIND_GROUP,
    AppConfig,
    AutoSignConfig,
    Contacts,
    LoginInfo,
    PersonalSignConfig,
    SparkFriendState,
    SparkWatchConfig,
    Target,
    Task,
)
from .onebot import OneBotClient, OneBotError, normalize_base_url
from .qq_daily import PersonalSignError, QQDailySigner
from .remote import MENU_TEXT, PREFIX, RemoteControl, RemoteEventServer
from .safety import (
    SafetyError,
    is_risk_error,
    pause_for_risk,
    reserve_messages,
    reserve_sign_groups,
)
from .scheduler import DueJob, Scheduler, collect_due_jobs
from .spark import (
    MAX_REMINDERS_PER_DAY,
    choose_message,
    compute_next_check,
    contact_sent_today,
    latest_message_time,
    parse_dt,
    today_iso,
)
from .storage import StateStore

LOGGER = logging.getLogger("qq_signin.service")


class AppService:
    """把状态、OneBot 和线程调度粘在一起。"""

    def __init__(
        self,
        store: StateStore,
        logger: logging.Logger | None = None,
        *,
        personal_sign_url: str | None = None,
    ) -> None:
        self.store = store
        self.logger = logger or LOGGER
        self._personal_sign_url = personal_sign_url
        self._rng = random.Random()
        self.remote_status = "未启动"
        self.remote_port = 0
        self._remote_server: RemoteEventServer | None = None
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._jobs: queue.Queue[tuple[Any, ...] | None] = queue.Queue()
        self._closed = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop, name="qq-signin-worker", daemon=True
        )
        self._scheduler = Scheduler(self, interval=5.0)
        self._client: OneBotClient | None = None
        self._client_signature: tuple[str, str] | None = None
        self._busy_lock = threading.Lock()
        self._busy_label = ""
        self._connected = False
        self._login_info: LoginInfo | None = None
        self._last_error = ""

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._closed.is_set():
            raise RuntimeError("服务已关闭，不能再次启动")
        if self._worker.is_alive():
            return
        self._worker.start()
        self._scheduler.start()
        self._start_remote()
        self.logger.info("后台服务已启动")

    def _start_remote(self) -> None:
        config = self.store.read().config.remote
        self.remote_port = config.port
        if not config.enabled:
            self.remote_status = "未启用"
            self._emit("remote_status")
            return
        control = RemoteControl(self)
        try:
            server = RemoteEventServer(control, config.port, config.token)
        except OSError as exc:
            self.remote_status = f"端口 {config.port} 启动失败：{exc}"
            self.logger.warning("手机控制监听失败：%s", exc)
            self._emit("remote_status")
            return
        self._remote_server = server
        server.start()
        self.remote_status = f"监听中 127.0.0.1:{config.port}"
        self.logger.info("手机控制监听：127.0.0.1:%s", config.port)
        self._emit("remote_status")

    def shutdown(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._scheduler.stop()
        if self._remote_server is not None:
            self._remote_server.stop()
            self._remote_server = None
        self._jobs.put(None)
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
        if self._scheduler.is_alive():
            self._scheduler.join(timeout=2.0)
        self.logger.info("后台服务已停止")

    # ------------------------------------------------------------------ 状态读取
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def login_info(self) -> LoginInfo | None:
        return self._login_info

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def busy(self) -> bool:
        with self._busy_lock:
            return bool(self._busy_label)

    @property
    def busy_label(self) -> str:
        with self._busy_lock:
            return self._busy_label

    # ------------------------------------------------------------------ 异步入口
    def connect_async(self) -> None:
        self._enqueue(("connect",))

    def refresh_contacts_async(self) -> None:
        self._enqueue(("refresh",))

    def test_connection_async(self) -> None:
        self._enqueue(("test",))

    def run_task_async(self, task_id: str) -> None:
        self._enqueue(("task", task_id, True))

    def run_checkin_async(
        self,
        groups: list[Target] | None = None,
        *,
        manual: bool = True,
    ) -> None:
        """执行群打卡。"""
        self._enqueue(("checkin", groups, manual))

    def run_personal_sign_async(self) -> None:
        """QQ 打卡（日签卡）。"""
        self._enqueue(("personal", True))

    def run_spark_check_async(self) -> None:
        """手动立即检查一次火花。"""
        self._enqueue(("spark", True))

    def enqueue_remote_command(self, command: str, self_id: int) -> None:
        """把手机指令放进串行队列，由 worker 回复。"""
        self._enqueue(("remote", command, int(self_id)))

    # ------------------------------------------------------------------ 保存操作
    def save_task(self, task: Task) -> None:
        errors = task.validate()
        if errors:
            raise ValueError("\n".join(errors))
        if not task.created_at:
            task.created_at = datetime.now().isoformat(timespec="seconds")

        def mutate(state: Any) -> None:
            state.upsert_task(task)

        self.store.update(mutate)
        self.logger.info("已保存任务：%s（%s）", task.name, task.time)
        self._emit("state_changed")

    def delete_task(self, task_id: str) -> bool:
        def mutate(state: Any) -> bool:
            return state.remove_task(task_id)

        removed = self.store.update(mutate)
        if removed:
            self.logger.info("已删除任务：%s", task_id)
            self._emit("state_changed")
        return removed

    def set_task_enabled(self, task_id: str, enabled: bool) -> None:
        def mutate(state: Any) -> None:
            task = state.find_task(task_id)
            if task:
                task.enabled = enabled

        self.store.update(mutate)
        self._emit("state_changed")

    def save_config(
        self,
        onebot_url: str,
        token: str,
        send_interval: float,
        napcat_webui: str,
        *,
        napcat_config: str = "",
        auto_connect: bool = True,
    ) -> AppConfig:
        """保存连接配置；地址不合法时抛 ``ValueError``。"""
        url = normalize_base_url(onebot_url)
        try:
            interval = max(5.0, min(60.0, float(send_interval)))
        except (TypeError, ValueError) as exc:
            raise ValueError("发送间隔必须是数字") from exc
        webui = (napcat_webui or "").strip().rstrip("/")

        def mutate(state: Any) -> AppConfig:
            state.config.onebot_url = url
            state.config.token = (token or "").strip()
            state.config.send_interval = interval
            state.config.napcat_webui = webui
            state.config.napcat_config = str(napcat_config or "").strip()
            state.config.auto_connect = bool(auto_connect)
            return state.config

        config = self.store.update(mutate)
        # 连接参数变了，下次调用时重建客户端
        self._client = None
        self._client_signature = None
        self.logger.info("连接配置已保存：%s", url)
        self._emit("config_changed")
        return config

    def save_checkin_config(
        self,
        enabled: bool,
        time_text: str,
        groups: list[Target],
    ) -> None:
        new_config = AutoSignConfig(
            enabled=bool(enabled),
            time=time_text,
            groups=groups,
        )
        errors = new_config.validate()
        if errors:
            raise ValueError("\n".join(errors))

        def mutate(state: Any) -> None:
            old: AutoSignConfig = state.config.group_checkin
            # 保留历史执行记录，避免保存设置后“上次执行”消失
            new_config.last_run = old.last_run
            new_config.last_result = old.last_result
            state.config.group_checkin = new_config

        self.store.update(mutate)
        self.logger.info(
            "群打卡设置已保存：%s，%s",
            new_config.time,
            "启用" if enabled else "停用",
        )
        self._emit("state_changed")

    def save_personal_sign_config(self, enabled: bool, time_text: str) -> None:
        """保存 QQ 打卡（日签卡）配置。"""
        new_config = PersonalSignConfig(enabled=bool(enabled), time=time_text)
        errors = new_config.validate()
        if errors:
            raise ValueError("\n".join(errors))

        def mutate(state: Any) -> None:
            old: PersonalSignConfig = state.config.personal_sign
            new_config.last_run = old.last_run
            new_config.last_result = old.last_result
            state.config.personal_sign = new_config

        self.store.update(mutate)
        self.logger.info(
            "QQ 打卡设置已保存：%s，%s",
            new_config.time,
            "启用" if enabled else "停用",
        )
        self._emit("state_changed")

    def save_spark_config(
        self,
        enabled: bool,
        friends: list[Target],
        messages: list[str],
    ) -> None:
        """保存火花守护设置，并计算下一次随机检查时间。"""
        now = datetime.now()
        new_config = SparkWatchConfig(
            enabled=bool(enabled),
            friends=friends,
            messages=messages,
        )
        errors = new_config.validate()
        if errors:
            raise ValueError("\n".join(errors))

        def mutate(state: Any) -> None:
            old: SparkWatchConfig = state.config.spark
            old_keys = {friend.key for friend in old.friends}
            new_keys = {friend.key for friend in new_config.friends}
            new_config.statuses = {
                key: old.statuses[key]
                for key in new_keys
                if key in old_keys and key in old.statuses
            }
            same_any = bool(new_keys & old_keys)
            new_config.last_check_at = old.last_check_at if same_any else ""
            new_config.last_result = old.last_result if same_any else ""
            if new_config.enabled and new_config.pending(now.date().isoformat()):
                new_config.next_check_at = compute_next_check(
                    now,
                    last_check_at=parse_dt(old.last_check_at),
                    rng=self._rng,
                ).isoformat(timespec="seconds")
            else:
                new_config.next_check_at = ""
            state.config.spark = new_config

        self.store.update(mutate)
        self.logger.info(
            "火花守护设置已保存：%s，好友 %s",
            "启用" if enabled else "停用",
            "、".join(friend.name for friend in friends) or "未选择",
        )
        self._emit("state_changed")

    def reset_today_marks(self) -> int:
        """清除今天的自动执行标记，便于失败后手动补跑。"""
        today = datetime.now().date().isoformat()
        count = 0

        def mutate(state: Any) -> int:
            nonlocal count
            for task in state.tasks:
                if task.last_run == today:
                    task.last_run = ""
                    task.last_result = "已重置今日执行标记"
                    count += 1
            for config in (
                state.config.group_checkin,
                state.config.personal_sign,
            ):
                if config.last_run == today:
                    config.last_run = ""
                    config.last_result = "已重置今日执行标记"
                    count += 1
            return count

        count = self.store.update(mutate)
        self.logger.info("已重置 %d 条今日执行标记", count)
        self._emit("state_changed")
        return count

    # ------------------------------------------------------------------ 到点检查
    def tick(self) -> None:
        now = datetime.now()
        preview = self.store.read()
        # 先只读检查，避免没有任务时每 5 秒写一次磁盘
        if not collect_due_jobs(preview, now):
            return

        due = self.store.update(lambda state: collect_due_jobs(state, now))
        for job in due:
            self.logger.info("到点触发：%s", job.label)
            self._enqueue_due(job)
        if due:
            self._emit("state_changed")

    def _enqueue_due(self, job: DueJob) -> None:
        if job.kind == "task":
            self._enqueue(("task", job.ref_id, False))
        elif job.kind == "checkin":
            self._enqueue(("checkin", None, False))
        elif job.kind == "personal":
            self._enqueue(("personal", False))
        elif job.kind == "spark":
            self._enqueue(("spark", False))
        else:  # pragma: no cover - 理论上不会发生
            self.logger.warning("未知的调度任务类型：%s", job.kind)

    # ------------------------------------------------------------------ 队列/线程
    def _enqueue(self, job: tuple[Any, ...]) -> None:
        if self._closed.is_set():
            return
        self._jobs.put(job)

    def _worker_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                self._jobs.task_done()
                return
            label = self._job_label(job)
            self._set_busy(label)
            self._emit("busy", busy=True, label=label)
            try:
                self._dispatch(job)
            except SafetyError as exc:
                self.logger.warning("安全限制：%s", exc)
                self._emit("error", message=str(exc))
            except Exception as exc:
                self.logger.exception("执行失败：%s", exc)
                self._emit("error", message=str(exc))
            finally:
                self._set_busy("")
                self._emit("busy", busy=False)
                self._jobs.task_done()

    def _dispatch(self, job: tuple[Any, ...]) -> None:
        kind = job[0]
        if kind == "connect":
            self._do_connect()
        elif kind == "refresh":
            self._do_refresh()
        elif kind == "test":
            self._do_test()
        elif kind == "task":
            self._do_task(str(job[1]), manual=bool(job[2]))
        elif kind == "checkin":
            self._do_checkin(job[1], manual=bool(job[2]))
        elif kind == "personal":
            self._do_personal_sign(manual=bool(job[1]))
        elif kind == "spark":
            self._do_spark_check(manual=bool(job[1]))
        elif kind == "remote":
            self._do_remote_command(str(job[1]), int(job[2]))
        else:  # pragma: no cover - 内部错误
            raise RuntimeError(f"未知任务：{kind!r}")

    def _job_label(self, job: tuple[Any, ...]) -> str:
        kind = job[0]
        if kind == "connect":
            return "连接并同步联系人"
        if kind == "refresh":
            return "刷新联系人"
        if kind == "test":
            return "测试连接"
        if kind == "task":
            task = self.store.read().find_task(str(job[1]))
            return f"发送任务：{task.name}" if task else "发送任务"
        if kind == "checkin":
            return "群打卡"
        if kind == "personal":
            return "QQ 打卡（日签卡）"
        if kind == "spark":
            return "好友火花检查"
        if kind == "remote":
            return "手机指令"
        return "处理中"

    def _set_busy(self, label: str) -> None:
        with self._busy_lock:
            self._busy_label = label

    def _emit(self, event_type: str, **payload: Any) -> None:
        self.events.put({"type": event_type, **payload})

    # ------------------------------------------------------------------ 具体操作
    def _client_or_raise(self) -> OneBotClient:
        config = self.store.read().config
        signature = (config.onebot_url, config.token)
        if self._client is None or signature != self._client_signature:
            try:
                self._client = OneBotClient(config.onebot_url, config.token)
            except ValueError as exc:
                raise OneBotError(f"OneBot 地址配置无效：{exc}") from exc
            self._client_signature = signature
        return self._client

    def _do_connect(self) -> None:
        try:
            client = self._client_or_raise()
            login = client.get_login_info()
            groups = client.get_group_list()
            friends = client.get_friend_list()
            updated_at = datetime.now().isoformat(timespec="seconds")

            def mutate(state: Any) -> None:
                state.contacts = Contacts(
                    groups=groups, friends=friends, updated_at=updated_at
                )

            self.store.update(mutate)
            self._connected = True
            self._login_info = login
            self._last_error = ""
            self.logger.info(
                "连接成功：%s，群 %d 个，好友 %d 个",
                login.display,
                len(groups),
                len(friends),
            )
            self._emit(
                "connection",
                ok=True,
                login=login,
                groups=len(groups),
                friends=len(friends),
            )
            self._emit("contacts_changed")
            self._emit("toast", message="连接成功，联系人已同步", level="info")
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            self.logger.error("连接失败：%s", exc)
            self._emit("connection", ok=False, message=str(exc))
            raise

    def _do_refresh(self) -> None:
        try:
            client = self._client_or_raise()
            login = client.get_login_info()
            groups = client.get_group_list()
            friends = client.get_friend_list()
            updated_at = datetime.now().isoformat(timespec="seconds")

            def mutate(state: Any) -> None:
                state.contacts = Contacts(
                    groups=groups, friends=friends, updated_at=updated_at
                )

            self.store.update(mutate)
            self._connected = True
            self._login_info = login
            self._last_error = ""
            self.logger.info(
                "联系人刷新完成：群 %d 个，好友 %d 个", len(groups), len(friends)
            )
            self._emit(
                "connection",
                ok=True,
                login=login,
                groups=len(groups),
                friends=len(friends),
            )
            self._emit("contacts_changed")
            self._emit("toast", message="联系人已刷新", level="info")
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            self.logger.error("刷新联系人失败：%s", exc)
            self._emit("connection", ok=False, message=str(exc))
            raise

    def _do_test(self) -> None:
        try:
            client = self._client_or_raise()
            login = client.get_login_info()
            self._connected = True
            self._login_info = login
            self._last_error = ""
            self.logger.info("连接测试成功：%s", login.display)
            self._emit("connection", ok=True, login=login)
            self._emit("toast", message=f"连接正常：{login.display}", level="info")
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            self.logger.error("连接测试失败：%s", exc)
            self._emit("connection", ok=False, message=str(exc))
            raise

    def _do_task(self, task_id: str, *, manual: bool) -> None:
        snapshot = self.store.read()
        task = snapshot.find_task(task_id)
        if task is None:
            raise RuntimeError("任务不存在，可能已被删除")
        if not task.targets:
            raise RuntimeError(f"任务“{task.name}”没有发送目标")

        # 先占用今日额度；超过上限时直接拒绝，不触碰网络
        self.store.update(
            lambda state: reserve_messages(
                state, len(task.targets), datetime.now()
            )
        )

        client = self._client_or_raise()
        ok = 0
        failed: list[str] = []
        total = len(task.targets)
        mode = "手动" if manual else "定时"
        message_payload = build_onebot_message(task.message)
        risk_paused = False
        self.logger.info(
            "[%s] 开始任务“%s”，目标 %d 个", mode, task.name, total
        )

        for index, target in enumerate(task.targets, start=1):
            try:
                if target.kind == KIND_GROUP:
                    client.send_group_msg(target.id, message_payload)
                elif target.kind == KIND_FRIEND:
                    client.send_private_msg(target.id, message_payload)
                else:  # pragma: no cover - 模型层已校验
                    raise RuntimeError(f"未知目标类型：{target.kind}")
                ok += 1
                self.logger.info(
                    "[%s] 已发送 %d/%d：%s", task.name, index, total, target.display
                )
            except Exception as exc:
                failed.append(f"{target.display}：{exc}")
                self.logger.warning(
                    "[%s] 发送失败 %d/%d：%s：%s",
                    task.name,
                    index,
                    total,
                    target.display,
                    exc,
                )
                if is_risk_error(exc):
                    self.store.update(
                        lambda state: pause_for_risk(
                            state, datetime.now(), str(exc)
                        )
                    )
                    self.logger.error("检测到风控信号，已自动暂停后续发送")
                    risk_paused = True
                    break
            if index < total:
                time.sleep(self._send_interval())

        now = datetime.now()
        summary = f"{mode} {now:%m-%d %H:%M} · 成功 {ok} / 失败 {len(failed)}"
        if risk_paused:
            summary += " · 已触发安全暂停"

        def mutate(state: Any) -> None:
            current = state.find_task(task_id)
            if current is None:
                return
            if not manual:
                current.last_run = now.date().isoformat()
            current.last_result = summary
            # 一次性任务执行后自动停用，避免第二天误触
            if current.repeat == "once":
                current.enabled = False

        self.store.update(mutate)
        self.logger.info("[%s] 任务“%s”结束：%s", mode, task.name, summary)
        self._emit("state_changed")
        toast = f"任务“{task.name}”完成：成功 {ok}，失败 {len(failed)}"
        if risk_paused:
            toast += "；已触发安全暂停 2 小时"
        self._emit(
            "toast",
            message=toast,
            level="error" if risk_paused else ("warning" if failed else "info"),
        )

    def _do_checkin(
        self,
        groups: list[Target] | None,
        *,
        manual: bool,
    ) -> None:
        label = "群打卡"
        snapshot = self.store.read()
        config: AutoSignConfig = snapshot.config.group_checkin
        selected = list(groups) if groups is not None else list(config.groups)
        selected = [g for g in selected if g.kind == KIND_GROUP]
        if not selected:
            raise RuntimeError("群打卡没有选择任何群")

        # 群打卡也占用每日额度，防止一次刷太多群
        self.store.update(
            lambda state: reserve_sign_groups(
                state, len(selected), datetime.now()
            )
        )

        client = self._client_or_raise()
        ok = 0
        failed: list[str] = []
        total = len(selected)
        mode = "手动" if manual else "定时"
        risk_paused = False
        self.logger.info("[%s] 开始群打卡，共 %d 个群", mode, total)

        for index, group in enumerate(selected, start=1):
            try:
                client.send_group_sign(group.id)
                ok += 1
                self.logger.info(
                    "[%s] 群打卡成功 %d/%d：%s",
                    label,
                    index,
                    total,
                    group.display,
                )
            except Exception as exc:
                failed.append(f"{group.display}：{exc}")
                self.logger.warning(
                    "[%s] 群打卡失败 %d/%d：%s：%s",
                    label,
                    index,
                    total,
                    group.display,
                    exc,
                )
                if is_risk_error(exc):
                    self.store.update(
                        lambda state: pause_for_risk(
                            state, datetime.now(), str(exc)
                        )
                    )
                    self.logger.error("检测到风控信号，已自动暂停后续群打卡")
                    risk_paused = True
                    break
            if index < total:
                time.sleep(self._send_interval())

        now = datetime.now()
        summary = f"{mode} {now:%m-%d %H:%M} · 成功 {ok} / 失败 {len(failed)}"
        if risk_paused:
            summary += " · 已触发安全暂停"

        def mutate(state: Any) -> None:
            current: AutoSignConfig = state.config.group_checkin
            if not manual:
                current.last_run = now.date().isoformat()
            current.last_result = summary

        self.store.update(mutate)
        self.logger.info("群打卡结束：%s", summary)
        self._emit("state_changed")
        toast = f"群打卡完成：成功 {ok}，失败 {len(failed)}"
        if risk_paused:
            toast += "；已触发安全暂停 2 小时"
        self._emit(
            "toast",
            message=toast,
            level="error" if risk_paused else ("warning" if failed else "info"),
        )

    def _do_personal_sign(self, *, manual: bool) -> None:
        """QQ 打卡（日签卡）。

        该功能依赖 NapCat 的 ``get_cookies``，失败时只记录到 QQ 打卡配置，
        不抛出到 worker，避免界面把预期内的风控/接口错误显示成程序异常。
        """
        now = datetime.now()
        mode = "手动" if manual else "定时"
        self.logger.info("[%s] 开始 QQ 打卡（日签卡）", mode)

        try:
            client = self._client_or_raise()
            login = self._login_info
            if login is None:
                login = client.get_login_info()
                self._login_info = login
            signer = (
                QQDailySigner(client, sign_url=self._personal_sign_url)
                if self._personal_sign_url
                else QQDailySigner(client)
            )
            result = signer.sign(
                fallback_uin=login.user_id if login is not None else None
            )
            summary = f"{mode} {now:%m-%d %H:%M} · {result.display}"
            self.logger.info("QQ 打卡（日签卡）：%s", result.display)
            toast_level = "info"
            toast_message = f"QQ 打卡：{result.display}"
        except (PersonalSignError, OneBotError) as exc:
            summary = f"{mode} {now:%m-%d %H:%M} · 失败：{exc}"
            self.logger.warning("QQ 打卡（日签卡）失败：%s", exc)
            toast_level = "error"
            toast_message = f"QQ 打卡失败：{exc}"
        except Exception as exc:  # pragma: no cover - 防御性保护
            summary = f"{mode} {now:%m-%d %H:%M} · 异常：{exc}"
            self.logger.exception("QQ 打卡（日签卡）异常")
            toast_level = "error"
            toast_message = f"QQ 打卡异常：{exc}"

        def mutate(state: Any) -> None:
            config: PersonalSignConfig = state.config.personal_sign
            if not manual:
                config.last_run = now.date().isoformat()
            config.last_result = summary

        self.store.update(mutate)
        self._emit("state_changed")
        self._emit("toast", message=toast_message, level=toast_level)

    def _do_spark_check(self, *, manual: bool) -> None:
        """检查最多三个好友今天有没有发消息续火花；未续上则分别提醒。"""
        now = datetime.now()
        today = today_iso(now)
        mode = "手动" if manual else "定时"
        snapshot = self.store.read()
        config: SparkWatchConfig = snapshot.config.spark
        friends = list(config.friends)
        if not friends:
            raise RuntimeError("火花守护没有选择好友")

        self.logger.info(
            "[%s] 检查火花：%s",
            mode,
            "、".join(friend.display for friend in friends),
        )
        states: dict[str, SparkFriendState] = {}
        for friend in friends:
            old = config.statuses.get(friend.key)
            states[friend.key] = (
                SparkFriendState.from_dict(old.to_dict())
                if isinstance(old, SparkFriendState)
                else SparkFriendState()
            )

        client = self._client_or_raise()
        reminded = 0
        failed = 0
        risk_paused = False

        for friend in friends:
            state = states[friend.key]
            if state.confirmed_date == today:
                continue
            try:
                messages = client.get_friend_msg_history(
                    friend.id, count=100, reverse_order=True
                )
            except Exception as exc:
                failed += 1
                state.last_result = (
                    f"{mode} {now:%m-%d %H:%M} · 检查失败：{exc}"
                )
                self.logger.warning(
                    "火花检查失败：%s：%s", friend.display, exc
                )
                if is_risk_error(exc):
                    self.store.update(
                        lambda s: pause_for_risk(
                            s, datetime.now(), str(exc)
                        )
                    )
                    risk_paused = True
                    break
                continue

            latest = latest_message_time(messages, friend.id)
            if contact_sent_today(messages, friend.id, now):
                state.confirmed_date = today
                state.last_result = (
                    f"{mode} {now:%m-%d %H:%M} · 已续上"
                )
                self.logger.info("火花已续上：%s", friend.display)
                continue

            reminders = (
                state.reminders_today if state.reminder_date == today else 0
            )
            sent = False
            send_note = ""
            if reminders >= MAX_REMINDERS_PER_DAY:
                send_note = "已达今日提醒上限，不再发送"
            elif not config.messages:
                send_note = "没有可用的提醒文案"
            else:
                try:
                    self.store.update(
                        lambda s: reserve_messages(s, 1, now)
                    )
                    message = choose_message(config.messages, self._rng)
                    client.send_private_msg(
                        friend.id, build_onebot_message(message)
                    )
                    reminders += 1
                    reminded += 1
                    sent = True
                    self.logger.info(
                        "火花未续上，已提醒 %s（今日第 %d 次）",
                        friend.display,
                        reminders,
                    )
                except SafetyError as exc:
                    send_note = f"安全限制，未发送：{exc}"
                    self.logger.warning("火花提醒被安全限制拦截：%s", exc)
                except Exception as exc:
                    send_note = f"发送失败：{exc}"
                    failed += 1
                    self.logger.warning(
                        "火花提醒发送失败：%s：%s", friend.display, exc
                    )
                    if is_risk_error(exc):
                        self.store.update(
                            lambda s: pause_for_risk(
                                s, datetime.now(), str(exc)
                            )
                        )
                        risk_paused = True

            state.reminder_date = today
            state.reminders_today = reminders
            latest_text = (
                f"；最后来信 {latest:%H:%M}" if latest is not None else ""
            )
            if sent:
                state.last_result = (
                    f"{mode} {now:%m-%d %H:%M} · 未续上，已提醒"
                    f"（今日第 {reminders} 次）{latest_text}"
                )
            else:
                state.last_result = (
                    f"{mode} {now:%m-%d %H:%M} · 未续上，未发送"
                    f"（{send_note or '无'}）{latest_text}"
                )
            if risk_paused:
                break

        pending = any(
            state.confirmed_date != today for state in states.values()
        )
        if pending and not risk_paused:
            next_check = compute_next_check(
                now, last_check_at=now, rng=self._rng
            ).isoformat(timespec="seconds")
        else:
            next_check = ""
        confirmed = sum(
            1 for state in states.values() if state.confirmed_date == today
        )
        summary = (
            f"{mode} {now:%m-%d %H:%M} · 检查 {len(friends)} 人："
            f"已续上 {confirmed}，未续上 {len(friends) - confirmed}，"
            f"已提醒 {reminded}"
        )
        if failed:
            summary += f"，失败 {failed}"
        if risk_paused:
            summary += " · 已触发安全暂停"

        def mutate(current: Any) -> None:
            spark: SparkWatchConfig = current.config.spark
            spark.statuses = states
            spark.last_check_at = now.isoformat(timespec="seconds")
            spark.next_check_at = next_check
            spark.last_result = summary

        self.store.update(mutate)
        self.logger.info("火花检查结束：%s", summary)
        self._emit("state_changed")
        toast = (
            f"火花：已续上 {confirmed}，未续上 "
            f"{len(friends) - confirmed}，提醒 {reminded}"
        )
        if failed:
            toast += f"，失败 {failed}"
        if risk_paused:
            toast += "；已触发安全暂停 2 小时"
        self._emit(
            "toast",
            message=toast,
            level="error" if (risk_paused or failed) else "info",
        )

    def _do_remote_command(self, command: str, self_id: int) -> None:
        if command == "menu":
            reply = MENU_TEXT
        elif command == "query":
            reply = self._remote_query()
        else:
            reply = self._remote_toggle(command)
        if not reply:
            return
        client = self._client_or_raise()
        client.send_private_msg(self_id, reply)
        self.logger.info("已回复手机指令：%s", command)

    def _remote_query(self) -> str:
        state = self.store.read()
        now = datetime.now()
        today = now.date().isoformat()
        tasks = state.tasks
        enabled_tasks = sum(1 for task in tasks if task.enabled)
        checkin = state.config.group_checkin
        personal = state.config.personal_sign
        spark = state.config.spark
        safety = state.safety
        lines = [
            f"{PREFIX}状态",
            f"消息任务：共 {len(tasks)} 个，启用 {enabled_tasks} 个",
            (
                f"群打卡：{'开启' if checkin.enabled else '关闭'}"
                f"（{checkin.time}，{len(checkin.groups)} 个群，"
                f"今日{'已' if checkin.last_run == today else '未'}执行）"
            ),
            (
                f"QQ打卡：{'开启' if personal.enabled else '关闭'}"
                f"（{personal.time}，"
                f"今日{'已' if personal.last_run == today else '未'}执行）"
            ),
        ]
        if not spark.friends:
            lines.append(f"火花：{'开启' if spark.enabled else '关闭'}（未选择好友）")
        else:
            lines.append(
                f"火花：{'开启' if spark.enabled else '关闭'}"
                f"（{len(spark.friends)}/3 人）"
            )
            for friend in spark.friends:
                state = spark.statuses.get(friend.key) or SparkFriendState()
                spark_state = (
                    "已续上"
                    if state.confirmed_date == today
                    else "未确认"
                )
                lines.append(
                    f"  {friend.name}：{spark_state}，"
                    f"今日提醒 {state.reminders_today}/3"
                )
            if spark.next_check_at:
                lines.append(
                    f"下次火花检查："
                    f"{spark.next_check_at[5:16].replace('T', ' ')}"
                )
        lines.append(
            f"今日用量：消息 {safety.messages}/100，"
            f"群打卡 {safety.sign_groups}/50"
        )
        if safety.paused_until:
            lines.append(f"安全暂停至：{safety.paused_until[5:16].replace('T', ' ')}")
        if checkin.last_result:
            lines.append(f"群打卡最近：{_short(checkin.last_result)}")
        if personal.last_result:
            lines.append(f"QQ打卡最近：{_short(personal.last_result)}")
        if spark.last_result:
            lines.append(f"火花最近：{_short(spark.last_result)}")
        return "\n".join(lines)

    def _remote_toggle(self, command: str) -> str:
        now = datetime.now()
        today = now.date().isoformat()
        messages: list[str] = []

        def mutate(state: Any) -> None:
            config = state.config
            checkin = config.group_checkin
            spark = config.spark
            if command in {"tasks_on", "tasks_off", "all_on", "all_off"}:
                enabled = command in {"tasks_on", "all_on"}
                for task in state.tasks:
                    task.enabled = enabled
                messages.append(
                    f"消息任务：{'全部开启' if enabled else '全部关闭'}"
                    f"（{len(state.tasks)} 个）"
                )
            if command in {"checkin_on", "all_on"}:
                if checkin.groups:
                    checkin.enabled = True
                    messages.append("群打卡：已开启")
                elif command == "checkin_on":
                    messages.append("群打卡：没有选群，未开启")
            elif command in {"checkin_off", "all_off"}:
                checkin.enabled = False
                messages.append("群打卡：已关闭")
            if command in {"spark_on", "all_on"}:
                if spark.friends and spark.messages:
                    spark.enabled = True
                    if spark.pending(today):
                        spark.next_check_at = compute_next_check(
                            now,
                            last_check_at=parse_dt(spark.last_check_at),
                            rng=self._rng,
                        ).isoformat(timespec="seconds")
                    messages.append(
                        f"火花：已开启（{len(spark.friends)} 人）"
                    )
                elif command == "spark_on":
                    messages.append("火花：没有好友或文案，未开启")
            elif command in {"spark_off", "all_off"}:
                spark.enabled = False
                messages.append("火花：已关闭")
            if command in {"personal_on", "all_on"}:
                config.personal_sign.enabled = True
                messages.append("QQ打卡：已开启")
            elif command in {"personal_off", "all_off"}:
                config.personal_sign.enabled = False
                messages.append("QQ打卡：已关闭")

        self.store.update(mutate)
        self._emit("state_changed")
        return f"{PREFIX}已执行\n" + "\n".join(messages)

    # ------------------------------------------------------------------ 小工具
    def _send_interval(self) -> float:
        return self.store.read().config.send_interval


def _short(text: str, limit: int = 70) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"
