"""Windows の「新規作成」一覧（ShellNew）をレジストリから再現する。

エクスプローラーが空白右クリック →「新規作成」に並べるファイル型は、
``HKEY_CLASSES_ROOT\\.<拡張子>\\ShellNew`` に登録されている。これを winreg
（標準ライブラリ）で列挙し、追加依存なしで同じ一覧を提供する。

対応する作成方式: NullFile（空ファイル）/ Data（バイナリ）/ FileName（雛形コピー）。
Command 型（.lnk ショートカット等）は対象外。失敗は握りつぶし、呼び出し側が
従来挙動へフォールバックできるよう空リスト/例外で表現する。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"

# list_new_items() の結果をセッション中キャッシュ（HKCR 全走査は重いため）。
_cache: "list[dict] | None" = None


def list_new_items() -> list[dict]:
    """ShellNew 登録のファイル型を列挙して返す（キャッシュ付き）。

    各要素: {"ext": ".txt", "label": "テキスト ドキュメント",
             "method": "null"|"data"|"file",
             "data": bytes|None, "template": str|None}
    Windows 以外や失敗時は空リスト。
    """
    global _cache
    if _cache is not None:
        return _cache
    if not _IS_WINDOWS:
        _cache = []
        return _cache
    try:
        _cache = _build_items()
    except Exception:  # noqa: BLE001 — 環境差異・権限差異は全て空扱い
        _cache = []
    return _cache


def create_item(item: dict, dest_dir: str, base_name: str) -> Path:
    """item の方式に従って dest_dir 直下に base_name+拡張子 のファイルを作成。

    衝突回避は呼び出し側で済ませた base_name を前提とする。失敗時は OSError 送出。
    """
    target = Path(dest_dir) / f"{base_name}{item['ext']}"
    method = item.get("method")
    if method == "data" and item.get("data") is not None:
        target.write_bytes(item["data"])
    elif method == "file" and item.get("template"):
        src = _resolve_template(item["template"])
        if src and src.is_file():
            shutil.copyfile(src, target)
        else:
            # 雛形が見つからなければ空ファイルにフォールバック。
            target.write_bytes(b"")
    else:  # "null" もしくは不明 → 空ファイル
        target.write_bytes(b"")
    return target


# --- 内部実装 -------------------------------------------------------------

def _build_items() -> list[dict]:
    import winreg

    hkcr = winreg.HKEY_CLASSES_ROOT
    by_ext: dict[str, dict] = {}

    with winreg.OpenKey(hkcr, "") as root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not name.startswith(".") or len(name) < 2:
                continue
            ext = name.lower()
            if ext in by_ext:
                continue
            sn = _find_shellnew(winreg, hkcr, name)
            if sn is None:
                continue
            method, data, template = sn
            item = {
                "ext": ext,
                "label": _friendly_name(winreg, hkcr, name, ext),
                "method": method,
                "data": data,
                "template": template,
            }
            by_ext[ext] = item

    return sorted(by_ext.values(), key=lambda d: d["label"].lower())


def _find_shellnew(winreg, hkcr, ext_key: str):
    """`.<ext>\\ShellNew` を探し (method, data, template) を返す。無ければ None。

    直下に無い場合は1段だけ子キー（ProgID 別）を走査して ShellNew を探す。
    """
    # まず直下の ShellNew
    result = _read_shellnew(winreg, hkcr, f"{ext_key}\\ShellNew")
    if result is not None:
        return result
    # 子キー（ProgID 別）を1段走査
    try:
        with winreg.OpenKey(hkcr, ext_key) as k:
            j = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, j)
                except OSError:
                    break
                j += 1
                if sub.lower() == "shellnew":
                    continue  # 直下は上で確認済み
                result = _read_shellnew(
                    winreg, hkcr, f"{ext_key}\\{sub}\\ShellNew")
                if result is not None:
                    return result
    except OSError:
        pass
    return None


def _read_shellnew(winreg, hkcr, path: str):
    """ShellNew キーの値を読み (method, data, template) を返す。対象外は None。"""
    try:
        key = winreg.OpenKey(hkcr, path)
    except OSError:
        return None
    try:
        values: dict[str, object] = {}
        i = 0
        while True:
            try:
                vname, vdata, _vtype = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            values[vname.lower()] = vdata
    finally:
        winreg.CloseKey(key)

    # Handler（CLSID）/ Command 駆動は自前で作れない。NullFile 等が併記されていても
    # 空ファイルを作ると壊れた .lnk/.library-ms になるため対象外とする。
    if "handler" in values or "command" in values:
        return None
    if "data" in values:
        data = values["data"]
        if isinstance(data, (bytes, bytearray)):
            return ("data", bytes(data), None)
    if "filename" in values:
        fn = values["filename"]
        if isinstance(fn, str) and fn.strip():
            return ("file", None, fn.strip())
    if "nullfile" in values:
        return ("null", None, None)
    # ItemName のみ等は対象外
    return None


def _friendly_name(winreg, hkcr, ext_key: str, ext: str) -> str:
    """型の表示名を解決。取れなければ "<EXT> ファイル"。"""
    progid = _read_default(winreg, hkcr, ext_key)
    if progid:
        # FriendlyTypeName（@dll,-id 形式は SHLoadIndirectString で解決）
        ftn = _read_value(winreg, hkcr, progid, "FriendlyTypeName")
        if isinstance(ftn, str) and ftn:
            resolved = _load_indirect(ftn) if ftn.startswith("@") else ftn
            if resolved:
                return resolved
        # ProgID 既定値
        desc = _read_default(winreg, hkcr, progid)
        if desc:
            return desc
    return f"{ext[1:].upper()} ファイル"


def _read_default(winreg, hkcr, path: str):
    try:
        with winreg.OpenKey(hkcr, path) as k:
            val, _ = winreg.QueryValueEx(k, "")
            return val if isinstance(val, str) and val else None
    except OSError:
        return None


def _read_value(winreg, hkcr, path: str, name: str):
    try:
        with winreg.OpenKey(hkcr, path) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val
    except OSError:
        return None


def _load_indirect(ref: str) -> str | None:
    """`@C:\\path\\x.dll,-123` 形式のリソース文字列を実テキストに解決。"""
    try:
        import ctypes
        from ctypes import create_unicode_buffer, c_wchar_p

        buf = create_unicode_buffer(1024)
        res = ctypes.windll.shlwapi.SHLoadIndirectString(
            c_wchar_p(ref), buf, 1024, None)
        if res == 0 and buf.value:
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_template(filename: str) -> "Path | None":
    """FileName 値から雛形ファイルの実パスを解決。"""
    p = Path(filename)
    if p.is_absolute():
        return p
    # CSIDL_TEMPLATES 相当: %APPDATA%\Microsoft\Windows\Templates
    appdata = os.environ.get("APPDATA")
    if appdata:
        cand = Path(appdata) / "Microsoft" / "Windows" / "Templates" / filename
        if cand.exists():
            return cand
    return p if p.exists() else None
