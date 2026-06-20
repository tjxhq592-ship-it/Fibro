"""手動で開閉できる折りたたみセクション。

クリック可能なヘッダーバー（タイトル左＋シェブロン ∨/∧ 右）と本体ウィジェットを
縦に並べ、ヘッダークリックで本体を畳む／開く。縦 QSplitter に複数並べると、
畳んだ分の縦スペースが残りの展開セクションへ自動再配分される（折りたたみ時に
maximumHeight をヘッダー高に固定してスプリッターに最小化を強制する）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

# Qt が縦サイズ無制限に使う番兵値（QWIDGETSIZE_MAX）。
_QWIDGETSIZE_MAX = (1 << 24) - 1


class _HeaderBar(QFrame):
    """全幅のクリック可能なヘッダー。クリックで clicked シグナルを emit。"""

    clicked = Signal()

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("collapsibleHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        self._chevron = QLabel("▽")
        self._chevron.setFixedWidth(16)
        self._title = QLabel(title.upper())
        row.addWidget(self._chevron)
        row.addWidget(self._title, stretch=1)

    def set_collapsed(self, collapsed: bool) -> None:
        self._chevron.setText("▷" if collapsed else "▽")

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


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
        if collapsed:
            self.setMaximumHeight(self._header.sizeHint().height())
        else:
            self.setMaximumHeight(_QWIDGETSIZE_MAX)

    def set_header_height(self, height: int) -> None:
        """見出しバーの高さを固定する（タブ行と高さを揃える用）。"""
        self._header.setFixedHeight(height)
        lay = self._header.layout()
        if lay is not None:
            m = lay.contentsMargins()
            lay.setContentsMargins(m.left(), 0, m.right(), 0)
