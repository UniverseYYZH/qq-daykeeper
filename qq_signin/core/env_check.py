"""运行环境检查。

既供命令行脚本使用，也可以被界面调用。所有检查都不修改用户数据，
唯一的写操作是在数据目录里创建一个临时文件后立即删除。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .onebot import OneBotClient
from .qq_daily import cookie_value


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool | None  # True 通过，False 失败，None 警告/跳过
    detail: str = ""

    @property
    def mark(self) -> str:
        if self.ok is True:
            return "✓"
        if self.ok is False:
            return "✗"
        return "!"


def check_local(data_dir: Path | str) -> list[CheckResult]:
    """检查 Python、Tkinter、数据目录写权限。"""
    results: list[CheckResult] = []

    version = ".".join(str(x) for x in sys.version_info[:3])
    results.append(
        CheckResult(
            "Python 版本",
            sys.version_info >= (3, 10),
            f"{version}（需要 3.10+）",
        )
    )

    try:
        import tkinter

        results.append(
            CheckResult(
                "Tkinter",
                True,
                f"Tk {tkinter.TkVersion}，可用",
            )
        )
    except Exception as exc:  # pragma: no cover - 依赖 Python 安装方式
        results.append(CheckResult("Tkinter", False, f"不可用：{exc}"))

    directory = Path(data_dir)
    probe = directory / ".write-test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        results.append(CheckResult("数据目录可写", True, str(directory)))
    except OSError as exc:
        results.append(CheckResult("数据目录可写", False, f"{directory}：{exc}"))

    return results


def check_onebot(
    url: str,
    token: str = "",
    *,
    timeout: float = 30.0,
) -> list[CheckResult]:
    """检查 OneBot 连接、联系人和 QQ 打卡 Cookie。

    注意：这是同步网络调用，命令行使用没问题；界面里请放到后台线程，
    或者直接使用“测试连接”按钮。
    """
    results: list[CheckResult] = []
    try:
        client = OneBotClient(url, token, timeout=timeout)
        login = client.get_login_info()
        results.append(
            CheckResult("OneBot 连接", True, f"登录账号：{login.display}")
        )
    except Exception as exc:
        results.append(CheckResult("OneBot 连接", False, str(exc)))
        return results

    try:
        groups = client.get_group_list()
        friends = client.get_friend_list()
        results.append(CheckResult("群聊列表", True, f"{len(groups)} 个群"))
        results.append(CheckResult("好友列表", True, f"{len(friends)} 个好友"))
    except Exception as exc:
        results.append(CheckResult("联系人列表", False, str(exc)))

    try:
        data = client.get_cookies("ti.qq.com")
        cookies = str(data.get("cookies", "") or "")
        if cookie_value(cookies, "p_skey"):
            results.append(
                CheckResult("QQ 打卡 Cookie", True, "包含 p_skey，可尝试 QQ 打卡")
            )
        else:
            results.append(
                CheckResult(
                    "QQ 打卡 Cookie",
                    None,
                    "没有 p_skey，QQ 打卡可能失败；请先在手机 QQ 打开“QQ打卡”页面",
                )
            )
    except Exception as exc:
        results.append(
            CheckResult(
                "QQ 打卡 Cookie",
                None,
                f"get_cookies 调用失败（可能是 NapCat 版本较旧）：{exc}",
            )
        )

    return results


def format_results(results: list[CheckResult]) -> str:
    lines = []
    for item in results:
        lines.append(f"[{item.mark}] {item.name}：{item.detail}")
    return "\n".join(lines)


def has_failure(results: list[CheckResult]) -> bool:
    return any(item.ok is False for item in results)
