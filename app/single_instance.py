"""単一インスタンス化と IPC（QLocalServer/QLocalSocket）。

二重起動を検知し、既存インスタンスへ「このフォルダを新規タブで開け」という
メッセージを送る。Win+E（fibro_hotkey.ahk）からの再起動もここで受ける。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "Fibro-SingleInstance"
_ENCODING = "utf-8"


def try_send_to_existing(paths: list[str], timeout_ms: int = 300) -> bool:
    """既存インスタンスへ paths を送る。送れたら True（＝既に起動中）。

    接続できなければ False（＝自分が最初のインスタンス）。
    """
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(timeout_ms):
        return False
    # 既存インスタンスに前面化の許可を渡す（フォアグラウンド横取り制限の緩和）
    _allow_foreground_for_any()
    payload = "\n".join(paths).encode(_ENCODING)
    socket.write(payload)
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.waitForDisconnected(timeout_ms)
    return True


def _allow_foreground_for_any() -> None:
    """AllowSetForegroundWindow(ASFW_ANY)。非 Windows・失敗時は no-op。"""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ASFW_ANY = -1  # 任意プロセスに前面化を許可
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:  # noqa: BLE001
        pass


class InstanceServer(QObject):
    """ローカルサーバ。受信ペイロードを message_received(list[str]) で通知。"""

    message_received = Signal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

    def start(self) -> bool:
        """サーバ待受を開始。古いソケットを掃除してから listen。"""
        QLocalServer.removeServer(SERVER_NAME)  # 前回クラッシュ等の残骸対策
        return self._server.listen(SERVER_NAME)

    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        buffer = bytearray()

        def read_available() -> None:
            buffer.extend(bytes(conn.readAll().data()))

        def finish() -> None:
            read_available()
            conn.deleteLater()
            text = bytes(buffer).decode(_ENCODING, errors="replace")
            paths = [p for p in text.split("\n") if p]
            self.message_received.emit(paths)

        conn.readyRead.connect(read_available)
        conn.disconnected.connect(finish)
        # 接続シグナル発火時点で既に到着済みのデータを取りこぼさない
        read_available()
