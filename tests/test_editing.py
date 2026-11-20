"""Page editing, run_tcl safety and library building logic (no Capture needed)."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from orcad_mcp import libedit, server
from orcad_mcp.bridge import TclError


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def call(command, *args, timeout=None):
        seen.append((command, args))
        if command == "page_objects":
            return {
                "parts": [
                    {"refdes": "R1", "bbox": [0, 0, 50, 50]},
                    {"refdes": "R2", "bbox": [500, 500, 550, 550]},
                ],
                "symbols": [{"name": "GND", "bbox": [40, 40, 60, 60]}, {"name": "VCC", "bbox": [900, 0, 920, 20]}],
                "wires": [
                    {"net": "A", "start": [100, 10], "end": [10, 10]},
                    {"net": "B", "start": [300, 300], "end": [300, 400]},
                ],
            }
        return {"ok": True}

    monkeypatch.setattr(server.bridge, "call", call)
    return seen


@pytest.mark.parametrize(
    "script, found",
    [
        ("GetWindows", ["GetWindows"]),
        ("set w [GetActiveWindow]", ["GetActiveWindow"]),
        ("if {1} {GetActivePage}", ["GetActivePage"]),
        ("puts 1; GetActiveOpjName; ::GetWindows x", ["GetActiveOpjName", "GetWindows"]),
        ("set GetWindows 1; puts $GetWindows", []),
        ("PlaceWire 1 2 3 4\nGetActivePMDesign", []),
        ("# GetWindows in a comment line\nputs ok", []),
    ],
)
def test_unsafe_commands(script, found):
    assert server.unsafe_commands(script) == found


def test_run_tcl_refuses_unsafe(monkeypatch):
    def never(*a, **k):
        raise AssertionError("must not reach Capture")

    monkeypatch.setattr(server.settings, "allow_tcl", True)
    monkeypatch.setattr(server.bridge, "eval", never)
    with pytest.raises(ToolError, match="GetActivePage can crash"):
        server.run_tcl("set p [GetActivePage]")
    monkeypatch.setattr(server.bridge, "eval", lambda script, timeout=None: "x")
    assert server.run_tcl("set p [GetActivePage]", allow_unsafe=True) == {"result": "x"}


def test_page_objects_area_filter(calls):
    r = server.page_objects("RS485", area=[0, 0, 120, 120])
    assert [p["refdes"] for p in r["parts"]] == ["R1"]
    assert [s["name"] for s in r["symbols"]] == ["GND"]
    assert [w["net"] for w in r["wires"]] == ["A"]
    assert calls[-1] == ("page_objects", ("", "RS485"))
    with pytest.raises(ToolError, match="area"):
        server.page_objects("RS485", area=[0, 0, 1])


def test_place_part_arguments(calls):
    server.place_part(" MCU ", r"D:\LIB.OLB", "R", 100, 200, refdes="R9", design="V14")
    assert calls[-1] == ("place_part", ("V14", "MCU", r"D:\LIB.OLB", "R", 100, 200, "R9", "", ""))


def test_place_symbol_defaults(calls):
    server.place_symbol("USB", "offpage", "USB_DP", 10, 20)
    assert calls[-1] == ("place_symbol", ("", "USB", "offpage", "USB_DP", 10, 20, "OFFPAGELEFT-L", "", []))
    server.place_symbol("USB", "ground", "GND_SIGNAL", 1, 2, symbol="GND_SIGNAL", offset=[10, 0])
    assert calls[-1][1][6:] == ("GND_SIGNAL", "", [10, 0])
    with pytest.raises(ToolError, match="offset"):
        server.place_symbol("USB", "power", "5V", 1, 2, offset=[1])


def test_place_wire_and_marks(calls):
    server.place_wire("USB", [[1, 2], [1, 20]])
    assert calls[-1] == ("place_wire", ("", "USB", [[1, 2], [1, 20]]))
    server.place_marks("USB", "no_connect", [[5, 5]])
    assert calls[-1] == ("place_marks", ("", "USB", "no_connect", [[5, 5]]))


def test_delete_area_defaults_to_dry_run(calls):
    server.delete_area("USB", 0, 0, 10, 10)
    assert calls[-1] == ("delete_area", ("", "USB", 0, 0, 10, 10, False))


def test_set_no_connect(calls):
    server.set_no_connect("U2", [13, "PC1"], False)
    assert calls[-1] == ("set_no_connect", ("", "U2", ["13", "PC1"], False))
    with pytest.raises(ToolError, match="no pins"):
        server.set_no_connect("U2", [], True)


def test_save_falls_back_to_database(tmp_path, monkeypatch):
    dsn = tmp_path / "B.DSN"
    dsn.write_bytes(b"x")
    monkeypatch.setattr(server.settings, "home", tmp_path / "home")
    seen = []

    def call(command, *args, timeout=None):
        seen.append(command)
        if command == "resolve":
            return {"path": str(dsn), "modified": False}
        if command == "save_gui":
            raise TclError("no page of the design is shown")
        return {"path": args[0], "saved": True}

    monkeypatch.setattr(server.bridge, "call", call)
    r = server.save_design()
    assert r["via"] == "database" and seen == ["resolve", "save_gui", "save"]


# --- library builder -------------------------------------------------------------------------------

PART = {
    "name": "CONN3",
    "footprint": "FP3",
    "body": [0, 10, 100, 60],
    "pins": [
        {"number": "1", "name": "A", "side": "L", "pos": 20},
        {"number": "2", "name": "B", "side": "R", "pos": 30},
        {"number": "SH", "name": "S", "side": "B", "pos": 50},
    ],
}


def test_validate_accepts_and_rejects():
    libedit.validate([PART])
    bad_pin = {**PART, "pins": [*PART["pins"], {"number": "1", "name": "X", "side": "L", "pos": 40}]}
    with pytest.raises(ValueError, match="repeated"):
        libedit.validate([bad_pin])
    outside = {**PART, "pins": [{"number": "1", "name": "A", "side": "L", "pos": 90}]}
    with pytest.raises(ValueError, match="outside"):
        libedit.validate([outside])
    with pytest.raises(ValueError, match="invalid part name"):
        libedit.validate([{**PART, "name": "A B"}])


def test_spec_text_is_a_tcl_list(tcl):
    text = libedit.spec_text([PART])
    tcl.setvar("line", text.strip())
    assert tcl.eval("llength $line") == "7"
    assert tcl.eval("lindex $line 2") == "CONN3"  # value defaults to the name
    assert tcl.eval("lindex [lindex $line 5] 2") == "SH S B 50"


def test_check_reports_differences():
    listing = {
        "parts": [
            {
                "name": "CONN3",
                "exists": True,
                "footprint": "FP3",
                "numbers": ["1", "2", "SH"],
                "pins": [
                    {"name": "A", "hot": [-30, 20]},
                    {"name": "B", "hot": [130, 30]},
                    {"name": "S", "hot": [50, 90]},
                ],
            }
        ]
    }
    assert libedit.check([PART], listing) == []
    listing["parts"][0]["numbers"] = ["1", "2", "3"]
    listing["parts"][0]["pins"][2]["hot"] = [50, 80]
    problems = libedit.check([PART], listing)
    assert any("pin numbers" in p for p in problems) and any("pin SH" in p for p in problems)
