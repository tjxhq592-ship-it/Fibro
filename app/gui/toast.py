"""画面下端に積むトースト通知（4分類: status/completion/warning/error）。

statusBar().showMessage の置き換え先。MainWindow 上のオーバーレイ
（レイアウト外・move() ベース）として配置するため、アニメーションしても
レイアウト再計算は走らない（モーション設計原則）。

- 入場: 下から translateY + フェード（MOTION.BASE / EASE_OUT）
- 退出: 同方向（下）へ translateY + フェード（MOTION.FAST）— 空間的一貫性
- 連続発生時の詰め直しは make_animation が現在位置から再ターゲットする
  （キーフレーム的なやり直しをしない）
- ホバー中は自動消滅タイマーを停止し、離脱で残り時間から再開する
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget,
)

from app.gui.motion import MOTION, make_animation
from app.gui.theme import current_tokens

# 分類 → (アイコン文字, 色トークンキー)。status は info 系＝アクセント色。
_KINDS: dict[str, tuple[str, str]] = {
    "status":     ("ℹ", "accent"),
    "completion": ("✓", "status_ok"),
    "warning":    ("⚠", "status_warn"),
    "error":      ("✕", "status_error"),
}

_GAP = 8              # トースト間の縦間隔
_MARGIN = 12          # ウィンドウ下端（ステータスバー上）からの余白
_SLIDE = 16           # 入退場の縦移動量
_DURATION_MS = 3500   # 自動消滅までの表示時間
_MAX_WIDTH = 420


class _Toast(QFrame):
    """単一のトースト。自動消滅タイマーとホバー一時停止を持つ。"""

    def __init__(self, kind: str, text: str, parent: QWidget,
                 on_expired) -> None:
        super().__init__(parent)
        icon, color_key = _KINDS.get(kind, _KINDS["status"])
        t = current_tokens()
        color = t[color_key]
        self.setStyleSheet(f"""
            _Toast {{
                background-color: {t['card']};
                border: 1px solid {t['border_str']};
                border-left: 3px solid {color};
                border-radius: 8px;
            }}
            QLabel {{ background: transparent; border: none; }}
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 14, 8)
        row.setSpacing(8)
        icon_label = QLabel(icon)
        icon_label.setStyleSheet(
            f"color: {color}; font-weight: bold;"
            "background: transparent; border: none;")
        text_label = QLabel(text)
        text_label.setStyleSheet(
            f"color: {t['text']}; background: transparent; border: none;")
        text_label.setWordWrap(True)
        row.addWidget(icon_label)
        row.addWidget(text_label, stretch=1)

        self.setMaximumWidth(_MAX_WIDTH)
        self.adjustSize()

        self.exiting = False  # 退出開始後はホバー・詰め直しの対象外

        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)

        self._remaining = _DURATION_MS
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: on_expired(self))

    def start_countdown(self) -> None:
        self._timer.start(self._remaining)

    def enterEvent(self, event) -> None:  # noqa: N802 — Qt API
        # ホバー中は自動消滅を止める（読んでいる最中に消さない）
        if self._timer.isActive():
            self._remaining = max(500, self._timer.remainingTime())
            self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 — Qt API
        if not self.exiting:
            self._timer.start(self._remaining)
        super().leaveEvent(event)


class ToastManager(QObject):
    """MainWindow 上にトーストを積み、入退場・詰め直しを駆動する。"""

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._toasts: list[_Toast] = []
        window.installEventFilter(self)

    def show_toast(self, kind: str, text: str) -> None:
        """kind: "status" | "completion" | "warning" | "error"。"""
        toast = _Toast(kind, text, self._window, self._dismiss)
        self._toasts.append(toast)
        toast.show()
        toast.raise_()

        targets = self._layout_targets()
        final = targets[toast]
        # 入場: 最終位置の少し下・不透明度0から（「無から出現」ではなく下端からの導線）
        toast.move(final + QPoint(0, _SLIDE))
        make_animation(toast, b"pos", final, MOTION.BASE, MOTION.EASE_OUT)
        make_animation(toast.effect, b"opacity", 1.0, MOTION.BASE,
                       MOTION.EASE_OUT)
        # 既存トーストは現在位置から新ターゲットへ詰め直し（再ターゲット）
        self._apply_targets(targets, skip=toast)
        toast.start_countdown()

    # ---- 内部 ----
    def _dismiss(self, toast: _Toast) -> None:
        """退出開始。下（入場と同方向）へスライドしながらフェードアウト。"""
        if toast.exiting:
            return
        toast.exiting = True
        make_animation(toast, b"pos", toast.pos() + QPoint(0, _SLIDE),
                       MOTION.FAST, MOTION.EASE_OUT)
        make_animation(toast.effect, b"opacity", 0.0, MOTION.FAST,
                       MOTION.EASE_OUT,
                       on_finished=lambda: self._remove(toast))

    def _remove(self, toast: _Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        toast.deleteLater()
        self._apply_targets(self._layout_targets())

    def _layout_targets(self) -> dict[_Toast, QPoint]:
        """各トーストの目標位置。新しいものほど下（下端中央に積む）。"""
        w = self._window
        y = w.height() - _MARGIN
        status_bar = w.statusBar() if hasattr(w, "statusBar") else None
        if status_bar is not None and status_bar.isVisible():
            y -= status_bar.height()
        targets: dict[_Toast, QPoint] = {}
        for toast in reversed(self._toasts):
            if toast.exiting:
                continue
            y -= toast.height()
            targets[toast] = QPoint((w.width() - toast.width()) // 2, y)
            y -= _GAP
        return targets

    def _apply_targets(self, targets: dict[_Toast, QPoint],
                       skip: _Toast | None = None) -> None:
        for toast, pos in targets.items():
            if toast is not skip:
                make_animation(toast, b"pos", pos, MOTION.BASE,
                               MOTION.EASE_OUT)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API
        # ウィンドウリサイズには即時追従（リサイズ演出は不要）
        if obj is self._window and event.type() == QEvent.Type.Resize:
            for toast, pos in self._layout_targets().items():
                toast.move(pos)
        return super().eventFilter(obj, event)
