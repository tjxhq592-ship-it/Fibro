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
from PySide6.QtWidgets import QAbstractItemView, QTableView, QTreeView


def _file_path(model, index: QModelIndex) -> str:
    """プロキシ越しでも QFileSystemModel のパスを取り出す。"""
    if isinstance(model, QSortFilterProxyModel):
        return model.sourceModel().filePath(model.mapToSource(index))
    return model.filePath(index)


def _urls_to_paths(mime: QMimeData) -> list[str]:
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


class _DropMixin:
    """フォルダへのドロップを files_dropped(paths, dest, copy) に変換する。"""

    def _dest_dir_for(self, index: QModelIndex) -> str | None:
        raise NotImplementedError

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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _dest_dir_for(self, index: QModelIndex) -> str | None:
        if not index.isValid():
            return None
        path = self.model().filePath(index)
        return str(Path(path)) if Path(path).is_dir() else None
