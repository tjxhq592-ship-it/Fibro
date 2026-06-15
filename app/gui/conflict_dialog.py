"""ファイル操作の衝突解決ダイアログ。

コピー/移動先に同名がある時、上書き/スキップ/両方残す（リネーム）/中止を
ユーザーに選ばせる。「以降すべてに適用」で残り全件へ同じ判断を適用できる。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


class ConflictDialog(QDialog):
    """戻り値は exec 後に result_action / apply_all で取得する。"""

    def __init__(self, src: Path, dest: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ファイルの衝突")
        self.result_action = "cancel"
        self.apply_all = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"「{dest.name}」は既に存在します。\n\n"
            f"移動/コピー元: {src}\n移動/コピー先: {dest}"))

        self.apply_all_check = QCheckBox("以降すべての衝突に同じ操作を適用")
        layout.addWidget(self.apply_all_check)

        buttons = QHBoxLayout()
        for label, action in (
            ("上書き", "overwrite"),
            ("スキップ", "skip"),
            ("両方残す", "rename"),
            ("中止", "cancel"),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _checked=False, a=action: self._choose(a))
            buttons.addWidget(btn)
        layout.addLayout(buttons)

    def _choose(self, action: str) -> None:
        self.result_action = action
        self.apply_all = self.apply_all_check.isChecked()
        self.accept()


def make_resolver(parent) -> object:
    """FileOps.move/copy に渡す resolver を生成する。

    apply-all がオンになったら以降はダイアログを出さず同じ判断を返す。
    """
    state = {"action": None}  # apply-all で固定された判断

    def resolver(src: Path, dest: Path) -> str:
        if state["action"] is not None:
            return state["action"]
        dlg = ConflictDialog(src, dest, parent)
        dlg.exec()
        if dlg.apply_all:
            state["action"] = dlg.result_action
        return dlg.result_action

    return resolver
