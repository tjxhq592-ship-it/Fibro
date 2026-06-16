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
        saved = win.theme_manager.get("tabs", [])
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


class TestNativeContextMenu:
    def _prepare(self, win, monkeypatch, called):
        """選択を固定で返し、QMenu がモーダルでブロックしないようにする（決定論的）。"""
        import app.gui.main_window as mw
        from PySide6.QtWidgets import QMenu
        monkeypatch.setattr(win, "selected_paths", lambda: [r"C:\dummy.txt"])
        monkeypatch.setattr(
            win, "_show_shell_menu",
            lambda paths, gpos: called.setdefault("p", paths) or True)

        # 従来メニュー経路に入っても exec() でブロックしないサブクラスに差し替え
        # （C++ メソッドの文字列パッチは効かないためクラス自体を置換）。
        class _NoExecMenu(QMenu):
            def exec(self, *a):  # noqa: A003
                return None
        monkeypatch.setattr(mw, "QMenu", _NoExecMenu)

    def test_native_on_calls_shell_menu(self, qapp, tmp_path, monkeypatch):
        """設定 ON＋選択あり → ネイティブメニュー（_show_shell_menu）が呼ばれる。"""
        from PySide6.QtCore import QPoint
        import app.shell_menu as sm
        monkeypatch.setattr(sm, "is_supported", lambda: True)
        win = _make_window(tmp_path, monkeypatch)
        win.theme_manager.set("native_context_menu", True)
        called = {}
        self._prepare(win, monkeypatch, called)
        win._show_context_menu(QPoint(5, 5))
        assert called.get("p")  # ネイティブ経路に入った

    def test_native_off_uses_fibro_menu(self, qapp, tmp_path, monkeypatch):
        """設定 OFF → ネイティブを呼ばず従来メニュー経路。"""
        from PySide6.QtCore import QPoint
        win = _make_window(tmp_path, monkeypatch)
        win.theme_manager.set("native_context_menu", False)
        called = {}
        self._prepare(win, monkeypatch, called)
        win._show_context_menu(QPoint(5, 5))
        assert "p" not in called

    def test_toggle_persists(self, qapp, tmp_path, monkeypatch):
        win = _make_window(tmp_path, monkeypatch)
        win._toggle_native_menu(False)
        assert win.theme_manager.get("native_context_menu") is False
        win._toggle_native_menu(True)
        assert win.theme_manager.get("native_context_menu") is True


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

    def test_theme_toggle_in_settings_menu(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMenu
        win = _make_window(tmp_path, monkeypatch)
        labels = [a.text() for m in win.menuBar().findChildren(QMenu)
                  for a in m.actions()]
        assert any("テーマ" in t for t in labels)

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
