"""nano_vm_mcp — MCP server for llm-nano-vm."""

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
