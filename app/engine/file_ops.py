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


def _remove_permanent(path: str) -> None:
    """1 件をゴミ箱を経由せず完全削除する。

    ディレクトリは再帰削除。シンボリックリンクはリンク自体のみ消し、
    リンク先を巻き込まない（Windows のディレクトリリンクは rmdir で消す）。
    """
    ep = extend(path)
    if os.path.islink(ep):
        try:
            os.remove(ep)
        except OSError:
            os.rmdir(ep)
    elif os.path.isdir(ep):
        shutil.rmtree(ep)
    else:
        os.remove(ep)


# 衝突解決の戻り値: "overwrite" | "skip" | "rename" | "cancel"
Resolver = Callable[[Path, Path], str]

# 進捗コールバック: コピーしたバイト数の増分を受け取る。
OnBytes = Callable[[int], None]
# 件数ベースの進捗コールバック: (処理済み件数, 現在処理中の名前) を受け取る。
OnItem = Callable[[int, str], None]
# キャンセル判定: True を返したら中断する。
ShouldCancel = Callable[[], bool]


class _Cancelled(Exception):
    """衝突解決でユーザーが中止を選んだことを表す内部シグナル。"""


def total_size(sources: list[str | Path],
               should_cancel: ShouldCancel | None = None) -> int:
    """ファイル/ディレクトリの総バイト数（進捗バーの分母）。

    走査失敗（権限・到達不可）は 0 として無視し、例外を漏らさない。
    should_cancel が True を返したら走査を打ち切り、それまでの合計を返す。
    """
    total = 0
    for src in sources:
        if should_cancel is not None and should_cancel():
            return total
        p = Path(src)
        try:
            if p.is_dir():
                for root, _dirs, files in os.walk(extend(p)):
                    if should_cancel is not None and should_cancel():
                        return total
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

    @staticmethod
    def run_delete(sources: list[str | Path], *, permanent: bool = False,
                   on_item: OnItem | None = None,
                   should_cancel: ShouldCancel | None = None) -> OpRecord:
        """削除を実行する（ワーカースレッド可。履歴には積まない）。

        permanent=False はゴミ箱へ、True は完全削除。キャンセル時は
        それまでに削除できた分だけ pairs に残して返す。履歴への追加は
        GUI スレッド側で add_record を呼ぶ（run_plan と同じ分担）。
        """
        record = OpRecord(kind="delete")
        for i, src in enumerate(map(Path, sources)):
            if should_cancel is not None and should_cancel():
                break
            if on_item is not None:
                on_item(i, src.name)
            if permanent:
                _remove_permanent(str(src))
            else:
                _send2trash(str(src))
            record.pairs.append((str(src), ""))
        return record

    def delete(self, sources: list[str | Path]) -> OpRecord:
        """ゴミ箱へ移動する（同期版）。"""
        record = self.run_delete(sources)
        self._history.append(record)
        return record

    def delete_permanent(self, sources: list[str | Path]) -> int:
        """ゴミ箱を経由せず完全削除し、削除件数を返す（同期版）。

        元に戻せないため履歴には積まない。
        """
        return len(self.run_delete(sources, permanent=True).pairs)

    def peek_undo(self) -> OpRecord | None:
        """取り消し対象（直近の move/copy）を返す。履歴からは外さない。"""
        for record in reversed(self._history):
            if record.kind in ("move", "copy"):
                return record
        return None

    def discard_record(self, record: OpRecord) -> None:
        """取り消し完了後に履歴から取り除く（GUI スレッドから呼ぶ）。"""
        try:
            self._history.remove(record)
        except ValueError:
            pass

    @staticmethod
    def apply_undo(record: OpRecord, on_item: OnItem | None = None,
                   should_cancel: ShouldCancel | None = None) -> None:
        """record の逆操作を実行する（ワーカースレッド可。履歴は触らない）。

        should_cancel はアプリ終了時の合流用（通常操作では中断しない）。
        """
        if record.kind == "move":
            for i, (src, dest) in enumerate(reversed(record.pairs)):
                if should_cancel is not None and should_cancel():
                    return
                if on_item is not None:
                    on_item(i, Path(dest).name)
                shutil.move(extend(dest), extend(src))
        elif record.kind == "copy":
            for i, (_, dest) in enumerate(reversed(record.pairs)):
                if should_cancel is not None and should_cancel():
                    return
                p = Path(dest)
                if on_item is not None:
                    on_item(i, p.name)
                if p.is_dir():
                    shutil.rmtree(extend(p))
                elif p.exists():
                    p.unlink()

    def undo(self) -> OpRecord:
        """直近の move/copy を取り消す（同期版。delete はゴミ箱から手動復元）。"""
        record = self.peek_undo()
        if record is None:
            raise RuntimeError("取り消せる操作がありません")
        self.apply_undo(record)
        self.discard_record(record)
        return record
