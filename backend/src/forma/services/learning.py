"""Learning from human decisions (ADR-038): every approval teaches FORMA something.

When a person approves a mapping, the approved document is compared with the version FORMA proposed
(the nearest ancestor created by ``system`` or ``ai``). Every field whose source column changed becomes a
*column alias* for that customer; every constant a person confirmed becomes a *convention* for that
customer; an approval without changes is recorded as *accepted as proposed*. The next proposal for the
customer reads these first (``Learned``), with evidence that says so; an alias that several customers
taught independently is suggested as a synonym for everyone, and an admin promotes it.

Learned knowledge is a runtime layer keyed by target pointer (rule 1): the schema's synonyms stay in the
schema file, FORMA never hard-codes a field name here.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forma.core.errors import NotFound, ValidationFailed
from forma.db.models import Correction, MappingVersion
from forma.fingerprint.fingerprint import norm_header
from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import TargetSchema
from forma.services.healing import field_changes
from forma.services.rounds import audit

PROPOSED_BY = ("system", "ai")
MAX_CHAIN = 50


@dataclass
class Learned:
    """What the mapper may use for a customer: column aliases and confirmed constants, with their why."""

    aliases: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # pointer → {normalized header: evidence}
    constants: dict[str, tuple[Any, str]] = field(default_factory=dict)  # pointer → (value, evidence)
    ids: dict[str, list[str]] = field(default_factory=dict)  # pointer → correction ids (to count uses)
    anchors: list[dict[str, Any]] = field(default_factory=list)  # other customers' pairs below the share bar
    anchored: int = 0  # pairs that became aliases because enough customers approved them (ADR-059)

    @property
    def empty(self) -> bool:
        return not self.aliases and not self.constants and not self.anchors

    def alias_for(self, pointer: str, header: str) -> str | None:
        return self.aliases.get(pointer, {}).get(norm_header(header))

    def summary(self) -> dict[str, Any]:
        return {
            "aliases": sum(len(v) for v in self.aliases.values()),
            "constants": len(self.constants),
            "anchored": self.anchored,
            "anchors": len(self.anchors),
        }

    def for_prompt(self) -> dict[str, Any]:
        """Bounded, model-facing view: what the operator taught FORMA before (data, not instructions)."""
        return {
            "column_aliases": [
                {"target": p, "header": h, "why": why}
                for p, hs in sorted(self.aliases.items())
                for h, why in sorted(hs.items())
            ][:40],
            "confirmed_constants": [
                {"target": p, "value": v, "why": why} for p, (v, why) in sorted(self.constants.items())
            ][:20],
            "anchors_from_other_customers": self.anchors[:40],
        }

    def absorb(self, anchors: Anchors, headers: set[str], share_after: int) -> None:
        """Other customers' approved header → field pairs for the headers of this file (ADR-059). A pair that
        ``share_after`` customers approved independently is an alias for this customer too (it wins in the
        matcher, with evidence that says why); a pair fewer approved is shown to the model as an anchor."""
        for (header, target), customers in sorted(anchors.pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if header not in headers:
                continue
            n = len(customers)
            if n >= share_after:
                hs = self.aliases.setdefault(target, {})
                if header not in hs:
                    hs[header] = f"learned: {n} other customers map this header here (anchor, ADR-059)"
                    self.anchored += 1
            else:
                self.anchors.append({"header": header, "target": target, "customers": n})


@dataclass
class Anchors:
    """(normalized header, target pointer) → the other customers whose approved mappings read it so."""

    pairs: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def add(self, header: str, target: str, customer_id: str) -> None:
        self.pairs.setdefault((header, target), set()).add(customer_id)


def _single_column(expr: Any) -> str | None:
    """The one column a stored expression reads, or None when it reads none or several."""
    leaves: list[str] = []

    def walk(e: Any) -> None:
        if not isinstance(e, dict):
            return
        if e.get("op") == "col" and e.get("name"):
            leaves.append(str(e["name"]))
        for a in e.get("args") or []:
            walk(a)

    walk(expr)
    return leaves[0] if len(leaves) == 1 else None


def anchors_for(
    session: Session, customer_id: str | None, schema: TargetSchema, *, limit: int = 300
) -> Anchors:
    """What other customers' approved mappings (and their operators' corrections) say a header means, for
    the same target contract. Each pair counts a customer once; the newest ``limit`` approved versions are
    read. Deterministic and cheap enough to run on every proposal."""
    from forma.services.format_memory import approved_history

    out = Anchors()
    known = {f.pointer for f in schema.fields(include_system=True)}
    seen: set[str] = set()
    for m in approved_history(session, None, schema.ref)[:limit]:
        if m.customer_id == customer_id or m.customer_id in seen:
            continue
        seen.add(m.customer_id)  # the newest approved layout speaks for a customer
        doc = m.document or {}
        fields = list(doc.get("fields") or [])
        for c in doc.get("collections") or []:
            fields.extend(c.get("fields") or [])
        for f in fields:
            target, col = str(f.get("target") or ""), _single_column(f.get("expr"))
            if target in known and col and (h := norm_header(col)):
                out.add(h, target, m.customer_id)
    for c in session.scalars(
        select(Correction).where(
            Correction.status == "confirmed",
            Correction.kind == "alias",
            Correction.header.is_not(None),
            Correction.customer_id.is_not(None),
            Correction.customer_id != customer_id,
        )
    ):
        if c.target in known and c.header and c.customer_id:
            out.add(c.header, c.target, c.customer_id)
    return out


# --------------------------------------------------------------------------------------------
# record: what an approval taught
# --------------------------------------------------------------------------------------------
def _proposed_ancestor(session: Session, m: MappingVersion) -> MappingVersion | None:
    """The version FORMA proposed that this approved version descends from (None when ``m`` is it)."""
    cur: MappingVersion | None = m
    for _ in range(MAX_CHAIN):
        if cur is None:
            return None
        if cur.created_by in PROPOSED_BY:
            return cur if cur.id != m.id else None
        cur = session.get(MappingVersion, cur.parent_id) if cur.parent_id else None
    return None


def _columns(reads: list[str]) -> list[str]:
    """``source.column`` → column names (the sheet prefix is the source's, not the header's)."""
    return [r.split(".", 1)[1] if "." in r else r for r in reads]


def record_from_approval(session: Session, m: MappingVersion, actor: str) -> list[Correction]:
    """Called when a mapping is approved: diff it against FORMA's own proposal and keep what changed."""
    if m.created_by in PROPOSED_BY:
        row = Correction(
            customer_id=m.customer_id,
            kind="accepted",
            target=None,
            header=None,
            before=None,
            after=None,
            actor=actor,
            mapping_version_id=m.id,
            round_id=m.round_id,
        )
        session.add(row)
        return [row]
    base = _proposed_ancestor(session, m)
    if base is None:
        return []
    old = MappingDocument.model_validate(base.document)
    new = MappingDocument.model_validate(m.document)
    out: list[Correction] = []
    for ch in field_changes(old, new):
        if ch["status"] not in ("changed", "added"):
            continue
        after = ch["after"] or {}
        before = ch["before"] or {}
        expr = after.get("expr") or {}
        if expr.get("op") == "const":
            if (before.get("expr") or {}) == expr:
                continue
            out.append(
                Correction(
                    customer_id=m.customer_id,
                    kind="constant",
                    target=ch["pointer"],
                    header=None,
                    before=before.get("expr"),
                    after=expr.get("value"),
                    actor=actor,
                    mapping_version_id=m.id,
                    round_id=m.round_id,
                )
            )
            continue
        new_cols = _columns(after.get("reads") or [])
        old_cols = set(_columns(before.get("reads") or []))
        for col_name in new_cols:
            if col_name in old_cols:
                continue
            out.append(
                Correction(
                    customer_id=m.customer_id,
                    kind="alias",
                    target=ch["pointer"],
                    header=norm_header(col_name),
                    before={"reads": before.get("reads") or []},
                    after={"column": col_name, "reads": after.get("reads") or []},
                    actor=actor,
                    mapping_version_id=m.id,
                    round_id=m.round_id,
                )
            )
    if not out:
        # answered or confirmed without changing a single source column or constant: accepted as proposed
        out.append(
            Correction(
                customer_id=m.customer_id,
                kind="accepted",
                target=None,
                header=None,
                before={"proposal": base.id},
                after=None,
                actor=actor,
                mapping_version_id=m.id,
                round_id=m.round_id,
            )
        )
    for row in out:
        session.add(row)
    return out


# --------------------------------------------------------------------------------------------
# use: what the mapper reads before it guesses
# --------------------------------------------------------------------------------------------
def learned_for(session: Session, customer_id: str | None, schema: TargetSchema) -> Learned:
    """This customer's confirmed aliases and constants plus the promoted (global) aliases."""
    out = Learned()
    known = {f.pointer for f in schema.fields(include_system=True)}
    rows = session.scalars(
        select(Correction)
        .where(
            Correction.status == "confirmed",
            Correction.kind.in_(("alias", "constant", "promoted")),
            (Correction.customer_id == customer_id) | (Correction.customer_id.is_(None)),
        )
        .order_by(Correction.created_at.desc())
    ).all()
    for r in rows:
        if r.target not in known:
            continue
        when = r.created_at.date().isoformat() if r.created_at else ""
        if r.kind == "constant":
            if r.target not in out.constants:
                out.constants[r.target] = (r.after, f"learned: confirmed by {r.actor} on {when}")
                out.ids.setdefault(r.target, []).append(r.id)
        elif r.header:
            hs = out.aliases.setdefault(r.target, {})
            if r.header not in hs:
                hs[r.header] = (
                    f"learned: everyone's alias (promoted by {r.actor} on {when})"
                    if r.kind == "promoted"
                    else f"learned: you mapped '{(r.after or {}).get('column', r.header)}' here on {when}"
                )
                out.ids.setdefault(r.target, []).append(r.id)
    return out


def count_uses(session: Session, learned: Learned, used_pointers: set[str]) -> None:
    ids = [i for p in used_pointers for i in learned.ids.get(p, [])]
    if not ids:
        return
    for row in session.scalars(select(Correction).where(Correction.id.in_(ids))):
        row.uses = (row.uses or 0) + 1


# --------------------------------------------------------------------------------------------
# share: aliases several customers taught become everyone's
# --------------------------------------------------------------------------------------------
def suggestions(session: Session, schema: TargetSchema, *, min_customers: int = 3) -> list[dict[str, Any]]:
    """Headers mapped to the same field by at least ``min_customers`` customers, not yet a synonym. A customer
    counts whether its operator taught the pair or accepted it from an anchor (ADR-059): both are approvals."""
    rows = [(h, t, len(cs)) for (h, t), cs in anchors_for(session, None, schema).pairs.items()]
    promoted = {
        (r.header, r.target)
        for r in session.scalars(
            select(Correction).where(Correction.kind == "promoted", Correction.status == "confirmed")
        )
    }
    out: list[dict[str, Any]] = []
    for header, target, n in rows:
        if n < min_customers or (header, target) in promoted or header is None or target is None:
            continue
        try:
            spec = schema.field(target)
        except NotFound:
            continue
        if any(norm_header(s) == header for s in (*spec.synonyms_en, *spec.synonyms_th, spec.name)):
            continue
        out.append({"header": header, "target": target, "field": spec.to_dict(), "customers": int(n)})
    out.sort(key=lambda s: (-s["customers"], s["header"]))
    return out


def promote(session: Session, header: str, target: str, actor: str) -> Correction:
    h = norm_header(header)
    if not h:
        raise ValidationFailed("A header is required")
    existing = session.scalar(
        select(Correction).where(
            Correction.kind == "promoted",
            Correction.header == h,
            Correction.target == target,
            Correction.status == "confirmed",
        )
    )
    if existing:
        return existing
    row = Correction(
        customer_id=None, kind="promoted", target=target, header=h, after={"column": header}, actor=actor
    )
    session.add(row)
    session.flush()
    audit(session, "learning.promote", "correction", row.id, actor=actor, header=h, target=target)
    return row


def get_correction(session: Session, correction_id: str) -> Correction:
    row = session.get(Correction, correction_id)
    if row is None:
        raise NotFound(f"correction {correction_id} not found")
    return row


def forget(session: Session, correction_id: str, actor: str) -> Correction:
    row = get_correction(session, correction_id)
    row.status = "retired"
    audit(session, "learning.forget", "correction", row.id, actor=actor, kind=row.kind, target=row.target)
    return row


# --------------------------------------------------------------------------------------------
# show: is FORMA getting better?
# --------------------------------------------------------------------------------------------
def _only(query: Any, column: Any, customers: frozenset[str] | None) -> Any:
    return query if customers is None else query.where(column.in_(sorted(customers)))


def stats(session: Session, *, months: int = 6, customers: frozenset[str] | None = None) -> dict[str, Any]:
    """Per month: mappings approved, accepted as proposed, corrections made, learned fields used."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=31 * months)
    rows = session.scalars(
        _only(select(Correction).where(Correction.created_at >= since), Correction.customer_id, customers)
    ).all()
    by_month: dict[str, Counter[str]] = defaultdict(Counter)
    uses_by_month: dict[str, int] = defaultdict(int)
    for r in rows:
        key = r.created_at.strftime("%Y-%m") if r.created_at else "?"
        by_month[key][r.kind] += 1
        uses_by_month[key] += int(r.uses or 0)
    approved_at = session.scalars(
        _only(
            select(MappingVersion.approved_at).where(
                MappingVersion.approved_at.is_not(None), MappingVersion.approved_at >= since
            ),
            MappingVersion.customer_id,
            customers,
        )
    ).all()
    approved_counter: Counter[str] = Counter(a.strftime("%Y-%m") for a in approved_at if a is not None)
    approved_by_month = dict(approved_counter)
    months_seen = sorted(set(by_month) | set(approved_by_month))
    series = []
    for mth in months_seen:
        c = by_month.get(mth, Counter())
        corrections = c["alias"] + c["constant"]
        decided = c["accepted"] + corrections
        series.append(
            {
                "month": mth,
                "mappings_approved": approved_by_month.get(mth, 0),
                "accepted_as_proposed": c["accepted"],
                "corrections": corrections,
                "corrections_per_approval": round(corrections / decided, 2) if decided else None,
                "learned_fields_used": uses_by_month.get(mth, 0),
            }
        )
    active = session.scalars(
        _only(select(Correction).where(Correction.status == "confirmed"), Correction.customer_id, customers)
    ).all()
    return {
        "series": series,
        "aliases": sum(1 for r in active if r.kind == "alias"),
        "constants": sum(1 for r in active if r.kind == "constant"),
        "promoted": sum(1 for r in active if r.kind == "promoted"),
        "customers_taught": len(
            {r.customer_id for r in active if r.customer_id and r.kind in ("alias", "constant")}
        ),
        "uses": sum(int(r.uses or 0) for r in active),
    }


def list_learned(
    session: Session,
    *,
    customer_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
    customers: frozenset[str] | None = None,
) -> dict[str, Any]:
    q = select(Correction).where(
        Correction.status == "confirmed", Correction.kind.in_(("alias", "constant", "promoted"))
    )
    if customer_id:
        q = q.where(Correction.customer_id == customer_id)
    q = _only(q, Correction.customer_id, customers)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = session.scalars(q.order_by(Correction.created_at.desc()).offset(offset).limit(limit)).all()
    return {
        "total": int(total),
        "items": [
            {
                "id": r.id,
                "customer_id": r.customer_id,
                "kind": r.kind,
                "target": r.target,
                "header": r.header,
                "column": (r.after or {}).get("column") if isinstance(r.after, dict) else None,
                "value": r.after if r.kind == "constant" else None,
                "actor": r.actor,
                "round_id": r.round_id,
                "uses": int(r.uses or 0),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
