"""conftest のセーフティネット自身が機能していることを固定するテスト。

安全網は「壊れても誰も気づかない」種類のコードで、実質無効化されても
スイートは緑のままになる。ハングの主因が「MainWindow の破棄漏れ」だった
以上、検出器が生きていることこそ回帰から守る対象なので、ここで固定する。
"""
from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget

from tests import conftest


class _Undisposable(QWidget):
    """破棄要求を無視するウィジェット（検出器のテスト用）。

    `dispose_widget` は deleteLater + DeferredDelete 配送で強制的に片付ける
    ため、普通のウィジェットでは残留を再現できない。deleteLater を握り潰して
    「回収を試みても残る」状況を意図的に作る。
    """

    def __init__(self) -> None:
        super().__init__()
        self.block_delete = True

    def deleteLater(self) -> None:  # noqa: N802 — Qt API
        if not self.block_delete:
            super().deleteLater()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class TestWidgetLeakDetection:
    """4.2 ウィジェット残留の検出。"""

    def test_undisposable_widget_is_reported(self, app):
        """回収を試みても残るウィジェットは名前付きで報告される。"""
        baseline_ids = {id(w) for w in app.topLevelWidgets()}
        widget = _Undisposable()
        widget.setObjectName("leak-probe")
        widget.show()
        try:
            leftover = conftest._dispose_new_top_levels(app, baseline_ids)
            assert leftover, "破棄漏れが検出されなかった（検出器が無効）"
            assert any("leak-probe" in entry for entry in leftover), (
                f"objectName が報告に含まれない: {leftover}")
            assert any("_Undisposable" in entry for entry in leftover), (
                f"クラス名が報告に含まれない: {leftover}")
        finally:
            # autouse の残留ガードに引っかからないよう、本当に片付ける。
            widget.block_delete = False
            conftest.dispose_widget(widget)
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_ordinary_widget_is_recovered(self, app):
        """普通のウィジェットは回収され、残留として報告されない。"""
        baseline_ids = {id(w) for w in app.topLevelWidgets()}
        widget = QWidget()
        widget.setObjectName("recoverable-probe")
        widget.show()
        # ここで参照を持ったままでも C++ 側は破棄される（Python 参照が
        # 残ることと破棄漏れは別問題）。
        leftover = conftest._dispose_new_top_levels(app, baseline_ids)
        assert not any("recoverable-probe" in e for e in leftover), (
            f"回収できるはずのウィジェットが残留扱い: {leftover}")

    def test_baseline_widgets_are_not_reported(self, app):
        """テスト開始時から居たウィジェットは残留として数えない。"""
        widget = QWidget()
        widget.setObjectName("pre-existing-probe")
        widget.show()
        try:
            baseline_ids = {id(w) for w in app.topLevelWidgets()}
            leftover = conftest._dispose_new_top_levels(app, baseline_ids)
            assert not any("pre-existing-probe" in e for e in leftover)
        finally:
            conftest.dispose_widget(widget)


class TestThreadResidueDetection:
    """4.2 スレッド残留の検出。"""

    def test_lingering_thread_is_reported(self):
        before = {t.ident for t in threading.enumerate()}
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, name="probe-residue",
                                  daemon=True)
        thread.start()
        try:
            residual = conftest._residual_threads(before)
            assert any("probe-residue" in entry for entry in residual), (
                f"残留スレッドが検出されなかった: {residual}")
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_netpath_daemon_thread_is_excluded(self):
        """netpath の使い捨てデーモンスレッドは残留に数えない。

        「返らない OS 呼び出し」を包むためのもので、諦めた後も生きているのが
        設計どおり。除外が外れると netpath のタイムアウト系テストが軒並み
        誤検出で落ちるため、意図を固定しておく。
        """
        before = {t.ident for t in threading.enumerate()}
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, name="fibro-netpath",
                                  daemon=True)
        thread.start()
        try:
            assert not conftest._residual_threads(before)
        finally:
            stop.set()
            thread.join(timeout=2)


class TestModalBlocking:
    """4.1 モーダルは必ず非ブロッキング化されている。"""

    def test_dialog_exec_fails_instead_of_blocking(self):
        from PySide6.QtWidgets import QDialog
        dialog = QDialog()
        try:
            # pytest.fail は BaseException 派生なので Exception では捕まらない。
            with pytest.raises(pytest.fail.Exception) as excinfo:
                dialog.exec()
            assert "モーダル" in str(excinfo.value)
        finally:
            conftest.dispose_widget(dialog)

    def test_message_box_question_returns_safe_answer(self):
        from PySide6.QtWidgets import QMessageBox
        sb = QMessageBox.StandardButton
        answer = QMessageBox.question(None, "t", "m", sb.Yes | sb.No)
        assert answer == sb.No, "確認ダイアログが安全側を返していない"
