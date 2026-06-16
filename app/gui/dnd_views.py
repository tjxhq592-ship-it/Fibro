"""ドラッグ&ドロップ対応のビュー。

一覧/ツリーからフォルダへファイルをドロップで移動、Ctrl押下でコピー。
実処理は files_dropped シグナル経由で FileOps に委譲する（Undo 対応のため
Qt 内蔵の moveEvent には任せない）。text/uri-list を使うので標準
エクスプローラーとの相互ドラッグも可能。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QItemSelection, QItemSelectionModel, QMimeData, QModelIndex, QPoint, QRect,
    QSortFilterProxyModel, Qt, QUrl, Signal,
)
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QListView, QRubberBand, QTableView,
    QTreeView,
)

# 右ボタンD&D / Ctrl+コピーD&D を識別するための marker MIME 形式
_RIGHT_DRAG_MIME = "application/x-fibro-rightdrag"
_COPY_DRAG_MIME = "application/x-fibro-copydrag"


def _file_path(model, index: QModelIndex) -> str:
    """プロキシ越しでも QFileSystemModel のパスを取り出す。"""
    if isinstance(model, QSortFilterProxyModel):
        return model.sourceModel().filePath(model.mapToSource(index))
    return model.filePath(index)


def _urls_to_paths(mime: QMimeData) -> list[str]:
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


def _drop_view_at(global_pos):
    """グローバル座標の下にある _DropMixin ビューを返す（無ければ None）。"""
    w = QApplication.widgetAt(global_pos)
    while w is not None:
        if isinstance(w, _DropMixin):
            return w
        w = w.parent()
    return None


class _DropMixin:
    """フォルダへのドロップを files_dropped(paths, dest, copy) に変換する。"""

    _drag_source = False  # ドラッグ元ビュー（一覧/サムネ）のみ True
    _rdrag_origin: QPoint | None = None  # 右ボタンドラッグ開始点
    _rdrag_active = False                 # 右ドラッグ中か
    _suppress_context = False             # 直後の右クリックメニュー抑止
    _cdrag_origin: QPoint | None = None   # Ctrl+左ドラッグ（コピー）開始点
    _cdrag_index = None                   # Ctrl+押下したアイテム（解放時トグル用）
    _cdrag_dragged = False                # Ctrl+ドラッグが起きたか
    _rdrag_release_time = 0.0             # 右ドラッグ完了時刻（直後の右クリック抑止）
    # 自前マーキー（半透明矩形）を描くか。Qt がマーキーを描かない詳細ビューで True。
    # 選択自体は Qt ネイティブのラバーバンド（連続更新・触れたら選択）に任せ、
    # ここでは Explorer 風の矩形を重ねて描くだけにする。
    _draw_marquee = False
    _marquee: QRubberBand | None = None
    _marquee_origin: QPoint | None = None

    def _dest_dir_for(self, index: QModelIndex) -> str | None:
        raise NotImplementedError

    def _selected_file_paths(self) -> list[str]:
        """選択中アイテムのパス（右ドラッグのソース）。"""
        sm = self.selectionModel()
        if sm is None:
            return []
        seen, paths = set(), []
        for i in sm.selectedIndexes():
            if i.column() != 0:
                continue
            p = _file_path(self.model(), i)
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
        return paths

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
        if self._drag_source and button == Qt.MouseButton.RightButton:
            # 右ボタンをアイテム上で押したら右ドラッグ開始候補にする。
            # 未選択のアイテムなら（Explorer 同様）その場で選択しておく。
            self._rdrag_active = False
            index = self.indexAt(event.position().toPoint())
            sm = self.selectionModel()
            if index.isValid() and sm is not None:
                if not sm.isSelected(index):
                    sm.select(
                        index,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect
                        | QItemSelectionModel.SelectionFlag.Rows)
                self._rdrag_origin = event.position().toPoint()
            else:
                self._rdrag_origin = None
        if self._drag_source and button == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            sm = self.selectionModel()
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            on_selected = (index.isValid() and sm is not None
                           and sm.isSelected(index))
            if ctrl and on_selected:
                # Ctrl+「選択済み」アイテム＝コピードラッグ候補。選択は変えずに
                # 保持し（Qt 標準の即時トグル解除を回避）、ドラッグしなければ
                # 解放時にトグル（Ctrl+クリックで選択解除）。未選択への Ctrl+
                # クリックは super に任せて複数選択を成立させる。
                from PySide6.QtCore import QPersistentModelIndex
                self._cdrag_origin = event.position().toPoint()
                self._cdrag_index = QPersistentModelIndex(index)
                self._cdrag_dragged = False
                event.accept()
                return
            # Explorer 流: 選択済みアイテムを押した時だけドラッグ移動を許可。
            # 空白／未選択アイテムを押した時はドラッグを無効化して、
            # ラバーバンド（範囲）選択を開始できるようにする。
            self.setDragEnabled(bool(on_selected))
            # 範囲選択の開始点を記録（ドラッグ移動でない時だけ自前マーキー対象）。
            if self._draw_marquee and not on_selected:
                self._marquee_origin = event.position().toPoint()
            else:
                self._marquee_origin = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — Qt API
        """右ボタン保持で一定距離動いたら右ドラッグ中とマークする。

        Qt の QDrag は右ボタンのドロップを配送しないため、ここでは状態を持つだけで
        実際の処理は mouseReleaseEvent で行う（手動方式）。
        """
        # Ctrl+左ドラッグ（コピー）の手動起動
        if (self._drag_source and self._cdrag_origin is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            moved = (event.position().toPoint()
                     - self._cdrag_origin).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._cdrag_origin = None
                self._cdrag_dragged = True
                self._cdrag_index = None
                self._start_copy_drag()
            event.accept()
            return
        if (self._drag_source and self._rdrag_origin is not None
                and event.buttons() & Qt.MouseButton.RightButton):
            moved = (event.position().toPoint()
                     - self._rdrag_origin).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._rdrag_active = True
            if self._rdrag_active:
                event.accept()  # ラバーバンド等を抑止
                return
        # 自前マーキー（Explorer 風の半透明矩形）を開始点から現在位置へ追従し、
        # 矩形に触れた行を連続更新で選択する（この詳細ビュー構成では Qt ネイティブの
        # ラバーバンド選択が発火しないため、選択も自前で行う）。
        if (self._draw_marquee and self._marquee_origin is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            pos = event.position().toPoint()
            if self._marquee is None:
                if (pos - self._marquee_origin).manhattanLength() \
                        < QApplication.startDragDistance():
                    return  # 閾値未満はクリック扱い（まだ範囲選択しない）
                self._marquee = QRubberBand(
                    QRubberBand.Shape.Rectangle, self.viewport())
            rect = QRect(self._marquee_origin, pos).normalized()
            self._marquee.setGeometry(rect)
            self._marquee.show()
            self._marquee.raise_()  # アイテム描画の前面へ
            self._select_rows_in_rect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _select_rows_in_rect(self, rect: QRect) -> None:
        """マーキー矩形に触れた行を ClearAndSelect で選択（Explorer 流の touch）。"""
        sm = self.selectionModel()
        model = self.model()
        if sm is None or model is None:
            return
        root = self.rootIndex()
        # 可視範囲の行だけ走査（巨大フォルダでも軽量）。
        first = self.rowAt(0) if hasattr(self, "rowAt") else 0
        last = (self.rowAt(self.viewport().height() - 1)
                if hasattr(self, "rowAt") else model.rowCount(root) - 1)
        if first < 0:
            first = 0
        if last < 0:
            last = model.rowCount(root) - 1
        width = self.viewport().width()
        last_col = max(0, model.columnCount(root) - 1)
        selection = QItemSelection()
        for row in range(first, last + 1):
            idx0 = model.index(row, 0, root)
            vr = self.visualRect(idx0)
            if vr.isEmpty():
                continue
            # 行は全幅。帯の縦範囲と重なれば選択（SelectRows の touch 判定）。
            row_rect = QRect(0, vr.y(), width, vr.height())
            if rect.intersects(row_rect):
                selection.select(idx0, model.index(row, last_col, root))
        sm.select(
            selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _start_copy_drag(self) -> None:
        """選択中アイテムをコピー意図でドラッグ開始（既定動作=Copy）。"""
        paths = self._selected_file_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        mime.setData(_COPY_DRAG_MIME, b"1")  # コピー意図 marker
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
                  Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt API
        if (self._drag_source and event.button() == Qt.MouseButton.RightButton
                and self._rdrag_active):
            self._rdrag_active = False
            self._rdrag_origin = None
            self._suppress_context = True  # 直後の右クリックメニューを抑止
            self._finish_right_drag(event.globalPosition().toPoint())
            # ネイティブメニュー（モーダル）終了後に来る右クリックメニューを
            # 抑止するため、完了時刻を記録（_show_context_menu が参照）。
            import time
            self._rdrag_release_time = time.monotonic()
            event.accept()
            return
        # Ctrl+押下したアイテムをドラッグせず解放 = Ctrl+クリック → 選択トグル
        if (self._drag_source and event.button() == Qt.MouseButton.LeftButton
                and self._cdrag_index is not None and not self._cdrag_dragged):
            from PySide6.QtCore import QModelIndex
            sm = self.selectionModel()
            if sm is not None:
                sm.select(
                    QModelIndex(self._cdrag_index),
                    QItemSelectionModel.SelectionFlag.Toggle
                    | QItemSelectionModel.SelectionFlag.Rows)
            self._cdrag_index = None
            self._cdrag_origin = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.RightButton:
            self._rdrag_origin = None
            self._rdrag_active = False
        if self._drag_source and event.button() == Qt.MouseButton.LeftButton:
            self._cdrag_origin = None
            self._cdrag_index = None
            self.setDragEnabled(True)
            # 自前マーキーを片付ける（選択結果はそのまま保持）。
            self._marquee_origin = None
            if self._marquee is not None:
                self._marquee.hide()
                self._marquee.deleteLater()
                self._marquee = None

    def contextMenuEvent(self, event) -> None:  # noqa: N802 — Qt API
        """右ドラッグ直後の右クリックメニューは抑止する。"""
        if self._suppress_context:
            self._suppress_context = False
            event.accept()
            return
        super().contextMenuEvent(event)

    def _finish_right_drag(self, global_pos) -> None:
        """離した位置のフォルダを宛先に、ネイティブ右ドラッグメニューを要求する。"""
        paths = self._selected_file_paths()
        target = _drop_view_at(global_pos)
        if target is None or not paths:
            return
        local = target.viewport().mapFromGlobal(global_pos)
        dest = target._dest_dir_for(target.indexAt(local))
        if not dest:
            return
        # ネイティブ右ドラッグでは同フォルダ投下も有効（「ここに解凍」「ここにコピー」）。
        # 自分自身（フォルダをそれ自身の上）へ落とすケースだけ除外。
        kept = [p for p in paths if Path(p) != Path(dest)]
        if not kept:
            return
        target.native_drop_requested.emit(
            kept, dest, global_pos.x(), global_pos.y())

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
        mime = event.mimeData()
        if not mime.hasUrls():
            return super().dropEvent(event)
        dest = self._dest_dir_for(self.indexAt(event.position().toPoint()))
        raw = _urls_to_paths(mime)
        if not dest or not raw:
            event.ignore()
            return
        # 右ドラッグ: ネイティブの「ここに解凍/コピー/移動…」メニューへ。
        # 同フォルダでも有効。自分自身への投下だけ除外。
        if mime.hasFormat(_RIGHT_DRAG_MIME):
            paths = [p for p in raw if Path(p) != Path(dest)]
            if not paths:
                event.ignore()
                return
            gpos = self.viewport().mapToGlobal(event.position().toPoint())
            event.acceptProposedAction()
            self.native_drop_requested.emit(paths, dest, gpos.x(), gpos.y())
            return
        # Ctrl 押下でコピー（押下なしは移動）。event.modifiers() は Windows で
        # 取りこぼすことがあるため、実キー状態とコピー marker も併用して判定。
        mods = event.modifiers() | QApplication.keyboardModifiers()
        copy = (mime.hasFormat(_COPY_DRAG_MIME)
                or bool(mods & Qt.KeyboardModifier.ControlModifier))
        # 自分自身への投下は常に除外。移動だけは「同じフォルダへ」も除外（no-op）。
        # コピーは同フォルダでも許可（複製）。
        paths = []
        for p in raw:
            if Path(p) == Path(dest):
                continue
            if not copy and str(Path(p).parent) == dest:
                continue
            paths.append(p)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(paths, dest, copy)


class FileTableView(_DropMixin, QTableView):
    """ファイル一覧。ドラッグ元かつ（フォルダ行への）ドロップ先。"""

    files_dropped = Signal(list, str, bool)  # paths, dest_dir, copy
    native_drop_requested = Signal(list, str, int, int)  # paths, dest, gx, gy
    mouse_nav = Signal(bool)  # True=進む / False=戻る
    preview_requested = Signal()  # Space キーでクイックプレビュー
    open_requested = Signal()  # Enter キーで選択項目を開く/実行
    _drag_source = True
    # 詳細ビューは Qt がマーキー矩形を描かないため、自前で半透明矩形を重ねる
    # （アイコンビューは setSelectionRectVisible で Qt が描くので不要）。
    _draw_marquee = True

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
        """Space でクイックプレビュー、Enter で選択項目を開く/実行。"""
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.preview_requested.emit()
            event.accept()
            return
        # テンキーの Enter は KeypadModifier が付くため除外して判定
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and mods == Qt.KeyboardModifier.NoModifier):
            self.open_requested.emit()
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
    native_drop_requested = Signal(list, str, int, int)
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
    native_drop_requested = Signal(list, str, int, int)
    mouse_nav = Signal(bool)
    preview_requested = Signal()
    open_requested = Signal()  # Enter キーで選択項目を開く/実行
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
        # テンキーの Enter は KeypadModifier が付くため除外して判定
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and mods == Qt.KeyboardModifier.NoModifier):
            self.open_requested.emit()
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
