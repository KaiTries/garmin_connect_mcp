"""Read-only MCP server for your own Garmin Connect data (official Personal API)."""

from importlib.metadata import version as _v, PackageNotFoundError

try:
    __version__ = _v("garmin-connect-mcp")
except PackageNotFoundError:
    __version__ = "unknown"
