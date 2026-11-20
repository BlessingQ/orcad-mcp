"""Pure data helpers: natural sorting, part table handling, BOM grouping and the PADS netlist writer."""

from __future__ import annotations

import csv
import fnmatch
import io
import re
from collections.abc import Iterable, Sequence
from typing import Any

PADS_HEADER = "!PADS-POWERPCB-V3.0-MILS! DESIGN DATABASE ASCII FILE 2.0"
PINS_PER_LINE = 8


def natural_key(text: str) -> tuple:
    """R2 < R10, U1.9 < U1.10."""
    return tuple(int(t) if t.isdigit() else t.upper() for t in re.split(r"(\d+)", text))


def strip_view(part_name: str) -> str:
    """'TPS76333_0.Normal' -> 'TPS76333_0' (Capture appends the symbol view)."""
    for suffix in (".Normal", ".Convert"):
        if part_name.endswith(suffix):
            return part_name[: -len(suffix)]
    return part_name


def patterns(spec: str | Sequence[str] | None) -> list[str]:
    """'R*, C1?' or ['R*', 'C1?'] -> ['R*', 'C1?']."""
    if spec is None:
        return []
    items = spec.split(",") if isinstance(spec, str) else list(spec)
    return [p.strip() for p in items if p and p.strip()]


def matches(value: str | None, pats: list[str]) -> bool:
    if not pats:
        return True
    text = (value or "").upper()
    return any(fnmatch.fnmatchcase(text, p.upper()) for p in pats)


def table_to_dicts(table: dict) -> list[dict[str, Any]]:
    cols = table["columns"]
    return [dict(zip(cols, row, strict=True)) for row in table["rows"]]


def pin_label(ref: str, number: str, name: str, with_name: bool) -> str:
    label = f"{ref}.{number}"
    if with_name and name and name != number:
        label += f":{name}"
    return label


# --------------------------------------------------------------------------------------------------
# BOM
# --------------------------------------------------------------------------------------------------


def bom_rows(parts: Iterable[dict[str, Any]], group_by: Sequence[str]) -> list[dict[str, Any]]:
    """Group parts (dicts keyed by property name) by the group_by values; one entry per package."""
    seen: set[str] = set()
    groups: dict[tuple, list[str]] = {}
    for part in parts:
        ref = part["reference"] or part["refdes"]
        if ref in seen:  # further sections (U1B, U1C ...) of a package already counted
            continue
        seen.add(ref)
        key = tuple(part.get(k) or "" for k in group_by)
        groups.setdefault(key, []).append(ref)
    rows = []
    for key, refs in groups.items():
        refs.sort(key=natural_key)
        row: dict[str, Any] = {"quantity": len(refs)}
        row.update(zip(group_by, key, strict=True))
        row["refdes"] = ", ".join(refs)
        rows.append(row)
    rows.sort(key=lambda r: natural_key(r["refdes"].split(",")[0]))
    return rows


def bom_csv(rows: list[dict[str, Any]], group_by: Sequence[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["Item", "Quantity", "Reference", *group_by])
    for i, row in enumerate(rows, 1):
        writer.writerow([i, row["quantity"], row["refdes"], *(row[k] for k in group_by)])
    return out.getvalue()


# --------------------------------------------------------------------------------------------------
# PADS netlist
# --------------------------------------------------------------------------------------------------


def pads_netlist(parts: Iterable[dict[str, Any]], nets: Iterable[dict[str, Any]]) -> tuple[str, list[str]]:
    """PADS PowerPCB ASCII netlist in the layout Capture's PADS formatter writes.

    parts: dicts with "reference" (package refdes, U1 for U1A) and "footprint".
    nets:  dicts with "name" and "pins" ([ref, pin_number, pin_name] lists).
    Returns (text with CRLF line ends, warnings).
    """
    warnings: list[str] = []
    footprints: dict[str, str] = {}
    for part in parts:
        ref = part["reference"] or part["refdes"]
        fp = (part.get("footprint") or "").strip()
        prev = footprints.get(ref)
        if not prev:
            footprints[ref] = fp
        elif fp and fp != prev:
            warnings.append(f"{ref}: sections have different PCB Footprints ({prev} / {fp}); using {prev}")
    missing = sorted((r for r, fp in footprints.items() if not fp), key=natural_key)
    if missing:
        warnings.append("no PCB Footprint (PADS will not import these): " + ", ".join(missing))

    lines = [PADS_HEADER, "*PART*"]
    for ref in sorted(footprints):
        lines.append(f"{ref:<16} {footprints[ref]}".rstrip())
    lines += ["", "*NET*"]
    bad_names = []
    for net in sorted(nets, key=lambda n: n["name"]):
        pins = sorted({f"{ref}.{num}" for ref, num, _name in net["pins"]}, key=natural_key)
        if not pins:
            continue
        name = net["name"]
        if not name or any(c.isspace() for c in name):
            bad_names.append(repr(name))
        lines.append(f"*SIGNAL* {name}")
        for i in range(0, len(pins), PINS_PER_LINE):
            lines.append(" ".join(pins[i : i + PINS_PER_LINE]))
        unknown = sorted({ref for ref, _n, _p in net["pins"]} - footprints.keys())
        if unknown:
            warnings.append(f"net {name}: pins on parts not in the part list: {', '.join(unknown)}")
    if bad_names:
        warnings.append("net names with spaces cannot be imported by PADS: " + ", ".join(bad_names))
    lines.append("*END*")
    return "\r\n".join(lines) + "\r\n", warnings
