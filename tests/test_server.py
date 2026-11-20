"""Tool logic with a fake bridge (no Capture needed)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from orcad_mcp import server
from orcad_mcp.bridge import BridgeError

DESIGN = r"D:\PROJ\BOARD.DSN"
PARTS = {
    "design": DESIGN,
    "columns": ["refdes", "reference", "value", "footprint", "page", "schematic", "part", "Tolerance"],
    "rows": [
        ["R10", "R10", "10K", "R1608", "POWER", "MAIN", "R.Normal", "1%"],
        ["R2", "R2", "10K", "R1608", "POWER", "MAIN", "R.Normal", None],
        ["C1", "C1", "100n", "C1608", "MCU", "MAIN", "C.Normal", None],
        ["U1A", "U1", "LM358", "SO8", "MCU", "MAIN", "LM358.Normal", None],
        ["U1B", "U1", "LM358", "SO8", "MCU", "MAIN", "LM358.Normal", None],
    ],
}
NETS = {
    "design": DESIGN,
    "nets": [
        {"name": "VCC", "pins": [["U1", "8", "V+"], ["C1", "1", "1"], ["R2", "1", "1"]]},
        {"name": "GND", "pins": [["U1", "4", "V-"], ["C1", "2", "2"]]},
        {"name": "N001", "pins": [["R10", "2", "2"], ["R2", "2", "2"]]},
    ],
}


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def call(command, *args, timeout=None):
        calls.append((command, args))
        if command == "parts":
            props = args[1]
            table = {"design": DESIGN, "columns": PARTS["columns"][:7] + props, "rows": []}
            for row in PARTS["rows"]:
                extra = {"Tolerance": row[7], "Value": row[2], "PCB Footprint": row[3]}
                table["rows"].append(row[:7] + [extra.get(p) for p in props])
            return table
        if command == "nets":
            return NETS
        if command == "resolve":
            return {"path": DESIGN, "modified": True}
        if command == "save":
            return {"path": args[0], "saved": True}
        raise AssertionError(command)

    monkeypatch.setattr(server.bridge, "call", call)
    return calls


def test_list_parts_filters(fake):
    r = server.list_parts(refdes="r*", properties=["Tolerance"])
    assert r["matched"] == 2 and r["total_parts"] == 5
    assert r["columns"] == ["refdes", "value", "footprint", "page", "part", "Tolerance"]
    assert r["rows"] == [["R2", "10K", "R1608", "POWER", "R", None], ["R10", "10K", "R1608", "POWER", "R", "1%"]]
    assert fake[0] == ("parts", ("", ["Tolerance"]))


def test_list_parts_sections_and_limit(fake):
    r = server.list_parts(refdes="U1", limit=1)
    assert "reference" in r["columns"] and r["matched"] == 2 and len(r["rows"]) == 1 and "truncated" in r
    assert server.list_parts(value="100N, 10k", page="power")["matched"] == 2


def test_list_nets(fake):
    r = server.list_nets(refdes="U1")
    assert [n["name"] for n in r["nets"]] == ["GND", "VCC"]
    assert r["nets"][1]["pins"] == ["C1.1", "R2.1", "U1.8:V+"]
    assert server.list_nets(name="n*", pin_names=False)["nets"] == [{"name": "N001", "pins": ["R2.2", "R10.2"]}]


def test_bom_counts_packages_once(fake, tmp_path):
    out = tmp_path / "bom.csv"
    r = server.get_bom(csv_path=str(out))
    assert r["bom"][-1] == {"quantity": 1, "Value": "LM358", "PCB Footprint": "SO8", "refdes": "U1"}
    assert out.read_text(encoding="utf-8-sig").splitlines()[0] == "Item,Quantity,Reference,Value,PCB Footprint"
    with pytest.raises(ToolError, match="absolute"):
        server.get_bom(csv_path="bom.csv")


def test_export_netlist(fake, tmp_path):
    target = tmp_path / "board.asc"
    r = server.export_netlist(str(target))
    assert r["parts"] == 4 and r["nets"] == 3 and r["warnings"] == []
    text = target.read_bytes().decode("ascii")
    assert "U1               SO8\r\n" in text and "*SIGNAL* VCC\r\nC1.1 R2.1 U1.8\r\n" in text
    with pytest.raises(ToolError, match="overwrite"):
        server.export_netlist(str(target))
    server.export_netlist(str(target), overwrite=True)


def test_save_makes_backup(fake, tmp_path, monkeypatch):
    dsn = tmp_path / "BOARD.DSN"
    dsn.write_bytes(b"dsn")
    monkeypatch.setattr(server.settings, "home", tmp_path / "home")

    def resolve(command, *args, timeout=None):
        return {"path": str(dsn), "modified": True} if command == "resolve" else {"path": args[0], "saved": True}

    monkeypatch.setattr(server.bridge, "call", resolve)
    r = server.save_design()
    assert r["saved"] and r["had_unsaved_changes"]
    assert (tmp_path / "home" / "backups" / "BOARD").is_dir()
    assert Path(r["backup"]).read_bytes() == b"dsn"


def test_status_offline(monkeypatch):
    def offline(*a, **k):
        raise BridgeError("not answering")

    monkeypatch.setattr(server.bridge, "call", offline)
    r = server.orcad_status()
    assert r["connected"] is False and "not answering" in r["problem"] and r["how_to_start"]


def test_run_tcl_can_be_disabled(monkeypatch):
    monkeypatch.setattr(server.settings, "allow_tcl", False)
    with pytest.raises(ToolError, match="disabled"):
        server.run_tcl("expr 1")


def test_set_part_properties_payload(monkeypatch):
    seen = {}

    def call(command, *args, timeout=None):
        seen["args"] = (command, args)
        return {"changes": []}

    monkeypatch.setattr(server.bridge, "call", call)
    server.set_part_properties([server.PropertyChange(refdes=" R1 ", property="Value", value="4.7K")], design="x")
    assert seen["args"] == ("set_props", ("x", [["R1", "Value", "4.7K"]], True))
    with pytest.raises(ToolError, match="no changes"):
        server.set_part_properties([])
