from __future__ import annotations

import pytest

from orcad_mcp import install as installer
from orcad_mcp.config import Settings


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "SPB_16.5"
    installer.autoload_dir(root).mkdir(parents=True)
    settings = Settings(home=tmp_path / "home", cdsroot=str(root))
    return settings, root


def test_install_uninstall(env):
    settings, root = env
    report = installer.install(settings)
    loader = installer.autoload_dir(root) / installer.AUTOLOAD_NAME
    assert report["autoload_loaders"] == [str(loader)]
    assert report["new_token"] is True
    token = settings.token_file.read_text()
    assert len(token) == 48
    assert settings.bridge_file.read_text(encoding="ascii").startswith("# orcad-mcp bridge")
    # idempotent, token kept
    assert installer.install(settings)["new_token"] is False
    assert settings.token_file.read_text() == token
    assert installer.installed_state(settings)["autoload_loaders"] == [str(loader)]

    assert installer.uninstall(settings)["removed"] == [str(loader)]
    assert not loader.exists() and settings.token_file.exists()
    installer.uninstall(settings, purge=True)
    assert not settings.token_file.exists() and not settings.bridge_file.exists()


def test_bad_cdsroot(tmp_path):
    with pytest.raises(FileNotFoundError):
        installer.find_cdsroots(str(tmp_path / "nope"))


def test_refresh_only_when_installed(env):
    settings, _ = env
    assert installer.refresh_bridge_if_installed(settings) is False
    assert not settings.bridge_file.exists()
    installer.write_bridge(settings)
    settings.bridge_file.write_text("old", encoding="ascii")
    assert installer.refresh_bridge_if_installed(settings) is True
    assert settings.bridge_file.read_text(encoding="ascii") != "old"


def test_loader_is_valid_tcl(tcl, tmp_path, monkeypatch):
    # the loader sources the bridge from ORCAD_MCP_HOME; with no bridge there it must do nothing
    monkeypatch.setenv("ORCAD_MCP_HOME", str(tmp_path))
    assert tcl.eval(f"info complete {{{installer.AUTOLOAD_SCRIPT}}}") == "1"
    tcl.eval(installer.AUTOLOAD_SCRIPT)
    assert tcl.eval("set ::orcadmcp_autoload_error") == ""
    assert tcl.eval("namespace exists ::orcadmcp::bridge") == "0"
