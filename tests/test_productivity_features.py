"""生産性向上機能（5-9）のテスト。

5. 「ここで開く」系・パスコピー / 6. 最近・頻繁フォルダ /
7. 高度な選択操作 / 8. 新規作成（テンプレート対応）/ 9. リネームプリセット。
GUI 部分は offscreen でスモーク確認、ロジックは純粋テスト。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.recent import RecentStore  # noqa: E402
from app.models.rename_presets import RenamePresetStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---- 6. 最近／よく使うフォルダ ----
class TestRecentStore:
    def test_record_and_recent_order(self, tmp_path):
        store = RecentStore(tmp_path / "recent.json")
        store.record(r"C:\a", now=100.0)
        store.record(r"C:\b", now=200.0)
        recent = store.recent()
        assert [e.path for e in recent] == [str_path("C:\\b"), str_path("C:\\a")]

    def test_frequent_order_by_count(self, tmp_path):
        store = RecentStore(tmp_path / "recent.json")
        store.record(r"C:\a", now=1.0)
        store.record(r"C:\a", now=2.0)
        store.record(r"C:\b", now=3.0)
        freq = store.frequent()
        assert freq[0].path == str_path("C:\\a")
        assert freq[0].count == 2

    def test_persist_reload(self, tmp_path):
        config = tmp_path / "recent.json"
        RecentStore(config).record(r"C:\x", now=5.0)
        store2 = RecentStore(config)
        assert len(store2.entries) == 1
        assert store2.entries[0].count == 1

    def test_corrupt_falls_back(self, tmp_path):
        config = tmp_path / "recent.json"
        config.write_text("{ broken", encoding="utf-8")
        assert RecentStore(config).entries == []

    def test_cap_max_stored(self, tmp_path):
        store = RecentStore(tmp_path / "recent.json", max_stored=3)
        for i in range(5):
            store.record(f"C:\\d{i}", now=float(i))
        assert len(store.entries) == 3
        # 最新（時刻が大きい）3件が残る
        paths = {e.path for e in store.entries}
        assert str_path("C:\\d4") in paths
        assert str_path("C:\\d0") not in paths


# ---- 9. リネームプリセット ----
class TestRenamePresetStore:
    def test_add_get_reload(self, tmp_path):
        config = tmp_path / "presets.json"
        store = RenamePresetStore(config)
        store.add("連番化", {"search": "", "replace": "img_${n}",
                            "use_regex": False, "target": "name",
                            "case_mode": "keep", "counter_start": 1,
                            "counter_step": 1, "counter_digits": 3})
        store2 = RenamePresetStore(config)
        p = store2.get("連番化")
        assert p is not None
        assert p.rule["replace"] == "img_${n}"

    def test_add_overwrites_same_name(self, tmp_path):
        store = RenamePresetStore(tmp_path / "p.json")
        store.add("x", {"replace": "a"})
        store.add("x", {"replace": "b"})
        assert len(store.presets) == 1
        assert store.get("x").rule["replace"] == "b"

    def test_remove(self, tmp_path):
        store = RenamePresetStore(tmp_path / "p.json")
        store.add("x", {"replace": "a"})
        assert store.remove("x")
        assert not store.remove("x")

    def test_only_known_keys_saved(self, tmp_path):
        store = RenamePresetStore(tmp_path / "p.json")
        store.add("x", {"replace": "a", "garbage": "ignored"})
        assert "garbage" not in store.get("x").rule

    def test_corrupt_falls_back(self, tmp_path):
        config = tmp_path / "p.json"
        config.write_text("nope", encoding="utf-8")
        assert RenamePresetStore(config).presets == []


# ---- 5/7/8: MainWindow スモーク ----
class TestMainWindowFeatures:
    def _make_window(self, tmp_path, monkeypatch):
        # CONFIG_DIR を一時ディレクトリへ
        import app.paths as paths
        import app.gui.main_window as mw
        monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr(mw, "CONFIG_DIR", tmp_path / "config")
        from app.gui.main_window import MainWindow
        return MainWindow()

    def test_new_folder(self, qapp, tmp_path, monkeypatch):
        work = tmp_path / "work"
        work.mkdir()
        win = self._make_window(tmp_path, monkeypatch)
        win.navigate(str(work))
        monkeypatch.setattr(
            "app.gui.main_window.QInputDialog.getText",
            lambda *a, **k: ("新規フォルダ", True))
        win.new_folder()
        assert (work / "新規フォルダ").is_dir()

    def test_new_folder_unique_name(self, qapp, tmp_path, monkeypatch):
        work = tmp_path / "work2"
        work.mkdir()
        (work / "dup").mkdir()
        win = self._make_window(tmp_path, monkeypatch)
        win.navigate(str(work))
        monkeypatch.setattr(
            "app.gui.main_window.QInputDialog.getText",
            lambda *a, **k: ("dup", True))
        win.new_folder()
        assert (work / "dup (2)").is_dir()

    def test_new_text_file(self, qapp, tmp_path, monkeypatch):
        work = tmp_path / "work3"
        work.mkdir()
        win = self._make_window(tmp_path, monkeypatch)
        win.navigate(str(work))
        monkeypatch.setattr(
            "app.gui.main_window.QInputDialog.getText",
            lambda *a, **k: ("memo.txt", True))
        win.new_text_file()
        assert (work / "memo.txt").is_file()

    def test_copy_paths_to_clipboard(self, qapp, tmp_path, monkeypatch):
        work = tmp_path / "work4"
        work.mkdir()
        (work / "f.txt").write_text("x")
        win = self._make_window(tmp_path, monkeypatch)
        win.navigate(str(work))
        win._copy_paths_to_clipboard([str(work / "f.txt")])
        assert "f.txt" in QApplication.clipboard().text()

    def test_recent_recorded_on_navigate(self, qapp, tmp_path, monkeypatch):
        work = tmp_path / "work5"
        work.mkdir()
        win = self._make_window(tmp_path, monkeypatch)
        win.navigate(str(work))
        assert any("work5" in e.path for e in win.recent_store.entries)


    def test_mouse_nav_back_forward(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        win = self._make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.navigate(str(b))
        win._on_mouse_nav(False)  # 戻る
        assert win.current_path == str(a)
        win._on_mouse_nav(True)  # 進む
        assert win.current_path == str(b)

    def test_disk_usage_shown(self, qapp, tmp_path, monkeypatch):
        work = tmp_path / "disk"
        work.mkdir()
        win = self._make_window(tmp_path, monkeypatch)
        win.navigate(str(work))
        assert "空き" in win.disk_label.text()


# ---- 11. アトミック書き込み ----
class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        from app.atomicio import atomic_write_text
        target = tmp_path / "sub" / "data.json"
        atomic_write_text(target, '{"a": 1}')
        assert target.read_text(encoding="utf-8") == '{"a": 1}'

    def test_no_temp_files_left(self, tmp_path):
        from app.atomicio import atomic_write_text
        target = tmp_path / "x.json"
        atomic_write_text(target, "hello")
        leftovers = [p for p in tmp_path.iterdir() if p.name != "x.json"]
        assert leftovers == []

    def test_overwrite_preserves_on_replace(self, tmp_path):
        from app.atomicio import atomic_write_text
        target = tmp_path / "x.json"
        atomic_write_text(target, "v1")
        atomic_write_text(target, "v2")
        assert target.read_text(encoding="utf-8") == "v2"

    def test_store_uses_atomic(self, tmp_path):
        # FavoriteStore 経由でも中身が壊れず保存される
        from app.models.favorite import FavoriteStore
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        store.add("x", "C:\\")
        import json
        data = json.loads(config.read_text(encoding="utf-8"))
        assert data["favorites"][0]["label"] == "x"


def str_path(p):
    from pathlib import Path
    return str(Path(p))
