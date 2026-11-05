"""Reviewed, content-addressed knowledge releases. No jobs, chat history, credentials or cache transfer."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import Float, Integer, String, select, text
from sqlalchemy.orm import Session

from forma.core.errors import NotFound, ValidationFailed
from forma.core.hashing import stable_hash
from forma.core.settings import get_settings
from forma.db.models import (
    AppSetting,
    Correction,
    Customer,
    ImportRound,
    KnowledgeLabel,
    KnowledgeRelease,
    MappingVersion,
    SourceFile,
    StoredSchema,
    utcnow,
)
from forma.mapping_dsl.models import MappingDocument
from forma.mapping_dsl.validate import validate_mapping
from forma.schema_registry import TargetSchema
from forma.services import knowledge_gate as gate
from forma.services.context import ServiceContext
from forma.services.rounds import audit
from forma.storage import safe_name

MAX_BYTES = 256 * 1024 * 1024
MAX_ITEMS = 500
INSTANCE_KEY = "knowledge.instance"


def instance(session: Session, *, create: bool = False) -> str:
    row = session.get(AppSetting, INSTANCE_KEY)
    if row is None:
        if not create:
            return "uninitialized"
        row = AppSetting(key=INSTANCE_KEY, value={"id": str(uuid.uuid4())}, updated_by="system")
        session.add(row)
        session.flush()
    return str(row.value["id"])


def inventory(session: Session, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    items = []
    query = select(MappingVersion).order_by(MappingVersion.created_at.desc(), MappingVersion.id)
    for row in session.scalars(query.offset(offset).limit(limit + 1)):
        label = gate.label_for(session, gate.subject(row))
        customer = session.get(Customer, row.customer_id)
        corrections = list(session.scalars(select(Correction).where(Correction.mapping_version_id == row.id)))
        items.append(
            {
                "subject": gate.subject(row),
                "customer": customer.code if customer else row.customer_id,
                "mapping_id": row.id,
                "round_id": row.round_id,
                "version": row.version,
                "target_schema": row.target_schema,
                "mapping_status": row.status,
                "origin": label.origin if label else "unknown",
                "state": label.state if label else "pending",
                "eligible": gate.allowed(session, row, strict=True),
                "corrections": [
                    {
                        "subject": gate.subject(c),
                        "target": c.target,
                        "kind": c.kind,
                        "eligible": gate.allowed(session, c, strict=True),
                    }
                    for c in corrections
                ],
                "dependencies": sorted(gate.dependencies(row)),
            }
        )
    return {
        "items": items[:limit],
        "next_offset": offset + limit if len(items) > limit else None,
        "instance": instance(session),
        "policy": get_settings().knowledge_policy,
    }


def _fields(row: Any, names: str) -> dict[str, Any]:
    result = {}
    for name in names.split():
        value = getattr(row, name)
        result[name] = value.isoformat() if isinstance(value, dt.datetime) else value
    return result


def export_bundle(session: Session, ctx: ServiceContext, roots: list[str], actor: str) -> bytes:
    f: Any
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(74603821)"))
    if not roots or len(roots) > 100:
        raise ValidationFailed("Select 1–100 knowledge items")
    selected: dict[str, Any] = {}
    pending = list(roots)
    while pending:
        key = pending.pop()
        if key in selected:
            continue
        row = gate.lookup(session, key)
        if not gate.allowed(session, row, strict=True):
            raise ValidationFailed(
                "Knowledge is unreviewed, withdrawn, synthetic or changed", details={"subject": key}
            )
        selected[key] = row
        if len(selected) > 200:
            raise ValidationFailed("Too many dependencies; split the release")
        pending.extend(gate.dependencies(row))
    mappings = [r for r in selected.values() if isinstance(r, MappingVersion)]
    customers, rounds, files, schemas = {}, {}, {}, {}
    for m in mappings:
        c = session.get(Customer, m.customer_id)
        r = session.get(ImportRound, m.round_id) if m.round_id else None
        if not c or not r or not r.files:
            raise ValidationFailed("Source customer, round and file evidence must still exist")
        customers[c.id] = _fields(c, "id code name erp_type default_target_schema")
        rounds[r.id] = _fields(r, "id customer_id label target_schema notes")
        schema = ctx.registry.get_ref(m.target_schema)
        if m.target_schema != schema.ref or (m.policy or {}).get("target_schema_hash") != stable_hash(
            schema.document
        ):
            raise ValidationFailed("The source schema changed; review a mapping pinned to the correct schema")
        schemas[schema.ref] = schema.document
        for f in r.files:
            files[f.id] = _fields(
                f, "id round_id customer_id sha256 original_name size kind profile fingerprint status"
            )
    entries = []
    for key, row in sorted(selected.items()):
        label = gate.label_for(session, key)
        assert label is not None
        names = (
            (
                "id customer_id round_id version status target_schema document confidence created_by model "
                "fingerprint_strict fingerprint_loose policy approved_by approved_at comment"
            )
            if isinstance(row, MappingVersion)
            else ("id customer_id kind target header before after actor mapping_version_id round_id status")
        )
        entries.append(
            {
                "subject": key,
                "origin": label.origin,
                "reviewed_by": label.actor,
                "review_reason": label.reason,
                "record": _fields(row, names),
            }
        )
    manifest: dict[str, Any] = {
        "format": "forma-knowledge-v1",
        "source_instance": instance(session, create=True),
        "roots": sorted(roots),
        "entries": entries,
        "customers": list(customers.values()),
        "rounds": list(rounds.values()),
        "files": list(files.values()),
        "schemas": schemas,
    }
    manifest["digest"] = stable_hash(manifest)
    if len(files) + 1 > MAX_ITEMS:
        raise ValidationFailed("Too many evidence files; split the release")
    out = io.BytesIO()
    size = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        payload = json.dumps(manifest, ensure_ascii=False).encode()
        if len(payload) > 8 * 1024 * 1024:
            raise ValidationFailed("Manifest too large; split the release")
        z.writestr("manifest.json", payload)
        for f in files.values():
            source = session.get(SourceFile, f["id"])
            assert source
            size += f["size"]
            if size > MAX_BYTES - len(payload):
                raise ValidationFailed("Evidence exceeds 256 MiB; split the release")
            with ctx.storage.open(source.storage_key) as stream:
                data = stream.read(f["size"] + 1)
            if len(data) != f["size"] or hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise ValidationFailed("Source file evidence does not match its recorded hash")
            z.writestr("files/" + f["id"], data)
    raw = out.getvalue()
    if len(raw) > MAX_BYTES:
        raise ValidationFailed("Compressed package exceeds 256 MiB; split the release")
    audit(
        session,
        "knowledge.export",
        "knowledge_release",
        manifest["digest"],
        actor=actor,
        subjects=sorted(selected),
        source_files=len(files),
    )
    return raw


@dataclass
class Package:
    manifest: dict[str, Any]
    files: dict[str, bytes]


def _manifest_shape(m: dict[str, Any]) -> None:
    """Reject malformed records before preview or ORM constructors can see them."""
    fields = {
        "customers": "id code name erp_type default_target_schema",
        "rounds": "id customer_id label target_schema notes",
        "files": "id round_id customer_id sha256 original_name size kind profile fingerprint status",
        "mapping": "id customer_id round_id version status target_schema document confidence created_by model fingerprint_strict fingerprint_loose policy approved_by approved_at comment",
        "correction": "id customer_id kind target header before after actor mapping_version_id round_id status",
    }
    models = {
        "customers": Customer,
        "rounds": ImportRound,
        "files": SourceFile,
        "mapping": MappingVersion,
        "correction": Correction,
    }
    if not isinstance(m.get("schemas"), dict) or not m["schemas"]:
        raise ValueError("Missing schema contracts")
    if (
        not isinstance(m.get("roots"), list)
        or not m["roots"]
        or not all(isinstance(x, str) for x in m["roots"])
    ):
        raise ValueError("Invalid selected roots")
    for collection in ("customers", "rounds", "files", "entries"):
        if not isinstance(m.get(collection), list) or not 0 < len(m[collection]) <= MAX_ITEMS:
            raise ValueError("Invalid manifest collection: " + collection)
        for item in m[collection]:
            if not isinstance(item, dict):
                raise ValueError("Invalid record")
            kind, record = collection, item
            if collection == "entries":
                if set(item) != {"subject", "origin", "reviewed_by", "review_reason", "record"}:
                    raise ValueError("Invalid knowledge entry")
                if not all(
                    isinstance(item[k], str) and item[k]
                    for k in ("subject", "origin", "reviewed_by", "review_reason")
                ):
                    raise ValueError("Missing knowledge provenance")
                kind, record = item["subject"].split(":", 1)[0], item["record"]
            if kind not in fields or not isinstance(record, dict) or set(record) != set(fields[kind].split()):
                raise ValueError("Invalid record fields: " + kind)
            for key, val in record.items():
                column = models[kind].__table__.columns[key]
                if val is None:
                    if not column.nullable:
                        raise ValueError("Required field: " + key)
                    continue
                if isinstance(column.type, String) and (
                    not isinstance(val, str) or (column.type.length and len(val) > column.type.length)
                ):
                    raise ValueError("Invalid text/length: " + key)
                if isinstance(column.type, Integer) and (type(val) is not int or not -(2**31) <= val < 2**31):
                    raise ValueError("Invalid integer: " + key)
                if isinstance(column.type, Float) and type(val) not in (int, float):
                    raise ValueError("Invalid number: " + key)
            if not isinstance(record["id"], str) or not record["id"] or len(record["id"]) > 64:
                raise ValueError("Invalid entity ID")
            for k in ("customer_id", "round_id", "code", "name", "target_schema", "original_name", "sha256"):
                if k in record and record[k] is not None and not isinstance(record[k], str):
                    raise ValueError("Invalid reference/text: " + k)
            if kind == "files" and (type(record["size"]) is not int or record["size"] < 0):
                raise ValueError("Invalid evidence size")
            if kind == "files" and any(
                record[k] is not None and not isinstance(record[k], dict) for k in ("profile", "fingerprint")
            ):
                raise ValueError("Invalid source profile")
            if kind == "mapping" and (
                not isinstance(record["document"], dict) or not isinstance(record["policy"], dict)
            ):
                raise ValueError("Invalid mapping document/policy")
    subjects = [e["subject"] for e in m["entries"]]
    if len(set(subjects)) != len(subjects) or not set(m["roots"]) <= set(subjects):
        raise ValueError("Missing/duplicate selected knowledge")


def read_bundle(raw: bytes) -> Package:
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError("Package exceeds 256 MiB")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            if len(infos) > MAX_ITEMS or len({i.filename for i in infos}) != len(infos):
                raise ValueError("Duplicate or excessive archive entries")
            if (
                sum(i.file_size for i in infos) > MAX_BYTES
                or z.getinfo("manifest.json").file_size > 8 * 1024 * 1024
            ):
                raise ValueError("Expanded package too large")
            m = json.loads(z.read("manifest.json"))
            if not isinstance(m, dict):
                raise ValueError("Manifest must be an object")
            digest = m.pop("digest")
            if m.get("format") != "forma-knowledge-v1" or stable_hash(m) != digest:
                raise ValueError("Manifest digest mismatch")
            uuid.UUID(m["source_instance"])
            _manifest_shape(m)
            files = {}
            for f in m["files"]:
                if not isinstance(f["id"], str) or "/" in f["id"] or "\\" in f["id"] or f["id"] in files:
                    raise ValueError("Invalid/duplicate file ID")
                data = z.read("files/" + f["id"])
                if len(data) != f["size"] or hashlib.sha256(data).hexdigest() != f["sha256"]:
                    raise ValueError("File hash mismatch")
                files[f["id"]] = data
            if set(z.namelist()) != {"manifest.json", *("files/" + i for i in files)}:
                raise ValueError("Unexpected archive content")
            m["digest"] = digest
            return Package(m, files)
    except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValidationFailed("Invalid knowledge package", details={"reason": str(exc)}) from exc


def _id(source: str, kind: str, old: str) -> str:
    return kind + "_k" + stable_hash([source, kind, old])[:32]


def _evidence_key(digest: str, file: dict[str, Any]) -> str:
    # Readers select Excel/CSV engines by suffix, including when materialized from S3.
    return f"knowledge/{digest}/{file['sha256']}/{safe_name(file['original_name'])}"


def _rewrite(value: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    # Only declared envelope paths are references. Business literals may themselves
    # have keys such as customer_id/dependencies; never recurse into their values.
    result = copy.deepcopy(value)

    def refs(obj: dict[str, Any], names: tuple[str, ...]) -> None:
        for key in names:
            if isinstance(obj.get(key), str):
                obj[key] = ids.get(obj[key], obj[key])

    def deps(obj: dict[str, Any], key: str) -> None:
        if key in obj:
            obj[key] = [s.split(":", 1)[0] + ":" + ids[s.split(":", 1)[1]] for s in obj[key]]

    refs(result, ("customer_id", "round_id", "mapping_version_id"))
    if isinstance(result.get("document"), dict):
        refs(result["document"].get("meta", {}), ("customer_id",))
    policy = result.get("policy")
    if isinstance(policy, dict):
        refs(policy, ("reused_from",))
        deps(policy, "knowledge_dependencies")
        learning = policy.get("learning")
        if isinstance(learning, dict):
            deps(learning, "dependencies")
            if "knowledge_ids" in learning:
                learning["knowledge_ids"] = [ids[s] for s in learning["knowledge_ids"]]
        for decision in policy.get("confirmed_decisions") or []:
            refs(decision, ("knowledge_mapping_id", "knowledge_id"))
    if result.get("kind") == "decision" and isinstance(result.get("after"), dict):
        refs(result["after"], ("knowledge_mapping_id", "knowledge_id"))
    return result


def preview(session: Session, ctx: ServiceContext, package: Package) -> dict[str, Any]:
    existing: Any
    cls: Any
    row: Any
    m = package.manifest
    errors, items, ids, snapshot = [], [], {}, []
    receipt = session.get(KnowledgeRelease, m["digest"])
    if receipt:
        return {
            "digest": m["digest"],
            "status": receipt.state,
            "items": [],
            "errors": [],
            "plan_digest": stable_hash([m["digest"], receipt.state]),
            "already_imported": True,
        }
    if instance(session) == m["source_instance"]:
        errors.append("Source and destination are the same database")
    schemas: dict[str, TargetSchema] = {}
    for ref, doc in m["schemas"].items():
        try:
            Draft202012Validator.check_schema(doc)
            sid, ver = ref.rsplit("@", 1)
            schema = TargetSchema(sid, int(ver), doc, None)
            if (
                schema.ref != ref
                or doc.get("x-forma", {}).get("schema_id") != sid
                or int(doc.get("x-forma", {}).get("version", 0)) != int(ver)
            ):
                raise ValueError("Invalid schema ref")
            schemas[ref] = schema
            stored = session.get(StoredSchema, (sid, int(ver)))
            try:
                existing = stored.document if stored else ctx.registry.get_ref(ref).document
            except NotFound:
                existing = None
            if existing is not None and stable_hash(existing) != stable_hash(doc):
                errors.append(f"Schema conflict: {ref}; publish a new schema version first")
            snapshot.append(["schema", ref, stable_hash(existing)])
            items.append({"kind": "schema", "name": ref, "action": "reuse" if existing else "create"})
        except Exception as exc:
            errors.append(f"Invalid schema {ref}: {type(exc).__name__}")
    for c in m["customers"]:
        existing = session.scalar(select(Customer).where(Customer.code == c["code"]))
        if existing and (
            existing.name != c["name"] or existing.erp_type != c["erp_type"] or not existing.active
        ):
            errors.append(f"Customer conflict: {c['code']}; resolve identity before importing")
        ids[c["id"]] = existing.id if existing else _id(m["source_instance"], "cus", c["id"])
        snapshot.append(
            ["customer", c["code"], _fields(existing, "id code name erp_type active") if existing else None]
        )
        items.append({"kind": "customer", "name": c["code"], "action": "reuse" if existing else "create"})
    if len(ids) != len(m["customers"]) or len({c["code"] for c in m["customers"]}) != len(m["customers"]):
        errors.append("Duplicate customer identity")
    seen = set(ids)
    for kind, records in (
        ("rnd", m["rounds"]),
        ("fil", m["files"]),
        ("map", [e["record"] for e in m["entries"] if e["subject"].startswith("mapping:")]),
        ("cor", [e["record"] for e in m["entries"] if e["subject"].startswith("correction:")]),
    ):
        for row in records:
            if row["id"] in seen:
                errors.append("Duplicate entity ID")
            seen.add(row["id"])
            ids[row["id"]] = _id(m["source_instance"], kind, row["id"])
    for kind, records, cls, fields in (
        ("rnd", m["rounds"], ImportRound, ("customer_id", "target_schema", "notes")),
        (
            "fil",
            m["files"],
            SourceFile,
            ("round_id", "customer_id", "sha256", "size", "kind", "original_name", "profile", "fingerprint"),
        ),
    ):
        for record in records:
            existing = session.get(cls, ids[record["id"]])
            expected = _rewrite(record, ids)
            snapshot.append(
                [kind, record["id"], {k: getattr(existing, k) for k in fields} if existing else None]
            )
            if existing and any(getattr(existing, k) != expected[k] for k in fields):
                errors.append(f"Immutable evidence conflict: {kind}:{record['id']}")
    known = {e["subject"] for e in m["entries"]}
    graph: dict[str, set[str]] = {}
    source_rounds = {q["id"]: q for q in m["rounds"]}
    customer_ids = {c["id"] for c in m["customers"]}
    for q in source_rounds.values():
        if q["customer_id"] not in customer_ids:
            errors.append("Source round has no matching customer")
    for entry in m["entries"]:
        r = entry["record"]
        cls = MappingVersion if entry["subject"].startswith("mapping:") else Correction
        try:
            row = (
                cls(**{**r, "approved_at": dt.datetime.fromisoformat(r["approved_at"])})
                if cls is MappingVersion
                else cls(**r)
            )
            if entry["subject"] != gate.subject(row) or entry["origin"] not in gate.REAL_ORIGINS:
                raise ValueError("Invalid origin or subject")
            if not gate.dependencies(row) <= known:
                raise ValueError("Missing knowledge dependency")
            graph[entry["subject"]] = gate.dependencies(row)
            if row.customer_id and row.customer_id not in {c["id"] for c in m["customers"]}:
                raise ValueError("Missing customer")
            if isinstance(row, MappingVersion):
                schema = schemas[row.target_schema]  # pinned ref only; never resolve a moving latest alias
                doc = MappingDocument.model_validate(row.document)
                rep = validate_mapping(doc, schema)
                v = (row.policy or {}).get("verification") or {}
                if not rep.ok or not v.get("complete") or not v.get("passed") or not row.approved_at:
                    raise ValueError("Mapping not verified or not compatible")
                if row.status not in {"approved", "superseded"}:
                    raise ValueError("Mapping is not approved")
                if (row.policy or {}).get("target_schema_hash") != stable_hash(schema.document):
                    raise ValueError("Schema hash differs from verified contract")
                if row.round_id not in {q["id"] for q in m["rounds"]}:
                    raise ValueError("Missing source round")
                if source_rounds[row.round_id]["customer_id"] != row.customer_id or not any(
                    f["round_id"] == row.round_id for f in m["files"]
                ):
                    raise ValueError("Source round/customer/file evidence does not match")
            elif row.status != "confirmed" or not row.mapping_version_id:
                raise ValueError("Unconfirmed or unscoped correction")
            existing = session.get(cls, ids[row.id])
            label = gate.label_for(session, gate.subject(existing)) if existing else None
            snapshot.append(
                [
                    entry["subject"],
                    gate.content(session, existing) if existing else None,
                    label.state if label else None,
                ]
            )
            if existing:
                # A different release may reference the same immutable record. Do not overwrite or revive it.
                translated = _rewrite(r, ids)
                if cls is MappingVersion:
                    translated["approved_at"] = dt.datetime.fromisoformat(translated["approved_at"])
                same = all(getattr(existing, k) == v for k, v in translated.items() if k != "id")
                if not same or not gate.allowed(session, existing, strict=True):
                    errors.append(f"Existing knowledge changed/revoked: {entry['subject']}")
            items.append(
                {
                    "kind": "mapping" if cls is MappingVersion else "answer",
                    "name": entry["subject"],
                    "action": "reuse" if existing else "create",
                    "reviewed_by": entry["reviewed_by"],
                }
            )
        except Exception as exc:
            errors.append(f"Invalid knowledge {entry['subject']}: {str(exc)[:180]}")
    for f in m["files"]:
        if f["round_id"] not in source_rounds or f["customer_id"] not in customer_ids:
            errors.append("File evidence has missing dependencies")
        elif source_rounds[f["round_id"]]["customer_id"] != f["customer_id"]:
            errors.append("File evidence belongs to another customer")
        items.append(
            {"kind": "file", "name": f["original_name"], "bytes": f["size"], "action": "copy evidence"}
        )
    visited: set[str] = set()

    def visit(key: str, trail: frozenset[str]) -> None:
        if key in trail or len(trail) > 64:
            raise ValueError("Cyclic or overly deep knowledge dependencies")
        if key in visited:
            return
        for dep in graph.get(key, set()):
            visit(dep, trail | {key})
        visited.add(key)

    try:
        for key in graph:
            visit(key, frozenset())
    except ValueError as exc:
        errors.append(str(exc))
    return {
        "digest": m["digest"],
        "status": "blocked" if errors else "ready",
        "items": items,
        "errors": errors,
        "ids": ids,
        "already_imported": False,
        "plan_digest": stable_hash([m["digest"], instance(session), snapshot, errors]),
    }


def apply(session: Session, ctx: ServiceContext, package: Package, digest: str, actor: str) -> dict[str, Any]:
    from forma.services import config

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(74603821)"))
    plan = preview(session, ctx, package)
    if plan["plan_digest"] != digest or plan["errors"]:
        raise ValidationFailed("Import plan changed or is blocked; preview again", details=plan)
    if plan["already_imported"]:
        if plan["status"] != "active":
            raise ValidationFailed("This release was revoked; it cannot be silently reactivated")
        return {"id": plan["digest"], "created": False}
    m, ids = package.manifest, plan["ids"]
    instance(session, create=True)
    # Content-addressed storage writes are safe to retry. If DB commit fails, leftover objects
    # are unreachable evidence, never eligible knowledge; a subsequent retry uses the same keys.
    for f in m["files"]:
        ctx.storage.put(_evidence_key(m["digest"], f), package.files[f["id"]])
    for ref, doc in m["schemas"].items():
        sid, version = ref.rsplit("@", 1)
        if session.get(StoredSchema, (sid, int(version))) is None:
            session.add(StoredSchema(schema_id=sid, version=int(version), document=doc, created_by=actor))
    for c in m["customers"]:
        if session.get(Customer, ids[c["id"]]) is None:
            session.add(
                Customer(
                    id=ids[c["id"]],
                    code=c["code"],
                    name=c["name"],
                    erp_type=c["erp_type"],
                    default_target_schema=None,
                )
            )
    session.flush()
    for r in m["rounds"]:
        if session.get(ImportRound, ids[r["id"]]) is None:
            session.add(
                ImportRound(
                    id=ids[r["id"]],
                    customer_id=ids[r["customer_id"]],
                    label=("Knowledge evidence: " + (r["label"] or r["id"]))[:255],
                    status="cancelled",
                    target_schema=r["target_schema"],
                    notes=r["notes"],
                    closed_reason="Imported evidence only; no processing job was enqueued",
                )
            )
    session.flush()
    for f in m["files"]:
        if session.get(SourceFile, ids[f["id"]]) is None:
            data = _rewrite(f, ids)
            data["id"] = ids[f["id"]]
            session.add(SourceFile(**data, storage_key=_evidence_key(m["digest"], f)))
    session.flush()
    session.add(
        KnowledgeRelease(
            id=m["digest"],
            source_instance=m["source_instance"],
            manifest={"roots": m["roots"], "ids": ids, "items": plan["items"]},
            actor=actor,
        )
    )
    session.flush()
    created = []
    for entry in m["entries"]:
        r = entry["record"]
        cls = MappingVersion if entry["subject"].startswith("mapping:") else Correction
        if session.get(cls, ids[r["id"]]):
            continue
        data = _rewrite(r, ids)
        data["id"] = ids[r["id"]]
        if cls is MappingVersion:
            data["approved_at"] = dt.datetime.fromisoformat(data["approved_at"])
        row = cls(**data)
        session.add(row)
        session.flush()
        label = KnowledgeLabel(
            subject=gate.subject(row),
            origin=entry["origin"],
            state="allowed",
            digest=stable_hash(gate.content(session, row)),
            actor=actor,
            reason=f"Imported reviewed release; source reviewer: {entry['reviewed_by']}",
            release_id=m["digest"],
        )
        session.add(label)
        created.append(label.subject)
    session.flush()
    for key in created:
        if not gate.allowed(session, gate.lookup(session, key), strict=True):
            raise ValidationFailed("Imported dependency graph is not eligible; transaction must roll back")
    config._put(session, "knowledge.revision", {"release": m["digest"]}, actor)
    audit(
        session,
        "knowledge.import",
        "knowledge_release",
        m["digest"],
        actor=actor,
        subjects=created,
        source_instance=m["source_instance"],
        plan_digest=digest,
    )
    return {"id": m["digest"], "created": True, "subjects": created}


def revoke(session: Session, release_id: str, actor: str, reason: str) -> dict[str, Any]:
    from forma.services import config

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(74603821)"))
    r = session.get(KnowledgeRelease, release_id)
    if not r:
        raise NotFound("Release not found")
    if not reason.strip():
        raise ValidationFailed("A reason is required")
    r.state, r.revoked_by, r.revoked_at = "revoked", actor, utcnow()
    keys = []
    for label in session.scalars(select(KnowledgeLabel).where(KnowledgeLabel.release_id == release_id)):
        label.state, label.actor, label.reason = "revoked", actor, reason
        keys.append(label.subject)
    session.flush()
    # Report only descendants of this release, not unrelated pending/revoked knowledge.
    impacted = set(keys)
    candidates = [*session.scalars(select(MappingVersion)), *session.scalars(select(Correction))]
    changed = True
    while changed:
        changed = False
        for item in candidates:
            key = gate.subject(item)
            if key not in impacted and gate.dependencies(item) & impacted:
                impacted.add(key)
                changed = True
    affected = sorted(k.split(":", 1)[1] for k in impacted if k.startswith("mapping:"))
    config._put(session, "knowledge.revision", {"revoked": release_id}, actor)
    audit(
        session,
        "knowledge.revoke",
        "knowledge_release",
        release_id,
        actor=actor,
        reason=reason,
        affected_mapping_ids=affected,
    )
    return {
        "id": release_id,
        "state": "revoked",
        "subjects": keys,
        "review_mapping_ids": affected,
        "published_outputs_changed": False,
    }
