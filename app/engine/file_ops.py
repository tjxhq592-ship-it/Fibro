"""ファイル操作（移動・コピー・削除）と Undo。

削除は send2trash でゴミ箱へ（実質 Undo 可能）。
移動・コピーは直近操作を取り消し可能。
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.longpath import extend

# チャンクコピーのバッファ（進捗報告・キャンセル確認の粒度）。
_CHUNK = 1024 * 1024  # 1 MiB


def _send2trash(path: str) -> None:
    """send2trash を遅延 import（起動時 ~65ms を削減）。"""
    from send2trash import send2trash
    send2trash(path)


# 衝突解決の戻り値: "overwrite" | "skip" | "rename" | "cancel"
Resolver = Callable[[Path, Path], str]

# 進捗コールバック: コピーしたバイト数の増分を受け取る。
OnBytes = Callable[[int], None]
# キャンセル判定: True を返したら中断する。
ShouldCancel = Callable[[], bool]


class _Cancelled(Exception):
    """衝突解決でユーザーが中止を選んだことを表す内部シグナル。"""


def total_size(sources: list[str | Path]) -> int:
    """ファイル/ディレクトリの総バイト数（進捗バーの分母）。

    走査失敗（権限・到達不可）は 0 として無視し、例外を漏らさない。
    """
    total = 0
    for src in sources:
        p = Path(src)
        try:
            if p.is_dir():
                for root, _dirs, files in os.walk(extend(p)):
                    for name in files:
                        try:
                            total += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass
            else:
                total += os.path.getsize(extend(p))
        except OSError:
            pass
    return total


def _copy_file_chunked(src: str | Path, dst: str | Path,
                       on_bytes: OnBytes | None,
                       should_cancel: ShouldCancel | None) -> None:
    """1 ファイルをチャンク単位でコピー（進捗報告・キャンセル可能）。

    途中キャンセル時は作りかけの dst を削除して _Cancelled を送出する。
    完了後にメタデータ（更新日時等）を複製する。
    """
    es, ed = extend(src), extend(dst)
    try:
        with open(es, "rb") as fsrc, open(ed, "wb") as fdst:
            while True:
                if should_cancel is not None and should_cancel():
                    raise _Cancelled
                chunk = fsrc.read(_CHUNK)
                if not chunk:
                    break
                fdst.write(chunk)
                if on_bytes is not None:
                    on_bytes(len(chunk))
    except _Cancelled:
        try:
            os.remove(ed)  # 作りかけを残さない
        except OSError:
            pass
        raise
    shutil.copystat(es, ed)


def _copy_tree_chunked(src_dir: str | Path, dst_dir: str | Path,
                       on_bytes: OnBytes | None,
                       should_cancel: ShouldCancel | None) -> None:
    """ディレクトリを再帰的にチャンクコピー（進捗報告・キャンセル可能）。

    shutil.copytree の代替。進捗とキャンセルを各ファイルで反映する。
    """
    es, ed = extend(src_dir), extend(dst_dir)
    os.makedirs(ed, exist_ok=True)
    with os.scandir(es) as it:
        entries = list(it)
    for entry in entries:
        if should_cancel is not None and should_cancel():
            raise _Cancelled
        src_child = os.path.join(str(src_dir), entry.name)
        dst_child = os.path.join(str(dst_dir), entry.name)
        if entry.is_dir(follow_symlinks=False):
            _copy_tree_chunked(src_child, dst_child, on_bytes, should_cancel)
        else:
            _copy_file_chunked(src_child, dst_child, on_bytes, should_cancel)
    shutil.copystat(es, ed)


def _same_device(src: str | Path, dest: str | Path) -> bool:
    """src と dest が同一ドライブ（同一デバイス）かを判定する。

    dest はまだ存在しないことがあるため親ディレクトリで判定する。
    判定不能時は False（安全側＝別ドライブ＝コピー扱い）を返す。
    """
    try:
        src_dev = os.stat(extend(src)).st_dev
        dest_dev = os.stat(extend(Path(dest).parent)).st_dev
        return src_dev == dest_dev
    except OSError:
        return False


@dataclass
class CopyPlanItem:
    """build_plan が生成する 1 件分の実行計画。"""
    src: str
    dest: str             # 最終的なコピー/移動先（リネーム解決済み）
    overwrite: bool       # True なら実行前に既存 dest をゴミ箱へ退避
    is_dir: bool


def build_plan(sources: list[str | Path], dest_dir: str | Path,
               resolver: Resolver | None = None) -> list[CopyPlanItem] | None:
    """衝突解決（GUI スレッド）だけ行い、実行計画を組み立てる。

    実際のコピー/ゴミ箱退避はしない（ワーカースレッドの run_plan に委譲）。
    ユーザーが「中止」を選んだら None を返す。
    """
    d = Path(dest_dir)
    plan: list[CopyPlanItem] = []
    for src in map(Path, sources):
        dest = d / src.name
        overwrite = False
        if dest.exists():
            action = resolver(src, dest) if resolver else "rename"
            if action == "skip":
                continue
            if action == "cancel":
                return None
            if action == "overwrite":
                overwrite = True
            else:  # "rename"（既定）
                dest = _unique_dest(dest)
        plan.append(CopyPlanItem(
            src=str(src), dest=str(dest),
            overwrite=overwrite, is_dir=src.is_dir()))
    return plan


@dataclass
class OpRecord:
    kind: str  # "move" | "copy" | "delete"
    # (元パス, 先パス) のリスト。delete は (元パス, "") のみ。
    pairs: list[tuple[str, str]] = field(default_factory=list)


def _unique_dest(dest: Path) -> Path:
    """衝突時に ` (2)` などを付与した重複しないパスを返す。"""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 2
    while True:
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _resolve_dest(dest: Path, resolver: Resolver | None,
                  src: Path) -> Path | None:
    """衝突時に解決方針を適用して最終 dest を返す。

    None を返したらスキップ。"cancel" は呼び出し側でループ中断するため
    番兵 None と区別して例外的に文字列を投げず、戻り値で表現する。
    """
    if not dest.exists():
        return dest
    action = resolver(src, dest) if resolver else "rename"
    if action == "skip":
        return None
    if action == "cancel":
        raise _Cancelled
    if action == "overwrite":
        # 既存をゴミ箱へ退避してから上書き（誤上書きでも復元可）
        _send2trash(str(dest))
        return dest
    return _unique_dest(dest)  # "rename"（既定）


class FileOps:
    def __init__(self) -> None:
        self._history: list[OpRecord] = []

    @property
    def can_undo(self) -> bool:
        return any(r.kind in ("move", "copy") for r in self._history)

    def add_record(self, record: OpRecord) -> None:
        """ワーカーが作った OpRecord を履歴へ積む（GUI スレッドから呼ぶ）。"""
        self._history.append(record)

    def run_plan(self, plan: list[CopyPlanItem], kind: str,
                 on_bytes: OnBytes | None = None,
                 should_cancel: ShouldCancel | None = None) -> OpRecord:
        """build_plan の計画をチャンク単位で実行する（ワーカースレッド）。

        kind="copy": ファイル/ツリーをチャンクコピー（overwrite はゴミ箱退避後）。
        kind="move": 同一ドライブは shutil.move（瞬時リネーム）、別ドライブは
                     チャンクコピー後に元を削除。
        途中キャンセルされた場合も、それまでに完了した分を record に残して返す
        （部分完了分は undo 可能）。
        """
        record = OpRecord(kind=kind)
        try:
            for item in plan:
                if should_cancel is not None and should_cancel():
                    break
                src, dest = item.src, item.dest
                if item.overwrite and Path(dest).exists():
                    _send2trash(dest)
                if kind == "move":
                    self._exec_move(item, on_bytes, should_cancel)
                else:
                    self._exec_copy(item, on_bytes, should_cancel)
                record.pairs.append((src, dest))
        except _Cancelled:
            pass
        return record

    @staticmethod
    def _exec_copy(item: CopyPlanItem, on_bytes: OnBytes | None,
                   should_cancel: ShouldCancel | None) -> None:
        if item.is_dir:
            _copy_tree_chunked(item.src, item.dest, on_bytes, should_cancel)
        else:
            _copy_file_chunked(item.src, item.dest, on_bytes, should_cancel)

    @staticmethod
    def _exec_move(item: CopyPlanItem, on_bytes: OnBytes | None,
                   should_cancel: ShouldCancel | None) -> None:
        # 同一ドライブなら rename 相当で瞬時（バイト進捗なし）。
        if _same_device(item.src, item.dest):
            shutil.move(extend(item.src), extend(item.dest))
            return
        # 別ドライブ: チャンクコピー後に元を削除（進捗・キャンセル可能）。
        if item.is_dir:
            _copy_tree_chunked(item.src, item.dest, on_bytes, should_cancel)
            shutil.rmtree(extend(item.src), ignore_errors=True)
        else:
            _copy_file_chunked(item.src, item.dest, on_bytes, should_cancel)
            try:
                os.remove(extend(item.src))
            except OSError:
                pass

    def move(self, sources: list[str | Path], dest_dir: str | Path,
             resolver: Resolver | None = None) -> OpRecord:
        d = Path(dest_dir)
        record = OpRecord(kind="move")
        try:
            for src in map(Path, sources):
                dest = _resolve_dest(d / src.name, resolver, src)
                if dest is None:
                    continue
                shutil.move(extend(src), extend(dest))
                record.pairs.append((str(src), str(dest)))
        except _Cancelled:
            pass
        self._history.append(record)
        return record

    def copy(self, sources: list[str | Path], dest_dir: str | Path,
             resolver: Resolver | None = None) -> OpRecord:
        d = Path(dest_dir)
        record = OpRecord(kind="copy")
        try:
            for src in map(Path, sources):
                dest = _resolve_dest(d / src.name, resolver, src)
                if dest is None:
                    continue
                if src.is_dir():
                    shutil.copytree(extend(src), extend(dest))
                else:
                    shutil.copy2(extend(src), extend(dest))
                record.pairs.append((str(src), str(dest)))
        except _Cancelled:
            pass
        self._history.append(record)
        return record

    def delete(self, sources: list[str | Path]) -> OpRecord:
        record = OpRecord(kind="delete")
        for src in map(Path, sources):
            _send2trash(str(src))
            record.pairs.append((str(src), ""))
        self._history.append(record)
        return record

    def undo(self) -> OpRecord:
        """直近の move/copy を取り消す（delete はゴミ箱から手動復元）。"""
        for idx in range(len(self._history) - 1, -1, -1):
            record = self._history[idx]
            if record.kind == "move":
                for src, dest in reversed(record.pairs):
                    shutil.move(extend(dest), extend(src))
                del self._history[idx]
                return record
            if record.kind == "copy":
                for _, dest in reversed(record.pairs):
                    p = Path(dest)
                    if p.is_dir():
                        shutil.rmtree(extend(p))
                    elif p.exists():
                        p.unlink()
                del self._history[idx]
                return record
        raise RuntimeError("取り消せる操作がありません")
