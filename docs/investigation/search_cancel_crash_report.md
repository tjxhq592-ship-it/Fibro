# 調査レポート: 検索キャンセル時のネイティブクラッシュ（test_search_panel_cancel）

- 対象: `app/gui/search_panel.py`（`SearchPanel.cancel_search` / `SearchWorker`）
- 症状テスト: `tests/test_gui_search_smoke.py::test_search_panel_cancel`
- 調査環境: Windows 11 / Python 3.14.5 / pytest 9.0.3 / **PySide6 6.11.1** / QT_QPA_PLATFORM=offscreen
- 再現テスト: `tests/test_search_cancel_crash.py`（子プロセス方式・xfail で登録）
- 本体コード(`app/`)は**未変更**。検証パッチはすべて使い捨てスクリプト側で実施。

---

## 0. 結論サマリ

クラッシュの正体は Qt の qFatal:

```
QThread: Destroyed while thread '' is still running
```

**走行中の子 QThread（SearchWorker）が、親である SearchPanel の C++ デストラクタに
道連れで破棄される**ことが確定原因。abort によりプロセスごと落ちる
（Windows 実 exit code は **0xC0000409** = fastfail。bash 経由だと 127 に丸められる）。

第一容疑 H1 は「破棄が起きる」という点では**成立**だが、機構の細部が申告と異なる。
`self._worker = None` での Python 参照落ちは無関係（worker は `parent=self` で
生成されており、C++ 所有権は親にある）。破棄のトリガは **テスト関数 return 直後の
`panel` GC → QWidget 親子カスケード**である。

| 仮説 | 判定 | 一言 |
|---|---|---|
| H1 参照落ちで走行中 QThread が破棄 | ✅ **成立（機構を修正）** | 破棄は起きるが、トリガは `_worker = None` ではなく panel 破棄の親子カスケード |
| H2 offscreen 固有の破棄順序問題 | ❌ 棄却 | `QT_QPA_PLATFORM=windows`（ネイティブ）でも同一クラッシュ |
| H3 Python 3.14 GC 挙動との相互作用 | ❌ 棄却 | `gc.disable()`（循環GC無効）でも再現 = 参照カウント駆動の即時破棄。バージョン固有性なし |
| H4 deleteLater + イベントループ未回転 | ❌ 棄却（原因としては） | deleteLater は一度も実行されないが、それ自体は無害（リークするだけ）。クラッシュの有無を分けるのは「panel 破棄時点でスレッドが終了済みか」のみ |

---

## 1. クラッシュの正体を掴む（手順1）

### 1.1 観測の落とし穴が2つあった

1. **Qt のログが見えない**: offscreen + リダイレクト環境では qFatal メッセージが
   OutputDebugString 側に行き stderr に出ない。`QT_FORCE_STDERR_LOGGING=1` で捕捉できた。
2. **faulthandler は沈黙する**: `PYTHONFAULTHANDLER=1` でもスタックは出ない。
   qFatal → abort/fastfail は SEH 例外（アクセス違反等）ではないため
   faulthandler のハンドラを通らない。「traceback なしで途切れる」症状と整合。

### 1.2 証拠ログ

```
$ QT_FORCE_STDERR_LOGGING=1 python -u -m pytest tests/test_gui_search_smoke.py::test_search_panel_cancel -q
（stderr）
QThread: Destroyed while thread '' is still running
```

実 exit code（PowerShell で計測）:

```
exitcode=-1073740791 (hex=0xC0000409)   # STATUS_STACK_BUFFER_OVERRUN = abort/fastfail
```

bash/pytest ラッパー経由では 127 に写像される。pytest の stdout が空のまま途切れるのは
リダイレクト時のブロックバッファリングで、abort がフラッシュ前にプロセスを殺すため。

---

## 2. 最小再現（手順2）

pytest 非依存で再現した。骨子（全文は `tests/test_search_cancel_crash.py` の
`_CHILD_SCRIPT` と同等）:

```python
app = QApplication([])                 # QT_QPA_PLATFORM=offscreen
panel = SearchPanel()
panel.set_root(str(tmp))               # a.txt 1つのフォルダ
panel.keyword_edit.setText("a")
panel.start_search()                   # worker.isRunning() == True
panel.cancel_search()                  # ここまでは正常に return する
del panel                              # ★ ここでクラッシュ（qFatal → abort）
```

`cancel_search()` は正常に return し、`panel._worker is None` の assert も通る。
クラッシュは **`del panel`（＝pytest ではテスト関数 return によるフレーム破棄）の瞬間**。

---

## 3. 仮説検証（手順3）— 変種実験マトリクス

最小再現に検証パッチを1つずつ当てた結果（検証スクリプトは使い捨て、コミットせず）:

| 変種 | 内容 | 結果 |
|---|---|---|
| baseline | テストと同じ流れ | 💥 `del panel` でクラッシュ |
| wait | cancel 後 `worker.wait(2000)` → panel 破棄 | ✅ 生存（isRunning=False を確認後） |
| spin | cancel 後 `finished` までイベントループを回す → panel 破棄 | ✅ 生存 |
| keep_panel | panel を破棄せず 1 秒待って終了 | ✅ 生存 |
| **keepref** | **worker への Python 参照をリスト退避**したまま panel 破棄 | 💥 **クラッシュ** |
| **noparent** | worker を `parent=None` で生成 → cancel で最後の Python 参照が落ちる | 💥 クラッシュ（panel 破棄より**前**、参照落ちの時点） |

### 読み解き

- **keepref がクラッシュする**のが決定的。Python 参照を保持しても、C++ 側の
  QThread は panel の子である限り親のデストラクタで破棄される。
  → 指示書の修正候補にあった「参照をリストに退避」は**単独では直らない**
  （`setParent(None)` を併用しない限り）。
- **noparent は参照落ちの時点で即クラッシュ**。原 H1 の機構
  （Python 参照落ち → PySide が C++ を破棄）は「parent が無い場合」にのみ発動する。
  現実装は `SearchWorker(self._root, options, use_index, self)` と親付きなので、
  実際に効いているのは親子カスケードの方。
- wait / spin / keep_panel の生存は、いずれも「破棄時点でスレッド終了済み」を
  満たすため。破棄経路自体は同じ。

### H4 の補足（手順4: 終了経路と deleteLater）

- `SearchWorker._run_scan()` / `_run_indexed()` とも cancel イベントを
  走査ループの先頭（`_iter_files` の各エントリ）で確認しており、cancel 後の
  Python 側の残処理は数 ms で終わる。ただし **QThread の起動〜終了のオーバーヘッド
  込みで、cancel から `isRunning()==False` まで実測 9〜20 ms** かかる（3回計測:
  14.2 / 8.9 / 20.2 ms）。
- `cancel_search()` が張る `finished → deleteLater` は、テストのように
  イベントループが回らない限り**一度も実行されない**。ただしこれは
  「panel 生存中は worker C++ オブジェクトがリークし続ける」だけで、
  クラッシュとは無関係（wait 変種でも deleteLater は未実行のまま生存した）。

---

## 4. 確定原因とタイミング依存の説明

### 確定原因

1. `start_search()` が `SearchWorker(..., parent=self)` を生成して `start()`。
2. 直後の `cancel_search()` は cancel フラグを立てるだけで**スレッドに合流しない**。
3. テスト関数が return → `panel` の参照カウントが 0 → PySide6 が SearchPanel の
   C++ QWidget を即時破棄 → **QObject 親子カスケードで子 QThread も破棄**。
4. その時点でワーカースレッドはまだ走行中（cancel 後の終了に 9〜20 ms かかる）
   → Qt が `QThread: Destroyed while thread is still running` の qFatal → abort。

### タイミング依存（高負荷で PASS した理由）

- テスト側: cancel → panel 破棄は**マイクロ秒オーダー**。
- ワーカー側: cancel → スレッド終了は**約 9〜20 ms**。
- アイドル環境ではワーカーがほぼ確実にレースに負ける → クラッシュ。
- 高負荷環境ではメインスレッドが cancel 後にデスケジュールされ、その間に
  ワーカーが終了できることがある → 生存。観測事実（フルスイート高負荷時のみ PASS）
  と整合する。

### 実アプリへの影響

通常運用では SearchPanel はアプリ終了まで生存するため顕在化しにくいが、
**検索走行中にアプリを閉じる**と `closeEvent → cancel_search()` の後、
ウィジェットツリー破棄時に同一機構でクラッシュし得る（終了時クラッシュとして
ユーザーに見える）。テストだけの問題ではない。

---

## 5. 修正方針の提案（本タスクでは実装しない）

### 案A: `cancel_search()` でスレッドに合流する（推奨）

```python
if self._worker is not None:
    self._worker.cancel()
    ...disconnect...
    if not self._worker.wait(2000):   # 合流。実測 9〜20ms で返る
        pass  # タイムアウト時は最後の手段として下記案Bの退避にフォールバック等
    self._worker.deleteLater()
self._worker = None
```

- 長所: 破棄時点で「スレッド終了済み」が構造的に保証される。wait 変種で生存を実証済み。
  コード変更が cancel_search 内で完結。
- 短所: GUI スレッドが最大 wait 時間ブロックする。ただし cancel フラグは走査ループ
  先頭で即座に効くため実測 9〜20 ms（インデックス構築中の SQLite 書込など最悪ケース
  でも `index.build/update` が cancel を見る粒度に依存。タイムアウト値はそれを考慮
  して決める）。
- 備考: 連続インクリメンタル検索（`start_search` 冒頭の `cancel_search()`）で
  毎回 ~20ms 待ちが入る。デバウンス 400ms があるため体感影響はほぼ無い見込み。

### 案B: worker を親なし化 + 退避リスト + finished で刈り取り（非ブロッキング）

```python
# start_search: parent を渡さない
self._worker = SearchWorker(self._root, options, use_index)  # parent なし

# cancel_search:
w = self._worker
w.cancel()
...disconnect...
self._retired.append(w)                          # Python 参照で延命
w.finished.connect(lambda: self._reap(w))        # finished で除去+deleteLater
self._worker = None
```

- 長所: GUI を一切ブロックしない。
- 短所・注意:
  - **`parent=None` が必須**。親付きのまま退避しても panel 破棄カスケードで
    クラッシュする（keepref 実験で実証済み）。
  - イベントループが回らない文脈（テスト・アプリ即終了）では finished が
    配送されず、インタープリタ終了時に走行中のままなら結局同じクラッシュに
    なり得る → `closeEvent` では退避分も含めて `wait()` する補完が必要。
  - 実装が分散し、ゾンビ管理の状態が増える。

### 案C（テスト側の緩和・恒久対策にはならない）

`test_search_panel_cancel` で cancel 後に `_wait_for(lambda: not worker.isRunning())`
相当を挟めばテストは通るが、アプリ側の終了時クラッシュ（§4）は残るため
単独採用は不可。案A/Bの回帰テスト補助としてのみ有効。

### 推奨

**案A を基本**とする。cancel の実測収束が 9〜20 ms と短く、シンプルさと
「破棄時に走行中スレッドが存在しない」という構造的保証の価値が大きい。
インデックス構築キャンセルの収束が遅い場合に備え、`wait(タイムアウト)` 失敗時のみ
案Bの退避（`setParent(None)` + 参照保持）にフォールバックするハイブリッドが堅い。

---

## 6. 再現テスト

`tests/test_search_cancel_crash.py::test_cancel_then_destroy_does_not_crash`

- クラッシュはプロセスを殺すため、`subprocess` で子プロセスとして再現フローを実行し
  `exit code == 0` と生存マーカー出力を assert する方式。
- 現状はバグが存在するため **`xfail(strict=False)`** で登録
  （タイミング依存で稀に生き残るため strict にはしない）。
  修正後は xfail マーカーを外して回帰テストに昇格させること。
