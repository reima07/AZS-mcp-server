from __future__ import annotations
import re
from typing import Optional, Dict, Any


def try_fill_pending(state: dict, user_text: str) -> Optional[Dict[str, Any]]:
    if not state.get("pendingTool") or not state.get("pendingArgs") or not state.get("pendingMissing"):
        return None
    args = dict(state["pendingArgs"])  # shallow copy
    params = args.setdefault("params", {}) or {}
    missing = list(state.get("pendingMissing", []))
    t = user_text.strip()
    tl = t.lower()
    # name extraction: "이름 rg-foo", or token like rg-foo, or bare word
    if "name" in missing:
        m = re.search(r"이름\s*([A-Za-z0-9_.\-]+)", t)
        if m:
            params["name"] = m.group(1)
        else:
            m2 = re.search(r"\b(rg[-_A-Za-z0-9]+)\b", t)
            if m2:
                params["name"] = m2.group(1)
    # location extraction
    if "location" in missing:
        regions = [
            "koreacentral","koreasouth","eastus","eastus2","westus","westus2","westeurope","northeurope",
            "southeastasia","eastasia","japaneast","japanwest","australiaeast","australiasoutheast",
        ]
        for rgn in regions:
            if rgn in tl:
                params["location"] = rgn
                break
    # resourceGroup extraction
    if "resourceGroup" in missing:
        mrg = re.search(r"\b(rg[-A-Za-z0-9_.\-]+)\b", t)
        if mrg:
            params["resourceGroup"] = mrg.group(1)
    # sku extraction (disk/ACR synonyms too)
    if "sku" in missing:
        sku_map = {
            "basic": "Basic", "standard": "Standard", "premium": "Premium",
            "기본": "Basic", "표준": "Standard", "프리미엄": "Premium",
            "standard_lrs": "Standard_LRS", "standard-lrs": "Standard_LRS", "표준hdd": "Standard_LRS", "표준 hdd": "Standard_LRS",
            "premium_lrs": "Premium_LRS", "premium-lrs": "Premium_LRS",
            "standardssd_lrs": "StandardSSD_LRS", "standardssd-lrs": "StandardSSD_LRS", "표준ssd": "StandardSSD_LRS", "표준 ssd": "StandardSSD_LRS",
            "premiumssd_lrs": "PremiumSSD_LRS", "premiumssd-lrs": "PremiumSSD_LRS", "프리미엄ssd": "PremiumSSD_LRS", "프리미엄 ssd": "PremiumSSD_LRS",
            "ultrassd_lrs": "UltraSSD_LRS", "ultrassd-lrs": "UltraSSD_LRS", "울트라ssd": "UltraSSD_LRS", "울트라 ssd": "UltraSSD_LRS",
        }
        for key, val in sku_map.items():
            if key in tl.replace(" ", "") or key in tl:
                params["sku"] = val
                break
    # sizeGB extraction
    if "sizeGB" in missing:
        mszgb = re.search(r"(\d+)\s*(gb|기가|GiB|GB)", t, flags=re.IGNORECASE)
        if mszgb:
            try:
                params["sizeGB"] = int(mszgb.group(1))
            except Exception:
                pass
    # nodeCount extraction
    if "nodeCount" in missing:
        mnc = re.search(r"(\d+)\s*노드", t) or re.search(r"노드\s*(\d+)", t) or re.search(r"nodes?\s*(\d+)", tl)
        if mnc:
            try:
                params["nodeCount"] = int(mnc.group(1))
            except Exception:
                pass
    # nodeVmSize extraction
    if "nodeVmSize" in missing:
        msz = re.search(r"(Standard_[A-Za-z0-9]+)", t)
        if msz:
            params["nodeVmSize"] = msz.group(1)

    # recompute missing
    rem = []
    for f in missing:
        if f == "name" and not params.get("name"):
            rem.append("name")
        if f == "location" and not params.get("location"):
            rem.append("location")
        if f == "resourceGroup" and not params.get("resourceGroup"):
            rem.append("resourceGroup")
        if f == "sku" and not params.get("sku"):
            rem.append("sku")
        if f == "sizeGB" and not params.get("sizeGB"):
            rem.append("sizeGB")
        if f == "nodeCount" and not params.get("nodeCount"):
            rem.append("nodeCount")
        if f == "nodeVmSize" and not params.get("nodeVmSize"):
            rem.append("nodeVmSize")
    if not rem:
        state.pop("pendingTool", None)
        state.pop("pendingArgs", None)
        state.pop("pendingMissing", None)
        return {"action": "apply", "args": args}
    else:
        state["pendingArgs"] = args
        state["pendingMissing"] = rem
        ask = []
        if "name" in rem:
            ask.append("name(리소스 그룹/리소스 이름)")
        if "location" in rem:
            ask.append("location(리전)")
        if "resourceGroup" in rem:
            ask.append("resourceGroup(리소스 그룹)")
        if "sku" in rem:
            ask.append("sku(예: Basic/Standard/Premium 또는 디스크 SKU)")
        if "sizeGB" in rem:
            ask.append("sizeGB(예: 128)")
        if "nodeCount" in rem:
            ask.append("nodeCount(예: 1)")
        if "nodeVmSize" in rem:
            ask.append("nodeVmSize(예: Standard_B2s)")
        return {"action": "answer", "args": {"text": f"리소스 생성에 필요한 {', '.join(ask)} 값을 알려주세요."}}

