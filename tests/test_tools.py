"""
Exercises every curated tool through the real MCP protocol layer, using
mcp's in-process Client/MCPServer pairing against a fake Avogadro RPC
server -- so this also validates that each tool's declared schema accepts
the arguments we send, not just that the underlying implementation works.

Each test opens its own short-lived Client(mcp_server) rather than sharing
one across tests via a fixture: an async generator fixture that enters and
exits an `async with Client(...)` block ends up straddling two different
anyio tasks under pytest-asyncio (the fixture's task and the test's), which
anyio's cancel-scope bookkeeping rejects. Opening the client inside the
test's own coroutine avoids that entirely, at the cost of one extra
initialize handshake per call -- cheap against a fake in-process server.
"""

from __future__ import annotations

import base64
import json

from mcp import Client

from tests._vendor.fake_rpc_server import TINY_PNG_BYTES


async def call_tool(mcp_server, name, args=None):
    async with Client(mcp_server) as c:
        return await c.call_tool(name, args or {})


async def read_resource(mcp_server, uri):
    async with Client(mcp_server) as c:
        return await c.read_resource(uri)


def _text(result):
    """Concatenate all TextContent blocks in a CallToolResult."""
    return "".join(
        block.text for block in result.content if getattr(block, "type", None) == "text"
    )


async def test_avogadro_status_running(mcp_server, fake_server):
    result = await call_tool(mcp_server, "avogadro_status")
    assert not result.is_error
    payload = json.loads(_text(result))
    assert payload["running"] is True
    assert payload["version"]["rpcProtocol"] == 2
    assert payload["molecule"]["formula"] == "C8H10N4O2"


async def test_open_file_resolves_absolute_path(mcp_server, fake_server, tmp_path):
    target = tmp_path / "water.xyz"
    target.write_text("3\n\nO 0 0 0\nH 0 0 1\nH 0 1 0\n")

    result = await call_tool(mcp_server, "open_file", {"path": str(target)})
    assert not result.is_error

    sent = fake_server.requests[-1]
    assert sent["method"] == "openFile"
    assert sent["params"]["fileName"] == str(target.resolve())


async def test_load_molecule(mcp_server, fake_server):
    result = await call_tool(
        mcp_server,
        "load_molecule",
        {"content": "3\n\nO 0 0 0\nH 0 0 1\nH 0 1 0\n", "format": "xyz"},
    )
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["method"] == "loadMolecule"
    assert sent["params"]["format"] == "xyz"


async def test_get_molecule_default_cjson(mcp_server, fake_server):
    result = await call_tool(mcp_server, "get_molecule")
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["params"].get("format", "cjson") == "cjson"


async def test_molecule_info(mcp_server, fake_server):
    result = await call_tool(mcp_server, "molecule_info")
    payload = json.loads(_text(result))
    assert payload["atomCount"] == 24


async def test_export_file_waits_by_default(mcp_server, fake_server, tmp_path):
    target = tmp_path / "out.xyz"
    result = await call_tool(mcp_server, "export_file", {"path": str(target)})
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["params"]["wait"] is True


async def test_screenshot_inline_returns_image_content(mcp_server, fake_server):
    result = await call_tool(mcp_server, "screenshot", {"width": 400, "height": 300})
    assert not result.is_error
    kinds = [block.type for block in result.content]
    assert "image" in kinds
    image_block = next(b for b in result.content if b.type == "image")
    assert base64.b64decode(image_block.data) == TINY_PNG_BYTES

    text_block = next(b for b in result.content if b.type == "text")
    summary = json.loads(text_block.text)
    assert summary["width"] == 400
    assert summary["height"] == 300


async def test_screenshot_requires_both_dimensions(mcp_server, fake_server):
    result = await call_tool(mcp_server, "screenshot", {"width": 400})
    assert result.is_error
    assert "width and height" in _text(result)


async def test_screenshot_to_path(mcp_server, fake_server, tmp_path):
    target = tmp_path / "shot.png"
    result = await call_tool(mcp_server, "screenshot", {"path": str(target)})
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["params"]["fileName"] == str(target.resolve())
    kinds = [block.type for block in result.content]
    assert "image" not in kinds


async def test_screenshot_write_error_flags_missing_parent_dir(
    mcp_server, fake_server, tmp_path
):
    target = tmp_path / "no-such-subdir" / "shot.png"
    fake_server.set_error(
        "renderImage", -1, f"Could not write image to {target.resolve()}."
    )
    result = await call_tool(mcp_server, "screenshot", {"path": str(target)})
    assert result.is_error
    message = _text(result)
    assert "does not exist" in message
    assert "not on this host" not in message  # sanity: only one hint fires


async def test_screenshot_write_error_with_existing_parent_dir(
    mcp_server, fake_server, tmp_path
):
    target = tmp_path / "shot.png"  # tmp_path itself exists
    fake_server.set_error(
        "renderImage", -1, f"Could not write image to {target.resolve()}."
    )
    result = await call_tool(mcp_server, "screenshot", {"path": str(target)})
    assert result.is_error
    message = _text(result)
    assert "not a path mismatch" in message


async def test_list_display_types(mcp_server, fake_server):
    result = await call_tool(mcp_server, "list_display_types")
    payload = json.loads(_text(result))
    names = [d["name"] for d in payload]
    assert "BallStick" in names


async def test_set_display_enable_only(mcp_server, fake_server):
    result = await call_tool(mcp_server, "set_display", {"enable": ["VanDerWaals"]})
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["params"] == {"types": ["VanDerWaals"]}


async def test_set_display_enable_and_disable(mcp_server, fake_server):
    result = await call_tool(
        mcp_server, "set_display", {"enable": ["VanDerWaals"], "disable": ["BallStick"]}
    )
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["params"] == {"VanDerWaals": True, "BallStick": False}


async def test_set_display_requires_an_argument(mcp_server, fake_server):
    result = await call_tool(mcp_server, "set_display")
    assert result.is_error


async def test_set_projection(mcp_server, fake_server):
    result = await call_tool(mcp_server, "set_projection", {"kind": "orthographic"})
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["params"] == {"type": "orthographic"}


async def test_set_projection_rejects_bad_enum(mcp_server, fake_server):
    result = await call_tool(mcp_server, "set_projection", {"kind": "isometric"})
    assert result.is_error


async def test_rotate_zoom_translate_reset(mcp_server, fake_server):
    await call_tool(mcp_server, "rotate", {"x": 10, "y": 0, "z": 0})
    assert fake_server.requests[-1]["method"] == "rotateScene"
    assert fake_server.requests[-1]["params"] == {"x": 10, "y": 0, "z": 0}

    await call_tool(mcp_server, "zoom", {"delta": 5})
    assert fake_server.requests[-1]["method"] == "zoomScene"
    assert fake_server.requests[-1]["params"] == {"delta": 5}

    await call_tool(mcp_server, "translate", {"x": 1, "y": 2})
    assert fake_server.requests[-1]["method"] == "translateScene"

    await call_tool(mcp_server, "reset_view")
    assert fake_server.requests[-1]["method"] == "alignView"


async def test_render_surface_always_waits(mcp_server, fake_server):
    fake_server.set_result(
        "renderMO",
        {
            "status": "finished",
            "message": "Surface Finished",
            "data": {"cubeIndex": 1, "isovalue": 0.02, "resolution": 0.1},
        },
    )
    result = await call_tool(
        mcp_server,
        "render_surface",
        {"kind": "molecular_orbital", "orbital": "homo", "isovalue": 0.02},
    )
    assert not result.is_error
    sent = fake_server.requests[-1]
    assert sent["method"] == "renderMO"
    assert sent["params"]["wait"] is True
    payload = json.loads(_text(result))
    assert payload["cubeIndex"] == 1
    assert payload["message"] == "Surface Finished"


async def test_render_surface_reports_busy_clearly(mcp_server, fake_server):
    fake_server.set_error(
        "renderVanDerWaals", -3, "That plugin is already running a command."
    )
    result = await call_tool(mcp_server, "render_surface", {"kind": "van_der_waals"})
    assert result.is_error
    assert "still working" in _text(result)


async def test_list_commands_merges_catalogue(mcp_server, fake_server):
    result = await call_tool(mcp_server, "list_commands")
    payload = json.loads(_text(result))
    by_name = {c["name"]: c for c in payload}
    # openFile comes back from the fake server with an empty schema; the
    # catalogue should have filled it in.
    assert by_name["openFile"]["schema"]["properties"]["fileName"]["type"] == "string"
    assert by_name["openFile"]["source"] == "catalogue"


async def test_run_command_is_the_escape_hatch(mcp_server, fake_server):
    fake_server.set_result("createBonds", True)
    result = await call_tool(mcp_server, "run_command", {"name": "createBonds"})
    assert not result.is_error
    assert fake_server.requests[-1]["method"] == "createBonds"


async def test_run_command_unknown_method(mcp_server, fake_server):
    fake_server.set_error("notAThing", -32601, "Method not found", data={"request": {}})
    result = await call_tool(mcp_server, "run_command", {"name": "notAThing"})
    assert result.is_error
    assert "list_commands" in _text(result)


async def test_resource_molecule_cjson(mcp_server, fake_server):
    res = await read_resource(mcp_server, "avogadro://molecule")
    content = json.loads(res.contents[0].text)
    assert content["chemical json"] == 1


async def test_resource_molecule_info(mcp_server, fake_server):
    res = await read_resource(mcp_server, "avogadro://molecule/info")
    content = json.loads(res.contents[0].text)
    assert content["atomCount"] == 24


async def test_resource_commands_live(mcp_server, fake_server):
    res = await read_resource(mcp_server, "avogadro://commands")
    content = json.loads(res.contents[0].text)
    names = {c["name"] for c in content}
    assert "openFile" in names
    assert "renderMO" in names


async def test_resource_docs_scripting_and_commands(mcp_server, fake_server):
    scripting = await read_resource(mcp_server, "avogadro://docs/scripting")
    assert "wait" in scripting.contents[0].text

    commands = await read_resource(mcp_server, "avogadro://docs/commands")
    assert "renderVanDerWaals" in commands.contents[0].text
