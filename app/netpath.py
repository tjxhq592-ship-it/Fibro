"""ネットワークパスのタイムアウト付きアクセス。

到達不可なネットワークドライブに対する os.path.isdir / shutil.disk_usage は
OS のリトライで数十秒ハングし、UI スレッドから呼ぶと固まる。別スレッドで
実行してタイムアウトを設け、返らなければ「到達不可」として早期に諦める。

諦めたスレッドは OS 呼び出しが返るまで居座る（デーモンなので終了は止めない）。
到達不可のお気に入り／場所が並ぶと更新のたびに件数ぶん積み上がるため、
同じ問い合わせの共有・否定結果の短命キャッシュ・同時本数の上限で頭を抑える。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

_TIMEOUT_THREAD_NAME = "fibro-netpath"

# 諦めた結果を覚えておく秒数。同じ切断先を何度も数秒待ち直さないためだけの
# ものなので寿命は短く取る。復帰の取りこぼしが困る場面（手動の「場所を更新」
# など）は clear_cache() で明示的に捨てること。
_NEGATIVE_TTL = 30.0

# 同時に抱える「まだ返ってこない」スレッドの上限。ここに達したら OS 呼び出しを
# 起こさず default を返す。あくまで最後の安全弁で、実際に確かめてはいないので
# 否定キャッシュには載せない（次の機会には改めて問い合わせる）。
_MAX_INFLIGHT = 16

_lock = threading.Lock()
# キーは (種別, パス)。関数オブジェクトを鍵にするとテストの差し替えで
# 同一視が壊れるため、呼び出し側が種別の文字列を明示する。
_inflight: dict[tuple[str, str], "_Call"] = {}
_negative: dict[tuple[str, str], float] = {}


class _Call:
    """1 回ぶんの OS 呼び出し。同じキーの呼び出し元はこれを共有して待つ。"""

    __slots__ = ("done", "result")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: list[Any] = []


def clear_cache() -> None:
    """否定キャッシュを捨てる（「今すぐ確かめ直す」ため）。

    進行中の問い合わせ（_inflight）は触らない。OS 呼び出しが返った時点で
    自分で片付くうえ、ここで消すと同じパスに二重にスレッドを立ててしまう。
    """
    with _lock:
        _negative.clear()


def _run(func: Callable[[str], Any], arg: str,
         key: tuple[str, str], call: _Call) -> None:
    """OS 呼び出しの本体。諦められた後に返ってきても安全に畳む。

    触るのは自分が握っている _Call とモジュール内の辞書だけで、Qt の
    オブジェクトには一切触れない。受け手（ウィジェット）が先に破棄されて
    いても影響しない。
    """
    try:
        call.result.append(func(arg))
    except (OSError, ValueError):
        pass
    finally:
        with _lock:
            _inflight.pop(key, None)
            if call.result:
                # 遅れて成功が返ったなら否定キャッシュは誤り。捨てる。
                _negative.pop(key, None)
        call.done.set()


def _call_with_timeout(func: Callable[[str], Any], arg: str,
                       timeout: float, default: Any, kind: str) -> Any:
    """func(arg) を別スレッドで実行し、timeout 秒で諦めて default を返す。

    ThreadPoolExecutor を使わない理由は2つある。

    1. プールのワーカーは非デーモンで、concurrent.futures の atexit フックが
       全ワーカーを join する。到達不可パスの os.path.isdir は OS 側で数十秒
       返らないため、こちらが諦めた後もプロセス終了がその分ブロックされる。
       デーモンスレッドなら諦めた時点で放置でき、終了を止めない。
    2. 固定 max_workers だと固まったワーカーが後続を頭詰まりさせ、本来到達
       可能なパスまで軒並みタイムアウト扱いになる。

    そのうえで、同じ (kind, arg) が飛行中なら新しいスレッドは立てずに相乗り
    する。到達性の確認は一覧の更新ごとに同じパスへ何度も飛ぶため。
    """
    key = (kind, arg)
    with _lock:
        # 期限切れは自分のキーだけでなくまとめて捨てる。以前は「引き直した
        # キーだけ」消していたので、二度と問い合わせないパス（消したお気に
        # 入り・抜いた USB）の分が寿命を過ぎても残り続けた。件数は高々
        # 到達不可パスの数なので掃除は毎回で足りる。
        now = time.monotonic()
        for stale in [k for k, exp in _negative.items() if exp <= now]:
            del _negative[stale]
        if key in _negative:
            return default     # 期限内の否定結果。OS には問い合わせない
        call = _inflight.get(key)
        spawn = call is None
        if spawn:
            if len(_inflight) >= _MAX_INFLIGHT:
                return default
            call = _Call()
            _inflight[key] = call
    if spawn:
        threading.Thread(target=_run, args=(func, arg, key, call),
                         daemon=True, name=_TIMEOUT_THREAD_NAME).start()
    call.done.wait(timeout)
    with _lock:
        # 待ちが明けた後に結果を見る。タイムアウト直後に届いた分も拾える。
        if call.result:
            return call.result[0]
        _negative[key] = time.monotonic() + _NEGATIVE_TTL
        return default


def reachable(path: str | Path, timeout: float = 2.0,
              require_dir: bool = True) -> bool:
    """path が到達可能か。timeout 内に返らなければ False。

    require_dir=True ならディレクトリであること、False なら存在（ファイル可）を
    確認する。お気に入りのファイル登録では存在確認（require_dir=False）を使う。
    """
    check = os.path.isdir if require_dir else os.path.exists
    kind = "isdir" if require_dir else "exists"
    return bool(_call_with_timeout(check, str(path), timeout, False, kind))


def safe_disk_usage(path: str | Path,
                    timeout: float = 2.0) -> tuple[int, int] | None:
    """(free, total) を返す。到達不可/タイムアウトなら None。"""
    usage = _call_with_timeout(shutil.disk_usage, str(path), timeout, None,
                               "disk_usage")
    if usage is None:
        return None
    return usage.free, usage.total
