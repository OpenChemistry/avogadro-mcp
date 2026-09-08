"""
Shared pytest fixtures for avogadro-mcp's tests.

Tests must pass in a fresh clone with no avogadrolibs checkout present (see
the top-level task notes / avogadro-mcp-plan.md §3.4), so rather than reach
across a sibling checkout for `python/tests/fake_rpc_server.py`, we keep a
synced copy in tests/_vendor/ -- the same vendoring approach
avogadro_mcp/_connect.py uses for connect.py itself. See
tests/_vendor/fake_rpc_server.py's header for the sync provenance and how
to re-sync it.
"""

from __future__ import annotations

import pytest

from avogadro_mcp.client import AvogadroClient
from avogadro_mcp.server import create_server
from tests._vendor.fake_rpc_server import FakeRPCServer


@pytest.fixture
def fake_server():
    """A FakeRPCServer listening on its own throwaway socket name."""
    with FakeRPCServer() as server:
        yield server


@pytest.fixture
def client(fake_server):
    """An AvogadroClient talking to fake_server. Closed after the test."""
    c = AvogadroClient(socket_name=fake_server.name)
    yield c
    c.close()


@pytest.fixture
def mcp_server(client):
    """The MCPServer built around `client`."""
    return create_server(client)
