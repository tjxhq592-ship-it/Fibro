"""Fibro — ファイルブラウザー拡張アプリ エントリポイント。"""
import sys


def _install_excepthook() -> None:
    """未捕捉例外を CONFIG_DIR/error.log へ追記する。

    PySide6 のスロット内例外は既定では sys.excepthook に流れるだけで、
    ユーザーには「何も起きない」ように見えて原因が残らない。ログへ
    書き出して調査可能にする（既定のフックにも流して挙動は変えない）。
    """
    import traceback
    from datetime import datetime

    from app.paths import CONFIG_DIR

    def hook(exc_type, exc, tb) -> None:
        try:
            with open(CONFIG_DIR / "error.log", "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


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
    from app.migrations import migrate_default_project_settings
    from app.paths import APP_ICON, CONFIG_DIR

    # 旧 settings.json のプロジェクト範囲キー（theme/place_names/tabs）を
    # default_project_settings.json へ移す（初回起動時のみ・冪等）。
    # MainWindow が settings.json を読む前に必ず実行する。
    migrate_default_project_settings(CONFIG_DIR)

    _install_excepthook()

    app = QApplication(sys.argv)
    app.setApplicationName("Fibro")
    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))

    # Windows の「アニメーション効果を表示する」設定を尊重する。
    # OFF なら全 UI アニメーションが 0ms になる（MOTION.duration 経由）。
    from app.gui.motion import init_reduced_motion
    init_reduced_motion()

    from app.gui.theme import app_font
    app.setFont(app_font())

    window = MainWindow()
    server = single_instance.InstanceServer()
    server.message_received.connect(window.handle_remote_open)
    server.start()
    window._instance_server = server  # GC 防止に保持

    # 保存済みテーマ（プロジェクト範囲）を起動時に適用
    window.theme_manager.apply(
        app, window.project_settings.get("theme", "light"))
    window.show()

    # フォルダ引数付き起動（ダブルクリックでの「開く」差し替え等）は
    # 初回インスタンスでもそのフォルダを開く
    first_dir = next((p for p in incoming if Path(p).is_dir()), None)
    if first_dir:
        window.navigate(str(Path(first_dir)))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
