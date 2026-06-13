"""GUI スモークテスト（QT_QPA_PLATFORM=offscreen で実行）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.engine.rename_history import RenameExecutor  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402
from app.gui.rename_dialog import RenameDialog  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_opens(qapp, tmp_path):
    window = MainWindow()
    window.navigate(str(tmp_path))
    assert window.current_path == str(tmp_path)
    window.close()


def test_navigation_history(qapp, tmp_path):
    (tmp_path / "sub").mkdir()
    window = MainWindow()
    window.navigate(str(tmp_path))
    window.navigate(str(tmp_path / "sub"))
    window.go_back()
    assert window.current_path == str(tmp_path)
    window.go_forward()
    assert window.current_path == str(tmp_path / "sub")
    window.go_up()
    assert window.current_path == str(tmp_path)
    window.close()


def test_rename_dialog_preview_and_execute(qapp, tmp_path):
    for name in ["doc_v1.pdf", "doc_v2.pdf"]:
        (tmp_path / name).write_text("x")
    executor = RenameExecutor()
    dialog = RenameDialog(str(tmp_path), ["doc_v1.pdf", "doc_v2.pdf"],
                          {"doc_v1.pdf", "doc_v2.pdf"}, executor)
    dialog.search_edit.setText("_v1")
    dialog.replace_edit.setText("_final")
    dialog._update_preview()  # デバウンスを待たず直接更新
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 1).text() == "doc_final.pdf"

    dialog._execute()
    assert (tmp_path / "doc_final.pdf").exists()
    assert executor.can_undo
    executor.undo()
    assert (tmp_path / "doc_v1.pdf").exists()


def test_rename_dialog_bad_regex_disables_apply(qapp, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    dialog = RenameDialog(str(tmp_path), ["a.txt"], {"a.txt"},
                          RenameExecutor())
    dialog.regex_check.setChecked(True)
    dialog.search_edit.setText("[")
    dialog._update_preview()
    assert not dialog.apply_btn.isEnabled()
    assert dialog.rule_error.text() != ""
