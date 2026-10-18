"""表の値のセルを数値（financial_facts）として解釈する（設計書 §8.3・段階 6）。

- 数値: 桁区切り・全角・負号（△ ▲ - − と括弧）を解析して Decimal にする。空欄・「—」は null（0 にしない）。
- 単位: 行見出し・列見出しの括弧書き、列見出しの単位語、表の「単位：」行から決める（セルに近いものを優先）。
- 期: 列見出し（なければ表題）。日付が書かれていれば期末日として読む。
verified にするのは、承認済み抽出版で、数値・単位・期のすべてが根拠から特定できたものだけ。
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.doc_models import DocumentVersion, Extraction, FinancialFact
from app.services.answers.candidates import Candidate

FACT_PARSER_VERSION = "facts-1"


@dataclass(frozen=True)
class Unit:
    label: str
    multiplier: Decimal
    quantity: str  # money / count / percent
    currency: str | None = None


# 長い表記から照合する（「百万円」を「円」と誤認しない）
_UNITS: list[tuple[str, Unit]] = [
    (label, unit)
    for label, unit in [
        ("兆円", Unit("兆円", Decimal(10) ** 12, "money", "JPY")),
        ("億円", Unit("億円", Decimal(10) ** 8, "money", "JPY")),
        ("百万円", Unit("百万円", Decimal(10) ** 6, "money", "JPY")),
        ("千円", Unit("千円", Decimal(10) ** 3, "money", "JPY")),
        ("円", Unit("円", Decimal(1), "money", "JPY")),
        ("百万米ドル", Unit("百万米ドル", Decimal(10) ** 6, "money", "USD")),
        ("百万ドル", Unit("百万ドル", Decimal(10) ** 6, "money", "USD")),
        ("千米ドル", Unit("千米ドル", Decimal(10) ** 3, "money", "USD")),
        ("千ドル", Unit("千ドル", Decimal(10) ** 3, "money", "USD")),
        ("米ドル", Unit("米ドル", Decimal(1), "money", "USD")),
        ("%", Unit("%", Decimal(1), "percent")),
        ("千人", Unit("千人", Decimal(10) ** 3, "count")),
        ("人", Unit("人", Decimal(1), "count")),
        ("千台", Unit("千台", Decimal(10) ** 3, "count")),
        ("台", Unit("台", Decimal(1), "count")),
    ]
]
# 括弧書きでなくても単位とみなす語（「円」「米ドル」単独は通貨名の可能性があるので除く）
_BARE_TOKENS = {"兆円", "億円", "百万円", "千円", "百万米ドル", "百万ドル", "千米ドル", "千ドル", "%"}
_PAREN = re.compile(r"[(（]([^()（）]{1,20})[)）]")
_UNIT_LINE = re.compile(r"単位\s*[:：]\s*([^\s)）]+)")
_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_NUMBER = re.compile(r"^([△▲\-−]?)\s*([(（]?)\s*(\d[\d,]*(?:\.\d+)?)\s*([)）]?)\s*(%?)$")
_BLANK = {"", "-", "－", "—", "―", "–", "‐", "ー"}


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _squash(text: str) -> str:
    return "".join(_nfkc(text).split())


def parse_number(raw: str) -> tuple[Decimal | None, str | None]:
    """原文の数値部分を Decimal にする。戻り値は (値, 問題)"""
    text = _squash(raw).replace("，", ",")
    if text in _BLANK:
        return None, "空欄または「—」のため数値として扱いません"
    m = _NUMBER.match(text)
    if not m:
        return None, f"数値として解釈できません: {raw}"
    sign, lp, digits, rp, _pct = m.groups()
    if bool(lp) != bool(rp):
        return None, f"括弧の対応が取れません: {raw}"
    try:
        value = Decimal(digits.replace(",", ""))
    except InvalidOperation:
        return None, f"数値として解釈できません: {raw}"
    return (-value if sign or lp else value), None


def _unit_in(text: str, *, bare: bool) -> Unit | None:
    t = _squash(text)
    segments = [m.group(1) for m in _UNIT_LINE.finditer(t)] + [m.group(1) for m in _PAREN.finditer(t)]
    for seg in segments:
        for label, unit in _UNITS:
            if seg == label or seg.endswith(label) and not seg.endswith("当たり" + label):
                return unit
    if bare:
        tokens = _nfkc(text).split()
        for label, unit in _UNITS:
            if label in _BARE_TOKENS and label in tokens:
                return unit
    return None


def parse_unit(
    raw_value: str, row_label: str | None, column: str | None, unit_line: str | None
) -> Unit | None:
    """セルに近い順（値 → 行見出し → 列見出し → 表の単位行）に単位を探す"""
    if "%" in _nfkc(raw_value):
        return _UNITS[[label for label, _ in _UNITS].index("%")][1]
    for text, bare in ((row_label, False), (column, True), (unit_line, True)):
        if text and (u := _unit_in(text, bare=bare)):
            return u
    return None


def parse_period_end(*texts: str | None) -> date | None:
    for text in texts:
        if text and (m := _DATE.search(_nfkc(text))):
            try:
                return date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                continue
    return None


def build_fact(c: Candidate, *, approved: bool, scope: str | None) -> dict:
    """候補（表の値のセル）から financial_facts の 1 行を作る"""
    issues: list[str] = []
    value, problem = parse_number(c.text)
    if problem:
        issues.append(problem)
    unit = parse_unit(c.text, c.row_label, c.column, c.unit)
    if unit is None:
        issues.append("単位を根拠から特定できません")
    if not c.column:
        issues.append("期（列見出し）を特定できません")
    if not approved:
        issues.append("抽出版が承認されていません")
    unit_ids = [c.unit_id] if c.unit_id else []
    return {
        "id": uuid.uuid4(),
        "extraction_id": c.extraction_id,
        "value_element_id": c.element_id,
        "label_element_ids": [
            str(x) for x in [*c.section_ids, *([c.row_label_id] if c.row_label_id else [])]
        ],
        "period_element_ids": [str(x) for x in c.column_ids],
        "unit_element_ids": [str(x) for x in unit_ids],
        "raw_value": c.text,
        "display_value": value,
        "normalized_value": value * unit.multiplier if value is not None and unit else None,
        "unit_label": unit.label if unit else None,
        "unit_multiplier": unit.multiplier if unit else None,
        "quantity": unit.quantity if unit else None,
        "currency": unit.currency if unit else None,
        "label_path": " > ".join([*c.section, c.row_label or ""]).strip(" >") or None,
        "period_label": c.column,
        "period_end": parse_period_end(c.column, c.title),
        "scope": scope,
        "review_status": "unverified" if issues else "verified",
        "issues": issues,
        "parser_version": FACT_PARSER_VERSION,
    }


async def ensure_facts(session: AsyncSession, candidates: list[Candidate]) -> dict[uuid.UUID, FinancialFact]:
    """値の候補の financial_facts を（なければ作って）返す。キーは値の要素 ID"""
    values = [c for c in candidates if c.kind == "value" and c.extraction_id]
    if not values:
        return {}
    ids = [c.element_id for c in values]
    existing = {
        f.value_element_id
        for f in await session.scalars(select(FinancialFact).where(FinancialFact.value_element_id.in_(ids)))
    }
    missing = [c for c in values if c.element_id not in existing]
    if missing:
        meta = {
            e_id: (approved is not None, scope)
            for e_id, approved, scope in (
                await session.execute(
                    select(Extraction.id, Extraction.approved_at, DocumentVersion.scope)
                    .join(DocumentVersion, DocumentVersion.id == Extraction.version_id)
                    .where(Extraction.id.in_({c.extraction_id for c in missing}))
                )
            ).all()
        }
        rows = [
            build_fact(c, approved=meta[c.extraction_id][0], scope=meta[c.extraction_id][1]) for c in missing
        ]
        # 同時に同じセルの事実を作っても 1 行にする
        await session.execute(
            insert(FinancialFact).values(rows).on_conflict_do_nothing(index_elements=["value_element_id"])
        )
        await session.commit()
    facts = await session.scalars(select(FinancialFact).where(FinancialFact.value_element_id.in_(ids)))
    return {f.value_element_id: f for f in facts}
