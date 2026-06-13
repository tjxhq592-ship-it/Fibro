"""検索パネル・お気に入りサイドバーのスモークテスト（offscreen）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui.favorites_sidebar import FavoritesSidebar  # noqa: E402
from app.gui.search_panel import SearchPanel  # noqa: E402
from app.models.favorite import FavoriteStore  # noqa: E402


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


def test_search_panel_streams_results(qapp, tmp_path):
    for i in range(5):
        (tmp_path / f"target_{i}.txt").write_text("hello target inside")
    (tmp_path / "other.txt").write_text("nothing")

    panel = SearchPanel()
    panel.set_root(str(tmp_path))
    panel.keyword_edit.setText("target")
    panel.mode_text.setChecked(True)
    panel.start_search()

    assert _wait_for(lambda: panel.search_btn.isEnabled())  # 完了待ち
    # ファイル名5件 + テキスト内容5件
    assert panel.results.count() == 10
    panel.cancel_search()


def test_search_panel_cancel(qapp, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    panel = SearchPanel()
    panel.set_root(str(tmp_path))
    panel.keyword_edit.setText("a")
    panel.start_search()
    panel.cancel_search()  # 即キャンセルしてもクラッシュしない
    assert panel._worker is None


def test_favorites_sidebar_roundtrip(qapp, tmp_path):
    store = FavoriteStore(tmp_path / "favorites.json")
    store.add("テスト", str(tmp_path))
    sidebar = FavoritesSidebar(store)
    assert sidebar.list.count() == 1

    received = []
    sidebar.path_selected.connect(received.append)
    sidebar._on_clicked(sidebar.list.item(0))
    assert received == [str(tmp_path)]
