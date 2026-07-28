# pytest tests/ ハング 原因調査レポート（Phase 0）

調査日: 2026-07-28
対象: `pytest tests/` の全体実行が数分ハングする件
本レポートは**調査のみ**。修正コードは含まない（Phase 1 以降で対応）。

---

## 0. 結論（先に要点）

**ハングの正体は「無限待ち」ではなく、テスト間で `MainWindow` が一切破棄されずに
蓄積することによる二次関数的な性能劣化**である。カタログ分類は **F（テスト間の
状態汚染）が主因**。A（モーダル）と B（スレッド待ち）は**実測で否定された**。

- 全テストファイルは**単体では完走する**（無限ブロックは存在しない）。
- 一方、全体実行では live な `MainWindow` が **0 → 64 個**まで単調増加し、
  1 テストあたりの所要時間が **0.01 秒 → 40.8 秒**まで悪化する。
- `ThemeManager.apply()` が呼ぶ `app.setStyleSheet()` は**プロセス内の全ウィジェット
  ツリーを再ポリッシュする**ため、生存ウィンドウ数 N に比例して重くなる。さらに
  各 `MainWindow` は `app.installEventFilter(self)` を張ったままなので、
  ポリッシュ中に飛ぶ全イベントが **N 個の Python 製 eventFilter** を通過する。
  結果として全体コストが O(N²) となり、「ハング」に見える。

---

## 1. ハングしたテスト ID とスタック

タイムアウト（90 秒）で最初に停止したテスト:

```
tests/test_projects.py::TestProjectSwitch::test_copy_current_duplicates_data
```

faulthandler / pytest-timeout が採取したメインスレッドのスタック（末尾のみ抜粋）:

```
File "tests/test_projects.py", line 167, in test_copy_current_duplicates_data
    win._create_project("copy_current", "コピー版")
File "app/gui/main_window.py", line 1092, in _create_project
    self._switch_project(proj.id)
File "app/gui/main_window.py", line 1150, in _switch_project
    self._reload_all_project_scoped_ui()
File "app/gui/main_window.py", line 1174, in _reload_all_project_scoped_ui
    self.theme_manager.apply(
File "app/gui/theme.py", line 746, in apply
    app.setStyleSheet(_stylesheet(t))
File "app/gui/main_window.py", line 471, in eventFilter
    return super().eventFilter(obj, event)
File "Lib/enum.py", line 708, in __call__
    return cls.__new__(cls, value)
```

読み方: `setStyleSheet()` が同期的に配送するスタイル変更イベントが、
アプリ全体に張られた `MainWindow.eventFilter` を通っている。**再帰ではなく**、
この経路が生存ウィンドウ数だけ繰り返されることによる純粋な遅さである。

補足: ダンプ直後に `Windows fatal exception: access violation` が出るが、これは
pytest-timeout のダンプ用スレッドが Qt の処理中に割り込んだ二次的なクラッシュで、
一次原因ではない。

---

## 2. カタログ A〜G の該当判定

| 分類 | 判定 | 根拠 |
|---|---|---|
| A モーダル `exec()` | **該当せず** | 全テストが `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` を実行済み。ハング地点のスタックに `QDialog::exec` は存在しない。ただし予防としてガードは必要（Phase 1 §4.1） |
| B スレッド待ち | **該当せず（実測で否定）** | 計測中スレッド数は常に 3〜4 で一定、`QThreadPool.activeThreadCount()` は常に 0。`app/` 内に引数なし `.wait()` / `waitForDone()` は 1 件も無く、既存の `wait()` は全て `2000`/`3000` ms 付き |
| C シグナル待ちのタイムアウト無し | **該当せず** | テスト側の `_wait_for` は全て `elapsed < timeout_ms` で上限付き |
| D `processEvents` ループ | **軽微** | `qapp.processEvents()` の使用は多いが全て上限付きループ内。単独ではハングしない |
| E 終了時ブロック | **未確定（要再検証）** | タイムアウトでプロセスが強制終了したため、サマリ出力後の挙動は未観測。`app/netpath.py` の `_EXECUTOR`（モジュール level の `ThreadPoolExecutor`）が明示 shutdown されない点は要対処 |
| **F 状態汚染** | **主因** | live `MainWindow` が 0→64 に単調増加。§3 の実測表を参照 |
| G 実 FS / 実環境 | **該当（別系統の実害あり）** | §5 を参照。実行中の Fibro 本体へテストが接続していた／リポジトリ内の実 config をテストが書き換えていた |

---

## 3. 蓄積の実測（決定的証拠）

診断用 pytest プラグインで各テストの前後を計測（プラグインはリポジトリ外の
一時ファイルとして実行、コミットしない）。

`tops` = `QApplication.topLevelWidgets()` 数、`main` = 生存 `MainWindow` 数、
`thr` = `threading.enumerate()` 数。

ファイルが切り替わる時点のスナップショット:

| 進行 | tops | main | thr |
|---|---|---|---|
| test_design_features 開始 | 0 | 0 | 2 |
| test_dnd_and_incremental | 8 | 8 | 3 |
| test_gui_smoke | 15 | 15 | 4 |
| test_motion | 16 | 16 | 4 |
| test_panes_tabs_preview | 18 | 16 | 4 |
| test_productivity_features | 64 | 56 | 4 |
| test_projects | 68 | 63 | 4 |

所要時間ワースト（`main` = その時点の生存ウィンドウ数）:

| 所要 | main | テスト |
|---|---|---|
| 40.79s | 64 | test_projects.py::TestProjectSwitch::test_favorites_swap_on_switch |
| 6.10s | 63 | test_productivity_features.py::TestMainWindowFeatures::test_disk_usage_shown |
| 5.82s | 56 | test_panes_tabs_preview.py::TestTreeSyncCoalesce::test_consecutive_navigate_coalesces_tree_sync |
| 2.86s | 37 | test_panes_tabs_preview.py::TestToolbarRemovalAndShortcuts::test_no_scroll_button_gap |
| 1.43s | 26 | test_panes_tabs_preview.py::TestFavoritesAsyncReachability::test_unreachable_marked_async |

**`main` の増加と所要時間が明確に相関している。スレッド数は一定。**
これが「B ではなく F」の決定的な根拠である。

### 3.1 なぜ破棄されないか

`MainWindow` を掴んでいる参照を `gc.get_referrers` で追跡した結果、
**`main_window.py` 内の 9 個の lambda が `self` をキャプチャ**していた。

```
app/gui/main_window.py:525, 588, 651, 742, 921, 1205, 1206, 1220, 1241
```

これらは子ウィジェット／QAction のシグナルに接続されており、
C++ 側の接続が生きている限り Python 側の lambda も解放されない。
`window → 子 → 接続 → lambda → window` の循環が C++/Python 境界をまたぐため、
Python の gc では回収できない。

検証した破棄手順と結果:

| 手順 | 解放されたか |
|---|---|
| 何もしない | ✗ |
| `close()` のみ | ✗ |
| `removeEventFilter()` + `close()` + `deleteLater()` + `processEvents()` | ✗ |
| 上記 + `sendPostedEvents(None, QEvent.Type.DeferredDelete)` | **✓** |

**要点: `QApplication.processEvents()` は `DeferredDelete` を処理しない。**
`deleteLater()` を実際に効かせるには `sendPostedEvents(None, DeferredDelete)` が要る。
この手順で 6 回連続生成→破棄しても `topLevelWidgets()` は 0 のまま維持できることを
確認済み。

---

## 4. 順序依存の確認（加害 → 被害の対応）

全 22 ファイルを個別実行（`--timeout=60`）。

| ファイル | rc | 実時間 | 結果 |
|---|---|---|---|
| test_design_features.py | 0 | 3s | 14 passed |
| test_dnd_and_incremental.py | 0 | 3s | 7 passed |
| test_document_search.py | 0 | 2s | 27 passed |
| test_excel_search_fixes.py | 0 | 1s | 30 passed |
| test_favorites.py | 0 | 1s | 14 passed |
| test_file_ops.py | 0 | 0s | 12 passed |
| test_future_improvements.py | 0 | 1s | 17 passed |
| test_gui_search_smoke.py | 0 | 4s | 8 passed |
| test_gui_smoke.py | 0 | 1s | 4 passed |
| test_index_and_excel_ext.py | 0 | 1s | 17 passed |
| **test_instance_and_shellmenu.py** | 1 | 2s | **3 failed**, 11 passed |
| test_motion.py | 0 | 1s | 14 passed |
| **test_panes_tabs_preview.py** | 1 | 28s | **3 failed**, 55 passed |
| test_productivity_features.py | 0 | 3s | 21 passed |
| **test_projects.py** | 0 | **105s** | 27 passed, 1 skipped |
| test_rename_engine.py | 0 | 0s | 35 passed |
| test_rename_history.py | 0 | 1s | 6 passed |
| test_robustness.py | 0 | 6s | 20 passed |
| test_search_cancel_crash.py | 0 | 1s | 1 passed |
| test_search_engine.py | 0 | 1s | 16 passed |
| test_theme_and_polish.py | 0 | 1s | 28 passed |
| test_toast.py | 0 | 1s | 7 passed |

**加害 → 被害の対応表**

| 加害（ウィンドウを生成して捨てない） | 生成数 | 被害（後続で顕著に遅くなる） |
|---|---|---|
| test_dnd_and_incremental.py | 3 | — |
| test_future_improvements.py | 4 | — |
| test_gui_smoke.py | 2 | — |
| test_toast.py | 2 | — |
| test_design_features.py / test_robustness.py / test_panes_tabs_preview.py / test_productivity_features.py / test_projects.py | 各 1（ただしヘルパー経由でテスト毎に生成） | test_panes_tabs_preview → test_productivity_features → **test_projects**（最後段が最大の被害者） |

**特筆**: `test_projects.py` は**単体でも 105 秒**かかる。これは同ファイル内 27 テストが
それぞれ `_make_window()` で `MainWindow` を作り捨てるためで、ファイル内でも同じ蓄積が
起きている。つまり被害はファイル間だけでなくファイル内にも存在する。

---

## 5. 実環境汚染（カタログ G）— 別系統の重大問題

`test_instance_and_shellmenu.py` の 3 failed は**コードのバグではなく実環境依存**。

調査中、**ユーザーの実 Fibro アプリ（PID 25200）が起動していた**。
`app/single_instance.py` は固定名 `SERVER_NAME = "Fibro-SingleInstance"` を使うため:

- `test_send_to_existing_false_when_no_server` は `try_send_to_existing(["C:\\"])` が
  `False` を期待するが、**実アプリに接続してしまい `True`** を返す。
  → このテストは**実行中の Fibro に "C:\\" を送りつけ、実際にタブを開かせている**。
  さらに `AllowSetForegroundWindow(ASFW_ANY)` まで呼ぶ。
- `test_server_receives_paths` / `test_empty_payload_emits_empty_list` は、
  サーバ名が実アプリに占有されているため自前サーバが listen できず `received == []`。

**テストがユーザーの実アプリの状態を書き換えている。** Phase 1 §4.4 で
サーバ名をテスト毎にユニーク化する隔離が必須。

`test_panes_tabs_preview.py` の 3 failed（コンテキストメニュー／テーマ切替／ヘルプ
メニュー）は蓄積とは無関係の既存機能不整合。ハング対応とは別件として扱う。

### 5.1 テストがリポジトリ内の実 config を書き換えている

`app/paths.py:37` は非 frozen 実行時 `CONFIG_DIR` を**リポジトリ直下の `config/`**
に解決する。`app/gui/main_window.py:45` はこれを
`from app.paths import CONFIG_DIR` でモジュール level に束縛しているため、
テスト側が `mw.CONFIG_DIR` を monkeypatch しない限り、`MainWindow()` は
**リポジトリの実 config を読み書きする**。

該当テスト（`CONFIG_DIR` を差し替えずに `MainWindow()` を生成している）:

```
tests/test_gui_smoke.py:22   test_main_window_opens
tests/test_gui_smoke.py:30   test_navigation_history
```

実害の証拠 — 調査中の `git status`:

```diff
 M config/default_project_settings.json
-    "C:\\Users\\hiros\\AppData\\Local\\Temp\\pytest-of-hiros\\pytest-308\\test_navigation_history0"
+    "C:\\Users\\hiros\\AppData\\Local\\Temp\\pytest-of-hiros\\pytest-367\\test_navigation_history0"
```

差分の**両側**が pytest の一時ディレクトリを指している。すなわち
「テストが書き潰した内容が過去に一度コミットされてしまっている」。
`docs/INVESTIGATE_search_state.md:259` にも同ファイルが汚れる旨の記述があり、
以前から既知だが未対処のまま放置されている。

副作用として、テストはユーザーの実プロジェクト設定・お気に入り・最近使った項目を
読み込み、かつ上書きする。§5 の単一インスタンス問題と併せ、
**テスト実行がユーザーの実データを壊しうる状態**である。Phase 1 §4.4 の
実環境分離（`CONFIG_DIR` を無条件に `tmp_path` へ向ける autouse fixture）で
構造的に塞ぐ。

---

## 6. サマリ出力の有無 / offscreen 差分

- **サマリ出力の有無**: 出力されない。pytest-timeout（thread 方式）が
  タイムアウト時にプロセスごと落とすため、テスト結果サマリの前に終了する。
  したがって「カタログ E（サマリ後に終わらない）」とは**別現象**。
  E の可能性自体は、ハング解消後に改めて確認する必要がある（§2 参照）。
- **offscreen での挙動差**: **差は無い**。GUI 系 15 ファイルが
  モジュール先頭で `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` を
  実行しており、環境変数の有無にかかわらず常に offscreen で動作する。
  この事実は同時に「A（モーダル）が主因ではない」ことの傍証でもある。

---

## 7. 静的洗い出し

### 7.1 `exec()` / モーダル（`app/` 配下）

`QDialog.exec()`:
```
app/gui/conflict_dialog.py:62
app/gui/main_window.py:201, 981, 1070, 1156, 2047, 2491
```

`QMessageBox`（静的メソッド）:
```
app/gui/favorites_sidebar.py:262, 411
app/gui/main_window.py:1144, 1320, 1548, 1765, 1776, 1943, 1984, 2002,
                       2034, 2086, 2106, 2134, 2343, 2352, 2478, 2501, 2522
app/gui/project_dialog.py:100
app/gui/rename_dialog.py:292
```

`QInputDialog.getText`:
```
app/gui/favorites_sidebar.py:267, 301, 459, 467
app/gui/main_window.py:1078, 2076, 2093, 2157, 2207
app/gui/places_sidebar.py:252
app/gui/project_dialog.py:87
app/gui/rename_dialog.py:237
```

`QFileDialog.getExistingDirectory`:
```
app/gui/main_window.py:1341, 2238
```

> 現状これらはテストから到達していないが、ガードが無いので将来の追加テストで
> 即ハングしうる。Phase 1 §4.1 で塞ぐ。

### 7.2 引数なし `.wait()` / `waitForDone()`

**0 件**。`app/` 内の `wait()` は全てタイムアウト付き:
```
app/gui/search_panel.py:374   worker.wait(2000)
app/gui/main_window.py:1880   thread.wait(3000)
app/gui/main_window.py:2471   self._op_thread.wait(3000)
```

### 7.3 タイムアウト未指定の `waitSignal`

**0 件**（`pytest-qt` は未導入で `qtbot` 自体を使っていない）。
テスト側の待機は自前の `QEventLoop` ヘルパーで、いずれも上限付き:
```
tests/test_dnd_and_incremental.py:22-28   _wait_for(timeout_ms=5000)
tests/test_gui_search_smoke.py:25-31      _wait_for(timeout_ms=5000)
tests/test_future_improvements.py:25-27   _process_events(ms=300)
tests/test_productivity_features.py:186-192  インライン（上限 5000ms）
```

### 7.4 その他の注意点

- `app/netpath.py:15` — モジュール level の `ThreadPoolExecutor(max_workers=2)` が
  明示 shutdown されない。到達不能パスに当たるとワーカーが `os.path.isdir` の中で
  ブロックしたまま残り、ワーカー 2 本が塞がると以降の `reachable()` が
  直列化して 1 件あたり 2 秒ずつ消費する。カタログ E/B の潜在要因。
- `tests/` に `conftest.py` が**存在しない**。共通の後始末フックが一切無いことが
  今回の蓄積を許した構造的な原因。
- `pyproject.toml` / `pytest.ini` が**存在しない**。timeout 等の恒久設定も無い。

---

## 8. 環境不備（副次的発見）

初回実行時、以下 3 ファイルが `ModuleNotFoundError: No module named 'openpyxl'` で
**collection error** となり、`pytest tests/` が 1 テストも実行せず中断していた。

```
tests/test_excel_search_fixes.py
tests/test_index_and_excel_ext.py
tests/test_search_engine.py
```

`requirements.txt` には `openpyxl>=3.1` が記載済みで、単に未インストールだった。
`pip install -r requirements.txt` で解消（`openpyxl` / `pypdf` / `send2trash` /
`xlrd` / `xlwt` が不足していた）。以後の調査は全て解消後に実施。

---

## 9. Phase 2 で必要になる修正範囲の見積もり

| 対象 | ファイル | 規模 | 影響機能 |
|---|---|---|---|
| F: `MainWindow` の破棄手順 | `tests/conftest.py`（新規） | 中 | テスト基盤のみ。まず conftest 側で確実に破棄する |
| F: lambda の自己参照解消 | `app/gui/main_window.py`（9 箇所） | 中 | 本番は単一ウィンドウなので実害は無いが、`closeEvent` での接続解除／`removeEventFilter` を入れる |
| G: 単一インスタンスのサーバ名 | `app/single_instance.py` + `tests/conftest.py` | 小 | テスト時のみ名前をユニーク化。実アプリへの誤爆を止める（最優先） |
| G: `CONFIG_DIR` の実環境分離 | `tests/conftest.py`（新規） | 小 | 全テストで `CONFIG_DIR` を tmp へ強制。リポジトリ config の汚染を止める（最優先） |
| E: `netpath._EXECUTOR` | `app/netpath.py` | 小 | プロセス終了時の shutdown、`aboutToQuit` 連動 |
| 恒久設定 | `pyproject.toml`（新規） | 小 | timeout / faulthandler_timeout / markers |
| 既存 failure 6 件 | `test_panes_tabs_preview.py` 3 件は機能不整合、`test_instance_and_shellmenu.py` 3 件は G の解消で復旧見込み | 小〜中 | ハング対応とは別課題 |

---

## 10. 再現手順

```bash
pip install -r requirements.txt pytest-timeout py-spy

# ハング再現（90 秒でタイムアウト、test_projects で停止する）
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -v \
  --timeout=90 --timeout-method=thread \
  -o faulthandler_timeout=120 -p no:cacheprovider

# 単体では完走することの確認
python -m pytest tests/test_projects.py -q --timeout=120   # ≈105 秒
```
