"""ファイル操作（移動・コピー・削除）と Undo。

削除は send2trash でゴミ箱へ（実質 Undo 可能）。
移動・コピーは直近操作を取り消し可能。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from send2trash import send2trash


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


class FileOps:
    def __init__(self) -> None:
        self._history: list[OpRecord] = []

    @property
    def can_undo(self) -> bool:
        return any(r.kind in ("move", "copy") for r in self._history)

    def move(self, sources: list[str | Path], dest_dir: str | Path) -> OpRecord:
        d = Path(dest_dir)
        record = OpRecord(kind="move")
        for src in map(Path, sources):
            dest = _unique_dest(d / src.name)
            shutil.move(str(src), str(dest))
            record.pairs.append((str(src), str(dest)))
        self._history.append(record)
        return record

    def copy(self, sources: list[str | Path], dest_dir: str | Path) -> OpRecord:
        d = Path(dest_dir)
        record = OpRecord(kind="copy")
        for src in map(Path, sources):
            dest = _unique_dest(d / src.name)
            if src.is_dir():
                shutil.copytree(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))
            record.pairs.append((str(src), str(dest)))
        self._history.append(record)
        return record

    def delete(self, sources: list[str | Path]) -> OpRecord:
        record = OpRecord(kind="delete")
        for src in map(Path, sources):
            send2trash(str(src))
            record.pairs.append((str(src), ""))
        self._history.append(record)
        return record

    def undo(self) -> OpRecord:
        """直近の move/copy を取り消す（delete はゴミ箱から手動復元）。"""
        for idx in range(len(self._history) - 1, -1, -1):
            record = self._history[idx]
            if record.kind == "move":
                for src, dest in reversed(record.pairs):
                    shutil.move(dest, src)
                del self._history[idx]
                return record
            if record.kind == "copy":
                for _, dest in reversed(record.pairs):
                    p = Path(dest)
                    if p.is_dir():
                        shutil.rmtree(dest)
                    elif p.exists():
                        p.unlink()
                del self._history[idx]
                return record
        raise RuntimeError("取り消せる操作がありません")
