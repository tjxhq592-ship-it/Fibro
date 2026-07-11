"""Fibro モーショントークン。全アニメーションはここだけを参照する。

テーマ TOKENS（theme.py）と同格の一元管理層。duration / easing の
ハードコードは禁止で、必ず MOTION.* を経由する。設計原則:

- 高頻度操作（キーボード起動・タブ/ペイン/プロジェクト切替・ナビゲーション）は
  アニメーション禁止（INSTANT）。
- 入場・退出は ease-out 系のみ。ease-in 系（InCubic 等）は全面禁止。
- UI アニメは MAX (300ms) 以内。退出は入場より速く。
- アニメーション中の再操作はロックせず、現在値から新ターゲットへ再開する
  （make_animation がこれを保証する）。
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QByteArray, QEasingCurve, QObject, QPropertyAnimation


class MOTION:
    # Duration (ms)
    INSTANT = 0        # キーボード起動の操作すべて
    FAST    = 120      # 退出、ホバー、押下フィードバック
    BASE    = 180      # トースト入場、小型ポップアップ
    PANEL   = 220      # サイドバー、プレビューパネルの開閉
    MAX     = 300      # これを超える値の新設は禁止

    # Easing
    EASE_OUT    = QEasingCurve.Type.OutQuint   # 入場・退出の既定
    EASE_IN_OUT = QEasingCurve.Type.InOutCubic # 画面内の移動のみ
    LINEAR      = QEasingCurve.Type.Linear     # プログレスバー等の実量のみ
    # ease-in 系（InCubic 等）の使用は禁止。コードレビューで機械的に弾く。

    # 実行時に reduced_motion が True なら全 duration を 0 として扱う
    reduced_motion: bool = False

    # デバッグ用スローモーション係数（検証時に 5.0 等へ変更。通常は 1.0 固定）
    scale: float = 1.0

    @classmethod
    def duration(cls, ms: int) -> int:
        assert ms <= cls.MAX, f"UI アニメは {cls.MAX}ms 以内（{ms}ms は禁止）"
        return 0 if cls.reduced_motion else int(ms * cls.scale)


def make_animation(
    target: QObject,
    prop: bytes | str,
    end_value,
    ms: int,
    easing: QEasingCurve.Type = MOTION.EASE_OUT,
    on_finished: Callable[[], None] | None = None,
) -> QPropertyAnimation | None:
    """target.prop を現在値から end_value へアニメーションする共通ラッパー。

    同じ (target, prop) で実行中のアニメーションがあれば stop し、
    現在値から新ターゲットへ再開する（割り込み可能性の担保）。
    reduced_motion（または duration 0）ではプロパティを即座に終値へ設定し
    None を返す。アニメーションの参照は target の属性として保持するため、
    呼び出し側でのライフサイクル管理は不要。
    """
    prop_b = prop if isinstance(prop, (bytes, bytearray)) else prop.encode()
    attr = f"_motion_anim_{prop_b.decode()}"

    old: QPropertyAnimation | None = getattr(target, attr, None)
    if old is not None:
        old.stop()  # stop 時点の現在値が次の startValue になる

    duration = MOTION.duration(ms)
    if duration <= 0:
        target.setProperty(prop_b.decode(), end_value)
        setattr(target, attr, None)
        if on_finished is not None:
            on_finished()
        return None

    anim = QPropertyAnimation(target, QByteArray(prop_b), target)
    anim.setDuration(duration)
    anim.setEasingCurve(easing)
    anim.setEndValue(end_value)
    if on_finished is not None:
        anim.finished.connect(on_finished)
    setattr(target, attr, anim)
    anim.start()
    return anim


def init_reduced_motion() -> None:
    """Windows の「アニメーション効果」設定を MOTION.reduced_motion へ反映する。

    SPI_GETCLIENTAREAANIMATION が OFF（ユーザーがアニメーションを無効化）なら
    全アニメーションを 0ms 化する。非 Windows・取得失敗時はアニメ有効のまま
    （フェイルオープン）。起動時に main() から一度呼ぶ。
    """
    if sys.platform != "win32":
        return
    import ctypes

    SPI_GETCLIENTAREAANIMATION = 0x1042
    enabled = ctypes.c_int(1)
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
    except OSError:
        return
    if ok:
        MOTION.reduced_motion = not bool(enabled.value)
