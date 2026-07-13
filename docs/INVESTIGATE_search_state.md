# 調査レポート: 検索パネル状態制御(Phase 0)

- 対象: FEAT「検索パネル 状態制御・リセット・即時キャンセル・進行バー」の Phase 0
- 対象コード: `app/gui/search_panel.py` / `app/gui/main_window.py` /
  `app/engine/search_engine.py` ほか各リーダー / `app/gui/theme.py` / `app/i18n.py`
- 調査環境: Windows 11 / Python 3.14.5 / PySide6 6.11.1 /
  `QT_QPA_PLATFORM=offscreen`（コミット bb1b425 時点）
- 本体コード(`app/`)は**未変更**。動的検証はすべて使い捨てスクリプト
  （セッションのスクラッチパッド、コミットせず）で実施。

---

## 0. 結論サマリ

| 調査項目 | 結論 |
|---|---|
| 1. ルート追従経路 | `set_root()` の呼び出し元は main_window の **3箇所のみ**。パネル内にルート変更 UI は無い（表示ラベルのみ） |
| 2. 既存の状態管理 | 明示的な状態フラグは**無い**。`_worker is not None` とボタンの enabled が実質の状態。**キャンセル押下後に UI が「検索中…」のまま恒久固着する現状不具合を実測で確認** |
| 3. cancel 粒度 | 走査ループ（ファイル間）は十分細かい。**リーダー内部（シート/ページ/行）には cancel が一切届いていない**（引数自体が無い）。ヒット0件の巨大ファイル1つ分がまるごと中断不能区間 |
| 4. 進行バー | **QSS 適用下の `QProgressBar(0,0)` は 10 テーマ全てでオフスクリーンでもアニメーションする**（左→右へ流れるマーキー動作を画像で確認）。自作ウィジェットは不要で、QSS 方式を採用できる |
| 5. 閉鎖経路 | Ctrl+F トグルとドックの ✕ ボタンの **2経路**（+アプリ終了）。Esc は未割り当て。現状、パネルを閉じても検索は走り続ける |
| ゲート条件 | **3件とも非該当**。Phase 1 に進める（承認待ち） |

---

## 1. ルート追従経路の特定（調査項目1）

### 1.1 `set_root()` の呼び出し元（全数）

`SearchPanel.set_root()`（`search_panel.py:142`）は `self._root` とラベルを
更新するだけの素朴なセッター。呼び出し元は grep 全数調査で以下の3箇所のみ:

| 呼び出し元 | 位置 | 契機 |
|---|---|---|
| `MainWindow._sync_active_pane()` | `main_window.py:704-705` | タブ切替・アクティブペイン変更 |
| `MainWindow.navigate()` | `main_window.py:1264-1265` | ディレクトリ移動（ダブルクリック・パンくず・履歴等はすべてここに合流） |
| `MainWindow.toggle_search()` | `main_window.py:2187` | Ctrl+F でパネルを**表示する**とき |

前2者は `if self.search_panel is not None:` ガード付き（遅延生成のため）。
ナビゲーション系の経路（戻る/進む/上へ/ツリークリック/お気に入り等）は
すべて `navigate()` に合流しており、**`set_root()` を迂回してルートを
書き換える経路は存在しない**。

### 1.2 パネル内のルート表示/変更 UI

- 表示: `root_label`（`search_panel.py:150-152`、「検索場所: {path}」）のみ。
- 変更 UI（参照ボタン等）: **存在しない**。→ §3.2 の「パネル内ルート変更 UI の
  setEnabled(False)」は該当なし。ラベルへの 🔒 表示とツールチップ追加のみでよい。

### 1.3 注意点: `_root` の「読み手」は live 参照

worker へはルートを**検索開始時にスナップショット渡し**する
（`search_panel.py:252`）ため走行中の再取得は無い。ただし結果整形の
`_on_hits_batch()` が相対パス計算に `self._root` を**都度参照**する
（`search_panel.py:296-300`）。現状は走行中に `set_root()` されると
**表示中/以降の相対パスの基準だけがズレる**（検索対象は変わらないのに
表示が絶対パスに化ける等）。Phase 1 で `_pinned_root` に置き換えるべき
箇所はこの2点（worker への受け渡しと `_on_hits_batch` の基準）。

---

## 2. 既存の状態管理の確認（調査項目2）

### 2.1 現状のフロー

- **開始** `start_search()`（`search_panel.py:231-256`）:
  冒頭で `cancel_search()`（進行中があれば止める）→ 結果クリア →
  `status_label` に「検索中…」→ `search_btn` 無効化・`cancel_btn` 有効化 →
  カウンタ初期化 → `_flush_timer`(100ms) 開始 → worker 生成
  （`hits_batch` / `status` / `finished_ok` を接続）→ `start()`。
- **キャンセル** `cancel_search()`（`search_panel.py:258-281`）:
  flush 停止・pending 破棄 → `_worker` を None に →
  `worker.cancel()`（`threading.Event`）→ **3シグナルを切断** →
  `wait(2000)` で合流（タイムアウト時のみ親切り離し+退避リスト。経緯は
  `docs/investigation/search_cancel_crash_report.md`）。
- **完了** `_on_finished()`（`search_panel.py:341-364`）:
  flush 最終実行 → `search_btn` 有効化・`cancel_btn` 無効化 → 完了メッセージ。

「検索中」を表す**明示的なフラグ/enum は存在しない**。
`self._worker is not None` と2つのボタンの enabled 状態が実質の状態表現で、
setEnabled の書き手が `start_search` / `_on_finished` /
`_incremental_search` の空文字分岐（`search_panel.py:211-216`）の3箇所に
散在している。§3.1 の「`_set_state()` への集約」を阻む構造は無い。

### 2.2 【実測】キャンセル押下後に UI が恒久固着する（現状不具合）

`cancel_search()` は**ボタン状態もステータスも復元しない**。さらに
`finished_ok` を worker 停止**前**に切断するため、キャンセル時の完了通知は
誰にも届かない。オフスクリーン実測（Fibro リポジトリ全体 11,898 ファイルを
filename 走査中にキャンセル、5試行 + 600ms イベント処理後まで観測）:

```
right after / +600ms とも:
  search_btn=False  cancel_btn=True  status='検索中…'   ← 全5試行で固着
```

つまり**現状、キャンセルボタンを押すと「検索」ボタンが押せず表示も
「検索中…」のまま**で、次のキーワード変更（インクリメンタル）か Enter が
偶然 `start_search()` を呼ぶまで回復しない。本 FEAT の
「キャンセル完了 → RESULTS 遷移 + 部分結果メッセージ」（§3.4）は
この不具合の修正を兼ねる。Phase 1 では**切断ではなく状態機械側で
「キャンセル後の finished を RESULTS 遷移に使う」設計**に改める必要がある
（現行の切断は「古い worker の遅延シグナルが新しい検索の表示を壊す」対策
なので、worker の世代識別と併用すること）。

### 2.3 キャンセルボタンの配置

検索入力行に `search_btn` と並んで常時表示（`search_panel.py:158-165`）。
表示/非表示ではなく enabled/disabled で切り替える方式。§3.3 の
リセットボタンは同じ行に `addWidget` で追加できる。

### 2.4 インクリメンタル検索

`textChanged` → `_schedule_incremental()`（内容検索モード時は追従しない、
`search_panel.py:202-206`）→ 400ms デバウンス → `_incremental_search()` →
非空なら `start_search()`（冒頭の `cancel_search()` で現行走行を止めてから
新検索）/ 空なら `cancel_search()` + 手動でボタン・表示リセット。
「cancel → 再検索」の挙動は §1 ※1 の想定どおりで、状態機械
（SEARCHING 中の再入力 → cancel → 新 SEARCHING）と両立する。

### 2.5 【実測】cancel_search() の GUI ブロック時間

`wait(2000)` は cancel フラグが走査ループ先頭で効くため短時間で返る。
実測（リポジトリ全体走査の実走行中に即キャンセル ×5）:
**中央値 0.9ms・最大 1.8ms**（filename 走査時）。FEAT §3.4 の
「UI 応答 200ms 以内」は、**リーダー内チェックポイント追加後**であれば
内容検索でも満たせる見込み（現状の最悪ケースは §3 参照）。

---

## 3. cancel フラグのチェック粒度（調査項目3）

### 3.1 チェックされている箇所（十分細かい）

| 層 | 位置 | 粒度 |
|---|---|---|
| `_iter_files()` | `search_engine.py:88,94` | ディレクトリ毎 + エントリ毎 |
| `search()` 本体 | `search_engine.py:155,167,184` | 各リーダーの**ヒット yield 毎** |
| `SearchWorker._run_scan` | `search_panel.py:65` | ヒット毎 |
| `SearchWorker._run_indexed` | `search_panel.py:97` | クエリ結果1行毎 |
| `SearchIndex._scan/build/update` | `index_engine.py:70,76` | ディレクトリ毎 + エントリ毎 |

→ **ファイル間の中断は即時**（実測 §2.5 のとおり）。

### 3.2 チェックされていない箇所（本 FEAT の追加対象）

各リーダーは **cancel 引数を受け取らない**ため、「ヒット0件のファイル
1つ分」がまるごと中断不能区間になる（ジェネレータは yield 時しか制御を
返さない）。max_file_size=50MB（`search_engine.py:32`）が上限のため、
最悪で数秒オーダーの区間が存在する:

| リーダー | 中断不能区間 | 推奨チェックポイント |
|---|---|---|
| `search_in_text`（`text_reader.py:43-62`） | 全行ループ（ヒット無し時） | N行毎（例: 1000行毎） |
| `search_in_excel`（`excel_reader.py:77-116`） | ①`may_contain_keyword` プレフィルタが **sharedStrings + 全ワークシート XML を zip 直読み**（`excel_reader.py:61-72`）②`load_workbook` ③シート×行×セルの三重ループ | 行毎またはシート毎 + プレフィルタのエントリ毎 |
| `search_in_xls`（`excel_reader.py:129-158`） | `open_workbook` + シート×行×セル | 行毎またはシート毎 |
| `search_in_pdf`（`pdf_reader.py:32-73`） | `PdfReader()` 構築 + **ページ毎 `extract_text()`**（1ページ数十〜数百ms になり得る） | ページ毎 |
| `search_in_docx`（`office_reader.py:21-46`） | `zf.read("word/document.xml")` 一括読み + `xml_to_text` 一括変換 | 段落ループ毎（一括読み自体は §6 スコープ外の「巨大単一ファイル読込」に相当） |
| `search_in_pptx`（`office_reader.py:49-80`） | スライド毎の `zf.read` + `xml_to_text` | スライド毎 |

いずれもシグネチャに `cancel: threading.Event | None = None` を追加し、
`search()`（`search_engine.py:153,165,182`）から引き渡す形で対応可能。
`openpyxl.load_workbook` / `PdfReader()` / 単一 XML の read など
**ライブラリ呼び出し1回の内部**は中断できないが、これは FEAT §6
（巨大単一ファイル読込の途中中断はスコープ外）と整合する。

---

## 4. QSS/テーマとプログレスバー（調査項目4）

### 4.1 【実測】`QProgressBar(0,0)` はオフスクリーンでもアニメーションする

検証方法: `ThemeManager.apply()` で各テーマの QSS を適用 →
`setRange(0,0)` のバーを表示 → 400ms 間隔で3回 `grab()` して
QImage を比較（差分あり = アニメーション動作）。
`#copyProgress` の QSS が当たる場合と素の QProgressBar の両方を検証。

**結果: 10 テーマすべて、QSS 有無どちらでも差分あり（アニメーション動作）。**

| テーマ | QSS(#copyProgress) | 素のバー |
|---|---|---|
| dark / light / nord / solarized_light / solarized_dark / dracula / gruvbox_dark / one_dark / monokai / high_contrast | 動作 | 動作 |

さらに dark / light でフレームを PNG 保存して目視確認したところ、
チャンク（accent 色）が**左→右へ移動し右端で周回するマーキー動作**で、
FEAT §3.5 の「左から右へ流れる」要件と方向が一致する。

### 4.2 採用提案

FEAT §3.5 の分岐に従い、**QSS 方式を採用可**（自作ウィジェット不要）:

- 既存の `QProgressBar#copyProgress`（`theme.py:598-613`）と同様に、
  検索用の objectName（例: `#searchProgress`）で高さ 3px・borderless に
  スタイルする。chunk は既に `{t['accent']}` を使う流儀があり、
  テーマ切替時の色再取得は QSS 再適用で自動的に済む。
- 端のフェードは chunk の `qlineargradient` で近似可能（Phase 1 で調整）。
- 「SEARCHING 以外では停止・非表示」は `hide()` + `setRange(0,1)` 等の
  リセットで足りるが、§4 受け入れ基準の「タイマー/アニメーション残留なし」の
  検証手段が QSS 内蔵アニメには無いため、**受け入れ検証は「hide 後に
  フレーム差分が出ないこと」で代替**する（`QVariantAnimation.state()` は
  自作ウィジェット採用時のみ適用可能な基準）。
- 10 テーマの accent は `TOKENS`（`theme.py:82-`）で全テーマ定義済み。
  bg/surface とのコントラスト確認は Phase 1 の受け入れ検証で実施する。

---

## 5. パネル閉鎖経路（調査項目5）

検索パネルは `QDockWidget`（右エリア、遅延生成: `main_window.py:2165-2177`）。

| # | 経路 | 実装 | 現状の挙動 |
|---|---|---|---|
| 1 | Ctrl+F トグル | `QAction`（`main_window.py:1083`）→ `toggle_search()`（`main_window.py:2179-2195`）→ `search_dock.hide()` | 隠すだけ。**検索は走り続ける** |
| 2 | ドックタイトルバーの ✕ | QDockWidget 既定の Closable 機能（フック無し） | dock が hide されるだけ。**検索は走り続ける** |
| 3 | アプリ終了 | `MainWindow.closeEvent`（`main_window.py:2255-2260`）→ `cancel_search()` | worker を止めて合流 |

- **Esc にパネルを閉じる割り当ては無い**（grep 全数確認。Esc を使うのは
  preview_dialog のみ）。閉鎖経路は実質 1・2 の2つ。
- `SearchPanel.closeEvent`（`search_panel.py:389-391`）は dock の hide では
  **発火しない**（子ウィジェットに close は伝播しない）ため、アプリ内では
  実質デッドコード。§3.6 の「閉鎖時の暗黙リセット」は
  **`search_dock.visibilityChanged`（または dock の `closeEvent`）を
  フックする**のが確実で、経路 1・2 を単一実装で覆える。
  なお経路 1 は再表示時に `set_root(current_path)`（`main_window.py:2187`）を
  呼ぶため、「再度開いた時は IDLE・現在ディレクトリ追従」の後段は既存挙動で
  満たされる。

---

## 6. ゲート条件の判定

| ゲート条件 | 判定 | 根拠 |
|---|---|---|
| `set_root()` 以外の経路でルートが書き換わる | **非該当** | 書き換えは3呼び出し元のみ（§1.1）。worker はスナップショット渡しで都度取得はしていない。※`_on_hits_batch` の表示基準のみ live 参照（§1.3、Phase 1 で `_pinned_root` に寄せる軽微対応） |
| 検索ライフサイクルが SearchPanel の外で管理 | **非該当** | 開始/キャンセル/完了とも SearchPanel 内で完結。main_window はアプリ終了時に `cancel_search()` を呼ぶだけ（§5 #3） |
| インクリメンタル検索が状態機械と両立しない | **非該当** | 「cancel → 新検索」の直列フローで、SEARCHING→SEARCHING の再入も `start_search` 冒頭の cancel で吸収できる（§2.4） |

**→ 実装(Phase 1)に進める。ただし本レポートの承認を待つこと（FEAT §3）。**

---

## 7. Phase 1 への申し送り

1. **キャンセル時の完了通知の設計変更が必須**（§2.2）。現行は
   `finished_ok` を先に切断するため「キャンセル完了 → RESULTS 遷移」の
   契機が取れない。worker に世代 ID を持たせる等で「最新 worker の
   シグナルだけ受ける」形にし、切断に頼らない。
2. リーダー6関数への cancel 引数追加は表の推奨チェックポイント（§3.2）で
   数百 ms 上限を満たせる見込み。`may_contain_keyword` プレフィルタ内の
   チェックを忘れないこと。
3. 進行バーは QSS 方式（§4.2）。受け入れ基準6の「残留なし」検証は
   フレーム差分方式に読み替える。
4. i18n は `app/i18n.py` の `"key": {"ja": …, "en": …}` 形式に追記
   （既存キー例: `search_root:237` / `search_cancel_btn:240` /
   `search_running:256`）。
5. 既存テストは `search_btn.isEnabled()` を完了待ちに使っている
   （`tests/test_gui_search_smoke.py:45,61,84`）。状態機械導入後も
   このプロパティの意味（IDLE/RESULTS で有効）が保たれるため互換だが、
   全件パス確認時は既知 fail 6件と `config/default_project_settings.json` の
   書き換え副作用に留意（テスト運用の既知事項）。
