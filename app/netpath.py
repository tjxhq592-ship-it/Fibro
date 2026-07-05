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


def reachable(path: str | Path, timeout: float = 2.0,
              require_dir: bool = True) -> bool:
    """path が到達可能か。timeout 内に返らなければ False。

    require_dir=True ならディレクトリであること、False なら存在（ファイル可）を
    確認する。お気に入りのファイル登録では存在確認（require_dir=False）を使う。
    """
    check = os.path.isdir if require_dir else os.path.exists
    future = _EXECUTOR.submit(check, str(path))
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
