"""
Opt-in auto-launch of Avogadro.

Auto-launch is off by default (see client.py / cli.py) -- this module only
implements the mechanics for when a caller has explicitly turned it on via
``AVOGADRO_MCP_LAUNCH=1`` or ``--launch``. It searches, in order: an
explicit path, the ``AVOGADRO_APP`` environment variable, then a short list
of platform defaults.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time

from . import _connect


def _platform_default_paths():
    """Yield plausible install locations for the current platform, in order."""
    system = platform.system()
    if system == "Darwin":
        yield "/Applications/Avogadro2.app/Contents/MacOS/Avogadro2"
        yield os.path.expanduser(
            "~/Applications/Avogadro2.app/Contents/MacOS/Avogadro2"
        )
    elif system == "Windows":
        for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
            program_files = os.environ.get(env_var)
            if program_files:
                yield os.path.join(program_files, "Avogadro2", "bin", "avogadro2.exe")
    # Linux/BSD have no single conventional install path; PATH lookup below
    # (avogadro2) is the realistic case, e.g. a distro package or AppImage
    # symlink.
    which = shutil.which("avogadro2")
    if which:
        yield which


def find_avogadro_app(explicit=None):
    """
    Resolve the Avogadro executable to launch.

    Search order: ``explicit`` (e.g. a ``--launch-app`` flag), then the
    ``AVOGADRO_APP`` environment variable, then platform defaults
    (``/Applications/Avogadro2.app/...`` on macOS, ``avogadro2`` on
    ``PATH``, ``%ProgramFiles%\\Avogadro2\\bin\\avogadro2.exe`` on
    Windows).

    :param explicit: A path to use directly, bypassing the search.
    :returns: A path or bare command name to pass to ``subprocess.Popen``.
        This is a best-effort guess when nothing concrete was found --
        callers should be ready for ``Popen`` to fail with ``OSError``.
    """
    if explicit:
        return explicit
    env_path = os.environ.get("AVOGADRO_APP")
    if env_path:
        return env_path
    for candidate in _platform_default_paths():
        if candidate and os.path.exists(candidate):
            return candidate
    # Nothing found; fall back to the bare command name and let Popen/the
    # shell PATH have a final say (mainly useful on Linux).
    return "avogadro2"


def launch_avogadro(socket_name, *, app_path=None, timeout=20.0, extra_args=None):
    """
    Start Avogadro and wait for it to accept RPC connections.

    :param socket_name: The ``--rpc-name`` Avogadro should listen on. Only
        passed on the command line when it differs from the default
        ("avogadro"), so a headless or test instance can run beside a
        user's existing session.
    :param app_path: Explicit path to the Avogadro executable; searched for
        via :func:`find_avogadro_app` when omitted.
    :param timeout: Seconds to wait for the new process to start answering
        ``internalPing`` before giving up.
    :param extra_args: Additional command-line arguments to pass through.
    :raises ConnectionError: if the process could not be started, or never
        started answering within ``timeout``.
    """
    resolved = find_avogadro_app(app_path)
    args = [resolved]
    if socket_name != _connect.DEFAULT_SERVER:
        args += ["--rpc-name", socket_name]
    if extra_args:
        args += list(extra_args)

    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise ConnectionError(
            "Could not launch Avogadro (tried running %r): %s. Set "
            "AVOGADRO_APP to the executable's path, or start Avogadro "
            "yourself." % (args, exc)
        ) from exc

    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        probe = None
        try:
            probe = _connect.connect(name=socket_name, timeout=1.0)
            if probe.ping():
                return
        except ConnectionError as exc:
            last_error = exc
        finally:
            if probe is not None:
                probe.close()
        time.sleep(0.3)

    raise ConnectionError(
        "Launched Avogadro (%r) but it never started answering on socket "
        "'%s' within %.0fs." % (args, socket_name, timeout)
    ) from last_error
