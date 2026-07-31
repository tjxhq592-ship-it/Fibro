"""お気に入り（フォルダブックマーク）の保存・読み込み。

JSON 保存。破損時は既定（空リスト）へフォールバックして起動不能を防ぐ。
"""
from __future__ import annotations

import json
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
    # 葉ノードが指すのがファイルなら True（フォルダなら False）。登録時に判定して保存。
    is_file: bool = False
    # グループの展開状態（葉ノードでは未使用）。デフォルト True により
    # 新規グループは自動的に展開状態になる。
    expanded: bool = True

    def is_reachable(self) -> bool:
        """パス到達確認（到達不可ネットワークドライブはタイムアウトで False）。

        グループはパスを持たないため常に到達可能とみなす。
        ファイル登録は存在確認、フォルダ登録はディレクトリ確認を行う。
        """
        if self.is_group:
            return True
        from app.netpath import reachable
        return reachable(self.path, require_dir=not self.is_file)


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
                    is_file=bool(item.get("is_file", False)),
                    # 旧形式（expanded キー無し）は True にフォールバック
                    expanded=bool(item.get("expanded", True)),
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
            note: str = "", parent_id: str = "",
            is_file: bool = False) -> Favorite:
        fav = Favorite(label=label, path=path, tags=tags or [], note=note,
                       parent_id=parent_id, is_file=is_file)
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

    # ---- 位置を指定した挿入・移動（ドラッグ&ドロップ用） ----
    #
    # favorites は「フラットなリスト + parent_id」で、表示順は children_of()
    # （リスト順のフィルタ）が決める。したがって **兄弟同士の相対順序さえ
    # 保てばよく**、リスト全体を depth-first に並べ直す必要はない。
    # ただし挿入・移動はどちらも depth-first 順を壊さないように実装してある
    # （favorites.json を人が読んだときに木の形が見えるようにするため）。

    def _descendant_ids(self, fav_id: str) -> set[str]:
        """指定 id の全子孫の id を返す（自身は含まない）。"""
        found: set[str] = set()
        changed = True
        while changed:
            changed = False
            for f in self.favorites:
                if f.id in found:
                    continue
                if f.parent_id == fav_id or f.parent_id in found:
                    found.add(f.id)
                    changed = True
        return found

    def _subtree_end(self, flat_index: int) -> int:
        """flat_index の要素とその子孫すべての「次」の位置を返す。"""
        ids = {self.favorites[flat_index].id}
        end = flat_index + 1
        for i in range(flat_index + 1, len(self.favorites)):
            if self.favorites[i].parent_id in ids:
                ids.add(self.favorites[i].id)
                end = i + 1
        return end

    def _flat_index_for(self, parent_id: str, index: int) -> int:
        """「parent_id の index 番目の子の直前」に当たるフラット位置を返す。

        index が負、または兄弟数以上なら「最後の兄弟（の子孫込み）の直後」。
        兄弟が 1 つも無ければ、親自身の直後（トップ階層ならリスト末尾）。
        """
        positions = [i for i, f in enumerate(self.favorites)
                     if f.parent_id == parent_id]
        if not positions:
            if not parent_id:
                return len(self.favorites)
            for i, f in enumerate(self.favorites):
                if f.id == parent_id:
                    return i + 1
            return len(self.favorites)
        if index < 0 or index >= len(positions):
            return self._subtree_end(positions[-1])
        return positions[index]

    def insert_many(self, specs, *, parent_id: str = "",
                    index: int = -1) -> list[Favorite]:
        """複数の「お気に入り」を指定位置へまとめて挿入し、保存は 1 回だけ行う。

        specs は Favorite そのもの、または Favorite のフィールド名をキーに
        持つマッピング（``{"label": ..., "path": ..., "is_file": ...}``）の
        並び。**渡された順序のまま**挿入する。

        index は parent_id の子の中での挿入位置。負値・兄弟数以上なら末尾。
        1 件も無ければ何もせず（save() も呼ばず）空リストを返す。
        """
        favs: list[Favorite] = []
        for spec in specs:
            fav = spec if isinstance(spec, Favorite) else Favorite(**dict(spec))
            fav.parent_id = parent_id
            favs.append(fav)
        if not favs:
            return []
        at = self._flat_index_for(parent_id, index)
        self.favorites[at:at] = favs
        self.save()
        return favs

    def move(self, fav_id: str, *, parent_id: str, index: int) -> bool:
        """既存の「お気に入り」を別の親・位置へ移す。

        グループは子孫ごと移動する。自分自身や子孫の中へは移せない
        （木が壊れるため False を返して何もしない）。index は **移動前の**
        兄弟列に対する位置として解釈する（「C の直前へ」がそのまま通る）。
        """
        fav = next((f for f in self.favorites if f.id == fav_id), None)
        if fav is None:
            return False
        if parent_id == fav_id:
            return False
        descendants = self._descendant_ids(fav_id)
        if parent_id in descendants:
            return False
        if parent_id and not any(f.id == parent_id for f in self.favorites):
            return False  # 存在しない親（孤児化を防ぐ）

        # 挿入位置は「取り除く前」のリストに対して求める。取り除いてから
        # 数えると、同じ親の中で後ろへ動かすとき 1 つずれる。
        at = self._flat_index_for(parent_id, index)
        block_ids = {fav_id} | descendants
        block = [f for f in self.favorites if f.id in block_ids]
        removed_before = sum(1 for i, f in enumerate(self.favorites)
                             if i < at and f.id in block_ids)
        self.favorites = [f for f in self.favorites if f.id not in block_ids]
        at -= removed_before
        at = max(0, min(at, len(self.favorites)))
        fav.parent_id = parent_id
        self.favorites[at:at] = block
        self.save()
        return True

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
