"""
MCP stdio client wrapper to connect to an MCP server process
and call tools via the model-context-protocol Python client.

If MCP stdio is not available, callers can still use the HTTP wrapper.
"""
from __future__ import annotations
from typing import Dict, Any
import asyncio

try:
    from mcp.client.stdio import StdioClient
except Exception:
    StdioClient = None  # type: ignore


class MCPStdioClient:
    def __init__(self, command: list[str] | None = None):
        self.command = command or ["python", "-m", "mcpserver.mcp_std_server"]
        self._client = None

    async def __aenter__(self):
        if StdioClient is None:
            raise RuntimeError("mcp client not installed: pip install mcp")
        self._client = StdioClient(self.command)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._client:
            await self._client.__aexit__(exc_type, exc, tb)
            self._client = None

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self._client:
            raise RuntimeError("client not started")
        resp = await self._client.call_tool(name, arguments)
        # resp: dict matching MCP response; normalize to simple dict
        return resp

