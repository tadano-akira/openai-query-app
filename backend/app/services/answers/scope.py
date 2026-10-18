"""質問の対象企業の解決（設計書 §10.1 scope_resolution）。

質問に企業名があれば、検索をその企業の資料に絞る。登録されていない企業を問われた場合や、
画面で絞り込んだ企業と質問の企業が食い違う場合は、検索せずに根拠不足として返す。
別の企業の資料から値を拾って答えることを防ぐ。
"""

import json
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.doc_models import Issuer

COMPANIES_PATH = Path(__file__).resolve().parents[3] / "search" / "companies.json"
_SUFFIXES = ("株式会社", "(株)", "㈱")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower().replace(" ", "")


def _base_name(name: str) -> str:
    n = unicodedata.normalize("NFKC", name)
    for s in _SUFFIXES:
        n = n.replace(unicodedata.normalize("NFKC", s), "")
    return n.strip()


@dataclass
class Company:
    key: str  # 証券コード、または登録企業の ID
    name: str
    aliases: list[str]
    issuer_id: uuid.UUID | None = None


@dataclass
class Resolution:
    issuer_ids: list[uuid.UUID] = field(default_factory=list)
    mentioned: list[str] = field(default_factory=list)
    # 根拠不足として返す理由（空なら検索に進む）
    missing: list[str] = field(default_factory=list)
    # 確認の質問（空なら不要）
    clarification: str | None = None


def load_companies() -> dict[str, dict]:
    return json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))["companies"]


async def known_companies(session: AsyncSession) -> list[Company]:
    """別名ファイルの企業と登録済みの企業（コードで突き合わせる）"""
    issuers = list(await session.scalars(select(Issuer)))
    by_code = {i.code: i for i in issuers if i.code}
    out: list[Company] = []
    for code, c in load_companies().items():
        issuer = by_code.pop(code, None)
        names = [c["name"], *c.get("aliases", []), code]
        if issuer:
            names.append(_base_name(issuer.name))
        out.append(Company(code, issuer.name if issuer else c["name"], names, issuer.id if issuer else None))
    claimed = {c.issuer_id for c in out}
    for i in issuers:
        if i.id not in claimed:
            out.append(Company(str(i.id), i.name, [_base_name(i.name), *([i.code] if i.code else [])], i.id))
    return out


def resolve(
    question: str, companies: list[Company], selected: list[uuid.UUID], in_scope: set[uuid.UUID]
) -> Resolution:
    """question の企業名から対象企業を決める。

    selected は画面で絞り込んだ企業、in_scope は検索対象の資料がある企業。
    """
    q = _norm(question)
    hits = [c for c in companies if any(a and _norm(a) in q for a in c.aliases)]
    res = Resolution(mentioned=[c.name for c in hits])
    if hits:
        for c in hits:
            if c.issuer_id is None or c.issuer_id not in in_scope:
                res.missing.append(f"{c.name}の資料が登録されていません（または検索対象になっていません）")
            elif selected and c.issuer_id not in selected:
                res.missing.append(f"{c.name}は絞り込みの対象外です")
            else:
                res.issuer_ids.append(c.issuer_id)
        return res
    res.issuer_ids = list(selected)
    targets = set(selected) & in_scope if selected else in_scope
    if len(targets) > 1:
        names = [c.name for c in companies if c.issuer_id in targets]
        res.clarification = f"どの企業についての質問ですか？（{'、'.join(names)}）"
    return res
