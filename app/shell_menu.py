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


def show_drag_drop_menu(hwnd: int, paths: list[str], dest_dir: str,
                        x: int, y: int) -> bool:
    """paths を dest_dir へ「右ドラッグ」したときの Windows ネイティブメニューを表示。

    「ここにコピー/移動/ショートカット作成」＋7-Zip 等の「ここに解凍」を含む。
    シェルの IDataObject + IDropTarget を使い、右ボタンドロップ（MK_RBUTTON）として
    Drop を実行する。メニュー表示まで到達で True、失敗で False。
    """
    if not is_supported() or not paths or not dest_dir:
        return False
    try:
        return _drag_drop(hwnd, paths, dest_dir, x, y)
    except Exception:  # noqa: BLE001 — どんな COM/ctypes 失敗もフォールバック
        return False


def show_combined_menu(hwnd: int, paths: list[str], x: int, y: int,
                       fibro_items: list) -> tuple[bool, str | None]:
    """Fibro 固有項目（上部）＋ シェル拡張（下部）の統合メニューを表示。

    fibro_items は上部に差し込むノードのリスト:
      {"type": "sep"}
      {"type": "action", "key": str, "label": str, "enabled": bool=True}
      {"type": "submenu", "label": str,
       "items": [{"key": str, "label": str}, ...]}
    戻り値 (ok, key):
      ok=False → メニュー表示に失敗（呼び出し側で Fibro 自前メニューへフォールバック）
      ok=True, key=str → Fibro 項目が選ばれた（呼び出し側がアクション実行）
      ok=True, key=None → シェル項目を実行 or キャンセル
    """
    if not is_supported() or not paths:
        return False, None
    try:
        return _combined(hwnd, paths, x, y, fibro_items)
    except Exception:  # noqa: BLE001 — どんな COM/ctypes 失敗もフォールバック
        return False, None


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


def _drag_drop(hwnd: int, paths: list[str], dest_dir: str,
               x: int, y: int) -> bool:
    import ctypes
    import os
    from ctypes import POINTER, byref, c_void_p, c_wchar_p
    from ctypes.wintypes import HWND, UINT

    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")

    S_OK = 0
    MK_RBUTTON = 0x0002
    DROPEFFECT_ALL = 0x1 | 0x2 | 0x4  # COPY | MOVE | LINK
    REL = 2
    SF_GET_UI_OBJECT_OF = 10
    # IDropTarget vtable: 3 DragEnter, 4 DragOver, 5 DragLeave, 6 Drop
    DT_DRAGENTER, DT_DRAGOVER, DT_DROP = 3, 4, 6

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    class POINTL(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def guid(s: str) -> GUID:
        g = GUID()
        ole32.IIDFromString(c_wchar_p(s), byref(g))
        return g

    def vtbl_call(ptr, index, restype, argtypes):
        vtbl = ctypes.cast(ptr, POINTER(c_void_p))
        func_ptr = ctypes.cast(vtbl[0], POINTER(c_void_p))[index]
        return ctypes.WINFUNCTYPE(restype, *argtypes)(func_ptr)

    def release(ptr):
        if ptr:
            vtbl_call(ptr, REL, ctypes.c_ulong, (c_void_p,))(ptr)

    IID_IShellFolder = guid("{000214E6-0000-0000-C000-000000000046}")
    IID_IDataObject = guid("{0000010E-0000-0000-C000-000000000046}")
    IID_IDropTarget = guid("{00000122-0000-0000-C000-000000000046}")

    shell32.SHParseDisplayName.restype = ctypes.HRESULT
    shell32.SHParseDisplayName.argtypes = [
        c_wchar_p, c_void_p, POINTER(c_void_p), ctypes.c_ulong,
        POINTER(ctypes.c_ulong)]
    shell32.SHBindToParent.restype = ctypes.HRESULT
    shell32.SHBindToParent.argtypes = [
        c_void_p, POINTER(GUID), POINTER(c_void_p), POINTER(c_void_p)]
    ILFindLastID = shell32.ILFindLastID
    ILFindLastID.restype = c_void_p
    ILFindLastID.argtypes = [c_void_p]
    CoTaskMemFree = ole32.CoTaskMemFree
    CoTaskMemFree.argtypes = [c_void_p]

    def parse(path: str) -> c_void_p:
        pidl = c_void_p()
        attrs = ctypes.c_ulong(0)
        if shell32.SHParseDisplayName(
                c_wchar_p(os.path.normpath(path)), None, byref(pidl), 0,
                byref(attrs)) == S_OK and pidl:
            return pidl
        return c_void_p()

    def get_ui_object(folder, n, children, iid):
        out = c_void_p()
        get_ui = vtbl_call(
            folder, SF_GET_UI_OBJECT_OF, ctypes.HRESULT,
            (c_void_p, HWND, UINT, POINTER(c_void_p), POINTER(GUID),
             POINTER(ctypes.c_ulong), POINTER(c_void_p)))
        if get_ui(folder, HWND(hwnd), UINT(n), children, byref(iid), None,
                  byref(out)) == S_OK and out:
            return out
        return c_void_p()

    ole32.CoInitialize(None)
    src_pidls: list = []
    dest_pidl = c_void_p()
    src_parent = c_void_p()
    dest_parent = c_void_p()
    dataobj = c_void_p()
    droptgt = c_void_p()
    try:
        for p in paths:
            pidl = parse(p)
            if pidl:
                src_pidls.append(pidl)
        if not src_pidls:
            return False
        child = c_void_p()
        if shell32.SHBindToParent(
                src_pidls[0], byref(IID_IShellFolder), byref(src_parent),
                byref(child)) != S_OK or not src_parent:
            return False
        arr = (c_void_p * len(src_pidls))()
        kept = 0
        for fp in src_pidls:
            last = ILFindLastID(fp)
            if last:
                arr[kept] = c_void_p(last)
                kept += 1
        if kept == 0:
            return False
        dataobj = get_ui_object(src_parent, kept, arr, IID_IDataObject)
        if not dataobj:
            return False
        dest_pidl = parse(dest_dir)
        if not dest_pidl:
            return False
        dest_child = c_void_p()
        if shell32.SHBindToParent(
                dest_pidl, byref(IID_IShellFolder), byref(dest_parent),
                byref(dest_child)) != S_OK or not dest_parent:
            return False
        dchild_arr = (c_void_p * 1)()
        dchild_arr[0] = c_void_p(ILFindLastID(dest_pidl))
        droptgt = get_ui_object(dest_parent, 1, dchild_arr, IID_IDropTarget)
        if not droptgt:
            return False
        pt = POINTL(x, y)
        effect = ctypes.c_ulong(DROPEFFECT_ALL)
        drag_enter = vtbl_call(
            droptgt, DT_DRAGENTER, ctypes.HRESULT,
            (c_void_p, c_void_p, ctypes.c_ulong, POINTL,
             POINTER(ctypes.c_ulong)))
        drag_over = vtbl_call(
            droptgt, DT_DRAGOVER, ctypes.HRESULT,
            (c_void_p, ctypes.c_ulong, POINTL, POINTER(ctypes.c_ulong)))
        drop = vtbl_call(
            droptgt, DT_DROP, ctypes.HRESULT,
            (c_void_p, c_void_p, ctypes.c_ulong, POINTL,
             POINTER(ctypes.c_ulong)))
        try:
            drag_enter(droptgt, dataobj, MK_RBUTTON, pt, byref(effect))
        except OSError:
            pass
        effect.value = DROPEFFECT_ALL
        try:
            drag_over(droptgt, MK_RBUTTON, pt, byref(effect))
        except OSError:
            pass
        effect.value = DROPEFFECT_ALL
        try:
            drop(droptgt, dataobj, MK_RBUTTON, pt, byref(effect))
        except OSError:
            pass
        return True
    finally:
        release(droptgt)
        release(dataobj)
        release(dest_parent)
        release(src_parent)
        if dest_pidl:
            CoTaskMemFree(dest_pidl)
        for fp in src_pidls:
            CoTaskMemFree(fp)
        ole32.CoUninitialize()


def _combined(hwnd: int, paths: list[str], x: int, y: int,
              fibro_items: list) -> "tuple[bool, str | None]":
    """シェル IContextMenu の HMENU に Fibro 項目を上部挿入して表示・振り分け。"""
    import ctypes
    import os
    from ctypes import POINTER, byref, c_void_p, c_wchar_p
    from ctypes.wintypes import HMENU, HWND, INT, UINT

    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")
    user32 = ctypes.WinDLL("user32")

    S_OK = 0
    CMF_EXTENDEDVERBS = 0x00000100
    TPM_RETURNCMD = 0x0100
    TPM_RIGHTBUTTON = 0x0002
    SW_SHOWNORMAL = 1
    ID_FIRST = 1
    ID_LAST = 0x7FFF
    FIBRO_ID_BASE = 0xE000     # Fibro 項目の ID（シェルの 1..0x7FFF と分離）
    MF_BYPOSITION = 0x0400
    MF_STRING = 0x0000
    MF_SEPARATOR = 0x0800
    MF_POPUP = 0x0010
    MF_GRAYED = 0x0001
    REL = 2
    SF_GET_UI_OBJECT_OF = 10
    CM_QUERY = 3
    CM_INVOKE = 4

    def vtbl_call(ptr, index, restype, argtypes):
        vtbl = ctypes.cast(ptr, POINTER(c_void_p))
        func_ptr = ctypes.cast(vtbl[0], POINTER(c_void_p))[index]
        return ctypes.WINFUNCTYPE(restype, *argtypes)(func_ptr)

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
    shell32.SHBindToParent.restype = ctypes.HRESULT
    shell32.SHBindToParent.argtypes = [
        c_void_p, POINTER(GUID), POINTER(c_void_p), POINTER(c_void_p)]
    ILFindLastID = shell32.ILFindLastID
    ILFindLastID.restype = c_void_p
    ILFindLastID.argtypes = [c_void_p]
    CoTaskMemFree = ole32.CoTaskMemFree
    CoTaskMemFree.argtypes = [c_void_p]
    user32.InsertMenuW.argtypes = [
        HMENU, UINT, UINT, ctypes.c_size_t, c_wchar_p]
    user32.InsertMenuW.restype = ctypes.c_bool
    user32.AppendMenuW.argtypes = [HMENU, UINT, ctypes.c_size_t, c_wchar_p]
    user32.AppendMenuW.restype = ctypes.c_bool
    user32.CreatePopupMenu.restype = HMENU

    ole32.CoInitialize(None)
    full_pidls: list = []
    parent = c_void_p()
    ccm = c_void_p()
    hmenu = None
    submenus: list = []
    id_to_key: dict = {}
    try:
        for p in paths:
            pidl = c_void_p()
            attrs = ctypes.c_ulong(0)
            if shell32.SHParseDisplayName(
                    c_wchar_p(os.path.normpath(p)), None, byref(pidl), 0,
                    byref(attrs)) == S_OK and pidl:
                full_pidls.append(pidl)
        if not full_pidls:
            return False, None
        first_child = c_void_p()
        if shell32.SHBindToParent(
                full_pidls[0], byref(IID_IShellFolder), byref(parent),
                byref(first_child)) != S_OK or not parent:
            return False, None
        child_array = (c_void_p * len(full_pidls))()
        kept = 0
        for fp in full_pidls:
            last = ILFindLastID(fp)
            if last:
                child_array[kept] = c_void_p(last)
                kept += 1
        if kept == 0:
            return False, None
        get_ui = vtbl_call(
            parent, SF_GET_UI_OBJECT_OF, ctypes.HRESULT,
            (c_void_p, HWND, UINT, POINTER(c_void_p), POINTER(GUID),
             POINTER(ctypes.c_ulong), POINTER(c_void_p)))
        if get_ui(parent, HWND(hwnd), UINT(kept), child_array,
                  byref(IID_IContextMenu), None, byref(ccm)) != S_OK or not ccm:
            return False, None

        hmenu = user32.CreatePopupMenu()
        query = vtbl_call(
            ccm, CM_QUERY, ctypes.HRESULT,
            (c_void_p, HMENU, UINT, UINT, UINT, UINT))
        if query(ccm, HMENU(hmenu), UINT(0), UINT(ID_FIRST), UINT(ID_LAST),
                 UINT(CMF_EXTENDEDVERBS)) < 0:
            return False, None

        next_id = FIBRO_ID_BASE
        pos = 0

        def insert_action(node):
            nonlocal next_id, pos
            fid = next_id
            next_id += 1
            id_to_key[fid] = node["key"]
            flags = MF_BYPOSITION | MF_STRING
            if not node.get("enabled", True):
                flags |= MF_GRAYED
            user32.InsertMenuW(HMENU(hmenu), UINT(pos), UINT(flags),
                               ctypes.c_size_t(fid), c_wchar_p(node["label"]))
            pos += 1

        def insert_submenu(node):
            nonlocal next_id, pos
            sub = user32.CreatePopupMenu()
            submenus.append(sub)
            for it in node["items"]:
                fid = next_id
                next_id += 1
                id_to_key[fid] = it["key"]
                user32.AppendMenuW(HMENU(sub), UINT(MF_STRING),
                                   ctypes.c_size_t(fid), c_wchar_p(it["label"]))
            user32.InsertMenuW(HMENU(hmenu), UINT(pos),
                               UINT(MF_BYPOSITION | MF_POPUP),
                               ctypes.c_size_t(sub), c_wchar_p(node["label"]))
            pos += 1

        for node in fibro_items:
            t = node.get("type")
            if t == "sep":
                user32.InsertMenuW(HMENU(hmenu), UINT(pos),
                                   UINT(MF_BYPOSITION | MF_SEPARATOR),
                                   ctypes.c_size_t(0), None)
                pos += 1
            elif t == "submenu":
                insert_submenu(node)
            else:
                insert_action(node)
        user32.InsertMenuW(HMENU(hmenu), UINT(pos),
                           UINT(MF_BYPOSITION | MF_SEPARATOR),
                           ctypes.c_size_t(0), None)

        user32.SetForegroundWindow(HWND(hwnd))
        user32.TrackPopupMenuEx.restype = INT
        user32.TrackPopupMenuEx.argtypes = [
            HMENU, UINT, INT, INT, HWND, c_void_p]
        cmd = user32.TrackPopupMenuEx(
            HMENU(hmenu), UINT(TPM_RETURNCMD | TPM_RIGHTBUTTON),
            INT(x), INT(y), HWND(hwnd), None)

        if cmd >= FIBRO_ID_BASE:
            return True, id_to_key.get(int(cmd))
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
            info.lpVerb = ctypes.cast(
                ctypes.c_void_p(cmd - ID_FIRST), ctypes.c_char_p)
            info.nShow = SW_SHOWNORMAL
            invoke = vtbl_call(
                ccm, CM_INVOKE, ctypes.HRESULT,
                (c_void_p, POINTER(CMINVOKECOMMANDINFO)))
            invoke(ccm, byref(info))
        return True, None
    finally:
        if hmenu:
            user32.DestroyMenu(HMENU(hmenu))
        release(ccm)
        release(parent)
        for fp in full_pidls:
            CoTaskMemFree(fp)
        ole32.CoUninitialize()
