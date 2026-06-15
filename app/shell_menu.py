"""Windows シェルコンテキストメニュー統合（ctypes COM、新依存なし）。

選択ファイルのネイティブなシェルメニュー（7-Zip 圧縮・「送る」・各種シェル拡張）を
そのままポップアップ表示し、選択された項目を実行する。フルの pywin32 は使わず、
ctypes で COM vtable を直接叩く。失敗時は例外を握りつぶして False を返し、
呼び出し側は従来メニューにフォールバックする。
"""
from __future__ import annotations

import sys

_IS_WINDOWS = sys.platform == "win32"


def is_supported() -> bool:
    """Windows かつ COM 関連 DLL を読み込めるか。"""
    if not _IS_WINDOWS:
        return False
    try:
        import ctypes

        ctypes.OleDLL("ole32")
        ctypes.WinDLL("shell32")
        ctypes.WinDLL("user32")
        return True
    except Exception:  # noqa: BLE001 — 環境差異は全て非対応扱い
        return False


def show_shell_context_menu(hwnd: int, paths: list[str], x: int, y: int) -> bool:
    """paths の Windows シェルメニューを (x, y) にポップアップし選択項目を実行。

    成功（メニュー表示まで到達）で True、失敗で False。
    同一フォルダ内の複数選択に対応。混在時は先頭 1 件にフォールバック。
    """
    if not is_supported() or not paths:
        return False
    try:
        return _show(hwnd, paths, x, y)
    except Exception:  # noqa: BLE001 — どんな COM/ctypes 失敗もフォールバック
        return False


# --- 以下 ctypes COM 実装 -------------------------------------------------

def _show(hwnd: int, paths: list[str], x: int, y: int) -> bool:
    import ctypes
    from ctypes import POINTER, byref, c_void_p, c_wchar_p
    from ctypes.wintypes import HMENU, HWND, INT, UINT

    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")
    user32 = ctypes.WinDLL("user32")

    # COM / シェル定数
    S_OK = 0
    CMF_EXTENDEDVERBS = 0x00000100
    TPM_RETURNCMD = 0x0100
    TPM_RIGHTBUTTON = 0x0002
    SW_SHOWNORMAL = 1
    ID_FIRST = 1
    ID_LAST = 0x7FFF

    # vtable インデックス
    REL = 2                    # IUnknown::Release
    SF_GET_UI_OBJECT_OF = 10   # IShellFolder::GetUIObjectOf
    CM_QUERY = 3               # IContextMenu::QueryContextMenu
    CM_INVOKE = 4              # IContextMenu::InvokeCommand

    def vtbl_call(ptr, index, restype, argtypes):
        """COM ポインタの vtable[index] を呼べる関数を返す。"""
        vtbl = ctypes.cast(ptr, POINTER(c_void_p))
        func_ptr = ctypes.cast(vtbl[0], POINTER(c_void_p))[index]
        proto = ctypes.WINFUNCTYPE(restype, *argtypes)
        return proto(func_ptr)

    def release(ptr):
        if ptr:
            vtbl_call(ptr, REL, ctypes.c_ulong, (c_void_p,))(ptr)

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def guid(s: str) -> GUID:
        g = GUID()
        ole32.IIDFromString(c_wchar_p(s), byref(g))
        return g

    IID_IShellFolder = guid("{000214E6-0000-0000-C000-000000000046}")
    IID_IContextMenu = guid("{000214E4-0000-0000-C000-000000000046}")

    shell32.SHParseDisplayName.restype = ctypes.HRESULT
    shell32.SHParseDisplayName.argtypes = [
        c_wchar_p, c_void_p, POINTER(c_void_p), ctypes.c_ulong,
        POINTER(ctypes.c_ulong)]
    # SHBindToParent: 完全 PIDL → 親 IShellFolder + 子（相対）PIDL
    shell32.SHBindToParent.restype = ctypes.HRESULT
    shell32.SHBindToParent.argtypes = [
        c_void_p, POINTER(GUID), POINTER(c_void_p), POINTER(c_void_p)]
    ILFindLastID = shell32.ILFindLastID
    ILFindLastID.restype = c_void_p
    ILFindLastID.argtypes = [c_void_p]
    CoTaskMemFree = ole32.CoTaskMemFree
    CoTaskMemFree.argtypes = [c_void_p]

    ole32.CoInitialize(None)
    full_pidls: list[c_void_p] = []
    parent = c_void_p()
    ccm = c_void_p()
    hmenu = None
    try:
        # 各 path を完全 PIDL 化（SHParseDisplayName はバックスラッシュ必須。
        # QFileSystemModel はスラッシュ区切りを返すため normpath で正規化）
        import os
        for p in paths:
            win_path = os.path.normpath(p)
            pidl = c_void_p()
            attrs = ctypes.c_ulong(0)
            if shell32.SHParseDisplayName(
                    c_wchar_p(win_path), None, byref(pidl), 0,
                    byref(attrs)) == S_OK and pidl:
                full_pidls.append(pidl)
        if not full_pidls:
            return False

        # 先頭から親 IShellFolder を取得（同一フォルダ内の複数選択を前提）
        first_child = c_void_p()
        if shell32.SHBindToParent(
                full_pidls[0], byref(IID_IShellFolder), byref(parent),
                byref(first_child)) != S_OK or not parent:
            return False

        # 子（相対）PIDL 配列 = 各完全 PIDL の末尾 ID
        child_array = (c_void_p * len(full_pidls))()
        kept = 0
        for fp in full_pidls:
            last = ILFindLastID(fp)
            if last:
                child_array[kept] = c_void_p(last)
                kept += 1
        if kept == 0:
            return False

        # GetUIObjectOf → IContextMenu
        get_ui = vtbl_call(
            parent, SF_GET_UI_OBJECT_OF, ctypes.HRESULT,
            (c_void_p, HWND, UINT, POINTER(c_void_p), POINTER(GUID),
             POINTER(ctypes.c_ulong), POINTER(c_void_p)))
        if get_ui(parent, HWND(hwnd), UINT(kept), child_array,
                  byref(IID_IContextMenu), None, byref(ccm)) != S_OK or not ccm:
            return False

        # QueryContextMenu（成功 HRESULT は >= 0。負値のみ失敗）
        hmenu = user32.CreatePopupMenu()
        query = vtbl_call(
            ccm, CM_QUERY, ctypes.HRESULT,
            (c_void_p, HMENU, UINT, UINT, UINT, UINT))
        if query(ccm, HMENU(hmenu), UINT(0), UINT(ID_FIRST), UINT(ID_LAST),
                 UINT(CMF_EXTENDEDVERBS)) < 0:
            return False
        if user32.GetMenuItemCount(HMENU(hmenu)) <= 0:
            return False

        # メニューが正しく閉じるよう前面化してからポップアップ
        user32.SetForegroundWindow(HWND(hwnd))
        user32.TrackPopupMenuEx.restype = INT
        user32.TrackPopupMenuEx.argtypes = [
            HMENU, UINT, INT, INT, HWND, c_void_p]
        cmd = user32.TrackPopupMenuEx(
            HMENU(hmenu), UINT(TPM_RETURNCMD | TPM_RIGHTBUTTON),
            INT(x), INT(y), HWND(hwnd), None)

        if cmd > 0:
            class CMINVOKECOMMANDINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint32),
                    ("fMask", ctypes.c_uint32),
                    ("hwnd", c_void_p),
                    ("lpVerb", ctypes.c_char_p),
                    ("lpParameters", ctypes.c_char_p),
                    ("lpDirectory", ctypes.c_char_p),
                    ("nShow", ctypes.c_int),
                    ("dwHotKey", ctypes.c_uint32),
                    ("hIcon", c_void_p)]

            info = CMINVOKECOMMANDINFO()
            info.cbSize = ctypes.sizeof(CMINVOKECOMMANDINFO)
            info.fMask = 0
            info.hwnd = hwnd
            # verb = MAKEINTRESOURCEA(cmd - ID_FIRST)
            info.lpVerb = ctypes.cast(
                ctypes.c_void_p(cmd - ID_FIRST), ctypes.c_char_p)
            info.nShow = SW_SHOWNORMAL
            invoke = vtbl_call(
                ccm, CM_INVOKE, ctypes.HRESULT,
                (c_void_p, POINTER(CMINVOKECOMMANDINFO)))
            invoke(ccm, byref(info))
        return True
    finally:
        if hmenu:
            user32.DestroyMenu(HMENU(hmenu))
        release(ccm)
        release(parent)
        for fp in full_pidls:
            CoTaskMemFree(fp)
        ole32.CoUninitialize()
