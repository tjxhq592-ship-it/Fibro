"""パレットの識別性・可読性の機械検証（tools/check_theme_palette.py）。

基準を満たさない配色をコミットしたらここで落ちる。将来テーマを足すとき
の品質ゲートでもある。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_theme_palette import (  # noqa: E402
    BG_DISTANCE_EXCEPTIONS,
    check_palette,
    ciede2000,
    contrast_ratio,
    delta_e,
    srgb_to_lab,
    verify_ciede2000_implementation,
)


class TestColorMath:
    """色計算そのものの検証。ここが狂うと判定結果が丸ごと信用できない。"""

    def test_ciede2000_matches_sharma_reference(self):
        """Sharma らの検証データと一致する（踏み外しどころの多い式なので必須）。"""
        got = ciede2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485))
        assert got == pytest.approx(2.0425, abs=1e-4)

    def test_ciede2000_self_check_passes(self):
        verify_ciede2000_implementation()  # 例外が出なければ OK

    def test_ciede2000_is_symmetric_and_zero_for_identity(self):
        assert delta_e("#3c6e9a", "#3c6e9a") == pytest.approx(0.0, abs=1e-9)
        assert delta_e("#3c6e9a", "#a97f21") == pytest.approx(
            delta_e("#a97f21", "#3c6e9a"), abs=1e-9)

    def test_lab_of_white_and_black(self):
        lw = srgb_to_lab("#ffffff")
        assert lw[0] == pytest.approx(100.0, abs=1e-3)
        assert (lw[1], lw[2]) == pytest.approx((0.0, 0.0), abs=1e-3)
        assert srgb_to_lab("#000000")[0] == pytest.approx(0.0, abs=1e-9)

    def test_contrast_ratio_extremes(self):
        """ガンマ展開を省くと 21.0 / 1.0 からずれる。"""
        assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
        assert contrast_ratio("#808080", "#808080") == pytest.approx(1.0)


class TestPalette:
    def test_palette_passes_all_criteria(self):
        fails = check_palette()
        assert not fails, "\n".join(["配色が基準を満たしていない:"] + fails)

    def test_bg_distance_exceptions_are_documented(self):
        """例外は理由を書いた場合のみ認める（黙って増やせないようにする）。"""
        for pair, reason in BG_DISTANCE_EXCEPTIONS.items():
            assert len(pair) == 2
            assert reason.strip(), f"{sorted(pair)} の例外理由が空"

    def test_detects_a_broken_palette(self):
        """検証が実際に落とせること（常に PASS を返す抜け殻でないこと）。"""
        from app.gui.theme import THEME_META, THEME_ORDER, TOKENS
        broken = {k: dict(v) for k, v in TOKENS.items()}
        broken["dark"]["text_sub"] = broken["dark"]["elevated"]  # 読めない
        fails = check_palette(broken, THEME_ORDER, THEME_META)
        assert any("dark.text_sub" in f for f in fails)
