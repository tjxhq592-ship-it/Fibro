"""クラウド/ネットワーク場所サイドバー。

app.places.get_all_places() の結果を表示し、クリックでナビゲーション。
到達性チェックは起動コストを抑えるため QTimer で 500ms 遅延後に非同期実行。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QInputDialog, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget,
)

from app.i18n import _
from app.gui.icons import material_icon

_PATH_ROLE = Qt.ItemDataRole.UserRole       # 実パス
_ID_ROLE = Qt.ItemDataRole.UserRole + 1     # index（到達性更新用）
_NAME_ROLE = Qt.ItemDataRole.UserRole + 2   # 自動検出した既定名（リネーム既定値用）

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

    def __init__(self, settings=None, parent=None) -> None:
        super().__init__(parent)
        # ProjectSettingsStore（カスタム表示名 place_names の永続化先）
        self._settings = settings
        self._places: list = []     # app.places.Place のリスト
        self._reach_gen = 0
        # パス（normcase）→ ユーザー設定の表示名。
        self._custom_names: dict[str, str] = {}
        self._load_custom_names()
        self._reach_checked.connect(self._apply_reachability)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.itemClicked.connect(self._on_clicked)
        # 右クリックメニュー（名前を変更 / 既定名に戻す）
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        # F2 で現在の項目をリネーム。MainWindow に既に F2（単一リネーム）の
        # グローバル QAction があり、QShortcut では競合（曖昧）して発火しない。
        # リストにフォーカスがある間は ShortcutOverride を横取りし、グローバル
        # ショートカットより優先して自前で KeyPress を処理する。
        self.list.installEventFilter(self)
        layout.addWidget(self.list, stretch=1)

        # 起動コスト最小化: データ取得・ウィジェット生成は同期実行（~10ms以下）。
        # 到達性チェックのみ遅延（ネットワーク遅延が起動をブロックしないため）。
        self._load()
        QTimer.singleShot(500, self._check_reachability)

    def _load_custom_names(self) -> None:
        """現在の settings からカスタム表示名を読み込む。"""
        self._custom_names = {}
        if self._settings is not None:
            saved = self._settings.get("place_names", {})
            if isinstance(saved, dict):
                self._custom_names = {str(k): str(v) for k, v in saved.items()}

    def set_settings(self, settings) -> None:
        """place_names の永続化先を差し替えて表示を更新する（プロジェクト切替用）。"""
        self._settings = settings
        self._load_custom_names()
        self.refresh()

    def _load(self) -> None:
        """場所を取得してリストを構築する（同期・高速）。"""
        from app.places import get_all_places
        self._places = get_all_places()
        self.list.clear()
        dark = self._is_dark()
        for i, place in enumerate(self._places):
            key = os.path.normcase(place.path)
            display = self._custom_names.get(key, place.name)
            item = QListWidgetItem(display)
            icon_name = _KIND_ICON.get(place.kind, "hard_drive")
            item.setIcon(material_icon(icon_name, dark=dark))
            item.setData(_PATH_ROLE, place.path)
            item.setData(_ID_ROLE, i)
            item.setData(_NAME_ROLE, place.name)   # 自動検出名（既定値）
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

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API
        """リスト上の F2 をグローバルショートカットより優先して横取りする。"""
        if obj is self.list:
            et = event.type()
            if (et == QEvent.Type.ShortcutOverride
                    and event.key() == Qt.Key.Key_F2):
                event.accept()   # グローバル F2 を抑止し KeyPress を自分へ流す
                return True
            if (et == QEvent.Type.KeyPress
                    and event.key() == Qt.Key.Key_F2):
                self._rename_current()
                return True
        return super().eventFilter(obj, event)

    # ---- リネーム（カスタム表示名） ----
    def _show_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction(_("ctx_open"), lambda: self._on_clicked(item))
        menu.addAction(_("fav_rename_title"), lambda: self._rename(item))
        # カスタム名が設定済みのときだけ「既定名に戻す」を出す
        key = os.path.normcase(item.data(_PATH_ROLE) or "")
        if key in self._custom_names:
            menu.addAction(_("place_reset_name"), lambda: self._reset_name(item))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _rename_current(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._rename(item)

    def _rename(self, item: QListWidgetItem) -> None:
        """表示名をユーザー入力で変更し、settings に永続化する。"""
        path = item.data(_PATH_ROLE)
        if not path:
            return
        key = os.path.normcase(path)
        default = item.data(_NAME_ROLE) or item.text()
        current = self._custom_names.get(key, default)
        name, ok = QInputDialog.getText(
            self, _("fav_rename_title"), _("fav_label_display"), text=current)
        if not ok:
            return
        name = name.strip()
        if not name or name == default:
            # 空 or 既定と同じ → カスタム名を解除（既定へ戻す）
            self._custom_names.pop(key, None)
            item.setText(default)
        else:
            self._custom_names[key] = name
            item.setText(name)
        self._save_names()

    def _reset_name(self, item: QListWidgetItem) -> None:
        """カスタム表示名を消して自動検出名に戻す。"""
        key = os.path.normcase(item.data(_PATH_ROLE) or "")
        if self._custom_names.pop(key, None) is not None:
            item.setText(item.data(_NAME_ROLE) or item.text())
            self._save_names()

    def _save_names(self) -> None:
        if self._settings is not None:
            self._settings.set("place_names", dict(self._custom_names))

    def refresh(self) -> None:
        """外部から再スキャンを要求する（USB抜き差し等）。"""
        self._load()
        self._reach_gen += 1
        QTimer.singleShot(0, self._check_reachability)
