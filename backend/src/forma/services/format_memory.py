"""Approved format library, scoped by customer and immutable target contract.

Superseding a mapping changes the current version, not its approval history. A customer's
older ERP layouts remain usable; unapproved drafts and rejected mappings never do.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from forma.core.hashing import stable_hash
from forma.db.models import MappingVersion, SourceFile
from forma.fingerprint.fingerprint import Fingerprint, compare, merge_fingerprints
from forma.schema_registry import TargetSchema


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
