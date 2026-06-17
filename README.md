# Fibro — ファイルブラウザー拡張アプリ

Windows標準エクスプローラーを補完するデスクトップアプリ。
中核機能は **複数ファイルの一括リネーム** / **複数形式の全文検索** / **フォルダのお気に入り管理**。

## 機能

- **3ペインUI** — フォルダツリー + ファイル一覧 + 詳細（`QFileSystemModel` による仮想化で数万ファイルでも軽快）
- **一括リネーム（PowerRename型）** — 検索→置換（正規表現対応）、連番 `${n}`（開始/桁/増分）、大文字小文字変換、対象選択（名前/拡張子/両方）、ライブプレビュー、衝突自動回避、**Undo**
- **検索** — ファイル名 / テキスト内容 / Excelセル値（.xlsx / .xlsm / .xls）。結果ストリーミング表示・途中キャンセル・バイナリ自動除外・50MB超スキップ・ワイルドカード（`*.md` 等）・サブフォルダー検索切替
- **高速インデックス** — SQLite FTS5（trigram）によるファイル名検索の高速化（再検索 0.3ms / 約1万ファイル、初回と10分経過後に自動再構築）
- **Excel検索の高速プレフィルタ** — xlsx を zip 直読みして文字列キーワードが無いブックを即スキップ
- **フィルタボックス** — 現在フォルダの内容をリアルタイム絞り込み（パスバー右）
- **お気に入り** — JSON保存、タグ/メモ、到達確認（ネットワークパス対応）、ドラッグで並び替え
- **ファイル操作** — コピー/移動（Undo可）、削除はゴミ箱へ（send2trash）
- **テーマ** — ダーク/ライト切替（再起動後も保持）。アイコンは [Feather Icons](https://feathericons.com)（MIT、SVG埋め込み）でテーマに追従
- **レイアウト記憶** — ペイン幅・ウィンドウ位置を終了時に保存し次回復元
- **Win+E 統合（任意）** — 同梱の `fibro_hotkey.ahk`（要 [AutoHotkey v2](https://www.autohotkey.com)）を起動すると Win+E で Fibro が開く。常駐スクリプト方式なのでレジストリ変更なし、終了すれば標準エクスプローラーに戻る。インストーラのオプションでスタートアップ登録も可能

## ショートカット

| キー | 機能 |
|------|------|
| Ctrl+F | 検索パネル |
| Ctrl+H | 一括リネーム |
| F2 | 単一リネーム |
| Ctrl+Z | Undo（リネーム/移動/コピー） |
| Delete | ゴミ箱へ削除 |
| Ctrl+C / X / V | コピー / 切り取り / 貼り付け |
| F5 | 更新 |
| Alt+← / Alt+↑ | 戻る / 上へ |

## 開発環境での実行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

## テスト

```bash
.venv\Scripts\python -m pytest tests/ -q
```

GUIテストは `QT_QPA_PLATFORM=offscreen` で自動実行されます。

## exe ビルド（配布）

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --onedir --windowed --noconfirm --name Fibro main.py
```

- 出力は `dist/Fibro/`（フォルダごと配布）。`Fibro.exe` をダブルクリックで起動。
- `--onedir` を使用（`--onefile` は毎起動でtemp展開され遅く、誤検知されやすいため）。
- 設定（お気に入り・テーマ）は exe と同じ場所の `config/` に保存されます。
- 配布形態: `dist/Fibro/` を zip 圧縮、またはインストーラを作る場合は
  [Inno Setup](https://jrsoftware.org/isinfo.php) をインストールして
  同梱の [installer.iss](installer.iss) をコンパイル（`iscc installer.iss`）。
  `installer_output/FibroSetup-1.0.0.exe` が生成されます（管理者権限不要の
  ユーザー単位インストール）。

## 構成

```
app/
├── engine/   # GUI非依存ロジック（リネーム・検索・ファイル操作）
├── gui/      # PySide6 ウィジェット
└── models/   # お気に入り等のデータモデル
tests/        # pytest（エンジンTDD + offscreen GUIスモーク）
```
