# Changelog

## 0.2.0 - 2026-11-20

- Drawing tools: `active_page`, `goto_page`, `page_objects`, `place_part`, `place_wire`, `place_symbol`,
  `place_marks`, `delete_area` (dry run by default), `rename_symbol`, `set_no_connect`, `check_points`.
  GUI coordinates are converted per page (millimetres on metric pages, inches otherwise).
- `create_library_parts`: adds parts to a `.olb` with Cadence's Tcl shell (copy, verify, back up, replace).
- `save_design` uses Capture's File > Save (like Ctrl+S) when a page of the design is shown, otherwise the
  database save as before (result field `via`).
- Crash protection: the bridge defines `::NULL` (a method call on a NULL Dbo object crashed Capture 16.5);
  `run_tcl` refuses undocumented GUI commands such as `GetWindows`/`GetActivePage` unless `allow_unsafe=true`.
- Bridge version 2: restart Capture after updating.
- Used to apply a 4-page revision (parts, wires, connectors, two new library parts) to a 160-part design.

## 0.1.0 - 2026-11-20

First release.

- Tcl bridge for OrCAD Capture (16.5+, Tcl 8.4): localhost only, token authentication, non-blocking, drops
  malformed requests; installed into `capAutoLoad` with `orcad-mcp install`.
- Tools: `orcad_status`, `install_bridge`, `list_pages`, `list_parts`, `get_part`, `list_nets`, `get_bom`,
  `set_part_properties`, `save_design`, `open_design`, `close_design`, `export_netlist` (PADS PowerPCB ASCII),
  `run_tcl`.
- Verified with OrCAD Capture 16.5-p003 on a 155-part design.
