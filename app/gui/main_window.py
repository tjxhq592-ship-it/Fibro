"""メインウィンドウ: QFileSystemModel ベースの3ペイン構成。

左=フォルダツリー(QTreeView)、中央=ファイル一覧(QTableView)、右=詳細。
上部にパンくず+パス直接入力。下部にステータス+主要操作。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import (
    QDir, QItemSelectionModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileSystemModel, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QSplitter, QStackedLayout, QStatusBar, QStyle, QToolBar, QToolButton,
    QTreeView, QVBoxLayout, QWidget, QTableView,
)

from app.engine.file_ops import FileOps
from app.engine.rename_history import RenameExecutor
from app.gui.dnd_views import FileTableView, FolderTreeView
from app.gui.icons import feather_icon
from app.gui.favorites_sidebar import FavoritesSidebar
from app.gui.properties_dialog import PropertiesDialog
from app.gui.rename_dialog import RenameDialog
from app.gui.search_panel import SearchPanel
from app.gui.theme import ThemeManager
from app.models.favorite import FavoriteStore

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


class CurrentDirFilterProxy(QSortFilterProxyModel):
    """現在フォルダ直下の行だけを名前でフィルタするプロキシ。

    ルートに至る祖先階層はフィルタ対象外（隠すと全消えになるため）。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._root_path = ""
        self._needle = ""

    def set_root_path(self, path: str) -> None:
        self._root_path = str(Path(path))
        self.invalidateRowsFilter()

    def set_needle(self, text: str) -> None:
        self._needle = text.lower()
        self.invalidateRowsFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        if str(Path(model.filePath(parent))) != self._root_path:
            return True  # カレント直下以外（祖先チェーン等）は素通し
        name = model.index(row, 0, parent).data() or ""
        return self._needle in str(name).lower()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fibro — ファイルエクスプローラー")
        self.resize(1100, 700)

        self.rename_executor = RenameExecutor()
        self.file_ops = FileOps()
        self.favorite_store = FavoriteStore(CONFIG_DIR / "favorites.json")
        self.theme_manager = ThemeManager(CONFIG_DIR / "settings.json")
        self._clipboard: tuple[str, list[str]] | None = None  # ("copy"|"cut", paths)
        self._history: list[str] = []
        self._history_pos = -1

        # --- モデル（ツリー用と一覧用で分離） ---
        self.tree_model = QFileSystemModel(self)
        self.tree_model.setFilter(
            QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        self.tree_model.setRootPath("")

        self.list_model = QFileSystemModel(self)
        self.list_model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self.list_model.setReadOnly(True)

        self._build_ui()
        self._build_toolbar()
        self._build_actions()
        # 初期ディレクトリ設定を復元（設定がなければホームに）
        initial = self.theme_manager.get("initial_dir", str(Path.home()))
        self.navigate(initial if Path(initial).is_dir() else str(Path.home()))

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("メイン")
        toolbar.setMovable(False)
        # (action名, Featherアイコン名, ツールチップ, スロット)
        self._toolbar_specs = [
            ("search", "search", "検索 (Ctrl+F)", self.toggle_search),
            ("rename", "edit", "一括リネーム (Ctrl+H)",
             self.open_rename_dialog),
            ("undo", "rotate-ccw", "Undo (Ctrl+Z)", self.undo_last),
            (None, None, None, None),  # separator
            ("refresh", "refresh-cw", "更新 (F5)", self.refresh),
            ("delete", "trash-2", "削除 (Delete)", self.delete_selected),
            (None, None, None, None),
            ("theme", "moon", "テーマ切替", self.toggle_theme),
        ]
        self._toolbar_actions: dict[str, QAction] = {}
        for key, icon_name, tip, slot in self._toolbar_specs:
            if key is None:
                toolbar.addSeparator()
                continue
            action = toolbar.addAction("", slot)
            action.setToolTip(tip)
            self._toolbar_actions[key] = action
        self.addToolBar(toolbar)
        self._apply_icons()

    def _apply_icons(self) -> None:
        """現在テーマの色でアイコンを設定（テーマ切替時に再呼び出し）。"""
        dark = self.theme_manager.theme == "dark"
        for key, icon_name, _tip, _slot in self._toolbar_specs:
            if key is None:
                continue
            name = icon_name
            if key == "theme":  # 次に切り替わるテーマを示す
                name = "sun" if dark else "moon"
            self._toolbar_actions[key].setIcon(feather_icon(name, dark))
        self.back_btn.setIcon(feather_icon("arrow-left", dark))
        self.fwd_btn.setIcon(feather_icon("arrow-right", dark))
        self.up_btn.setIcon(feather_icon("arrow-up", dark))
        for btn in (self.back_btn, self.fwd_btn, self.up_btn):
            btn.setText("")

    # ---- UI ----
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # 上部バー: 戻る/進む + パンくず
        top = QHBoxLayout()
        self.back_btn = QToolButton(text="←")
        self.fwd_btn = QToolButton(text="→")
        self.up_btn = QToolButton(text="↑")
        self.back_btn.clicked.connect(self.go_back)
        self.fwd_btn.clicked.connect(self.go_forward)
        self.up_btn.clicked.connect(self.go_up)
        self.breadcrumb = BreadcrumbBar()
        self.breadcrumb.path_selected.connect(self.navigate)
        for w in (self.back_btn, self.fwd_btn, self.up_btn):
            top.addWidget(w)
        top.addWidget(self.breadcrumb, stretch=1)
        root.addLayout(top)

        # フィルタボックス（パスバー下）
        filter_row = QHBoxLayout()
        filter_label = QLabel("フィルタ:")
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("カレントフォルダ内を絞り込み…")
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_debounce = QTimer(self, singleShot=True, interval=200)
        self._filter_debounce.timeout.connect(
            lambda: self.proxy.set_needle(self.filter_edit.text().strip()))
        self.filter_edit.textChanged.connect(self._filter_debounce.start)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.filter_edit, stretch=1)
        root.addLayout(filter_row)

        # 3ペイン
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = FolderTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.files_dropped.connect(self._on_files_dropped)
        for col in range(1, self.tree_model.columnCount()):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self._on_tree_clicked)

        self.proxy = CurrentDirFilterProxy(self)
        self.proxy.setSourceModel(self.list_model)
        self.table = FileTableView()
        self.table.setModel(self.proxy)
        self.table.files_dropped.connect(self._on_files_dropped)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.horizontalHeader().resizeSection(0, 280)

        # 左ペイン: お気に入い（上）+ フォルダツリー（下）
        self.favorites = FavoritesSidebar(self.favorite_store)
        self.favorites.path_selected.connect(self.navigate)
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.favorites)
        left.addWidget(self.tree)
        left.setSizes([180, 420])

        splitter.addWidget(left)
        splitter.addWidget(self.table)
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

        # 下部: ステータス
        bottom = QHBoxLayout()
        self.selection_label = QLabel("選択: 0件")
        bottom.addWidget(self.selection_label, stretch=1)
        root.addLayout(bottom)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

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
        add("単一リネーム", "F2", self.rename_single)
        add("フィルタへ", "F3", self._focus_filter)
        add("パス入力へ", "F4", self.breadcrumb.focus_path_edit)
        add("更新", "F5", self.refresh)

        # メニューバー：ファイル・設定
        menubar = self.menuBar()
        file_menu = menubar.addMenu("ファイル")
        file_menu.addAction("終了", self.close)
        settings_menu = menubar.addMenu("設定")
        settings_menu.addAction("初期ディレクトリを設定…",
                                self._set_initial_directory)

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
        self.list_model.setRootPath(path)
        self.proxy.set_root_path(path)
        self.filter_edit.clear()
        self.table.setRootIndex(
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
        self.back_btn.setEnabled(self._history_pos > 0)
        self.fwd_btn.setEnabled(self._history_pos < len(self._history) - 1)

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

    def go_back(self) -> None:
        if self._history_pos > 0:
            self._history_pos -= 1
            self.navigate(self._history[self._history_pos], record=False)
            self.back_btn.setEnabled(self._history_pos > 0)
            self.fwd_btn.setEnabled(True)

    def go_forward(self) -> None:
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self.navigate(self._history[self._history_pos], record=False)
            self.fwd_btn.setEnabled(self._history_pos < len(self._history) - 1)
            self.back_btn.setEnabled(True)

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
            os.startfile(path)  # noqa: S606 — ユーザー操作による「開く」

    # ---- 選択 ----
    def selected_paths(self) -> list[str]:
        sel_model = self.table.selectionModel()
        if not sel_model:
            return []
        return [self._table_path(i) for i in sel_model.selectedRows(0)]

    def _update_selection_status(self, *_args) -> None:
        paths = self.selected_paths()
        total = 0
        for p in paths:
            try:
                if Path(p).is_file():
                    total += Path(p).stat().st_size
            except OSError:
                pass
        text = f"選択: {len(paths)}件"
        if total:
            text += f" / {_human_size(total)}"
        self.selection_label.setText(text)

    # ---- コンテキストメニュー ----
    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        paths = self.selected_paths()
        if paths:
            menu.addAction("開く", lambda: self._open_paths(paths))
            menu.addSeparator()
            if len(paths) == 1:
                menu.addAction("名前の変更\tF2", self.rename_single)
            menu.addAction("一括リネーム…\tCtrl+H", self.open_rename_dialog)
            menu.addSeparator()
            menu.addAction("コピー\tCtrl+C", lambda: self._set_clipboard("copy"))
            menu.addAction("切り取り\tCtrl+X", lambda: self._set_clipboard("cut"))
        if self._clipboard:
            menu.addAction("貼り付け\tCtrl+V", self.paste_clipboard)
        if paths:
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
        if menu.actions():
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_paths(self, paths: list[str]) -> None:
        for p in paths[:5]:  # 誤爆防止に上限
            if Path(p).is_dir():
                self.navigate(p)
                break
            os.startfile(p)  # noqa: S606

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
        mode, paths = self._clipboard
        try:
            if mode == "copy":
                self.file_ops.copy(paths, self.current_path)
            else:
                self.file_ops.move(paths, self.current_path)
                self._clipboard = None
        except OSError as e:
            QMessageBox.critical(self, "貼り付け失敗", str(e))
            return
        self.statusBar().showMessage(f"{len(paths)}件を貼り付けました", 3000)

    def _on_files_dropped(self, paths: list, dest: str, copy: bool) -> None:
        """D&D: 既定は移動、Ctrl押下でコピー。どちらも Undo 可。"""
        try:
            if copy:
                self.file_ops.copy(paths, dest)
            else:
                self.file_ops.move(paths, dest)
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
        self.table.setRootIndex(
            self.proxy.mapFromSource(self.list_model.index(path)))

    def toggle_theme(self) -> None:
        app = QApplication.instance()
        if app:
            theme = self.theme_manager.toggle(app)
            self._apply_icons()
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
            "window": [g.x(), g.y(), g.width(), g.height()],
        })

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
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
                              self.rename_executor, self)
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
