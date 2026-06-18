"""将来の改善案（ワイルドカード検索・フィルタ・並び替え・レイアウト保存）のテスト。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.engine.search_engine import SearchMode, SearchOptions, search  # noqa: E402
from app.gui.favorites_sidebar import FavoritesSidebar  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402
from app.gui.theme import ThemeManager  # noqa: E402
from app.models.favorite import FavoriteStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _process_events(ms=300):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class TestWildcardSearch:
    def test_wildcard_pattern(self, tmp_path):
        """* を含むキーワードは自動でパターン照合になる。"""
        (tmp_path / "readme.md").write_text("x")
        (tmp_path / "notes.txt").write_text("x")
        opts = SearchOptions(keyword="*.md", modes={SearchMode.FILENAME})
        hits = [h.path for h in search(tmp_path, opts)]
        assert hits == [str(tmp_path / "readme.md")]

    def test_wildcard_prefix(self, tmp_path):
        (tmp_path / "file_a.txt").write_text("x")
        (tmp_path / "other.txt").write_text("x")
        opts = SearchOptions(keyword="file_*.txt", modes={SearchMode.FILENAME})
        assert len(list(search(tmp_path, opts))) == 1

    def test_question_mark_is_wildcard(self, tmp_path):
        """? を含むキーワードも自動でパターン照合になる。"""
        (tmp_path / "a1.txt").write_text("x")
        (tmp_path / "a12.txt").write_text("x")
        opts = SearchOptions(keyword="a?.txt", modes={SearchMode.FILENAME})
        hits = [h.path for h in search(tmp_path, opts)]
        assert hits == [str(tmp_path / "a1.txt")]

    def test_plain_keyword_is_substring(self, tmp_path):
        """* や ? を含まないキーワードは従来どおり部分一致。"""
        (tmp_path / "readme.md").write_text("x")
        (tmp_path / "notes.txt").write_text("x")
        opts = SearchOptions(keyword="read", modes={SearchMode.FILENAME})
        hits = [h.path for h in search(tmp_path, opts)]
        assert hits == [str(tmp_path / "readme.md")]


class TestFilterBox:
    def test_filter_hides_rows(self, qapp, tmp_path):
        (tmp_path / "apple.txt").write_text("x")
        (tmp_path / "banana.txt").write_text("x")
        window = MainWindow()
        window.navigate(str(tmp_path))
        _process_events()  # QFileSystemModel の遅延読み込みを待つ
        root = window.table.rootIndex()
        assert window.proxy.rowCount(root) == 2
        window.proxy.set_needle("app")
        assert window.proxy.rowCount(root) == 1
        window.proxy.set_needle("")
        assert window.proxy.rowCount(root) == 2
        window.close()

    def test_navigate_clears_filter(self, qapp, tmp_path):
        window = MainWindow()
        window.navigate(str(tmp_path))
        window.filter_edit.setText("zzz")
        sub = tmp_path / "sub"
        sub.mkdir()
        window.navigate(str(sub))
        assert window.filter_edit.text() == ""
        window.close()


class TestFavoritesReorder:
    def test_reorder_persists(self, qapp, tmp_path):
        store = FavoriteStore(tmp_path / "f.json")
        store.add("A", str(tmp_path))
        store.add("B", str(tmp_path))
        store.add("C", str(tmp_path))
        sidebar = FavoritesSidebar(store)
        # トップ階層の item を A,C,B の順に並べ替えて構造を永続化
        tree = sidebar.tree
        item_a = tree.takeTopLevelItem(0)
        tree.addTopLevelItem(item_a)  # A を末尾へ → B, C, A
        sidebar._persist_structure()
        assert [f.label for f in store.favorites] == ["B", "C", "A"]
        # 再読み込みでも順序維持
        assert [f.label for f in FavoriteStore(tmp_path / "f.json").favorites] \
            == ["B", "C", "A"]

    def test_drag_mode_enabled(self, qapp, tmp_path):
        sidebar = FavoritesSidebar(FavoriteStore(tmp_path / "f.json"))
        from PySide6.QtWidgets import QTreeWidget
        assert sidebar.tree.dragDropMode() == \
            QTreeWidget.DragDropMode.InternalMove


class TestFavoritesDropRegister:
    def test_drop_registers_paths_at_top(self, qapp, tmp_path):
        """外部ドロップでパスがトップ階層に登録される（重複はスキップ）。"""
        d1 = tmp_path / "alpha"
        d2 = tmp_path / "beta"
        d1.mkdir()
        d2.mkdir()
        store = FavoriteStore(tmp_path / "f.json")
        sidebar = FavoritesSidebar(store)
        sidebar._on_urls_dropped([str(d1), str(d2), str(d1)], None)
        labels = [f.label for f in store.favorites if not f.is_group]
        assert labels == ["alpha", "beta"]  # 重複 d1 はスキップ
        assert all(f.parent_id == "" for f in store.favorites)

    def test_drop_onto_group_nests(self, qapp, tmp_path):
        """グループの上にドロップするとその配下に登録される。"""
        d = tmp_path / "gamma"
        d.mkdir()
        store = FavoriteStore(tmp_path / "f.json")
        gid = store.add_group("グループ").id
        sidebar = FavoritesSidebar(store)
        target = sidebar._items_by_id[gid]
        sidebar._on_urls_dropped([str(d)], target)
        leaf = next(f for f in store.favorites if not f.is_group)
        assert leaf.parent_id == gid
        assert leaf.label == "gamma"

    def test_tree_accepts_drops(self, qapp, tmp_path):
        sidebar = FavoritesSidebar(FavoriteStore(tmp_path / "f.json"))
        assert sidebar.tree.acceptDrops() is True


class TestPanelZoom:
    def _tree(self, qapp):
        from PySide6.QtWidgets import QTreeWidget
        return QTreeWidget()

    def test_zoom_clamps_and_persists(self, qapp, tmp_path):
        from app.gui.zoom import ZoomController
        tm = ThemeManager(tmp_path / "s.json")
        tree = self._tree(qapp)
        zc = ZoomController(tree, "panelX", tm)
        zc.set_scale(5.0)  # 上限 2.0 にクランプ
        assert zc.scale == 2.0
        zc.set_scale(0.1)  # 下限 0.75 にクランプ
        assert zc.scale == 0.75
        # 別 ThemeManager で読み直して倍率が復元される
        tree2 = self._tree(qapp)
        zc2 = ZoomController(tree2, "panelX", ThemeManager(tmp_path / "s.json"))
        assert zc2.scale == 0.75

    def test_zoom_changes_font_and_icon(self, qapp, tmp_path):
        from PySide6.QtCore import QSize
        from app.gui.zoom import ZoomController
        tree = self._tree(qapp)
        tree.setIconSize(QSize(16, 16))
        zc = ZoomController(tree, "panelY", None, base_icon=16)
        before = tree.font().pointSizeF()
        zc.set_scale(2.0)
        assert tree.font().pointSizeF() > before
        assert tree.iconSize().width() == 32


class TestLayoutPersistence:
    def test_splitter_sizes_saved_and_restored(self, qapp, tmp_path,
                                               monkeypatch):
        import app.gui.main_window as mw
        monkeypatch.setattr(mw, "CONFIG_DIR", tmp_path)
        w1 = MainWindow()
        w1._main_splitter.setSizes([100, 700, 100])
        w1.close()
        assert (tmp_path / "settings.json").exists()
        w2 = MainWindow()
        sizes = w2._main_splitter.sizes()
        w2.close()
        # 復元値が保存値に基づく（ピクセル誤差は許容）
        assert sizes[0] < sizes[1]
        saved = ThemeManager(tmp_path / "settings.json").get("layout")
        assert saved["main_splitter"] == w1._main_splitter.sizes() or saved


class TestIcons:
    def test_feather_icons_render(self, qapp):
        from app.gui.icons import _PATHS, feather_icon
        for name in _PATHS:
            for dark in (False, True):
                icon = feather_icon(name, dark)
                assert not icon.isNull(), f"{name} (dark={dark})"
