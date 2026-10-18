"""抽出結果から検索用チャンクを作る（設計書 §9.1）。DB に依存しない純粋な処理。

- 本文: ページ内で表の外にある行を、見出しの切れ目を優先して目標 500・上限 800 トークンにまとめる。
  前のチャンクの末尾を約 100 トークン重ねる。ページをまたいでまとめない（根拠のページを 1 つに保つ）。
- 表: 表題・単位・列見出しを各チャンクに再掲し、行を「行見出し | 列見出し: 値」の形で並べる。
  数値だけを切り離さない。
- チャンク内の各部分がどの要素（行・セル）から来たかを、文字位置（コードポイント、半開区間）で返す。
トークン数は文字数からの概算（日本語はおおむね 1 文字 1 トークン強）。実際の使用量は API の返却値を記録する。
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

TARGET_TOKENS = 500
MAX_TOKENS = 800
OVERLAP_TOKENS = 100
# 表チャンクの最大行数（見出しの再掲を含めても上限に収まるよう、トークン数でも区切る）
MAX_TABLE_ROWS = 25

_HEADING = re.compile(
    r"^\s*(?:[0-9０-９]+[.．、]\s*\S|[（(][0-9０-９]+[)）]|【[^】]+】|[①-⑳]|第[0-9０-９一二三四五六七八九十]+[章節部]|[■●◆□◇]\s*\S)"
)
_UNIT = re.compile(r"単位")


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) * 1.1))


# 均等割付で 1 文字ずつ空白が入った語（例: 「研 究 開 発 費」）を詰めるため、日本語の文字どうしの間の空白を消す
_CJK = r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uff66-\uff9f々〆ヵヶー（）()]"
_CJK_GAP = re.compile(rf"(?<={_CJK})\s+(?={_CJK})")


def normalize(text: str) -> str:
    """字句検索用。NFKC・小文字・空白の整理（日本語の文字の間の空白は詰める）。質問にも同じ処理をする"""
    text = " ".join(unicodedata.normalize("NFKC", text).lower().split())
    return _CJK_GAP.sub("", text)


@dataclass(frozen=True)
class LineIn:
    element_id: uuid.UUID
    line_seq: int
    text: str
    # PyMuPDF の回転前座標 [x0, y0, x1, y1]（y 下向き）
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class CellIn:
    element_id: uuid.UUID
    row: int
    col: int
    text: str
    header_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class TableIn:
    element_id: uuid.UUID
    bbox: tuple[float, float, float, float]
    cells: tuple[CellIn, ...]


@dataclass(frozen=True)
class PageIn:
    page_id: uuid.UUID
    page_number: int
    lines: tuple[LineIn, ...]
    tables: tuple[TableIn, ...]


@dataclass
class Part:
    element_id: uuid.UUID
    start: int
    end: int
    role: str  # content / header / unit / title


@dataclass
class ChunkOut:
    page_id: uuid.UUID
    page_number: int
    kind: str  # text / table
    heading: str | None
    text: str = ""
    parts: list[Part] = field(default_factory=list)

    def add(self, element_id: uuid.UUID, text: str, role: str, sep: str = "\n") -> None:
        if self.text:
            self.text += sep
        start = len(self.text)
        self.text += text
        self.parts.append(Part(element_id, start, len(self.text), role))

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def _center_inside(bbox, area, margin: float = 1.0) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return area[0] - margin <= cx <= area[2] + margin and area[1] - margin <= cy <= area[3] + margin


def is_heading(text: str) -> bool:
    return len(text.strip()) <= 40 and bool(_HEADING.match(text))


def running_texts(pages: list[PageIn]) -> set[str]:
    """多くのページに同じ文言で繰り返し出る行（ページ見出し・フッター）。表題や本文の候補から外す"""
    if len(pages) < 3:
        return set()
    counts: dict[str, int] = {}
    for p in pages:
        for t in {normalize(ln.text) for ln in p.lines}:
            counts[t] = counts.get(t, 0) + 1
    threshold = max(3, int(len(pages) * 0.4))
    # 短い行（ページ番号等）と単位注記（表ごとに必要）は対象にしない
    return {t for t, n in counts.items() if n >= threshold and len(t) >= 6 and not _UNIT.search(t)}


def text_chunks(page: PageIn, skip: set[str] = frozenset()) -> list[ChunkOut]:
    lines = [
        ln
        for ln in sorted(page.lines, key=lambda x: x.line_seq)
        if not any(_center_inside(ln.bbox, t.bbox) for t in page.tables) and normalize(ln.text) not in skip
    ]
    chunks: list[ChunkOut] = []
    heading: str | None = None
    current: ChunkOut | None = None

    def start(carry: list[LineIn]) -> ChunkOut:
        c = ChunkOut(page.page_id, page.page_number, "text", heading)
        for ln in carry:
            c.add(ln.element_id, ln.text, "content")
        return c

    body: list[LineIn] = []  # 現在のチャンクに入れた行（重ねる行を選ぶため）
    for ln in lines:
        if is_heading(ln.text):
            heading = ln.text.strip()
            # 見出しの切れ目でチャンクを区切る（短すぎる場合は続ける）
            if current and current.tokens >= TARGET_TOKENS // 3:
                chunks.append(current)
                current, body = None, []
        if current is None:
            current, body = start([]), []
        if (
            current.text
            and current.tokens + estimate_tokens(ln.text) > MAX_TOKENS
            or current.tokens >= TARGET_TOKENS
        ):
            chunks.append(current)
            carry: list[LineIn] = []
            for prev in reversed(body):
                if sum(estimate_tokens(x.text) for x in carry) + estimate_tokens(prev.text) > OVERLAP_TOKENS:
                    break
                carry.insert(0, prev)
            current, body = start(carry), list(carry)
        current.add(ln.element_id, ln.text, "content")
        body.append(ln)
    if current and current.text:
        chunks.append(current)
    return chunks


def _table_title_and_unit(
    page: PageIn, table: TableIn, skip: set[str] = frozenset()
) -> tuple[LineIn | None, LineIn | None]:
    """表のすぐ上（80pt 以内）にある行から、表題と単位注記を探す"""
    above = [
        ln
        for ln in page.lines
        if ln.bbox[3] <= table.bbox[1] + 2
        and table.bbox[1] - ln.bbox[3] <= 80
        and ln.bbox[2] >= table.bbox[0]
        and ln.bbox[0] <= table.bbox[2]
        and normalize(ln.text) not in skip
    ]
    above.sort(key=lambda ln: ln.bbox[3], reverse=True)  # 表に近い順
    unit = next((ln for ln in above if _UNIT.search(ln.text)), None)
    title = next((ln for ln in above if not _UNIT.search(ln.text) and len(ln.text.strip()) >= 2), None)
    return title, unit


def table_chunks(
    page: PageIn, table: TableIn, skip: set[str] = frozenset(), inherited_title: str | None = None
) -> list[ChunkOut]:
    cells = {(c.row, c.col): c for c in table.cells}
    by_id = {c.element_id: c for c in table.cells}
    # 列見出しの行: 他の行のセルから見出しとして参照されている行
    header_rows = {
        by_id[h].row for c in table.cells for h in c.header_ids if h in by_id and by_id[h].row != c.row
    }
    n_rows = max((c.row for c in table.cells), default=-1) + 1
    n_cols = max((c.col for c in table.cells), default=-1) + 1
    body_rows = [r for r in range(n_rows) if r not in header_rows]
    title, unit = _table_title_and_unit(page, table, skip)
    # 表題が見つからない表は、直前の表の続き（改ページした表）とみなして表題を引き継ぐ
    heading = title.text.strip() if title else (f"{inherited_title}（続き）" if inherited_title else None)

    def col_header(col: int) -> str:
        parts = [
            cells[(r, col)].text
            for r in sorted(header_rows)
            if (r, col) in cells and cells[(r, col)].text.strip()
        ]
        return " ".join(" ".join(p.split()) for p in parts)

    def new_chunk() -> ChunkOut:
        c = ChunkOut(page.page_id, page.page_number, "table", heading)
        c.add(table.element_id, "【表】", "title", sep="")
        if title:
            c.add(title.element_id, title.text.strip(), "title", sep="")
        elif heading:
            c.text += heading
        if unit:
            c.add(unit.element_id, unit.text.strip(), "unit", sep=" ")
        heads = [(col, col_header(col)) for col in range(1, n_cols)]
        if any(h for _, h in heads):
            c.text += "\n列:"
            for col, _ in heads:
                for r in sorted(header_rows):
                    hc = cells.get((r, col))
                    if hc and hc.text.strip():
                        c.add(hc.element_id, " ".join(hc.text.split()), "header", sep=" ")
                if col < n_cols - 1:
                    c.text += " |"
        return c

    chunks: list[ChunkOut] = []
    current = new_chunk()
    rows_in = 0
    for r in body_rows:
        row_cells = [cells[(r, col)] for col in range(n_cols) if (r, col) in cells]
        if not any(c.text.strip() for c in row_cells):
            continue
        row_text_len = sum(len(c.text) for c in row_cells) + sum(len(col_header(c.col)) for c in row_cells)
        if rows_in and (
            rows_in >= MAX_TABLE_ROWS or current.tokens + estimate_tokens("x" * row_text_len) > MAX_TOKENS
        ):
            chunks.append(current)
            current, rows_in = new_chunk(), 0
        current.text += "\n"
        first = True
        for c in row_cells:
            value = " ".join(c.text.split())
            if not value:
                continue
            if c.col == 0:
                current.add(c.element_id, value, "content", sep="")
            else:
                label = col_header(c.col)
                current.text += ("" if first else " | ") + (f"{label}: " if label else "")
                current.add(c.element_id, value, "content", sep="")
            first = False
        rows_in += 1
    if rows_in:
        chunks.append(current)
    return chunks


def build_chunks(pages: list[PageIn]) -> list[ChunkOut]:
    out: list[ChunkOut] = []
    skip = running_texts(pages)
    last_title: str | None = None
    for page in sorted(pages, key=lambda p: p.page_number):
        out += text_chunks(page, skip)
        # ページ内の表は上から順に処理する（表題の引き継ぎのため）
        for table in sorted(page.tables, key=lambda t: t.bbox[1]):
            chunks = table_chunks(page, table, skip, last_title)
            out += chunks
            if chunks and chunks[0].heading:
                last_title = chunks[0].heading.removesuffix("（続き）")
    return out
