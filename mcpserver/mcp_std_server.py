"""Stdio MCP server delegating to MCPToolHandler catalog."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

try:
    # FastMCP provides a simple @tool decorator and stdio runner
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "mcp package is required. Install with `pip install mcp` (model-context-protocol).\n"
        f"Import error: {exc}"
    )

from dotenv import load_dotenv

# Support both `python -m mcpserver.mcp_std_server` and `python mcp_std_server.py`
_HERE = os.path.dirname(__file__)
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.append(_PARENT)

try:
    from mcpserver.mcp_tools import MCPToolHandler, MCPToolRequest
except Exception:  # pragma: no cover - fallback if package name not resolvable
    from mcp_tools import MCPToolHandler, MCPToolRequest  # type: ignore

load_dotenv()

mcp = FastMCP("az-servant")
_handler = MCPToolHandler()


def _invoke(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to call shared MCPToolHandler and unwrap the response."""
    response = _handler.handle_tool(MCPToolRequest(name=name, arguments=arguments))
    if response.is_error:
        if response.content:
            first = response.content[0]
            if isinstance(first, dict):
                return first
        return {"error": "툴 실행 중 오류가 발생했습니다."}
    if response.content:
        first = response.content[0]
        return first if isinstance(first, dict) else {"result": first}
    return {}


@mcp.tool()
def azs_plan(
    scope: str,
    mode: str = "capabilities",
    resourceType: Optional[str] = None,
    location: Optional[str] = None,
    tags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    args: Dict[str, Any] = {"scope": scope, "mode": mode}
    if resourceType is not None:
        args["resourceType"] = resourceType
    if location is not None:
        args["location"] = location
    if tags is not None:
        args["tags"] = tags
    return _invoke("azs.plan", args)


@mcp.tool()
def azs_apply(
    action: str,
    resourceType: str,
    scope: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "action": action,
        "resourceType": resourceType,
        "scope": scope,
        "params": params or {},
    }
    return _invoke("azs.apply", args)


@mcp.tool()
def azs_spec(resourceType: str) -> Dict[str, Any]:
    return _invoke("azs.spec", {"resourceType": resourceType})


@mcp.tool()
def azs_request(
    blockedItems: List[Dict[str, Any]],
    scope: str,
    userName: Optional[str] = None,
    expiryDays: Optional[int] = None,
    subscriptionName: Optional[str] = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "blockedItems": blockedItems,
        "scope": scope,
    }
    if userName is not None:
        args["userName"] = userName
    if expiryDays is not None:
        args["expiryDays"] = expiryDays
    if subscriptionName is not None:
        args["subscriptionName"] = subscriptionName
    if confidence is not None:
        args["confidence"] = confidence
    return _invoke("azs.request", args)


@mcp.tool()
def azs_list_subscriptions() -> Dict[str, Any]:
    return _invoke("azs.list_subscriptions", {})


@mcp.tool()
def azs_list_resource_groups(scope: str) -> Dict[str, Any]:
    return _invoke("azs.list_resource_groups", {"scope": scope})


@mcp.tool()
def azs_list_resources(scope: str, resourceType: Optional[str] = None) -> Dict[str, Any]:
    args: Dict[str, Any] = {"scope": scope}
    if resourceType is not None:
        args["resourceType"] = resourceType
    return _invoke("azs.list_resources", args)


@mcp.tool()
def azs_get_resource(id: str) -> Dict[str, Any]:
    return _invoke("azs.get_resource", {"id": id})


if __name__ == "__main__":
    # Run stdio loop
    mcp.run()
