"""起動時マイグレーション。バージョン間の設定ファイル構造差分を吸収する。"""
from __future__ import annotations

import json
from pathlib import Path

from app.atomicio import atomic_write_text

_PROJECT_SCOPED_KEYS = ("theme", "place_names", "tabs")


def migrate_default_project_settings(config_dir: Path) -> None:
    """settings.json からプロジェクト範囲キーを default_project_settings.json へ移動する。

    既に default_project_settings.json が存在する場合は何もしない（冪等）。
    settings.json が存在しない/壊れている場合も何もしない（フォールバックは
    各ストア側の既存の例外ハンドリングに任せる）。
    """
    settings_path = config_dir / "settings.json"
    default_project_path = config_dir / "default_project_settings.json"
    if default_project_path.exists():
        return
    if not settings_path.exists():
        return
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    moved = {k: data[k] for k in _PROJECT_SCOPED_KEYS if k in data}
    if not moved:
        return
    try:
        atomic_write_text(
            default_project_path,
            json.dumps(moved, ensure_ascii=False, indent=2))
        remaining = {k: v for k, v in data.items()
                     if k not in _PROJECT_SCOPED_KEYS}
        atomic_write_text(
            settings_path,
            json.dumps(remaining, ensure_ascii=False, indent=2))
    except OSError:
        pass
