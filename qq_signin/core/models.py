"""应用数据模型。

所有需要落盘的对象都提供 ``to_dict`` / ``from_dict``，方便直接查看和手改
``data/state.json``。这里的字段尽量保持简单，避免把网络层对象混进来。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# 目标类型
KIND_GROUP = "group"
KIND_FRIEND = "friend"
KIND_LABELS = {KIND_GROUP: "群聊", KIND_FRIEND: "好友"}
VALID_KINDS = frozenset(KIND_LABELS)

# 重复方式
REPEAT_DAILY = "daily"
REPEAT_ONCE = "once"
REPEAT_LABELS = {REPEAT_DAILY: "每天", REPEAT_ONCE: "仅一次"}

# 允许 8:05 和 08:05 两种写法，输出统一补零
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_time(value: str) -> str:
    """把 ``8:05`` 统一成 ``08:05``；格式不合法时抛 ``ValueError``。"""
    text = str(value).strip()
    match = _TIME_RE.fullmatch(text)
    if not match:
        raise ValueError(f"时间格式应为 HH:MM，收到：{value!r}")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def is_valid_date(value: str) -> bool:
    """判断是否为 ``YYYY-MM-DD`` 且真实存在的日期。"""
    try:
        date.fromisoformat(str(value).strip())
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class Target:
    """一个发送目标：群聊或好友。

    ``id`` 对群聊是群号，对好友是 QQ 号。名称只用于界面展示；
    即使好友改昵称，仍按 ``id`` 发送。
    """

    kind: str
    id: int
    name: str = ""

    def __post_init__(self) -> None:
        kind = str(self.kind).strip().lower()
        if kind not in VALID_KINDS:
            raise ValueError(f"未知目标类型：{self.kind!r}")
        target_id = int(self.id)
        if target_id <= 0:
            raise ValueError(f"目标 ID 必须为正整数：{self.id!r}")
        name = str(self.name).strip() or str(target_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "id", target_id)
        object.__setattr__(self, "name", name)

    @property
    def key(self) -> str:
        """稳定的唯一键，例如 ``group:123456``。"""
        return f"{self.kind}:{self.id}"

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def display(self) -> str:
        return f"{self.kind_label} · {self.name}（{self.id}）"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Target":
        return cls(kind=raw["kind"], id=raw["id"], name=raw.get("name", ""))


@dataclass
class Task:
    """一条定时消息任务。"""

    name: str
    time: str
    message: str
    targets: list[Target] = field(default_factory=list)
    repeat: str = REPEAT_DAILY
    date: str = ""  # repeat == once 时生效，格式 YYYY-MM-DD
    enabled: bool = True
    last_run: str = ""  # 最近一次自动执行的日期 YYYY-MM-DD
    last_result: str = ""  # 最近一次执行结果，给人看的短文本
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = ""

    def __post_init__(self) -> None:
        self.name = str(self.name).strip()
        self.message = str(self.message)
        self.repeat = str(self.repeat).strip().lower()
        self.date = str(self.date).strip()
        self.last_run = str(self.last_run).strip()
        self.last_result = str(self.last_result).strip()
        self.id = str(self.id).strip() or uuid.uuid4().hex
        self.enabled = bool(self.enabled)
        # 时间字段如果可规范化就立即规范化，避免调度器比较 "8:00" 和 "08:00" 失败
        try:
            self.time = normalize_time(self.time)
        except ValueError:
            self.time = str(self.time).strip()
        self.targets = _dedupe_targets(self.targets)

    @property
    def repeat_label(self) -> str:
        if self.repeat == REPEAT_ONCE:
            return f"仅一次 {self.date}".strip()
        return REPEAT_LABELS.get(self.repeat, self.repeat)

    @property
    def target_summary(self) -> str:
        groups = sum(1 for t in self.targets if t.kind == KIND_GROUP)
        friends = sum(1 for t in self.targets if t.kind == KIND_FRIEND)
        parts = []
        if groups:
            parts.append(f"{groups} 群")
        if friends:
            parts.append(f"{friends} 好友")
        return " + ".join(parts) or "无目标"

    def validate(self) -> list[str]:
        """返回所有校验错误；空列表表示可以保存/执行。"""
        errors: list[str] = []
        if not self.name:
            errors.append("任务名称不能为空")
        try:
            normalize_time(self.time)
        except ValueError as exc:
            errors.append(str(exc))
        if self.repeat not in REPEAT_LABELS:
            errors.append(f"重复方式不合法：{self.repeat!r}")
        if self.repeat == REPEAT_ONCE:
            if not self.date:
                errors.append("“仅一次”任务必须填写日期")
            elif not is_valid_date(self.date):
                errors.append(f"日期格式应为 YYYY-MM-DD，收到：{self.date!r}")
        if not self.message.strip():
            errors.append("发送文案不能为空")
        if not self.targets:
            errors.append("至少选择一个群聊或好友")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "time": self.time,
            "message": self.message,
            "targets": [t.to_dict() for t in self.targets],
            "repeat": self.repeat,
            "date": self.date,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_result": self.last_result,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Task":
        targets: list[Target] = []
        for item in raw.get("targets", []) or []:
            try:
                targets.append(Target.from_dict(item))
            except (KeyError, TypeError, ValueError):
                # 单条脏数据不应该导致整个配置文件无法加载
                continue
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            time=raw.get("time", ""),
            message=raw.get("message", ""),
            targets=targets,
            repeat=raw.get("repeat", REPEAT_DAILY),
            date=raw.get("date", ""),
            enabled=raw.get("enabled", True),
            last_run=raw.get("last_run", ""),
            last_result=raw.get("last_result", ""),
            created_at=raw.get("created_at", ""),
        )


@dataclass
class AutoSignConfig:
    """群打卡的自动执行配置。"""

    enabled: bool = False
    time: str = "08:00"
    groups: list[Target] = field(default_factory=list)
    last_run: str = ""  # 最近一次自动执行的日期
    last_result: str = ""

    def __post_init__(self) -> None:
        try:
            self.time = normalize_time(self.time)
        except ValueError:
            self.time = str(self.time).strip()
        self.groups = _dedupe_targets(
            [g for g in self.groups if g.kind == KIND_GROUP]
        )
        self.last_run = str(self.last_run).strip()
        self.last_result = str(self.last_result).strip()

    def validate(self) -> list[str]:
        errors: list[str] = []
        try:
            normalize_time(self.time)
        except ValueError as exc:
            errors.append(str(exc))
        if self.enabled and not self.groups:
            errors.append("启用自动执行时至少选择一个群")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "time": self.time,
            "groups": [g.to_dict() for g in self.groups],
            "last_run": self.last_run,
            "last_result": self.last_result,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], default_time: str) -> "AutoSignConfig":
        groups: list[Target] = []
        for item in raw.get("groups", []) or []:
            try:
                target = Target.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if target.kind == KIND_GROUP:
                groups.append(target)
        return cls(
            enabled=raw.get("enabled", False),
            time=raw.get("time", default_time),
            groups=groups,
            last_run=raw.get("last_run", ""),
            last_result=raw.get("last_result", ""),
        )


@dataclass
class PersonalSignConfig:
    """QQ 空间个人签到配置。"""

    enabled: bool = False
    time: str = "08:10"
    last_run: str = ""  # 最近一次自动执行的日期
    last_result: str = ""

    def __post_init__(self) -> None:
        try:
            self.time = normalize_time(self.time)
        except ValueError:
            self.time = str(self.time).strip()
        self.last_run = str(self.last_run).strip()
        self.last_result = str(self.last_result).strip()

    def validate(self) -> list[str]:
        try:
            normalize_time(self.time)
        except ValueError as exc:
            return [str(exc)]
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "time": self.time,
            "last_run": self.last_run,
            "last_result": self.last_result,
        }

    @classmethod
    def from_dict(
        cls, raw: dict[str, Any], default_time: str = "08:10"
    ) -> "PersonalSignConfig":
        return cls(
            enabled=raw.get("enabled", False),
            time=raw.get("time", default_time),
            last_run=raw.get("last_run", ""),
            last_result=raw.get("last_result", ""),
        )


DEFAULT_SPARK_MESSAGES = (
    "在吗？今天火花还没续上，记得回我一下～",
    "火花要断了，快回我一条消息！",
    "今日火花提醒：等你一条消息续上火花。",
    "别让我们的火花灭掉，回我一下～",
)


@dataclass
class SparkFriendState:
    """一个好友的火花检查状态。"""

    confirmed_date: str = ""
    reminder_date: str = ""
    reminders_today: int = 0
    last_result: str = ""

    def __post_init__(self) -> None:
        self.confirmed_date = str(self.confirmed_date).strip()
        self.reminder_date = str(self.reminder_date).strip()
        self.reminders_today = max(0, int(self.reminders_today or 0))
        self.last_result = str(self.last_result).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed_date": self.confirmed_date,
            "reminder_date": self.reminder_date,
            "reminders_today": self.reminders_today,
            "last_result": self.last_result,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SparkFriendState":
        return cls(
            confirmed_date=raw.get("confirmed_date", ""),
            reminder_date=raw.get("reminder_date", ""),
            reminders_today=raw.get("reminders_today", 0),
            last_result=raw.get("last_result", ""),
        )


@dataclass
class SparkWatchConfig:
    """好友火花守护：最多三个好友，18:00 后随机检查，23:50 最终检查。"""

    enabled: bool = False
    friends: list[Target] = field(default_factory=list)
    messages: list[str] = field(default_factory=lambda: list(DEFAULT_SPARK_MESSAGES))
    statuses: dict[str, SparkFriendState] = field(default_factory=dict)
    next_check_at: str = ""  # 本地 ISO 时间
    last_check_at: str = ""
    last_result: str = ""  # 全局摘要

    def __post_init__(self) -> None:
        cleaned_friends: list[Target] = []
        seen: set[str] = set()
        for friend in self.friends:
            if friend.kind != KIND_FRIEND:
                raise ValueError("火花守护只能选择好友")
            if friend.key in seen:
                continue
            seen.add(friend.key)
            cleaned_friends.append(friend)
        # 旧数据/手动改文件最多保留三个，保存时会进一步校验
        self.friends = cleaned_friends[:3]
        cleaned_messages: list[str] = []
        for message in self.messages:
            text = str(message).strip()
            if text and text not in cleaned_messages:
                cleaned_messages.append(text)
        self.messages = cleaned_messages
        self.statuses = {
            key: value
            for key, value in self.statuses.items()
            if key in {friend.key for friend in self.friends}
            and isinstance(value, SparkFriendState)
        }
        self.next_check_at = str(self.next_check_at).strip()
        self.last_check_at = str(self.last_check_at).strip()
        self.last_result = str(self.last_result).strip()

    def pending(self, today: str) -> bool:
        return any(
            (self.statuses.get(friend.key) or SparkFriendState()).confirmed_date
            != today
            for friend in self.friends
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.enabled and not self.friends:
            errors.append("启用火花守护前，至少选择一个好友")
        if len(self.friends) > 3:
            errors.append("火花守护最多选择 3 个好友")
        if any(friend.kind != KIND_FRIEND for friend in self.friends):
            errors.append("火花守护只能选择好友")
        if self.enabled and not self.messages:
            errors.append("至少保留一条提醒文案")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "friends": [friend.to_dict() for friend in self.friends],
            "messages": list(self.messages),
            "statuses": {
                key: value.to_dict() for key, value in self.statuses.items()
            },
            "next_check_at": self.next_check_at,
            "last_check_at": self.last_check_at,
            "last_result": self.last_result,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SparkWatchConfig":
        friends: list[Target] = []
        raw_friends = raw.get("friends")
        if isinstance(raw_friends, list):
            for item in raw_friends:
                try:
                    friends.append(Target.from_dict(item))
                except (KeyError, TypeError, ValueError):
                    continue
        elif raw.get("friend"):
            # 兼容旧版单好友配置
            try:
                friends.append(Target.from_dict(raw["friend"]))
            except (KeyError, TypeError, ValueError):
                pass

        statuses: dict[str, SparkFriendState] = {}
        raw_statuses = raw.get("statuses")
        if isinstance(raw_statuses, dict):
            for key, value in raw_statuses.items():
                if isinstance(value, dict):
                    try:
                        statuses[str(key)] = SparkFriendState.from_dict(value)
                    except (TypeError, ValueError):
                        continue
        # 兼容旧版单好友的顶层状态字段
        if friends and not statuses and any(
            key in raw
            for key in ("confirmed_date", "reminder_date", "reminders_today")
        ):
            statuses[friends[0].key] = SparkFriendState(
                confirmed_date=raw.get("confirmed_date", ""),
                reminder_date=raw.get("reminder_date", ""),
                reminders_today=raw.get("reminders_today", 0),
                last_result=raw.get("last_result", ""),
            )

        messages = raw.get("messages", list(DEFAULT_SPARK_MESSAGES))
        if not isinstance(messages, list):
            messages = list(DEFAULT_SPARK_MESSAGES)
        return cls(
            enabled=raw.get("enabled", False),
            friends=friends,
            messages=[str(m) for m in messages],
            statuses=statuses,
            next_check_at=raw.get("next_check_at", ""),
            last_check_at=raw.get("last_check_at", ""),
            last_result=raw.get("last_result", ""),
        )


@dataclass
class RemoteConfig:
    """手机 QQ 远程控制：本机接收 NapCat 事件上报。"""

    enabled: bool = True
    port: int = 18080
    token: str = ""

    def __post_init__(self) -> None:
        try:
            self.port = int(self.port)
        except (TypeError, ValueError):
            self.port = 18080
        if not 1024 <= self.port <= 65535:
            self.port = 18080
        self.token = str(self.token).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "port": self.port,
            "token": self.token,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RemoteConfig":
        return cls(
            enabled=raw.get("enabled", True),
            port=raw.get("port", 18080),
            token=raw.get("token", ""),
        )


@dataclass
class AppConfig:
    """连接与全局行为配置。"""

    onebot_url: str = "http://127.0.0.1:3000"
    token: str = ""
    send_interval: float = 8.0
    napcat_webui: str = "http://127.0.0.1:6099"
    napcat_config: str = ""
    auto_connect: bool = True
    group_checkin: AutoSignConfig = field(
        default_factory=lambda: AutoSignConfig(time="08:05")
    )
    personal_sign: PersonalSignConfig = field(
        default_factory=lambda: PersonalSignConfig(time="08:10")
    )
    spark: SparkWatchConfig = field(default_factory=SparkWatchConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)

    def __post_init__(self) -> None:
        self.onebot_url = str(self.onebot_url).strip().rstrip("/")
        self.token = str(self.token).strip()
        self.napcat_webui = str(self.napcat_webui).strip().rstrip("/")
        self.napcat_config = str(self.napcat_config).strip()
        try:
            self.send_interval = max(5.0, min(60.0, float(self.send_interval)))
        except (TypeError, ValueError):
            self.send_interval = 8.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "onebot_url": self.onebot_url,
            "token": self.token,
            "send_interval": self.send_interval,
            "napcat_webui": self.napcat_webui,
            "napcat_config": self.napcat_config,
            "auto_connect": self.auto_connect,
            "group_checkin": self.group_checkin.to_dict(),
            "personal_sign": self.personal_sign.to_dict(),
            "spark": self.spark.to_dict(),
            "remote": self.remote.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        return cls(
            onebot_url=raw.get("onebot_url", "http://127.0.0.1:3000"),
            token=raw.get("token", ""),
            send_interval=raw.get("send_interval", 8.0),
            napcat_webui=raw.get("napcat_webui", "http://127.0.0.1:6099"),
            napcat_config=raw.get("napcat_config", ""),
            auto_connect=raw.get("auto_connect", True),
            group_checkin=AutoSignConfig.from_dict(
                raw.get("group_checkin", {}) or {}, "08:05"
            ),
            personal_sign=PersonalSignConfig.from_dict(
                raw.get("personal_sign", {}) or {}, "08:10"
            ),
            spark=SparkWatchConfig.from_dict(raw.get("spark", {}) or {}),
            remote=RemoteConfig.from_dict(raw.get("remote", {}) or {}),
        )


@dataclass
class Contacts:
    """最近一次同步到的联系人快照，断网时也能打开界面。"""

    groups: list[Target] = field(default_factory=list)
    friends: list[Target] = field(default_factory=list)
    updated_at: str = ""

    @property
    def all(self) -> list[Target]:
        return [*self.groups, *self.friends]

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [g.to_dict() for g in self.groups],
            "friends": [f.to_dict() for f in self.friends],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Contacts":
        def parse(items: Any) -> list[Target]:
            result: list[Target] = []
            for item in items or []:
                try:
                    result.append(Target.from_dict(item))
                except (KeyError, TypeError, ValueError):
                    continue
            return result

        return cls(
            groups=parse(raw.get("groups")),
            friends=parse(raw.get("friends")),
            updated_at=str(raw.get("updated_at", "")),
        )


@dataclass
class SafetyStats:
    """每日发送计数与风控暂停状态，用于硬性限流。"""

    date: str = ""
    messages: int = 0
    sign_groups: int = 0
    paused_until: str = ""
    last_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "messages": self.messages,
            "sign_groups": self.sign_groups,
            "paused_until": self.paused_until,
            "last_reason": self.last_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SafetyStats":
        return cls(
            date=str(raw.get("date", "")),
            messages=int(raw.get("messages", 0) or 0),
            sign_groups=int(raw.get("sign_groups", 0) or 0),
            paused_until=str(raw.get("paused_until", "")),
            last_reason=str(raw.get("last_reason", "")),
        )


@dataclass
class AppState:
    """整个应用需要持久化的状态。"""

    version: int = 1
    config: AppConfig = field(default_factory=AppConfig)
    contacts: Contacts = field(default_factory=Contacts)
    tasks: list[Task] = field(default_factory=list)
    safety: SafetyStats = field(default_factory=SafetyStats)

    def find_task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def upsert_task(self, task: Task) -> None:
        for index, existing in enumerate(self.tasks):
            if existing.id == task.id:
                self.tasks[index] = task
                return
        self.tasks.append(task)

    def remove_task(self, task_id: str) -> bool:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        return len(self.tasks) != before

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "config": self.config.to_dict(),
            "contacts": self.contacts.to_dict(),
            "tasks": [t.to_dict() for t in self.tasks],
            "safety": self.safety.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppState":
        tasks: list[Task] = []
        for item in raw.get("tasks", []) or []:
            try:
                tasks.append(Task.from_dict(item))
            except (TypeError, ValueError):
                continue
        return cls(
            version=int(raw.get("version", 1) or 1),
            config=AppConfig.from_dict(raw.get("config", {}) or {}),
            contacts=Contacts.from_dict(raw.get("contacts", {}) or {}),
            tasks=tasks,
            safety=SafetyStats.from_dict(raw.get("safety", {}) or {}),
        )


@dataclass(frozen=True)
class LoginInfo:
    """``get_login_info`` 返回的当前登录账号。"""

    user_id: int
    nickname: str = ""

    @property
    def display(self) -> str:
        return f"{self.nickname}（{self.user_id}）" if self.nickname else str(self.user_id)


def _dedupe_targets(targets: list[Target]) -> list[Target]:
    """按 kind:id 去重，同时保持原顺序。"""
    seen: set[str] = set()
    result: list[Target] = []
    for target in targets:
        if target.key in seen:
            continue
        seen.add(target.key)
        result.append(target)
    return result
