from __future__ import annotations

import socket
import threading
import time
import tkinter
from pathlib import Path

import pytest

from orcad_mcp.bridge import Bridge
from orcad_mcp.config import Settings

TCL_DIR = Path(__file__).resolve().parents[1] / "src" / "orcad_mcp" / "tcl"
TOKEN = "test-token-0123456789"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def tcl():
    """A plain Tcl interpreter (no Capture) for testing quoting and the JSON helpers."""
    return tkinter.Tcl()


@pytest.fixture
def tcl_bridge(tmp_path, monkeypatch):
    """orcad_bridge.tcl running in a Tcl interpreter on its own thread; yields (Bridge, Settings)."""
    port = free_port()
    (tmp_path / "token").write_text(TOKEN, encoding="ascii")
    monkeypatch.setenv("ORCAD_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("ORCAD_MCP_PORT", str(port))
    monkeypatch.delenv("ORCAD_MCP_TOKEN", raising=False)
    ready = threading.Event()
    stop = threading.Event()
    failure: list[BaseException] = []

    def run() -> None:
        try:
            interp = tkinter.Tcl()
            interp.eval(f"source {{{(TCL_DIR / 'orcad_bridge.tcl').as_posix()}}}")
        except BaseException as exc:  # pragma: no cover - reported below
            failure.append(exc)
            ready.set()
            return
        ready.set()
        while not stop.is_set():
            if not interp.tk.dooneevent(tkinter._tkinter.DONT_WAIT):
                time.sleep(0.001)
        interp.eval("::orcadmcp::bridge::stop")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(10)
    if failure:
        raise failure[0]
    settings = Settings(port=port, home=tmp_path, timeout=10)
    yield Bridge(settings), settings
    stop.set()
    thread.join(5)
