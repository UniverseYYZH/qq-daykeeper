"""测试辅助：在项目内 ``.tmp/tests`` 下创建临时目录。

不用 ``tempfile.TemporaryDirectory`` 的原因：Windows 上它会以 0o700
创建目录，在部分受限/中文路径环境里会生成难以清理的 ACL。测试目录放在
项目内也方便定位，且已被 ``.gitignore`` 忽略。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

TEST_TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp" / "tests"


def make_temp_dir(prefix: str = "qqsignin-test-") -> Path:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir(parents=False, exist_ok=False)
    return path


def remove_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
