"""QQ 打卡（日签卡）个人签到。

对应手机 QQ 左上角“已打卡”进入的“QQ打卡”页面里的日签卡按钮。

实现方式：
1. 调用 NapCat ``get_cookies(domain="ti.qq.com")`` 获取当前 QQ 的 Cookie；
2. 从 Cookie 中取 ``p_skey`` 和 ``uin``；
3. 向 ``daily_attendance/SignIn`` 提交 ``type=1`` 的日签卡打卡请求。

这是手机 QQ 的网页接口，不是腾讯官方开放 API；接口或风控变化时可能失效。
"""

from __future__ import annotations

import gzip
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .onebot import OneBotClient

LOGGER = logging.getLogger("qq_signin.qq_daily")

DAILY_SIGN_URL = "https://ti.qq.com/hybrid-h5/api/json/daily_attendance/SignIn"
DAILY_COOKIE_DOMAINS = ("ti.qq.com", "qq.com", "qzone.qq.com")

# 手机 QQ WebView 的 QUA，接口用来区分客户端版本
DEFAULT_QUA = "V1_AND_SQ_8.9.25_3640_YYB_D"
_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 Mobile Safari/537.36 "
    "QQ/8.9.25.3640"
)


class PersonalSignError(RuntimeError):
    """个人打卡失败。"""


@dataclass(frozen=True)
class PersonalSignResult:
    """一次 QQ 打卡的结果。"""

    success: bool
    already: bool
    message: str
    code: int | None = None
    days: int | None = None
    raw: str = ""

    @property
    def display(self) -> str:
        if self.already:
            return "今天已经打卡过了"
        if self.success and self.days:
            return f"打卡成功，已连续 {self.days} 天"
        if self.success:
            return "打卡成功"
        return self.message


class QQDailySigner:
    """QQ 打卡（日签卡）执行器。"""

    def __init__(
        self,
        onebot: OneBotClient,
        *,
        sign_url: str = DAILY_SIGN_URL,
        timeout: float = 20.0,
        qua: str = DEFAULT_QUA,
    ) -> None:
        self.onebot = onebot
        self.sign_url = sign_url
        self.timeout = max(3.0, float(timeout))
        self.qua = qua
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    def sign(self, *, fallback_uin: int | None = None) -> PersonalSignResult:
        """执行一次 QQ 日签卡打卡。"""
        cookies, domain = self._get_cookies()
        p_skey = cookie_value(cookies, "p_skey")
        if not p_skey:
            raise PersonalSignError(
                f"从 NapCat 获取的 {domain} Cookie 中没有 p_skey。"
                "请确认 NapCat 已登录，并在手机 QQ 打开一次“QQ打卡”页面。"
            )
        uin = cookie_uin(cookies) or (str(fallback_uin) if fallback_uin else "")
        if not uin.isdigit():
            raise PersonalSignError(
                "无法从 Cookie 或登录信息中获取 QQ 号，不能执行 QQ 打卡。"
            )

        body = {
            "uin": int(uin),
            "type": 1,  # 1 = 日签卡
            "sId": "",
            "qua": self.qua,
            "mpExtend": {
                "tianshuAdsReq": json.dumps(
                    {
                        "app": "QQ",
                        "os": "AND",
                        "version": "8.9.25.3640",
                        "imei": "",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            },
        }
        request = urllib.request.Request(
            self.sign_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cookie": cookies,
                "User-Agent": _USER_AGENT,
                "Referer": "https://ti.qq.com/signin/public/index.html",
                "Origin": "https://ti.qq.com",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "identity",
            },
            method="POST",
        )

        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw_body = response.read()
                status = response.status
                content_encoding = (
                    response.headers.get("Content-Encoding") or ""
                ).lower()
        except urllib.error.HTTPError as exc:
            text = _decode(exc.read())
            raise PersonalSignError(
                f"QQ 打卡接口 HTTP {exc.code}：{text[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise PersonalSignError(
                f"无法连接 QQ 打卡接口：{exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise PersonalSignError(
                f"QQ 打卡接口超时（{self.timeout:.0f}s）"
            ) from exc

        if "gzip" in content_encoding:
            try:
                raw_body = gzip.decompress(raw_body)
            except OSError:
                pass
        text = _decode(raw_body)
        if status != 200:
            raise PersonalSignError(f"QQ 打卡接口返回 HTTP {status}")

        payload = _parse_payload(text)
        if payload is None:
            raise PersonalSignError(
                "QQ 打卡接口没有返回 JSON，可能是 Cookie 失效或被重定向到登录页。"
                f"响应开头：{text[:200]!r}"
            )

        code = _first_int(payload, ("retCode", "ret", "code"))
        message = _first_text(payload, ("msg", "message", "wording"))
        days = _first_int(payload, ("totalDays", "continuousDays", "days"))
        nested = payload.get("data")
        if isinstance(nested, dict):
            if code is None:
                code = _first_int(nested, ("retCode", "ret", "code"))
            if not message:
                message = _first_text(nested, ("msg", "message", "wording"))
            if days is None:
                days = _first_int(
                    nested, ("totalDays", "continuousDays", "days")
                )

        if code == 0:
            return PersonalSignResult(
                success=True,
                already=False,
                message=message or "打卡成功",
                code=code,
                days=days,
                raw=text,
            )
        if code == 1:
            return PersonalSignResult(
                success=True,
                already=True,
                message=message or "今天已经打卡过了",
                code=code,
                days=days,
                raw=text,
            )

        # -3000 通常代表 Cookie 无效/请求未登录；给出可操作提示
        if code == -3000:
            raise PersonalSignError(
                "QQ 打卡接口返回 -3000：Cookie 可能失效，"
                "请重新登录 NapCat，并在手机 QQ 打开一次“QQ打卡”页面。"
            )
        raise PersonalSignError(
            f"QQ 打卡失败：code={code}，{message or '接口未返回原因'}"
        )

    # ------------------------------------------------------------------ Cookie
    def _get_cookies(self) -> tuple[str, str]:
        errors: list[str] = []
        for domain in DAILY_COOKIE_DOMAINS:
            try:
                data = self.onebot.get_cookies(domain)
            except Exception as exc:
                errors.append(f"{domain}: {exc}")
                continue
            cookies = str(data.get("cookies", "") or "")
            if cookie_value(cookies, "p_skey"):
                LOGGER.info("已从 NapCat 获取 %s 的 QQ 打卡 Cookie", domain)
                return cookies, domain
            errors.append(f"{domain}: 缺少 p_skey")
        detail = "；".join(errors[-3:]) if errors else "没有返回 Cookie"
        raise PersonalSignError(
            "无法从 NapCat 获取 QQ 打卡所需的 p_skey。请确认 NapCat 已登录，"
            "并先在手机 QQ 打开一次“QQ打卡”页面。详情：" + detail
        )


def cookie_value(cookies: str, name: str) -> str:
    """从 ``a=1; b=2`` 形式的 Cookie 中取指定值。"""
    for item in (cookies or "").split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key.strip() == name:
            value = value.strip().strip('"')
            return urllib.parse.unquote(value)
    return ""


def cookie_uin(cookies: str) -> str:
    """从 Cookie 中提取纯数字 QQ 号。"""
    for name in ("uin", "p_uin", "wxuin", "o_cookie"):
        value = cookie_value(cookies, name)
        if not value:
            continue
        if value.startswith("o"):
            value = value[1:]
        value = value.lstrip("_")
        if value.isdigit():
            return value
    return ""


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_payload(text: str) -> dict[str, Any] | None:
    """兼容纯 JSON、JSONP 和 callback(...) 包装。"""
    stripped = text.strip().lstrip("\ufeff")
    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key not in payload:
            continue
        try:
            return int(payload[key])
        except (TypeError, ValueError):
            continue
    return None


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return ""
