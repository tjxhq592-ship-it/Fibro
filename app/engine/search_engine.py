"""検索エンジン（GUI非依存・ジェネレータでストリーミング・キャンセル可能）。

os.scandir で再帰走査し、ファイル名 → テキスト内容 → Excelセル値 の
モードで一致を逐次 yield する。threading.Event でいつでも中断できる。
"""
from __future__ import annotations

import fnmatch
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.engine.excel_reader import search_in_excel, search_in_xls
from app.engine.text_reader import is_binary, search_in_text

# テキスト検索の既定ホワイトリスト
DEFAULT_TEXT_EXTS = {
    ".txt", ".csv", ".json", ".log", ".md", ".xml", ".html", ".htm",
    ".ini", ".cfg", ".yaml", ".yml", ".toml", ".py", ".js", ".ts",
    ".bat", ".ps1", ".sh", ".sql",
}
EXCEL_EXTS = {".xlsx", ".xlsm", ".xls"}
DEFAULT_MAX_SIZE = 50 * 1024 * 1024  # 50MB


class SearchMode(Enum):
    FILENAME = "filename"
    TEXT = "text"
    EXCEL = "excel"


def is_wildcard(keyword: str) -> bool:
    """キーワードがワイルドカードパターンか。

    `*` `?` は Windows のファイル名に使えない文字なので、含まれていれば
    パターン照合（fnmatch）、含まれなければ部分一致とみなす。これにより
    ワイルドカードの ON/OFF を自動判定でき、ユーザー設定が不要になる。
    """
    return "*" in keyword or "?" in keyword


@dataclass
class SearchOptions:
    keyword: str
    modes: set[SearchMode] = field(
        default_factory=lambda: {SearchMode.FILENAME})
    case_sensitive: bool = False
    recursive: bool = True
    extensions: set[str] | None = None   # None = モード既定に従う
    max_file_size: int = DEFAULT_MAX_SIZE


@dataclass
class SearchHit:
    path: str            # フルパス
    kind: SearchMode     # 一致の種類
    detail: str = ""     # "Line 23: ..." / "Sheet1!C5: ..." / ""


@dataclass
class SearchStats:
    scanned: int = 0
    skipped: int = 0     # サイズ超過・バイナリ・アクセス不可


def _iter_files(root: str, recursive: bool,
                cancel: threading.Event) -> Iterator[os.DirEntry]:
    """scandir ベースの走査。アクセス不可フォルダはスキップして継続。"""
    stack = [root]
    while stack:
        if cancel.is_set():
            return
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if cancel.is_set():
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                stack.append(entry.path)
                        else:
                            yield entry
                    except OSError:
                        continue
        except OSError:
            continue


def search(root: str | Path, options: SearchOptions,
           cancel: threading.Event | None = None,
           stats: SearchStats | None = None) -> Iterator[SearchHit]:
    """検索を実行し、一致を1件ずつ yield する。"""
    cancel = cancel or threading.Event()
    stats = stats if stats is not None else SearchStats()
    keyword = options.keyword
    if not keyword:
        return
    needle = keyword if options.case_sensitive else keyword.lower()

    for entry in _iter_files(str(root), options.recursive, cancel):
        stats.scanned += 1
        name = entry.name
        ext = os.path.splitext(name)[1].lower()

        if SearchMode.FILENAME in options.modes:
            if is_wildcard(keyword):
                # fnmatch は大文字小文字を区別しない（Windows流儀）
                matched = fnmatch.fnmatch(name, keyword)
            else:
                haystack = name if options.case_sensitive else name.lower()
                matched = needle in haystack
            if matched:
                yield SearchHit(entry.path, SearchMode.FILENAME)

        content_modes = options.modes & {SearchMode.TEXT, SearchMode.EXCEL}
        if not content_modes:
            continue
        try:
            if entry.stat().st_size > options.max_file_size:
                stats.skipped += 1
                continue
        except OSError:
            stats.skipped += 1
            continue

        if SearchMode.TEXT in options.modes:
            allowed = (options.extensions if options.extensions is not None
                       else DEFAULT_TEXT_EXTS)
            if ext in allowed:
                if is_binary(entry.path):
                    stats.skipped += 1
                else:
                    for lineno, line in search_in_text(
                            entry.path, keyword, options.case_sensitive):
                        if cancel.is_set():
                            return
                        yield SearchHit(entry.path, SearchMode.TEXT,
                                        f"Line {lineno}: {line.strip()}")

        if SearchMode.EXCEL in options.modes and ext in EXCEL_EXTS:
            reader = search_in_xls if ext == ".xls" else search_in_excel
            for sheet, address, value in reader(
                    entry.path, keyword, options.case_sensitive):
                if cancel.is_set():
                    return
                yield SearchHit(entry.path, SearchMode.EXCEL,
                                f"{sheet}!{address}: {value.strip()}")
