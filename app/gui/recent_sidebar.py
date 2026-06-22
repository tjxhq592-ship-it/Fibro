"""最近使った／よく使うフォルダのサイドバー。

「最近」「頻繁」の2セクションを QTreeWidget で表示。クリックでジャンプ。
お気に入りと違い手動登録は不要で、ナビゲート履歴から自動集計される。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.i18n import _
from app.models.recent import RecentStore

_PATH_ROLE = Qt.ItemDataRole.UserRole


class RecentSidebar(QWidget):
    path_selected = Signal(str)

    def __init__(self, store: RecentStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.tree, stretch=1)

        self.refresh()

    def _section(self, title: str, entries) -> None:
        head = QTreeWidgetItem([title])
        head.setFlags(Qt.ItemFlag.ItemIsEnabled)  # クリック不可の見出し
        self.tree.addTopLevelItem(head)
        for e in entries:
            name = Path(e.path).name or e.path
            item = QTreeWidgetItem([name])
            item.setData(0, _PATH_ROLE, e.path)
            item.setToolTip(0, e.path)
            head.addChild(item)
        head.setExpanded(True)

    def refresh(self) -> None:
        self.tree.clear()
        self._section(_("sidebar_recent"), self._store.recent(10))
        self._section(_("sidebar_frequent"), self._store.frequent(10))

    def _on_clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        path = item.data(0, _PATH_ROLE)
        if path and Path(path).is_dir():
            self.path_selected.emit(path)
