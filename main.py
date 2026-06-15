"""Fibro — ファイルエクスプローラー拡張アプリ エントリポイント。"""
import sys


def main() -> int:
    incoming = sys.argv[1:]  # 開きたいフォルダ（省略可）

    # 軽量フォワード: 既存インスタンスがあれば、重い GUI を一切読み込まずに
    # フォルダを転送して即終了する（Win+E・フォルダ既定動作の体感を高速化）。
    # QApplication すら作らずに済む（QtNetwork のみで完結）。
    from app import single_instance
    if single_instance.try_send_to_existing(incoming):
        return 0

    # ここからが主インスタンス。重い GUI モジュールはここで初めて読み込む。
    from pathlib import Path

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.gui.main_window import MainWindow
    from app.paths import APP_ICON

    app = QApplication(sys.argv)
    app.setApplicationName("Fibro")
    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))

    window = MainWindow()
    server = single_instance.InstanceServer()
    server.message_received.connect(window.handle_remote_open)
    server.start()
    window._instance_server = server  # GC 防止に保持

    window.theme_manager.apply(app)  # 保存済みテーマを起動時に適用
    window.show()

    # フォルダ引数付き起動（ダブルクリックでの「開く」差し替え等）は
    # 初回インスタンスでもそのフォルダを開く
    first_dir = next((p for p in incoming if Path(p).is_dir()), None)
    if first_dir:
        window.navigate(str(Path(first_dir)))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
