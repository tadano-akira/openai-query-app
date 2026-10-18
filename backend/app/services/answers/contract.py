"""LLM 出力の契約（JSON schema）とサーバー側の検証（設計書 §10.2・§10.3）。

LLM は run 内 ID の選択と短い説明だけを返す。任意の式・SQL・金額を返すフィールドは設けない。
検証で 1 つでも問題があれば、その出力は採用しない（呼び出し側が 1 回だけ作り直させる）。
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.services.answers.candidates import Candidate
from app.services.pdf.extract import _AMOUNT

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "claims", "calculation", "clarification", "missing_requirements"],
    "properties": {
        "status": {"type": "string", "enum": ["candidate", "insufficient_evidence", "needs_clarification"]},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "evidence_id", "note"],
                "properties": {
                    "kind": {"type": "string", "enum": ["value", "text"]},
                    "evidence_id": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "calculation": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operation", "claim_indexes"],
                    "properties": {
                        "operation": {"type": "string", "enum": ["sum", "subtract"]},
                        "claim_indexes": {"type": "array", "items": {"type": "integer"}},
                    },
                },
            ]
        },
        "clarification": {"anyOf": [{"type": "null"}, {"type": "string"}]},
        "missing_requirements": {"type": "array", "items": {"type": "string"}},
    },
}

_DIGITS = re.compile(r"\d+")


@dataclass
class ValidClaim:
    kind: str
    candidate: Candidate
    note: str


@dataclass
class Selection:
    status: str
    claims: list[ValidClaim] = field(default_factory=list)
    calculation: dict | None = None
    clarification: str | None = None
    missing: list[str] = field(default_factory=list)


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def validate(
    output: Any, candidates: dict[str, Candidate], question: str
) -> tuple[Selection | None, list[str]]:
    """出力を検証する。問題がなければ (Selection, [])、あれば (None, 問題の一覧)"""
    errors: list[str] = []
    # 構造（strict な JSON schema 出力でも、念のため型と値を確かめる）
    if not isinstance(output, dict) or set(output) != set(SCHEMA["required"]):
        return None, ["構造: 出力の項目が契約と一致しません"]
    status = output["status"]
    if status not in SCHEMA["properties"]["status"]["enum"]:
        return None, [f"構造: status が不正です: {status}"]
    claims_in = output["claims"] if isinstance(output["claims"], list) else []

    claims: list[ValidClaim] = []
    seen: set[str] = set()
    for i, c in enumerate(claims_in):
        if not isinstance(c, dict) or set(c) != {"kind", "evidence_id", "note"}:
            errors.append(f"構造: claims[{i}] の項目が契約と一致しません")
            continue
        ref, kind, note = c["evidence_id"], c["kind"], str(c["note"])
        # 所属: run 内で発行した候補 ID だけを許す（不許可・架空の ID を拒否）
        cand = candidates.get(ref)
        if cand is None:
            errors.append(f"所属: {ref} は候補にない ID です")
            continue
        if ref in seen:
            errors.append(f"重複: {ref} が複数回選ばれています")
            continue
        seen.add(ref)
        # 種類: 値は表の値のセルで、金額らしい原文であること
        if kind == "value" and (cand.kind != "value" or not _AMOUNT.match(_nfkc(cand.text).strip())):
            errors.append(f"種類: {ref} は数値のセルではないため kind=value にできません")
            continue
        if kind == "text" and cand.kind != "text":
            errors.append(f"種類: {ref} は表の値のため kind=value にしてください")
            continue
        # 説明: 根拠にない数字（金額など）を書かせない。質問・見出し・表題に出る数字だけ許す
        allowed = _nfkc(" ".join([question, *cand.context_texts]))
        bad = [d for d in _DIGITS.findall(_nfkc(note)) if d not in allowed]
        if bad:
            errors.append(f"説明: {ref} の note に根拠の見出しにない数字があります: {', '.join(bad)}")
            continue
        claims.append(ValidClaim(kind, cand, note.strip()))

    calc = output["calculation"]
    if calc is not None:
        if not isinstance(calc, dict) or calc.get("operation") not in ("sum", "subtract"):
            errors.append("計算: calculation の形式が不正です")
        else:
            idx = calc.get("claim_indexes") or []
            if len(idx) < 2 or any(not isinstance(x, int) or x < 0 or x >= len(claims_in) for x in idx):
                errors.append("計算: claim_indexes が claims の範囲外です")
            elif any(claims_in[x].get("kind") != "value" for x in idx):
                errors.append("計算: 計算に使えるのは kind=value の主張だけです")

    clarification = output["clarification"]
    if status == "candidate" and not claims_in:
        errors.append("状態: candidate なのに根拠が選ばれていません")
    if status == "needs_clarification" and not (isinstance(clarification, str) and clarification.strip()):
        errors.append("状態: needs_clarification なのに確認の質問がありません")
    if errors:
        return None, errors
    missing = [str(m) for m in output["missing_requirements"] if str(m).strip()]
    return Selection(
        status, claims, calc, clarification.strip() if isinstance(clarification, str) else None, missing
    ), []
