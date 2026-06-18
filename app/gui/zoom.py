"""パネルごとの Ctrl+ホイール ズーム（75%〜200%）。

ビュー（ツリー/テーブル）にイベントフィルタを仕込み、Ctrl 押下中のホイールで
フォントとアイコンサイズを拡大・縮小する。倍率は共有 settings に保存して
次回起動時へ引き継ぐ。各ビューは独立した倍率を持つ（key で識別）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


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

        self._scale = self._load()
        self._apply()
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)

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

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, scale: float) -> None:
        scale = self._clamp(scale)
        if abs(scale - self._scale) < 1e-6:
            return
        self._scale = scale
        self._apply()
        self._save()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API
        if (event.type() == QEvent.Type.Wheel
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            dy = event.angleDelta().y()
            if dy != 0:
                step = self.STEP if dy > 0 else -self.STEP
                self.set_scale(self._scale + step)
            return True  # 既定のスクロール挙動を抑止
        return super().eventFilter(obj, event)
