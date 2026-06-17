"""検索パネル: QThread ワーカー + 結果ストリーミング + キャンセル。

結果は1件ずつ Signal で受け取り逐次表示。クリックで該当フォルダへジャンプ。
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from app.engine.index_engine import SearchIndex
from app.engine.search_engine import (
    SearchHit, SearchMode, SearchOptions, SearchStats, is_wildcard, search,
)
from app.paths import INDEX_DB

INDEX_MAX_AGE_SEC = 10 * 60  # これより古いインデックスは自動再構築

_KIND_ICON = {
    SearchMode.FILENAME: "📁",
    SearchMode.TEXT: "📄",
    SearchMode.EXCEL: "📊",
}


class SearchWorker(QThread):
    hit = Signal(object)        # SearchHit
    status = Signal(str)        # 進捗メッセージ
    finished_ok = Signal(int, int)  # scanned, skipped

    def __init__(self, root: str, options: SearchOptions,
                 use_index: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._root = root
        self._options = options
        self._use_index = use_index
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        if self._use_index:
            self._run_indexed()
        else:
            self._run_scan()

    def _run_scan(self) -> None:
        stats = SearchStats()
        for h in search(self._root, self._options,
                        cancel=self._cancel, stats=stats):
            if self._cancel.is_set():
                break
            self.hit.emit(h)
        self.finished_ok.emit(stats.scanned, stats.skipped)

    def _run_indexed(self) -> None:
        """FTS5 インデックスでファイル名検索（必要なら構築してから照会）。"""
        import time
        index = SearchIndex(INDEX_DB)
        try:
            built_at = index.indexed_at(self._root)
            count = -1
            if built_at is None:
                self.status.emit("インデックス構築中…")
                count = index.build(self._root, cancel=self._cancel)
                if count < 0:  # キャンセル
                    self.finished_ok.emit(0, 0)
                    return
            elif time.time() - built_at > INDEX_MAX_AGE_SEC:
                # 期限切れは全再構築せず差分更新（書込量を抑える）
                self.status.emit("インデックス更新中…")
                count = index.update(self._root, cancel=self._cancel)
                if count < 0:  # キャンセル
                    self.finished_ok.emit(0, 0)
                    return
            for path in index.query(self._root, self._options.keyword):
                if self._cancel.is_set():
                    break
                self.hit.emit(SearchHit(path, SearchMode.FILENAME))
            self.finished_ok.emit(max(count, 0), 0)
        finally:
            index.close()


class SearchPanel(QWidget):
    """検索UI。file_selected(path) でメインウィンドウにファイル選択を依頼。"""

    file_selected = Signal(str)  # ファイルを選択（親フォルダへ navigate して select）

    def __init__(self, parent=None, settings=None) -> None:
        super().__init__(parent)
        self._worker: SearchWorker | None = None
        self._root = str(Path.home())
        # settings は ThemeManager 互換オブジェクト（get/set）。
        # 共有することで settings.json の単一ライターを保証する。
        self._settings = settings
        self._build_ui()
        self._load_options()  # 保存されたオプションを復元（signal 接続前）
        # 復元後に signal 接続 → 以降のユーザー変更だけが保存される
        for check in (self.recursive_check, self.index_check):
            check.stateChanged.connect(self.save_options)
        # インクリメンタル検索（ファイル名モードのみ、デバウンス400ms）
        self._debounce = QTimer(self, singleShot=True, interval=400)
        self._debounce.timeout.connect(self._incremental_search)
        self.keyword_edit.textChanged.connect(self._schedule_incremental)

    def set_root(self, path: str) -> None:
        self._root = path
        self.root_label.setText(f"検索場所: {path}")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.root_label = QLabel(f"検索場所: {self._root}")
        self.root_label.setWordWrap(True)
        layout.addWidget(self.root_label)

        row = QHBoxLayout()
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("検索キーワード…")
        self.keyword_edit.returnPressed.connect(self.start_search)
        self.search_btn = QPushButton("検索")
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setEnabled(False)
        self.search_btn.clicked.connect(self.start_search)
        self.cancel_btn.clicked.connect(self.cancel_search)
        row.addWidget(self.keyword_edit, stretch=1)
        row.addWidget(self.search_btn)
        row.addWidget(self.cancel_btn)
        layout.addLayout(row)

        modes = QHBoxLayout()
        self.mode_filename = QCheckBox("ファイル名")
        self.mode_filename.setChecked(True)
        self.mode_text = QCheckBox("テキスト")
        self.mode_excel = QCheckBox("Excel")
        self.case_check = QCheckBox("大小区別")
        for w in (self.mode_filename, self.mode_text, self.mode_excel,
                  self.case_check):
            modes.addWidget(w)
        modes.addStretch()
        layout.addLayout(modes)

        opts_row = QHBoxLayout()
        self.recursive_check = QCheckBox("サブフォルダーも検索")
        self.index_check = QCheckBox("高速インデックス")
        self.index_check.setToolTip(
            "ファイル名検索を SQLite FTS5 インデックスで高速化。\n"
            "初回と10分経過後は自動で再構築します。\n"
            "（ファイル名モードのみ・ワイルドカード/サブフォルダOFFとは併用不可）")
        # キーワードに * や ? があれば自動でワイルドカード照合（設定不要）。
        self.keyword_edit.setToolTip(
            "部分一致で検索。* や ? を含めると *.md / file_?.txt などの"
            "パターン照合になります。")
        # デフォルト値。signal 接続は _load_options() の後（__init__）で行う。
        self.recursive_check.setChecked(True)
        opts_row.addWidget(self.recursive_check)
        opts_row.addWidget(self.index_check)
        opts_row.addStretch()
        layout.addLayout(opts_row)

        self.results = QListWidget()
        self.results.itemActivated.connect(self._on_item_activated)
        self.results.itemClicked.connect(self._on_item_activated)
        layout.addWidget(self.results, stretch=1)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    # ---- 検索制御 ----
    def _schedule_incremental(self) -> None:
        # 内容検索（テキスト/Excel）は重いので入力追従しない
        if self.mode_text.isChecked() or self.mode_excel.isChecked():
            return
        self._debounce.start()

    def _incremental_search(self) -> None:
        if self.keyword_edit.text().strip():
            self.start_search()
        else:
            self.cancel_search()
            self.results.clear()
            self.status_label.setText("")
            self.search_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)

    def current_options(self) -> SearchOptions:
        modes: set[SearchMode] = set()
        if self.mode_filename.isChecked():
            modes.add(SearchMode.FILENAME)
        if self.mode_text.isChecked():
            modes.add(SearchMode.TEXT)
        if self.mode_excel.isChecked():
            modes.add(SearchMode.EXCEL)
        return SearchOptions(
            keyword=self.keyword_edit.text().strip(),
            modes=modes or {SearchMode.FILENAME},
            case_sensitive=self.case_check.isChecked(),
            recursive=self.recursive_check.isChecked(),
        )

    def start_search(self) -> None:
        options = self.current_options()
        if not options.keyword:
            return
        self.cancel_search()  # 進行中があれば止める
        self.results.clear()
        self.status_label.setText("検索中…")
        self.search_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        # インデックスは「ファイル名のみ・通常一致（ワイルドカード無し）・再帰」
        # のときだけ有効
        use_index = (self.index_check.isChecked()
                     and options.modes == {SearchMode.FILENAME}
                     and not is_wildcard(options.keyword)
                     and not options.case_sensitive
                     and options.recursive)
        self._worker = SearchWorker(self._root, options, use_index, self)
        self._worker.hit.connect(self._on_hit)
        self._worker.status.connect(self.status_label.setText)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.start()

    def cancel_search(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._worker = None

    # ---- 結果 ----
    def _on_hit(self, hit: SearchHit) -> None:
        rel = hit.path
        try:
            rel = str(Path(hit.path).relative_to(self._root))
        except ValueError:
            pass
        text = f"{_KIND_ICON[hit.kind]} {rel}"
        if hit.detail:
            text += f"  —  {hit.detail}"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, hit.path)
        item.setToolTip(hit.path)
        self.results.addItem(item)
        self.status_label.setText(f"検索中… {self.results.count()}件")

    def _on_finished(self, scanned: int, skipped: int) -> None:
        self.search_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        text = (f"{self.results.count()}件ヒット "
                f"（{scanned}ファイル走査")
        if skipped:
            text += f"、{skipped}件スキップ"
        self.status_label.setText(text + "）")

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """検索結果クリック時、ファイルを選択（親フォルダへ navigate して select）。"""
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.file_selected.emit(path)

    def _load_options(self) -> None:
        """共有 settings から検索オプションを復元。"""
        if self._settings is None:
            return
        search_opts = self._settings.get("search_options", {}) or {}
        self.recursive_check.setChecked(search_opts.get("recursive", True))
        self.index_check.setChecked(search_opts.get("use_index", False))

    def save_options(self) -> None:
        """検索オプションを共有 settings に保存（単一ライター）。"""
        if self._settings is None:
            return
        self._settings.set("search_options", {
            "recursive": self.recursive_check.isChecked(),
            "use_index": self.index_check.isChecked(),
        })

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        self.cancel_search()
        super().closeEvent(event)
