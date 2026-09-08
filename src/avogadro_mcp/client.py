"""
The connection between the (async) MCP tool layer and the (blocking) socket
client in ``_connect.py``.

Design notes (see avogadro-mcp-plan.md §3.1):

* Connection is lazy: nothing is opened until the first tool call.
* Auto-launch is off by default. It is controlled by the ``launch``
  constructor argument, which ``cli.py`` sets from ``AVOGADRO_MCP_LAUNCH=1``
  or ``--launch``.
* All socket I/O happens on a single dedicated worker thread (a
  ``ThreadPoolExecutor`` of size 1). Because that pool has exactly one
  worker, submitted calls are automatically serialized in submission order
  -- two tool calls can never interleave bytes on the wire, and the
  connect/reconnect bookkeeping (``self._conn``) is only ever touched from
  that one thread, so it needs no separate lock.
* ``RPCError`` and ``ConnectionError`` are translated into
  ``mcp.server.mcpserver.exceptions.ToolError`` with a message aimed at the
  model calling the tool, not a Python traceback.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from mcp.server.mcpserver.exceptions import ToolError

from . import _connect
from ._paths import macos_user_temp_dir
from .launch import launch_avogadro

#: Default socket/pipe name, matching _connect.DEFAULT_SERVER ("avogadro").
DEFAULT_SOCKET_NAME = _connect.DEFAULT_SERVER


class AvogadroClient:
    """
    An async-friendly handle to one Avogadro RPC connection.

    One instance is shared by every tool call in the server process. It
    owns exactly one ``_connect.connect`` socket and exactly one worker
    thread; every ``await client.call(...)`` from any tool goes through
    that same thread, one at a time.
    """

    def __init__(
        self,
        socket_name: str = DEFAULT_SOCKET_NAME,
        *,
        launch: bool = False,
        app_path: str | None = None,
        launch_timeout: float = 20.0,
        connect_timeout: float = 10.0,
    ):
        self._socket_name = socket_name
        self._launch = launch
        self._app_path = app_path
        self._launch_timeout = launch_timeout
        self._connect_timeout = connect_timeout
        self._conn: _connect.connect | None = None
        # A single worker: the whole point is that only one thread ever
        # touches self._conn or the socket, so calls cannot interleave.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="avogadro-mcp-rpc"
        )

    @property
    def socket_name(self) -> str:
        return self._socket_name

    @property
    def launch_enabled(self) -> bool:
        return self._launch

    # -- connection management (worker thread only) ----------------------

    def _connect_now(self) -> _connect.connect:
        try:
            return _connect.connect(name=self._socket_name, timeout=self._connect_timeout)
        except ConnectionError:
            # macOS-only fallback for a stripped-down subprocess environment
            # (e.g. the mcp SDK's own stdio client does not forward TMPDIR to
            # a spawned server) -- see _paths.py for the full story. Only
            # retried when it would actually change the socket path, and
            # only when the socket exists there, so a genuinely-not-running
            # Avogadro still raises the normal "not running" error.
            alt_dir = macos_user_temp_dir()
            if not alt_dir or alt_dir == tempfile.gettempdir():
                raise
            alt_socket = os.path.join(alt_dir, self._socket_name)
            if not os.path.exists(alt_socket):
                raise
            previous = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = alt_dir
            tempfile.tempdir = None  # force tempfile to re-resolve gettempdir()
            try:
                return _connect.connect(
                    name=self._socket_name, timeout=self._connect_timeout
                )
            finally:
                if previous is None:
                    os.environ.pop("TMPDIR", None)
                else:
                    os.environ["TMPDIR"] = previous
                tempfile.tempdir = None

    def _ensure_connected(self) -> _connect.connect:
        """Return a live connection, connecting (and maybe launching) first.

        Only ever called from the worker thread.
        """
        if self._conn is not None:
            return self._conn
        try:
            self._conn = self._connect_now()
        except ConnectionError:
            if not self._launch:
                raise
            launch_avogadro(
                self._socket_name,
                app_path=self._app_path,
                timeout=self._launch_timeout,
            )
            self._conn = self._connect_now()
        return self._conn

    def _call_sync(self, fn: Callable[[_connect.connect], Any]) -> Any:
        """Run fn(connection) on the worker thread, reconnecting once on a
        stale/closed socket.

        Caught as bare ``OSError`` rather than just ``ConnectionError``:
        writing to a socket the peer has already closed can raise either,
        depending on how the descriptor died -- e.g. a locally-closed fd
        surfaces as a plain ``OSError`` ("Bad file descriptor"), not the
        ``ConnectionError`` subclass ``connect.py`` raises for its own
        "not connected"/"closed by Avogadro" cases. ``ConnectionError`` is
        itself an ``OSError`` subclass, so this covers both.
        """
        conn = self._ensure_connected()
        try:
            return fn(conn)
        except OSError:
            # The socket may have died (Avogadro restarted, crashed, etc).
            # Drop it and retry exactly once with a fresh connection so a
            # single blip does not require restarting avogadro-mcp.
            self._conn = None
            conn = self._ensure_connected()
            return fn(conn)

    def _probe_sync(self) -> bool:
        """Best-effort liveness check; never raises. Worker thread only."""
        try:
            conn = self._ensure_connected()
            if conn.ping():
                return True
        except Exception:
            pass
        self._conn = None
        return False

    # -- public async API --------------------------------------------------

    async def _run(self, fn: Callable[[_connect.connect], Any], *, command: str) -> Any:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._call_sync, fn)
        except _connect.RPCError as exc:
            raise self._map_rpc_error(exc, command) from exc
        except OSError as exc:
            # Covers ConnectionError (connect.py's own "not connected" /
            # "closed by Avogadro" cases) and other socket-level failures
            # (e.g. a locally-closed descriptor) alike -- see _call_sync.
            raise self._map_connection_error(exc) from exc

    async def send(
        self,
        method: str,
        params: dict | None = None,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> dict:
        """
        Send a JSON-RPC request and return the full decoded response
        (``{"jsonrpc": ..., "id": ..., "result": ...}``).

        Prefer :meth:`call` when only the result payload is needed, which
        is almost always.
        """

        def _do(conn: _connect.connect):
            return conn.send(method, params, wait=wait, timeout=timeout)

        return await self._run(_do, command=method)

    async def call(
        self,
        method: str,
        params: dict | None = None,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """Send a request and return just its ``result``."""
        response = await self.send(method, params, wait=wait, timeout=timeout)
        return response.get("result")

    async def is_running(self) -> bool:
        """
        Check whether Avogadro is reachable, without raising.

        Used by ``avogadro_status``, which reports "not running" as data
        rather than failing the tool call.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._probe_sync)

    def close(self) -> None:
        """Close the socket (if open) and shut down the worker thread."""

        def _do():
            if self._conn is not None:
                self._conn.close()
                self._conn = None

        try:
            self._executor.submit(_do).result(timeout=5)
        except Exception:
            pass
        self._executor.shutdown(wait=False)

    # -- error mapping -------------------------------------------------

    def _map_rpc_error(self, exc: _connect.RPCError, command: str) -> ToolError:
        code = exc.code
        if code == _connect.RPCError.PLUGIN_BUSY:
            return ToolError(
                f"Avogadro is still working on a previous command and cannot "
                f"start '{command}' right now. Wait a moment and retry, or "
                f"call it again with wait=True and a longer timeout."
            )
        if code == _connect.RPCError.COMMAND_FAILED:
            return ToolError(f"'{command}' failed: {exc.message}")
        if code == _connect.RPCError.METHOD_NOT_FOUND:
            return ToolError(
                f"Avogadro has no command named '{command}'. Call "
                f"list_commands to see what is available."
            )
        if code == -1:
            return ToolError(f"'{command}' could not be carried out: {exc.message}")
        return ToolError(f"Avogadro RPC error {code} for '{command}': {exc.message}")

    def _map_connection_error(self, exc: OSError) -> ToolError:
        if self._launch:
            return ToolError(
                f"Could not connect to Avogadro on socket "
                f"'{self._socket_name}', even with auto-launch enabled: {exc}"
            )
        return ToolError(
            f"Avogadro is not running (no listener on socket "
            f"'{self._socket_name}'). Start Avogadro, or set "
            f"AVOGADRO_MCP_LAUNCH=1 (or run avogadro-mcp --launch) to have "
            f"avogadro-mcp start it automatically."
        )
