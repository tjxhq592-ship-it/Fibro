# モーション&インタラクション リデザイン フェーズ0 調査結果

調査日: 2026-07-11 / 対象コミット: 4b66316

## 1. 既存アニメーションの棚卸し

`QPropertyAnimation` / `QVariantAnimation` / `QEasingCurve` / `QParallelAnimationGroup` /
`QSequentialAnimationGroup` / `QGraphicsOpacityEffect` で app/ 全体を grep した結果、
**該当ゼロ件**。Fibro には現在プロパティアニメーションが一切存在しない。

| 対象 | トリガー | duration | easing | 頻度分類 |
|---|---|---|---|---|
| （なし） | — | — | — | — |

時間ベースの UI 挙動は以下の2種のみ（いずれもアニメーションではなくタイマー）:

- `zoom.py _ZoomToast` — Ctrl+ホイール時の倍率表示。QTimer 1200ms で hide()。フェードなし・即時表示。
- `statusBar().showMessage(msg, 3000〜5000)` — main_window.py 内 約30箇所。完了/失敗/情報の全通知がここに集約されている。

**結論**: フェーズ2の「高頻度操作の既存アニメ削除」は削除対象なし。タブ/ペイン/
プロジェクト切替・ファイルナビゲーション・Space プレビュー（QDialog 即時表示）は
すべて既に即時であり、ポリシー準拠。

## 2. TOKENS 基盤の構造

- 定義: `app/gui/theme.py` の `TOKENS: dict[str, dict[str, str]]`（テーマ名 → 19キーのセマンティックトークン、10テーマ）。
- 消費: 同ファイルの `_palette(t)`（QPalette 生成）と `_stylesheet(t)`（QSS 生成、f-string 一関数）。
- `ThemeManager.apply()` が `app.setPalette` + `app.setStyleSheet` で全体適用。

**MOTION トークンの追加方法**: theme.py と同じ `app/gui/` 配下に `motion.py` を新設し、
クラス定数でトークン化する（TOKENS と同格の一元管理）。テーマとは独立（色ではないため
テーマ辞書には入れない）。

## 3. QSS 生成パイプライン（:pressed 挿入ポイント）

- `_stylesheet(t)` は全10テーマ共通の1関数なので、**ここに1回書けば全テーマに一括適用**される。
- 既存の押下スタイル: `QToolButton:pressed { background: sel_bg }` のみ。
  `QPushButton` は QSS 未定義（Fusion スタイル任せ）。タブ・collapsibleHeader・
  pathBox 内ボタンにも :pressed なし。
- pressed 専用色トークンは存在しない → `pressed_bg` を全10テーマに新設する
  （hover_bg と同系の rgba オーバーレイを一段深く）。

## 4. サイドバー・プレビュー・パネル開閉の実装方式

| UI | 実装 | 開閉方式 | 判定 |
|---|---|---|---|
| サイドバーセクション（お気に入り/履歴/場所/ツリー） | `collapsible.py CollapsibleSection`（縦 QSplitter 内のレイアウト内ウィジェット） | `_content.setVisible()` + sizePolicy/maximumHeight 切替（即時） | `maximumWidth` 連続アニメは**不使用** |
| プレビュー (Space) | `preview_dialog.py QuickPreviewDialog`（QDialog） | show()/close() 即時 | キーボード起動 → ポリシー上アニメ禁止。現状のまま維持 |
| サイドバー全体 | 常時表示（QSplitter 左ペイン）。表示切替機能なし | — | — |
| トースト | 汎用トーストは**存在しない**（statusBar 通知のみ） | — | フェーズ6は最小実装が必要 |

**フェーズ4の判定: スキップ**。`maximumWidth` / `setFixedWidth` の連続アニメは
どこにも存在せず、即時 `setVisible` 実装のパネルは「頻度の原則」によりそのまま維持で
よい、に該当する。

**サイドバー開閉アニメ（受け入れ基準3）の実装方針**: CollapsibleSection は
QSplitter 内のレイアウト内ウィジェットのため、高さの連続アニメは
「レイアウトプロパティをアニメしない」原則（禁止事項: width/height/geometry の
毎フレームアニメ）に抵触する。オーバーレイ化（move ベース）は splitter 内
セクションには適用できない。したがって:

- レイアウト変更（setVisible / sizePolicy）は従来どおり**即時1回**。
- 展開時に本体コンテンツを `QGraphicsOpacityEffect` のフェードイン
  （`MOTION.PANEL` / `EASE_OUT`）で表示する。折りたたみは即時（退出は入場より速く、
  かつ「畳む」操作の応答性を優先）。
- 開閉中の再クリックは**現在の不透明度から**新ターゲットへ再開（割り込み可能性）。

「220ms / OutQuint で現在位置から滑らかに反転」は不透明度軸で満たす。位置移動軸で
満たそうとするとレイアウトアニメ禁止と矛盾するため、原則側を優先した。

## 5. トースト/通知の現状

- 汎用通知 = `statusBar().showMessage(msg, ms)`（約30箇所）。分類なし・色分けなし・
  アニメなし。表示位置はウィンドウ下端のステータスバー左側。
- `zoom.py _ZoomToast` はズーム倍率専用のビューポート中央オーバーレイ（QLabel +
  QTimer）。汎用化されていない。

**フェーズ6の方針**: `app/gui/toast.py` を新設（最小実装）。

- MainWindow 上のオーバーレイ（レイアウト外・`move()` ベース）として下端中央に積む。
- 4分類: status=accent(info系) / completion=status_ok / warning=status_warn /
  error=status_error（テーマ TOKENS の色を使用）。
- 入場: 下端から translateY + フェード（`BASE` / `EASE_OUT`）。退出: 同方向へ `FAST`。
- 連続発生時の詰め直し・ホバー中のタイマー停止に対応。move() アニメは
  オーバーレイのため「レイアウトアニメ禁止」に抵触しない。
- 呼び出し側: MainWindow に `notify(kind, text)` を追加し、ファイル操作の
  完了/失敗系 statusBar 通知を段階的に移行。情報系（件数表示等）は statusBar 併存。

## 6. reduced motion の適用ポイント

- エントリポイントは `main.py main()`。QApplication 生成後・MainWindow 生成前に
  `motion.init_reduced_motion()` を呼ぶのが最小侵襲（GUI モジュール読み込み順の
  制約なし）。
- Windows 設定は `SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION=0x1042)` で取得。
  非 Windows / 取得失敗時はアニメ有効のまま（フェイルオープン）。

## 実装対象まとめ

| フェーズ | 内容 | 対象ファイル |
|---|---|---|
| 1 | MOTION トークン + make_animation | app/gui/motion.py（新設） |
| 2 | 削除対象なし（現状が既にポリシー準拠であることを本書で確認） | — |
| 3 | pressed_bg トークン新設 + QSS :pressed 一括追加 | app/gui/theme.py |
| 4 | スキップ（該当実装なし）。サイドバーはフェードで対応 | app/gui/collapsible.py |
| 5 | reduced motion 検出 | app/gui/motion.py, main.py |
| 6 | トースト最小実装 + 完了/失敗通知の移行 | app/gui/toast.py（新設）, main_window.py |
