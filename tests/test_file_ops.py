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


class TestDeletePermanent:
    def test_delete_permanent_file_and_dir(self, env):
        src, _dst = env
        sub = src / "sub"
        sub.mkdir()
        (sub / "inner.txt").write_text("x")
        ops = FileOps()
        n = ops.delete_permanent([src / "f1.txt", sub])
        assert n == 2
        assert not (src / "f1.txt").exists()
        assert not sub.exists()
        assert (src / "f2.txt").exists()  # 対象外は残る

    def test_delete_permanent_missing_raises(self, env):
        src, _dst = env
        with pytest.raises(OSError):
            FileOps().delete_permanent([src / "nope.txt"])

    def test_run_delete_cancel_partial(self, env):
        """キャンセル時はそれまでに削除できた分だけ pairs に残る。"""
        src, _dst = env
        deleted = []

        def cancel_after_first() -> bool:
            return len(deleted) >= 1

        record = FileOps.run_delete(
            [src / "f1.txt", src / "f2.txt"], permanent=True,
            on_item=lambda i, name: deleted.append(name),
            should_cancel=cancel_after_first)
        assert len(record.pairs) == 1
        assert not (src / "f1.txt").exists()
        assert (src / "f2.txt").exists()  # 2件目の前で中断

    def test_run_delete_reports_progress(self, env):
        src, _dst = env
        seen = []
        FileOps.run_delete([src / "f1.txt", src / "f2.txt"], permanent=True,
                           on_item=lambda i, name: seen.append((i, name)))
        assert seen == [(0, "f1.txt"), (1, "f2.txt")]


class TestUndoSplit:
    """undo のワーカー分割 API（peek → apply → discard）。"""

    def test_peek_apply_discard_roundtrip(self, env):
        src, dst = env
        ops = FileOps()
        ops.move([src / "f1.txt"], dst)
        record = ops.peek_undo()
        assert record is not None and record.kind == "move"
        assert ops.can_undo  # peek では履歴から外れない
        FileOps.apply_undo(record)
        assert (src / "f1.txt").exists()
        ops.discard_record(record)
        assert not ops.can_undo

    def test_peek_undo_skips_delete(self, env):
        src, _dst = env
        ops = FileOps()
        ops.delete_permanent([src / "f1.txt"])
        assert ops.peek_undo() is None


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
