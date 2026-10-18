"""指標ポリシー（設計書 §10.4、段階 6）。

指標（例: 有利子負債）の算入項目を企業ごとにファイル（backend/metrics/*.json）で版管理する。
質問がポリシーのある指標を問う場合:
- 対象企業のポリシーが未定義・未承認なら、LLM を呼ばずに needs_clarification にする。
- 承認済みなら、定義を LLM に渡し、選ばれた根拠が定義の項目と過不足なく一致するかをサーバーが検証する。
"""

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.services.answers.contract import Selection

METRICS_DIR = Path(__file__).resolve().parents[3] / "metrics"


def _norm(text: str | None) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split()).lower()


@dataclass
class PolicyItem:
    label: str
    section: str | None = None

    def matches(self, row_label: str | None, sections: list[str]) -> bool:
        if _norm(row_label) != _norm(self.label):
            return False
        return self.section is None or _norm(self.section) in {_norm(s) for s in sections}

    def describe(self) -> str:
        return f"{self.section} > {self.label}" if self.section else self.label


@dataclass
class Policy:
    metric_key: str
    name: str
    issuer_key: str
    approved: bool
    policy_version: str | None = None
    basis: str | None = None
    description: str | None = None
    consolidation_scope: str | None = None
    operation: str | None = None
    items: list[PolicyItem] = field(default_factory=list)
    clarification: str | None = None
    approved_on: str | None = None
    # ポリシーファイルの内容のハッシュ（回答時点の定義を特定する）
    file_hash: str = ""

    def snapshot(self) -> dict:
        return asdict(self)

    def llm_block(self) -> str:
        items = "\n".join(f"- {i.describe()}" for i in self.items)
        op = {"sum": "合計", "subtract": "差"}.get(self.operation or "", self.operation)
        return (
            f"# 指標の定義（必ず従う）\n"
            f"「{self.name}」は次の項目の{op}とする（{'利用者が決めた定義' if self.basis == 'user_defined' else '資料記載の定義'}）。\n"
            f"{self.description}\n{items}\n"
            f"各項目について、質問の期に当たる列の値を 1 つずつ kind=value で選び、calculation の operation を "
            f"{self.operation}、claim_indexes をそれらの番号にする。定義にない項目を足さない。"
        )


def load_metrics() -> list[dict]:
    out = []
    for p in sorted(METRICS_DIR.glob("*.json")):
        raw = p.read_bytes()
        data = json.loads(raw)
        data["_hash"] = hashlib.sha256(raw).hexdigest()[:12]
        out.append(data)
    return out


def match_metric(question: str, metrics: list[dict]) -> dict | None:
    """質問に指標の別名が含まれていれば、その指標（最も長い別名が一致したもの）"""
    q = _norm(question)
    best: tuple[int, dict] | None = None
    for m in metrics:
        for a in m.get("aliases", []):
            if _norm(a) and _norm(a) in q and (best is None or len(a) > best[0]):
                best = (len(a), m)
    return best[1] if best else None


def policy_for(metric: dict, issuer_key: str) -> Policy:
    p = metric.get("policies", {}).get(issuer_key) or metric.get("default", {})
    return Policy(
        metric_key=metric["metric_key"],
        name=metric["name"],
        issuer_key=issuer_key,
        approved=bool(p.get("approved")) and bool(p.get("items")),
        policy_version=p.get("policy_version"),
        basis=p.get("basis"),
        description=p.get("description"),
        consolidation_scope=p.get("consolidation_scope"),
        operation=p.get("operation"),
        items=[PolicyItem(i["label"], i.get("section")) for i in p.get("items", [])],
        clarification=p.get("clarification"),
        approved_on=p.get("approved_on"),
        file_hash=metric.get("_hash", ""),
    )


def check_selection(policy: Policy, sel: Selection) -> list[str]:
    """選ばれた根拠と計算が、ポリシーの項目と過不足なく一致するか"""
    if sel.status != "candidate":
        return []
    errors: list[str] = []
    calc = sel.calculation
    if not calc or calc.get("operation") != policy.operation:
        errors.append(
            f"定義: {policy.name}は {policy.operation} の計算が必要です（calculation を指定してください）"
        )
        inputs = [c for c in sel.claims if c.kind == "value"]
    else:
        inputs = [sel.claims[i] for i in calc["claim_indexes"] if i < len(sel.claims)]
    used: set[int] = set()
    for item in policy.items:
        hits = [
            n
            for n, c in enumerate(inputs)
            if c.candidate.kind == "value" and item.matches(c.candidate.row_label, c.candidate.section)
        ]
        if not hits:
            errors.append(f"定義: 「{item.describe()}」の値が選ばれていません")
        elif len(hits) > 1:
            errors.append(f"定義: 「{item.describe()}」の値が複数選ばれています（期を 1 つに絞ってください）")
        used.update(hits)
    extra = [c.candidate.ref for n, c in enumerate(inputs) if n not in used]
    if extra:
        errors.append(f"定義: {', '.join(extra)} は{policy.name}の定義にない項目です")
    if policy.consolidation_scope == "consolidated":
        bad = [
            c.candidate.ref
            for c in inputs
            if "単体" in (c.candidate.title or "") + (c.candidate.column or "")
        ]
        if bad:
            errors.append(f"定義: {', '.join(bad)} は単体の値です（連結の値を選んでください）")
    return errors
