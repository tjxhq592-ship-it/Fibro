"""クラウド/ネットワーク場所の検出（Windows 専用）。

3つのソースから、エクスプローラーと同等の「場所」リストを取得する:
  ①: SyncRootManager レジストリ（iCloud/OneDrive/Dropbox 等）
  ②: Network Shortcuts フォルダ（ネットワーク場所）
  ③: ドライブ文字 走査 A〜Z（ローカルドライブ/USB 含む）

全関数は失敗時に例外を外へ漏らさず空リストを返す（フォールバック安全）。
"""
from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass

_IS_WINDOWS = sys.platform == "win32"


@dataclass
class Place:
    """サイドバーの「場所」1件。"""
    name: str        # 表示名
    path: str        # 実ローカルパス（UNC \\server\share を含む）
    kind: str        # "cloud" | "network" | "drive"
    icon: str = ""   # 表示アイコン（emoji fallback）

    @property
    def icon_str(self) -> str:
        if self.icon:
            return self.icon
        return {"cloud": "☁", "network": "🌐", "drive": "💾"}.get(
            self.kind, "📁")


def get_all_places() -> list[Place]:
    """①②③の全ソースをまとめて返す（重複パスは除去）。起動時に呼ぶ。"""
    if not _IS_WINDOWS:
        return []
    seen: set[str] = set()
    results: list[Place] = []

    def add(p: Place) -> None:
        norm = os.path.normcase(p.path)
        if norm not in seen:
            seen.add(norm)
            results.append(p)

    for p in _sync_root_places():
        add(p)
    for p in _network_shortcut_places():
        add(p)
    for p in _drive_places():
        add(p)
    return results


# ── ① SyncRootManager（iCloud / OneDrive / Dropbox 等） ──────────────────────

def _sync_root_places() -> list[Place]:
    """Cloud Files API で登録されたクラウドドライブを列挙する。

    レジストリ:
      HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\SyncRootManager
        {Provider}!{SID}!{Sub}\\UserSyncRoots\\{SID} = "C:\\Users\\...\\CloudFolder"

    winreg は Python 標準ライブラリ（追加依存なし）。
    """
    if not _IS_WINDOWS:
        return []
    try:
        import winreg
    except ImportError:
        return []

    results: list[Place] = []
    key_path = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\Explorer\SyncRootManager")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as root:
            i = 0
            while True:
                try:
                    provider_key = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break

                # 表示名: "iCloudDrive!S-1-5-..." → "iCloudDrive"
                display_name = provider_key.split("!")[0]

                # UserSyncRoots の下にある全エントリがローカルパス
                sub_path = f"{key_path}\\{provider_key}\\UserSyncRoots"
                try:
                    with winreg.OpenKey(
                            winreg.HKEY_LOCAL_MACHINE, sub_path) as sub:
                        j = 0
                        while True:
                            try:
                                _, local_path, _reg_type = winreg.EnumValue(
                                    sub, j)
                                j += 1
                                if (isinstance(local_path, str)
                                        and local_path
                                        and os.path.isdir(local_path)):
                                    results.append(Place(
                                        name=display_name,
                                        path=local_path,
                                        kind="cloud",
                                    ))
                            except OSError:
                                break
                except OSError:
                    pass
    except OSError:
        pass
    return results


# ── ② Network Shortcuts（ネットワーク場所） ─────────────────────────────────

def _network_shortcut_places() -> list[Place]:
    """%APPDATA%\\Microsoft\\Windows\\Network Shortcuts 内のショートカットを列挙。

    各エントリは:
      a) サブフォルダ形式（GUID フォルダ内に target.lnk）
      b) .lnk ファイル形式

    .lnk の解析は IShellLink COM（ctypes）で行う。
    """
    if not _IS_WINDOWS:
        return []
    net_dir = os.path.expandvars(
        r"%APPDATA%\Microsoft\Windows\Network Shortcuts")
    if not os.path.isdir(net_dir):
        return []

    results: list[Place] = []
    try:
        for entry in os.scandir(net_dir):
            try:
                if entry.is_dir(follow_symlinks=False):
                    # パターン a: フォルダ内の target.lnk
                    lnk = os.path.join(entry.path, "target.lnk")
                    if os.path.isfile(lnk):
                        path = _resolve_lnk(lnk)
                        if path:
                            results.append(Place(
                                name=entry.name, path=path, kind="network"))
                elif entry.name.lower().endswith(".lnk"):
                    # パターン b: .lnk ファイル直置き。
                    path = _resolve_lnk(entry.path)
                    if path:
                        results.append(Place(
                            name=entry.name[:-4], path=path, kind="network"))
            except OSError:
                continue
    except OSError:
        pass
    return results


def _resolve_lnk(lnk_path: str) -> str | None:
    """IShellLink::GetPath() で .lnk の実ターゲットパスを解決する。

    失敗時は None を返す（例外を漏らさない）。
    """
    try:
        import ctypes
        from ctypes import POINTER, byref, c_void_p, c_wchar_p
        from ctypes.wintypes import MAX_PATH

        ole32 = ctypes.OleDLL("ole32")

        # CLSID / IID
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_uint32),
                        ("Data2", ctypes.c_uint16),
                        ("Data3", ctypes.c_uint16),
                        ("Data4", ctypes.c_ubyte * 8)]

        def guid(s: str) -> GUID:
            g = GUID()
            ole32.IIDFromString(c_wchar_p(s), byref(g))
            return g

        CLSID_ShellLink = guid("{00021401-0000-0000-C000-000000000046}")
        IID_IShellLinkW = guid("{000214F9-0000-0000-C000-000000000046}")
        IID_IPersistFile = guid("{0000010B-0000-0000-C000-000000000046}")

        REL = 2   # IUnknown::Release
        QI = 0    # IUnknown::QueryInterface
        LOAD = 5  # IPersistFile::Load
        GET_PATH = 3  # IShellLinkW::GetPath
        CLSCTX_INPROC_SERVER = 1

        def vtbl(ptr, idx, res, args):
            vt = ctypes.cast(ptr, POINTER(c_void_p))
            fp = ctypes.cast(vt[0], POINTER(c_void_p))[idx]
            return ctypes.WINFUNCTYPE(res, *args)(fp)

        def release(ptr):
            if ptr:
                vtbl(ptr, REL, ctypes.c_ulong, (c_void_p,))(ptr)

        ole32.CoInitialize(None)
        psl = c_void_p()
        ppf = c_void_p()
        try:
            hr = ole32.CoCreateInstance(
                byref(CLSID_ShellLink), None, CLSCTX_INPROC_SERVER,
                byref(IID_IShellLinkW), byref(psl))
            if hr != 0 or not psl:
                return None

            # IPersistFile を QueryInterface で取得
            qi = vtbl(psl, QI, ctypes.HRESULT,
                      (c_void_p, POINTER(GUID), POINTER(c_void_p)))
            if qi(psl, byref(IID_IPersistFile), byref(ppf)) != 0 or not ppf:
                return None

            # .lnk を Load
            load = vtbl(ppf, LOAD, ctypes.HRESULT,
                        (c_void_p, c_wchar_p, ctypes.c_ulong))
            if load(ppf, c_wchar_p(lnk_path), 0) != 0:
                return None

            # GetPath で実パスを取得
            buf = ctypes.create_unicode_buffer(MAX_PATH)
            gp = vtbl(psl, GET_PATH, ctypes.HRESULT,
                      (c_void_p, ctypes.c_wchar_p, ctypes.c_int,
                       c_void_p, ctypes.c_ulong))
            gp(psl, buf, MAX_PATH, None, 0)
            path = buf.value
            return path if path else None
        finally:
            release(ppf)
            release(psl)
            ole32.CoUninitialize()
    except Exception:   # noqa: BLE001 — COM 失敗は全て None フォールバック
        return None


# ── ③ ドライブ文字 A〜Z 総当たり ────────────────────────────────────────────

def _drive_places() -> list[Place]:
    """A:〜Z: のうち実在するドライブを返す。C:/D: 等のローカル/USB/CD-ROM。

    os.path.exists のみ使用（依存なし）。
    """
    if not _IS_WINDOWS:
        return []
    results: list[Place] = []
    for letter in string.ascii_uppercase:
        path = f"{letter}:\\"
        try:
            if os.path.exists(path):
                results.append(Place(
                    name=f"{letter}:",
                    path=path,
                    kind="drive",
                ))
        except OSError:
            continue
    return results
