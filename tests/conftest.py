"""テスト全体の安全網（セーフティネット）。

`pytest tests/` が数分ハングしていた件（docs/investigation/pytest_hang_20260728.md）
への構造的対策。個別テストを直す前に、ここで「ハングしうる操作」と
「テスト間へ漏れる状態」を機械的に塞ぐ。

内容:
  1. モーダルブロック    — exec() 系を全て非ブロッキング化し、未対策の
                            モーダル呼び出しはその場で fail させる
  2. GUI/スレッド残留検出 — テストが作ったトップレベルウィジェットを確実に破棄し、
                            残留があれば次テストへ持ち越さず fail させる
  3. 待機のタイムアウト必須 — wait_until / wait_signal（既定 5000ms）
  4. 実環境からの隔離    — CONFIG_DIR / INDEX_DB / 単一インスタンス名を
                            テスト専用の場所へ向ける

注意: このファイルは Qt を import する前に QT_QPA_PLATFORM を設定する。
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

# --- Qt の import より前に実行する必要がある設定 -----------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# テストがユーザーの実 GUI 設定を読まないようにする
os.environ.setdefault("QT_SCALE_FACTOR", "1")

from PySide6.QtCore import (  # noqa: E402
    QDeadlineTimer, QEvent, QEventLoop, QThreadPool,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox,
)

# 待機の既定上限。無制限待ちは禁止（ハングの温床）。
DEFAULT_WAIT_MS = 5000

# テスト単位の残留として数えないスレッド。
# app.netpath は「返らない OS 呼び出し」を使い捨てのデーモンスレッドで包む。
# 諦めた後も OS 側の呼び出しは走り続けるため、テスト終了時に生きていても
# 設計どおりであり残留ではない（デーモンなのでプロセス終了も止めない）。
_PERSISTENT_THREAD_PREFIXES = ("ThreadPoolExecutor-", "fibro-netpath")


# =============================================================================
# QApplication（全テストで 1 つだけ）
# =============================================================================

@pytest.fixture(scope="session")
def qapp():
    """プロセス内で唯一の QApplication。

    各テストファイルが同名の fixture を定義している場合はそちらが優先されるが、
    どれも `QApplication.instance() or QApplication([])` なので実体は共有される。
    """
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session", autouse=True)
def _ensure_qapp():
    """GUI fixture を要求しないテストでも QApplication を必ず用意する。

    後始末フィクスチャが `QApplication.instance() is None` を気にせずに済む。
    """
    app = QApplication.instance() or QApplication([])
    yield app
    _drain_global_thread_pool()


def _drain_global_thread_pool() -> None:
    """セッション終了時に QThreadPool の実行中ジョブを合流させる。

    残したまま QApplication が壊れると、ジョブ側が触る C++ オブジェクトが
    先に消えてクラッシュしうる。上限付きで待ち、返らなければ諦める
    （無制限待ちはハングの温床なので禁止）。
    """
    QThreadPool.globalInstance().waitForDone(DEFAULT_WAIT_MS)


# =============================================================================
# 4.4 実環境からの隔離
# =============================================================================

@pytest.fixture(autouse=True)
def _isolate_real_environment(monkeypatch, tmp_path_factory):
    """テストが実ユーザー環境へ書き込まないようにする。

    実害が確認された 2 点を塞ぐ（docs/investigation/pytest_hang_20260728.md §5）:
      - CONFIG_DIR が未差し替えのテストがリポジトリ内 config/ を書き換えていた
      - 固定の単一インスタンス名により、テストが実行中の Fibro 本体へ
        パスを送信していた

    この fixture が `monkeypatch` を要求することで monkeypatch が最初に
    セットアップされる → undo は最後に走る。よって後段の後始末フィクスチャ
    （ウィンドウ破棄など）は差し替え済みの CONFIG_DIR のまま動作する。
    """
    # テスト自身の tmp_path の中には作らない（tmp_path の中身を検証する
    # テストを壊してしまうため）。専用の一時ディレクトリを毎回切る。
    config_dir = tmp_path_factory.mktemp("fibro_config")

    import app.paths as paths
    monkeypatch.setattr(paths, "CONFIG_DIR", config_dir, raising=False)
    monkeypatch.setattr(paths, "INDEX_DB", config_dir / "file_index.db",
                        raising=False)

    # モジュール level に束縛済みの参照も差し替える（from ... import した側）
    for mod_name, attr, value in (
        ("app.gui.main_window", "CONFIG_DIR", config_dir),
        ("app.gui.search_panel", "INDEX_DB", config_dir / "file_index.db"),
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, value, raising=False)

    # 単一インスタンス: 実行中の Fibro 本体へ接続しないよう名前を分離する
    try:
        from app import single_instance
    except Exception:  # noqa: BLE001
        pass
    else:
        unique = f"Fibro-Test-{os.getpid()}-{threading.get_ident()}"
        monkeypatch.setattr(single_instance, "SERVER_NAME", unique,
                            raising=False)

    # netpath の否定キャッシュはモジュール変数で、放っておくとテストを跨ぐ。
    # 前のテストが「到達不可」と諦めたパスを次のテストが引き継がないよう毎回捨てる。
    try:
        from app import netpath
    except Exception:  # noqa: BLE001
        pass
    else:
        netpath.clear_cache()

    yield config_dir


def pytest_configure(config):
    """マーカー登録。pyproject.toml と併せて --strict-markers に耐えるようにする。"""
    config.addinivalue_line("markers", "gui: GUI を生成するテスト")
    config.addinivalue_line("markers", "slow: 実行に時間がかかるテスト")


# =============================================================================
# 4.1 モーダルブロック
# =============================================================================

def _safe_button(default, *args):
    """StandardButton 群から「安全側」（No > Cancel > Close > Ok）を選ぶ。

    buttons 引数が渡されていなければ `default` を返す。
    """
    sb = QMessageBox.StandardButton
    buttons = None
    for a in args:
        if isinstance(a, sb):
            buttons = a
            break
    if buttons is None:
        return default
    for candidate in (sb.No, sb.Cancel, sb.Close, sb.Abort, sb.Ok):
        if buttons & candidate:
            return candidate
    return default


def _fail_on_modal(kind):
    def _blocked(self, *args, **kwargs):
        cls = type(self).__name__
        pytest.fail(
            f"モーダル {cls}.{kind}() がテスト中に呼ばれた。"
            f"テストはここでブロックする。\n"
            f"対処: ダイアログ生成/確認を注入可能にする"
            f"（confirm_provider / dialog_factory）か、"
            f"該当テストで {cls}.{kind} を明示的に差し替えること。",
            pytrace=True,
        )
    return _blocked


@pytest.fixture(autouse=True)
def _block_modal_dialogs(monkeypatch):
    """exec() 系を全て非ブロッキング化する。

    - QMessageBox / QFileDialog / QInputDialog の静的メソッドは「安全側」の
      戻り値（キャンセル / 空）を返す。テスト側で上書きすれば従来どおり使える。
    - 上記でカバーされない `QDialog.exec()` は即 fail させる。黙って通すと
      オフスクリーンでも実際にイベントループが回りハングするため。
    """
    sb = QMessageBox.StandardButton

    # --- QMessageBox 静的メソッド ---
    # 通知系（選択肢なし）は Ok、確認系は「安全側」を返す。
    for name, default in (("information", sb.Ok),
                          ("warning", sb.Ok),
                          ("critical", sb.Ok),
                          ("question", sb.No)):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(
                lambda *a, _d=default, **k: _safe_button(_d, *a[3:])),
            raising=False)
    monkeypatch.setattr(
        QMessageBox, "about", staticmethod(lambda *a, **k: None),
        raising=False)
    monkeypatch.setattr(
        QMessageBox, "aboutQt", staticmethod(lambda *a, **k: None),
        raising=False)

    # --- QFileDialog 静的メソッド（キャンセル相当を返す） ---
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: ("", "")), raising=False)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([], "")), raising=False)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: ("", "")), raising=False)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: ""), raising=False)

    # --- QInputDialog 静的メソッド（キャンセル相当を返す） ---
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("", False)), raising=False)
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText",
        staticmethod(lambda *a, **k: ("", False)), raising=False)
    monkeypatch.setattr(
        QInputDialog, "getItem",
        staticmethod(lambda *a, **k: ("", False)), raising=False)
    monkeypatch.setattr(
        QInputDialog, "getInt",
        staticmethod(lambda *a, **k: (0, False)), raising=False)
    monkeypatch.setattr(
        QInputDialog, "getDouble",
        staticmethod(lambda *a, **k: (0.0, False)), raising=False)

    # --- ブロックする exec() は即 fail ---
    monkeypatch.setattr(QDialog, "exec", _fail_on_modal("exec"),
                        raising=False)
    monkeypatch.setattr(QDialog, "exec_", _fail_on_modal("exec_"),
                        raising=False)

    # --- QMenu は差し替えが効かない（_close_popup_menus で塞ぐ） ---
    # QMenu.exec だけはクラス属性を差し替えても効かない。Shiboken が
    # インスタンス参照 `m.exec` で C++ の built-in を返すため、
    # `QMenu.exec is our_func` が True でも呼ばれるのは本物であり、
    # ネストしたイベントループに入ったまま返らない（＝ハング）。
    # QDialog / QMessageBox / QFileDialog にこの癖は無い。
    # 代わりにポップアップを閉じるタイマーで塞ぐ（_close_popup_menus）。

    # --- QDrag は「何も起きなかった」を返す ---
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QDrag
    except Exception:  # noqa: BLE001
        pass
    else:
        monkeypatch.setattr(
            QDrag, "exec",
            lambda self, *a, **k: Qt.DropAction.IgnoreAction, raising=False)
        monkeypatch.setattr(
            QDrag, "exec_",
            lambda self, *a, **k: Qt.DropAction.IgnoreAction, raising=False)

    yield


@pytest.fixture(autouse=True)
def _close_popup_menus():
    """開いてしまった QMenu を閉じ、テストを失敗させる（ハングさせない）。

    `menu.exec(pos)` はメニューを表示してネストしたイベントループへ入る。
    誰もクリックしないテスト環境では返らず、オフスクリーンでも同じ。
    `QMenu.exec` はクラス属性の差し替えが効かない（上記参照）ため、外から
    閉じるしかない。QApplication に紐付けた繰り返しタイマーはネストした
    ループの中でも回るので、そこで activePopupWidget() を掴んで close() する。

    黙って閉じると「メニューが出ていない」と区別できなくなるので、
    捕まえた分は teardown で fail させる。テスト側で意図してポップアップを
    確認したい場合は、対象モジュールの QMenu を継承で差し替えること
    （tests/test_panes_tabs_preview.py の _capture_popup が実例）。

    この fixture 自身を要求すると捕獲リストが得られる。検証済みのものを
    clear() すれば teardown の fail を取り下げられる（この番人のテスト用）。
    """
    from PySide6.QtCore import QTimer
    app = QApplication.instance()
    caught: list[str] = []

    def _sweep() -> None:
        popup = app.activePopupWidget()
        # QMenu 以外のポップアップ（コンボボックスのリスト等）は正常な表示
        # なので触らない。閉じるのはイベントループを止めるメニューだけ。
        if isinstance(popup, QMenu):
            title = popup.title() or popup.objectName() or ""
            actions = [a.text() for a in popup.actions() if not a.isSeparator()]
            caught.append(f"{title or type(popup).__name__}: {actions}")
            popup.close()

    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(_sweep)
    timer.start()
    try:
        yield caught
    finally:
        timer.stop()
        timer.timeout.disconnect(_sweep)
    if caught:
        pytest.fail(
            "モーダルな QMenu.exec() がテスト中に開かれた（閉じて続行した）。\n"
            "そのままだとネストしたイベントループでハングする。\n"
            + "\n".join(f"  - {c}" for c in caught),
            pytrace=False,
        )


# =============================================================================
# 4.2 GUI / スレッド残留検出
# =============================================================================

def dispose_widget(widget) -> None:
    """ウィジェットを「実際に」破棄する。

    close() だけでは Python 側の参照（self をキャプチャした lambda など）が
    残り、C++ オブジェクトも生き続ける。アプリ全体へ張った eventFilter を
    外し、親を切り、deleteLater した上で DeferredDelete を明示配送する。

    重要: QApplication.processEvents() は DeferredDelete を処理しない。
    sendPostedEvents(None, DeferredDelete) が必須。
    """
    app = QApplication.instance()
    if app is None:
        return
    try:
        app.removeEventFilter(widget)
        widget.close()
        widget.setParent(None)
        widget.deleteLater()
    except RuntimeError:
        # C++ 側が既に破棄済み（Shiboken の wrapper だけ残っている）
        return
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _drain_events(app, rounds: int = 5) -> None:
    """保留中のイベント・タイマーを流し切る。

    `QTimer.singleShot(ms, callable)` は receiver を持たないため、対象
    ウィジェットが破棄されても発火する。破棄後に発火すると削除済みの
    C++ オブジェクトへ触れて RuntimeError になるが、それを次のテストへ
    持ち越さないよう、ここで消化して握り潰す。
    （根本対処は singleShot に context を渡すこと。Phase 2 で修正済み。）
    """
    for _ in range(rounds):
        try:
            app.processEvents()
        except RuntimeError:
            continue
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _dispose_new_top_levels(app, baseline_ids: set[int]) -> list[str]:
    """テスト中に増えたトップレベルウィジェットを全て破棄する。

    破棄しきれずに残ったものの名前リストを返す。
    """
    # 破棄前に保留中のタイマーを消化しておく（破棄後の発火を減らす）
    _drain_events(app, rounds=2)

    for widget in list(app.topLevelWidgets()):
        if id(widget) in baseline_ids:
            continue
        dispose_widget(widget)

    # deleteLater の連鎖（子 → 親、QMenu など）を流し切る
    _drain_events(app, rounds=5)

    leftover = []
    for widget in list(app.topLevelWidgets()):
        if id(widget) in baseline_ids:
            continue
        try:
            leftover.append(f"{type(widget).__name__}({widget.objectName()!r})")
        except RuntimeError:
            pass
    return leftover


def _residual_threads(before: set[int]) -> list[str]:
    """テスト中に生まれ、終了後も生きている Python スレッドを返す。"""
    residual = []
    for t in threading.enumerate():
        if t.ident in before or not t.is_alive():
            continue
        if t.name.startswith(_PERSISTENT_THREAD_PREFIXES):
            # プロセス寿命のプール。セッション終了時にまとめて畳む。
            continue
        residual.append(f"{t.name}(daemon={t.daemon})")
    return residual


@pytest.fixture(autouse=True)
def _gui_and_thread_leak_guard(monkeypatch, request):
    """GUI とスレッドの残留を次のテストへ持ち越さない。

    ハングの主因は「テストが作った MainWindow が一切破棄されず蓄積し、
    setStyleSheet の再ポリッシュ × アプリ全体 eventFilter が O(N^2) に
    劣化する」ことだった。ここで毎回ゼロに戻す。

    `monkeypatch` を要求しているのは実環境隔離と同じ理由（順序制御）。
    後始末は CONFIG_DIR 差し替えが有効なうちに走る必要がある。
    """
    app = QApplication.instance()
    baseline = list(app.topLevelWidgets()) if app is not None else []
    baseline_ids = {id(w) for w in baseline}
    threads_before = {t.ident for t in threading.enumerate()}

    yield

    if app is None:
        return

    leftover = _dispose_new_top_levels(app, baseline_ids)

    pool = QThreadPool.globalInstance()
    pool_drained = pool.waitForDone(5000)
    app.processEvents()

    residual_threads = _residual_threads(threads_before)

    problems = []
    if leftover:
        problems.append(
            f"トップレベルウィジェットが {len(leftover)} 個残留: "
            f"{leftover[:5]}")
    if not pool_drained:
        problems.append(
            f"QThreadPool が 5000ms で空にならない "
            f"(active={pool.activeThreadCount()})")
    if residual_threads:
        problems.append(f"スレッドが残留: {residual_threads}")

    if problems:
        pytest.fail(
            "テスト後に状態が残留している（次テストへ持ち越さず失敗させる）:\n"
            + "\n".join(f"  - {p}" for p in problems),
            pytrace=False,
        )


# =============================================================================
# 4.3 待機は必ずタイムアウト付き
# =============================================================================

def wait_until(predicate, timeout_ms: int = DEFAULT_WAIT_MS,
               interval_ms: int = 10) -> bool:
    """predicate() が真になるまでイベントを回す。上限必須。

    生の `QEventLoop.exec()` を使わないこと。条件が来なければ必ず上限で抜ける。
    戻り値は最終的な predicate() の結果。
    """
    app = QApplication.instance()
    if app is None:
        return bool(predicate())
    deadline = QDeadlineTimer(int(timeout_ms))
    flags = QEventLoop.ProcessEventsFlag.AllEvents
    while not predicate():
        if deadline.hasExpired():
            return bool(predicate())
        # maxtime 付き processEvents。イベントが無ければ最大 interval_ms
        # 待つので、ビジーループにならず、かつ必ず上限で抜ける。
        app.processEvents(flags, max(1, int(interval_ms)))
        app.sendPostedEvents()
    return True


def process_events(duration_ms: int = 50) -> None:
    """指定時間だけイベントを回す（上限必須の sleep 代替）。"""
    wait_until(lambda: False, timeout_ms=duration_ms)


def wait_signal(signal, timeout_ms: int = DEFAULT_WAIT_MS) -> bool:
    """シグナルが 1 回発火するまで待つ。タイムアウトしたら False。"""
    fired = []
    signal.connect(lambda *a: fired.append(a))
    return wait_until(lambda: bool(fired), timeout_ms=timeout_ms)


@pytest.fixture
def wait_for():
    """`wait_until` をテストから使うための fixture。"""
    return wait_until


@pytest.fixture
def pump():
    """`process_events` をテストから使うための fixture。"""
    return process_events
