"""お気に入りへのドロップ位置指定とインジケータ表示。

ドロップ位置の判定は純粋関数（`_FavTree._drop_target`）に切り出してあるので、
描画を伴わずにここで固定できる。Qt 側の制約（`setDropIndicatorShown(False)`
にすると `dropIndicatorPosition()` が使えなくなる）は
docs/investigation/qt_drop_indicator_20260731.md を参照。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models.favorite import FavoriteStore


def labels_of(store: FavoriteStore, parent_id: str = "") -> list[str]:
    return [f.label for f in store.children_of(parent_id)]


class TestInsertMany:
    """T-5: 指定位置へ、渡した順序どおりに入り、保存は 1 回だけ。"""

    def _store(self, tmp_path) -> FavoriteStore:
        store = FavoriteStore(tmp_path / "favorites.json")
        for name in ("A", "B", "C"):
            store.add(name, str(tmp_path / name))
        return store

    @staticmethod
    def _specs(tmp_path, *names):
        return [{"label": n, "path": str(tmp_path / n)} for n in names]

    def test_inserts_at_index_in_given_order(self, tmp_path):
        store = self._store(tmp_path)
        store.insert_many(self._specs(tmp_path, "X", "Y"), index=1)
        assert labels_of(store) == ["A", "X", "Y", "B", "C"]

    def test_negative_index_appends(self, tmp_path):
        store = self._store(tmp_path)
        store.insert_many(self._specs(tmp_path, "X"), index=-1)
        assert labels_of(store) == ["A", "B", "C", "X"]

    def test_index_beyond_end_appends(self, tmp_path):
        store = self._store(tmp_path)
        store.insert_many(self._specs(tmp_path, "X"), index=99)
        assert labels_of(store) == ["A", "B", "C", "X"]

    def test_index_zero_inserts_at_head(self, tmp_path):
        store = self._store(tmp_path)
        store.insert_many(self._specs(tmp_path, "X"), index=0)
        assert labels_of(store) == ["X", "A", "B", "C"]

    def test_saves_once_for_multiple_items(self, tmp_path, monkeypatch):
        store = self._store(tmp_path)
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))
        store.insert_many(self._specs(tmp_path, "X", "Y", "Z"), index=0)
        assert len(calls) == 1  # 件数ぶん走らない

    def test_empty_specs_does_not_save(self, tmp_path, monkeypatch):
        """T-7 のモデル側: 全件重複でスキップされたときに書き込まない。"""
        store = self._store(tmp_path)
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))
        assert store.insert_many([], index=0) == []
        assert calls == []

    def test_into_group(self, tmp_path):
        store = FavoriteStore(tmp_path / "favorites.json")
        group = store.add_group("G")
        store.add("g1", str(tmp_path / "g1"), parent_id=group.id)
        store.add("after", str(tmp_path / "after"))
        store.insert_many(self._specs(tmp_path, "X"),
                          parent_id=group.id, index=0)
        assert labels_of(store, group.id) == ["X", "g1"]
        assert labels_of(store) == ["G", "after"]

    def test_into_empty_group(self, tmp_path):
        store = FavoriteStore(tmp_path / "favorites.json")
        group = store.add_group("G")
        store.add("top", str(tmp_path / "top"))
        store.insert_many(self._specs(tmp_path, "X"),
                          parent_id=group.id, index=-1)
        assert labels_of(store, group.id) == ["X"]
        assert labels_of(store) == ["G", "top"]

    def test_append_to_top_level_skips_group_contents(self, tmp_path):
        """末尾追加が、最後の兄弟（グループ）の中に紛れ込まない。"""
        store = FavoriteStore(tmp_path / "favorites.json")
        group = store.add_group("G")
        store.add("inside", str(tmp_path / "inside"), parent_id=group.id)
        store.insert_many(self._specs(tmp_path, "X"), index=-1)
        assert labels_of(store) == ["G", "X"]
        assert labels_of(store, group.id) == ["inside"]
        # フラット順も depth-first のまま（末尾に来ている）
        assert [f.label for f in store.favorites] == ["G", "inside", "X"]

    def test_accepts_favorite_objects(self, tmp_path):
        from app.models.favorite import Favorite
        store = self._store(tmp_path)
        store.insert_many([Favorite(label="X", path=str(tmp_path))], index=0)
        assert labels_of(store) == ["X", "A", "B", "C"]

    def test_persisted_to_disk(self, tmp_path):
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        store.add("A", str(tmp_path / "A"))
        store.insert_many(self._specs(tmp_path, "X"), index=0)
        assert labels_of(FavoriteStore(config)) == ["X", "A"]


class TestMove:
    """T-6: 自分自身・子孫への移動を拒否する。通常の移動は通る。"""

    def _nested(self, tmp_path):
        """G > (g1, H > h1) と、トップの A / B。"""
        store = FavoriteStore(tmp_path / "favorites.json")
        g = store.add_group("G")
        store.add("g1", str(tmp_path / "g1"), parent_id=g.id)
        h = store.add_group("H", parent_id=g.id)
        store.add("h1", str(tmp_path / "h1"), parent_id=h.id)
        a = store.add("A", str(tmp_path / "A"))
        b = store.add("B", str(tmp_path / "B"))
        return store, g, h, a, b

    def test_rejects_move_into_self(self, tmp_path, monkeypatch):
        store, g, _h, _a, _b = self._nested(tmp_path)
        before = [(f.id, f.parent_id) for f in store.favorites]
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))
        assert store.move(g.id, parent_id=g.id, index=0) is False
        assert [(f.id, f.parent_id) for f in store.favorites] == before
        assert calls == []

    def test_rejects_move_into_descendant(self, tmp_path, monkeypatch):
        store, g, h, _a, _b = self._nested(tmp_path)
        before = [(f.id, f.parent_id) for f in store.favorites]
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))
        assert store.move(g.id, parent_id=h.id, index=0) is False
        assert [(f.id, f.parent_id) for f in store.favorites] == before
        assert calls == []

    def test_rejects_unknown_id(self, tmp_path):
        store, _g, _h, _a, _b = self._nested(tmp_path)
        assert store.move("nope", parent_id="", index=0) is False

    def test_rejects_unknown_parent(self, tmp_path):
        store, _g, _h, a, _b = self._nested(tmp_path)
        assert store.move(a.id, parent_id="nope", index=0) is False

    def test_move_to_top_level_position(self, tmp_path):
        store, _g, _h, _a, b = self._nested(tmp_path)
        assert store.move(b.id, parent_id="", index=0) is True
        assert labels_of(store) == ["B", "G", "A"]

    def test_move_forward_within_same_parent(self, tmp_path):
        """「移動前の兄弟列」で解釈する: A を B の直後（index=3）へ。"""
        store = FavoriteStore(tmp_path / "favorites.json")
        for name in ("A", "B", "C", "D"):
            store.add(name, str(tmp_path / name))
        target = store.favorites[0]
        assert store.move(target.id, parent_id="", index=3) is True
        assert labels_of(store) == ["B", "C", "A", "D"]

    def test_move_before_same_position_is_noop(self, tmp_path):
        store = FavoriteStore(tmp_path / "favorites.json")
        for name in ("A", "B", "C"):
            store.add(name, str(tmp_path / name))
        target = store.favorites[0]
        assert store.move(target.id, parent_id="", index=1) is True
        assert labels_of(store) == ["A", "B", "C"]

    def test_move_group_carries_descendants(self, tmp_path):
        store, g, h, _a, _b = self._nested(tmp_path)
        assert store.move(h.id, parent_id="", index=0) is True
        assert labels_of(store) == ["H", "G", "A", "B"]
        assert labels_of(store, h.id) == ["h1"]   # 中身は付いてくる
        assert labels_of(store, g.id) == ["g1"]
        # フラット順も depth-first のまま
        assert [f.label for f in store.favorites] == [
            "H", "h1", "G", "g1", "A", "B"]

    def test_move_leaf_into_group(self, tmp_path):
        store, g, _h, a, _b = self._nested(tmp_path)
        assert store.move(a.id, parent_id=g.id, index=0) is True
        assert labels_of(store, g.id) == ["A", "g1", "H"]
        assert labels_of(store) == ["G", "B"]

    def test_move_saves_once(self, tmp_path, monkeypatch):
        store, _g, _h, a, _b = self._nested(tmp_path)
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))
        assert store.move(a.id, parent_id="", index=0) is True
        assert len(calls) == 1

    def test_move_persisted_to_disk(self, tmp_path):
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        store.add("A", str(tmp_path / "A"))
        b = store.add("B", str(tmp_path / "B"))
        assert store.move(b.id, parent_id="", index=0) is True
        assert labels_of(FavoriteStore(config)) == ["B", "A"]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
