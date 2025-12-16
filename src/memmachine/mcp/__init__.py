"""MemMachine MCP Server - Model Context Protocol tools for memory management.

This package exposes MemMachine's Tesseract 4D Memory System as MCP tools
for use with Claude Code and other AI assistants.
"""

from .server import create_server, run_server

__all__ = ["create_server", "run_server"]
