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
    QDir, QFileInfo, QItemSelectionModel, QModelIndex, QRunnable, Qt,
    QThreadPool, QTimer, Signal,
)
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QDockWidget, QFileIconProvider,
    QFileSystemModel, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QSizePolicy, QSplitter,
    QStackedLayout, QStackedWidget, QStatusBar, QStyle, QTabBar, QToolButton,
    QTreeView, QVBoxLayout, QWidget,
)

from app.engine.file_ops import FileOps
from app.engine.rename_history import RenameExecutor
from app.i18n import _
from app.gui.async_icons import shared_icon_provider
from app.gui.dnd_views import FolderTreeView
from app.gui.file_pane import FilePane
from app.gui.favorites_sidebar import FavoritesSidebar
from app.gui.icons import material_icon
from app.gui.preview_dialog import QuickPreviewDialog
from app.gui.properties_dialog import PropertiesDialog
from app.gui.recent_sidebar import RecentSidebar
from app.gui.rename_dialog import RenameDialog
from app.gui.theme import ThemeManager
from app.models.favorite import FavoriteStore
from app.models.recent import RecentStore
from app.models.rename_presets import RenamePresetStore

from app.paths import CONFIG_DIR


# ショートカット一覧（カテゴリキー → [(キー, 機能名キー)]）。
# _build_actions と eventFilter（Ctrl+Tab / Space）に対応。一覧ポップアップの表示元。
# 文字列は i18n キーで保持し、表示時に _() で解決する（モジュール読込は言語適用前のため）。
SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("sc_cat_file", [
        ("Ctrl+C", "sc_copy"),
        ("Ctrl+X", "sc_cut"),
        ("Ctrl+V", "sc_paste"),
        ("Ctrl+Shift+C", "sc_copy_path"),
        ("Delete", "sc_trash"),
        ("Shift+Delete", "sc_delete"),
        ("F2", "sc_rename"),
        ("Ctrl+H", "sc_batch_rename"),
        ("Ctrl+Shift+N", "sc_new_folder"),
        ("Ctrl+Z", "sc_undo"),
    ]),
    ("sc_cat_nav", [
        ("Alt+↑", "sc_up"),
        ("Alt+←", "sc_back"),
        ("Alt+→", "sc_forward"),
        ("F4", "sc_path_input"),
        ("F5", "sc_refresh"),
    ]),
    ("sc_cat_search", [
        ("Ctrl+F", "sc_search"),
        ("F3", "sc_filter"),
        ("Ctrl+A", "sc_select_all"),
    ]),
    ("sc_cat_view", [
        ("Ctrl+T", "sc_new_tab"),
        ("Ctrl+W", "sc_close_tab"),
        ("Ctrl+Tab / Ctrl+Shift+Tab", "sc_switch_tab"),
        ("F9", "sc_dual_pane"),
        ("F6", "sc_switch_pane"),
        ("Ctrl+Shift+T", "sc_toggle_view"),
        ("Space", "sc_quick_preview"),
    ]),
]


def _human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:,.1f} TB"


class SingleRenameDialog(QDialog):
    """名前と拡張子を別フィールドで編集するリネームダイアログ。

    ファイルは「名前」と「拡張子（先頭の . は除く）」を分けて表示する。
    フォルダや拡張子なしのファイルは拡張子欄を空のまま使える。
    起動時は名前欄に拡張子を除いた部分を選択した状態でフォーカスする。
    """

    def __init__(self, old_name: str, is_dir: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("rename_title"))
        stem, ext = self._split(old_name, is_dir)

        self.name_edit = QLineEdit(stem)
        self.ext_edit = QLineEdit(ext)

        form = QFormLayout()
        form.addRow(_("rename_name_label"), self.name_edit)
        # フォルダは拡張子の概念がないため欄を出さない
        if not is_dir:
            form.addRow(_("rename_ext_label"), self.ext_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        # 長いファイル名でも見やすいよう、既定の約2倍の幅を確保。
        self.setMinimumWidth(560)

        self.name_edit.setFocus()
        self.name_edit.selectAll()

    @staticmethod
    def _split(name: str, is_dir: bool) -> tuple[str, str]:
        """name を (ステム, 拡張子) に分割。先頭ドットのみ等は拡張子扱いしない。"""
        if is_dir:
            return name, ""
        dot = name.rfind(".")
        if dot <= 0:  # 先頭ドット（.gitignore 等）や拡張子なしは分割しない
            return name, ""
        return name[:dot], name[dot + 1:]

    def new_name(self) -> str:
        """編集後の完全なファイル名を返す。"""
        stem = self.name_edit.text().strip()
        ext = self.ext_edit.text().strip().lstrip(".")
        if ext:
            return f"{stem}.{ext}"
        return stem

    @classmethod
    def get_new_name(cls, old_name: str, is_dir: bool,
                     parent=None) -> tuple[str, bool]:
        """(新しい名前, OK押下か) を返す（QInputDialog.getText 互換の使い勝手）。"""
        dlg = cls(old_name, is_dir, parent)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        return (dlg.new_name() if ok else ""), ok


class _ClickableWidget(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class BreadcrumbBar(QWidget):
    """パンくずバー。クリックで切替、ダブルクリック相当でパス直接入力。"""

    path_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stack = QStackedLayout(self)

        self._crumb_widget = _ClickableWidget()
        self._crumb_widget.setObjectName("pathBox")
        self._crumb_widget.clicked.connect(self._show_edit)
        self._crumb_layout = QHBoxLayout(self._crumb_widget)
        self._crumb_layout.setContentsMargins(6, 2, 6, 2)
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
        # QFileSystemModel はスラッシュ区切りを返し、UNC（//server/share/…）だと
        # os.startfile が「ファイルが見つからない」になる。バックスラッシュへ正規化。
        self._path = os.path.normpath(path)
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
        self.setWindowTitle(_("app_title"))
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
        self._pending_paths: dict[int, str] = {}  # index→未ロードパス（遅延ロード）
        self._active_pane: FilePane | None = None
        self._dual = False

        # ツリーは共有（左サイドバー）
        self.tree_model = QFileSystemModel(self)
        self.tree_model.setFilter(
            QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        # カスタムフォルダアイコンのシェル問い合わせを無効化（OneDrive 対策）
        self.tree_model.setOption(
            QFileSystemModel.Option.DontUseCustomDirectoryIcons, True)
        # 拡張子単位の遅延アイコンプロバイダを共有し、ツリー展開時のシェル
        # 問い合わせ回数を抑える（解決後に差分で再描画）。
        self.tree_model.setIconProvider(shared_icon_provider())
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
        central.setObjectName("centralRoot")
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # 上部バー: パンくず（戻る/進む/上は Alt+←/→/↑ のショートカットで）
        top = QHBoxLayout()
        self.breadcrumb = BreadcrumbBar()
        self.breadcrumb.path_selected.connect(self.navigate)
        top.addWidget(self.breadcrumb, stretch=1)
        # ショートカット一覧の左隣: 設定ボタン（歯車）
        self.settings_btn = QToolButton()
        self.settings_btn.setIcon(material_icon("settings", dark=self._is_dark()))
        self.settings_btn.setToolTip(_("tip_settings"))
        self.settings_btn.setAutoRaise(True)
        self.settings_btn.clicked.connect(self._show_settings_menu)
        top.addWidget(self.settings_btn)
        # パンくず行の右端: ショートカット一覧ボタン（⌘）
        self.shortcuts_btn = QToolButton()
        self.shortcuts_btn.setIcon(
            material_icon("keyboard_command_key", dark=self._is_dark()))
        self.shortcuts_btn.setToolTip(_("tip_shortcuts"))
        self.shortcuts_btn.setAutoRaise(True)
        self.shortcuts_btn.clicked.connect(self._show_shortcuts)
        top.addWidget(self.shortcuts_btn)
        # ショートカット一覧の右隣: ヘルプボタン（?）
        self.help_btn = QToolButton()
        self.help_btn.setIcon(material_icon("help", dark=self._is_dark()))
        self.help_btn.setToolTip(_("tip_help"))
        self.help_btn.setAutoRaise(True)
        self.help_btn.clicked.connect(self._show_help_menu)
        top.addWidget(self.help_btn)
        root.addLayout(top)

        # 3ペイン
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setHandleWidth(10)
        splitter.setChildrenCollapsible(False)

        self.tree = FolderTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.files_dropped.connect(self._on_files_dropped)
        self.tree.native_drop_requested.connect(self._on_native_drop)
        self.tree.mouse_nav.connect(self._on_mouse_nav)
        for col in range(1, self.tree_model.columnCount()):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self._on_tree_clicked)
        # 遅延アイコン解決後にツリーを差分再描画
        shared_icon_provider().signals.ready.connect(
            self.tree.viewport().update)

        # 中央右側: タブバー + ペインスプリッタ（主スタック + サブペイン）
        self.tab_bar = QTabBar()
        self.tab_bar.setMovable(True)
        self.tab_bar.setTabsClosable(False)
        self.tab_bar.setExpanding(False)
        # スクロールボタン用の予約領域を無くし、タブと「＋」の間の隙間を解消
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)

        # 21: タブの右に「＋」新規タブボタン
        self.new_tab_btn = QToolButton()
        self.new_tab_btn.setText("＋")
        self.new_tab_btn.setAutoRaise(True)
        self.new_tab_btn.setToolTip(_("new_tab_tooltip"))
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
        self.pane_splitter.setHandleWidth(1)
        self.pane_splitter.addWidget(self.primary_stack)
        self.pane_splitter.addWidget(self.secondary_pane)

        # フィルタボックス（タブ行の下）
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(_("filter_placeholder"))
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_debounce = QTimer(self, singleShot=True, interval=200)
        self._filter_debounce.timeout.connect(self._apply_filter)
        self.filter_edit.textChanged.connect(self._filter_debounce.start)

        right_area = QFrame()
        right_area.setObjectName("rightContentBox")
        right_area.setFrameShape(QFrame.Shape.NoFrame)
        right_layout = QVBoxLayout(right_area)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(2)
        right_layout.addLayout(tab_row)
        right_layout.addWidget(self.filter_edit)
        right_layout.addWidget(self.pane_splitter, stretch=1)

        # 左ペイン: お気に入い（上）+ 履歴（中）+ フォルダツリー（下）
        self.favorites = FavoritesSidebar(self.favorite_store)
        self.favorites.path_selected.connect(self.navigate)
        self.recent_sidebar = RecentSidebar(self.recent_store)
        self.recent_sidebar.path_selected.connect(self.navigate)
        from app.gui.collapsible import CollapsibleSection
        self.fav_section = CollapsibleSection(_("sidebar_fav"), self.favorites)
        self.recent_section = CollapsibleSection(_("sidebar_history"), self.recent_sidebar)
        self.tree_section = CollapsibleSection(_("sidebar_tree"), self.tree)
        left = QSplitter(Qt.Orientation.Vertical)
        left.setHandleWidth(1)
        left.addWidget(self.fav_section)
        left.addWidget(self.recent_section)
        left.addWidget(self.tree_section)
        # 全セクション折りたたみ時に左ペインが縦に潰れないよう、
        # 余白を吸収する伸縮スペーサーを最下段に置く（初期高さ0）。
        left_spacer = QWidget()
        left_spacer.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        left.addWidget(left_spacer)
        left.setSizes([160, 160, 320, 0])
        for sec in (self.fav_section, self.recent_section, self.tree_section):
            sec.toggled.connect(lambda _checked: self._save_layout())

        left_box = QFrame()
        left_box.setObjectName("leftSidebarBox")
        left_box.setFrameShape(QFrame.Shape.NoFrame)
        left_box_layout = QVBoxLayout(left_box)
        left_box_layout.setContentsMargins(8, 8, 8, 8)
        left_box_layout.setSpacing(0)
        left_box_layout.addWidget(left)
        splitter.addWidget(left_box)
        splitter.addWidget(right_area)
        splitter.setSizes([220, 860])
        root.addWidget(splitter, stretch=1)
        self._main_splitter = splitter
        self._left_splitter = left
        self._restore_layout()
        # タブ行の実高さにサイドバー見出しの高さを合わせる（QSS 適用後に実行）。
        QTimer.singleShot(0, self._align_section_headers)

        # 各パネルの Ctrl+ホイール ズーム（75〜200%・倍率は settings に保存）
        from app.gui.zoom import ZoomController
        ZoomController(self.favorites.tree, "favorites", self.theme_manager)
        ZoomController(self.recent_sidebar.tree, "recent", self.theme_manager)
        ZoomController(self.tree, "folder_tree", self.theme_manager)

        # 検索ドックは初回 Ctrl+F まで作らない（起動を速く保つ。SearchPanel は
        # 検索エンジン等を引き込み import が重いため遅延生成）。
        self.search_panel = None
        self.search_dock = None

        # 下部: ステータス（選択情報 + ドライブ空き容量）
        bottom = QHBoxLayout()
        self.selection_label = QLabel(_("sel_0"))
        self.selection_label.setObjectName("statusLabel")
        self.disk_label = QLabel("")
        self.disk_label.setObjectName("statusLabel")
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
            view.native_drop_requested.connect(self._on_native_drop)
            view.mouse_nav.connect(self._on_mouse_nav)
            view.doubleClicked.connect(self._on_table_double_clicked)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(self._show_context_menu)
            view.preview_requested.connect(self.quick_preview)
            view.open_requested.connect(self._open_selected)
        pane.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        pane.set_view_mode(self.theme_manager.get("view_mode", "details"))
        cols = self.theme_manager.get("columns")
        if isinstance(cols, str) and cols:
            from PySide6.QtCore import QByteArray
            pane.header.restoreState(QByteArray.fromBase64(cols.encode()))
        pane.activated.connect(self._set_active_pane)
        pane.list_model.directoryLoaded.connect(
            lambda p, pn=pane: self._on_directory_loaded(pn, p))
        # ファイル一覧（詳細ビュー）の Ctrl+ホイール ズーム。タブ間で同じ
        # キーを共有し、保存倍率を各ペインが読み込む。
        from app.gui.zoom import ZoomController
        ZoomController(pane.table, "filelist", self.theme_manager)
        return pane

    def _on_directory_loaded(self, pane: FilePane, path: str) -> None:
        """QFileSystemModel が非同期に読み込んだ件数をステータスへ（巨大フォルダ確認）。"""
        if pane is not self._active_pane:
            return
        if str(Path(path)) != pane.current_path:
            return
        count = pane.list_model.rowCount(pane.list_model.index(path))
        self.statusBar().showMessage(_("items_n").format(n=count), 4000)

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
        if self.search_panel is not None:
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
        """デュアル時にアクティブペインを枠線で示す（paintEvent 描画・SS不使用）。"""
        if not self._dual:
            self.secondary_pane.set_active_border(None)
            self._current_primary().set_active_border(None)
            return
        for pane in (self._current_primary(), self.secondary_pane):
            pane.set_active_border(pane is self._active_pane)

    def new_tab(self, path: str | None = None) -> FilePane:
        target = path or (self._active_pane.current_path
                          if self._active_pane else self._default_dir())
        if not Path(target).is_dir():
            target = self._default_dir()
        pane = self._make_pane()
        self._tabs.append(pane)
        self.primary_stack.addWidget(pane)
        idx = self.tab_bar.addTab(self._tab_title(target))
        self._install_tab_close_button(idx)
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
        # 未ロードのタブを初めて選択したとき、ここで遅延ロードを実行する
        if index in self._pending_paths:
            path = self._pending_paths.pop(index)
            pane = self._tabs[index]
            self.primary_stack.setCurrentWidget(pane)
            self._set_active_pane(pane)
            self.navigate(path)
            return
        pane = self._tabs[index]
        self.primary_stack.setCurrentWidget(pane)
        self._set_active_pane(pane)

    def close_tab(self, index: int) -> None:
        if len(self._tabs) <= 1 or not (0 <= index < len(self._tabs)):
            return
        # 閉じるタブの pending を削除し、それより後ろの index を補正する
        self._pending_paths.pop(index, None)
        self._pending_paths = {
            (i - 1 if i > index else i): p
            for i, p in self._pending_paths.items()
        }
        pane = self._tabs.pop(index)
        self.primary_stack.removeWidget(pane)
        self.tab_bar.removeTab(index)  # currentChanged → _on_tab_changed
        pane.deleteLater()
        # サブがアクティブだった場合に備えて主へ寄せる
        if self._active_pane is pane:
            self._set_active_pane(self._current_primary())

    def close_current_tab(self) -> None:
        self.close_tab(self.tab_bar.currentIndex())

    def _install_tab_close_button(self, index: int) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTabBar
        btn = QToolButton()
        btn.setObjectName("tabClose")
        btn.setText("✕")
        btn.setAutoRaise(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(_("tab_close_tooltip"))
        btn.clicked.connect(lambda: self._close_tab_for_button(btn))
        self.tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, btn)

    def _close_tab_for_button(self, btn) -> None:
        from PySide6.QtWidgets import QTabBar
        for i in range(self.tab_bar.count()):
            if self.tab_bar.tabButton(i, QTabBar.ButtonPosition.RightSide) is btn:
                self.close_tab(i)
                return

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
            _("view_mode_thumb" if new_mode == "thumbnails" else "view_mode_detail"),
            3000)

    def _restore_tabs(self) -> None:
        saved = self.theme_manager.get("tabs", [])
        paths = ([p for p in saved if isinstance(p, str) and Path(p).is_dir()]
                 if isinstance(saved, list) else [])
        if not paths:
            paths = [self._default_dir()]

        for i, p in enumerate(paths):
            pane = self._make_pane()
            self._tabs.append(pane)
            self.primary_stack.addWidget(pane)
            idx = self.tab_bar.addTab(self._tab_title(p))
            self._install_tab_close_button(idx)
            self.tab_bar.setTabToolTip(idx, p)

            if i == 0:
                # アクティブタブのみ即ロード（setRootPath + navigate を実行）
                self.navigate(p)
            else:
                # 非アクティブタブはパスのみ記録し、navigate を遅延させる
                self._pending_paths[i] = p

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
        add("完全削除", "Shift+Delete", self.delete_selected_permanent)
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

        # 設定はトップバーの歯車アイコン（settings_btn → _show_settings_menu）に移動。
        # Win+E オーバーライド用の永続アクションをここで生成しておく。
        from app import winekey
        self.winekey_action = QAction(_("menu_override_wine"), self)
        self.winekey_action.setCheckable(True)
        self.winekey_action.setChecked(winekey.is_enabled())
        self.winekey_action.setEnabled(winekey.is_supported())
        self.winekey_action.toggled.connect(self._toggle_winekey)

    def _show_settings_menu(self) -> None:
        """歯車ボタン: 設定項目をポップアップメニューで表示。"""
        menu = QMenu(self)
        menu.addAction(_("menu_toggle_theme"), self.toggle_theme)
        menu.addAction(_("menu_set_initial"), self._set_initial_directory)
        # 言語切り替えサブメニュー（保存→次回起動時に反映）
        lang_menu = menu.addMenu(_("menu_language"))
        lang_menu.addAction(_("menu_lang_ja"), lambda: self._set_language("ja"))
        lang_menu.addAction(_("menu_lang_en"), lambda: self._set_language("en"))
        menu.addSeparator()
        # Win+E オーバーライド（HKCU レジストリ・トグル可）の永続アクション
        menu.addAction(self.winekey_action)
        pos = self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomLeft())
        menu.exec(pos)

    def _set_language(self, lang: str) -> None:
        """言語を設定に保存。即時反映はせず次回起動時に適用する。"""
        self.theme_manager.set("language", lang)
        self.statusBar().showMessage(_("lang_restart"), 5000)

    # バグ報告先（GitHub Issues）
    _ISSUES_URL = "https://github.com/tjxhq592-ship-it/release/issues"

    def _report_bug(self) -> None:
        """既定ブラウザで GitHub の Issues ページを開く。"""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(self._ISSUES_URL))

    def _show_about(self) -> None:
        """バージョン情報ダイアログ。"""
        import platform
        from PySide6 import __version__ as pyside_version
        from app import __version__ as app_version
        QMessageBox.about(
            self, _("dlg_about_title"),
            _("dlg_about_body").format(
                ver=app_version,
                py=platform.python_version(),
                pyside=pyside_version,
                sys=f"{platform.system()} {platform.release()}",
                url=self._ISSUES_URL))

    def _toggle_winekey(self, checked: bool) -> None:
        """Win+E オーバーライドの ON/OFF（レジストリ書換）。"""
        from app import winekey
        if checked:
            exe = winekey.fibro_exe_path()
            if not exe:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self, _("dlg_wine_title"), _("dlg_wine_exe_only"))
                self.winekey_action.blockSignals(True)
                self.winekey_action.setChecked(False)
                self.winekey_action.blockSignals(False)
                return
            ok = winekey.enable(exe)
            msg = _("win_e_on" if ok else "win_e_fail_on")
        else:
            ok = winekey.disable()
            msg = _("win_e_off" if ok else "win_e_fail_off")
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
            self, _("dlg_initial_dir_title"),
            self.theme_manager.get("initial_dir", "C:\\"))
        if path:
            self.theme_manager.set("initial_dir", path)
            self.navigate(path)
            self.statusBar().showMessage(
                _("initial_dir_set").format(path=path), 3000)

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
        if self.search_panel is not None:
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
        self.disk_label.setText(_("drive_free").format(
            drive=drive, free=_human_size(free), total=_human_size(total)))

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
            self, _("dlg_open_fail"),
            _("dlg_open_fail_msg").format(path=path, err=err))

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
        self.selection_label.setText(_("sel_n").format(n=self._sel_count))
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
        if total:
            text = _("sel_n_size").format(
                n=self._sel_count, size=_human_size(total))
        else:
            text = _("sel_n").format(n=self._sel_count)
        self.selection_label.setText(text)

    # ---- コンテキストメニュー ----
    def _show_context_menu(self, pos) -> None:
        # 右ドラッグ直後（0.6秒以内）に来る右クリックメニューは抑止する。
        # CustomContextMenu 経由でここに届くため contextMenuEvent では拾えない。
        import time
        src = self.sender()
        if src is not None and (
                time.monotonic() - getattr(src, "_rdrag_release_time", 0.0)
                < 0.6):
            return
        paths = self.selected_paths()
        gpos = self._active_view().viewport().mapToGlobal(pos)
        from app import shell_menu
        # 選択あり: Fibro固有＋シェル拡張(7-Zip等)の統合メニューを表示。
        if paths:
            if shell_menu.is_supported():
                items, callables = self._build_combined_items(paths)
                ok, key = shell_menu.show_combined_menu(
                    int(self.winId()), paths, gpos.x(), gpos.y(), items)
                if ok:
                    if key and key in callables:
                        callables[key]()
                    return  # シェル項目実行/キャンセルも含めここで終了
        # 選択なし（空白）: Windows 本物の「新規作成」＋Fibro追加項目の統合メニュー。
        elif shell_menu.is_supported():
            before = self._dir_snapshot()
            items, callables = self._build_new_menu_items()
            ok, key = shell_menu.show_new_menu(
                int(self.winId()), str(self.current_path),
                gpos.x(), gpos.y(), items)
            if ok:
                if key == "__created__":
                    QTimer.singleShot(
                        0, lambda b=before: self._select_new_file(b))
                elif key and key in callables:
                    callables[key]()
                return
        # 非対応 or 失敗 → Fibro 自前メニュー（shellnew レジストリ版を含む）
        menu = self._build_fibro_menu(paths)
        if menu.actions():
            menu.exec(gpos)

    def _build_combined_items(self, paths: list[str]):
        """統合メニューに足す Fibro 項目（「お気に入りに追加」のみ）と key→callable。

        シェルの「プロパティ」の直上に挿入される。フォルダ選択時のみ。
        """
        callables: dict = {}
        items: list = []
        dirs = [p for p in paths if Path(p).is_dir()]
        fav_target = dirs[0] if dirs else None
        if fav_target:
            callables["fav"] = lambda: self.favorites.add_favorite(fav_target)
            items.append({"type": "action", "key": "fav",
                          "label": _("ctx_add_fav")})
        return items, callables

    def _build_new_menu_items(self):
        """空白右クリックの統合メニューに足す Fibro 項目（New はシェル側が出す）。

        フォルダー/各ファイル型は CNewMenu が出すため Fibro 側には入れない。
        貼り付け・お気に入りに追加・雛形のみ。戻り値 (items, key→callable)。
        """
        from app import clipboard_files
        callables: dict = {}
        items: list = []
        if clipboard_files.has_files():
            callables["paste"] = self.paste_clipboard
            items.append({"type": "action", "key": "paste",
                          "label": _("ctx_paste")})
        cur = self.current_path
        if cur:
            callables["fav"] = lambda c=cur: self.favorites.add_favorite(c)
            items.append({"type": "action", "key": "fav",
                          "label": _("ctx_add_fav")})
        # config/templates/ の雛形
        try:
            templates = sorted(
                p for p in self._template_dir().iterdir() if p.is_file())
        except OSError:
            templates = []
        for idx, tpl in enumerate(templates):
            key = f"tpl{idx}"
            callables[key] = lambda t=tpl: self._new_from_template(t)
            items.append({"type": "action", "key": key,
                          "label": _("tpl_label").format(name=tpl.name)})
        return items, callables

    def _dir_snapshot(self) -> set:
        """カレント直下の名前集合（作成直後の新規ファイル検出用）。"""
        try:
            return set(os.listdir(self.current_path))
        except OSError:
            return set()

    def _select_new_file(self, before: set) -> None:
        """before に無い新規エントリを選択（シェル New 作成直後のハイライト）。"""
        after = self._dir_snapshot()
        new = sorted(after - before)
        if new:
            self._select_by_name(new[0])

    def _build_fibro_menu(self, paths: list[str]) -> QMenu:
        """Fibro 自前の QMenu（統合メニュー非対応/失敗時・空白時のフォールバック）。"""
        from app import clipboard_files
        menu = QMenu(self)
        if paths:
            menu.addAction(_("ctx_open"), lambda: self._open_paths(paths))
            self._add_open_with_menu(menu, paths)
            menu.addSeparator()
            if len(paths) == 1:
                menu.addAction(_("ctx_rename"), self.rename_single)
            menu.addAction(_("ctx_batch_rename"), self.open_rename_dialog)
            menu.addSeparator()
            menu.addAction(_("ctx_copy"), lambda: self._set_clipboard("copy"))
            menu.addAction(_("ctx_cut"), lambda: self._set_clipboard("cut"))
        if clipboard_files.has_files():
            menu.addAction(_("ctx_paste"), self.paste_clipboard)
        new_menu = menu.addMenu(_("ctx_new"))
        new_menu.addAction(_("menu_new_folder"), self.new_folder)
        # Windows の ShellNew 登録型を展開。空（取得不可）なら従来の固定行。
        if not self._add_shellnew_actions(new_menu):
            new_menu.addAction(_("menu_new_text"), self.new_text_file)
        self._add_template_actions(new_menu)
        if paths:
            menu.addSeparator()
            menu.addAction(_("ctx_copy_path"),
                           lambda: self._copy_paths_to_clipboard(paths))
            menu.addSeparator()
            menu.addAction(_("ctx_trash"), self.delete_selected)
            menu.addAction(_("ctx_delete"),
                           self.delete_selected_permanent)
            menu.addSeparator()
            dirs = [p for p in paths if Path(p).is_dir()]
            fav_target = dirs[0] if dirs else None
        else:
            fav_target = self.current_path
        if fav_target:
            menu.addAction(_("ctx_add_fav"),
                           lambda: self.favorites.add_favorite(fav_target))
        if paths:
            menu.addAction(_("ctx_properties"), self.show_properties)
        return menu

    def _show_shell_menu(self, paths: list[str], gpos) -> bool:
        """選択ファイルの Windows シェルコンテキストメニューを表示。成功で True。"""
        from app import shell_menu
        ok = shell_menu.show_shell_context_menu(
            int(self.winId()), paths, gpos.x(), gpos.y())
        if not ok:
            self.statusBar().showMessage(_("shell_menu_fail"), 3000)
        return ok

    def _active_view(self):
        """アクティブペインの現在表示中ビュー（詳細 or サムネ）。"""
        pane = self._active_pane
        return pane.icon_view if pane.view_mode == "thumbnails" else pane.table

    def _add_open_with_menu(self, menu: QMenu, paths: list[str]) -> None:
        """「ここで開く」系（ターミナル/PowerShell/VS Code/場所）。"""
        dirs = [p for p in paths if Path(p).is_dir()]
        # フォルダ選択時はそのフォルダ、ファイル選択時は親フォルダを対象に
        target = dirs[0] if dirs else str(Path(paths[0]).parent)
        sub = menu.addMenu(_("ctx_open_with"))
        sub.addAction(_("ctx_terminal"),
                      lambda: self._open_terminal(target))
        sub.addAction("PowerShell",
                      lambda: self._open_in(["powershell.exe"], target))
        sub.addAction("VS Code",
                      lambda: self._open_in(["code"], target, shell=True))
        sub.addAction(_("ctx_explorer"),
                      lambda: self._open_in(["explorer.exe"], target))

    def _open_terminal(self, directory: str) -> None:
        """Windows Terminal があれば優先、なければ cmd で開く。"""
        try:
            subprocess.Popen(["wt.exe", "-d", directory])  # noqa: S603,S607
        except OSError:
            try:
                subprocess.Popen(["cmd.exe"], cwd=directory)  # noqa: S603,S607
            except OSError as e:
                QMessageBox.warning(self, _("dlg_terminal_fail"),
                                    _("dlg_terminal_fail_msg").format(err=e))

    def _open_in(self, cmd: list[str], directory: str, shell: bool = False) -> None:
        try:
            if cmd[-1] == "code":  # VS Code は対象パスを引数で渡す
                subprocess.Popen([*cmd, directory], shell=shell)  # noqa: S603
            else:
                subprocess.Popen([*cmd, directory], cwd=directory, shell=shell)  # noqa: S603
        except OSError as e:
            QMessageBox.warning(self, _("dlg_open_app_fail"),
                                _("dlg_terminal_fail_msg").format(err=e))

    def _copy_paths_to_clipboard(self, paths: list[str]) -> None:
        clip = QApplication.clipboard()
        if clip:
            clip.setText("\n".join(paths))
            self.statusBar().showMessage(
                _("copied_n").format(n=len(paths)), 3000)

    def _open_selected(self) -> None:
        """Enter キー: 選択中の項目を開く/実行（ダブルクリックと同等）。"""
        paths = self.selected_paths()
        if paths:
            self._open_paths(paths)

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
            # システムクリップボードへ（エクスプローラーと相互運用）
            from app import clipboard_files
            clipboard_files.set_files(paths, move=(mode == "cut"))
            self._clipboard = (mode, paths)  # 後方互換（内部参照用）
            verb = _("clip_copied" if mode == "copy" else "clip_cut")
            self.statusBar().showMessage(
                _("clipped_n").format(n=len(paths), verb=verb), 3000)

    def paste_clipboard(self) -> None:
        # システムクリップボードから読む（エクスプローラーでコピーした物も貼れる）
        from app import clipboard_files
        got = clipboard_files.get_files()
        if got is None:
            return
        paths, move = got
        from app.gui.conflict_dialog import make_resolver
        resolver = make_resolver(self)
        try:
            if move:
                self.file_ops.move(paths, self.current_path, resolver=resolver)
                clipboard_files.clear()
                self._clipboard = None
            else:
                self.file_ops.copy(paths, self.current_path, resolver=resolver)
        except OSError as e:
            QMessageBox.critical(self, _("dlg_paste_fail"), str(e))
            return
        self.statusBar().showMessage(_("pasted_n").format(n=len(paths)), 3000)

    def _on_files_dropped(self, paths: list, dest: str, copy: bool) -> None:
        """D&D: 既定は移動、Ctrl押下でコピー。どちらも Undo 可。"""
        from app.gui.conflict_dialog import make_resolver
        resolver = make_resolver(self)
        try:
            if copy:
                # 同フォルダへのコピーは「複製」なので衝突ダイアログを出さず
                # 自動で (2) 付きの名前にする。別フォルダは従来の衝突解決。
                dest_norm = str(Path(dest))
                same = [p for p in paths
                        if str(Path(p).parent) == dest_norm]
                cross = [p for p in paths
                         if str(Path(p).parent) != dest_norm]
                if same:
                    self.file_ops.copy(same, dest, resolver=None)
                if cross:
                    self.file_ops.copy(cross, dest, resolver=resolver)
            else:
                self.file_ops.move(paths, dest, resolver=resolver)
        except OSError as e:
            QMessageBox.critical(self, _("dlg_drop_fail"), str(e))
            return
        verb = _("kind_copy" if copy else "kind_move")
        self.statusBar().showMessage(
            _("moved_n").format(
                n=len(paths), dest=Path(dest).name or dest, verb=verb), 5000)

    def _on_native_drop(self, paths: list, dest: str, gx: int, gy: int) -> None:
        """右ドラッグ: Windows ネイティブの「ここに解凍/コピー/移動…」を表示。

        未対応・失敗時は Fibro の簡易メニュー（ここにコピー/移動）へフォールバック。
        """
        from app import shell_menu
        if shell_menu.is_supported() and shell_menu.show_drag_drop_menu(
                int(self.winId()), paths, dest, gx, gy):
            return
        # フォールバック: 自前メニュー（Undo 可能な既存操作を流用）
        from PySide6.QtCore import QPoint
        menu = QMenu(self)
        name = Path(dest).name or dest
        menu.addAction(_("ctx_here_copy").format(name=name),
                       lambda: self._on_files_dropped(paths, dest, True))
        menu.addAction(_("ctx_here_move").format(name=name),
                       lambda: self._on_files_dropped(paths, dest, False))
        menu.addSeparator()
        menu.addAction(_("ctx_cancel"))
        menu.exec(QPoint(gx, gy))

    def delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        names = "\n".join(Path(p).name for p in paths[:10])
        if len(paths) > 10:
            names += f"\n… 他{len(paths) - 10}件"
        answer = QMessageBox.question(
            self, _("dlg_trash_title"),
            _("dlg_trash_msg").format(n=len(paths), names=names))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.file_ops.delete(paths)
            self.statusBar().showMessage(
                _("trashed_n").format(n=len(paths)), 3000)
        except OSError as e:
            QMessageBox.critical(self, _("dlg_trash_fail"), str(e))

    def delete_selected_permanent(self) -> None:
        """Shift+Delete: ゴミ箱を経由せず完全削除（元に戻せない）。"""
        paths = self.selected_paths()
        if not paths:
            return
        names = "\n".join(Path(p).name for p in paths[:10])
        if len(paths) > 10:
            names += f"\n… 他{len(paths) - 10}件"
        answer = QMessageBox.warning(
            self, _("dlg_delete_title"),
            _("dlg_delete_msg").format(n=len(paths), names=names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self.file_ops.delete_permanent(paths)
            self.statusBar().showMessage(_("deleted_n").format(n=n), 3000)
        except OSError as e:
            QMessageBox.critical(self, _("dlg_delete_fail"), str(e))

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
            self, _("dlg_new_folder_title"), _("dlg_new_folder_label"),
            text=_("dlg_new_folder_default"))
        name = name.strip()
        if not ok or not name:
            return
        target = self._unique_path(name)
        try:
            target.mkdir()
        except OSError as e:
            QMessageBox.critical(self, _("dlg_create_fail"), str(e))
            return
        # QFileSystemModel はカレントを監視しており作成は自動で一覧へ反映される。
        # 重い全再スキャン（refresh の setRootPath 往復）は避け、反映後に選択する。
        QTimer.singleShot(0, lambda n=target.name: self._select_by_name(n))

    def new_text_file(self) -> None:
        name, ok = QInputDialog.getText(
            self, _("dlg_new_text_title"), _("dlg_new_text_label"),
            text=_("dlg_new_text_default"))
        name = name.strip()
        if not ok or not name:
            return
        self._create_file(name, "")

    def _create_file(self, name: str, content: str) -> None:
        target = self._unique_path(name)
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, _("dlg_create_fail"), str(e))
            return
        # 監視中のモデルが自動反映するため全再スキャンは不要。反映後に選択。
        QTimer.singleShot(0, lambda n=target.name: self._select_by_name(n))

    def _add_shellnew_actions(self, menu: QMenu) -> bool:
        """Windows の ShellNew 登録型をメニューに展開。1件でも出せたら True。"""
        from app import shellnew
        items = shellnew.list_new_items()
        if not items:
            return False
        provider = QFileIconProvider()
        for item in items:
            action = menu.addAction(
                item["label"],
                lambda checked=False, it=item: self._new_shellnew(it))
            icon = provider.icon(QFileInfo(f"x{item['ext']}"))
            if not icon.isNull():
                action.setIcon(icon)
        return True

    def _new_shellnew(self, item: dict) -> None:
        """ShellNew 型を Windows 風に「新しい <型名>.<拡張子>」で即作成し選択。"""
        from app import shellnew
        target = self._unique_path(f"新しい {item['label']}{item['ext']}")
        try:
            shellnew.create_item(item, str(self.current_path), target.stem)
        except OSError as e:
            QMessageBox.critical(self, _("dlg_create_fail"), str(e))
            return
        QTimer.singleShot(0, lambda n=target.name: self._select_by_name(n))

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
                _("tpl_label").format(name=tpl.name),
                lambda checked=False, t=tpl: self._new_from_template(t))

    def _new_from_template(self, template: Path) -> None:
        name, ok = QInputDialog.getText(
            self, _("dlg_new_tpl_title"), _("dlg_new_tpl_label"),
            text=template.name)
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
            self, _("dlg_pattern_title"), _("dlg_pattern_label"), text="*.*")
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
        self.statusBar().showMessage(_("selected_n").format(n=matched), 3000)

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
            self, _("dlg_initial_dir_title"), self.theme_manager.get("initial_dir", "C:\\"))
        if path:
            self.theme_manager.set("initial_dir", path)
            self.navigate(path)
            self.statusBar().showMessage(_("initial_dir_set").format(path=path), 3000)

    # ---- その他操作 ----
    def refresh(self) -> None:
        """一覧を再読み込み（モデルのキャッシュを更新）。"""
        path = self.current_path
        self.list_model.setRootPath("")
        self.list_model.setRootPath(path)
        self._active_pane.set_root_index(
            self.proxy.mapFromSource(self.list_model.index(path)))

    def _is_dark(self) -> bool:
        """現在テーマがダークかどうか。"""
        return self.theme_manager.theme == "dark"

    def _show_help_menu(self) -> None:
        """? ボタン: ヘルプ項目をポップアップメニューで表示。"""
        menu = QMenu(self)
        menu.addAction(_("menu_report_bug"), self._report_bug)
        menu.addSeparator()
        menu.addAction(_("menu_about"), self._show_about)
        pos = self.help_btn.mapToGlobal(self.help_btn.rect().bottomLeft())
        menu.exec(pos)

    def _show_shortcuts(self) -> None:
        """⌘ ボタン: ショートカット一覧を参照専用ポップアップで表示。"""
        dialog = QDialog(self)
        dialog.setWindowTitle(_("shortcuts_title"))
        # 枠（タイトルバー＋×ボタン）なし。設定／ヘルプのメニューと同様に
        # 外側クリックで自動的に閉じる。
        dialog.setWindowFlags(Qt.WindowType.Popup)
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        for category, items in SHORTCUTS:
            heading = QLabel(_(category))
            heading.setStyleSheet("font-weight: 600;")
            outer.addWidget(heading)
            grid = QGridLayout()
            grid.setContentsMargins(8, 0, 0, 0)
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(3)
            for row, (key, label) in enumerate(items):
                key_label = QLabel(key)
                key_label.setStyleSheet("color: palette(highlight);")
                grid.addWidget(key_label, row, 0)
                grid.addWidget(QLabel(_(label)), row, 1)
            grid.setColumnStretch(1, 1)
            outer.addLayout(grid)

        # ボタン直下に配置
        btn_pos = self.shortcuts_btn.mapToGlobal(
            self.shortcuts_btn.rect().bottomLeft())
        dialog.adjustSize()
        dialog.move(btn_pos.x() - dialog.width()
                    + self.shortcuts_btn.width(), btn_pos.y() + 4)
        dialog.show()

    def toggle_theme(self) -> None:
        app = QApplication.instance()
        if app:
            theme = self.theme_manager.toggle(app)
            self.statusBar().showMessage(
                _("theme_dark" if theme == "dark" else "theme_light"), 3000)
            self.favorites.refresh()
            self.recent_sidebar.refresh()
            self.settings_btn.setIcon(
                material_icon("settings", dark=self._is_dark()))
            self.shortcuts_btn.setIcon(
                material_icon("keyboard_command_key", dark=self._is_dark()))
            self.help_btn.setIcon(material_icon("help", dark=self._is_dark()))

    def rename_single(self) -> None:
        """F2: 選択中1件をその場でリネーム（RenameExecutor 経由で Undo 可）。"""
        paths = self.selected_paths()
        if len(paths) != 1:
            if len(paths) > 1:
                self.open_rename_dialog()
            return
        old_name = Path(paths[0]).name
        new_name, ok = SingleRenameDialog.get_new_name(
            old_name, Path(paths[0]).is_dir(), self)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        if (Path(self.current_path) / new_name).exists():
            QMessageBox.warning(self, _("rename_title"),
                                _("dlg_rename_exists").format(name=new_name))
            return
        try:
            self.rename_executor.execute(
                self.current_path, [(old_name, new_name)])
            self.statusBar().showMessage(
                _("single_rename_done").format(old=old_name, new=new_name), 5000)
        except OSError as e:
            QMessageBox.critical(self, _("dlg_rename_fail"), str(e))

    # ---- 検索 ----
    def _ensure_search_panel(self):
        """検索ドックを遅延生成（初回 Ctrl+F 時）。"""
        if self.search_panel is None:
            from app.gui.search_panel import SearchPanel
            self.search_panel = SearchPanel(settings=self.theme_manager)
            self.search_panel.file_selected.connect(
                self._on_search_file_selected)
            self.search_dock = QDockWidget(_("search_title"), self)
            self.search_dock.setWidget(self.search_panel)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                               self.search_dock)
            self.search_dock.hide()
        return self.search_panel

    def toggle_search(self) -> None:
        self._ensure_search_panel()
        sp = self._main_splitter
        left_w = sp.sizes()[0]

        if self.search_dock.isVisible():
            self.search_dock.hide()
        else:
            self.search_panel.set_root(self.current_path)
            self.search_dock.show()
            self.search_panel.keyword_edit.setFocus()

        def _keep_left():
            total = sp.size().width()
            right_w = max(total - left_w - sp.handleWidth(), 1)
            sp.setSizes([left_w, right_w])
        QTimer.singleShot(0, _keep_left)

    def _restore_layout(self) -> None:
        layout = self.theme_manager.get("layout", {})
        if not isinstance(layout, dict):
            return
        if sizes := layout.get("main_splitter"):
            self._main_splitter.setSizes([int(s) for s in sizes])
        if sizes := layout.get("left_splitter"):
            self._left_splitter.setSizes([int(s) for s in sizes])
        if collapsed := layout.get("left_collapsed"):
            sections = (self.fav_section, self.recent_section,
                        self.tree_section)
            for sec, c in zip(sections, collapsed):
                sec.set_collapsed(bool(c))
        if sizes := layout.get("pane_splitter"):
            self.pane_splitter.setSizes([int(s) for s in sizes])
        if geo := layout.get("window"):
            try:
                self.setGeometry(*[int(v) for v in geo])
            except (TypeError, ValueError):
                pass

    def _align_section_headers(self) -> None:
        """サイドバー見出しの高さをタブ行の実高さに合わせる。"""
        h = self.tab_bar.sizeHint().height()
        if h <= 0:
            return
        for sec in (self.fav_section, self.recent_section, self.tree_section):
            sec.set_header_height(h)

    def _save_layout(self) -> None:
        g = self.geometry()
        self.theme_manager.set("layout", {
            "main_splitter": self._main_splitter.sizes(),
            "left_splitter": self._left_splitter.sizes(),
            "pane_splitter": self.pane_splitter.sizes(),
            "left_collapsed": [
                self.fav_section.is_collapsed(),
                self.recent_section.is_collapsed(),
                self.tree_section.is_collapsed(),
            ],
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
        if self.search_panel is not None:
            self.search_panel.cancel_search()
        super().closeEvent(event)

    # ---- リネーム / Undo ----
    def open_rename_dialog(self) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(
                self, _("dlg_rename_select"), _("dlg_rename_select_msg"))
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
            self.statusBar().showMessage(_("rename_done"), 5000)

    def undo_last(self) -> None:
        if self.rename_executor.can_undo:
            try:
                record = self.rename_executor.undo()
                self.statusBar().showMessage(
                    _("undo_rename_done").format(n=len(record.mapping)), 3000)
            except (OSError, RuntimeError) as e:
                QMessageBox.critical(self, _("undo_failed"), str(e))
        elif self.file_ops.can_undo:
            try:
                record = self.file_ops.undo()
                kind = _("kind_move" if record.kind == "move" else "kind_copy")
                self.statusBar().showMessage(
                    _("undo_move_done").format(
                        kind=kind, n=len(record.pairs)), 3000)
            except (OSError, RuntimeError) as e:
                QMessageBox.critical(self, _("undo_failed"), str(e))
        else:
            self.statusBar().showMessage(_("undo_none"), 3000)
