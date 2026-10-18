"""資料Q&A の回答（設計書 §10、段階 5）。

scope_resolution → retrieval → evidence_selection → validation → rendering の順に処理する。
- 検索（段階 4）で根拠の候補を取り、run 内 ID（E1…）を振って LLM に渡す。
- LLM は ID の選択と短い説明だけを JSON schema で返す。値・引用・ページはサーバーが原文から組み立てる。
- 検証に失敗した出力は採用せず、1 回だけ作り直させる。それでも失敗なら failed。
- 合算などの計算（段階 6）が必要な場合は、個々の値と根拠を示して needs_review にする。
未検証の値を確定した計算結果として表示しない。
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
from app.doc_models import AnswerRun, Claim, ClaimEvidence, LlmCall, RetrievalHit
from app.models import utcnow
from app.services import openai_io, pricing
from app.services.answers import contract, scope
from app.services.answers.candidates import Candidate, CandidateGroup, build_candidates, with_table_siblings
from app.services.chat import ChatError
from app.services.search import retrieval
from app.services.tracing import Tracer

log = logging.getLogger(__name__)

INSTRUCTIONS_PATH = Path(__file__).resolve().parents[3] / "search" / "answer_instructions.md"
MAX_ATTEMPTS = 2  # 初回＋作り直し 1 回（設計書 §10.3）
MAX_OUTPUT_TOKENS = 4000
# 設計書の初期値: 回答は 2 並列まで
_answer_slots = asyncio.Semaphore(2)


def load_instructions() -> tuple[str, str]:
    text = re.sub(r"<!--.*?-->", "", INSTRUCTIONS_PATH.read_text(encoding="utf-8"), flags=re.DOTALL).strip()
    return text, hashlib.sha256(text.encode()).hexdigest()[:12]


def build_input(question: str, groups: list[CandidateGroup], feedback: list[str] | None) -> str:
    parts = [f"# 質問\n{question}", "# 候補", *(g.render() for g in groups)]
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
        "scope": scope,
        "freshness_basis": "registered_documents_only",
    }


def render(run: AnswerRun, sel: contract.Selection, scope: list[dict], docs: dict[str, dict]) -> dict:
    """回答をサーバーが原文から組み立てる（LLM の文章は note として補助的に表示するだけ）"""
    items = []
    for n, c in enumerate(sel.claims, start=1):
        cand: Candidate = c.candidate
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
                **docs.get(str(cand.element_id), {}),
            }
        )
    calc = None
    if sel.calculation:
        calc = {
            "operation": sel.calculation["operation"],
            "claim_numbers": [i + 1 for i in sel.calculation["claim_indexes"]],
            "status": "pending",
            "message": "この質問には複数の値の計算が必要です。計算（Decimal による合算・差し引き）は次の段階で対応します。"
            "個々の値と根拠を確認してください。",
        }
    return {
        "answer_kind": run.answer_kind,
        "items": items,
        "calculation": calc,
        "clarification": sel.clarification,
        "missing": sel.missing,
        "scope": scope,
        # 「最新」は登録資料の中での最新であり、公式サイト全体の最新性は保証しない
        "freshness_basis": "registered_documents_only",
    }


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
        resolution = scope.resolve(
            run.question,
            await scope.known_companies(session),
            filters.issuer_ids,
            {uuid.UUID(t["issuer_id"]) for t in targets},
        )
        timings["scope_ms"] = round((time.perf_counter() - t0) * 1000)
        if resolution.missing or resolution.clarification:
            run.result = empty_result(
                targets, missing=resolution.missing, clarification=resolution.clarification
            )
            run.result["mentioned_issuers"] = resolution.mentioned
            return await finish("insufficient_evidence" if resolution.missing else "needs_clarification")
        filters = retrieval.Filters(resolution.issuer_ids, filters.document_types, filters.version_ids)

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
            },
        ) as root:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                t = time.perf_counter()
                output, err = await _select(
                    client, tracer, run, instructions, build_input(run.question, groups, feedback)
                )
                timings[f"selection_{attempt}_ms"] = round((time.perf_counter() - t) * 1000)
                run.stage = "validation"
                if err and err != "invalid_json":
                    root.update(level="ERROR", status_message=err)
                    return await finish("failed", error=(err, "根拠の選択（LLM 呼び出し）に失敗しました"))
                run.llm_outputs = [*run.llm_outputs, {"attempt": attempt, "output": output}]
                if err == "invalid_json":
                    errors = ["構造: JSON として読めません"]
                else:
                    selection, errors = contract.validate(output, cands, run.question)
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

        run.stage = "rendering"
        status = {
            "insufficient_evidence": "insufficient_evidence",
            "needs_clarification": "needs_clarification",
        }.get(selection.status)
        if status is None:
            run.answer_kind = "calculation_pending" if selection.calculation else "direct"
            status = "needs_review" if selection.calculation else "completed"
        for n, c in enumerate(selection.claims, start=1):
            claim = Claim(run_id=run.id, seq=n, kind=c.kind, note=c.note, verification_status="quoted")
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
        run.result = render(run, selection, search_run.scope_snapshot, docs)
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
