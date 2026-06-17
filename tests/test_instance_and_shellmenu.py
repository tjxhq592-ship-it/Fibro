"""改善 22・23 のテスト。

23 単一インスタンス: QLocalServer/Socket をプロセス内で往復させ IPC を検証。
   （本番は別プロセス間。ここでは送信側と受信側のイベントループを交互に回して
   そのプロセス間挙動を再現する。）
22 シェルメニュー: is_supported が例外なく bool を返すことを検証（実表示は手動）。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import shell_menu  # noqa: E402
from app import single_instance  # noqa: E402
from app import winekey  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def server(qapp):
    """各テスト用にサーバを起動し、終了時に確実に閉じる（GC 競合回避）。"""
    QLocalServer.removeServer(single_instance.SERVER_NAME)
    srv = single_instance.InstanceServer()
    assert srv.start() is True
    yield srv
    srv._server.close()
    for _ in range(20):
        qapp.processEvents()


def _pump(app, predicate, limit=300):
    for _ in range(limit):
        app.processEvents()
        if predicate():
            return


def _send_interleaved(app, paths):
    """送信側と受信側を交互に回しつつ paths を送る（別プロセス挙動を再現）。"""
    sock = QLocalSocket()
    sock.connectToServer(single_instance.SERVER_NAME)
    assert sock.waitForConnected(500)
    sock.write("\n".join(paths).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(500)
    _pump(app, lambda: False, limit=20)  # サーバに読ませる
    sock.disconnectFromServer()
    _pump(app, lambda: False, limit=20)


def test_send_to_existing_false_when_no_server(qapp):
    """サーバ未起動時は委譲できず False。"""
    QLocalServer.removeServer(single_instance.SERVER_NAME)
    assert single_instance.try_send_to_existing(["C:\\"]) is False


def test_try_send_returns_true_when_server_present(qapp, server):
    """サーバ起動中なら接続成立で True を返す。"""
    assert single_instance.try_send_to_existing(["C:\\"]) is True


def test_server_receives_paths(qapp, server):
    """送られた paths を message_received が emit する。"""
    received = []
    server.message_received.connect(received.append)
    _send_interleaved(qapp, ["C:\\", "D:\\data"])
    _pump(qapp, lambda: bool(received))

    assert received and received[0] == ["C:\\", "D:\\data"]


def test_empty_payload_emits_empty_list(qapp, server):
    """paths 空でも接続は成立し、空リストを emit。"""
    received = []
    server.message_received.connect(received.append)
    _send_interleaved(qapp, [])
    _pump(qapp, lambda: bool(received))

    assert received and received[0] == []


def test_shell_menu_is_supported_returns_bool():
    """is_supported は例外を出さず bool を返す。"""
    assert isinstance(shell_menu.is_supported(), bool)


def test_shell_menu_empty_paths_returns_false():
    """空 paths は表示せず False。"""
    assert shell_menu.show_shell_context_menu(0, [], 0, 0) is False


def test_drag_drop_menu_guards():
    """右ドラッグメニュー: 空 paths / 空 dest は False。"""
    assert shell_menu.show_drag_drop_menu(0, [], "C:\\", 0, 0) is False
    assert shell_menu.show_drag_drop_menu(0, [r"C:\x.txt"], "", 0, 0) is False


def test_combined_menu_guards():
    """統合メニュー: 空 paths は (False, None)（例外を出さない）。"""
    assert shell_menu.show_combined_menu(0, [], 0, 0, []) == (False, None)


def test_shell_menu_builds_menu_for_forward_slash_path(tmp_path):
    """スラッシュ区切り（QFileSystemModel 形式）でもメニューを構築できる。

    TrackPopupMenuEx はブロックするため 0（選択なし）を返すよう差し替え、
    パース→親バインド→メニュー構築まで到達して True を返すことを確認。
    """
    if not shell_menu.is_supported():
        pytest.skip("Windows 以外")
    import ctypes
    f = tmp_path / "a.txt"
    f.write_text("x")
    fwd = str(f).replace("\\", "/")

    real = ctypes.WinDLL

    class _FakeUser32:
        def __init__(self, r):
            self._r = r

        def __getattr__(self, n):
            if n == "TrackPopupMenuEx":
                return lambda *a, **k: 0  # ブロックせず「選択なし」
            return getattr(self._r, n)

    def _patched(name, *a, **k):
        d = real(name, *a, **k)
        return _FakeUser32(d) if name == "user32" else d

    ctypes.WinDLL = _patched
    try:
        assert shell_menu.show_shell_context_menu(0, [fwd], 100, 100) is True
    finally:
        ctypes.WinDLL = real


# ---- Win+E・フォルダ既定動作のオーバーライド（安全なテスト用キーで検証） ----
@pytest.fixture
def safe_winekey(monkeypatch):
    """実際のキーには触れず、HKCU\\Software\\FibroTest 配下で検証。"""
    if not winekey.is_supported():
        pytest.skip("Windows 以外")
    import winreg
    wine_key = r"Software\FibroTest\winekey\command"
    dir_key = r"Software\FibroTest\dir\shell\open\command"
    dir_shell_key = r"Software\FibroTest\dir\shell"
    monkeypatch.setattr(winekey, "_WINE_KEY", wine_key)
    monkeypatch.setattr(winekey, "_DIR_KEY", dir_key)
    monkeypatch.setattr(winekey, "_DIR_SHELL_KEY", dir_shell_key)
    yield (wine_key, dir_key, dir_shell_key)
    # 後始末: 作成したテストキーを末端から削除
    for sub in (wine_key, r"Software\FibroTest\winekey",
                dir_key, r"Software\FibroTest\dir\shell\open",
                dir_shell_key, r"Software\FibroTest\dir",
                r"Software\FibroTest"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass


def test_winekey_enable_disable_roundtrip(safe_winekey):
    assert winekey.is_enabled() is False
    assert winekey.enable(r"C:\apps\Fibro.exe") is True
    assert winekey.is_enabled() is True
    assert winekey.disable() is True
    assert winekey.is_enabled() is False


def test_winekey_enable_sets_both_keys(safe_winekey):
    """有効化で Win+E・フォルダ open・既定動詞 open が設定される。"""
    import winreg
    wine_key, dir_key, dir_shell_key = safe_winekey
    assert winekey.enable(r"C:\apps\Fibro.exe") is True
    # フォルダ open は "%1" 付きでコマンドが入る
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, dir_key) as k:
        cmd, _ = winreg.QueryValueEx(k, None)
    assert "%1" in cmd and "Fibro.exe" in cmd
    # Win+E は引数なし
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, wine_key) as k:
        cmd2, _ = winreg.QueryValueEx(k, None)
    assert "Fibro.exe" in cmd2 and "%1" not in cmd2
    # Directory の既定動詞が open になっている
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, dir_shell_key) as k:
        assert winreg.QueryValueEx(k, None)[0] == "open"
    winekey.disable()
    # コマンドキーは消え、既定動詞も解除される
    for key in (wine_key, dir_key):
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key).Close()
            raise AssertionError(f"{key} が残存")
        except FileNotFoundError:
            pass
    # 既定動詞の値が消えていること（キーは残ってもよい）
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, dir_shell_key) as k:
            try:
                winreg.QueryValueEx(k, None)
                raise AssertionError("既定動詞が残存")
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def test_winekey_disable_when_absent_is_ok(safe_winekey):
    """未設定でも disable は True（冪等）。"""
    assert winekey.disable() is True


def test_winekey_exe_path_none_in_source_mode():
    """ソース実行時は exe パスが確定しない（UI で無効化される）。"""
    assert winekey.fibro_exe_path() is None


def test_force_foreground_noop_on_zero_hwnd():
    """hwnd=0 は no-op で False、例外を出さない（実 HWND は手動確認）。"""
    from app.foreground import force_foreground
    assert force_foreground(0) is False
