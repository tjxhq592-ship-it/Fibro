"""ネットワークパスのタイムアウト付きアクセス。

到達不可なネットワークドライブに対する os.path.isdir / shutil.disk_usage は
OS のリトライで数十秒ハングし、UI スレッドから呼ぶと固まる。別スレッドで
実行してタイムアウトを設け、返らなければ「到達不可」として早期に諦める。
"""
from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

# 短命なワーカー（呼び出しは稀なのでプール1本で十分）
_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def reachable(path: str | Path, timeout: float = 2.0) -> bool:
    """path がディレクトリとして到達可能か。timeout 内に返らなければ False。"""
    future = _EXECUTOR.submit(os.path.isdir, str(path))
    try:
        return bool(future.result(timeout=timeout))
    except (FutureTimeout, OSError):
        return False


def safe_disk_usage(path: str | Path,
                    timeout: float = 2.0) -> tuple[int, int] | None:
    """(free, total) を返す。到達不可/タイムアウトなら None。"""
    future = _EXECUTOR.submit(shutil.disk_usage, str(path))
    try:
        usage = future.result(timeout=timeout)
    except (FutureTimeout, OSError, ValueError):
        return None
    return usage.free, usage.total
