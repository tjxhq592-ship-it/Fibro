"""ダーク/ライトテーマ。Fusion スタイル + QPalette で外部依存なし。

設定は config/settings.json に永続化（破損時は既定ライトにフォールバック）。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.atomicio import atomic_write_text

ACCENT = QColor("#3d7eff")


def _dark_palette() -> QPalette:
    p = QPalette()
    window = QColor("#2b2d31")
    base = QColor("#1e1f22")
    text = QColor("#e8e8e8")
    disabled = QColor("#7a7a7a")
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, window)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, window)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, disabled)
    p.setColor(QPalette.ColorRole.Highlight, ACCENT)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, ACCENT)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return p


class ThemeManager:
    def __init__(self, settings_path: str | Path) -> None:
        self._path = Path(settings_path)
        self._settings = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        try:
            atomic_write_text(
                self._path,
                json.dumps(self._settings, ensure_ascii=False, indent=2))
        except OSError:
            pass  # 設定保存失敗でアプリは止めない

    def get(self, key: str, default=None):
        return self._settings.get(key, default)

    def set(self, key: str, value) -> None:
        self._settings[key] = value
        self._save()

    @property
    def theme(self) -> str:
        return self._settings.get("theme", "light")

    def apply(self, app: QApplication, theme: str | None = None) -> None:
        theme = theme or self.theme
        app.setStyle("Fusion")
        if theme == "dark":
            app.setPalette(_dark_palette())
        else:
            app.setPalette(app.style().standardPalette())
        self._settings["theme"] = theme
        self._save()

    def toggle(self, app: QApplication) -> str:
        new_theme = "dark" if self.theme == "light" else "light"
        self.apply(app, new_theme)
        return new_theme
