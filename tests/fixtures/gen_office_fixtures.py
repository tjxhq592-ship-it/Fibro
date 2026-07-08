"""Word / PowerPoint / PDF 内容検索のフィクスチャ生成。

`gen_excel_fixtures.py` と同じ方針: 各ビルダー関数は出力ディレクトリを
受け取り、生成したファイルの Path を返す。テストはこれらを import して
`tmp_path` に生成する（バイナリをリポジトリに commit しないため）。

docx / pptx は XML を直書きし zipfile で組み立てる（python-docx /
python-pptx に依存しない = 実装と同じ依存ゼロ方針）。
PDF は外部ライブラリを使わず、最小のテキスト入り PDF をバイト列で
静的に組み立てる（1ページ・1テキストオブジェクトで十分）。

standalone 実行すると `tests/fixtures/` に一式を書き出す（人手での中身
確認用・gitignore 対象）:

    python tests/fixtures/gen_office_fixtures.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path

# ── docx ──────────────────────────────────────────────────────────────────

_DOCX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    ),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    ),
}


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def make_docx(out_dir: Path, paragraphs: list[str],
              name: str = "doc.docx") -> Path:
    """段落リストから最小構成の .docx を組み立てる。"""
    body = "".join(
        f"<w:p><w:r><w:t>{_xml_escape(p)}</w:t></w:r></w:p>"
        for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    path = out_dir / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for part, data in _DOCX_PARTS.items():
            zf.writestr(part, data)
        zf.writestr("word/document.xml", document.encode("utf-8"))
    return path


def make_docx_entities(out_dir: Path) -> Path:
    """エンティティ/NCR 表記を含む document.xml を直書きした .docx。

    段落1=`R&D部門`（& は &amp;、日本語は 10進 NCR）、段落2=通常文字列。
    F1 共通ヘルパー経由の unescape を検証する。
    """
    body = (
        "<w:p><w:r><w:t>R&amp;D&#37096;&#38272;</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>ENTITY_PLAIN_PARA</w:t></w:r></w:p>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    path = out_dir / "entities.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for part, data in _DOCX_PARTS.items():
            zf.writestr(part, data)
        zf.writestr("word/document.xml", document.encode("utf-8"))
    return path


def make_fake_docx(out_dir: Path) -> Path:
    """拡張子だけ .docx の非 zip ファイル（破損/暗号化の代用）。"""
    path = out_dir / "fake.docx"
    path.write_bytes(b"not a zip - stands in for encrypted/corrupt")
    return path


# ── pptx ──────────────────────────────────────────────────────────────────

_PPTX_STATIC_PARTS = {
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        "</Relationships>"
    ),
    "ppt/presentation.xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    ),
}


def _slide_xml(texts: list[str]) -> str:
    paras = "".join(
        f"<a:p><a:r><a:t>{_xml_escape(t)}</a:t></a:r></a:p>" for t in texts)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        f"{paras}"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )


def make_pptx(out_dir: Path, slides: dict[int, list[str]],
              name: str = "deck.pptx") -> Path:
    """{スライド番号: 段落テキストのリスト} から最小構成の .pptx を組む。

    番号は連番でなくてもよい（slide2 と slide10 の数値順検証などに使う）。
    """
    overrides = "".join(
        f'<Override PartName="/ppt/slides/slide{n}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for n in slides)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        f"{overrides}</Types>"
    )
    path = out_dir / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        for part, data in _PPTX_STATIC_PARTS.items():
            zf.writestr(part, data)
        for number, texts in slides.items():
            zf.writestr(f"ppt/slides/slide{number}.xml",
                        _slide_xml(texts).encode("utf-8"))
    return path


def make_fake_pptx(out_dir: Path) -> Path:
    """拡張子だけ .pptx の非 zip ファイル。"""
    path = out_dir / "fake.pptx"
    path.write_bytes(b"not a zip - stands in for encrypted/corrupt")
    return path


# ── PDF ───────────────────────────────────────────────────────────────────

def _pdf_escape(text: str) -> str:
    return (text.replace("\\", r"\\").replace("(", r"\(")
            .replace(")", r"\)"))


def build_pdf_bytes(pages_text: list[str | None]) -> bytes:
    """テキスト入り最小 PDF をバイト列で組み立てる（外部ライブラリ不使用）。

    pages_text の各要素が 1 ページ。None のページはコンテンツストリーム
    を空にする＝テキスト層なし（スキャン PDF の代用）。
    テキストは Helvetica の標準エンコーディングで書くため ASCII 限定。
    """
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def add(num: int, body: bytes) -> None:
        offsets[num] = len(out)
        out.extend(f"{num} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    n = len(pages_text)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add(2, f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode("ascii"))
    add(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages_text):
        page_num, content_num = 4 + 2 * i, 5 + 2 * i
        add(page_num, (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_num} 0 R >>").encode("ascii"))
        if text is None:
            stream = b""
        else:
            stream = (f"BT /F1 12 Tf 72 720 Td ({_pdf_escape(text)}) Tj ET"
                      .encode("ascii"))
        add(content_num,
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    size = 4 + 2 * n  # 最大オブジェクト番号 + 1
    xref_pos = len(out)
    out.extend(f"xref\n0 {size}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for num in range(1, size):
        out.extend(f"{offsets[num]:010d} 00000 n \n".encode("ascii"))
    out.extend((f"trailer\n<< /Size {size} /Root 1 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii"))
    return bytes(out)


def make_pdf(out_dir: Path, pages_text: list[str | None],
             name: str = "doc.pdf") -> Path:
    path = out_dir / name
    path.write_bytes(build_pdf_bytes(pages_text))
    return path


def make_broken_pdf(out_dir: Path) -> Path:
    """PDF ヘッダだけあって構造が壊れているファイル。"""
    path = out_dir / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\ngarbage without xref or trailer")
    return path


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    made = [
        make_docx(here, ["hello world", "target paragraph"]),
        make_docx_entities(here),
        make_fake_docx(here),
        make_pptx(here, {1: ["slide one"], 2: ["slide two"]}),
        make_fake_pptx(here),
        make_pdf(here, ["Hello PDF target", None]),
        make_broken_pdf(here),
    ]
    for path in made:
        print(f"-> {path.name}")
