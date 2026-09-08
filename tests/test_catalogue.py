"""Tests for catalogue.json loading and the listCommands overlay."""

from __future__ import annotations

from avogadro_mcp.catalogue import (
    catalogue_as_command_list,
    load_catalogue,
    merge_commands,
)


def test_load_catalogue_has_every_documented_command():
    catalogue = load_catalogue()
    # 16 builtins (14 + getCamera/setCamera) + 28 plugin commands documented
    # in rpc.md (§4.2 of the plan).
    assert len(catalogue) == 44
    assert "_comment" not in catalogue
    assert catalogue["openFile"]["schema"]["required"] == ["fileName"]
    assert catalogue["renderMO"]["async"] is True


def test_catalogue_as_command_list_is_sorted_and_marked():
    commands = catalogue_as_command_list()
    names = [c["name"] for c in commands]
    assert names == sorted(names)
    assert all(c["source"] == "catalogue" for c in commands)


def test_merge_fills_empty_live_schema_from_catalogue():
    live = [
        {
            "name": "openFile",
            "description": "Read a file from disk and make it the active molecule.",
            "kind": "builtin",
            "plugin": "",
            "async": False,
            "schema": {},
        }
    ]
    merged = merge_commands(live)
    assert merged[0]["schema"]["required"] == ["fileName"]
    assert merged[0]["source"] == "catalogue"


def test_merge_leaves_a_real_app_schema_alone():
    live = [
        {
            "name": "openFile",
            "description": "app description",
            "kind": "builtin",
            "plugin": "",
            "async": False,
            "schema": {"type": "object", "properties": {"fileName": {"type": "string"}}},
        }
    ]
    merged = merge_commands(live)
    assert merged[0]["source"] == "app"
    assert merged[0]["description"] == "app description"


def test_merge_leaves_unknown_commands_untouched():
    live = [
        {
            "name": "someThirdPartyCommand",
            "description": "from a plugin not in the catalogue",
            "kind": "extension",
            "plugin": "SomePlugin",
            "async": False,
            "schema": {},
        }
    ]
    merged = merge_commands(live)
    assert merged[0]["schema"] == {}
    assert merged[0]["source"] == "app"
