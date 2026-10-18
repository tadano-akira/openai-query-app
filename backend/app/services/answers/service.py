"""資料Q&A の回答（設計書 §10、段階 5・6）。

scope_resolution → retrieval → evidence_selection → validation → calculation → rendering の順に処理する。
- 検索（段階 4）で根拠の候補を取り、run 内 ID（E1…）を振って LLM に渡す。
- LLM は ID の選択と短い説明・計算の要否だけを JSON schema で返す。値・引用・ページ・計算結果はサーバーが作る。
- 検証に失敗した出力は採用せず、1 回だけ作り直させる。それでも失敗なら failed。
- 指標ポリシー（例: 有利子負債）のある質問は、承認済みの定義の項目と過不足なく一致する根拠だけを受け付ける。
- 計算は verified の financial_facts だけを入力に Decimal で行い、式と入力を calculations に残す。
未検証の値を確定した回答・計算結果として表示しない（その場合は needs_review）。
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import SessionLocal
from app.doc_models import (
    AnswerRun,
    Calculation,
    Claim,
    ClaimEvidence,
    Element,
    FinancialFact,
    LlmCall,
    RetrievalHit,
)
from app.models import utcnow
from app.services import openai_io, pricing
from app.services.answers import calc, contract, metrics, scope
from app.services.answers.candidates import Candidate, CandidateGroup, build_candidates, with_table_siblings
from app.services.answers.facts import ensure_facts
from app.services.chat import ChatError
from app.services.search import retrieval
from app.services.tracing import Tracer

log = logging.getLogger(__name__)

INSTRUCTIONS_PATH = Path(__file__).resolve().parents[3] / "search" / "answer_instructions.md"
MAX_ATTEMPTS = 2  # 初回＋作り直し 1 回（設計書 §10.3）
MAX_OUTPUT_TOKENS = 4000
# 設計書の初期値: 回答は 2 並列まで
_answer_slots = asyncio.Semaphore(2)

BASIS_LABELS = {"user_defined": "利用者が決めた定義", "document_defined": "資料記載の定義"}


def load_instructions() -> tuple[str, str]:
    text = re.sub(r"<!--.*?-->", "", INSTRUCTIONS_PATH.read_text(encoding="utf-8"), flags=re.DOTALL).strip()
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


def build_input(
    question: str,
    groups: list[CandidateGroup],
    feedback: list[str] | None,
    policy: metrics.Policy | None = None,
) -> str:
    parts = [f"# 質問\n{question}"]
    if policy:
        parts.append(policy.llm_block())
    parts += ["# 候補", *(g.render() for g in groups)]
    if feedback:
        parts.append("# 前回の出力の問題（直して出力し直すこと）\n" + "\n".join(f"- {e}" for e in feedback))
    return "\n\n".join(parts)


def empty_result(scope: list[dict], *, missing: list[str], clarification: str | None = None) -> dict:
    return {
        "answer_kind": None,
        "items": [],
        "calculation": None,
        "clarification": clarification,
        "missing": missing,
        "review_reasons": [],
        "scope": scope,
        "freshness_basis": "registered_documents_only",
    }


def policy_info(policy: metrics.Policy | None) -> dict | None:
    if policy is None:
        return None
    return {
        "metric_key": policy.metric_key,
        "name": policy.name,
        "basis": policy.basis,
        "basis_label": BASIS_LABELS.get(policy.basis or "", policy.basis),
        "policy_version": policy.policy_version,
        "description": policy.description,
        "items": [i.describe() for i in policy.items],
        "approved_on": policy.approved_on,
    }


def validate_selection(
    output: dict | None,
    cands: dict[str, Candidate],
    question: str,
    policy: metrics.Policy | None,
    facts: dict[uuid.UUID, FinancialFact],
) -> tuple[contract.Selection | None, list[str]]:
    """LLM の出力の検証（契約 → 指標の定義 → 計算の入力）。問題があれば作り直させる"""
    selection, errors = contract.validate(output, cands, question)
    if selection is None or selection.status != "candidate":
        return selection, errors
    if policy:
        errors += metrics.check_selection(policy, selection)
    if selection.calculation and not errors:
        inputs = [
            facts.get(selection.claims[i].candidate.element_id)
            for i in selection.calculation["claim_indexes"]
        ]
        if any(f is None for f in inputs):
            errors.append("計算: 計算の入力に数値として扱えない根拠があります")
        else:
            fixable, _ = calc.check_inputs(selection.calculation["operation"], inputs)  # type: ignore[arg-type]
            errors += fixable
    return (None, errors) if errors else (selection, [])


def render(
    run: AnswerRun,
    sel: contract.Selection,
    scope: list[dict],
    docs: dict[str, dict],
    facts: dict[uuid.UUID, FinancialFact],
    calculation: dict | None,
    review_reasons: list[str],
) -> dict:
    """回答をサーバーが原文から組み立てる（LLM の文章は note として補助的に表示するだけ）"""
    items = []
    for n, c in enumerate(sel.claims, start=1):
        cand: Candidate = c.candidate
        fact = facts.get(cand.element_id) if c.kind == "value" else None
        items.append(
            {
                "n": n,
                "kind": c.kind,
                "value": cand.text,
                "unit": cand.unit,
                "row": " > ".join([*cand.section, cand.row_label or ""]).strip(" >") or None,
                "column": cand.column,
                "title": cand.title,
                "page_number": cand.page_number,
                "note": c.note,
                "evidence_id": str(cand.element_id),
                "context_ids": [str(x) for x in cand.context_ids],
                "viewer_url": f"/viewer/evidence/{cand.element_id}",
                "fact": (
                    {
                        "id": str(fact.id),
                        "status": fact.review_status,
                        "unit": fact.unit_label,
                        "issues": fact.issues,
                    }
                    if fact
                    else None
                ),
                **docs.get(str(cand.element_id), {}),
            }
        )
    return {
        "answer_kind": run.answer_kind,
        "items": items,
        "calculation": calculation,
        "clarification": sel.clarification,
        "missing": sel.missing,
        "review_reasons": review_reasons,
        "policy": policy_info(metrics.Policy(**_policy_kwargs(run.metric_policy_snapshot)))
        if run.metric_policy_snapshot
        else None,
        "scope": scope,
        # 「最新」は登録資料の中での最新であり、公式サイト全体の最新性は保証しない
        "freshness_basis": "registered_documents_only",
    }


def _policy_kwargs(snapshot: dict) -> dict:
    data = dict(snapshot)
    data["items"] = [metrics.PolicyItem(**i) for i in data.get("items", [])]
    return data


def calculation_view(
    sel: contract.Selection, facts: dict[uuid.UUID, FinancialFact], result: calc.CalcResult | None
) -> dict:
    assert sel.calculation is not None
    numbers = [i + 1 for i in sel.calculation["claim_indexes"]]
    inputs = []
    for i in sel.calculation["claim_indexes"]:
        f = facts.get(sel.claims[i].candidate.element_id)
        inputs.append(
            {
                "n": i + 1,
                "fact_id": str(f.id) if f else None,
                "raw_value": sel.claims[i].candidate.text,
                "unit": f.unit_label if f else None,
                "normalized_value": calc.format_decimal(f.normalized_value)
                if f and f.normalized_value is not None
                else None,
                "period_label": f.period_label if f else None,
                "label": f.label_path if f else None,
                "status": f.review_status if f else "unverified",
            }
        )
    view = {
        "operation": sel.calculation["operation"],
        "claim_numbers": numbers,
        "expression": calc.expression_text(sel.calculation["operation"], numbers),
        "inputs": inputs,
        "status": "computed" if result else "unverified",
    }
    if result:
        view |= {
            "result_display": result.display_text,
            "unit": result.unit_label,
            "result_base": calc.format_decimal(result.result),
            "currency": result.currency,
            "rounding": result.rounding,
            "notes": result.notes,
        }
    return view


async def line_context(
    session: AsyncSession, element_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    """本文の行の前後 1 行（行の途中で文が切れていても読めるように、表示だけに使う）"""
    if not element_ids:
        return {}
    lines = list(await session.scalars(select(Element).where(Element.id.in_(element_ids))))
    out: dict[uuid.UUID, tuple[str, str]] = {}
    for el in lines:
        if el.line_seq is None:
            continue
        near = {
            n.line_seq: " ".join(n.raw_text.split())
            for n in await session.scalars(
                select(Element).where(
                    Element.page_id == el.page_id,
                    Element.kind == "line",
                    Element.line_seq.in_([el.line_seq - 1, el.line_seq + 1]),
                )
            )
        }
        out[el.id] = (near.get(el.line_seq - 1, ""), near.get(el.line_seq + 1, ""))
    return out


async def _record_call(call: LlmCall) -> None:
    async with SessionLocal() as s:
        s.add(call)
        await s.commit()


async def _select(
    client: AsyncOpenAI, tracer: Tracer, run: AnswerRun, instructions: str, user_input: str
) -> tuple[dict | None, str | None]:
    """LLM に根拠を選ばせる。戻り値は (出力 JSON, エラーコード)"""
    started = time.perf_counter()
    call = LlmCall(purpose="answer_selection", model=run.model, answer_run_id=run.id)
    with tracer.generation(
        name="answer.evidence_selection",
        model=run.model,
        input=user_input,
        metadata={"answer_run_id": str(run.id)},
    ) as gen:
        try:
            resp = await client.responses.create(
                model=run.model,
                instructions=instructions,
                input=user_input,
                store=False,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "evidence_selection",
                        "schema": contract.SCHEMA,
                        "strict": True,
                    }
                },
            )
        except Exception as e:  # noqa: BLE001 - 分類して記録する
            err = openai_io.classify_error(e)
            call.error_code = err.code
            call.latency_ms = round((time.perf_counter() - started) * 1000)
            await _record_call(call)
            gen.update(level="ERROR", status_message=f"{err.code}: {err.message}")
            return None, err.code
        usage = openai_io.parse_response(resp).usage
        call.input_tokens, call.output_tokens = usage.input_tokens, usage.output_tokens
        call.estimated_cost_usd = pricing.call_cost_usd(
            run.model, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens
        )
        call.latency_ms = round((time.perf_counter() - started) * 1000)
        await _record_call(call)
        gen.update(
            output=resp.output_text,
            usage_details={"input": usage.input_tokens or 0, "output": usage.output_tokens or 0},
        )
    try:
        return json.loads(resp.output_text), None
    except (json.JSONDecodeError, TypeError):
        return None, "invalid_json"


async def answer(
    session: AsyncSession,
    settings: Settings,
    tracer: Tracer,
    question: str,
    filters: retrieval.Filters,
    model: str | None,
) -> AnswerRun:
    model = model or settings.openai_model
    if model not in settings.available_models:
        raise ChatError(422, "invalid_model", f"選択できないモデルです: {model}")
    instructions, version = load_instructions()
    run = AnswerRun(
        question=question.strip(), model=model, prompt_version=version, status="running", stage="retrieval"
    )
    session.add(run)
    await session.commit()
    t0 = time.perf_counter()
    timings: dict[str, int] = {}

    async def finish(status: str, *, error: tuple[str, str] | None = None) -> AnswerRun:
        run.status = status
        run.completed_at = utcnow()
        if error:
            run.error_code, run.error_message = error
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000)
        run.timings = dict(timings)
        await session.commit()
        return run

    async with _answer_slots:
        # 対象企業の解決: 質問の企業名で検索を絞る。資料のない企業を問われたら検索せずに返す
        run.stage = "scope_resolution"
        base = retrieval.Filters([], filters.document_types, filters.version_ids)
        targets = await retrieval.scope(session, settings, base)
        companies = await scope.known_companies(session)
        in_scope = {uuid.UUID(t["issuer_id"]) for t in targets}
        resolution = scope.resolve(run.question, companies, filters.issuer_ids, in_scope)
        timings["scope_ms"] = round((time.perf_counter() - t0) * 1000)
        if resolution.missing or resolution.clarification:
            run.result = empty_result(
                targets, missing=resolution.missing, clarification=resolution.clarification
            )
            run.result["mentioned_issuers"] = resolution.mentioned
            return await finish("insufficient_evidence" if resolution.missing else "needs_clarification")
        filters = retrieval.Filters(resolution.issuer_ids, filters.document_types, filters.version_ids)

        # 指標ポリシー: 定義が決まっていない指標は、根拠を探す前に利用者に確認する
        policy: metrics.Policy | None = None
        if metric := metrics.match_metric(run.question, metrics.load_metrics()):
            issuers = set(resolution.issuer_ids) or in_scope
            if len(issuers) == 1:
                issuer_id = next(iter(issuers))
                key = next((c.key for c in companies if c.issuer_id == issuer_id), str(issuer_id))
                policy = metrics.policy_for(metric, key)
                run.metric_policy_snapshot = policy.snapshot()
                if not policy.approved:
                    run.result = empty_result(targets, missing=[], clarification=policy.clarification)
                    run.result["policy"] = policy_info(policy)
                    return await finish("needs_clarification")

        # 検索（スコープの固定・機密情報検知・利用上限を含む）
        run.stage = "retrieval"
        try:
            search_run = await retrieval.search(session, settings, run.question, filters)
        except ChatError as e:
            await finish("failed", error=(e.code, e.message))
            raise
        run.search_run_id = search_run.id
        timings["retrieval_ms"] = round((time.perf_counter() - t0) * 1000)
        if search_run.status == "no_scope":
            run.result = empty_result(
                [], missing=["検索対象の資料（承認済み・既定・公式性確認済み）がありません"]
            )
            return await finish("insufficient_evidence")

        hits = (
            (
                await session.execute(
                    select(RetrievalHit.chunk_id)
                    .where(RetrievalHit.run_id == search_run.id)
                    .order_by(RetrievalHit.final_rank)
                )
            )
            .scalars()
            .all()
        )
        terms, _ = retrieval.query_terms(run.question, retrieval.load_synonyms())
        if policy:
            terms += [i.label for i in policy.items]
        groups = await build_candidates(session, await with_table_siblings(session, list(hits), terms))
        by_extraction = {t["extraction_id"]: t for t in search_run.scope_snapshot}
        for g in groups:
            t = by_extraction.get(str(g.items[0].extraction_id), {})
            g.source = (
                " ".join(x for x in (t.get("issuer"), t.get("title"), t.get("period_label")) if x) or None
            )
        cands = {c.ref: c for g in groups for c in g.items}
        run.stage = "evidence_selection"
        run.candidates = [
            {
                "ref": c.ref,
                "element_id": str(c.element_id),
                "kind": c.kind,
                "page_number": c.page_number,
                "text": c.text,
                "row": c.row_label,
                "section": c.section,
                "column": c.column,
                "unit": c.unit,
            }
            for c in cands.values()
        ]
        await session.commit()
        if not cands:
            run.result = empty_result(
                search_run.scope_snapshot, missing=["質問に関係する根拠の候補が見つかりませんでした"]
            )
            return await finish("insufficient_evidence")
        # 値の候補を数値として解釈しておく（計算の入力の検証に使う）
        facts = await ensure_facts(session, list(cands.values()))

        client = AsyncOpenAI(
            api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0
        )
        trace_id = tracer.new_trace_id(seed=str(run.id))
        run.langfuse_trace_id = trace_id
        selection: contract.Selection | None = None
        feedback: list[str] | None = None
        with tracer.run_span(
            trace_id=trace_id,
            session_id=str(run.id),
            name="doc-answer",
            input={"question": run.question, "candidates": len(cands)},
            metadata={
                "answer_run_id": str(run.id),
                "search_run_id": str(search_run.id),
                "prompt_version": version,
                "metric_policy": f"{policy.metric_key}@{policy.policy_version}" if policy else "",
            },
        ) as root:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                t = time.perf_counter()
                output, err = await _select(
                    client, tracer, run, instructions, build_input(run.question, groups, feedback, policy)
                )
                timings[f"selection_{attempt}_ms"] = round((time.perf_counter() - t) * 1000)
                run.stage = "validation"
                if err and err != "invalid_json":
                    root.update(level="ERROR", status_message=err)
                    return await finish("failed", error=(err, "根拠の選択（LLM 呼び出し）に失敗しました"))
                run.llm_outputs = [*run.llm_outputs, {"attempt": attempt, "output": output}]
                if err == "invalid_json":
                    selection, errors = None, ["構造: JSON として読めません"]
                else:
                    selection, errors = validate_selection(output, cands, run.question, policy, facts)
                if not errors:
                    break
                run.validation_errors = [*run.validation_errors, {"attempt": attempt, "errors": errors}]
                feedback = errors
                log.warning("answer %s attempt %d rejected: %s", run.id, attempt, errors)
            root.update(output={"status": selection.status if selection else "rejected"})

        if selection is None:
            return await finish(
                "failed",
                error=("EVIDENCE_REJECTED", "根拠の選択が検証に合格しませんでした（作り直しも不合格）"),
            )

        # 計算: verified の値だけを入力に Decimal で計算する。入力に未検証の値があれば結果を出さない
        run.stage = "calculation"
        review_reasons: list[str] = []
        calc_view = None
        status = {
            "insufficient_evidence": "insufficient_evidence",
            "needs_clarification": "needs_clarification",
        }.get(selection.status)
        if status is None:
            value_facts = [facts.get(c.candidate.element_id) for c in selection.claims if c.kind == "value"]
            if selection.calculation:
                op = selection.calculation["operation"]
                inputs = [
                    facts[selection.claims[i].candidate.element_id]
                    for i in selection.calculation["claim_indexes"]
                ]
                _, data_issues = calc.check_inputs(op, inputs)
                result = None
                if data_issues:
                    review_reasons += data_issues
                else:
                    result = calc.compute(op, inputs)
                    session.add(
                        Calculation(
                            run_id=run.id,
                            expression=result.expression,
                            input_fact_ids=[str(f.id) for f in inputs],
                            result=result.result,
                            result_display=result.display_text,
                            output_unit=result.unit_label,
                            output_multiplier=result.multiplier,
                            currency=result.currency,
                            rounding=result.rounding,
                            policy=policy.snapshot() if policy else None,
                        )
                    )
                calc_view = calculation_view(selection, facts, result)
                calc_view["policy"] = policy_info(policy)
                run.answer_kind = "calculated" if result else "calculation_unverified"
            else:
                run.answer_kind = "direct"
                review_reasons += [
                    f"「{f.label_path}」{f.raw_value}: {'、'.join(f.issues)}"
                    for f in value_facts
                    if f is not None and f.review_status != "verified"
                ]
            status = "needs_review" if review_reasons else "completed"

        run.stage = "rendering"
        for n, c in enumerate(selection.claims, start=1):
            fact = facts.get(c.candidate.element_id) if c.kind == "value" else None
            verification = (fact.review_status if fact else "unverified") if c.kind == "value" else "quoted"
            claim = Claim(run_id=run.id, seq=n, kind=c.kind, note=c.note, verification_status=verification)
            claim.evidence = [
                ClaimEvidence(
                    element_id=c.candidate.element_id,
                    role="value",
                    quote_start=0,
                    quote_end=len(c.candidate.text),
                )
            ]
            claim.evidence += [
                ClaimEvidence(element_id=x, role="context", quote_start=0, quote_end=0)
                for x in dict.fromkeys(c.candidate.context_ids)
                if x != c.candidate.element_id
            ]
            session.add(claim)
        docs = {str(c.element_id): doc_info(by_extraction.get(str(c.extraction_id))) for c in cands.values()}
        around = await line_context(
            session, [c.candidate.element_id for c in selection.claims if c.kind == "text"]
        )
        for el_id, (before, after) in around.items():
            docs.setdefault(str(el_id), {}).update(context_before=before, context_after=after)
        run.result = render(run, selection, search_run.scope_snapshot, docs, facts, calc_view, review_reasons)
        return await finish(status)


def doc_info(target: dict | None) -> dict:
    """根拠の資料（質問受付時点のスコープから）"""
    if not target:
        return {}
    return {
        "document_title": target["title"],
        "issuer": target["issuer"],
        "period_label": target.get("period_label"),
        "version_id": target["version_id"],
    }
