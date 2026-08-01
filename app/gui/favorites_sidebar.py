"""お気に入りサイドバー（階層化対応）。

QTreeWidget でグループ（フォルダ）によるネストを表現。
ドラッグ&ドロップで再配置・グループへの移動が可能。
構造は favorites.json に parent_id 付きで保存される。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QPoint, QPointF, QRect, QRectF, Qt, QThreadPool, QTimer, Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QInputDialog, QMenu, QMessageBox, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app.gui.icons import material_icon
from app.gui.jobs import JobTracker, StoppableJob
from app.gui.theme import current_tokens
from app.i18n import _
from app.models.favorite import FavoriteStore

_ID_ROLE = Qt.ItemDataRole.UserRole
_IS_GROUP_ROLE = Qt.ItemDataRole.UserRole + 1

#: 項目の上下このぶんはそれぞれ「直前へ」「直後へ」の帯（残り 50% が中央）。
_DROP_BAND = 0.25
#: 挿入位置を示すバーの太さ（px）。
_BAR_THICKNESS = 2
#: バー両端の丸キャップの半径（px）。細い線だけだと見落としやすいため付ける。
_BAR_CAP = 3
#: "into"（グループの中へ）を示す枠線の太さと角丸半径。
_FRAME_WIDTH = 1.5
_FRAME_RADIUS = 4


@dataclass
class DropTarget:
    """カーソル位置から決まったドロップ先。

    Qt の `dropIndicatorPosition()` は使わない（`setDropIndicatorShown(False)`
    と併用できないため。docs/investigation/qt_drop_indicator_20260731.md）。
    """

    parent_id: str   # 挿入先の親グループ id（トップ階層は ""）
    index: int       # 兄弟内の挿入位置
    mode: str        # "between" | "into"
    rect: QRect      # インジケータ描画用
    depth: int       # インジケータの左インデント算出用


class _ReachJob(StoppableJob):
    """お気に入りの到達性をバックグラウンドで確認する。

    切断中のネットワーク/クラウドパスでは is_reachable() が最大2秒ブロックする
    ため、GUI スレッドではなくここで確認し、結果を emit(gen, id, ok) で返す。
    """

    def __init__(self, leaves: list[tuple[str, str, bool]], gen: int,
                 emit) -> None:
        super().__init__(emit)
        self._leaves = leaves  # (fav_id, path, is_file)
        self._gen = gen

    def _work(self) -> None:
        from app.netpath import reachable
        for fid, path, is_file in self._leaves:
            if self._stopped:
                return
            self._notify(self._gen, fid,
                         reachable(path, require_dir=not is_file))


class _ActivateJob(StoppableJob):
    """クリックされたお気に入りの到達性をバックグラウンドで確認する。

    is_reachable() は切断中のネットワーク/クラウドパスで最大2秒ブロック
    するため GUI スレッドでは呼ばず、結果を emit(gen, ok) で返してから
    遷移（または警告）する。
    """

    def __init__(self, path: str, is_file: bool, gen: int, emit) -> None:
        super().__init__(emit)
        self._path = path
        self._is_file = is_file
        self._gen = gen

    def _work(self) -> None:
        from app.netpath import reachable
        self._notify(self._gen,
                     reachable(self._path, require_dir=not self._is_file))


class _FavTree(QTreeWidget):
    """ドロップ完了を通知する QTreeWidget。

    内部移動（並べ替え）に加え、ファイル一覧などからの外部ドロップ
    （text/uri-list）を受けてお気に入り登録できるようにする。
    """

    urls_dropped = Signal(list, object)  # paths, DropTarget（外部からの登録）
    items_moved = Signal(list, object)   # fav_id のリスト, DropTarget（内部移動）
    rename_requested = Signal()          # F2: 現在の項目のリネーム要求

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        #: 現在のドロップ先。None ならインジケータを描かない。
        self._drop_hint: DropTarget | None = None
        #: ホバー中の折りたたみグループと、その自動展開タイマー。
        self._expand_item: QTreeWidgetItem | None = None
        self._expand_timer = QTimer(self)
        self._expand_timer.setSingleShot(True)
        self._expand_timer.timeout.connect(self._auto_expand)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API
        """F2 で選択中のお気に入りをリネーム（項目はインライン編集不可のため自前で処理）。"""
        if (event.key() == Qt.Key.Key_F2
                and self.currentItem() is not None):
            self.rename_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---- ドロップ位置の判定（自前） ----
    #
    # Qt のバンド判定には乗らない。理由は 3 つ:
    #   * 葉に「中に入れる」を作りたくない（Qt は OnItem を返してくる）
    #   * `setDropIndicatorShown(False)` にすると Qt 側の位置が取れなくなる
    #   * 純粋関数にしておけば描画なしで単体テストできる
    # 詳細は docs/investigation/qt_drop_indicator_20260731.md。

    @staticmethod
    def _item_id(item: QTreeWidgetItem) -> str:
        return item.data(0, _ID_ROLE) or ""

    @staticmethod
    def _item_is_group(item: QTreeWidgetItem) -> bool:
        return bool(item.data(0, _IS_GROUP_ROLE))

    @staticmethod
    def _depth_of(item: QTreeWidgetItem) -> int:
        depth = 0
        parent = item.parent()
        while parent is not None:
            depth += 1
            parent = parent.parent()
        return depth

    def _parent_id_of(self, item: QTreeWidgetItem) -> str:
        parent = item.parent()
        return "" if parent is None else self._item_id(parent)

    def _index_of(self, item: QTreeWidgetItem) -> int:
        parent = item.parent()
        if parent is None:
            return self.indexOfTopLevelItem(item)
        return parent.indexOfChild(item)

    def _bar_rect(self, y: int, depth: int) -> QRect:
        """挿入位置を示す横バーの矩形。左端は挿入先の階層ぶん字下げする。"""
        view = self.viewport()
        left = self.indentation() * depth
        top = max(0, min(y - _BAR_THICKNESS // 2,
                         max(0, view.height() - _BAR_THICKNESS)))
        return QRect(left, top, max(0, view.width() - left), _BAR_THICKNESS)

    def _between(self, item: QTreeWidgetItem, rect: QRect, *,
                 after: bool) -> DropTarget:
        depth = self._depth_of(item)
        y = rect.bottom() + 1 if after else rect.top()
        return DropTarget(
            parent_id=self._parent_id_of(item),
            index=self._index_of(item) + (1 if after else 0),
            mode="between", rect=self._bar_rect(y, depth), depth=depth)

    def _append_to_top(self) -> DropTarget:
        """項目の無い空白へのドロップ → トップ階層の末尾。"""
        count = self.topLevelItemCount()
        y = 0
        if count:
            last = self.topLevelItem(count - 1)
            while last.isExpanded() and last.childCount():
                last = last.child(last.childCount() - 1)
            y = self.visualItemRect(last).bottom() + 1
        return DropTarget(parent_id="", index=count, mode="between",
                          rect=self._bar_rect(y, 0), depth=0)

    def _display_order(self) -> list[QTreeWidgetItem]:
        """折りたたみに関係なく、ツリーの表示順で全 item を列挙する。"""
        out: list[QTreeWidgetItem] = []

        def walk(item: QTreeWidgetItem) -> None:
            for i in range(item.childCount()):
                child = item.child(i)
                out.append(child)
                walk(child)

        walk(self.invisibleRootItem())
        return out

    def dragged_ids(self) -> list[str]:
        """内部ドラッグで掴んでいる fav_id を、選択順ではなく表示順で返す。

        グループとその子孫が同時に選ばれている場合、子孫は除く
        （グループごと動くので、二重に動かすと壊れる）。
        """
        chosen = set(self.selectedItems())
        ids: list[str] = []
        for item in self._display_order():
            if item not in chosen:
                continue
            parent = item.parent()
            while parent is not None:
                if parent in chosen:
                    break
                parent = parent.parent()
            else:
                ids.append(self._item_id(item))
        return ids

    def _is_inside_dragged(self, target: DropTarget) -> bool:
        """掴んでいるグループ自身、またはその子孫の中へ落とそうとしているか。"""
        dragged = set(self.dragged_ids())
        if not dragged or not target.parent_id:
            return False
        node = next((it for it in self._display_order()
                     if self._item_id(it) == target.parent_id), None)
        while node is not None:
            if self._item_id(node) in dragged:
                return True
            node = node.parent()
        return False

    def _drop_target(self, point: QPoint) -> DropTarget | None:
        """カーソル位置からドロップ先を決める。"""
        item = self.itemAt(point)
        if item is None:
            return self._append_to_top()
        rect = self.visualItemRect(item)
        height = rect.height()
        if height <= 0:
            return self._append_to_top()
        rel = point.y() - rect.top()
        band = height * _DROP_BAND
        # 境界値（25% ちょうど）は中央側に含める。
        if rel < band:
            return self._between(item, rect, after=False)
        if rel > height - band:
            # 展開済みで子を持つグループの下端は、視覚上の次の行がその第 1 子。
            # 「直後の兄弟」ではなく第 1 子位置に入れる（Explorer / VS Code と同じ）。
            if (self._item_is_group(item) and item.isExpanded()
                    and item.childCount() > 0):
                depth = self._depth_of(item) + 1
                return DropTarget(
                    parent_id=self._item_id(item), index=0, mode="between",
                    rect=self._bar_rect(rect.bottom() + 1, depth), depth=depth)
            return self._between(item, rect, after=True)
        if self._item_is_group(item):
            return DropTarget(
                parent_id=self._item_id(item), index=item.childCount(),
                mode="into", rect=rect, depth=self._depth_of(item) + 1)
        # 葉に「中に入れる」は無い。中点で直前 / 直後に振り分ける。
        return self._between(item, rect, after=rel >= height / 2)

    # ---- インジケータの描画（自前） ----

    def _set_hint(self, target: DropTarget | None) -> None:
        if target == self._drop_hint:
            return  # ドラッグ中は毎ピクセル呼ばれるので、変化時だけ再描画する
        self._drop_hint = target
        self.viewport().update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().paintEvent(event)
        hint = self._drop_hint
        if hint is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(current_tokens()["accent"])  # 色はテーマトークンから
        if hint.mode == "into":
            pen = QPen(color)
            pen.setWidthF(_FRAME_WIDTH)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)  # 塗ると文字が読めなくなる
            painter.drawRoundedRect(
                QRectF(hint.rect).adjusted(1, 1, -1, -1),
                _FRAME_RADIUS, _FRAME_RADIUS)
        else:
            rect = hint.rect
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(rect)
            cy = rect.top() + rect.height() / 2
            for cx in (rect.left() + _BAR_CAP, rect.right() - _BAR_CAP):
                painter.drawEllipse(QPointF(cx, cy), _BAR_CAP, _BAR_CAP)
        painter.end()

    # ---- ドラッグ&ドロップ（内部移動・外部登録とも自前で処理する） ----
    #
    # super().dropEvent() は呼ばない。QTreeWidgetItem を Qt に動かさせず
    # モデル側だけ更新して refresh() で描き直すことで、「グループを自分の
    # 子孫へ落とす」といった破綻経路が構造的に消える。

    def _handles(self, event) -> bool:
        """扱えるドラッグか（外部のパス、または自分自身からの内部移動）。"""
        return event.mimeData().hasUrls() or event.source() is self

    def _is_internal(self, event) -> bool:
        """自分自身からのドラッグ（= 既存項目の移動）か。

        外部ドロップでは「掴んでいるもの」が無いので、
        _is_internal を通さずに _is_inside_dragged を見てはいけない
        （選択中の項目を掴んでいるものと誤認して、そのグループへ
        落とせなくなる）。
        """
        return event.source() is self and not event.mimeData().hasUrls()

    # ---- ホバーでの自動展開・端での自動スクロール ----
    #
    # どちらも本来は QTreeView / QAbstractItemView の dragMoveEvent が面倒を
    # 見るが、そこを super に流していないので効かない。加えて Qt 側の自動展開は
    # state() == DraggingState と実カーソル位置を要求し、DraggingState は
    # canDrop()（= モデルの mimeTypes）が真のときしか入らないため、
    # text/uri-list を持たない QTreeWidget の外部ドロップでは元々動かない。
    # mimeTypes() を足すのは指示書で禁止されているので、受け取った
    # event の座標だけで完結する形で自前で持つ。遅延は setAutoExpandDelay()
    # の値をそのまま使う（設定の出どころを 1 つに保つ）。

    def _arm_auto_expand(self, item: QTreeWidgetItem | None) -> None:
        delay = self.autoExpandDelay()
        collapsed_group = (item is not None and self._item_is_group(item)
                           and not item.isExpanded() and item.childCount() > 0)
        if delay < 0 or not collapsed_group:
            self._expand_timer.stop()
            self._expand_item = None
            return
        if item is self._expand_item and self._expand_timer.isActive():
            return  # 同じ項目の上に居る間はタイマーを延長しない
        self._expand_item = item
        self._expand_timer.start(delay)

    def _auto_expand(self) -> None:
        item = self._expand_item
        self._expand_item = None
        if item is not None:
            self.expandItem(item)
            self._set_hint(None)  # 行が動くので、次の dragMove で引き直す

    def _auto_scroll(self, point: QPoint) -> None:
        """ビューポートの上下端に近づいたら送る（画面外の位置も指定できる）。"""
        margin = self.autoScrollMargin()
        height = self.viewport().height()
        bar = self.verticalScrollBar()
        if point.y() < margin:
            bar.setValue(bar.value() - bar.singleStep())
        elif point.y() > height - margin:
            bar.setValue(bar.value() + bar.singleStep())

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt API
        if self._handles(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt API
        point = event.position().toPoint()
        target = None
        if self._handles(event):
            target = self._drop_target(point)
            if (target is not None and self._is_internal(event)
                    and self._is_inside_dragged(target)):
                target = None  # 掴んでいるものの中へは落とせない
        if target is None:
            event.ignore()
            self._arm_auto_expand(None)
        else:
            event.acceptProposedAction()
            self._arm_auto_expand(self.itemAt(point))
            self._auto_scroll(point)
        self._set_hint(target)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 — Qt API
        # ドロップ時と離脱時の両方で消さないと、バーが描かれたまま残る
        self._set_hint(None)
        self._arm_auto_expand(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt API
        target = (self._drop_target(event.position().toPoint())
                  if self._handles(event) else None)
        self._set_hint(None)
        self._arm_auto_expand(None)
        internal = self._is_internal(event)
        if target is None or (internal and self._is_inside_dragged(target)):
            event.ignore()
            return
        if internal:
            ids = self.dragged_ids()
            if ids:
                self.items_moved.emit(ids, target)
        else:
            paths = [u.toLocalFile() for u in event.mimeData().urls()
                     if u.isLocalFile()]
            if paths:
                self.urls_dropped.emit(paths, target)
        event.acceptProposedAction()


class FavoritesSidebar(QWidget):
    path_selected = Signal(str)              # フォルダのお気に入り → フォルダへ移動
    file_activated = Signal(str)             # ファイルのお気に入り → 既定アプリで開く
    notify_requested = Signal(str, str)      # kind, message（MainWindow.notify へ）
    _reach_checked = Signal(int, str, bool)  # gen, fav_id, reachable
    _activate_checked = Signal(int, bool)    # gen, reachable（クリック時の確認）

    def __init__(self, store: FavoriteStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._reach_gen = 0                 # 到達性チェックの世代
        self._activate_gen = 0              # クリック時確認の世代（連打対策）
        self._pending_activation = None     # (fav, as_file) 確認結果待ちの1件
        self._items_by_id: dict[str, QTreeWidgetItem] = {}
        # refresh() でのツリー再構築中は itemExpanded/itemCollapsed を無視する
        self._restoring = False
        # 破棄時に実行中ワーカーの通知を止める（消えた C++ オブジェクトへ
        # emit するとワーカースレッド側で例外になる）。
        self._jobs = JobTracker(self)
        self._reach_checked.connect(self._apply_reachability)
        self._activate_checked.connect(self._apply_activation)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = _FavTree()
        self.tree.setHeaderHidden(True)
        # InternalMove は外部ドラッグを受理しないため DragDrop にする。
        # 位置判定とインジケータ描画は自前で持つ（Qt の細線は出さない）。
        # 根拠: docs/investigation/qt_drop_indicator_20260731.md
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        # DragDrop 側は startDrag が Copy を提案してくるので明示的に打ち消す
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setDropIndicatorShown(False)
        self.tree.setAutoExpandDelay(700)  # 折りたたみグループへホバーで自動展開
        self.tree.setAcceptDrops(True)  # 外部（ファイル一覧）からの登録ドロップ用
        # 内部移動をまとめて行えるように複数選択を許可する
        self.tree.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.urls_dropped.connect(self._on_urls_dropped)
        self.tree.items_moved.connect(self._on_items_moved)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        self.tree.rename_requested.connect(self._rename_current)
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
        # アイコンは item.setIcon() で付与するため、ラベルには含めない。
        if fav.is_group:
            return fav.label
        label = fav.label
        if fav.tags:
            label += f"  [{', '.join(fav.tags)}]"
        return label

    def _make_item(self, fav) -> QTreeWidgetItem:
        item = QTreeWidgetItem([self._label_for(fav)])
        icon_name = "folder_special" if fav.is_group else "star"
        item.setIcon(0, material_icon(icon_name))
        item.setData(0, _ID_ROLE, fav.id)
        # ドロップ位置の判定でストアを引かずに済むよう、種別を item に持たせる
        item.setData(0, _IS_GROUP_ROLE, fav.is_group)
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
        self._restoring = True
        try:
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
                    if fav.is_group:
                        # 保存済み状態を復元（新規グループは expanded=True）
                        item.setExpanded(fav.expanded)
                pending = still
        finally:
            self._restoring = False
        # 到達性チェックはバックグラウンドで（切断パスでも GUI を固めない）
        leaves = [(f.id, f.path, f.is_file) for f in self._store.favorites
                  if not f.is_group and f.path]
        if leaves:
            QThreadPool.globalInstance().start(self._jobs.track(
                _ReachJob(leaves, self._reach_gen, self._reach_checked.emit)))

    def _apply_reachability(self, gen: int, fav_id: str, ok: bool) -> None:
        """非同期チェック結果を反映。到達不可なら灰色＋(到達不可)、
        到達可能なら印を消して既定色へ戻す（接続回復時に追従）。"""
        if gen != self._reach_gen:
            return  # 古い結果は破棄
        item = self._items_by_id.get(fav_id)
        if item is None:
            return
        fav = next((f for f in self._store.favorites if f.id == fav_id), None)
        mark = "  " + _("fav_unreachable_mark")
        base = (self._label_for(fav) if fav is not None
                else item.text(0).replace(mark, ""))
        if ok:
            item.setText(0, base)
            item.setData(0, Qt.ItemDataRole.ForegroundRole, None)  # 既定色へ戻す
        else:
            item.setText(0, base + mark)
            item.setForeground(0, QColor(current_tokens()["text_hint"]))

    def _recheck_reachability(self) -> None:
        """現在のお気に入りの到達性を再確認（定期実行）。回復で印が消える。"""
        leaves = [(f.id, f.path, f.is_file) for f in self._store.favorites
                  if not f.is_group and f.path]
        if not leaves:
            return
        self._reach_gen += 1
        QThreadPool.globalInstance().start(self._jobs.track(
            _ReachJob(leaves, self._reach_gen, self._reach_checked.emit)))

    # ---- 追加 ----
    def add_favorite(self, path: str) -> None:
        if self._store.find_by_path(path):
            QMessageBox.information(
                self, _("sidebar_fav"), _("fav_already_msg"))
            return
        from pathlib import Path as P
        default_label = P(path).name or path
        label, ok = QInputDialog.getText(
            self, _("ctx_add_fav"), _("fav_label_display"), text=default_label)
        if not ok or not label.strip():
            return
        # 選択中のグループがあればその配下に追加
        parent_id = self._selected_group_id()
        self._store.add(label.strip(), path, parent_id=parent_id,
                        is_file=P(path).is_file())
        self.refresh()

    def _on_urls_dropped(self, paths: list[str], target: DropTarget) -> None:
        """ファイル一覧などからドロップされたパスをお気に入りに登録する。

        カーソル位置から決まった挿入位置（target）へ、**渡された順序のまま**
        まとめて登録する。表示名は付け足し入力なしでファイル/フォルダ名を使う。
        重複（同一パス）はスキップし、全件重複なら無言で終わらず通知する。
        """
        from pathlib import Path as P
        specs = []
        # 登録は最後にまとめて行うので、同じドロップ内の重複は自分で覚えておく
        # （store.find_by_path はまだ挿入前の分を知らない）。
        seen: set[str] = set()
        for path in paths:
            if not path or self._store.find_by_path(path):
                continue
            norm = str(P(path))
            if norm in seen:
                continue
            seen.add(norm)
            specs.append({"label": P(path).name or path, "path": path,
                          "is_file": P(path).is_file()})
        if not specs:
            # 何も起きないとドロップ失敗に見えるので必ず伝える
            self.notify_requested.emit("warning", _("fav_already_msg"))
            return
        self._store.insert_many(specs, parent_id=target.parent_id,
                                index=target.index)
        self.refresh()

    def _on_items_moved(self, fav_ids: list[str], target: DropTarget) -> None:
        """既存のお気に入りを、カーソル位置で決まった位置へ移動する。"""
        index = target.index
        moved = False
        for fav_id in fav_ids:
            if not self._store.move(fav_id, parent_id=target.parent_id,
                                    index=index):
                continue
            moved = True
            # 次の 1 件は今動かしたものの直後へ。移動後の実位置から数え直す
            # （同じ親の中で後ろへ動かすと添字がずれるため）。
            siblings = self._store.children_of(target.parent_id)
            index = next(i for i, f in enumerate(siblings)
                         if f.id == fav_id) + 1
        if moved:
            self.refresh()

    def _add_group(self, parent_id: str = "") -> None:
        label, ok = QInputDialog.getText(
            self, _("fav_group_new_title"), _("fav_group_name_lbl"),
            text=_("fav_group_new_default"))
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

    # 以前あった _persist_structure（ツリーを走査して parent_id・順序を
    # 作り直す後処理）は削除した。Qt に item を動かさせなくなったので、
    # 構造の正は常にストア側（insert_many / move）になり、ツリーは
    # refresh() で描き直すだけになったため。

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
        # フォルダは単クリックで移動。ファイルはダブルクリックで開く（ここでは何もしない）。
        if not fav.is_file:
            self._activate(fav, as_file=False)

    def expand_all_groups(self, expanded: bool) -> None:
        """全グループを一括で開閉し、保存は 1 回にまとめる。

        itemExpanded/itemCollapsed 経由の _set_expanded_state() は 1 件ごとに
        store.save() を呼ぶため、そのままでは書き込みがグループ数だけ走る。
        既存の _restoring ゲートで通知を止め、Favorite.expanded の更新と保存は
        ここで明示的に行う。

        全ノードがメモリ上にある QTreeWidget なので expandAll()/collapseAll()
        は安全かつ即時。ディスク I/O もネットワークアクセスも発生しない。
        """
        self._restoring = True
        try:
            if expanded:
                self.tree.expandAll()
            else:
                self.tree.collapseAll()
        finally:
            self._restoring = False

        changed = False
        for fav in self._store.favorites:
            # グループ以外は expanded を持たない（_set_expanded_state と同じ判定）
            if not fav.is_group or fav.expanded == expanded:
                continue
            fav.expanded = expanded
            changed = True
        if changed:
            self._store.save()

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        self._set_expanded_state(item, True)

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
        self._set_expanded_state(item, False)

    def _set_expanded_state(self, item: QTreeWidgetItem,
                            expanded: bool) -> None:
        """ユーザー操作による展開・折りたたみを Favorite.expanded に保存する。

        refresh() によるツリー再構築中（_restoring 中）は無視する。
        再構築中の setExpanded() 呼び出しも itemExpanded/itemCollapsed を
        発火させるため、ここで弾かないと無意味な保存が連発する。
        """
        if self._restoring:
            return
        fav = self._fav_for_item(item)
        if fav is None or not fav.is_group:
            return
        if fav.expanded == expanded:
            return
        fav.expanded = expanded
        self._store.save()

    def _on_double_clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        fav = self._fav_for_item(item)
        if not fav or fav.is_group:
            return
        # ファイルはダブルクリックで既定アプリで開く（フォルダは単クリックで処理済み）。
        if fav.is_file:
            self._activate(fav, as_file=True)

    def _activate(self, fav, *, as_file: bool) -> None:
        """到達性を確認したうえで、ファイルは開く／フォルダは移動を要求する。

        is_reachable() は切断中のネットワークパスで最大2秒ブロックするため
        バックグラウンドで確認し、結果が返ってから遷移か警告を出す
        （_ReachJob と同じパターン。世代カウンタで連打時は最後の1件のみ有効）。
        """
        self._activate_gen += 1
        self._pending_activation = (fav, as_file)
        QThreadPool.globalInstance().start(self._jobs.track(_ActivateJob(
            fav.path, fav.is_file, self._activate_gen,
            self._activate_checked.emit)))

    def _apply_activation(self, gen: int, ok: bool) -> None:
        """非同期の到達性確認の結果を受けて遷移/警告する（GUI スレッド）。"""
        if gen != self._activate_gen or self._pending_activation is None:
            return  # 古い結果は破棄
        fav, as_file = self._pending_activation
        self._pending_activation = None
        if not ok:
            QMessageBox.warning(
                self, _("fav_unreachable_title"),
                _("fav_unreachable_msg").format(path=fav.path))
            self.refresh()
            return
        if as_file:
            self.file_activated.emit(fav.path)
        else:
            self.path_selected.emit(fav.path)

    def _show_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            # 空白部分: トップ階層に新規グループ
            menu.addAction(_("fav_ctx_new_group"), lambda: self._add_group(""))
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        fav = self._fav_for_item(item)
        if not fav:
            return
        if fav.is_group:
            menu.addAction(_("fav_ctx_new_subgroup"),
                           lambda: self._add_group(fav.id))
            menu.addAction(_("fav_ctx_rename"), lambda: self._rename(fav))
            menu.addSeparator()
            menu.addAction(_("fav_ctx_remove_group"), lambda: self._remove(fav))
        else:
            menu.addAction(_("ctx_open"),
                           lambda: self._activate(fav, as_file=fav.is_file))
            menu.addAction(_("fav_ctx_rename"), lambda: self._rename(fav))
            menu.addAction(_("fav_ctx_edit_note"), lambda: self._edit_note(fav))
            menu.addSeparator()
            menu.addAction(_("fav_ctx_remove"), lambda: self._remove(fav))
        menu.addSeparator()
        menu.addAction(_("fav_ctx_new_group"), lambda: self._add_group(""))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename_current(self) -> None:
        """F2 ハンドラ: 現在選択中の項目（グループ/葉どちらも）をリネーム。"""
        item = self.tree.currentItem()
        if item is None:
            return
        fav = self._fav_for_item(item)
        if fav is not None:
            self._rename(fav)

    def _rename(self, fav) -> None:
        label, ok = QInputDialog.getText(
            self, _("fav_rename_title"), _("fav_label_display"), text=fav.label)
        if ok and label.strip():
            fav.label = label.strip()
            self._store.save()
            self.refresh()

    def _edit_note(self, fav) -> None:
        note, ok = QInputDialog.getText(
            self, _("fav_note_title"), _("fav_note_label"), text=fav.note)
        if ok:
            fav.note = note
            self._store.save()
            self.refresh()

    def _remove(self, fav) -> None:
        self._store.remove(fav.id)
        self.refresh()
