"""クイックプレビュー（スペースキー peek）。

選択ファイルを開かずに覗き見る。画像は縮小表示、テキスト/コードは先頭を
表示、それ以外はファイル情報。Space/Esc で閉じる。外部依存なし。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QLabel, QPlainTextEdit, QVBoxLayout,
)

from app.engine.text_reader import detect_encoding, is_binary
from app.imagetypes import IMAGE_EXTS

# 拡張子が無くてもテキスト判定するが、既知のテキスト/コードは優先的に text に。
_TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".log", ".ini",
    ".cfg", ".xml", ".html", ".css", ".yml", ".yaml", ".toml", ".sh", ".bat",
    ".c", ".h", ".cpp", ".java", ".rs", ".go", ".rb", ".php", ".sql",
}
_PREVIEW_BYTES = 8192


def preview_kind(path: str | Path) -> str:
    """プレビュー種別を返す: "image" | "text" | "info"。"""
    p = Path(path)
    if not p.is_file():
        return "info"
    ext = p.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in _TEXT_EXTS:
        return "text"
    # 拡張子不明: バイナリでなければテキスト扱い
    if not is_binary(p):
        return "text"
    return "info"


def read_text_preview(path: str | Path, max_bytes: int = _PREVIEW_BYTES) -> str:
    """先頭 max_bytes をデコードして返す（text_reader のフォールバックを再利用）。"""
    encoding = detect_encoding(path) or "utf-8"
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
    except OSError as e:
        return f"(読み込めません: {e})"
    return raw.decode(encoding, errors="replace")


def _info_text(path: Path) -> str:
    try:
        st = path.stat()
        size = st.st_size
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        size, mtime = 0, "?"
    kind = "フォルダー" if path.is_dir() else "ファイル"
    return (f"{path.name}\n\n種別: {kind}\nサイズ: {size:,} バイト\n"
            f"更新日時: {mtime}\nパス: {path}")


class QuickPreviewDialog(QDialog):
    """選択ファイルの軽量プレビュー。Space/Esc で閉じる。"""

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        p = Path(path)
        self.setWindowTitle(f"プレビュー — {p.name}")
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        kind = preview_kind(p)
        if kind == "image":
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(p))
            if pixmap.isNull():
                label.setText("(画像を表示できません)")
            else:
                label.setPixmap(pixmap.scaled(
                    600, 480, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            layout.addWidget(label)
        elif kind == "text":
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setPlainText(read_text_preview(p))
            view.setStyleSheet("font-family: Consolas, monospace;")
            layout.addWidget(view)
        else:
            info = QLabel(_info_text(p))
            info.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            info.setAlignment(Qt.AlignmentFlag.AlignTop)
            layout.addWidget(info)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.close()
            return
        super().keyPressEvent(event)
