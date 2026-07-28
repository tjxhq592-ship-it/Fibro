# 収集テスト数の前後比較（W1）

日付: 2026-07-29
目的: 「失敗 6 → 3」という Phase 2 の成果が、テストが収集・実行されなくなった
ことによる見かけ上の減少ではないことを確定させる。

比較対象:

- before: `222d76a`（= `2231b53~1`、Phase 0 の調査コミットの直前）
- after: `9c6268c`（Phase 2 完了時点の HEAD）

---

## 1. 手順

pytest 9 では `--collect-only -q` がファイル単位の件数サマリを出すようになり、
ノード ID の一覧が得られない。差分を取るため、収集後に全 ID を書き出すだけの
使い捨てプラグインを一時的に噛ませた（リポジトリには入れていない）。

```python
def pytest_collection_finish(session):
    with open(os.environ["IDSDUMP_OUT"], "w", encoding="utf-8") as f:
        for item in session.items:
            f.write(item.nodeid.replace("\\", "/") + "\n")
```

```
QT_QPA_PLATFORM=offscreen pytest tests/ --collect-only -q -p idsdump
```

before 側も同じプラグインで収集した（`git checkout 2231b53~1` → 収集 → 復帰）。
収集のみなので before でもハングしない。

---

## 2. 結果

| | 収集件数 |
|---|---|
| before (`222d76a`) | **398** |
| after (`9c6268c`) | **398** |

ノード ID の集合も**完全一致**（`Compare-Object` で差分ゼロ）。
実行結果 `3 failed, 394 passed, 1 skipped` = 398 とも一致する。

**判定: 合格。** 失敗の減少は、テストが収集されなくなったことによるものではない。

補足: before では `-q` 1 回で ID 一覧が出るのに対し、after では
`pyproject.toml` の `addopts = "-q --strict-markers"` が加算されて実質 `-qq` に
なるため、ファイル単位サマリになる。表示形式の差であって収集内容の差ではない。

---

## 3. Phase 2 で復旧した 3 件

| テスト ID | 効いた修正 |
|---|---|
| `tests/test_instance_and_shellmenu.py::TestSingleInstance::test_send_to_existing_false_when_no_server` | conftest §4.4 の実環境隔離 |
| `tests/test_instance_and_shellmenu.py::TestSingleInstance::test_server_receives_paths` | 同上 |
| `tests/test_instance_and_shellmenu.py::TestSingleInstance::test_empty_payload_emits_empty_list` | 同上 |

`app/single_instance.py` は固定名 `SERVER_NAME = "Fibro-SingleInstance"` を使うため、
ユーザーの実 Fibro が起動していると、テストが実アプリのサーバに接続してしまい
`try_send_to_existing()` が `True` を返す／自前サーバが listen できず
`received == []` になっていた（レポート `pytest_hang_20260728.md` §5）。

修正は [tests/conftest.py:119-127](../../tests/conftest.py#L119-L127)。テスト毎に
`SERVER_NAME` を `Fibro-Test-<pid>-<tid>` へ差し替える（コミット `263e618`）。
プロダクション側の挙動は変えていない。単体実行で 14 passed を確認。

これは「テストの期待値をプロダクションに合わせて書き換えた」たぐいの修正では
なく、テストが実環境へ漏れ出していた経路を塞いだもの。
