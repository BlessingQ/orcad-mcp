"""Add parts to an OrCAD library (.olb) with Cadence's standalone Tcl shell and the OrCAD database API.

Capture is not involved: a copy of the library is edited by tcl/olb_builder.tcl, read back and checked,
then the original is backed up and replaced.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from importlib import resources
from pathlib import Path
from typing import Any

from . import install as installer
from .bridge import OrcadError, tcl_list
from .config import Settings

SIDES = ("L", "R", "B")
PIN_LENGTH = 30


def cadence_tools(settings: Settings) -> tuple[Path, Path, list[Path]]:
    """(tclsh.exe, orDb_Dll_Tcl.dll, folders to put on PATH) of the first usable Cadence installation."""
    for root in installer.find_cdsroots(settings.cdsroot or None):
        tools = root / "tools"
        dll = tools / "capture" / "orDb_Dll_Tcl.dll"
        for tclsh in (tools / "tcltk" / "8.4" / "bin" / "tclsh.exe", tools / "tcltk" / "8.4" / "bin" / "tclsh84.exe"):
            if tclsh.is_file() and dll.is_file():
                return tclsh, dll, [tools / "bin", tools / "capture", tclsh.parent]
    raise OrcadError(
        "Cadence's Tcl shell (tools\\tcltk\\8.4\\bin\\tclsh.exe) and orDb_Dll_Tcl.dll were not found; "
        "set ORCAD_MCP_CDSROOT to the Cadence installation folder"
    )


def validate(parts: list[dict[str, Any]]) -> None:
    if not parts:
        raise ValueError("no parts given")
    names = set()
    for part in parts:
        name = part["name"].strip()
        if not name or re.search(r"[\s{}\[\]\"\\$;]", name):
            raise ValueError(f"invalid part name '{part['name']}'")
        if name.upper() in names:
            raise ValueError(f"part {name} is given twice")
        names.add(name.upper())
        body = part["body"]
        if len(body) != 4 or body[0] >= body[2] or body[1] >= body[3]:
            raise ValueError(f"{name}: body must be [x1, y1, x2, y2] with x1 < x2 and y1 < y2")
        pins = part["pins"]
        if not pins:
            raise ValueError(f"{name}: no pins")
        numbers = set()
        places = set()
        for pin in pins:
            num = str(pin["number"]).strip()
            if not num or num.upper() in numbers:
                raise ValueError(f"{name}: pin number '{num}' empty or repeated")
            numbers.add(num.upper())
            side = pin["side"]
            if side not in SIDES:
                raise ValueError(f"{name}: pin {num} side must be one of {SIDES}")
            pos = int(pin["pos"])
            lo, hi = (body[1], body[3]) if side in ("L", "R") else (body[0], body[2])
            if not lo <= pos <= hi:
                raise ValueError(f"{name}: pin {num} at {pos} is outside the body side ({lo}..{hi})")
            if (side, pos) in places:
                raise ValueError(f"{name}: two pins at {side} {pos}")
            places.add((side, pos))


def spec_text(parts: list[dict[str, Any]]) -> str:
    lines = []
    for part in parts:
        pins = [[str(p["number"]).strip(), p["name"], p["side"], int(p["pos"])] for p in part["pins"]]
        lines.append(
            tcl_list(
                [
                    part["name"].strip(),
                    part["footprint"],
                    part.get("value") or part["name"].strip(),
                    part.get("prefix") or "U",
                    [int(v) for v in part["body"]],
                    pins,
                    part.get("template") or "",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _run(settings: Settings, args: list[str], timeout: float = 300) -> dict[str, Any]:
    tclsh, dll, path = cadence_tools(settings)
    script = resources.files("orcad_mcp").joinpath("tcl", "olb_builder.tcl")
    with resources.as_file(script) as script_path:
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([*(str(p) for p in path), env.get("PATH", "")])
        proc = subprocess.run(
            [str(tclsh), str(script_path), args[0], str(dll), *args[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode != 0 or not lines:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-6:]
        raise OrcadError(f"library builder failed (exit {proc.returncode}): {' | '.join(detail)}")
    return json.loads(lines[-1])


def _expected_hot(part: dict[str, Any], pin: dict[str, Any]) -> list[int]:
    x1, y1, x2, y2 = part["body"]
    pos = int(pin["pos"])
    return {
        "L": [x1 - PIN_LENGTH, pos],
        "R": [x2 + PIN_LENGTH, pos],
        "B": [pos, y2 + PIN_LENGTH],
    }[pin["side"]]


def check(parts: list[dict[str, Any]], listing: dict[str, Any]) -> list[str]:
    """Differences between what was asked for and what the library holds."""
    problems = []
    by_name = {p["name"]: p for p in listing["parts"]}
    for part in parts:
        name = part["name"].strip()
        got = by_name.get(name)
        if not got or not got["exists"]:
            problems.append(f"{name} missing")
            continue
        want_nums = [str(p["number"]).strip() for p in part["pins"]]
        if got["numbers"] != want_nums:
            problems.append(f"{name}: pin numbers {got['numbers']} != {want_nums}")
        if got["footprint"] != part["footprint"]:
            problems.append(f"{name}: footprint {got['footprint']} != {part['footprint']}")
        if len(got["pins"]) != len(part["pins"]):
            problems.append(f"{name}: {len(got['pins'])} pins, expected {len(part['pins'])}")
            continue
        for want, have in zip(part["pins"], got["pins"], strict=True):
            if have["name"] != want["name"] or have["hot"] != _expected_hot(part, want):
                problems.append(f"{name}: pin {want['number']} is {have['name']} at {have['hot']}")
    return problems


def add_parts(settings: Settings, library: Path, parts: list[dict[str, Any]]) -> dict[str, Any]:
    validate(parts)
    stamp = library.stat().st_mtime_ns
    with tempfile.TemporaryDirectory(prefix="orcad-mcp-lib-") as tmp:
        work = Path(tmp) / library.name
        shutil.copy2(library, work)
        spec = Path(tmp) / "spec.tcl"
        spec.write_text(spec_text(parts), encoding="utf-8")
        before = _run(settings, ["list", str(work), str(spec)])
        exists = [p["name"] for p in before["parts"] if p["exists"]]
        if exists:
            raise OrcadError(f"already in {library.name}: {', '.join(exists)}")
        built = _run(settings, ["build", str(work), str(spec)])
        after = _run(settings, ["list", str(work), str(spec)])
        problems = check(parts, after)
        if after["packages"] != before["packages"] + len(parts):
            problems.append(f"library has {after['packages']} packages, expected {before['packages'] + len(parts)}")
        if problems:
            raise OrcadError("the built parts do not match the request, library left unchanged: " + "; ".join(problems))
        if library.stat().st_mtime_ns != stamp:
            raise OrcadError(f"{library} changed while the parts were built; nothing was replaced, try again")
        folder = settings.backup_dir / "libraries" / library.stem
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / f"{library.stem}_{time.strftime('%Y%m%d-%H%M%S')}{library.suffix}"
        shutil.copy2(library, backup)
        shutil.copy2(work, library)
    return {
        "library": str(library),
        "added": built["built"],
        "packages": after["packages"],
        "backup": str(backup),
        "note": "Restart OrCAD Capture before placing the new parts (it keeps libraries in memory).",
    }
