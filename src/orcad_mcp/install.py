"""Install the bridge: script + token in the user folder, a loader in Capture's capAutoLoad folder."""

from __future__ import annotations

import glob
import secrets
from pathlib import Path

from .bridge import tcl_resource
from .config import Settings

AUTOLOAD_NAME = "orcad_mcp_autoload.tcl"

AUTOLOAD_SCRIPT = """\
# Installed by orcad-mcp (https://github.com/BlessingQ/orcad-mcp).
# Starts the orcad-mcp bridge when OrCAD Capture starts.
# Remove with "orcad-mcp uninstall", or delete this file.
proc ::orcadmcp_autoload {} {
    global env
    set f ""
    if {[info exists env(ORCAD_MCP_HOME)] && $env(ORCAD_MCP_HOME) ne ""} {
        set f [file join $env(ORCAD_MCP_HOME) orcad_bridge.tcl]
    } elseif {[info exists env(USERPROFILE)] && $env(USERPROFILE) ne ""} {
        set f [file join $env(USERPROFILE) .orcad-mcp orcad_bridge.tcl]
    }
    if {$f ne "" && [file exists $f]} {
        uplevel #0 [list source $f]
    }
    return ""
}
# empty after a successful start, otherwise the error message
if {[catch {::orcadmcp_autoload} ::orcadmcp_autoload_error]} {
    catch {puts "orcad-mcp: $::orcadmcp_autoload_error"}
}
"""


def autoload_dir(cdsroot: Path) -> Path:
    return cdsroot / "tools" / "capture" / "tclscripts" / "capAutoLoad"


def find_cdsroots(explicit: str | None = None) -> list[Path]:
    """Cadence installations that have a Capture capAutoLoad folder."""
    if explicit:
        root = Path(explicit)
        if not autoload_dir(root).is_dir():
            raise FileNotFoundError(f"{autoload_dir(root)} does not exist; is {root} a Cadence SPB/OrCAD folder?")
        return [root]
    found: list[Path] = []
    candidates = [*glob.glob("C:/Cadence/*"), *glob.glob("C:/Cadence/*/*"), *glob.glob("D:/Cadence/*")]
    for c in candidates:
        root = Path(c)
        if autoload_dir(root).is_dir() and root.resolve() not in [f.resolve() for f in found]:
            found.append(root)
    return found


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="ascii", errors="replace") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii", newline="\n")
    return True


def ensure_token(settings: Settings) -> bool:
    """Create the shared secret if missing. Returns True when a new token was written."""
    if settings.token_file.exists() and settings.token_file.read_text(encoding="ascii").strip():
        return False
    settings.home.mkdir(parents=True, exist_ok=True)
    settings.token_file.write_text(secrets.token_hex(24), encoding="ascii")
    return True


def write_bridge(settings: Settings) -> bool:
    return _write_if_changed(settings.bridge_file, tcl_resource("orcad_bridge.tcl"))


def refresh_bridge_if_installed(settings: Settings) -> bool:
    """Keep an installed bridge script in sync with this package version (used at server start)."""
    if not settings.bridge_file.exists():
        return False
    return write_bridge(settings)


def install(settings: Settings, cdsroot: str | None = None) -> dict:
    roots = find_cdsroots(cdsroot or settings.cdsroot or None)
    new_token = ensure_token(settings)
    write_bridge(settings)
    loaders = []
    errors = []
    for root in roots:
        target = autoload_dir(root) / AUTOLOAD_NAME
        try:
            _write_if_changed(target, AUTOLOAD_SCRIPT)
            loaders.append(str(target))
        except OSError as exc:
            errors.append(f"{target}: {exc}")
    return {
        "bridge_script": str(settings.bridge_file),
        "token_file": str(settings.token_file),
        "new_token": new_token,
        "autoload_loaders": loaders,
        "errors": errors,
        "start_now": f"source {{{settings.bridge_file.as_posix()}}}",
    }


def uninstall(settings: Settings, cdsroot: str | None = None, purge: bool = False) -> dict:
    removed = []
    for root in find_cdsroots(cdsroot or settings.cdsroot or None):
        target = autoload_dir(root) / AUTOLOAD_NAME
        if target.exists():
            target.unlink()
            removed.append(str(target))
    if purge:
        for f in (settings.bridge_file, settings.token_file):
            if f.exists():
                f.unlink()
                removed.append(str(f))
    return {"removed": removed}


def installed_state(settings: Settings) -> dict:
    loaders = []
    try:
        for root in find_cdsroots(settings.cdsroot or None):
            target = autoload_dir(root) / AUTOLOAD_NAME
            if target.exists():
                loaders.append(str(target))
    except FileNotFoundError:
        pass
    return {
        "bridge_script": settings.bridge_file.exists(),
        "token": settings.token_file.exists(),
        "autoload_loaders": loaders,
    }
