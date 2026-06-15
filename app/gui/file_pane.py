"""中央のファイル一覧1枚分を自己完結させるペイン。

タブ／デュアルペインのために、QFileSystemModel + フィルタプロキシ +
ファイル一覧テーブル + ナビ履歴を1ウィジェットへまとめる。ナビ/操作ロジックは
MainWindow 側に残し、ここは薄い容器に徹する（アクティブペインへ委譲する設計）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QDir, QEvent, QModelIndex, QSize, QSortFilterProxyModel, Qt, Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QFileSystemModel, QMenu, QStackedLayout, QTableView,
    QWidget,
)

from app.gui.dnd_views import FileIconView, FileTableView
from app.gui.thumbnails import ThumbnailDelegate


class CurrentDirFilterProxy(QSortFilterProxyModel):
    """カレント直下のみ名前で絞り込むフィルタ（簡易フィルタボックス用）。

    祖先チェーンや非カレント階層は素通しして、フィルタが効くのは
    現在表示中のフォルダ直下だけにする。
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


class FilePane(QWidget):
    """ファイル一覧1枚（モデル・プロキシ・テーブル・履歴）を内包するペイン。"""

    activated = Signal(object)  # フォーカス/クリックで自身を emit

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.history: list[str] = []
        self.history_pos: int = -1
        self.filter_text: str = ""
        self.view_mode: str = "details"  # "details" | "thumbnails"

        self.list_model = QFileSystemModel(self)
        self.list_model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self.list_model.setReadOnly(True)
        # OneDrive 等でフォルダごとのカスタムアイコン取得（シェル問い合わせ）が
        # 遅く固まる一因になるため無効化（通常の種別アイコンは維持）。
        self.list_model.setOption(
            QFileSystemModel.Option.DontUseCustomDirectoryIcons, True)

        self.proxy = CurrentDirFilterProxy(self)
        self.proxy.setSourceModel(self.list_model)

        # 詳細ビュー（QTableView）
        self.table = FileTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(18, 18))  # 16: 種別アイコンを見やすく
        header = self.table.horizontalHeader()
        header.resizeSection(0, 280)
        header.setSectionsMovable(True)  # 18: 列の並べ替え
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        self.header = header

        # サムネイルビュー（QListView, IconMode）— 同じモデル・同じ選択を共有
        self.icon_view = FileIconView()
        self.icon_view.setModel(self.proxy)
        self.icon_view.setSelectionModel(self.table.selectionModel())
        self.icon_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.icon_view.setSelectionRectVisible(True)  # 20: ラバーバンド可視化
        self.icon_view.setIconSize(QSize(96, 96))
        self.icon_view.setGridSize(QSize(120, 132))
        self._thumb_delegate = ThumbnailDelegate(self._path_of_index, size=96)
        self.icon_view.setItemDelegate(self._thumb_delegate)
        # サムネが非同期で完成したらアイコンビューを再描画（生成中の仮表示を更新）
        from app.gui.thumbnails import shared_loader
        shared_loader().ready.connect(self.icon_view.viewport().update)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.table)       # index 0 = details
        self._stack.addWidget(self.icon_view)   # index 1 = thumbnails

        # フォーカス/クリックでアクティブ通知（両ビュー）
        for view in (self.table, self.icon_view):
            view.installEventFilter(self)
            view.viewport().installEventFilter(self)

    def _path_of_index(self, proxy_index: QModelIndex) -> str:
        """プロキシ index → ファイルパス（サムネデリゲート用）。"""
        if not proxy_index.isValid():
            return ""
        return self.list_model.filePath(self.proxy.mapToSource(proxy_index))

    @property
    def current_path(self) -> str:
        return str(Path(self.list_model.rootPath()))

    def set_root_index(self, proxy_index: QModelIndex) -> None:
        """詳細・サムネ両ビューのルートを同期する。"""
        self.table.setRootIndex(proxy_index)
        self.icon_view.setRootIndex(proxy_index)

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = "thumbnails" if mode == "thumbnails" else "details"
        self._stack.setCurrentIndex(1 if self.view_mode == "thumbnails" else 0)

    def _show_header_menu(self, pos) -> None:
        """18: 列の表示/非表示トグル（名前列0は常時表示）。"""
        menu = QMenu(self)
        for col in range(1, self.list_model.columnCount()):
            label = self.list_model.headerData(
                col, Qt.Orientation.Horizontal) or f"列{col}"
            action = menu.addAction(str(label))
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(col))
            action.toggled.connect(
                lambda checked, c=col: self.table.setColumnHidden(c, not checked))
        menu.exec(self.header.mapToGlobal(pos))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            self.activated.emit(self)
        return super().eventFilter(obj, event)
