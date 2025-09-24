"""Common loaders for MCP server catalogs (tools/resources/policy)."""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, List

import yaml

_BASE_DIR = Path(__file__).resolve().parent


def _safe_load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)  # type: ignore[no-any-return]


@lru_cache(maxsize=1)
def load_resource_catalog() -> Dict[str, Any]:
    """Return resource catalog data with fallback structure."""
    data = _safe_load_yaml(_BASE_DIR / "specs" / "resource_types.yaml")
    if not isinstance(data, dict):
        return {"commonRegions": [], "resourceTypes": {}}
    # Ensure keys exist
    data.setdefault("commonRegions", [])
    data.setdefault("resourceTypes", {})
    return data


@lru_cache(maxsize=1)
def load_policy_config() -> Dict[str, Any]:
    """Return policy configuration (enforced location, confirmation, ...)."""
    data = _safe_load_yaml(_BASE_DIR / "config" / "policy.yaml")
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_tool_catalog() -> List[Dict[str, Any]]:
    """Return list of tool descriptors for catalog endpoint."""
    data = _safe_load_yaml(_BASE_DIR / "specs" / "tools.yaml")
    if isinstance(data, dict):
        tools = data.get("tools")
        if isinstance(tools, list):
            return [t for t in tools if isinstance(t, dict)]
    return []


def resource_spec(resource_type: str) -> Dict[str, Any] | None:
    catalog = load_resource_catalog()
    resource_types = catalog.get("resourceTypes", {})
    if isinstance(resource_types, dict):
        entry = resource_types.get(resource_type)
        if isinstance(entry, dict):
            return entry
    return None


def common_regions() -> List[str]:
    catalog = load_resource_catalog()
    regions = catalog.get("commonRegions", [])
    return list(regions) if isinstance(regions, list) else []


def enforced_location_policy() -> Dict[str, Any]:
    policy = load_policy_config().get("enforcedLocation", {})
    return policy if isinstance(policy, dict) else {}
