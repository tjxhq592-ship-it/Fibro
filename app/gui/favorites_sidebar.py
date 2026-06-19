"""お気に入りサイドバー（階層化対応）。

QTreeWidget でグループ（フォルダ）によるネストを表現。
ドラッグ&ドロップで再配置・グループへの移動が可能。
構造は favorites.json に parent_id 付きで保存される。
"""
from __future__ import annotations

from PySide6.QtCore import QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QInputDialog, QMenu, QMessageBox, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.models.favorite import FavoriteStore

_ID_ROLE = Qt.ItemDataRole.UserRole


class _ReachJob(QRunnable):
    """お気に入りの到達性をバックグラウンドで確認する。

    切断中のネットワーク/クラウドパスでは is_reachable() が最大2秒ブロックする
    ため、GUI スレッドではなくここで確認し、結果を emit(gen, id, ok) で返す。
    """

    def __init__(self, leaves: list[tuple[str, str]], gen: int, emit) -> None:
        super().__init__()
        self._leaves = leaves  # (fav_id, path)
        self._gen = gen
        self._emit = emit

    def run(self) -> None:
        from app.netpath import reachable
        for fid, path in self._leaves:
            self._emit(self._gen, fid, reachable(path))


class _FavTree(QTreeWidget):
    """ドロップ完了を通知する QTreeWidget。

    内部移動（並べ替え）に加え、ファイル一覧などからの外部ドロップ
    （text/uri-list）を受けてお気に入り登録できるようにする。
    """

    dropped = Signal()
    urls_dropped = Signal(list, object)  # paths, ドロップ先 item（group or None）

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt API
        mime = event.mimeData()
        if mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            target = self.itemAt(event.position().toPoint())
            if paths:
                self.urls_dropped.emit(paths, target)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
        self.dropped.emit()


class FavoritesSidebar(QWidget):
    path_selected = Signal(str)
    _reach_checked = Signal(int, str, bool)  # gen, fav_id, reachable

    def __init__(self, store: FavoriteStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._reach_gen = 0                 # 到達性チェックの世代
        self._items_by_id: dict[str, QTreeWidgetItem] = {}
        self._reach_checked.connect(self._apply_reachability)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = _FavTree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.tree.setAcceptDrops(True)  # 外部（ファイル一覧）からの登録ドロップ用
        self.tree.dropped.connect(self._persist_structure)
        self.tree.urls_dropped.connect(self._on_urls_dropped)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        layout.addWidget(self.tree, stretch=1)

        self.refresh()

        # 到達性を定期的に再確認（切断→接続回復時に「(到達不可)」を自動で消す）。
        self._reach_timer = QTimer(self)
        self._reach_timer.setInterval(20000)  # 20秒ごと
        self._reach_timer.timeout.connect(self._recheck_reachability)
        self._reach_timer.start()

    # ---- 表示 ----
    @property
    def list(self):
        """後方互換: 旧 API で list.count() などを参照するテスト向け。"""
        return self.tree

    def _label_for(self, fav) -> str:
        # 到達性は同期で見ない（GUI を固めるため）。後から非同期で印を付ける。
        if fav.is_group:
            return f"📁 {fav.label}"
        label = f"⭐ {fav.label}"
        if fav.tags:
            label += f"  [{', '.join(fav.tags)}]"
        return label

    def _make_item(self, fav) -> QTreeWidgetItem:
        item = QTreeWidgetItem([self._label_for(fav)])
        item.setData(0, _ID_ROLE, fav.id)
        tooltip = fav.path or fav.label
        if fav.note:
            tooltip += f"\n{fav.note}"
        item.setToolTip(0, tooltip)
        # グループはドロップ受け入れ可、葉は不可
        flags = item.flags() | Qt.ItemFlag.ItemIsDragEnabled
        if fav.is_group:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        else:
            flags &= ~Qt.ItemFlag.ItemIsDropEnabled
        item.setFlags(flags)
        return item

    def refresh(self) -> None:
        self.tree.clear()
        self._reach_gen += 1
        self._items_by_id = {}
        # parent_id → 親 item のマップを構築しながら、保存順に追加
        items: dict[str, QTreeWidgetItem] = {}
        # 親が先に作られるよう、トポロジカルに数回パスする
        pending = list(self._store.favorites)
        guard = 0
        while pending and guard < len(self._store.favorites) + 2:
            guard += 1
            still: list = []
            for fav in pending:
                if fav.parent_id and fav.parent_id not in items:
                    # 親がまだ未作成なら次パスへ
                    if any(p.id == fav.parent_id for p in self._store.favorites):
                        still.append(fav)
                        continue
                    # 親が存在しない（孤児）→ トップ扱い
                item = self._make_item(fav)
                if fav.parent_id and fav.parent_id in items:
                    items[fav.parent_id].addChild(item)
                else:
                    self.tree.addTopLevelItem(item)
                items[fav.id] = item
                self._items_by_id[fav.id] = item
            pending = still
        self.tree.expandAll()
        # 到達性チェックはバックグラウンドで（切断パスでも GUI を固めない）
        leaves = [(f.id, f.path) for f in self._store.favorites
                  if not f.is_group and f.path]
        if leaves:
            QThreadPool.globalInstance().start(
                _ReachJob(leaves, self._reach_gen, self._reach_checked.emit))

    def _apply_reachability(self, gen: int, fav_id: str, ok: bool) -> None:
        """非同期チェック結果を反映。到達不可なら灰色＋(到達不可)、
        到達可能なら印を消して既定色へ戻す（接続回復時に追従）。"""
        if gen != self._reach_gen:
            return  # 古い結果は破棄
        item = self._items_by_id.get(fav_id)
        if item is None:
            return
        fav = next((f for f in self._store.favorites if f.id == fav_id), None)
        base = (self._label_for(fav) if fav is not None
                else item.text(0).replace("  (到達不可)", ""))
        if ok:
            item.setText(0, base)
            item.setData(0, Qt.ItemDataRole.ForegroundRole, None)  # 既定色へ戻す
        else:
            item.setText(0, base + "  (到達不可)")
            item.setForeground(0, QColor("#9e9e9e"))

    def _recheck_reachability(self) -> None:
        """現在のお気に入りの到達性を再確認（定期実行）。回復で印が消える。"""
        leaves = [(f.id, f.path) for f in self._store.favorites
                  if not f.is_group and f.path]
        if not leaves:
            return
        self._reach_gen += 1
        QThreadPool.globalInstance().start(
            _ReachJob(leaves, self._reach_gen, self._reach_checked.emit))

    # ---- 追加 ----
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
        # 選択中のグループがあればその配下に追加
        parent_id = self._selected_group_id()
        self._store.add(label.strip(), path, parent_id=parent_id)
        self.refresh()

    def _on_urls_dropped(self, paths: list[str], target) -> None:
        """ファイル一覧などからドロップされたパスをお気に入りに登録する。

        ドロップ先がグループならその配下、そうでなければトップ階層へ。
        表示名は付け足し入力なしでファイル/フォルダ名を使い、複数まとめて登録。
        重複（同一パス）はスキップする。
        """
        from pathlib import Path as P
        parent_id = ""
        if target is not None:
            fav = self._fav_for_item(target)
            if fav is not None and fav.is_group:
                parent_id = fav.id
        added = 0
        for path in paths:
            if not path or self._store.find_by_path(path):
                continue
            self._store.add(P(path).name or path, path, parent_id=parent_id)
            added += 1
        if added:
            self.refresh()

    def _add_group(self, parent_id: str = "") -> None:
        label, ok = QInputDialog.getText(
            self, "新規グループ", "グループ名:", text="新しいグループ")
        if ok and label.strip():
            self._store.add_group(label.strip(), parent_id=parent_id)
            self.refresh()

    def _selected_group_id(self) -> str:
        """選択中のアイテムがグループならその id、そうでなければ空文字。"""
        item = self.tree.currentItem()
        if item is None:
            return ""
        fav = self._fav_for_item(item)
        if fav and fav.is_group:
            return fav.id
        return ""

    # ---- 構造の永続化（D&D 後） ----
    def _persist_structure(self) -> None:
        """ツリーを走査して parent_id・順序を再構築し、ストアに保存。"""
        by_id = {f.id: f for f in self._store.favorites}
        ordered: list = []

        def walk(item: QTreeWidgetItem, parent_id: str) -> None:
            for i in range(item.childCount()):
                child = item.child(i)
                fid = child.data(0, _ID_ROLE)
                fav = by_id.get(fid)
                if fav is None:
                    continue
                fav.parent_id = parent_id
                ordered.append(fav)
                walk(child, fav.id)

        root = self.tree.invisibleRootItem()
        walk(root, "")
        if len(ordered) == len(self._store.favorites):
            self._store.reorder(ordered)
        else:
            # 不整合時は保存せず再描画のみ（データ損失を避ける）
            self.refresh()

    # ---- 操作 ----
    def _fav_for_item(self, item: QTreeWidgetItem):
        fav_id = item.data(0, _ID_ROLE)
        return next((f for f in self._store.favorites if f.id == fav_id), None)

    def _on_clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        fav = self._fav_for_item(item)
        if not fav:
            return
        if fav.is_group:
            item.setExpanded(not item.isExpanded())
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
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            # 空白部分: トップ階層に新規グループ
            menu.addAction("新規グループ…", lambda: self._add_group(""))
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        fav = self._fav_for_item(item)
        if not fav:
            return
        if fav.is_group:
            menu.addAction("このグループ内に新規グループ…",
                           lambda: self._add_group(fav.id))
            menu.addAction("名前を変更…", lambda: self._rename(fav))
            menu.addSeparator()
            menu.addAction("削除（中身ごと）", lambda: self._remove(fav))
        else:
            menu.addAction("開く", lambda: self._on_clicked(item))
            menu.addAction("名前を変更…", lambda: self._rename(fav))
            menu.addAction("メモを編集…", lambda: self._edit_note(fav))
            menu.addSeparator()
            menu.addAction("削除", lambda: self._remove(fav))
        menu.addSeparator()
        menu.addAction("新規グループ…", lambda: self._add_group(""))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

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
