"""PyMuPDF による文字・行・座標・表の抽出（OCR は使わない）。

座標の扱い（2026-10-18 に回転 0/90/180/270・CropBox・MediaBox 原点ずれ・横長で検証）:
- PyMuPDF の抽出座標は「回転前・CropBox 左上原点・y 下向き」。
- PDF user space へは、表示範囲 view（CropBox ∩ MediaBox、PDF 座標）を使い
  x_pdf = view.x0 + x、y_pdf = view.y1 - y で変換する。page.transformation_matrix は
  CropBox のオフセットを含まない場合があるため使わない。
- PDF.js は同じ view を基準にするので、フロントは viewport.convertToViewportPoint で表示座標にできる。
"""

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pymupdf

PARSER_VERSION = f"pymupdf-{pymupdf.VersionBind}/oqa-extract-1"
# 文字化けとみなす文字（置換文字・私用領域）の割合がこれを超えたら要確認
GARBLED_RATIO = 0.05
# 画像があるのに文字がこれ未満なら要確認（画像の表や図の可能性）
MIN_CHARS_WITH_IMAGES = 20

Rect = tuple[float, float, float, float]
Quad = list[list[float]]


def _round(v: float) -> float:
    return round(v, 2)


def _box(doc: pymupdf.Document, xref: int, key: str) -> Rect | None:
    """ページ（親の Pages から継承した値を含む）の PDF 上の枠。正規化して返す"""
    seen = set()
    while xref and xref not in seen:
        seen.add(xref)
        kind, value = doc.xref_get_key(xref, key)
        if kind == "array":
            x0, y0, x1, y1 = (float(v) for v in value.strip("[]").split())
            return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        kind, parent = doc.xref_get_key(xref, "Parent")
        xref = int(parent.split()[0]) if kind == "xref" else 0
    return None


def page_geometry(page: pymupdf.Page) -> dict[str, Any]:
    doc = page.parent
    media = _box(doc, page.xref, "MediaBox") or (0.0, 0.0, 612.0, 792.0)
    crop = _box(doc, page.xref, "CropBox") or media
    # CropBox は MediaBox と交差した範囲が表示される
    view = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
    kind, unit = doc.xref_get_key(page.xref, "UserUnit")
    return {
        "rotation": page.rotation,
        "mediabox": [_round(v) for v in media],
        "cropbox": [_round(v) for v in crop],
        "view": [_round(v) for v in view],
        "user_unit": float(unit) if kind in ("int", "real") else 1.0,
        "coord_version": "pdf-user-space-1",
    }


def to_pdf(view: Rect, x: float, y: float) -> list[float]:
    return [_round(view[0] + x), _round(view[3] - y)]


def rect_to_quad(view: Rect, r: Rect) -> Quad:
    """回転前の矩形を PDF user space の四辺形（左上・右上・左下・右下）にする"""
    x0, y0, x1, y1 = r
    return [to_pdf(view, x0, y0), to_pdf(view, x1, y0), to_pdf(view, x0, y1), to_pdf(view, x1, y1)]


def rect_to_pdf_rect(view: Rect, r: Rect) -> list[float]:
    """回転前の矩形を PDF user space の [x0, y0, x1, y1]（y0 < y1）にする"""
    x0, y0, x1, y1 = r
    return [_round(view[0] + x0), _round(view[3] - y1), _round(view[0] + x1), _round(view[3] - y0)]


def search_text(raw: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", raw).split())


def _garbled(ch: str) -> bool:
    return ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF


@dataclass
class LineResult:
    block_seq: int
    line_seq: int
    text: str
    bbox: Rect
    quad: Quad
    glyphs: list[list[float]]


@dataclass
class CellResult:
    row: int
    col: int
    text: str
    bbox: Rect
    quad: Quad


@dataclass
class TableResult:
    bbox: Rect
    quad: Quad
    row_count: int
    col_count: int
    # 上から何行を列見出しとみなすか（header_rows 参照。自動推定でレビュー対象）
    header_rows: int
    cells: list[CellResult] = field(default_factory=list)


@dataclass
class PageResult:
    page_number: int
    pdf_page_label: str | None
    geometry: dict[str, Any]
    quality: str
    char_count: int
    image_count: int
    lines: list[LineResult]
    tables: list[TableResult]


def _lines(page: pymupdf.Page, view: Rect) -> tuple[list[LineResult], int, int]:
    raw = page.get_text("rawdict")
    blocks = [b for b in raw["blocks"] if b.get("type") == 0]
    # 決定的な順序: ブロックの上端・左端・抽出順（段組みでは読順が誤ることがある）
    blocks = sorted(
        enumerate(blocks), key=lambda ib: (round(ib[1]["bbox"][1], 1), round(ib[1]["bbox"][0], 1), ib[0])
    )
    lines: list[LineResult] = []
    chars = garbled = 0
    for block_seq, (_, block) in enumerate(blocks, start=1):
        for line in block["lines"]:
            glyphs = [ch for span in line["spans"] for ch in span["chars"]]
            text = "".join(ch["c"] for ch in glyphs)
            if not text.strip():
                continue
            chars += sum(not ch.isspace() for ch in text)
            garbled += sum(_garbled(ch) for ch in text)
            bbox = tuple(line["bbox"])
            lines.append(
                LineResult(
                    block_seq=block_seq,
                    line_seq=len(lines) + 1,
                    text=text,
                    bbox=bbox,
                    quad=rect_to_quad(view, bbox),
                    glyphs=[rect_to_pdf_rect(view, tuple(ch["bbox"])) for ch in glyphs],
                )
            )
    return lines, chars, garbled


def _find_tables(page: pymupdf.Page) -> list:
    """表を検出する。find_tables は回転後の向きで動き、座標も回転後になるため、
    回転したページでは一時的に回転を外して（文字抽出と同じ回転前の座標系で）検出する。
    文書はメモリ上で開いているだけなので、原本ファイルは変わらない。"""
    rotation = page.rotation
    if not rotation:
        return page.find_tables().tables
    page.set_rotation(0)
    try:
        return page.find_tables().tables
    finally:
        page.set_rotation(rotation)


def _is_amount(text: str) -> bool:
    return bool(_AMOUNT.match(unicodedata.normalize("NFKC", text).strip()))


def _cluster_by_y(lines: list[LineResult]) -> list[list[LineResult]]:
    """同じ高さに並ぶ行（列をまたいで同じ勘定科目の行）をまとめる"""
    if not lines:
        return []
    heights = sorted(ln.bbox[3] - ln.bbox[1] for ln in lines)
    tol = max(1.0, heights[len(heights) // 2] * 0.6)
    ordered = sorted(lines, key=lambda ln: (ln.bbox[1] + ln.bbox[3]) / 2)
    clusters = [[ordered[0]]]
    for ln in ordered[1:]:
        prev = clusters[-1]
        prev_center = sum((x.bbox[1] + x.bbox[3]) / 2 for x in prev) / len(prev)
        if (ln.bbox[1] + ln.bbox[3]) / 2 - prev_center > tol:
            clusters.append([ln])
        else:
            prev.append(ln)
    return clusters


def split_multiline_rows(cells: list[CellResult], lines: list[LineResult], view: Rect) -> list[CellResult]:
    """横罫線のない財務諸表では、罫線ベースの検出で複数の勘定科目が 1 行（1 セル）にまとまる。
    金額が 2 つ以上入ったセルを含む行だけを、文字の行の高さで細かい行に分ける。
    列の区切りは罫線の検出結果をそのまま使う。見出し行（金額を含まない行）は分けない。"""
    by_row: dict[int, list[CellResult]] = {}
    for c in cells:
        by_row.setdefault(c.row, []).append(c)
    out: list[CellResult] = []
    new_row = 0
    for r in sorted(by_row):
        row = sorted(by_row[r], key=lambda c: c.col)
        multi = any(c.col > 0 and sum(_is_amount(t) for t in c.text.splitlines()) >= 2 for c in row)
        y0, y1 = min(c.bbox[1] for c in row), max(c.bbox[3] for c in row)
        x0, x1 = min(c.bbox[0] for c in row), max(c.bbox[2] for c in row)
        inside = [
            ln
            for ln in lines
            if y0 <= (ln.bbox[1] + ln.bbox[3]) / 2 <= y1 and x0 <= (ln.bbox[0] + ln.bbox[2]) / 2 <= x1
        ]
        clusters = _cluster_by_y(inside) if multi else []
        if len(clusters) < 2:
            out += [CellResult(new_row, c.col, c.text, c.bbox, c.quad) for c in row]
            new_row += 1
            continue
        # 行の境界は、隣り合う行のあいだの中点（元の行の範囲を隙間なく分ける）
        tops = [min(ln.bbox[1] for ln in cl) for cl in clusters]
        bottoms = [max(ln.bbox[3] for ln in cl) for cl in clusters]
        bounds = [y0] + [(bottoms[i] + tops[i + 1]) / 2 for i in range(len(clusters) - 1)] + [y1]
        for k, cl in enumerate(clusters):
            for c in row:
                in_col = sorted(
                    (ln for ln in cl if c.bbox[0] <= (ln.bbox[0] + ln.bbox[2]) / 2 <= c.bbox[2]),
                    key=lambda ln: ln.bbox[0],
                )
                bbox = (c.bbox[0], bounds[k], c.bbox[2], bounds[k + 1])
                out.append(
                    CellResult(
                        new_row, c.col, " ".join(ln.text for ln in in_col), bbox, rect_to_quad(view, bbox)
                    )
                )
            new_row += 1
    return out


def _tables(page: pymupdf.Page, view: Rect, lines: list[LineResult]) -> list[TableResult]:
    results = []
    for t in _find_tables(page):
        data = t.extract()
        table = TableResult(
            bbox=tuple(t.bbox),
            quad=rect_to_quad(view, tuple(t.bbox)),
            row_count=t.row_count,
            col_count=t.col_count,
            header_rows=0,
        )
        for r, row in enumerate(t.rows):
            for c, cell in enumerate(row.cells):
                # 結合セルの一部は None になる（結合の扱いはレビューで確認する）
                if cell is None:
                    continue
                text = (data[r][c] if r < len(data) and c < len(data[r]) else None) or ""
                table.cells.append(CellResult(r, c, text, tuple(cell), rect_to_quad(view, tuple(cell))))
        table.cells = split_multiline_rows(table.cells, lines, view)
        table.row_count = max((c.row for c in table.cells), default=-1) + 1
        table.header_rows = header_rows(table.cells)
        results.append(table)
    return results


# 金額らしい値（△・▲・マイナス・括弧の負号、桁区切り、小数を含む）
_AMOUNT = re.compile(r"^[△▲\-−(（]?\s*[\d,，]+(?:[.．]\d+)?\s*[)）]?%?$")


def header_rows(cells: list[CellResult]) -> int:
    """列見出しの行数を推定する。上から、1 列目以外に金額らしい値が出てくる直前の行までを見出しとする。

    決算書の表は「前期／当期」「(2025年3月31日)」のように見出しが複数行になることが多い。
    日付は金額とみなさない。最大 3 行。PyMuPDF の見出し推定は多段の見出しを扱えないため使わない。
    """
    rows: dict[int, list[str]] = {}
    for c in cells:
        if c.col > 0:
            rows.setdefault(c.row, []).append(unicodedata.normalize("NFKC", c.text).strip())
    count = 0
    for r in sorted(rows)[:3]:
        values = [v for v in rows[r] if v]
        if not values or any(_AMOUNT.match(v) for v in values):
            break
        count = r + 1
    return count


def classify_page(chars: int, garbled: int, images: int, has_drawings: bool) -> str:
    if chars == 0:
        return "non_text" if images else ("blank" if not has_drawings else "needs_review")
    if garbled / chars > GARBLED_RATIO or (images and chars < MIN_CHARS_WITH_IMAGES):
        return "needs_review"
    return "ok"


def classify_document(page_qualities: list[str]) -> str:
    """文書全体の品質。全ページが文字なしなら unsupported_no_text（OCR はしない）"""
    if not page_qualities or all(q in ("non_text", "blank") for q in page_qualities):
        return "unsupported_no_text"
    if "non_text" in page_qualities:
        return "partial"
    if "needs_review" in page_qualities:
        return "needs_review"
    return "ok"


def page_label(page: pymupdf.Page) -> str | None:
    """PDF 内部のページラベル。ラベルの定義が途中のページから始まる PDF では、
    それより前のページで PyMuPDF 1.28.2 の get_label が IndexError になるため、ラベルなしとして扱う"""
    try:
        return page.get_label() or None
    except IndexError:
        return None


def extract_pages(doc: pymupdf.Document) -> Iterator[PageResult]:
    for page in doc:
        geometry = page_geometry(page)
        view = tuple(geometry["view"])
        lines, chars, garbled = _lines(page, view)
        images = len(page.get_images(full=False))
        tables = _tables(page, view, lines) if chars else []
        has_drawings = bool(page.get_drawings()) if not chars and not images else False
        yield PageResult(
            page_number=page.number + 1,
            pdf_page_label=page_label(page),
            geometry=geometry,
            quality=classify_page(chars, garbled, images, has_drawings),
            char_count=chars,
            image_count=images,
            lines=lines,
            tables=tables,
        )
