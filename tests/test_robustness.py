"""アプリの堅牢性（10/12/13/14/15）のテスト。

ロジックは純粋テスト、GUI は offscreen スモーク。
"""
import os
import threading
import time

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


# ---- 15. ネットワーク問い合わせの抑制（スレッド積み上がり対策） ----
class _Blocker:
    """呼ばれた回数を数え、解放されるまで返らないダミーの OS 呼び出し。"""

    def __init__(self, value=True) -> None:
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self._value = value

    def __call__(self, path):
        self.calls.append(path)
        self.entered.set()
        self.release.wait(5.0)
        return self._value


def _wait_until(pred, deadline: float = 3.0) -> bool:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class TestNetPathThrottling:
    """諦めた問い合わせが積み上がらないこと。

    到達不可パスのスレッドは OS のリトライが終わるまで（数十秒）居座る。
    一覧の更新ごとに件数ぶん立て直すと、切断中のネットワークドライブが
    1 つあるだけでスレッドが積み上がる。
    """

    def _fresh(self, monkeypatch, value=True):
        import app.netpath as netpath
        blocker = _Blocker(value)
        monkeypatch.setattr(netpath.os.path, "isdir", blocker)
        netpath.clear_cache()
        return netpath, blocker

    def test_inflight_call_is_shared(self, monkeypatch):
        """飛行中の問い合わせには相乗りし、スレッドを二重に立てない。"""
        netpath, blocker = self._fresh(monkeypatch)
        path = "\\\\srv-share\\inflight"
        assert reachable(path, timeout=0.1) is False
        assert blocker.entered.wait(1.0)
        assert len(blocker.calls) == 1
        # 否定キャッシュを外しても、飛行中である限り呼び直さない
        netpath.clear_cache()
        assert reachable(path, timeout=0.1) is False
        assert len(blocker.calls) == 1
        assert ("isdir", path) in netpath._inflight
        blocker.release.set()
        assert _wait_until(lambda: ("isdir", path) not in netpath._inflight)

    def test_negative_result_is_cached_briefly(self, monkeypatch):
        """諦めた結果は短時間だけ覚え、飛行中でなくても待ち直さない。

        失敗で終わる呼び出しを使う。成功で終わる呼び出しは
        test_late_success_drops_negative_cache のとおり記憶を捨てるため。
        """
        import app.netpath as netpath
        calls = []
        entered = threading.Event()
        release = threading.Event()

        def failing(path):
            calls.append(path)
            entered.set()
            release.wait(5.0)
            raise OSError("unreachable")

        monkeypatch.setattr(netpath.os.path, "isdir", failing)
        netpath.clear_cache()
        path = "\\\\srv-share\\negative"
        assert reachable(path, timeout=0.1) is False
        assert entered.wait(1.0)
        release.set()
        assert _wait_until(lambda: ("isdir", path) not in netpath._inflight)
        assert ("isdir", path) in netpath._negative
        # 飛行中ではないのに呼び直されない＝キャッシュが効いている
        t0 = time.monotonic()
        assert reachable(path, timeout=5.0) is False
        assert time.monotonic() - t0 < 0.5
        assert len(calls) == 1
        # clear_cache() が逃げ道になっている（手動更新用）
        netpath.clear_cache()
        assert reachable(path, timeout=1.0) is False
        assert len(calls) == 2

    def test_late_success_drops_negative_cache(self, monkeypatch):
        """諦めた後に成功が返ったら、否定キャッシュは捨てる。

        これがないと、たまたま遅かっただけの到達可能パスが TTL のあいだ
        「到達不可」に固定されてしまう。
        """
        netpath, blocker = self._fresh(monkeypatch, value=True)
        path = "\\\\srv-share\\late"
        assert reachable(path, timeout=0.05) is False
        assert blocker.entered.wait(1.0)
        assert ("isdir", path) in netpath._negative
        blocker.release.set()
        assert _wait_until(lambda: ("isdir", path) not in netpath._inflight)
        assert ("isdir", path) not in netpath._negative
        assert reachable(path, timeout=1.0) is True
        assert len(blocker.calls) == 2

    def test_inflight_is_capped(self, monkeypatch):
        """飛行中が上限に達したら OS 呼び出しを起こさず諦める。"""
        import app.netpath as netpath
        blocker = _Blocker()
        monkeypatch.setattr(netpath.os.path, "isdir", blocker)
        netpath.clear_cache()
        filler = {("isdir", f"\\\\srv-share\\f{i}"): netpath._Call()
                  for i in range(netpath._MAX_INFLIGHT)}
        with netpath._lock:
            netpath._inflight.update(filler)
        try:
            assert reachable("\\\\srv-share\\overflow", timeout=1.0) is False
            assert blocker.calls == []
            # 安全弁は「確かめていない」ので否定キャッシュには載せない
            assert ("isdir", "\\\\srv-share\\overflow") not in netpath._negative
        finally:
            with netpath._lock:
                for key in filler:
                    netpath._inflight.pop(key, None)

    def test_abandoned_thread_is_daemon_and_named(self, monkeypatch):
        """諦めたスレッドはデーモンで、conftest の残留除外名を保つ。

        名前が変わると残留スレッド検査に引っかかり、終了もブロックされる。
        """
        netpath, blocker = self._fresh(monkeypatch)
        assert reachable("\\\\srv-share\\daemon", timeout=0.05) is False
        assert blocker.entered.wait(1.0)
        alive = [t for t in threading.enumerate()
                 if t.name == netpath._TIMEOUT_THREAD_NAME]
        assert alive and all(t.daemon for t in alive)
        blocker.release.set()

    def test_kinds_do_not_collide(self, monkeypatch):
        """require_dir の有無は別の問い合わせとして扱う。

        isdir と exists を同じ鍵にすると、ファイル登録のお気に入りが
        フォルダ判定の結果を拾ってしまう。
        """
        import app.netpath as netpath
        netpath.clear_cache()
        monkeypatch.setattr(netpath.os.path, "isdir", lambda p: False)
        monkeypatch.setattr(netpath.os.path, "exists", lambda p: True)
        target = "\\\\srv-share\\file.txt"
        assert reachable(target, require_dir=True) is False
        assert reachable(target, require_dir=False) is True


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


# ---- モーダルメニューの番人（conftest の _close_popup_menus） ----
class TestPopupMenuGuard:
    """開いたままのメニューでハングしないことを保証する。

    QMenu.exec はクラス属性の差し替えが効かない（Shiboken がインスタンス
    参照で C++ の built-in を返す）。以前 conftest にあった
    monkeypatch.setattr(QMenu, "exec", ...) は素通りしており、
    メニューを開くテストはネストしたイベントループでハングしていた。
    番人が外れたら（あるいはまた効かない方法に戻ったら）ここで気付ける。
    """

    def test_exec_returns_instead_of_hanging(self, qapp, _close_popup_menus):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QMenu
        menu = QMenu("guard-probe")
        menu.addAction("alpha")
        menu.addAction("beta")
        # 番人が居なければここで返らない（pytest-timeout で落ちる）。
        menu.exec(QPoint(0, 0))
        assert not menu.isVisible()
        assert any("guard-probe" in c for c in _close_popup_menus), \
            f"番人がメニューを捕まえていない: {_close_popup_menus}"
        # 捕獲を検証したので teardown の fail は取り下げる。
        _close_popup_menus.clear()


# ---- サムネイル生成ワーカーの停止（W5: gui/jobs.py への統一） ----
class TestThumbnailJobStop:
    """ThumbnailLoader のワーカーが破棄後に emit しないことを確かめる。

    _store_result はワーカースレッドから呼ばれる。以前はその末尾で
    ready.emit() していたため、ローダーが先に消えているとワーカー側で
    RuntimeError になった（GUI からは見えない場所で落ちる）。通知は
    StoppableJob._notify 経由にして、停止要求で黙るようにしてある。
    """

    def _png(self, tmp_path):
        from PySide6.QtGui import QImage
        path = tmp_path / "a.png"
        img = QImage(8, 8, QImage.Format.Format_RGB32)
        img.fill(0xFF0000)
        assert img.save(str(path))
        return path

    def test_running_job_stores_and_notifies(self, qapp, tmp_path):
        from app.gui.thumbnails import ThumbnailLoader, _ThumbJob
        loader = ThumbnailLoader()
        got = []
        loader.ready.connect(lambda: got.append(1))
        key = (str(self._png(tmp_path)), 96)
        loader._inflight.add(key)

        _ThumbJob(loader, key, loader.ready.emit).run()

        assert got == [1]
        assert key in loader._images
        assert key not in loader._inflight

    def test_stopped_job_neither_decodes_nor_notifies(self, qapp, tmp_path):
        from app.gui.thumbnails import ThumbnailLoader, _ThumbJob
        loader = ThumbnailLoader()
        got = []
        loader.ready.connect(lambda: got.append(1))
        key = (str(self._png(tmp_path)), 96)
        loader._inflight.add(key)

        job = _ThumbJob(loader, key, loader.ready.emit)
        job.request_stop()
        job.run()

        assert got == []
        assert key not in loader._images, "結果を残すと停止の意味がない"
        # 「生成中」の印は外す（残すとそのキーは二度と生成されない）
        assert key not in loader._inflight
        assert job.finished, "JobTracker が保持を解放できない"

    def test_loader_destruction_stops_tracked_jobs(self, qapp, tmp_path):
        """ローダーの破棄で、保持中のワーカーへ停止要求が伝わる。"""
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
        from app.gui.thumbnails import ThumbnailLoader, _ThumbJob
        loader = ThumbnailLoader()
        # start せずに track だけして、破棄との競合を排除して観測する。
        job = loader._jobs.track(
            _ThumbJob(loader, ("dummy", 96), loader.ready.emit))
        assert job._stopped is False

        loader.deleteLater()
        app = QApplication.instance()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        assert job._stopped is True
