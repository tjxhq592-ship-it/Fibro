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


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    made = [
        make_docx(here, ["hello world", "target paragraph"]),
        make_docx_entities(here),
        make_fake_docx(here),
        make_pptx(here, {1: ["slide one"], 2: ["slide two"]}),
        make_fake_pptx(here),
    ]
    for path in made:
        print(f"-> {path.name}")
