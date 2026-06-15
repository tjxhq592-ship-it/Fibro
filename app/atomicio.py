"""設定/データ JSON のアトミック書き込み。

一時ファイルに書いてから os.replace で差し替えることで、書込中の
クラッシュや電源断による「半分書けた壊れた JSON」を防ぐ。
（検索オプション保存の二重書き込みバグの教訓を恒久化）
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """text を path へアトミックに書き込む。

    同じディレクトリに一時ファイルを作って flush+fsync し、os.replace で
    原子的に差し替える（同一ボリューム内の rename は原子的）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        # 失敗時は一時ファイルを後始末して、元ファイルは温存する
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
