"""
The `avogadro-mcp` console script: runs the stdio MCP server by default, or
prints/installs client config with --print-config / --install.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import config
from .client import DEFAULT_SOCKET_NAME, AvogadroClient
from .server import create_server


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avogadro-mcp",
        description=(
            "MCP server that drives a running Avogadro molecular editor "
            "over its local JSON-RPC socket."
        ),
    )
    parser.add_argument(
        "--rpc-name",
        default=os.environ.get("AVOGADRO_MCP_SOCKET", DEFAULT_SOCKET_NAME),
        help=(
            "Name of the Avogadro RPC socket/pipe to connect to -- pass "
            "the same value Avogadro was started with via its own "
            "--rpc-name, for a non-default instance. Also settable with "
            "AVOGADRO_MCP_SOCKET. Default: %(default)s"
        ),
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        default=_env_flag("AVOGADRO_MCP_LAUNCH"),
        help=(
            "Start Avogadro automatically if it is not already listening. "
            "Off by default; can also be enabled with AVOGADRO_MCP_LAUNCH=1."
        ),
    )
    parser.add_argument(
        "--launch-app",
        default=os.environ.get("AVOGADRO_APP"),
        help=(
            "Path to the Avogadro executable to launch (only used with "
            "--launch). Defaults to $AVOGADRO_APP, then platform defaults "
            "(e.g. /Applications/Avogadro2.app/... on macOS, avogadro2 on "
            "PATH, %%ProgramFiles%%\\Avogadro2\\bin\\avogadro2.exe on "
            "Windows)."
        ),
    )
    parser.add_argument(
        "--print-config",
        metavar="CLIENT",
        choices=sorted(config.CLIENT_PATHS),
        help="Print the MCP client config snippet for CLIENT and exit.",
    )
    parser.add_argument(
        "--install",
        metavar="CLIENT",
        choices=sorted(config.CLIENT_PATHS),
        help=(
            "Merge avogadro-mcp into CLIENT's config file and exit. "
            "Existing content is preserved and backed up to <file>.bak."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.print_config:
        print(config.print_config(args.print_config))
        return 0

    if args.install:
        path, backed_up = config.install_config(args.install)
        print(f"Wrote avogadro-mcp into {path}")
        if backed_up:
            print(f"(previous contents backed up to {path}.bak)")
        return 0

    client = AvogadroClient(
        socket_name=args.rpc_name,
        launch=args.launch,
        app_path=args.launch_app,
    )
    server = create_server(client)
    try:
        server.run(transport="stdio")
    finally:
        client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
