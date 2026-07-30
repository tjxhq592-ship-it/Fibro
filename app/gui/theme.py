"""カラーテーマ（6種）。Fusion + QPalette + QSS で外部依存なし。

アプリ全般設定（language/initial_dir/view_mode 等）は config/settings.json に
永続化する。テーマ（"theme" キー）はプロジェクト範囲のため ThemeManager では
保持せず、ProjectSettingsStore に保存された値を apply(app, theme=...) で
呼び出し側が明示的に渡す。
色は TOKENS（テーマ名 → ThemeTokens のセマンティックトークン）に集約し、
QPalette と QSS の両方を同じ値から生成する。未知のテーマ名は light に
フォールバックする。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from app.atomicio import atomic_write_text

# アプリ共通フォント。英数字=Segoe UI → 日本語=Yu Gothic UI の順でフォールバック。
APP_FONT_FAMILIES = ["Segoe UI", "Yu Gothic UI", "sans-serif"]
APP_FONT_SIZE_PT = 9

# --- テーマ一覧 -------------------------------------------------------------
# メニュー表示順。内部キーもこの順序でサブメニューを構築する。
# 明るい順に並べ、選ぶ側が明暗のどのあたりかを一目で分かるようにする。
THEME_ORDER: list[str] = [
    "light", "sepia", "dark", "navy", "coffee", "high_contrast",
]

# 表示名と明暗分類（OSタイトルバー・アイコン色分岐に使う）
THEME_META: dict[str, dict] = {
    "light":         {"label_ja": "ライト",            "label_en": "Light",         "is_dark": False},
    "sepia":         {"label_ja": "セピア",            "label_en": "Sepia",         "is_dark": False},
    "dark":          {"label_ja": "ダーク",            "label_en": "Dark",          "is_dark": True},
    "navy":          {"label_ja": "ネイビー",          "label_en": "Navy",          "is_dark": True},
    "coffee":        {"label_ja": "コーヒー",          "label_en": "Coffee",        "is_dark": True},
    "high_contrast": {"label_ja": "ハイコントラスト",  "label_en": "High Contrast", "is_dark": True},
}

# --- デザイントークン -------------------------------------------------------
# 3層の明度（bg=一覧の地色 / surface=パネル / elevated=行ストライプ・タブ選択）
# + border（細線）+ accent。全テーマで同じキー構成。
#
# 配色は「並べたときに互いに違って見えること」と「3つの面すべてで文字が読める
# こと」を基準に設計している。文字は bg の上だけでなく行ストライプやカードの
# 上にも乗るため、コントラストは bg / surface / elevated の3面すべてに対して
# 満たす必要がある（最も厳しいのはたいてい elevated）。
# 数値上の裏付けは tools/check_theme_palette.py が機械的に検証する。値を変える
# ときは pytest tests/test_theme_palette.py を通すこと。
#
# status_* の色相（緑/灰/橙/赤）はテーマをまたいで固定する。「赤＝エラー」の
# 学習を壊さないためで、テーマごとに変えるのは明度と彩度だけ。


class ThemeTokens(TypedDict):
    """テーマ1件分のトークン構成。キー漏れは静的解析で検出する。"""

    bg: str
    surface: str
    elevated: str
    app_base: str
    card: str
    border: str
    border_str: str
    text: str
    text_sub: str
    text_hint: str
    icon: str
    accent: str
    on_accent: str
    sel_bg: str
    hover_bg: str
    pressed_bg: str
    overlay_bg: str
    scrollbar: str
    status_ok: str
    status_unchanged: str
    status_warn: str
    status_error: str


TOKENS: dict[str, ThemeTokens] = {
    "light": {
        "bg": "#ffffff", "surface": "#f3f4f7", "elevated": "#e9ebef",
        "app_base": "#e3e5e9", "card": "#f3f4f7",
        "border": "#d4d6d8", "border_str": "#b7babd",
        "text": "#1a1c20", "text_sub": "#4a5058", "text_hint": "#6f7680",
        "icon": "#1a1c20",
        "accent": "#1f5fd0", "on_accent": "#ffffff",
        "sel_bg": "#6b85d7", "hover_bg": "#dcdde1", "pressed_bg": "#cbccd0",
        "overlay_bg": "#e9ebef", "scrollbar": "#989da2",
        "status_ok": "#00661d", "status_unchanged": "#575757",
        "status_warn": "#7d4c00", "status_error": "#b2001d",
    },
    "sepia": {
        "bg": "#ece0c6", "surface": "#e4d6b6", "elevated": "#dac9a2",
        "app_base": "#d4c49e", "card": "#e4d6b6",
        "border": "#c6bba4", "border_str": "#ada28d",
        "text": "#332c24", "text_sub": "#5c5142", "text_hint": "#756956",
        "icon": "#332c24",
        "accent": "#7f4310", "on_accent": "#ffffff",
        "sel_bg": "#af947b", "hover_bg": "#cdbd98", "pressed_bg": "#beaf8d",
        "overlay_bg": "#dac9a2", "scrollbar": "#948976",
        "status_ok": "#004f14", "status_unchanged": "#434343",
        "status_warn": "#613b00", "status_error": "#8c0014",
    },
    "dark": {
        "bg": "#141518", "surface": "#202227", "elevated": "#2b2e34",
        "app_base": "#0e0f12", "card": "#202227",
        "border": "#2f3137", "border_str": "#43474e",
        "text": "#e7e9ed", "text_sub": "#a8aeb8", "text_hint": "#7c838f",
        "icon": "#e7e9ed",
        "accent": "#6aa8ff", "on_accent": "#000000",
        "sel_bg": "#40669c", "hover_bg": "#35373c", "pressed_bg": "#404347",
        "overlay_bg": "#2b2e34", "scrollbar": "#656a74",
        "status_ok": "#4dc55b", "status_unchanged": "#adadad",
        "status_warn": "#ef9a19", "status_error": "#fe8c80",
    },
    "navy": {
        "bg": "#172a48", "surface": "#20375c", "elevated": "#27406b",
        "app_base": "#11213a", "card": "#20375c",
        "border": "#36455f", "border_str": "#4a5a73",
        "text": "#e2eaf7", "text_sub": "#adbfd9", "text_hint": "#8598b6",
        "icon": "#e2eaf7",
        "accent": "#5ec8e0", "on_accent": "#000000",
        "sel_bg": "#326c82", "hover_bg": "#344970", "pressed_bg": "#425377",
        "overlay_bg": "#27406b", "scrollbar": "#6d7e99",
        "status_ok": "#69de72", "status_unchanged": "#c6c6c6",
        "status_warn": "#ffb964", "status_error": "#feb4aa",
    },
    "coffee": {
        "bg": "#2b2118", "surface": "#372b1f", "elevated": "#423426",
        "app_base": "#221912", "card": "#372b1f",
        "border": "#463d32", "border_str": "#5c5245",
        "text": "#ece2d2", "text_sub": "#b8a892", "text_hint": "#9a8c78",
        "icon": "#ece2d2",
        "accent": "#f09339", "on_accent": "#000000",
        "sel_bg": "#8a5524", "hover_bg": "#4a3d30", "pressed_bg": "#54493d",
        "overlay_bg": "#423426", "scrollbar": "#807463",
        "status_ok": "#58cf64", "status_unchanged": "#b8b8b8",
        "status_warn": "#fba527", "status_error": "#fea093",
    },
    "high_contrast": {
        "bg": "#000000", "surface": "#0d0d0d", "elevated": "#1c1c1c",
        "app_base": "#000000", "card": "#0d0d0d",
        # 輪郭を最大限見せることがこのテーマの存在理由なので、border だけは
        # 他テーマの ΔE00 帯（bg から 6〜12 / 12〜20）を外して純白を使う。
        "border": "#ffffff", "border_str": "#ffffff",
        "text": "#ffffff", "text_sub": "#e0e0e0", "text_hint": "#bdbdbd",
        "icon": "#ffffff",
        # 黄のアクセント上では黒文字（白抜きだと視認不能）。
        "accent": "#ffff00", "on_accent": "#000000",
        "sel_bg": "#767600", "hover_bg": "#262626", "pressed_bg": "#323232",
        "overlay_bg": "#1c1c1c", "scrollbar": "#ffffff",
        # 純赤 #ff0000 は elevated 上で 4.26:1 しかなく基準に届かないため、
        # 赤の色相を保ったまま明度を上げた値にしている。
        "status_ok": "#00ff57", "status_unchanged": "#9a9a9a",
        "status_warn": "#fca000", "status_error": "#fe6a5f",
    },
}

# 現在テーマのアクセント色。ThemeManager.apply() が更新するモジュール状態。
# import 時に固定される旧 ACCENT 定数はテーマ追従しないため廃止した。
_current_accent = QColor(TOKENS["light"]["accent"])


def current_accent() -> QColor:
    """現在のテーマのアクセントカラーを返す（file_pane 等のカスタム描画用）。"""
    return _current_accent


# 現在テーマのトークン辞書。ThemeManager.apply() が更新するモジュール状態。
_current_tokens: ThemeTokens = TOKENS["light"]


def current_tokens() -> ThemeTokens:
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


def _palette(t: ThemeTokens) -> QPalette:
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
        QPalette.ColorRole.HighlightedText: QColor(t["on_accent"]),
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


def _stylesheet(t: ThemeTokens) -> str:
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
    selection-color: {t['on_accent']};
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

/* ---- 見出し右端のアクションボタン（お気に入りの一括開閉など） ---- */
/* 見出し内に限定して、他のツールボタンへ波及させない。 */
#collapsibleHeader #headerAction {{
    background: transparent;
    border: none;
    border-radius: 3px;
    padding: 0;
}}

#collapsibleHeader #headerAction:hover {{
    background: {t['elevated']};
}}

#collapsibleHeader #headerAction:pressed {{
    background: {t['pressed_bg']};
}}

/* 折りたたみ中は本体が無いので押せない＝アイコンを薄く見せる */
#collapsibleHeader #headerAction:disabled {{
    background: transparent;
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

/* ---- 検索パネルの進行バー（3px・SEARCHING 中のみ表示のインジターミネート。
        chunk は accent 帯の左右端を透明へフェードさせ「流れる」表現にする） ---- */
QProgressBar#searchProgress {{
    background: transparent;
    border: none;
    min-height: 3px;
    max-height: 3px;
}}

QProgressBar#searchProgress::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 transparent, stop:0.25 {t['accent']},
        stop:0.75 {t['accent']}, stop:1 transparent);
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

