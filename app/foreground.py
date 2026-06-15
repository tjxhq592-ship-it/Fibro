"""ウィンドウを確実に最前面へ持ってくる（Windows のフォアグラウンド制限対策）。

別プロセスからの依頼（Win+E 等）では SetForegroundWindow だけでは前面化できない
ため、現在のフォアグラウンドスレッドへ AttachThreadInput してから前面化する定番手法。
非 Windows では no-op。ウィンドウの最大化状態は変えない。
"""
from __future__ import annotations

import sys


def force_foreground(hwnd: int) -> bool:
    """hwnd を最前面・アクティブにする。成功で True。"""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        SW_SHOW = 5  # 表示のみ（最大化状態は維持）

        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_thread = kernel32.GetCurrentThreadId()

        attached = []
        try:
            for t in {fg_thread, cur_thread}:
                if t and t != target_thread:
                    if user32.AttachThreadInput(t, target_thread, True):
                        attached.append(t)
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            for t in attached:
                user32.AttachThreadInput(t, target_thread, False)
        return True
    except Exception:  # noqa: BLE001 — 前面化失敗は致命的でない
        return False
