"""
Loading and overlaying the bundled command catalogue (§4.2 of the plan).

``listCommands`` on a live Avogadro reports ``schema: {}`` for every command
today (phase 1 of the RPC introspection work; see rpclistener.cpp). This
module overlays the bundled ``catalogue.json`` -- hand-written JSON Schema
for every command documented in rpc.md -- onto that live list, so
``list_commands()`` and the ``avogadro://commands`` resource have real
parameter types now, without waiting on ``commandSchema()`` (phase 3).

Each catalogue-sourced entry is marked ``"source": "catalogue"``; an entry
whose live schema is already non-empty (a future app that implements
``commandSchema()``) is left alone and marked ``"source": "app"``, so the
app's own schema always wins when it exists.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_catalogue() -> dict[str, dict[str, Any]]:
    """Load catalogue.json, bundled inside the package, as a dict keyed by
    command name."""
    text = resources.files("avogadro_mcp").joinpath("catalogue.json").read_text(
        encoding="utf-8"
    )
    data = json.loads(text)
    data.pop("_comment", None)
    return data


def catalogue_as_command_list(
    catalogue: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Render the catalogue in the same shape ``listCommands`` uses, sorted by
    name. Used as the fallback for the ``avogadro://commands`` resource and
    ``list_commands()`` when Avogadro is not running.
    """
    if catalogue is None:
        catalogue = load_catalogue()
    commands = []
    for name, entry in catalogue.items():
        commands.append(
            {
                "name": name,
                "description": entry.get("description", ""),
                "kind": entry.get("kind", ""),
                "plugin": entry.get("plugin", ""),
                "async": bool(entry.get("async", False)),
                "schema": entry.get("schema", {}),
                "source": "catalogue",
            }
        )
    commands.sort(key=lambda c: c["name"])
    return commands


def merge_commands(
    live_commands: list[dict[str, Any]],
    catalogue: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Overlay the bundled catalogue onto a live ``listCommands`` result.

    :param live_commands: The list ``listCommands`` returned.
    :param catalogue: Pre-loaded catalogue, or ``None`` to load it fresh.
    :returns: A new list, same order as ``live_commands``, each entry
        carrying a real ``schema`` when the catalogue has one and the app
        did not already supply one, plus a ``source`` key ("app" or
        "catalogue").
    """
    if catalogue is None:
        catalogue = load_catalogue()

    merged = []
    for entry in live_commands:
        entry = dict(entry)
        name = entry.get("name")
        app_schema = entry.get("schema") or {}
        cat_entry = catalogue.get(name)

        if not app_schema and cat_entry is not None:
            entry["schema"] = cat_entry.get("schema", {})
            entry["source"] = "catalogue"
            if cat_entry.get("async") and not entry.get("async"):
                entry["async"] = True
        else:
            entry.setdefault("source", "app")

        merged.append(entry)
    return merged
