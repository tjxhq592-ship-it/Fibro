"""デザイン機能（16 種別アイコン・17 サムネイル・18 カラム）のテスト。

サムネ生成は純粋テスト、ビュー切替/カラムは GUI スモーク。
"""
import os
import struct
import zlib

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.gui.thumbnails import ThumbnailCache, thumbnail  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _write_png(path, w=4, h=4):
    """依存なしで最小の有効 PNG を生成する。"""
    raw = b""
    for _ in range(h):
        raw += b"\x00" + b"\xff\x00\x00" * w  # 各行: フィルタ0 + RGB赤
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw)
    path.write_bytes(sig + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def _make_window(tmp_path, monkeypatch):
    import app.paths as paths
    import app.gui.main_window as mw
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(mw, "CONFIG_DIR", tmp_path / "config")
    from app.gui.main_window import MainWindow
    return MainWindow()


# ---- 17. サムネイル（純粋） ----
class TestThumbnails:
    def test_image_returns_pixmap(self, qapp, tmp_path):
        p = tmp_path / "a.png"
        _write_png(p)
        pix = thumbnail(str(p))
        assert isinstance(pix, QPixmap) and not pix.isNull()

    def test_non_image_returns_none(self, qapp, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("x", encoding="utf-8")
        assert thumbnail(str(p)) is None

    def test_cache_returns_same_object(self, qapp, tmp_path):
        p = tmp_path / "b.png"
        _write_png(p)
        cache = ThumbnailCache()
        first = cache.get(str(p))
        second = cache.get(str(p))
        assert first is second  # 2回目はキャッシュ

    def test_cache_evicts(self, qapp, tmp_path):
        cache = ThumbnailCache(capacity=2)
        for i in range(3):
            p = tmp_path / f"i{i}.txt"
            p.write_text("x", encoding="utf-8")
            cache.get(str(p))
        assert len(cache) <= 2

    def test_oversize_returns_none(self, qapp, tmp_path, monkeypatch):
        import app.gui.thumbnails as th
        p = tmp_path / "big.png"
        _write_png(p)
        monkeypatch.setattr(th, "_MAX_BYTES", 1)  # 1バイト上限 → 超過
        # 直接 _make_thumbnail を見る（共有キャッシュを汚さない）
        assert th._make_thumbnail(str(p), 96) is None

    def test_async_loader_pending_then_ready(self, qapp, tmp_path):
        """非同期ローダー: 初回 PENDING → 完成後に QPixmap を返す。"""
        from app.gui.thumbnails import ThumbnailLoader, _PENDING
        p = tmp_path / "async.png"
        _write_png(p, 8, 8)
        loader = ThumbnailLoader()
        done = []
        loader.ready.connect(lambda: done.append(True))
        # 初回はバックグラウンド生成開始 → PENDING
        assert loader.request(str(p)) is _PENDING
        # ワーカー完了を待ってから queued な ready を配送
        loader._pool.waitForDone(3000)
        for _ in range(50):
            qapp.processEvents()
            if done:
                break
        assert done, "ready が発火しなかった"
        result = loader.request(str(p))
        assert isinstance(result, QPixmap) and not result.isNull()

    def test_async_loader_non_image_is_none(self, qapp, tmp_path):
        """非画像は最終的に None（生成中は PENDING）。"""
        from app.gui.thumbnails import ThumbnailLoader, _PENDING
        p = tmp_path / "x.txt"
        p.write_text("x", encoding="utf-8")
        loader = ThumbnailLoader()
        done = []
        loader.ready.connect(lambda: done.append(True))
        assert loader.request(str(p)) is _PENDING
        loader._pool.waitForDone(3000)
        for _ in range(50):
            qapp.processEvents()
            if done:
                break
        assert loader.request(str(p)) is None


# ---- 16/17/18 GUI スモーク ----
class TestViewModes:
    def test_toggle_view_mode(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "v"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        assert win._active_pane.view_mode == "details"
        win.toggle_view_mode()
        assert win._active_pane.view_mode == "thumbnails"
        # 設定にも保存
        assert win.theme_manager.get("view_mode") == "thumbnails"

    def test_view_mode_applies_all_panes(self, qapp, tmp_path, monkeypatch):
        a, b = tmp_path / "p1", tmp_path / "p2"
        a.mkdir()
        b.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win.new_tab(str(b))
        win.toggle_view_mode()
        assert all(p.view_mode == "thumbnails" for p in win._tabs)

    def test_shared_selection_between_views(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "sel"
        a.mkdir()
        (a / "f.txt").write_text("x")
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        pane = win._active_pane
        # table と icon_view が同じ選択モデルを共有
        assert pane.table.selectionModel() is pane.icon_view.selectionModel()

    def test_navigate_syncs_both_roots(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "r"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        pane = win._active_pane
        assert pane.icon_view.rootIndex() == pane.table.rootIndex()

    def test_icon_size_set(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "ic"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        assert win._active_pane.table.iconSize().width() == 18


# ---- 18. カラムカスタマイズ ----
class TestColumns:
    def test_hide_show_column(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "c"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        table = win._active_pane.table
        table.setColumnHidden(2, True)
        assert table.isColumnHidden(2)
        table.setColumnHidden(2, False)
        assert not table.isColumnHidden(2)

    def test_column_state_roundtrip(self, qapp, tmp_path, monkeypatch):
        a = tmp_path / "c2"
        a.mkdir()
        win = _make_window(tmp_path, monkeypatch)
        win.navigate(str(a))
        win._active_pane.table.setColumnHidden(1, True)
        win._save_columns()
        saved = win.theme_manager.get("columns")
        assert isinstance(saved, str) and saved
        # 新しいペインに復元すると列1が隠れている
        win.new_tab(str(a))
        assert win._active_pane.table.isColumnHidden(1)
