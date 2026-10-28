# ADR-012 — Storage behind one protocol; rows store keys, not paths

**Status** accepted (Phase 7).

## Decision

1. **`Storage` protocol** (`forma.storage`): `put / get / open / exists / delete / size / list / url /
   local_path / healthy`. Two implementations: `LocalStorage` (the data directory, dev default) and
   `S3Storage` (boto3; AWS S3 or MinIO via `FORMA_S3_ENDPOINT_URL`). Selected by `FORMA_STORAGE=local|s3`.
2. **Keys are the identity.** Source files: `files/<sha2>/<sha256>/<safe name>` (content-addressed,
   immutable); datasets: `datasets/<customer>/<version id>.jsonl`; exports:
   `exports/<customer>/<round>_<version>_<stamp>.<fmt>`. `SourceFile.storage_key`,
   `DatasetVersion.records_key` and `ExportArtifact.storage_key` hold keys; migration `0002` renames the
   old path columns and converts absolute paths under the data directory to keys.
3. **Readers get files, not streams.** fastexcel/Polars need a path, so `local_path(key)` returns the file
   itself on local storage and a per-node cache copy (`<data>/cache/<key>`) on S3. Because every key is
   immutable, the cache needs no invalidation; retention prunes it.
4. **Writes are atomic.** Local: temp file + rename. S3: single `put_object`/`upload_file` (atomic by
   definition). Dataset versions write the JSONL object *before* the DB row (ADR-015).
5. **Downloads**: with S3 the API answers `307` to a pre-signed URL (`FORMA_S3_PRESIGN_S`, attachment
   disposition); with local storage it streams the object. Clients never see a storage path.
6. **Names are sanitised once** (`safe_name`): directory parts and traversal are dropped, separators
   and control characters replaced; Thai and other non-ASCII names are kept.

## Why

Moving off a single node must not touch services: they already only know keys and a `ServiceContext`.
MinIO in compose makes the S3 path testable locally; `moto` makes it testable in CI without Docker.

## Consequences

* Code must never build a `Path` from a key; it asks the storage (`ctx.storage.local_path`).
* Backups are "DB dump + storage manifest" (ADR-015): the manifest lists keys, so restores work on
  either backend.
* S3 adds a download per cold read on a worker; profiling and transforming a round reads each file once
  per stage, so the cache keeps the extra cost to one transfer per node.
