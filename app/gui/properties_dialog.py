"""ファイル/フォルダのプロパティ表示ダイアログ。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel


def _human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:,.1f} TB"


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class PropertiesDialog(QDialog):
    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        p = Path(path)
        self.setWindowTitle(f"プロパティ — {p.name}")
        layout = QFormLayout(self)

        try:
            st = p.stat()
            kind = "フォルダ" if p.is_dir() else f"ファイル ({p.suffix or 'なし'})"
            rows = [
                ("名前", p.name),
                ("種類", kind),
                ("場所", str(p.parent)),
                ("サイズ", "-" if p.is_dir() else _human_size(st.st_size)),
                ("作成日時", _fmt_time(st.st_ctime)),
                ("更新日時", _fmt_time(st.st_mtime)),
                ("読み取り専用", "はい" if not os.access(path, os.W_OK) else "いいえ"),
            ]
        except OSError as e:
            rows = [("エラー", str(e))]

        for label, value in rows:
            value_label = QLabel(str(value))
            value_label.setTextInteractionFlags(
                value_label.textInteractionFlags()
                | value_label.textInteractionFlags().TextSelectableByMouse)
            layout.addRow(f"{label}:", value_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addRow(buttons)
