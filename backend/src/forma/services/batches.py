"""Bulk upload (ADR-027): many files at once, each matched to its customer by its format, processed into a
JSON of its own, and published without a person when the policy allows it.

A file is stored (content-addressed) the moment it arrives, before anyone knows whose it is. Identification
profiles it and compares its structure with every customer's approved formats:

- ``exact``: the same sheets and ordered headers as one customer's approved mapping;
- ``same_columns``: the same headers in another order / on other sheets;
- ``similar``: one customer's format is clearly the closest (drift such as a renamed column — the round
  then goes through the usual drift handling: self-healing review, sandbox, policy);
- ``file_name``: no approved format fits, but the file name carries one customer's code (a first file of
  that customer, or a format several customers share).

A customer code in the file name that contradicts the format sends the file to a person.

Anything else — no close format, or a format several customers share — waits for a person to choose the
customer (``needs_customer``). Once a file has a customer it is an ordinary round of one file, with
``summary.auto_publish`` so the pipeline finishes it (ADR-027).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.errors import NotFound, UnsupportedFile, ValidationFailed
from forma.db.models import Batch, BatchItem, Customer, ImportRound, Job, MappingVersion
from forma.fingerprint.fingerprint import Fingerprint, compare
from forma.ingestion.sniff import sniff_kind
from forma.services import config as cfg
from forma.services import format_memory
from forma.services import rounds as rs
from forma.services.context import ServiceContext
from forma.services.publishing import FORMAT, latest_export
from forma.services.round_lifecycle import CLOSED
from forma.storage import safe_name

SIMILAR_MIN = 0.75  # closest approved format must be at least this similar …
SIMILAR_MARGIN = 0.1  # … and this much closer than the next customer's, or a person decides
QUEUED_LOST_S = 120  # a job still queued after this long was never handed to a worker (queue was down)


def create_batch(
    ctx: ServiceContext,
    session: Session,
    files: list[tuple[str, bytes]],
    actor: str,
    *,
    label: str | None = None,
    auto_publish: bool = True,
    work_date: dt.date | None = None,
    expected_files: int | None = None,
) -> Batch:
    if not files:
        raise ValidationFailed("add at least one file")
    b = Batch(
        label=(label or "").strip() or f"Upload {dt.date.today().isoformat()}",
        auto_publish=auto_publish,
        created_by=actor,
        work_date=work_date,
        expected_files=expected_files,
    )
    session.add(b)
    session.flush()
    add_files(ctx, session, b, files, actor)
    rs.audit(session, "batch.create", "batch", b.id, actor=actor, files=len(files), auto_publish=auto_publish)
    return b


def add_files(
    ctx: ServiceContext, session: Session, b: Batch, files: list[tuple[str, bytes]], actor: str
) -> list[str]:
    """Append immutable files; same filename/content retries within a round are idempotent.

    The caller locks an existing batch before appending to serialize concurrent requests.
    Different filenames may represent different customers even with identical contents.
    """
    if not files:
        raise ValidationFailed("add at least one file")
    existing = {(it.file_name, it.sha256) for it in b.items}
    added: list[str] = []
    skipped = 0
    for name, data in files:
        name = safe_name(name)
        digest = hashlib.sha256(data).hexdigest()
        if (name, digest) in existing:
            skipped += 1
            continue
        existing.add((name, digest))
        try:
            kind = sniff_kind(data, name)  # bytes decide, not the extension (ADR-013)
        except (UnsupportedFile, ValidationFailed) as e:
            session.add(
                BatchItem(
                    batch_id=b.id,
                    file_name=name,
                    sha256=digest,
                    size=len(data),
                    kind="unknown",
                    storage_key="",
                    status="failed",
                    error=e.message,
                )
            )
            continue
        stored = ctx.files.put_bytes(data, name)
        item = BatchItem(
            batch_id=b.id,
            file_name=name,
            sha256=stored.sha256,
            size=stored.size,
            kind=kind,
            storage_key=stored.key,
        )
        session.add(item)
        session.flush()
        added.append(item.id)
    session.flush()
    session.expire(b, ["items"])
    rs.audit(session, "batch.append", "batch", b.id, actor=actor, files=len(files), skipped=skipped)
    return added


def get_batch(session: Session, batch_id: str) -> Batch:
    b = session.get(Batch, batch_id)
    if b is None:
        raise NotFound(f"upload {batch_id} not found")
    return b


def get_item(session: Session, item_id: str) -> BatchItem:
    it = session.get(BatchItem, item_id)
    if it is None:
        raise NotFound(f"file {item_id} not found")
    return it


# --------------------------------------------------------------------------------------------
# identification: whose file is this?
# --------------------------------------------------------------------------------------------
def _approved_formats(session: Session) -> list[tuple[MappingVersion, Fingerprint]]:
    """Every approved layout, including older layouts; this identifies customers, not reuse eligibility."""
    return format_memory.catalog(session)


def named_customer(session: Session, file_name: str) -> Customer | None:
    """The one customer whose code appears in the file name as a word ("สินค้า_ต.ค.69_C101.xlsx" → C101)."""
    words = {w.upper() for w in re.split(r"[^0-9A-Za-z]+", Path(file_name).stem) if w}
    hits = [c for c in session.scalars(select(Customer)) if len(c.code) >= 2 and c.code.upper() in words]
    return hits[0] if len(hits) == 1 else None


def match_customer(session: Session, fp: Fingerprint, file_name: str = "") -> dict[str, Any]:
    """The format decides; a customer code in the file name confirms it, settles a format several customers
    share, or names the customer of a format nobody has yet. When name and format disagree, a person decides
    — the same ERP template exported by two customers must not be published as the wrong one."""
    by_format = _match_format(session, fp)
    named = named_customer(session, file_name) if file_name else None
    if named is None:
        return by_format
    if by_format["kind"] == "known":
        if by_format["customer_id"] == named.id:
            return {**by_format, "named": True}
        return {
            "kind": "unknown",
            "reason": f"the file name says {named.code} but the format is {by_format['customer_code']}'s",
            "candidates": [
                {"customer_id": named.id, "customer_code": named.code, "similarity": None},
                {
                    "customer_id": by_format["customer_id"],
                    "customer_code": by_format["customer_code"],
                    "similarity": by_format["similarity"],
                },
            ],
        }
    shared = [c for c in by_format.get("candidates", []) if c["customer_id"] == named.id]
    return {
        "kind": "known",
        "how": "file_name",
        "customer_id": named.id,
        "customer_code": named.code,
        "similarity": shared[0]["similarity"] if shared else None,
        "classification": None,
        "named": True,
    }


def _match_format(session: Session, fp: Fingerprint) -> dict[str, Any]:
    formats = _approved_formats(session)
    codes = {c.id: c.code for c in session.scalars(select(Customer))}

    def known(how: str, mv: MappingVersion, similarity: float, classification: str) -> dict[str, Any]:
        return {
            "kind": "known",
            "how": how,
            "customer_id": mv.customer_id,
            "customer_code": codes.get(mv.customer_id),
            "mapping_version_id": mv.id,
            "similarity": round(similarity, 3),
            "classification": classification,
        }

    for how, attr in (("exact", "strict"), ("same_columns", "loose")):
        hits = [(mv, f) for mv, f in formats if getattr(f, attr) == getattr(fp, attr)]
        customers = {mv.customer_id for mv, _ in hits}
        if len(customers) == 1:
            rep = compare(hits[0][1], fp)
            return known(how, hits[0][0], rep.similarity if how != "exact" else 1.0, rep.classification)
        if len(customers) > 1:
            return {
                "kind": "unknown",
                "reason": "the same format is approved for several customers",
                "candidates": [
                    {
                        "customer_id": mv.customer_id,
                        "customer_code": codes.get(mv.customer_id),
                        "similarity": 1.0,
                    }
                    for mv, _ in hits
                ][:5],
            }
    ranked = sorted(((compare(f, fp), mv) for mv, f in formats), key=lambda x: x[0].similarity, reverse=True)
    # Competing layouts from the same customer are not competing customer identities.
    seen_customers: set[str] = set()
    scored = []
    for report, mapping in ranked:
        if mapping.customer_id not in seen_customers:
            seen_customers.add(mapping.customer_id)
            scored.append((report, mapping))
    candidates = [
        {
            "customer_id": mv.customer_id,
            "customer_code": codes.get(mv.customer_id),
            "similarity": round(rep.similarity, 3),
        }
        for rep, mv in scored[:3]
    ]
    if scored:
        best, mv = scored[0]
        runner_up = scored[1][0].similarity if len(scored) > 1 else 0.0
        if (
            best.classification != "unrelated_file"
            and best.similarity >= SIMILAR_MIN
            and best.similarity - runner_up >= SIMILAR_MARGIN
        ):
            return known("similar", mv, best.similarity, best.classification)
    return {
        "kind": "unknown",
        "reason": "no customer has an approved format like this file"
        if not scored or scored[0][0].similarity < SIMILAR_MIN
        else "several customers' formats are about as close",
        "candidates": candidates,
    }


def identify(ctx: ServiceContext, session: Session, item: BatchItem) -> dict[str, Any]:
    """Profile the file once (kept for its round) and decide whose it is."""
    if item.fingerprint is None:
        prof, fp, _ = rs._profile_file(ctx.storage.local_path(item.storage_key))
        item.profile, item.fingerprint = prof, fp
    item.match = match_customer(session, Fingerprint.from_dict(item.fingerprint or {}), item.file_name)
    item.status = "identified" if item.match["kind"] == "known" else "needs_customer"
    return item.match


def attach(
    ctx: ServiceContext, session: Session, item: BatchItem, customer_id: str, actor: str
) -> ImportRound:
    """The file becomes a one-file round of its customer, flagged to finish (and publish) automatically."""
    # row lock: a resumed duplicate job waits here, then finds the round the first one made
    item = session.get(BatchItem, item.id, with_for_update=True, populate_existing=True) or item
    if item.round_id:
        return rs.get_round(session, item.round_id)
    customer = rs.get_customer(session, customer_id)
    b = get_batch(session, item.batch_id)
    target = cfg.resolve_target_schema(session, ctx.registry, None, customer.default_target_schema)
    r = rs.create_round(session, customer.id, label=f"{b.label} · {item.file_name}", target_schema=target)
    f = rs.add_file(ctx, session, r, item.file_name, ctx.storage.local_path(item.storage_key).read_bytes())
    if item.profile and item.fingerprint:  # profiled during identification: not again
        f.profile, f.fingerprint, f.status = item.profile, item.fingerprint, "profiled"
    r.summary = {
        **(r.summary or {}),
        "batch_id": b.id,
        "batch_item_id": item.id,
        "auto_publish": b.auto_publish,
    }
    item.customer_id, item.round_id, item.status = customer.id, r.id, "attached"
    rs.audit(
        session,
        "batch.attach",
        "batch",
        b.id,
        actor=actor,
        item=item.id,
        round_id=r.id,
        customer_id=customer.id,
        match=(item.match or {}).get("how", "chosen"),
    )
    return r


# --------------------------------------------------------------------------------------------
# where each file stands
# --------------------------------------------------------------------------------------------
def json_name(file_name: str) -> str:
    return f"{Path(file_name).stem}.json"


def item_state(item: BatchItem, r: ImportRound | None) -> dict[str, Any]:
    """step 1 identify · 2 mapping · 3 create data · 4 check data · 5 JSON; state: what is happening."""
    if item.status == "failed":
        return {"state": "failed", "step": 1, "message": item.error or "the file could not be read"}
    if item.status in ("received", "identified") and r is None:
        return {"state": "processing", "step": 1, "message": "Reading the file and matching its format"}
    if item.status == "needs_customer":
        return {
            "state": "needs_customer",
            "step": 1,
            "message": (item.match or {}).get("reason", "unknown format"),
        }
    if r is None:
        return {"state": "processing", "step": 1, "message": "Preparing the round"}
    pub = (r.summary or {}).get("publish") or {}
    s = r.status
    if s in CLOSED:
        return {"state": "closed", "step": 1, "message": r.closed_reason or s, "closed_as": s}
    if s in ("uploaded", "profiled"):
        return {"state": "processing", "step": 2, "message": "Proposing the mapping"}
    if s in ("review", "rejected"):
        return {"state": "needs_review", "step": 2, "message": "Check the mapping"}
    if s == "blocked":
        return {"state": "needs_review", "step": 2, "message": "Blocked — check the file"}
    if s == "failed":
        return {
            "state": "failed",
            "step": 2,
            "message": (r.summary or {}).get("error", "the pipeline failed"),
        }
    if s in ("auto_approved", "approved"):
        return {"state": "processing", "step": 3, "message": "Creating the data"}
    if s == "transformed":
        if pub.get("decision") == "review" or not (r.summary or {}).get("auto_publish"):
            return {
                "state": "needs_data_check",
                "step": 4,
                "message": "Check and approve the data",
                "reasons": pub.get("reasons", []),
            }
        return {"state": "processing", "step": 4, "message": "Checking the data"}
    if s == "approved_data":
        return {"state": "processing", "step": 5, "message": "Writing the JSON"}
    if s == "published":
        return {"state": "ready", "step": 5, "message": "JSON ready"}
    return {"state": "processing", "step": 1, "message": s}


def stuck_items(session: Session, b: Batch) -> list[str]:
    """Files waiting to be matched with no job queued or running for them (the queue was full or down when
    they were uploaded): ``resume`` queues them again. The job is idempotent, so a duplicate is harmless."""
    waiting = {i.id for i in b.items if i.status in ("received", "identified") and not i.round_id}
    if not waiting:
        return []
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=QUEUED_LOST_S)
    active = {
        (j.payload or {}).get("item_id")
        for j in session.scalars(
            select(Job).where(Job.kind == "process_batch_item", Job.status.in_(("queued", "running")))
        )
        if j.status == "running" or (j.created_at is not None and j.created_at > cutoff)
    }
    return sorted(waiting - active)


def _brief_verdict(v: dict[str, Any] | None) -> dict[str, Any] | None:
    """The conclusion a file row shows (ADR-036), without evidence payloads."""
    if not v:
        return None
    return {
        k: v.get(k) for k in ("conclusion", "outcome", "confidence", "word", "rules_fired", "digest", "stage")
    }


SUMMARY_KEYS = (
    "id",
    "label",
    "work_date",
    "expected_files",
    "complete",
    "auto_publish",
    "created_by",
    "created_at",
    "total",
    "counts",
    "mapped",
    "stuck",
    "done",
)


def batch_view(session: Session, b: Batch) -> dict[str, Any]:
    items = list(b.items)
    rounds = {
        r.id: r
        for r in session.scalars(
            select(ImportRound).where(ImportRound.id.in_([i.round_id for i in items if i.round_id]))
        )
    }
    codes = {
        c.id: (c.code, c.name)
        for c in session.scalars(
            select(Customer).where(Customer.id.in_([i.customer_id for i in items if i.customer_id]))
        )
    }
    out_items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    mapped = 0
    for it in items:
        r = rounds.get(it.round_id or "")
        st = item_state(it, r)
        counts[st["state"]] = counts.get(st["state"], 0) + 1
        mapped += st["step"] >= 3
        art = latest_export(session, r.id) if r is not None and r.status == "published" else None
        code, name = codes.get(it.customer_id or "", (None, None))
        out_items.append(
            {
                "id": it.id,
                "file_name": it.file_name,
                "size": it.size,
                "match": it.match,
                "customer": {"id": it.customer_id, "code": code, "name": name} if it.customer_id else None,
                "round_id": it.round_id,
                "round_status": r.status if r else None,
                "mapping_version_id": (r.summary or {}).get("mapping_version_id") if r else None,
                "format_memory": (r.summary or {}).get("format_memory") if r else None,
                "dataset_version_id": (r.summary or {}).get("dataset_version_id") if r else None,
                "publish": (r.summary or {}).get("publish") if r else None,
                "export_id": art.id if art else None,
                "json_name": json_name(it.file_name),
                "rows": (r.summary or {}).get("rows") if r else None,
                "verdict": _brief_verdict((r.summary or {}).get("verdict")) if r else None,
                **st,
            }
        )
    return {
        "id": b.id,
        "label": b.label,
        "work_date": b.work_date.isoformat() if b.work_date else None,
        "expected_files": b.expected_files,
        "complete": bool(items) and counts.get("ready", 0) == len(items),
        "auto_publish": b.auto_publish,
        "created_by": b.created_by,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "total": len(items),
        "counts": counts,
        "mapped": mapped,
        "stuck": len(stuck_items(session, b)),
        "done": all(
            i["state"] in ("ready", "failed", "needs_customer", "needs_review", "needs_data_check", "closed")
            for i in out_items
        ),
        "items": out_items,
    }


def batch_summary(session: Session, b: Batch) -> dict[str, Any]:
    """A batch without its items: light enough to poll from any page."""
    view = batch_view(session, b)
    return {k: view[k] for k in SUMMARY_KEYS}


def list_batches(
    session: Session,
    limit: int = 20,
    offset: int = 0,
    search: str = "",
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> list[dict[str, Any]]:
    out = []
    if start and end and start > end:
        raise ValidationFailed("Start date must not be after end date")
    q = select(Batch)
    if search.strip():
        q = q.where(Batch.label.icontains(search.strip(), autoescape=True))
    if start:
        q = q.where(Batch.work_date >= start)
    if end:
        q = q.where(Batch.work_date <= end)
    for b in session.scalars(
        q.order_by(Batch.created_at.desc(), Batch.id.desc()).offset(offset).limit(limit)
    ):
        out.append(batch_summary(session, b))
    return out


def zip_outputs(ctx: ServiceContext, session: Session, b: Batch) -> tuple[bytes, int]:
    """One JSON per file, named after the file, for every file whose JSON is ready."""
    buf = io.BytesIO()
    used: set[str] = set()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for it in b.items:
            if not it.round_id:
                continue
            r = session.get(ImportRound, it.round_id)
            if r is None or r.status in CLOSED:
                continue
            art = latest_export(session, it.round_id, FORMAT)
            if art is None:
                continue
            name, k = json_name(it.file_name), 2
            while name in used:
                name, k = f"{Path(it.file_name).stem} ({k}).json", k + 1
            used.add(name)
            z.writestr(name, ctx.storage.local_path(art.storage_key).read_bytes())
            n += 1
    return buf.getvalue(), n
