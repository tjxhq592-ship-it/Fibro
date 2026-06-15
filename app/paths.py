"""設定・データの保存場所。PyInstaller 実行時は exe と同じ場所。"""
from __future__ import annotations

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    CONFIG_DIR = Path(sys.executable).resolve().parent / "config"
else:
    CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

INDEX_DB = CONFIG_DIR / "file_index.db"


def resource_path(rel: str) -> Path:
    """同梱リソースの絶対パス。PyInstaller(onedir) では _MEIPASS 配下。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    return Path(__file__).resolve().parents[1] / rel


APP_ICON = resource_path("assets/icons/fibro.ico")
