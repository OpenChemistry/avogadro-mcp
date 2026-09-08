# Writing standalone Avogadro scripts

This page is bundled with `avogadro-mcp` and served as the
`avogadro://docs/scripting` resource, so a model writing a plain Python
script (not going through an MCP tool call) has the same source of truth
the tools themselves use. It is derived from
`two.avogadro.cc/source/develop/rpc.md`; if the two ever disagree, the
website is current and this copy is stale -- re-sync it (see the bottom of
this file).

## The connection

Avogadro creates a single local server named `avogadro` when it starts:

* **Linux, macOS, BSD** -- a Unix domain socket at `$TMPDIR/avogadro`
  (Python's `tempfile.gettempdir()` resolves to the same place).
* **Windows** -- a named pipe at `\\.\pipe\avogadro`.

Only one Avogadro instance owns the name at a time, so a script always talks
to whichever instance started first and is still running.

## Quick start

```python
from avogadro.connect import connect

with connect() as avo:
    avo.open_file("/path/to/caffeine.cml")
    avo.command("renderVanDerWaals")
    avo.save_graphic("/path/to/caffeine.png")
```

`connect()` raises `ConnectionError` if Avogadro is not running. Each call
blocks until Avogadro answers, and raises `avogadro.connect.RPCError` if
Avogadro reports a problem.

**If the `avogadro` PyPI package is not installed** (avogadro-mcp itself
does not depend on it -- it vendors a copy of `connect.py`), the protocol is
small enough to write directly:

```python
import json, socket, struct, tempfile

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(tempfile.gettempdir() + "/avogadro")

request = json.dumps({
    "jsonrpc": "2.0", "id": 1,
    "method": "openFile", "params": {"fileName": "rutile.POSCAR"},
}).encode("utf-8")
sock.sendall(struct.pack(">I", len(request)) + request)

size = struct.unpack(">I", sock.recv(4))[0]
print(json.loads(sock.recv(size).decode("utf-8")))
```

Each packet on the wire is a 4-byte big-endian unsigned length followed by
that many bytes of UTF-8 JSON, in both directions. On Windows, open
`\\.\pipe\avogadro` as an unbuffered binary file instead of a socket; the
framing and JSON are identical.

## The `wait` rule

Most commands finish before they reply. A few -- rendering a surface or an
orbital -- hand the work to a background thread, so the reply would
otherwise go out while the calculation is still running. **Pass `wait=True`
for those**, or a script that renders a surface and immediately screenshots
will capture the molecule without the surface:

```python
avo.command("renderMO", orbital="homo", isovalue=0.02, wait=True, timeout=300)
avo.save_graphic("homo.png")   # the orbital is actually there now
```

`wait` and `timeout` are reserved parameter names, stripped before the
options reach the plugin -- no command can ever have an option by either
name. `timeout` defaults to 60 seconds; give more for a large surface.
A `wait=True` reply carries a result envelope:

```json
{ "status": "finished", "message": "Surface Finished",
  "data": { "cubeIndex": 1, "isovalue": 0.02, "resolution": 0.1 } }
```

`avogadro.connect.result_data(response)` pulls `data` out of that envelope
(or returns `{}` for a plain-`true` reply, which is what you get without
`wait`).

## The busy rule

A plugin runs one command at a time. Sending it a second command while the
first is still running is refused with error code `-3` rather than queued --
a script decides for itself whether to wait, retry, or give up:

```python
from avogadro.connect import RPCError

for orbital in ("homo-1", "homo", "lumo"):
    try:
        avo.command("renderMO", orbital=orbital, wait=True, timeout=300)
        avo.save_graphic(f"{orbital}.png")
    except RPCError as error:
        if error.code == RPCError.PLUGIN_BUSY:
            print(f"{orbital}: still busy, skipping")
        else:
            print(f"{orbital} failed: {error.message}")
```

## Error codes

| Code | Meaning |
|------|---------|
| `-1` | The request could not be carried out -- a file that could not be read, or no open window. |
| `-2` | A command failed, or timed out while being waited for. The message is the plugin's own. |
| `-3` | That plugin is already running a command. |
| `-32600` | The request was not valid JSON-RPC. |
| `-32601` | No tool or extension claims this method. |

## Read-back replies

Three reply shapes exist, and they do not overlap:

* An action-only built-in (`openFile`, `setProjection`, ...) answers the
  plain `true` it always has.
* A *waited* plugin command answers the
  `{"status": "finished", "message": ..., "data": {...}}` envelope above.
* A **read-back** (`version`, `listCommands`, `moleculeInfo`, `getMolecule`,
  `renderImage`, `listDisplayTypes`) answers its payload directly as
  `result`, with no envelope. `wait` is meaningless for these -- they have
  already finished by the time they answer.

## Windows caveat

The `connect` client opens the named pipe as an ordinary file, which has no
read timeout on Windows -- unlike the Unix socket path, which honours
`timeout=` on `connect()`. A server that stops answering blocks a Windows
script indefinitely; Avogadro's own command timeout (`-2` above) is the
backstop, not the client's.

## Headless / batch use

Nothing here changes for batch scripts (e.g. one screenshot per file in a
directory) except that they should not assume a visible window will always
be at the same size: use `render_image(width=..., height=...)` rather than
`save_graphic()` if the output size needs to be predictable, and remember
`nativeWidth`/`nativeHeight` in the reply tell you the true resolution of
the underlying grab, which matters if the requested size exceeds it.

---

*Re-syncing this file*: it is a hand-maintained digest of
`two.avogadro.cc/source/develop/rpc.md`, not a mechanical extract (that
mechanical extract is `avogadro://docs/commands`, sourced from
`commands.md` next to this file). When `rpc.md` changes in a way that
affects the `wait`/error-code/read-back contract, update both this file and
`commands.md` by hand.
