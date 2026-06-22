"""ファイル/フォルダのプロパティ表示ダイアログ。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel

from app.i18n import _


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
        self.setWindowTitle(_("prop_title").format(name=p.name))
        layout = QFormLayout(self)

        try:
            st = p.stat()
            if p.is_dir():
                kind = _("prop_kind_folder")
            elif p.suffix:
                kind = _("prop_kind_file").format(ext=p.suffix)
            else:
                kind = _("prop_kind_noext")
            rows = [
                (_("prop_row_name"), p.name),
                (_("prop_row_kind"), kind),
                (_("prop_row_location"), str(p.parent)),
                (_("prop_row_size"), "-" if p.is_dir() else _human_size(st.st_size)),
                (_("prop_row_created"), _fmt_time(st.st_ctime)),
                (_("prop_row_modified"), _fmt_time(st.st_mtime)),
                (_("prop_row_readonly"),
                 _("prop_yes") if not os.access(path, os.W_OK) else _("prop_no")),
            ]
        except OSError as e:
            rows = [(_("prop_error"), str(e))]

        for label, value in rows:
            value_label = QLabel(str(value))
            value_label.setTextInteractionFlags(
                value_label.textInteractionFlags()
                | value_label.textInteractionFlags().TextSelectableByMouse)
            layout.addRow(f"{label}:", value_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addRow(buttons)
