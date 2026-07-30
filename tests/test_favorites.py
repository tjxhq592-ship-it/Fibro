import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models.favorite import FavoriteStore


class TestPersistence:
    def test_add_and_reload(self, tmp_path):
        config = tmp_path / "config" / "favorites.json"
        store = FavoriteStore(config)
        store.add("プロジェクト", r"C:\Users\hiros\projects",
                  tags=["work"], note="2026年度")
        # 再読み込み（再起動相当）
        store2 = FavoriteStore(config)
        assert len(store2.favorites) == 1
        fav = store2.favorites[0]
        assert fav.label == "プロジェクト"
        assert fav.tags == ["work"]
        assert fav.note == "2026年度"

    def test_remove(self, tmp_path):
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        fav = store.add("a", "C:\\")
        assert store.remove(fav.id)
        assert FavoriteStore(config).favorites == []

    def test_remove_missing_returns_false(self, tmp_path):
        store = FavoriteStore(tmp_path / "favorites.json")
        assert not store.remove("nonexistent")


class TestCorruption:
    def test_corrupt_json_falls_back(self, tmp_path):
        config = tmp_path / "favorites.json"
        config.write_text("{ broken json !!!", encoding="utf-8")
        store = FavoriteStore(config)
        assert store.favorites == []
        # 保存し直せば復旧する
        store.add("x", "C:\\")
        assert len(FavoriteStore(config).favorites) == 1

    def test_wrong_schema_falls_back(self, tmp_path):
        config = tmp_path / "favorites.json"
        config.write_text('{"favorites": [{"nope": 1}]}', encoding="utf-8")
        assert FavoriteStore(config).favorites == []


class TestExpandedState:
    """グループ展開状態の永続化（モデル層）。"""

    def test_new_group_expanded_by_default(self, tmp_path):
        store = FavoriteStore(tmp_path / "favorites.json")
        assert store.add_group("G").expanded is True

    def test_expanded_roundtrip(self, tmp_path):
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        group = store.add_group("G")
        group.expanded = False
        store.save()
        store2 = FavoriteStore(config)
        assert store2.favorites[0].expanded is False

    def test_legacy_json_without_expanded_falls_back_true(self, tmp_path):
        # 旧形式（expanded キー無し）は True にフォールバックし例外も出ない
        config = tmp_path / "favorites.json"
        config.write_text(json.dumps({"favorites": [
            {"label": "G", "path": "", "id": "abc12345",
             "parent_id": "", "is_group": True},
            {"label": "leaf", "path": "C:\\\\", "id": "def67890",
             "parent_id": "abc12345"},
        ]}), encoding="utf-8")
        store = FavoriteStore(config)
        assert len(store.favorites) == 2
        assert all(f.expanded is True for f in store.favorites)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestExpandedStateGui:
    """グループ展開状態が refresh() でリセットされない（GUI 層）。"""

    @staticmethod
    def _sidebar(store):
        from app.gui.favorites_sidebar import FavoritesSidebar
        return FavoritesSidebar(store)

    def test_collapsed_group_survives_operations(self, qapp, tmp_path):
        store = FavoriteStore(tmp_path / "favorites.json")
        group = store.add_group("G")
        store.add("child", str(tmp_path), parent_id=group.id)
        sidebar = self._sidebar(store)
        item = sidebar._items_by_id[group.id]
        assert item.isExpanded() is True  # 新規グループは展開状態

        # ユーザー操作相当の折りたたみ → itemCollapsed 経由で保存される
        item.setExpanded(False)
        assert group.expanded is False

        # 別のお気に入りを追加（refresh が走る操作の代表）
        store.add("other", str(tmp_path))
        sidebar.refresh()
        assert group.expanded is False
        assert sidebar._items_by_id[group.id].isExpanded() is False

        # リネーム相当（label 変更 + refresh）でも維持される
        group.label = "G2"
        store.save()
        sidebar.refresh()
        assert sidebar._items_by_id[group.id].isExpanded() is False

    def test_user_expand_is_persisted(self, qapp, tmp_path):
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        group = store.add_group("G")
        group.expanded = False
        store.save()
        sidebar = self._sidebar(store)
        item = sidebar._items_by_id[group.id]
        assert item.isExpanded() is False  # 保存済み状態を復元

        item.setExpanded(True)  # ユーザー操作相当
        assert group.expanded is True
        # ディスクにも保存されている（再起動相当で復元できる）
        assert FavoriteStore(config).favorites[0].expanded is True

    def test_refresh_does_not_trigger_save(self, qapp, tmp_path, monkeypatch):
        # _restoring ガード: 再構築中の setExpanded() が save を起こさない
        store = FavoriteStore(tmp_path / "favorites.json")
        group = store.add_group("G")
        store.add("child", str(tmp_path), parent_id=group.id)
        sidebar = self._sidebar(store)

        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))
        sidebar.refresh()
        assert calls == []


class TestReachability:
    def test_existing_dir(self, tmp_path):
        store = FavoriteStore(tmp_path / "f.json")
        fav = store.add("here", str(tmp_path))
        assert fav.is_reachable()

    def test_missing_dir(self, tmp_path):
        store = FavoriteStore(tmp_path / "f.json")
        fav = store.add("gone", str(tmp_path / "no_such_dir"))
        assert not fav.is_reachable()

    def test_find_by_path(self, tmp_path):
        store = FavoriteStore(tmp_path / "f.json")
        store.add("here", str(tmp_path))
        assert store.find_by_path(str(tmp_path)) is not None
        assert store.find_by_path("C:\\nope") is None


class TestBulkExpandCollapse:
    """一括開閉（見出しのボタンから呼ばれる）。

    肝は保存回数。itemExpanded/itemCollapsed 経由の _set_expanded_state() は
    1 件ごとに save() を呼ぶので、素直に expandAll() するとグループ数だけ
    ファイル書き込みが走る。
    """

    @staticmethod
    def _sidebar(store):
        from app.gui.favorites_sidebar import FavoritesSidebar
        return FavoritesSidebar(store)

    @staticmethod
    def _tree_groups(sidebar, store):
        return [sidebar._items_by_id[f.id] for f in store.favorites
                if f.is_group]

    def _store_with_groups(self, tmp_path, n=4):
        store = FavoriteStore(tmp_path / "favorites.json")
        parent = store.add_group("G0")
        for i in range(1, n):
            # ネストも混ぜる（一括なので階層の深さに関わらず全部開く）
            store.add_group(f"G{i}", parent_id=parent.id if i % 2 else "")
        store.add("leaf", str(tmp_path), parent_id=parent.id)
        return store

    def test_collapse_all_saves_once(self, qapp, tmp_path, monkeypatch):
        store = self._store_with_groups(tmp_path)
        sidebar = self._sidebar(store)
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))

        sidebar.expand_all_groups(False)

        assert all(not it.isExpanded() for it in self._tree_groups(sidebar, store))
        assert all(not f.expanded for f in store.favorites if f.is_group)
        assert len(calls) == 1  # グループ数ぶん走らない

    def test_expand_all_saves_once(self, qapp, tmp_path, monkeypatch):
        store = self._store_with_groups(tmp_path)
        sidebar = self._sidebar(store)
        sidebar.expand_all_groups(False)  # いったん全部畳んでから
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))

        sidebar.expand_all_groups(True)

        assert all(it.isExpanded() for it in self._tree_groups(sidebar, store))
        assert all(f.expanded for f in store.favorites if f.is_group)
        assert len(calls) == 1

    def test_no_change_skips_save(self, qapp, tmp_path, monkeypatch):
        """すでに全部その状態なら書き込まない（_set_expanded_state と同じ扱い）。"""
        store = self._store_with_groups(tmp_path)
        sidebar = self._sidebar(store)
        calls = []
        monkeypatch.setattr(store, "save", lambda: calls.append(1))
        sidebar.expand_all_groups(True)  # 新規グループは既に expanded=True
        assert calls == []

    def test_persisted_to_disk(self, qapp, tmp_path):
        """再起動相当で復元できる（グループぶんが永続化されている）。"""
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        g1 = store.add_group("A")
        g2 = store.add_group("B", parent_id=g1.id)
        sidebar = self._sidebar(store)

        sidebar.expand_all_groups(False)

        reloaded = FavoriteStore(config)
        assert {f.id for f in reloaded.favorites} == {g1.id, g2.id}
        assert all(f.expanded is False for f in reloaded.favorites)

    def test_leaves_are_untouched(self, qapp, tmp_path):
        """グループでないお気に入りは影響を受けない。"""
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        group = store.add_group("G")
        leaf = store.add("leaf", str(tmp_path), parent_id=group.id)
        sidebar = self._sidebar(store)
        before = json.loads(json.dumps(vars(leaf), default=str))

        sidebar.expand_all_groups(False)

        assert json.loads(json.dumps(vars(leaf), default=str)) == before
        assert group.expanded is False

    def test_restoring_flag_is_reset(self, qapp, tmp_path):
        """一括操作のあとも通常のユーザー操作の保存が効く。"""
        store = self._store_with_groups(tmp_path, n=2)
        sidebar = self._sidebar(store)
        sidebar.expand_all_groups(False)
        assert sidebar._restoring is False

        group = next(f for f in store.favorites if f.is_group)
        sidebar._items_by_id[group.id].setExpanded(True)  # ユーザー操作相当
        assert group.expanded is True
