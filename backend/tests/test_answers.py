"""段階 5（根拠 ID の選択と検証）の純粋な処理のテスト。

完了条件: 不許可（候補にない）ID と根拠不足を拒否できること。
"""

import uuid

from app.services.answers import contract, scope, service
from app.services.answers.candidates import Candidate, CandidateGroup

QUESTION = "トヨタの直近の有利子負債はいくら？"


def _cands() -> dict[str, Candidate]:
    value = Candidate(
        "E1",
        uuid.uuid4(),
        "value",
        "17,868,651",
        12,
        1,
        title="要約四半期連結財政状態計算書",
        unit="（単位：百万円）",
        section=["負債", "流動負債"],
        row_label="有利子負債",
        column="当第1四半期連結会計期間 (2026年6月30日)",
        context_ids=[uuid.uuid4()],
        context_texts=[
            "負債",
            "流動負債",
            "有利子負債",
            "当第1四半期連結会計期間 (2026年6月30日)",
            "（単位：百万円）",
        ],
    )
    value2 = Candidate(
        "E2",
        uuid.uuid4(),
        "value",
        "26,084,517",
        12,
        1,
        section=["負債", "非流動負債"],
        row_label="有利子負債",
        column="当第1四半期連結会計期間 (2026年6月30日)",
        context_texts=["非流動負債", "有利子負債", "当第1四半期連結会計期間 (2026年6月30日)"],
    )
    label = Candidate("E3", uuid.uuid4(), "value", "注記7", 12, 1, row_label="有利子負債")
    text = Candidate("E4", uuid.uuid4(), "text", "販売台数は前年同期を上回った。", 3, 2)
    return {c.ref: c for c in (value, value2, label, text)}


def _out(status="candidate", claims=None, calculation=None, clarification=None, missing=None) -> dict:
    return {
        "status": status,
        "claims": claims if claims is not None else [],
        "calculation": calculation,
        "clarification": clarification,
        "missing_requirements": missing or [],
    }


def _claim(ref, kind="value", note="流動負債の有利子負債") -> dict:
    return {"kind": kind, "evidence_id": ref, "note": note}


def test_accepts_listed_ids():
    sel, errors = contract.validate(
        _out(claims=[_claim("E1"), _claim("E4", "text", "販売の記載")]), _cands(), QUESTION
    )
    assert errors == []
    assert [c.candidate.ref for c in sel.claims] == ["E1", "E4"]


def test_rejects_unknown_id():
    sel, errors = contract.validate(_out(claims=[_claim("E1"), _claim("E99")]), _cands(), QUESTION)
    assert sel is None
    assert any("E99" in e and "所属" in e for e in errors)


def test_rejects_raw_element_uuid_as_id():
    # 候補に載せていない要素（UUID を直接指定）も、run 内 ID ではないので拒否する
    sel, errors = contract.validate(_out(claims=[_claim(str(uuid.uuid4()))]), _cands(), QUESTION)
    assert sel is None and errors


def test_rejects_duplicate_id():
    sel, errors = contract.validate(_out(claims=[_claim("E1"), _claim("E1")]), _cands(), QUESTION)
    assert sel is None and any("重複" in e for e in errors)


def test_rejects_value_claim_on_non_amount_or_text():
    _, errors = contract.validate(_out(claims=[_claim("E3")]), _cands(), QUESTION)
    assert any("種類" in e for e in errors)
    _, errors = contract.validate(_out(claims=[_claim("E4")]), _cands(), QUESTION)
    assert any("種類" in e for e in errors)


def test_rejects_amount_written_in_note():
    _, errors = contract.validate(
        _out(claims=[_claim("E1", note="有利子負債は17,868,651百万円")]), _cands(), QUESTION
    )
    assert any("説明" in e for e in errors)


def test_note_may_use_digits_from_headers_and_question():
    sel, errors = contract.validate(
        _out(claims=[_claim("E1", note="2026年6月30日時点、第1四半期")]), _cands(), QUESTION
    )
    assert errors == [] and sel is not None


def test_rejects_candidate_without_evidence():
    _, errors = contract.validate(_out(claims=[]), _cands(), QUESTION)
    assert any("根拠が選ばれていません" in e for e in errors)


def test_accepts_insufficient_evidence_without_claims():
    sel, errors = contract.validate(
        _out("insufficient_evidence", missing=["2025年度の有利子負債"]), _cands(), QUESTION
    )
    assert errors == []
    assert sel.status == "insufficient_evidence" and sel.missing == ["2025年度の有利子負債"]


def test_needs_clarification_requires_question():
    _, errors = contract.validate(_out("needs_clarification"), _cands(), QUESTION)
    assert any("確認の質問" in e for e in errors)
    sel, errors = contract.validate(
        _out("needs_clarification", clarification="連結と単体のどちらですか？"), _cands(), QUESTION
    )
    assert errors == [] and sel.clarification


def test_calculation_must_reference_value_claims():
    ok = _out(
        claims=[_claim("E1"), _claim("E2", note="非流動負債の有利子負債")],
        calculation={"operation": "sum", "claim_indexes": [0, 1]},
    )
    sel, errors = contract.validate(ok, _cands(), QUESTION)
    assert errors == [] and sel.calculation["claim_indexes"] == [0, 1]

    out_of_range = _out(claims=[_claim("E1")], calculation={"operation": "sum", "claim_indexes": [0, 5]})
    assert any("範囲外" in e for e in contract.validate(out_of_range, _cands(), QUESTION)[1])

    with_text = _out(
        claims=[_claim("E1"), _claim("E4", "text", "販売")],
        calculation={"operation": "sum", "claim_indexes": [0, 1]},
    )
    assert any("kind=value" in e for e in contract.validate(with_text, _cands(), QUESTION)[1])


def test_rejects_malformed_output():
    assert contract.validate({"status": "candidate"}, _cands(), QUESTION)[0] is None
    assert contract.validate("E1", _cands(), QUESTION)[0] is None
    bad_status = _out("completed", claims=[_claim("E1")])
    assert contract.validate(bad_status, _cands(), QUESTION)[0] is None


def test_schema_is_strict_compatible():
    # OpenAI の strict JSON schema: object はすべて additionalProperties=false で、全項目 required
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(contract.SCHEMA)


def test_instructions_strip_comment_and_version_is_stable():
    text, version = service.load_instructions()
    assert "<!--" not in text and "E1" in text
    assert version == service.load_instructions()[1] and len(version) == 12


def test_build_input_lists_candidates_and_feedback():
    c = _cands()
    group = CandidateGroup(uuid.uuid4(), 1, "table", 12, "財政状態", "（単位：百万円）", [c["E1"], c["E2"]])
    text = service.build_input(QUESTION, [group], ["所属: E99 は候補にない ID です"])
    assert "E1 行: 負債 > 流動負債 > 有利子負債" in text and "値: 17,868,651" in text
    assert "前回の出力の問題" in text and "E99" in text


# ---- 対象企業の解決 ---------------------------------------------------------------------------

TOYOTA, HONDA_ID = uuid.uuid4(), uuid.uuid4()


def _companies(honda_registered=False):
    return [
        scope.Company("7203", "トヨタ自動車株式会社", ["トヨタ自動車", "トヨタ", "toyota", "7203"], TOYOTA),
        scope.Company(
            "7267",
            "本田技研工業",
            ["本田技研工業", "ホンダ", "honda", "7267"],
            HONDA_ID if honda_registered else None,
        ),
        scope.Company("7201", "日産自動車", ["日産自動車", "日産", "nissan", "7201"]),
    ]


def test_scope_narrows_to_mentioned_company():
    res = scope.resolve("トヨタの営業利益は？", _companies(True), [], {TOYOTA, HONDA_ID})
    assert res.issuer_ids == [TOYOTA] and not res.missing and res.clarification is None


def test_scope_rejects_unregistered_company():
    # 登録されていない企業を問われたら、別の企業の資料で答えない
    res = scope.resolve("ホンダの営業利益は？", _companies(), [], {TOYOTA})
    assert res.missing and "本田技研工業" in res.missing[0]


def test_scope_rejects_company_outside_selected_filter():
    res = scope.resolve("Hondaの営業利益は？", _companies(True), [TOYOTA], {TOYOTA, HONDA_ID})
    assert res.missing and "絞り込み" in res.missing[0]


def test_scope_asks_which_company_when_ambiguous():
    res = scope.resolve("営業利益は？", _companies(True), [], {TOYOTA, HONDA_ID})
    assert res.clarification and "トヨタ" in res.clarification
    single = scope.resolve("営業利益は？", _companies(True), [], {TOYOTA})
    assert single.clarification is None and not single.missing


def test_bundled_companies_file_is_valid():
    companies = scope.load_companies()
    assert {"7203", "7267", "7201"} <= set(companies)
    assert all(c["name"] and isinstance(c["aliases"], list) for c in companies.values())
