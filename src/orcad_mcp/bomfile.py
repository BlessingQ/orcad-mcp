"""Bill of materials files in the layout of Capture's Tools > Bill of Materials report.

The .BOM text matches Capture's own report (tab separated, ANSI, CRLF): a header from the title block of the
first page, then Item / Quantity / Reference / Part, one line per Value. The .xlsx holds the same header and
table as a worksheet.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

RULE = "_" * 46
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def header_fields(title_block: dict[str, str]) -> dict[str, str]:
    """Title, revised date, document and revision as Capture prints them (missing values are empty)."""
    tb = {k.lower(): v for k, v in title_block.items()}
    return {
        "title": tb.get("title", ""),
        "revised": tb.get("page modify date", "") or tb.get("design modify date", ""),
        "doc": tb.get("doc", ""),
        "revision": tb.get("revcode", ""),
    }


def stamp(now: dt.datetime) -> tuple[str, str]:
    """Capture's report date and time: 'October 19,2023' and '15:57:25'."""
    return f"{MONTHS[now.month - 1]} {now.day},{now.year}", now.strftime("%H:%M:%S")


def capture_key(ref: str) -> list[tuple[int, Any]]:
    """Sort key of Capture's BOM: digit runs compare as numbers, and at the same position a letter sorts
    before a digit (LS1 before L1, C2 before C10)."""
    out: list[tuple[int, Any]] = []
    for run in re.findall(r"\d+|\D", ref.upper()):
        out.append((1, int(run)) if run.isdigit() else (0, run))
    return out


def capture_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows with their references and the rows themselves in Capture's BOM order."""
    out = []
    for row in rows:
        refs = sorted((r.strip() for r in row["refdes"].split(",")), key=capture_key)
        out.append({**row, "refdes": ", ".join(refs), "_first": refs[0]})
    out.sort(key=lambda r: capture_key(r["_first"]))
    for r in out:
        del r["_first"]
    return out


def references(row: dict[str, Any]) -> str:
    return ",".join(r.strip() for r in row["refdes"].split(","))


def bom_text(rows: list[dict[str, Any]], keys: Sequence[str], title_block: dict[str, str], now: dt.datetime) -> str:
    """Capture .BOM report text (lines joined with CRLF). keys[0] is printed as "Part"."""
    h = header_fields(title_block)
    date, time = stamp(now)
    extra = list(keys[1:])
    lines = [
        f"{h['title']}  Revised: {h['revised']}",
        f"{h['doc']}          Revision: {h['revision']}",
        *[""] * 7,
        f"Bill Of Materials        {date}      {time}\tPage1",
        "",
        "\t".join(["Item", "Quantity", "Reference", "Part", *extra]),
        RULE,
        "",
    ]
    for i, row in enumerate(rows, 1):
        values = [row.get(k) or "" for k in keys]
        lines.append("\t".join([str(i), str(row["quantity"]), references(row), *values]))
    return "\r\n".join(lines) + "\r\n"


def write_xlsx(
    path: Path, rows: list[dict[str, Any]], keys: Sequence[str], title_block: dict[str, str], now: dt.datetime
) -> None:
    """The same report as an Excel sheet (needs openpyxl)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    h = header_fields(title_block)
    date, time = stamp(now)
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    base = Font(name="Arial", size=10)
    bold = Font(name="Arial", size=10, bold=True)
    thin = Side(style="thin", color="808080")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws["A1"], ws["C1"] = h["title"], f"Revised: {h['revised']}"
    ws["A2"], ws["C2"] = h["doc"], f"Revision: {h['revision']}"
    ws["A4"], ws["C4"] = "Bill Of Materials", f"{date}  {time}"
    for cell in ("A1", "C1", "A2", "C2", "C4"):
        ws[cell].font = base
    ws["A1"].font = Font(name="Arial", size=12, bold=True)
    ws["A4"].font = bold

    headers = ["Item", "Quantity", "Reference", "Part", *keys[1:]]
    top = 6
    for col, text in enumerate(headers, 1):
        c = ws.cell(row=top, column=col, value=text)
        c.font = bold
        c.fill = PatternFill("solid", fgColor="D9D9D9")
        c.border = box
        c.alignment = Alignment(horizontal="center", vertical="center")
    for i, row in enumerate(rows, 1):
        values = [i, row["quantity"], references(row), *(row.get(k) or "" for k in keys)]
        for col, value in enumerate(values, 1):
            c = ws.cell(row=top + i, column=col, value=value)
            c.font = base
            c.border = box
            c.alignment = Alignment(vertical="top", wrap_text=(col == 3), horizontal="center" if col <= 2 else "left")

    widths = [7, 10, 48, 32, *([24] * (len(keys) - 1))]
    for col, width in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=top, column=col).column_letter].width = width
    last = ws.cell(row=top + max(len(rows), 1), column=len(headers)).coordinate
    ws.auto_filter.ref = f"A{top}:{last}"
    ws.freeze_panes = f"A{top + 1}"
    ws.print_title_rows = f"{top}:{top}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.save(path)
