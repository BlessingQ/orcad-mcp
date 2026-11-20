# Changelog

## 0.1.0 - 2026-11-20

First release.

- Tcl bridge for OrCAD Capture (16.5+, Tcl 8.4): localhost only, token authentication, non-blocking, drops
  malformed requests; installed into `capAutoLoad` with `orcad-mcp install`.
- Tools: `orcad_status`, `install_bridge`, `list_pages`, `list_parts`, `get_part`, `list_nets`, `get_bom`,
  `set_part_properties`, `save_design`, `open_design`, `close_design`, `export_netlist` (PADS PowerPCB ASCII),
  `run_tcl`.
- Verified with OrCAD Capture 16.5-p003 on a 155-part design.
