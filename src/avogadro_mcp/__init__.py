"""avogadro-mcp: an MCP server that drives a running Avogadro molecular
editor over its local JSON-RPC socket."""

from importlib import metadata

try:
    __version__ = metadata.version("avogadro-mcp")
except metadata.PackageNotFoundError:  # pragma: no cover - editable/dev checkout
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
