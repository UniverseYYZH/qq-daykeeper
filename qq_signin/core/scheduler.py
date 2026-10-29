"""到点判断与后台调度线程。

调度器不直接发网络请求，它只负责：
1. 找到当前分钟应该执行的任务；
2. 把 ``last_run`` 标记为今天，避免同一分钟重复触发；
3. 交给执行服务的工作线程。

这样即使发送 50 个群很慢，也不会阻塞“到点检查”。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from .models import AppState
from .spark import START_TIME, compute_next_check, parse_dt

LOGGER = logging.getLogger("qq_signin.scheduler")


@dataclass(frozen=True)
class DueJob:
    """一条待执行任务。"""

    kind: str  # task | sign | checkin
    ref_id: str  # task 的 id；打卡类为空
    label: str  # 给日志/界面看的名称


def collect_due_jobs(state: AppState, now: datetime) -> list[DueJob]:
    """找出 ``now`` 这一分钟到点的任务，并就地写入执行标记。

    注意：本函数会修改 ``state``。调用方应把它放在
    ``StateStore.update(...)`` 里，保证标记和读取是原子的。
    """
    today = now.date().isoformat()
    current_time = now.strftime("%H:%M")
    due: list[DueJob] = []

    for task in state.tasks:
        if not task.enabled:
            continue
        if task.time != current_time:
            continue
        if task.last_run == today:
            continue
        if task.repeat == "once" and task.date != today:
            continue

        task.last_run = today
        task.last_result = f"{now:%m-%d %H:%M} 已触发，等待执行"
        due.append(DueJob(kind="task", ref_id=task.id, label=task.name))

    # 群打卡
    sign_items = (
        ("checkin", "群打卡", state.config.group_checkin),
    )
    for kind, label, config in sign_items:
        if not config.enabled:
            continue
        if config.time != current_time:
            continue
        if config.last_run == today:
            continue
        if not config.groups:
            continue

        config.last_run = today
        config.last_result = f"{now:%m-%d %H:%M} 已触发，等待执行"
        due.append(DueJob(kind=kind, ref_id="", label=label))

    # QQ 打卡（日签卡）
    personal = state.config.personal_sign
    if (
        personal.enabled
        and personal.time == current_time
        and personal.last_run != today
    ):
        personal.last_run = today
        personal.last_result = f"{now:%m-%d %H:%M} 已触发，等待执行"
        due.append(DueJob(kind="personal", ref_id="", label="QQ 打卡（日签卡）"))

    # 好友火花守护：next_check_at 是绝对时间，支持 45–75 分钟随机间隔
    spark = state.config.spark
    if spark.enabled and spark.friends and spark.pending(today):
        if not spark.next_check_at:
            next_at = compute_next_check(
                now, last_check_at=parse_dt(spark.last_check_at)
            )
            spark.next_check_at = next_at.isoformat(timespec="seconds")
        due_at = parse_dt(spark.next_check_at)
        start_today = datetime.combine(now.date(), START_TIME)
        # 昨天的检查时间不应该在第二天上午补触发
        if due_at is not None and due_at < start_today:
            due_at = compute_next_check(
                now, last_check_at=parse_dt(spark.last_check_at)
            )
            spark.next_check_at = due_at.isoformat(timespec="seconds")
        if due_at is not None and due_at <= now:
            spark.next_check_at = ""
            due.append(DueJob(kind="spark", ref_id="", label="好友火花检查"))

    return due


class Scheduler(threading.Thread):
    """每 5 秒检查一次时间。

    没有使用 cron 库，是为了保持零第三方依赖；对“每天某个 HH:MM”
    这种需求，5 秒轮询已经足够，而且程序休眠/恢复后也能自然恢复。
    """

    def __init__(self, service: object, interval: float = 5.0) -> None:
        super().__init__(name="qq-signin-scheduler", daemon=True)
        self.service = service
        self.interval = max(1.0, float(interval))
        self._stop_event = threading.Event()

    def run(self) -> None:
        # 启动时立即检查一次，避免刚好在目标分钟启动却错过
        self._safe_tick()
        while not self._stop_event.wait(self.interval):
            self._safe_tick()

    def _safe_tick(self) -> None:
        try:
            self.service.tick()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 防御性保护
            LOGGER.exception("调度检查出现未预期异常")

    def stop(self) -> None:
        self._stop_event.set()
