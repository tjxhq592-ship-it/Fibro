"""タブ機能・デュアルペイン・クイックプレビューのテスト。

GUI 部分は offscreen でスモーク、プレビュー分類は純粋テスト。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui.preview_dialog import preview_kind, read_text_preview  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_window(tmp_path, monkeypatch):
    import app.paths as paths
    import app.gui.main_window as mw
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(mw, "CONFIG_DIR", tmp_path / "config")
    from app.gui.main_window import MainWindow
    return MainWindow()


def _capture_popup(monkeypatch, show_menu):
    """トップバーのボタンが組み立てるポップアップメニューを取り出す。

    `QMenu.exec` はクラス属性の差し替えが効かない。Shiboken がインスタンス
    参照で C++ の built-in を返すため、conftest のモーダル抑止をすり抜けて
    ネストしたイベントループに入ったまま返らない（＝テストがハングする）。
    継承で潰したものを main_window の名前空間へ差し込んで回避する。
    """
    import app.gui.main_window as mw
    captured = []

    class _CapturingMenu(mw.QMenu):
        def exec(self, *a, **k):  # noqa: A003 — Qt API
            captured.append(self)
            return None

        def exec_(self, *a, **k):
            return self.exec(*a, **k)

    monkeypatch.setattr(mw, "QMenu", _CapturingMenu)
    show_menu()
    assert captured, "ポップアップメニューが exec されなかった"
    return captured[-1]


class TestColumnSorting:
    def _wait_rows(self, qapp, model, root_index, n, limit=400):
        import time
        for _ in range(limit):
            qapp.processEvents()
            if model.rowCount(root_index) >= n:
                return
            time.sleep(0.005)

    def _sorted_names(self, qapp, d, column):
        """d を FilePane で開き、指定列で昇順ソートした名前列を返す。

        QFileSystemModel の監視スレッドがプロセス終了時に segfault しないよう、
        使い終わったら root を解除して破棄する。
        """
        from PySide6.QtCore import Qt
        from app.gui.file_pane import FilePane
        pane = FilePane()
        try:
            src = pane.list_model
            proxy = pane.proxy
            root_src = src.setRootPath(str(d))
            self._wait_rows(qapp, src, root_src, 2)
            proxy.set_root_path(str(d))
            proxy.sort(column, Qt.SortOrder.AscendingOrder)
            root_proxy = proxy.mapFromSource(root_src)
            return [proxy.index(r, 0, root_proxy).data()
                    for r in range(proxy.rowCount(root_proxy))]
        finally:
            pane.list_model.setRootPath("")
            pane.deleteLater()
            qapp.processEvents()

    def test_date_sort_uses_real_datetime(self, qapp, tmp_path):
        """更新日時ソートは表示文字列でなく実日時で並ぶ（8:00 < 10:00）。"""
        import os
        import time
        d = tmp_path / "sortdir"
        d.mkdir()
        early = d / "early.txt"
        late = d / "late.txt"
        early.write_text("x")
        late.write_text("x")
        # 同日・時刻のみ差。文字列比較だと "10:00" < "8:00" で逆転する条件。
        base = time.mktime((2026, 6, 18, 8, 0, 0, 0, 0, -1))
        os.utime(early, (base, base))
        os.utime(late, (base + 2 * 3600, base + 2 * 3600))  # 10:00
        names = self._sorted_names(qapp, d, 3)
        assert names.index("early.txt") < names.index("late.txt")

    def test_size_sort_uses_real_bytes(self, qapp, tmp_path):
        """サイズソートは表示文字列でなく実バイト数で並ぶ。"""
        d = tmp_path / "sizedir"
        d.mkdir()
        (d / "small.bin").write_bytes(b"x" * 10)
        (d / "big.bin").write_bytes(b"x" * 5000)
        names = self._sorted_names(qapp, d, 1)
        assert names.index("small.bin") < names.index("big.bin")

    def test_headers_are_japanese(self, qapp, tmp_path):
        """列見出しが日本語で表示される。"""
        from PySide6.QtCore import Qt
        from app.gui.file_pane import FilePane
        pane = FilePane()
        try:
            h = pane.proxy.headerData
            o = Qt.Orientation.Horizontal
            assert h(0, o) == "名前"
            assert h(1, o) == "サイズ"
            assert h(2, o) == "種類"
            assert h(3, o) == "更新日時"
        finally:
            pane.deleteLater()
            qapp.processEvents()


# ---- プレビュー分類（純粋） ----
class TestPreviewKind:
    def test_image_by_ext(self, tmp_path):
        p = tmp_path / "a.PNG"
        p.write_bytes(b"\x89PNG\r\n")
        assert preview_kind(p) == "image"

    def test_text_by_ext(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("# hi", encoding="utf-8")
        assert preview_kind(p) == "text"

    def test_unknown_ext_text_if_not_binary(self, tmp_path):
        p = tmp_path / "data.unknown"
        p.write_text("plain content", encoding="utf-8")
        assert preview_kind(p) == "text"

    def test_binary_is_info(self, tmp_path):
        p = tmp_path / "blob.dat"
        p.write_bytes(b"\x00\x01\x02\x00binary")
        assert preview_kind(p) == "info"

    def test_directory_is_info(self, tmp_path):
        d = tmp_path / "dir"
        d.mkdir()
        assert preview_kind(d) == "info"

    def test_read_text_preview_truncates(self, tmp_path):
        p = tmp_path / "big.txt"
        p.write_text("x" * 20000, encoding="utf-8")
        assert len(read_text_preview(p, max_bytes=100)) <= 100

    def test_read_text_preview_cp932(self, tmp_path):
        p = tmp_path / "sjis.txt"
        p.write_bytes("日本語テキスト".encode("cp932"))
        assert "日本語" in read_text_preview(p)


# ---- タブ機能 ----
class TestTabs:
    def test_initial_single_tab(self, qapp, tmp_path, monkeypatch):
        win = _make_window(tmp_path, monkeypatch)
        assert len(win._tabs) == 1

    def test_new_tab_switches_active(self, qapp, tmp_path, monkeypatch):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.new_tab(str(b))
        assert len(win._tabs) == 2
        assert win.current_path == str(b)

    def test_switch_tab_restores_path(self, qapp, tmp_path, monkeypatch):
        a, b = tmp_path / "a2", tmp_path / "b2"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.new_tab(str(b))
        win.prev_tab()
        assert win.current_path == str(a)

    def test_close_tab_keeps_minimum_one(self, qapp, tmp_path, monkeypatch):
        win = _make_window(tmp_path, monkeypatch)
        win.close_current_tab()
        assert len(win._tabs) == 1  # 最低1枚は残る

    def test_close_tab_reduces_count(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "a3"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.new_tab(str(a))
        win.close_current_tab()
        assert len(win._tabs) == 1

    def test_tab_persistence(self, qapp, tmp_path, monkeypatch):
        a, b = tmp_path / "p1", tmp_path / "p2"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.new_tab(str(b))
        win._save_tabs()
        saved = win.project_settings.get("tabs", [])
        assert str(a) in saved and str(b) in saved

    # 21: 「＋」タブ追加ボタン
    def test_plus_button_adds_tab(self, qapp, tmp_path, monkeypatch):
        win = _make_window(tmp_path, monkeypatch)
        before = len(win._tabs)
        win.new_tab_btn.click()
        assert len(win._tabs) == before + 1

    def test_plus_button_exists(self, qapp, tmp_path, monkeypatch):
        win = _make_window(tmp_path, monkeypatch)
        assert win.new_tab_btn.text() == "＋"

    def test_ctrl_tab_switches_tabs(self, qapp, tmp_path, monkeypatch):
        """Ctrl+Tab で次タブ、Ctrl+Shift+Tab で前タブへ（eventFilter 経由）。"""
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import Qt
        a, b = tmp_path / "t1", tmp_path / "t2"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.new_tab(str(b))  # 2タブ・現在は index 1
        assert win.tab_bar.count() == 2
        start = win.tab_bar.currentIndex()

        def send(key, mods):
            ev = QKeyEvent(QEvent.Type.KeyPress, key, mods)
            assert win.eventFilter(win, ev) is True

        send(Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
        assert win.tab_bar.currentIndex() == (start + 1) % 2
        send(Qt.Key.Key_Backtab,
             Qt.KeyboardModifier.ControlModifier
             | Qt.KeyboardModifier.ShiftModifier)
        assert win.tab_bar.currentIndex() == start


class TestAsyncSelectionSize:
    def test_size_job_sums_files(self, tmp_path):
        """_SelectionSizeJob はファイルサイズ合計を emit する（同期実行で検証）。"""
        from app.gui.main_window import _SelectionSizeJob
        a = tmp_path / "a.bin"
        a.write_bytes(b"x" * 100)
        b = tmp_path / "b.bin"
        b.write_bytes(b"y" * 50)
        d = tmp_path / "sub"
        d.mkdir()  # ディレクトリは加算しない
        got = []
        job = _SelectionSizeJob([str(a), str(b), str(d)], 7,
                                lambda g, t: got.append((g, t)))
        job.run()
        assert got == [(7, 150)]

    def test_apply_size_ignores_stale_gen(self, qapp, tmp_path, monkeypatch):
        """古い世代の結果は破棄され、最新世代のみ反映される。"""
        win = _make_window(tmp_path, monkeypatch)
        win._sel_gen = 5
        win._sel_count = 3
        win.selection_label.setText("選択: 3件")
        win._apply_selection_size(4, 999)  # 古い → 無視
        assert "/" not in win.selection_label.text()
        win._apply_selection_size(5, 999)  # 最新 → 反映
        assert "/" in win.selection_label.text()


class TestFavoritesAsyncReachability:
    def test_unreachable_marked_async(self, qapp, tmp_path):
        """到達性は同期で見ず、非同期で(到達不可)が後付けされる。"""
        from PySide6.QtCore import QThreadPool
        from app.models.favorite import FavoriteStore
        from app.gui.favorites_sidebar import FavoritesSidebar
        store = FavoriteStore(tmp_path / "f.json")
        ok_fav = store.add("ok", str(tmp_path))
        ng_fav = store.add("ng", str(tmp_path / "no_such_dir"))
        sb = FavoritesSidebar(store)
        # 構築直後は同期チェックしていないので両方マーク無し
        assert "(到達不可)" not in sb._items_by_id[ng_fav.id].text(0)
        # 非同期チェック完了を待つ
        QThreadPool.globalInstance().waitForDone(3000)
        for _ in range(50):
            qapp.processEvents()
            if "(到達不可)" in sb._items_by_id[ng_fav.id].text(0):
                break
        assert "(到達不可)" in sb._items_by_id[ng_fav.id].text(0)
        assert "(到達不可)" not in sb._items_by_id[ok_fav.id].text(0)

    def test_reachable_again_clears_mark(self, qapp, tmp_path):
        """接続が回復（到達可能化）したら (到達不可) が消える。"""
        from PySide6.QtCore import QThreadPool
        from app.models.favorite import FavoriteStore
        from app.gui.favorites_sidebar import FavoritesSidebar
        store = FavoriteStore(tmp_path / "f.json")
        target = tmp_path / "srv"  # 最初は存在しない＝到達不可
        fav = store.add("srv", str(target))
        sb = FavoritesSidebar(store)
        QThreadPool.globalInstance().waitForDone(3000)
        for _ in range(50):
            qapp.processEvents()
            if "(到達不可)" in sb._items_by_id[fav.id].text(0):
                break
        assert "(到達不可)" in sb._items_by_id[fav.id].text(0)
        # 接続回復に相当：パスを作成 → 再チェック
        target.mkdir()
        sb._recheck_reachability()
        QThreadPool.globalInstance().waitForDone(3000)
        for _ in range(50):
            qapp.processEvents()
            if "(到達不可)" not in sb._items_by_id[fav.id].text(0):
                break
        assert "(到達不可)" not in sb._items_by_id[fav.id].text(0)


class TestCombinedContextMenu:
    def test_build_combined_items(self, qapp, tmp_path, monkeypatch):
        """統合メニューに足す Fibro 項目は「お気に入りに追加」のみ。

        対象はフォルダ・ファイルどちらも（18c4169 でフォルダ限定から拡張）。
        登録先は選択の先頭。
        """
        from app.i18n import _
        win = _make_window(tmp_path, monkeypatch)
        f = tmp_path / "a.txt"
        f.write_text("x")
        d = tmp_path / "sub"
        d.mkdir()
        for target in (f, d):
            items, callables = win._build_combined_items([str(target)])
            actions = [n for n in items if n.get("type") == "action"]
            assert {n["key"] for n in actions} == {"fav"}
            assert [n["label"] for n in actions] == [_("ctx_add_fav")]
            # 登録先が「選択の先頭」であることまで見る（キーの有無だけだと
            # 対象を取り違えても気づけない）
            registered = []
            monkeypatch.setattr(win.favorites, "add_favorite",
                                lambda p: registered.append(p))
            callables["fav"]()
            assert registered == [str(target)]
        # 選択なし: Fibro 項目なし
        items, callables = win._build_combined_items([])
        assert items == [] and callables == {}

    def test_combined_key_runs_action(self, qapp, tmp_path, monkeypatch):
        """show_combined_menu が key を返したら対応アクションが呼ばれる。"""
        from PySide6.QtCore import QPoint
        import app.shell_menu as sm
        monkeypatch.setattr(sm, "is_supported", lambda: True)
        # 統合メニューは 'fav' を選んだ体で (True, 'fav') を返す
        monkeypatch.setattr(sm, "show_combined_menu",
                            lambda *a, **k: (True, "fav"))
        win = _make_window(tmp_path, monkeypatch)
        d = tmp_path / "sub"
        d.mkdir()
        monkeypatch.setattr(win, "selected_paths", lambda: [str(d)])
        fired = []
        monkeypatch.setattr(win.favorites, "add_favorite",
                            lambda p: fired.append(p))
        win._show_context_menu(QPoint(5, 5))
        assert fired == [str(d)]  # favorites.add_favorite が呼ばれた

    def test_combined_fallback_to_fibro_menu(self, qapp, tmp_path, monkeypatch):
        """show_combined_menu 失敗(False) → 従来 Fibro QMenu にフォールバック。"""
        from PySide6.QtCore import QPoint
        import app.shell_menu as sm
        import app.gui.main_window as mw
        from PySide6.QtWidgets import QMenu
        monkeypatch.setattr(sm, "is_supported", lambda: True)
        monkeypatch.setattr(sm, "show_combined_menu",
                            lambda *a, **k: (False, None))
        win = _make_window(tmp_path, monkeypatch)
        monkeypatch.setattr(win, "selected_paths", lambda: [r"C:\dummy.txt"])
        built = []
        orig = win._build_fibro_menu

        class _NoExecMenu(QMenu):
            def exec(self, *a):
                return None
        monkeypatch.setattr(mw, "QMenu", _NoExecMenu)
        monkeypatch.setattr(
            win, "_build_fibro_menu",
            lambda paths: built.append(True) or orig(paths))
        win._show_context_menu(QPoint(5, 5))
        assert built  # フォールバックで Fibro メニューを構築した


class TestToolbarRemovalAndShortcuts:
    def test_no_toolbar(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QToolBar
        win = _make_window(tmp_path, monkeypatch)
        assert len(win.findChildren(QToolBar)) == 0
        # 旧ナビボタンも無い
        assert not hasattr(win, "back_btn")
        assert not hasattr(win, "fwd_btn")
        assert not hasattr(win, "up_btn")

    def test_nav_shortcuts_present(self, qapp, tmp_path, monkeypatch):
        win = _make_window(tmp_path, monkeypatch)
        seqs = {a.shortcut().toString() for a in win.actions()}
        assert {"Alt+Left", "Alt+Right", "Alt+Up"} <= seqs

    def test_shortcuts_button_present(self, qapp, tmp_path, monkeypatch):
        """パンくず行右端にショートカット一覧ボタンがある。"""
        win = _make_window(tmp_path, monkeypatch)
        assert hasattr(win, "shortcuts_btn")
        assert not win.shortcuts_btn.icon().isNull()

    def test_show_shortcuts_popup(self, qapp, tmp_path, monkeypatch):
        """_show_shortcuts がショートカット一覧ダイアログを生成する。"""
        from PySide6.QtWidgets import QDialog, QLabel
        win = _make_window(tmp_path, monkeypatch)
        win._show_shortcuts()
        dialogs = win.findChildren(QDialog)
        popup = next(d for d in dialogs
                     if d.windowTitle() == "ショートカット一覧")
        texts = {lbl.text() for lbl in popup.findChildren(QLabel)}
        assert "ファイル操作" in texts
        assert "Ctrl+C" in texts
        popup.close()

    def test_theme_toggle_in_settings_menu(self, qapp, tmp_path, monkeypatch):
        """設定（歯車）にテーマ選択サブメニューがある。

        ff2ed7e でメニューバーは全廃され、設定はトップバーの歯車ボタンへ
        移った。実体はトグルではなく全テーマの排他チェック式ピッカーだが、
        受け入れ確認の対応を崩さないためテスト名は据え置く。
        """
        from app.gui.theme import THEME_META, THEME_ORDER
        from app.i18n import _
        win = _make_window(tmp_path, monkeypatch)
        assert hasattr(win, "settings_btn")
        menu = _capture_popup(monkeypatch, win._show_settings_menu)

        theme_action = next(
            (a for a in menu.actions() if a.text() == _("menu_theme")), None)
        assert theme_action is not None, \
            f"テーマ項目が無い: {[a.text() for a in menu.actions()]}"
        submenu = theme_action.menu()
        assert submenu is not None, "テーマがサブメニューになっていない"

        # 全テーマが THEME_ORDER の順で並ぶ
        lang = win.theme_manager.get("language", "ja")
        label_key = "label_ja" if lang == "ja" else "label_en"
        expected = [THEME_META[k][label_key] for k in THEME_ORDER]
        assert [a.text() for a in submenu.actions()] == expected

        # 現在テーマだけが排他チェックされている
        current = win.project_settings.get("theme", "light")
        checked = [a.text() for a in submenu.actions() if a.isChecked()]
        assert checked == [THEME_META[current][label_key]]
        assert all(a.isCheckable() for a in submenu.actions())

    def test_help_menu_present(self, qapp, tmp_path, monkeypatch):
        """ヘルプメニューにバグ報告と About がある。

        ff2ed7e 以降、メニューバーではなくトップバーの ? ボタンから出る。
        """
        from app.i18n import _
        win = _make_window(tmp_path, monkeypatch)
        assert hasattr(win, "help_btn")
        menu = _capture_popup(monkeypatch, win._show_help_menu)
        # ラベル直書きだと i18n を変えた時にテストだけ生き残るため、
        # プロダクションと同じキーから引いて突き合わせる。
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert labels == [_("menu_report_bug"), _("menu_about")]

    def test_issues_url_and_version(self, qapp, tmp_path, monkeypatch):
        """バグ報告 URL とバージョン定数が想定どおり。"""
        from app import __version__
        win = _make_window(tmp_path, monkeypatch)
        assert win._ISSUES_URL == \
            "https://github.com/tjxhq592-ship-it/release/issues"
        assert __version__

    def test_no_scroll_button_gap(self, qapp, tmp_path, monkeypatch):
        """タブと「＋」の間にスクロールボタン予約の隙間が無いこと。"""
        win = _make_window(tmp_path, monkeypatch)
        win.show()
        for _ in range(20):
            qapp.processEvents()
        tb = win.tab_bar
        assert tb.usesScrollButtons() is False
        # タブバー幅 = タブ実幅（余白なし）→ ＋ がタブ直後に並ぶ
        assert tb.width() == tb.tabRect(0).width()


# ---- 20. ドラッグ範囲選択（ラバーバンド） ----
class TestRubberBandSelection:
    def test_icon_view_extended_selection(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QAbstractItemView
        a = tmp_path / "rb"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        iv = win._active_pane.icon_view
        assert iv.selectionMode() == \
            QAbstractItemView.SelectionMode.ExtendedSelection
        assert iv.isSelectionRectVisible()

    def test_table_extended_selection(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QAbstractItemView
        a = tmp_path / "rb2"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        assert win._active_pane.table.selectionMode() == \
            QAbstractItemView.SelectionMode.ExtendedSelection

    def _press(self, view, point):
        """合成左クリック press を view に送ってドラッグ可否判定を駆動。"""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(point), QPointF(point),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        view.mousePressEvent(ev)

    def test_press_empty_area_disables_drag_for_marquee(
            self, qapp, tmp_path, monkeypatch):
        """空白を押すとドラッグ無効化＝ラバーバンド選択を開始できる。"""
        from PySide6.QtCore import QPoint
        d = tmp_path / "rb3"
        d.mkdir()
        (d / "x.txt").write_text("x")
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(d))
        for _ in range(50):
            qapp.processEvents()
        table = win._active_pane.table
        table.resize(400, 400)
        # ずっと下の空白領域を押す → 未選択/空白なのでドラッグ無効
        self._press(table, QPoint(50, 380))
        assert table.dragEnabled() is False

    def test_press_selected_item_keeps_drag(
            self, qapp, tmp_path, monkeypatch):
        """選択済みアイテム上を押すとドラッグ移動が許可される。"""
        d = tmp_path / "rb4"
        d.mkdir()
        (d / "y.txt").write_text("y")
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(d))
        for _ in range(50):
            qapp.processEvents()
        table = win._active_pane.table
        table.resize(400, 400)
        idx = table.model().index(0, 0, table.rootIndex())
        table.selectionModel().select(
            idx, table.selectionModel().SelectionFlag.Select
            | table.selectionModel().SelectionFlag.Rows)
        center = table.visualRect(idx).center()
        self._press(table, center)
        assert table.dragEnabled() is True

    def test_press_unselected_item_grabs_no_marquee(
            self, qapp, tmp_path, monkeypatch):
        """未選択アイテムの上から押しても、対象をつかめる（範囲選択を始めない）。"""
        d = tmp_path / "rb6"
        d.mkdir()
        (d / "w.txt").write_text("w")
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(d))
        for _ in range(50):
            qapp.processEvents()
        table = win._active_pane.table
        table.resize(400, 400)
        idx = table.model().index(0, 0, table.rootIndex())
        # 選択しないままアイテム上を押す → ドラッグ有効・マーキー開始点なし
        center = table.visualRect(idx).center()
        self._press(table, center)
        assert table.dragEnabled() is True
        assert table._marquee_origin is None

    def _move(self, view, point, buttons):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        ev = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(point), QPointF(point),
            Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier)
        view.mouseMoveEvent(ev)

    def test_table_draws_marquee_on_empty_drag(
            self, qapp, tmp_path, monkeypatch):
        """空白からドラッグすると詳細ビューが自前マーキー矩形を表示する。"""
        from PySide6.QtCore import QPoint, Qt
        d = tmp_path / "rb5"
        d.mkdir()
        (d / "z.txt").write_text("z")
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(d))
        for _ in range(50):
            qapp.processEvents()
        table = win._active_pane.table
        assert table._draw_marquee is True
        table.resize(400, 400)
        # 空白を押して開始点を記録 → 大きく動かすとマーキーが出る
        self._press(table, QPoint(60, 360))
        assert table._marquee_origin is not None
        # 下から上へドラッグして先頭の行（z.txt）を帯で覆う
        self._move(table, QPoint(260, 4), Qt.MouseButton.LeftButton)
        # トップレベル未表示の headless では isVisible は False のため、
        # マーキーが生成され矩形に追従していること（geometry が非空）で検証する。
        assert table._marquee is not None
        assert not table._marquee.geometry().isEmpty()
        # 帯に触れた行が選択されていること（自前の touch 選択）
        assert len(table.selectionModel().selectedRows(0)) >= 1


# ---- Win+E リモート起動（タブ追加・サイズ維持） ----
class TestRemoteOpen:
    def test_adds_tab_for_each_dir(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "ra"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        before = win.tab_bar.count()
        win.handle_remote_open([str(a)])
        assert win.tab_bar.count() == before + 1

    def test_preserves_window_size(self, qapp, tmp_path, monkeypatch):
        """Win+E のタブ追加で現在のウィンドウサイズを初期サイズに戻さないこと。

        オフスクリーンでは仮想スクリーン幅の影響で resize 値が揺れるため、
        「初期サイズ(1100x700)へリセットされない／縮まない」ことを検証する
        （実機/実プロセスでは現在サイズがそのまま維持されることを確認済み）。
        """
        a = tmp_path / "rsz"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.show()
        win.resize(1280, 860)
        for _ in range(10):
            qapp.processEvents()
        before = (win.width(), win.height())
        win.handle_remote_open([str(a)])
        for _ in range(10):
            qapp.processEvents()
        # showNormal を使っていれば (1100,700) へ縮む。縮んでいないことを確認。
        assert (win.width(), win.height()) != (1100, 700)
        assert win.width() >= before[0] and win.height() >= before[1]


# ---- デュアルペイン ----
class TestDualPane:
    def test_toggle_shows_secondary(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "d1"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.toggle_dual_pane()
        assert win._dual
        assert not win.secondary_pane.isHidden()

    def test_dual_border_no_stylesheet(self, qapp, tmp_path, monkeypatch):
        """デュアルの枠線は paintEvent 描画で、スタイルシートを一切使わない。"""
        a = tmp_path / "dt"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.toggle_dual_pane()
        # スタイルシートは使わない（テーブルもペインも空＝ダークパレット維持）
        assert win._current_primary().table.styleSheet() == ""
        assert win._current_primary().styleSheet() == ""
        assert win.secondary_pane.styleSheet() == ""
        # アクティブ枠の状態は内部フラグで保持
        assert win._active_pane._active_border is True
        other = (win.secondary_pane if win._active_pane is not win.secondary_pane
                 else win._current_primary())
        assert other._active_border is False
        # 解除で枠なし
        win.toggle_dual_pane()
        assert win.secondary_pane._active_border is None

    def test_f6_switches_active_pane(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "d2"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.toggle_dual_pane()
        primary = win._current_primary()
        win.toggle_active_pane()
        assert win._active_pane is win.secondary_pane
        win.toggle_active_pane()
        assert win._active_pane is primary

    def test_operations_target_active_pane(self, qapp, tmp_path, monkeypatch):
        # サブペインをアクティブにすると current_path がサブのものになる
        a, b = tmp_path / "src", tmp_path / "dst"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.toggle_dual_pane()
        win.toggle_active_pane()  # secondary active
        win.navigate(str(b))
        assert win.current_path == str(b)
        assert win._current_primary().current_path == str(a)  # 主は不変

    def test_toggle_off_hides(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "d3"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.toggle_dual_pane()
        win.toggle_dual_pane()
        assert not win._dual
        assert win.secondary_pane.isHidden()


# ---- クイックプレビュー ----
class TestQuickPreview:
    def test_quick_preview_no_selection_noop(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "qp"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.quick_preview()  # 選択なし → 例外なく何もしない

    def test_preview_dialog_builds_for_text(self, qapp, tmp_path):
        from app.gui.preview_dialog import QuickPreviewDialog
        p = tmp_path / "x.txt"
        p.write_text("content", encoding="utf-8")
        dlg = QuickPreviewDialog(str(p))
        assert dlg.windowTitle().endswith("x.txt")


# ---- Phase 2.1 バグ修正（B1: ネットワークパス判定） ----
class TestNetworkPathDetection:
    def test_unc_paths_are_network(self):
        from app.gui.main_window import _is_network_path
        assert _is_network_path("\\\\server\\share") is True
        assert _is_network_path("//server/share") is True

    def test_local_and_relative_are_not_network(self, tmp_path):
        """固定ドライブ・相対・空パスはネットワーク扱いにしない（同期 is_dir へ）。"""
        from app.gui.main_window import _is_network_path
        assert _is_network_path(str(tmp_path)) is False   # 固定ドライブ配下
        assert _is_network_path("subdir/file") is False
        assert _is_network_path("") is False

    def test_unassigned_drive_letter_not_network(self):
        """未割り当てドライブレターは DRIVE_REMOTE でない → ネットワーク扱いしない。"""
        from app.gui.main_window import _is_network_path
        # Q: は通常未割り当て（DRIVE_NO_ROOT_DIR=1）。非 Windows でも UNC でない→False。
        assert _is_network_path("Q:\\foo") is False

    def test_detection_is_non_blocking(self):
        """判定はブロックしない（1000 回でも数 ms）。ネットワーク I/O を伴わない。"""
        import time
        from app.gui.main_window import _is_network_path
        paths = ["\\\\srv\\s", "C:\\Windows", "Q:\\x", "rel/p", ""]
        t0 = time.perf_counter()
        for _ in range(1000):
            for p in paths:
                _is_network_path(p)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5  # 実 I/O があれば秒単位に膨れる

    # ---- W4: GetDriveType をモックした種別ごとの回帰 ----
    # 既存の 4 件は実機のドライブ構成に依存しており、DRIVE_REMOTE の分岐
    # （＝B1 で足した本体）を一度も通っていない。種別を固定して押さえる。

    @pytest.mark.parametrize("drive_type,label,expected", [
        (4, "DRIVE_REMOTE", True),
        (3, "DRIVE_FIXED", False),
        (1, "DRIVE_NO_ROOT_DIR", False),
        (2, "DRIVE_REMOVABLE", False),
        (5, "DRIVE_CDROM", False),
        (0, "DRIVE_UNKNOWN", False),
    ])
    def test_drive_type_decides_network(self, monkeypatch, drive_type, label,
                                        expected):
        """割り当てドライブは DRIVE_REMOTE のときだけネットワーク扱い。"""
        import app.gui.main_window as mw
        monkeypatch.setattr(mw, "_drive_type", lambda root: drive_type)
        assert mw._is_network_path("Z:\\folder") is expected, label
        assert mw._is_network_path("Z:") is expected, label

    def test_drive_type_is_queried_with_drive_root(self, monkeypatch):
        """GetDriveTypeW にはドライブルート（"Z:\\"）を渡す。

        パス全体を渡すと種別を取り違える（存在しないパスで DRIVE_NO_ROOT_DIR）。
        """
        import app.gui.main_window as mw
        seen = []
        monkeypatch.setattr(mw, "_drive_type",
                            lambda root: (seen.append(root), 4)[1])
        assert mw._is_network_path("Z:\\a\\b\\c.txt") is True
        assert seen == ["Z:\\"]

    def test_unc_short_circuits_before_drive_type(self, monkeypatch):
        """UNC は GetDriveType を呼ばずに True（splitdrive に UNC を渡さない）。"""
        import app.gui.main_window as mw

        def _boom(root):
            raise AssertionError(f"UNC で _drive_type が呼ばれた: {root!r}")

        monkeypatch.setattr(mw, "_drive_type", _boom)
        assert mw._is_network_path("\\\\server\\share\\x") is True
        assert mw._is_network_path("//server/share/x") is True

    def test_drive_type_wraps_getdrivetypew(self, monkeypatch):
        """_drive_type は GetDriveTypeW の戻り値をそのまま int で返す。"""
        import sys
        import app.gui.main_window as mw
        if sys.platform != "win32":
            pytest.skip("GetDriveTypeW は Windows 専用（CI は windows-latest）")
        import ctypes
        calls = []
        monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW",
                            lambda root: (calls.append(root), 4)[1])
        mw._drive_type.cache_clear()
        try:
            assert mw._drive_type("Z:\\") == 4
            assert calls == ["Z:\\"]
            # lru_cache: 2 回目は OS を叩かない
            assert mw._drive_type("Z:\\") == 4
            assert len(calls) == 1
        finally:
            mw._drive_type.cache_clear()

    def test_non_windows_falls_back_to_unc_only(self, monkeypatch):
        """非 Windows では windll が無い。UNC 判定だけに落ち、例外を出さない。"""
        import app.gui.main_window as mw
        monkeypatch.setattr(mw.sys, "platform", "linux")
        mw._drive_type.cache_clear()
        try:
            assert mw._drive_type("Z:\\") == 0
            assert mw._is_network_path("Z:\\x") is False
            assert mw._is_network_path("\\\\srv\\s") is True
        finally:
            mw._drive_type.cache_clear()


# ---- Phase 2.1 バグ修正（B2: 読み込みバー残存） ----
class TestLoadingBarResidue:
    def test_mismatch_completion_clears_bar_and_keeps_p1(
            self, qapp, tmp_path, monkeypatch):
        """current と不一致の古いロード完了でもバーは必ず消す（B2）。

        かつ不一致では finish_population を呼ばず、進行中 population の動的ソートを
        復活させない（P1 の逐次ソート回避を維持）。
        """
        a, b = tmp_path / "m_a", tmp_path / "m_b"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        pane = win._active_pane
        # population 中（動的ソート停止）＆バー表示中の状態を作る
        pane.begin_population()
        assert pane.proxy.dynamicSortFilter() is False
        pane.show_loading_bar()
        pane._loading_timer.start()
        # 別ドライブ相当の古いパスの完了通知（b != current a）
        win._on_directory_loaded(pane, str(b))
        assert pane._loading_timer.isActive() is False   # バーのタイマー停止
        assert pane.progress_bar.maximum() == 1           # hide 済み（非インジターミネート）
        assert pane.proxy.dynamicSortFilter() is False    # finish 未実行→P1 維持

    def test_match_completion_finishes_population(
            self, qapp, tmp_path, monkeypatch):
        """一致 root の完了では一括ソートして動的ソートを再開する。"""
        a = tmp_path / "m_c"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        pane = win._active_pane
        pane.begin_population()
        pane.show_loading_bar()
        pane._loading_timer.start()
        win._on_directory_loaded(pane, pane.current_path)
        assert pane._loading_timer.isActive() is False   # バー消し
        assert pane.proxy.dynamicSortFilter() is True     # 一括ソート後に再開


# ---- Phase 2.1 バグ修正（B3: ネットワークタブ検証は最大1回） ----
class TestNetworkTabValidationOnce:
    def test_validation_runs_at_most_once(self, qapp, tmp_path, monkeypatch):
        """ネットワーク保存タブを選択→検証中に別タブ→戻る、を繰り返しても検証は1回。

        検証済みの確定/フォールバック値は pending へ (path, True) で戻り、
        再選択時は再検証せず直接 navigate される。
        """
        import app.gui.main_window as mw

        a, b = tmp_path / "na", tmp_path / "nb"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))     # tab0 = A（アクティブ）
        win.new_tab(str(b))      # tab1 = B（アクティブ）

        # _PathValidateJob を非同期スタブ化：構築＝検証開始を記録、run は何もしない
        starts: list[str] = []
        holder: dict = {}

        # 本物と同じ土台（停止要求に応じられる）を使う。JobTracker が
        # request_stop / finished を触るため QRunnable 直下では足りない。
        class FakeJob(mw.StoppableJob):
            def __init__(self, path, pane, emit):
                super().__init__(emit)
                starts.append(path)
                holder["pane"] = pane
                holder["path"] = path
                holder["emit"] = emit

            def run(self):  # 実 is_dir を打たない（切断ブロック回避）
                self.finished = True

        monkeypatch.setattr(mw, "_PathValidateJob", FakeJob)

        unc = "\\\\server\\share"
        win.tab_bar.setCurrentIndex(0)          # A をアクティブに戻す
        win._pending_paths[1] = (unc, False)    # B を未検証ネットワーク pending に

        # 1回目の選択 → ネットワーク＆未検証なので検証開始（1回）
        win.tab_bar.setCurrentIndex(1)
        assert starts == [unc]

        # 検証完了前に別タブへ → B は非アクティブ
        win.tab_bar.setCurrentIndex(0)
        # 検証完了（無効→フォールバック確定）。非アクティブなので pending に (確定, True)
        holder["emit"](holder["pane"], holder["path"], False)
        assert win._pending_paths[1] == (win._default_dir(), True)

        # 再選択 → 検証済みなので再検証せず直接 navigate（検証回数は増えない）
        win.tab_bar.setCurrentIndex(1)
        assert starts == [unc]                  # 依然として1回だけ
        assert win.current_path == win._default_dir()

    def test_close_tab_preserves_validated_marker(
            self, qapp, tmp_path, monkeypatch):
        """タブを閉じて index がズレても検証済みマーカーが正しいタブに追従する。"""
        a, b, c = tmp_path / "ca", tmp_path / "cb", tmp_path / "cc"
        for d in (a, b, c):
            d.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.new_tab(str(b))
        win.new_tab(str(c))          # tab0=A, tab1=B, tab2=C
        win.tab_bar.setCurrentIndex(0)          # A をカレントに固定
        # tab2 を検証済み確定値の pending にする（カレントではないので消費されない）
        win._pending_paths[2] = (str(c), True)
        # 中間の tab1(B) を閉じる → C は index 2→1 に補正され pending は保持される
        win.close_tab(1)
        assert 1 in win._pending_paths
        assert win._pending_paths[1] == (str(c), True)


# ---- Phase 2.1 バグ修正（B4: tree 同期 singleShot 集約） ----
class TestTreeSyncCoalesce:
    def test_consecutive_navigate_coalesces_tree_sync(
            self, qapp, tmp_path, monkeypatch):
        """連続 navigate では tree 同期の保留は最新1本に集約される。"""
        a, b, c = tmp_path / "ta", tmp_path / "tb", tmp_path / "tc"
        for d in (a, b, c):
            d.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.navigate(str(b))
        win.navigate(str(c))
        # 保留は最新パスのみ、スケジュールは1本だけ立っている
        assert win._pending_tree_path == str(c)
        assert win._tree_sync_scheduled is True
        for _ in range(5):
            qapp.processEvents()
        # ハンドラ実行後はフラグが下りる（再スケジュールされていない）
        assert win._tree_sync_scheduled is False
