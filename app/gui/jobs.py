"""ワーカー（QRunnable）の共通土台。

ワーカーは結果の受け手であるウィジェットより長生きしうる。停止要求を見ずに
シグナルを emit すると、破棄済みの C++ オブジェクトに触れてワーカースレッド
側で「Error calling Python override of QRunnable::run()」になる。GUI スレッド
から見えない場所で起きるため気付きにくく、テストではプロセス終了時にだけ
現れることもある。停止フラグと破棄時の一括停止をここに集約する。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QRunnable


class StoppableJob(QRunnable):
    """結果通知を止められる QRunnable。

    サブクラスは run() ではなく _work() を実装し、emit の代わりに _notify() を
    呼び、長いループでは _stopped を見て早期に抜けること。finished を立てるのと
    例外を記録するのは run() が引き受けるので、サブクラスは書かなくてよい
    （書き忘れると JobTracker._jobs から永久に落ちなくなる類の漏れを、
    継承した時点で構造的に防ぐ）。
    """

    def __init__(self, emit) -> None:
        super().__init__()
        self._emit = emit
        self._stopped = False
        self.finished = False

    def run(self) -> None:
        """QRunnable の入口。ここで例外と finished を必ず引き受ける。

        サブクラスはこれを上書きせず _work() を実装すること。run() から例外が
        漏れると Qt 側で「Error calling Python override of QRunnable::run()」に
        なり、ワーカースレッドで起きるため GUI からは何も見えない。
        """
        try:
            self._work()
        except Exception:  # noqa: BLE001 — ワーカーの例外を握って必ず記録する
            # main.py の excepthook が CONFIG_DIR/error.log へ追記する。
            # 標準の sys.excepthook にも流すので挙動は変わらない。
            sys.excepthook(*sys.exc_info())
        finally:
            self.finished = True

    def _work(self) -> None:
        """実処理。サブクラスで実装する。"""
        raise NotImplementedError

    def request_stop(self) -> None:
        """以降の通知を止める（受け手を破棄する側から呼ぶ）。"""
        self._stopped = True

    def _notify(self, *args) -> None:
        if self._stopped:
            return
        try:
            self._emit(*args)
        except RuntimeError:
            # request_stop と実際の破棄の間の競合。受け手が消えていれば捨てる。
            self._stopped = True


class JobTracker:
    """owner の破棄時に、実行中ワーカーの通知をまとめて止める保持箱。"""

    def __init__(self, owner: QObject) -> None:
        self._jobs: list[StoppableJob] = []
        # owner を捕捉するとワーカー経由で参照が残り破棄を妨げる。リスト実体
        # だけを掴む（以降 self._jobs は「その場で」書き換えること）。
        jobs = self._jobs
        owner.destroyed.connect(lambda *_: [j.request_stop() for j in jobs])

    def track(self, job: StoppableJob) -> StoppableJob:
        """ワーカーを保持して返す（完了済みの分はここで捨てる）。"""
        self._jobs[:] = [j for j in self._jobs if not j.finished]
        self._jobs.append(job)
        return job

    def stop_all(self) -> None:
        """明示的に全ワーカーの通知を止める（破棄前の後片付け用）。"""
        for job in self._jobs:
            job.request_stop()
