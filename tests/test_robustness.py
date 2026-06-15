"""アプリの堅牢性（10/12/13/14/15）のテスト。

ロジックは純粋テスト、GUI は offscreen スモーク。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.engine.file_ops import FileOps  # noqa: E402
from app.engine.index_engine import SearchIndex  # noqa: E402
from app.longpath import extend  # noqa: E402
from app.netpath import reachable, safe_disk_usage  # noqa: E402


# ---- 13. ロングパス ----
class TestLongPath:
    def test_relative_passthrough(self):
        assert extend("foo/bar") == "foo/bar"

    def test_non_windows_passthrough(self, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        assert extend("/abs/long/path") == "/abs/long/path"

    def test_windows_prefix(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(os.path, "isabs", lambda s: True)
        monkeypatch.setattr(os.path, "normpath", lambda s: s.replace("/", "\\"))
        out = extend("C:/Users/x/file.txt")
        assert out.startswith("\\\\?\\")
        assert "C:\\Users\\x\\file.txt" in out

    def test_idempotent(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        already = "\\\\?\\C:\\x"
        assert extend(already) == already

    def test_unc_prefix(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(os.path, "isabs", lambda s: True)
        monkeypatch.setattr(os.path, "normpath", lambda s: s)
        out = extend("\\\\server\\share\\f")
        assert out.startswith("\\\\?\\UNC\\")


# ---- 15. ネットワークタイムアウト ----
class TestNetPath:
    def test_existing_dir_reachable(self, tmp_path):
        assert reachable(str(tmp_path)) is True

    def test_missing_dir_unreachable(self, tmp_path):
        assert reachable(str(tmp_path / "nope")) is False

    def test_timeout_returns_false(self, monkeypatch):
        import app.netpath as netpath
        import time as _t
        monkeypatch.setattr(netpath.os.path, "isdir",
                            lambda p: _t.sleep(5) or True)
        assert reachable("anything", timeout=0.2) is False

    def test_disk_usage_value_or_none(self, tmp_path):
        usage = safe_disk_usage(str(tmp_path))
        assert usage is None or (isinstance(usage, tuple) and len(usage) == 2)

    def test_disk_usage_missing_none(self):
        assert safe_disk_usage("Z:/definitely/missing/xyz", timeout=0.5) is None


# ---- 10. 競合解決 resolver ----
class TestConflictResolver:
    def test_default_renames(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("new")
        dst_dir = tmp_path / "d"
        dst_dir.mkdir()
        (dst_dir / "a.txt").write_text("old")
        ops = FileOps()
        rec = ops.copy([src], dst_dir)  # resolver なし → 連番
        assert (dst_dir / "a (2).txt").exists()
        assert (dst_dir / "a.txt").read_text() == "old"
        assert rec.pairs

    def test_overwrite_replaces(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("NEW")
        dst_dir = tmp_path / "d"
        dst_dir.mkdir()
        (dst_dir / "a.txt").write_text("OLD")
        ops = FileOps()
        ops.copy([src], dst_dir, resolver=lambda s, d: "overwrite")
        assert (dst_dir / "a.txt").read_text() == "NEW"
        assert not (dst_dir / "a (2).txt").exists()

    def test_skip_keeps_existing(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("NEW")
        dst_dir = tmp_path / "d"
        dst_dir.mkdir()
        (dst_dir / "a.txt").write_text("OLD")
        ops = FileOps()
        rec = ops.copy([src], dst_dir, resolver=lambda s, d: "skip")
        assert (dst_dir / "a.txt").read_text() == "OLD"
        assert rec.pairs == []  # 何も処理していない

    def test_cancel_stops_loop(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        for n in ("a.txt", "b.txt"):
            (tmp_path / n).write_text("x")
            (d / n).write_text("old")
        ops = FileOps()
        rec = ops.copy([tmp_path / "a.txt", tmp_path / "b.txt"], d,
                       resolver=lambda s, dd: "cancel")
        assert rec.pairs == []

    def test_move_overwrite_then_undo(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("NEW")
        d = tmp_path / "d"
        d.mkdir()
        (d / "a.txt").write_text("OLD")
        ops = FileOps()
        ops.move([src], d, resolver=lambda s, dd: "overwrite")
        assert (d / "a.txt").read_text() == "NEW"
        ops.undo()
        assert src.exists()  # 元へ戻る


# ---- 14. インデックス差分更新 ----
class TestIndexDifferentialUpdate:
    def test_update_reflects_add_and_remove(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        (root / "one.txt").write_text("x")
        idx = SearchIndex(tmp_path / "idx.db")
        assert idx.build(root) == 1
        # 追加と削除
        (root / "two.txt").write_text("x")
        (root / "one.txt").unlink()
        assert idx.update(root) == 1  # one 消滅 + two 追加 → 合計1件
        paths = idx.query(root, "two")
        assert any("two.txt" in p for p in paths)
        assert idx.query(root, "one") == []
        idx.close()

    def test_update_falls_back_to_build(self, tmp_path):
        root = tmp_path / "root2"
        root.mkdir()
        (root / "f.txt").write_text("x")
        idx = SearchIndex(tmp_path / "idx2.db")
        # build を経ずに update → 内部で build にフォールバック
        assert idx.update(root) == 1
        assert idx.query(root, "f.txt")
        idx.close()


# ---- 12 / GUI スモーク ----
class TestGuiSmoke:
    def test_conflict_dialog_builds(self, tmp_path):
        from PySide6.QtWidgets import QApplication
        from app.gui.conflict_dialog import ConflictDialog, make_resolver
        QApplication.instance() or QApplication([])
        from pathlib import Path
        dlg = ConflictDialog(Path(tmp_path / "a"), Path(tmp_path / "b"))
        assert dlg.result_action == "cancel"  # 既定
        assert callable(make_resolver(None))

    def test_directory_loaded_status(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication
        import app.paths as paths
        import app.gui.main_window as mw
        monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr(mw, "CONFIG_DIR", tmp_path / "config")
        QApplication.instance() or QApplication([])
        work = tmp_path / "work"
        work.mkdir()
        (work / "x.txt").write_text("x")
        win = mw.MainWindow()
        win.navigate(str(work))
        # ハンドラが例外なく動く
        win._on_directory_loaded(win._active_pane, str(work))


# ---- ファイルを開く処理の非ブロッキング化（フリーズ対策） ----
class TestOpenFileNonBlocking:
    def test_open_failure_falls_back_and_reports(self):
        """関連付けで開けない場合に on_fail が呼ばれる（GUI を固めない）。"""
        if os.name != "nt":
            pytest.skip("Windows only")
        from app.gui.main_window import _OpenFileJob
        got = []
        # 存在しない .json → startfile も openas も失敗 → on_fail
        job = _OpenFileJob(
            r"C:\__fibro_no_such_file__.json",
            lambda p, e: got.append((p, e)))
        job.run()
        assert got and got[0][0].endswith(".json")
