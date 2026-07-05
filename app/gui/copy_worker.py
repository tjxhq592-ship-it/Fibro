"""コピー/移動をバックグラウンドで実行するワーカー。

GUI スレッドで build_plan（衝突解決）を済ませた計画を受け取り、QThread 上で
チャンクコピーを実行する。進捗は progress、完了/失敗は finished で GUI へ返す。
進捗 emit はスロットリングして大量ファイルでもシグナル洪水にならないようにする。
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from app.engine.file_ops import CopyPlanItem, FileOps, OpRecord


class CopyWorker(QObject):
    """run_plan をワーカースレッドで回し、進捗/完了をシグナルで通知する。"""

    # done_bytes, total_bytes, 現在処理中の名前
    progress = Signal(int, int, str)
    # OpRecord（成功・部分完了）, エラー文字列 or None
    finished = Signal(object, object)

    _EMIT_INTERVAL = 0.05  # s。進捗 emit の最小間隔（UI 更新の間引き）

    def __init__(self, file_ops: FileOps, plan: list[CopyPlanItem],
                 kind: str, total_bytes: int) -> None:
        super().__init__()
        self._file_ops = file_ops
        self._plan = plan
        self._kind = kind
        self._total = total_bytes
        self._done = 0
        self._cancel = threading.Event()
        self._last_emit = 0.0
        self._idx = 0  # 進捗ラベル用の現在アイテム位置

    def cancel(self) -> None:
        """GUI スレッドから呼ぶ。次のチャンク確認で中断する。"""
        self._cancel.set()

    def _on_bytes(self, delta: int) -> None:
        self._done += delta
        now = time.monotonic()
        if now - self._last_emit >= self._EMIT_INTERVAL:
            self._last_emit = now
            self.progress.emit(self._done, self._total, self._current_name())

    def _should_cancel(self) -> bool:
        return self._cancel.is_set()

    def _current_name(self) -> str:
        if 0 <= self._idx < len(self._plan):
            from pathlib import Path
            return Path(self._plan[self._idx].src).name
        return ""

    @Slot()
    def run(self) -> None:
        """QThread.started から呼ばれる本体。"""
        try:
            # アイテム境界で名前を更新しつつ run_plan に流す。
            record = OpRecord(kind=self._kind)
            for i, item in enumerate(self._plan):
                if self._cancel.is_set():
                    break
                self._idx = i
                self.progress.emit(self._done, self._total, self._current_name())
                single = self._file_ops.run_plan(
                    [item], self._kind, self._on_bytes, self._should_cancel)
                record.pairs.extend(single.pairs)
                if self._cancel.is_set() and not single.pairs:
                    break
            # 完了時に最終値を必ず反映
            self.progress.emit(self._total, self._total, "")
            self.finished.emit(record, None)
        except Exception as e:  # noqa: BLE001 — ワーカーの失敗は GUI へ集約
            self.finished.emit(None, str(e))
