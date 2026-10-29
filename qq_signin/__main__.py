"""允许 ``python -m qq_signin`` 启动。"""

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
