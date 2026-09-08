"""
Client configuration snippets and installers for `avogadro-mcp --print-config`
/ `--install` (plan §3.5).

Kept conservative on purpose: `install_config` merges the "avogadro" entry
into `mcpServers` in the existing file rather than overwriting it, and backs
up whatever was there first.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Callable

SERVER_NAME = "avogadro"


def server_command() -> str:
    """The command to launch avogadro-mcp with, preferring an absolute path
    if the console script can be found on PATH (a client spawning the
    server may not inherit the same PATH/venv as this shell)."""
    return shutil.which("avogadro-mcp") or "avogadro-mcp"


def mcp_server_entry() -> dict:
    """The `mcpServers.avogadro` entry, in the shape every client here
    shares (Claude Desktop, Claude Code, and Cursor all read
    `{"command": ..., "args": [...]}`)."""
    return {"command": server_command(), "args": []}


def _claude_desktop_config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home()))
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    # Linux and other Unix-likes.
    xdg_config = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg_config) / "Claude" / "claude_desktop_config.json"


def _claude_code_config_path() -> Path:
    # Project-scoped MCP config that Claude Code reads automatically;
    # see https://docs.claude.com/en/docs/claude-code/mcp -- this is the
    # file `claude mcp add --scope project` would also write to.
    return Path.cwd() / ".mcp.json"


def _cursor_config_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


#: Client name -> function returning the config file it reads.
CLIENT_PATHS: dict[str, Callable[[], Path]] = {
    "claude-desktop": _claude_desktop_config_path,
    "claude-code": _claude_code_config_path,
    "cursor": _cursor_config_path,
}


def config_path(client: str) -> Path:
    try:
        return CLIENT_PATHS[client]()
    except KeyError:
        raise ValueError(
            f"Unknown client {client!r}; choose one of {sorted(CLIENT_PATHS)}."
        ) from None


def print_config(client: str) -> str:
    """Return the JSON snippet to merge into `client`'s config file."""
    # Validates the client name even though we don't need the path here.
    config_path(client)
    return json.dumps({"mcpServers": {SERVER_NAME: mcp_server_entry()}}, indent=2)


def install_config(client: str) -> tuple[Path, bool]:
    """
    Merge the avogadro-mcp entry into `client`'s config file, creating it
    (and its parent directories) if necessary.

    Existing content is preserved -- only `mcpServers.avogadro` is added or
    replaced. If the file already existed, it is copied to `<file>.bak`
    first (overwriting any previous backup).

    :returns: (path written, whether a pre-existing file was backed up).
    """
    path = config_path(client)
    path.parent.mkdir(parents=True, exist_ok=True)

    backed_up = False
    data: dict = {}
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
        backed_up = True
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if not isinstance(data, dict):
            data = {}

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    servers[SERVER_NAME] = mcp_server_entry()

    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path, backed_up
