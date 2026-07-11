"""トースト通知（フェーズ6）: 4分類・積み上げ・ホバー停止・退出のテスト。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from app.gui.motion import MOTION  # noqa: E402
from app.gui.toast import _KINDS, ToastManager, _Toast  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    w = QMainWindow()
    w.resize(800, 600)
    w.show()
    yield w
    w.close()


@pytest.fixture(autouse=True)
def _instant_motion():
    """テストではアニメを 0ms 化して同期的に検証する。"""
    MOTION.reduced_motion = True
    yield
    MOTION.reduced_motion = False


class TestKinds:
    def test_four_kinds_defined(self):
        assert set(_KINDS) == {"status", "completion", "warning", "error"}

    def test_kind_colors_come_from_tokens(self):
        from app.gui.theme import TOKENS
        for _icon, color_key in _KINDS.values():
            assert color_key in TOKENS["light"]


class TestToastManager:
    def test_show_adds_visible_toast(self, window):
        mgr = ToastManager(window)
        mgr.show_toast("completion", "コピー完了")
        assert len(mgr._toasts) == 1
        toast = mgr._toasts[0]
        assert toast.isVisible()
        # reduced_motion では即座に最終不透明度・最終位置
        assert toast.effect.opacity() == pytest.approx(1.0)
        assert toast.y() < window.height()

    def test_stacking_pushes_older_up(self, window):
        mgr = ToastManager(window)
        mgr.show_toast("status", "1件目")
        first = mgr._toasts[0]
        y_before = first.y()
        mgr.show_toast("status", "2件目")
        assert len(mgr._toasts) == 2
        # 既存(古い方)が上に詰め直され、新しい方が下に入る
        assert first.y() < y_before
        assert mgr._toasts[1].y() > first.y()

    def test_dismiss_removes_toast(self, window):
        mgr = ToastManager(window)
        mgr.show_toast("error", "失敗")
        toast = mgr._toasts[0]
        mgr._dismiss(toast)
        # reduced_motion では退出も即時 → リストから消える
        assert toast not in mgr._toasts

    def test_hover_pauses_timer(self, window):
        mgr = ToastManager(window)
        mgr.show_toast("warning", "警告")
        toast = mgr._toasts[0]
        assert toast._timer.isActive()
        toast.enterEvent(None)
        assert not toast._timer.isActive()
        toast.leaveEvent(None)
        assert toast._timer.isActive()

    def test_notify_via_main_window_api(self, qapp, tmp_path, monkeypatch):
        """MainWindow.notify が ToastManager を遅延生成して表示する。"""
        import app.paths as paths
        import app.gui.main_window as mw
        monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr(mw, "CONFIG_DIR", tmp_path / "config")
        win = mw.MainWindow()
        win.notify("completion", "テスト完了")
        assert hasattr(win, "_toaster")
        assert len(win._toaster._toasts) == 1
        win.close()
