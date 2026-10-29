"""OneBot 11 HTTP 客户端。

只实现本项目用到的接口，协议格式参考：
https://github.com/botuniverse/onebot-11

约定：
- 配置里的地址是“API 根地址”，例如 http://127.0.0.1:3000；
- 每次调用 POST 到根地址，JSON 体为 {"action": "...", "params": {...}}；
- 如果配置了 token，会带 ``Authorization: Bearer <token>``。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import KIND_FRIEND, KIND_GROUP, LoginInfo, Target

LOGGER = logging.getLogger("qq_signin.onebot")


class OneBotError(RuntimeError):
    """OneBot 调用失败，包含可展示给用户的原因。"""

    def __init__(
        self,
        message: str,
        *,
        action: str = "",
        retcode: int | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.action = action
        self.retcode = retcode
        self.response = response


class OneBotClient:
    """同步 HTTP 客户端。

    它只会被后台工作线程调用，所以这里用阻塞式 urllib 就够了，
    不需要引入 aiohttp/httpx。
    """

    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.token = (token or "").strip()
        self.timeout = max(3.0, float(timeout))
        # NapCat 4 的 HTTP API 使用 /action 路径；标准 OneBot 11 使用根路径 + action 字段。
        # 第一次调用时自动探测，之后缓存。
        self._path_style: bool | None = None
        # 本地 API 通常不应该走系统代理，显式禁用代理避免奇怪的 502
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    # ------------------------------------------------------------------ 基础调用
    def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """调用一个 OneBot action，返回响应里的 ``data`` 字段。

        兼容两种 HTTP 形式：
        - 标准 OneBot 11：POST /，body 为 ``{"action": ..., "params": ...}``；
        - NapCat 4：POST /get_login_info，body 直接是参数对象。
        """
        params = params or {}

        if self._path_style is None:
            probe = self._post_json(
                self.base_url,
                {"action": action, "params": params},
                action,
            )
            if _is_napcat4_health(probe):
                self._path_style = True
            else:
                self._path_style = False
                return _unwrap_data(probe, action)

        if self._path_style:
            url = self.base_url + urllib.parse.quote(action, safe="")
            body = self._post_json(url, params, action)
        else:
            body = self._post_json(
                self.base_url,
                {"action": action, "params": params},
                action,
            )
        return _unwrap_data(body, action)

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        """POST 一个 JSON 请求，返回解析后的响应对象。"""
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "qq-signin/0.6.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw_body = response.read()
                status_code = response.status
        except urllib.error.HTTPError as exc:
            body = _decode_body(exc.read())
            detail = _extract_error_text(body) or body[:300]
            raise OneBotError(
                f"HTTP {exc.code}（{action}）：{detail or exc.reason}",
                action=action,
                response=body,
            ) from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if isinstance(exc.reason, ConnectionRefusedError) or "10061" in reason:
                detail = (
                    "连接被拒绝：NapCat 可能没有启动，或 OneBot HTTP 服务"
                    "没有监听这个端口。请先启动 NapCat 并完成登录，再点"
                    "“测试连接”。"
                )
            else:
                detail = (
                    "无法连接 OneBot：请检查 NapCat 是否启动、"
                    "HTTP 服务端口和地址是否正确。"
                )
            raise OneBotError(
                f"{detail}（{action}）",
                action=action,
            ) from exc
        except TimeoutError as exc:
            raise OneBotError(
                f"OneBot 请求超时（{action}，{self.timeout:.0f}s）",
                action=action,
            ) from exc
        except OSError as exc:
            raise OneBotError(
                f"OneBot 网络错误（{action}）：{exc}",
                action=action,
            ) from exc

        body_text = _decode_body(raw_body)
        if not body_text.strip():
            raise OneBotError(
                f"OneBot 返回空响应（{action}，HTTP {status_code}）",
                action=action,
            )
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise OneBotError(
                f"OneBot 返回的不是 JSON（{action}）：{body_text[:300]}",
                action=action,
            ) from exc

        if not isinstance(body, dict):
            raise OneBotError(
                f"OneBot 返回格式异常（{action}）：期望 JSON 对象",
                action=action,
                response=body,
            )
        return body

    # ------------------------------------------------------------------ 账号/联系人
    def get_login_info(self) -> LoginInfo:
        data = self.call("get_login_info")
        if not isinstance(data, dict) or "user_id" not in data:
            raise OneBotError(
                "get_login_info 返回格式异常，无法读取登录账号",
                action="get_login_info",
                response=data,
            )
        return LoginInfo(
            user_id=int(data["user_id"]),
            nickname=str(data.get("nickname", "") or ""),
        )

    def get_group_list(self) -> list[Target]:
        data = self.call("get_group_list")
        if not isinstance(data, list):
            raise OneBotError(
                "get_group_list 返回格式异常，期望数组",
                action="get_group_list",
                response=data,
            )
        targets: list[Target] = []
        for item in data:
            if not isinstance(item, dict) or "group_id" not in item:
                continue
            name = item.get("group_name") or item.get("group_remark") or ""
            try:
                targets.append(
                    Target(kind=KIND_GROUP, id=item["group_id"], name=name)
                )
            except (TypeError, ValueError):
                continue
        return _sort_targets(targets)

    def get_friend_list(self) -> list[Target]:
        data = self.call("get_friend_list")
        if not isinstance(data, list):
            raise OneBotError(
                "get_friend_list 返回格式异常，期望数组",
                action="get_friend_list",
                response=data,
            )
        targets: list[Target] = []
        for item in data:
            if not isinstance(item, dict) or "user_id" not in item:
                continue
            # 备注优先，其次昵称
            name = item.get("remark") or item.get("nickname") or ""
            try:
                targets.append(
                    Target(kind=KIND_FRIEND, id=item["user_id"], name=name)
                )
            except (TypeError, ValueError):
                continue
        return _sort_targets(targets)

    # ------------------------------------------------------------------ 发消息
    def send_group_msg(self, group_id: int, message: str | list[dict[str, Any]]) -> Any:
        return self.call(
            "send_group_msg",
            {"group_id": int(group_id), "message": message},
        )

    def send_private_msg(self, user_id: int, message: str | list[dict[str, Any]]) -> Any:
        return self.call(
            "send_private_msg",
            {"user_id": int(user_id), "message": message},
        )

    def get_friend_msg_history(
        self,
        user_id: int,
        *,
        count: int = 100,
        reverse_order: bool = True,
    ) -> list[dict[str, Any]]:
        """读取与指定好友的私聊历史（NapCat 扩展接口）。"""
        data = self.call(
            "get_friend_msg_history",
            {
                "user_id": str(int(user_id)),
                "count": int(count),
                "reverseOrder": bool(reverse_order),
            },
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            messages = data.get("messages")
            if isinstance(messages, list):
                return [item for item in messages if isinstance(item, dict)]
        raise OneBotError(
            "get_friend_msg_history 返回格式异常，期望 messages 数组",
            action="get_friend_msg_history",
            response=data,
        )

    # ------------------------------------------------------------------ 群打卡
    def send_group_sign(self, group_id: int) -> Any:
        """群打卡（go-cqhttp 兼容接口）。"""
        return self.call("send_group_sign", {"group_id": int(group_id)})

    # ------------------------------------------------------------------ 其他
    def get_status(self) -> Any:
        return self.call("get_status")

    def get_cookies(self, domain: str) -> dict[str, Any]:
        """获取指定域名的 Cookie（NapCat 扩展接口）。

        返回结构通常为 ``{"cookies": "uin=o123; p_skey=...;", "bkn": "..."}``。
        QQ 空间个人签到需要 ``qzone.qq.com`` 域下的 ``p_skey``。
        """
        data = self.call("get_cookies", {"domain": str(domain).strip()})
        if not isinstance(data, dict) or "cookies" not in data:
            raise OneBotError(
                f"get_cookies 返回格式异常（domain={domain}）",
                action="get_cookies",
                response=data,
            )
        return data


def normalize_base_url(value: str) -> str:
    """校验并规范化 API 根地址。"""
    text = (value or "").strip()
    if not text:
        raise ValueError("OneBot 地址不能为空")
    if "://" not in text:
        text = "http://" + text
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("OneBot 地址只支持 http:// 或 https://")
    if not parsed.netloc:
        raise ValueError(f"OneBot 地址缺少主机名：{value!r}")
    # 允许带子路径，但不能带查询参数/锚点
    if parsed.query or parsed.fragment:
        raise ValueError("OneBot 地址不能包含查询参数或锚点")
    path = parsed.path.rstrip("/") + "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, "", "")
    )


def _decode_body(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _is_napcat4_health(body: dict[str, Any]) -> bool:
    """NapCat 4 对根路径的所有请求都返回 "NapCat4 Is Running"。"""
    message = str(body.get("message", ""))
    if "NapCat4" not in message:
        return False
    data = body.get("data")
    return data in (None, {}, [])


def _unwrap_data(body: dict[str, Any], action: str) -> Any:
    retcode = body.get("retcode")
    status = str(body.get("status", "ok")).lower()
    is_ok = status in {"ok", "async"} and retcode in (0, None)
    if not is_ok:
        detail = _extract_error_text(body) or "未知错误"
        raise OneBotError(
            f"OneBot 调用失败（{action}）：{detail}",
            action=action,
            retcode=retcode if isinstance(retcode, int) else None,
            response=body,
        )
    return body.get("data")


def _extract_error_text(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("message", "wording", "msg", "error"):
            value = body.get(key)
            if value:
                return str(value)
        data = body.get("data")
        if isinstance(data, dict):
            for key in ("message", "wording", "msg"):
                if data.get(key):
                    return str(data[key])
    if isinstance(body, str):
        return body
    return ""


def _sort_targets(targets: list[Target]) -> list[Target]:
    return sorted(targets, key=lambda t: (t.name.casefold(), t.id))
