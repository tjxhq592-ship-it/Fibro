import pytest

from app.engine.file_ops import FileOps


@pytest.fixture
def env(tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    (src_dir / "f1.txt").write_text("f1")
    (src_dir / "f2.txt").write_text("f2")
    return src_dir, dst_dir


class TestMove:
    def test_move_and_undo(self, env):
        src, dst = env
        ops = FileOps()
        ops.move([src / "f1.txt"], dst)
        assert (dst / "f1.txt").exists()
        assert not (src / "f1.txt").exists()
        ops.undo()
        assert (src / "f1.txt").exists()
        assert not (dst / "f1.txt").exists()

    def test_move_conflict_renamed(self, env):
        src, dst = env
        (dst / "f1.txt").write_text("existing")
        ops = FileOps()
        record = ops.move([src / "f1.txt"], dst)
        assert record.pairs[0][1].endswith("f1 (2).txt")
        assert (dst / "f1 (2).txt").read_text() == "f1"
        assert (dst / "f1.txt").read_text() == "existing"


class TestCopy:
    def test_copy_and_undo(self, env):
        src, dst = env
        ops = FileOps()
        ops.copy([src / "f1.txt", src / "f2.txt"], dst)
        assert (dst / "f1.txt").exists()
        assert (src / "f1.txt").exists()  # 元は残る
        ops.undo()
        assert not (dst / "f1.txt").exists()
        assert (src / "f1.txt").exists()

    def test_copy_dir(self, env):
        src, dst = env
        sub = src / "sub"
        sub.mkdir()
        (sub / "inner.txt").write_text("x")
        ops = FileOps()
        ops.copy([sub], dst)
        assert (dst / "sub" / "inner.txt").exists()


class TestUndoOrder:
    def test_undo_latest_first(self, env):
        src, dst = env
        ops = FileOps()
        ops.copy([src / "f1.txt"], dst)
        ops.move([src / "f2.txt"], dst)
        ops.undo()  # move を取り消し
        assert (src / "f2.txt").exists()
        assert (dst / "f1.txt").exists()  # copy はまだ
        ops.undo()  # copy を取り消し
        assert not (dst / "f1.txt").exists()

    def test_no_undoable(self):
        with pytest.raises(RuntimeError):
            FileOps().undo()
