"""テーマ永続化・F2リネーム・更新のスモークテスト（offscreen）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui.theme import ThemeManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestThemeManager:
    def test_default_is_light(self, tmp_path):
        tm = ThemeManager(tmp_path / "settings.json")
        assert tm.theme == "light"

    def test_toggle_persists(self, qapp, tmp_path):
        path = tmp_path / "settings.json"
        tm = ThemeManager(path)
        assert tm.toggle(qapp) == "dark"
        # 再起動相当
        tm2 = ThemeManager(path)
        assert tm2.theme == "dark"
        assert tm2.toggle(qapp) == "light"
        assert ThemeManager(path).theme == "light"

    def test_corrupt_settings_fallback(self, qapp, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{ bad json", encoding="utf-8")
        tm = ThemeManager(path)
        assert tm.theme == "light"
        tm.apply(qapp)  # クラッシュしない
        assert ThemeManager(path).theme == "light"  # 保存し直されて復旧

    def test_apply_dark_changes_palette(self, qapp, tmp_path):
        tm = ThemeManager(tmp_path / "settings.json")
        tm.apply(qapp, "dark")
        from PySide6.QtGui import QPalette
        color = qapp.palette().color(QPalette.ColorRole.Window)
        assert color.lightness() < 100  # 暗い背景
        tm.apply(qapp, "light")

    def test_apply_sets_native_color_scheme(self, qapp, tmp_path):
        """テーマ適用で Qt のカラースキーム（タイトルバー追従）も切り替わる。

        offscreen プラットフォームでは setColorScheme が no-op（Unknown のまま）
        のため、その場合は例外なく適用できることのみ確認する。
        """
        from PySide6.QtCore import Qt
        tm = ThemeManager(tmp_path / "settings.json")
        hints = qapp.styleHints()
        tm.apply(qapp, "dark")
        if hints.colorScheme() != Qt.ColorScheme.Unknown:
            assert hints.colorScheme() == Qt.ColorScheme.Dark
            tm.apply(qapp, "light")
            assert hints.colorScheme() == Qt.ColorScheme.Light
        else:
            tm.apply(qapp, "light")  # no-op 環境でも落ちないこと


class TestSingleRename:
    def test_f2_rename_via_executor(self, qapp, tmp_path):
        """F2 相当の単一リネームが RenameExecutor 経由で Undo 可能。"""
        from app.engine.rename_history import RenameExecutor
        (tmp_path / "old.txt").write_text("x")
        ex = RenameExecutor()
        ex.execute(str(tmp_path), [("old.txt", "new.txt")])
        assert (tmp_path / "new.txt").exists()
        ex.undo()
        assert (tmp_path / "old.txt").exists()


class TestSingleRenameDialog:
    def test_split_file_separates_extension(self, qapp):
        from app.gui.main_window import SingleRenameDialog
        assert SingleRenameDialog._split("report.final.pdf", False) == (
            "report.final", "pdf")

    def test_split_no_extension(self, qapp):
        from app.gui.main_window import SingleRenameDialog
        assert SingleRenameDialog._split("README", False) == ("README", "")

    def test_split_dotfile_not_extension(self, qapp):
        from app.gui.main_window import SingleRenameDialog
        assert SingleRenameDialog._split(".gitignore", False) == (
            ".gitignore", "")

    def test_split_dir_keeps_dots(self, qapp):
        from app.gui.main_window import SingleRenameDialog
        assert SingleRenameDialog._split("my.folder", True) == ("my.folder", "")

    def test_dialog_fields_and_join(self, qapp):
        from app.gui.main_window import SingleRenameDialog
        dlg = SingleRenameDialog("photo.jpg", False)
        assert dlg.name_edit.text() == "photo"
        assert dlg.ext_edit.text() == "jpg"
        dlg.name_edit.setText("vacation")
        dlg.ext_edit.setText("png")
        assert dlg.new_name() == "vacation.png"

    def test_dir_has_no_ext_field_but_joins_name(self, qapp):
        from app.gui.main_window import SingleRenameDialog
        dlg = SingleRenameDialog("docs", True)
        assert dlg.name_edit.text() == "docs"
        dlg.name_edit.setText("documents")
        assert dlg.new_name() == "documents"
