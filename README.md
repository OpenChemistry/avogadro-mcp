# avogadro-mcp

An [MCP](https://modelcontextprotocol.io/) server that drives a running
[Avogadro](https://avogadro.cc/) molecular editor over its local
JSON-RPC socket. Point Claude Desktop, Claude Code, Cursor, or any other
MCP client at it, and it can open and inspect molecules and crystals,
change the view and rendering, render isosurfaces and molecular orbitals,
and take screenshots -- for computational chemistry, molecular modeling,
and visualization work.

Design and background: see `avogadro-mcp-plan.md` in the `openchemistry`
superbuild repo. The wire protocol this package speaks is documented at
<https://avogadro.cc/develop/rpc.html>.

## Requirements

- Python 3.10+.
- Avogadro already running. This package does **not** launch Avogadro by
  default (see [Auto-launch](#auto-launch) below).

## Install

```sh
pip install avogadro-mcp
```

This installs a pure-Python package. It does **not** depend on the
`avogadro` PyPI package (see [Why no `avogadro` dependency](#why-no-avogadro-dependency)
below) -- `mcp` is its only runtime dependency.

## Use

Start Avogadro, then either let your MCP client start the server for you
(see [Client setup](#client-setup)), or run it by hand for testing:

```sh
avogadro-mcp
```

It speaks MCP over stdio. Ask a model connected to it something like "open
caffeine.cml and screenshot it" or "render the HOMO and take a picture" --
call `avogadro_status` first to confirm it can see Avogadro and to see what
is currently loaded.

### Client setup

```sh
avogadro-mcp --print-config claude-desktop   # or: claude-code, cursor
```

prints the JSON snippet to add to that client's MCP config. To write it
directly:

```sh
avogadro-mcp --install claude-desktop
```

This merges an `"avogadro"` entry into `mcpServers` in the client's config
file rather than overwriting it, and backs up whatever was there first to
`<file>.bak`. It prints exactly which file it touched.

| Client | Config file |
|--------|-------------|
| `claude-desktop` | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows), `$XDG_CONFIG_HOME/Claude/claude_desktop_config.json` (Linux) |
| `claude-code` | `.mcp.json` in the current directory (project-scoped) |
| `cursor` | `~/.cursor/mcp.json` |

### Auto-launch

Off by default. If Avogadro is not running, tools fail with a message
telling you to start it, or set `AVOGADRO_MCP_LAUNCH=1` (or pass
`avogadro-mcp --launch`) to have avogadro-mcp start it automatically. When
enabled, the executable is found via `--launch-app`, then `AVOGADRO_APP`,
then a platform default (`/Applications/Avogadro2.app/...` on macOS,
`avogadro2` on `PATH`, `%ProgramFiles%\Avogadro2\bin\avogadro2.exe` on
Windows).

### Non-default socket

If Avogadro was started with `--rpc-name <name>` (e.g. a second, headless
instance), point avogadro-mcp at it with `--rpc-name <name>` or
`AVOGADRO_MCP_SOCKET=<name>`.

### Tools and resources

Curated tools: `avogadro_status`, `open_file`, `load_molecule`,
`get_molecule`, `molecule_info`, `export_file`, `screenshot`,
`list_display_types`, `set_display`, `set_projection`, `rotate`, `zoom`,
`translate`, `reset_view`, `render_surface`, `list_commands`, and the
escape hatch `run_command` for anything without a curated tool (including
third-party plugin commands) -- call `list_commands` to see the full,
schema-annotated vocabulary.

Resources: `avogadro://commands` (live command list, JSON), `avogadro://molecule`
(active molecule as CJSON), `avogadro://molecule/info` (summary stats),
`avogadro://docs/scripting` and `avogadro://docs/commands` (bundled docs
for writing standalone `avogadro.connect` scripts).

## Why no `avogadro` dependency

The `avogadro` wheel on PyPI is a large compiled package (it bundles
avogadrolibs), and this server only needs its ~500-line pure-Python
`connect.py` socket client. To keep `avogadro-mcp` a light, pure-Python
install, we vendor a copy at `src/avogadro_mcp/_connect.py` instead of
depending on the wheel. This also sidesteps the released wheel lagging
behind the RPC features this server needs (`wait`/`timeout` support and
the phase 1 introspection methods).

### Re-syncing the vendored files

Two files are synced copies from an `avogadrolibs` checkout, each with a
header comment recording the source path and the commit it was copied
from:

- `src/avogadro_mcp/_connect.py` <- `avogadrolibs/python/avogadro/connect.py`
- `tests/_vendor/fake_rpc_server.py` <- `avogadrolibs/python/tests/fake_rpc_server.py`

To re-sync either one: diff it against the current avogadrolibs source,
copy the new version in below the header comment, and update the commit
hash in the header. `tests/_vendor/fake_rpc_server.py` is vendored (rather
than reached via `sys.path` into a sibling `avogadrolibs` checkout) so this
package's test suite passes in a fresh clone with no `avogadrolibs`
checkout present at all.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Tests run entirely against a fake, in-process RPC server
(`tests/_vendor/fake_rpc_server.py`) and the `mcp` SDK's in-process
`Client`/`MCPServer` pairing -- no running Avogadro required.

### `catalogue.json`

`src/avogadro_mcp/catalogue.json` is a hand-maintained JSON Schema for
every command documented at `avogadro.cc/source/develop/rpc.md`. The
server overlays it on whatever a running Avogadro's `listCommands` reports,
so tools have real parameter types even though Avogadro's own
`commandSchema()` introspection (a later phase) does not exist yet. Keep it
in sync with `rpc.md` by hand; each catalogue entry is marked
`"source": "catalogue"` so a future app-provided schema wins automatically
once one exists.
