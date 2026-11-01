"""Format extractors. Every input becomes one NormalizedDoc: ordered sections of ordered spans."""

from __future__ import annotations

import io
import re
import statistics
from dataclasses import dataclass, field

from charset_normalizer import from_bytes


@dataclass
class NSpan:
    kind: str
    text: str
    location: dict = field(default_factory=dict)


@dataclass
class NSection:
    kind: str  # body | page | slide
    title: str | None = None
    location: dict = field(default_factory=dict)
    spans: list[NSpan] = field(default_factory=list)


@dataclass
class NormalizedDoc:
    render_kind: str  # text | doc | slides | pages
    sections: list[NSection] = field(default_factory=list)

    def word_count(self) -> int:
        return sum(len(sp.text.split()) for s in self.sections for sp in s.spans)


class ExtractionError(ValueError):
    pass


# --- Plain text -------------------------------------------------------------------------


def split_paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    parts = re.split(r"\n\s*\n", text)
    if len(parts) == 1:
        parts = text.split("\n")
    return [re.sub(r"[ \t]+", " ", p).strip() for p in parts if p.strip()]


def extract_text(text: str) -> NormalizedDoc:
    paras = split_paragraphs(text)
    if not paras:
        raise ExtractionError("no text content")
    section = NSection(kind="body")
    for i, p in enumerate(paras):
        section.spans.append(NSpan(kind="paragraph", text=p, location={"paragraph": i}))
    return NormalizedDoc(render_kind="text", sections=[section])


def decode_text_file(data: bytes) -> str:
    """UTF-8 first (with or without BOM), then detection. Short Western European files are often
    misdetected as other single-byte Latin encodings, so cp1252 wins when it decodes cleanly."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    best = from_bytes(data).best()
    if best is None:
        raise ExtractionError("could not detect text encoding")
    enc = (best.encoding or "").lower().replace("-", "_")
    if enc.startswith(("iso8859", "latin", "cp125", "windows_125", "mac_")):
        try:
            return data.decode("cp1252")
        except UnicodeDecodeError:
            pass
    return str(best)


def extract_txt(data: bytes) -> NormalizedDoc:
    return extract_text(decode_text_file(data))


# --- DOCX -------------------------------------------------------------------------------


def extract_docx(data: bytes) -> NormalizedDoc:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"invalid .docx: {exc}") from exc

    doc = NormalizedDoc(render_kind="doc")
    current = NSection(kind="body", title=None, location={"index": 0})
    block_index = 0

    def flush_section(title: str | None) -> NSection:
        nonlocal current
        if current.spans:
            doc.sections.append(current)
        current = NSection(kind="body", title=title, location={"index": len(doc.sections)})
        return current

    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, document)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name if para.style is not None else "") or ""
            is_list = "List" in style or child.find(".//{*}numPr") is not None
            if style.startswith("Heading") or style == "Title":
                level = 0 if style == "Title" else int(re.sub(r"\D", "", style) or 1)
                if level <= 2:
                    flush_section(text)
                current.spans.append(NSpan("heading", text, {"block": block_index, "level": level}))
            elif is_list:
                current.spans.append(NSpan("list_item", text, {"block": block_index}))
            else:
                current.spans.append(NSpan("paragraph", text, {"block": block_index}))
            block_index += 1
        elif tag == "tbl":
            table = Table(child, document)
            rows = []
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                rows.append(" | ".join(cells))
            text = "\n".join(r for r in rows if r.strip(" |"))
            if text:
                current.spans.append(NSpan("table", text, {"block": block_index, "rows": len(rows)}))
            block_index += 1
    if current.spans:
        doc.sections.append(current)

    # Reviewer comments already present in the source document are preserved as their own section.
    try:
        comments = list(document.comments)
    except Exception:  # noqa: BLE001
        comments = []
    if comments:
        section = NSection(kind="body", title="Document comments", location={"comments": True})
        for c in comments:
            text = (c.text or "").strip()
            if text:
                author = getattr(c, "author", "") or ""
                section.spans.append(NSpan("comment", text, {"author": author}))
        if section.spans:
            doc.sections.append(section)

    if not doc.sections:
        raise ExtractionError("no text content in .docx")
    return doc


# --- PPTX -------------------------------------------------------------------------------


def extract_pptx(data: bytes) -> NormalizedDoc:
    from pptx import Presentation

    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"invalid .pptx: {exc}") from exc

    doc = NormalizedDoc(render_kind="slides")
    for idx, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title if slide.shapes.title is not None else None
        title = title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else None
        section = NSection(kind="slide", title=title or f"Slide {idx}", location={"slide": idx})
        if title:
            section.spans.append(NSpan("title", title, {"slide": idx}))
        for shape in slide.shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if shape.has_text_frame:
                for p_i, para in enumerate(shape.text_frame.paragraphs):
                    text = "".join(r.text for r in para.runs).strip()
                    if text:
                        kind = "list_item" if para.level > 0 else "paragraph"
                        section.spans.append(
                            NSpan(kind, text, {"slide": idx, "shape": shape.shape_id, "paragraph": p_i})
                        )
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = [" | ".join(c.text.strip() for c in row.cells) for row in shape.table.rows]
                text = "\n".join(rows).strip()
                if text:
                    section.spans.append(NSpan("table", text, {"slide": idx, "shape": shape.shape_id}))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                section.spans.append(NSpan("notes", notes, {"slide": idx}))
        if section.spans:
            doc.sections.append(section)
    if not doc.sections:
        raise ExtractionError("no text content in .pptx")
    return doc


# --- PDF --------------------------------------------------------------------------------


def _overlaps(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def extract_pdf(data: bytes) -> NormalizedDoc:
    import pymupdf

    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"invalid .pdf: {exc}") from exc

    doc = NormalizedDoc(render_kind="pages")
    for page_no, page in enumerate(pdf, start=1):
        width, height = page.rect.width, page.rect.height
        section = NSection(kind="page", title=f"Page {page_no}", location={"page": page_no, "width": width, "height": height})
        table_boxes: list[tuple] = []
        tables: list[tuple[tuple, str]] = []
        try:
            for tbl in page.find_tables().tables:
                rows = tbl.extract()
                text = "\n".join(" | ".join((c or "").strip() for c in r) for r in rows).strip()
                if text:
                    bbox = tuple(tbl.bbox)
                    table_boxes.append(bbox)
                    tables.append((bbox, text))
        except Exception:  # noqa: BLE001
            pass

        blocks = []
        sizes = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            bbox = tuple(block["bbox"])
            if any(_overlaps(bbox, tb) for tb in table_boxes):
                continue
            lines = []
            max_size = 0.0
            for line in block["lines"]:
                line_text = "".join(s["text"] for s in line["spans"]).strip()
                if line_text:
                    lines.append(line_text)
                for s in line["spans"]:
                    max_size = max(max_size, s["size"])
                    sizes.append(s["size"])
            text = " ".join(lines).strip()
            if text:
                blocks.append((bbox, text, max_size))
        median = statistics.median(sizes) if sizes else 0
        items = [(b[0], "block", b[1], b[2]) for b in blocks] + [(t[0], "table", t[1], 0) for t in tables]
        items.sort(key=lambda it: (round(it[0][1]), it[0][0]))
        for bbox, kind, text, size in items:
            if kind == "table":
                span_kind = "table"
            elif median and size >= median * 1.25 and len(text) < 120:
                span_kind = "heading"
            else:
                span_kind = "paragraph"
            section.spans.append(NSpan(span_kind, text, {"page": page_no, "bbox": [round(v, 1) for v in bbox]}))
        if section.spans:
            doc.sections.append(section)
    if not doc.sections:
        raise ExtractionError("no text layer in .pdf (OCR for scanned PDFs is not in the MVP)")
    return doc
