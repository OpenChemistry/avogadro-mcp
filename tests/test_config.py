"""Tests for --print-config / --install: the client config snippet and the
conservative merge-and-backup installer."""

from __future__ import annotations

import json

import pytest

from avogadro_mcp import config


def test_print_config_shape():
    text = config.print_config("claude-desktop")
    data = json.loads(text)
    assert "avogadro" in data["mcpServers"]
    assert data["mcpServers"]["avogadro"]["command"]


def test_print_config_rejects_unknown_client():
    with pytest.raises(ValueError):
        config.print_config("not-a-real-client")


def test_install_creates_new_file(monkeypatch, tmp_path):
    monkeypatch.setitem(config.CLIENT_PATHS, "cursor", lambda: tmp_path / "mcp.json")
    path, backed_up = config.install_config("cursor")
    assert path == tmp_path / "mcp.json"
    assert not backed_up
    data = json.loads(path.read_text())
    assert data["mcpServers"]["avogadro"]["command"]


def test_install_merges_and_backs_up_existing_content(monkeypatch, tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps({"mcpServers": {"other-server": {"command": "x"}}}))
    monkeypatch.setitem(config.CLIENT_PATHS, "cursor", lambda: target)

    path, backed_up = config.install_config("cursor")
    assert backed_up
    backup = target.with_name(target.name + ".bak")
    assert backup.exists()
    assert json.loads(backup.read_text())["mcpServers"] == {"other-server": {"command": "x"}}

    data = json.loads(path.read_text())
    assert "other-server" in data["mcpServers"]
    assert "avogadro" in data["mcpServers"]


def test_install_tolerates_corrupt_existing_file(monkeypatch, tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text("{not valid json")
    monkeypatch.setitem(config.CLIENT_PATHS, "cursor", lambda: target)

    path, backed_up = config.install_config("cursor")
    assert backed_up
    data = json.loads(path.read_text())
    assert "avogadro" in data["mcpServers"]
