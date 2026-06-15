r"""Windows のロングパス（MAX_PATH 260 超）対応。

Windows API は既定でパス長 260 文字に制限され、深い階層や長い名前で
ファイル操作が失敗する。絶対パスに `\\?\`（UNC は `\\?\UNC\`）プレフィックスを
付けると拡張長パスとして扱われ制限を回避できる。非 Windows・相対パスは無変換。
"""
from __future__ import annotations

import os
from pathlib import Path

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"


def extend(path: str | Path) -> str:
    """ロングパスプレフィックスを付与した文字列を返す（冪等）。

    Windows 以外、相対パス、既にプレフィックス付きの場合はそのまま返す。
    """
    s = str(path)
    if os.name != "nt":
        return s
    if s.startswith(_PREFIX):
        return s  # 既に拡張長パス（冪等）
    # 絶対パスのみ対象（相対パスはプレフィックスを付けられない）
    if not os.path.isabs(s):
        return s
    # バックスラッシュへ正規化
    norm = os.path.normpath(s)
    if norm.startswith("\\\\"):
        # UNC パス: \\server\share → \\?\UNC\server\share
        return _UNC_PREFIX + norm[2:]
    return _PREFIX + norm
