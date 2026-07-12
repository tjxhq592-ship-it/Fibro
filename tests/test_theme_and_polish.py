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
    def test_apply_default_is_light(self, qapp, tmp_path):
        """theme 未指定の apply はライトテーマ（明るい背景）になる。"""
        from PySide6.QtGui import QPalette
        tm = ThemeManager(tmp_path / "settings.json")
        tm.apply(qapp)
        color = qapp.palette().color(QPalette.ColorRole.Window)
        assert color.lightness() >= 128

    def test_theme_persists_in_project_settings(self, tmp_path):
        """テーマの永続化先はプロジェクト範囲の ProjectSettingsStore。"""
        from app.models.project_settings import ProjectSettingsStore
        ps = ProjectSettingsStore(tmp_path / "ps.json")
        ps.set("theme", "dark")
        assert ProjectSettingsStore(tmp_path / "ps.json").get("theme") == "dark"

    def test_corrupt_settings_fallback(self, qapp, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{ bad json", encoding="utf-8")
        tm = ThemeManager(path)
        tm.apply(qapp)  # クラッシュしない
        tm.apply(qapp, "light")

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


class TestThemeTokenIntegrity:
    """10テーマ全てが同一キー構成を持つことを保証する回帰テスト。"""

    def test_all_themes_have_same_keys(self):
        from app.gui.theme import TOKENS
        base_keys = set(TOKENS["light"].keys())
        for theme_name, tokens in TOKENS.items():
            assert set(tokens.keys()) == base_keys, f"{theme_name} のキー構成が不一致"

    def test_theme_order_and_tokens_match(self):
        from app.gui.theme import TOKENS, THEME_ORDER
        assert len(THEME_ORDER) == 10
        assert set(THEME_ORDER) == set(TOKENS.keys())

    def test_all_themes_have_meta(self):
        from app.gui.theme import THEME_META, THEME_ORDER
        for key in THEME_ORDER:
            assert key in THEME_META
            assert "is_dark" in THEME_META[key]
            assert "label_ja" in THEME_META[key]
            assert "label_en" in THEME_META[key]

    def test_unknown_theme_falls_back_to_light(self, qapp, tmp_path):
        """壊れた settings.json 等の未知テーマ名でも例外なく light になる。"""
        from PySide6.QtGui import QColor, QPalette
        from app.gui.theme import TOKENS, ThemeManager
        tm = ThemeManager(tmp_path / "settings.json")
        tm.apply(qapp, "nonexistent_theme")
        color = qapp.palette().color(QPalette.ColorRole.Window)
        assert color == QColor(TOKENS["light"]["surface"])
        tm.apply(qapp, "light")

    def test_set_theme_applies_and_returns_name(self, qapp, tmp_path):
        from PySide6.QtGui import QColor, QPalette
        from app.gui.theme import TOKENS, ThemeManager
        tm = ThemeManager(tmp_path / "settings.json")
        applied = tm.set_theme(qapp, "nord")
        assert applied == "nord"
        color = qapp.palette().color(QPalette.ColorRole.Window)
        assert color == QColor(TOKENS["nord"]["surface"])
        tm.apply(qapp, "light")

    def test_set_theme_unknown_returns_light(self, qapp, tmp_path):
        from app.gui.theme import ThemeManager
        tm = ThemeManager(tmp_path / "settings.json")
        assert tm.set_theme(qapp, "no_such_theme") == "light"

    def test_current_accent_follows_theme(self, qapp, tmp_path):
        from PySide6.QtGui import QColor
        from app.gui.theme import TOKENS, ThemeManager, current_accent
        tm = ThemeManager(tmp_path / "settings.json")
        tm.set_theme(qapp, "dracula")
        assert current_accent() == QColor(TOKENS["dracula"]["accent"])
        tm.set_theme(qapp, "light")
        assert current_accent() == QColor(TOKENS["light"]["accent"])

    def test_status_colors_any_theme(self):
        from app.gui.theme import TOKENS, THEME_ORDER, status_colors
        from PySide6.QtGui import QColor
        for key in THEME_ORDER:
            sc = status_colors(key)
            assert sc["error"] == QColor(TOKENS[key]["status_error"])


class TestColorThemeImprovement:
    """カラーテーマ改善指示書（on_accent/icon/overlay_bg・配色修正）の回帰テスト。"""

    def test_new_tokens_in_all_themes(self):
        from app.gui.theme import TOKENS
        for name, tokens in TOKENS.items():
            for key in ("on_accent", "icon", "overlay_bg"):
                assert key in tokens, f"{name} に {key} がない"

    def test_highlighted_text_uses_on_accent(self, qapp, tmp_path):
        """ハイコントラスト（黄アクセント）の選択文字が白抜きにならない。"""
        from PySide6.QtGui import QColor, QPalette
        from app.gui.theme import TOKENS, ThemeManager
        tm = ThemeManager(tmp_path / "settings.json")
        tm.apply(qapp, "high_contrast")
        color = qapp.palette().color(QPalette.ColorRole.HighlightedText)
        assert color == QColor(TOKENS["high_contrast"]["on_accent"])
        assert color == QColor("#000000")
        tm.apply(qapp, "light")

    def test_qss_selection_color_uses_on_accent(self):
        """QLineEdit の selection-color がハードコード #ffffff でない。"""
        from app.gui.theme import TOKENS, _stylesheet
        t = TOKENS["high_contrast"]
        qss = _stylesheet(t)
        assert f"selection-color: {t['on_accent']}" in qss

    def test_monokai_accent_differs_from_status_ok(self):
        """accent と status_ok の意味論的衝突（同色）が解消されている。"""
        from app.gui.theme import TOKENS
        assert TOKENS["monokai"]["accent"] != TOKENS["monokai"]["status_ok"]

    def test_one_dark_elevation_order(self):
        """ダークの Elevation 原則: app_base < bg < surface < elevated。"""
        from PySide6.QtGui import QColor
        from app.gui.theme import TOKENS
        t = TOKENS["one_dark"]
        levels = [QColor(t[k]).lightness()
                  for k in ("app_base", "bg", "surface", "elevated")]
        assert levels == sorted(levels) and len(set(levels)) == len(levels)


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


class TestPressedFeedback:
    """押下フィードバック（フェーズ3）: pressed_bg トークンと QSS :pressed。"""

    def test_all_themes_have_pressed_bg(self):
        from app.gui.theme import TOKENS
        for theme_name, tokens in TOKENS.items():
            assert "pressed_bg" in tokens, f"{theme_name} に pressed_bg がない"

    def test_qss_has_pressed_rules(self):
        """QToolButton/QPushButton/タブ/ヘッダに :pressed 相当のルールがある。"""
        from app.gui.theme import TOKENS, _stylesheet
        qss = _stylesheet(TOKENS["light"])
        assert "QToolButton:pressed" in qss
        assert "QPushButton:pressed" in qss
        assert "QTabBar::tab:pressed" in qss
        assert '#collapsibleHeader[pressed="true"]' in qss
        # pressed_bg が実際に埋め込まれている
        assert TOKENS["light"]["pressed_bg"] in qss

    def test_header_pressed_property_toggles(self, qapp):
        """collapsibleHeader が押下中のみ pressed プロパティを立てる。"""
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QWidget
        from app.gui.collapsible import CollapsibleSection

        section = CollapsibleSection("TEST", QWidget())
        header = section._header
        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(5, 5),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease, QPointF(5, 5),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier)
        header.mousePressEvent(press)
        assert header.property("pressed") is True
        header.mouseReleaseEvent(release)
        assert header.property("pressed") is False
