"""画像ファイル種別の単一情報源。

プレビュー（preview_dialog）とサムネイル（thumbnails）が同じ拡張子集合を
参照できるよう一箇所に集約する（拡張子追加時の片側忘れを防ぐ）。
"""
from __future__ import annotations

from pathlib import Path

# QPixmap で読める想定の画像拡張子（小文字・ドット付き）
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})


def is_image(path: str | Path) -> bool:
    """拡張子が画像種別か（大小無視）。"""
    return Path(path).suffix.lower() in IMAGE_EXTS
