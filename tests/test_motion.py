"""MOTION トークン層と make_animation の割り込み・reduced motion のテスト。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation, QEasingCurve  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.gui.motion import MOTION, make_animation  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_motion():
    """テストごとに MOTION のクラス状態を初期値へ戻す。"""
    yield
    MOTION.reduced_motion = False
    MOTION.scale = 1.0


class TestMotionTokens:
    def test_duration_passthrough(self):
        assert MOTION.duration(MOTION.BASE) == MOTION.BASE

    def test_duration_zero_when_reduced(self):
        MOTION.reduced_motion = True
        assert MOTION.duration(MOTION.PANEL) == 0

    def test_duration_scale_for_slowmo_debug(self):
        MOTION.scale = 5.0
        assert MOTION.duration(100) == 500

    def test_duration_rejects_over_max(self):
        with pytest.raises(AssertionError):
            MOTION.duration(MOTION.MAX + 1)

    def test_exit_faster_than_entrance(self):
        assert MOTION.FAST < MOTION.BASE <= MOTION.PANEL <= MOTION.MAX

    def test_no_ease_in_tokens(self):
        """MOTION が公開する easing に ease-in 系がないこと。"""
        eases = {MOTION.EASE_OUT, MOTION.EASE_IN_OUT, MOTION.LINEAR}
        forbidden = {QEasingCurve.Type.InQuad, QEasingCurve.Type.InCubic,
                     QEasingCurve.Type.InQuart, QEasingCurve.Type.InQuint}
        assert not (eases & forbidden)


class TestMakeAnimation:
    def test_starts_running(self, qapp):
        w = QWidget()
        anim = make_animation(w, b"windowOpacity", 0.5, MOTION.PANEL)
        assert anim is not None
        assert anim.state() == QAbstractAnimation.State.Running
        anim.stop()

    def test_interrupt_stops_previous(self, qapp):
        """実行中の再呼び出しで旧アニメが stop し、新アニメに置き換わる。"""
        w = QWidget()
        first = make_animation(w, b"windowOpacity", 0.0, MOTION.PANEL)
        second = make_animation(w, b"windowOpacity", 1.0, MOTION.PANEL)
        assert first.state() == QAbstractAnimation.State.Stopped
        assert second.state() == QAbstractAnimation.State.Running
        # startValue 未指定 → 停止時点の現在値から再開（ジャンプしない）
        assert second.startValue() is None
        second.stop()

    def test_reduced_motion_sets_value_immediately(self, qapp):
        MOTION.reduced_motion = True
        w = QWidget()
        fired = []
        anim = make_animation(w, b"windowOpacity", 0.25, MOTION.BASE,
                              on_finished=lambda: fired.append(1))
        assert anim is None
        # windowOpacity は 1/255 単位に量子化されるため誤差を許容
        assert abs(w.windowOpacity() - 0.25) < 1 / 255 + 1e-6
        assert fired == [1]

    def test_str_property_name_accepted(self, qapp):
        w = QWidget()
        anim = make_animation(w, "windowOpacity", 0.5, MOTION.FAST)
        assert anim is not None
        anim.stop()
