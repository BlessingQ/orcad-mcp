from __future__ import annotations

import re
import socket

import pytest

from orcad_mcp.bridge import BRIDGE_VERSION, Bridge, BridgeError, TclError, tcl_quote

from .conftest import TCL_DIR, TOKEN

TRICKY = [
    "",
    "plain",
    "with space",
    'R1 {x} [exit] $env(PATH) "q" ; # c \\ end\\',
    "tab\tnew\nline\rcr",
    "한글 값 10K/±1%",
    "\x01\x1f\x7f",
    "{",
    "}",
    "\\",
    "a\\",
    "C:\\Cadence\\SPB_16.5\\x.dsn",
    "\u00a0nbsp\u3000ideo",
]


@pytest.mark.parametrize("text", TRICKY)
def test_quote_roundtrip(tcl, text):
    tcl.eval(f"set x {tcl_quote(text)}")
    assert tcl.getvar("x") == text


def test_quote_lists(tcl):
    data = [["R1", "Value", "10 K"], ["U1A", "PCB Footprint", "{odd} [x]"], ["C1", "Note", ""]]
    tcl.eval(f"set x {tcl_quote(data)}")
    assert int(tcl.eval("llength $x")) == 3
    for i, item in enumerate(data):
        assert int(tcl.eval(f"llength [lindex $x {i}]")) == 3
        for j, value in enumerate(item):
            assert tcl.eval(f"lindex [lindex $x {i}] {j}") == value
    assert tcl_quote(True) == "1" and tcl_quote(False) == "0"


def test_bridge_version_matches_tcl():
    text = (TCL_DIR / "orcad_bridge.tcl").read_text(encoding="ascii")
    assert re.search(r"variable version (\d+)", text).group(1) == str(BRIDGE_VERSION)


def test_tcl_sources_are_ascii():
    # Capture sources scripts in the system code page; keep them ASCII.
    for f in TCL_DIR.glob("*.tcl"):
        f.read_text(encoding="ascii")


def test_eval_roundtrip(tcl_bridge):
    bridge, _ = tcl_bridge
    assert bridge.eval("expr {6 * 7}") == "42"
    assert bridge.eval("set s {한글 μ±}") == "한글 μ±"
    assert bridge.eval("string repeat x 200000") == "x" * 200000
    with pytest.raises(TclError, match="boom"):
        bridge.eval("error boom")
    assert bridge.eval("return ok") == "ok"


def test_global_scope(tcl_bridge):
    bridge, _ = tcl_bridge
    bridge.eval("set ::persist 5")
    assert bridge.eval("set persist") == "5"


def test_wrong_token(tcl_bridge, monkeypatch):
    bridge, _ = tcl_bridge
    monkeypatch.setenv("ORCAD_MCP_TOKEN", "wrong")
    with pytest.raises(BridgeError, match="rejected the token"):
        bridge.eval("set x 1")


def test_malformed_header_closes(tcl_bridge):
    _, settings = tcl_bridge
    with socket.create_connection(("127.0.0.1", settings.port), timeout=5) as s:
        s.sendall(b"POST / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert s.recv(100) == b""  # closed without evaluating anything
    with socket.create_connection(("127.0.0.1", settings.port), timeout=5) as s:
        s.sendall(b"x" * 300)  # no newline: header too long
        assert s.recv(100) == b""


def test_split_request(tcl_bridge):
    _, settings = tcl_bridge
    payload = b"expr {1 + 2}"
    with socket.create_connection(("127.0.0.1", settings.port), timeout=5) as s:
        s.sendall(f"{TOKEN} {len(payload)}".encode())
        s.sendall(b"\n" + payload[:5])
        s.sendall(payload[5:])
        assert s.recv(100) == b"OK 1\n3"


def test_two_requests_one_connection(tcl_bridge):
    _, settings = tcl_bridge
    a, b = b"set a 1", b"set b 22"
    with socket.create_connection(("127.0.0.1", settings.port), timeout=5) as s:
        s.sendall(f"{TOKEN} {len(a)}\n".encode() + a + f"{TOKEN} {len(b)}\n".encode() + b)
        data = b""
        while data.count(b"\n") < 2 or not data.endswith(b"22"):
            data += s.recv(100)
        assert data == b"OK 1\n1OK 2\n22"


def test_not_running(tmp_path):
    (tmp_path / "token").write_text(TOKEN)
    from orcad_mcp.config import Settings

    from .conftest import free_port

    bridge = Bridge(Settings(port=free_port(), home=tmp_path, connect_timeout=1))
    with pytest.raises(BridgeError, match="not answering"):
        bridge.eval("set x 1")


def test_missing_token(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCAD_MCP_TOKEN", raising=False)
    from orcad_mcp.config import Settings

    bridge = Bridge(Settings(home=tmp_path))
    with pytest.raises(BridgeError, match="No bridge token"):
        bridge.eval("set x 1")


def test_call_loads_lib_once(tcl_bridge):
    bridge, _ = tcl_bridge
    # c_* commands need Capture; define a fake one after the library is loaded to check the dispatch.
    with pytest.raises(TclError, match="unknown command"):
        bridge.call("nothing_like_this")
    bridge.eval(
        "proc ::orcadmcp::c_echo {a b} { return [::orcadmcp::jobj a [::orcadmcp::js $a] b [::orcadmcp::jstrs $b]] }"
    )
    assert bridge.call("echo", "x y", ["1", "{2}", ""]) == {"a": "x y", "b": ["1", "{2}", ""]}
    assert bridge.eval("set ::orcadmcp::lib_hash") == bridge.lib_hash
