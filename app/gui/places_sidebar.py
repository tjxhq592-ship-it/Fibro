"""クラウド/ネットワーク場所サイドバー。

app.places.get_all_places() の結果を表示し、クリックでナビゲーション。
場所の取得（SyncRoot レジストリ読み・ネットワークショートカットの COM 解決・
ドライブ列挙）は起動をブロックしないようワーカースレッドで実行する。
到達性チェックは places 確定後に QTimer で 500ms 遅延して非同期実行する。
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QInputDialog, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget,
)

from app.i18n import _
from app.gui.icons import material_icon
from app.gui.jobs import JobTracker, StoppableJob
from app.gui.theme import current_tokens

_PATH_ROLE = Qt.ItemDataRole.UserRole       # 実パス
_ID_ROLE = Qt.ItemDataRole.UserRole + 1     # index（到達性更新用）
_NAME_ROLE = Qt.ItemDataRole.UserRole + 2   # 自動検出した既定名（リネーム既定値用）

# 場所の種別 → Material Symbols アイコン名（icons.py の _MATERIAL_PATHS に対応）
_KIND_ICON = {
    "cloud": "cloud",         # クラウドドライブ
    "drive": "hard_drive",    # ローカルドライブ
    "network": "public",      # ネットワーク場所
}


class _PlaceReachJob(StoppableJob):
    """場所の到達性をバックグラウンドで確認する（UI をブロックしない）。"""

    def __init__(self, places: list, gen: int, emit) -> None:
        super().__init__(emit)
        self._places = places   # list[(index, path)]
        self._gen = gen

    def _work(self) -> None:
        from app.netpath import reachable
        for idx, path in self._places:
            if self._stopped:
                return
            self._notify(self._gen, idx, reachable(path))


class _PlacesLoadJob(StoppableJob):
    """場所一覧の取得をバックグラウンドで行う（起動時の同期 I/O を排除）。

    get_all_places() はレジストリ読み・ネットワークショートカットの
    IShellLink COM 解決・ドライブ列挙を含む。COM をワーカースレッド自身の
    アパートメントで初期化してから呼び、GUI スレッドの COM 状態に依存しない。
    """

    def __init__(self, gen: int, emit) -> None:
        super().__init__(emit)
        self._gen = gen

    def _work(self) -> None:
        # COM の初期化/解除はワーカースレッド自身のアパートメントで閉じる。
        # 基底（StoppableJob）はスレッドを跨ぐ知識を持たないので、この対は
        # ここに置いたままにすること。
        co_inited = False
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.ole32.CoInitialize(None)
                co_inited = True
            except Exception:   # noqa: BLE001 — COM 初期化失敗でも列挙は続行
                pass
        try:
            from app.places import get_all_places
            places = get_all_places()
        except Exception:       # noqa: BLE001 — 取得失敗は空リストでフォールバック
            places = []
        finally:
            if co_inited:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:   # noqa: BLE001
                    pass
        self._notify(self._gen, places)


class PlacesSidebar(QWidget):
    """クラウド/ネットワーク場所の一覧ウィジェット。"""

    path_selected = Signal(str)
    _reach_checked = Signal(int, int, bool)   # gen, index, reachable
    _places_loaded = Signal(int, object)      # gen, list[Place]

    def __init__(self, settings=None, parent=None) -> None:
        super().__init__(parent)
        # ProjectSettingsStore（カスタム表示名 place_names の永続化先）
        self._settings = settings
        self._places: list = []     # app.places.Place のリスト
        self._reach_gen = 0
        self._load_gen = 0          # 場所取得の世代（古い結果を破棄）
        # パス（normcase）→ ユーザー設定の表示名。
        self._custom_names: dict[str, str] = {}
        # 破棄時に実行中ワーカーの通知を止める（消えた C++ オブジェクトへ
        # emit するとワーカースレッド側で例外になる）。
        self._jobs = JobTracker(self)
        self._load_custom_names()
        self._reach_checked.connect(self._apply_reachability)
        self._places_loaded.connect(self._on_places_loaded)

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

        # 起動コスト最小化: 場所取得（同期 I/O 込み）はワーカースレッドで実行し、
        # 完了時に _on_places_loaded でリストを構築する。到達性チェックは
        # places 確定後にそこから 500ms 遅延で開始する。
        self._start_load()

    def _load_custom_names(self) -> None:
        """現在の settings からカスタム表示名を読み込む。"""
        self._custom_names = {}
        if self._settings is not None:
            saved = self._settings.get("place_names", {})
            if isinstance(saved, dict):
                self._custom_names = {str(k): str(v) for k, v in saved.items()}

    def set_settings(self, settings) -> None:
        """place_names の永続化先を差し替えて表示を更新する（プロジェクト切替用）。

        場所自体はシステム共通で変わらないため、取得済みなら再スキャンせず
        カスタム表示名だけを反映してリストを作り直す（未取得なら取得を開始）。
        """
        self._settings = settings
        self._load_custom_names()
        if self._places:
            self._rebuild_list()
        else:
            self._start_load()

    def _start_load(self) -> None:
        """場所取得をワーカースレッドで開始する（起動をブロックしない）。"""
        self._load_gen += 1
        QThreadPool.globalInstance().start(
            self._jobs.track(_PlacesLoadJob(self._load_gen,
                                            self._places_loaded.emit)))

    def _on_places_loaded(self, gen: int, places: list) -> None:
        """ワーカーが取得した場所一覧を反映し、到達性チェックを予約する。"""
        if gen != self._load_gen:
            return  # 古い取得結果は破棄
        self._places = places
        self._rebuild_list()
        # context に self を渡し、破棄後に発火しないようにする。
        QTimer.singleShot(500, self, self._check_reachability)

    def _rebuild_list(self) -> None:
        """現在の self._places とカスタム表示名からリストを構築する（GUI スレッド）。"""
        self.list.clear()
        for i, place in enumerate(self._places):
            key = os.path.normcase(place.path)
            display = self._custom_names.get(key, place.name)
            item = QListWidgetItem(display)
            icon_name = _KIND_ICON.get(place.kind, "hard_drive")
            item.setIcon(material_icon(icon_name))
            item.setData(_PATH_ROLE, place.path)
            item.setData(_ID_ROLE, i)
            item.setData(_NAME_ROLE, place.name)   # 自動検出名（既定値）
            item.setToolTip(place.path)
            self.list.addItem(item)


    def _check_reachability(self) -> None:
        """到達性確認を非同期で開始（起動500ms後）。"""
        targets = [(i, p.path) for i, p in enumerate(self._places)]
        if not targets:
            return
        self._reach_gen += 1
        QThreadPool.globalInstance().start(
            self._jobs.track(_PlaceReachJob(targets, self._reach_gen,
                                            self._reach_checked.emit)))

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
                    item.setForeground(QColor(current_tokens()["text_hint"]))
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
        """外部から再スキャンを要求する（USB抜き差し等）。

        取得はワーカースレッドで行い、完了時に _on_places_loaded がリストを
        作り直し到達性チェックを予約する。
        """
        # 明示的な更新なので、前回諦めた結果は捨てて必ず問い合わせ直す
        # （ネットワークを繋ぎ直した直後にグレーのままにしない）。
        from app.netpath import clear_cache
        clear_cache()
        # ドライブの割り当て/解除が起きている可能性があるため種別も引き直す。
        from app.gui.main_window import _drive_type
        _drive_type.cache_clear()
        self._start_load()
