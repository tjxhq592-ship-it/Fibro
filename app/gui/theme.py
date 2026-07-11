"""カラーテーマ（10種）。Fusion + QPalette + QSS で外部依存なし。

アプリ全般設定（language/initial_dir/view_mode 等）は config/settings.json に
永続化する。テーマ（"theme" キー）はプロジェクト範囲のため ThemeManager では
保持せず、ProjectSettingsStore に保存された値を apply(app, theme=...) で
呼び出し側が明示的に渡す。
色は TOKENS（テーマ名 → 20キーのセマンティックトークン）に集約し、
QPalette と QSS の両方を同じ値から生成する。未知のテーマ名は light に
フォールバックする。
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

# --- テーマ一覧 -------------------------------------------------------------
# メニュー表示順。内部キーもこの順序でサブメニューを構築する。
THEME_ORDER: list[str] = [
    "light", "dark", "nord", "solarized_light", "solarized_dark",
    "dracula", "gruvbox_dark", "one_dark", "monokai", "high_contrast",
]

# 表示名と明暗分類（OSタイトルバー・アイコン色分岐に使う）
THEME_META: dict[str, dict] = {
    "light":           {"label_ja": "ライト",              "label_en": "Light",           "is_dark": False},
    "dark":            {"label_ja": "ダーク",              "label_en": "Dark",            "is_dark": True},
    "nord":            {"label_ja": "Nord",                "label_en": "Nord",            "is_dark": True},
    "solarized_light": {"label_ja": "Solarized ライト",    "label_en": "Solarized Light", "is_dark": False},
    "solarized_dark":  {"label_ja": "Solarized ダーク",    "label_en": "Solarized Dark",  "is_dark": True},
    "dracula":         {"label_ja": "Dracula",             "label_en": "Dracula",         "is_dark": True},
    "gruvbox_dark":    {"label_ja": "Gruvbox ダーク",      "label_en": "Gruvbox Dark",    "is_dark": True},
    "one_dark":        {"label_ja": "One Dark",            "label_en": "One Dark",        "is_dark": True},
    "monokai":         {"label_ja": "Monokai",             "label_en": "Monokai",         "is_dark": True},
    "high_contrast":   {"label_ja": "ハイコントラスト",    "label_en": "High Contrast",   "is_dark": True},
}

# --- デザイントークン -------------------------------------------------------
# 3層の明度（bg=最暗 / surface=パネル / elevated=行ストライプ・タブ選択）
# + border（細線）+ accent。全テーマで同じキー構成。
# 各配色は公式パレット（Nord / Solarized / Dracula / Gruvbox / One Dark /
# Monokai）の値を基準に、公式に存在しない中間色（surface/elevated 段差等）は
# 近傍色から補間した近似値。high_contrast のみアクセシビリティ用の自作配色。
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
        "pressed_bg": "rgba(255,255,255,0.13)",

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
        "pressed_bg": "rgba(0,0,0,0.10)",

        "scrollbar": "#c7cad1",

        "status_ok": "#2e7d32",
        "status_unchanged": "#9e9e9e",
        "status_warn": "#ef6c00",
        "status_error": "#c62828",
    },
    "nord": {
        "bg": "#2e3440", "surface": "#3b4252", "elevated": "#434c5e",
        "app_base": "#262a33", "card": "#3b4252",
        "border": "#434c5e", "border_str": "#4c566a",
        "text": "#eceff4", "text_sub": "#d8dee9", "text_hint": "#7b88a1",
        "accent": "#88c0d0",
        "sel_bg": "rgba(136,192,208,0.28)", "hover_bg": "rgba(255,255,255,0.07)",
        "pressed_bg": "rgba(255,255,255,0.13)",
        "scrollbar": "#4c566a",
        "status_ok": "#a3be8c", "status_unchanged": "#7b88a1",
        "status_warn": "#d08770", "status_error": "#bf616a",
    },
    "solarized_light": {
        "bg": "#fdf6e3", "surface": "#eee8d5", "elevated": "#e4ddc7",
        "app_base": "#f5efd9", "card": "#fdf6e3",
        "border": "#eee8d5", "border_str": "#93a1a1",
        "text": "#586e75", "text_sub": "#657b83", "text_hint": "#93a1a1",
        "accent": "#268bd2",
        "sel_bg": "rgba(38,139,210,0.14)", "hover_bg": "rgba(0,0,0,0.04)",
        "pressed_bg": "rgba(0,0,0,0.10)",
        "scrollbar": "#93a1a1",
        "status_ok": "#859900", "status_unchanged": "#93a1a1",
        "status_warn": "#cb4b16", "status_error": "#dc322f",
    },
    "solarized_dark": {
        "bg": "#002b36", "surface": "#073642", "elevated": "#0a4657",
        "app_base": "#001e27", "card": "#073642",
        "border": "#0d4f5c", "border_str": "#586e75",
        "text": "#93a1a1", "text_sub": "#839496", "text_hint": "#657b83",
        "accent": "#268bd2",
        "sel_bg": "rgba(38,139,210,0.28)", "hover_bg": "rgba(255,255,255,0.06)",
        "pressed_bg": "rgba(255,255,255,0.12)",
        "scrollbar": "#586e75",
        "status_ok": "#859900", "status_unchanged": "#657b83",
        "status_warn": "#cb4b16", "status_error": "#dc322f",
    },
    "dracula": {
        "bg": "#282a36", "surface": "#2f313f", "elevated": "#44475a",
        "app_base": "#21222c", "card": "#282a36",
        "border": "#383a4a", "border_str": "#44475a",
        "text": "#f8f8f2", "text_sub": "#a4a8c5", "text_hint": "#6272a4",
        "accent": "#bd93f9",
        "sel_bg": "rgba(189,147,249,0.28)", "hover_bg": "rgba(255,255,255,0.07)",
        "pressed_bg": "rgba(255,255,255,0.13)",
        "scrollbar": "#6272a4",
        "status_ok": "#50fa7b", "status_unchanged": "#6272a4",
        "status_warn": "#ffb86c", "status_error": "#ff5555",
    },
    "gruvbox_dark": {
        "bg": "#282828", "surface": "#3c3836", "elevated": "#504945",
        "app_base": "#1d2021", "card": "#3c3836",
        "border": "#504945", "border_str": "#665c54",
        "text": "#ebdbb2", "text_sub": "#d5c4a1", "text_hint": "#a89984",
        "accent": "#fe8019",
        "sel_bg": "rgba(254,128,25,0.25)", "hover_bg": "rgba(255,255,255,0.06)",
        "pressed_bg": "rgba(255,255,255,0.12)",
        "scrollbar": "#7c6f64",
        "status_ok": "#b8bb26", "status_unchanged": "#928374",
        "status_warn": "#fabd2f", "status_error": "#fb4934",
    },
    "one_dark": {
        "bg": "#282c34", "surface": "#21252b", "elevated": "#2c313a",
        "app_base": "#1e2227", "card": "#282c34",
        "border": "#3a3f4b", "border_str": "#4b5263",
        "text": "#abb2bf", "text_sub": "#828997", "text_hint": "#5c6370",
        "accent": "#61afef",
        "sel_bg": "rgba(97,175,239,0.25)", "hover_bg": "rgba(255,255,255,0.06)",
        "pressed_bg": "rgba(255,255,255,0.12)",
        "scrollbar": "#4b5263",
        "status_ok": "#98c379", "status_unchanged": "#5c6370",
        "status_warn": "#d19a66", "status_error": "#e06c75",
    },
    "monokai": {
        "bg": "#272822", "surface": "#2d2e27", "elevated": "#3e3d32",
        "app_base": "#1e1f1c", "card": "#272822",
        "border": "#3e3d32", "border_str": "#49483e",
        "text": "#f8f8f2", "text_sub": "#c2c2bf", "text_hint": "#75715e",
        "accent": "#a6e22e",
        "sel_bg": "rgba(166,226,46,0.22)", "hover_bg": "rgba(255,255,255,0.07)",
        "pressed_bg": "rgba(255,255,255,0.13)",
        "scrollbar": "#75715e",
        "status_ok": "#a6e22e", "status_unchanged": "#75715e",
        "status_warn": "#fd971f", "status_error": "#f92672",
    },
    "high_contrast": {
        "bg": "#000000", "surface": "#0a0a0a", "elevated": "#1a1a1a",
        "app_base": "#000000", "card": "#0a0a0a",
        "border": "#ffffff", "border_str": "#ffffff",
        "text": "#ffffff", "text_sub": "#e0e0e0", "text_hint": "#b0b0b0",
        "accent": "#ffff00",
        "sel_bg": "rgba(255,255,0,0.35)", "hover_bg": "rgba(255,255,255,0.15)",
        "pressed_bg": "rgba(255,255,255,0.30)",
        "scrollbar": "#ffffff",
        "status_ok": "#00ff00", "status_unchanged": "#b0b0b0",
        "status_warn": "#ffaa00", "status_error": "#ff3333",
    },
}

# 現在テーマのアクセント色。ThemeManager.apply() が更新するモジュール状態。
# import 時に固定される旧 ACCENT 定数はテーマ追従しないため廃止した。
_current_accent = QColor(TOKENS["light"]["accent"])


def current_accent() -> QColor:
    """現在のテーマのアクセントカラーを返す（file_pane 等のカスタム描画用）。"""
    return _current_accent


# 現在テーマのトークン辞書。ThemeManager.apply() が更新するモジュール状態。
_current_tokens: dict[str, str] = TOKENS["light"]


def current_tokens() -> dict[str, str]:
    """現在適用中のテーマのトークン辞書を返す（トースト等のカスタム描画用）。"""
    return _current_tokens


def status_colors(theme: str = "light") -> dict[str, QColor]:
    """ステータス表示用カラーをテーマ別に返す（rename_dialog 等で使用）。"""
    t = TOKENS.get(theme, TOKENS["light"])
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

QTabBar::tab:pressed {{
    background: {t['pressed_bg']};
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
    background: {t['pressed_bg']};
    padding: 4px 6px 2px 6px; /* 1px 沈み込み（高さは維持） */
}}

/* ---- プッシュボタン（ダイアログ / 設定画面） ---- */
QPushButton {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border_str']};
    border-radius: 6px;
    padding: 5px 14px;
    min-width: 64px;
}}

QPushButton:hover {{
    background-color: {t['elevated']};
}}

QPushButton:pressed {{
    background-color: {t['pressed_bg']};
    padding: 6px 14px 4px 14px; /* 1px 沈み込み（高さは維持） */
}}

QPushButton:default {{
    border: 1px solid {t['accent']};
}}

QPushButton:disabled {{
    color: {t['text_hint']};
    border-color: {t['border']};
}}

/* ---- トップバーの現在プロジェクト名表示（未選択時は控えめな色） ---- */
QToolButton#projectNameBtn[noProject="true"] {{
    color: {t['text_hint']};
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

/* QFrame は :pressed 疑似状態を持たないため動的プロパティで表現 */
#collapsibleHeader[pressed="true"] {{
    background: {t['pressed_bg']};
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

/* ---- コピー/移動の進捗バー（pathBox と同じ角丸・枠でデザイン統一） ---- */
QProgressBar#copyProgress {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 7px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    color: {t['text_sub']};
    font-size: 10px;
}}

QProgressBar#copyProgress::chunk {{
    background: {t['accent']};
    border-radius: 6px;
    margin: 1px;
}}

/* ---- 進捗の中止ボタン（タブ閉じる ✕ と同系。hover で警告色） ---- */
QToolButton#copyCancel {{
    color: {t['text_hint']};
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0px 4px;
    font-size: 12px;
}}

QToolButton#copyCancel:hover {{
    color: {t['status_error']};
    background: {t['hover_bg']};
}}

QToolButton#copyCancel:pressed {{
    color: {t['status_error']};
    background: {t['pressed_bg']};
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

QToolButton#tabClose:pressed {{
    color: {t['text']};
    background: {t['pressed_bg']};
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

QFrame#pathBox QToolButton:hover {{
    background: {t['hover_bg']};
}}

QFrame#pathBox QToolButton:pressed {{
    background: {t['pressed_bg']};
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

    def apply(self, app: QApplication, theme: str | None = None) -> None:
        """テーマを適用する。永続化は行わない（保存はプロジェクト設定側）。

        未知のテーマ名（壊れた settings.json 等）は light にフォールバックする。
        """
        theme = theme or "light"
        if theme not in TOKENS:
            theme = "light"
        t = TOKENS[theme]

        app.setStyle("Fusion")
        app.setFont(app_font())          # スタイル変更でリセットされる環境への保険
        self._apply_color_scheme(app, theme)  # OS タイトルバー等を追従
        app.setPalette(_palette(t))
        app.setStyleSheet(_stylesheet(t))

        global _current_accent, _current_tokens
        _current_accent = QColor(t["accent"])
        _current_tokens = t

    def set_theme(self, app: QApplication, theme: str) -> str:
        """指定テーマを適用し、実際に適用されたテーマ名を返す。

        未知のテーマ名は light にフォールバックする。永続化は呼び出し側
        （ProjectSettingsStore）が返り値を保存して行う。
        """
        if theme not in TOKENS:
            theme = "light"
        self.apply(app, theme)
        return theme

    @staticmethod
    def _apply_color_scheme(app: QApplication, theme: str) -> None:
        """ネイティブのカラースキーム（タイトルバー等）を切替。Qt6.5+。"""
        from PySide6.QtCore import Qt

        hints = app.styleHints()
        if hasattr(hints, "setColorScheme"):
            is_dark = THEME_META.get(theme, THEME_META["light"])["is_dark"]
            hints.setColorScheme(
                Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light
            )

