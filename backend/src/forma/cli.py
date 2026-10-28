"""`forma` command line."""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="FORMA — AI-native data transformation platform")
schema_app = typer.Typer(help="Target schema registry")
app.add_typer(schema_app, name="schema")
db_app = typer.Typer(help="Database migrations (Alembic)")
app.add_typer(db_app, name="db")


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head")) -> None:
    """Apply migrations up to REVISION (default head)."""
    from forma.db import migrate
    from forma.db.session import get_engine

    migrate.upgrade(get_engine(), revision)
    typer.echo(json.dumps(migrate.status(get_engine())))


@db_app.command("downgrade")
def db_downgrade(revision: str) -> None:
    """Revert migrations down to REVISION (e.g. -1 or a revision id)."""
    from alembic import command

    from forma.db import migrate
    from forma.db.session import get_engine

    command.downgrade(
        migrate.alembic_config(get_engine().url.render_as_string(hide_password=False)), revision
    )
    typer.echo(json.dumps(migrate.status(get_engine())))


@db_app.command("revision")
def db_revision(m: str = typer.Option(..., "-m", "--message"), autogenerate: bool = True) -> None:
    """Create a new migration from the model diff (edit the generated file before committing)."""
    from forma.db import migrate

    migrate.revision(m, autogenerate=autogenerate)


@db_app.command("current")
def db_current() -> None:
    """Show current vs head revision."""
    from forma.db import migrate
    from forma.db.session import get_engine

    typer.echo(json.dumps(migrate.status(get_engine())))


@db_app.command("stamp")
def db_stamp(revision: str = typer.Argument("head")) -> None:
    """Mark the database as being at REVISION without running migrations."""
    from forma.db import migrate
    from forma.db.session import get_engine

    migrate.stamp(get_engine(), revision)
    typer.echo(json.dumps(migrate.status(get_engine())))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run("forma.api.app:app", host=host, port=port, reload=reload)


@app.command()
def worker(backend: str | None = None, workers: int | None = None) -> None:
    """Run a job worker. With FORMA_JOB_BACKEND=arq this is an arq worker (Redis); otherwise it drains the
    database queue with the thread/process pool and keeps polling for new rows (single-node fallback)."""
    import os
    import time

    from forma.core.logging import configure_logging
    from forma.core.settings import get_settings
    from forma.db.session import init_db
    from forma.jobs import handlers as _h  # noqa: F401
    from forma.jobs import runner

    if backend:
        os.environ["FORMA_JOB_BACKEND"] = backend
    if workers:
        os.environ["FORMA_WORKERS"] = str(workers)
    get_settings.cache_clear()
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    init_db()
    if s.job_backend == "arq":
        from arq.worker import run_worker

        run_worker(runner.arq_worker_settings())
        return
    typer.echo(json.dumps(runner.start()))
    try:
        while True:
            time.sleep(2.0)
            from sqlalchemy import select

            from forma.db.models import Job
            from forma.db.session import session_scope

            with session_scope() as sess:
                ids = list(sess.scalars(select(Job.id).where(Job.status == "queued")))
            for jid in ids:
                runner.get_backend().submit(jid)
    except KeyboardInterrupt:
        runner.stop()


@app.command()
def profile(path: Path, out: Path | None = None) -> None:
    """Profile an Excel/CSV file: sheets, tables, columns, fingerprint."""
    from forma.fingerprint.fingerprint import fingerprint_profile
    from forma.profiling.profile import profile_workbook

    prof = profile_workbook(path)
    fp = fingerprint_profile(prof)
    data = {"profile": prof.to_dict(), "fingerprint": fp.to_dict()}
    if out:
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        for t in prof.tables:
            typer.echo(f"[{t.sheet}] header_row={t.table.header_row} rows={t.n_rows} cols={len(t.columns)}")
            for c in t.columns:
                typer.echo(
                    f"   {c.name!r:40} {c.inferred_type:8} null={c.null_ratio:.2f} uniq={c.unique_ratio:.2f} {c.hints} {c.samples[:3]}"
                )
        typer.echo(f"fingerprint strict={fp.strict} loose={fp.loose}")


@app.command()
def propose(path: Path, schema: str = "product", out: Path | None = None, llm: bool = False) -> None:
    """Propose a mapping for a file (heuristic, optionally refined by the configured LLM)."""
    from forma.ai.heuristics import propose_mapping
    from forma.mapping_dsl.validate import validate_mapping
    from forma.profiling.profile import profile_workbook
    from forma.services.context import get_context

    ctx = get_context()
    sch = ctx.registry.get_ref(schema)
    prof = profile_workbook(path)
    for t in prof.tables:
        t.file_name = path.name
    prop = propose_mapping(prof, sch)
    doc = prop.mapping
    if llm and not ctx.llm.is_mock:
        from forma.ai.agents import mapping_agent

        doc, _ = mapping_agent(ctx.llm, prof, sch, prop)
    rep = validate_mapping(doc, sch)
    text = doc.to_yaml()
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out} (confidence={doc.meta.confidence}, valid={rep.ok})")
    else:
        typer.echo(text)
        typer.echo(json.dumps(rep.to_dict(), ensure_ascii=False))
        for q in prop.open_questions:
            typer.echo(f"? {q}")


@app.command()
def transform(
    path: Path, mapping: Path, out: Path = Path("out.jsonl"), fmt: str = "jsonl", limit: int | None = None
) -> None:
    """Run a mapping YAML over a file and write JSON/JSONL/CSV."""
    from forma.engine.transform import BoundSource, TransformEngine
    from forma.export.exporter import export_file
    from forma.mapping_dsl.models import MappingDocument
    from forma.services.context import get_context

    ctx = get_context()
    doc = MappingDocument.from_yaml(mapping.read_text(encoding="utf-8"))
    sch = ctx.registry.get_ref(doc.meta.target_schema)
    res = TransformEngine(sch).run(doc, {s.id: BoundSource(path) for s in doc.sources}, limit=limit)
    export_file(res.records, fmt, sch, out)
    typer.echo(json.dumps(res.summary(), ensure_ascii=False))
    for f in res.findings[:30]:
        typer.echo(f" - [{f.severity}] {f.kind}: {f.message}")
    typer.echo(f"wrote {out} ({len(res.records)} records)")


@app.command()
def synth(
    out: Path = Path("../data/synthetic"), seed: int = 42, variants: int = 30, scale: str = "small"
) -> None:
    """Generate the synthetic ERP corpus with ground truth."""
    from forma.synth.generator import generate_corpus

    stats = generate_corpus(out, seed=seed, n_variants=variants, scale=scale)
    typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))


@app.command()
def bench(
    files: int = 300,
    rows: int = 100_000,
    distinct: int = 8,
    backend: str | None = None,
    workers: int | None = None,
    out: Path = Path("../data/bench"),
    corpus: Path | None = None,
    fresh: bool = True,
) -> None:
    """End-to-end throughput benchmark on a throw-away database (see docs/PERFORMANCE.md)."""
    import os
    import shutil

    from forma.core.settings import get_settings

    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    run_dir = out / "run"
    if fresh and run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FORMA_DATABASE_URL"] = f"sqlite:///{(run_dir / 'bench.db').as_posix()}"
    os.environ["FORMA_DATA_DIR"] = str(run_dir / "data")
    os.environ.setdefault("FORMA_LLM_PROVIDER", "mock")
    if backend:
        os.environ["FORMA_JOB_BACKEND"] = backend
    if workers:
        os.environ["FORMA_WORKERS"] = str(workers)
    get_settings.cache_clear()
    from forma.bench.run import run_bench

    rep = run_bench(files=files, rows=rows, distinct=distinct, out_dir=out, corpus_dir=corpus)
    typer.echo(
        json.dumps(
            {k: v for k, v in rep.items() if k not in ("batch1", "batch2")}, ensure_ascii=False, indent=2
        )
    )
    typer.echo(f"wrote {out / 'report.json'} and {out / 'report.md'}")


@app.command()
def eval(corpus: Path = Path("../data/synthetic"), report: Path | None = None) -> None:
    """Evaluate heuristic mapping + engine against the synthetic corpus ground truth."""
    from forma.eval.evaluate import evaluate_corpus

    rep = evaluate_corpus(corpus)
    typer.echo(json.dumps(rep["summary"], ensure_ascii=False, indent=2))
    if report:
        report.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"wrote {report}")


@app.command()
def seed(corpus: Path = Path("../data/synthetic"), run: bool = True) -> None:
    """Load the synthetic corpus into the database as customers/rounds (demo data)."""
    from forma.synth.seed import seed_database

    typer.echo(json.dumps(seed_database(corpus, run_pipeline=run), ensure_ascii=False, indent=2))


@schema_app.command("list")
def schema_list() -> None:
    from forma.services.context import get_context

    for s in get_context().registry.list():
        typer.echo(f"{s.ref:16} {s.status:8} fields={len(s.leaf_fields())} identity={s.identity_keys}")


@schema_app.command("fields")
def schema_fields(ref: str = "product") -> None:
    from forma.services.context import get_context

    for f in get_context().registry.get_ref(ref).fields():
        typer.echo(
            f"{f.pointer:36} {'/'.join(f.json_type):14} {f.risk:8} {'req' if f.required else '   '} {f.semantic or ''}"
        )


@schema_app.command("validate")
def schema_validate(path: Path) -> None:
    from jsonschema import Draft202012Validator

    doc = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(doc)
    typer.echo("schema OK")


@schema_app.command("check-output")
def schema_check_output(ref: str, path: Path) -> None:
    from forma.services.context import get_context

    sch = get_context().registry.get_ref(ref)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    errs = sch.validate_records(records)
    typer.echo(f"{len(records)} records, {len(errs)} errors")
    for e in errs[:20]:
        typer.echo(f"  row {e['row']} {e['pointer']}: {e['message']}")


if __name__ == "__main__":
    app()
