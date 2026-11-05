"""Knowledge release commands for repeatable server deployments (operator shell access required)."""

import json
from pathlib import Path

import typer

from forma.db.session import session_scope
from forma.services import knowledge_gate as gate
from forma.services import knowledge_transfer as transfer
from forma.services.context import get_context

app = typer.Typer(help="Review/export/import/withdraw knowledge; never copies an entire development database")


@app.command("list")
def inventory(offset: int = 0) -> None:
    with session_scope() as session:
        typer.echo(json.dumps(transfer.inventory(session, offset=offset), ensure_ascii=False, default=str))


@app.command("classify")
def classify(subject: str, origin: str, state: str, actor: str, reason: str) -> None:
    with session_scope() as session:
        label = gate.classify(session, subject, origin, state, actor, reason)
        transfer.instance(session, create=True)
        typer.echo(json.dumps({"subject": label.subject, "state": label.state}))


@app.command("export")
def export(out: Path, subjects: list[str], actor: str = typer.Option(...)) -> None:
    with session_scope() as session:
        data = transfer.export_bundle(session, get_context(), subjects, actor)
        with out.open("xb") as stream:
            stream.write(data)
        typer.echo(f"Wrote reviewed knowledge and source evidence to {out}")


@app.command("preview")
def preview(package: Path) -> None:
    with package.open("rb") as stream:
        data = transfer.read_bundle(stream.read(transfer.MAX_BYTES + 1))
    with session_scope() as session:
        typer.echo(json.dumps(transfer.preview(session, get_context(), data), ensure_ascii=False))


@app.command("import")
def import_release(package: Path, plan_digest: str, actor: str) -> None:
    with package.open("rb") as stream:
        data = transfer.read_bundle(stream.read(transfer.MAX_BYTES + 1))
    with session_scope() as session:
        typer.echo(
            json.dumps(transfer.apply(session, get_context(), data, plan_digest, actor), ensure_ascii=False)
        )


@app.command("revoke")
def revoke(release_id: str, actor: str, reason: str) -> None:
    with session_scope() as session:
        typer.echo(json.dumps(transfer.revoke(session, release_id, actor, reason), ensure_ascii=False))
