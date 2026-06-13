"""D&D ビューとインクリメンタル検索のスモークテスト（offscreen）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui.main_window import MainWindow  # noqa: E402
from app.gui.search_panel import SearchPanel  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _wait_for(condition, timeout_ms=5000) -> bool:
    loop = QEventLoop()
    elapsed = 0
    while not condition() and elapsed < timeout_ms:
        QTimer.singleShot(50, loop.quit)
        loop.exec()
        elapsed += 50
    return condition()


class TestDropHandling:
    def test_drop_moves_with_undo(self, qapp, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "f.txt").write_text("x")

        window = MainWindow()
        window._on_files_dropped([str(src / "f.txt")], str(dst), False)
        assert (dst / "f.txt").exists()
        assert not (src / "f.txt").exists()
        window.undo_last()
        assert (src / "f.txt").exists()
        window.close()

    def test_drop_copy_with_ctrl(self, qapp, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "f.txt").write_text("x")

        window = MainWindow()
        window._on_files_dropped([str(src / "f.txt")], str(dst), True)
        assert (dst / "f.txt").exists()
        assert (src / "f.txt").exists()  # コピーなので元が残る
        window.close()

    def test_views_accept_drops(self, qapp):
        window = MainWindow()
        assert window.table.acceptDrops()
        assert window.tree.acceptDrops()
        assert window.table.dragEnabled()
        window.close()


class TestIncrementalSearch:
    def test_typing_triggers_filename_search(self, qapp, tmp_path):
        (tmp_path / "target_a.txt").write_text("x")
        (tmp_path / "other.txt").write_text("x")
        panel = SearchPanel()
        panel.set_root(str(tmp_path))
        panel.keyword_edit.setText("target")  # textChanged → デバウンス起動
        assert _wait_for(
            lambda: panel.results.count() == 1 and panel.search_btn.isEnabled())
        panel.cancel_search()

    def test_clearing_keyword_clears_results(self, qapp, tmp_path):
        (tmp_path / "target.txt").write_text("x")
        panel = SearchPanel()
        panel.set_root(str(tmp_path))
        panel.keyword_edit.setText("target")
        assert _wait_for(lambda: panel.results.count() == 1)
        panel.keyword_edit.setText("")
        assert _wait_for(lambda: panel.results.count() == 0)
        panel.cancel_search()

    def test_content_mode_not_incremental(self, qapp, tmp_path):
        (tmp_path / "target.txt").write_text("x")
        panel = SearchPanel()
        panel.set_root(str(tmp_path))
        panel.mode_text.setChecked(True)
        panel.keyword_edit.setText("target")
        # デバウンスは起動しない
        assert not panel._debounce.isActive()
        panel.cancel_search()
