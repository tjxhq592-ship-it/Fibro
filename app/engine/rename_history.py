"""リネーム実行と Undo 履歴。

衝突回避のため、循環リネーム（a→b, b→a）にも耐えるよう
全ファイルを一時名に退避してから新名を適用する2段階方式。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.longpath import extend


@dataclass
class RenameRecord:
    """1回の一括リネーム操作の記録（Undo用）。"""
    directory: str
    mapping: list[tuple[str, str]] = field(default_factory=list)  # (旧名, 新名)


class RenameExecutor:
    def __init__(self) -> None:
        self._history: list[RenameRecord] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    @property
    def last_record(self) -> RenameRecord | None:
        return self._history[-1] if self._history else None

    def execute(self, directory: str | Path,
                pairs: list[tuple[str, str]]) -> RenameRecord:
        """(旧名, 新名) のペア群を一時名経由で適用し、履歴に積む。

        途中で失敗した場合は適用済み分をロールバックして例外を再送出。
        """
        d = Path(directory)
        temp_map: list[tuple[Path, Path, str]] = []  # (temp, final, old_name)
        done_temp: list[tuple[Path, Path]] = []      # ロールバック用 (orig, temp)
        try:
            # 第1段階: 全て一時名へ
            for old, new in pairs:
                src = d / old
                tmp = d / f".__rename_tmp_{uuid.uuid4().hex}"
                os.rename(extend(src), extend(tmp))
                done_temp.append((src, tmp))
                temp_map.append((tmp, d / new, old))
            # 第2段階: 一時名 → 新名
            record = RenameRecord(directory=str(d))
            for tmp, final, old in temp_map:
                os.rename(extend(tmp), extend(final))
                record.mapping.append((old, final.name))
        except OSError:
            # ロールバック: 一時名に退避済みのものを元へ戻す
            for orig, tmp in reversed(done_temp):
                if tmp.exists() and not orig.exists():
                    os.rename(extend(tmp), extend(orig))
            raise
        self._history.append(record)
        return record

    def undo(self) -> RenameRecord:
        """直近のリネームを取り消す。"""
        if not self._history:
            raise RuntimeError("取り消す操作がありません")
        record = self._history[-1]
        # 新名→旧名の逆適用（同じく一時名経由）
        inverse = [(new, old) for old, new in record.mapping]
        d = Path(record.directory)
        executor = RenameExecutor()  # 履歴を汚さない使い捨て
        executor.execute(d, inverse)
        self._history.pop()
        return record
