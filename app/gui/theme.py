"""ダーク/ライトテーマ。Fusion スタイル + QPalette で外部依存なし。

設定は config/settings.json に永続化（破損時は既定ライトにフォールバック）。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from app.atomicio import atomic_write_text

ACCENT = QColor("#3d7eff")

# アプリ共通フォント。英数字=Segoe UI → 日本語=Yu Gothic UI の順でフォールバック。
# setFamilies() は Qt 5.13+ で有効。pointSize はポイント指定なので高 DPI でも崩れない。
APP_FONT_FAMILIES = ["Segoe UI", "Yu Gothic UI", "sans-serif"]
APP_FONT_SIZE_PT = 9


def app_font() -> QFont:
    """アプリ全体に適用する共通フォントを返す。"""
    f = QFont()
    f.setFamilies(APP_FONT_FAMILIES)
    f.setPointSize(APP_FONT_SIZE_PT)
    return f


def _dark_palette() -> QPalette:
    p = QPalette()
    window = QColor("#2b2d31")
    base = QColor("#1e1f22")
    text = QColor("#e8e8e8")
    disabled = QColor("#7a7a7a")

    # Active グループ
    active_colors = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: base,
        QPalette.ColorRole.AlternateBase: window,
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: window,
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.ToolTipBase: base,
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: disabled,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: QColor("#ffffff"),
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, color in active_colors.items():
        p.setColor(QPalette.ColorGroup.Active, role, color)
        # Inactive も同じ色で明示設定（未設定だと QPalette() 生成元＝旧パレットの値が残る）
        p.setColor(QPalette.ColorGroup.Inactive, role, color)

    # Disabled グループ
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
        # setStyle() 直後にフォントを再設定（スタイル変更でリセットされる環境への保険）
        app.setFont(app_font())
        # OS のタイトルバー（非クライアント領域）も Qt 経由で追従させる。
        self._apply_color_scheme(app, theme)
        if theme == "dark":
            app.setPalette(_dark_palette())
            # QPalette の伝播が非同期の OS コールバックで上書きされる場合があるため、
            # スタイルシートでビュー系ウィジェットの文字色・背景色を明示的に固定する。
            app.setStyleSheet(
                "QTreeView, QTreeWidget, QTableView, QListView {"
                "  color: #e8e8e8;"
                "  background-color: #1e1f22;"
                "}"
                "QTreeView::item, QTreeWidget::item, QTableView::item {"
                "  color: #e8e8e8;"
                "}"
                "QHeaderView::section {"
                "  color: #e8e8e8;"
                "  background-color: #2b2d31;"
                "}"
            )
        else:
            app.setPalette(app.style().standardPalette())
            app.setStyleSheet("")
        self._settings["theme"] = theme
        self._save()

    @staticmethod
    def _apply_color_scheme(app: QApplication, theme: str) -> None:
        """ネイティブのカラースキーム（タイトルバー等）を切替。Qt6.5+。"""
        from PySide6.QtCore import Qt
        hints = app.styleHints()
        if hasattr(hints, "setColorScheme"):
            hints.setColorScheme(
                Qt.ColorScheme.Dark if theme == "dark"
                else Qt.ColorScheme.Light)

    def toggle(self, app: QApplication) -> str:
        new_theme = "dark" if self.theme == "light" else "light"
        self.apply(app, new_theme)
        return new_theme
