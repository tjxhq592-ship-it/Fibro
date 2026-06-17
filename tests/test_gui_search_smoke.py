"""検索パネル・お気に入りサイドバーのスモークテスト（offscreen）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui.favorites_sidebar import FavoritesSidebar  # noqa: E402
from app.gui.search_panel import SearchPanel  # noqa: E402
from app.gui.theme import ThemeManager  # noqa: E402
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
    assert sidebar.tree.topLevelItemCount() == 1

    received = []
    sidebar.path_selected.connect(received.append)
    sidebar._on_clicked(sidebar.tree.topLevelItem(0))
    assert received == [str(tmp_path)]


def test_favorites_hierarchy(qapp, tmp_path):
    """グループにお気に入りをネストして保存・復元できる。"""
    store = FavoriteStore(tmp_path / "favorites.json")
    group = store.add_group("プロジェクト")
    store.add("Project A", str(tmp_path), parent_id=group.id)

    sidebar = FavoritesSidebar(store)
    # トップ階層はグループ1つ、その配下に子1つ
    assert sidebar.tree.topLevelItemCount() == 1
    top = sidebar.tree.topLevelItem(0)
    assert top.childCount() == 1

    # 再起動相当: ディスクから読み直しても階層が保たれる
    store2 = FavoriteStore(tmp_path / "favorites.json")
    groups = [f for f in store2.favorites if f.is_group]
    assert len(groups) == 1
    children = store2.children_of(groups[0].id)
    assert len(children) == 1
    assert children[0].label == "Project A"


def test_favorites_remove_group_removes_children(qapp, tmp_path):
    """グループ削除で子孫もまとめて削除される。"""
    store = FavoriteStore(tmp_path / "favorites.json")
    group = store.add_group("G")
    store.add("child", str(tmp_path), parent_id=group.id)
    assert len(store.favorites) == 2
    store.remove(group.id)
    assert store.favorites == []


def test_search_options_persist_across_restart(qapp, tmp_path):
    """検索オプションがセッションをまたいで保存される（共有 settings 経由）。"""
    settings_file = tmp_path / "settings.json"

    # セッション1: オプションを変更
    tm1 = ThemeManager(settings_file)
    panel1 = SearchPanel(settings=tm1)
    panel1.recursive_check.setChecked(False)
    panel1.index_check.setChecked(True)

    # 別コンポーネント（MainWindow 相当）が layout を書いても消えないこと
    tm1.set("layout", {"window": [0, 0, 800, 600]})

    # セッション2: 新しい ThemeManager でディスクから読み直す
    tm2 = ThemeManager(settings_file)
    panel2 = SearchPanel(settings=tm2)
    assert panel2.recursive_check.isChecked() is False
    assert panel2.index_check.isChecked() is True
    # layout も残っていること（相互上書きが起きない）
    assert tm2.get("layout") == {"window": [0, 0, 800, 600]}
