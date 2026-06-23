"""クラウド/ネットワーク場所サイドバー。

app.places.get_all_places() の結果を表示し、クリックでナビゲーション。
到達性チェックは起動コストを抑えるため QTimer で 500ms 遅延後に非同期実行。
"""
from __future__ import annotations

from PySide6.QtCore import QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from app.gui.icons import material_icon

_PATH_ROLE = Qt.ItemDataRole.UserRole       # 実パス
_ID_ROLE = Qt.ItemDataRole.UserRole + 1     # index（到達性更新用）

# 場所の種別 → Material Symbols アイコン名（icons.py の _MATERIAL_PATHS に対応）
_KIND_ICON = {
    "cloud": "cloud",         # クラウドドライブ
    "drive": "hard_drive",    # ローカルドライブ
    "network": "public",      # ネットワーク場所
}


class _PlaceReachJob(QRunnable):
    """場所の到達性をバックグラウンドで確認する（UI をブロックしない）。"""

    def __init__(self, places: list, gen: int, emit) -> None:
        super().__init__()
        self._places = places   # list[(index, path)]
        self._gen = gen
        self._emit = emit

    def run(self) -> None:
        from app.netpath import reachable
        for idx, path in self._places:
            self._emit(self._gen, idx, reachable(path))


class PlacesSidebar(QWidget):
    """クラウド/ネットワーク場所の一覧ウィジェット。"""

    path_selected = Signal(str)
    _reach_checked = Signal(int, int, bool)   # gen, index, reachable

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._places: list = []     # app.places.Place のリスト
        self._reach_gen = 0
        self._reach_checked.connect(self._apply_reachability)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.list, stretch=1)

        # 起動コスト最小化: データ取得・ウィジェット生成は同期実行（~10ms以下）。
        # 到達性チェックのみ遅延（ネットワーク遅延が起動をブロックしないため）。
        self._load()
        QTimer.singleShot(500, self._check_reachability)

    def _load(self) -> None:
        """場所を取得してリストを構築する（同期・高速）。"""
        from app.places import get_all_places
        self._places = get_all_places()
        self.list.clear()
        dark = self._is_dark()
        for i, place in enumerate(self._places):
            item = QListWidgetItem(place.name)
            icon_name = _KIND_ICON.get(place.kind, "hard_drive")
            item.setIcon(material_icon(icon_name, dark=dark))
            item.setData(_PATH_ROLE, place.path)
            item.setData(_ID_ROLE, i)
            item.setToolTip(place.path)
            self.list.addItem(item)

    @staticmethod
    def _is_dark() -> bool:
        """QApplication のパレットからダークテーマかどうかを判定する。"""
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QPalette
        app = QApplication.instance()
        if app is None:
            return True
        return app.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _check_reachability(self) -> None:
        """到達性確認を非同期で開始（起動500ms後）。"""
        targets = [(i, p.path) for i, p in enumerate(self._places)]
        if not targets:
            return
        self._reach_gen += 1
        QThreadPool.globalInstance().start(
            _PlaceReachJob(targets, self._reach_gen,
                           self._reach_checked.emit))

    def _apply_reachability(self, gen: int, idx: int, ok: bool) -> None:
        """到達性チェック結果を反映: 到達不可→グレー表示。"""
        if gen != self._reach_gen:
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item and item.data(_ID_ROLE) == idx:
                if ok:
                    # 既定色へ戻す（接続回復時に追従）
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)
                    place = self._places[idx]
                    item.setToolTip(place.path)
                else:
                    item.setForeground(QColor("#9e9e9e"))
                    place = self._places[idx]
                    item.setToolTip(f"{place.path}  （到達不可）")
                break

    def _on_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(_PATH_ROLE)
        if path:
            self.path_selected.emit(path)

    def refresh(self) -> None:
        """外部から再スキャンを要求する（USB抜き差し等）。"""
        self._load()
        self._reach_gen += 1
        QTimer.singleShot(0, self._check_reachability)
