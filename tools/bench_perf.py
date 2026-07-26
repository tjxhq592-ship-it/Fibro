"""起動・population・タブ切替の性能ベンチ（修正前後比較用）。

計測項目（各3回、中央値を報告）:
  1. importtime : MainWindow import の累積時間（別プロセス -X importtime）
  2. startup    : QApplication生成 / テーマ適用 / MainWindow構築 / show の各wall。
                  併せて「構築後にもう一度 apply() した場合の再ポリッシュ時間」を
                  計測する（P3 が起動パスから取り除くコスト）。別プロセスで実行。
  3. population : 1万ファイルフォルダへ navigate し、全行ロード完了までの wall time
  4. jank       : population と並行して 50ms QTimer を回し、発火間隔の最大値
                  （= GUI スレッドの最長ブロック時間の近似）
  5. tabswitch  : ロード済み3タブを12回巡回する setCurrentIndex+processEvents

使い方（Windows/Linux 共通、既存 Fibro インスタンスに依存しない）:
  python tools/bench_perf.py                 # 全項目を3回計測して報告
  python tools/bench_perf.py --files 10000   # population 用フォルダの件数

計測は offscreen プラットフォームで行い、ユーザーの実 config には一切
触れない（一時ディレクトリへ隔離）。population 用フォルダはシステム一時
領域にキャッシュし再利用する。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# GUI を出さず再現性を確保するため offscreen を既定にする（環境変数で上書き可）。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 隔離した config ディレクトリ（親→子へ env で伝搬）。
_ENV_CONFIG = "FIBRO_BENCH_CONFIG"
_ENV_TABDIR = "FIBRO_BENCH_TABDIR"


def _isolate_config(config_dir: Path, tab_dir: Path) -> None:
    """app.paths.CONFIG_DIR を一時ディレクトリへ差し替え、実 config を汚さない。

    main_window など `from app.paths import CONFIG_DIR` する側より前に呼ぶこと。
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    import app.paths as paths
    paths.CONFIG_DIR = config_dir
    paths.INDEX_DB = config_dir / "file_index.db"
    # 復元タブを小さなフォルダに固定して構築時間を安定させる。
    (config_dir / "default_project_settings.json").write_text(
        json.dumps({"tabs": [str(tab_dir)], "theme": "light"}),
        encoding="utf-8")


def _ensure_data_dir(n_files: int) -> Path:
    """population 計測用に n_files 個の空ファイルを持つフォルダを用意（キャッシュ）。"""
    d = Path(tempfile.gettempdir()) / f"fibro_bench_data_{n_files}"
    d.mkdir(parents=True, exist_ok=True)
    have = sum(1 for _ in os.scandir(d))
    for i in range(have, n_files):
        (d / f"file_{i:06d}.txt").touch()
    return d


def _small_dir(name: str, n: int = 20) -> Path:
    d = Path(tempfile.gettempdir()) / f"fibro_bench_small_{name}"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"{name}_{i:03d}.txt").touch()
    return d


# ── 1. importtime ───────────────────────────────────────────────────────────

def measure_importtime() -> float:
    """別プロセスで MainWindow import の累積ミリ秒を返す。"""
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c",
         "from app.gui.main_window import MainWindow"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    cumulative_us = 0
    # 各行: "import time:  self [us] | cumulative [us] | imported package"
    for line in proc.stderr.splitlines():
        if line.rstrip().endswith("app.gui.main_window"):
            parts = line.split("|")
            if len(parts) >= 2:
                try:
                    cumulative_us = int(parts[1].strip())
                except ValueError:
                    pass
    return cumulative_us / 1000.0


# ── 2. startup phases（子プロセスで1回計測） ─────────────────────────────────

def _child_startup() -> None:
    """子プロセス: 起動フェーズを計測し JSON を stdout に出す。"""
    config_dir = Path(os.environ[_ENV_CONFIG])
    tab_dir = Path(os.environ[_ENV_TABDIR])
    _isolate_config(config_dir, tab_dir)

    from PySide6.QtWidgets import QApplication
    from app.gui.theme import ThemeManager
    from app.models.project import ProjectManager
    from app.models.project_settings import ProjectSettingsStore
    import app.paths as paths

    t0 = time.perf_counter()
    app = QApplication([])
    t1 = time.perf_counter()

    # テーマ適用（本番 main.py と同じく構築前）
    tm = ThemeManager(paths.CONFIG_DIR / "settings.json")
    pm = ProjectManager(paths.CONFIG_DIR)
    sp = pm.store_paths(pm.active_project_id)["project_settings"]
    theme = ProjectSettingsStore(sp).get("theme", "light")
    tm.apply(app, theme)
    t2 = time.perf_counter()

    from app.gui.main_window import MainWindow
    window = MainWindow()
    t3 = time.perf_counter()

    window.show()
    app.processEvents()
    t4 = time.perf_counter()

    # P3 が起動パスから取り除く「再ポリッシュ」コスト = 構築後にもう一度 apply()。
    tm.apply(app, theme)
    app.processEvents()
    t5 = time.perf_counter()

    result = {
        "qapp_ms": (t1 - t0) * 1000,
        "theme_apply_ms": (t2 - t1) * 1000,
        "construct_ms": (t3 - t2) * 1000,
        "show_ms": (t4 - t3) * 1000,
        "repolish_ms": (t5 - t4) * 1000,
        "total_ms": (t4 - t0) * 1000,
    }
    print("BENCH_STARTUP_JSON:" + json.dumps(result))
    # closeEvent（保存）を走らせないため close せずに即終了する。
    sys.stdout.flush()   # os._exit はバッファをフラッシュしないため明示する
    os._exit(0)


def measure_startup(config_dir: Path, tab_dir: Path) -> dict:
    env = {**os.environ,
           _ENV_CONFIG: str(config_dir),
           _ENV_TABDIR: str(tab_dir),
           "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child-startup"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env)
    for line in proc.stdout.splitlines():
        if line.startswith("BENCH_STARTUP_JSON:"):
            return json.loads(line[len("BENCH_STARTUP_JSON:"):])
    raise RuntimeError(
        "startup child produced no result:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}")


# ── 3〜5. population / jank / tabswitch（親プロセス内で計測） ─────────────────

def _spin_until(app, predicate, timeout_s: float, on_tick=None) -> None:
    t0 = time.perf_counter()
    while not predicate() and time.perf_counter() - t0 < timeout_s:
        app.processEvents()
        if on_tick is not None:
            on_tick()


def measure_population(window, app, data_dir: Path, reset_dir: Path) -> tuple:
    """1回分の population / jank を計測する。

    直前イテレーションと同一 root だと QFileSystemModel が directoryLoaded を
    再発火しないため、まず reset_dir へ寄せてから data_dir をロード計測する。
    """
    from PySide6.QtCore import QTimer

    window.navigate(str(reset_dir))
    _spin_until(app, lambda: False, timeout_s=0.15)   # reset のロードを進める
    app.processEvents()

    pane = window._active_pane
    model = pane.list_model
    data_str = str(Path(data_dir))
    target_count = sum(1 for _ in os.scandir(data_dir))

    done = {"loaded": False}

    def on_loaded(p):
        if str(Path(p)) == data_str:
            done["loaded"] = True
    conn = model.directoryLoaded.connect(on_loaded)

    # jank 用 50ms タイマ（発火間隔の最大値 = GUI スレッド最長ブロックの近似）
    jank = {"last": None, "max_gap": 0.0}

    def on_jank():
        now = time.perf_counter()
        if jank["last"] is not None:
            gap = (now - jank["last"]) * 1000
            if gap > jank["max_gap"]:
                jank["max_gap"] = gap
        jank["last"] = now
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(on_jank)
    timer.start()

    t0 = time.perf_counter()
    window.navigate(data_str)
    root = model.index(data_str)
    _spin_until(
        app,
        lambda: done["loaded"] and model.rowCount(root) >= target_count,
        timeout_s=30)
    pop_ms = (time.perf_counter() - t0) * 1000
    timer.stop()
    model.directoryLoaded.disconnect(conn)
    return pop_ms, jank["max_gap"]


def measure_tabswitch(window, app) -> float:
    """ロード済みタブを12回巡回する setCurrentIndex+processEvents の中央値ms。"""
    n = window.tab_bar.count()
    switch_times = []
    for k in range(12):
        idx = k % n
        ts = time.perf_counter()
        window.tab_bar.setCurrentIndex(idx)
        app.processEvents()
        switch_times.append((time.perf_counter() - ts) * 1000)
    return statistics.median(switch_times)


def measure_loading_bar(window, app, fast_dir, slow_dir, reset_dir) -> dict:
    """読み込みバーの遅延表示正当性（D1）: 高速フォルダで出ず、低速で出るか。

    progress_bar.isVisible() をサンプリングして自動検証する。
    """
    bar = window._active_pane.progress_bar

    # 高速フォルダ（<150ms）: reset へ寄せてから fast へ。バーが一度も出ないこと。
    window.navigate(str(reset_dir))
    _spin_until(app, lambda: False, timeout_s=0.3)
    fast_shown = False
    window.navigate(str(fast_dir))
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.8:
        app.processEvents()
        if bar.isVisible():
            fast_shown = True

    # 低速フォルダ（>150ms）: reset へ寄せてから slow へ。バーが出ること。
    window.navigate(str(reset_dir))
    _spin_until(app, lambda: False, timeout_s=0.3)
    slow_shown = False
    window.navigate(str(slow_dir))
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.5 and not slow_shown:
        app.processEvents()
        if bar.isVisible():
            slow_shown = True
    _spin_until(app, lambda: not bar.isVisible(), timeout_s=6.0)  # settle
    return {"fast_bar_shown": fast_shown, "slow_bar_shown": slow_shown}


def measure_navigate_sync(window, app, target_dir, reset_dir) -> float:
    """navigate() の同期区間の wall time（D2）。

    is_dir・setRootPath・ツリー操作を含む navigate 呼び出しそのものの所要時間。
    D2 で is_dir(UNC のみ)とツリー展開を非同期化したため、この同期区間は
    短くなる（ローカルパスでは元々小さいが、深いパス/低速ドライブで効く）。
    ネットワークドライブ（切断）での効果は実機でのみ観測できる。
    """
    window.navigate(str(reset_dir))
    _spin_until(app, lambda: False, timeout_s=0.3)
    t0 = time.perf_counter()
    window.navigate(str(target_dir))   # 同期区間のみ計測（ロード完了は待たない）
    return (time.perf_counter() - t0) * 1000


# ── オーケストレーション ─────────────────────────────────────────────────────

def _median(vals):
    return statistics.median(vals) if vals else float("nan")


def run_all(n_files: int, repeats: int) -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="fibro_bench_cfg_"))
    tab_dir = _small_dir("tab", 20)
    data_dir = _ensure_data_dir(n_files)
    small_dirs = [_small_dir(f"s{i}", 30) for i in range(3)]

    print(f"platform={os.environ.get('QT_QPA_PLATFORM')} "
          f"python={sys.version.split()[0]} files={n_files} repeats={repeats}")
    print(f"data_dir={data_dir}")

    # 1. importtime
    imp = [measure_importtime() for _ in range(repeats)]

    # 2. startup（各回フレッシュな子プロセス）
    starts = []
    for i in range(repeats):
        cfg = tmp_root / f"cfg_start_{i}"
        starts.append(measure_startup(cfg, tab_dir))

    # 3〜5. runtime（親プロセスで window を作り、再 navigate で反復）
    _isolate_config(tmp_root / "cfg_runtime", tab_dir)
    from PySide6.QtWidgets import QApplication
    from app.gui.theme import ThemeManager
    from app.models.project import ProjectManager
    from app.models.project_settings import ProjectSettingsStore
    import app.paths as paths

    app = QApplication.instance() or QApplication([])
    tm = ThemeManager(paths.CONFIG_DIR / "settings.json")
    pm = ProjectManager(paths.CONFIG_DIR)
    sp = pm.store_paths(pm.active_project_id)["project_settings"]
    tm.apply(app, ProjectSettingsStore(sp).get("theme", "light"))
    from app.gui.main_window import MainWindow
    window = MainWindow()
    window.show()
    app.processEvents()

    # tabswitch 用に3タブをロード済みにする（tab0=構築時の1枚 + 2枚）。
    window.new_tab(str(small_dirs[0]))
    window.new_tab(str(small_dirs[1]))
    _spin_until(app, lambda: False, timeout_s=0.4)   # 各タブのロードを進める

    runs = []
    for i in range(repeats):
        pop_ms, jank_ms = measure_population(
            window, app, data_dir, small_dirs[i % len(small_dirs)])
        tab_ms = measure_tabswitch(window, app)
        runs.append({
            "population_ms": pop_ms,
            "jank_max_gap_ms": jank_ms,
            "tabswitch_ms_median": tab_ms,
        })

    # D1: 読み込みバーの遅延表示正当性（fast=非表示 / slow=表示）
    bar_ok = measure_loading_bar(window, app, small_dirs[0], data_dir,
                                 small_dirs[1])

    # D2: navigate() 同期区間の wall time（ローカル大フォルダ）
    nav_sync = [measure_navigate_sync(window, app, data_dir,
                                      small_dirs[i % len(small_dirs)])
                for i in range(repeats)]

    # レポート
    def col(key):
        return _median([r[key] for r in starts])
    print("\n==== RESULT (median of {} runs) ====".format(repeats))
    print(f"importtime_cumulative_ms : {_median(imp):8.1f}")
    print(f"startup.qapp_ms          : {col('qapp_ms'):8.1f}")
    print(f"startup.theme_apply_ms   : {col('theme_apply_ms'):8.1f}")
    print(f"startup.construct_ms     : {col('construct_ms'):8.1f}")
    print(f"startup.show_ms          : {col('show_ms'):8.1f}")
    print(f"startup.total_ms         : {col('total_ms'):8.1f}")
    print(f"startup.repolish_ms(P3)  : {col('repolish_ms'):8.1f}"
          "   # 構築後 apply の再ポリッシュ=P3が起動パスから除くコスト")
    print(f"population_ms            : {_median([r['population_ms'] for r in runs]):8.1f}")
    print(f"jank_max_gap_ms          : {_median([r['jank_max_gap_ms'] for r in runs]):8.1f}")
    print(f"tabswitch_ms_median      : {_median([r['tabswitch_ms_median'] for r in runs]):8.1f}")
    print(f"loading_bar(D1)          : fast_shown={bar_ok['fast_bar_shown']} "
          f"slow_shown={bar_ok['slow_bar_shown']}  # 期待: fast=False, slow=True")
    print(f"navigate_sync_ms(D2)     : {_median(nav_sync):8.1f}"
          "   # navigate 同期区間（ローカル大フォルダ）")

    # 生データ（各回）も JSON で残す
    print("\nRAW " + json.dumps({
        "importtime_ms": imp,
        "startup": starts,
        "runtime": runs,
        "loading_bar": bar_ok,
        "navigate_sync_ms": nav_sync,
    }))
    sys.stdout.flush()   # os._exit はバッファをフラッシュしないため明示する
    os._exit(0)  # closeEvent（保存）を避けて即終了


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child-startup", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    if args.child_startup:
        _child_startup()
        return
    run_all(args.files, args.repeats)


if __name__ == "__main__":
    main()
