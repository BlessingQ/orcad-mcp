"""Client for the Tcl bridge that runs inside OrCAD Capture (tcl/orcad_bridge.tcl)."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
from importlib import resources
from typing import Any

from .config import Settings

BRIDGE_VERSION = 2
NEEDLIB = "ORCADMCP_NEEDLIB"

_TCL_SPECIAL = set('\\[]{}$";# ')


class OrcadError(Exception):
    """A user-facing failure (message is shown to the MCP client as is)."""


class BridgeError(OrcadError):
    """The bridge cannot be reached or does not speak the protocol."""


class TclError(OrcadError):
    """The script ran in Capture and raised a Tcl error."""


def tcl_quote(value: Any) -> str:
    """Quote one value as a single Tcl word (backslash escapes; safe for any text)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return tcl_quote(tcl_list(value))
    text = str(value)
    if text == "":
        return "{}"
    out = []
    for ch in text:
        if ch in _TCL_SPECIAL:
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ch == "\x7f" or ch.isspace():
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def tcl_list(items: Any) -> str:
    """A Tcl list (as text) of the given items; nested lists/tuples become nested Tcl lists."""
    return " ".join(tcl_quote(i) for i in items) if items else "{}"


def tcl_resource(name: str) -> str:
    return resources.files("orcad_mcp").joinpath("tcl", name).read_text(encoding="utf-8")


class Bridge:
    """Sends Tcl scripts to Capture. One request per TCP connection; calls are serialized."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._lib = tcl_resource("orcad_mcp_lib.tcl")
        self.lib_hash = hashlib.sha1(self._lib.encode("utf-8")).hexdigest()[:12]

    # -- transport ----------------------------------------------------------------------------

    def _token(self) -> str:
        env = os.environ.get("ORCAD_MCP_TOKEN", "").strip()
        if env:
            return env
        try:
            token = self.settings.token_file.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            raise BridgeError(
                f"No bridge token at {self.settings.token_file}. Run 'orcad-mcp install' (or the install_bridge "
                "tool) and restart Capture, or start the bridge in Capture's Command Window."
            ) from None
        if not token or any(c.isspace() for c in token):
            raise BridgeError(f"Invalid bridge token in {self.settings.token_file}")
        return token

    def not_running_message(self) -> str:
        bridge = self.settings.bridge_file.as_posix()
        return (
            f"OrCAD Capture bridge is not answering on {self.settings.host}:{self.settings.port}. "
            "Make sure Capture is running. If the bridge is installed ('orcad-mcp install'), restart Capture; "
            f"or start it now in Capture's Command Window (View > Command Window): source {{{bridge}}}"
        )

    def _roundtrip(self, script: str, timeout: float | None) -> tuple[str, str]:
        token = self._token()
        payload = script.encode("utf-8")
        try:
            sock = socket.create_connection(
                (self.settings.host, self.settings.port), timeout=self.settings.connect_timeout
            )
        except OSError:
            raise BridgeError(self.not_running_message()) from None
        with sock:
            sock.settimeout(timeout or self.settings.timeout)
            try:
                sock.sendall(f"{token} {len(payload)}\n".encode("ascii") + payload)
                stream = sock.makefile("rb")
                header = stream.readline(128)
                if not header.endswith(b"\n"):
                    raise BridgeError(
                        "The program on the bridge port closed the connection without an answer "
                        "(token mismatch, or not an orcad-mcp bridge)."
                    )
                tag, _, length = header.decode("ascii", "replace").strip().partition(" ")
                if tag not in ("OK", "ERR") or not length.isdigit():
                    raise BridgeError(f"Unexpected reply from the bridge port: {header[:60]!r}")
                body = stream.read(int(length))
                if len(body) != int(length):
                    raise BridgeError("The bridge closed the connection in the middle of a reply.")
            except TimeoutError:
                raise BridgeError(
                    f"Capture did not answer within {timeout or self.settings.timeout:.0f} s. A modal dialog "
                    "may be open in Capture, or the request is still running there."
                ) from None
            except OSError as exc:
                raise BridgeError(f"Connection to the Capture bridge failed: {exc}") from None
        text = body.decode("utf-8", "replace")
        if tag == "ERR" and text.startswith("bad header"):
            raise BridgeError(
                "An older, incompatible bridge (the feasibility spike?) is listening on port "
                f"{self.settings.port}. Stop it in Capture (::mcpbridge::stop) and start the orcad-mcp bridge."
            )
        if tag == "ERR" and text == "orcad-mcp bridge: invalid token":
            raise BridgeError(
                f"The bridge rejected the token in {self.settings.token_file}. Restart the bridge in Capture "
                "so it reads the current token (source the bridge again, or restart Capture)."
            )
        return tag, text

    def eval(self, script: str, timeout: float | None = None) -> str:
        """Evaluate a Tcl script at global level in Capture and return its result."""
        with self._lock:
            tag, text = self._roundtrip(script, timeout)
        if tag == "ERR":
            raise TclError(text)
        return text

    # -- helper library -----------------------------------------------------------------------

    def load_lib(self) -> None:
        self.eval(f"{self._lib}\nset ::orcadmcp::lib_hash {self.lib_hash}\nreturn {{}}")

    def call(self, command: str, *args: Any, timeout: float | None = None) -> Any:
        """Run ::orcadmcp::c_<command> (loading the helper library first if needed); returns parsed JSON."""
        guard = (
            f'if {{![info exists ::orcadmcp::lib_hash] || $::orcadmcp::lib_hash ne "{self.lib_hash}"}} '
            f"{{error {NEEDLIB}}}\n"
        )
        script = guard + " ".join(["::orcadmcp::call", tcl_quote(command), *(tcl_quote(a) for a in args)])
        try:
            text = self.eval(script, timeout)
        except TclError as exc:
            if str(exc) != NEEDLIB:
                raise
            self.load_lib()
            text = self.eval(script, timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise OrcadError(f"Invalid JSON from Capture ({exc}): {text[:200]}") from None
