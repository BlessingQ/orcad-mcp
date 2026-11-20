from __future__ import annotations

import json

import pytest

from .conftest import TCL_DIR

STRINGS = ["", "a", 'q"uote', "back\\slash", "nl\nx\r\t", "\x00\x01\x1f", "한글", "{brace} [b] $d"]


@pytest.fixture
def lib(tcl):
    tcl.eval(f"source {{{(TCL_DIR / 'orcad_mcp_lib.tcl').as_posix()}}}")
    return tcl


@pytest.mark.parametrize("text", STRINGS)
def test_js(lib, text):
    lib.setvar("s", text)
    assert json.loads(lib.eval("::orcadmcp::js $s")) == text


def test_nested(lib):
    lib.setvar("s", 'x"y')
    text = lib.eval(
        "::orcadmcp::jobj name [::orcadmcp::js $s] n 3 ok [::orcadmcp::jbool 1] none [::orcadmcp::jnum abc] "
        "list [::orcadmcp::jarr [list [::orcadmcp::jnum 1.5] [::orcadmcp::jstrs {a {b c}}]]] "
        "empty [::orcadmcp::jarr {}]"
    )
    expected = {"name": 'x"y', "n": 3, "ok": True, "none": None, "list": [1.5, ["a", "b c"]], "empty": []}
    assert json.loads(text) == expected


def test_reload_keeps_opened(lib):
    lib.eval("set ::orcadmcp::opened(X.DSN) 1")
    lib.eval(f"source {{{(TCL_DIR / 'orcad_mcp_lib.tcl').as_posix()}}}")
    assert lib.eval("info exists ::orcadmcp::opened(X.DSN)") == "1"


def test_call_unknown(lib):
    with pytest.raises(Exception, match="unknown command"):
        lib.eval("::orcadmcp::call nope")
