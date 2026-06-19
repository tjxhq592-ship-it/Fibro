"""パネルごとの Ctrl+ホイール ズーム（75%〜200%）。

ビュー（ツリー/テーブル）にイベントフィルタを仕込み、Ctrl 押下中のホイールで
フォントとアイコンサイズを拡大・縮小する。倍率は共有 settings に保存して
次回起動時へ引き継ぐ。各ビューは独立した倍率を持つ（key で識別）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel


class _ZoomToast(QLabel):
    """ビュー中央に一時表示する倍率インジケーター。"""

    _DURATION_MS = 1200

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background: rgba(30,30,30,200);"
            "color: #fff;"
            "border-radius: 6px;"
            "padding: 4px 10px;"
            "font-size: 13pt;"
            "font-weight: bold;"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_scale(self, scale: float) -> None:
        self.setText(f"{round(scale * 100)}%")
        self.adjustSize()
        # 親（ビューのビューポート）中央に配置
        p = self.parent()
        if p is not None:
            pw, ph = p.width(), p.height()
            self.move((pw - self.width()) // 2, (ph - self.height()) // 2)
        self.show()
        self.raise_()
        self._timer.start(self._DURATION_MS)


class ZoomController(QObject):
    MIN = 0.75
    MAX = 2.0
    STEP = 0.1

    def __init__(self, view, key: str, settings=None,
                 base_icon: int | None = None) -> None:
        super().__init__(view)  # view を親にして GC されないようにする
        self._view = view
        self._key = key
        self._settings = settings

        self._base_pt = view.font().pointSizeF()
        if self._base_pt <= 0:
            self._base_pt = QApplication.font().pointSizeF()
        if self._base_pt <= 0:
            self._base_pt = 9.0
        if base_icon is None:
            sz = view.iconSize()
            base_icon = sz.width() if sz.width() > 0 else 16
        self._base_icon = base_icon

        # 基準の行高（等倍時）。アイコン or 文字の高い方＋上下パディング。
        # 行高も倍率で拡大するので、ズームしても上下の隙間がバランスよく保たれる。
        base_font = QFont(view.font())
        base_font.setPointSizeF(self._base_pt)
        line = QFontMetrics(base_font).height()
        self._base_row = max(self._base_icon, line) + 8

        self._scale = self._load()
        self._apply()
        view.installEventFilter(self)
        vp = view.viewport()
        vp.installEventFilter(self)
        self._toast = _ZoomToast(vp)

    # ---- 永続化 ----
    def _load(self) -> float:
        if self._settings is None:
            return 1.0
        z = self._settings.get("zoom", {}) or {}
        try:
            return self._clamp(float(z.get(self._key, 1.0)))
        except (TypeError, ValueError):
            return 1.0

    def _save(self) -> None:
        if self._settings is None:
            return
        z = dict(self._settings.get("zoom", {}) or {})
        z[self._key] = round(self._scale, 3)
        self._settings.set("zoom", z)

    # ---- 適用 ----
    def _clamp(self, scale: float) -> float:
        return max(self.MIN, min(self.MAX, scale))

    def _apply(self) -> None:
        font = QFont(self._view.font())
        font.setPointSizeF(self._base_pt * self._scale)
        self._view.setFont(font)
        s = max(1, round(self._base_icon * self._scale))
        self._view.setIconSize(QSize(s, s))
        # 行高（上下の隙間）も倍率に比例させる。QTableView（ファイル一覧）は
        # 縦ヘッダーの既定セクションサイズで全行の高さを制御する。
        vheader = getattr(self._view, "verticalHeader", None)
        if callable(vheader):
            header = vheader()
            if header is not None:
                header.setDefaultSectionSize(
                    max(1, round(self._base_row * self._scale)))

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, scale: float, show_toast: bool = False) -> None:
        scale = self._clamp(scale)
        if abs(scale - self._scale) < 1e-6:
            return
        self._scale = scale
        self._apply()
        self._save()
        if show_toast:
            self._toast.show_scale(self._scale)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API
        if (event.type() == QEvent.Type.Wheel
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            dy = event.angleDelta().y()
            if dy != 0:
                step = self.STEP if dy > 0 else -self.STEP
                self.set_scale(self._scale + step, show_toast=True)
            return True  # 既定のスクロール挙動を抑止
        return super().eventFilter(obj, event)
