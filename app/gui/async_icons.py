"""拡張子単位でキャッシュする遅延シェルアイコンプロバイダ。

QFileSystemModel の gatherer スレッドが OneDrive 等のクラウドフォルダで
ファイルごとにシェルへアイコンを問い合わせる（SHGetFileInfo）と、初回列挙時に
数十秒固まることがある。これを避けるため:

  * icon() は即座に汎用アイコンを返して gatherer スレッドを止めない。
  * 実アイコンは GUI スレッドのイベントループで「拡張子ごとに1回だけ」解決し、
    キャッシュする（1000ファイルでも種類数ぶんの問い合わせで済む）。
  * 解決できたら ready を emit し、ビューに再描画（差分反映）を促す。

QIcon は GUI スレッド専用のため、解決自体は GUI スレッドで行う（サムネイルの
ように QImage をワーカーで作る手は使えない）。代わりに「拡張子ごと1回」へ
まとめることで、シェル問い合わせ回数を劇的に減らしてフリーズを解消する。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QFileInfo, QObject, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileIconProvider


class _ProviderSignals(QObject):
    ready = Signal()      # 1件でも実アイコンが解決したら再描画を促す
    request = Signal()    # gatherer→GUI: 解決要求（キュー接続でGUIスレッドへ）


class LazyShellIconProvider(QFileIconProvider):
    """拡張子をキーに実アイコンを遅延・差分解決するアイコンプロバイダ。"""

    def __init__(self) -> None:
        super().__init__()
        self.signals = _ProviderSignals()
        # gatherer スレッドから emit → GUI スレッドの _resolve_pending で受ける
        self.signals.request.connect(
            self._resolve_pending, Qt.ConnectionType.QueuedConnection)

        # 実アイコン解決用（自分の override を踏まないよう別インスタンスを使う）
        self._base = QFileIconProvider()
        self._lock = threading.Lock()
        self._cache: dict[str, QIcon] = {}     # 拡張子(小文字) -> 実アイコン
        self._pending: dict[str, str] = {}     # 拡張子 -> 代表ファイルパス

        # シェル問い合わせ無しの汎用アイコン（即返し用）
        self._generic_file = self._base.icon(QFileIconProvider.IconType.File)
        self._generic_dir = self._base.icon(QFileIconProvider.IconType.Folder)

    def icon(self, arg):  # noqa: N802 — Qt API。gatherer スレッドから呼ばれる
        # IconType を要求する汎用オーバーロードは既定に委ねる
        if not isinstance(arg, QFileInfo):
            return self._base.icon(arg)
        if arg.isDir():
            return self._generic_dir
        ext = arg.suffix().lower()
        with self._lock:
            cached = self._cache.get(ext)
            if cached is not None:
                return cached
            # 未解決：代表パスを控えて汎用を即返す（スレッドを止めない）
            if ext not in self._pending:
                self._pending[ext] = arg.absoluteFilePath()
                schedule = True
            else:
                schedule = False
        if schedule:
            self.signals.request.emit()  # GUI スレッドへキュー
        return self._generic_file

    def _resolve_pending(self) -> None:
        """GUI スレッド: 未解決の拡張子を少しずつ実アイコンへ解決する。"""
        resolved = False
        for _ in range(8):  # 1ティックあたり最大8種（UI を細切れに保つ）
            with self._lock:
                if not self._pending:
                    break
                ext, path = self._pending.popitem()
            try:
                real = self._base.icon(QFileInfo(path))  # 実シェル問い合わせ
            except Exception:
                real = None
            with self._lock:
                self._cache[ext] = real if real and not real.isNull() \
                    else self._generic_file
            resolved = True
        if resolved:
            self.signals.ready.emit()
        # まだ残っていれば次のティックへ
        with self._lock:
            more = bool(self._pending)
        if more:
            self.signals.request.emit()


_SHARED_PROVIDER: "LazyShellIconProvider | None" = None


def shared_icon_provider() -> "LazyShellIconProvider":
    """プロセス共有の遅延アイコンプロバイダ（拡張子キャッシュを全モデルで共有）。"""
    global _SHARED_PROVIDER
    if _SHARED_PROVIDER is None:
        _SHARED_PROVIDER = LazyShellIconProvider()
    return _SHARED_PROVIDER
