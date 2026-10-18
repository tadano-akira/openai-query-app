"""PDF 抽出のテスト（PDF は架空データをその場で生成する。OCR は使わない）。"""

import io

import pymupdf
import pytest

from app.services.pdf import extract
from app.services.pdf.storage import StorageError, path_for, save_upload, storage_key

CASES = [
    # name, MediaBox, CropBox, rotation
    ("plain", (0, 0, 595, 842), None, 0),
    ("media_offset", (100, 200, 695, 1042), None, 0),
    ("crop", (0, 0, 595, 842), (20, 30, 575, 812), 0),
    ("media_offset+crop", (100, 200, 695, 1042), (150, 260, 650, 1000), 0),
    ("rot90+crop", (0, 0, 595, 842), (20, 30, 575, 812), 90),
    ("rot180", (0, 0, 595, 842), None, 180),
    ("rot270+media_offset+crop", (100, 200, 695, 1042), (150, 260, 650, 1000), 270),
    ("landscape", (0, 0, 842, 595), None, 0),
]


def _page_with_text_at(doc: pymupdf.Document, media, crop, rot, px: float, py: float) -> None:
    """PDF user space の (px, py) をベースライン開始点として "TESTTEXT" を内容ストリームに直接書く"""
    page = doc.new_page()
    doc.xref_set_key(page.xref, "MediaBox", "[{:g} {:g} {:g} {:g}]".format(*media))
    if crop:
        doc.xref_set_key(page.xref, "CropBox", "[{:g} {:g} {:g} {:g}]".format(*crop))
    page.insert_text((0, 0), " ")  # フォント資源を作らせる
    font = page.get_fonts()[0][4]
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, f"BT /{font} 20 Tf {px} {py} Td (TESTTEXT) Tj ET".encode())
    kind, contents = doc.xref_get_key(page.xref, "Contents")
    doc.xref_set_key(page.xref, "Contents", f"[{contents.strip('[]')} {xref} 0 R]")
    if rot:
        page.set_rotation(rot)


@pytest.mark.parametrize(("name", "media", "crop", "rot"), CASES)
def test_coordinates_are_converted_to_pdf_user_space(name, media, crop, rot):
    px, py = media[0] + 200, media[1] + 400
    if crop:
        px, py = max(px, crop[0] + 50), max(py, crop[1] + 100)
    doc = pymupdf.open()
    _page_with_text_at(doc, media, crop, rot, px, py)
    doc = pymupdf.open("pdf", doc.tobytes())
    page = doc[0]
    geometry = extract.page_geometry(page)
    view = tuple(geometry["view"])
    raw = page.get_text("rawdict")
    line = next(
        ln
        for b in raw["blocks"]
        if b["type"] == 0
        for ln in b["lines"]
        if "TESTTEXT" in "".join(c["c"] for s in ln["spans"] for c in s["chars"])
    )
    origin = line["spans"][0]["origin"]
    assert extract.to_pdf(view, *origin) == pytest.approx([px, py], abs=0.5)
    assert geometry["rotation"] == rot
    # 四辺形の左上は y が大きい（PDF user space は y 上向き）
    result = next(extract.extract_pages(doc))
    quad = result.lines[0].quad
    assert quad[0][1] > quad[2][1] and quad[1][0] > quad[0][0]


def _table_pdf(rotation: int = 0) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    rows = [
        ["", "前連結会計年度", "当連結会計年度"],
        ["", "(2025年3月31日)", "(2026年3月31日)"],
        ["短期借入金", "1,000", "1,200"],
        ["社債", "2,000", "△300"],
    ]
    xs = [72, 222, 342, 462]
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(xs[c], 100 + r * 22, xs[c + 1], 122 + r * 22)
            page.draw_rect(rect, color=(0, 0, 0), width=0.5)
            w = pymupdf.get_text_length(val, fontname="japan", fontsize=10)
            page.insert_text(
                (rect.x1 - 5 - w if c else rect.x0 + 5, rect.y1 - 7), val, fontname="japan", fontsize=10
            )
    if rotation:
        page.set_rotation(rotation)
    return pymupdf.open("pdf", doc.tobytes())


@pytest.mark.parametrize("rotation", [0, 90])
def test_table_cells_keep_orientation_and_multi_row_headers(rotation):
    doc = _table_pdf(rotation)
    result = next(extract.extract_pages(doc))
    assert len(result.tables) == 1
    table = result.tables[0]
    grid = {(c.row, c.col): c.text for c in table.cells}
    # 回転したページでも、行・列が入れ替わらない
    assert grid[(0, 2)] == "当連結会計年度" and grid[(3, 0)] == "社債" and grid[(3, 2)] == "△300"
    assert table.header_rows == 2
    # セルの位置は、同じ文字の行の位置を含む
    cell = next(c for c in table.cells if c.text == "△300")
    line = next(ln for ln in result.lines if ln.text == "△300")
    cx = [p[0] for p in cell.quad]
    cy = [p[1] for p in cell.quad]
    lx = [p[0] for p in line.quad]
    ly = [p[1] for p in line.quad]
    assert min(cx) <= min(lx) and max(lx) <= max(cx) and min(cy) <= min(ly) and max(ly) <= max(cy)
    # 検出のために外した回転は元に戻っている
    assert doc[0].rotation == rotation


def test_header_rows_heuristic():
    def cells(rows):
        return [
            extract.CellResult(r, c, t, (0, 0, 1, 1), [])
            for r, row in enumerate(rows)
            for c, t in enumerate(row)
        ]

    assert extract.header_rows(cells([["", "前期", "当期"], ["売上", "1,000", "(200)"]])) == 1
    assert extract.header_rows(cells([["売上", "1,000", "△200"]])) == 0
    assert (
        extract.header_rows(
            cells([["", "2025年度", "2026年度"], ["", "百万円", "百万円"], ["売上", "1", "2"]])
        )
        == 2
    )


def test_page_label_starting_after_first_page_does_not_crash():
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 72), "text")
    doc.set_page_labels([{"startpage": 1, "prefix": "", "style": "D", "firstpagenum": 1}])
    doc = pymupdf.open("pdf", doc.tobytes())
    assert [extract.page_label(p) for p in doc] == [None, "1", "2"]


@pytest.mark.parametrize(
    ("chars", "garbled", "images", "drawings", "expected"),
    [
        (100, 0, 0, False, "ok"),
        (0, 0, 0, False, "blank"),
        (0, 0, 1, False, "non_text"),
        (100, 10, 0, False, "needs_review"),
        (5, 0, 1, False, "needs_review"),
    ],
)
def test_classify_page(chars, garbled, images, drawings, expected):
    assert extract.classify_page(chars, garbled, images, drawings) == expected


def test_classify_document():
    assert extract.classify_document(["non_text", "blank"]) == "unsupported_no_text"
    assert extract.classify_document(["ok", "non_text"]) == "partial"
    assert extract.classify_document(["ok", "needs_review"]) == "needs_review"
    assert extract.classify_document(["ok", "ok"]) == "ok"


class _Upload:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    async def read(self, n: int) -> bytes:
        return self._buf.read(n)


async def test_save_upload_hashes_and_rejects_non_pdf_and_oversize(tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    data = pdf.tobytes()
    stored = await save_upload(_Upload(data), str(tmp_path), max_bytes=10_000_000)
    assert stored.size == len(data) and stored.storage_key == storage_key(stored.sha256)
    assert path_for(str(tmp_path), stored.storage_key).read_bytes() == data
    # 同じ内容は同じ保存先（上書きしない）
    again = await save_upload(_Upload(data), str(tmp_path), max_bytes=10_000_000)
    assert again.storage_key == stored.storage_key
    with pytest.raises(StorageError) as e:
        await save_upload(_Upload(b"not a pdf at all"), str(tmp_path), max_bytes=10_000_000)
    assert e.value.code == "INVALID_PDF"
    with pytest.raises(StorageError) as e:
        await save_upload(_Upload(data), str(tmp_path), max_bytes=100)
    assert e.value.code == "FILE_LIMIT_EXCEEDED" and e.value.status_code == 413
    # 一時ファイルは残らない
    assert not list((tmp_path / "tmp").iterdir())


def test_path_for_rejects_outside_root(tmp_path):
    with pytest.raises(ValueError):
        path_for(str(tmp_path), "../etc/passwd")


def test_rows_without_horizontal_rules_are_split_per_line_item():
    """財務諸表のように横罫線がない表でも、勘定科目ごとに 1 行・1 セルになる"""
    doc = pymupdf.open()
    page = doc.new_page()
    x = [72, 260, 380, 500]
    top, header_bottom = 100, 130
    items = [
        ("現金及び現金同等物", "12,659,622", "10,343,085"),
        ("棚卸資産", "5,134,996", "5,469,806"),
        ("小計", "40,807,277", "38,721,891"),
    ]
    bottom = header_bottom + 18 * len(items) + 6
    # 外枠・縦罫線・見出しの下の横罫線だけを引く（明細行のあいだには横罫線がない）
    page.draw_rect(pymupdf.Rect(x[0], top, x[-1], bottom), color=(0, 0, 0), width=0.5)
    for xv in x[1:-1]:
        page.draw_line((xv, top), (xv, bottom), color=(0, 0, 0), width=0.5)
    page.draw_line((x[0], header_bottom), (x[-1], header_bottom), color=(0, 0, 0), width=0.5)
    page.insert_text((x[1] + 5, 112), "前連結会計年度", fontname="japan", fontsize=9)
    page.insert_text((x[1] + 5, 124), "（2026年３月31日）", fontname="japan", fontsize=9)
    page.insert_text((x[2] + 5, 118), "当第１四半期", fontname="japan", fontsize=9)
    for i, (label, a, b) in enumerate(items):
        y = header_bottom + 14 + 18 * i
        page.insert_text((x[0] + 5, y), label, fontname="japan", fontsize=10)
        for col, val in ((1, a), (2, b)):
            w = pymupdf.get_text_length(val, fontname="japan", fontsize=10)
            page.insert_text((x[col + 1] - 5 - w, y), val, fontname="japan", fontsize=10)
    doc = pymupdf.open("pdf", doc.tobytes())
    table = next(extract.extract_pages(doc)).tables[0]
    grid = {(c.row, c.col): c.text for c in table.cells}
    # 見出しは分けずに 1 行、明細は勘定科目ごとに 1 行
    assert table.row_count == 1 + len(items)
    assert "前連結会計年度" in grid[(0, 1)] and "（2026年３月31日）" in grid[(0, 1)]
    for i, (label, a, b) in enumerate(items, start=1):
        assert (grid[(i, 0)], grid[(i, 1)], grid[(i, 2)]) == (label, a, b)
    assert table.header_rows == 1
    # 分けた行は重ならず、上から順に並ぶ（PDF user space は y 上向き）
    tops = [
        next(c for c in table.cells if c.row == r and c.col == 1).quad[0][1] for r in range(table.row_count)
    ]
    assert tops == sorted(tops, reverse=True)
