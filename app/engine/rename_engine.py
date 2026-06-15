"""一括リネームの純粋ロジック（GUI非依存）。

PowerRename型: 1本のルール（検索→置換、regexトグル、対象、連番、大小変換）を
選択ファイル全体に適用し、プレビュー（現在名→新名→状態）を生成する。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath


class Target(Enum):
    NAME = "name"       # 拡張子を除く名前部分のみ
    EXT = "ext"         # 拡張子のみ（先頭ドットを除く）
    BOTH = "both"       # ファイル名全体


class CaseMode(Enum):
    KEEP = "keep"
    UPPER = "upper"
    LOWER = "lower"
    TITLE = "title"


class Status(Enum):
    OK = "ok"                 # リネーム可能
    UNCHANGED = "unchanged"   # 変更なし
    CONFLICT = "conflict"     # 新名が衝突（自動連番で回避済みなら RESOLVED）
    RESOLVED = "resolved"     # 衝突を連番付与で回避
    INVALID = "invalid"       # 無効な新名（空・禁止文字）


# Windows のファイル名禁止文字
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

COUNTER_TOKEN = re.compile(r"\$\{n\}")


@dataclass
class RenameRule:
    search: str = ""
    replace: str = ""
    use_regex: bool = False
    target: Target = Target.NAME
    case_mode: CaseMode = CaseMode.KEEP
    counter_start: int = 1
    counter_step: int = 1
    counter_digits: int = 3


@dataclass
class RenamePlanItem:
    old_name: str
    new_name: str
    status: Status
    message: str = ""


@dataclass
class RenamePlan:
    items: list[RenamePlanItem] = field(default_factory=list)

    @property
    def changed_items(self) -> list[RenamePlanItem]:
        return [i for i in self.items
                if i.status in (Status.OK, Status.RESOLVED)]

    @property
    def has_errors(self) -> bool:
        return any(i.status in (Status.CONFLICT, Status.INVALID)
                   for i in self.items)


def split_name(filename: str) -> tuple[str, str]:
    """名前と拡張子（ドット込み）に分割。ドットファイルは拡張子なし扱い。"""
    p = PurePath(filename)
    if p.name.startswith(".") and p.suffix == "":
        return p.name, ""
    return p.stem, p.suffix


def is_valid_filename(name: str) -> bool:
    if not name or name in (".", ".."):
        return False
    if _INVALID_CHARS.search(name):
        return False
    if name.rstrip() != name or name.rstrip(".") == "":
        return False
    if split_name(name)[0].upper() in _RESERVED_NAMES:
        return False
    return True


class RenameEngine:
    """ルールをファイル名リストに適用してプランを生成する。"""

    def validate_rule(self, rule: RenameRule) -> str | None:
        """ルール自体の妥当性チェック。問題があればメッセージを返す。"""
        if rule.use_regex and rule.search:
            try:
                re.compile(rule.search)
            except re.error as e:
                return f"正規表現エラー: {e}"
        return None

    def _apply_search_replace(self, text: str, rule: RenameRule,
                              counter: int) -> str:
        replacement = COUNTER_TOKEN.sub(
            format(counter, f"0{rule.counter_digits}d"), rule.replace)
        if not rule.search:
            # 検索が空: 置換文字列が指定されていれば全体を置き換え
            return replacement if rule.replace else text
        if rule.use_regex:
            return re.sub(rule.search, replacement, text)
        return text.replace(rule.search, replacement)

    def _apply_case(self, text: str, mode: CaseMode) -> str:
        if mode is CaseMode.UPPER:
            return text.upper()
        if mode is CaseMode.LOWER:
            return text.lower()
        if mode is CaseMode.TITLE:
            return text.title()
        return text

    def _transform_one(self, filename: str, rule: RenameRule,
                       counter: int) -> str:
        stem, suffix = split_name(filename)
        ext = suffix[1:] if suffix.startswith(".") else suffix
        if rule.target is Target.BOTH:
            new = self._apply_search_replace(filename, rule, counter)
            new = self._apply_case(new, rule.case_mode)
            return new
        if rule.target is Target.NAME:
            new_stem = self._apply_search_replace(stem, rule, counter)
            new_stem = self._apply_case(new_stem, rule.case_mode)
            return new_stem + suffix
        # Target.EXT
        new_ext = self._apply_search_replace(ext, rule, counter)
        new_ext = self._apply_case(new_ext, rule.case_mode)
        return stem + ("." + new_ext if new_ext else "")

    def build_plan(self, filenames: list[str], rule: RenameRule,
                   existing_names: set[str] | None = None,
                   auto_resolve: bool = True) -> RenamePlan:
        """プレビュー用プランを生成。

        existing_names: 同フォルダ内の（選択外も含む）既存ファイル名。
        auto_resolve: 衝突時に ` (2)` 連番を付与して回避する。
        """
        existing = set(existing_names or ())
        selected = set(filenames)
        # 選択外の既存ファイルとは衝突してはならない
        taken = {n.lower() for n in existing - selected}
        plan = RenamePlan()
        counter = rule.counter_start
        used_new: set[str] = set()

        for old in filenames:
            new = self._transform_one(old, rule, counter)
            if COUNTER_TOKEN.search(rule.replace):
                counter += rule.counter_step

            if new == old:
                plan.items.append(RenamePlanItem(old, new, Status.UNCHANGED))
                # 変更なしファイルの名前は引き続き占有される
                used_new.add(new.lower())
                continue

            if not is_valid_filename(new):
                plan.items.append(RenamePlanItem(
                    old, new, Status.INVALID, "無効なファイル名"))
                continue

            status = Status.OK
            msg = ""
            if new.lower() in taken or new.lower() in used_new:
                if auto_resolve:
                    new = self._resolve_conflict(new, taken | used_new)
                    status = Status.RESOLVED
                    msg = "衝突のため連番を付与"
                else:
                    plan.items.append(RenamePlanItem(
                        old, new, Status.CONFLICT, "名前が衝突"))
                    continue
            used_new.add(new.lower())
            plan.items.append(RenamePlanItem(old, new, status, msg))
        return plan

    @staticmethod
    def _resolve_conflict(name: str, taken: set[str]) -> str:
        stem, suffix = split_name(name)
        i = 2
        while True:
            candidate = f"{stem} ({i}){suffix}"
            if candidate.lower() not in taken:
                return candidate
            i += 1
