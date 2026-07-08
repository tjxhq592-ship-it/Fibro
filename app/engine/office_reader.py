"""Word (.docx/.docm) / PowerPoint (.pptx/.pptm) の本文検索。

python-docx / python-pptx には依存しない。Excel プレフィルタ（F1）と同じ
zip + XML 直読み方式で実装する（追加依存ゼロ・業務PC制約に適合）。
タグ除去 / unescape は _xmltext の共通ヘルパーを使う。
"""
from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from app.engine._xmltext import xml_to_text
from app.engine.errors import ContentReadError
from app.i18n import _

_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def search_in_docx(filepath: str | Path, keyword: str,
                   case_sensitive: bool = False,
                   max_hits: int = 100) -> Iterator[tuple[str, str]]:
    """word/document.xml を段落単位で検索し (位置ラベル, スニペット) を返す。

    ヘッダー/フッター/コメントはスコープ外。位置ラベルは「段落 {n}」、
    スニペットは該当段落の先頭200文字。
    開けない・必須エントリが無いファイルは ContentReadError を送出する
    （呼び出し側で skipped_read_error 計上）。
    """
    needle = keyword if case_sensitive else keyword.lower()
    try:
        with zipfile.ZipFile(filepath) as zf:
            data = zf.read("word/document.xml")
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        raise ContentReadError(f"cannot open document: {filepath}") from e
    # </w:p> を改行にして段落境界を保存 → 行番号 = 段落番号
    text = xml_to_text(data, paragraph_tag=b"</w:p>")
    hits = 0
    for idx, para in enumerate(text.split("\n"), start=1):
        haystack = para if case_sensitive else para.lower()
        if needle in haystack:
            yield _("search_hit_paragraph").format(n=idx), para[:200]
            hits += 1
            if hits >= max_hits:
                return


def search_in_pptx(filepath: str | Path, keyword: str,
                   case_sensitive: bool = False,
                   max_hits: int = 100) -> Iterator[tuple[str, str]]:
    """ppt/slides/slide*.xml を検索し (位置ラベル, スニペット) を返す。

    スライド番号はファイル名から取得し数値順に走査（slide10 は slide2 の
    後）。ノートはスコープ外。位置ラベルは「スライド {n}」。
    開けない・必須エントリが無いファイルは ContentReadError を送出する。
    """
    needle = keyword if case_sensitive else keyword.lower()
    hits = 0
    try:
        with zipfile.ZipFile(filepath) as zf:
            names = zf.namelist()
            if "ppt/presentation.xml" not in names:
                raise ContentReadError(
                    f"not a presentation: {filepath}")
            slides = sorted(
                (int(m.group(1)), name)
                for name in names if (m := _SLIDE_RE.match(name)))
            for number, name in slides:
                text = xml_to_text(zf.read(name), paragraph_tag=b"</a:p>")
                for para in text.split("\n"):
                    haystack = para if case_sensitive else para.lower()
                    if needle in haystack:
                        yield (_("search_hit_slide").format(n=number),
                               para[:200])
                        hits += 1
                        if hits >= max_hits:
                            return
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        raise ContentReadError(f"cannot open presentation: {filepath}") from e
