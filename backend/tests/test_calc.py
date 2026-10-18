"""段階 6（指標ポリシー・数値の解釈・Decimal 計算）のテスト。

完了条件: 各入力値と式を追跡できること。
"""

import uuid
from decimal import Decimal

import pytest

from app.doc_models import FinancialFact
from app.services.answers import calc, contract, facts, metrics, service
from app.services.answers.candidates import Candidate

# ---- 数値・単位・期の解釈 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("17,868,651", Decimal("17868651")),
        ("１７，８６８，６５１", Decimal("17868651")),
        ("△3,406,472", Decimal("-3406472")),
        ("▲12", Decimal("-12")),
        ("-1,000", Decimal("-1000")),
        ("(1,234)", Decimal("-1234")),
        ("（1,234）", Decimal("-1234")),
        ("6.5", Decimal("6.5")),
        ("12.3%", Decimal("12.3")),
    ],
)
def test_parse_number(raw, value):
    assert facts.parse_number(raw) == (value, None)


@pytest.mark.parametrize("raw", ["－", "—", "-", "", "注7", "(1,234"])
def test_parse_number_rejects_blank_and_text(raw):
    value, problem = facts.parse_number(raw)
    assert value is None and problem


def test_parse_unit_prefers_cell_level_sources():
    assert facts.parse_unit("1,000", None, None, "（単位：百万円）").label == "百万円"
    assert facts.parse_unit("367,990", "従 業 員 数 （人）", "第1四半期", "（単位：百万円）").label == "人"
    assert facts.parse_unit("540", "営業利益", "営業利益 億円", None).label == "億円"
    assert facts.parse_unit("6.5", "営業収益", "％", None).quantity == "percent"
    assert facts.parse_unit("1,000", "営業収益", "当期", None) is None
    # 通貨名だけの見出し（為替レートの行など）は単位とみなさない
    assert facts.parse_unit("150", "米ドル", "当期", None) is None
    unit = facts.parse_unit("1", None, None, "（単位：千円）")
    assert unit.multiplier == Decimal(1000) and unit.currency == "JPY"


def test_parse_period_end():
    assert str(facts.parse_period_end("当第１四半期 連結会計期間 （2026年６月30日）")) == "2026-06-30"
    assert facts.parse_period_end("2027/3月期 第1四半期 (2026/4-6月)") is None
    assert (
        str(facts.parse_period_end(None, "当第１四半期（2026年６月30日に終了した３ヶ月間）")) == "2026-06-30"
    )


def _cand(
    text,
    row="有利子負債",
    section=("負債", "流動負債"),
    column="当第１四半期 連結会計期間 （2026年６月30日）",
    unit="（単位：百万円）",
    ref="E1",
):
    return Candidate(
        ref,
        uuid.uuid4(),
        "value",
        text,
        9,
        1,
        unit=unit,
        section=list(section),
        row_label=row,
        column=column,
        context_texts=[*section, row, column, unit or ""],
        extraction_id=uuid.uuid4(),
        row_label_id=uuid.uuid4(),
        section_ids=[uuid.uuid4() for _ in section],
        column_ids=[uuid.uuid4()],
        unit_id=uuid.uuid4(),
    )


def test_build_fact_records_value_unit_period_and_evidence_ids():
    c = _cand("17,868,651")
    row = facts.build_fact(c, approved=True, scope="consolidated")
    assert row["review_status"] == "verified" and row["issues"] == []
    assert row["normalized_value"] == Decimal("17868651") * 10**6
    assert row["unit_label"] == "百万円" and row["currency"] == "JPY"
    assert row["label_path"] == "負債 > 流動負債 > 有利子負債"
    assert str(row["period_end"]) == "2026-06-30"
    # 項目・期・単位の根拠の要素 ID を残す
    assert row["label_element_ids"] == [str(x) for x in [*c.section_ids, c.row_label_id]]
    assert row["period_element_ids"] == [str(c.column_ids[0])]
    assert row["unit_element_ids"] == [str(c.unit_id)]


def test_build_fact_unverified_reasons():
    row = facts.build_fact(_cand("－"), approved=True, scope="consolidated")
    assert row["review_status"] == "unverified" and row["normalized_value"] is None
    row = facts.build_fact(_cand("1,000", unit=None), approved=True, scope="consolidated")
    assert any("単位" in i for i in row["issues"])
    row = facts.build_fact(_cand("1,000"), approved=False, scope="consolidated")
    assert any("承認" in i for i in row["issues"])


# ---- 計算 -----------------------------------------------------------------------------------------


def _fact(
    value,
    *,
    label="負債 > 流動負債 > 有利子負債",
    unit="百万円",
    mult=10**6,
    period="当期",
    quantity="money",
    currency="JPY",
    status="verified",
):
    v = Decimal(value)
    return FinancialFact(
        id=uuid.uuid4(),
        raw_value=f"{v:,}",
        display_value=v,
        normalized_value=v * mult,
        unit_label=unit,
        unit_multiplier=Decimal(mult),
        quantity=quantity,
        currency=currency,
        label_path=label,
        period_label=period,
        review_status=status,
        issues=[] if status == "verified" else ["単位を根拠から特定できません"],
    )


def test_sum_is_exact_decimal_with_traceable_expression():
    a = _fact("17868651")
    b = _fact("26084517", label="負債 > 非流動負債 > 有利子負債")
    assert calc.check_inputs("sum", [a, b]) == ([], [])
    r = calc.compute("sum", [a, b])
    assert r.result == Decimal("43953168") * 10**6
    assert r.display_text == "43,953,168" and r.unit_label == "百万円"
    assert r.expression == {"op": "sum", "args": [{"fact": str(a.id)}, {"fact": str(b.id)}]}
    assert r.rounding["applied"] is False


def test_subtract_order_and_negative_result():
    cur, prev = (
        _fact("13525400", label="営業収益合計"),
        _fact("13000000", label="営業収益合計", period="前期"),
    )
    assert calc.compute("subtract", [cur, prev]).display_text == "525,400"
    assert calc.compute("subtract", [prev, cur]).display_text == "-525,400"


def test_mixed_units_convert_to_smallest_unit():
    a = _fact("1", unit="億円", mult=10**8)
    b = _fact("50", unit="百万円", mult=10**6, label="負債 > 非流動負債 > 有利子負債")
    r = calc.compute("sum", [a, b])
    assert r.display_text == "150" and r.unit_label == "百万円" and r.notes


def test_rounding_half_up_when_fraction():
    a = _fact("0.005", unit="百万円")
    b = _fact("0.010", unit="百万円", label="x")
    r = calc.compute("sum", [a, b])
    assert r.display_text == "0.02" and r.rounding["applied"] is True


def test_check_inputs_rejects_mixed_periods_totals_and_kinds():
    a, b = _fact("1"), _fact("2", period="前期", label="y")
    assert any("期" in e for e in calc.check_inputs("sum", [a, b])[0])
    total = _fact("33238696", label="負債 > 流動負債 > 流動負債合計")
    assert any("二重計上" in e for e in calc.check_inputs("sum", [_fact("1"), total])[0])
    grand = _fact("100", label="合計")
    assert any("二重計上" in e for e in calc.check_inputs("sum", [_fact("1"), grand])[0])
    people = _fact("10", unit="人", mult=1, quantity="count", currency=None, label="z")
    assert any("種類" in e for e in calc.check_inputs("sum", [_fact("1"), people])[0])
    assert calc.check_inputs("subtract", [a])[0]
    assert calc.check_inputs("multiply", [a, b])[0]


def test_check_inputs_reports_unverified_as_data_issue():
    fixable, data = calc.check_inputs("sum", [_fact("1"), _fact("2", status="unverified", label="y")])
    assert fixable == [] and data and "単位" in data[0]


def test_currencies_are_not_mixed():
    usd = _fact("1", unit="百万米ドル", currency="USD", label="y")
    _, data = calc.check_inputs("sum", [_fact("1"), usd])
    assert any("通貨" in e for e in data)


# ---- 指標ポリシー ---------------------------------------------------------------------------------

DEBT = {
    "metric_key": "interest_bearing_debt",
    "name": "有利子負債",
    "aliases": ["有利子負債"],
    "policies": {
        "7203": {
            "policy_version": "1",
            "basis": "user_defined",
            "approved": True,
            "description": "流動＋非流動",
            "operation": "sum",
            "consolidation_scope": "consolidated",
            "items": [
                {"label": "有利子負債", "section": "流動負債"},
                {"label": "有利子負債", "section": "非流動負債"},
            ],
        }
    },
    "default": {"approved": False, "clarification": "範囲が未定義です"},
}


def test_bundled_metric_files_are_valid():
    ms = metrics.load_metrics()
    debt = metrics.match_metric("トヨタの有利子負債は？", ms)
    assert debt and debt["metric_key"] == "interest_bearing_debt"
    toyota = metrics.policy_for(debt, "7203")
    assert toyota.approved and toyota.basis == "user_defined" and len(toyota.items) == 2
    assert not metrics.policy_for(debt, "7267").approved
    assert metrics.match_metric("営業利益は？", ms) is None


def _selection(cands, indexes, op="sum"):
    out = {
        "status": "candidate",
        "claims": [{"kind": "value", "evidence_id": c.ref, "note": "有利子負債"} for c in cands],
        "calculation": {"operation": op, "claim_indexes": indexes} if indexes else None,
        "clarification": None,
        "missing_requirements": [],
    }
    sel, errors = contract.validate(out, {c.ref: c for c in cands}, "トヨタの直近の有利子負債はいくら？")
    assert errors == []
    return sel


def test_policy_accepts_exact_items():
    policy = metrics.policy_for(DEBT, "7203")
    cur = _cand("17,868,651", ref="E1")
    non = _cand("26,084,517", section=("非流動負債",), ref="E2")
    assert metrics.check_selection(policy, _selection([cur, non], [0, 1])) == []


def test_policy_rejects_missing_extra_or_no_calculation():
    policy = metrics.policy_for(DEBT, "7203")
    cur = _cand("17,868,651", ref="E1")
    non = _cand("26,084,517", section=("非流動負債",), ref="E2")
    other = _cand("5,683,767", row="営業債務及びその他の債務", ref="E3")
    assert any(
        "非流動負債 > 有利子負債" in e for e in metrics.check_selection(policy, _selection([cur], None))
    )
    errors = metrics.check_selection(policy, _selection([cur, non, other], [0, 1, 2]))
    assert any("E3" in e and "定義にない" in e for e in errors)
    prev = _cand("17,581,104", column="前連結会計年度 （2026年３月31日）", ref="E4")
    errors = metrics.check_selection(policy, _selection([cur, prev, non], [0, 1, 2]))
    assert any("複数" in e for e in errors)


def test_unapproved_policy_asks_for_definition():
    policy = metrics.policy_for(DEBT, "7267")
    assert not policy.approved and policy.clarification


def test_validate_selection_rejects_mixed_period_sum_before_answering():
    cur = _cand("17,868,651", ref="E1")
    prev = _cand("25,624,365", section=("非流動負債",), column="前連結会計年度 （2026年３月31日）", ref="E2")
    cands = {c.ref: c for c in (cur, prev)}
    fs = {
        c.element_id: FinancialFact(**facts.build_fact(c, approved=True, scope="consolidated"))
        for c in cands.values()
    }
    output = {
        "status": "candidate",
        "claims": [{"kind": "value", "evidence_id": r, "note": "有利子負債"} for r in cands],
        "calculation": {"operation": "sum", "claim_indexes": [0, 1]},
        "clarification": None,
        "missing_requirements": [],
    }
    sel, errors = service.validate_selection(
        output, cands, "有利子負債", metrics.policy_for(DEBT, "7203"), fs
    )
    assert sel is None and any("期" in e for e in errors)


def test_policy_block_is_given_to_llm():
    text = service.build_input("有利子負債は？", [], None, metrics.policy_for(DEBT, "7203"))
    assert "指標の定義" in text and "非流動負債 > 有利子負債" in text and "利用者が決めた定義" in text


def test_separate_subtotals_are_not_double_counted():
    current = _fact("1", label="資産 > 流動資産 > 流動資産合計")
    non_current = _fact("2", label="非流動資産 > 非流動資産合計")
    assert calc.check_inputs("sum", [current, non_current]) == ([], [])
