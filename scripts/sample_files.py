"""Generate realistic sample files in every supported format (used by tests and the seed script)."""

from __future__ import annotations

import io

LETTER = [
    ("heading", "Mortgage affordability assessment"),
    ("paragraph", "Dear Ms Patel, thank you for your application for a residential mortgage of £245,000 over 25 years."),
    ("paragraph", "Based on your declared annual income of £58,400, your loan-to-income ratio is 4.2, which is within our lending policy."),
    ("paragraph", "We recommend a two-year fixed rate at 4.39%, which would mean monthly repayments of approximately £1,347."),
    ("paragraph", "According to the Bank of England Financial Stability Report, rates may fall over the next 12 months."),
    ("paragraph", "Under FCA Consumer Duty rules, we must ensure this product offers fair value and meets your needs."),
    ("paragraph", "Please review the enclosed key facts illustration and contact your adviser with any questions."),
]

SLIDES = [
    ("Q3 claims review", ["Prepared for the Underwriting Committee", "Motor and home portfolios"], "Open with the headline figures."),
    ("Claims volume", ["Motor claims rose 18% year on year to 12,450", "Home claims fell 6% to 3,210",
                       "Average motor claim cost was £3,870"], "Volume growth was driven by the July storms."),
    ("Reserving", ["We recommend increasing the motor reserve by £4.2m", "The PRA expects reserves to reflect emerging trends"],
     "Reserve figures are subject to actuarial sign-off."),
    ("Next steps", ["Review pricing for young drivers", "Report back to the committee in December"], ""),
]

REPORT_PAGES = [
    ["Engagement summary",
     "This report sets out our findings on the group's transfer pricing arrangements for the year ended 31 March 2026.",
     "Intercompany service charges totalled £12.8m, an increase of 9% on the prior year.",
     "We understand the group may restructure its UK operations during 2027."],
    ["Findings",
     "The management fee mark-up of 5% is consistent with the OECD Transfer Pricing Guidelines (OECD, 2022).",
     "Royalty payments of £2.1m to the Irish subsidiary lack supporting benchmarking analysis.",
     "We recommend the group commissions a benchmarking study before filing its 2026 return to HMRC."],
]


def txt_bytes() -> bytes:
    return "\n\n".join(t for _, t in LETTER).encode("utf-8")


def plain_text() -> str:
    return "\n\n".join(t for _, t in LETTER)


def docx_bytes() -> bytes:
    import docx

    d = docx.Document()
    for kind, text in LETTER:
        if kind == "heading":
            d.add_heading(text, level=1)
        else:
            d.add_paragraph(text)
    d.add_heading("Summary of terms", level=2)
    table = d.add_table(rows=3, cols=2)
    for i, (k, v) in enumerate([("Loan amount", "£245,000"), ("Term", "25 years"), ("Initial rate", "4.39%")]):
        table.cell(i, 0).text = k
        table.cell(i, 1).text = v
    d.add_paragraph("Check eligibility criteria", style="List Bullet")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def pptx_bytes() -> bytes:
    from pptx import Presentation

    prs = Presentation()
    for title, bullets, notes in SLIDES:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = bullets[0]
        for b in bullets[1:]:
            body.add_paragraph().text = b
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def pdf_bytes() -> bytes:
    import pymupdf

    pdf = pymupdf.open()
    for page_lines in REPORT_PAGES:
        page = pdf.new_page()
        y = 72
        page.insert_text((72, y), page_lines[0], fontsize=18)
        y += 40
        for line in page_lines[1:]:
            rect = pymupdf.Rect(72, y, 523, y + 60)
            page.insert_textbox(rect, line, fontsize=11)
            y += 70
    return pdf.tobytes()


ALL = {
    "letter.txt": txt_bytes,
    "mortgage-letter.docx": docx_bytes,
    "claims-review.pptx": pptx_bytes,
    "transfer-pricing-report.pdf": pdf_bytes,
}
