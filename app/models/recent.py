"""最近使った／よく使うフォルダの記録。

ナビゲートのたびに record() で更新。最終アクセス時刻順（最近）と
アクセス回数順（頻繁）の2軸で取り出せる。JSON 保存、破損時は空にフォールバック。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.atomicio import atomic_write_text

# ディスクに残す最大件数（無限に肥大化しないよう上限）
_MAX_STORED = 200


@dataclass
class RecentEntry:
    path: str
    count: int = 0
    last_access: float = 0.0


class RecentStore:
    def __init__(self, config_path: str | Path, max_stored: int = _MAX_STORED) -> None:
        self._path = Path(config_path)
        self._max = max_stored
        self.entries: list[RecentEntry] = []
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self.entries = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.entries = [
                RecentEntry(
                    path=item["path"],
                    count=int(item.get("count", 0)),
                    last_access=float(item.get("last_access", 0.0)),
                )
                for item in data.get("entries", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            self.entries = []

    def save(self) -> None:
        try:
            data = {"entries": [asdict(e) for e in self.entries]}
            atomic_write_text(
                self._path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError:
            pass  # 記録保存失敗でアプリは止めない

    def record(self, path: str, now: float | None = None) -> None:
        """フォルダへのアクセスを記録（回数++・最終時刻更新）。"""
        norm = str(Path(path))
        ts = time.time() if now is None else now
        for e in self.entries:
            if e.path == norm:
                e.count += 1
                e.last_access = ts
                break
        else:
            self.entries.append(RecentEntry(path=norm, count=1, last_access=ts))
        # 上限超過は最終アクセスが古いものから捨てる
        if len(self.entries) > self._max:
            self.entries.sort(key=lambda e: e.last_access, reverse=True)
            self.entries = self.entries[: self._max]
        self.save()

    def recent(self, limit: int = 10) -> list[RecentEntry]:
        return sorted(
            self.entries, key=lambda e: e.last_access, reverse=True)[:limit]

    def frequent(self, limit: int = 10) -> list[RecentEntry]:
        return sorted(
            self.entries, key=lambda e: (e.count, e.last_access),
            reverse=True)[:limit]
