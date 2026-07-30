"""手動で開閉できる折りたたみセクション。

クリック可能なヘッダーバー（シェブロンアイコン左＋タイトル）と本体ウィジェットを
縦に並べ、ヘッダークリックで本体を畳む／開く。縦 QSplitter に複数並べると、
畳んだ分の縦スペースが残りの展開セクションへ自動再配分される。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from app.gui.icons import material_pixmap
from app.gui.motion import MOTION, make_animation
from app.gui.theme import current_tokens

# 見出しバーに載せるアクションボタンの寸法。見出し高（タブ行に合わせて可変）を
# 超えないよう小さめに固定し、アイコンは material_pixmap で実寸ラスタライズする
# （QIcon 経由の縮小だと 14px では輪郭がぼやける）。
_ACTION_BTN_SIZE = 18
_ACTION_ICON_SIZE = 14

# Qt が縦サイズ無制限に使う番兵値（QWIDGETSIZE_MAX）。
_QWIDGETSIZE_MAX = (1 << 24) - 1

# Tabler Icons → chevron-right / chevron-down の SVG テンプレート。
# stroke-width=2, stroke-linecap/linejoin=round で Tabler アウトラインの見た目を再現。
_CHEVRON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
     fill="none" stroke="{color}" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round">
  <path d="{path}"/>
</svg>"""

# chevron-right: ▷ 相当（折りたたみ時）
_PATH_RIGHT = "M9 6l6 6-6 6"
# chevron-down: ▽ 相当（展開時）
_PATH_DOWN = "M6 9l6 6 6-6"


def _chevron_pixmap(collapsed: bool) -> QPixmap:
    """折りたたみ状態に応じた chevron QPixmap を返す。

    stroke は現在テーマの text_hint トークン（補助的な図形＝ヒント系の色）。
    """
    color = current_tokens()["text_hint"]
    path = _PATH_RIGHT if collapsed else _PATH_DOWN
    svg = _CHEVRON_SVG.format(color=color, path=path).encode()
    px = QPixmap()
    px.loadFromData(QByteArray(svg), "SVG")
    return px


class _HeaderBar(QFrame):
    """全幅のクリック可能なヘッダー。クリックで clicked シグナルを emit。"""

    clicked = Signal()

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("collapsibleHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)

        self._chevron_label = QLabel()
        self._chevron_label.setFixedWidth(16)
        self._chevron_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

        self._title = QLabel(title.upper())

        row.addWidget(self._chevron_label)
        row.addWidget(self._title, stretch=1)
        self._row = row

        # 見出し右端に並ぶアクションボタン（(button, icon_name) を保持）。
        # テーマ切替時に icon_name から再描画するため名前も覚えておく。
        self._actions: list[tuple[QToolButton, str]] = []

        # 初期状態は展開（collapsed=False）
        self._collapsed = False
        self._update_chevron(collapsed=False)

    def add_action(self, icon_name: str, tooltip: str, on_click) -> QToolButton:
        """見出し右端にアイコンボタンを足す。押してもセクションは開閉しない。

        QToolButton は子ウィジェットとして自分でマウスプレスを消費するので、
        _HeaderBar.mousePressEvent には届かない＝追加のイベント制御は不要。
        ただしこれは暗黙の前提で、将来ヘッダをイベントフィルタ方式などに
        書き換えた瞬間に静かに壊れるため、回帰テストで固定してある。
        """
        btn = QToolButton(self)
        btn.setObjectName("headerAction")
        btn.setAutoRaise(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        # アイコンのみのボタンなので、支援技術向けに同じ文言を名前としても持たせる
        btn.setAccessibleName(tooltip)
        btn.setFixedSize(_ACTION_BTN_SIZE, _ACTION_BTN_SIZE)
        btn.setIconSize(QSize(_ACTION_ICON_SIZE, _ACTION_ICON_SIZE))
        btn.clicked.connect(on_click)
        # 追加順に左→右へ並ぶ（title が stretch=1 なのでまとめて右端に寄る）
        self._row.addWidget(btn)
        self._actions.append((btn, icon_name))
        self._update_action_icon(btn, icon_name)
        # 折りたたみ中に足された場合も無効状態を揃える
        btn.setEnabled(not self._collapsed)
        return btn

    def _update_action_icon(self, btn: QToolButton, icon_name: str) -> None:
        """現在テーマのアイコン色でアクションボタンの絵柄を作り直す。"""
        btn.setIcon(QIcon(material_pixmap(icon_name, _ACTION_ICON_SIZE)))

    def refresh_icons(self) -> None:
        """シェブロンとアクションボタンを現在テーマの色で描き直す。"""
        self._update_chevron(self._collapsed)
        for btn, icon_name in self._actions:
            self._update_action_icon(btn, icon_name)

    def _update_chevron(self, collapsed: bool) -> None:
        """現在テーマのトークン色でシェブロンアイコンを更新する。"""
        self._chevron_label.setPixmap(_chevron_pixmap(collapsed))

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._update_chevron(collapsed)
        # 本体が見えていないのに押せるボタンを残さない
        for btn, _icon_name in self._actions:
            btn.setEnabled(not collapsed)

    def _set_pressed(self, pressed: bool) -> None:
        """QSS の #collapsibleHeader[pressed="true"] を発火させる。

        QFrame は :pressed 疑似状態を持たないため動的プロパティで代替する。
        """
        self.setProperty("pressed", pressed)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(True)
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(False)
        super().mouseReleaseEvent(event)


class CollapsibleSection(QWidget):
    toggled = Signal(bool)  # True=折りたたみ

    def __init__(self, title: str, content: QWidget,
                 collapsed: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._content = content

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = _HeaderBar(title)
        self._header.clicked.connect(self._on_clicked)
        layout.addWidget(self._header)
        layout.addWidget(content, stretch=1)

        self._collapsed = not collapsed  # 反転させてから set で確実に適用
        self.set_collapsed(collapsed)

    def add_action(self, icon_name: str, tooltip: str, on_click) -> QToolButton:
        """見出し右端にアイコンボタンを足す（押してもセクションは開閉しない）。

        セクションの中身に依存しない汎用 API にしてあるので、履歴やクラウドの
        セクションに後から付けることもできる。
        """
        return self._header.add_action(icon_name, tooltip, on_click)

    def refresh_icons(self) -> None:
        """テーマ変更後にシェブロンとアクションボタンを描き直す。

        _update_chevron は __init__ と set_collapsed からしか呼ばれず、
        set_collapsed は同値なら早期 return するため、テーマを切り替えても
        シェブロンが前テーマの色のまま残っていた。ここで明示的に描き直す。
        """
        self._header.refresh_icons()

    def _on_clicked(self) -> None:
        self.set_collapsed(not self._collapsed)
        self.toggled.emit(self._collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._content.setVisible(not collapsed)
        self._header.set_collapsed(collapsed)
        if not collapsed and self.isVisible():
            # 展開時のみフェードイン。畳む方は即時（退出は入場より速く）。
            # 構築時（未表示）は演出しない＝起動時に4セクションが揺れない。
            self._fade_in_content()
        if collapsed:
            # Fixed ポリシーで QSplitter が余剰スペースを強制配分するのを防ぐ。
            # maximumHeight だけでは QSplitter が子の最大値を超えて拡張するため不十分。
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            # sizeHint（自然高=24px）でなく set_header_height 後の実高を使う。
            # 初期化時など実高未確定の場合は sizeHint にフォールバック。
            h = self._header.height()
            if h <= 0:
                h = self._header.sizeHint().height()
            self.setMaximumHeight(h)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            self.setMaximumHeight(_QWIDGETSIZE_MAX)

    def _fade_in_content(self) -> None:
        """展開した本体を MOTION.PANEL / EASE_OUT でフェードイン。

        セクションは QSplitter 内のレイアウト内ウィジェットで、高さの連続
        アニメは毎フレームのレイアウト再計算を誘発する（禁止事項）ため、
        レイアウト変更は setVisible の即時1回とし、演出は不透明度のみ。
        開閉を連打しても make_animation が現在の不透明度から再ターゲットする。
        """
        effect = self._content.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(self._content)
            self._content.setGraphicsEffect(effect)
            effect.setOpacity(0.0)

        def _clear_effect() -> None:
            # finished 発火中に effect（アニメの親）を破棄しないよう1tick遅延。
            # 効果を外すことで通常描画へ戻す（QGraphicsOpacityEffect の常駐回避）。
            # context に self を渡し、破棄後に発火しないようにする。
            QTimer.singleShot(
                0, self, lambda: self._content.setGraphicsEffect(None))

        make_animation(effect, b"opacity", 1.0, MOTION.PANEL, MOTION.EASE_OUT,
                       on_finished=_clear_effect)

    def set_header_height(self, height: int) -> None:
        """見出しバーの高さを固定する（タブ行と高さを揃える用）。"""
        if height < 20:
            return
        self._header.setFixedHeight(height)
        lay = self._header.layout()
        if lay is not None:
            m = lay.contentsMargins()
            lay.setContentsMargins(m.left(), 0, m.right(), 0)
        if self._collapsed:
            self.setMaximumHeight(height)
