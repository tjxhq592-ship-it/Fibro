"""プロジェクト範囲の設定（テーマ・タブ・クイックアクセス表示名）。

ThemeManager と同じ get/set API を持つ軽量 JSON ストア。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.atomicio import atomic_write_text


class ProjectSettingsStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        try:
            atomic_write_text(
                self._path, json.dumps(self._data, ensure_ascii=False, indent=2))
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._save()
