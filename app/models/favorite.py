"""お気に入り（フォルダブックマーク）の保存・読み込み。

JSON 保存。破損時は既定（空リスト）へフォールバックして起動不能を防ぐ。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.atomicio import atomic_write_text


@dataclass
class Favorite:
    label: str
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    tags: list[str] = field(default_factory=list)
    note: str = ""
    # 階層化: parent_id が空ならトップ階層。is_group はグループ（フォルダ）ノード。
    parent_id: str = ""
    is_group: bool = False

    def is_reachable(self) -> bool:
        """パス到達確認（ネットワークパスも os.path.isdir で判定）。

        グループはパスを持たないため常に到達可能とみなす。
        """
        if self.is_group:
            return True
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
                    path=item.get("path", ""),
                    id=item.get("id", uuid.uuid4().hex[:8]),
                    tags=list(item.get("tags", [])),
                    note=item.get("note", ""),
                    parent_id=item.get("parent_id", ""),
                    is_group=bool(item.get("is_group", False)),
                )
                for item in data.get("favorites", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            # 破損した設定ファイルでは既定にフォールバック
            self.favorites = []

    def save(self) -> None:
        data = {"favorites": [asdict(f) for f in self.favorites]}
        atomic_write_text(
            self._path, json.dumps(data, ensure_ascii=False, indent=2))

    def add(self, label: str, path: str, tags: list[str] | None = None,
            note: str = "", parent_id: str = "") -> Favorite:
        fav = Favorite(label=label, path=path, tags=tags or [], note=note,
                       parent_id=parent_id)
        self.favorites.append(fav)
        self.save()
        return fav

    def add_group(self, label: str, parent_id: str = "") -> Favorite:
        """お気に入りをまとめるグループ（フォルダ）を追加。"""
        group = Favorite(label=label, path="", parent_id=parent_id,
                         is_group=True)
        self.favorites.append(group)
        self.save()
        return group

    def reorder(self, ordered: list[Favorite]) -> None:
        """ツリー UI が再構築した順序・親子関係でリストを置き換えて保存。"""
        self.favorites = ordered
        self.save()

    def children_of(self, parent_id: str) -> list[Favorite]:
        """指定 parent_id 直下のお気に入りを、保存順で返す。"""
        return [f for f in self.favorites if f.parent_id == parent_id]

    def remove(self, fav_id: str) -> bool:
        """お気に入りを削除。グループの場合は子孫もまとめて削除する。"""
        # 削除対象 id を収集（自身 + 全子孫）
        to_remove = {fav_id}
        changed = True
        while changed:
            changed = False
            for f in self.favorites:
                if f.parent_id in to_remove and f.id not in to_remove:
                    to_remove.add(f.id)
                    changed = True
        before = len(self.favorites)
        self.favorites = [f for f in self.favorites if f.id not in to_remove]
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
