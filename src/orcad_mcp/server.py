"""MCP tools for OrCAD Capture."""

from __future__ import annotations

import functools
import locale
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Annotated, Any, Literal

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
* Coordinates are Capture database units (1/100 inch, y grows downwards); rotation is 0-3 (x 90 degrees).
* Drawing: page_objects (free space, pin coordinates) -> goto_page -> place_part / place_wire /
  place_symbol / place_marks -> check_points or list_nets to verify. Placement works on the page Capture
  shows. Connect new parts with short wires and off-page connectors or power/ground symbols named like the
  existing nets instead of long wires. delete_area lists first and deletes only with apply=true.
* create_library_parts adds parts to a .olb without Capture; restart Capture afterwards.
* run_tcl runs any Tcl in Capture (Dbo* database API, Capture commands). Prefer the dedicated tools. Two
  mistakes crash Capture (Tcl catch cannot stop it): calling a method on a NULL Dbo result, and calling
  undocumented GUI commands (GetWindows, GetActiveWindow, GetActivePage, ...); run_tcl refuses the latter.
"""

log = logging.getLogger("orcad_mcp")

settings = Settings.from_env()
bridge = Bridge(settings)
mcp = MCPServer(name="orcad", instructions=INSTRUCTIONS, version=__version__, log_level=settings.log_level)

# MCP meaning: destructiveHint=true "may perform destructive updates", false "performs only additive updates".
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# changes what Capture shows or loads, or writes a new output file; the design is not changed
CONTROL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
# adds objects (parts, wires, symbols, library parts) without changing or removing existing ones
ADD = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
# overwrites existing values; repeating the same call gives the same result
UPDATE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)
# deletes, overwrites files or runs arbitrary code
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


@tool(ADD)
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


@tool(UPDATE)
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


# --------------------------------------------------------------------------------------------------
# page editing (Capture GUI commands on the page shown in the active window)
# --------------------------------------------------------------------------------------------------

Point = Annotated[list[int], Field(min_length=2, max_length=2, description="[x, y] in database units (1/100 inch)")]


def _points(points: list[list[int]]) -> list[list[int]]:
    out = []
    for p in points:
        if len(p) != 2:
            raise ValueError(f"a point is [x, y], got {p}")
        out.append([int(p[0]), int(p[1])])
    return out


@tool(CONTROL)
def active_page() -> dict:
    """The page Capture shows in its active window: design, schematic folder, page name, page number, metric.

    Capture has no safe query for this, so a 0.2 mm marker wire is drawn in the page corner and deleted again
    (the page is marked modified). All page values are null when the active window is not a schematic page.
    """
    return bridge.call("active_page")


@tool(CONTROL)
def goto_page(page: str, design: str | None = None) -> dict:
    """Show another page (name, or "schematic/page") of the same schematic folder in Capture's active window,
    by stepping View > Next Page / Previous Page (they follow the page numbers). The placement tools work on
    the page that is shown, so call this first. A schematic page of the design must already be shown."""
    return bridge.call("goto_page", _spec(design), page.strip())


@tool(READ)
def page_objects(page: str, design: str | None = None, area: list[int] | None = None) -> dict:
    """Geometry of one page in database units (1/100 inch, y grows downwards): parts (placement point,
    rotation, bounding box incl. their text, pins with connection-point coordinates), power/ground symbols
    and off-page connectors (placement point, bounding box), wires (net, end points).

    Use it to find free space and pin coordinates before placing. area=[x1, y1, x2, y2] keeps only the
    objects that touch that rectangle. The page does not need to be shown.
    """
    data = bridge.call("page_objects", _spec(design), page.strip())
    if area:
        if len(area) != 4:
            raise ValueError("area is [x1, y1, x2, y2]")
        x1, y1, x2, y2 = area

        def touches(box: list) -> bool:
            return box[2] >= x1 and box[0] <= x2 and box[3] >= y1 and box[1] <= y2

        data["parts"] = [p for p in data["parts"] if touches(p["bbox"])]
        data["symbols"] = [s for s in data["symbols"] if touches(s["bbox"])]
        data["wires"] = [
            w
            for w in data["wires"]
            if touches(
                [
                    min(w["start"][0], w["end"][0]),
                    min(w["start"][1], w["end"][1]),
                    max(w["start"][0], w["end"][0]),
                    max(w["start"][1], w["end"][1]),
                ]
            )
        ]
    return data


@tool(ADD)
def place_part(
    page: str,
    library: str,
    part: str,
    x: int,
    y: int,
    refdes: str | None = None,
    value: str | None = None,
    footprint: str | None = None,
    design: str | None = None,
) -> dict:
    """Place a library part (library = full .olb path) on the page shown in Capture with its placement point
    at (x, y), then set Part Reference, Value and PCB Footprint. Returns the pins with the coordinates of
    their connection points, to wire them with place_wire / place_symbol.

    Use goto_page first. Edit > Undo in Capture does not cover it; remove it again with delete_area.
    """
    return bridge.call(
        "place_part",
        _spec(design),
        page.strip(),
        library,
        part,
        int(x),
        int(y),
        refdes or "",
        value or "",
        footprint or "",
    )


@tool(ADD)
def place_wire(page: str, points: list[Point], design: str | None = None) -> dict:
    """Draw a wire through points [[x, y], ...] (horizontal or vertical segments) on the page shown.

    Wires connect where an end lands on a pin connection point or on another wire's end. A wire that only
    crosses another one, or ends in the middle of it, does not connect: add a junction there (place_marks).
    The result lists the nets of the wires at the given points and whether both ends are connection points.
    """
    return bridge.call("place_wire", _spec(design), page.strip(), _points(points))


@tool(ADD)
def place_symbol(
    page: str,
    kind: Literal["offpage", "power", "ground"],
    name: str,
    x: int,
    y: int,
    symbol: str | None = None,
    library: str | None = None,
    offset: list[int] | None = None,
    design: str | None = None,
) -> dict:
    """Place an off-page connector, power or ground symbol whose connection point is at (x, y).

    Off-page connectors with the same name connect across the pages of a schematic folder; power and ground
    symbols connect by name everywhere. Defaults from CAPSYM.OLB: offpage OFFPAGELEFT-L (connection point at
    its left end, text to the right; OFFPAGELEFT-R has it at the right end), power VCC_ARROW, ground GND
    (GND_SIGNAL, GND_POWER, GND_EARTH also known). For another symbol pass offset = connection point minus
    placement point.
    """
    default = {"offpage": "OFFPAGELEFT-L", "power": "VCC_ARROW", "ground": "GND"}[kind]
    off = offset or []
    if off and len(off) != 2:
        raise ValueError("offset is [dx, dy]")
    return bridge.call(
        "place_symbol",
        _spec(design),
        page.strip(),
        kind,
        name,
        int(x),
        int(y),
        symbol or default,
        library or "",
        [int(v) for v in off],
    )


@tool(ADD)
def place_marks(
    page: str, kind: Literal["junction", "no_connect"], points: list[Point], design: str | None = None
) -> dict:
    """Place junctions (to join a wire end to the middle of another wire) or no-connect marks (on unused pin
    connection points) at the given points of the page shown."""
    return bridge.call("place_marks", _spec(design), page.strip(), kind, _points(points))


@tool(WRITE)
def delete_area(page: str, x1: int, y1: int, x2: int, y2: int, apply: bool = False, design: str | None = None) -> dict:
    """Delete everything on a page that touches the rectangle (Capture's area select takes objects that
    intersect it, not only enclosed ones, and the bounding box of a part includes its text).

    With apply=false (default) nothing is deleted: the result lists what would go. Check it, then call again
    with apply=true; the page must be shown for that. "remaining" lists what still touches the area afterwards.
    """
    return bridge.call("delete_area", _spec(design), page.strip(), int(x1), int(y1), int(x2), int(y2), bool(apply))


@tool(UPDATE)
def rename_symbol(page: str, x: int, y: int, name: str, design: str | None = None) -> dict:
    """Rename the power/ground symbol or off-page connector whose bounding box contains (x, y) on the page
    shown (this moves it to another net)."""
    return bridge.call("rename_symbol", _spec(design), page.strip(), int(x), int(y), name)


@tool(UPDATE)
def set_no_connect(refdes: str, pins: list[str], no_connect: bool, design: str | None = None) -> dict:
    """Set or clear the no-connect flag of pins (numbers or names) of a part, directly in the database
    (no page needs to be shown). Clear it when wiring a pin that was marked unused."""
    if not pins:
        raise ValueError("no pins given")
    return bridge.call("set_no_connect", _spec(design), refdes.strip(), [str(p) for p in pins], bool(no_connect))


@tool(READ)
def check_points(page: str, points: list[Point], design: str | None = None) -> dict:
    """For each point: is it a connection point (pin end, wire end, symbol) and which nets do wires ending
    there belong to. Use it to verify a placement."""
    return bridge.call("check_points", _spec(design), page.strip(), _points(points))


# --------------------------------------------------------------------------------------------------
# libraries
# --------------------------------------------------------------------------------------------------


class LibPin(BaseModel):
    number: str = Field(description="Pin number as on the footprint: 1, 2, A5, SH1 ...")
    name: str = Field(description="Pin name shown on the symbol")
    side: Literal["L", "R", "B"] = Field(description="Body side: L left, R right, B bottom")
    pos: int = Field(description="y of the pin (L, R) or x (B) in database units, on the 10-unit grid")


class LibPart(BaseModel):
    name: str = Field(description="New package/part name")
    footprint: str = Field(description="PCB Footprint")
    value: str | None = Field(default=None, description="Default Value (default: the name)")
    prefix: str = Field(default="U", description="Reference prefix: U, J, R ...")
    body: list[int] = Field(description="Body rectangle [x1, y1, x2, y2]; pins are 30 long, outside the body")
    pins: list[LibPin]
    template: str | None = Field(
        default=None,
        description="Existing package in the library with at least as many pins to copy from "
        "(default: the smallest that fits)",
    )


@tool(ADD)
def create_library_parts(library: str, parts: list[LibPart]) -> dict:
    """Add new parts to an OrCAD library (.olb), all pins passive, with a rectangular body.

    Capture is not used: Cadence's own Tcl shell edits a copy of the library through the OrCAD database API,
    the copy is checked, the original is backed up to %USERPROFILE%\\.orcad-mcp\\backups and replaced. Capture
    keeps libraries in memory: restart Capture before placing the new parts. Existing parts are not changed;
    a name that already exists is refused.
    """
    from . import libedit

    lib = Path(library)
    if not lib.is_absolute():
        raise ValueError("library must be an absolute path to a .olb file")
    if not lib.is_file():
        raise FileNotFoundError(f"{lib} does not exist")
    return libedit.add_parts(settings, lib, [p.model_dump() for p in parts])


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
    """Save the design to its .dsn file. The current .dsn is copied to %USERPROFILE%\\.orcad-mcp\\backups
    first. Ask the user before saving.

    "file_written" tells whether the .dsn on disk was rewritten. Capture's window may keep showing its "*"
    (modified) mark afterwards: no scripted save clears it (database save, File > Save by menu id or by menu
    path were all tried); the file is saved anyway, and Ctrl+S in Capture clears the mark.
    """
    info = bridge.call("resolve", _spec(design))
    dsn = Path(info["path"])
    backup = _backup(dsn)
    before = dsn.stat().st_mtime_ns if dsn.is_file() else None
    result = bridge.call("save", info["path"])
    after = dsn.stat().st_mtime_ns if dsn.is_file() else None
    result["file_written"] = after is not None and after != before
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


# Capture GUI commands without a documented signature. Calling them (e.g. without the arguments they
# expect) crashed Capture 16.5 with an access violation that Tcl catch cannot stop.
UNSAFE_COMMANDS = frozenset(
    {
        "GetWindows",
        "GetActiveWindow",
        "GetActivePage",
        "GetActiveOpjName",
        "GetParent",
        "GetClass",
        "GetSubControls",
        "GetControlName",
        "GetControlType",
        "GetControlValue",
        "GetMenu",
        "GetSubMenu",
        "GetSystemMenu",
        "GetMenuItemCount",
        "GetMenuItemId",
        "GetMenuState",
        "GetMenuString",
        "GetEventTags",
        "GetLastMouseClickPointOnPage",
        "GetToolName",
        "GetSelectedObjects",
        "GetSelectedPMItems",
        "GetPMItemName",
        "GetPMItemType",
        "FindWindowEx",
        "OpenWindow",
        "CloseWindow",
        "SetAppWindowAsParent",
    }
)
_COMMAND_WORD = re.compile(r"(?:^|[\n;\[{])\s*((?:::)?[A-Za-z_][\w:]*)")


def unsafe_commands(script: str) -> list[str]:
    """Names from UNSAFE_COMMANDS used in command position in a Tcl script (a rough, conservative scan)."""
    found = []
    for m in _COMMAND_WORD.finditer(script):
        word = m.group(1).rsplit("::", 1)[-1]
        if word in UNSAFE_COMMANDS and word not in found:
            found.append(word)
    return found


@tool(WRITE)
def run_tcl(script: str, timeout_s: float | None = None, allow_unsafe: bool = False) -> dict:
    """Run a Tcl script in Capture's interpreter (Tcl 8.4: no {*}, dict, lassign, lmap, min()/max() in expr)
    and return its result.

    Gives full access to Capture's database API (DboSession, DboDesign, Dbo* classes; see Cadence's
    OrCAD_Capture_TclTk_Extensions.pdf) and Capture commands (Menu, PlaceWire, ...). Use only when no dedicated
    tool fits, and tell the user what the script changes. Disabled when ORCAD_MCP_ALLOW_TCL=0.

    Refused when the script uses an undocumented Capture GUI command known to crash Capture (GetWindows,
    GetActiveWindow, GetActivePage, ...) unless allow_unsafe=true. Never call a method on a Dbo result
    without checking it for NULL (e.g. the net of an unconnected pin).
    """
    if not settings.allow_tcl:
        raise OrcadError("run_tcl is disabled (ORCAD_MCP_ALLOW_TCL=0)")
    bad = unsafe_commands(script)
    if bad and not allow_unsafe:
        raise OrcadError(
            f"run_tcl refused: {', '.join(bad)} can crash OrCAD Capture (undocumented GUI command). Use the "
            "dedicated tools (active_page, goto_page, ...) or pass allow_unsafe=true after saving the design."
        )
    return {"result": bridge.eval(script, timeout_s)}


def run() -> None:
    try:
        installer.refresh_bridge_if_installed(settings)
    except OSError as exc:
        log.warning("could not update %s: %s", settings.bridge_file, exc)
    mcp.run("stdio")
