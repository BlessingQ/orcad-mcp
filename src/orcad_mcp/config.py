"""Settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 47800


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _flag(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() not in ("0", "false", "no", "off")


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name)) if _env(name) else default
    except ValueError:
        return default


def default_home() -> Path:
    """Folder for the bridge script, the token and backups (same rule as the Tcl bridge)."""
    explicit = _env("ORCAD_MCP_HOME")
    if explicit:
        return Path(explicit).expanduser()
    profile = _env("USERPROFILE")
    return (Path(profile) if profile else Path.home()) / ".orcad-mcp"


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    home: Path = Path()
    timeout: float = 120.0
    connect_timeout: float = 3.0
    allow_tcl: bool = True
    backup: bool = True
    backups_kept: int = 20
    cdsroot: str = ""
    log_level: str = "WARNING"

    @classmethod
    def from_env(cls) -> Settings:
        port = _env("ORCAD_MCP_PORT")
        level = _env("ORCAD_MCP_LOG_LEVEL", "WARNING").upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            level = "WARNING"
        return cls(
            host=_env("ORCAD_MCP_HOST", "127.0.0.1"),
            port=int(port) if port.isdigit() else DEFAULT_PORT,
            home=default_home(),
            timeout=_float("ORCAD_MCP_TIMEOUT", 120.0),
            allow_tcl=_flag("ORCAD_MCP_ALLOW_TCL", True),
            backup=_flag("ORCAD_MCP_BACKUP", True),
            cdsroot=_env("ORCAD_MCP_CDSROOT") or _env("CDSROOT"),
            log_level=level,
        )

    @property
    def token_file(self) -> Path:
        return self.home / "token"

    @property
    def bridge_file(self) -> Path:
        return self.home / "orcad_bridge.tcl"

    @property
    def backup_dir(self) -> Path:
        return self.home / "backups"
