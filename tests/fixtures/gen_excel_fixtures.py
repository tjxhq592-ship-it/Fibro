"""Excel 内容検索「とりこぼし」調査用フィクスチャ生成。

各ビルダー関数は出力ディレクトリを受け取り、生成した .xlsx の Path を返す。
調査テスト(`tests/test_excel_search_investigation.py`)はこれらを import し
`tmp_path` に生成する（バイナリをリポジトリに commit しないため）。

standalone 実行すると `tests/fixtures/` に一式を書き出す（人手での中身確認用・
gitignore 対象）:

    python tests/fixtures/gen_excel_fixtures.py

依存は openpyxl のみ（既存バージョン範囲: 3.1 系で確認）。追加依存なし。
"""
from __future__ import annotations

import datetime
import zipfile
from pathlib import Path

import openpyxl


def make_entity(out_dir: Path) -> Path:
    """XML エンティティ対象文字を含むセル。

    A1=`R&D部門`(& → &amp;), B1=`<重要>`(< > → &lt; &gt;),
    C1=`"引用"`(引用符は XML テキスト内では非エスケープ)。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "R&D部門"
    ws["B1"] = "<重要>"
    ws["C1"] = '"引用"'
    path = out_dir / "entity.xlsx"
    wb.save(path)
    return path


_MINIMAL_XLSX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    ),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    ),
    "xl/workbook.xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    ),
    "xl/_rels/workbook.xml.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    ),
}


def _write_minimal_xlsx(path: Path, cells_xml: str) -> Path:
    """ワークシート XML を直接指定して最小構成の .xlsx を組み立てる。

    openpyxl の書き出しに依存せず、エンティティ/NCR の XML 表現を
    バイト列レベルで固定するため（環境による出力揺れを排除）。
    """
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData><row r=\"1\">{cells_xml}</row></sheetData>"
        "</worksheet>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in _MINIMAL_XLSX_PARTS.items():
            zf.writestr(name, data)
        zf.writestr("xl/worksheets/sheet1.xml", sheet.encode("utf-8"))
    return path


def make_entity_named(out_dir: Path) -> Path:
    """名前付きエンティティ表記のインライン文字列を直組みしたブック。

    A1=`R&D部門`, B1=`<重要>`, C1=`"引用"` を &amp; &lt; &gt; &quot; で
    エンコードして書く（日本語は UTF-8 素のまま）。
    """
    cells = (
        '<c r="A1" t="inlineStr"><is><t>R&amp;D部門</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>&lt;重要&gt;</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>&quot;引用&quot;</t></is></c>'
    )
    return _write_minimal_xlsx(out_dir / "entity_named.xlsx", cells)


def make_entity_ncr(out_dir: Path) -> Path:
    """数値文字参照(NCR)表記のインライン文字列を直組みしたブック。

    日本語を &#37096;&#38272;（部門）等の 10進 NCR で、記号は名前付き
    エンティティで書く。openpyxl 系ツールやロケール設定によっては
    この形式で書き出される環境がある。
    """
    cells = (
        '<c r="A1" t="inlineStr"><is><t>R&amp;D&#37096;&#38272;</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>&lt;&#37325;&#35201;&gt;</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>&quot;&#24341;&#29992;&quot;</t></is></c>'
    )
    return _write_minimal_xlsx(out_dir / "entity_ncr.xlsx", cells)


def make_formats(out_dir: Path) -> Path:
    """表示書式付きセル（内部値と表示文字列が異なる）。

    A1=日付 2026-07-04 表示`yyyy/m/d`, B1=1000 表示`#,##0`,
    C1=0.15 表示`0%`。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    a = ws["A1"]
    a.value = datetime.datetime(2026, 7, 4)
    a.number_format = "yyyy/m/d"
    b = ws["B1"]
    b.value = 1000
    b.number_format = "#,##0"
    c = ws["C1"]
    c.value = 0.15
    c.number_format = "0%"
    path = out_dir / "formats.xlsx"
    wb.save(path)
    return path


def make_bad_dimension(out_dir: Path) -> Path:
    """`<dimension>` 宣言を実データより狭く改竄したブック。

    E10 に `TARGET_OUT_OF_DIM` を置き、zip を開いて sheet1.xml の
    dimension を A1:A1 に書き換える。read_only 走査が dimension を
    信用してセルを取りこぼすか（H3）を検証するための細工。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["E10"] = "TARGET_OUT_OF_DIM"
    raw = out_dir / "_bad_dim_raw.xlsx"
    wb.save(raw)

    with zipfile.ZipFile(raw) as zf:
        contents = {n: zf.read(n) for n in zf.namelist()}
    sheet = contents["xl/worksheets/sheet1.xml"].decode("utf-8")
    # openpyxl は自己終了タグ <dimension ref="E10:E10"/> を出力する。
    import re
    sheet = re.sub(r'<dimension ref="[^"]*"\s*/>',
                   '<dimension ref="A1:A1"/>', sheet)
    contents["xl/worksheets/sheet1.xml"] = sheet.encode("utf-8")

    path = out_dir / "bad_dimension.xlsx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in contents.items():
            zf.writestr(name, data)
    raw.unlink()
    return path


def make_formula_nocache(out_dir: Path) -> Path:
    """キャッシュ値を持たない数式セル。

    openpyxl で `=CONCATENATE("HIT","KEY")` を書くと Excel を経由しないため
    数式の計算結果キャッシュが存在しない（data_only=True では None）。
    ライブラリ生成ファイルで頻出する状況を再現する。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = '=CONCATENATE("HIT","KEY")'
    path = out_dir / "formula_nocache.xlsx"
    wb.save(path)
    return path


def make_fake_encrypted(out_dir: Path) -> Path:
    """拡張子だけ .xlsx の非 zip ファイル。

    実際の暗号化ブック（CFB/OLE コンテナ）の生成は難しいため、
    `load_workbook` が失敗する経路（BadZipFile）を非 zip バイト列で代用する。
    暗号化・破損・OneDrive オンライン専用のいずれも「開けない」点で同経路。
    """
    path = out_dir / "fake_encrypted.xlsx"
    path.write_bytes(b"not a zip - stands in for encrypted/corrupt/online-only")
    return path


def make_inline_string(out_dir: Path) -> Path:
    """インライン文字列(t="inlineStr")を持つブック。

    openpyxl の標準出力は sharedStrings.xml を使わずワークシート XML に
    インライン文字列を書く。プレフィルタの worksheet-XML 走査経路を
    検証するための素直なブック。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "INLINE_UNIQUE_TOKEN"
    path = out_dir / "inline_string.xlsx"
    wb.save(path)
    return path


ALL_BUILDERS = (
    make_entity,
    make_entity_named,
    make_entity_ncr,
    make_formats,
    make_bad_dimension,
    make_formula_nocache,
    make_fake_encrypted,
    make_inline_string,
)


def generate_all(out_dir: Path) -> dict[str, Path]:
    """全フィクスチャを out_dir に生成し {stem: path} を返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    return {b.__name__.removeprefix("make_"): b(out_dir) for b in ALL_BUILDERS}


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    made = generate_all(here)
    for name, path in made.items():
        print(f"{name:16} -> {path.name}")
