# Fibro 改善計画 — フリーズ対策・ロード表示・macOS風洗練・不要削除・ショートカット一覧

## Context（背景）

Fibro は PySide6 製のファイルエクスプローラー（Phase 1 完了済み）。今回の要望は次の5点:

1. **フリーズが発生しないこと** — 調査の結果、ディレクトリ走査(`QFileSystemModel`)、ネットワーク到達性(`netpath.reachable`/`safe_disk_usage` の2秒タイムアウト)、サムネ・アイコンの遅延解決、選択サイズ計算(`_SelectionSizeJob`)は **既に非同期化されており、フリーズ対策は丁寧に実装済み**。残る課題は「読み込み中にUIが無応答に見える」という体感（プログレス表示の不足）。
2. **読み込み中の明示** — ネットワークドライブ等で読み込み中に「ロード中」表示が欲しい。→ ファイル一覧内オーバーレイ（ユーザー選択）。
3. **macOS風の洗練** — レイアウトは現行踏襲。ネイティブ枠は維持（ユーザー選択。Win11のDWMで既に薄く角丸）。内部ウィジェット（フィルタ欄・タブ・ボタン・リスト選択・スクロールバー・メニュー）を角丸・余白・配色でブラッシュアップ。お気に入りに ★ アイコン。
4. **不要なロジック/メニューの削除** — F6「ペイン切替」を削除（ユーザーは手動=クリックで切替）。重複デッドコードを除去。
5. **ヘルプにショートカット一覧** — ヘルプメニューにボタンを追加し、押下でショートカット一覧ポップアップを表示。

調査で判明した主なファイル: [app/gui/main_window.py](app/gui/main_window.py)（1544行・全体制御）、[app/gui/file_pane.py](app/gui/file_pane.py)（ペイン）、[app/gui/theme.py](app/gui/theme.py)（テーマ）、[app/gui/favorites_sidebar.py](app/gui/favorites_sidebar.py)、[app/gui/collapsible.py](app/gui/collapsible.py)。

---

## 変更内容

### 1. ロード中オーバーレイ（ファイル一覧内）

**[app/gui/file_pane.py](app/gui/file_pane.py)** — `FilePane` に自己完結したロード表示を追加。

- `__init__` 末尾で**オーバーレイ用の子ウィジェット**を作成（`FilePane` 直下に parent、`_stack` の上に `raise_()`）。中身は角丸の半透明カード（`QFrame`）＋ `QLabel("読み込み中…")` ＋ ビジーインジケータ `QProgressBar`（`setRange(0,0)`）。初期は `hide()`。
- `resizeEvent` をオーバーライドし、オーバーレイを `FilePane` 全面に追従させる（カードは中央寄せ）。
- `self._load_timer = QTimer(self, singleShot=True, interval=150)` → タイムアウトで `self._loading_overlay.show()`。**150ms 遅延**でローカル高速フォルダのチラつきを防ぐ（瞬時に読み込めるフォルダではオーバーレイは出ない）。
- `__init__` で `self.list_model.directoryLoaded.connect(self._on_dir_loaded)` を接続（MainWindow 側の件数表示用接続(478行)とは別に、ペイン自身で持つ）。
- メソッド追加:
  - `begin_loading(path)`: `self._expected_path = str(Path(path))` を記録し、`_load_timer.start()`。安全策として最大表示時間のフォールバックタイマー（例: 60秒で自動 `_hide_loading()`）も開始し、`directoryLoaded` が来ない異常時のスタックを防ぐ。
  - `_on_dir_loaded(path)`: `str(Path(path)) == self._expected_path` なら `_hide_loading()`。
  - `_hide_loading()`: `_load_timer.stop()` ＋ フォールバックタイマー停止 ＋ `self._loading_overlay.hide()`。

**[app/gui/main_window.py](app/gui/main_window.py)** — ナビゲーション時にロード開始を通知。

- `navigate()`（831行）で `self.list_model.setRootPath(path)`（842行）の直後に `self._active_pane.begin_loading(path)` を呼ぶ。
- `refresh()`（1392行）でも `setRootPath` 後に `self._active_pane.begin_loading(path)` を呼ぶ。
- 既存の `_on_directory_loaded`（486行・「N項目」表示）はそのまま残す。

> 補足（フリーズ堅牢化・任意）: `navigate()` の `Path(path).is_dir()`（833行）は死んだネットワークパスで稀にブロックし得る同期チェック。今回はオーバーレイで体感を改善しつつ、必要なら `netpath` のタイムアウト付きチェックに置き換える余地があることを明記（スコープ外・任意）。

### 2. macOS風スタイルの洗練（QSS）

**[app/gui/theme.py](app/gui/theme.py)** — 既存の Fusion + QPalette を維持しつつ、`ThemeManager.apply()` の末尾でアプリ全体に **QSS スタイルシート**を適用。ネイティブ枠は変更しない（フレームレス化しない）。

- 新関数 `_build_stylesheet(theme: str) -> str` を追加し、ライト/ダークでアクセント `#3d7eff`・境界色・ホバー色を切り替えて返す。`apply()` 内で `app.setStyleSheet(_build_stylesheet(theme))` を呼ぶ（パレット適用の後）。
- 角丸・余白の対象（macOS的な柔らかさ）:
  - `QLineEdit`（フィルタ欄・検索欄）: `border-radius: 8px; padding: 4px 8px;` ＋ 細い境界線、フォーカス時にアクセント枠。
  - `QToolButton, QPushButton`: `border-radius: 6px; padding: 3px 8px;` ＋ ホバー時に薄い背景。
  - `QTabBar::tab`: 上角丸 `border-top-left/right-radius: 8px;`・`padding: 5px 12px;`・選択タブはアクセント下線かやや明るい背景。
  - `QTreeView/QTableView/QListView`: `::item { padding: 3px 4px; }` で行に余白（macOS的な行高）。選択は `selection-background-color` をアクセントの淡色に。
  - `QScrollBar:vertical/horizontal`: 幅 10px・`border-radius`・`background: transparent`・ハンドルのみ半透明グレーの**細い macOS 風**スクロールバー。
  - `QMenu`: `border-radius: 8px; padding: 4px;`・`QMenu::item { padding: 4px 24px; border-radius: 5px; }`・選択項目にアクセント淡色。
  - `QHeaderView::section`: 余白＋下境界のみの控えめな見た目。
  - `QStatusBar` / 下部ラベル: 文字をやや淡色にして情報密度を下げる。
- ロード中オーバーレイのカードも QSS（角丸＋半透明背景）でテーマ追従させる（オブジェクト名 `#loadingCard` を付与して theme.py 側でスタイル指定、もしくは file_pane 側で最小指定）。

> 注: `QTableView` の行選択を完全な「角丸ピル」にするのは Qt の制約で難しいため、選択色・余白・ホバーの調整に留める（レイアウトは現行踏襲）。

### 3. お気に入りに ★ アイコン

- **[app/gui/main_window.py](app/gui/main_window.py)** 417行: セクション見出しを `CollapsibleSection("お気に入り", ...)` → `CollapsibleSection("★ お気に入り", ...)` に変更。統一感のため履歴/フォルダツリーにも軽いアイコン付与を検討（例: `🕘 履歴` / `🗂 フォルダツリー`）。
- お気に入りの各項目は既に `⭐`（葉）/`📁`（グループ）を [favorites_sidebar.py:114-121](app/gui/favorites_sidebar.py#L114-L121) で付与済み。要望の「★」は実装済みのため、見出しへの ★ 追加で要望を明確に満たす。

### 4. 不要なロジック/メニューの削除

**確定削除（調査で確認済み）:**

- **重複デッドコード**: `_set_initial_directory` が [804-814行](app/gui/main_window.py#L804) と [1381-1389行](app/gui/main_window.py#L1381) に二重定義され、**前者(804-814)は後者で上書きされ到達不能**。804-814 を削除。
- **F6「ペイン切替」**（ユーザー要望: 手動切替で十分）:
  - アクション登録を削除: [707行](app/gui/main_window.py#L707) `add("ペイン切替", "F6", self.toggle_active_pane)`。
  - メニュー項目を削除: [731行](app/gui/main_window.py#L731) 表示メニューの「ペイン切替\tF6」。
  - メソッド `toggle_active_pane`（[638-646行](app/gui/main_window.py#L638)）を削除。クリック/フォーカスでのアクティブ化は `FilePane.activated` シグナル → `_set_active_pane` で既に機能するため、手動切替は維持される。
- **テスト修正** [tests/test_panes_tabs_preview.py](tests/test_panes_tabs_preview.py):
  - `test_f6_switches_active_pane`（608-618行）を削除（削除した機能のテストのため）。
  - `test_operations_target_active_pane`（620-631行）の `win.toggle_active_pane()`（628行）を `win._set_active_pane(win.secondary_pane)` に置換（「操作はアクティブペインを対象にする」検証は維持）。

**隠し機能の調査結果（参考）:** 検索(Ctrl+F・遅延生成)、ズーム(Ctrl+ホイール)、デュアルペイン(F9)、一括リネーム、Undo、クイックプレビュー等はすべて UI から到達可能で使用中。明確な未使用は上記の重複定義のみ。実装時に `git grep` で「定義のみ・未参照のメソッド」を最終確認し、見つかれば併せて除去。

### 5. ヘルプメニューにショートカット一覧

**[app/gui/main_window.py](app/gui/main_window.py)** ヘルプメニュー（747-750行）に項目を追加し、ポップアップ表示メソッドを新設。

- メニュー: `help_menu.addAction("ショートカット一覧…", self._show_shortcuts)` を「バグを報告…」の前に追加。
- `_show_shortcuts()` を新設。`QDialog`（モーダル不要なら `show()`）でショートカット一覧を**カテゴリ別の表**（`QGridLayout` または `QTableWidget`）で表示。角丸スタイルは QSS で追従。内容は実装済みショートカット（[_build_actions](app/gui/main_window.py#L676) と eventFilter 由来）から構成:

  | 操作 | キー |
  |---|---|
  | ナビゲーション | 戻る `Alt+←` / 進む `Alt+→` / 上へ `Alt+↑` / パス入力 `F4` / フィルタ `F3` / 更新 `F5` |
  | タブ | 新規 `Ctrl+T` / 閉じる `Ctrl+W` / 切替 `Ctrl+Tab`・`Ctrl+Shift+Tab` |
  | 表示 | 詳細⇔サムネイル `Ctrl+Shift+T` / デュアルペイン `F9` / クイックプレビュー `Space` / ズーム `Ctrl+ホイール` |
  | 編集 | コピー `Ctrl+C` / 切取 `Ctrl+X` / 貼付 `Ctrl+V` / 削除 `Delete` / 完全削除 `Shift+Delete` / 元に戻す `Ctrl+Z` |
  | ファイル操作 | 名前変更 `F2` / 一括リネーム `Ctrl+H` / 新規フォルダー `Ctrl+Shift+N` / パスをコピー `Ctrl+Shift+C` |
  | 選択 | すべて選択 `Ctrl+A` |
  | 検索 | 検索 `Ctrl+F` |

  ※ 削除する F6 は一覧に載せない。一覧の文字列は単一の定数（リスト/辞書）から生成し、将来のショートカット変更に追従しやすくする。

---

## 変更対象ファイル一覧

- [app/gui/file_pane.py](app/gui/file_pane.py) — ロード中オーバーレイ（カード/タイマー/`begin_loading`/`directoryLoaded` 接続）。
- [app/gui/main_window.py](app/gui/main_window.py) — `navigate`/`refresh` で `begin_loading` 呼び出し、F6 と重複 `_set_initial_directory` 削除、お気に入り見出しに ★、ヘルプに「ショートカット一覧…」＋ `_show_shortcuts`。
- [app/gui/theme.py](app/gui/theme.py) — `_build_stylesheet` 追加と `apply()` での適用（macOS風 QSS）。
- [tests/test_panes_tabs_preview.py](tests/test_panes_tabs_preview.py) — F6 テスト削除／置換。

---

## 検証（テスト手順）

1. **既存テスト**: `python -m pytest -q` で全テスト緑（特に `test_panes_tabs_preview.py` の修正後）。
2. **ロード表示**:
   - ローカルの小フォルダへ移動 → オーバーレイが**出ない**（150ms 未満で完了）こと。
   - 大量ファイルのフォルダ／ネットワークドライブへ移動 → 中央に「読み込み中…」＋ビジーバーが表示され、完了で消えること。UI が固まらないこと。
   - `F5`（更新）でも同様に動作すること。
3. **macOS風スタイル**: アプリ起動し、フィルタ欄・タブ・ボタン・メニュー・スクロールバー・リスト選択が角丸＋余白で洗練されて見えること。ライト/ダーク両テーマで破綻がないこと（`設定 → テーマ切替`）。ネイティブのタイトルバー/枠は維持されていること。
4. **お気に入り ★**: 左サイドバーの見出しが「★ お気に入り」になっていること。
5. **不要削除**: `F6` を押してもペインが切り替わらない（手動=クリックで切替できる）こと。表示メニューに「ペイン切替」が無いこと。
6. **ショートカット一覧**: `ヘルプ → ショートカット一覧…` でポップアップが開き、上表のショートカットがカテゴリ別に表示されること（F6 が含まれないこと）。
7. 起動・基本ナビゲーション（戻る/進む/上へ/タブ/デュアルペイン）に回帰がないこと。
