# 残件3件の切り分け（W3 Step 1〜4）

日付: 2026-07-29
対象:

- `tests/test_panes_tabs_preview.py::TestCombinedContextMenu::test_build_combined_items`
- `tests/test_panes_tabs_preview.py::TestToolbarRemovalAndShortcuts::test_theme_toggle_in_settings_menu`
- `tests/test_panes_tabs_preview.py::TestToolbarRemovalAndShortcuts::test_help_menu_present`

指示どおり、テストコードを読む前に UI の実態を先に確定させた。

---

## 1. UI の実態（Step 1）

`dist\Fibro\Fibro.exe` を起動したうえで、実 `MainWindow` を構築して
メニューをランタイムに列挙した（画面を目で追うより取りこぼしが無いため）。

### 1.1 メニューバー

```
menuBar().actions() = 0   （空。アプリにメニューバーは無い）
```

### 1.2 設定（トップバーの歯車ボタン `settings_btn` / tooltip "設定"）

```
'テーマ'
  'ライト' [x]   'ダーク' [ ]   'Nord' [ ]   'Solarized ライト' [ ]
  'Solarized ダーク' [ ]   'Dracula' [ ]   'Gruvbox ダーク' [ ]
  'One Dark' [ ]   'Monokai' [ ]   'ハイコントラスト' [ ]
'初期ディレクトリを設定…'
'場所を更新（ドライブ/クラウド）'
'言語'
  '日本語'   'English'
---
'標準フォルダのオーバーライド' [ ]
```

テーマは**存在し、機能している**（10 種の排他チェック式サブメニュー）。
ただし「トグル」ではなく「選択メニュー」。

### 1.3 ヘルプ（トップバーの `?` ボタン `help_btn` / tooltip "ヘルプ"）

```
'バグを報告…'
---
'Fibro について…'
```

ヘルプは**存在し、機能している**。ただしメニューバーではなくポップアップ。

### 1.4 統合コンテキストメニューの Fibro 項目

```python
win._build_combined_items(["<ファイルのパス>"])
# → ([{'type': 'action', 'key': 'fav', 'label': 'お気に入りに追加'}], {'fav': ...})
```

ファイルでも `fav` を返す。`app/gui/main_window.py` の docstring にも
「フォルダ・ファイルどちらも対象」と明記されている。

---

## 2. 判定（Step 2）

**3 件とも「テスト側の陳腐化」。プロダクション側の欠陥ではない。**

| テスト | 期待 | 実装 | 判定 |
|---|---|---|---|
| `test_help_menu_present` | `menuBar()` 配下に "ヘルプ" メニュー | `help_btn` のポップアップ | 陳腐化 |
| `test_theme_toggle_in_settings_menu` | `menuBar()` 配下に "テーマ" | `settings_btn` のポップアップ | 陳腐化 |
| `test_build_combined_items` | ファイルのみ選択なら `items == []` | ファイルでも `fav` を返す | 陳腐化 |

前 2 件は**機能ではなく到達経路**が変わった。テストは `menuBar()` を
覗いており、そこはもう空。3 件目は**仕様そのもの**が広がった
（お気に入り登録がフォルダ限定 → ファイルも可）。

---

## 3. 原因コミット（Step 3）

| テスト | 追加 | 壊したコミット | 日付 |
|---|---|---|---|
| `test_theme_toggle_in_settings_menu` | `081dd97` (06-15) | `ff2ed7e` / `7eb269f` "Ver.1.5.0" | 2026-06-21 |
| `test_help_menu_present` | `d77bb28` (06-17) | `ff2ed7e` / `7eb269f` "Ver.1.5.0" | 2026-06-21 |
| `test_build_combined_items` | `2371677` (06-17) | `18c4169` "あ" | 2026-07-05 |

### 3.1 意図的な変更であることの根拠

`ff2ed7e` はメニューバーを丸ごと削除している（"ファイル"／"選択"／"表示"
／"設定" の 4 メニュー）。移動先は同コミットで新設されたトップバーの
歯車・`?` ボタン。コード上にも意図が残っている:

[app/gui/main_window.py:1261](../../app/gui/main_window.py#L1261)
> 設定はトップバーの歯車アイコン（settings_btn → _show_settings_menu）に移動。

`18c4169` は docstring ごと書き換えている（差分そのもの）:

```diff
-        シェルの「プロパティ」の直上に挿入される。フォルダ選択時のみ。
+        シェルの「プロパティ」の直上に挿入される。フォルダ・ファイルどちらも対象。
-        dirs = [p for p in paths if Path(p).is_dir()]
-        fav_target = dirs[0] if dirs else None
+        fav_target = paths[0] if paths else None
```

同コミットは `_build_fibro_menu` 側にも同じ変更を入れており、
「フォルダ・ファイルどちらも先頭の選択項目をお気に入りに登録できる」
というコメントを追加している。取りこぼしではなく意図的な仕様拡張。

### 3.2 Phase 2 の変更は無関係（実測で確認）

`closeEvent` / `QTimer.singleShot` 3 引数化 / `jobs.py` 移行の関与を疑って
検証した。Phase 0 直前の `222d76a` を worktree に取り出し、同じ 3 件だけを
実行した結果:

```
3 failed in 1.55s
E  AssertionError: assert 'ヘルプ' in {}
```

**Phase 0 以前から同じ assertion で同じように落ちている。**
Phase 2 の変更は関与していない。原因コミットは 6/21 と 7/5 であり、
Phase 0 着手（7/28）より前。

---

## 4. 修正方針と影響範囲（Step 4）

いずれも**プロダクションは直さない**。テストを現在の到達経路・仕様に
合わせて書き換える。指示書の原則どおり、根拠コミット SHA を上に記載した。

**assert を消す／条件を緩めることはしない。** 検証する内容は同じか、
むしろ強くする。

### 4.1 `test_theme_toggle_in_settings_menu`

`menuBar()` ではなく `_show_settings_menu()` が構築するメニューを検証する。
「'テーマ' が在る」だけでなく、サブメニューに 10 テーマが並び、現在テーマが
排他チェックされていることまで見る。

### 4.2 `test_help_menu_present`

同様に `_show_help_menu()` の構築結果を検証する。項目は
'バグを報告…' と 'Fibro について…'。ハードコードしたラベル一致ではなく、
i18n キー（`menu_report_bug` / `menu_about`）から引いて突き合わせる。

### 4.3 `test_build_combined_items`

「ファイルのみなら空」を「ファイルでもフォルダでも先頭の選択項目が
`fav` になる」へ改める。`fav_target` が `paths[0]` であることまで確認する
（現行テストはキーの有無しか見ておらず、対象パスを検証していない）。

### 4.4 影響範囲

`tests/test_panes_tabs_preview.py` のみ。プロダクションコードは触らない。
`.exe` の再ビルドも不要。

---

## 5. 副産物: conftest のモーダル抑止に穴がある（別件）

上記の UI 列挙を書いている最中に踏んだ。**`QMenu.exec` だけは
クラス属性の差し替えが効かない。**

```
QDialog      class-attr-set=True  instance-resolves-to-python=True
QMessageBox  class-attr-set=True  instance-resolves-to-python=True
QFileDialog  class-attr-set=True  instance-resolves-to-python=True
QMenu        class-attr-set=True  instance-resolves-to-python=False
             repr=<built-in method exec of PySide6.QtWidgets.QMenu object>
```

`QMenu.exec = f` はクラス辞書には入るが、インスタンスからの参照は C++ の
built-in のまま。つまり
[tests/conftest.py:242-245](../../tests/conftest.py#L242-L245) の

```python
monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: None, raising=False)
```

は**無効**で、実コードの `menu.exec(pos)` に到達したテストは offscreen でも
ネストしたイベントループに入ったまま返らない。実際に
`_show_settings_menu()` を素で呼ぶと 90 秒の faulthandler タイムアウトまで
固まることを確認した。

既存テストが緑なのは、この経路に到達するテストが今は無いからにすぎない。
`test_combined_fallback_to_fibro_menu`（同ファイル 358 行）が
`_NoExecMenu(QMenu)` という継承版の回避策を自前で持っており、過去に同じ壁に
当たった形跡がある。

ハングの再発源として残っているので、W5 の掃除に含めて塞ぐ（継承による
差し替え、もしくはポップアップを強制的に閉じるタイマー）。
