"""Render a report as one self-contained HTML file (REP-03).

Images are embedded as data URIs and the style sheet is inline, so the file
can be e-mailed or archived on its own.
"""

from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path

from psfwidget.report.document import (
    Figure,
    Heading,
    KeyValues,
    PageBreak,
    Paragraph,
    Report,
    Status,
    StatusLine,
    Table,
)

# Status colours (dataviz reference palette); text on warning is dark for contrast.
STATUS_STYLE = {
    Status.PASS: ("#0ca30c", "#ffffff"),
    Status.WARNING: ("#fab219", "#0b0b0b"),
    Status.FAIL: ("#d03b3b", "#ffffff"),
    Status.NOT_EVALUATED: ("#898781", "#ffffff"),
}

CSS = """
:root { --ink: #0b0b0b; --ink2: #52514e; --muted: #898781; --rule: #e1e0d9;
        --surface: #fcfcfb; --zebra: #f4f4f1; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink);
       font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 24px 16px 48px; }
header.top { display: flex; align-items: center; gap: 16px; border-bottom: 1px solid var(--rule);
             padding-bottom: 12px; margin-bottom: 20px; }
header.top img { max-height: 48px; }
header.top .facility { color: var(--ink2); font-size: 13px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 8px; border-bottom: 1px solid var(--rule); padding-bottom: 4px; }
h3 { font-size: 15px; margin: 20px 0 6px; }
.subtitle { color: var(--ink2); margin: 0 0 12px; }
p { margin: 6px 0; } p.muted { color: var(--ink2); font-size: 13px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 4px; font-weight: 600;
         font-size: 13px; white-space: nowrap; }
.statusline { display: flex; gap: 10px; align-items: baseline; margin: 8px 0; }
.big .badge { font-size: 18px; padding: 4px 14px; }
table { border-collapse: collapse; margin: 8px 0 12px; width: 100%; font-size: 13px; }
caption { text-align: left; color: var(--ink2); font-size: 12px; padding-bottom: 4px; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { color: var(--ink2); font-weight: 600; }
tbody tr:nth-child(even) { background: var(--zebra); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.scroll { overflow-x: auto; }
table.kv { width: auto; } table.kv th { white-space: nowrap; padding-right: 16px; }
figure { margin: 12px 0; } figure img { max-width: 100%; height: auto; display: block; }
figcaption { color: var(--ink2); font-size: 12px; margin-top: 4px; }
footer { margin-top: 32px; border-top: 1px solid var(--rule); padding-top: 8px; color: var(--muted);
         font-size: 12px; }
@media print { main { max-width: none; padding: 0; } h2 { break-after: avoid; }
               figure, table { break-inside: avoid; } }
"""


def _text(value: str) -> str:
    return html.escape(value, quote=True)


def badge(status: Status) -> str:
    """Return the HTML for a status badge: symbol, word, and colour."""
    background, foreground = STATUS_STYLE[status]
    return (
        f'<span class="badge" style="background:{background};color:{foreground}">'
        f"{status.symbol} {_text(status.value)}</span>"
    )


def _image(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _logo(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f'<img src="{_image(path.read_bytes(), mime)}" alt="logo">'


def _table(block: Table) -> str:
    head = "".join(f"<th>{_text(h)}</th>" for h in block.header)
    rows = []
    for row in block.rows:
        cells = []
        for index, value in enumerate(row):
            if index == block.status_column and value in Status._value2member_map_:
                cells.append(f"<td>{badge(Status(value))}</td>")
            else:
                css = ' class="num"' if index in block.numeric else ""
                cells.append(f"<td{css}>{_text(value)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    caption = f"<caption>{_text(block.caption)}</caption>" if block.caption else ""
    return (
        f'<div class="scroll"><table>{caption}<thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_html(report: Report) -> str:
    """Return the report as a complete HTML document."""
    parts: list[str] = []
    for block in report.blocks:
        if isinstance(block, Heading):
            level = min(max(block.level, 2), 3)
            parts.append(f"<h{level}>{_text(block.text)}</h{level}>")
        elif isinstance(block, Paragraph):
            css = ' class="muted"' if block.muted else ""
            parts.append(f"<p{css}>{_text(block.text)}</p>")
        elif isinstance(block, KeyValues):
            rows = "".join(
                f"<tr><th>{_text(k)}</th><td>{_text(v)}</td></tr>" for k, v in block.rows
            )
            parts.append(f'<table class="kv"><tbody>{rows}</tbody></table>')
        elif isinstance(block, Table):
            parts.append(_table(block))
        elif isinstance(block, Figure):
            width = f"{max(10, min(100, round(block.width * 100)))}%"
            parts.append(
                f'<figure style="max-width:{width}"><img src="{_image(block.png)}" '
                f'alt="{_text(block.caption)}"><figcaption>{_text(block.caption)}'
                "</figcaption></figure>"
            )
        elif isinstance(block, StatusLine):
            parts.append(
                f'<div class="statusline">{badge(block.status)}'
                f"<span>{_text(block.text)}</span></div>"
            )
        elif isinstance(block, PageBreak):
            continue
    brand = report.branding
    logo = _logo(brand.logo) if brand.logo is not None else ""
    facility = f'<div class="facility">{_text(brand.facility)}</div>' if brand.facility else ""
    status = (
        f'<div class="statusline big">{badge(report.status)}</div>'
        if report.status is not None
        else ""
    )
    generated = report.generated_utc.strftime("%Y-%m-%d %H:%M UTC")
    footer = " · ".join(p for p in (brand.footer, f"Generated {generated} by PSFWidget") if p)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_text(report.title)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header class="top">{logo}<div><h1>{_text(report.title)}</h1>
<p class="subtitle">{_text(report.subtitle)}</p>{facility}</div></header>
{status}
{"".join(parts)}
<footer>{_text(footer)}</footer>
</main>
</body>
</html>
"""


def write_html(report: Report, path: Path) -> Path:
    """Write the report to ``path`` as HTML and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
    return path
