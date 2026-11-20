from __future__ import annotations

from orcad_mcp.netlist import PADS_HEADER, bom_csv, bom_rows, matches, natural_key, pads_netlist, patterns, strip_view


def test_natural_key():
    assert sorted(["R10", "R2", "R1", "C3", "U1.10", "U1.9"], key=natural_key) == [
        "C3",
        "R1",
        "R2",
        "R10",
        "U1.9",
        "U1.10",
    ]


def test_patterns_and_matches():
    assert patterns("R*, c1? ,") == ["R*", "c1?"]
    assert patterns(None) == []
    assert matches("C12", patterns("R*,c1?"))
    assert not matches("C123", patterns("R*,c1?"))
    assert matches(None, [])
    assert not matches(None, ["R*"])


def test_strip_view():
    assert strip_view("TPS76333_0.Normal") == "TPS76333_0"
    assert strip_view("X.Convert") == "X"
    assert strip_view("R") == "R"


def test_pads_netlist_layout():
    parts = [
        {"refdes": "C10", "reference": "C10", "footprint": "C2012"},
        {"refdes": "C1", "reference": "C1", "footprint": "C2012"},
        {"refdes": "U1A", "reference": "U1", "footprint": "SO14"},
        {"refdes": "U1B", "reference": "U1", "footprint": "SO14"},
        {"refdes": "R5", "reference": "R5", "footprint": ""},
    ]
    nets = [
        {"name": "VCC", "pins": [["U1", "14", "VCC"], ["C1", "1", "1"], ["C10", "1", "1"]]},
        {"name": "GND", "pins": [["U1", "7", "GND"], ["C1", "2", "2"], ["C10", "2", "2"], ["R5", "2", "2"]]},
        {"name": "EMPTY", "pins": []},
        {
            "name": "BIG",
            "pins": [["R5", str(i), str(i)] for i in range(1, 11)],
        },
    ]
    text, warnings = pads_netlist(parts, nets)
    lines = text.split("\r\n")
    assert lines[0] == PADS_HEADER
    assert lines[1:6] == ["*PART*", "C1               C2012", "C10              C2012", "R5", "U1               SO14"]
    assert lines[6:8] == ["", "*NET*"]
    assert "*SIGNAL* EMPTY" not in lines
    i = lines.index("*SIGNAL* BIG")
    assert lines[i + 1] == "R5.1 R5.2 R5.3 R5.4 R5.5 R5.6 R5.7 R5.8"
    assert lines[i + 2] == "R5.9 R5.10"
    assert lines[lines.index("*SIGNAL* GND") + 1] == "C1.2 C10.2 R5.2 U1.7"
    assert text.endswith("*END*\r\n")
    assert any("R5" in w and "Footprint" in w for w in warnings)


def test_bom():
    parts = [
        {"refdes": "R10", "reference": "R10", "Value": "10K", "PCB Footprint": "R1608"},
        {"refdes": "R2", "reference": "R2", "Value": "10K", "PCB Footprint": "R1608"},
        {"refdes": "U1A", "reference": "U1", "Value": "LM358", "PCB Footprint": "SO8"},
        {"refdes": "U1B", "reference": "U1", "Value": "LM358", "PCB Footprint": "SO8"},
        {"refdes": "C1", "reference": "C1", "Value": None, "PCB Footprint": "C1608"},
    ]
    rows = bom_rows(parts, ["Value", "PCB Footprint"])
    assert rows == [
        {"quantity": 1, "Value": "", "PCB Footprint": "C1608", "refdes": "C1"},
        {"quantity": 2, "Value": "10K", "PCB Footprint": "R1608", "refdes": "R2, R10"},
        {"quantity": 1, "Value": "LM358", "PCB Footprint": "SO8", "refdes": "U1"},
    ]
    csv_text = bom_csv(rows, ["Value", "PCB Footprint"])
    assert csv_text.splitlines()[0] == "Item,Quantity,Reference,Value,PCB Footprint"
    assert csv_text.splitlines()[2] == '2,2,"R2, R10",10K,R1608'
