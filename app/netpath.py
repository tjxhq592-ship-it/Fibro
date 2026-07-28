"""ネットワークパスのタイムアウト付きアクセス。

到達不可なネットワークドライブに対する os.path.isdir / shutil.disk_usage は
OS のリトライで数十秒ハングし、UI スレッドから呼ぶと固まる。別スレッドで
実行してタイムアウトを設け、返らなければ「到達不可」として早期に諦める。
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

_TIMEOUT_THREAD_NAME = "fibro-netpath"


def _call_with_timeout(func: Callable[[str], Any], arg: str,
                       timeout: float, default: Any) -> Any:
    """func(arg) を別スレッドで実行し、timeout 秒で諦めて default を返す。

    ThreadPoolExecutor を使わない理由は2つある。

    1. プールのワーカーは非デーモンで、concurrent.futures の atexit フックが
       全ワーカーを join する。到達不可パスの os.path.isdir は OS 側で数十秒
       返らないため、こちらが諦めた後もプロセス終了がその分ブロックされる。
       デーモンスレッドなら諦めた時点で放置でき、終了を止めない。
    2. 固定 max_workers だと固まったワーカーが後続を頭詰まりさせ、本来到達
       可能なパスまで軒並みタイムアウト扱いになる。呼び出しは稀なので、
       1回ごとに使い捨てのスレッドを立てるほうが素直で安全。
    """
    result: list[Any] = []
    done = threading.Event()

    def _run() -> None:
        try:
            result.append(func(arg))
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True,
                     name=_TIMEOUT_THREAD_NAME).start()
    if not done.wait(timeout) or not result:
        return default
    return result[0]


def reachable(path: str | Path, timeout: float = 2.0,
              require_dir: bool = True) -> bool:
    """path が到達可能か。timeout 内に返らなければ False。

    require_dir=True ならディレクトリであること、False なら存在（ファイル可）を
    確認する。お気に入りのファイル登録では存在確認（require_dir=False）を使う。
    """
    check = os.path.isdir if require_dir else os.path.exists
    return bool(_call_with_timeout(check, str(path), timeout, False))


def safe_disk_usage(path: str | Path,
                    timeout: float = 2.0) -> tuple[int, int] | None:
    """(free, total) を返す。到達不可/タイムアウトなら None。"""
    usage = _call_with_timeout(shutil.disk_usage, str(path), timeout, None)
    if usage is None:
        return None
    return usage.free, usage.total
