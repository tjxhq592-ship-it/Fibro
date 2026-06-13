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
