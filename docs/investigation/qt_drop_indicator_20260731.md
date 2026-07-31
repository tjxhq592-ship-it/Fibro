# Qt のドロップインジケータの制約（お気に入り D&D 位置指定の前調査）

日付: 2026-07-31
環境: PySide6 **6.11.1** / Qt **6.11.1** / Python 3.14.5 / Windows 11
（`QT_QPA_PLATFORM=offscreen`、`QT_SCALE_FACTOR=1`）

目的: お気に入りサイドバーへ「ドロップ位置の指定」と「インジケータ表示」を
入れるにあたり、Qt の組み込み機構にどこまで乗れるかを実測で確定させる。

`QDragEnterEvent` / `QDragMoveEvent` を合成してビューへ直接投げ、
`QAbstractItemView::dropIndicatorPosition()`（protected。派生クラスで公開）を
観測した。調査用スクリプトはリポジトリには入れていない。

---

## 0. 要約

| # | 内容 | 実装への帰結 |
|---|---|---|
| 1 | `InternalMove` は外部ドラッグを受理せず、位置も出さない | `DragDrop` へ変える必要がある |
| 2 | `setDropIndicatorShown(False)` にすると位置が **常に `OnViewport`** | **Qt の細線を消して位置だけ貰う、はできない** |
| 3 | Qt のバンド幅は 25% ではなく `clamp(round(h/5.5), 2, 12)` ≒ 18% | 自前判定なら好きな比率にできる |
| 4 | `autoExpandDelay` の既定は `-1`（無効） | ホバー自動展開は明示的に有効化が要る |
| 5 | `defaultDropAction()` の既定は `IgnoreAction`（モードで変わらない） | `DragDrop` では `startDrag` が Copy を提案するので明示設定が要る |

結論として、**位置判定も描画も自前でやる**のが唯一まっすぐな道になる。

---

## 1. 発見 1: `InternalMove` では外部ドラッグの位置が計算されない

項目（行高 12px、既定サイズヒント）の上端 / 中央 / 下端に
`text/uri-list` を持つ `QDragMoveEvent` を投げた結果。

| ドラッグドロップモード | 上端 | 中央 | 下端 | 受理 |
|---|---|---|---|---|
| `InternalMove`（変更前の実装） | OnItem | OnItem | OnItem | **False** |
| `DragDrop` | AboveItem | OnItem | BelowItem | False |
| `DragDrop` + `mimeTypes()` に `text/uri-list` | AboveItem | OnItem | BelowItem | **True** |

指示書 §1 の表と完全に一致した。

`InternalMove` はドラッグ元がそのビュー自身でない限り受理しない。
変更前の `dragMoveEvent` が `super()` を呼ばずに `acceptProposedAction()` して
いたのは、この拒否を迂回するために必要だったもので、副作用として
Qt の位置計算ごと失われていた。

**ただし本実装では `mimeTypes()` を上書きしない。** 3 行目の「受理 True」は
Qt の位置計算に乗る場合に必要なだけで、こちらは `dragMoveEvent` /
`dropEvent` を自前で受理するため不要。むしろ入れると Qt の内部モデルが
`uri-list` を横取りしうる。

---

## 2. 発見 2: `setDropIndicatorShown(False)` にすると位置が取れなくなる

行高 24px、`DragDrop` で `showDropIndicator` を振った結果。

| `mimeTypes()` に uri-list | showDropIndicator | 上端 | 中央 | 下端 | 受理 |
|---|---|---|---|---|---|
| あり | True | AboveItem | OnItem | BelowItem | True |
| あり | **False** | **OnViewport** | **OnViewport** | **OnViewport** | True |
| なし | True | AboveItem | OnItem | BelowItem | False |
| なし | False | AboveItem | OnItem | BelowItem | False |

指示書 §1 の発見 2 は再現した。**受理される場合、位置は常に `OnViewport`
になる。**

4 行目だけ値が残って見えるのは「取れている」のではない。Qt の
`dragMoveEvent` は次の構造になっている:

```cpp
event->ignore();
QModelIndex index = indexAt(pos);
if (!droppingOnItself(event, index) && canDrop(event)) {   // ← canDrop 内の
    if (index.isValid() && showDropIndicator) {            //   dropOn() が
        dropIndicatorPosition = position(pos, rect, index);//   位置を先に書く
    } else {
        dropIndicatorPosition = OnViewport;                // ← ここで潰される
    }
    ...
}
```

`canDrop()` は内部で `dropOn()` を呼び、その中で `dropIndicatorPosition` を
`showDropIndicator` と無関係に一度書く。受理されない場合（3・4 行目）は
外側の `if` に入らないため、その途中結果が読めてしまうだけで、
イベント自体は拒否されている＝ドロップできない。受理される経路
（2 行目）では必ず `OnViewport` で上書きされる。

したがって **「Qt の細線インジケータを消し、`dropIndicatorPosition()` は
使って自前の太いバーを描く」という方法は成立しない。** 片方を取ると
片方が死ぬ。この事実は Qt の文書に明記がない。

**次に D&D を触る人へ:** `dropIndicatorPosition()` が `OnViewport` しか
返さないときは、まず `showDropIndicator()` を疑うこと。

---

## 3. 発見 3: Qt のバンド判定は 25% ではない

`DragDrop` + uri-list、`showDropIndicator=True` で、1 行の上端から下端まで
y を 1px ずつ掃引した結果。

行高 24px:

| 上端からの相対 y | 幅 | 位置 |
|---|---|---|
| 0〜3 | 4px (16.7%) | AboveItem |
| 4〜19 | 16px (66.7%) | OnItem |
| 20〜23 | 4px (16.7%) | BelowItem |

行高 40px:

| 上端からの相対 y | 幅 | 位置 |
|---|---|---|
| 0〜6 | 7px (17.5%) | AboveItem |
| 7〜32 | 26px (65.0%) | OnItem |
| 33〜39 | 7px (17.5%) | BelowItem |

`QAbstractItemViewPrivate::position()` の
`margin = qBound(2, qRound(qreal(rect.height()) / 5.5), 12)` と一致する
（24/5.5 = 4.36 → 4、40/5.5 = 7.27 → 7）。

指示書は Qt のバンドを「上下 25%」と書いているが、実際は **約 18%**、
しかも行高 66px 以上では 12px 固定に張り付く。どちらにせよ自前判定
（上下 25% 固定、かつ葉には "into" を作らない）とは別物であり、
「自前でやる」という結論は変わらない。

空白領域（項目のない場所）は `OnViewport` / `itemAt()` は `None` を返す。
自前判定でも同じくトップ階層の末尾として扱えばよい。

---

## 4. 発見 4・5: 組み込み機能の既定値

素の `QTreeWidget` で読んだ値。

| プロパティ | 既定値 | 備考 |
|---|---|---|
| `autoExpandDelay()` | **-1** | 無効。ホバー自動展開は明示的に有効化が必要 |
| `hasAutoScroll()` | True | 端に近づいたときのスクロールは既に効く |
| `autoScrollMargin()` | 16 | 同上 |
| `indentation()` | 20 | インジケータの左インデント算出に使う |
| `defaultDropAction()` | `IgnoreAction` | `setDragDropMode()` では変化しない |

`defaultDropAction()` がモードで変わらないのは実測どおりだが、
**効いてくるのは `QAbstractItemView::startDrag()` の中の分岐**である
（以下は Qt ソースの読み取り。実測ではない）:

```cpp
if (defaultDropAction() != Qt::IgnoreAction && (supportedActions & defaultDropAction()))
    defaultDropAction = d->defaultDropAction;
else if (supportedActions & Qt::CopyAction && dragDropMode() != QAbstractItemView::InternalMove)
    defaultDropAction = Qt::CopyAction;      // ← DragDrop だとここに落ちる
```

`IgnoreAction` のままだと、`InternalMove` では Move が提案されるのに
`DragDrop` へ変えた途端 **Copy が提案される**。よって `DragDrop` に
するなら `setDefaultDropAction(Qt.DropAction.MoveAction)` が必要になる。

---

## 5. 実装方針への帰結

1. `setDragDropMode(DragDrop)` — 外部ドラッグを受理できるようにする（発見 1）
2. `setDefaultDropAction(MoveAction)` — Copy 提案を打ち消す（発見 5）
3. `setDropIndicatorShown(False)` + **位置判定も描画も自前** — 発見 2 より、
   Qt の位置計算と自前描画は併用できないため、両方こちらで持つ
4. `mimeTypes()` は **上書きしない** — Qt の位置計算に乗らないので不要であり、
   入れると内部モデルが `uri-list` を横取りしうる
5. `setAutoExpandDelay(700)` — ホバー自動展開（発見 4）
6. `dropEvent` は内部・外部とも自前処理し `super().dropEvent()` を呼ばない。
   `QTreeWidgetItem` を Qt に動かさせないことで、グループを自分の子孫へ
   落とすといった破綻経路が構造的に消える

判定を純粋関数（`_drop_target(point) -> DropTarget | None`）に切り出せば、
描画を伴わずに単体テストで固定できる。発見 2 の罠には回帰ゲート
（`showDropIndicator()` が False かつ `dropIndicatorPosition()` を参照して
いないこと）を置いて、将来踏み直さないようにする。
