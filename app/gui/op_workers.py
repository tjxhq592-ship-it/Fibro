"""削除・Undo をバックグラウンドで実行するワーカー。

CopyWorker と同じパターン（QThread + シグナル + キャンセルフラグ）。
削除・Undo は件数ベースで進捗を報告する。進捗 emit はスロットリングして
大量ファイルでもシグナル洪水にならないようにする。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from app.engine.file_ops import FileOps, OpRecord


class DeleteWorker(QObject):
    """ゴミ箱移動/完全削除をワーカースレッドで実行する。

    send2trash はシェル API 経由で大量選択やネットワークドライブでは
    数秒かかることがあり、GUI スレッドで回すと固まるためここで実行する。
    """

    # done_count, total_count, 現在処理中の名前
    progress = Signal(int, int, str)
    # OpRecord（成功・部分完了）, エラー文字列 or None
    finished = Signal(object, object)

    _EMIT_INTERVAL = 0.05  # s。進捗 emit の最小間隔（UI 更新の間引き）

    def __init__(self, file_ops: FileOps, paths: list[str],
                 permanent: bool) -> None:
        super().__init__()
        self._file_ops = file_ops
        self._paths = paths
        self._permanent = permanent
        self._cancel = threading.Event()
        self._last_emit = 0.0

    def cancel(self) -> None:
        """GUI スレッドから呼ぶ。次のアイテム境界で中断する。"""
        self._cancel.set()

    def _should_cancel(self) -> bool:
        return self._cancel.is_set()

    def _on_item(self, done: int, name: str) -> None:
        now = time.monotonic()
        if now - self._last_emit >= self._EMIT_INTERVAL:
            self._last_emit = now
            self.progress.emit(done, len(self._paths), name)

    @Slot()
    def run(self) -> None:
        """QThread.started から呼ばれる本体。"""
        try:
            record = self._file_ops.run_delete(
                self._paths, permanent=self._permanent,
                on_item=self._on_item, should_cancel=self._should_cancel)
            self.progress.emit(len(self._paths), len(self._paths), "")
            self.finished.emit(record, None)
        except Exception as e:  # noqa: BLE001 — ワーカーの失敗は GUI へ集約
            self.finished.emit(None, str(e))


class UndoWorker(QObject):
    """直近の move/copy の取り消しをワーカースレッドで実行する。

    大きな移動/コピーの取り消し（shutil.move / rmtree のループ）は
    進めるときと同様に時間がかかるため、GUI スレッドでは回さない。
    ユーザー向けのキャンセルは部分取り消し状態を作るため提供しない
    （cancel() はウィンドウを閉じるときの合流専用）。
    履歴からの除去は成功後に GUI スレッド側で discard_record を呼ぶ。
    """

    # done_count, total_count, 現在処理中の名前
    progress = Signal(int, int, str)
    # 取り消した OpRecord, エラー文字列 or None
    finished = Signal(object, object)

    _EMIT_INTERVAL = 0.05  # s

    def __init__(self, record: OpRecord) -> None:
        super().__init__()
        self._record = record
        self._cancel = threading.Event()
        self._last_emit = 0.0

    def cancel(self) -> None:
        """アプリ終了時の合流用。次のアイテム境界で中断する。"""
        self._cancel.set()

    def _should_cancel(self) -> bool:
        return self._cancel.is_set()

    def _on_item(self, done: int, name: str) -> None:
        now = time.monotonic()
        if now - self._last_emit >= self._EMIT_INTERVAL:
            self._last_emit = now
            self.progress.emit(done, len(self._record.pairs), name)

    @Slot()
    def run(self) -> None:
        """QThread.started から呼ばれる本体。"""
        try:
            FileOps.apply_undo(self._record, self._on_item,
                               self._should_cancel)
            n = len(self._record.pairs)
            self.progress.emit(n, n, "")
            self.finished.emit(self._record, None)
        except Exception as e:  # noqa: BLE001 — ワーカーの失敗は GUI へ集約
            self.finished.emit(self._record, str(e))
