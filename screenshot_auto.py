"""Microsoft Store 出品用スクリーンショット自動撮影スクリプト。

PyAutoGUI で Fibro を自動操作し、推奨 9 シーンをキャプチャ。
1920x1080 で撮影し screenshots/ に保存。

実行: python screenshot_auto.py
（Fibro が起動していないことを確認してから実行）
"""
import subprocess
import time
import os
from pathlib import Path
from PIL import ImageGrab
import pyautogui

# スクリーンショット保存先
SCREENSHOTS_DIR = Path("screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# UI オートメーション用の遅延（秒）
WAIT = 1.5
WAIT_SHORT = 0.5


def take_screenshot(name: str, desc: str = ""):
    """スクリーンショットを撮影して保存。"""
    path = SCREENSHOTS_DIR / f"{name}.png"
    time.sleep(WAIT_SHORT)
    img = ImageGrab.grab()
    img.save(path)
    print(f"✓ {name:30} — {desc}")
    return path


def main():
    print("=" * 70)
    print("Fibro Microsoft Store スクリーンショット自動撮影")
    print("=" * 70)
    print()
    print("準備:")
    print("1. Fibro が起動していないことを確認")
    print("2. 画面解像度が 1920×1080 以上であることを確認")
    print("3. Enter を押して開始...")
    input()

    # Fibro を起動
    print("\n[1/10] Fibro を起動中...")
    subprocess.Popen([r".\dist\Fibro\Fibro.exe"], cwd=".")
    time.sleep(5)  # UI 起動待ち

    # ウィンドウを最大化（pyautogui で Win+Up）
    pyautogui.hotkey("win", "up")
    time.sleep(WAIT)

    print("[2/10] テスト用フォルダを作成中...")
    test_dir = Path.home() / "FibroScreenshots"
    test_dir.mkdir(exist_ok=True)

    # テスト画像・テキストを生成（簡易版）
    (test_dir / "image1.txt").write_text("テスト画像 1")
    (test_dir / "image2.txt").write_text("テスト画像 2")
    (test_dir / "document.txt").write_text("サンプルドキュメント\nこれはテキストファイルです。")

    # テスト用画像フォルダ
    images_dir = test_dir / "Images"
    images_dir.mkdir(exist_ok=True)
    for i in range(3):
        (images_dir / f"photo_{i}.txt").write_text(f"Image {i}")

    # Fibro にテストフォルダを開かせる（クリップボード経由）
    import subprocess as sp
    folder_path = str(test_dir)
    sp.run(
        f'powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait(\\\"{folder_path}\\\"); "',
        shell=True
    )

    # ナビゲーション: Ctrl+L でアドレスバー → フォルダパスを入力
    time.sleep(WAIT)
    pyautogui.hotkey("ctrl", "l")
    time.sleep(WAIT_SHORT)
    pyautogui.typewrite(str(test_dir), interval=0.01)
    pyautogui.press("enter")
    time.sleep(WAIT)

    # === シーン 1: 詳細ビュー全体 ===
    print("[3/10] シーン 1: 詳細ビュー全体...")
    take_screenshot("01_detail_view", "タブ・ツールバー・ドライブ容量表示")

    # === シーン 2: サムネイル表示 ===
    print("[4/10] シーン 2: サムネイル表示...")
    pyautogui.hotkey("ctrl", "shift", "t")  # 表示モード切替
    time.sleep(WAIT)
    take_screenshot("02_thumbnail_view", "アイコンビュー・画像フォルダ")

    # 詳細ビューに戻す
    pyautogui.hotkey("ctrl", "shift", "t")
    time.sleep(WAIT)

    # === シーン 3: 検索パネル ===
    print("[5/10] シーン 3: 検索パネル...")
    pyautogui.hotkey("ctrl", "f")  # 検索パネルを開く
    time.sleep(WAIT)
    pyautogui.typewrite("test", interval=0.05)
    time.sleep(WAIT)
    take_screenshot("03_search_panel", "ファイル名・本文検索結果")

    # 検索をクローズ
    pyautogui.press("escape")
    time.sleep(WAIT_SHORT)

    # === シーン 4: 一括リネーム ===
    print("[6/10] シーン 4: 一括リネーム...")
    # すべてを選択
    pyautogui.hotkey("ctrl", "a")
    time.sleep(WAIT_SHORT)
    # 一括リネーム（Ctrl+H）
    pyautogui.hotkey("ctrl", "h")
    time.sleep(WAIT)
    take_screenshot("04_rename_dialog", "ライブプレビュー表示")

    # リネームダイアログをキャンセル
    pyautogui.press("escape")
    time.sleep(WAIT_SHORT)

    # === シーン 5: デュアルペイン ===
    print("[7/10] シーン 5: デュアルペイン...")
    pyautogui.hotkey("f9")  # デュアルペイン切替
    time.sleep(WAIT)
    take_screenshot("05_dual_pane", "左右分割表示")

    # デュアルペインを閉じる
    pyautogui.hotkey("f9")
    time.sleep(WAIT_SHORT)

    # === シーン 6: お気に入い・最近（左サイドバー） ===
    print("[8/10] シーン 6: お気に入い・最近...")
    # 左サイドバーは常時表示なのでそのまま
    take_screenshot("06_sidebar", "お気に入い・最近フォルダサイドバー")

    # === シーン 7: クイックプレビュー ===
    print("[9/10] シーン 7: クイックプレビュー...")
    # ファイルを1つ選択してから Space
    pyautogui.click(400, 300)  # テーブル内をクリック
    time.sleep(WAIT_SHORT)
    pyautogui.press("space")
    time.sleep(WAIT)
    take_screenshot("07_quick_preview", "Space キー起動プレビュー")

    # プレビューを閉じる
    pyautogui.press("escape")
    time.sleep(WAIT_SHORT)

    # === シーン 8: ファイルプロパティ ===
    print("[10/10] シーン 8: ファイルプロパティ...")
    # ファイルを1つ選択
    pyautogui.click(400, 300)
    time.sleep(WAIT_SHORT)
    # 右クリック → プロパティ
    pyautogui.rightClick(400, 300)
    time.sleep(WAIT)
    # メニューから「プロパティ」を探してクリック（y 座標は要調整）
    pyautogui.click(450, 350)  # "プロパティ" メニュー位置（近似）
    time.sleep(WAIT)
    take_screenshot("08_properties", "詳細情報表示")

    # プロパティダイアログを閉じる
    pyautogui.press("escape")
    time.sleep(WAIT_SHORT)

    # === シーン 9: ダークテーマ（オプション） ===
    print("[9/10] シーン 9: ダークテーマ...")
    # テーマ切替（Ctrl+Shift+T は表示モード）。メニューから切替
    pyautogui.hotkey("alt", "e")  # 編集メニュー（要確認）
    time.sleep(WAIT)
    take_screenshot("09_dark_theme", "ダークテーマ表示")

    # メニューを閉じる
    pyautogui.press("escape")
    time.sleep(WAIT_SHORT)

    print()
    print("=" * 70)
    print("✅ スクリーンショット撮影完了!")
    print(f"   保存先: {SCREENSHOTS_DIR.absolute()}")
    print("   9 枚のスクリーンショットが生成されました。")
    print()
    print("次のステップ:")
    print("1. screenshots/ フォルダを確認し、見栄えをチェック")
    print("2. 必要に応じてトリミング・調整")
    print("3. Store 申請フォームにアップロード")
    print("=" * 70)

    # Fibro を閉じる
    pyautogui.hotkey("alt", "f4")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ エラーが発生しました: {e}")
        print("   PyAutoGUI と PIL (Pillow) がインストールされていることを確認してください。")
        print("   インストール: pip install pyautogui pillow")
