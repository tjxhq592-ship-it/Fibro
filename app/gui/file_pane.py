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
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QFileSystemModel, QMenu, QStackedLayout, QStyle,
    QStyledItemDelegate, QTableView, QWidget,
)

from app.gui.async_icons import shared_icon_provider
from app.gui.dnd_views import FileIconView, FileTableView
from app.gui.thumbnails import ThumbnailDelegate
from app.i18n import _


# 一覧の列見出し（列インデックス → i18n キー）。
# 表示時に _() で解決する（モジュール読込時に評価すると言語適用前になるため）。
# キーは QFileSystemModel の列インデックス（0=名前 1=サイズ 2=種類 3=更新日時）。
_COLUMN_KEYS = {
    0: "col_name",
    1: "col_size",
    2: "col_type",
    3: "col_modified",
}


class AccentBarDelegate(QStyledItemDelegate):
    """選択行の左端に縦のアクセントバーを描く（詳細ビューの0列目専用）。"""

    BAR_WIDTH = 3

    def paint(self, painter, option, index):  # noqa: N802 — Qt API
        super().paint(painter, option, index)
        if not (option.state & QStyle.StateFlag.State_Selected):
            return
        accent = option.palette.color(QPalette.ColorRole.Highlight)
        r = option.rect
        painter.save()
        painter.fillRect(r.left(), r.top(), self.BAR_WIDTH, r.height(), accent)
        painter.restore()


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
        # QFileSystemModel.filePath() が返すスラッシュ区切りの正規形で保持し、
        # filterAcceptsRow 内の model.filePath(parent) と文字列直接比較できる
        # ようにする（行ごとの Path オブジェクト生成を排除）。
        src = self.sourceModel()
        if src is not None:
            idx = src.index(path)
            self._root_path = (src.filePath(idx) if idx.isValid()
                               else path.replace("\\", "/"))
        else:
            self._root_path = path.replace("\\", "/")
        self.invalidateRowsFilter()

    def set_needle(self, text: str) -> None:
        self._needle = text.lower()
        self.invalidateRowsFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        # filePath は set_root_path と同じスラッシュ区切りの正規形。行ごとの
        # Path 生成を挟まず文字列直接比較で「カレント直下か」を判定する。
        if model.filePath(parent) != self._root_path:
            return True  # カレント直下以外（祖先チェーン等）は素通し
        name = model.index(row, 0, parent).data() or ""
        return self._needle in str(name).lower()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        """列見出しを日本語表示にする（横方向の表示テキストのみ差し替え）。"""
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole
                and section in _COLUMN_KEYS):
            return _(_COLUMN_KEYS[section])
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        """0列目のアイコンは遅延プロバイダの生キャッシュから直接返す。

        QFileSystemModel は gatherer が取得した時点のアイコンを内部キャッシュし、
        後から再問い合わせしない。そのため拡張子別アイコンを遅延解決しても、
        モデル経由（既定 data）では汎用アイコンのまま固定されてしまう。
        ここで DecorationRole を横取りし、解決済みなら実アイコン・未解決なら
        汎用＋解決スケジュールを返す。ビューは再描画のたびに data() を引くので、
        provider.ready→viewport.update() で解決後のアイコンへ差し替わる。
        """
        if (role == Qt.ItemDataRole.DecorationRole
                and index.isValid() and index.column() == 0):
            src = self.mapToSource(index)
            info = self.sourceModel().fileInfo(src)
            return shared_icon_provider().icon(info)
        return super().data(index, role)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        """サイズ・更新日時は表示文字列でなく実値で比較する。

        既定の QSortFilterProxyModel は DisplayRole 文字列で並べるため、
        サイズ（"721 KiB" vs "2.79 GiB"）や更新日時（時刻がゼロ埋めされず
        "8:02" と "10:15" が逆転）で時系列・大小がずれる。比較のたびに
        QFileInfo を2つ生成すると population 中のソートが重くなるため、
        QFileSystemModel の直接 API（size/lastModified）で実値を取り出す。
        """
        model = self.sourceModel()
        col = left.column()
        if col == 1:  # サイズ
            return model.size(left) < model.size(right)
        if col == 3:  # 更新日時
            return model.lastModified(left) < model.lastModified(right)
        return super().lessThan(left, right)


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
        # ファイルアイコンは拡張子単位で遅延解決し、gatherer スレッドの
        # シェル問い合わせによる初回フリーズを避ける（完成後に差分で再描画）。
        self.list_model.setIconProvider(shared_icon_provider())

        self.proxy = CurrentDirFilterProxy(self)
        self.proxy.setSourceModel(self.list_model)

        # 詳細ビュー（QTableView）
        self.table = FileTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(18, 18))  # 16: 種別アイコンを見やすく
        self._accent_delegate = AccentBarDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self._accent_delegate)
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

        # アクティブ枠表示用に 2px のマージンを確保（枠は paintEvent で描く）。
        # スタイルシートは使わない（テーマのパレットを壊さないため）。
        self._active_border = None  # None=枠なし / True=アクティブ / False=非アクティブ
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(2, 2, 2, 2)
        self._stack.addWidget(self.table)       # index 0 = details
        self._stack.addWidget(self.icon_view)   # index 1 = thumbnails

        # 遅延アイコンが解決したら両ビューを再描画（viewport は QObject なので
        # ペイン破棄時に Qt が自動で接続解除する）。
        provider = shared_icon_provider()
        provider.signals.ready.connect(self.table.viewport().update)
        provider.signals.ready.connect(self.icon_view.viewport().update)

        # フォーカス/クリックでアクティブ通知（両ビュー）
        for view in (self.table, self.icon_view):
            view.installEventFilter(self)
            view.viewport().installEventFilter(self)

    def set_active_border(self, state) -> None:
        """アクティブ枠の表示。None=なし / True=アクティブ / False=非アクティブ。"""
        if self._active_border != state:
            self._active_border = state
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().paintEvent(event)
        if self._active_border is None:
            return
        from PySide6.QtGui import QColor, QPainter, QPalette, QPen
        painter = QPainter(self)
        if self._active_border:
            from app.gui.theme import current_accent
            pen = QPen(current_accent(), 2)
        else:
            pen = QPen(self.palette().color(QPalette.ColorRole.Mid), 1)
        painter.setPen(pen)
        # 枠線が切れないよう内側に 1px インセットして矩形を描く
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

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

    def begin_population(self) -> None:
        """population 開始前に動的ソート/フィルタを停止する。

        QFileSystemModel は100件ごとに rowsInserted を発する。動的ソートが
        有効なままだと挿入のたびに逐次ソートが走り、巨大フォルダで GUI
        スレッドを長時間占有する。ここで止め、directoryLoaded 後に
        finish_population() で一括ソートへ切り替える。
        """
        self.proxy.setDynamicSortFilter(False)

    def finish_population(self) -> None:
        """population 完了後に一括ソートし、動的ソート/フィルタを再開する。

        ソート列・順序はヘッダのソートインジケータ（ユーザーのクリックで
        変わる現在の並び）から取得する。再開後のファイル増減は動的に反映される。
        """
        header = self.table.horizontalHeader()
        col = header.sortIndicatorSection()
        order = header.sortIndicatorOrder()
        self.proxy.sort(col, order)
        self.proxy.setDynamicSortFilter(True)

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = "thumbnails" if mode == "thumbnails" else "details"
        self._stack.setCurrentIndex(1 if self.view_mode == "thumbnails" else 0)

    def _show_header_menu(self, pos) -> None:
        """18: 列の表示/非表示トグル（名前列0は常時表示）。"""
        menu = QMenu(self)
        for col in range(1, self.list_model.columnCount()):
            label = self.proxy.headerData(
                col, Qt.Orientation.Horizontal) or _("col_n").format(n=col)
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
