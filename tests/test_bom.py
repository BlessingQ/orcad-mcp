"""BOM files in the layout of Capture's Bill of Materials report (no Capture needed)."""

from __future__ import annotations

import datetime as dt

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from openpyxl import load_workbook

from orcad_mcp import bomfile, server

TITLE = {
    "Title": "GW-8000",
    "Doc": "COMMUNICATION",
    "RevCode": "1.3",
    "Page Modify Date": "Monday, September 04, 2023",
    "Page Number": "1",
}
NOW = dt.datetime(2023, 10, 19, 15, 57, 25)
ROWS = [
    {"quantity": 1, "refdes": "BT1", "Value": "CR2032_HOLDER&BAT"},
    {"quantity": 3, "refdes": "C2, C6, C17", "Value": "CC1608/1u/K"},
]

# first lines of the .BOM that Capture 16.5 wrote for this header (copied from a real report)
CAPTURE_HEAD = [
    "GW-8000  Revised: Monday, September 04, 2023",
    "COMMUNICATION          Revision: 1.3",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "Bill Of Materials        October 19,2023      15:57:25\tPage1",
    "",
    "Item\tQuantity\tReference\tPart",
    "______________________________________________",
    "",
    "1\t1\tBT1\tCR2032_HOLDER&BAT",
    "2\t3\tC2,C6,C17\tCC1608/1u/K",
]


def test_bom_text_matches_capture_report():
    text = bomfile.bom_text(ROWS, ["Value"], TITLE, NOW)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert text.split("\r\n")[:-1] == CAPTURE_HEAD


def test_bom_text_extra_columns():
    rows = [{"quantity": 2, "refdes": "R1, R2", "Value": "10K", "PCB Footprint": "R1608"}]
    lines = bomfile.bom_text(rows, ["Value", "PCB Footprint"], {}, NOW).split("\r\n")
    assert lines[11] == "Item\tQuantity\tReference\tPart\tPCB Footprint"
    assert lines[14] == "1\t2\tR1,R2\t10K\tR1608"
    assert lines[0] == "  Revised: " and lines[1] == "          Revision: "


def test_xlsx_holds_the_same_report(tmp_path):
    path = tmp_path / "b.xlsx"
    bomfile.write_xlsx(path, ROWS, ["Value"], TITLE, NOW)
    ws = load_workbook(path).active
    assert ws["A1"].value == "GW-8000" and ws["C1"].value == "Revised: Monday, September 04, 2023"
    assert ws["A2"].value == "COMMUNICATION" and ws["C2"].value == "Revision: 1.3"
    assert ws["A4"].value == "Bill Of Materials" and ws["C4"].value == "October 19,2023  15:57:25"
    assert [c.value for c in ws[6]] == ["Item", "Quantity", "Reference", "Part"]
    assert [c.value for c in ws[8]] == [2, 3, "C2,C6,C17", "CC1608/1u/K"]
    assert ws.freeze_panes == "A7" and ws.auto_filter.ref == "A6:D8"


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def call(command, *args, timeout=None):
        calls.append(command)
        if command == "parts":
            cols = ["refdes", "reference", "value", "footprint", "page", "schematic", "part", *args[1]]
            rows = [
                ["C2", "C2", "1u", "C1608", "P", "MAIN", "C.Normal", "1u", "C1608"],
                ["C1", "C1", "1u", "C1608", "P", "MAIN", "C.Normal", "1u", "C1608"],
                ["U1A", "U1", "LM358", "SO8", "P", "MAIN", "LM.Normal", "LM358", "SO8"],
                ["U1B", "U1", "LM358", "SO8", "P", "MAIN", "LM.Normal", "LM358", "SO8"],
            ]
            return {"design": r"D:\X\B.DSN", "columns": cols, "rows": [r[: len(cols)] for r in rows]}
        if command == "title_block":
            return {"design": r"D:\X\B.DSN", "page": "P", "properties": TITLE}
        raise AssertionError(command)

    monkeypatch.setattr(server.bridge, "call", call)
    return calls


def test_export_bom_writes_both(fake, tmp_path):
    r = server.export_bom(path=str(tmp_path / "out.xlsx"))
    assert set(r["files"]) == {"bom", "xlsx"} and r["lines"] == 2 and r["parts"] == 3
    text = (tmp_path / "out.BOM").read_bytes().decode("ascii")
    assert "1\t2\tC1,C2\t1u\r\n2\t1\tU1\tLM358\r\n" in text
    assert load_workbook(tmp_path / "out.xlsx").active["C7"].value == "C1,C2"
    with pytest.raises(ToolError, match="overwrite"):
        server.export_bom(path=str(tmp_path / "out"))
    r = server.export_bom(path=str(tmp_path / "out"), formats=["bom"], properties=["PCB Footprint"], overwrite=True)
    assert list(r["files"]) == ["bom"]
    assert "Part\tPCB Footprint" in (tmp_path / "out.BOM").read_text(encoding="ascii")


def test_export_bom_needs_absolute_path(fake):
    with pytest.raises(ToolError, match="absolute"):
        server.export_bom(path="relative/out")


def test_capture_order():
    rows = [
        {"quantity": 1, "refdes": "L1", "Value": "a"},
        {"quantity": 1, "refdes": "LS1", "Value": "b"},
        {"quantity": 2, "refdes": "C10, C2", "Value": "c"},
        {"quantity": 1, "refdes": "C9", "Value": "d"},
    ]
    out = bomfile.capture_order(rows)
    assert [r["refdes"] for r in out] == ["C2, C10", "C9", "LS1", "L1"]
