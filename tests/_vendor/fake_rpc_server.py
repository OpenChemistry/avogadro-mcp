"""
Vendored copy -- do not hand-edit.

Synced from avogadrolibs `python/tests/fake_rpc_server.py`
(https://github.com/OpenChemistry/avogadrolibs), commit 5180f9172
("Add phase 1 introspection helpers to avogadro.connect"), PLUS local
additions below that commit: stateful getCamera/setCamera/zoomScene/
rotateScene/translateScene/alignView responders, added directly here for
the camera-tools usability fix (avogadro-mcp-plan.md phase 2 pulled
forward); and a canned "fetchByName" responder (§5.2-F), added for the
fetch_molecule tool. These are not yet part of the upstream avogadrolibs
fixture -- carry them forward by hand on the next re-sync until they land
there too.

avogadro-mcp's tests need to pass in a fresh clone with no avogadrolibs
checkout present, so rather than reach across a sibling checkout via
sys.path (which this module's own docstring below documents as the
intended reuse path), we keep a synced copy here instead -- the same way
avogadro_mcp/_connect.py vendors connect.py itself. It has no dependency on
`avogadro` or anything outside the standard library, so copying it verbatim
is safe.

To re-sync: diff this file against the current avogadrolibs
python/tests/fake_rpc_server.py, copy the new version in, reapply the local
additions noted above if upstream still lacks them, and update the commit
hash above.

---- original module docstring below ----

A fake Avogadro RPC server, for testing ``avogadro.connect`` (and anything
built on top of it) without a running Avogadro instance.

It speaks the real wire protocol described in
``two.avogadro.cc/source/develop/rpc.md``: a 4-byte big-endian length
prefix followed by that many bytes of UTF-8 JSON-RPC 2.0, on a Unix domain
socket named the way ``avogadro.connect.connect`` expects
(``$TMPDIR/<name>``).

Reuse from another package
---------------------------

This module has no dependency on ``avogadro`` itself -- only the standard
library -- so another project (``avogadro-mcp`` is the intended consumer,
per the MCP bridge plan) can use it as a test fixture by putting this
file's directory on ``sys.path`` (e.g. a checked-out ``avogadrolibs``
tree, ``.../avogadrolibs/python/tests``) and importing it directly::

    import sys
    sys.path.insert(0, "/path/to/avogadrolibs/python/tests")
    from fake_rpc_server import FakeRPCServer

    with FakeRPCServer() as server:
        # point a client at server.name / server.path

A pytest fixture wrapping it lives in ``conftest.py`` next to this file.

Platform limitation
--------------------

Only the Unix domain socket transport is implemented, matching Linux,
macOS and BSD. Windows Avogadro listens on a named pipe
(``\\\\.\\pipe\\<name>``) instead; a named-pipe variant of this server
would need the ``pywin32`` (or ``ctypes`` + the Win32 API) overlapped-IO
calls and is not attempted here. Tests that use this module do not run on
Windows as a result -- skip them there rather than trying to fake the
pipe transport.
"""

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import uuid
import zlib


def _png_chunk(tag, data):
    """Build one PNG chunk: length + tag + data + CRC32."""
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _make_tiny_png():
    """
    Build a minimal, valid 1x1 white PNG using only the standard library
    (struct + zlib), so render_image() tests have real image bytes to
    base64-decode and check, without shipping a binary fixture file.
    """
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit RGB
    raw = b"\x00" + b"\xff\xff\xff"  # filter-type byte + one white pixel
    idat = zlib.compress(raw)
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


#: A real (if trivial) PNG, used to answer renderImage.
TINY_PNG_BYTES = _make_tiny_png()


def _default_render_image(params):
    width = params.get("width", 1600)
    height = params.get("height", 1200)
    filename = params.get("fileName")
    if filename:
        if "." not in os.path.basename(filename):
            filename = filename + ".png"
        return {"width": width, "height": height, "fileName": filename}
    import base64

    return {
        "width": width,
        "height": height,
        "format": "png",
        "data": base64.b64encode(TINY_PNG_BYTES).decode("ascii"),
    }


def _default_get_molecule(params):
    fmt = params.get("format", "cjson")
    return {"format": fmt, "content": '{"chemical json": 1, "atoms": {}}'}


def _default_export_file(params):
    # Mirrors the real app: exportFile writes on a background thread, so
    # without wait it answers immediately with True, and with wait it
    # holds the reply for the "finished" shape carrying the results.
    if params.get("wait"):
        return {
            "status": "finished",
            "message": "Export finished",
            "data": {"fileName": params.get("fileName")},
        }
    return True


def _default_fetch_by_name(params):
    # Mirrors the real NetworkDatabases plugin: fetchByName downloads on a
    # background thread, so without wait it answers before the download
    # finishes, and with wait it holds the reply for the "finished" shape
    # carrying the resolved name/source (and, when the plugin could verify
    # the molecule was already updated, atomCount/formula -- this fixture
    # always has them). A caller wanting the empty-name/not-found error
    # paths should use set_error("fetchByName", -2, ...) instead.
    name = params.get("name", "")
    if params.get("wait"):
        return {
            "status": "finished",
            "message": "Downloaded %s" % name,
            "data": {
                "name": name,
                "source": "cactus",
                "atomCount": 24,
                "formula": "C8H10N4O2",
            },
        }
    return True


#: Canned results for every phase 1 method, per Appendix A of the MCP
#: bridge plan. Each entry is either a plain value or a callable taking
#: the request's params dict and returning the "result" payload.
DEFAULT_RESPONSES = {
    "internalPing": "pong",
    "version": {
        "avogadroApp": "1.103.0",
        "avogadroLibs": "2.0.0",
        "qt": "6.8.3",
        "platform": "macos",
        "rpcProtocol": 2,
    },
    "listCommands": [
        {
            "name": "openFile",
            "description": "Read a file from disk and make it the active molecule.",
            "kind": "builtin",
            "plugin": "",
            "async": False,
            "schema": {},
        },
        {
            "name": "renderMO",
            "description": "Render a molecular orbital.",
            "kind": "extension",
            "plugin": "Surfaces",
            "async": False,
            "schema": {},
        },
    ],
    "moleculeInfo": {
        "atomCount": 24,
        "bondCount": 25,
        "formula": "C8H10N4O2",
        "mass": 194.19,
        "totalCharge": 0,
        "spinMultiplicity": 1,
        "coordinateSetCount": 0,
        "selectedAtomCount": 0,
        "residueCount": 0,
        "hasResidues": False,
        "hasUnitCell": False,
        "hasCustomElements": False,
        "hasBasisSet": False,
        "orbitalCount": 0,
        "homoIndex": -1,
        "cubeCount": 0,
        "vibrationCount": 0,
        "fileName": "/path/to/caffeine.cml",
    },
    "getMolecule": _default_get_molecule,
    "renderImage": _default_render_image,
    "listDisplayTypes": [
        {
            "name": "BallStick",
            "displayName": "Ball and Stick",
            "enabled": True,
            "applicable": True,
            "hasSettings": True,
        },
        {
            "name": "Wireframe",
            "displayName": "Wireframe",
            "enabled": False,
            "applicable": True,
            "hasSettings": False,
        },
    ],
    "setRenderTypes": True,
    "setProjection": True,
    "openFile": True,
    "loadMolecule": True,
    "saveGraphic": True,
    "exportFile": _default_export_file,
    "fetchByName": _default_fetch_by_name,
    "kill": True,
}


class FakeRPCServer:
    """
    A background-thread Unix-socket server that answers JSON-RPC 2.0
    requests framed the way Avogadro frames them.

    Use it as a context manager, or call start()/stop() directly::

        with FakeRPCServer() as server:
            server.set_error("openFile", -1, "No such file.")
            ...

    Every request the server receives is recorded in ``self.requests``
    (the parsed JSON-RPC objects, in order) so a test can assert on what
    was sent, not just what came back.

    :param name: The socket name, i.e. the file is
        ``$TMPDIR/<name>``, matching what ``avogadro.connect.connect``
        expects for its ``name`` argument. Defaults to a random name so
        parallel test runs, and a real running Avogadro, never collide.
    :param responses: Extra or overriding canned responses, merged over
        DEFAULT_RESPONSES. Same shape: method name -> value or
        callable(params) -> value.
    """

    def __init__(self, name=None, responses=None):
        self.name = name or ("avogadro-test-%s" % uuid.uuid4().hex[:8])
        self.path = os.path.join(tempfile.gettempdir(), self.name)
        self._responses = dict(DEFAULT_RESPONSES)
        if responses:
            self._responses.update(responses)
        self._overrides = {}
        self.requests = []
        self._socket = None
        self._thread = None
        self._stop = threading.Event()

        # Per-instance camera state for getCamera/setCamera/zoomScene/
        # rotateScene/translateScene/alignView, so a test can zoom (or set a
        # camera directly) and then read the effect back via getCamera. This
        # is a simplified stand-in for Avogadro's real camera -- it tracks
        # only a scalar distance-to-focus plus the other getCamera fields,
        # and applies zoomScene's real ratio law (1/(1 - 0.02*delta)) with a
        # deliberately-tighter-than-real clamp (t capped at 0.9, vs. the
        # real Navigator::zoom clamp, which is distance-dependent) so tests
        # have a deterministic, reachable "clamped" case. setdefault, not a
        # plain assignment, so a caller can still override these via the
        # `responses` constructor argument the way any other method can.
        self._camera = {
            "distance": 12.34,
            "focus": [0.0, 0.0, 0.0],
            "projection": "perspective",
            "orthographicScale": 1.0,
        }
        self._responses.setdefault("getCamera", self._get_camera)
        self._responses.setdefault("setCamera", self._set_camera)
        self._responses.setdefault("zoomScene", self._zoom_scene)
        self._responses.setdefault("rotateScene", lambda params: True)
        self._responses.setdefault("translateScene", lambda params: True)
        self._responses.setdefault("alignView", self._align_view)

    # -- camera state (getCamera/setCamera/zoomScene/alignView) ----------

    @staticmethod
    def _model_view_for_distance(distance):
        """
        A model view matrix consistent with `distance`: identity rotation,
        translated -distance along Z, as 16 row-major floats. Not a
        faithful reproduction of Avogadro's actual camera math (see
        Camera::distance in avogadro/rendering/camera.cpp for the real
        thing) -- just enough that getCamera's "distance" and "modelView"
        fields agree with each other.
        """
        return [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, -distance,
            0.0, 0.0, 0.0, 1.0,
        ]

    def _get_camera(self, params):
        camera = dict(self._camera)
        camera["modelView"] = self._model_view_for_distance(camera["distance"])
        return camera

    def _set_camera(self, params):
        model_view = params.get("modelView")
        if model_view is not None and len(model_view) == 16:
            # Row 2, col 3 (flat index 2*4+3=11) carries -distance in the
            # simplified matrix _model_view_for_distance builds.
            self._camera["distance"] = abs(model_view[11])
        if "projection" in params:
            self._camera["projection"] = params["projection"]
        if "orthographicScale" in params:
            self._camera["orthographicScale"] = params["orthographicScale"]
        return self._get_camera(params)

    def _zoom_scene(self, params):
        delta = params.get("delta", 0)
        t = delta * 0.02  # Navigator::zoom's ZOOM_SPEED
        # Simplified stand-in for the real distance-dependent clamp: cap
        # the per-call approach well short of the delta=50 pole, so a
        # large factor/delta reliably exercises the "clamped" path in
        # tests without the real clamp's distance-dependent threshold.
        t = min(t, 0.9)
        self._camera["distance"] *= 1.0 - t
        return True

    def _align_view(self, params):
        self._camera["distance"] = 12.34
        self._camera["focus"] = [0.0, 0.0, 0.0]
        return True

    def set_result(self, method, result):
        """Answer the next (and all subsequent) calls to method with result."""
        self._overrides[method] = ("result", result)

    def set_error(self, method, code, message, data=None):
        """
        Answer calls to method with a JSON-RPC error instead of a result.

        :param code: One of the RPCError codes, e.g. -1, -2, -3, -32601.
        :param message: The error message text.
        :param data: Optional extra error data.
        """
        self._overrides[method] = ("error", (code, message, data))

    def clear_override(self, method):
        """Go back to the default canned response for method."""
        self._overrides.pop(method, None)

    def start(self):
        """Bind the socket and start answering requests in a background thread."""
        if sys.platform.startswith("win"):
            raise NotImplementedError(
                "FakeRPCServer only implements the Unix domain socket "
                "transport; there is no named-pipe variant yet."
            )
        if os.path.exists(self.path):
            os.unlink(self.path)
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(self.path)
        self._socket.listen(1)
        self._socket.settimeout(0.5)
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Stop answering requests and remove the socket file."""
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if os.path.exists(self.path):
            os.unlink(self.path)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False

    # -- internals ---------------------------------------------------

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._handle_connection(conn)
            finally:
                conn.close()

    def _handle_connection(self, conn):
        conn.settimeout(5.0)
        while not self._stop.is_set():
            header = self._recv_exactly(conn, 4)
            if header is None:
                return
            (size,) = struct.unpack(">I", header)
            body = self._recv_exactly(conn, size)
            if body is None:
                return
            request = json.loads(body.decode("utf-8"))
            self.requests.append(request)
            reply = self._build_reply(request)
            payload = json.dumps(reply).encode("utf-8")
            conn.sendall(struct.pack(">I", len(payload)) + payload)

    @staticmethod
    def _recv_exactly(conn, size):
        """Read exactly size bytes, or None if the peer closes early."""
        if size == 0:
            return b""
        chunks = []
        remaining = size
        while remaining > 0:
            try:
                chunk = conn.recv(remaining)
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _build_reply(self, request):
        method = request.get("method")
        params = request.get("params") or {}
        reply = {"jsonrpc": "2.0", "id": request.get("id")}

        if method in self._overrides:
            kind, payload = self._overrides[method]
            if kind == "error":
                code, message, data = payload
                error = {"code": code, "message": message}
                if data is not None:
                    error["data"] = data
                reply["error"] = error
            else:
                reply["result"] = payload
            return reply

        if method not in self._responses:
            reply["error"] = {
                "code": -32601,
                "message": "Method not found",
                "data": {"request": {"method": method, "params": params}},
            }
            return reply

        responder = self._responses[method]
        reply["result"] = responder(params) if callable(responder) else responder
        return reply
