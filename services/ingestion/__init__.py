"""Ingestion service: validate, virus-scan and store originals, then extract the normalised document model."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Document, Section, Span, Submission
from services import audit
from services.config import get_settings
from services.ingestion import extractors
from services.ingestion.conversion import ConversionUnavailable, convert_ppt_to_pptx
from services.ingestion.extractors import ExtractionError, NormalizedDoc
from services.ingestion.scanning import MIME, ScanUnavailable, UnsupportedType, VirusFound, detect_format, scan
from services.storage import get_storage

__all__ = [
    "ConversionUnavailable",
    "ExtractionError",
    "IngestionError",
    "ScanUnavailable",
    "UnsupportedType",
    "VirusFound",
    "content_hash",
    "extract_document",
    "store_file",
    "store_text",
]


class IngestionError(ValueError):
    pass


RENDER_KIND = {"text": "text", "txt": "text", "docx": "doc", "pptx": "slides", "ppt": "slides", "pdf": "pages"}


def _store(session: Session, submission: Submission, filename: str, data: bytes, fmt: str, role: str) -> Document:
    sha = hashlib.sha256(data).hexdigest()
    key = f"{submission.tenant_id}/{submission.id}/{role}/{sha[:16]}-{_safe_name(filename)}"
    uri = get_storage().put(key, data, MIME[fmt])
    doc = Document(
        tenant_id=submission.tenant_id,
        submission_id=submission.id,
        role=role,
        filename=filename,
        original_file_uri=uri,
        mime_type=MIME[fmt],
        sha256=sha,
        size_bytes=len(data),
        render_kind=RENDER_KIND[fmt],
    )
    session.add(doc)
    session.flush()
    audit.record(
        session, submission.tenant_id, "system:ingestion", "document.stored", "document", doc.id,
        {"submission_id": submission.id, "role": role, "sha256": sha, "mime_type": doc.mime_type, "size": len(data)},
    )
    return doc


def _safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return keep[-120:] or "file"


def store_file(session: Session, submission: Submission, filename: str, data: bytes, role: str = "primary") -> Document:
    """Validate type and size, virus-scan, and store the original. Raises IngestionError subclasses."""
    limit = get_settings().max_upload_mb * 1024 * 1024
    if len(data) == 0:
        raise IngestionError("file is empty")
    if len(data) > limit:
        raise IngestionError(f"file exceeds {get_settings().max_upload_mb} MB limit")
    fmt = detect_format(filename, data)
    scan(data)
    return _store(session, submission, filename, data, fmt, role)


def store_text(session: Session, submission: Submission, text: str, role: str = "primary") -> Document:
    data = text.encode("utf-8")
    if not text.strip():
        raise IngestionError("text is empty")
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise IngestionError("text exceeds size limit")
    scan(data)
    doc = _store(session, submission, "content.txt", data, "text", role)
    doc.mime_type = "text/plain"
    return doc


def _format_of(doc: Document) -> str:
    for fmt, mime in MIME.items():
        if mime == doc.mime_type and fmt != "text":
            if fmt == "txt":
                return "txt"
            return fmt
    raise IngestionError(f"unknown stored type {doc.mime_type}")


def normalise(doc: Document, data: bytes) -> NormalizedDoc:
    fmt = _format_of(doc)
    if fmt == "txt":
        return extractors.extract_txt(data)
    if fmt == "docx":
        return extractors.extract_docx(data)
    if fmt == "pptx":
        return extractors.extract_pptx(data)
    if fmt == "ppt":
        return extractors.extract_pptx(convert_ppt_to_pptx(data))
    if fmt == "pdf":
        return extractors.extract_pdf(data)
    raise IngestionError(f"no extractor for {fmt}")


def extract_document(session: Session, doc: Document) -> NormalizedDoc:
    """Extract the stored original into Section and Span rows with stable ids."""
    data = get_storage().get(doc.original_file_uri)
    if hashlib.sha256(data).hexdigest() != doc.sha256:
        raise IngestionError("stored original does not match its recorded hash")
    normalized = normalise(doc, data)
    for s_order, nsec in enumerate(normalized.sections):
        section = Section(
            tenant_id=doc.tenant_id, document_id=doc.id, order=s_order, kind=nsec.kind,
            title=nsec.title, location=nsec.location,
        )
        session.add(section)
        session.flush()
        for sp_order, nspan in enumerate(nsec.spans):
            session.add(
                Span(
                    tenant_id=doc.tenant_id, document_id=doc.id, section_id=section.id,
                    order=float(sp_order), kind=nspan.kind, text=nspan.text, location=nspan.location,
                )
            )
    session.flush()
    audit.record(
        session, doc.tenant_id, "system:ingestion", "document.extracted", "document", doc.id,
        {"sections": len(normalized.sections), "words": normalized.word_count(), "render_kind": normalized.render_kind},
    )
    return normalized


def original_spans(session: Session, document_id: str) -> list[tuple[Section, Span]]:
    rows = session.execute(
        select(Section, Span)
        .join(Span, Span.section_id == Section.id)
        .where(Span.document_id == document_id, Span.review_only.is_(False))
        .order_by(Section.order, Span.order)
    ).all()
    return [(r[0], r[1]) for r in rows]


def content_hash(session: Session, document_id: str) -> str:
    """Hash of the original normalised content. Canary spans (review_only) are excluded by design."""
    h = hashlib.sha256()
    for section, span in original_spans(session, document_id):
        h.update(f"{section.order}:{span.order}:{span.kind}:".encode())
        h.update(span.text.encode())
        h.update(b"\x00")
    return h.hexdigest()
