"""ファイル操作（移動・コピー・削除）と Undo。

削除は send2trash でゴミ箱へ（実質 Undo 可能）。
移動・コピーは直近操作を取り消し可能。
"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.longpath import extend


def _send2trash(path: str) -> None:
    """send2trash を遅延 import（起動時 ~65ms を削減）。"""
    from send2trash import send2trash
    send2trash(path)


# 衝突解決の戻り値: "overwrite" | "skip" | "rename" | "cancel"
Resolver = Callable[[Path, Path], str]


@dataclass
class OpRecord:
    kind: str  # "move" | "copy" | "delete"
    # (元パス, 先パス) のリスト。delete は (元パス, "") のみ。
    pairs: list[tuple[str, str]] = field(default_factory=list)


def _unique_dest(dest: Path) -> Path:
    """衝突時に ` (2)` などを付与した重複しないパスを返す。"""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 2
    while True:
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _resolve_dest(dest: Path, resolver: Resolver | None,
                  src: Path) -> Path | None:
    """衝突時に解決方針を適用して最終 dest を返す。

    None を返したらスキップ。"cancel" は呼び出し側でループ中断するため
    番兵 None と区別して例外的に文字列を投げず、戻り値で表現する。
    """
    if not dest.exists():
        return dest
    action = resolver(src, dest) if resolver else "rename"
    if action == "skip":
        return None
    if action == "cancel":
        raise _Cancelled
    if action == "overwrite":
        # 既存をゴミ箱へ退避してから上書き（誤上書きでも復元可）
        _send2trash(str(dest))
        return dest
    return _unique_dest(dest)  # "rename"（既定）


class _Cancelled(Exception):
    """衝突解決でユーザーが中止を選んだことを表す内部シグナル。"""


class FileOps:
    def __init__(self) -> None:
        self._history: list[OpRecord] = []

    @property
    def can_undo(self) -> bool:
        return any(r.kind in ("move", "copy") for r in self._history)

    def move(self, sources: list[str | Path], dest_dir: str | Path,
             resolver: Resolver | None = None) -> OpRecord:
        d = Path(dest_dir)
        record = OpRecord(kind="move")
        try:
            for src in map(Path, sources):
                dest = _resolve_dest(d / src.name, resolver, src)
                if dest is None:
                    continue
                shutil.move(extend(src), extend(dest))
                record.pairs.append((str(src), str(dest)))
        except _Cancelled:
            pass
        self._history.append(record)
        return record

    def copy(self, sources: list[str | Path], dest_dir: str | Path,
             resolver: Resolver | None = None) -> OpRecord:
        d = Path(dest_dir)
        record = OpRecord(kind="copy")
        try:
            for src in map(Path, sources):
                dest = _resolve_dest(d / src.name, resolver, src)
                if dest is None:
                    continue
                if src.is_dir():
                    shutil.copytree(extend(src), extend(dest))
                else:
                    shutil.copy2(extend(src), extend(dest))
                record.pairs.append((str(src), str(dest)))
        except _Cancelled:
            pass
        self._history.append(record)
        return record

    def delete(self, sources: list[str | Path]) -> OpRecord:
        record = OpRecord(kind="delete")
        for src in map(Path, sources):
            _send2trash(str(src))
            record.pairs.append((str(src), ""))
        self._history.append(record)
        return record

    def undo(self) -> OpRecord:
        """直近の move/copy を取り消す（delete はゴミ箱から手動復元）。"""
        for idx in range(len(self._history) - 1, -1, -1):
            record = self._history[idx]
            if record.kind == "move":
                for src, dest in reversed(record.pairs):
                    shutil.move(extend(dest), extend(src))
                del self._history[idx]
                return record
            if record.kind == "copy":
                for _, dest in reversed(record.pairs):
                    p = Path(dest)
                    if p.is_dir():
                        shutil.rmtree(extend(p))
                    elif p.exists():
                        p.unlink()
                del self._history[idx]
                return record
        raise RuntimeError("取り消せる操作がありません")
