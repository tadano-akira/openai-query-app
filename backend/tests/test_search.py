"""段階 4（検索）の純粋な処理のテスト。実際の検索精度は `python -m app.cli eval-search` で測る。"""

import uuid

from app.services.search import chunking, retrieval
from app.services.search.chunking import CellIn, LineIn, PageIn, TableIn


def _id() -> uuid.UUID:
    return uuid.uuid4()


def test_normalize_joins_letter_spaced_japanese_but_keeps_other_spaces():
    assert chunking.normalize("研 究 開 発 費 （億円）") == "研究開発費(億円)"
    assert chunking.normalize("Toyota  Motor ＴＯＹＯＴＡ") == "toyota motor toyota"
    assert chunking.normalize("短期借入金 1,000") == "短期借入金 1,000"


def test_rrf_sums_reciprocal_ranks():
    a, b, c = _id(), _id(), _id()
    scores = retrieval.rrf([[a, b], [b, c]], k=60)
    assert scores[b] == 1 / 61 + 1 / 62
    assert scores[a] == 1 / 61 and scores[c] == 1 / 62
    assert max(scores, key=scores.get) == b


def test_query_terms_and_synonym_expansion():
    terms, expanded = retrieval.query_terms(
        "トヨタの有利子負債はいくら？", {"有利子負債": ["借入金", "社債"]}
    )
    assert "有利子負債" in terms and "トヨタ" in terms
    assert expanded == ["借入金", "社債"]


def test_bundled_synonyms_file_is_valid():
    syn = retrieval.load_synonyms()
    assert "有利子負債" in syn and all(isinstance(v, list) for v in syn.values())


def _line(seq, text, y, x0=50.0, x1=300.0) -> LineIn:
    return LineIn(_id(), seq, text, (x0, y, x1, y + 10))


def test_running_headers_are_detected_but_units_are_kept():
    pages = [
        PageIn(
            _id(),
            n,
            (
                _line(1, "架空自動車(株) 2027年3月期 決算短信", 10),
                _line(2, "（単位：百万円）", 30),
                _line(3, f"本文{n}", 60),
            ),
            (),
        )
        for n in range(1, 6)
    ]
    running = chunking.running_texts(pages)
    assert chunking.normalize("架空自動車(株) 2027年3月期 決算短信") in running
    assert not any("単位" in t for t in running)


def test_text_chunks_split_overlap_and_track_offsets():
    lines = tuple(_line(i, "あ" * 40 + f"{i:02d}", 20 * i) for i in range(1, 40))
    chunks = chunking.text_chunks(PageIn(_id(), 3, lines, ()))
    assert len(chunks) >= 2
    assert all(c.tokens <= chunking.MAX_TOKENS + 120 for c in chunks)
    # 前のチャンクの末尾が次のチャンクの先頭に重なる
    assert chunks[0].parts[-1].element_id in [p.element_id for p in chunks[1].parts[:3]]
    by_id = {ln.element_id: ln.text for ln in lines}
    for c in chunks:
        for p in c.parts:
            assert c.text[p.start : p.end] == by_id[p.element_id]


def test_table_chunk_repeats_title_unit_and_column_headers():
    h1, h2 = _id(), _id()
    cells = (
        CellIn(_id(), 0, 0, "", ()),
        CellIn(h1, 0, 1, "前連結会計年度\n（2026年３月31日）", ()),
        CellIn(h2, 0, 2, "当第１四半期", ()),
        CellIn(_id(), 1, 0, "有利子負債", ()),
        CellIn(_id(), 1, 1, "17,581,104", (h1,)),
        CellIn(_id(), 1, 2, "17,868,651", (h2,)),
    )
    title = _line(1, "１．要約四半期連結財政状態計算書", 50)
    unit = _line(2, "（単位：百万円）", 70, x0=400, x1=500)
    table = TableIn(_id(), (50, 90, 550, 200), cells)
    page = PageIn(_id(), 8, (title, unit), (table,))
    [chunk] = chunking.table_chunks(page, table)
    assert chunk.text.splitlines()[0] == "【表】１．要約四半期連結財政状態計算書 （単位：百万円）"
    assert (
        "有利子負債 | 前連結会計年度 （2026年３月31日）: 17,581,104 | 当第１四半期: 17,868,651" in chunk.text
    )
    roles = {p.role for p in chunk.parts}
    assert roles == {"title", "unit", "header", "content"}
    texts = {c.element_id: " ".join(c.text.split()) for c in cells}
    for p in chunk.parts:
        if p.element_id in texts:
            assert chunk.text[p.start : p.end] == texts[p.element_id]


def test_continued_table_inherits_previous_title():
    def table_page(n, with_title):
        h = _id()
        cells = (
            CellIn(h, 0, 1, "当期", ()),
            CellIn(_id(), 1, 0, "社債", ()),
            CellIn(_id(), 1, 1, "100", (h,)),
        )
        lines = (_line(1, "２．要約連結損益計算書", 50),) if with_title else ()
        return PageIn(_id(), n, lines, (TableIn(_id(), (50, 90, 550, 200), cells),))

    chunks = chunking.build_chunks([table_page(1, True), table_page(2, False)])
    tables = [c for c in chunks if c.kind == "table"]
    assert tables[0].heading == "２．要約連結損益計算書"
    assert tables[1].heading == "２．要約連結損益計算書（続き）"
