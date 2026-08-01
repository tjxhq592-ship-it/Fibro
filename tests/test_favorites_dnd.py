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


ROW_H = 24  # 判定の境界値を px で書けるように行高を固定する


@pytest.fixture
def sidebar_factory(qapp, tmp_path):
    """お気に入りサイドバーを作って、行高 24px で表示状態にして返す。"""
    from PySide6.QtCore import QSize

    from app.gui.favorites_sidebar import FavoritesSidebar

    made = []

    def make(build) -> "FavoritesSidebar":
        store = FavoriteStore(tmp_path / "favorites.json")
        build(store)
        sidebar = FavoritesSidebar(store)
        made.append(sidebar)
        for item in sidebar._items_by_id.values():
            item.setSizeHint(0, QSize(160, ROW_H))
        sidebar.resize(240, 400)
        sidebar.show()
        qapp.processEvents()
        return sidebar

    yield make
    for sidebar in made:
        sidebar.close()
        sidebar.deleteLater()
    qapp.processEvents()


def point_in(tree, item, rel_y: int):
    """item の上端から rel_y px の位置（ビューポート座標）。"""
    from PySide6.QtCore import QPoint
    rect = tree.visualItemRect(item)
    return QPoint(rect.center().x(), rect.top() + rel_y)


class TestDropTargetBands:
    """T-1: 上下 25% の帯と中央 50% の境界。"""

    @pytest.fixture
    def flat(self, sidebar_factory):
        def build(store):
            for name in ("A", "B", "C"):
                store.add(name, str(name))
        return sidebar_factory(build)

    def _target(self, sidebar, label: str, rel_y: int):
        fav = next(f for f in sidebar._store.favorites if f.label == label)
        item = sidebar._items_by_id[fav.id]
        assert sidebar.tree.visualItemRect(item).height() == ROW_H
        return sidebar.tree._drop_target(point_in(sidebar.tree, item, rel_y))

    @pytest.mark.parametrize("rel_y", [0, 1, 5])
    def test_top_band_inserts_before(self, flat, rel_y):
        target = self._target(flat, "B", rel_y)
        assert (target.parent_id, target.index, target.mode) == ("", 1, "between")

    @pytest.mark.parametrize("rel_y", [19, 23])
    def test_bottom_band_inserts_after(self, flat, rel_y):
        target = self._target(flat, "B", rel_y)
        assert (target.parent_id, target.index, target.mode) == ("", 2, "between")

    def test_boundary_25_percent_belongs_to_middle(self, flat):
        """境界値ちょうど（6 と 18）は中央扱い。葉なので中点で振り分けられる。"""
        assert self._target(flat, "B", 6).index == 1   # 中点より上 → 直前
        assert self._target(flat, "B", 18).index == 2  # 中点より下 → 直後

    @pytest.mark.parametrize("rel_y,index", [(7, 1), (11, 1), (12, 2), (17, 2)])
    def test_leaf_middle_splits_at_midpoint(self, flat, rel_y, index):
        """T-2: 葉には "into" が無く、中央 50% は中点で上下に割れる。"""
        target = self._target(flat, "B", rel_y)
        assert target.mode == "between"
        assert target.index == index

    def test_depth_is_zero_at_top_level(self, flat):
        assert self._target(flat, "B", 1).depth == 0


class TestDropTargetGroups:
    """T-2 / T-3: グループの中央は "into"、下端は展開状態で変わる。"""

    def _tree(self, sidebar_factory, *, expanded: bool, with_child: bool):
        def build(store):
            store.add("A", "A")
            group = store.add_group("G")
            group.expanded = expanded
            if with_child:
                store.add("g1", "g1", parent_id=group.id)
            store.add("Z", "Z")
        return sidebar_factory(build)

    def _at(self, sidebar, label: str, rel_y: int):
        fav = next(f for f in sidebar._store.favorites if f.label == label)
        item = sidebar._items_by_id[fav.id]
        return sidebar.tree._drop_target(point_in(sidebar.tree, item, rel_y))

    def test_group_middle_is_into(self, sidebar_factory):
        sidebar = self._tree(sidebar_factory, expanded=True, with_child=True)
        group = next(f for f in sidebar._store.favorites if f.is_group)
        target = self._at(sidebar, "G", 12)
        assert target.mode == "into"
        assert target.parent_id == group.id
        assert target.index == 1        # 末尾（既存の子 1 件の後ろ）
        assert target.depth == 1        # 中に入るので 1 段深い

    def test_collapsed_group_middle_is_still_into(self, sidebar_factory):
        sidebar = self._tree(sidebar_factory, expanded=False, with_child=True)
        target = self._at(sidebar, "G", 12)
        assert target.mode == "into"

    def test_group_top_band_inserts_before_group(self, sidebar_factory):
        sidebar = self._tree(sidebar_factory, expanded=True, with_child=True)
        target = self._at(sidebar, "G", 1)
        assert (target.parent_id, target.index, target.depth) == ("", 1, 0)

    def test_expanded_group_bottom_is_first_child(self, sidebar_factory):
        """T-3: 視覚上の次の行が第 1 子なので、そこへ入れる。"""
        sidebar = self._tree(sidebar_factory, expanded=True, with_child=True)
        group = next(f for f in sidebar._store.favorites if f.is_group)
        target = self._at(sidebar, "G", 23)
        assert (target.parent_id, target.index) == (group.id, 0)
        assert target.mode == "between"
        assert target.depth == 1

    def test_collapsed_group_bottom_is_next_sibling(self, sidebar_factory):
        sidebar = self._tree(sidebar_factory, expanded=False, with_child=True)
        target = self._at(sidebar, "G", 23)
        assert (target.parent_id, target.index, target.depth) == ("", 2, 0)

    def test_childless_group_bottom_is_next_sibling(self, sidebar_factory):
        sidebar = self._tree(sidebar_factory, expanded=True, with_child=False)
        target = self._at(sidebar, "G", 23)
        assert (target.parent_id, target.index, target.depth) == ("", 2, 0)

    def test_child_bottom_stays_in_group(self, sidebar_factory):
        """グループ内最後の子の下端は、グループの末尾（外へ出さない）。"""
        sidebar = self._tree(sidebar_factory, expanded=True, with_child=True)
        group = next(f for f in sidebar._store.favorites if f.is_group)
        target = self._at(sidebar, "g1", 23)
        assert (target.parent_id, target.index, target.depth) == (group.id, 1, 1)

    def test_bar_is_indented_by_depth(self, sidebar_factory):
        sidebar = self._tree(sidebar_factory, expanded=True, with_child=True)
        tree = sidebar.tree
        top = self._at(sidebar, "A", 1)
        nested = self._at(sidebar, "g1", 1)
        assert top.rect.left() == 0
        assert nested.rect.left() == tree.indentation()


class TestDropTargetEmptyArea:
    """T-4: 項目の無い空白 → トップ階層の末尾。"""

    def test_empty_area_appends_to_top_level(self, sidebar_factory):
        def build(store):
            store.add("A", "A")
            store.add("B", "B")
        sidebar = sidebar_factory(build)
        tree = sidebar.tree
        from PySide6.QtCore import QPoint
        last = sidebar._items_by_id[sidebar._store.favorites[-1].id]
        y = tree.visualItemRect(last).bottom() + 60
        assert tree.itemAt(QPoint(60, y)) is None  # 前提: 本当に空白
        target = tree._drop_target(QPoint(60, y))
        assert (target.parent_id, target.index, target.mode, target.depth) \
            == ("", 2, "between", 0)

    def test_empty_tree_appends_at_head(self, sidebar_factory):
        sidebar = sidebar_factory(lambda store: None)
        from PySide6.QtCore import QPoint
        target = sidebar.tree._drop_target(QPoint(60, 40))
        assert (target.parent_id, target.index) == ("", 0)

    def test_bar_stays_inside_viewport(self, sidebar_factory):
        """末尾のバーがビューポートからはみ出さない（描画で消えない）。"""
        def build(store):
            for i in range(40):
                store.add(f"f{i}", f"f{i}")
        sidebar = sidebar_factory(build)
        from PySide6.QtCore import QPoint
        target = sidebar.tree._drop_target(QPoint(60, 4000))
        rect = target.rect
        assert rect.top() >= 0
        assert rect.bottom() < sidebar.tree.viewport().height()


def send_drag_move(tree, point):
    from PySide6.QtCore import QMimeData, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(r"C:\Windows")])
    args = (Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
    tree.dragEnterEvent(QDragEnterEvent(point, *args))
    tree.dragMoveEvent(QDragMoveEvent(point, *args))
    return mime


class TestDropHintLifecycle:
    """T-8: インジケータの状態がドラッグ後に残らない。"""

    @pytest.fixture
    def sidebar(self, sidebar_factory):
        def build(store):
            for name in ("A", "B"):
                store.add(name, str(name))
        return sidebar_factory(build)

    def _hover(self, sidebar):
        tree = sidebar.tree
        item = sidebar._items_by_id[sidebar._store.favorites[0].id]
        return send_drag_move(tree, point_in(tree, item, 1))

    def test_hint_set_on_drag_move(self, sidebar):
        self._hover(sidebar)
        assert sidebar.tree._drop_hint is not None

    def test_hint_cleared_on_drag_leave(self, sidebar):
        from PySide6.QtGui import QDragLeaveEvent
        self._hover(sidebar)
        sidebar.tree.dragLeaveEvent(QDragLeaveEvent())
        assert sidebar.tree._drop_hint is None

    def test_hint_cleared_on_drop(self, sidebar, tmp_path):
        from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
        from PySide6.QtGui import QDropEvent
        tree = sidebar.tree
        self._hover(sidebar)
        target = tmp_path / "dropped"
        target.mkdir()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(target))])
        tree.dropEvent(QDropEvent(
            QPoint(*point_in(tree, sidebar._items_by_id[
                sidebar._store.favorites[0].id], 1).toTuple()),
            Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier))
        assert tree._drop_hint is None

    def test_paints_without_error(self, sidebar, qapp):
        """バー / 枠の描画が例外にならない（QPainter の使い方の回帰ゲート）。"""
        from PySide6.QtGui import QPixmap
        tree = sidebar.tree
        for mode in ("between", "into"):
            item = sidebar._items_by_id[sidebar._store.favorites[0].id]
            rect = tree.visualItemRect(item)
            from app.gui.favorites_sidebar import DropTarget
            tree._drop_hint = DropTarget("", 0, mode, rect, 0)
            pixmap = QPixmap(tree.viewport().size())
            tree.viewport().render(pixmap)
            qapp.processEvents()
        tree._drop_hint = None

    def test_indicator_color_comes_from_theme(self):
        """色のハードコード禁止（トークン経由であること）。"""
        import inspect
        import re

        from app.gui import favorites_sidebar
        src = inspect.getsource(favorites_sidebar._FavTree.paintEvent)
        assert 'current_tokens()["accent"]' in src
        # 生のカラーコード（#rgb / #rrggbb）が混ざっていない
        assert re.search(r"#[0-9a-fA-F]{3,8}\b", src) is None


class TestDropRouting:
    """T-7 / T-10 と §6: 外部登録・内部移動が 1 本の経路を通る。"""

    @pytest.fixture
    def sidebar(self, sidebar_factory, tmp_path):
        def build(store):
            for name in ("A", "B", "C"):
                (tmp_path / name).mkdir()
                store.add(name, str(tmp_path / name))
        return sidebar_factory(build)

    def _target(self, parent_id="", index=-1, mode="between"):
        from PySide6.QtCore import QRect

        from app.gui.favorites_sidebar import DropTarget
        return DropTarget(parent_id, index, mode, QRect(), 0)

    def _paths(self, tmp_path, *names):
        made = []
        for name in names:
            (tmp_path / name).mkdir(exist_ok=True)
            made.append(str(tmp_path / name))
        return made

    # ---- 外部ドロップ ----

    def test_inserts_at_position_in_order(self, sidebar, tmp_path):
        sidebar._on_urls_dropped(self._paths(tmp_path, "X", "Y"),
                                 self._target(index=1))
        assert labels_of(sidebar._store) == ["A", "X", "Y", "B", "C"]

    def test_duplicates_skipped_but_rest_inserted(self, sidebar, tmp_path):
        existing = str(tmp_path / "A")
        sidebar._on_urls_dropped([existing] + self._paths(tmp_path, "X"),
                                 self._target(index=0))
        assert labels_of(sidebar._store) == ["X", "A", "B", "C"]

    def test_all_duplicates_notifies_and_does_not_save(
            self, sidebar, tmp_path, monkeypatch):
        """T-7: 全件重複なら保存もせず、無言でもなく通知する。"""
        calls = []
        monkeypatch.setattr(sidebar._store, "save", lambda: calls.append(1))
        seen = []
        sidebar.notify_requested.connect(
            lambda kind, msg: seen.append((kind, msg)))
        sidebar._on_urls_dropped([str(tmp_path / "A"), str(tmp_path / "B")],
                                 self._target(index=0))
        assert calls == []
        assert len(seen) == 1 and seen[0][0] == "warning"
        assert labels_of(sidebar._store) == ["A", "B", "C"]

    def test_drop_into_group_appends_inside(self, sidebar_factory, tmp_path):
        """T-10: グループの上へのドロップは、従来どおり配下（末尾）へ。"""
        def build(store):
            group = store.add_group("G")
            store.add("g1", str(tmp_path / "g1"), parent_id=group.id)
        sidebar = sidebar_factory(build)
        group = next(f for f in sidebar._store.favorites if f.is_group)
        item = sidebar._items_by_id[group.id]
        target = sidebar.tree._drop_target(point_in(sidebar.tree, item, 12))
        assert target.mode == "into"
        sidebar._on_urls_dropped(self._paths(tmp_path, "X"), target)
        assert labels_of(sidebar._store, group.id) == ["g1", "X"]
        assert labels_of(sidebar._store) == ["G"]

    # ---- 内部移動 ----

    def test_move_single_item(self, sidebar):
        store = sidebar._store
        sidebar._on_items_moved([store.favorites[2].id], self._target(index=0))
        assert labels_of(store) == ["C", "A", "B"]

    def test_move_multiple_keeps_order(self, sidebar):
        """複数選択の移動で、掴んだ並びが崩れない。"""
        store = sidebar._store
        ids = [store.favorites[0].id, store.favorites[1].id]  # A, B
        sidebar._on_items_moved(ids, self._target(index=3))   # C の後ろへ
        assert labels_of(store) == ["C", "A", "B"]

    def test_move_into_group(self, sidebar_factory, tmp_path):
        def build(store):
            store.add_group("G")
            store.add("A", str(tmp_path / "A"))
            store.add("B", str(tmp_path / "B"))
        sidebar = sidebar_factory(build)
        store = sidebar._store
        gid = next(f.id for f in store.favorites if f.is_group)
        ids = [f.id for f in store.favorites if f.label in ("A", "B")]
        sidebar._on_items_moved(ids, self._target(parent_id=gid, index=0))
        assert labels_of(store, gid) == ["A", "B"]
        assert labels_of(store) == ["G"]

    def test_move_into_own_descendant_is_rejected(
            self, sidebar_factory, tmp_path):
        def build(store):
            outer = store.add_group("G")
            store.add_group("H", parent_id=outer.id)
        sidebar = sidebar_factory(build)
        store = sidebar._store
        outer = next(f for f in store.favorites if f.label == "G")
        inner = next(f for f in store.favorites if f.label == "H")
        sidebar._on_items_moved([outer.id],
                                self._target(parent_id=inner.id, index=0))
        assert labels_of(store) == ["G"]
        assert labels_of(store, outer.id) == ["H"]

    # ---- ドラッグ元の判定 ----

    def test_dragged_ids_use_display_order(self, sidebar):
        tree = sidebar.tree
        store = sidebar._store
        items = [sidebar._items_by_id[f.id] for f in store.favorites]
        items[2].setSelected(True)   # 選択順は C → A
        items[0].setSelected(True)
        assert tree.dragged_ids() == [store.favorites[0].id,
                                      store.favorites[2].id]

    def test_dragged_ids_drop_descendants_of_selection(
            self, sidebar_factory, tmp_path):
        """グループごと掴んだら、その中の子は二重に動かさない。"""
        def build(store):
            group = store.add_group("G")
            store.add("g1", str(tmp_path / "g1"), parent_id=group.id)
        sidebar = sidebar_factory(build)
        for item in sidebar._items_by_id.values():
            item.setSelected(True)
        group = next(f for f in sidebar._store.favorites if f.is_group)
        assert sidebar.tree.dragged_ids() == [group.id]

    def test_no_super_dropevent_call(self):
        """Qt に item を動かさせない（構造の正はストア側だけ）。"""
        import inspect

        from app.gui import favorites_sidebar
        src = inspect.getsource(favorites_sidebar._FavTree.dropEvent)
        assert "super().dropEvent" not in src

    def test_persist_structure_is_gone(self):
        """Qt の内部移動を前提にした後処理は残っていない。"""
        from app.gui.favorites_sidebar import FavoritesSidebar
        assert not hasattr(FavoritesSidebar, "_persist_structure")


class TestQtIndicatorNotUsed:
    """T-9: 発見 2 の穴（Qt の位置計算に頼る）へ戻っていないことの回帰ゲート。"""

    def test_drop_indicator_is_hidden(self, sidebar_factory):
        sidebar = sidebar_factory(lambda store: None)
        assert sidebar.tree.showDropIndicator() is False

    def test_no_reference_to_drop_indicator_position(self):
        """コメントでの言及は許すが、コードから呼ぶのは禁止。"""
        import ast
        from pathlib import Path

        offenders = []
        for path in Path("app").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = getattr(node, "attr", None) or getattr(node, "id", None)
                if name == "dropIndicatorPosition":
                    offenders.append(f"{path}:{node.lineno}")
        assert offenders == [], (
            "dropIndicatorPosition() は showDropIndicator(False) と併用できない。"
            " docs/investigation/qt_drop_indicator_20260731.md を参照。")
