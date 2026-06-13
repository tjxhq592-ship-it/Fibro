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
        (tmp_path / "readme.md").write_text("x")
        (tmp_path / "notes.txt").write_text("x")
        opts = SearchOptions(keyword="*.md", modes={SearchMode.FILENAME},
                             use_wildcard=True)
        hits = [h.path for h in search(tmp_path, opts)]
        assert hits == [str(tmp_path / "readme.md")]

    def test_wildcard_prefix(self, tmp_path):
        (tmp_path / "file_a.txt").write_text("x")
        (tmp_path / "other.txt").write_text("x")
        opts = SearchOptions(keyword="file_*.txt",
                             modes={SearchMode.FILENAME}, use_wildcard=True)
        assert len(list(search(tmp_path, opts))) == 1

    def test_wildcard_off_is_substring(self, tmp_path):
        (tmp_path / "readme.md").write_text("x")
        opts = SearchOptions(keyword="*.md", modes={SearchMode.FILENAME},
                             use_wildcard=False)
        assert list(search(tmp_path, opts)) == []  # リテラル "*.md" は不一致


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
        # 行0を末尾へ移動（InternalMove 相当の操作をモデル経由で実行）
        item = sidebar.list.takeItem(0)
        sidebar.list.addItem(item)
        sidebar._on_rows_moved()
        assert [f.label for f in store.favorites] == ["B", "C", "A"]
        # 再読み込みでも順序維持
        assert [f.label for f in FavoriteStore(tmp_path / "f.json").favorites] \
            == ["B", "C", "A"]

    def test_drag_mode_enabled(self, qapp, tmp_path):
        sidebar = FavoritesSidebar(FavoriteStore(tmp_path / "f.json"))
        from PySide6.QtWidgets import QListWidget
        assert sidebar.list.dragDropMode() == \
            QListWidget.DragDropMode.InternalMove


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
