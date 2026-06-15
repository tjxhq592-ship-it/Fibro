import pytest

from app.engine.rename_history import RenameExecutor


@pytest.fixture
def workdir(tmp_path):
    for name in ["a.txt", "b.txt", "c.txt"]:
        (tmp_path / name).write_text(name)
    return tmp_path


class TestExecute:
    def test_basic_rename(self, workdir):
        ex = RenameExecutor()
        ex.execute(workdir, [("a.txt", "x.txt")])
        assert (workdir / "x.txt").exists()
        assert not (workdir / "a.txt").exists()
        assert (workdir / "x.txt").read_text() == "a.txt"

    def test_swap_names(self, workdir):
        """a↔b の循環リネームが一時名経由で成功する。"""
        ex = RenameExecutor()
        ex.execute(workdir, [("a.txt", "b.txt"), ("b.txt", "a.txt")])
        assert (workdir / "a.txt").read_text() == "b.txt"
        assert (workdir / "b.txt").read_text() == "a.txt"

    def test_failure_rolls_back(self, workdir):
        ex = RenameExecutor()
        with pytest.raises(OSError):
            ex.execute(workdir, [("a.txt", "x.txt"),
                                 ("missing.txt", "y.txt")])
        # ロールバックで元に戻っている
        assert (workdir / "a.txt").exists()
        assert not (workdir / "x.txt").exists()
        assert not ex.can_undo


class TestUndo:
    def test_undo_restores(self, workdir):
        ex = RenameExecutor()
        ex.execute(workdir, [("a.txt", "x.txt"), ("b.txt", "y.txt")])
        assert ex.can_undo
        ex.undo()
        assert (workdir / "a.txt").exists()
        assert (workdir / "b.txt").exists()
        assert not (workdir / "x.txt").exists()
        assert not ex.can_undo

    def test_undo_swap(self, workdir):
        ex = RenameExecutor()
        ex.execute(workdir, [("a.txt", "b.txt"), ("b.txt", "a.txt")])
        ex.undo()
        assert (workdir / "a.txt").read_text() == "a.txt"
        assert (workdir / "b.txt").read_text() == "b.txt"

    def test_undo_empty_raises(self):
        with pytest.raises(RuntimeError):
            RenameExecutor().undo()
