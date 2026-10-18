"""LLM に渡す根拠候補の作成（設計書 §10.1 evidence_selection の入力）。

検索結果のチャンクから、値のセル（表）と本文の行を候補にし、run 内だけの短い ID（E1…）を振る。
表のセルには、行見出し・区分（値のない上位の科目行）・列見出し・単位・表題をサーバーが付ける。
これらは LLM の判断材料であると同時に、回答に表示する文脈の根拠（context）になる。
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.doc_models import Chunk, ChunkElement, Element, Page, TableCell
from app.services.search.chunking import estimate_tokens, normalize

# 候補全体の上限（設計書の MAX_CONTEXT_TOKENS）。超えたら順位の低いチャンクから丸ごと外す
MAX_CANDIDATE_TOKENS = 20_000
# 同じ表の別チャンクを足す上限
MAX_SIBLINGS = 6


@dataclass
class Candidate:
    ref: str
    element_id: uuid.UUID
    kind: str  # value（表の値のセル）/ text（本文の行）
    text: str
    page_number: int
    chunk_rank: int
    title: str | None = None
    unit: str | None = None
    section: list[str] = field(default_factory=list)
    row_label: str | None = None
    column: str | None = None
    # 回答に文脈として表示・記録する要素（行見出し・区分・列見出し・単位・表題）
    context_ids: list[uuid.UUID] = field(default_factory=list)
    context_texts: list[str] = field(default_factory=list)
    extraction_id: uuid.UUID | None = None
    # 文脈の内訳（financial_facts の項目・期・単位の根拠に使う）
    row_label_id: uuid.UUID | None = None
    section_ids: list[uuid.UUID] = field(default_factory=list)
    column_ids: list[uuid.UUID] = field(default_factory=list)
    unit_id: uuid.UUID | None = None

    def line(self) -> str:
        if self.kind == "value":
            row = " > ".join([*self.section, self.row_label or ""]).strip(" >")
            return f"{self.ref} 行: {row} | 列: {self.column or '（見出しなし）'} | 値: {self.text}"
        return f"{self.ref} {self.text}"


@dataclass
class CandidateGroup:
    chunk_id: uuid.UUID
    rank: int
    kind: str
    page_number: int
    heading: str | None
    unit: str | None
    items: list[Candidate]
    # 資料（企業・資料名・対象期）。LLM が質問の企業・期間と照らし合わせるため
    source: str | None = None

    def render(self) -> str:
        head = f"## 候補群{self.rank}（{'表' if self.kind == 'table' else '本文'}、PDF {self.page_number}ページ）"
        if self.heading:
            head += f" {self.heading}"
        if self.unit:
            head += f" {self.unit}"
        source = [f"資料: {self.source}"] if self.source else []
        return "\n".join([head, *source, *(c.line() for c in self.items)])


def _squash(text: str) -> str:
    return " ".join(text.split())


async def with_table_siblings(
    session: AsyncSession, chunk_ids: list[uuid.UUID], terms: list[str], limit: int = MAX_SIBLINGS
) -> list[uuid.UUID]:
    """ヒットした表の、ほかの部分（同じ表の別チャンク）で質問の語を含むものを、ヒットの直後に足す。

    大きな表は行の範囲で複数チャンクに分かれる。例: 有利子負債は流動負債と非流動負債の両方に行があり、
    検索で片方しか上位に入らないと、LLM が一部の値だけで答えてしまう。
    """
    words = [w for w in (normalize(t) for t in terms) if w]
    if not words or not chunk_ids:
        return chunk_ids
    table_of = dict(
        (
            await session.execute(
                select(ChunkElement.chunk_id, Element.id)
                .join(Element, Element.id == ChunkElement.element_id)
                .where(
                    ChunkElement.chunk_id.in_(chunk_ids),
                    ChunkElement.role == "title",
                    Element.kind == "table",
                )
            )
        ).all()
    )
    if not table_of:
        return chunk_ids
    has_word = or_(*(Chunk.search_text.like(f"%{w}%") for w in words))
    siblings = (
        await session.execute(
            select(ChunkElement.element_id, Chunk.id)
            .join(Chunk, Chunk.id == ChunkElement.chunk_id)
            .where(
                ChunkElement.element_id.in_(set(table_of.values())),
                ChunkElement.role == "title",
                Chunk.id.not_in(chunk_ids),
                has_word,
            )
            .order_by(Chunk.seq)
        )
    ).all()
    by_table: dict[uuid.UUID, list[uuid.UUID]] = {}
    for table_id, cid in siblings:
        by_table.setdefault(table_id, []).append(cid)
    out: list[uuid.UUID] = []
    added: set[uuid.UUID] = set()
    for cid in chunk_ids:
        out.append(cid)
        for sib in by_table.get(table_of.get(cid), []):  # type: ignore[arg-type]
            if sib not in added and len(added) < limit:
                added.add(sib)
                out.append(sib)
    return out


async def build_candidates(
    session: AsyncSession, chunk_ids: list[uuid.UUID], budget: int = MAX_CANDIDATE_TOKENS
) -> list[CandidateGroup]:
    """検索順位の順に、チャンクごとの候補群を作る。同じ要素は順位の高いチャンクにだけ入れる"""
    chunks = {c.id: c for c in await session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))}
    parts = (
        await session.execute(
            select(ChunkElement, Element)
            .join(Element, Element.id == ChunkElement.element_id)
            .where(ChunkElement.chunk_id.in_(chunk_ids))
            .order_by(ChunkElement.chunk_id, ChunkElement.start_offset)
        )
    ).all()
    by_chunk: dict[uuid.UUID, list[tuple[ChunkElement, Element]]] = {}
    for ce, el in parts:
        by_chunk.setdefault(ce.chunk_id, []).append((ce, el))

    # 表のセルの位置と、同じ表の全セル（行見出し・区分を求めるため）
    cell_ids = [el.id for _, el in parts if el.kind == "cell"]
    cells = {
        c.element_id: c
        for c in await session.scalars(select(TableCell).where(TableCell.element_id.in_(cell_ids)))
    }
    table_ids = {c.table_element_id for c in cells.values()}
    table_cells = (
        await session.execute(
            select(TableCell, Element.raw_text)
            .join(Element, Element.id == TableCell.element_id)
            .where(TableCell.table_element_id.in_(table_ids))
        )
    ).all()
    grid: dict[uuid.UUID, dict[tuple[int, int], tuple[uuid.UUID, str]]] = {}
    for tc, text in table_cells:
        grid.setdefault(tc.table_element_id, {})[(tc.row_index, tc.col_index)] = (tc.element_id, text)
    header_text = {el.id: el.raw_text for _, el in parts}
    for tc, text in table_cells:
        header_text.setdefault(tc.element_id, text)
    page_numbers = {
        p.id: p.page_number
        for p in await session.scalars(select(Page).where(Page.id.in_({c.page_id for c in chunks.values()})))
    }

    def section_of(table_id: uuid.UUID, row: int) -> list[tuple[uuid.UUID, str]]:
        """行より上にある「値のない科目行」（例: 負債 > 流動負債）を近い順に最大 2 つ"""
        g = grid[table_id]
        out: list[tuple[uuid.UUID, str]] = []
        for r in range(row - 1, -1, -1):
            label = g.get((r, 0))
            values = [g[(r, c)][1] for c in range(1, 20) if (r, c) in g and g[(r, c)][1].strip()]
            if label and label[1].strip() and not values:
                out.insert(0, (label[0], _squash(label[1])))
                if len(out) == 2:
                    break
            elif values and out:
                break
        return out

    groups: list[CandidateGroup] = []
    seen: set[uuid.UUID] = set()
    used = 0
    n = 0
    for rank, cid in enumerate(chunk_ids, start=1):
        chunk = chunks.get(cid)
        if chunk is None:
            continue
        rows = by_chunk.get(cid, [])
        unit = next((el for ce, el in rows if ce.role == "unit"), None)
        title = next((el for ce, el in rows if ce.role == "title" and el.kind != "table"), None)
        items: list[Candidate] = []
        for ce, el in rows:
            if ce.role != "content" or el.id in seen:
                continue
            if el.kind == "line":
                items.append(
                    Candidate(
                        "",
                        el.id,
                        "text",
                        _squash(el.raw_text),
                        chunk.page_number,
                        rank,
                        extraction_id=chunk.extraction_id,
                    )
                )
            elif el.kind == "cell" and el.id in cells and cells[el.id].col_index > 0 and el.raw_text.strip():
                cell = cells[el.id]
                g = grid.get(cell.table_element_id, {})
                row_label = g.get((cell.row_index, 0))
                sections = section_of(cell.table_element_id, cell.row_index)
                col_ids = [
                    uuid.UUID(h) for h in cell.header_ids if not row_label or uuid.UUID(h) != row_label[0]
                ]
                column = " ".join(_squash(header_text.get(h, "")) for h in col_ids).strip() or None
                context = [*(s[0] for s in sections), *([row_label[0]] if row_label else []), *col_ids]
                context += [x.id for x in (unit, title) if x is not None]
                items.append(
                    Candidate(
                        "",
                        el.id,
                        "value",
                        _squash(el.raw_text),
                        chunk.page_number,
                        rank,
                        title=_squash(title.raw_text) if title else chunk.heading,
                        unit=_squash(unit.raw_text) if unit else None,
                        section=[s[1] for s in sections],
                        row_label=_squash(row_label[1]) if row_label else None,
                        column=column,
                        context_ids=context,
                        context_texts=[
                            *(s[1] for s in sections),
                            *([_squash(row_label[1])] if row_label else []),
                            *([column] if column else []),
                            *([_squash(unit.raw_text)] if unit else []),
                            *(
                                [_squash(title.raw_text)]
                                if title
                                else ([chunk.heading] if chunk.heading else [])
                            ),
                        ],
                        extraction_id=chunk.extraction_id,
                        row_label_id=row_label[0] if row_label else None,
                        section_ids=[s[0] for s in sections],
                        column_ids=col_ids,
                        unit_id=unit.id if unit else None,
                    )
                )
        if not items:
            continue
        group = CandidateGroup(
            cid,
            rank,
            chunk.kind,
            page_numbers.get(chunk.page_id, chunk.page_number),
            chunk.heading,
            _squash(unit.raw_text) if unit else None,
            items,
        )
        cost = estimate_tokens(group.render())
        # 根拠の一式（チャンク）を分断しない。入りきらないチャンクは丸ごと外す
        if used + cost > budget:
            continue
        used += cost
        for c in items:
            n += 1
            c.ref = f"E{n}"
            seen.add(c.element_id)
        groups.append(group)
    return groups
