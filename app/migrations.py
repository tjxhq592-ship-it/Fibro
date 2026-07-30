"""起動時マイグレーション。バージョン間の設定ファイル構造差分を吸収する。"""
from __future__ import annotations

import json
from pathlib import Path

from app.atomicio import atomic_write_text

_PROJECT_SCOPED_KEYS = ("theme", "place_names", "tabs")

# 廃止したテーマ名 -> 後継テーマ。10テーマを6テーマへ整理した際の対応表。
# theme.py は未知のテーマ名を無条件に light へ落とすため、これを通さないと
# ダークテーマ利用者が更新後にいきなり真っ白な画面になる。色相と明度が
# 最も近いものへ寄せる。
_RETIRED_THEMES = {
    "nord": "navy",
    "solarized_dark": "navy",
    "one_dark": "dark",
    "dracula": "dark",
    "gruvbox_dark": "coffee",
    "monokai": "coffee",
    "solarized_light": "sepia",
}


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


def _project_settings_paths(config_dir: Path) -> list[Path]:
    """テーマ設定が入り得るプロジェクト設定ファイルを全て列挙する。

    テーマはプロジェクト単位の設定なので、デフォルトプロジェクトの
    default_project_settings.json と projects/<id>/settings.json の
    両方を見る必要がある。
    """
    paths = [config_dir / "default_project_settings.json"]
    projects_dir = config_dir / "projects"
    try:
        entries = sorted(projects_dir.iterdir()) if projects_dir.is_dir() else []
    except OSError:
        entries = []
    paths.extend(d / "settings.json" for d in entries if d.is_dir())
    return paths


def migrate_retired_theme_names(config_dir: Path) -> None:
    """廃止したテーマ名を後継テーマへ読み替える（冪等）。

    未知のテーマ名は theme.py 側で light にフォールバックされてしまうため、
    設定を読む前にここで書き換える。対応表にない未知の値はそのまま残す
    （利用者が手で書いた値を勝手に潰さないため）。読めない/壊れている
    ファイルは黙って飛ばす。
    """
    for path in _project_settings_paths(config_dir):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        current = data.get("theme")
        if not isinstance(current, str):
            continue
        new_theme = _RETIRED_THEMES.get(current)
        if new_theme is None:
            continue
        data["theme"] = new_theme
        try:
            atomic_write_text(
                path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError:
            pass
