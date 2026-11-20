"""MCP tools for OrCAD Capture."""

from __future__ import annotations

import functools
import locale
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from . import install as installer
from .bridge import BRIDGE_VERSION, Bridge, BridgeError, OrcadError
from .config import Settings
from .netlist import (
    bom_csv,
    bom_rows,
    matches,
    natural_key,
    pads_netlist,
    patterns,
    pin_label,
    strip_view,
    table_to_dicts,
)

INSTRUCTIONS = """\
Reads and edits OrCAD Capture schematics (16.5 and later) through a small Tcl bridge running inside Capture.

* Start with orcad_status. If the bridge does not answer, it reports why and how to start it
  (install_bridge once, then restart Capture or source the bridge in Capture's Command Window).
* `design` arguments take the .dsn path, the file name, or a unique part of the file name; leave them
  out to use the design that is active in Capture's project manager.
* Reading is always safe. set_part_properties changes the design in Capture's memory right away (the user
  sees it); the .dsn file changes only with save_design. Ask the user before saving. save_design copies the
  .dsn to a backup folder first.
* Capture's Edit > Undo does not cover changes made here. set_part_properties returns the old values, so
  restore them with another set_part_properties call if needed.
* Scripts run on Capture's GUI thread: while a modal dialog is open in Capture, calls wait or time out.
* Coordinates are Capture database units (1/100 inch); rotation is 0-3 (x 90 degrees).
* run_tcl runs any Tcl in Capture (Dbo* database API, Capture commands). Prefer the dedicated tools.
"""

log = logging.getLogger("orcad_mcp")

settings = Settings.from_env()
bridge = Bridge(settings)
mcp = MCPServer(name="orcad", instructions=INSTRUCTIONS, version=__version__, log_level=settings.log_level)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
CONTROL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def tool(annotations: ToolAnnotations):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (OrcadError, FileNotFoundError, FileExistsError, ValueError) as exc:
                raise ToolError(str(exc)) from None
            except Exception as exc:
                log.exception("%s failed unexpectedly", fn.__name__)
                raise ToolError(f"{type(exc).__name__}: {exc}") from None

        return mcp.tool(annotations=annotations)(wrapper)

    return decorator


def _spec(design: str | None) -> str:
    return (design or "").strip()


def _parts(design: str | None, properties: list[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    table = bridge.call("parts", _spec(design), list(properties or []))
    rows = table_to_dicts(table)
    for row in rows:
        row["part"] = strip_view(row["part"])
    return table["design"], rows


def _nets(design: str | None) -> tuple[str, list[dict[str, Any]]]:
    data = bridge.call("nets", _spec(design))
    return data["design"], data["nets"]


# --------------------------------------------------------------------------------------------------
# status / setup
# --------------------------------------------------------------------------------------------------


@tool(READ)
def orcad_status() -> dict:
    """Bridge connection, Capture version and the open designs (path, active, unsaved changes).

    If Capture or the bridge is not reachable, the result explains how to start it instead of failing.
    """
    try:
        status = bridge.call("status")
    except BridgeError as exc:
        return {
            "connected": False,
            "problem": str(exc),
            "installed": installer.installed_state(settings),
            "server_version": __version__,
            "how_to_start": [
                "Once per PC: run the install_bridge tool (or 'orcad-mcp install' in a terminal).",
                "Then restart OrCAD Capture, or type this in Capture's Command Window (View > Command Window): "
                f"source {{{settings.bridge_file.as_posix()}}}",
            ],
        }
    status["connected"] = True
    status["server_version"] = __version__
    if str(status.get("bridge")) != str(BRIDGE_VERSION):
        status["bridge_note"] = (
            f"Capture runs bridge version {status.get('bridge')}; this server ships version {BRIDGE_VERSION}. "
            "Run install_bridge and restart Capture to update it."
        )
    return status


@tool(WRITE)
def install_bridge(cdsroot: str | None = None) -> dict:
    """Install the Capture-side bridge so it starts with Capture (once per PC).

    Writes the bridge script and a random access token to %USERPROFILE%\\.orcad-mcp and a small loader into
    <Cadence>\\tools\\capture\\tclscripts\\capAutoLoad of every Cadence installation found (or `cdsroot`, e.g.
    C:\\Cadence\\SPB_16.5). Capture must be restarted afterwards, or the bridge sourced in its Command Window.
    """
    report = installer.install(settings, cdsroot)
    if not report["autoload_loaders"]:
        report["note"] = (
            "No Capture installation was found (or it is not writable). Pass cdsroot, or start the bridge by hand "
            f"in Capture's Command Window: {report['start_now']}"
        )
    try:
        bridge.call("status")
        report["bridge_running"] = True
        if report["new_token"]:
            report["note"] = "A new token was written: restart Capture (or source the bridge again) to use it."
    except BridgeError:
        report["bridge_running"] = False
    return report


# --------------------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------------------


@tool(READ)
def list_pages(design: str | None = None) -> dict:
    """Schematic folders and pages of a design, with the number of placed parts per page."""
    return bridge.call("pages", _spec(design))


@tool(READ)
def list_parts(
    design: str | None = None,
    refdes: str | None = None,
    value: str | None = None,
    page: str | None = None,
    properties: list[str] | None = None,
    limit: int | None = None,
) -> dict:
    """Placed parts as a table: refdes, value, footprint (PCB Footprint), page, part (library part name).

    Filters are case-insensitive wildcards, several separated by commas: refdes="R*,C1?", value="*10K*",
    page="POWER". `properties` adds columns for more properties (e.g. ["Manufacturer", "Tolerance"]);
    a part without that property shows null. Multi-section parts (U1A, U1B) are listed per section with the
    package reference in the "reference" column.
    """
    design_path, rows = _parts(design, properties)
    total = len(rows)
    ref_p, val_p, page_p = patterns(refdes), patterns(value), patterns(page)
    rows = [
        r
        for r in rows
        if (matches(r["refdes"], ref_p) or matches(r["reference"], ref_p))
        and matches(r["value"], val_p)
        and matches(r["page"], page_p)
    ]
    rows.sort(key=lambda r: natural_key(r["refdes"]))
    cols = ["refdes", "reference", "value", "footprint", "page", "schematic", "part", *(properties or [])]
    if all(r["reference"] == r["refdes"] for r in rows):
        cols.remove("reference")
    if len({r["schematic"] for r in rows}) <= 1:
        cols.remove("schematic")
    shown = rows[:limit] if limit else rows
    result = {
        "design": design_path,
        "total_parts": total,
        "matched": len(rows),
        "columns": cols,
        "rows": [[r[c] for c in cols] for r in shown],
    }
    if len(shown) < len(rows):
        result["truncated"] = f"showing {len(shown)} of {len(rows)}; raise limit or narrow the filters"
    return result


@tool(READ)
def get_part(refdes: str, design: str | None = None) -> dict:
    """Everything about one part: all properties (read_only lists those Capture does not allow to edit),
    pins with pin name, type and connected net, library, page, location and rotation.

    refdes may be a section (U1A) or the package (U1, returns every section).
    """
    data = bridge.call("part", _spec(design), refdes.strip())
    for part in data["parts"]:
        part["part"] = strip_view(part["part"])
        part["pins"].sort(key=lambda p: natural_key(p["number"]))
    return data


@tool(READ)
def list_nets(
    design: str | None = None,
    name: str | None = None,
    refdes: str | None = None,
    pin_names: bool = True,
    limit: int | None = None,
) -> dict:
    """Nets of the design (connectivity across pages, as in the netlist) with their pins.

    Pins read "U1.9:PA3" (refdes.pin-number:pin-name; the name is left out when it equals the number,
    or when pin_names=false). Filters: name="*3V3*,GND" (wildcards, commas), refdes="U1" (nets touching
    that part).
    """
    design_path, nets = _nets(design)
    name_p, ref_p = patterns(name), patterns(refdes)
    out = []
    for net in nets:
        if not matches(net["name"], name_p):
            continue
        if ref_p and not any(matches(ref, ref_p) for ref, _num, _pin in net["pins"]):
            continue
        pins = sorted(
            {pin_label(ref, num, pname, pin_names) for ref, num, pname in net["pins"]},
            key=natural_key,
        )
        out.append({"name": net["name"], "pins": pins})
    out.sort(key=lambda n: natural_key(n["name"]))
    shown = out[:limit] if limit else out
    result = {"design": design_path, "total_nets": len(nets), "matched": len(out), "nets": shown}
    if len(shown) < len(out):
        result["truncated"] = f"showing {len(shown)} of {len(out)}; raise limit or narrow the filters"
    return result


@tool(READ)
def get_bom(
    design: str | None = None,
    group_by: list[str] | None = None,
    csv_path: str | None = None,
) -> dict:
    """Bill of materials: parts grouped by `group_by` properties (default ["Value", "PCB Footprint"]),
    with quantity and reference list. Each multi-section package counts once.

    csv_path (optional, absolute) also writes the BOM as a CSV file (UTF-8 with BOM, opens in Excel).
    """
    keys = group_by or ["Value", "PCB Footprint"]
    design_path, rows = _parts(design, keys)
    rows_by_name = []
    for r in rows:
        item = {"refdes": r["refdes"], "reference": r["reference"]}
        item.update({k: r.get(k) for k in keys})
        rows_by_name.append(item)
    bom = bom_rows(rows_by_name, keys)
    result: dict[str, Any] = {"design": design_path, "lines": len(bom), "group_by": keys, "bom": bom}
    if csv_path:
        path = Path(csv_path)
        if not path.is_absolute():
            raise ValueError("csv_path must be an absolute path")
        path.write_text(bom_csv(bom, keys), encoding="utf-8-sig", newline="")
        result["csv"] = str(path)
    return result


# --------------------------------------------------------------------------------------------------
# editing
# --------------------------------------------------------------------------------------------------


class PropertyChange(BaseModel):
    refdes: str = Field(description="Reference designator: R12, or U3 for every section of U3 (U3A only that one)")
    property: str = Field(description="Property name, e.g. Value, PCB Footprint; a new name adds a user property")
    value: str = Field(description="New value (text)")


@tool(WRITE)
def set_part_properties(changes: list[PropertyChange], design: str | None = None) -> dict:
    """Set part properties (Value, PCB Footprint, Part Reference, user properties ...) in Capture.

    All changes are checked first and applied together; if one fails, the ones already applied are
    rolled back. Read-only properties (see get_part) are refused. The result lists old and new values.
    The design is changed in Capture's memory; call save_design (after the user agrees) to write the file.
    """
    if not changes:
        raise ValueError("no changes given")
    triples = [[c.refdes.strip(), c.property.strip(), c.value] for c in changes]
    return bridge.call("set_props", _spec(design), triples, True)


def _backup(dsn: Path) -> str | None:
    if not settings.backup or not dsn.is_file():
        return None
    folder = settings.backup_dir / dsn.stem
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{dsn.stem}_{time.strftime('%Y%m%d-%H%M%S')}{dsn.suffix}"
    shutil.copy2(dsn, target)
    old = sorted(folder.glob(f"{dsn.stem}_*{dsn.suffix}"))
    for stale in old[: max(0, len(old) - settings.backups_kept)]:
        stale.unlink(missing_ok=True)
    return str(target)


@tool(WRITE)
def save_design(design: str | None = None) -> dict:
    """Save the design to its .dsn file (like File > Save). The current .dsn is copied to
    %USERPROFILE%\\.orcad-mcp\\backups first. Ask the user before saving."""
    info = bridge.call("resolve", _spec(design))
    backup = _backup(Path(info["path"]))
    result = bridge.call("save", info["path"])
    result["had_unsaved_changes"] = info["modified"]
    result["backup"] = backup
    return result


@tool(CONTROL)
def open_design(path: str) -> dict:
    """Load a .dsn into Capture's database (no window opens) so the other tools can read or edit it.
    Close it again with close_design."""
    dsn = Path(path)
    if not dsn.is_absolute():
        raise ValueError("path must be an absolute path to a .dsn file")
    if not dsn.is_file():
        raise FileNotFoundError(f"{dsn} does not exist")
    return bridge.call("open", str(dsn.resolve()))


@tool(CONTROL)
def close_design(design: str, discard: bool = False) -> dict:
    """Unload a design that was loaded with open_design. Refuses when it has unsaved changes unless
    discard=true. Designs the user opened in Capture are not closed."""
    return bridge.call("close", _spec(design), discard)


# --------------------------------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------------------------------


@tool(CONTROL)
def export_netlist(path: str | None = None, design: str | None = None, overwrite: bool = False) -> dict:
    """Write a PADS PowerPCB ASCII netlist (.asc, the layout Capture's PADS formatter uses) for PADS Layout.

    path: absolute output file; default <design folder>\\<design name>.asc. An existing file is only replaced
    with overwrite=true. Parts are listed per package with their PCB Footprint; warnings name parts without
    a footprint and net names PADS cannot import.
    """
    design_path, parts = _parts(design)
    _, nets = _nets(design_path)
    target = Path(path) if path else Path(design_path).with_suffix(".asc")
    if not target.is_absolute():
        raise ValueError("path must be absolute")
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} exists; pass overwrite=true to replace it")
    text, warnings = pads_netlist(parts, nets)
    target.write_bytes(text.encode(locale.getpreferredencoding(False), errors="replace"))
    packages = {p["reference"] or p["refdes"] for p in parts}
    return {
        "design": design_path,
        "file": str(target),
        "parts": len(packages),
        "nets": sum(1 for n in nets if n["pins"]),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------------------------------
# escape hatch
# --------------------------------------------------------------------------------------------------


@tool(WRITE)
def run_tcl(script: str, timeout_s: float | None = None) -> dict:
    """Run a Tcl script in Capture's interpreter (Tcl 8.4: no {*}, dict, lassign, lmap) and return its result.

    Gives full access to Capture's database API (DboSession, DboDesign, Dbo* classes; see Cadence's
    OrCAD_Capture_TclTk_Extensions.pdf) and Capture commands (Menu, PlaceWire, ...). Use only when no dedicated
    tool fits, and tell the user what the script changes. Disabled when ORCAD_MCP_ALLOW_TCL=0.
    """
    if not settings.allow_tcl:
        raise OrcadError("run_tcl is disabled (ORCAD_MCP_ALLOW_TCL=0)")
    return {"result": bridge.eval(script, timeout_s)}


def run() -> None:
    try:
        installer.refresh_bridge_if_installed(settings)
    except OSError as exc:
        log.warning("could not update %s: %s", settings.bridge_file, exc)
    mcp.run("stdio")
