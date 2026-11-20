"""Command line: run the MCP server (default) or set up / check the Capture bridge."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys

from . import __version__


def _print(data) -> None:
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    print(json.dumps(data, indent=2, ensure_ascii=False) if not isinstance(data, str) else data)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="orcad-mcp",
        description="MCP server for OrCAD Capture. Without a command it runs the MCP server on stdio.",
    )
    parser.add_argument("--version", action="version", version=f"orcad-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    p_install = sub.add_parser("install", help="install the bridge so it starts with Capture")
    p_install.add_argument("--cdsroot", help="Cadence folder, e.g. C:\\Cadence\\SPB_16.5 (default: search)")
    p_uninstall = sub.add_parser("uninstall", help="remove the Capture start-up loader")
    p_uninstall.add_argument("--cdsroot")
    p_uninstall.add_argument("--purge", action="store_true", help="also delete the bridge script and token")
    sub.add_parser("status", help="check the connection to Capture")
    p_tcl = sub.add_parser("tcl", help="run a Tcl script in Capture and print the result")
    p_tcl.add_argument("script", help="Tcl script, or @file.tcl")
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        from .server import run, settings

        logging.basicConfig(stream=sys.stderr, level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
        run()
        return

    from . import install as installer
    from .bridge import Bridge, OrcadError
    from .config import Settings

    settings = Settings.from_env()
    try:
        if args.command == "install":
            report = installer.install(settings, args.cdsroot)
            _print(report)
            if report["autoload_loaders"]:
                _print(
                    "\nInstalled. Restart OrCAD Capture, or start the bridge now in Capture's Command Window:\n"
                    f"    {report['start_now']}"
                )
            else:
                _print(
                    "\nNo Capture installation found (use --cdsroot). Start the bridge by hand in Capture's "
                    f"Command Window:\n    {report['start_now']}"
                )
        elif args.command == "uninstall":
            _print(installer.uninstall(settings, args.cdsroot, args.purge))
        elif args.command == "status":
            _print(Bridge(settings).call("status"))
        elif args.command == "tcl":
            script = args.script
            if script.startswith("@"):
                with open(script[1:], encoding="utf-8") as f:
                    script = f.read()
            _print(Bridge(settings).eval(script))
    except (OrcadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
