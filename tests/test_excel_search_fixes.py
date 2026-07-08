"""Excel 内容検索「とりこぼし」修正の検証テスト。

`tests/test_excel_search_investigation.py`（調査フェーズ・削除済み）の
反転テスト。調査レポート `docs/investigation/excel_search_report.md` の
仮説 H1 / H3 / H5 / H6 が修正され「あるべき挙動」になったことを固定する。

H2（表示書式）/ H4（数式キャッシュ無し）は**既知の仕様制限**として
現状挙動を固定する（実装対応はしない。H2 は UI ツールチップで明記）。

フィクスチャ生成: `tests/fixtures/gen_excel_fixtures.py`（tmp_path に生成、
バイナリは commit しない）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import gen_excel_fixtures as gen  # noqa: E402

from app.engine.excel_reader import (  # noqa: E402
    ExcelReadError, may_contain_keyword, search_in_excel,
)
from app.engine.search_engine import (  # noqa: E402
    SearchMode, SearchOptions, SearchStats, search,
)


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    """全フィクスチャを一時ディレクトリに生成（gitignore 対象を汚さない）。"""
    d = tmp_path_factory.mktemp("excel_fixtures")
    return gen.generate_all(d)


def _hits(path, keyword):
    return list(search_in_excel(path, keyword))


# ---------------------------------------------------------------------------
# F1 (H1): プレフィルタがエンティティ/NCR をデコードして照合し、
#          &/</> を含むキーワードも取りこぼさない
# ---------------------------------------------------------------------------
class TestF1EntityPrefilter:
    """3種の XML 表現（openpyxl 出力 / 名前付きエンティティ直組み /
    NCR 直組み）すべてでプレフィルタ True かつ本走査ヒットすること。"""

    FIXTURES = ["entity", "entity_named", "entity_ncr"]

    @pytest.mark.parametrize("fixture", FIXTURES)
    @pytest.mark.parametrize("kw", ["R&D", "<重要>", '"引用"', "部門"])
    def test_prefilter_passes(self, fx, fixture, kw):
        assert may_contain_keyword(fx[fixture], kw) is True

    @pytest.mark.parametrize("fixture", FIXTURES)
    def test_full_scan_hits(self, fx, fixture):
        p = fx[fixture]
        assert _hits(p, "R&D") == [("Sheet", "A1", "R&D部門")]
        assert _hits(p, "<重要>") == [("Sheet", "B1", "<重要>")]
        assert _hits(p, '"引用"') == [("Sheet", "C1", '"引用"')]
        assert _hits(p, "部門") == [("Sheet", "A1", "R&D部門")]

    def test_absent_keyword_still_filtered(self, fx):
        # unescape 導入後も「存在しない語は False」の保証は維持される
        assert may_contain_keyword(fx["entity"], "存在しない語") is False


# ---------------------------------------------------------------------------
# F2 (H3): reset_dimensions 常時適用により、dimension 宣言が実データより
#          狭いブックでも範囲外セルを取りこぼさない
# ---------------------------------------------------------------------------
class TestF2BadDimension:
    def test_out_of_dimension_cell_is_found(self, fx):
        # dimension を A1:A1 に偽装したブックの E10 が本走査でヒットする
        p = fx["bad_dimension"]
        assert may_contain_keyword(p, "TARGET_OUT_OF_DIM") is True
        assert _hits(p, "TARGET_OUT_OF_DIM") == [
            ("Sheet", "E10", "TARGET_OUT_OF_DIM")]


# ---------------------------------------------------------------------------
# F3 (H5): 開けないブックは silent skip せず ExcelReadError を送出し、
#          エンジンが skipped_read_error に計上する
# ---------------------------------------------------------------------------
class TestF3ReadError:
    def test_unopenable_file_raises(self, fx):
        p = fx["fake_encrypted"]
        # プレフィルタは BadZipFile を握りつぶし True（本走査に委ねる）
        assert may_contain_keyword(p, "x") is True
        # load_workbook 失敗は例外として呼び出し側に届く
        with pytest.raises(ExcelReadError):
            _hits(p, "x")

    def test_engine_counts_read_error(self, fx, tmp_path):
        # 非 zip の偽 .xlsx を含むディレクトリ検索で skipped_read_error 計上
        d = tmp_path / "tree"
        d.mkdir()
        (d / "broken.xlsx").write_bytes(b"not a zip")
        opts = SearchOptions(keyword="anything", modes={SearchMode.EXCEL})
        stats = SearchStats()
        hits = list(search(d, opts, stats=stats))
        assert hits == []
        assert stats.scanned == 1
        assert stats.skipped_read_error == 1
        assert stats.skipped == 1  # 合計 property でも見える


# ---------------------------------------------------------------------------
# F3 (H6): サイズ上限超過は skipped_size として理由別に計上される
# ---------------------------------------------------------------------------
class TestF3SizeLimit:
    def test_oversize_excel_counted_as_skipped_size(self, fx, tmp_path):
        d = tmp_path / "tree"
        d.mkdir()
        shutil.copy(fx["inline_string"], d / "big.xlsx")
        opts = SearchOptions(keyword="INLINE_UNIQUE_TOKEN",
                             modes={SearchMode.EXCEL}, max_file_size=10)
        stats = SearchStats()
        hits = list(search(d, opts, stats=stats))
        assert hits == []
        assert stats.skipped_size == 1
        assert stats.skipped_read_error == 0  # 読込失敗とは区別される
        assert stats.skipped == 1

    def test_within_limit_excel_is_found(self, fx, tmp_path):
        d = tmp_path / "tree"
        d.mkdir()
        shutil.copy(fx["inline_string"], d / "ok.xlsx")
        opts = SearchOptions(keyword="INLINE_UNIQUE_TOKEN",
                             modes={SearchMode.EXCEL})  # 既定 50MB
        stats = SearchStats()
        hits = list(search(d, opts, stats=stats))
        assert len(hits) == 1
        assert hits[0].detail == "Sheet!A1: INLINE_UNIQUE_TOKEN"
        assert stats.skipped == 0


# ---------------------------------------------------------------------------
# H2（仕様制限として固定）: 表示書式文字列では一致しない。
# 内部値（str(datetime)/生数値）で検索する仕様。UI ツールチップに明記済み。
# ---------------------------------------------------------------------------
class TestSpecH2DisplayFormat:
    """仕様制限: 表示書式（日付/％/桁区切り）は検索対象にしない。"""

    @pytest.mark.parametrize("kw", ["2026/07/04", "1,000", "15%"])
    def test_display_format_does_not_match(self, fx, kw):
        assert _hits(fx["formats"], kw) == []

    def test_internal_repr_matches(self, fx):
        p = fx["formats"]
        assert _hits(p, "2026-07-04") == [
            ("Sheet", "A1", "2026-07-04 00:00:00")]
        assert _hits(p, "1000") == [("Sheet", "B1", "1000")]
        assert _hits(p, "0.15") == [("Sheet", "C1", "0.15")]


# ---------------------------------------------------------------------------
# H4（仕様制限として固定）: キャッシュ値のない数式セルは検索できない。
# data_only=True 固定のため、数式結果も数式文字列もヒットしない。
# ---------------------------------------------------------------------------
class TestSpecH4FormulaNoCache:
    """仕様制限: ライブラリ生成等でキャッシュ無しの数式セルは対象外。"""

    def test_formula_result_not_matched(self, fx):
        p = fx["formula_nocache"]
        assert _hits(p, "HITKEY") == []
        assert may_contain_keyword(p, "HITKEY") is False

    def test_formula_text_not_matched(self, fx):
        # 数式文字列はプレフィルタを通るが、本走査は data_only=True のため
        # 数式セルが None になりヒットしない（Excel で再保存すれば当たる）
        p = fx["formula_nocache"]
        assert may_contain_keyword(p, "CONCATENATE") is True
        assert _hits(p, "CONCATENATE") == []


# ---------------------------------------------------------------------------
# プレフィルタ網羅（修正後の真値表）
# ---------------------------------------------------------------------------
class TestPrefilterMatrix:
    def test_ascii_and_japanese_present(self, fx):
        p = fx["inline_string"]
        assert may_contain_keyword(p, "INLINE_UNIQUE_TOKEN") is True
        assert may_contain_keyword(p, "inline_unique_token") is True  # 小文字
        assert may_contain_keyword(p, "存在しない語") is False

    def test_numeric_keywords_always_pass(self, fx):
        # _NUMERIC_RE に一致 → 数値セルの可能性ありとして常に True
        p = fx["formats"]
        for kw in ["123", "1.5", "-42", "1e5", "1,000"]:
            assert may_contain_keyword(p, kw) is True

    def test_numeric_with_symbol_falls_to_string_scan(self, fx):
        # 数値+記号は文字列走査へ。実在しなければ False（仕様どおり）
        p = fx["formats"]
        assert may_contain_keyword(p, "100円") is False
        assert may_contain_keyword(p, "¥1,000") is False
        assert may_contain_keyword(p, "2026/07/04") is False  # 内部はシリアル値
