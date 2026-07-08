"""zip 直読みした Office 系 XML からテキストを取り出す共通ヘルパー。

excel_reader のプレフィルタ（F1）と office_reader（Word/PowerPoint 本文検索）
で共用する。タグ除去 → UTF-8 デコード → XML エンティティ/NCR の unescape
という手順・挙動を 1 箇所に固定するためのモジュール。
"""
from __future__ import annotations

import html
import re

_TAG_RE = re.compile(rb"<[^>]+>")


def xml_to_text(data: bytes, paragraph_tag: bytes | None = None) -> str:
    """XML バイト列からタグを除去し、unescape 済みテキストを返す。

    paragraph_tag（例: b"</w:p>"）を渡すと、その閉じタグを改行に置換して
    からタグ除去する。段落/スライド内テキストの境界を改行として保存し、
    呼び出し側が行単位で位置番号を数えられるようにするため。
    """
    if paragraph_tag is not None:
        data = data.replace(paragraph_tag, b"\n")
    text = _TAG_RE.sub(b"", data).decode("utf-8", errors="replace")
    if "&" in text:
        # エンティティ/NCR はどちらも必ず & を含む。
        # 含まないテキストは unescape 不要（高速パス）。
        text = html.unescape(text)
    return text
