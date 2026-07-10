"""プロジェクト機能のテスト。

マイグレーション・ProjectManager は純粋テスト、切替まわりは offscreen スモーク。
"""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.migrations import migrate_default_project_settings  # noqa: E402
from app.models.project import ProjectManager  # noqa: E402
from app.models.project_settings import ProjectSettingsStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_window(tmp_path, monkeypatch):
    import app.paths as paths
    import app.gui.main_window as mw
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(mw, "CONFIG_DIR", tmp_path / "config")
    from app.gui.main_window import MainWindow
    return MainWindow()


# ---- 1. マイグレーション ----
class TestMigration:
    def test_moves_project_scoped_keys(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "theme": "dark",
            "place_names": {"c:\\": "システム"},
            "tabs": ["C:\\Users"],
            "language": "ja",
            "initial_dir": "C:\\",
        }), encoding="utf-8")
        migrate_default_project_settings(tmp_path)

        moved = json.loads(
            (tmp_path / "default_project_settings.json").read_text("utf-8"))
        assert moved == {
            "theme": "dark",
            "place_names": {"c:\\": "システム"},
            "tabs": ["C:\\Users"],
        }
        remaining = json.loads(settings.read_text("utf-8"))
        assert remaining == {"language": "ja", "initial_dir": "C:\\"}

    def test_second_run_is_noop(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        migrate_default_project_settings(tmp_path)
        # 移行後に settings.json へ再度プロジェクト範囲キーが入っても動かさない
        settings.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
        migrate_default_project_settings(tmp_path)
        moved = json.loads(
            (tmp_path / "default_project_settings.json").read_text("utf-8"))
        assert moved == {"theme": "dark"}  # 初回の内容のまま（冪等）

    def test_no_settings_file(self, tmp_path):
        migrate_default_project_settings(tmp_path)  # 何も起きない
        assert not (tmp_path / "default_project_settings.json").exists()

    def test_corrupt_settings(self, tmp_path):
        (tmp_path / "settings.json").write_text("{ bad", encoding="utf-8")
        migrate_default_project_settings(tmp_path)  # クラッシュしない
        assert not (tmp_path / "default_project_settings.json").exists()

    def test_no_project_keys(self, tmp_path):
        (tmp_path / "settings.json").write_text(
            json.dumps({"language": "en"}), encoding="utf-8")
        migrate_default_project_settings(tmp_path)
        assert not (tmp_path / "default_project_settings.json").exists()


# ---- 2-3. ProjectManager ----
class TestProjectManager:
    def test_add_creates_dir_and_persists(self, tmp_path):
        pm = ProjectManager(tmp_path)
        proj = pm.add("仕事")
        assert pm.project_dir(proj.id).is_dir()
        pm2 = ProjectManager(tmp_path)
        assert [p.name for p in pm2.projects] == ["仕事"]

    def test_rename(self, tmp_path):
        pm = ProjectManager(tmp_path)
        proj = pm.add("旧名")
        pm.rename(proj.id, "新名")
        assert ProjectManager(tmp_path).projects[0].name == "新名"

    def test_remove_deletes_directory(self, tmp_path):
        pm = ProjectManager(tmp_path)
        proj = pm.add("削除対象")
        d = pm.project_dir(proj.id)
        (d / "favorites.json").write_text("[]", encoding="utf-8")
        pm.remove(proj.id)
        assert not d.exists()
        assert ProjectManager(tmp_path).projects == []

    def test_remove_active_falls_back_to_default(self, tmp_path):
        pm = ProjectManager(tmp_path)
        proj = pm.add("A")
        pm.set_active(proj.id)
        pm.remove(proj.id)
        assert pm.active_project_id is None
        assert ProjectManager(tmp_path).active_project_id is None

    def test_reorder(self, tmp_path):
        pm = ProjectManager(tmp_path)
        a, b, c = pm.add("A"), pm.add("B"), pm.add("C")
        pm.reorder([c.id, a.id, b.id])
        assert [p.name for p in ProjectManager(tmp_path).projects] \
            == ["C", "A", "B"]

    def test_store_paths_default_vs_project(self, tmp_path):
        pm = ProjectManager(tmp_path)
        proj = pm.add("P")
        default = pm.store_paths(None)
        scoped = pm.store_paths(proj.id)
        assert default["favorites"] == tmp_path / "favorites.json"
        assert default["project_settings"] \
            == tmp_path / "default_project_settings.json"
        assert scoped["favorites"] \
            == tmp_path / "projects" / proj.id / "favorites.json"

    def test_corrupt_projects_json(self, tmp_path):
        (tmp_path / "projects.json").write_text("{ bad", encoding="utf-8")
        pm = ProjectManager(tmp_path)
        assert pm.projects == [] and pm.active_project_id is None


# ---- 4-11. 切替（GUI スモーク） ----
class TestProjectSwitch:
    def test_favorites_swap_on_switch(self, qapp, tmp_path, monkeypatch):
        """項目4・11: 切替後お気に入りが新プロジェクトの内容になる。"""
        win = _make_window(tmp_path, monkeypatch)
        fav_dir = tmp_path / "favtarget"
        fav_dir.mkdir()
        win.favorite_store.add("デフォルトのお気に入り", str(fav_dir))
        win.favorites.refresh()
        assert win.favorites.tree.topLevelItemCount() == 1

        win._create_project("empty", "空プロジェクト")  # 作成後に自動切替
        assert win.project_manager.active is not None
        assert win.favorites.tree.topLevelItemCount() == 0  # 空で開始
        # コピー元（デフォルト）のファイルは無変更
        assert len(win.project_manager.store_paths(None)["favorites"]
                   .read_text("utf-8")) > 2

    def test_copy_current_duplicates_data(self, qapp, tmp_path, monkeypatch):
        """項目10: パターンA はコピー元を変更せず同一内容を複製する。"""
        win = _make_window(tmp_path, monkeypatch)
        fav_dir = tmp_path / "favtarget2"
        fav_dir.mkdir()
        win.favorite_store.add("共通", str(fav_dir))
        src_path = win.project_manager.store_paths(None)["favorites"]
        src_before = src_path.read_text("utf-8")

        win._create_project("copy_current", "コピー版")
        proj = win.project_manager.active
        dst_path = win.project_manager.store_paths(proj.id)["favorites"]
        assert dst_path.read_text("utf-8") == src_before
        assert src_path.read_text("utf-8") == src_before  # コピー元は無変更
        assert win.favorites.tree.topLevelItemCount() == 1

        # 新プロジェクト側の変更はコピー元に伝播しない
        win.favorite_store.remove(win.favorite_store.favorites[0].id)
        assert src_path.read_text("utf-8") == src_before

    def test_tabs_independent_per_project(self, qapp, tmp_path, monkeypatch):
        """項目8: タブの保存・復元がプロジェクトごとに独立している。"""
        a, b = tmp_path / "tab_a", tmp_path / "tab_b"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))

        win._create_project("empty", "P1")
        win.navigate(str(b))
        assert win.current_path == str(b)

        pid = win.project_manager.active_project_id
        win._switch_project(None)  # デフォルトへ戻る
        assert win.current_path == str(a)
        win._switch_project(pid)
        assert win.current_path == str(b)
        assert len(win._tabs) == 1  # 仮タブが残っていない

    def test_theme_reapplied_on_switch(self, qapp, tmp_path, monkeypatch):
        """項目6: 切替後にテーマが再適用される。"""
        from PySide6.QtGui import QPalette
        win = _make_window(tmp_path, monkeypatch)
        win.project_settings.set("theme", "dark")
        win._create_project("empty", "ライト版")  # 空 → theme 既定 light
        assert not win._is_dark()
        assert qapp.palette().color(
            QPalette.ColorRole.Window).lightness() >= 128
        win._switch_project(None)  # ダーク設定のデフォルトへ戻る
        assert win._is_dark()
        assert qapp.palette().color(
            QPalette.ColorRole.Window).lightness() < 128
        win.theme_manager.apply(qapp, "light")  # 後続テストのため戻す

    def test_place_names_swap_on_switch(self, qapp, tmp_path, monkeypatch):
        """項目5: クイックアクセス表示名が新プロジェクトの place_names になる。"""
        win = _make_window(tmp_path, monkeypatch)
        if win.places_sidebar.list.count() == 0:
            pytest.skip("表示できる場所がない環境")
        from app.gui.places_sidebar import _PATH_ROLE
        item = win.places_sidebar.list.item(0)
        key = os.path.normcase(item.data(_PATH_ROLE))
        win.places_sidebar._custom_names[key] = "デフォルト用の名前"
        win.places_sidebar._save_names()

        win._create_project("empty", "P")
        assert win.places_sidebar.list.item(0).text() != "デフォルト用の名前"
        win._switch_project(None)
        assert win.places_sidebar.list.item(0).text() == "デフォルト用の名前"

    def test_switch_blocked_during_copy(self, qapp, tmp_path, monkeypatch):
        """項目7: コピー/移動の実行中は切替をブロックして警告する。"""
        from PySide6.QtWidgets import QMessageBox
        win = _make_window(tmp_path, monkeypatch)
        proj = win.project_manager.add("待機中")
        warned = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *a, **k: warned.append(a) or QMessageBox.StandardButton.Ok)
        win._op_thread = object()  # 実行中を偽装
        win._switch_project(proj.id)
        assert warned
        assert win.project_manager.active_project_id is None  # 切替されない
        win._op_thread = None

    def test_app_wide_settings_unaffected(self, qapp, tmp_path, monkeypatch):
        """項目9: 履歴・言語・initial_dir 等はプロジェクト切替の影響を受けない。"""
        win = _make_window(tmp_path, monkeypatch)
        win.theme_manager.set("language", "en")
        win.theme_manager.set("initial_dir", str(tmp_path))
        recent_store_before = win.recent_store
        recent_paths_before = {e.path for e in win.recent_store.entries}

        win._create_project("empty", "P")
        assert win.theme_manager.get("language") == "en"
        assert win.theme_manager.get("initial_dir") == str(tmp_path)
        # 履歴ストアは同一インスタンスのまま（差し替えられない）
        assert win.recent_store is recent_store_before
        assert recent_paths_before <= {e.path for e in win.recent_store.entries}

    def test_delete_active_project_reloads_default(
            self, qapp, tmp_path, monkeypatch):
        """項目3(GUI): アクティブプロジェクト削除でデフォルトへ戻り再読込。"""
        win = _make_window(tmp_path, monkeypatch)
        fav_dir = tmp_path / "favdef"
        fav_dir.mkdir()
        win.favorite_store.add("デフォルト", str(fav_dir))
        win._create_project("empty", "消えるP")
        pid = win.project_manager.active_project_id
        assert pid is not None

        win.project_manager.remove(pid)  # ダイアログの削除相当
        assert win.project_manager.active_project_id is None
        win._load_project_scoped_stores()
        win._reload_all_project_scoped_ui()
        assert win.favorites.tree.topLevelItemCount() == 1


# ---- クイック切替（Alt+1〜5）と現在プロジェクト名表示 ----
class TestProjectQuickSwitch:
    def test_switch_by_index(self, qapp, tmp_path, monkeypatch):
        """並び順 N 番目への切替。"""
        win = _make_window(tmp_path, monkeypatch)
        a = win.project_manager.add("A")
        win.project_manager.add("B")
        win._switch_project_by_index(0)
        assert win.project_manager.active_project_id == a.id

    def test_out_of_range_is_noop(self, qapp, tmp_path, monkeypatch):
        """プロジェクト数不足のインデックスは例外なく無視される。"""
        win = _make_window(tmp_path, monkeypatch)
        for i in range(5):  # 0件の状態
            win._switch_project_by_index(i)
        assert win.project_manager.active_project_id is None
        a = win.project_manager.add("A")
        win._switch_project_by_index(0)
        win._switch_project_by_index(4)  # 2件目以降は存在しない
        assert win.project_manager.active_project_id == a.id

    def test_same_project_is_noop(self, qapp, tmp_path, monkeypatch):
        """選択中プロジェクトの再指定で再読込が走らない。"""
        win = _make_window(tmp_path, monkeypatch)
        win._create_project("empty", "A")
        calls = []
        monkeypatch.setattr(win, "_reload_all_project_scoped_ui",
                            lambda: calls.append(1))
        win._switch_project_by_index(0)
        assert not calls

    def test_follows_reorder_without_reregistration(
            self, qapp, tmp_path, monkeypatch):
        """並び替え直後、再起動なしで新しい並び順の N 番目が対象になる。"""
        win = _make_window(tmp_path, monkeypatch)
        a, b, c = (win.project_manager.add(n) for n in "ABC")
        win.project_manager.reorder([c.id, a.id, b.id])
        win._switch_project_by_index(0)
        assert win.project_manager.active.name == "C"
        win.project_manager.reorder([b.id, c.id, a.id])
        win._switch_project_by_index(0)
        assert win.project_manager.active.name == "B"

    def test_alt_shortcuts_registered_once(self, qapp, tmp_path, monkeypatch):
        """Alt+1〜5 がそれぞれ1アクションにのみ割り当てられている（衝突なし）。"""
        from PySide6.QtGui import QKeySequence
        win = _make_window(tmp_path, monkeypatch)
        for n in range(1, 6):
            seq = QKeySequence(f"Alt+{n}")
            owners = [a for a in win.actions() if seq in a.shortcuts()]
            assert len(owners) == 1

    def test_button_shows_active_and_none_state(
            self, qapp, tmp_path, monkeypatch):
        """未選択時は (プロジェクトなし) 表示、切替で名前に即時更新。"""
        from app.i18n import _
        win = _make_window(tmp_path, monkeypatch)
        assert win.project_name_btn.text() == _("project_none")
        assert win.project_name_btn.property("noProject") is True
        win._create_project("empty", "仕事")
        assert win.project_name_btn.text() == "仕事"
        assert win.project_name_btn.property("noProject") is False
        win._switch_project(None)
        assert win.project_name_btn.text() == _("project_none")
        assert win.project_name_btn.property("noProject") is True

    def test_button_elides_long_name_with_tooltip(
            self, qapp, tmp_path, monkeypatch):
        """50文字超の名前はエリジオン表示、ツールチップはフル名。"""
        win = _make_window(tmp_path, monkeypatch)
        long_name = "とても長いプロジェクト名" * 5  # 60文字
        win._create_project("empty", long_name)
        text = win.project_name_btn.text()
        assert text != long_name
        assert text.endswith("…")
        assert win.project_name_btn.toolTip() == long_name

    def test_button_updates_on_rename_via_manage(
            self, qapp, tmp_path, monkeypatch):
        """管理ダイアログでのリネーム（アクティブ不変）後に表示名が更新される。"""
        from app.gui.project_dialog import ProjectDialog
        win = _make_window(tmp_path, monkeypatch)
        win._create_project("empty", "旧名")
        pid = win.project_manager.active_project_id
        monkeypatch.setattr(
            ProjectDialog, "exec",
            lambda dlg: dlg._manager.rename(pid, "新名"))
        win._manage_projects()
        assert win.project_name_btn.text() == "新名"
