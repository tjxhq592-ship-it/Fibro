"""Win+E オーバーライド＋フォルダ既定動作の差し替え（HKCU レジストリ方式）。

AutoHotkey 不要・管理者権限不要・トグルで元に戻せる。チェック ON で:
  - Win+E が Fibro を開く（CLSID opennewwindow を差し替え）
  - フォルダのダブルクリックで Fibro が開く（Directory の open 動詞を差し替え）
いずれも HKCU\\Software\\Classes 配下のみ。OFF で削除して標準に戻す。
"""
from __future__ import annotations

import sys

# Win+E が使う CLSID（"新しいウィンドウ"）。opennewwindow\command を差し替える。
_CLSID = "{52205fd8-5dfb-447d-801a-d0b52f2e83e1}"
_WINE_KEY = rf"Software\Classes\CLSID\{_CLSID}\shell\opennewwindow\command"
# フォルダ（実ファイルシステムのディレクトリ）の「開く」動詞。
_DIR_KEY = r"Software\Classes\Directory\shell\open\command"
# Directory の既定動詞。標準では 'none' で、ダブルクリックは親 Folder の
# open（Explorer）にフォールバックする。これを 'open' にして Directory 自身の
# open（＝Fibro）を既定にする。
_DIR_SHELL_KEY = r"Software\Classes\Directory\shell"


def is_supported() -> bool:
    """Windows でのみ有効。"""
    return sys.platform == "win32"


def fibro_exe_path() -> str | None:
    """オーバーライド先の Fibro 実行ファイル。frozen(exe) 時のみ確定。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def is_enabled() -> bool:
    """現在オーバーライドが有効か（代表として Win+E キーで判定）。"""
    if not is_supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINE_KEY) as k:
            value, _ = winreg.QueryValueEx(k, None)
            return bool(value)
    except OSError:
        return False


def enable(exe_path: str) -> bool:
    """Win+E とフォルダのダブルクリックを Fibro に割り当てる。成功で True。"""
    if not is_supported():
        return False
    # Win+E は引数なし、フォルダ open は対象フォルダ "%1" を渡す
    ok_wine = _write_command(_WINE_KEY, f'"{exe_path}"')
    ok_dir = _write_command(_DIR_KEY, f'"{exe_path}" "%1"')
    # Directory の既定動詞を open にして Fibro の open を使わせる
    ok_verb = _set_default_verb("open")
    return ok_wine and ok_dir and ok_verb


def disable() -> bool:
    """差し替えを解除して標準（エクスプローラー）に戻す。成功で True。"""
    if not is_supported():
        return False
    ok_wine = _delete_command(_WINE_KEY)
    ok_dir = _delete_command(_DIR_KEY)
    ok_verb = _clear_default_verb()  # 既定動詞を標準（none 継承）へ戻す
    return ok_wine and ok_dir and ok_verb


# --- winreg ヘルパー -------------------------------------------------------

def _write_command(key: str, command: str) -> bool:
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, command)
            # 既定の委譲を無効化（これが無いと差し替えが効かない）
            winreg.SetValueEx(k, "DelegateExecute", 0, winreg.REG_SZ, "")
        return True
    except OSError:
        return False


def _delete_command(key: str) -> bool:
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        return True
    except FileNotFoundError:
        return True  # 既に無効
    except OSError:
        return False


def _set_default_verb(verb: str) -> bool:
    """Directory\\shell の既定動詞（既定値）を設定。"""
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _DIR_SHELL_KEY) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, verb)
        return True
    except OSError:
        return False


def _clear_default_verb() -> bool:
    """設定した既定動詞を削除し、標準（HKLM 継承の none）へ戻す。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _DIR_SHELL_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, None)
        return True
    except FileNotFoundError:
        return True  # もともと無い
    except OSError:
        return False
