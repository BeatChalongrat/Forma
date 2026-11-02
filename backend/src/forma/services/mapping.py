"""Mapping lifecycle: memory lookup → heuristic draft → AI → simulation → policy → review/approve."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forma.ai import plausibility
from forma.ai.agents import PROMPT_VERSION, AgentResult, adaptation_agent, mapping_agent, schema_agent
from forma.ai.heuristics import VAT_ASSUMED_QUESTION_RE, HeuristicProposal, propose_mapping
from forma.ai.llm import LLMError
from forma.ai.policy import PolicyDecision
from forma.ai.workbench import FileWorkbench
from forma.core.errors import Conflict, NotFound, PolicyBlocked, ValidationFailed
from forma.core.hashing import stable_hash
from forma.core.ids import new_id
from forma.db.models import (
    REVIEW_UNRESOLVED,
    ImportRound,
    LLMCall,
    MappingVersion,
    Override,
    ReviewTask,
    SourceFile,
)
from forma.engine.transform import BoundSource, TransformEngine, TransformResult
from forma.mapping_dsl.models import MappingDocument, SourceSpec
from forma.mapping_dsl.validate import validate_mapping
from forma.profiling.profile import WorkbookProfile
from forma.schema_registry import TargetSchema
from forma.services import format_memory, healing, sandbox
from forma.services.context import ServiceContext
from forma.services.rounds import audit, get_round, round_profile


# --------------------------------------------------------------------------------------------
# Binding DSL sources to the round's files
# --------------------------------------------------------------------------------------------
def bind_sources(
    mapping: MappingDocument,
    files: list[SourceFile],
    *,
    resolve: Callable[[str], Path] | None = None,
) -> tuple[dict[str, BoundSource], list[str]]:
    """Resolve each DSL source to a physical file of the round. Returns (bindings, problems).

    ``resolve`` turns a storage key into a local path (``ctx.storage.local_path``); without it keys are
    taken as paths (unit tests on local files)."""
    resolve = resolve or Path
    bindings: dict[str, BoundSource] = {}
    problems: list[str] = []
    usable = [f for f in files if f.status == "profiled"]
    for s in mapping.sources:
        cand = _match_file(s, usable)
        if cand is None:
            problems.append(
                f"source {s.id!r}: no file in this round matches (pattern={s.file.name_pattern!r}, sheets={s.file.sheet_names_any})"
            )
            continue
        bindings[s.id] = BoundSource(resolve(cand.storage_key), None, cand.id, cand.original_name)
    return bindings, problems


def _match_file(s: SourceSpec, files: list[SourceFile]) -> SourceFile | None:
    if not files:
        return None
    if len(files) == 1 and not s.file.name_pattern and not s.file.sheet_names_any:
        return files[0]
    scored: list[tuple[float, SourceFile]] = []
    for f in files:
        score = 0.0
        if s.file.name_pattern:
            try:
                if re.search(s.file.name_pattern, f.original_name):
                    score += 2.0
            except re.error:
                pass
        if s.file.sheet_names_any and f.profile:
            names = {sh["name"].strip().lower() for sh in f.profile.get("meta", {}).get("sheets", [])}
            if any(n.strip().lower() in names for n in s.file.sheet_names_any):
                score += 1.5
        if s.file.fingerprint and f.fingerprint and f.fingerprint.get("strict") == s.file.fingerprint:
            score += 3.0
        if s.sheet and s.sheet.name and f.profile:
            names = {sh["name"].strip().lower() for sh in f.profile.get("meta", {}).get("sheets", [])}
            if s.sheet.name.strip().lower() in names:
                score += 1.0
        if f.kind == "csv" and s.sheet is None:
            score += 0.5
        scored.append((score, f))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return files[0] if len(files) == 1 else None


# --------------------------------------------------------------------------------------------
# Proposal
# --------------------------------------------------------------------------------------------
def _record_llm(session: Session, round_id: str | None, res: AgentResult) -> None:
    u = res.usage
    session.add(
        LLMCall(
            round_id=round_id,
            agent=res.name,
            model=u.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cost_usd=u.cost_usd,
            latency_ms=u.latency_ms,
            attempts=res.attempts,
        )
    )


def simulate(
    ctx: ServiceContext,
    mapping: MappingDocument,
    schema: TargetSchema,
    files: list[SourceFile],
    *,
    limit: int | None = 3000,
) -> tuple[TransformResult | None, list[str]]:
    bindings, problems = bind_sources(mapping, files, resolve=ctx.storage.local_path)
    if problems:
        return None, problems
    engine = TransformEngine(schema)
    return engine.run(mapping, bindings, limit=limit), []


def _risk(decision: PolicyDecision) -> dict[str, Any]:
    """What the review queue shows next to a task: low / medium / high and why (ADR-019)."""
    return {
        "risk_level": decision.risk_level,
        "risk_factors": decision.risk_factors,
        "impact": decision.impact,
    }


def active_overrides(session: Session, customer_id: str) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(Override).where(Override.customer_id == customer_id, Override.active.is_(True))
    ).all()
    return [
        {
            "id": o.id,
            "target": o.target,
            "where": o.where or {},
            "value": o.value,
            "author": o.author,
            "note": o.note,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in rows
    ]


def _sandbox(
    ctx: ServiceContext,
    schema: TargetSchema,
    doc: MappingDocument,
    baseline: MappingVersion | None,
    files: list[SourceFile],
    mapping_version_id: str,
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Run ``doc`` and the approved ``baseline`` on the same files (ADR-018). The drill-down lists are
    stored before the mapping row that points at them; the report itself goes into the row's policy."""
    if baseline is None or not ctx.policy.policy["sandbox"]["enabled"]:
        return None
    try:
        base_doc = MappingDocument.model_validate(baseline.document)
    except Exception:
        return None
    report, details = sandbox.run(
        ctx, schema, doc, base_doc, files, limit=ctx.settings.sandbox_max_rows or None, overrides=overrides
    )
    key = sandbox.details_key(baseline.customer_id, mapping_version_id)
    sandbox.store_details(ctx, key, details)
    report.update(baseline_id=baseline.id, baseline_version=baseline.version, details_key=key)
    return report


FIELD_REVIEW_ROWS = 5000  # records of the new mapping compared with last round's published data


def _field_review(
    ctx: ServiceContext,
    session: Session,
    schema: TargetSchema,
    baseline: MappingVersion,
    doc: MappingDocument,
    after: list[dict[str, Any]] | None,
    customer_id: str,
    round_id: str | None,
    *,
    check_unchanged: bool = False,
) -> dict[str, Any]:
    """Field-by-field changes vs the approved mapping, each checked on the data (ADR-021)."""
    from forma.services.datasets import iter_records, previous_published

    after = (after or [])[:FIELD_REVIEW_ROWS]
    before: list[dict[str, Any]] = []
    prev = previous_published(session, customer_id, round_id or "")
    if prev is not None and after:
        wanted = {sandbox._identity(r, schema) for r in after}
        before = [r for r in iter_records(ctx.records_path(prev)) if sandbox._identity(r, schema) in wanted]
    out = healing.review(
        schema,
        MappingDocument.model_validate(baseline.document),
        doc,
        before,
        after,
        risk_of=lambda p: ctx.policy.risk_of(schema, p),
        check_unchanged=check_unchanged,
    )
    out.update(
        baseline_id=baseline.id,
        baseline_version=baseline.version,
        compared_with=prev.id if prev is not None else None,
    )
    return out


def field_review(ctx: ServiceContext, session: Session, mapping_id: str) -> dict[str, Any]:
    """The Studio's "What changed" view: stored with the proposal, or computed for older versions."""
    m = get_mapping(session, mapping_id)
    stored = (m.policy or {}).get("field_review")
    if stored:
        return dict(stored)
    schema = ctx.registry.get_ref(m.target_schema)
    baseline = format_memory.select_baseline(session, m.customer_id, schema, None)
    if baseline is not None and baseline.id == m.id:
        baseline = session.get(MappingVersion, m.parent_id) if m.parent_id else None
    if baseline is None:
        return {"baseline_id": None, "fields": [], "checks": [], "needs_decision": [], "counts": {}}
    doc = MappingDocument.model_validate(m.document)
    r = session.get(ImportRound, m.round_id) if m.round_id else None
    sim, _ = simulate(ctx, doc, schema, r.files if r else [], limit=FIELD_REVIEW_ROWS)
    return _field_review(
        ctx, session, schema, baseline, doc, sim.records if sim else None, m.customer_id, m.round_id
    )


def _verdict(ctx: ServiceContext, report: dict[str, Any] | None) -> None:
    if report is not None:
        report["problems"] = ctx.policy.sandbox_problems(report)
        report["verdict"] = "fail" if report["problems"] else "pass" if report.get("delta") else "n/a"


def propose_for_round(
    ctx: ServiceContext,
    session: Session,
    round_id: str,
    *,
    use_llm: bool | None = None,
    actor: str = "system",
) -> MappingVersion:
    r = get_round(session, round_id)
    schema = ctx.registry.get_ref(r.target_schema)
    r.target_schema = schema.ref  # pin this round before defaults or latest schema aliases can change
    profile = round_profile(session, round_id)
    current_fp = format_memory.files_fingerprint(r.files)
    previous = format_memory.select_baseline(session, r.customer_id, schema, current_fp)
    previous_fp = format_memory.mapping_fingerprint(previous) if previous else None
    # Recheck at proposal time: a cached round summary can predate another approval or schema edit.
    from forma.fingerprint.fingerprint import compare

    drift = compare(previous_fp, current_fp).classification if previous_fp and current_fp else None
    next_version = (
        int(
            session.scalar(
                select(func.max(MappingVersion.version)).where(MappingVersion.customer_id == r.customer_id)
            )
            or 0
        )
        + 1
    )
    use_llm = (not ctx.llm.is_mock) if use_llm is None else use_llm
    agent_log: list[dict[str, Any]] = []
    open_questions: list[str] = []
    llm_fallback = False
    learning_note: dict[str, Any] | None = None
    file_fps = {f.id: f.fingerprint for f in r.files if f.fingerprint}

    # 1) Mapping memory: identical structure → reuse the approved mapping verbatim
    if previous is not None and drift in ("identical", "header_moved"):
        doc = MappingDocument.model_validate(previous.document)
        doc.meta.version = next_version
        doc.meta.confidence = 1.0
        doc.meta.created_by = "system"
        doc.meta.evidence = [
            f"reused approved mapping {previous.id} (v{previous.version}); structure {drift}"
        ]
        agent_log.append({"agent": "memory", "result": "reuse", "previous": previous.id, "drift": drift})
        source_kind = "memory"
    else:
        # 2) deterministic draft, reading what this customer's operators taught FORMA before (ADR-038)
        from forma.services import learning

        learned = learning.learned_for(session, r.customer_id, schema)
        draft: HeuristicProposal = propose_mapping(
            profile, schema, customer_id=r.customer_id, learned=None if learned.empty else learned
        )
        open_questions.extend(draft.open_questions)
        agent_log.append(
            {
                "agent": "heuristic",
                "confidence": draft.confidence,
                "table_roles": draft.table_roles,
                "unmatched": draft.unmatched_columns,
            }
        )
        if not learned.empty:
            used = _learned_pointers(draft.mapping)
            learning.count_uses(session, learned, used)
            learning_note = {**learned.summary(), "used": sorted(used)}
            agent_log.append({"agent": "learning", **learning_note})
        doc = draft.mapping
        source_kind = "heuristic"
        # 3) AI refinement, with the file to look at and the engine to try the mapping on (ADR-058)
        if use_llm:
            workbench = FileWorkbench(
                schema,
                profile,
                r.files,
                bind=lambda d: bind_sources(d, r.files, resolve=ctx.storage.local_path),
                resolve=ctx.storage.local_path,
                rows=ctx.settings.llm_self_check_rows,
                max_chars=ctx.settings.llm_max_sample_chars,
            )
            try:
                if previous is not None and drift not in (None, "unrelated_file"):
                    prev_doc = MappingDocument.model_validate(previous.document)
                    drift_detail = next((f.drift for f in r.files if f.drift), {"classification": drift})
                    doc, res = adaptation_agent(
                        ctx.llm,
                        prev_doc,
                        schema,
                        profile,
                        drift_detail or {},
                        draft,
                        learned=None if learned.empty else learned.for_prompt(),
                        workbench=workbench,
                    )
                    source_kind = "ai_adaptation"
                else:
                    sa = schema_agent(ctx.llm, profile, schema)
                    _record_llm(session, r.id, sa)
                    agent_log.append(sa.to_dict())
                    examples = _examples(session, r.customer_id, profile)
                    doc, res = mapping_agent(
                        ctx.llm,
                        profile,
                        schema,
                        draft,
                        schema_analysis=sa.data,
                        examples=examples,
                        learned=None if learned.empty else learned.for_prompt(),
                        workbench=workbench,
                    )
                    source_kind = "ai"
                _record_llm(session, r.id, res)
                agent_log.append(res.to_dict())
                open_questions = _without_settled(open_questions, doc, schema)
                open_questions.extend(
                    res.data.get("open_questions", []) if isinstance(res.data, dict) else []
                )
                if any(n.startswith("llm_failed") for n in res.notes):
                    llm_fallback = True
                    source_kind = "heuristic"
            except LLMError as e:
                agent_log.append({"agent": "llm", "error": str(e)[:500], "fallback": "heuristic"})
                llm_fallback = True
        doc.meta.customer_id = r.customer_id
        doc.meta.target_schema = schema.ref
        doc.meta.version = next_version
    doc.meta.fingerprints = {
        s.id: (file_fps.get(_bound_file_id(s, r.files)) or {}).get("strict", "") for s in doc.sources
    }

    # 4) validate + simulate
    rep = validate_mapping(doc, schema)
    sim, problems = (
        (None, [f"{i.code}: {i.message}" for i in rep.issues if i.level == "error"])
        if not rep.ok
        else simulate(ctx, doc, schema, r.files)
    )
    findings = (
        [f.to_dict() for f in sim.findings]
        if sim
        else [{"kind": "binding", "severity": "error", "message": p} for p in problems]
    )
    if sim:  # values that pass the contract and still cannot be right (ADR-058)
        findings.extend(
            {"kind": "plausibility", "severity": "warning", "message": p}
            for p in plausibility.check(sim.records, schema)
        )
    rows_out = sim.stats["rows_out"] if sim else 0

    # 5) policy (row-count sanity vs the previous approved round, when both simulations were complete)
    row_change_ratio = None
    prev_sim = (previous.policy or {}).get("simulation") if previous else None
    if (
        sim
        and prev_sim
        and prev_sim.get("rows_out")
        and sim.stats["rows_out"] < 3000
        and prev_sim["rows_out"] < 3000
    ):
        row_change_ratio = abs(sim.stats["rows_out"] - prev_sim["rows_out"]) / prev_sim["rows_out"]
    mv_id = new_id("map")
    # 6) sandbox: a new mapping (not a verbatim reuse) runs next to the approved one on these files
    sandbox_report = (
        _sandbox(ctx, schema, doc, previous, r.files, mv_id, active_overrides(session, r.customer_id))
        if rep.ok and source_kind != "memory"
        else None
    )
    # 7) self-healing review: what changed per field vs the approved mapping, checked on the data. A reused
    #    mapping is checked too (ADR-056): the layout did not move, but the values may have.
    review = (
        _field_review(
            ctx,
            session,
            schema,
            previous,
            doc,
            sim.records if sim else None,
            r.customer_id,
            r.id,
            check_unchanged=source_kind == "memory",
        )
        if previous is not None and rep.ok
        else None
    )
    if review is not None and previous is not None and source_kind != "memory":
        _inherit_confidence(doc, review, previous)  # a person approved these exact field mappings before
    decision = ctx.policy.for_customer(r.customer.policy_overrides if r.customer else None).evaluate(
        doc,
        schema,
        findings=findings,
        rows_out=rows_out,
        drift=drift if previous else None,
        other_customer=(r.summary or {}).get("matches_other_customer"),
        has_previous_approval=previous is not None,
        reused_approved=(source_kind == "memory"),
        row_change_ratio=row_change_ratio,
        llm_fallback=llm_fallback,
        sandbox=sandbox_report,
        field_checks=review["checks"] if review else None,
        open_questions=open_questions,
    )
    _verdict(ctx, sandbox_report)
    mv = MappingVersion(
        id=mv_id,
        customer_id=r.customer_id,
        round_id=r.id,
        version=doc.meta.version,
        status="approved" if decision.decision == "auto_approve" else "proposed",
        target_schema=schema.ref,
        document=doc.model_dump(mode="json", exclude_none=True),
        confidence=doc.meta.confidence,
        created_by=doc.meta.created_by,
        model=doc.meta.model,
        fingerprint_strict=_round_fingerprint(r.files, "strict"),
        fingerprint_loose=_round_fingerprint(r.files, "loose"),
        policy={
            **decision.to_dict(),
            "source": source_kind,
            "target_schema_hash": stable_hash(schema.document),
            "reused_from": previous.id if source_kind == "memory" and previous else None,
            "file_fingerprints": file_fps,
            "simulation": sim.summary() if sim else None,
            "sample": (sim.records[:5] if sim else []),
            "sandbox": sandbox_report,
            "field_review": review,
            "learning": learning_note,
            "self_check": next(
                (
                    e["data"]["self_check"]
                    for e in agent_log
                    if isinstance(e.get("data"), dict) and e["data"].get("self_check")
                ),
                None,
            ),
        },
        agent_log=agent_log,
        open_questions=open_questions,
        validation=rep.to_dict(),
        parent_id=previous.id if previous else None,
        approved_by="policy" if decision.decision == "auto_approve" else None,
        approved_at=dt.datetime.now(dt.UTC) if decision.decision == "auto_approve" else None,
    )
    session.add(mv)
    session.flush()
    r.summary = {
        **(r.summary or {}),
        "drift": drift,
        "has_previous_approval": previous is not None,
        "format_memory": {
            "status": "reused" if source_kind == "memory" else "changed" if previous else "new",
            "mapping_id": previous.id if previous else None,
            "mapping_version": previous.version if previous else None,
            "target_schema": schema.ref,
        },
    }
    task: ReviewTask | None = None
    if decision.decision == "auto_approve":
        _supersede_others(session, r.customer_id, mv.id)
        r.status = "auto_approved"
    else:
        kind = (
            "blocked"
            if decision.decision == "block"
            else "drift"
            if (drift and drift != "identical")
            else "mapping"
        )
        task = ReviewTask(
            round_id=r.id,
            customer_id=r.customer_id,
            mapping_version_id=mv.id,
            kind=kind,
            priority=decision.priority,
            reasons=decision.reasons,
            details={
                "decision": decision.decision,
                **_risk(decision),
                "open_questions": open_questions,
                "findings": findings[:50],
            },
        )
        session.add(task)
        r.status = "blocked" if decision.decision == "block" else "review"
    r.summary = {
        **(r.summary or {}),
        "mapping_version_id": mv.id,
        "decision": decision.decision,
        "simulation": sim.summary() if sim else None,
    }
    from forma.services import verdict_flow

    verdict_flow.attach_to_mapping(ctx, session, mv, r, task)  # the conclusion (ADR-036)
    audit(
        session,
        "mapping.propose",
        "mapping",
        mv.id,
        actor=actor,
        round_id=r.id,
        decision=decision.decision,
        source=source_kind,
    )
    return mv


def _without_settled(questions: list[str], doc: MappingDocument, schema: TargetSchema) -> list[str]:
    """The heuristic's "assumed VAT-inclusive — confirm" question is moot once the AI set the flag from evidence
    (ADR-058); a model that kept the guess keeps the question."""
    vat = schema.semantic_pointers("price.vat_included")
    fields = [f for f in [*doc.fields, *(f for c in doc.collections for f in c.fields)] if f.target in vat]
    settled = bool(fields) and all(
        f.expr.op == "const" and (f.confidence or 0) >= 0.9 and f.evidence for f in fields
    )
    if not settled:
        return questions
    return [q for q in questions if not VAT_ASSUMED_QUESTION_RE.fullmatch(q)]


def _inherit_confidence(doc: MappingDocument, review: dict[str, Any], previous: MappingVersion) -> None:
    """Field memory (ADR-006, ADR-037): a field mapped exactly as in the approved mapping — same expression,
    same table — carries the confidence a person already gave it, so it is not asked about again."""
    unchanged = {f["pointer"] for f in review.get("fields") or [] if f.get("status") == "unchanged"}
    if not unchanged:
        return
    for fm in [*doc.fields, *(x for c in doc.collections for x in c.fields)]:
        if fm.target in unchanged and (fm.confidence is None or fm.confidence < 1.0):
            fm.confidence = 1.0
            fm.evidence = [*fm.evidence, f"same as the approved mapping v{previous.version} ({previous.id})"]


def _learned_pointers(doc: MappingDocument) -> set[str]:
    """Targets whose field mapping carries learned evidence (ADR-038)."""
    return {
        f.target
        for f in [*doc.fields, *(x for c in doc.collections for x in c.fields)]
        if any("learned:" in e for e in f.evidence)
    }


def _bound_file_id(s: SourceSpec, files: list[SourceFile]) -> str:
    f = _match_file(s, [x for x in files if x.status == "profiled"])
    return f.id if f else ""


def _round_fingerprint(files: list[SourceFile], kind: str) -> str | None:
    fps = sorted((f.fingerprint or {}).get(kind, "") for f in files if f.fingerprint)
    if not fps:
        return None
    from forma.core.hashing import stable_hash

    return fps[0] if len(fps) == 1 else stable_hash(fps)


def _supersede_others(session: Session, customer_id: str, keep_id: str) -> None:
    for m in session.scalars(
        select(MappingVersion).where(
            MappingVersion.customer_id == customer_id,
            MappingVersion.status == "approved",
            MappingVersion.id != keep_id,
        )
    ):
        m.status = "superseded"


def _examples(session: Session, customer_id: str, profile: WorkbookProfile) -> list[dict[str, Any]]:
    """Few-shot: approved mappings from *other* customers whose headers overlap (loose fingerprint / header jaccard)."""
    from forma.fingerprint.fingerprint import norm_header

    mine = {norm_header(c.name) for t in profile.tables for c in t.columns}
    out: list[tuple[float, dict[str, Any]]] = []
    for m in session.scalars(
        select(MappingVersion)
        .where(MappingVersion.status == "approved", MappingVersion.customer_id != customer_id)
        .limit(200)
    ):
        fps = (m.policy or {}).get("file_fingerprints", {})
        theirs = {h for fp in fps.values() for t in fp.get("tables", []) for h in t.get("headers", [])}
        if not theirs:
            continue
        j = len(mine & theirs) / len(mine | theirs)
        if j >= 0.3:
            out.append(
                (
                    j,
                    {
                        "similarity": round(j, 2),
                        "mapping": {
                            k: v for k, v in m.document.items() if k in ("fields", "collections", "joins")
                        },
                    },
                )
            )
    out.sort(key=lambda x: -x[0])
    return [d for _, d in out[:3]]


# --------------------------------------------------------------------------------------------
# Human decisions & edits
# --------------------------------------------------------------------------------------------
def get_mapping(session: Session, mapping_id: str) -> MappingVersion:
    m = session.get(MappingVersion, mapping_id)
    if m is None:
        raise NotFound(f"mapping {mapping_id} not found")
    return m


def approve_mapping(
    session: Session,
    mapping_id: str,
    actor: str,
    comment: str | None = None,
    *,
    override_block: bool = False,
) -> MappingVersion:
    m = get_mapping(session, mapping_id)
    if m.status not in ("proposed", "approved"):
        raise Conflict(f"mapping is {m.status}; cannot approve")
    # a policy block (wrong file, zero rows) is not an ordinary review: approving it is an explicit,
    # justified override that the audit log records
    blocked = m.status == "proposed" and (m.policy or {}).get("decision") == "block"
    if blocked and not override_block:
        raise PolicyBlocked(
            "mapping is blocked by policy; approving it needs an explicit override with a reason",
            details={"reasons": (m.policy or {}).get("reasons", [])},
        )
    if blocked and not (comment or "").strip():
        raise ValidationFailed("overriding a policy block needs a comment explaining why")
    m.status = "approved"
    m.approved_by = actor
    m.approved_at = dt.datetime.now(dt.UTC)
    m.comment = comment
    _supersede_others(session, m.customer_id, m.id)
    for t in session.scalars(
        select(ReviewTask).where(
            ReviewTask.mapping_version_id == m.id, ReviewTask.status.in_(REVIEW_UNRESOLVED)
        )
    ):
        t.status = "approved"
        t.resolver = actor
        t.comment = comment
        t.resolved_at = dt.datetime.now(dt.UTC)
    if m.round_id:
        r = session.get(ImportRound, m.round_id)
        if r:
            r.status = "approved"
    from forma.services import learning

    taught = learning.record_from_approval(session, m, actor)  # what this approval teaches FORMA (ADR-038)
    audit(
        session,
        "mapping.approve",
        "mapping",
        m.id,
        actor=actor,
        comment=comment,
        override_block=blocked,
        learned={
            "corrections": sum(1 for t in taught if t.kind != "accepted"),
            "accepted": any(t.kind == "accepted" for t in taught),
        },
    )
    return m


def reject_mapping(
    session: Session, mapping_id: str, actor: str, comment: str | None = None
) -> MappingVersion:
    m = get_mapping(session, mapping_id)
    m.status = "rejected"
    m.comment = comment
    for t in session.scalars(
        select(ReviewTask).where(
            ReviewTask.mapping_version_id == m.id, ReviewTask.status.in_(REVIEW_UNRESOLVED)
        )
    ):
        t.status = "rejected"
        t.resolver = actor
        t.comment = comment
        t.resolved_at = dt.datetime.now(dt.UTC)
    if m.round_id:
        r = session.get(ImportRound, m.round_id)
        if r:
            r.status = "rejected"
    audit(session, "mapping.reject", "mapping", m.id, actor=actor, comment=comment)
    return m


def save_mapping_edit(
    ctx: ServiceContext,
    session: Session,
    mapping_id: str,
    document: dict[str, Any],
    actor: str,
    *,
    simulate_rows: int | None = 3000,
    remaining_questions: list[Any] | None = None,
) -> MappingVersion:
    """A human edited the DSL in the Mapping Studio → new proposed version (never mutate in place)."""
    parent = get_mapping(session, mapping_id)
    schema = ctx.registry.get_ref(parent.target_schema)
    try:
        doc = MappingDocument.model_validate(document)
    except Exception as e:
        raise ValidationFailed(f"invalid mapping: {str(e)[:800]}") from e
    rep = validate_mapping(doc, schema)
    if not rep.ok:
        raise ValidationFailed("mapping failed validation", details=rep.to_dict())
    doc.meta.created_by = "human"
    doc.meta.version = (
        int(
            session.scalar(
                select(func.max(MappingVersion.version)).where(
                    MappingVersion.customer_id == parent.customer_id
                )
            )
            or 0
        )
        + 1
    )
    doc.meta.target_schema = schema.ref
    doc.meta.customer_id = parent.customer_id
    r = session.get(ImportRound, parent.round_id) if parent.round_id else None
    sim, problems = simulate(ctx, doc, schema, r.files if r else [], limit=simulate_rows) if r else (None, [])
    findings = (
        [f.to_dict() for f in sim.findings]
        if sim
        else [{"kind": "binding", "severity": "error", "message": p} for p in problems]
    )
    mv_id = new_id("map")
    approved = format_memory.select_baseline(
        session, parent.customer_id, schema, format_memory.files_fingerprint(r.files) if r else None
    )
    sandbox_report = (
        _sandbox(ctx, schema, doc, approved, r.files, mv_id, active_overrides(session, parent.customer_id))
        if r is not None
        else None
    )
    engine = ctx.policy.for_customer(r.customer.policy_overrides if r is not None and r.customer else None)
    decision = engine.evaluate(
        doc,
        schema,
        findings=findings,
        rows_out=sim.stats["rows_out"] if sim else 0,
        # drift belongs to the round's files, not to the mapping: an edit cannot clear a wrong-file block
        drift=((parent.policy or {}).get("checks") or {}).get("drift"),
        has_previous_approval=True,
        sandbox=sandbox_report,
        open_questions=remaining_questions,
    )
    _verdict(ctx, sandbox_report)
    mv = MappingVersion(
        id=mv_id,
        customer_id=parent.customer_id,
        round_id=parent.round_id,
        version=doc.meta.version,
        status="proposed",
        target_schema=schema.ref,
        document=doc.model_dump(mode="json", exclude_none=True),
        confidence=doc.meta.confidence,
        created_by="human",
        fingerprint_strict=parent.fingerprint_strict,
        fingerprint_loose=parent.fingerprint_loose,
        policy={
            **decision.to_dict(),
            "source": "human_edit",
            "target_schema_hash": stable_hash(schema.document),
            "file_fingerprints": (parent.policy or {}).get("file_fingerprints", {}),
            "simulation": sim.summary() if sim else None,
            "sample": sim.records[:5] if sim else [],
            "sandbox": sandbox_report,
        },
        agent_log=[{"agent": "human", "actor": actor, "parent": parent.id}],
        open_questions=remaining_questions or [],
        validation=rep.to_dict(),
        parent_id=parent.id,
    )
    if parent.status == "proposed":
        parent.status = "superseded"
        for t in session.scalars(
            select(ReviewTask).where(
                ReviewTask.mapping_version_id == parent.id, ReviewTask.status.in_(REVIEW_UNRESOLVED)
            )
        ):
            t.mapping_version_id = None  # will be re-created below
            t.status = "dismissed"
    session.add(mv)
    session.flush()
    task = ReviewTask(
        round_id=parent.round_id or "",
        customer_id=parent.customer_id,
        mapping_version_id=mv.id,
        kind="mapping",
        priority=decision.priority,
        reasons=["edited by " + actor, *decision.reasons],
        details={
            "decision": decision.decision,
            **_risk(decision),
            "findings": findings[:50],
            "open_questions": remaining_questions or [],
        },
    )
    session.add(task)
    if r is not None:
        from forma.services import verdict_flow

        r.summary = {
            **(r.summary or {}),
            "mapping_version_id": mv.id,
            "decision": decision.decision,
            "format_memory": {
                "status": "edited",
                "mapping_id": approved.id if approved else None,
                "mapping_version": approved.version if approved else None,
                "target_schema": schema.ref,
            },
        }
        verdict_flow.attach_to_mapping(ctx, session, mv, r, task)  # the conclusion (ADR-036)
    audit(session, "mapping.edit", "mapping", mv.id, actor=actor, parent=parent.id)
    return mv


SANDBOX_LISTS = ("price_changes", "changed", "lost", "gained")


def sandbox_compare(
    ctx: ServiceContext,
    session: Session,
    mapping_id: str,
    document: dict[str, Any] | None = None,
    *,
    limit: int | None = 5000,
) -> dict[str, Any]:
    """On demand (the Studio's Compare tab): this mapping — or unsaved edits of it — against the approved
    one, on the round's files. Nothing is stored; the policy's verdict is included."""
    m = get_mapping(session, mapping_id)
    r = session.get(ImportRound, m.round_id) if m.round_id else None
    if r is None:
        raise Conflict("this mapping has no import round to compare on")
    schema = ctx.registry.get_ref(m.target_schema)
    try:
        doc = MappingDocument.model_validate(document if document is not None else m.document)
    except Exception as e:
        raise ValidationFailed(f"invalid mapping: {str(e)[:800]}") from e
    rep = validate_mapping(doc, schema)
    if not rep.ok:
        raise ValidationFailed("mapping failed validation", details=rep.to_dict())
    baseline = format_memory.select_baseline(
        session, m.customer_id, schema, format_memory.files_fingerprint(r.files)
    )
    if baseline is not None and baseline.id == m.id and document is None:
        baseline = session.get(MappingVersion, m.parent_id) if m.parent_id else None
    if baseline is None:
        return {"baseline_id": None, "message": "no approved mapping to compare with yet", "delta": None}
    report, details = sandbox.run(
        ctx,
        schema,
        doc,
        MappingDocument.model_validate(baseline.document),
        r.files,
        limit=limit,
        overrides=active_overrides(session, m.customer_id),
    )
    report.update(baseline_id=baseline.id, baseline_version=baseline.version)
    _verdict(ctx, report)
    return {**report, "details": {k: v[:200] for k, v in details.items()}}


def sandbox_details(
    ctx: ServiceContext, session: Session, mapping_id: str, kind: str, offset: int, limit: int
) -> dict[str, Any]:
    """Drill into the comparison stored with a mapping version (which prices changed, which records went)."""
    if kind not in SANDBOX_LISTS:
        raise ValidationFailed(f"list must be one of {SANDBOX_LISTS}")
    report = (get_mapping(session, mapping_id).policy or {}).get("sandbox")
    if not report:
        return {"report": None, "list": kind, "total": 0, "items": []}
    key = report.get("details_key")
    items = sandbox.load_details(ctx, key).get(kind, []) if key and ctx.storage.exists(key) else []
    return {"report": report, "list": kind, "total": len(items), "items": items[offset : offset + limit]}


def preview_mapping(
    ctx: ServiceContext, session: Session, round_id: str, document: dict[str, Any], *, limit: int = 200
) -> dict[str, Any]:
    """Dry-run an (unsaved) mapping against the round's files — powers the live preview in the Studio."""
    r = get_round(session, round_id)
    schema = ctx.registry.get_ref(r.target_schema)
    try:
        doc = MappingDocument.model_validate(document)
    except Exception as e:
        return {"ok": False, "errors": [str(e)[:1500]], "records": [], "findings": []}
    rep = validate_mapping(doc, schema)
    if not rep.ok:
        return {"ok": False, "validation": rep.to_dict(), "records": [], "findings": []}
    sim, problems = simulate(ctx, doc, schema, r.files, limit=limit)
    if sim is None:
        return {"ok": False, "validation": rep.to_dict(), "errors": problems, "records": [], "findings": []}
    return {
        "ok": True,
        "validation": rep.to_dict(),
        "records": sim.records[:limit],
        "findings": [f.to_dict() for f in sim.findings],
        "stats": sim.stats,
    }


def source_preview(
    ctx: ServiceContext, session: Session, round_id: str, source: dict[str, Any], *, limit: int = 100
) -> dict[str, Any]:
    """Show what a SourceSpec extracts (header + rows) — side-by-side Excel view in the Studio."""
    from forma.engine.transform import load_source_frame

    r = get_round(session, round_id)
    spec = SourceSpec.model_validate(source)
    f = _match_file(spec, [x for x in r.files if x.status == "profiled"])
    if f is None:
        raise ValidationFailed("no file in this round matches the source")
    frame = load_source_frame(
        spec, BoundSource(ctx.storage.local_path(f.storage_key), None, f.id, f.original_name), limit=limit
    )
    return {
        "file": f.original_name,
        "columns": frame.columns,
        "rows": [list(map(_json_safe, row)) for row in frame.iter_rows()],
        "n_rows_preview": frame.height,
    }


def _json_safe(v: Any) -> Any:
    if isinstance(v, dt.date | dt.datetime):
        return v.isoformat()
    return v
