"""お気に入りサイドバー。クリックでジャンプ、右クリックで管理。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QVBoxLayout, QWidget,
)

from app.models.favorite import FavoriteStore


class FavoritesSidebar(QWidget):
    path_selected = Signal(str)

    def __init__(self, store: FavoriteStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("<b>お気に入り</b>"))

        self.list = QListWidget()
        # ドラッグ&ドロップで並び替え（結果は favorites.json に保存）
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list.model().rowsMoved.connect(self._on_rows_moved)
        self.list.itemClicked.connect(self._on_clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        layout.addWidget(self.list, stretch=1)

        hint = QLabel("フォルダを右クリック→「お気に入りに追加」")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for fav in self._store.favorites:
            reachable = fav.is_reachable()
            label = f"⭐ {fav.label}"
            if fav.tags:
                label += f"  [{', '.join(fav.tags)}]"
            if not reachable:
                label += "  (到達不可)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, fav.id)
            tooltip = fav.path
            if fav.note:
                tooltip += f"\n{fav.note}"
            item.setToolTip(tooltip)
            if not reachable:
                item.setForeground(QColor("#9e9e9e"))
            self.list.addItem(item)

    def add_favorite(self, path: str) -> None:
        if self._store.find_by_path(path):
            QMessageBox.information(self, "お気に入り", "既に登録されています。")
            return
        from pathlib import Path as P
        default_label = P(path).name or path
        label, ok = QInputDialog.getText(
            self, "お気に入りに追加", "表示名:", text=default_label)
        if not ok or not label.strip():
            return
        self._store.add(label.strip(), path)
        self.refresh()

    def _on_rows_moved(self, *_args) -> None:
        """並び替え後のリスト順をストアに反映して保存。"""
        order = [self.list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.list.count())]
        by_id = {f.id: f for f in self._store.favorites}
        self._store.favorites = [by_id[fid] for fid in order if fid in by_id]
        self._store.save()

    def _fav_for_item(self, item: QListWidgetItem):
        fav_id = item.data(Qt.ItemDataRole.UserRole)
        return next((f for f in self._store.favorites if f.id == fav_id),
                    None)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        fav = self._fav_for_item(item)
        if not fav:
            return
        if not fav.is_reachable():
            QMessageBox.warning(
                self, "到達不可",
                f"パスにアクセスできません:\n{fav.path}\n\n"
                "ネットワークドライブの接続や共有設定を確認してください。")
            self.refresh()
            return
        self.path_selected.emit(fav.path)

    def _show_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        fav = self._fav_for_item(item)
        if not fav:
            return
        menu = QMenu(self)
        menu.addAction("開く", lambda: self._on_clicked(item))
        menu.addAction("名前を変更…", lambda: self._rename(fav))
        menu.addAction("メモを編集…", lambda: self._edit_note(fav))
        menu.addSeparator()
        menu.addAction("削除", lambda: self._remove(fav))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _rename(self, fav) -> None:
        label, ok = QInputDialog.getText(
            self, "名前を変更", "表示名:", text=fav.label)
        if ok and label.strip():
            fav.label = label.strip()
            self._store.save()
            self.refresh()

    def _edit_note(self, fav) -> None:
        note, ok = QInputDialog.getText(
            self, "メモを編集", "メモ:", text=fav.note)
        if ok:
            fav.note = note
            self._store.save()
            self.refresh()

    def _remove(self, fav) -> None:
        self._store.remove(fav.id)
        self.refresh()
