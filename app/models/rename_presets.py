"""一括リネームのルールプリセット保存。

PowerRename 型の設定（検索・置換・regex・対象・大小・連番）を名前付きで
保存して再利用する。JSON 保存、破損時は空にフォールバック。
ルールは GUI 非依存のプリミティブ dict として持つ（enum は .value 文字列）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.atomicio import atomic_write_text

# 1つのプリセットが保持するルールのキー（順序固定）
RULE_KEYS = (
    "search", "replace", "use_regex", "target", "case_mode",
    "counter_start", "counter_step", "counter_digits",
)


@dataclass
class RenamePreset:
    name: str
    rule: dict = field(default_factory=dict)


class RenamePresetStore:
    def __init__(self, config_path: str | Path) -> None:
        self._path = Path(config_path)
        self.presets: list[RenamePreset] = []
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self.presets = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.presets = [
                RenamePreset(name=item["name"], rule=dict(item.get("rule", {})))
                for item in data.get("presets", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            self.presets = []

    def save(self) -> None:
        try:
            data = {"presets": [{"name": p.name, "rule": p.rule}
                                for p in self.presets]}
            atomic_write_text(
                self._path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError:
            pass

    def names(self) -> list[str]:
        return [p.name for p in self.presets]

    def get(self, name: str) -> RenamePreset | None:
        return next((p for p in self.presets if p.name == name), None)

    def add(self, name: str, rule: dict) -> RenamePreset:
        """同名があれば上書き、なければ追加。"""
        clean = {k: rule[k] for k in RULE_KEYS if k in rule}
        existing = self.get(name)
        if existing:
            existing.rule = clean
            preset = existing
        else:
            preset = RenamePreset(name=name, rule=clean)
            self.presets.append(preset)
        self.save()
        return preset

    def remove(self, name: str) -> bool:
        before = len(self.presets)
        self.presets = [p for p in self.presets if p.name != name]
        if len(self.presets) != before:
            self.save()
            return True
        return False
