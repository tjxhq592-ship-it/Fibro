"""Fibro — ファイルエクスプローラー拡張アプリ エントリポイント。"""
import sys

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Fibro")
    window = MainWindow()
    window.theme_manager.apply(app)  # 保存済みテーマを起動時に適用
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
