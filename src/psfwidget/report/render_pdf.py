"""Render a report as PDF with reportlab (REP-03).

The built-in PDF fonts cover the Windows-1252 character set only, so symbols
outside it (minus sign, ≤, Greek letters, check marks) are replaced with
plain-text equivalents.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

from psfwidget.report import document as doc
from psfwidget.report.render_html import STATUS_STYLE

PAGE_SIZES = {"letter": LETTER, "a4": A4}
INK = colors.HexColor("#0b0b0b")
INK2 = colors.HexColor("#52514e")
RULE = colors.HexColor("#e1e0d9")
ZEBRA = colors.HexColor("#f4f4f1")

_REPLACEMENTS = {
    "−": "-",
    "≤": "<=",
    "≥": ">=",
    "λ": "lambda",
    "√": "sqrt",
    "σ": "sigma",
    "ρ": "rho",
    "✓": "",
    "✕": "",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₁": "1",
    "ₛ": "s",
    "ₓ": "x",
    "→": "->",
    "…": "...",
    "≈": "~",
}


def pdf_text(text: str) -> str:
    """Return ``text`` with characters the PDF base fonts cannot show replaced."""
    out = []
    for char in text:
        char = _REPLACEMENTS.get(char, char)
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            char = "?"
        out.append(char)
    return "".join(out).strip() if text.strip() else ""


def _escape(text: str) -> str:
    return pdf_text(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "body",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=INK,
    )
    return {
        "title": ParagraphStyle(
            "title", parent=body, fontName="Helvetica-Bold", fontSize=16, leading=20
        ),
        "subtitle": ParagraphStyle("subtitle", parent=body, textColor=INK2, fontSize=10),
        "h2": ParagraphStyle(
            "h2",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            spaceBefore=6,
            spaceAfter=2,
        ),
        "body": body,
        "muted": ParagraphStyle("muted", parent=body, textColor=INK2, fontSize=8, leading=10),
        "cell": ParagraphStyle("cell", parent=body, fontSize=7.5, leading=9),
        "cellnum": ParagraphStyle(
            "cellnum", parent=body, fontSize=7.5, leading=9, alignment=TA_RIGHT
        ),
        "head": ParagraphStyle(
            "head", parent=body, fontSize=7.5, leading=9, fontName="Helvetica-Bold", textColor=INK2
        ),
        "caption": ParagraphStyle("caption", parent=body, textColor=INK2, fontSize=8, leading=10),
    }


def _badge(status: doc.Status, style: ParagraphStyle, size: float = 9) -> Table:
    background, foreground = STATUS_STYLE[status]
    label = Paragraph(
        f'<font color="{foreground}"><b>{_escape(status.value)}</b></font>',
        ParagraphStyle("badge", parent=style, fontSize=size, leading=size + 2),
    )
    text_width = stringWidth(pdf_text(status.value), "Helvetica-Bold", size)
    table = Table([[label]], hAlign="LEFT", colWidths=[text_width + 12])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(background)),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _table(block: doc.Table, styles: dict[str, ParagraphStyle], width: float) -> list[Flowable]:
    header = [Paragraph(_escape(h), styles["head"]) for h in block.header]
    rows: list[list[Any]] = [header]
    for row in block.rows:
        cells: list[Any] = []
        for index, value in enumerate(row):
            if index == block.status_column and value in doc.Status._value2member_map_:
                cells.append(_badge(doc.Status(value), styles["cell"], 7.5))
            else:
                style = styles["cellnum"] if index in block.numeric else styles["cell"]
                cells.append(Paragraph(_escape(value), style))
        rows.append(cells)
    table = Table(rows, repeatRows=1, hAlign="LEFT", colWidths=None)
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    natural_width, _ = table.wrap(width, 10_000)
    if natural_width > width:  # spread columns evenly when the natural layout is too wide
        count = len(block.header)
        table = Table(rows, repeatRows=1, hAlign="LEFT", colWidths=[width / count] * count)
        table.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
    parts: list[Flowable] = []
    if block.caption:
        parts.append(Paragraph(_escape(block.caption), styles["caption"]))
    parts.append(table)
    parts.append(Spacer(1, 6))
    # Short tables stay on one page; long ones may split, repeating the header row.
    return [KeepTogether(parts)] if len(block.rows) <= 15 else parts


def _figure(
    block: doc.Figure, styles: dict[str, ParagraphStyle], width: float, height: float
) -> Flowable:
    reader = ImageReader(io.BytesIO(block.png))
    pixels_w, pixels_h = reader.getSize()
    target = width * block.width
    scale = min(target / pixels_w, (height * 0.75) / pixels_h)
    image = Image(
        io.BytesIO(block.png), width=pixels_w * scale, height=pixels_h * scale, hAlign="LEFT"
    )
    return KeepTogether(
        [image, Paragraph(_escape(block.caption), styles["caption"]), Spacer(1, 6)]
    )


def _page_decoration(report: doc.Report, page_size: tuple[float, float]) -> Callable[..., None]:
    generated = report.generated_utc.strftime("%Y-%m-%d %H:%M UTC")
    footer = " - ".join(
        p for p in (report.branding.footer, f"Generated {generated} by PSFWidget") if p
    )

    def decorate(canvas: Canvas, document: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(INK2)
        width = page_size[0]
        canvas.drawString(18 * mm, 10 * mm, pdf_text(footer)[:150])
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {document.page}")
        if report.branding.facility:
            canvas.drawString(18 * mm, page_size[1] - 10 * mm, pdf_text(report.branding.facility))
        canvas.drawRightString(width - 18 * mm, page_size[1] - 10 * mm, pdf_text(report.title))
        canvas.restoreState()

    return decorate


def render_pdf(report: doc.Report, path: Path, *, page_size: str = "letter") -> Path:
    """Write the report to ``path`` as PDF and return the path.

    Raises
    ------
    ValueError
        If the page size is not ``letter`` or ``a4``.
    """
    if page_size not in PAGE_SIZES:
        raise ValueError(f"page size must be one of {sorted(PAGE_SIZES)}, got {page_size!r}")
    size = PAGE_SIZES[page_size]
    styles = _styles()
    path.parent.mkdir(parents=True, exist_ok=True)
    template = SimpleDocTemplate(
        str(path),
        pagesize=size,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=pdf_text(report.title),
        author="PSFWidget",
    )
    width, height = template.width, template.height
    story: list[Flowable] = []
    brand = report.branding
    if brand.logo is not None:
        reader = ImageReader(str(brand.logo))
        logo_w, logo_h = reader.getSize()
        scale = (14 * mm) / logo_h
        story.append(Image(str(brand.logo), width=logo_w * scale, height=14 * mm, hAlign="LEFT"))
    story.append(Paragraph(_escape(report.title), styles["title"]))
    story.append(Paragraph(_escape(report.subtitle), styles["subtitle"]))
    if report.status is not None:
        story += [Spacer(1, 4), _badge(report.status, styles["body"], 12)]
    story.append(Spacer(1, 8))
    for block in report.blocks:
        if isinstance(block, doc.Heading):
            story.append(
                Paragraph(_escape(block.text), styles["h2" if block.level <= 2 else "h3"])
            )
        elif isinstance(block, doc.Paragraph):
            story.append(
                Paragraph(_escape(block.text), styles["muted" if block.muted else "body"])
            )
        elif isinstance(block, doc.KeyValues):
            story.extend(_table(doc.Table(("Item", "Value"), block.rows), styles, width))
        elif isinstance(block, doc.Table):
            story.extend(_table(block, styles, width))
        elif isinstance(block, doc.Figure):
            story.append(_figure(block, styles, width, height))
        elif isinstance(block, doc.StatusLine):
            line = Table(
                [
                    [
                        _badge(block.status, styles["body"]),
                        Paragraph(_escape(block.text), styles["body"]),
                    ]
                ],
                hAlign="LEFT",
                colWidths=[32 * mm, width - 32 * mm],
            )
            line.setStyle(
                TableStyle(
                    [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]
                )
            )
            story.append(line)
        elif isinstance(block, doc.PageBreak):
            story.append(PageBreak())
    decorate = _page_decoration(report, size)
    template.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path
