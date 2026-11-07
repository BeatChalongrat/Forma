"""No provider calls: complete discovery, unit-safe comparisons and immutable editorial revisions."""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import os
import zipfile

import pytest
from tests.test_assistant_reports import args, seed_records
from tests.test_document_reports import app as app
from tests.test_document_reports import make_job
from tests.test_workspace_tools import call

from forma.auth import SYSTEM, Principal
from forma.core.errors import NotFound, ValidationFailed
from forma.db.models import (
    AssistantConversation,
    AssistantTurn,
    Correction,
    Customer,
    DatasetVersion,
    ImportRound,
    KnowledgeLabel,
    SourceFile,
)
from forma.db.session import session_scope
from forma.jobs import runner
from forma.reporting import authoring
from forma.reporting.brand_templates import inspect_template
from forma.services import conversations, document_reports, memory, workspace_search
from forma.services.context import get_context


def test_search_scans_beyond_first_page_and_preserves_customer_scope(app):
    c, _ = app
    with session_scope() as s:
        s.add_all([Customer(id="c1", code="ONE", name="First"), Customer(id="c2", code="TWO", name="Other")])
        s.flush()
        s.add_all([ImportRound(id="r1", customer_id="c1"), ImportRound(id="r2", customer_id="c2")])
        s.flush()
        s.add_all(
            [
                SourceFile(
                    id=f"f{i:04}",
                    round_id="r1",
                    customer_id="c1",
                    original_name=f"inventory {i}.xlsx",
                    sha256="x",
                    size=1,
                    kind="excel",
                    storage_key="unused",
                )
                for i in range(125)
            ]
        )
        s.add(
            SourceFile(
                id="hidden",
                round_id="r2",
                customer_id="c2",
                original_name="inventory private.xlsx",
                sha256="x",
                size=1,
                kind="excel",
                storage_key="unused",
            )
        )
    with session_scope() as s:
        p = Principal(
            subject="tester", roles=frozenset({"importer"}), customers=frozenset({"c1"}), mode="dev"
        )
        found = workspace_search.search(s, p, {"query": "สินค้าคงคลัง", "sources": ["files"]})
        assert found["total"] == 125 and found["coverage"]["scan_complete"]
        assert len(found["items"]) == 20 and found["next_offset"] == 20
        last = workspace_search.search(s, p, {"query": "สินค้าคงคลัง", "sources": ["files"], "offset": 120})
        assert len(last["items"]) == 5 and last["next_offset"] is None
        assert "not all source cells" in last["scope"]["contents"]
        with pytest.raises(Exception, match=r"[Cc]ustomer"):
            workspace_search.search(s, p, {"query": "inventory", "customer_id": "c2"})
    # Registered through MCP, not an orphaned internal helper.
    assert call(c, "search_workspace", {"query": "ONE", "sources": ["customers"]})["items"]


def test_old_chat_search_counts_all_and_never_reads_other_owners(app):
    _, _ = app
    owner, access = conversations.identity(SYSTEM)
    with session_scope() as s:
        for i in range(112):
            s.add(
                AssistantConversation(
                    id=f"chat{i}", owner_key=owner, access_key=access, title=f"Discussion {i}"
                )
            )
            s.add(
                AssistantTurn(
                    id=f"turn{i}",
                    conversation_id=f"chat{i}",
                    request_id="q",
                    sequence=1,
                    question="warehouse inventory",
                    answer="answer",
                    status="complete",
                    created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(minutes=i),
                )
            )
        s.add(AssistantConversation(id="private", owner_key="different", access_key=access, title="Secret"))
        s.add(
            AssistantTurn(
                conversation_id="private",
                request_id="q",
                sequence=1,
                question="inventory",
                answer="PRIVATE",
                status="complete",
            )
        )
    with session_scope() as s:
        old = memory.search_chats(s, conversations.owned(SYSTEM), "current", "inventory", 110)
        assert old["total"] == 112 and len(old["results"]) == 2 and old["next_offset"] is None
        found = workspace_search.search(s, SYSTEM, {"query": "สต็อก", "sources": ["chats"]})
        assert found["total"] == 112 and "PRIVATE" not in json.dumps(found)
        assert all(item["historical_not_live"] for item in found["items"])


def test_search_does_not_recommend_revoked_knowledge(app):
    with session_scope() as s:
        s.add(
            Correction(
                id="revoked",
                customer_id="c",
                kind="constant",
                target="/vat",
                after=True,
                status="confirmed",
                actor="operator",
            )
        )
        s.add(
            KnowledgeLabel(
                subject="correction:revoked",
                origin="production",
                state="revoked",
                digest="old",
                actor="operator",
                reason="Withdrawn test knowledge",
            )
        )
    with session_scope() as s:
        assert workspace_search.search(s, SYSTEM, {"query": "vat", "sources": ["knowledge"]})["total"] == 0


def second_dataset(records, *, customer="customer-report"):
    ctx = get_context()
    key = ctx.dataset_key(customer, "dsv_after")
    ctx.storage.put(key, ("\n".join(json.dumps(r) for r in records) + "\n").encode())
    with session_scope() as s:
        s.add(
            DatasetVersion(
                id="dsv_after",
                round_id="rnd_report",
                customer_id=customer,
                mapping_version_id="map_report",
                records_key=key,
                row_count=len(records),
                version=2,
            )
        )


def comparison(**extra):
    return {
        k: v
        for k, v in args(
            **{"report": "dataset_comparison", "dataset_ids": ["dsv_report", "dsv_after"], **extra}
        ).items()
        if k != "dataset_id"
    }


def test_compare_exact_decimal_missing_zero_and_units(app):
    c, _ = app
    seed_records(
        [
            {"department": "A", "amount": "0.1", "currency": "THB"},
            {"department": "A", "amount": "0.2", "currency": "THB"},
            {"department": "Z", "amount": 0, "currency": "THB"},
        ]
    )
    second_dataset(
        [
            {"department": "A", "amount": "0.60", "currency": "THB"},
            {"department": "Z", "amount": 4, "currency": "THB"},
            {"department": "NEW", "amount": 10, "currency": "THB"},
        ]
    )
    result = call(c, "build_report", comparison())["report"]
    rows = {r["label"]: r for r in result["rows"]}
    assert rows["A"]["change"] == "0.30" and rows["A"]["percent"] == "100.00"
    assert rows["Z"]["percent"] is None and rows["NEW"]["before"] is None and rows["NEW"]["change"] is None
    assert result["comparison"]["unit"] == "THB"
    assert result["comparison"]["coverage"][0]["scanned"] == 3
    from forma.services.assistant_reports import build_report

    with session_scope() as s:
        with pytest.raises(ValidationFailed, match="unit"):
            build_report(get_context(), s, SYSTEM, comparison(unit="USD"))
        with pytest.raises(ValidationFailed, match="distinct"):
            build_report(get_context(), s, SYSTEM, comparison(dataset_ids=["dsv_report", "dsv_report"]))


def test_compare_rejects_currency_and_customer_mismatch(app):
    seed_records([{"department": "A", "amount": 1, "currency": "THB"}])
    second_dataset([{"department": "A", "amount": 1, "currency": "USD"}])
    from forma.services.assistant_reports import build_report

    with session_scope() as s:
        with pytest.raises(ValidationFailed, match="currencies"):
            build_report(get_context(), s, SYSTEM, comparison())
        s.get(DatasetVersion, "dsv_after").customer_id = "other"
        s.flush()
        with pytest.raises(ValidationFailed, match="same customer"):
            build_report(get_context(), s, SYSTEM, comparison())


def test_large_exact_totals_do_not_use_default_decimal_rounding(app):
    c, _ = app
    seed_records(
        [
            {"department": "A", "amount": "12345678901234567890.1234567890123456789", "currency": "THB"},
            {"department": "A", "amount": "0.0000000000000000001", "currency": "THB"},
        ]
    )
    result = call(c, "build_report", args())["report"]
    assert result["rows"][0]["value"] == "12345678901234567890.1234567890123456790"
    from decimal import Decimal

    from forma.services.report_numbers import percentage

    assert percentage(Decimal("1e40"), Decimal("1e-10")) == Decimal("1e52")


def section(id="summary", **extra):
    return {
        "id": id,
        "layout": "summary",
        "title": "ผลการดำเนินงาน",
        "points": ["ยอดรวม {{e5.r0.value}}"],
        "evidence_ids": [5],
        **extra,
    }


def test_narrative_binding_and_targeted_edit_preserve_other_sections(app):
    c, _ = app
    _, jid, _ = make_job(
        c,
        brief={"title": "รายงานผลการดำเนินงาน", "audience": "ผู้บริหาร", "purpose": "ประกอบการตรวจข้อมูล"},
        sections=[section(), section("actions", layout="actions", points=["ตรวจสอบข้อมูลต้นทางก่อนเผยแพร่"])],
    )
    spec = c.get(f"/api/assistant/reports/{jid}").json()["snapshot"]
    assert spec["sections"][0]["points"] == ["ยอดรวม 0.30"]
    old = copy.deepcopy(spec)
    new = authoring.revise(spec, [section(points=["ค่าที่ตรวจได้ {{e5.r0.value}}"], layout="findings")])
    assert new["sections"][1] == spec["sections"][1] and new["sources"] == spec["sources"]
    assert spec == old and new["revision"]["data_changed"] is False
    for bad in (
        "ยอดรวม 999",
        "ยอดรวม {{e5.r999.value}}",
        "ยอดรวม {{e5.r0.missing}}",
        "ยอดรวม {{e99.r0.value}}",
    ):
        with pytest.raises(ValidationFailed):
            authoring.revise(spec, [section(points=[bad])])
    with pytest.raises(ValidationFailed, match="existing"):
        authoring.revise(spec, [section("invented")])


def test_revision_tool_and_stale_base_are_enforced(app):
    c, _ = app
    chat, jid, _ = make_job(c, sections=[section()])
    assert runner.run_job(jid)
    artifact = c.get(f"/api/assistant/reports/{jid}").json()["artifacts"][0]["id"]
    from tests.test_artifacts import model, say

    model(
        ("inspect_document", {"artifact_id": artifact}),
        (
            "create_document",
            {
                "template": "executive",
                "formats": ["docx", "pptx", "pdf"],
                "revise_artifact_id": artifact,
                "base_version": 1,
                "section_patches": [section(layout="findings", points=["ยืนยันจากข้อมูล {{e5.r0.value}}"])],
            },
        ),
    )
    say(c, chat, request="revision-0001")
    jobs = c.get(f"/api/assistant/conversations/{chat}/reports").json()["items"]
    assert len(jobs) == 2
    revised = next(j for j in jobs if j["id"] != jid)
    model(
        (
            "create_document",
            {
                "template": "executive",
                "formats": ["docx", "pptx", "pdf"],
                "revise_artifact_id": artifact,
                "base_version": 1,
                "section_patches": [section(points=["อีกคำขอที่เริ่มจากข้อมูล {{e5.r0.value}}"])],
            },
        )
    )
    say(c, chat, request="revision-race")
    pending = c.get(f"/api/assistant/conversations/{chat}/reports").json()["items"]
    assert len(pending) == 3
    competing = next(j for j in pending if j["id"] not in (jid, revised["id"]))
    assert runner.run_job(revised["id"])
    assert c.get(f"/api/assistant/reports/{revised['id']}").json()["status"] == "done"
    runner.run_job(competing["id"])
    assert c.get(f"/api/assistant/reports/{competing['id']}").json()["status"] == "failed"
    assert max(v["number"] for v in c.get(f"/api/assistant/artifacts/{artifact}").json()["versions"]) == 2
    model(
        ("inspect_document", {"artifact_id": artifact}),
        (
            "create_document",
            {
                "template": "executive",
                "formats": ["docx", "pptx", "pdf"],
                "revise_artifact_id": artifact,
                "base_version": 1,
                "section_patches": [section()],
            },
        ),
    )
    say(c, chat, request="revision-0002")
    assert len(c.get(f"/api/assistant/conversations/{chat}/reports").json()["items"]) == 3
    with session_scope() as s:
        stranger = Principal(subject="stranger", roles=frozenset({"admin"}), customers=None, mode="dev")
        with pytest.raises(NotFound):
            document_reports.inspect(s, stranger, artifact)


def zipped(contents):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize(
    "contents",
    [
        {"word/document.xml": "<root/>", "word/vbaProject.bin": b"macro"},
        {
            "word/document.xml": "<root/>",
            "_rels/.rels": '<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>',
        },
        {"word/document.xml": '<!DOCTYPE root [<!ENTITY x "bad">]><root/>'},
    ],
)
def test_templates_reject_active_or_external_content(contents):
    with pytest.raises(ValidationFailed):
        inspect_template("company.docx", zipped(contents), "att")


def test_template_validation_and_format():
    data = zipped({"word/document.xml": "<root/>"})
    meta = inspect_template("company.docx", data, "att")
    assert meta["format"] == "docx" and len(meta["sha256"]) == 64
    with pytest.raises(ValidationFailed):
        inspect_template("company.pdf", data, "att")
    with pytest.raises(ValidationFailed):
        inspect_template("broken.docx", b"not zip", "att")


@pytest.mark.skipif(
    os.environ.get("FORMA_TEST_REPORT_DOCKER") != "1", reason="Real renderer, no network or provider"
)
def test_real_authored_layouts(app):
    c, sandbox = app
    _, jid, _ = make_job(
        c,
        brief={"title": "รายงานสำหรับผู้บริหาร", "audience": "ทีมปฏิบัติการ", "purpose": "ตรวจผลก่อนนำไปใช้"},
        sections=[
            section(),
            section(
                "findings", layout="findings", title="ประเด็นที่ควรตรวจสอบ", points=["ค่าที่ตรวจได้ {{e5.r0.value}}"]
            ),
            section(
                "compare",
                layout="comparison",
                title="ข้อมูลประกอบการตัดสินใจ",
                points=["ข้อมูลชุดนี้ {{e5.r0.value}}", "อ้างอิงข้อมูลครบตามขอบเขต"],
            ),
            section("next", layout="actions", title="ขั้นตอนถัดไป", points=["ตรวจสอบความหมายทางธุรกิจก่อนส่งต่อ"]),
        ],
    )
    assert runner.run_job(jid)
    detail = c.get(f"/api/assistant/reports/{jid}").json()
    assert detail["status"] == "done", detail
    assert detail["quality"]["files"]["report.pptx"]["content_overlap"] == "passed"
    assert len(detail["outline"]) == 4 and sandbox.calls == 1


@pytest.mark.skipif(
    os.environ.get("FORMA_TEST_REPORT_DOCKER") != "1", reason="Real company template rendering, no API"
)
@pytest.mark.parametrize("fmt", ["docx", "pptx"])
def test_real_company_template_upload_and_render(app, fmt):
    from tests.test_artifacts import model, say

    c, sandbox = app
    script = """from pathlib import Path
from copy import deepcopy
from docx import Document
from pptx import Presentation
from pptx.util import Inches, Pt
Path("outputs").mkdir(exist_ok=True)
doc = Document()
doc.sections[0].header.paragraphs[0].text = "ACOMMERCE QA BRAND"
doc.add_paragraph("REMOVE ORIGINAL TEMPLATE BODY")
doc.save("outputs/company.docx")
deck = Presentation()
deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
slide = deck.slides.add_slide(deck.slide_layouts[6])
mark = slide.shapes.add_textbox(Inches(10), Inches(7.1), Inches(3), Inches(.3))
mark.text = "ACOMMERCE QA BRAND"
mark.text_frame.paragraphs[0].font.size = Pt(10)
deck.slide_master._element.spTree.append(deepcopy(mark._element))
slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = "REMOVE ORIGINAL TEMPLATE BODY"
deck.save("outputs/company.pptx")
"""
    sandbox.calls = 40 if fmt == "docx" else 50
    generated = sandbox.run(script, [], 60)
    assert generated.status == "ok", generated.stderr
    template_data = dict(generated.files)["company." + fmt]
    chat, _, _ = make_job(c)  # Earlier queued report is deliberately left alone.
    upload = c.post(
        f"/api/assistant/conversations/{chat}/attachments", files={"file": ("company." + fmt, template_data)}
    )
    assert upload.status_code == 200, upload.text
    maker = model(
        ("read_report_skill", {"skill": "formal-report"}),
        ("build_report", args()),
        (
            "create_document",
            {
                "template": "executive",
                "formats": [fmt],
                "evidence_ids": [4],
                "template_attachment_id": upload.json()["id"],
            },
        ),
    )
    original = maker.complete_json

    def complete(**kw):
        payload = json.loads(kw["user"])
        if maker.steps and maker.steps[0][0] == "create_document":
            maker.steps[0][1]["evidence_ids"] = [
                next(e["id"] for e in payload["evidence"] if e["tool"] == "build_report")
            ]
        return original(**kw)

    maker.complete_json = complete
    say(c, chat, request="company-template-0001")
    jobs = c.get(f"/api/assistant/conversations/{chat}/reports").json()["items"]
    branded = next(j for j in jobs if j.get("company_template"))
    assert runner.run_job(branded["id"])
    finished = c.get(f"/api/assistant/reports/{branded['id']}").json()
    assert finished["status"] == "done", finished
    artifact = c.get(f"/api/assistant/artifacts/{finished['artifacts'][0]['id']}").json()
    output = c.get(f"/api/assistant/attachments/{artifact['versions'][0]['file']['id']}").content
    with zipfile.ZipFile(io.BytesIO(output)) as z:
        xml = "\n".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xml"))
    assert "ACOMMERCE QA BRAND" in xml and "REMOVE ORIGINAL TEMPLATE BODY" not in xml
