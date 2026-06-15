"""メインウィンドウ: QFileSystemModel ベースの3ペイン構成。

左=フォルダツリー(QTreeView)、中央=ファイル一覧(QTableView)、右=詳細。
上部にパンくず+パス直接入力。下部にステータス+主要操作。
"""
from __future__ import annotations

import os
import stat as stat_module
import subprocess
from pathlib import Path

from PySide6.QtCore import (
    QDir, QItemSelectionModel, QModelIndex, QRunnable, Qt, QThreadPool, QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileSystemModel, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QSplitter,
    QStackedLayout, QStackedWidget, QStatusBar, QStyle, QTabBar,
    QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from app.engine.file_ops import FileOps
from app.engine.rename_history import RenameExecutor
from app.gui.dnd_views import FolderTreeView
from app.gui.file_pane import FilePane
from app.gui.favorites_sidebar import FavoritesSidebar
from app.gui.preview_dialog import QuickPreviewDialog
from app.gui.properties_dialog import PropertiesDialog
from app.gui.recent_sidebar import RecentSidebar
from app.gui.rename_dialog import RenameDialog
from app.gui.search_panel import SearchPanel
from app.gui.theme import ThemeManager
from app.models.favorite import FavoriteStore
from app.models.recent import RecentStore
from app.models.rename_presets import RenamePresetStore

from app.paths import CONFIG_DIR


def _human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:,.1f} TB"


class BreadcrumbBar(QWidget):
    """パンくずバー。クリックで切替、ダブルクリック相当でパス直接入力。"""

    path_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stack = QStackedLayout(self)

        self._crumb_widget = QWidget()
        self._crumb_layout = QHBoxLayout(self._crumb_widget)
        self._crumb_layout.setContentsMargins(4, 0, 4, 0)
        self._crumb_layout.setSpacing(0)

        self._edit = QLineEdit()
        self._edit.returnPressed.connect(self._commit_edit)
        self._edit.editingFinished.connect(self._show_crumbs)

        self._stack.addWidget(self._crumb_widget)
        self._stack.addWidget(self._edit)
        self._path = ""

    def set_path(self, path: str) -> None:
        self._path = path
        while self._crumb_layout.count():
            item = self._crumb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        parts = Path(path).parts
        accumulated = ""
        for i, part in enumerate(parts):
            accumulated = part if i == 0 else os.path.join(accumulated, part)
            btn = QToolButton(text=part.rstrip("\\") or part)
            btn.setAutoRaise(True)
            btn.clicked.connect(
                lambda checked=False, p=accumulated: self.path_selected.emit(p))
            self._crumb_layout.addWidget(btn)
            if i < len(parts) - 1:
                self._crumb_layout.addWidget(QLabel("›"))
        edit_btn = QToolButton(text="✎")
        edit_btn.setAutoRaise(True)
        edit_btn.setToolTip("パスを直接入力")
        edit_btn.clicked.connect(self._show_edit)
        self._crumb_layout.addWidget(edit_btn)
        self._crumb_layout.addStretch()
        self._stack.setCurrentWidget(self._crumb_widget)

    def _show_edit(self) -> None:
        self._edit.setText(self._path)
        self._stack.setCurrentWidget(self._edit)
        self._edit.setFocus()
        self._edit.selectAll()

    def focus_path_edit(self) -> None:
        """パス直接入力欄にフォーカス（F4 から呼び出し）。"""
        self._show_edit()

    def _show_crumbs(self) -> None:
        self._stack.setCurrentWidget(self._crumb_widget)

    def _commit_edit(self) -> None:
        path = self._edit.text().strip()
        if path and Path(path).is_dir():
            self.path_selected.emit(path)
        self._show_crumbs()


class _SelectionSizeJob(QRunnable):
    """選択ファイルのサイズ合計をバックグラウンドで計算する。

    OneDrive 等のクラウド/低速パスでは stat() が遅く、GUI スレッドで回すと
    固まるため別スレッドで集計し、結果を emit(gen, total) で返す。
    """

    def __init__(self, paths: list[str], gen: int, emit) -> None:
        super().__init__()
        self._paths = paths
        self._gen = gen
        self._emit = emit

    def run(self) -> None:
        total = 0
        for p in self._paths:
            try:
                st = os.stat(p)
                if not stat_module.S_ISDIR(st.st_mode):
                    total += st.st_size
            except OSError:
                pass
        self._emit(self._gen, total)


class _OpenFileJob(QRunnable):
    """ファイルを関連付けアプリで開く（バックグラウンド）。

    os.startfile は通常ノンブロッキングだが、関連付けが壊れている/未導入の
    アプリ（例: VS Code 未インストールなのに .json が VS Code 関連付け）だと
    ShellExecute の解決でブロックし GUI が固まる。別スレッドで実行し、関連付けが
    無ければ「プログラムから開く」へフォールバック、それも失敗なら通知する。
    """

    def __init__(self, path: str, on_fail) -> None:
        super().__init__()
        self._path = path
        self._on_fail = on_fail

    def run(self) -> None:
        try:
            os.startfile(self._path)  # noqa: S606 — ユーザー操作による「開く」
        except OSError:
            try:
                os.startfile(self._path, "openas")  # 「プログラムから開く」
            except OSError as e:
                self._on_fail(self._path, str(e))


class MainWindow(QMainWindow):
    # 選択サイズ合計の非同期計算結果（gen, 合計バイト）
    _size_computed = Signal(int, int)
    # ファイルを開くのに失敗（path, エラー文）
    _open_failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fibro — ファイルエクスプローラー")
        self.resize(1100, 700)
        self._sel_gen = 0            # 選択世代（古い計算結果を破棄）
        self._sel_count = 0          # 直近の選択件数
        self._size_computed.connect(self._apply_selection_size)
        self._open_failed.connect(self._on_open_failed)

        self.rename_executor = RenameExecutor()
        self.file_ops = FileOps()
        self.favorite_store = FavoriteStore(CONFIG_DIR / "favorites.json")
        self.recent_store = RecentStore(CONFIG_DIR / "recent.json")
        self.preset_store = RenamePresetStore(CONFIG_DIR / "rename_presets.json")
        self.theme_manager = ThemeManager(CONFIG_DIR / "settings.json")
        self._clipboard: tuple[str, list[str]] | None = None  # ("copy"|"cut", paths)

        # --- ペイン（タブ＝主ペイン群 + デュアル用サブペイン） ---
        self._tabs: list[FilePane] = []        # タブ順に並ぶ主ペイン
        self._active_pane: FilePane | None = None
        self._dual = False

        # ツリーは共有（左サイドバー）
        self.tree_model = QFileSystemModel(self)
        self.tree_model.setFilter(
            QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        # カスタムフォルダアイコンのシェル問い合わせを無効化（OneDrive 対策）
        self.tree_model.setOption(
            QFileSystemModel.Option.DontUseCustomDirectoryIcons, True)
        self.tree_model.setRootPath("")

        self._build_ui()
        self._build_actions()
        self._restore_tabs()
        # Ctrl+Tab / Ctrl+Shift+Tab はフォーカス移動に横取りされ QAction まで
        # 届かないため、アプリ全体のイベントフィルタで先に捕捉する。
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API
        """Ctrl+Tab / Ctrl+Shift+Tab をタブ切替に割り当てる。"""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            mods = event.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                key = event.key()
                # Shift+Tab は Key_Backtab として届く
                if key == Qt.Key.Key_Backtab or (
                        key == Qt.Key.Key_Tab
                        and mods & Qt.KeyboardModifier.ShiftModifier):
                    self.prev_tab()
                    return True
                if key == Qt.Key.Key_Tab:
                    self.next_tab()
                    return True
        return super().eventFilter(obj, event)

    # ---- アクティブペインへの委譲（既存コードを無修正で活かす） ----
    @property
    def table(self):
        return self._active_pane.table

    @property
    def proxy(self):
        return self._active_pane.proxy

    @property
    def list_model(self):
        return self._active_pane.list_model

    @property
    def _history(self) -> list[str]:
        return self._active_pane.history

    @_history.setter
    def _history(self, value: list[str]) -> None:
        self._active_pane.history = value

    @property
    def _history_pos(self) -> int:
        return self._active_pane.history_pos

    @_history_pos.setter
    def _history_pos(self, value: int) -> None:
        self._active_pane.history_pos = value

    # ---- UI ----
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # 上部バー: パンくず（戻る/進む/上は Alt+←/→/↑ のショートカットで）
        top = QHBoxLayout()
        self.breadcrumb = BreadcrumbBar()
        self.breadcrumb.path_selected.connect(self.navigate)
        top.addWidget(self.breadcrumb, stretch=1)
        root.addLayout(top)

        # フィルタボックス（パスバー下）
        filter_row = QHBoxLayout()
        filter_label = QLabel("フィルタ:")
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("カレントフォルダ内を絞り込み…")
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_debounce = QTimer(self, singleShot=True, interval=200)
        self._filter_debounce.timeout.connect(self._apply_filter)
        self.filter_edit.textChanged.connect(self._filter_debounce.start)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.filter_edit, stretch=1)
        root.addLayout(filter_row)

        # 3ペイン
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = FolderTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.files_dropped.connect(self._on_files_dropped)
        self.tree.mouse_nav.connect(self._on_mouse_nav)
        for col in range(1, self.tree_model.columnCount()):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self._on_tree_clicked)

        # 中央右側: タブバー + ペインスプリッタ（主スタック + サブペイン）
        self.tab_bar = QTabBar()
        self.tab_bar.setMovable(True)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setExpanding(False)
        # スクロールボタン用の予約領域を無くし、タブと「＋」の間の隙間を解消
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)

        # 21: タブの右に「＋」新規タブボタン
        self.new_tab_btn = QToolButton()
        self.new_tab_btn.setText("＋")
        self.new_tab_btn.setAutoRaise(True)
        self.new_tab_btn.setToolTip("新しいタブ (Ctrl+T)")
        self.new_tab_btn.clicked.connect(lambda: self.new_tab())
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(0)
        tab_row.addWidget(self.tab_bar)
        tab_row.addWidget(self.new_tab_btn)
        tab_row.addStretch(1)

        self.primary_stack = QStackedWidget()  # 各タブ = 1 FilePane
        self.secondary_pane = self._make_pane()
        self.secondary_pane.hide()

        self.pane_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pane_splitter.addWidget(self.primary_stack)
        self.pane_splitter.addWidget(self.secondary_pane)

        right_area = QWidget()
        right_layout = QVBoxLayout(right_area)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addLayout(tab_row)
        right_layout.addWidget(self.pane_splitter, stretch=1)

        # 左ペイン: お気に入い（上）+ 履歴（中）+ フォルダツリー（下）
        self.favorites = FavoritesSidebar(self.favorite_store)
        self.favorites.path_selected.connect(self.navigate)
        self.recent_sidebar = RecentSidebar(self.recent_store)
        self.recent_sidebar.path_selected.connect(self.navigate)
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.favorites)
        left.addWidget(self.recent_sidebar)
        left.addWidget(self.tree)
        left.setSizes([160, 160, 320])

        splitter.addWidget(left)
        splitter.addWidget(right_area)
        splitter.setSizes([220, 860])
        root.addWidget(splitter, stretch=1)
        self._main_splitter = splitter
        self._left_splitter = left
        self._restore_layout()

        # 検索ドック（Ctrl+F でトグル）
        # settings は ThemeManager を共有（単一ライターにして上書き消失を防ぐ）
        self.search_panel = SearchPanel(settings=self.theme_manager)
        self.search_panel.file_selected.connect(self._on_search_file_selected)
        self.search_dock = QDockWidget("検索", self)
        self.search_dock.setWidget(self.search_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                           self.search_dock)
        self.search_dock.hide()

        # 下部: ステータス（選択情報 + ドライブ空き容量）
        bottom = QHBoxLayout()
        self.selection_label = QLabel("選択: 0件")
        self.disk_label = QLabel("")
        bottom.addWidget(self.selection_label, stretch=1)
        bottom.addWidget(self.disk_label)
        root.addLayout(bottom)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

    # ---- ペイン / タブ / デュアル ----
    def _make_pane(self) -> FilePane:
        """FilePane を生成し、テーブルのシグナルを MainWindow へ接続する。"""
        pane = FilePane()
        for view in (pane.table, pane.icon_view):
            view.files_dropped.connect(self._on_files_dropped)
            view.mouse_nav.connect(self._on_mouse_nav)
            view.doubleClicked.connect(self._on_table_double_clicked)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(self._show_context_menu)
            view.preview_requested.connect(self.quick_preview)
        pane.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        pane.set_view_mode(self.theme_manager.get("view_mode", "details"))
        cols = self.theme_manager.get("columns")
        if isinstance(cols, str) and cols:
            from PySide6.QtCore import QByteArray
            pane.header.restoreState(QByteArray.fromBase64(cols.encode()))
        pane.activated.connect(self._set_active_pane)
        pane.list_model.directoryLoaded.connect(
            lambda p, pn=pane: self._on_directory_loaded(pn, p))
        return pane

    def _on_directory_loaded(self, pane: FilePane, path: str) -> None:
        """QFileSystemModel が非同期に読み込んだ件数をステータスへ（巨大フォルダ確認）。"""
        if pane is not self._active_pane:
            return
        if str(Path(path)) != pane.current_path:
            return
        count = pane.list_model.rowCount(pane.list_model.index(path))
        self.statusBar().showMessage(f"{count:,} 項目", 4000)

    def _default_dir(self) -> str:
        initial = self.theme_manager.get("initial_dir", str(Path.home()))
        return initial if Path(initial).is_dir() else str(Path.home())

    def _tab_title(self, path: str) -> str:
        return Path(path).name or path

    def _current_primary(self) -> FilePane:
        idx = self.tab_bar.currentIndex()
        if 0 <= idx < len(self._tabs):
            return self._tabs[idx]
        return self._tabs[0]

    def _set_active_pane(self, pane: FilePane) -> None:
        if pane is None or pane is self._active_pane:
            return
        self._active_pane = pane
        self._sync_active_pane()

    def _sync_active_pane(self) -> None:
        """共有 UI（パンくず/フィルタ/ツリー/検索/ステータス）をアクティブペインに揃える。"""
        pane = self._active_pane
        path = pane.current_path
        self.breadcrumb.set_path(path)
        self.filter_edit.blockSignals(True)
        self.filter_edit.setText(pane.filter_text)
        self.filter_edit.blockSignals(False)
        self.search_panel.set_root(path)
        if path:
            self.tree.setCurrentIndex(self.tree_model.index(path))
        sel = pane.table.selectionModel()
        if sel:
            sel.selectionChanged.connect(
                self._update_selection_status, Qt.ConnectionType.UniqueConnection)
        self._update_selection_status()
        self._update_disk_usage(path)
        self._update_pane_borders()

    def _update_pane_borders(self) -> None:
        """デュアル時にアクティブペインを枠線で示す。"""
        if not self._dual:
            self.secondary_pane.table.setStyleSheet("")
            self._current_primary().table.setStyleSheet("")
            return
        for pane in (self._current_primary(), self.secondary_pane):
            active = pane is self._active_pane
            pane.table.setStyleSheet(
                "QTableView { border: 2px solid #3d7eff; }" if active
                else "QTableView { border: 1px solid palette(mid); }")

    def new_tab(self, path: str | None = None) -> FilePane:
        target = path or (self._active_pane.current_path
                          if self._active_pane else self._default_dir())
        if not Path(target).is_dir():
            target = self._default_dir()
        pane = self._make_pane()
        self._tabs.append(pane)
        self.primary_stack.addWidget(pane)
        idx = self.tab_bar.addTab(self._tab_title(target))
        self.tab_bar.setCurrentIndex(idx)  # → _on_tab_changed でアクティブ化
        self.navigate(target)
        return pane

    def handle_remote_open(self, paths: list[str]) -> None:
        """別プロセス（Win+E/再起動）からの依頼で新規タブを開き最前面化。"""
        targets = []
        for p in paths:
            path = Path(p)
            if path.is_dir():
                targets.append(str(path))
            elif path.exists():
                targets.append(str(path.parent))
        if not targets:
            self.new_tab()
        else:
            for t in targets:
                self.new_tab(t)
        # 最前面化。現在のサイズ・最大化状態は維持し、最小化のときだけ解除する
        # （showNormal() は通常サイズに戻してしまうため使わない）。
        if self.isMinimized():
            self.setWindowState(
                self.windowState() & ~Qt.WindowState.WindowMinimized)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        # 別プロセス（Win+E 等）からの依頼では SetForegroundWindow 制限により
        # 上記だけでは前面化しないため、AttachThreadInput 方式で確実に前面化する。
        from app.foreground import force_foreground
        force_foreground(int(self.winId()))

    def _on_tab_changed(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        pane = self._tabs[index]
        self.primary_stack.setCurrentWidget(pane)
        self._set_active_pane(pane)

    def close_tab(self, index: int) -> None:
        if len(self._tabs) <= 1 or not (0 <= index < len(self._tabs)):
            return
        pane = self._tabs.pop(index)
        self.primary_stack.removeWidget(pane)
        self.tab_bar.removeTab(index)  # currentChanged → _on_tab_changed
        pane.deleteLater()
        # サブがアクティブだった場合に備えて主へ寄せる
        if self._active_pane is pane:
            self._set_active_pane(self._current_primary())

    def close_current_tab(self) -> None:
        self.close_tab(self.tab_bar.currentIndex())

    def next_tab(self) -> None:
        n = self.tab_bar.count()
        if n > 1:
            self.tab_bar.setCurrentIndex((self.tab_bar.currentIndex() + 1) % n)

    def prev_tab(self) -> None:
        n = self.tab_bar.count()
        if n > 1:
            self.tab_bar.setCurrentIndex((self.tab_bar.currentIndex() - 1) % n)

    def _update_tab_title(self, pane: FilePane) -> None:
        if pane in self._tabs:
            idx = self._tabs.index(pane)
            self.tab_bar.setTabText(idx, self._tab_title(pane.current_path))
            self.tab_bar.setTabToolTip(idx, pane.current_path)

    def toggle_dual_pane(self) -> None:
        self._dual = not self._dual
        if self._dual:
            self.secondary_pane.show()
            self.pane_splitter.setSizes([1, 1])
            primary_path = self._current_primary().current_path
            prev = self._active_pane
            self._set_active_pane(self.secondary_pane)
            if not self.secondary_pane.history:
                self.navigate(primary_path)
            self._set_active_pane(prev)
        else:
            self.secondary_pane.hide()
            self._set_active_pane(self._current_primary())
        self._update_pane_borders()
        self.theme_manager.set("dual_pane", self._dual)

    def toggle_active_pane(self) -> None:
        """F6: 主ペイン⇔サブペインでアクティブを切替（デュアル時のみ）。"""
        if not self._dual:
            return
        primary = self._current_primary()
        target = (primary if self._active_pane is self.secondary_pane
                  else self.secondary_pane)
        self._set_active_pane(target)
        target.table.setFocus()

    def quick_preview(self) -> None:
        """Space: 選択中の先頭ファイルを軽量プレビュー。"""
        paths = self.selected_paths()
        if paths:
            QuickPreviewDialog(paths[0], self).exec()

    def toggle_view_mode(self) -> None:
        """17: 詳細 ⇔ サムネイル表示を全ペインで切替し、設定に保存。"""
        new_mode = ("thumbnails"
                    if self._active_pane.view_mode == "details" else "details")
        for pane in (*self._tabs, self.secondary_pane):
            pane.set_view_mode(new_mode)
        self.theme_manager.set("view_mode", new_mode)
        self.statusBar().showMessage(
            f"表示: {'サムネイル' if new_mode == 'thumbnails' else '詳細'}", 3000)

    def _restore_tabs(self) -> None:
        saved = self.theme_manager.get("tabs", [])
        paths = ([p for p in saved if isinstance(p, str) and Path(p).is_dir()]
                 if isinstance(saved, list) else [])
        if not paths:
            paths = [self._default_dir()]
        for p in paths:
            self.new_tab(p)
        self.tab_bar.setCurrentIndex(0)
        if self.theme_manager.get("dual_pane", False):
            self.toggle_dual_pane()

    def _build_actions(self) -> None:
        def add(text: str, seq: str, slot) -> QAction:
            action = QAction(text, self)
            action.setShortcut(QKeySequence(seq))
            action.triggered.connect(slot)
            self.addAction(action)
            return action

        add("検索", "Ctrl+F", self.toggle_search)
        add("一括リネーム", "Ctrl+H", self.open_rename_dialog)
        add("Undo", "Ctrl+Z", self.undo_last)
        add("削除", "Delete", self.delete_selected)
        add("コピー", "Ctrl+C", lambda: self._set_clipboard("copy"))
        add("切り取り", "Ctrl+X", lambda: self._set_clipboard("cut"))
        add("貼り付け", "Ctrl+V", self.paste_clipboard)
        add("上へ", "Alt+Up", self.go_up)
        add("戻る", "Alt+Left", self.go_back)
        add("進む", "Alt+Right", self.go_forward)
        add("単一リネーム", "F2", self.rename_single)
        add("フィルタへ", "F3", self._focus_filter)
        add("パス入力へ", "F4", self.breadcrumb.focus_path_edit)
        add("更新", "F5", self.refresh)
        add("新規フォルダー", "Ctrl+Shift+N", self.new_folder)
        add("パスをコピー", "Ctrl+Shift+C", self._copy_selected_paths)
        add("すべて選択", "Ctrl+A", self.select_all)
        add("新規タブ", "Ctrl+T", lambda: self.new_tab())
        add("タブを閉じる", "Ctrl+W", self.close_current_tab)
        # Ctrl+Tab / Ctrl+Shift+Tab は eventFilter で処理（QAction だと
        # フォーカス移動に横取りされ発火しないため）
        add("デュアルペイン", "F9", self.toggle_dual_pane)
        add("ペイン切替", "F6", self.toggle_active_pane)
        add("表示モード切替", "Ctrl+Shift+T", self.toggle_view_mode)
        # Space は FileTableView.preview_requested 経由（QAction だと二重発火するため）

        # メニューバー：ファイル・選択・表示・設定
        menubar = self.menuBar()
        file_menu = menubar.addMenu("ファイル")
        new_menu = file_menu.addMenu("新規作成")
        new_menu.addAction("フォルダー\tCtrl+Shift+N", self.new_folder)
        new_menu.addAction("テキストファイル", self.new_text_file)
        file_menu.addSeparator()
        file_menu.addAction("新規タブ\tCtrl+T", lambda: self.new_tab())
        file_menu.addAction("タブを閉じる\tCtrl+W", self.close_current_tab)
        file_menu.addSeparator()
        file_menu.addAction("終了", self.close)
        select_menu = menubar.addMenu("選択")
        select_menu.addAction("すべて選択\tCtrl+A", self.select_all)
        select_menu.addAction("選択を反転", self.invert_selection)
        select_menu.addAction("パターンで選択…", self.select_by_pattern)
        view_menu = menubar.addMenu("表示")
        view_menu.addAction("詳細／サムネイル切替\tCtrl+Shift+T",
                            self.toggle_view_mode)
        view_menu.addSeparator()
        view_menu.addAction("デュアルペイン\tF9", self.toggle_dual_pane)
        view_menu.addAction("ペイン切替\tF6", self.toggle_active_pane)
        view_menu.addAction("クイックプレビュー\tSpace", self.quick_preview)
        settings_menu = menubar.addMenu("設定")
        settings_menu.addAction("テーマ切替（ダーク／ライト）",
                                self.toggle_theme)
        settings_menu.addAction("初期ディレクトリを設定…",
                                self._set_initial_directory)
        # Win+E オーバーライド（HKCU レジストリ・トグル可）
        from app import winekey
        self.winekey_action = settings_menu.addAction("標準フォルダのオーバーライド")
        self.winekey_action.setCheckable(True)
        self.winekey_action.setChecked(winekey.is_enabled())
        self.winekey_action.setEnabled(winekey.is_supported())
        self.winekey_action.toggled.connect(self._toggle_winekey)

    def _toggle_winekey(self, checked: bool) -> None:
        """Win+E オーバーライドの ON/OFF（レジストリ書換）。"""
        from app import winekey
        if checked:
            exe = winekey.fibro_exe_path()
            if not exe:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self, "Win+E オーバーライド",
                    "この機能はインストール版／exe 版（Fibro.exe）でのみ有効です。\n"
                    "ソースから実行中は利用できません。")
                self.winekey_action.blockSignals(True)
                self.winekey_action.setChecked(False)
                self.winekey_action.blockSignals(False)
                return
            ok = winekey.enable(exe)
            msg = ("Win+E を Fibro に割り当てました" if ok
                   else "設定に失敗しました（権限などをご確認ください）")
        else:
            ok = winekey.disable()
            msg = ("Win+E を標準エクスプローラーに戻しました" if ok
                   else "解除に失敗しました")
        if not ok:
            # 実状態へチェックを戻す（再帰しないようシグナル抑止）
            self.winekey_action.blockSignals(True)
            self.winekey_action.setChecked(winekey.is_enabled())
            self.winekey_action.blockSignals(False)
        self.statusBar().showMessage(msg, 4000)

    def _set_initial_directory(self) -> None:
        """初期ディレクトリをユーザーが選択して保存。"""
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self, "初期ディレクトリを選択",
            self.theme_manager.get("initial_dir", "C:\\"))
        if path:
            self.theme_manager.set("initial_dir", path)
            self.navigate(path)
            self.statusBar().showMessage(
                f"初期ディレクトリを設定しました", 3000)

    def _apply_filter(self) -> None:
        text = self.filter_edit.text().strip()
        self._active_pane.filter_text = text
        self.proxy.set_needle(text)

    def _focus_filter(self) -> None:
        """簡易フィルタリングボックスにフォーカス（F3 から呼び出し）。"""
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    # ---- ナビゲーション ----
    @property
    def current_path(self) -> str:
        return str(Path(self.list_model.rootPath()))

    def navigate(self, path: str, record: bool = True) -> None:
        path = str(Path(path))
        if not Path(path).is_dir():
            return
        if record:
            self._history = self._history[: self._history_pos + 1]
            self._history.append(path)
            self._history_pos = len(self._history) - 1
            self.recent_store.record(path)
            if hasattr(self, "recent_sidebar"):
                self.recent_sidebar.refresh()
        self.list_model.setRootPath(path)
        self.proxy.set_root_path(path)
        self.filter_edit.clear()
        self._active_pane.set_root_index(
            self.proxy.mapFromSource(self.list_model.index(path)))
        self.breadcrumb.set_path(path)
        self.search_panel.set_root(path)
        tree_index = self.tree_model.index(path)
        self.tree.setCurrentIndex(tree_index)
        self.tree.expand(tree_index)
        sel_model = self.table.selectionModel()
        if sel_model:
            sel_model.selectionChanged.connect(
                self._update_selection_status, Qt.ConnectionType.UniqueConnection)
        self._update_selection_status()
        self._update_disk_usage(path)
        self._update_tab_title(self._active_pane)

    def _update_disk_usage(self, path: str) -> None:
        """カレントフォルダがあるドライブの空き容量を表示（NWはタイムアウト付き）。"""
        from app.netpath import safe_disk_usage
        usage = safe_disk_usage(path)
        if usage is None:
            self.disk_label.setText("")
            return
        free, total = usage
        drive = os.path.splitdrive(str(Path(path)))[0] or str(Path(path).anchor)
        self.disk_label.setText(
            f"{drive} 空き {_human_size(free)} / {_human_size(total)}")

    def _on_search_file_selected(self, file_path: str) -> None:
        """検索結果でファイルを選択。親フォルダへ navigate してから select。"""
        file_path = str(Path(file_path))
        if not Path(file_path).exists():
            return
        parent = str(Path(file_path).parent if Path(file_path).is_file() else file_path)
        self.navigate(parent)
        # ファイルをテーブルで select（既存の選択をクリアして置き換え）
        idx = self.list_model.index(file_path)
        if idx.isValid():
            proxy_idx = self.proxy.mapFromSource(idx)
            if proxy_idx.isValid():
                sel_model = self.table.selectionModel()
                if sel_model:
                    sel_model.setCurrentIndex(
                        proxy_idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _on_mouse_nav(self, forward: bool) -> None:
        """マウスサイドボタン: 進む/戻る。"""
        if forward:
            self.go_forward()
        else:
            self.go_back()

    def go_back(self) -> None:
        if self._history_pos > 0:
            self._history_pos -= 1
            self.navigate(self._history[self._history_pos], record=False)

    def go_forward(self) -> None:
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self.navigate(self._history[self._history_pos], record=False)

    def go_up(self) -> None:
        parent = Path(self.current_path).parent
        if str(parent) != self.current_path:
            self.navigate(str(parent))

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        self.navigate(self.tree_model.filePath(index))

    def _table_path(self, index: QModelIndex) -> str:
        return self.list_model.filePath(self.proxy.mapToSource(index))

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        path = self._table_path(index)
        if Path(path).is_dir():
            self.navigate(path)
        else:
            self._open_file(path)

    def _open_file(self, path: str) -> None:
        """関連付けアプリでファイルを開く（GUI を固めないよう別スレッド）。"""
        QThreadPool.globalInstance().start(
            _OpenFileJob(path, self._open_failed.emit))

    def _on_open_failed(self, path: str, err: str) -> None:
        QMessageBox.warning(
            self, "開けません",
            f"ファイルを開けませんでした:\n{path}\n\n{err}")

    # ---- 選択 ----
    def selected_paths(self) -> list[str]:
        sel_model = self.table.selectionModel()
        if not sel_model:
            return []
        return [self._table_path(i) for i in sel_model.selectedRows(0)]

    def _update_selection_status(self, *_args) -> None:
        # 件数は即時表示（I/O なし）。サイズ合計は OneDrive 等で stat() が
        # 重く GUI を固めるため、バックグラウンドで計算して後から反映する。
        paths = self.selected_paths()
        self._sel_count = len(paths)
        self._sel_gen += 1
        self.selection_label.setText(f"選択: {self._sel_count}件")
        if not paths:
            return
        gen = self._sel_gen
        files = paths[:5000]  # 安全弁（大量選択時の上限）
        emit = self._size_computed.emit
        QThreadPool.globalInstance().start(_SelectionSizeJob(files, gen, emit))

    def _apply_selection_size(self, gen: int, total: int) -> None:
        """非同期で求めた選択サイズ合計を反映（最新の選択のみ）。"""
        if gen != self._sel_gen:
            return  # 既に選択が変わっている → 破棄
        text = f"選択: {self._sel_count}件"
        if total:
            text += f" / {_human_size(total)}"
        self.selection_label.setText(text)

    # ---- コンテキストメニュー ----
    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        paths = self.selected_paths()
        if paths:
            menu.addAction("開く", lambda: self._open_paths(paths))
            self._add_open_with_menu(menu, paths)
            menu.addSeparator()
            if len(paths) == 1:
                menu.addAction("名前の変更\tF2", self.rename_single)
            menu.addAction("一括リネーム…\tCtrl+H", self.open_rename_dialog)
            menu.addSeparator()
            menu.addAction("コピー\tCtrl+C", lambda: self._set_clipboard("copy"))
            menu.addAction("切り取り\tCtrl+X", lambda: self._set_clipboard("cut"))
        if self._clipboard:
            menu.addAction("貼り付け\tCtrl+V", self.paste_clipboard)
        # 新規作成（空白部分でも選択中でも使えるようカレント直下に作成）
        new_menu = menu.addMenu("新規作成")
        new_menu.addAction("フォルダー\tCtrl+Shift+N", self.new_folder)
        new_menu.addAction("テキストファイル", self.new_text_file)
        self._add_template_actions(new_menu)
        if paths:
            menu.addSeparator()
            menu.addAction("パスをコピー\tCtrl+Shift+C",
                           lambda: self._copy_paths_to_clipboard(paths))
            menu.addSeparator()
            menu.addAction("削除（ゴミ箱へ）\tDelete", self.delete_selected)
            menu.addSeparator()
            dirs = [p for p in paths if Path(p).is_dir()]
            fav_target = dirs[0] if dirs else None
        else:
            fav_target = self.current_path
        if fav_target:
            menu.addAction("お気に入りに追加",
                           lambda: self.favorites.add_favorite(fav_target))
        if paths:
            menu.addAction("プロパティ", self.show_properties)
            from app import shell_menu
            if shell_menu.is_supported():
                menu.addSeparator()
                gpos = self._active_view().viewport().mapToGlobal(pos)
                menu.addAction(
                    "その他のオプション（Windows）",
                    lambda: self._show_shell_menu(paths, gpos))
        if menu.actions():
            menu.exec(self._active_view().viewport().mapToGlobal(pos))

    def _show_shell_menu(self, paths: list[str], gpos) -> None:
        """選択ファイルの Windows シェルコンテキストメニューを表示。"""
        from app import shell_menu
        ok = shell_menu.show_shell_context_menu(
            int(self.winId()), paths, gpos.x(), gpos.y())
        if not ok:
            self.statusBar().showMessage(
                "Windowsメニューを表示できませんでした", 3000)

    def _active_view(self):
        """アクティブペインの現在表示中ビュー（詳細 or サムネ）。"""
        pane = self._active_pane
        return pane.icon_view if pane.view_mode == "thumbnails" else pane.table

    def _add_open_with_menu(self, menu: QMenu, paths: list[str]) -> None:
        """「ここで開く」系（ターミナル/PowerShell/VS Code/場所）。"""
        dirs = [p for p in paths if Path(p).is_dir()]
        # フォルダ選択時はそのフォルダ、ファイル選択時は親フォルダを対象に
        target = dirs[0] if dirs else str(Path(paths[0]).parent)
        sub = menu.addMenu("ここで開く")
        sub.addAction("ターミナル",
                      lambda: self._open_terminal(target))
        sub.addAction("PowerShell",
                      lambda: self._open_in(["powershell.exe"], target))
        sub.addAction("VS Code",
                      lambda: self._open_in(["code"], target, shell=True))
        sub.addAction("エクスプローラー",
                      lambda: self._open_in(["explorer.exe"], target))

    def _open_terminal(self, directory: str) -> None:
        """Windows Terminal があれば優先、なければ cmd で開く。"""
        try:
            subprocess.Popen(["wt.exe", "-d", directory])  # noqa: S603,S607
        except OSError:
            try:
                subprocess.Popen(["cmd.exe"], cwd=directory)  # noqa: S603,S607
            except OSError as e:
                QMessageBox.warning(self, "ターミナル", f"起動できませんでした:\n{e}")

    def _open_in(self, cmd: list[str], directory: str, shell: bool = False) -> None:
        try:
            if cmd[-1] == "code":  # VS Code は対象パスを引数で渡す
                subprocess.Popen([*cmd, directory], shell=shell)  # noqa: S603
            else:
                subprocess.Popen([*cmd, directory], cwd=directory, shell=shell)  # noqa: S603
        except OSError as e:
            QMessageBox.warning(self, "開く", f"起動できませんでした:\n{e}")

    def _copy_paths_to_clipboard(self, paths: list[str]) -> None:
        clip = QApplication.clipboard()
        if clip:
            clip.setText("\n".join(paths))
            self.statusBar().showMessage(
                f"{len(paths)}件のパスをコピーしました", 3000)

    def _open_paths(self, paths: list[str]) -> None:
        for p in paths[:5]:  # 誤爆防止に上限
            if Path(p).is_dir():
                self.navigate(p)
                break
            self._open_file(p)

    # ---- ファイル操作 ----
    def _set_clipboard(self, mode: str) -> None:
        paths = self.selected_paths()
        if paths:
            self._clipboard = (mode, paths)
            self.statusBar().showMessage(
                f"{len(paths)}件を{'コピー' if mode == 'copy' else '切り取り'}しました", 3000)

    def paste_clipboard(self) -> None:
        if not self._clipboard:
            return
        from app.gui.conflict_dialog import make_resolver
        mode, paths = self._clipboard
        resolver = make_resolver(self)
        try:
            if mode == "copy":
                self.file_ops.copy(paths, self.current_path, resolver=resolver)
            else:
                self.file_ops.move(paths, self.current_path, resolver=resolver)
                self._clipboard = None
        except OSError as e:
            QMessageBox.critical(self, "貼り付け失敗", str(e))
            return
        self.statusBar().showMessage(f"{len(paths)}件を貼り付けました", 3000)

    def _on_files_dropped(self, paths: list, dest: str, copy: bool) -> None:
        """D&D: 既定は移動、Ctrl押下でコピー。どちらも Undo 可。"""
        from app.gui.conflict_dialog import make_resolver
        resolver = make_resolver(self)
        try:
            if copy:
                self.file_ops.copy(paths, dest, resolver=resolver)
            else:
                self.file_ops.move(paths, dest, resolver=resolver)
        except OSError as e:
            QMessageBox.critical(self, "ドロップ失敗", str(e))
            return
        verb = "コピー" if copy else "移動"
        self.statusBar().showMessage(
            f"{len(paths)}件を {Path(dest).name or dest} へ{verb}しました"
            "（Ctrl+Zで取り消し）", 5000)

    def delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        names = "\n".join(Path(p).name for p in paths[:10])
        if len(paths) > 10:
            names += f"\n… 他{len(paths) - 10}件"
        answer = QMessageBox.question(
            self, "削除の確認",
            f"次の{len(paths)}件をゴミ箱に移動しますか?\n\n{names}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.file_ops.delete(paths)
            self.statusBar().showMessage(f"{len(paths)}件をゴミ箱に移動しました", 3000)
        except OSError as e:
            QMessageBox.critical(self, "削除失敗", str(e))

    def show_properties(self) -> None:
        paths = self.selected_paths()
        if paths:
            PropertiesDialog(paths[0], self).exec()

    # ---- 新規作成 ----
    def _unique_path(self, name: str) -> Path:
        """カレント直下で衝突しない名前にして返す（… (2) を付与）。"""
        base = Path(self.current_path) / name
        if not base.exists():
            return base
        stem, suffix = base.stem, base.suffix
        i = 2
        while True:
            candidate = base.with_name(f"{stem} ({i}){suffix}")
            if not candidate.exists():
                return candidate
            i += 1

    def new_folder(self) -> None:
        name, ok = QInputDialog.getText(
            self, "新規フォルダー", "フォルダー名:", text="新しいフォルダー")
        name = name.strip()
        if not ok or not name:
            return
        target = self._unique_path(name)
        try:
            target.mkdir()
        except OSError as e:
            QMessageBox.critical(self, "作成失敗", str(e))
            return
        self.refresh()
        self._select_by_name(target.name)

    def new_text_file(self) -> None:
        name, ok = QInputDialog.getText(
            self, "新規テキストファイル", "ファイル名:", text="新しいテキスト.txt")
        name = name.strip()
        if not ok or not name:
            return
        self._create_file(name, "")

    def _create_file(self, name: str, content: str) -> None:
        target = self._unique_path(name)
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "作成失敗", str(e))
            return
        self.refresh()
        self._select_by_name(target.name)

    def _template_dir(self) -> Path:
        return CONFIG_DIR / "templates"

    def _add_template_actions(self, menu: QMenu) -> None:
        """config/templates/ 内の雛形ファイルをメニューに展開。"""
        tdir = self._template_dir()
        try:
            templates = sorted(p for p in tdir.iterdir() if p.is_file())
        except OSError:
            templates = []
        if not templates:
            return
        menu.addSeparator()
        for tpl in templates:
            menu.addAction(
                f"雛形: {tpl.name}",
                lambda checked=False, t=tpl: self._new_from_template(t))

    def _new_from_template(self, template: Path) -> None:
        name, ok = QInputDialog.getText(
            self, "雛形から新規作成", "ファイル名:", text=template.name)
        name = name.strip()
        if not ok or not name:
            return
        try:
            content = template.read_text(encoding="utf-8")
        except OSError:
            content = ""
        self._create_file(name, content)

    def _select_by_name(self, name: str) -> None:
        """カレント直下の名前を選択してフォーカス（作成直後のハイライト用）。"""
        path = str(Path(self.current_path) / name)
        idx = self.list_model.index(path)
        if not idx.isValid():
            return
        proxy_idx = self.proxy.mapFromSource(idx)
        sel_model = self.table.selectionModel()
        if proxy_idx.isValid() and sel_model:
            sel_model.setCurrentIndex(
                proxy_idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    # ---- 選択操作 ----
    def select_all(self) -> None:
        self.table.selectAll()

    def invert_selection(self) -> None:
        sel_model = self.table.selectionModel()
        if not sel_model:
            return
        root = self.table.rootIndex()
        selected = {i.row() for i in sel_model.selectedRows(0)}
        sel_model.clearSelection()
        for row in range(self.proxy.rowCount(root)):
            if row not in selected:
                idx = self.proxy.index(row, 0, root)
                sel_model.select(
                    idx, QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows)

    def select_by_pattern(self) -> None:
        """ワイルドカード（例 *.png）でカレント直下を一括選択。"""
        import fnmatch
        pattern, ok = QInputDialog.getText(
            self, "パターンで選択", "ワイルドカード（例: *.png）:", text="*.*")
        pattern = pattern.strip()
        if not ok or not pattern:
            return
        sel_model = self.table.selectionModel()
        if not sel_model:
            return
        root = self.table.rootIndex()
        sel_model.clearSelection()
        matched = 0
        for row in range(self.proxy.rowCount(root)):
            idx = self.proxy.index(row, 0, root)
            name = str(idx.data() or "")
            if fnmatch.fnmatch(name.lower(), pattern.lower()):
                sel_model.select(
                    idx, QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows)
                matched += 1
        self.statusBar().showMessage(f"{matched}件を選択しました", 3000)

    def _copy_selected_paths(self) -> None:
        """Ctrl+Shift+C: 選択中のフルパスをクリップボードへ。"""
        paths = self.selected_paths()
        if paths:
            self._copy_paths_to_clipboard(paths)

    # ---- 設定 ----
    def _set_initial_directory(self) -> None:
        """初期ディレクトリをユーザーが選択して保存。"""
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self, "初期ディレクトリを選択", self.theme_manager.get("initial_dir", "C:\\"))
        if path:
            self.theme_manager.set("initial_dir", path)
            self.navigate(path)
            self.statusBar().showMessage(f"初期ディレクトリを {path} に設定しました", 3000)

    # ---- その他操作 ----
    def refresh(self) -> None:
        """一覧を再読み込み（モデルのキャッシュを更新）。"""
        path = self.current_path
        self.list_model.setRootPath("")
        self.list_model.setRootPath(path)
        self._active_pane.set_root_index(
            self.proxy.mapFromSource(self.list_model.index(path)))

    def toggle_theme(self) -> None:
        app = QApplication.instance()
        if app:
            theme = self.theme_manager.toggle(app)
            self.statusBar().showMessage(
                f"テーマ: {'ダーク' if theme == 'dark' else 'ライト'}", 3000)

    def rename_single(self) -> None:
        """F2: 選択中1件をその場でリネーム（RenameExecutor 経由で Undo 可）。"""
        paths = self.selected_paths()
        if len(paths) != 1:
            if len(paths) > 1:
                self.open_rename_dialog()
            return
        old_name = Path(paths[0]).name
        new_name, ok = QInputDialog.getText(
            self, "名前の変更", "新しい名前:", text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        if (Path(self.current_path) / new_name).exists():
            QMessageBox.warning(self, "名前の変更",
                                f"「{new_name}」は既に存在します。")
            return
        try:
            self.rename_executor.execute(
                self.current_path, [(old_name, new_name)])
            self.statusBar().showMessage(
                f"{old_name} → {new_name}（Ctrl+Zで取り消し）", 5000)
        except OSError as e:
            QMessageBox.critical(self, "リネーム失敗", str(e))

    # ---- 検索 ----
    def toggle_search(self) -> None:
        if self.search_dock.isVisible():
            self.search_dock.hide()
        else:
            self.search_panel.set_root(self.current_path)
            self.search_dock.show()
            self.search_panel.keyword_edit.setFocus()

    def _restore_layout(self) -> None:
        layout = self.theme_manager.get("layout", {})
        if not isinstance(layout, dict):
            return
        if sizes := layout.get("main_splitter"):
            self._main_splitter.setSizes([int(s) for s in sizes])
        if sizes := layout.get("left_splitter"):
            self._left_splitter.setSizes([int(s) for s in sizes])
        if sizes := layout.get("pane_splitter"):
            self.pane_splitter.setSizes([int(s) for s in sizes])
        if geo := layout.get("window"):
            try:
                self.setGeometry(*[int(v) for v in geo])
            except (TypeError, ValueError):
                pass

    def _save_layout(self) -> None:
        g = self.geometry()
        self.theme_manager.set("layout", {
            "main_splitter": self._main_splitter.sizes(),
            "left_splitter": self._left_splitter.sizes(),
            "pane_splitter": self.pane_splitter.sizes(),
            "window": [g.x(), g.y(), g.width(), g.height()],
        })

    def _save_tabs(self) -> None:
        self.theme_manager.set(
            "tabs", [p.current_path for p in self._tabs if p.current_path])

    def _save_columns(self) -> None:
        state = self._current_primary().header.saveState()
        self.theme_manager.set("columns", state.toBase64().data().decode())

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        self._save_tabs()
        self._save_columns()
        self._save_layout()
        self.search_panel.cancel_search()
        super().closeEvent(event)

    # ---- リネーム / Undo ----
    def open_rename_dialog(self) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(
                self, "一括リネーム", "リネームするファイルを選択してください。")
            return
        directory = self.current_path
        selected = [Path(p).name for p in paths]
        try:
            existing = {e.name for e in os.scandir(directory)}
        except OSError:
            existing = set(selected)
        dialog = RenameDialog(directory, selected, existing,
                              self.rename_executor, self,
                              preset_store=self.preset_store)
        if dialog.exec():
            self.statusBar().showMessage("リネームが完了しました（Ctrl+Zで取り消し）", 5000)

    def undo_last(self) -> None:
        if self.rename_executor.can_undo:
            try:
                record = self.rename_executor.undo()
                self.statusBar().showMessage(
                    f"リネーム {len(record.mapping)}件を取り消しました", 3000)
            except (OSError, RuntimeError) as e:
                QMessageBox.critical(self, "Undo失敗", str(e))
        elif self.file_ops.can_undo:
            try:
                record = self.file_ops.undo()
                kind = "移動" if record.kind == "move" else "コピー"
                self.statusBar().showMessage(
                    f"{kind} {len(record.pairs)}件を取り消しました", 3000)
            except (OSError, RuntimeError) as e:
                QMessageBox.critical(self, "Undo失敗", str(e))
        else:
            self.statusBar().showMessage("取り消せる操作はありません", 3000)
