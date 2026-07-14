"""PDF のテキスト層検索。pypdf 使用（純 Python・オフライン動作）。

ページ単位で extract_text() し、全文を一括でメモリに載せない。
テキスト層が無いページ（画像のみのスキャン PDF）は空文字が返るだけで
正常動作（エラー扱いにしない）。
"""
from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from pathlib import Path

from app.engine.errors import ContentReadError
from app.i18n import _

# PDF は抽出時に空白・改行が崩れやすいため、連続空白を1つに正規化して照合する
_WS_RE = re.compile(r"\s+")

# pypdf は import に数十ms かかるため、PDF を実際に検索する時だけ
# 遅延ロードする（excel_reader と同方式・起動時間への影響ゼロ）。
_pypdf = None


def _load_pypdf():
    global _pypdf
    if _pypdf is None:
        import pypdf
        _pypdf = pypdf
    return _pypdf


def search_in_pdf(filepath: str | Path, keyword: str,
                  case_sensitive: bool = False,
                  max_hits: int = 100,
                  cancel: threading.Event | None = None
                  ) -> Iterator[tuple[str, str]]:
    """全ページを走査し (位置ラベル, スニペット) を逐次返す。

    位置ラベルは「ページ {n}」、スニペットはヒット位置の前後合計200文字
    程度。暗号化 PDF は空パスワード復号を1回試み、失敗したらパース例外と
    同様に ContentReadError を送出する（呼び出し側で skipped 計上）。
    cancel はページ単位で確認する（1ページの extract_text 途中は中断不可）。
    """
    needle = keyword if case_sensitive else keyword.lower()
    pypdf = _load_pypdf()
    try:
        reader = pypdf.PdfReader(str(filepath))
        if reader.is_encrypted:
            if reader.decrypt("") == pypdf.PasswordType.NOT_DECRYPTED:
                raise ContentReadError(f"encrypted pdf: {filepath}")
        num_pages = len(reader.pages)
    except ContentReadError:
        raise
    except Exception as e:  # 破損・非PDF・復号処理の失敗等
        raise ContentReadError(f"cannot open pdf: {filepath}") from e

    hits = 0
    for page_no in range(1, num_pages + 1):
        if cancel is not None and cancel.is_set():
            return
        try:
            raw = reader.pages[page_no - 1].extract_text() or ""
        except Exception as e:  # ページ単位のパース失敗
            raise ContentReadError(
                f"cannot parse pdf page {page_no}: {filepath}") from e
        text = _WS_RE.sub(" ", raw).strip()
        if not text:
            continue  # テキスト層なし（スキャンページ）は正常スキップ
        haystack = text if case_sensitive else text.lower()
        pos = haystack.find(needle)
        while pos != -1:
            start = max(0, pos - 100)
            snippet = text[start:pos + len(keyword) + 100]
            yield _("search_hit_page").format(n=page_no), snippet
            hits += 1
            if hits >= max_hits:
                return
            pos = haystack.find(needle, pos + len(needle))
