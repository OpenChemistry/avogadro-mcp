"""
Tests for AvogadroClient: connection handling, error-code mapping, and the
single-worker-thread concurrency guard (the main correctness risk called
out in the task -- two tool calls must never interleave on one socket).
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time

import pytest

from avogadro_mcp import _connect, client as client_module
from avogadro_mcp.client import AvogadroClient
from mcp.server.mcpserver.exceptions import ToolError
from tests._vendor.fake_rpc_server import FakeRPCServer


async def test_call_returns_result(client, fake_server):
    result = await client.call("internalPing")
    assert result == "pong"


async def test_call_with_params(client, fake_server):
    result = await client.call("moleculeInfo")
    assert result["formula"] == "C8H10N4O2"


async def test_not_running_raises_actionable_tool_error():
    c = AvogadroClient(socket_name="avogadro-mcp-test-does-not-exist")
    try:
        with pytest.raises(ToolError) as excinfo:
            await c.call("internalPing")
        message = str(excinfo.value)
        assert "not running" in message
        assert "AVOGADRO_MCP_LAUNCH" in message
    finally:
        c.close()


async def test_not_running_reports_launch_hint_when_launch_enabled():
    c = AvogadroClient(
        socket_name="avogadro-mcp-test-does-not-exist", launch=True, launch_timeout=0.5,
        app_path="/definitely/not/a/real/avogadro/binary",
    )
    try:
        with pytest.raises(ToolError) as excinfo:
            await c.call("internalPing")
        assert "auto-launch" in str(excinfo.value)
    finally:
        c.close()


async def test_is_running_true(client, fake_server):
    assert await client.is_running() is True


async def test_is_running_false_without_raising():
    c = AvogadroClient(socket_name="avogadro-mcp-test-does-not-exist")
    try:
        assert await c.is_running() is False
    finally:
        c.close()


async def test_busy_error_mapped_clearly(client, fake_server):
    fake_server.set_error("renderMO", -3, "That plugin is already running a command.")
    with pytest.raises(ToolError) as excinfo:
        await client.call("renderMO", {"orbital": "homo"}, wait=True)
    message = str(excinfo.value)
    assert "still working" in message
    assert "renderMO" in message


async def test_command_failed_error_includes_plugin_message(client, fake_server):
    fake_server.set_error("renderMO", -2, "No basis set loaded.")
    with pytest.raises(ToolError) as excinfo:
        await client.call("renderMO", {"orbital": "homo"}, wait=True)
    assert "No basis set loaded." in str(excinfo.value)


async def test_method_not_found_points_at_list_commands(client, fake_server):
    fake_server.set_error(
        "notARealCommand", -32601, "Method not found", data={"request": {}}
    )
    with pytest.raises(ToolError) as excinfo:
        await client.call("notARealCommand")
    assert "list_commands" in str(excinfo.value)


async def test_request_failed_error_carries_plugin_message(client, fake_server):
    fake_server.set_error("openFile", -1, "Failed to read file: no such file")
    with pytest.raises(ToolError) as excinfo:
        await client.call("openFile", {"fileName": "/no/such/file"})
    assert "Failed to read file" in str(excinfo.value)


async def test_reconnects_after_connection_drops(client, fake_server):
    # First call establishes the connection.
    assert await client.call("internalPing") == "pong"

    # Simulate Avogadro restarting: kill the underlying socket without
    # telling the client.
    client._conn.sock.close()

    # The next call should transparently reconnect and succeed, not raise.
    assert await client.call("internalPing") == "pong"


async def test_macos_tmpdir_fallback_finds_a_socket_env_stripped_of_tmpdir(monkeypatch):
    """
    Regression test for a real gap found in live verification: the `mcp`
    SDK's own stdio client only forwards HOME/LOGNAME/PATH/SHELL/TERM/USER
    to a spawned server on POSIX, not TMPDIR. On macOS that makes
    tempfile.gettempdir() resolve to plain "/tmp" instead of the real
    per-session directory Avogadro's socket lives in, so a perfectly
    healthy Avogadro looks "not running". AvogadroClient falls back to the
    real directory (via _paths.macos_user_temp_dir, monkeypatched here to
    avoid depending on the actual platform / `getconf` binary) when the
    normal path fails and the fallback path both differs and actually has
    the socket.
    """
    with FakeRPCServer() as server:
        # The fake server's socket lives in the real tempfile.gettempdir();
        # simulate a spawned process whose TMPDIR was stripped, so the
        # *default* lookup path misses it, the way it would on a real
        # macOS system with a naively-launched avogadro-mcp.
        real_dir = tempfile.gettempdir()
        monkeypatch.delenv("TMPDIR", raising=False)
        monkeypatch.setattr(tempfile, "tempdir", None)
        monkeypatch.setattr(client_module, "macos_user_temp_dir", lambda: real_dir)

        c = client_module.AvogadroClient(socket_name=server.name)
        try:
            assert await c.is_running() is True
            assert await c.call("internalPing") == "pong"
        finally:
            c.close()
            monkeypatch.setattr(tempfile, "tempdir", None)


async def test_macos_tmpdir_fallback_not_used_when_it_would_not_help(monkeypatch):
    """The fallback must not fire (and must not mask the real error) when
    the alternate directory is the same as the one already tried, or does
    not have the socket either -- it should behave exactly like the
    fallback did not exist."""
    monkeypatch.setattr(client_module, "macos_user_temp_dir", lambda: tempfile.gettempdir())
    c = client_module.AvogadroClient(socket_name="avogadro-mcp-test-still-not-there")
    try:
        assert await c.is_running() is False
    finally:
        c.close()


async def test_concurrent_calls_never_interleave_on_the_socket(client, fake_server, monkeypatch):
    """
    The whole point of routing every call through a single-worker executor:
    fire many tool calls concurrently from asyncio and confirm the
    underlying connect.send() is never entered by two threads at once.

    Without the executor serializing things, two coroutines racing to call
    a blocking socket method from different threads could interleave reads
    / writes on the same socket and corrupt the framing; this test would
    catch that by observing overlapping send() calls directly, which is a
    tighter check than merely "did every call get the right answer" (a
    lucky interleaving could still produce correct-looking results).
    """
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()
    orig_send = _connect.connect.send

    def tracking_send(self, *args, **kwargs):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            # Give a concurrent call a real chance to land here too, if the
            # serialization guarantee did not hold.
            time.sleep(0.02)
            return orig_send(self, *args, **kwargs)
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(_connect.connect, "send", tracking_send)

    results = await asyncio.gather(*(client.call("internalPing") for _ in range(20)))

    assert results == ["pong"] * 20
    assert state["max_active"] == 1
