"""プロジェクト管理ダイアログ（並び替え・リネーム・削除）。

favorites_sidebar と同じ QListWidget の InternalMove D&D パターンで並び替え、
ドロップ確定のたびに ProjectManager へ順序を永続化する。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from app.i18n import _

_ID_ROLE = Qt.ItemDataRole.UserRole


class _ProjectList(QListWidget):
    """ドロップ完了時に並び順の永続化を要求する QListWidget。"""

    def __init__(self, on_reordered, parent=None) -> None:
        super().__init__(parent)
        self._on_reordered = on_reordered
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().dropEvent(event)
        self._on_reordered()


class ProjectDialog(QDialog):
    """プロジェクトの並び替え（D&D）・リネーム・削除を行う。

    操作はその場で ProjectManager に反映される（OK/キャンセルの概念はない）。
    アクティブなプロジェクトを削除した場合のフォールバック（デフォルトへの
    切替）は ProjectManager.remove が行い、UI 再読込は呼び出し側が
    active_project_id の変化を見て行う。
    """

    def __init__(self, manager, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self.setWindowTitle(_("project_manage_title"))
        self.setMinimumWidth(380)

        self.list = _ProjectList(self._persist_order)
        self._reload()

        self.rename_btn = QPushButton(_("project_btn_rename"))
        self.rename_btn.clicked.connect(self._rename_selected)
        self.delete_btn = QPushButton(_("project_btn_delete"))
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.rename_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list, stretch=1)
        layout.addLayout(btn_row)
        layout.addWidget(buttons)

    def _reload(self) -> None:
        self.list.clear()
        for proj in self._manager.projects:
            item = QListWidgetItem(proj.name)
            item.setData(_ID_ROLE, proj.id)
            self.list.addItem(item)

    def _persist_order(self) -> None:
        ids = [self.list.item(i).data(_ID_ROLE)
               for i in range(self.list.count())]
        self._manager.reorder(ids)

    def _selected_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(_ID_ROLE) if item is not None else None

    def _rename_selected(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        name, ok = QInputDialog.getText(
            self, _("project_rename_title"), _("project_name_label"),
            text=self.list.currentItem().text())
        name = name.strip()
        if ok and name:
            self._manager.rename(pid, name)
            self._reload()

    def _delete_selected(self) -> None:
        pid = self._selected_id()
        if pid is None:
            return
        name = self.list.currentItem().text()
        answer = QMessageBox.warning(
            self, _("project_delete_title"),
            _("project_delete_msg").format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._manager.remove(pid)
        self._reload()
