"""計算（設計書 §10.5、段階 6）。

- 許可する演算は sum（合計）と subtract（差: 1 番目 − 2 番目）だけ。式は構造化した AST で保存し、eval は使わない。
- 入力は verified の financial_facts だけ。基準単位（円・人など）に換算して Decimal で計算する。
- 異なる通貨・量の種類は計算しない（為替換算は対象外）。
- 合計では、期が混在するもの・合計行と他の行の合算（二重計上の疑い）を拒否する。
- 表示は入力の単位（混在時は最も小さい単位）で行い、端数が出たときだけ小数 2 桁に ROUND_HALF_UP で丸める。
"""

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.doc_models import FinancialFact

OPERATIONS = ("sum", "subtract")
DISPLAY_PLACES = 2
_TOTAL = re.compile(r"(合計|小計|総計|計)$")


@dataclass
class CalcResult:
    operation: str
    expression: dict
    result: Decimal  # 基準単位
    display: Decimal  # 表示単位（丸め後）
    display_text: str
    unit_label: str | None
    multiplier: Decimal
    currency: str | None
    rounding: dict
    notes: list[str] = field(default_factory=list)


def _norm(text: str | None) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def format_decimal(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole, _, frac = f"{value:f}".partition(".")
    frac = frac.rstrip("0")
    return f"{sign}{int(whole):,}" + (f".{frac}" if frac else "")


def check_inputs(operation: str, facts: list[FinancialFact]) -> tuple[list[str], list[str]]:
    """計算できるか確かめる。戻り値は (選び直しで直せる問題, データの問題)

    選び直しで直せる問題（期の混在・二重計上など）は LLM に作り直させる。
    データの問題（数値・単位が特定できない）は needs_review にする。
    """
    fixable: list[str] = []
    data: list[str] = []
    if operation not in OPERATIONS:
        return [f"計算: 許可されていない演算です: {operation}"], []
    if operation == "subtract" and len(facts) != 2:
        fixable.append("計算: subtract の入力はちょうど 2 つにしてください（1 番目 − 2 番目）")
    if len({f.id for f in facts}) != len(facts):
        fixable.append("計算: 同じ値が重複しています")
    for f in facts:
        if f.review_status != "verified" or f.normalized_value is None:
            data.append(f"「{f.label_path}」{f.raw_value}: {'、'.join(f.issues) or '検証されていない値です'}")
    if data:
        return fixable, data
    if len({f.quantity for f in facts}) > 1:
        fixable.append("計算: 金額・人数・割合など種類の異なる値を計算しようとしています")
    if len({f.currency for f in facts}) > 1:
        data.append("通貨の異なる値は計算しません（為替換算は対象外）")
    if any(f.quantity == "percent" for f in facts):
        fixable.append("計算: 割合（%）の合算・差し引きはしません")
    if operation == "sum":
        periods = {_norm(f.period_label) for f in facts}
        if len(periods) > 1:
            fixable.append("計算: 合計する値の期（列）が混在しています。同じ期の値を選んでください")
        for total, part in overlapping(facts):
            fixable.append(
                f"計算: 合計行「{total.label_path}」とその内訳「{part.label_path}」を合算しています（二重計上）"
            )
    return fixable, data


def _path(f: FinancialFact) -> list[str]:
    return [_norm(p) for p in (f.label_path or "").split(">") if _norm(p)]


def overlapping(facts: list[FinancialFact]) -> list[tuple[FinancialFact, FinancialFact]]:
    """合計行と、その内訳の行の組（同時に合算すると二重計上になる）。

    合計行の対象は、科目名から「合計・小計・計」を除いた名前（例: 流動負債合計 → 流動負債）。
    名前が残らない「合計」「総計」は表全体の合計とみなす。内訳は、その名前を区分（上位の科目行）に持つ行。
    「流動資産合計＋非流動資産合計」のように、別々の合計どうしは重複しない。
    """
    pairs = []
    for total in facts:
        path = _path(total)
        if not path or not _TOTAL.search(path[-1]):
            continue
        base = _TOTAL.sub("", path[-1])
        for part in facts:
            if part is total:
                continue
            sections = _path(part)[:-1]
            if not base or base in sections:
                pairs.append((total, part))
    return pairs


def compute(operation: str, facts: list[FinancialFact]) -> CalcResult:
    """check_inputs で問題がないことを確かめてから呼ぶ"""
    values = [Decimal(f.normalized_value) for f in facts]
    result = sum(values, Decimal(0)) if operation == "sum" else values[0] - values[1]
    # 表示単位: 入力がすべて同じ単位ならそれ、混在なら最も小さい単位
    unit_fact = min(facts, key=lambda f: Decimal(f.unit_multiplier))
    multiplier = Decimal(unit_fact.unit_multiplier)
    exact = result / multiplier
    places = Decimal(1).scaleb(-DISPLAY_PLACES)
    display = exact if exact == exact.to_integral_value() else exact.quantize(places, rounding=ROUND_HALF_UP)
    rounding = {"mode": "ROUND_HALF_UP", "places": DISPLAY_PLACES, "applied": display != exact}
    notes = []
    if len({f.unit_label for f in facts}) > 1:
        notes.append(f"単位の異なる値を基準単位に換算して計算し、{unit_fact.unit_label}で表示しています")
    expression = {"op": operation, "args": [{"fact": str(f.id)} for f in facts]}
    return CalcResult(
        operation=operation,
        expression=expression,
        result=result,
        display=display,
        display_text=format_decimal(display),
        unit_label=unit_fact.unit_label,
        multiplier=multiplier,
        currency=unit_fact.currency,
        rounding=rounding,
        notes=notes,
    )


def expression_text(operation: str, numbers: list[int]) -> str:
    sep = " + " if operation == "sum" else " − "
    return sep.join(f"[{n}]" for n in numbers)
