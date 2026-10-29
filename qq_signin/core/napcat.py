"""NapCat 配置发现与一键读取。

只读取 ``config/onebot11_*.json`` 里的 HTTP 服务地址和 token，不修改
NapCat 配置。用户点“自动检测”后，软件把地址和 token 填进设置。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NapCatConfig:
    path: Path
    url: str
    token: str
    host: str
    port: int
    server_name: str
    enabled: bool

    @property
    def root(self) -> Path:
        # ...\NapCat.Shell\config\onebot11_xxx.json -> ...\NapCat.Shell
        return self.path.parent.parent


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env_home = os.environ.get("NAPCAT_HOME", "").strip()
    if env_home:
        roots.append(Path(env_home))
    home = Path.home()
    roots.extend(
        [
            home / "NapCat.Shell",
            home / "Desktop" / "NapCat.Shell",
            home / "Downloads" / "NapCat.Shell",
            home / "Documents" / "NapCat.Shell",
            home / "OneDrive" / "Desktop" / "NapCat.Shell",
        ]
    )
    # 常见盘符根目录；只查固定深度，不做全盘递归
    for letter in "CDEFGHIJ":
        roots.extend(
            [
                Path(f"{letter}:\\NapCat.Shell"),
                Path(f"{letter}:\\NapCat"),
                Path(f"{letter}:\\Program Files\\NapCat.Shell"),
            ]
        )
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _config_files(root: Path) -> list[Path]:
    config_dir = root / "config"
    if not config_dir.is_dir():
        return []
    files = sorted(config_dir.glob("onebot11_*.json"))
    if not files:
        files = sorted(config_dir.glob("onebot11.json"))
    return files


def find_onebot_configs() -> list[NapCatConfig]:
    """扫描常见 NapCat 安装位置，返回所有可解析的 OneBot HTTP 配置。"""
    results: list[NapCatConfig] = []
    for root in _candidate_roots():
        for path in _config_files(root):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            network = raw.get("network")
            if not isinstance(network, dict):
                continue
            servers = network.get("httpServers")
            if not isinstance(servers, list):
                continue
            for server in servers:
                if not isinstance(server, dict):
                    continue
                try:
                    port = int(server.get("port", 3000))
                except (TypeError, ValueError):
                    continue
                host = str(server.get("host", "127.0.0.1")).strip()
                if host in {"", "0.0.0.0", "::"}:
                    host = "127.0.0.1"
                results.append(
                    NapCatConfig(
                        path=path,
                        url=f"http://{host}:{port}",
                        token=str(server.get("token", "")).strip(),
                        host=host,
                        port=port,
                        server_name=str(server.get("name", "")),
                        enabled=bool(server.get("enable", False)),
                    )
                )
                break  # 一个配置文件取第一个 HTTP 服务
    return results


def best_config() -> NapCatConfig | None:
    """优先返回已启用的 HTTP 服务，其次返回任意一个。"""
    configs = find_onebot_configs()
    if not configs:
        return None
    return next((item for item in configs if item.enabled), configs[0])


def find_qq_emoji_image(face_id: int) -> Path | None:
    """找 QQ 客户端的静态表情图，用于发送“小版”图片。"""
    roots: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_name, "").strip()
        if root:
            roots.append(Path(root) / "Tencent" / "QQNT" / "versions")
    for root in roots:
        for version in sorted(root.glob("*"), reverse=True):
            path = (
                version
                / "resources"
                / "app"
                / "resource"
                / "default-emojis"
                / f"{int(face_id)}.png"
            )
            if path.is_file():
                return path
    return None


def has_remote_client(config: NapCatConfig, url: str) -> bool:
    """检查配置里是否已有指向本软件的手机控制 HTTP 上报。"""
    try:
        raw = json.loads(config.path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    network = raw.get("network")
    if not isinstance(network, dict):
        return False
    clients = network.get("httpClients")
    if not isinstance(clients, list):
        return False
    target = url.rstrip("/").lower()
    for client in clients:
        if not isinstance(client, dict):
            continue
        client_url = str(client.get("url", "")).rstrip("/").lower()
        if client_url == target and bool(client.get("reportSelfMessage", False)):
            return True
    return False


def write_remote_client(
    config: NapCatConfig,
    url: str = "http://127.0.0.1:18080/event",
) -> Path:
    """把一个 HTTP 上报客户端写进 NapCat 配置，并返回备份路径。

    调用前应先退出 NapCat，否则它退出时可能覆盖本次修改。
    """
    raw = json.loads(config.path.read_text(encoding="utf-8-sig"))
    network = raw.get("network")
    if not isinstance(network, dict):
        raise ValueError("NapCat 配置里没有 network 字段")
    clients = network.get("httpClients")
    if not isinstance(clients, list):
        clients = []
        network["httpClients"] = clients

    target = url.rstrip("/")
    entry: dict[str, object] | None = None
    for client in clients:
        if not isinstance(client, dict):
            continue
        if (
            str(client.get("url", "")).rstrip("/") == target
            or client.get("name") == "qq-signin-remote"
        ):
            entry = client
            break
    if entry is None:
        entry = {}
        clients.append(entry)
    entry.update(
        {
            "enable": True,
            "name": "qq-signin-remote",
            "url": target,
            "messagePostFormat": "array",
            "reportSelfMessage": True,
            "token": "",
            "debug": False,
        }
    )

    backup = config.path.with_name(
        f"{config.path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    backup.write_bytes(config.path.read_bytes())
    temp = config.path.with_name(config.path.name + ".tmp")
    temp.write_text(
        json.dumps(raw, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, config.path)
    return backup
