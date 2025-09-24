from typing import Dict, Any, List
import httpx
from .config import MCP_SERVER_BASE


class MCPClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or MCP_SERVER_BASE).rstrip("/")

    async def tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"name": name, "arguments": arguments}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.base}/mcp/tools", json=payload)
            r.raise_for_status()
            data = r.json()
            # MCPToolResponse schema: {content: [..], is_error: bool}
            return data

    async def list_subscriptions(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.base}/me/subscriptions")
            r.raise_for_status()
            return r.json()

    async def list_resource_groups(self, subscription_id: str) -> List[Dict[str, Any]]:
        sub = subscription_id
        if not sub.startswith("/subscriptions/"):
            sub = f"/subscriptions/{sub}"
        sub_guid = sub.split("/")[2]
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.base}/me/resource-groups", params={"subscription_id": sub_guid})
            r.raise_for_status()
            return r.json()

    async def list_mcp_tools(self) -> Dict[str, Any]:
        """Return available MCP tools catalog with fallback if endpoint missing."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"{self.base}/mcp/tools")
                r.raise_for_status()
                return r.json()
        except Exception:
            # Fallback minimal catalog
            return {
                "tools": [
                    {
                        "name": "azs.plan",
                        "description": "Azure 리소스 계획 및 권한/정책 확인",
                        "parameters": {
                            "scope": "Azure 스코프 (예: /subscriptions/<id>)",
                            "mode": "capabilities|constraints|check",
                            "resourceType": "리소스 타입",
                            "location": "리전",
                            "tags": "태그(JSON)"
                        }
                    },
                    {
                        "name": "azs.apply",
                        "description": "Azure 리소스 생성/삭제",
                        "parameters": {
                            "action": "create|delete",
                            "resourceType": "리소스 타입",
                            "scope": "Azure 스코프",
                            "params": "리소스별 파라미터"
                        }
                    },
                    {
                        "name": "azs.request",
                        "description": "ServiceNow 신청서 생성",
                        "parameters": {
                            "blockedItems": "차단 항목 배열",
                            "scope": "Azure 스코프"
                        }
                    },
                    {
                        "name": "azs.spec",
                        "description": "리소스 타입별 필수/옵션 파라미터 스펙",
                        "parameters": {"resourceType": "예: Microsoft.Compute/disks"}
                    }
                ]
            }
