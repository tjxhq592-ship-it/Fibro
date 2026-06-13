"""お気に入り（フォルダブックマーク）の保存・読み込み。

JSON 保存。破損時は既定（空リスト）へフォールバックして起動不能を防ぐ。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Favorite:
    label: str
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    tags: list[str] = field(default_factory=list)
    note: str = ""

    def is_reachable(self) -> bool:
        """パス到達確認（ネットワークパスも os.path.isdir で判定）。"""
        try:
            return os.path.isdir(self.path)
        except OSError:
            return False


class FavoriteStore:
    def __init__(self, config_path: str | Path) -> None:
        self._path = Path(config_path)
        self.favorites: list[Favorite] = []
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self.favorites = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.favorites = [
                Favorite(
                    label=item["label"],
                    path=item["path"],
                    id=item.get("id", uuid.uuid4().hex[:8]),
                    tags=list(item.get("tags", [])),
                    note=item.get("note", ""),
                )
                for item in data.get("favorites", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            # 破損した設定ファイルでは既定にフォールバック
            self.favorites = []

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"favorites": [asdict(f) for f in self.favorites]}
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, label: str, path: str, tags: list[str] | None = None,
            note: str = "") -> Favorite:
        fav = Favorite(label=label, path=path, tags=tags or [], note=note)
        self.favorites.append(fav)
        self.save()
        return fav

    def remove(self, fav_id: str) -> bool:
        before = len(self.favorites)
        self.favorites = [f for f in self.favorites if f.id != fav_id]
        if len(self.favorites) != before:
            self.save()
            return True
        return False

    def find_by_path(self, path: str) -> Favorite | None:
        norm = str(Path(path))
        for f in self.favorites:
            if str(Path(f.path)) == norm:
                return f
        return None
