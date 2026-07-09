"""検索キャンセル直後のパネル破棄がネイティブクラッシュしないことの回帰テスト。

調査レポート: docs/investigation/search_cancel_crash_report.md

修正前は Qt の qFatal（QThread: Destroyed while thread is still running）
→ abort でプロセスごと落ちていた（Windows では exit 0xC0000409）。再発時に
スイートを道連れにしないよう、子プロセスで実行し exit code で判定する。
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# pytest 非依存の最小再現。テスト test_search_panel_cancel と同じ流れ:
# start_search → 即 cancel_search → SearchPanel 破棄。
# ワーカーが確実に走行中になるよう、走査に数十 ms かかるツリーを用意する。
_CHILD_SCRIPT = """
import gc, os, sys, tempfile
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from app.gui.search_panel import SearchPanel

app = QApplication.instance() or QApplication([])
tmp = Path(tempfile.mkdtemp())
for i in range(20):
    d = tmp / f"d{i}"
    d.mkdir()
    for j in range(10):
        (d / f"f{j}.txt").write_text("x")

panel = SearchPanel()
panel.set_root(str(tmp))
panel.keyword_edit.setText("f")
panel.start_search()
panel.cancel_search()
assert panel._worker is None
# ここで SearchPanel の C++ デストラクタが子 QThread を走行中のまま破棄する
del panel
gc.collect()
print("SURVIVED", flush=True)
"""


def test_cancel_then_destroy_does_not_crash():
    env = dict(os.environ,
               QT_QPA_PLATFORM="offscreen",
               QT_FORCE_STDERR_LOGGING="1")  # qFatal を stderr に出す
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0 and "SURVIVED" in proc.stdout, (
        f"child process crashed: exit={proc.returncode} "
        f"(hex={proc.returncode & 0xFFFFFFFF:#010x})\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
