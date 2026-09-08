"""
Works around a macOS-specific socket-discovery gap found during live
verification (2026-09-07), not anticipated in the original plan.

Avogadro creates its RPC socket at ``$TMPDIR/<name>`` (matching
``tempfile.gettempdir()`` -- see connect.py). On macOS, ``$TMPDIR`` is a
per-login-session directory under ``/var/folders/...`` , not ``/tmp``.

The official `mcp` Python SDK's own stdio client (``mcp.client.stdio``,
used by real MCP hosts to spawn a server subprocess) only forwards a fixed
allowlist of environment variables on POSIX: ``HOME``, ``LOGNAME``, ``PATH``,
``SHELL``, ``TERM``, ``USER`` -- **not** ``TMPDIR``. When ``avogadro-mcp``
is launched that way (confirmed against the SDK's own ``StdioServerParameters``
in this package's live-verification pass), ``tempfile.gettempdir()`` inside
the spawned process falls back to plain ``/tmp`` instead of the real
per-session directory, so a perfectly healthy Avogadro is reported as "not
running" -- the socket exists, just not where this process is looking.

The fix: macOS exposes the same per-session directory independent of the
environment, via ``confstr(_CS_DARWIN_USER_TEMP_DIR)`` -- the same value
``getconf DARWIN_USER_TEMP_DIR`` prints, and what Qt/``NSTemporaryDirectory()``
resolve to, which is why it matches where Avogadro actually put the socket.
``client.py`` tries the normal path first and only falls back to this when
that fails, so a correctly-inherited ``TMPDIR`` is never overridden.

This shells out to ``getconf`` rather than calling ``confstr(3)`` directly
via ``ctypes``: tried directly first, but it returns an empty string in some
sandboxed/non-interactive process contexts (observed while testing this
package itself) where the ``getconf`` *binary* still resolves it correctly
-- both ultimately read the same per-session value, but evidently not every
calling context can query it in-process. Shelling out costs a few
milliseconds and only happens on the (rare, macOS-only) fallback path, so
the extra reliability is worth it.

Linux/BSD have no equivalent split -- ``/tmp`` there is not session-scoped --
and Windows uses a named pipe rather than a temp-directory socket, so this
module is a no-op everywhere but macOS.
"""

from __future__ import annotations

import subprocess
import sys


def macos_user_temp_dir() -> str | None:
    """
    Return the current user's per-session temp directory on macOS
    (independent of the ``TMPDIR`` environment variable), or ``None`` on
    any other platform or if the lookup fails for any reason.
    """
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["getconf", "DARWIN_USER_TEMP_DIR"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return path.rstrip("/") or None
