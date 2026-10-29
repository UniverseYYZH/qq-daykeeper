"""运行前检查脚本。

用法：

    python tools/check_env.py
    python tools/check_env.py --url http://127.0.0.1:3000
    python tools/check_env.py --url http://127.0.0.1:3000 --token 你的token

不带 ``--url`` 时只检查本机 Python/Tkinter/数据目录；带 ``--url`` 时
还会检查 OneBot 登录、群/好友列表和 QQ 打卡 Cookie。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从项目根目录直接运行：python tools/check_env.py
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qq_signin.core.env_check import (  # noqa: E402
    check_local,
    check_onebot,
    format_results,
    has_failure,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="QQ 签到助手运行环境检查")
    parser.add_argument("--url", default="", help="OneBot HTTP 地址，例如 http://127.0.0.1:3000")
    parser.add_argument("--token", default="", help="OneBot Access Token")
    parser.add_argument("--data-dir", default="", help="数据目录，默认项目下的 data/")
    parser.add_argument("--timeout", type=float, default=30.0, help="网络检查超时秒数")
    args = parser.parse_args()

    data_dir = (
        Path(args.data_dir).expanduser().resolve()
        if args.data_dir
        else ROOT / "data"
    )
    print("=" * 64)
    print("QQ 签到助手 · 运行环境检查")
    print("=" * 64)

    results = check_local(data_dir)
    print(format_results(results))

    if args.url.strip():
        print("-" * 64)
        print(f"检查 OneBot：{args.url.strip()}")
        network_results = check_onebot(
            args.url.strip(), args.token.strip(), timeout=args.timeout
        )
        print(format_results(network_results))
        results.extend(network_results)
    else:
        print("-" * 64)
        print("[!] 未提供 --url，已跳过 OneBot / QQ 打卡网络检查。")
        print("    需要检查连接时加：--url http://127.0.0.1:3000")

    print("-" * 64)
    if has_failure(results):
        print("结果：存在失败项，请按上面的提示修复后重试。")
        return 1
    print("结果：本机检查通过。")
    if not args.url.strip():
        print("下一步：启动 NapCat 后，用 --url 再运行一次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
