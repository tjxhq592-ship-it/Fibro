"""サムネイル生成（非同期＋LRU キャッシュ）と描画デリゲート。

画像ファイルのみ縮小サムネを生成。重いデコードは QThreadPool でバックグラウンド
処理し、GUI スレッドを止めない（生成中はネイティブアイコンを仮表示し、完成後に
その場を再描画）。QPixmap は GUI スレッド専用のため、ワーカーでは QImage を扱い、
描画時に QPixmap 化する。非画像・サイズ超過・失敗は None。外部依存なし。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate

from app.imagetypes import is_image

_MAX_BYTES = 30 * 1024 * 1024  # 30MB 超は生成しない
_DEFAULT_SIZE = 96


class ThumbnailCache:
    """path → QPixmap|None の LRU キャッシュ（同期 API・テスト互換）。"""

    def __init__(self, capacity: int = 512) -> None:
        self._capacity = capacity
        self._store: OrderedDict[tuple[str, int], object] = OrderedDict()

    def get(self, path: str | Path, size: int = _DEFAULT_SIZE):
        """サムネを返す（QPixmap か None）。生成結果はキャッシュ。"""
        key = (str(path), size)
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        pixmap = _make_thumbnail(path, size)
        self._store[key] = pixmap
        if len(self._store) > self._capacity:
            self._store.popitem(last=False)  # 最古を捨てる
        return pixmap

    def __len__(self) -> int:
        return len(self._store)


def _make_thumbnail_image(path: str | Path, size: int):
    """サムネを QImage で生成（ワーカースレッドで安全）。失敗時 None。"""
    p = Path(path)
    if not is_image(p):
        return None
    try:
        if p.stat().st_size > _MAX_BYTES:
            return None
    except OSError:
        return None
    img = QImage(str(p))
    if img.isNull():
        return None
    return img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def _make_thumbnail(path: str | Path, size: int):
    """サムネを QPixmap で生成（同期 API。GUI スレッドで呼ぶこと）。"""
    img = _make_thumbnail_image(path, size)
    return QPixmap.fromImage(img) if img is not None else None


# モジュール共有のキャッシュ（同期 API。プロセス全体で再利用）
_SHARED = ThumbnailCache()


def thumbnail(path: str | Path, size: int = _DEFAULT_SIZE):
    """共有キャッシュ経由でサムネを取得（QPixmap か None・同期）。"""
    return _SHARED.get(path, size)


# --- 非同期ローダー -------------------------------------------------------

_PENDING = object()  # 「生成中」を表すセンチネル


class _ThumbJob(QRunnable):
    def __init__(self, loader: "ThumbnailLoader", key: tuple[str, int]) -> None:
        super().__init__()
        self._loader = loader
        self._key = key

    def run(self) -> None:  # ワーカースレッド
        path, size = self._key
        img = _make_thumbnail_image(path, size)
        self._loader._store_result(self._key, img)


class ThumbnailLoader(QObject):
    """バックグラウンドでサムネ生成し、完成したら ready を emit する。"""

    ready = Signal()  # 1枚でも完成したらビューへ再描画を促す

    def __init__(self, capacity: int = 1024, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._capacity = capacity
        self._pool = QThreadPool(self)
        half = (QThreadPool.globalInstance().maxThreadCount() // 2) or 2
        self._pool.setMaxThreadCount(max(2, half))
        self._lock = threading.Lock()
        self._images: OrderedDict[tuple[str, int], object] = OrderedDict()
        self._pixmaps: dict[tuple[str, int], object] = {}
        self._inflight: set[tuple[str, int]] = set()

    def request(self, path: str | Path, size: int = _DEFAULT_SIZE):
        """QPixmap（完成）／None（非画像等）／_PENDING（生成中）を返す。"""
        key = (str(path), size)
        pm = self._pixmaps.get(key, _PENDING)
        if pm is not _PENDING:
            return pm  # QPixmap か None（GUI スレッドで変換済み）
        with self._lock:
            if key in self._images:
                img = self._images[key]
                self._images.move_to_end(key)
                result = QPixmap.fromImage(img) if img is not None else None
                self._pixmaps[key] = result
                return result
            if key in self._inflight:
                return _PENDING
            self._inflight.add(key)
        self._pool.start(_ThumbJob(self, key))
        return _PENDING

    def _store_result(self, key: tuple[str, int], img) -> None:
        """ワーカーから結果（QImage|None）を格納し、再描画を促す。"""
        with self._lock:
            self._images[key] = img
            self._inflight.discard(key)
            while len(self._images) > self._capacity:
                old, _ = self._images.popitem(last=False)
                self._pixmaps.pop(old, None)
        self.ready.emit()


_SHARED_LOADER: "ThumbnailLoader | None" = None


def shared_loader() -> "ThumbnailLoader":
    """プロセス共有の非同期ローダー（遅延生成）。"""
    global _SHARED_LOADER
    if _SHARED_LOADER is None:
        _SHARED_LOADER = ThumbnailLoader()
    return _SHARED_LOADER


class ThumbnailDelegate(QStyledItemDelegate):
    """アイコンビュー用デリゲート。画像はサムネ（非同期）、他は既定描画。"""

    def __init__(self, model_path_func, size: int = _DEFAULT_SIZE,
                 loader: "ThumbnailLoader | None" = None, parent=None) -> None:
        super().__init__(parent)
        self._path_of = model_path_func  # index -> ファイルパス文字列
        self._size = size
        self._loader = loader or shared_loader()

    def paint(self, painter, option, index) -> None:  # noqa: N802 — Qt API
        # ネイティブ描画（選択ハイライト＋既定アイコン＝生成中の仮表示）
        super().paint(painter, option, index)
        path = self._path_of(index)
        if not path:
            return
        pix = self._loader.request(path, self._size)
        if pix is _PENDING or pix is None:
            return  # 生成中／非画像はネイティブアイコンのまま
        rect = option.rect
        x = rect.x() + (rect.width() - pix.width()) // 2
        y = rect.y() + (rect.height() - pix.height()) // 2 - 8
        painter.drawPixmap(x, y, pix)
