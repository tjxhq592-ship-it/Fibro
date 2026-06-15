"""ドラッグ&ドロップ対応のビュー。

一覧/ツリーからフォルダへファイルをドロップで移動、Ctrl押下でコピー。
実処理は files_dropped シグナル経由で FileOps に委譲する（Undo 対応のため
Qt 内蔵の moveEvent には任せない）。text/uri-list を使うので標準
エクスプローラーとの相互ドラッグも可能。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QMimeData, QModelIndex, QSortFilterProxyModel, Qt, QUrl, Signal,
)
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QListView, QTableView, QTreeView,
)


def _file_path(model, index: QModelIndex) -> str:
    """プロキシ越しでも QFileSystemModel のパスを取り出す。"""
    if isinstance(model, QSortFilterProxyModel):
        return model.sourceModel().filePath(model.mapToSource(index))
    return model.filePath(index)


def _urls_to_paths(mime: QMimeData) -> list[str]:
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


class _DropMixin:
    """フォルダへのドロップを files_dropped(paths, dest, copy) に変換する。"""

    _drag_source = False  # ドラッグ元ビュー（一覧/サムネ）のみ True

    def _dest_dir_for(self, index: QModelIndex) -> str | None:
        raise NotImplementedError

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        """サイドボタンで履歴ナビ＋左ボタンはドラッグ/ラバーバンドを切替。"""
        button = event.button()
        if button == Qt.MouseButton.BackButton:
            self.mouse_nav.emit(False)
            event.accept()
            return
        if button == Qt.MouseButton.ForwardButton:
            self.mouse_nav.emit(True)
            event.accept()
            return
        if self._drag_source and button == Qt.MouseButton.LeftButton:
            # Explorer 流: 選択済みアイテムを押した時だけドラッグ移動を許可。
            # 空白／未選択アイテムを押した時はドラッグを無効化して、
            # ラバーバンド（範囲）選択を開始できるようにする。
            index = self.indexAt(event.position().toPoint())
            on_selected = (index.isValid()
                           and self.selectionModel() is not None
                           and self.selectionModel().isSelected(index))
            self.setDragEnabled(bool(on_selected))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt API
        """ドラッグ可否を既定（有効）へ戻す。"""
        super().mouseReleaseEvent(event)
        if self._drag_source and event.button() == Qt.MouseButton.LeftButton:
            self.setDragEnabled(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            return super().dropEvent(event)
        dest = self._dest_dir_for(self.indexAt(event.position().toPoint()))
        paths = _urls_to_paths(event.mimeData())
        if not dest or not paths:
            event.ignore()
            return
        # 自分自身や親フォルダへのドロップは無視
        paths = [p for p in paths
                 if Path(p) != Path(dest) and str(Path(p).parent) != dest]
        if not paths:
            event.ignore()
            return
        copy = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        event.acceptProposedAction()
        self.files_dropped.emit(paths, dest, copy)


class FileTableView(_DropMixin, QTableView):
    """ファイル一覧。ドラッグ元かつ（フォルダ行への）ドロップ先。"""

    files_dropped = Signal(list, str, bool)  # paths, dest_dir, copy
    mouse_nav = Signal(bool)  # True=進む / False=戻る
    preview_requested = Signal()  # Space キーでクイックプレビュー
    _drag_source = True

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def _dest_dir_for(self, index: QModelIndex) -> str | None:
        model = self.model()
        if index.isValid():
            path = _file_path(model, index.siblingAtColumn(0))
            if Path(path).is_dir():
                return str(Path(path))
        # フォルダ行以外への投下は現在のフォルダ宛て
        return str(Path(_file_path(model, self.rootIndex())))

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API
        """Space で選択中ファイルのクイックプレビューを要求。"""
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.preview_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        indexes = self.selectionModel().selectedRows(0)
        if not indexes:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(_file_path(self.model(), i))
                      for i in indexes])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)


class FolderTreeView(_DropMixin, QTreeView):
    """フォルダツリー。ドロップ先（ツリーのフォルダへ移動/コピー）。"""

    files_dropped = Signal(list, str, bool)
    mouse_nav = Signal(bool)  # True=進む / False=戻る

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _dest_dir_for(self, index: QModelIndex) -> str | None:
        if not index.isValid():
            return None
        path = self.model().filePath(index)
        return str(Path(path)) if Path(path).is_dir() else None


class FileIconView(_DropMixin, QListView):
    """サムネイル表示用のアイコンビュー（詳細ビューと同じモデル・選択を共有）。"""

    files_dropped = Signal(list, str, bool)
    mouse_nav = Signal(bool)
    preview_requested = Signal()
    _drag_source = True

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setMovement(QListView.Movement.Static)
        self.setWordWrap(True)
        self.setSpacing(8)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def _dest_dir_for(self, index: QModelIndex) -> str | None:
        model = self.model()
        if index.isValid():
            path = _file_path(model, index)
            if Path(path).is_dir():
                return str(Path(path))
        return str(Path(_file_path(model, self.rootIndex())))

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.preview_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        indexes = [i for i in self.selectionModel().selectedIndexes()
                   if i.column() == 0]
        if not indexes:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(_file_path(self.model(), i))
                      for i in indexes])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
