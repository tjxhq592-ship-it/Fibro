"""PowerRename型の一括リネームダイアログ。

上部ルールバー（検索→置換、regex、対象、連番、大小変換）+
ライブプレビュー表（現在名→新名→状態）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.engine.rename_engine import (
    CaseMode, RenameEngine, RenamePlan, RenameRule, Status, Target,
)
from app.engine.rename_history import RenameExecutor
from app.models.rename_presets import RenamePresetStore

_STATUS_LABEL = {
    Status.OK: "✓",
    Status.UNCHANGED: "- 変更なし",
    Status.RESOLVED: "⚠ 衝突→連番",
    Status.CONFLICT: "✗ 衝突",
    Status.INVALID: "✗ 無効な名前",
}
_STATUS_COLOR = {
    Status.OK: QColor("#2e7d32"),
    Status.UNCHANGED: QColor("#9e9e9e"),
    Status.RESOLVED: QColor("#ef6c00"),
    Status.CONFLICT: QColor("#c62828"),
    Status.INVALID: QColor("#c62828"),
}


class RenameDialog(QDialog):
    """selected_names: 選択中ファイル名、existing_names: フォルダ内全ファイル名。"""

    def __init__(self, directory: str, selected_names: list[str],
                 existing_names: set[str], executor: RenameExecutor,
                 parent=None, preset_store: RenamePresetStore | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"一括リネーム — {len(selected_names)}件")
        self.resize(720, 480)
        self._directory = directory
        self._names = selected_names
        self._existing = existing_names
        self._engine = RenameEngine()
        self._executor = executor
        self._preset_store = preset_store
        self._plan: RenamePlan | None = None

        self._build_ui()
        if self._preset_store is not None:
            self._reload_presets()
        # デバウンス付きライブプレビュー
        self._debounce = QTimer(self, singleShot=True, interval=150)
        self._debounce.timeout.connect(self._update_preview)
        self._update_preview()

    # ---- UI構築 ----
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # プリセット行（preset_store がある時のみ表示）
        if self._preset_store is not None:
            preset_row = QHBoxLayout()
            preset_row.addWidget(QLabel("プリセット:"))
            self.preset_combo = QComboBox()
            self.preset_combo.setMinimumWidth(160)
            preset_row.addWidget(self.preset_combo, stretch=1)
            load_btn = QPushButton("読み込み")
            save_btn = QPushButton("保存…")
            del_btn = QPushButton("削除")
            load_btn.clicked.connect(self._load_selected_preset)
            save_btn.clicked.connect(self._save_preset)
            del_btn.clicked.connect(self._delete_selected_preset)
            for b in (load_btn, save_btn, del_btn):
                preset_row.addWidget(b)
            layout.addLayout(preset_row)

        grid = QGridLayout()
        self.search_edit = QLineEdit()
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("連番は ${n}")
        self.regex_check = QCheckBox("正規表現")
        self.target_combo = QComboBox()
        self.target_combo.addItem("名前", Target.NAME)
        self.target_combo.addItem("拡張子", Target.EXT)
        self.target_combo.addItem("両方", Target.BOTH)
        self.case_combo = QComboBox()
        self.case_combo.addItem("そのまま", CaseMode.KEEP)
        self.case_combo.addItem("UPPER", CaseMode.UPPER)
        self.case_combo.addItem("lower", CaseMode.LOWER)
        self.case_combo.addItem("Title", CaseMode.TITLE)
        self.counter_start = QSpinBox(minimum=0, maximum=999999, value=1)
        self.counter_digits = QSpinBox(minimum=1, maximum=10, value=3)
        self.counter_step = QSpinBox(minimum=1, maximum=9999, value=1)

        grid.addWidget(QLabel("検索:"), 0, 0)
        grid.addWidget(self.search_edit, 0, 1)
        grid.addWidget(self.regex_check, 0, 2)
        grid.addWidget(QLabel("対象:"), 0, 3)
        grid.addWidget(self.target_combo, 0, 4)
        grid.addWidget(QLabel("置換:"), 1, 0)
        grid.addWidget(self.replace_edit, 1, 1)
        grid.addWidget(QLabel("大小:"), 1, 3)
        grid.addWidget(self.case_combo, 1, 4)

        counter_row = QHBoxLayout()
        counter_row.addWidget(QLabel("連番 ${n}:  開始"))
        counter_row.addWidget(self.counter_start)
        counter_row.addWidget(QLabel("桁"))
        counter_row.addWidget(self.counter_digits)
        counter_row.addWidget(QLabel("増分"))
        counter_row.addWidget(self.counter_step)
        counter_row.addStretch()
        grid.addLayout(counter_row, 2, 1, 1, 4)
        layout.addLayout(grid)

        self.rule_error = QLabel()
        self.rule_error.setStyleSheet("color: #c62828;")
        layout.addWidget(self.rule_error)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["現在名", "新名", "状態"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 130)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.summary = QLabel()
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox()
        self.apply_btn = QPushButton("実行")
        cancel_btn = QPushButton("キャンセル")
        buttons.addButton(self.apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_btn.clicked.connect(self._execute)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)

        for w in (self.search_edit, self.replace_edit):
            w.textChanged.connect(self._schedule_preview)
        self.regex_check.toggled.connect(self._schedule_preview)
        for combo in (self.target_combo, self.case_combo):
            combo.currentIndexChanged.connect(self._schedule_preview)
        for spin in (self.counter_start, self.counter_digits,
                     self.counter_step):
            spin.valueChanged.connect(self._schedule_preview)

    # ---- プレビュー ----
    def _schedule_preview(self) -> None:
        self._debounce.start()

    def current_rule(self) -> RenameRule:
        return RenameRule(
            search=self.search_edit.text(),
            replace=self.replace_edit.text(),
            use_regex=self.regex_check.isChecked(),
            target=self.target_combo.currentData(),
            case_mode=self.case_combo.currentData(),
            counter_start=self.counter_start.value(),
            counter_step=self.counter_step.value(),
            counter_digits=self.counter_digits.value(),
        )

    # ---- プリセット ----
    def _rule_to_dict(self, rule: RenameRule) -> dict:
        return {
            "search": rule.search,
            "replace": rule.replace,
            "use_regex": rule.use_regex,
            "target": rule.target.value,
            "case_mode": rule.case_mode.value,
            "counter_start": rule.counter_start,
            "counter_step": rule.counter_step,
            "counter_digits": rule.counter_digits,
        }

    def _apply_dict(self, rule: dict) -> None:
        self.search_edit.setText(rule.get("search", ""))
        self.replace_edit.setText(rule.get("replace", ""))
        self.regex_check.setChecked(bool(rule.get("use_regex", False)))
        self._set_combo(self.target_combo, Target, rule.get("target"))
        self._set_combo(self.case_combo, CaseMode, rule.get("case_mode"))
        self.counter_start.setValue(int(rule.get("counter_start", 1)))
        self.counter_step.setValue(int(rule.get("counter_step", 1)))
        self.counter_digits.setValue(int(rule.get("counter_digits", 3)))
        self._update_preview()

    @staticmethod
    def _set_combo(combo: QComboBox, enum_cls, value) -> None:
        try:
            target = enum_cls(value)
        except ValueError:
            return
        idx = combo.findData(target)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _reload_presets(self) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItems(self._preset_store.names())

    def _load_selected_preset(self) -> None:
        name = self.preset_combo.currentText()
        preset = self._preset_store.get(name) if name else None
        if preset:
            self._apply_dict(preset.rule)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(
            self, "プリセットを保存", "プリセット名:",
            text=self.preset_combo.currentText())
        name = name.strip()
        if not ok or not name:
            return
        self._preset_store.add(name, self._rule_to_dict(self.current_rule()))
        self._reload_presets()
        idx = self.preset_combo.findText(name)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)

    def _delete_selected_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name and self._preset_store.remove(name):
            self._reload_presets()

    def _update_preview(self) -> None:
        rule = self.current_rule()
        error = self._engine.validate_rule(rule)
        if error:
            self.rule_error.setText(error)
            self.apply_btn.setEnabled(False)
            return
        self.rule_error.setText("")

        self._plan = self._engine.build_plan(
            self._names, rule, existing_names=self._existing)
        self.table.setRowCount(len(self._plan.items))
        for row, item in enumerate(self._plan.items):
            for col, text in enumerate(
                    (item.old_name, item.new_name,
                     _STATUS_LABEL[item.status])):
                cell = QTableWidgetItem(text)
                cell.setForeground(_STATUS_COLOR[item.status])
                self.table.setItem(row, col, cell)

        n_change = len(self._plan.changed_items)
        self.summary.setText(
            f"変更: {n_change}件 / 全{len(self._plan.items)}件"
            + ("  （エラー行はスキップされます）" if self._plan.has_errors else ""))
        self.apply_btn.setEnabled(n_change > 0)

    # ---- 実行 ----
    def _execute(self) -> None:
        if not self._plan:
            return
        pairs = [(i.old_name, i.new_name) for i in self._plan.changed_items]
        if not pairs:
            return
        try:
            self._executor.execute(self._directory, pairs)
        except OSError as e:
            QMessageBox.critical(
                self, "リネーム失敗",
                f"リネームに失敗しました（変更はロールバック済み）:\n{e}")
            return
        self.accept()
