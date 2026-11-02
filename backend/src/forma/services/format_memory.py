"""Approved format library, scoped by customer and immutable target contract.

Superseding a mapping changes the current version, not its approval history. A customer's
older ERP layouts remain usable; unapproved drafts and rejected mappings never do.
"""

from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from forma.core.hashing import stable_hash
from forma.db.models import MappingVersion, SourceFile
from forma.fingerprint.fingerprint import Fingerprint, compare, merge_fingerprints
from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import TargetSchema


def rebind_approved(
    mapping: MappingVersion, files: list[SourceFile], *, for_comparison: bool = False
) -> MappingDocument:
    """Rebind a proven format to renamed files by its stored structural fingerprint.

    Only an already approved version may use this path. Fresh AI proposals must satisfy
    their explicit selectors; neither similar sheet names nor a one-file round is proof.
    Shadow comparisons may bind a unique close structural match, so column drift is
    measured against the old reading. This exception never enables production reuse.
    """
    from forma.services.mapping import _match_file

    doc = MappingDocument.model_validate(mapping.document)
    if mapping.status != "approved" and mapping.approved_at is None:
        return doc
    prior = (mapping.policy or {}).get("file_fingerprints", {})
    for source in doc.sources:
        if _match_file(source, files) is not None:
            continue
        digest = doc.meta.fingerprints.get(source.id)
        old = next((v for v in prior.values() if v.get("strict") == digest), None)
        if old is None and len(prior) == 1:
            old = next(iter(prior.values()))
        if old is None:
            continue
        candidates = []
        for f in files:
            if f.status != "profiled" or not f.fingerprint:
                continue
            drift = compare(Fingerprint.from_dict(old), Fingerprint.from_dict(f.fingerprint))
            if drift.classification in ("identical", "header_moved") or (
                for_comparison
                and drift.similarity >= 0.8
                and drift.classification
                in ("columns_renamed", "columns_added", "columns_removed", "types_changed")
            ):
                candidates.append(f)
        selector = source.model_copy(deep=True)
        selector.file.name_pattern = None
        selector.file.fingerprint = None
        hit = _match_file(selector, candidates)
        if hit is not None:
            source.file.name_pattern = "^" + re.escape(hit.original_name) + "$"
            source.file.fingerprint = None
    return doc


def approved_history(
    session: Session, customer_id: str | None = None, target_schema: str | None = None
) -> list[MappingVersion]:
    query = select(MappingVersion).where(
        or_(
            MappingVersion.status == "approved",
            (MappingVersion.status == "superseded") & MappingVersion.approved_at.is_not(None),
        )
    )
    if customer_id is not None:
        query = query.where(MappingVersion.customer_id == customer_id)
    if target_schema is not None:
        query = query.where(MappingVersion.target_schema == target_schema)
    return list(
        session.scalars(
            query.order_by(
                MappingVersion.approved_at.desc(), MappingVersion.created_at.desc(), MappingVersion.id.desc()
            )
        )
    )


def mapping_fingerprint(mapping: MappingVersion) -> Fingerprint | None:
    values = (mapping.policy or {}).get("file_fingerprints", {}).values()
    fps = [Fingerprint.from_dict(value) for value in values if value]
    return merge_fingerprints(fps) if fps else None


def files_fingerprint(files: list[SourceFile]) -> Fingerprint | None:
    fps = [Fingerprint.from_dict(f.fingerprint) for f in files if f.fingerprint and f.status == "profiled"]
    return merge_fingerprints(fps) if fps else None


def compatible(mapping: MappingVersion, schema: TargetSchema) -> bool:
    # Legacy mappings already store a pinned id@version. Keep them usable; new mappings
    # also guard against an out-of-band edit of a file-backed schema at the same version.
    digest = (mapping.policy or {}).get("target_schema_hash")
    return mapping.target_schema == schema.ref and (digest is None or digest == stable_hash(schema.document))


def select_baseline(
    session: Session, customer_id: str, schema: TargetSchema, current: Fingerprint | None
) -> MappingVersion | None:
    """Newest exact approved layout, otherwise closest compatible baseline for adaptation."""
    candidates = [m for m in approved_history(session, customer_id, schema.ref) if compatible(m, schema)]
    closest: MappingVersion | None = None
    similarity = -1.0
    if current is not None:
        for mapping in candidates:
            fp = mapping_fingerprint(mapping)
            if fp is not None:
                report = compare(fp, current)
                if report.classification in ("identical", "header_moved"):
                    return mapping
                if report.similarity > similarity:
                    closest, similarity = mapping, report.similarity
    return closest or (candidates[0] if candidates else None)


def catalog(session: Session) -> list[tuple[MappingVersion, Fingerprint]]:
    """Newest approved rule per customer / target / layout, for customer identification only."""
    seen: set[tuple[str, str, str]] = set()
    result: list[tuple[MappingVersion, Fingerprint]] = []
    for mapping in approved_history(session):
        fp = mapping_fingerprint(mapping)
        if fp is None:
            continue
        key = (mapping.customer_id, mapping.target_schema, fp.strict)
        if key not in seen:
            seen.add(key)
            result.append((mapping, fp))
    return result
