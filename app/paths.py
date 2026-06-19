"""設定・データの保存場所。

通常の PyInstaller(onedir) 配布は exe と同じ場所の config/ に保存する。
ただし MSIX パッケージ実行時はインストール先が読み取り専用のため、
書き込み可能な %LOCALAPPDATA%\\Fibro\\config に保存する。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_msix_packaged() -> bool:
    """MSIX パッケージとして実行中か（パッケージ ID の有無で判定）。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        length = ctypes.c_uint32(0)
        # GetCurrentPackageFullName: APPMODEL_ERROR_NO_PACKAGE(15700)=非パッケージ
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(
            ctypes.byref(length), None)
        return rc != 15700
    except Exception:
        return False


def _default_config_dir() -> Path:
    if getattr(sys, "frozen", False):
        if _is_msix_packaged():
            base = os.environ.get("LOCALAPPDATA") \
                or str(Path.home() / "AppData" / "Local")
            return Path(base) / "Fibro" / "config"
        return Path(sys.executable).resolve().parent / "config"
    return Path(__file__).resolve().parents[1] / "config"


CONFIG_DIR = _default_config_dir()
try:  # 保存先が無ければ作成（読み取り専用などは無視）
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

INDEX_DB = CONFIG_DIR / "file_index.db"


def resource_path(rel: str) -> Path:
    """同梱リソースの絶対パス。PyInstaller(onedir) では _MEIPASS 配下。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    return Path(__file__).resolve().parents[1] / rel


APP_ICON = resource_path("assets/icons/fibro.ico")
