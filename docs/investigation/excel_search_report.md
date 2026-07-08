# 調査レポート: Excel 内容検索のとりこぼし

- 対象: `SearchMode.EXCEL`（`app/engine/excel_reader.py` / `app/engine/search_engine.py`）
- 調査環境: Windows 11 / Python 3.14.5 / openpyxl 3.1.5 / xlrd 2.0.2
- 再現テスト: `tests/test_excel_search_investigation.py`
- フィクスチャ生成: `tests/fixtures/gen_excel_fixtures.py`
- 本体コード(`app/`)は**未変更**。実験はテストコード側で完結。

---

## 0. 結論サマリ

「セルにあるはずのキーワードがヒットしない」原因は**単一ではなく複数**あり、
`may_contain_keyword`（高速プレフィルタ）と `search_in_excel`（本走査）の
**両段で別々のとりこぼし**が起きる。実害の大きさ順は §3 のランキング参照。

| 仮説 | 判定 | 一言 |
|---|---|---|
| H1 エンティティ未デコード（`&` `<` `>`） | ✅ **成立** | プレフィルタが `R&amp;D` のまま照合し `R&D` を除外 |
| H2 表示書式文字列の不一致（日付/％/桁区切り） | ✅ **成立** | 表示 `2026/07/04`・`1,000`・`15%` は全滅。内部表現のみ一致 |
| H3 read_only の dimension 過信 | ✅ **成立** | dimension を狭く偽ると範囲外セルを落とす。`reset_dimensions()` で回復 |
| H4 数式キャッシュ無し（`data_only=True`） | ✅ **成立** | キャッシュ無し数式は None。結果も数式文字列も取れない |
| H5 開けないブックの silent skip | ✅ **成立** | 暗号化/破損/オンライン専用が無言で 0 件。`skipped` にも載らず診断不能 |
| H6 サイズ上限スキップの不可視 | ⚠️ **部分成立（仕様）** | 上限超過は `skipped` に計上されるが理由が UI で区別されない |

---

## 1. 仮説ごとの詳細（根拠：テスト名・観測）

### H1 — プレフィルタのエンティティ未デコード ✅ 成立

`may_contain_keyword` は worksheet XML からタグ(`<[^>]+>`)だけ除去して
本文照合するが、**XML エンティティを unescape していない**。openpyxl は
セル文字列をインライン文字列として次のように書く（実測）:

```xml
<c r="A1" t="inlineStr"><is><t>R&amp;D部門</t></is></c>
<c r="B1" t="inlineStr"><is><t>&lt;重要&gt;</t></is></c>
<c r="C1" t="inlineStr"><is><t>"引用"</t></is></c>
```

タグ除去後の本文は `R&amp;D部門` / `&lt;重要&gt;` / `"引用"`。

- `R&D` → 本文 `r&amp;d部門` に含まれず → **プレフィルタ False → 本走査に到達せず取りこぼし**
- `<重要>` → 本文 `&lt;重要&gt;` に含まれず → 同上、取りこぼし
- `"引用"` → 引用符は XML テキスト内で**非エスケープ** → **ヒットする**（対照群）
- `部門` / `重要` → エンティティを跨がない部分文字列 → ヒット

根拠テスト: `TestH1EntityPrefilter`, `TestPrefilterMatrix.test_entity_chars_falsely_filtered`

> 注: openpyxl 3.1.5 の既定出力は `sharedStrings.xml` を使わず**インライン文字列**。
> プレフィルタが worksheet XML も走査対象にしているため照合自体は届くが、
> エンティティ表記のせいで False になる。sharedStrings 版でも `&amp;` 等は同様。

### H2 — 表示書式文字列の不一致 ✅ 成立

本走査は `data_only=True` で開き `str(cell.value)` を照合する。日付は
`datetime`、パーセント/桁区切りは**素の数値**が返り、**画面表示の文字列は
どこにも現れない**。

| ユーザーが打つ語 | 実セル(表示) | 内部値 → `str()` | プレフィルタ | 本走査 | 結果 |
|---|---|---|---|---|---|
| `2026/07/04` | 2026/07/04 | `2026-07-04 00:00:00` | **False** | – | ✗ 取りこぼし |
| `1,000` | 1,000 | `1000` | True(数値扱い) | 不一致 | ✗ 取りこぼし |
| `15%` | 15% | `0.15` | **False** | – | ✗ 取りこぼし |
| `2026-07-04` | 2026/07/04 | `2026-07-04 00:00:00` | True | 一致 | ○ ヒット |
| `1000` | 1,000 | `1000` | True | 一致 | ○ ヒット |
| `0.15` | 15% | `0.15` | True | 一致 | ○ ヒット |

**表示どおりに打つと全滅。内部表現に寄せた時だけ当たる**という直感に反する挙動。
`1,000` は「数値だからプレフィルタは通過するが本走査で外れる」多段取りこぼし。

根拠テスト: `TestH2DisplayFormat`

### H3 — read_only の dimension 過信 ✅ 成立

`<dimension ref>` を実データ(E10)より狭い `A1:A1` に偽装すると、
read_only の `iter_rows(values_only=True)` は**宣言範囲に丸められ E10 を
返さない**。プレフィルタは worksheet XML に値が残るため True を返すのに
本走査で消える、という「XMLには在るのに見えない」型。

- `load_workbook(..., read_only=True)` 直後に **`ws.reset_dimensions()`** を
  呼ぶと範囲が再計算され、E10 を読めるようになる（実験で確認）。

根拠テスト: `TestH3BadDimension.test_out_of_dimension_cell_is_missed` /
`test_reset_dimensions_recovers_the_cell`

> 実データで dimension が壊れるのは、一部の生成ツール・古い保存経路・
> 手動編集された xlsx で起こり得る。頻度は低いが起きると全セル取りこぼし級。

### H4 — 数式キャッシュ無し ✅ 成立

`data_only=True` は「最後に Excel が計算しキャッシュした値」を返す。
openpyxl 等が**Excel を経由せず生成した数式セルはキャッシュが無く None**。

- `=CONCATENATE("HIT","KEY")` の結果 `HITKEY` → 本走査 None → 取りこぼし
- プレフィルタも False（XML には `HIT` と `KEY` が分断されて入るだけ）
- 参考: `data_only=False` なら数式文字列 `=CONCATENATE(...)` は読めるが、
  本走査は `data_only=True` 固定なので数式テキストの `CONCATENATE` すら
  ヒットしない

根拠テスト: `TestH4FormulaNoCache`

> Excel で一度開いて保存された数式はキャッシュを持つため当たる。影響を受けるのは
> 「ライブラリ生成直後」「Excel で未計算のまま保存」等のファイル。

### H5 — 開けないブックの silent skip ✅ 成立

`search_in_excel` は `except Exception: return`（88–89行）で、暗号化・破損・
OneDrive オンライン専用などを**無言で 0 件**にする。さらに `search_engine.py`
は Excel リーダの失敗を **`stats.skipped` に一切計上しない**（size/binary のみ計上）。
結果、ユーザにもログにも「開けなかった」痕跡が残らず、**「該当なし」と区別不能**。

根拠テスト: `TestH5SilentSkip.test_unopenable_file_yields_nothing_silently` /
`test_engine_does_not_count_skip`（`scanned=1, skipped=0`）

### H6 — サイズ上限スキップの不可視 ⚠️ 部分成立（仕様）

`max_file_size`（既定 50MB）超過は `stats.skipped += 1` される（仕様どおり）。
ただし **H5 と同じ `skipped` カウンタ**に混ざるため、UI では
「サイズ超過 / 読込失敗 / プレフィルタ除外」の区別ができない。

根拠テスト: `TestH6SizeLimit`（超過で `skipped=1`、範囲内で命中 `skipped=0`）

---

## 2. Step3 プレフィルタ網羅表（`may_contain_keyword`）

代表キーワード群に対する「実セルに存在するか」と「返り値」の対応。
`偽陰性` = 存在するのに False（＝取りこぼしの根本原因）。

| キーワード | 種別 | 実在するフィクスチャ | 期待 | 返り値 | 判定 |
|---|---|---|---|---|---|
| `INLINE_UNIQUE_TOKEN` | ASCII | inline_string | True | True | ○ |
| `inline_unique_token` | ASCII小文字 | inline_string | True | True | ○ |
| `部門` / `重要` | 日本語 | entity | True | True | ○ |
| `存在しない語` | 日本語 | – | False | False | ○ |
| `"引用"` | 引用符 | entity | True | True | ○ |
| `R&D` | `&` 含む | entity(R&D部門) | True | **False** | ✗ 偽陰性 |
| `<重要>` | `<>` 含む | entity | True | **False** | ✗ 偽陰性 |
| `123` `1.5` `-42` `1e5` | 数値 | (常に通す) | True | True | ○ |
| `1,000` | 数値+カンマ | – | True | True | ○※ |
| `100円` | 数値+記号 | – | False | False | ○ |
| `¥1,000` | 記号+数値 | – | False | False | ○ |
| `2026/07/04` | 日付表示 | formats(内部はシリアル値) | True相当 | **False** | ✗ 偽陰性 |

※ `1,000` はプレフィルタは通るが本走査で `str(1000)` と外れる（H2）。

`_NUMERIC_RE = ^[\d.,\-+eE]+$` は `1,000` を数値扱いで通すが、`100円`・`¥1,000`・
`2026/07/04` は記号混じりで文字列走査に落ち、内部表現と食い違えば False になる。

---

## 3. 「検索できていない」原因の寄与度ランキング（推定）

日常業務での遭遇頻度 × 影響範囲での主観ランク。

1. **H2 表示書式（日付・％・桁区切り）** — 最頻。日付や金額を「見たとおり」に
   打つのは自然で、そのほぼ全てが外れる。業務 Excel で最も刺さる。
2. **H1 エンティティ（`&` `<` `>`）** — 「R&D」「A&B」「<要確認>」等、記号入り
   ラベルは実在しやすく、プレフィルタ段で無言除外される。
3. **H5 開けないブックの silent skip** — 暗号化ブック/OneDrive オンライン専用/
   破損が混じると無言で欠落し、原因調査もできない（診断性ゼロが厄介）。
4. **H4 数式キャッシュ無し** — ライブラリ生成・未計算保存のファイル限定だが、
   該当すると数式結果が丸ごと見えない。
5. **H6 サイズ上限の不可視** — 取りこぼし自体は仕様。ただし理由不明の
   `skipped` として現れ、ユーザの誤解を生む。
6. **H3 dimension 過信** — 発生条件が限定的（壊れた dimension）で頻度は低いが、
   起きるとシート全体規模で落ちる。

---

## 4. 修正方針の提案（実装はしない・比較のみ）

### H1: エンティティの取り扱い
- **案A（推奨）**: プレフィルタで本文を `html.unescape`（または `xml.sax.saxutils`
  相当）してから照合。副作用が小さく、`&amp;`→`&` 等を正しく戻せる。
  コスト: 走査テキストごとに unescape 1回（正規表現より軽微）。
- 案B: キーワード側を `escape` して XML 表記に合わせる。`&`→`&amp;` は対応できるが、
  `<`/`>` のエンコード揺れ（`&gt;` 省略等）に弱く、案Aより脆い。
- → **案A を推奨**。

### H2: 表示書式
- **案A**: 仕様として「内部値を検索対象とする」と UI に明記（プレースホルダや
  ツールチップに「日付は 2026-07-04、％は 0.15 で検索」等）。実装コスト最小。
- 案B: 各セルの `number_format` を使って表示文字列を再構築し、内部値と表示の
  両方を照合対象にする。命中率は最大化するが、書式解釈の実装量・誤差・速度
  （read_only + data_only との整合）が重い。ロケール依存も増える。
- → まず**案A（明記）**、要望が強ければ限定書式（日付・％・桁区切り）だけ案B。

### H3: dimension
- `load_workbook(read_only=True)` 直後に全 worksheet へ `reset_dimensions()` を
  常時適用する案。**コスト影響**: reset_dimensions は「範囲情報を捨てて
  iter 時に実データから再計算」させるだけで追加 I/O はほぼ無い（既に全行を
  舐める本走査では実質ノーコスト）。安全側倒しとして常時適用が妥当。

### H4: 数式
- `data_only=True/False` の**二回開き**でキャッシュ値と数式文字列の両取り、は
  速度倍増で高コスト。代替として「data_only=True で None の数式セルは、
  必要時のみ data_only=False 値（数式文字列）を照合」する遅延フォールバック。
  ただし数式文字列一致はユーザ期待とズレやすい。まずは**現状維持＋ドキュメント**を推奨。

### H5 / H6: skipped の粒度と UI
- `SearchStats` に理由別カウンタを分離: `skipped_size` / `skipped_read_error`
  /（必要なら）`skipped_prefilter`。`search_in_excel` の `except` を
  呼び出し側へ通知できる形（例: 失敗を stats に記録するコールバック or 戻り値）に。
- UI（`search_panel.py`）で「N 件スキップ（サイズ超過 x / 読込失敗 y）」と内訳表示。
  暗号化・オンライン専用の取りこぼしが**可視化**され、原因調査が可能になる。

---

## 5. 新規テストの位置づけ

`tests/test_excel_search_investigation.py` は**調査フェーズ専用**。現状（バグを含む）
挙動を assert で固定し、原因を可視化している。実装フェーズでは各テストを
「あるべき挙動」への**反転テスト**として流用する想定（ファイル docstring に明記）。
フィクスチャは `tmp_path` に生成し、バイナリはリポジトリに commit しない
（生成器のみ commit・`tests/fixtures/*.xlsx` は gitignore）。

---

## 6. 対応状況（2026-07-08 実装フェーズ完了）

検証テスト: `tests/test_excel_search_fixes.py`（調査テストは反転・整理のうえ削除）

| 仮説 | 対応 | コミット / 備考 |
|---|---|---|
| H1 エンティティ未デコード | ✅ 修正 | `fix: プレフィルタで XML エンティティ/NCR をデコードして照合 (F1)` — `html.unescape` 適用。NCR（`&#37096;` / `&#x9928;`）も解決 |
| H2 表示書式の不一致 | 📝 仕様として明記 | `feat: Excel 検索が内部値対象である旨をツールチップに明記 (F4)` — 照合ロジックは変更せず、Excel モードのツールチップ（ja/en）で注記 |
| H3 dimension 過信 | ✅ 修正 | `fix: read_only 走査前に reset_dimensions を常時適用 (F2)` |
| H4 数式キャッシュ無し | 📝 現状維持（仕様） | 実装対応なし。`TestSpecH4FormulaNoCache` で仕様制限として固定 |
| H5 silent skip | ✅ 修正 | `fix: 開けないブックの silent skip を廃止し skipped を理由別に計上・表示 (F3)` — `ExcelReadError` 送出 + `skipped_read_error` 計上 |
| H6 スキップ理由の不可視 | ✅ 修正 | 同上 (F3) — `SearchStats` を理由別カウンタに分離し、UI 完了メッセージに内訳（サイズ超過・読込失敗・バイナリ）を表示 |
