"""ダーク/ライトテーマ。Fusion + QPalette + QSS で外部依存なし。

設定は config/settings.json に永続化（破損時は既定ライトにフォールバック）。
色は TOKENS（dark/light）に集約し、QPalette と QSS の両方を同じ値から生成する。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from app.atomicio import atomic_write_text

# アプリ共通フォント。英数字=Segoe UI → 日本語=Yu Gothic UI の順でフォールバック。
APP_FONT_FAMILIES = ["Segoe UI", "Yu Gothic UI", "sans-serif"]
APP_FONT_SIZE_PT = 9

# --- デザイントークン -------------------------------------------------------
# 3層の明度（bg=最暗 / surface=パネル / elevated=行ストライプ・タブ選択）
# + border（細線）+ accent。dark/light で同じキー構成。
TOKENS: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#16171a",
        "surface": "#1e2024",
        "elevated": "#262830",

        "app_base": "#141518",
        "card": "#1c1d21",

        "border": "#34363d",
        "border_str": "#3f424a",

        "text": "#e3e5ea",
        "text_sub": "#a0a4ad",
        "text_hint": "#70737c",

        "accent": "#5b9cf6",

        "sel_bg": "rgba(91,156,246,0.28)",
        "hover_bg": "rgba(255,255,255,0.07)",

        "scrollbar": "#4a4d55",

        "status_ok": "#66bb6a",
        "status_unchanged": "#9e9e9e",
        "status_warn": "#ffa726",
        "status_error": "#ef5350",
    },
    "light": {
        "bg": "#ffffff",
        "surface": "#f5f6f8",
        "elevated": "#f2f3f5",

        "app_base": "#eceef1",
        "card": "#ffffff",

        "border": "#e3e5ea",
        "border_str": "#d0d3da",

        "text": "#1f2329",
        "text_sub": "#5c616b",
        "text_hint": "#8b909a",

        "accent": "#2f6fe0",

        "sel_bg": "rgba(47,111,224,0.14)",
        "hover_bg": "rgba(0,0,0,0.04)",

        "scrollbar": "#c7cad1",

        "status_ok": "#2e7d32",
        "status_unchanged": "#9e9e9e",
        "status_warn": "#ef6c00",
        "status_error": "#c62828",
    },
}

# 既存コードが import している定数（file_pane の枠線描画等）を維持。
ACCENT = QColor(TOKENS["dark"]["accent"])


def status_colors(theme: str = "light") -> dict[str, QColor]:
    """ステータス表示用カラーをテーマ別に返す（rename_dialog 等で使用）。"""
    t = TOKENS["dark"] if theme == "dark" else TOKENS["light"]
    return {
        "ok": QColor(t["status_ok"]),
        "unchanged": QColor(t["status_unchanged"]),
        "warn": QColor(t["status_warn"]),
        "error": QColor(t["status_error"]),
    }


def app_font() -> QFont:
    """アプリ全体に適用する共通フォントを返す。"""
    f = QFont()
    f.setFamilies(APP_FONT_FAMILIES)
    f.setPointSize(APP_FONT_SIZE_PT)
    return f


def _palette(t: dict[str, str]) -> QPalette:
    """トークンから QPalette を生成（dark/light 共通）。"""
    p = QPalette()
    window = QColor(t["surface"])
    base = QColor(t["bg"])
    alt = QColor(t["elevated"])
    text = QColor(t["text"])
    disabled = QColor(t["text_hint"])
    accent = QColor(t["accent"])

    roles = {
        QPalette.ColorRole.Window: window,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: base,
        QPalette.ColorRole.AlternateBase: alt,
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: window,
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.ToolTipBase: base,
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: disabled,
        QPalette.ColorRole.Highlight: accent,
        QPalette.ColorRole.HighlightedText: QColor("#ffffff"),
        QPalette.ColorRole.Link: accent,
        QPalette.ColorRole.Mid: QColor(t["border"]),
    }

    for role, color in roles.items():
        p.setColor(QPalette.ColorGroup.Active, role, color)
        # Inactive も同色で明示（未設定だと旧パレットの値が残る）
        p.setColor(QPalette.ColorGroup.Inactive, role, color)

    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)

    return p


def _stylesheet(t: dict[str, str]) -> str:
    """トークンからアプリ全体の QSS を生成（dark/light 共通）。"""
    return f"""
/* ---- ビュー（一覧 / ツリー / アイコン） ---- */
QTreeView, QTreeWidget, QTableView, QListView {{
    background-color: {t['bg']};
    alternate-background-color: {t['elevated']};
    color: {t['text']};
    border: none;
    outline: 0;
    selection-background-color: {t['sel_bg']};
    selection-color: {t['text']};
}}

QTableView::item, QTreeView::item, QTreeWidget::item, QListView::item {{
    padding: 5px 6px;
    border: none;
    color: {t['text']};
}}

QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{
    background-color: {t['hover_bg']};
}}

QTableView::item:selected, QTreeView::item:selected,
QTreeWidget::item:selected, QListView::item:selected {{
    background-color: {t['sel_bg']};
    color: {t['text']};
}}

/* ---- 列ヘッダ ---- */
QHeaderView::section {{
    background-color: {t['surface']};
    color: {t['text_sub']};
    padding: 5px 8px;
    border: none;
    border-bottom: 1px solid {t['border']};
    border-right: 1px solid {t['border']};
    font-weight: 400;
}}

QHeaderView::section:hover {{
    color: {t['text']};
}}

/* ---- タブ ---- */
QTabBar {{
    qproperty-drawBase: 0;
    border-bottom: 1px solid {t['border']};
}}

QTabBar::tab {{
    background: {t['surface']};
    color: {t['text_sub']};
    padding: 6px 14px;
    margin-right: 2px;
    border: 1px solid {t['border']};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QTabBar::tab:hover {{
    background: {t['elevated']};
    color: {t['text']};
}}

QTabBar::tab:selected {{
    background: {t['elevated']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-bottom: none;
    border-top: 2px solid {t['accent']};
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
}}

QTabBar::close-button {{
    margin-left: 6px;
    subcontrol-position: right;
}}

QTabBar::close-button:hover {{
    background: {t['hover_bg']};
    border-radius: 3px;
}}

/* ---- 入力欄（フィルタ / パス直接入力 / ダイアログ） ---- */
QLineEdit {{
    background-color: {t['bg']};
    color: {t['text']};
    border: 1px solid {t['border_str']};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {t['accent']};
    selection-color: #ffffff;
}}

QLineEdit:focus {{
    border: 1px solid {t['accent']};
}}

/* ---- ツールボタン（パンくず / ？ / アイコンボタン） ---- */
QToolButton {{
    background: transparent;
    color: {t['text']};
    border: none;
    border-radius: 5px;
    padding: 3px 6px;
}}

QToolButton:hover {{
    background: {t['hover_bg']};
}}

QToolButton:pressed {{
    background: {t['sel_bg']};
}}

/* ---- アプリのベース背景（カード間の余白に見える層） ---- */
QMainWindow, QWidget#centralRoot {{
    background-color: {t['app_base']};
}}

/* ---- カード（左サイドバー / 右メイン） ---- */
#leftSidebarBox, #rightContentBox {{
    background-color: {t['card']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}

/* ---- メインスプリッタのハンドルはベース背景を透過 ---- */
QSplitter#mainSplitter::handle {{
    background-color: transparent;
}}

/* ---- 折りたたみセクションのヘッダ（サイドバー見出し） ---- */
#collapsibleHeader {{
    background-color: {t['surface']};
    font-size: 11px;
    color: {t['text_sub']};
}}

#collapsibleHeader:hover {{
    background: {t['elevated']};
    color: {t['text']};
}}

/* ---- スプリッタの仕切り（点線ハンドル廃止→細線） ---- */
QSplitter::handle {{
    background-color: {t['border']};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QSplitter::handle:hover {{
    background-color: {t['accent']};
}}

/* ---- スクロールバー（スリム・オーバーレイ風） ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {t['scrollbar']};
    border-radius: 5px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: {t['text_hint']};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {t['scrollbar']};
    border-radius: 5px;
    min-width: 28px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {t['text_hint']};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---- ステータスのラベル（objectName で限定） ---- */
QLabel#statusLabel {{
    color: {t['text_sub']};
}}

/* ---- タブ閉じるボタン（カスタム QToolButton） ---- */
QToolButton#tabClose {{
    color: {t['text_hint']};
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 13px;
}}

QToolButton#tabClose:hover {{
    color: {t['text']};
    background: {t['hover_bg']};
}}

/* ---- パスボックス（パンくずバー枠） ---- */
QFrame#pathBox {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 6px;
}}

QFrame#pathBox QToolButton {{
    background: transparent;
    border: none;
    border-radius: 0;
    color: {t['text']};
}}

QFrame#pathBox QLabel {{
    background: transparent;
    border: none;
    color: {t['text_hint']};
}}
"""


class ThemeManager:
    def __init__(self, settings_path: str | Path) -> None:
        self._path = Path(settings_path)
        self._settings = self._load()
        # 言語をここで適用する。MainWindow は theme_manager 生成後・UI 構築前の
        # この時点を通るため、_() を使う全ウィジェットが正しい言語で生成される
        # （main.py は MainWindow() を theme.apply() より先に呼ぶため apply() では遅い）。
        from app.i18n import apply_language
        apply_language(self._settings.get("language", "ja"))

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        try:
            atomic_write_text(
                self._path,
                json.dumps(self._settings, ensure_ascii=False, indent=2),
            )
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
        t = TOKENS["dark"] if theme == "dark" else TOKENS["light"]

        app.setStyle("Fusion")
        app.setFont(app_font())          # スタイル変更でリセットされる環境への保険
        self._apply_color_scheme(app, theme)  # OS タイトルバー等を追従
        app.setPalette(_palette(t))
        app.setStyleSheet(_stylesheet(t))

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
                else Qt.ColorScheme.Light
            )

    def toggle(self, app: QApplication) -> str:
        new_theme = "dark" if self.theme == "light" else "light"
        self.apply(app, new_theme)
        return new_theme