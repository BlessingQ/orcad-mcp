# orcad-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Code, Claude Desktop, any MCP
client) read and edit **OrCAD Capture** schematics while Capture is running.

[한국어 문서 (Korean)](README.ko.md)

- **Read** designs, pages, parts (Value, PCB Footprint, any property), pins with their nets, and the full
  netlist (connectivity across pages, as Capture's netlister sees it)
- **Edit** part properties in bulk: Value, PCB Footprint, Part Reference, user properties. All changes of a
  call are checked first and applied together, with rollback on failure
- **Save** the design (the `.dsn` is backed up first)
- **BOM**: grouped by any properties, optionally written as CSV
- **PADS netlist**: writes a PADS PowerPCB ASCII netlist (`.asc`) for PADS Layout, in the layout Capture's
  own PADS formatter uses
- **Tcl escape hatch** (`run_tcl`): the complete Capture database API (`Dbo*`) and Capture commands

## How it works

```
MCP client ──stdio──> orcad-mcp (Python) ──TCP 127.0.0.1:47800──> bridge (Tcl, inside Capture) ──> Capture database
```

Capture has a Tcl interpreter with a database API. A small bridge script runs inside Capture, listens on
`127.0.0.1` only, and executes requests from orcad-mcp. Every request must carry a random token stored in
`%USERPROFILE%\.orcad-mcp\token`, so only programs of the same Windows user can talk to it.

## Requirements

- Windows with **OrCAD Capture 16.5** or later (tested with 16.5, Tcl 8.4.15; 17.x uses the same API but
  has not been tested yet)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python by itself):
  `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

## Installation

### 1. Install the bridge into Capture (once per PC)

```powershell
uvx orcad-mcp install
```

This writes the bridge script and a token to `%USERPROFILE%\.orcad-mcp`, and a small loader to
`<Cadence>\tools\capture\tclscripts\capAutoLoad` (found through `CDSROOT` or under `C:\Cadence`;
use `--cdsroot C:\Cadence\SPB_16.5` to choose). Capture then starts the bridge every time it starts.

**Restart Capture**, or start the bridge right away in Capture's Command Window (View > Command Window):

```tcl
source {C:/Users/<you>/.orcad-mcp/orcad_bridge.tcl}
```

The Command Window shows `orcad-mcp bridge 1 listening on 127.0.0.1:47800`. Check from a terminal:

```powershell
uvx orcad-mcp status
```

> Not on PyPI yet? Use the GitHub version everywhere `uvx orcad-mcp` appears:
> `uvx --from git+https://github.com/BlessingQ/orcad-mcp orcad-mcp install`

### 2. Add the server to your MCP client

**Claude Code** (all projects on this PC):

```powershell
claude mcp add --scope user orcad -- uvx orcad-mcp
```

Use `--scope project` to write `.mcp.json` into a project instead, or `--scope local` for this project only.
Start a new Claude Code session afterwards.

**Claude Desktop / other clients** (`claude_desktop_config.json` or the client's MCP config):

```json
{
  "mcpServers": {
    "orcad": {
      "command": "uvx",
      "args": ["orcad-mcp"]
    }
  }
}
```

If `uvx` is not on the `PATH` of the client, use its full path (`C:\Users\<you>\.local\bin\uvx.exe`).

**Updating**: `uvx` caches the package. Run `uvx orcad-mcp@latest --version` (or `uv cache clean orcad-mcp`)
to fetch a new release; the server updates the installed bridge script when it starts. Restart Capture when
`orcad_status` reports a bridge version mismatch.

## Tools

| Tool | What it does |
|------|--------------|
| `orcad_status` | Bridge connection, Capture version, open designs (active, unsaved changes). Explains how to start the bridge when it does not answer |
| `install_bridge` | Same as `orcad-mcp install`, from the AI client |
| `list_pages` | Schematic folders and pages with part counts |
| `list_parts` | Part table (refdes, value, footprint, page, library part) with wildcard filters and extra property columns |
| `get_part` | All properties (and which are read-only), pins with pin name, type and net, location |
| `list_nets` | Nets with their pins (`U1.9:PA3`), filter by net name or by part |
| `get_bom` | BOM grouped by properties; optional CSV file |
| `set_part_properties` | Change properties of one or many parts at once (checked first, rolled back on failure) |
| `save_design` | Save to the `.dsn` (backup to `%USERPROFILE%\.orcad-mcp\backups` first) |
| `open_design` / `close_design` | Load a `.dsn` that is not open in Capture (no window) and unload it again |
| `export_netlist` | PADS PowerPCB ASCII netlist (`.asc`) |
| `run_tcl` | Run any Tcl in Capture |

`design` arguments accept the `.dsn` path, the file name or a unique part of it; without it the design active
in Capture's project manager is used.

Example prompts:

- *"List all 100n capacitors and their footprints."* → `list_parts value="*100n*"`
- *"Which nets is U2 connected to? Is PA9 really on USART1_TX?"* → `list_nets refdes="U2"`
- *"Set the footprint of every 0603 resistor to R1608, then show me the changes."* → `list_parts`,
  `set_part_properties`
- *"Make a BOM grouped by Value and Manufacturer and save it as CSV next to the design."* → `get_bom`
- *"Export the PADS netlist."* → `export_netlist`

## Safety

- Reading never changes anything.
- `set_part_properties` changes the design in Capture's memory immediately (the server asks Capture to
  redraw the active page); the `.dsn` file only changes with `save_design`. Capture's **Edit > Undo does not cover these changes**: the
  tool returns the old values, so the assistant can set them back.
- `save_design` copies the current `.dsn` to `%USERPROFILE%\.orcad-mcp\backups\<design>\` first (the last 20
  are kept). Capture also writes its usual `_0.DBK` backup.
- Write tools are marked destructive in the MCP tool annotations, so clients ask for approval.
- `run_tcl` can do anything Capture's Tcl can (including starting programs). Disable it with
  `ORCAD_MCP_ALLOW_TCL=0`.
- The bridge only accepts connections from `127.0.0.1` with the token; malformed requests (for example a web
  page posting to the port) are dropped without being evaluated.
- Capture runs requests on its GUI thread. While a modal dialog is open in Capture, requests wait and time
  out after `ORCAD_MCP_TIMEOUT` seconds.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `ORCAD_MCP_PORT` | `47800` | Bridge port (set it for Capture too, e.g. as a Windows user variable) |
| `ORCAD_MCP_HOME` | `%USERPROFILE%\.orcad-mcp` | Bridge script, token, backups (must be the same for Capture) |
| `ORCAD_MCP_TIMEOUT` | `120` | Seconds to wait for Capture |
| `ORCAD_MCP_ALLOW_TCL` | `1` | `0` disables `run_tcl` |
| `ORCAD_MCP_BACKUP` | `1` | `0` disables the backup before `save_design` |
| `ORCAD_MCP_CDSROOT` | `CDSROOT` | Cadence folder used by `install` |
| `ORCAD_MCP_LOG_LEVEL` | `WARNING` | Log level (stderr) |

## Command line

```
orcad-mcp                 run the MCP server on stdio (what MCP clients start)
orcad-mcp install         install the bridge (see above); --cdsroot DIR
orcad-mcp uninstall       remove the Capture loader; --purge also deletes bridge script and token
orcad-mcp status          check the connection, list open designs
orcad-mcp tcl "<script>"  run Tcl in Capture (or @file.tcl)
```

## Limitations / roadmap

- Placing parts, drawing wires, net aliases and power symbols are not tools yet. Capture's user-action Tcl
  commands (`PlacePart`, `PlaceWire`, ...) can be used through `run_tcl`; dedicated tools are planned once
  their behaviour is verified.
- Hierarchical designs with reused blocks (occurrence properties): parts are read per instance, not per
  occurrence.
- PDF export is not included.
- One Capture instance per port.

## Development

```powershell
git clone https://github.com/BlessingQ/orcad-mcp
cd orcad-mcp
uv sync
uv run pytest            # unit tests; the bridge protocol is tested in a local Tcl interpreter, no Capture needed
uv run ruff check src tests
```

The Capture-side code is in `src/orcad_mcp/tcl/`: `orcad_bridge.tcl` (TCP server, kept small and stable) and
`orcad_mcp_lib.tcl` (queries and edits; sent to Capture by the server and reloaded whenever it changes).
It must stay compatible with Tcl 8.4 (no `{*}`, `dict`, `lassign`, `lmap`, `apply`).

Cadence's reference for the Tcl API: `<Cadence>\tools\capture\tclscripts\OrCAD_Capture_TclTk_Extensions.pdf`.

## License

MIT. OrCAD and Cadence are trademarks of Cadence Design Systems, Inc. This project is not affiliated with
Cadence.
