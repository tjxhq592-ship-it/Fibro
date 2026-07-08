"""Word / PowerPoint / PDF 内容検索（FEAT_document_search）の検証テスト。

フィクスチャ生成: `tests/fixtures/gen_office_fixtures.py`（tmp_path に生成、
バイナリは commit しない）。docx/pptx は XML 直書き + zipfile 組み立て、
PDF はバイト列を静的に組み立てる（追加依存なし）。

位置ラベルは i18n 既定言語（ja）で検証する。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import gen_office_fixtures as gen  # noqa: E402

from app.engine.errors import ContentReadError  # noqa: E402
from app.engine.office_reader import (  # noqa: E402
    search_in_docx, search_in_pptx,
)
from app.engine.pdf_reader import search_in_pdf  # noqa: E402


# ---------------------------------------------------------------------------
# D1: Word (.docx)
# ---------------------------------------------------------------------------
class TestDocx:
    def test_hit_with_paragraph_number(self, tmp_path):
        p = gen.make_docx(tmp_path, [
            "最初の段落です", "second paragraph with TARGET", "三番目の段落"])
        assert list(search_in_docx(p, "TARGET")) == [
            ("段落 2", "second paragraph with TARGET")]
        assert list(search_in_docx(p, "三番目")) == [
            ("段落 3", "三番目の段落")]

    def test_case_insensitive_by_default(self, tmp_path):
        p = gen.make_docx(tmp_path, ["Hello Target"])
        assert list(search_in_docx(p, "target")) == [
            ("段落 1", "Hello Target")]
        assert list(search_in_docx(p, "target", case_sensitive=True)) == []

    def test_entities_and_ncr_unescaped(self, tmp_path):
        # F1 共通ヘルパー経由: &amp; と 10進 NCR がデコードされて一致する
        p = gen.make_docx_entities(tmp_path)
        assert list(search_in_docx(p, "R&D")) == [("段落 1", "R&D部門")]
        assert list(search_in_docx(p, "部門")) == [("段落 1", "R&D部門")]
        assert list(search_in_docx(p, "ENTITY_PLAIN_PARA")) == [
            ("段落 2", "ENTITY_PLAIN_PARA")]

    def test_snippet_is_first_200_chars(self, tmp_path):
        long_para = "あ" * 250 + " TARGET"
        p = gen.make_docx(tmp_path, [long_para])
        [(label, snippet)] = list(search_in_docx(p, "TARGET"))
        assert label == "段落 1"
        assert snippet == long_para[:200]

    def test_max_hits(self, tmp_path):
        p = gen.make_docx(tmp_path, [f"para {i} COMMON" for i in range(10)])
        assert len(list(search_in_docx(p, "COMMON", max_hits=3))) == 3

    def test_non_zip_raises(self, tmp_path):
        p = gen.make_fake_docx(tmp_path)
        with pytest.raises(ContentReadError):
            list(search_in_docx(p, "x"))

    def test_zip_without_document_xml_raises(self, tmp_path):
        p = tmp_path / "empty.docx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("dummy.txt", "not a document")
        with pytest.raises(ContentReadError):
            list(search_in_docx(p, "x"))


# ---------------------------------------------------------------------------
# D1: PowerPoint (.pptx)
# ---------------------------------------------------------------------------
class TestPptx:
    def test_hit_with_slide_number(self, tmp_path):
        p = gen.make_pptx(tmp_path, {
            1: ["表紙スライド"], 2: ["中身 TARGET あり"], 3: ["まとめ"]})
        assert list(search_in_pptx(p, "TARGET")) == [
            ("スライド 2", "中身 TARGET あり")]

    def test_slides_scanned_in_numeric_order(self, tmp_path):
        # 文字列ソートだと slide10 < slide2 になる。数値順を検証する。
        p = gen.make_pptx(tmp_path, {
            2: ["two COMMON"], 10: ["ten COMMON"], 1: ["one COMMON"]})
        labels = [label for label, _s in search_in_pptx(p, "COMMON")]
        assert labels == ["スライド 1", "スライド 2", "スライド 10"]

    def test_entities_unescaped(self, tmp_path):
        # ビルダーは & < > をエンティティにエンコードして書く →
        # リーダーの unescape で元に戻って一致する（F1 共通ヘルパー経由）
        p = gen.make_pptx(tmp_path, {1: ["R&D部門 <重要>"]})
        assert list(search_in_pptx(p, "R&D")) == [
            ("スライド 1", "R&D部門 <重要>")]
        assert list(search_in_pptx(p, "<重要>")) == [
            ("スライド 1", "R&D部門 <重要>")]

    def test_max_hits_across_slides(self, tmp_path):
        p = gen.make_pptx(tmp_path,
                          {i: [f"slide {i} COMMON"] for i in range(1, 8)})
        assert len(list(search_in_pptx(p, "COMMON", max_hits=5))) == 5

    def test_non_zip_raises(self, tmp_path):
        p = gen.make_fake_pptx(tmp_path)
        with pytest.raises(ContentReadError):
            list(search_in_pptx(p, "x"))

    def test_zip_without_presentation_xml_raises(self, tmp_path):
        p = tmp_path / "empty.pptx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("dummy.txt", "not a presentation")
        with pytest.raises(ContentReadError):
            list(search_in_pptx(p, "x"))

    def test_zero_slides_is_not_an_error(self, tmp_path):
        # presentation.xml はあるがスライドが無い＝正当な空プレゼン
        p = gen.make_pptx(tmp_path, {})
        assert list(search_in_pptx(p, "x")) == []


# ---------------------------------------------------------------------------
# D2: PDF (.pdf)
# ---------------------------------------------------------------------------
class TestPdf:
    def test_hit_with_page_number(self, tmp_path):
        p = gen.make_pdf(tmp_path, [
            "first page nothing here",
            "second page has KEYWORD inside",
            "third page also has KEYWORD"])
        hits = list(search_in_pdf(p, "KEYWORD"))
        assert [label for label, _s in hits] == ["ページ 2", "ページ 3"]
        assert "KEYWORD" in hits[0][1]

    def test_case_insensitive_by_default(self, tmp_path):
        p = gen.make_pdf(tmp_path, ["Some Keyword Here"])
        assert len(list(search_in_pdf(p, "keyword"))) == 1
        assert list(search_in_pdf(p, "keyword", case_sensitive=True)) == []

    def test_whitespace_normalized(self, tmp_path):
        # 抽出で空白が崩れても連続空白1つに正規化して照合する。
        # Tj を2回に分けても extract_text が1行に繋ぐことを利用。
        p = gen.make_pdf(tmp_path, ["alpha  beta"])
        assert len(list(search_in_pdf(p, "alpha beta"))) == 1

    def test_textless_page_yields_nothing_without_error(self, tmp_path):
        # テキスト層なし（画像のみスキャンの代用: 空コンテンツストリーム）
        p = gen.make_pdf(tmp_path, [None, "page two TOKEN"])
        hits = list(search_in_pdf(p, "TOKEN"))
        assert hits == [("ページ 2", "page two TOKEN")]

    def test_all_pages_textless_is_zero_hits(self, tmp_path):
        p = gen.make_pdf(tmp_path, [None, None])
        assert list(search_in_pdf(p, "anything")) == []

    def test_max_hits(self, tmp_path):
        p = gen.make_pdf(tmp_path,
                         [f"page {i} COMMON" for i in range(1, 9)])
        assert len(list(search_in_pdf(p, "COMMON", max_hits=4))) == 4

    def test_broken_pdf_raises(self, tmp_path):
        p = gen.make_broken_pdf(tmp_path)
        with pytest.raises(ContentReadError):
            list(search_in_pdf(p, "x"))

    def test_snippet_is_around_hit(self, tmp_path):
        text = "x" * 150 + " NEEDLE " + "y" * 150
        p = gen.make_pdf(tmp_path, [text])
        [(label, snippet)] = list(search_in_pdf(p, "NEEDLE"))
        assert label == "ページ 1"
        assert "NEEDLE" in snippet
        assert len(snippet) <= 220  # 前後合計200文字程度
