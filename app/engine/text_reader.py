"""テキストファイルの読み込み・検索（バイナリ除外 + エンコーディング推定）。

バイナリ判定は「先頭8KBにNULバイトがあるか」で即決。
エンコーディングは utf-8 → cp932 の簡易フォールバック、
どちらも失敗したら charset-normalizer で先頭サンプルから推定。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from charset_normalizer import from_bytes

_SAMPLE_SIZE = 8192


def is_binary(filepath: str | Path) -> bool:
    """先頭8KBにNULバイトがあればバイナリと判定。"""
    try:
        with open(filepath, "rb") as f:
            return b"\x00" in f.read(_SAMPLE_SIZE)
    except OSError:
        return True


def detect_encoding(filepath: str | Path) -> str | None:
    """先頭サンプルのみでエンコーディングを推定。"""
    try:
        with open(filepath, "rb") as f:
            sample = f.read(_SAMPLE_SIZE)
    except OSError:
        return None
    for enc in ("utf-8", "cp932"):
        try:
            sample.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    best = from_bytes(sample).best()
    return best.encoding if best else None


def search_in_text(filepath: str | Path, keyword: str,
                   case_sensitive: bool = False,
                   max_hits: int = 100) -> Iterator[tuple[int, str]]:
    """行単位ストリーミングでキーワード検索。(行番号, 行内容) を逐次返す。"""
    encoding = detect_encoding(filepath)
    if encoding is None:
        return
    needle = keyword if case_sensitive else keyword.lower()
    hits = 0
    try:
        with open(filepath, encoding=encoding, errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    yield lineno, line.rstrip("\r\n")[:200]
                    hits += 1
                    if hits >= max_hits:
                        return
    except OSError:
        return
