"""
Builds the MCPServer instance: every curated tool from avogadro-mcp-plan.md
§3.2 phase 1, plus the resources from §3.3.

``create_server(client)`` is a factory rather than a module-level singleton
so tests can build a server around a client pointed at a fake RPC server
(see tests/conftest.py) without touching module state.
"""

from __future__ import annotations

import base64
import json
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from .catalogue import catalogue_as_command_list, merge_commands
from .client import AvogadroClient

_DOCS_DIR = Path(__file__).parent / "docs"

#: zoomScene's ZOOM_SPEED constant (Navigator::zoom in navigator.cpp):
#: t = delta * ZOOM_SPEED, and the camera's distance to the focus becomes
#: distance * (1 - t). Apparent size goes as 1/distance, so apparent size
#: scales as 1/(1 - ZOOM_SPEED*delta) -- a hyperbola with a pole at
#: delta = 1/ZOOM_SPEED (50). Measured against a live Avogadro: predicted
#: and measured agree within 0.5% below the clamp (see `zoom` below for the
#: clamp itself, which is distance-dependent and caps how far one call can
#: approach).
_ZOOM_SPEED = 0.02
_ZOOM_POLE_DELTA = 1.0 / _ZOOM_SPEED


def _predicted_zoom_ratio(delta: float) -> float:
    """The apparent-size ratio `delta` would produce with no clamp."""
    denominator = 1.0 - _ZOOM_SPEED * delta
    if denominator <= 0:
        return float("inf")
    return 1.0 / denominator


def _factor_to_delta(factor: float) -> float:
    """
    Convert an intuitive "make it this many times bigger" factor into the
    raw zoomScene delta. Inverts _predicted_zoom_ratio: solving
    factor = 1/(1 - 0.02*delta) for delta gives 50*(1 - 1/factor).

    Check: factor=2 -> delta=25 (matches the measured "+25 doubles size");
    factor=0.5 -> delta=-50 (matches "halving needs -50").
    """
    return _ZOOM_POLE_DELTA * (1.0 - 1.0 / factor)


#: Maps the render_surface "kind" enum onto the RPC command it sends.
SURFACE_METHODS = {
    "van_der_waals": "renderVanDerWaals",
    "solvent_accessible": "renderSolventAccessible",
    "solvent_excluded": "renderSolventExcluded",
    "molecular_orbital": "renderMO",
    "electron_density": "renderElectronDensity",
    "spin_density": "renderSpinDensity",
    "cube": "renderCube",
}


def _package_version() -> str:
    try:
        return metadata.version("avogadro-mcp")
    except metadata.PackageNotFoundError:
        return "0.0.0+dev"


def _abs_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _clarify_write_error(exc: ToolError, resolved_path: str) -> ToolError:
    """
    Add a hint to a write-failure ToolError, distinguishing "the parent
    directory does not exist on this host" -- almost always a client/host
    filesystem mismatch, since paths are resolved on the machine running
    avogadro-mcp -- from "the directory exists but the write still failed"
    (permissions, disk space, a locked file, ...), which is not that.

    Only called for failures from a call that carried a resolved path (the
    C++ side reports both cases with the same generic message, so this is
    the only place with enough information to tell them apart), and never
    changes the underlying error code -- just appends context.
    """
    parent = Path(resolved_path).parent
    if not parent.is_dir():
        hint = (
            f" The directory '{parent}' does not exist on the machine "
            "running the avogadro-mcp server (and Avogadro). Paths are "
            "resolved on that host, not on whichever machine sent this "
            "tool call -- if this looks like a path from a sandboxed or "
            "remote client, that is almost certainly the problem."
        )
    else:
        hint = (
            f" The directory '{parent}' exists on the server host, so "
            "this is not a path mismatch -- check permissions, free disk "
            "space, or whether the file is open elsewhere."
        )
    return ToolError(str(exc) + hint)


def create_server(client: AvogadroClient) -> MCPServer:
    """Build and return a fully-configured MCPServer bound to `client`."""

    mcp = MCPServer(
        name="avogadro",
        version=_package_version(),
        instructions=(
            "Drive a running Avogadro molecular editor: open and inspect "
            "molecules and crystals, change the view and rendering, render "
            "isosurfaces and molecular orbitals, and take screenshots. "
            "Avogadro must already be running -- call avogadro_status "
            "first. Auto-launch is off by default; see its message for how "
            "to turn it on."
        ),
    )

    # -- tools ---------------------------------------------------------

    @mcp.tool()
    async def avogadro_status() -> dict:
        """
        Check whether Avogadro is running, and if so, report its versions
        and what molecule is currently loaded.

        Call this first, before any other tool. Unlike the other tools,
        this one never fails just because Avogadro is not running -- it
        reports `"running": false` with guidance instead, so a model can
        decide what to do next rather than getting a bare error.
        """
        running = await client.is_running()
        if not running:
            return {
                "running": False,
                "socket": client.socket_name,
                "message": (
                    "Avogadro is not running (no listener on socket "
                    f"'{client.socket_name}'). Start Avogadro, or set "
                    "AVOGADRO_MCP_LAUNCH=1 (or run avogadro-mcp --launch) "
                    "to have it started automatically."
                ),
            }
        version = await client.call("version")
        info = await client.call("moleculeInfo")
        return {"running": True, "version": version, "molecule": info}

    @mcp.tool()
    async def open_file(path: str) -> dict:
        """
        Open a molecule or crystal structure file from disk and make it the
        active molecule, replacing whatever was loaded before. The format
        is inferred from the file extension (e.g. .cml, .xyz, .pdb, .sdf,
        POSCAR, .cjson).

        :param path: Path to the file. Resolved to an absolute path before
            sending, so relative paths are read relative to wherever the
            avogadro-mcp server process runs, not Avogadro's own working
            directory.
        """
        resolved = _abs_path(path)
        await client.call("openFile", {"fileName": resolved})
        return {"opened": resolved}

    @mcp.tool()
    async def load_molecule(content: str, format: str = "cjson") -> dict:
        """
        Load molecular data directly from a string, without writing a file
        to disk, and make it the active molecule.

        :param content: The molecule data, e.g. the text of an XYZ or
            Chemical JSON document.
        :param format: The format `content` is written in -- any format
            Avogadro can read (e.g. "xyz", "cml", "pdb", "sdf", "cjson").
        """
        await client.call("loadMolecule", {"content": content, "format": format})
        return {"loaded": True, "format": format}

    @mcp.tool()
    async def get_molecule(format: str = "cjson") -> dict:
        """
        Read the active molecule back as text, without writing a file to
        disk. Use this to hand the model the actual structure (atoms,
        bonds, coordinates) rather than just a summary.

        :param format: Any format Avogadro can write (e.g. "cjson", "xyz",
            "cml", "pdb", "sdf"). Defaults to Chemical JSON, which is the
            most complete for round-tripping back into Avogadro.
        """
        return await client.call("getMolecule", {"format": format})

    @mcp.tool()
    async def molecule_info() -> dict:
        """
        Summarize the active molecule: atom/bond counts, formula, mass,
        total charge, spin multiplicity, and what it has -- a unit cell, a
        basis set with orbitals (and the HOMO index), vibrations, cubes,
        multiple coordinate sets, residues, a current atom selection.

        All keys are always present, even with nothing open (counts are 0,
        strings empty), so this is safe to call just to check "what is
        loaded right now?"
        """
        return await client.call("moleculeInfo")

    @mcp.tool()
    async def export_file(path: str, wait: bool = True) -> dict:
        """
        Write the active molecule to a file. The format is inferred from
        the file extension.

        :param path: Destination path. Resolved to an absolute path before
            sending.
        :param wait: If True (the default), do not return until the write
            has actually finished. The write runs on a background thread
            in Avogadro, so with wait=False this call can return before
            the file exists on disk -- only turn it off if the caller does
            not need to read the file back immediately afterward.
        """
        resolved = _abs_path(path)
        result = await client.call("exportFile", {"fileName": resolved}, wait=wait)
        if wait:
            data = result.get("data", {}) if isinstance(result, dict) else {}
            return {"fileName": data.get("fileName", resolved)}
        return {"started": resolved}

    @mcp.tool()
    async def screenshot(
        width: int | None = None,
        height: int | None = None,
        transparent: bool = False,
        path: str | None = None,
    ) -> list:
        """
        Render the current 3D view as a PNG image.

        With no `path`, the image is returned inline for the model to see
        directly. With `path`, it is written to disk instead and the tool
        reports the file written.

        `width` and `height` must be given together, or not at all --
        omitting both renders at native resolution, the maximum quality
        available. The returned summary's `width`/`height` are the image's
        true size (never just an echo of the request); compare against
        `nativeWidth`/`nativeHeight` (the size of the underlying view grab,
        e.g. 2x on a Retina/HiDPI display) to tell whether the request
        exceeded what the view could actually provide -- if so, the image
        was upscaled rather than rendered at that true resolution.

        :param width: Requested image width in pixels (1-8192).
        :param height: Requested image height in pixels (1-8192).
        :param transparent: Render with a transparent background instead of
            the scene's current background colour.
        :param path: If given, write the PNG here instead of returning it
            inline. ".png" is appended if the name has no extension.
            Resolved to an absolute path before sending, so it is read and
            written on whatever machine is running the avogadro-mcp server
            (and Avogadro) -- not on the machine that sent this tool call.
            In a sandboxed or remote client, a path that looks valid there
            (e.g. "/mnt/user-data/outputs/...") will not exist on the
            server host; omit `path` and use the inline image instead if
            you cannot be sure the two machines share a filesystem.
        """
        if (width is None) != (height is None):
            raise ToolError(
                "screenshot needs both width and height together, or "
                "neither (to use the native view size)."
            )

        params: dict[str, Any] = {}
        if width is not None:
            params["width"] = width
            params["height"] = height
        if transparent:
            params["transparentBackground"] = True

        resolved_path = _abs_path(path) if path is not None else None
        if resolved_path is not None:
            params["fileName"] = resolved_path

        try:
            result = await client.call("renderImage", params)
        except ToolError as exc:
            if resolved_path is not None:
                raise _clarify_write_error(exc, resolved_path) from exc
            raise
        summary = {
            "width": result.get("width"),
            "height": result.get("height"),
            "nativeWidth": result.get("nativeWidth"),
            "nativeHeight": result.get("nativeHeight"),
        }

        if resolved_path is not None:
            summary["fileName"] = result.get("fileName", resolved_path)
            return [json.dumps(summary)]

        png_bytes = base64.b64decode(result["data"])
        return [json.dumps(summary), Image(data=png_bytes, format="png")]

    @mcp.tool()
    async def list_display_types() -> str:
        """
        List the scene display types available for the active view (Ball
        and Stick, Wireframe, Van der Waals, Cartoons, ...): the stable
        identifier to pass to set_display, whether each is currently on,
        whether it applies to the current molecule (e.g. a crystal-only
        type needs a unit cell), and whether it has settings of its own
        (not yet reachable over this API).
        """
        # Returned as one JSON string rather than the raw list: MCPServer's
        # content conversion unrolls a returned list one item at a time
        # (each dict becoming its own text block), which would hand the
        # model N separate blocks instead of one JSON array.
        types = await client.call("listDisplayTypes")
        return json.dumps(types, indent=2)

    @mcp.tool()
    async def set_display(
        enable: list[str] | None = None,
        disable: list[str] | None = None,
    ) -> dict:
        """
        Turn scene display types on and/or off, e.g.
        enable=["VanDerWaals"], disable=["BallStick"]. Names are the
        stable identifiers from list_display_types (e.g. "BallStick",
        "VanDerWaals", "Wireframe", "Label", "Cartoons", "SurfaceRender"),
        not the translated display name.

        :param enable: Display types to turn on.
        :param disable: Display types to turn off.
        """
        if not enable and not disable:
            raise ToolError("set_display needs at least one of enable or disable.")
        if disable:
            params: dict[str, Any] = {name: True for name in (enable or [])}
            params.update({name: False for name in disable})
        else:
            params = {"types": list(enable)}
        await client.call("setRenderTypes", params)
        return {"enabled": enable or [], "disabled": disable or []}

    @mcp.tool()
    async def set_projection(kind: Literal["perspective", "orthographic"]) -> dict:
        """Switch the camera projection between perspective and orthographic."""
        await client.call("setProjection", {"type": kind})
        return {"projection": kind}

    @mcp.tool()
    async def rotate(x: float = 0, y: float = 0, z: float = 0) -> dict:
        """
        Rotate the camera about the screen x, y, and z axes, in degrees,
        through the current focal point.

        Returns the resulting camera state (the same shape as get_camera)
        rather than an echo of x/y/z, so a caller can converge on a
        framing from the reply alone, without a screenshot after every
        call.
        """
        await client.call("rotateScene", {"x": x, "y": y, "z": z})
        return await client.call("getCamera")

    @mcp.tool()
    async def zoom(delta: float | None = None, factor: float | None = None) -> dict:
        """
        Zoom the camera -- move it toward or away from the current focal
        point -- and report the resulting distance, so a caller can see
        the actual effect instead of screenshotting after every attempt.

        Give exactly one of `factor` or `delta`:

        :param factor: An intuitive multiplicative target: "make the
            molecule this many times bigger on screen". factor=2.0
            doubles apparent size, factor=0.5 halves it. Converted to
            `delta` as 50 * (1 - 1/factor). Must be greater than 0.
        :param delta: Avogadro's raw zoom control. Positive moves toward
            the molecule, negative away. The real law (measured against
            a live Avogadro): apparent size scales as
            1/(1 - 0.02*delta) -- a hyperbola with a pole at delta=50,
            where the camera would pass through the focal point. This
            makes the scale asymmetric: +25 doubles apparent size, but
            halving it takes -50. A clamp inside Avogadro
            (Navigator::zoom) caps how far a single call can approach
            the focus, so a large delta -- or a large `factor`, which is
            converted to one -- can silently under-deliver; see
            `achieved`/`clamped` below rather than guessing. Repeated
            calls compound multiplicatively: prefer a couple of moderate
            calls over one extreme one.

        The reply carries the post-zoom camera state (`distance`,
        `projection`, etc, from get_camera) plus:

        * `achieved`: the actual multiplicative change in apparent size,
          measured from the camera's distance before and after this
          call -- compare against `factor` (or the ratio `delta`
          implies) to see whether the move landed.
        * `clamped`: true if `achieved` fell meaningfully short of what
          was requested -- e.g. a request for factor=5 that only
          achieved ~2x. When true, call zoom again rather than
          repeating the same oversized request.
        """
        if (delta is None) == (factor is None):
            raise ToolError(
                "zoom needs exactly one of 'delta' or 'factor', not both "
                "and not neither."
            )

        if factor is not None:
            if factor <= 0:
                raise ToolError(f"zoom's factor must be greater than 0, got {factor}.")
            delta = _factor_to_delta(factor)

        before = await client.call("getCamera")
        distance_before = before.get("distance")

        await client.call("zoomScene", {"delta": delta})

        after = await client.call("getCamera")
        distance_after = after.get("distance")

        result: dict[str, Any] = dict(after)
        result["delta"] = delta
        if factor is not None:
            result["factor"] = factor

        requested_ratio = factor if factor is not None else _predicted_zoom_ratio(delta)
        if distance_before and distance_after:
            achieved_ratio = distance_before / distance_after
            result["achieved"] = achieved_ratio
            # 2% slack for floating-point/measurement noise around the
            # clamp boundary, so an unclamped move never reads as clamped.
            result["clamped"] = achieved_ratio < requested_ratio * 0.98

        return result

    @mcp.tool()
    async def translate(x: float = 0, y: float = 0) -> dict:
        """
        Pan the camera by a distance given in screen pixels.

        Returns the resulting camera state (the same shape as
        get_camera), not an echo of x/y.
        """
        await client.call("translateScene", {"x": x, "y": y})
        return await client.call("getCamera")

    @mcp.tool()
    async def reset_view() -> dict:
        """
        Align the view to the x, y, and z axes, centered on the molecule
        and backed off far enough to frame it. The equivalent of pressing
        the "reset view" toolbar button.

        Returns the resulting camera state (the same shape as
        get_camera).
        """
        await client.call("alignView")
        return await client.call("getCamera")

    @mcp.tool()
    async def get_camera() -> dict:
        """
        Report the active view's camera: distance to the focal point, the
        focus point, the projection mode, the orthographic scale, and the
        full model view matrix.

        `modelView` is 16 floats in row-major order: element
        `[row * 4 + col]` is `modelView[row][col]`.

        Save the result and hand it back to set_camera to restore this
        exact view later -- for a reproducible figure, or a turntable
        movie built from repeated rotate calls each followed by a
        screenshot.
        """
        return await client.call("getCamera")

    @mcp.tool()
    async def set_camera(
        model_view: list[float] | None = None,
        projection: Literal["perspective", "orthographic"] | None = None,
        orthographic_scale: float | None = None,
    ) -> dict:
        """
        Apply a saved camera view. Give any combination of the three
        parameters -- only what is given is changed.

        :param model_view: The 16-float row-major model view matrix from
            a previous get_camera call (element `[row * 4 + col]` is
            `modelView[row][col]`). Must be exactly 16 numbers.
        :param projection: Switch between perspective and orthographic.
        :param orthographic_scale: The orthographic zoom factor (only
            has a visible effect in orthographic projection).

        Returns the resulting camera state (the same shape as
        get_camera).
        """
        if model_view is None and projection is None and orthographic_scale is None:
            raise ToolError(
                "set_camera needs at least one of model_view, projection, "
                "or orthographic_scale."
            )

        params: dict[str, Any] = {}
        if model_view is not None:
            params["modelView"] = model_view
        if projection is not None:
            params["projection"] = projection
        if orthographic_scale is not None:
            params["orthographicScale"] = orthographic_scale

        return await client.call("setCamera", params)

    @mcp.tool()
    async def render_surface(
        kind: Literal[
            "van_der_waals",
            "solvent_accessible",
            "solvent_excluded",
            "molecular_orbital",
            "electron_density",
            "spin_density",
            "cube",
        ],
        orbital: str | int | None = None,
        isovalue: float | None = None,
        resolution: float | None = None,
        spin: Literal["alpha", "beta"] | None = None,
        index: int | None = None,
        timeout: float = 60.0,
    ) -> dict:
        """
        Render an isosurface: a van der Waals / solvent-accessible /
        solvent-excluded surface, a molecular orbital, the electron or
        spin density, or a cube that came with the file.

        This always waits for the render to finish -- it runs in the
        background inside Avogadro -- so a screenshot taken right after
        this call will show the surface. Increase `timeout` for a large
        molecule or a fine resolution; if Avogadro reports it is still
        busy, wait and call again rather than retrying immediately.

        :param kind: Which surface to render.
        :param orbital: Only for kind="molecular_orbital": a number
            counting from 1 (the lowest orbital), or a name relative to
            the frontier orbitals: "homo", "lumo", "homo-1", "lumo+2".
        :param isovalue: The isosurface value. Avogadro defaults this to
            0.03 for the kinds that use it (molecular_orbital,
            electron_density, spin_density, cube).
        :param resolution: Cube spacing in angstroms. Avogadro picks a
            value from the molecule's size (0.05-0.5 A) when omitted.
        :param spin: "beta" for the beta orbitals/density of an open-shell
            calculation; alpha is used otherwise. Only meaningful for
            molecular_orbital, electron_density, spin_density.
        :param index: Only for kind="cube": which cube to render, counting
            from 1 (defaults to the first cube that came with the file).
        :param timeout: Seconds to allow the render before giving up.
        """
        method = SURFACE_METHODS[kind]
        params: dict[str, Any] = {}
        if orbital is not None:
            params["orbital"] = orbital
        if isovalue is not None:
            params["isovalue"] = isovalue
        if resolution is not None:
            params["resolution"] = resolution
        if spin is not None:
            params["spin"] = spin
        if index is not None:
            params["index"] = index

        result = await client.call(method, params, wait=True, timeout=timeout)
        message = None
        data: dict[str, Any] = {}
        if isinstance(result, dict):
            message = result.get("message")
            data = result.get("data") or {}
        return {"kind": kind, "message": message, **data}

    @mcp.tool()
    async def list_commands() -> str:
        """
        List every command Avogadro will answer -- built-ins and
        plugin-registered commands alike -- with a JSON Schema for its
        parameters where one is known (from this server's bundled
        catalogue, or eventually from Avogadro itself). The curated tools
        in this server cover the common cases; use run_command to reach
        anything else, including commands a third-party plugin adds that
        has no curated tool yet.
        """
        # See list_display_types for why this is a JSON string, not a list.
        live = await client.call("listCommands")
        return json.dumps(merge_commands(live), indent=2)

    @mcp.tool()
    async def run_command(
        name: str,
        params: dict | None = None,
        wait: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """
        Run any command Avogadro understands by name. This is the escape
        hatch for anything without a curated tool above, including
        commands registered by third-party plugins. Call list_commands
        first to see what is available and what parameters it takes.

        :param name: The JSON-RPC method / command name, e.g.
            "createBonds", "centerAtom", or "setVibrationalMode".
        :param params: Parameters for the command, as a JSON object, e.g.
            {"index": 0, "axis": "z"}.
        :param wait: If True, do not return until the command has finished
            -- needed for commands that run in the background (e.g.
            surface rendering); the reply then carries whatever the
            command reported. Prefer render_surface for the surface
            commands, which always sets this correctly.
        :param timeout: Seconds to allow the command when wait=True.
            Defaults to 60.
        """
        return await client.call(name, params, wait=wait, timeout=timeout)

    # -- resources -------------------------------------------------------

    @mcp.resource(
        "avogadro://commands",
        name="commands",
        title="Avogadro commands",
        description=(
            "Every command Avogadro understands, as JSON Schema entries. "
            "Live from a running Avogadro (merged with the bundled "
            "catalogue) when it is running, else the catalogue alone."
        ),
        mime_type="application/json",
    )
    async def commands_resource() -> str:
        if await client.is_running():
            live = await client.call("listCommands")
            merged = merge_commands(live)
        else:
            merged = catalogue_as_command_list()
        return json.dumps(merged, indent=2)

    @mcp.resource(
        "avogadro://molecule",
        name="molecule",
        title="Active molecule (CJSON)",
        description="The active molecule, serialized as Chemical JSON.",
        mime_type="application/json",
    )
    async def molecule_resource() -> str:
        try:
            result = await client.call("getMolecule", {"format": "cjson"})
        except ToolError as exc:
            raise ResourceError(str(exc)) from exc
        return result["content"]

    @mcp.resource(
        "avogadro://molecule/info",
        name="molecule-info",
        title="Active molecule summary",
        description="moleculeInfo: counts, formula, and what the active molecule has.",
        mime_type="application/json",
    )
    async def molecule_info_resource() -> str:
        try:
            info = await client.call("moleculeInfo")
        except ToolError as exc:
            raise ResourceError(str(exc)) from exc
        return json.dumps(info, indent=2)

    @mcp.resource(
        "avogadro://docs/scripting",
        name="scripting-docs",
        title="Scripting Avogadro from Python",
        description=(
            "How to drive Avogadro from a standalone Python script: the "
            "connection, the wait rule, error codes, the busy rule, "
            "read-back reply shapes."
        ),
        mime_type="text/markdown",
    )
    def scripting_doc() -> str:
        return (_DOCS_DIR / "scripting.md").read_text(encoding="utf-8")

    @mcp.resource(
        "avogadro://docs/commands",
        name="commands-docs",
        title="Avogadro RPC command reference",
        description="The built-in and plugin command tables from rpc.md, as prose.",
        mime_type="text/markdown",
    )
    def commands_doc() -> str:
        return (_DOCS_DIR / "commands.md").read_text(encoding="utf-8")

    return mcp
