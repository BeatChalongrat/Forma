# ADR-054 — A backup holds everything, and has been restored

**Status** accepted · 2026-11 · supersedes point 6 of ADR-053 and its consequence on objects; builds on ADR-015

## Context

`backup-nightly` ran with `--no-objects`, an option meant for a bucket replicated somewhere else. Here the bucket
sits on the same disk. Opening a real backup showed a database dump and a list of keys: 444 KB against 10 MB of
objects. Nobody had restored one on this stack, and doing so found more:

* `forma backup` named three kinds of object. Chat files, works (ADR-049, ADR-050) and sandbox drill-downs (ADR-018)
  were neither copied nor listed in the manifest, so a restore could not have said they were missing.
* No backup of this stack could be restored. The image's `pg_dump` 17 writes `SET transaction_timeout`, Postgres 16
  refuses it, and `ON_ERROR_STOP` ends the restore on the header.
* Copying through `local_path` downloads every object twice and leaves a second copy of the bucket in the S3 cache.
* The nightly loop pruned old backups even after a failed run, and an interrupted archive carried the real name.

## Decision

1. **The nightly backup holds the files.** `backup-nightly` and `backup` drop `--no-objects`, and both wait for MinIO.
   The option stays for a bucket replicated elsewhere.
2. **A bucket is taken whole.** It holds nothing but objects, so a new kind of object cannot be missed. The local data
   directory also holds the database and the backups, so there the kinds are named.
3. **One read per object.** The bytes are written and hashed from the same stream, so the sha256 in the manifest is of
   what the backup holds, and the cache is left alone.
4. **Only a finished backup carries the name.** Until then it is a hidden partial. A failed run removes its own, the
   next run sweeps one older than a day, and old backups are pruned only after a success.
5. **Restore drops that one setting from the dump's header.** Only the header is touched, so a row holding the same
   text stays. Pinning the client to the server's version would not have saved the backups already taken.
6. **System health says what the last backup holds.** It shows database and files with the size, or database only in
   amber, so going back to the old state cannot pass unseen.

## Consequences

* Fourteen full copies take about fourteen times the compressed store, and a run needs one more uncompressed copy
  while it works. Today one backup is 4 MB and the disk has 689 GB free. Objects are immutable, so a mirror that copies
  only new keys is the next step if this ever matters.
* The backups share the disk with the data. They cover a mistake, a bad migration or a damaged store, not losing the
  machine. R36 stays open until a copy leaves it.
* A restore on another machine needs `.env.production` too, because AI keys are stored encrypted with
  `FORMA_SECRET_KEY`. A backup holds customer data in the clear, so any copy needs the same care as the data.
* An object removed between listing and reading fails that night's run. Older backups stay, System health turns amber
  after a day, and the next run passes.

## How it was checked

The newest backup was restored into a throwaway Postgres 16 and MinIO on an internal Docker network, with the backups
volume read-only. All 127 objects came back with no key missing and every sha256 equal to the manifest. All 24 tables
matched the live stack, except the one `backup.create` audit row written after the dump. The restored app passed
`/ready` and returned the same bytes as the live app for a dataset's rows. The oldest backup on the volume, taken before
this change, restored too, with its 21 customers.

`test_retention_backup.py` covers the bytes against the manifest, every kind of object, a bucket taken whole, the cache
left alone, a failed run leaving nothing, the name and the sweep, the dump header, the health page, and the compose
file keeping the files and pruning only after a success.
