"""Milestone 2: ingestion of every format into the normalised section/span model."""

import json

import pytest
from sqlalchemy import select

from scripts import sample_files
from tests.conftest import make_tenant


def upload(client, world, name, data, **fields):
    form = {"workflow_id": world["workflow_id"], "external_ref": name, **fields}
    return client.post("/v1/submissions", files={"file": (name, data)}, data=form, headers={"X-API-Key": world["api_key"]})


def spans_of(submission_id):
    from db.models import Document, Section, Span
    from db.session import session_scope

    with session_scope() as db:
        rows = db.execute(
            select(Section, Span).join(Span, Span.section_id == Section.id).join(Document, Document.id == Span.document_id)
            .where(Document.submission_id == submission_id, Document.role == "primary", Span.review_only.is_(False))
            .order_by(Section.order, Span.order)
        ).all()
        return [(s.kind, s.title, sp.kind, sp.text, sp.location) for s, sp in rows]


def test_plain_text_body(client):
    world = make_tenant()
    r = client.post("/v1/submissions", json={"workflow_id": world["workflow_id"], "text": "First para.\n\nSecond para here."},
                    headers={"X-API-Key": world["api_key"]})
    assert r.status_code == 201 and r.json()["status"] == "in_review"
    spans = spans_of(r.json()["id"])
    assert [s[3] for s in spans] == ["First para.", "Second para here."]


def test_txt_with_encoding_detection(client):
    world = make_tenant()
    data = "Café report\n\nThe figure is £1,200 for the année.".encode("latin-1")
    r = upload(client, world, "notes.txt", data)
    assert r.status_code == 201, r.text
    texts = [s[3] for s in spans_of(r.json()["id"])]
    assert texts[0] == "Café report" and "£1,200" in texts[1]


def test_docx_preserves_headings_tables_and_lists(client):
    world = make_tenant()
    r = upload(client, world, "letter.docx", sample_files.docx_bytes())
    assert r.status_code == 201, r.text
    spans = spans_of(r.json()["id"])
    kinds = {s[2] for s in spans}
    assert {"heading", "paragraph", "table", "list_item"} <= kinds
    assert any("Loan amount | £245,000" in s[3] for s in spans if s[2] == "table")
    assert spans[0][1] == "Mortgage affordability assessment"


def test_docx_comments_are_preserved(client):
    import io

    import docx

    d = docx.Document()
    p = d.add_paragraph("Revenue grew by 12% in 2025.")
    d.add_comment(p.runs, text="Check this against the accounts", author="Analyst")
    buf = io.BytesIO()
    d.save(buf)
    world = make_tenant()
    r = upload(client, world, "c.docx", buf.getvalue())
    spans = spans_of(r.json()["id"])
    assert any(s[2] == "comment" and "Check this" in s[3] for s in spans)


def test_pptx_one_section_per_slide_with_notes(client):
    world = make_tenant()
    r = upload(client, world, "deck.pptx", sample_files.pptx_bytes())
    assert r.status_code == 201, r.text
    spans = spans_of(r.json()["id"])
    slides = {s[4]["slide"] for s in spans}
    assert slides == {1, 2, 3, 4}
    assert all(s[0] == "slide" for s in spans)
    assert any(s[2] == "title" and s[3] == "Claims volume" for s in spans)
    assert any(s[2] == "notes" and "July storms" in s[3] for s in spans)


def test_pdf_pages_with_bounding_boxes(client):
    world = make_tenant()
    r = upload(client, world, "report.pdf", sample_files.pdf_bytes())
    assert r.status_code == 201, r.text
    spans = spans_of(r.json()["id"])
    assert {s[4]["page"] for s in spans} == {1, 2}
    assert all(len(s[4]["bbox"]) == 4 for s in spans)
    assert any(s[2] == "heading" and s[3] == "Engagement summary" for s in spans)
    assert any("£12.8m" in s[3] for s in spans)


def test_legacy_ppt_is_converted_via_libreoffice(client, monkeypatch):
    from services import ingestion

    monkeypatch.setattr(ingestion, "convert_ppt_to_pptx", lambda data: sample_files.pptx_bytes())
    world = make_tenant()
    fake_ppt = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512
    r = upload(client, world, "old.ppt", fake_ppt)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "in_review"
    assert any(s[0] == "slide" for s in spans_of(r.json()["id"]))


def test_ppt_without_converter_is_deferred_not_lost(client, sink, monkeypatch):
    monkeypatch.setenv("POR_SOFFICE_PATH", "definitely-not-installed-soffice")
    from services.config import get_settings

    get_settings.cache_clear()
    world = make_tenant()
    r = upload(client, world, "old.ppt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512)
    assert r.status_code == 201
    assert r.json()["status"] == "deferred"
    deferred = [e for e in sink.events() if e["event"] == "submission.deferred"]
    assert deferred and deferred[0]["data"]["reason"] == "processing_failed"


@pytest.mark.parametrize("name,data,status", [
    ("malware.exe", b"MZ\x90\x00", 415),
    ("fake.docx", b"this is not a zip", 415),
    ("fake.pdf", b"hello", 415),
    ("empty.txt", b"", 422),
    ("eicar.txt", b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*", 422),
])
def test_rejected_uploads(client, name, data, status):
    world = make_tenant()
    r = upload(client, world, name, data)
    assert r.status_code == status, r.text


def test_size_limit(client, monkeypatch):
    monkeypatch.setenv("POR_MAX_UPLOAD_MB", "1")
    from services.config import get_settings

    get_settings.cache_clear()
    world = make_tenant()
    r = upload(client, world, "big.txt", b"a " * (600 * 1024))
    assert r.status_code == 422 and "limit" in r.text


def test_idempotency_key_prevents_duplicates(client):
    world = make_tenant()
    h = {"X-API-Key": world["api_key"], "Idempotency-Key": "abc-123"}
    body = {"workflow_id": world["workflow_id"], "text": "Some content to review today."}
    first = client.post("/v1/submissions", json=body, headers=h)
    second = client.post("/v1/submissions", json=body, headers=h)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Idempotent-Replay"] == "true"


def test_validation_and_reference_documents(client):
    world = make_tenant()
    h = {"X-API-Key": world["api_key"]}
    both = client.post("/v1/submissions", json={"workflow_id": world["workflow_id"], "text": "x y z",
                                                "file": {"filename": "a.txt", "content_base64": "eHl6"}}, headers=h)
    assert both.status_code == 422
    assert client.post("/v1/submissions", json={"workflow_id": world["workflow_id"], "text": "a b c", "risk_tier": "extreme"},
                       headers=h).status_code == 422
    assert client.post("/v1/submissions", json={"workflow_id": "nope", "text": "a b c"}, headers=h).status_code == 404

    r = client.post(
        "/v1/submissions",
        files=[("file", ("letter.docx", sample_files.docx_bytes())), ("reference_files", ("client-file.txt", b"Income 58,400"))],
        data={"workflow_id": world["workflow_id"], "metadata": json.dumps({"title": "Letter"}), "reference_texts": "Loan 245,000"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    from db.models import Document
    from db.session import session_scope

    with session_scope() as db:
        roles = sorted(d.role for d in db.scalars(select(Document).where(Document.submission_id == r.json()["id"])).all())
    assert roles == ["primary", "reference", "reference"]
    assert r.json()["metadata"] == {"title": "Letter"}
