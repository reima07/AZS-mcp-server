from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Dict, Any, TypedDict, Optional, List
import re
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage
from openai import OpenAI

from .mcp_client import MCPClient
from .mcp_stdio_client import MCPStdioClient
from .slot_fill import try_fill_pending
from .confirm import handle_confirmation_fastpath
from .config import OPENAI_API_KEY
import os
from dotenv import load_dotenv
import yaml

# 환경변수 로드
load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT_DIR / "mcpserver" / "config" / "policy.yaml"
RESOURCE_SPEC_PATH = ROOT_DIR / "mcpserver" / "specs" / "resource_types.yaml"


def _load_policy_config() -> Dict[str, Any]:
    try:
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError:
        return {}


_POLICY_CONFIG = _load_policy_config()
_LOCATION_POLICY = _POLICY_CONFIG.get("enforcedLocation", {})
ENFORCED_LOCATION_VALUE = _LOCATION_POLICY.get("value", "koreacentral")
ENFORCED_LOCATION_MESSAGE = _LOCATION_POLICY.get("message") or f"회사 정책상 리전이 {ENFORCED_LOCATION_VALUE}로 자동 설정됩니다."


def _load_resource_catalog() -> Dict[str, Any]:
    try:
        with open(RESOURCE_SPEC_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError:
        return {}


_RESOURCE_CATALOG = _load_resource_catalog()


def _build_resource_mapping_prompt() -> str:
    resource_types = _RESOURCE_CATALOG.get("resourceTypes", {})
    lines: List[str] = []
    if isinstance(resource_types, dict):
        for rtype, info in resource_types.items():
            if not isinstance(info, dict):
                continue
            aliases = info.get("aliases")
            if not isinstance(aliases, list) or not aliases:
                continue
            alias_text = "/".join(str(a) for a in aliases if a)
            if alias_text:
                lines.append(f"{alias_text} -> {rtype}")
    return "\n".join(lines) if lines else "(no resource mapping available)"


RESOURCE_MAPPING_PROMPT = _build_resource_mapping_prompt()


def _format_param_prompts(spec: Optional[Dict[str, Any]], missing_keys: List[str]) -> List[str]:
    prompts: List[str] = []
    spec_params: Dict[str, Dict[str, Any]] = {}
    if isinstance(spec, dict):
        for entry in spec.get("params", []):
            if isinstance(entry, dict) and "key" in entry:
                spec_params[entry["key"]] = entry

    for key in missing_keys:
        info = spec_params.get(key, {})
        label = info.get("label", key)
        details: List[str] = []
        hint = info.get("hint")
        if hint:
            details.append(f"힌트: {hint}")
        enum = info.get("enum")
        if isinstance(enum, list) and enum:
            options = ", ".join(map(str, enum))
            details.append(f"옵션: {options}")
        if "default" in info and info.get("default") not in (None, ""):
            details.append(f"기본값: {info.get('default')}")
        if info.get("secret"):
            details.append("비밀 값 (화면에 노출되지 않도록 주의)")
        line = f"- {key}: {label}"
        if details:
            line += " (" + "; ".join(details) + ")"
        prompts.append(line)
    return prompts


def _match_resource_group_name(rgs: List[Dict[str, Any]], candidate: str) -> Optional[str]:
    if not candidate:
        return None
    cand = candidate.strip().lower()
    if not cand:
        return None
    rg_names = [rg.get("name", "") for rg in rgs]
    rg_names_lower = [name.lower() for name in rg_names]

    # Exact token match for rg- names
    import re
    tokens = re.findall(r"\b(rg[-A-Za-z0-9_.\-]+)\b", cand)
    for tok in tokens:
        tok_l = tok.lower()
        for name_l, name in zip(rg_names_lower, rg_names):
            if tok_l == name_l:
                return name

    # Containment match
    contained = [name for name_l, name in zip(rg_names_lower, rg_names) if name_l and name_l in cand]
    if contained:
        return max(contained, key=len)

    # Partial match using last token
    base_tokens = [x for x in re.split(r"\s+|,|/|\\|\(|\)", cand) if x]
    if base_tokens:
        key = base_tokens[-1]
        part_matches = [rg for rg in rg_names if key in rg.lower()]
        if part_matches:
            return max(part_matches, key=len)
    return None


class AgentState(TypedDict, total=False):
    messages: list
    workingSubscription: Optional[str]
    workingResourceGroup: Optional[str]
    intent: Optional[Dict[str, Any]]
    lastResult: Optional[Dict[str, Any]]
    lastRaw: Optional[Dict[str, Any]]
    pendingTool: Optional[str]
    pendingArgs: Optional[Dict[str, Any]]
    pendingMissing: Optional[list]


SYSTEM_INTENT = f"""
You are an Azure MCP assistant. Understand user requests intelligently and route to appropriate Azure tools.
Output JSON: {{action: one of [list_subscriptions, choose_subscription, list_rgs, choose_rg, plan, apply, request, answer, find_resources], args: {{...}}}}.
For answer action, use args.text (not args.content or args.message).
For non-Azure questions, politely redirect to Azure topics. Be efficient and Azure-focused.

Resource type mapping:
{RESOURCE_MAPPING_PROMPT}

Action patterns:
- 권한확인/생성가능/can create → plan with mode=capabilities and location={ENFORCED_LOCATION_VALUE}
- 찾기/찾아줘/where/find → find_resources with resourceType
- 생성/만들기/create → apply with action=create
- 삭제/지우기/delete → apply with action=delete
- 리소스그룹 선택/select resource group → choose_rg with candidate

IMPORTANT Examples:
- 'rg-xxx에 ACR 만들어줘' → apply with resourceType=Microsoft.ContainerRegistry/registries, params.resourceGroup=rg-xxx
- 'rg-xxx에 AKS 만들어줘' → apply with resourceType=Microsoft.ContainerService/managedClusters, params.resourceGroup=rg-xxx
- 'rg-xxx에 키볼트 만들어줘' → apply with resourceType=Microsoft.KeyVault/vaults, params.resourceGroup=rg-xxx
- 'rg-xxx에 이벤트허브 만들어줘' → apply with resourceType=Microsoft.EventHub/namespaces, params.resourceGroup=rg-xxx
- 'rg-xxx에 VM 만들어줘' → apply with resourceType=Microsoft.Compute/virtualMachines, params.resourceGroup=rg-xxx
- 'rg-xxx의 리소스명 삭제해줘' → apply with action=delete, resourceType=..., params.resourceGroup=rg-xxx, params.name=리소스명
- '기본으로 진행해' or '기본안으로 진행' → apply with current pending resource creation
- 'rg-xxx 선택' or 'rg-xxx 선택해줘' → choose_rg with candidate=rg-xxx
- 'rg-xxx 리소스 그룹 만들어줘' or '리소스 그룹 만들어줘 rg-xxx' → apply with resourceType=Microsoft.Resources/resourceGroups, params.name=rg-xxx
- 'rg-xxx 리소스 그룹 만들거 선택해줘' → choose_rg with candidate=rg-xxx
- NEVER confuse resource creation with resource group creation!
- NEVER interpret '기본으로 진행해' as subscription selection!

CRITICAL RULES:
0. Respond in Korean when communicating with users. Use natural language mixing Korean and English as appropriate for technical contexts.
1. NEVER choose_subscription unless user explicitly requests subscription selection
2. If user wants to list resource groups but no subscription selected, use list_rgs action anyway - system will handle it
3. If user asks 'can I create X?' or '만들 수 있어?' → use plan action with mode=capabilities
4. If user wants to create resources but no subscription selected, ask user to select subscription first
5. When user wants to select a subscription:
   - ALWAYS use choose_subscription action, never just ask questions
   - Examples: '145번' → choose_subscription with '145', '응' after suggestion → choose_subscription with suggested subscription
   - If unsure which subscription they mean, still use choose_subscription - the system will handle errors
6. Use natural language understanding - don't rely on hardcoded patterns

For apply on resource groups, args should include params.name and params.location when available. If missing, still return apply and the client will clarify.
"""


class AgentOrchestrator:
    def __init__(self):
        self.client = MCPClient()
        self.memory = MemorySaver()
        
        api_key = os.getenv('OPENAI_API_KEY')
        self.llm = OpenAI(api_key=api_key) if api_key else None
        
        # Tool invocation route: stdio or HTTP
        self._use_stdio = os.getenv('MCP_USE_STDIO', 'false').lower() in ('1','true','yes','on')
        cmd = os.getenv('MCP_STDIO_COMMAND', '').strip()
        if self._use_stdio:
            if cmd:
                self._stdio_cmd = cmd.split()
            else:
                self._stdio_cmd = ["python","-m","mcpserver.mcp_std_server"]
        else:
            self._stdio_cmd = []

        self.sessions: Dict[str, AgentState] = {}
        self._spec_cache: Dict[str, Dict[str, Any]] = {}
        self.graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(AgentState)

        async def decide(state: AgentState) -> AgentState:
            # last human message
            last = state["messages"][-1]
            text = last.content if isinstance(last, HumanMessage) else str(last)
            intent: Dict[str, Any] | None = None

            # If there is a pending slot-fill, try to capture values from this turn first
            async def _try_fill_pending_with_llm(user_text: str, state: AgentState, llm) -> Optional[Dict[str, Any]]:
                """LLM을 활용한 스마트 파라미터 파싱"""
                print(f"DEBUG: _try_fill_pending_with_llm called with: {user_text}")
                print(f"DEBUG: pendingTool={state.get('pendingTool')}, llm={llm is not None}")
                print(f"DEBUG: pendingMissing={state.get('pendingMissing', [])}")
                
                if not state.get("pendingTool"):
                    print("DEBUG: No pending tool, returning None")
                    return None
                    
                pending_args = state.get("pendingArgs", {})
                missing_raw = state.get("pendingMissing")
                if not isinstance(missing_raw, list):
                    missing = []
                else:
                    missing = list(missing_raw)
                policy_note: Optional[str] = None
                
                if not missing:
                    print("DEBUG: No missing parameters, returning None")
                    return None

                if not llm:
                    print("DEBUG: No LLM available, falling back to legacy parser")
                    return _try_fill_pending_legacy(user_text, state)

                # LLM에게 파라미터 추출 요청
                try:
                    extraction_prompt = f"""
사용자가 다음 값들을 제공했습니다: "{user_text}"

필요한 파라미터들: {missing}

사용자 입력에서 파라미터를 추출해서 JSON으로 반환해주세요.

파라미터 매핑 규칙:
- name/이름: 리소스 이름
- adminUser/관리자/사용자: 관리자 계정명  
- adminPassword/비밀번호/패스워드: 관리자 비밀번호
- location/리전/위치: "{ENFORCED_LOCATION_VALUE}"를 우선 적용하며 필요 시 다른 Azure 리전명도 인식하세요

다양한 입력 형식을 지원합니다:
- "name = jiwootest, adminUser: jiwoo, adminPassword=jiwoo1234" → {{"name": "jiwootest", "adminUser": "jiwoo", "adminPassword": "jiwoo1234"}}
- "이름은 myserver 관리자는 admin 비번은 pass123" → {{"name": "myserver", "adminUser": "admin", "adminPassword": "pass123"}}
- "admin=jiwoo, password=1234" → {{"adminUser": "jiwoo", "adminPassword": "1234"}}
- "서버명: testdb, 사용자: dbadmin, 암호: secret" → {{"name": "testdb", "adminUser": "dbadmin", "adminPassword": "secret"}}

중요: = : , 등의 구분자와 공백을 무시하고 값을 추출해주세요.

추출된 값만 JSON으로 반환하세요. 값이 없으면 빈 객체 {{}}를 반환하세요.
"""
                    
                    resp = llm.chat.completions.create(
                        model=os.getenv('OPENAI_MODEL', 'gpt-5'),
                        messages=[
                            {"role": "system", "content": "Extract parameters from user input and return ONLY valid JSON. No explanations, no markdown formatting."},
                            {"role": "user", "content": f"Extract these parameters from: '{user_text}'\nNeeded: {missing}\n\nExamples:\n- '이름 testjiwoost' → {{\"name\":\"testjiwoost\"}}\n- '이름 = testjiwoost' → {{\"name\":\"testjiwoost\"}}\n- 'name testjiwoost' → {{\"name\":\"testjiwoost\"}}\n- 'testjiwoost' → {{\"name\":\"testjiwoost\"}} (if name is needed)\n\nReturn JSON only:"}
                        ]
                    )
                    
                    llm_response = resp.choices[0].message.content.strip()
                    print(f"DEBUG: LLM parameter extraction input: '{user_text}'")
                    print(f"DEBUG: LLM parameter extraction missing: {missing}")
                    print(f"DEBUG: LLM parameter extraction response: {llm_response}")
                    
                    # JSON 파싱
                    if llm_response.startswith('```json'):
                        llm_response = llm_response.replace('```json', '').replace('```', '').strip()
                    
                    extracted = json.loads(llm_response)
                    
                    # 기존 파라미터에 병합 (params 구조 고려)
                    params = pending_args.setdefault("params", {})
                    for key, value in extracted.items():
                        if key in missing and value:
                            params[key] = value
                            print(f"DEBUG: Set parameter params.{key} = {value}")
                    
                    if "location" in missing:
                        current = params.get("location")
                        if current != ENFORCED_LOCATION_VALUE:
                            params["location"] = ENFORCED_LOCATION_VALUE
                            print("DEBUG: Auto-set params.location via policy")
                            policy_note = ENFORCED_LOCATION_MESSAGE
                        missing = [m for m in missing if m != "location"]

                    # 여전히 누락된 파라미터 확인
                    remaining = [f for f in missing if not params.get(f)]
                    print(f"DEBUG: pending_args after extraction: {pending_args}")
                    print(f"DEBUG: remaining parameters: {remaining}")
                    
                    if not remaining:
                        # 모든 파라미터가 채워짐 - 실행 준비
                        state.pop("pendingTool", None)
                        state.pop("pendingArgs", None)
                        state.pop("pendingMissing", None)
                        return {"action": "apply", "args": pending_args}
                    else:
                        # 일부만 채워짐 - 상태 업데이트 후 나머지 요청
                        state["pendingArgs"] = pending_args
                        state["pendingMissing"] = remaining
                        
                        spec = await self._get_resource_spec(pending_args.get("resourceType"))
                        ask_list = _format_param_prompts(spec, remaining)
                        if not ask_list:
                            ask_list = [f"- {param}" for param in remaining]
                        message_lines = ["다음 값들을 더 알려주세요:", ""]
                        message_lines.extend(ask_list)
                        if policy_note:
                            message_lines.extend(["", f"ℹ️ {policy_note}"])
                        example_keys = ", ".join(f"{key}=값" for key in remaining[:2]) or "param=값"
                        message_lines.extend(["", "여러 값을 쉼표로 구분해 한 줄로 입력해주세요.", f"예시 형식: {example_keys}"])
                        message = "\n".join(message_lines)
                        
                        return {"action": "answer", "args": {"text": message}}
                        
                except Exception as e:
                    print(f"LLM parameter extraction error: {e}")
                    
                    policy_note = None
                    if "location" in missing:
                        params = pending_args.setdefault("params", {})
                        if params.get("location") != ENFORCED_LOCATION_VALUE:
                            params["location"] = ENFORCED_LOCATION_VALUE
                            policy_note = ENFORCED_LOCATION_MESSAGE
                            print("DEBUG: Auto-set location via policy (fallback)")
                        missing = [f for f in missing if f != "location"]
                    
                    if not missing:
                        # location만 필요했던 경우 바로 실행
                        state.pop("pendingTool", None)
                        state.pop("pendingArgs", None)
                        state.pop("pendingMissing", None)
                        return {"action": "apply", "args": pending_args}
                    
                    # 나머지 파라미터 요청
                    state["pendingArgs"] = pending_args
                    state["pendingMissing"] = missing
                    spec = await self._get_resource_spec(pending_args.get("resourceType"))
                    asks = _format_param_prompts(spec, missing)
                    if not asks:
                        asks = [f"- {param}" for param in missing]
                    message_lines = ["다음 값들을 알려주세요:", ""]
                    message_lines.extend(asks)
                    if policy_note:
                        message_lines.extend(["", f"ℹ️ {policy_note}"])
                    example_keys = ", ".join(f"{key}=값" for key in missing[:2]) or "param=값"
                    message_lines.extend(["", "여러 값을 쉼표로 구분해 한 줄로 입력해주세요.", f"예시: {example_keys}"])
                    message = "\n".join(message_lines)
                    return {"action": "answer", "args": {"text": message}}
                
                return None
            
            def _try_fill_pending_legacy(user_text: str, state: AgentState) -> Optional[Dict[str, Any]]:
                """키워드 기반 파라미터 파싱(Fallback)."""
                if not state.get("pendingTool") or not state.get("pendingArgs") or not state.get("pendingMissing"):
                    return None
                args = dict(state["pendingArgs"])  # shallow copy
                params = args.setdefault("params", {}) or {}
                pending_missing = state.get("pendingMissing")
                missing = list(pending_missing) if isinstance(pending_missing, list) else []
                policy_note = None
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
                # location extraction: common regions (simple list)
                if "location" in missing:
                    regions = [
                        "koreacentral","koreasouth","eastus","eastus2","westus","westus2","westeurope","northeurope",
                        "southeastasia","eastasia","japaneast","japanwest","australiaeast","australiasoutheast",
                    ]
                    for rgn in regions:
                        if rgn in tl:
                            params["location"] = rgn
                            break
                    if params.get("location") != ENFORCED_LOCATION_VALUE:
                        params["location"] = ENFORCED_LOCATION_VALUE
                        policy_note = ENFORCED_LOCATION_MESSAGE
                # resourceGroup extraction
                if "resourceGroup" in missing:
                    mrg = re.search(r"\b(rg[-A-Za-z0-9_.\-]+)\b", t)
                    if mrg:
                        params["resourceGroup"] = mrg.group(1)
                # sku extraction (Basic/Standard/Premium + Korean synonyms)
                if "sku" in missing:
                    sku_map = {
                        # Generic tiers
                        "basic": "Basic", "standard": "Standard", "premium": "Premium",
                        "기본": "Basic", "표준": "Standard", "프리미엄": "Premium",
                        # Disk SKUs
                        "standard_lrs": "Standard_LRS", "standard-lrs": "Standard_LRS", "표준 hdd": "Standard_LRS", "표준hdd": "Standard_LRS",
                        "premium_lrs": "Premium_LRS", "premium-lrs": "Premium_LRS", "프리미엄 hdd": "Premium_LRS",
                        "standardssd_lrs": "StandardSSD_LRS", "standardssd-lrs": "StandardSSD_LRS", "표준 ssd": "StandardSSD_LRS", "표준ssd": "StandardSSD_LRS",
                        "premiumssd_lrs": "PremiumSSD_LRS", "premiumssd-lrs": "PremiumSSD_LRS", "프리미엄 ssd": "PremiumSSD_LRS", "프리미엄ssd": "PremiumSSD_LRS",
                        "ultrassd_lrs": "UltraSSD_LRS", "ultrassd-lrs": "UltraSSD_LRS", "울트라 ssd": "UltraSSD_LRS", "울트라ssd": "UltraSSD_LRS"
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
                    mnc = re.search(r"(\d+)\s*노드", t)
                    if not mnc:
                        mnc = re.search(r"노드\s*(\d+)", t)
                    if not mnc:
                        mnc = re.search(r"nodes?\s*(\d+)", tl)
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
                
                # adminUser extraction
                if "adminUser" in missing:
                    admin_patterns = [
                        r"adminUser\s*[:=]\s*([A-Za-z0-9_]+)",
                        r"관리자계정\s*[:=]\s*([A-Za-z0-9_]+)",
                        r"관리자\s*사용자\s*[:=]\s*([A-Za-z0-9_]+)",
                        r"사용자\s*[:=]\s*([A-Za-z0-9_]+)"
                    ]
                    for pattern in admin_patterns:
                        match = re.search(pattern, t, re.IGNORECASE)
                        if match:
                            params["adminUser"] = match.group(1)
                            break
                
                # adminPassword extraction
                if "adminPassword" in missing:
                    pwd_patterns = [
                        r"adminPassword\s*[:=]\s*([A-Za-z0-9!@#$%^&*()_+-=]+)",
                        r"비밀번호\s*[:=]\s*([A-Za-z0-9!@#$%^&*()_+-=]+)",
                        r"password\s*[:=]\s*([A-Za-z0-9!@#$%^&*()_+-=]+)"
                    ]
                    for pattern in pwd_patterns:
                        match = re.search(pattern, t, re.IGNORECASE)
                        if match:
                            params["adminPassword"] = match.group(1)
                            break
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
                    if f == "nodeCount" and not params.get("nodeCount"):
                        rem.append("nodeCount")
                    if f == "nodeVmSize" and not params.get("nodeVmSize"):
                        rem.append("nodeVmSize")
                    if f == "sizeGB" and not params.get("sizeGB"):
                        rem.append("sizeGB")
                if not rem:
                    # ready to execute original tool
                    state.pop("pendingTool", None)
                    state.pop("pendingArgs", None)
                    state.pop("pendingMissing", None)
                    return {"action": "apply", "args": args}
                else:
                    # update pending and ask remaining only
                    state["pendingArgs"] = args
                    state["pendingMissing"] = rem
                    ask = []
                    if "name" in rem:
                        ask.append("name(리소스 그룹 이름)")
                    if "location" in rem:
                        ask.append("location(리전)")
                    if "resourceGroup" in rem:
                        ask.append("resourceGroup(리소스 그룹)")
                    if "sku" in rem:
                        ask.append("sku(예: Basic, Standard, Premium)")
                    if "nodeCount" in rem:
                        ask.append("nodeCount(노드 수, 예: 1)")
                    if "nodeVmSize" in rem:
                        ask.append("nodeVmSize(예: Standard_B2s)")
                    if "sizeGB" in rem:
                        ask.append("sizeGB(디스크 크기, 예: 128)")
                    message_lines = [f"리소스 생성에 필요한 {', '.join(ask)} 값을 알려주세요."]
                    if policy_note:
                        message_lines.append(f"\nℹ️ {policy_note}")
                    return {"action": "answer", "args": {"text": "".join(message_lines)}}

                # attempt fill if pending - LLM을 활용한 스마트 파싱
            pend = await _try_fill_pending_with_llm(text, state, self.llm)
            if pend:
                state["intent"] = pend
                return state
            # '원문' 요청 처리(직전 툴 응답 JSON 보기)
            if any(k in text for k in ["원문", "raw", "json", "자세히"]):
                raw = state.get("lastRaw")
                if raw:
                    pretty = f"```json\n{json.dumps(raw, ensure_ascii=False, indent=2)}\n```"
                else:
                    pretty = "(원문 JSON이 없습니다. 리소스 생성/조회 후 다시 시도해주세요.)"
                state["intent"] = {"action": "answer", "args": {"text": pretty}}
                return state

            # Fast-path: handle confirmation replies before calling LLM
            # If the last result asked for confirmation about a subscription and the user said yes/no,
            # resolve deterministically without involving the LLM (which might misinterpret short replies like "네").
            try:
                # Prefer external helper (modularized)
                fp2 = handle_confirmation_fastpath(state, text)
                if fp2:
                    state["intent"] = fp2
                    return state
                last_res = state.get("lastResult") or {}
                # Normalize current reply for yes/no detection
                tnorm = (text or "").strip().lower()
                # Accept only short, explicit confirmations to reduce false positives
                YES_SET = {"y", "yes", "ok", "okay", "확인", "네", "예", "응", "그래", "맞아"}
                NO_SET = {"n", "no", "cancel", "취소", "아니요", "아니오", "아니"}
                is_yes = tnorm in YES_SET or tnorm.rstrip(".!? ") in {x.lower() for x in YES_SET}
                is_no = tnorm in NO_SET or tnorm.rstrip(".!? ") in {x.lower() for x in NO_SET}

                # 1) Engine-confirmation path (structured)
                if isinstance(last_res, dict) and last_res.get("type") == "confirmation":
                    if is_yes:
                        pending_sub = last_res.get("pendingSubscription") or {}
                        sub_id = pending_sub.get("id")
                        if sub_id:
                            state["intent"] = {"action": "choose_subscription", "args": {"candidate": sub_id, "confirmed": True}}
                            return state
                        # Apply confirmation
                        pending_apply = last_res.get("pendingApply")
                        if pending_apply and isinstance(pending_apply, dict):
                            # Force confirmed apply
                            args = dict(pending_apply)
                            args["confirmed"] = True
                            state["intent"] = {"action": "apply", "args": args}
                            return state
                    if is_no:
                        state["intent"] = {"action": "answer", "args": {"text": "선택을 취소했습니다. 다른 작업을 도와드릴까요?"}}
                        return state

                # 2) Content-based confirmation detection (LLM asked but not structured)
                last_msgs = state.get("messages", [])
                if len(last_msgs) >= 2:
                    prev_ai = last_msgs[-2]
                    prev_text = prev_ai.content if hasattr(prev_ai, "content") else str(prev_ai)
                    pli = (prev_text or "").strip()
                    if is_yes and pli:
                        import re as _re
                        # Subscription confirmation text pattern
                        if "이 구독을 선택하시겠습니까" in pli:
                            m = _re.search(r"ID\s*:\s*(/subscriptions/[0-9a-f\-]+)", pli, flags=_re.I)
                            if m:
                                sub_id = m.group(1)
                                state["intent"] = {"action": "choose_subscription", "args": {"candidate": sub_id, "confirmed": True}}
                                return state
                        # Resource group confirmation text pattern
                        if "리소스 그룹" in pli and "선택하시겠습니까" in pli:
                            mrg = _re.search(r"'?(rg[-A-Za-z0-9_\.\-]+)'?\s*리소스\s*그룹.*선택하시겠습니까", pli)
                            if not mrg:
                                mrg = _re.search(r"리소스\s*그룹\s*'?([A-Za-z0-9_\.\-]+)'?.*선택하시겠습니까", pli)
                            if mrg:
                                rg_name = mrg.group(1)
                                state["intent"] = {"action": "choose_rg", "args": {"candidate": rg_name}}
                                return state
                    if is_no and ("선택하시겠습니까" in pli):
                        state["intent"] = {"action": "answer", "args": {"text": "선택을 취소했습니다. 다른 작업을 도와드릴까요?"}}
                        return state
            except Exception:
                # Non-fatal; fall through to normal flow
                pass

            # 컨텍스트 기반 처리 우선 (기본으로 진행해, 네, 예 등)
            context_patterns = [
                (r"기본.*진행|진행해|진행할게|진행하자", "proceed_with_defaults"),
                (r"네|예|맞아|좋아|괜찮", "confirm_action"),
                (r"승인함|허용함|동의함", "confirm_action"),  # 사용자 승인 메시지 인식
                (r"거부함|취소함|반대함", "cancel_action"),   # 사용자 거부 메시지 인식
                (r"아니|싫어|취소", "cancel_action")
            ]
            
            for pattern, action_type in context_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    if action_type == "confirm_action":
                        # 승인 메시지 처리 - 구독 선택 확인인지 체크
                        last_msgs = state.get("messages", [])
                        if len(last_msgs) >= 2:
                            prev_ai = last_msgs[-2]
                            prev_text = prev_ai.content if hasattr(prev_ai, "content") else str(prev_ai)
                            if "이 구독을 선택하시겠습니까" in prev_text:
                                # 구독 선택 승인 처리
                                import re as _re
                                m = _re.search(r"ID\s*:\s*(/subscriptions/[0-9a-f\-]+)", prev_text, flags=_re.I)
                                if m:
                                    sub_id = m.group(1)
                                    print(f"DEBUG: Context-aware subscription confirmation for {sub_id}")
                                    return {"action": "choose_subscription", "args": {"candidate": sub_id, "confirmed": True}}
                        
                        # 일반적인 pending 작업 승인 처리
                        if state.get("pendingTool"):
                            print(f"DEBUG: Context-aware confirmation for {state.get('pendingTool')}")
                            pending_args = dict(state.get("pendingArgs", {}) or {})
                            # 안전 보강: scope 자동 설정
                            scope = pending_args.get("scope")
                            if not scope:
                                working_rg = state.get("workingResourceGroup")
                                working_sub = state.get("workingSubscription")
                                if working_rg and working_sub:
                                    scope = f"{working_sub}/resourceGroups/{working_rg}"
                                elif working_sub:
                                    scope = working_sub
                                pending_args["scope"] = scope
                                print(f"DEBUG: Filled scope for confirmation: {scope}")
                            # params 기본값 보강
                            params = pending_args.setdefault("params", {}) or {}
                            # 회사 정책: location 기본값
                            if not params.get("location"):
                                params["location"] = ENFORCED_LOCATION_VALUE
                            # RG 컨텍스트 보강
                            if not params.get("resourceGroup") and scope and "/resourceGroups/" in scope:
                                try:
                                    rg_name = scope.split("/resourceGroups/")[1].split("/")[0]
                                    params["resourceGroup"] = rg_name
                                except Exception:
                                    pass
                            pending_args["params"] = params
                            # 승인 플래그
                            pending_args["confirmed"] = True
                            state.pop("pendingTool", None)
                            state.pop("pendingArgs", None)
                            state.pop("pendingMissing", None)
                            # LangGraph 일관성: intent에 설정하여 act 단계로 위임
                            state["intent"] = {"action": "apply", "args": pending_args}
                            return state
                    
                    elif action_type == "proceed_with_defaults" and state.get("pendingTool"):
                        # 기존 pending 작업을 기본값으로 진행
                        print(f"DEBUG: Context-aware proceed with defaults for {state.get('pendingTool')}")
                        # 기본값을 자동으로 채워서 바로 실행
                        pending_args = state.get("pendingArgs", {})
                        missing = state.get("pendingMissing", [])
                        
                        # VM 생성의 경우 기본값 자동 설정
                        if pending_args.get("resourceType") == "Microsoft.Compute/virtualMachines":
                            params = pending_args.setdefault("params", {})
                            if "name" in missing and not params.get("name"):
                                params["name"] = "defaultvm"
                            if "adminUser" in missing and not params.get("adminUser"):
                                params["adminUser"] = "azureuser"
                            if "adminPassword" in missing and not params.get("adminPassword"):
                                params["adminPassword"] = "TempPass123!"
                            # 기타 기본값들...
                        
                        # 스토리지 계정 생성의 경우 기본값 자동 설정
                        elif pending_args.get("resourceType") == "Microsoft.Storage/storageAccounts":
                            params = pending_args.setdefault("params", {})
                            if "name" in missing and not params.get("name"):
                                params["name"] = "stjiwoo1p145"  # 기본 제안된 이름 사용
                            if "sku" in missing and not params.get("sku"):
                                params["sku"] = "Standard_LRS"  # 최소 스펙
                            if "kind" in missing and not params.get("kind"):
                                params["kind"] = "StorageV2"
                            # 기타 기본값들...
                        
                        state.pop("pendingTool", None)
                        state.pop("pendingArgs", None) 
                        state.pop("pendingMissing", None)
                        state["intent"] = {"action": "apply", "args": pending_args}
                        return state
                    elif action_type == "confirm_action":
                        # 확인 응답 처리 - pending 작업이 있으면 confirmed=true로 실행
                        if state.get("pendingTool"):
                            print("DEBUG: Context-aware confirmation - executing pending tool")
                            pending_args = state.get("pendingArgs", {})
                            # confirmed 플래그 추가해서 바로 실행
                            pending_args["confirmed"] = True
                            state.pop("pendingTool", None)
                            state.pop("pendingArgs", None) 
                            state.pop("pendingMissing", None)
                            state["intent"] = {"action": "apply", "args": pending_args}
                            return state
                        else:
                            print("DEBUG: Context-aware confirmation but no pending tool")
                    elif action_type == "cancel_action":
                        # 취소 응답 처리
                        if state.get("pendingTool"):
                            print("DEBUG: Context-aware cancellation")
                            state.pop("pendingTool", None)
                            state.pop("pendingArgs", None) 
                            state.pop("pendingMissing", None)
                            state["intent"] = {"action": "answer", "args": {"text": "작업이 취소되었습니다."}}
                            return state
                    break

            # LLM 중심 의도 파싱 (실제 MCP 툴 정보 기반)
            if self.llm:
                try:
                    # MCP 서버에서 실제 툴 정보 가져오기
                    mcp_tools_response = await self.client.list_mcp_tools()
                    mcp_tools = mcp_tools_response.get("tools", [])
                    
                    # 현재 사용 가능한 구독 목록 가져오기
                    try:
                        subscriptions = await self.client.list_subscriptions()
                        subscriptions_info = "Available Subscriptions:\n"
                        for sub in subscriptions:
                            subscriptions_info += f"- Name: '{sub['name']}' → ID: '{sub['id']}'\n"
                    except:
                        subscriptions_info = "Available Subscriptions: Unable to fetch\n"
                    
                    # 툴 정보를 LLM이 이해할 수 있는 형태로 변환
                    tools_description = "Available MCP Tools:\n"
                    for tool in mcp_tools:
                        tools_description += f"- {tool['name']}: {tool['description']}\n"
                        for param, desc in tool.get('parameters', {}).items():
                            tools_description += f"  * {param}: {desc}\n"
                        tools_description += "\n"
                    
                    tools_description += f"""
                    {subscriptions_info}
                    
                    Helper APIs:
                    - list_subscriptions: Get available subscriptions
                    - list_rgs: Get resource groups for a subscription
                    - choose_subscription: Set working subscription
                    """
                    
                    enhanced_prompt = f"""
                    {tools_description}
                    
                    User input: "{text}"
                    Current working subscription: {state.get('workingSubscription', 'None')}
                    Current working resource group: {state.get('workingResourceGroup', 'None')}
                    
                    You are an intelligent Azure assistant. Follow this workflow:
                    
                    1. **Resource Creation Requests**: 
                       - Use appropriate MCP tools (azs.apply) with correct resourceType
                       - Supported types: Microsoft.ContainerService/managedClusters (AKS), Microsoft.ContainerRegistry/registries (ACR), Microsoft.KeyVault/vaults, Microsoft.EventHub/namespaces, Microsoft.Storage/storageAccounts, Microsoft.Resources/resourceGroups, etc.
                       - If missing required params, ask user to provide them
                       - Example: "AKS 만들어줘" → call azs.apply tool
                    
                    2. **Resource Listing**:
                       - "구독 목록" → azs.list_subscriptions
                       - "리소스 그룹 목록" → azs.list_resource_groups  
                       - "리소스 목록" → azs.list_resources
                    
                    3. **Subscription Management**:
                       - Use choose_subscription for subscription selection
                       - Match user input to available subscriptions intelligently
                    
                    4. **Natural Language Understanding**:
                       - "여기에" = current subscription
                       - "이벤트허브" = Microsoft.EventHub/namespaces
                       - "ACR" = Microsoft.ContainerRegistry/registries
                       - "AKS" = Microsoft.ContainerService/managedClusters
                       - "키볼트" = Microsoft.KeyVault/vaults
                       - "스토리지" = Microsoft.Storage/storageAccounts
                    
                    CRITICAL: Always call the appropriate MCP tool rather than hardcoded actions.
                    Return JSON: {{"action": "mcp_tool_name", "args": {{...}}}}
                    """
                    
                    # 컨텍스트 정보 추가
                    context_info = ""
                    if state.get("pendingTool"):
                        context_info += f"\n[PENDING] User has {state.get('pendingTool')} operation waiting for: {state.get('pendingMissing', [])}"
                    if state.get("workingSubscription"):
                        context_info += f"\n[SUBSCRIPTION] {state.get('workingSubscription')}"
                    if state.get("workingResourceGroup"):
                        context_info += f"\n[RESOURCE_GROUP] {state.get('workingResourceGroup')}"
                    
                    enhanced_prompt_with_context = enhanced_prompt + context_info
                    
                    resp = self.llm.chat.completions.create(
                        model=os.getenv('OPENAI_MODEL', 'gpt-5'),
                        messages=[
                            {"role": "system", "content": SYSTEM_INTENT},
                            {"role": "user", "content": enhanced_prompt_with_context},
                        ],
                    )
                    raw = resp.choices[0].message.content
                    print(f"LLM Response: {raw}")  # 디버깅
                    
                    # 마크다운 블록 제거
                    if raw.startswith('```json'):
                        raw = raw.replace('```json', '').replace('```', '').strip()
                    elif raw.startswith('```'):
                        raw = raw.replace('```', '').strip()
                    
                    intent = json.loads(raw)
                    
                    # args가 문자열인 경우 딕셔너리로 변환
                    if isinstance(intent.get('args'), str):
                        intent['args'] = {"text": intent['args']}
                except Exception as e:
                    print(f"LLM parsing error: {e}")  # 디버깅을 위한 에러 출력
                    print(f"LLM raw response: {raw}")  # 원본 응답도 출력
                    intent = None  # 폴백 로직으로 위임
            else:
                intent = None

            # 공통 폴백/보정 로직(LLM 미사용/실패 또는 부정확한 응답 시)
            def build_fallback(user_text: str) -> Dict[str, Any]:
                t = user_text.lower().strip()
                # 구독 목록 요구 - 직접적인 요청이거나 이전에 구독 목록 확인 질문 후 긍정 응답
                last_messages = state.get("messages", [])
                last_ai_message = ""
                if len(last_messages) >= 2:
                    last_ai = last_messages[-2]  # 사용자 메시지 바로 전의 AI 메시지
                    if hasattr(last_ai, 'content'):
                        last_ai_message = last_ai.content.lower()
                
                subscription_list_question = "구독 목록" in last_ai_message and "확인" in last_ai_message
                # 구독 목록 요청 패턴들
                list_patterns = [
                    "구독목록", "구독 목록", "구독리스트", "구독 리스트",
                    "내 구독", "subscriptions", "subscription list"
                ]
                has_list_keyword = any(k in t for k in ["목록", "list", "보여줘", "show", "리스트"])
                has_subscription = "구독" in user_text or "subscription" in t
                
                direct_request = (
                    any(pattern in user_text.replace(" ", "") for pattern in list_patterns) or
                    (has_subscription and has_list_keyword) or
                    "subscriptions" in t
                )
                confirmation_for_list = subscription_list_question and any(k in t for k in ["응", "네", "yes", "맞아", "그래"])
                
                if direct_request or confirmation_for_list:
                    return {"action": "list_subscriptions", "args": {}}
                # 현재 설정 질의
                if ("현재" in user_text or "지금" in user_text) and ("구독" in user_text or "subscription" in t):
                    ws = state.get("workingSubscription")
                    return {"action": "answer", "args": {"text": f"현재 작업 중인 구독은 {ws or '아직 선택되지 않았습니다.'}"}}
                if ("현재" in user_text or "지금" in user_text) and ("리소스그룹" in user_text or "resource group" in t or "rg" in t):
                    wrg = state.get("workingResourceGroup")
                    return {"action": "answer", "args": {"text": f"현재 작업 중인 리소스그룹은 {wrg or '아직 선택되지 않았습니다.'}"}}
                # 구독 선택 의도
                import re
                # 이전 응답이 구독 목록이면, 숫자/별칭만 입력해도 선택으로 간주
                last = state.get("lastResult") or {}
                last_was_subs = last.get("type") == "subscriptions"
                last_was_confirmation = last.get("type") == "confirmation"
                
                # 확인 응답 처리 (이전에 확인 요청이 있었고 긍정 응답인 경우)
                if last_was_confirmation and any(k in t for k in ["네", "예", "응", "yes", "맞아", "그래", "확인"]):
                    pending_sub = last.get("pendingSubscription")
                    if pending_sub:
                        return {"action": "choose_subscription", "args": {"candidate": pending_sub["id"], "confirmed": True}}
                
                # 구독 선택: 명시적 선택 키워드가 있지만 목록 요청이 아닌 경우
                has_selection_keyword = any(k in t for k in ["구독으로", "구독에서", "할게", "사용할게"]) or t.startswith("choose_subscription")
                # "선택"이 있어도 "목록"이나 "리스트"와 함께 있으면 목록 요청으로 간주
                selection_not_list = "선택" in t and not any(k in t for k in ["목록", "리스트", "보여줘"])
                explicit_selection = has_selection_keyword or selection_not_list
                # 구독 목록 후 번호나 구독 이름 패턴 매칭
                number_or_name_after_list = last_was_subs and (
                    re.search(r'^\d+\s*번?$', user_text) or  # "3번" 또는 "3"
                    re.search(r'^\d+$', user_text) or        # 순수 숫자
                    any(sub_keyword in t for sub_keyword in ["145", "144", "icstr", "dev"]) or  # 구독 식별 키워드
                    re.search(r'^sub-', user_text.lower())   # 구독 이름 패턴
                )
                
                if explicit_selection or number_or_name_after_list:
                    # 후보 추출: 정확한 패턴 매칭 우선
                    cand = user_text
                    
                    # 1. choose_subscription 명령어
                    m = re.search(r"choose_subscription\s+([^\s]+)", t)
                    if m:
                        cand = m.group(1)
                    # 2. UUID 패턴
                    elif re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", t):
                        m2 = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", t)
                        cand = m2.group(0)
                    # 3. 숫자 패턴 추출 ("3번", "3번으로", "145번" 등)
                    else:
                        # 먼저 "N번" 패턴 시도
                        m3 = re.search(r'(\d+)\s*번', user_text)
                        if m3:
                            cand = m3.group(1)
                            print(f"DEBUG: Extracted number from 'N번' pattern: '{cand}'")
                        # 순수 숫자 패턴 (구독 목록 후에만)
                        elif last_was_subs and re.search(r'^\d+$', user_text):
                            cand = user_text.strip()
                            print(f"DEBUG: Extracted pure number: '{cand}'")
                        else:
                            print(f"DEBUG: No number pattern matched, using original: '{cand}'")
                    args: Dict[str, Any] = {"candidate": cand}
                    if "리소스그룹" in t or " rg" in t:
                        args["listAfter"] = True
                    return {"action": "choose_subscription", "args": args}
                # 리소스 그룹 생성 - 이름 추출 시도
                if ("리소스 그룹" in user_text or "resource group" in t) and any(k in t for k in ["만들", "생성", "create"]):
                    params: Dict[str, Any] = {}
                    # 이름 추출: "이름은 jiwoo-test3" 패턴
                    mname = re.search(r"(?:이름(?:은|을|를)?)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)", user_text)
                    if mname:
                        params["name"] = mname.group(1)
                        params["location"] = ENFORCED_LOCATION_VALUE  # 기본 리전
                        # 바로 생성 시도
                        return {
                            "action": "apply",
                            "args": {
                                "resourceType": "Microsoft.Resources/resourceGroups",
                                "action": "create", 
                                "params": params
                            }
                        }
                    else:
                        # 이름이 없으면 정책 확인 후 요청
                        scope = state.get("workingSubscription") or ""
                        return {"action": "check_policy_and_ask", "args": {"scope": scope, "user_text": user_text, "resourceType": "Microsoft.Resources/resourceGroups"}}
                if ("리소스 그룹" in user_text or "resource group" in t) and any(k in t for k in ["삭제", "지워", "delete"]):
                    # LLM이 처리하도록 위임 - 복잡한 룰 대신 LLM이 리소스 그룹 이름을 추출하도록
                    return {"action": "apply", "args": {"scope": state.get("workingSubscription", ""), "resourceType": "Microsoft.Resources/resourceGroups", "action": "delete", "params": {"user_request": user_text}}}
                # 리소스 그룹 목록 - 더 포괄적인 패턴
                rg_list_patterns = [
                    "리소스그룹", "리소스 그룹", "rg", "resource group",
                    "머있는데", "뭐있어", "목록", "리스트"
                ]
                if (any(pattern in user_text for pattern in rg_list_patterns) and 
                    any(k in t for k in ["목록", "리스트", "list", "보여", "있는", "머있"])) or t.startswith("rgs"):
                    return {"action": "list_rgs", "args": {}}
                
                # 하드코딩된 리소스 생성 패턴들을 제거하고 LLM이 처리하도록 함
                # LLM이 적절한 MCP 툴을 선택할 것임
                
                # 리소스그룹 선택(간단): rg-명시 혹은 목록 직후 텍스트
                last_was_rgs = last.get("type") == "resourceGroups"
                if last_was_rgs or ("리소스그룹" in user_text and any(k in t for k in ["사용", "선택", "작업"])) or re.search(r"\brg-", t):
                    return {"action": "choose_rg", "args": {"candidate": user_text}}
                
                # 정책 조회 요청
                if any(k in t for k in ["정책", "폴리시", "policy", "제약", "규칙"]) and any(k in t for k in ["조회", "확인", "보여줘", "알려줘", "show"]):
                    scope = state.get("workingSubscription")
                    if not scope:
                        return {"action": "answer", "args": {"text": "정책을 조회하려면 먼저 구독을 선택해야 합니다."}}
                    return {"action": "plan", "args": {"scope": scope, "mode": "constraints"}}

                # 생성 가능 리소스(권한/카탈로그) 질의 → capabilities
                if (any(k in t for k in ["무슨", "어떤"]) and any(k in t for k in ["리소스", "서비스"]) and any(k in t for k in ["만들", "생성", "가능", "할 수"])) or "capabilities" in t:
                    sub = state.get("workingSubscription")
                    if not sub:
                        return {"action": "answer", "args": {"text": "리소스를 무엇을 만들 수 있는지 확인하려면 먼저 구독을 선택해주세요."}}
                    # RG가 선택되어 있으면 RG 스코프 사용
                    rg = state.get("workingResourceGroup")
                    scope = sub if not rg else f"{sub}/resourceGroups/{rg}"
                    return {"action": "plan", "args": {"scope": scope, "mode": "capabilities"}}

                # 현재 존재하는 리소스 목록 조회
                if (any(k in t for k in ["리소스", "자원"]) and any(k in t for k in ["목록", "리스트", "알려줘", "보여줘", "list"]) and not any(k in t for k in ["만들", "생성", "create"])):
                    sub = state.get("workingSubscription")
                    if not sub:
                        return {"action": "answer", "args": {"text": "이 구독의 리소스를 보려면 먼저 구독을 선택해주세요."}}
                    rg = state.get("workingResourceGroup")
                    scope = sub if not rg else f"{sub}/resourceGroups/{rg}"
                    return {"action": "list_resources", "args": {"scope": scope}}
                
                # 사용자 불만이나 오류 피드백 처리
                if any(k in t for k in ["왜이래", "제대로안", "안되", "오류", "에러", "문제", "맘대로", "이상해"]):
                    return {"action": "answer", "args": {"text": "죄송합니다. 명확하지 않았네요. 다시 시도해보겠습니다.\n\n도움이 필요하시면:\n- '구독 목록 보여줘' - 사용 가능한 구독 확인\n- '145번 구독으로 할게' - 특정 구독 선택\n- '리소스 그룹 만들어줘' - 리소스 그룹 생성\n\n어떤 작업을 도와드릴까요?"}}
                
                # 간단한 fallback만 유지 (LLM이 대부분 처리)
                pass
                
                # 기본 응답
                return {"action": "answer", "args": {"text": "도움말: '내 구독 목록', '이 구독으로 할게 <SUB_ID>', '리소스그룹 목록', 'plan/apply' 등을 입력해 보세요."}}

            # LLM 결과가 없거나 부족하면 폴백 적용
            if not intent or not isinstance(intent, dict) or not intent.get("action"):
                intent = build_fallback(text)
            state["intent"] = intent
            return state

        async def act(state: AgentState) -> AgentState:
            intent = state.get("intent") or {}
            action = intent.get("action")
            args = intent.get("args") or {}
            result: Dict[str, Any] = {}

            # fill from session state
            scope = args.get("scope")
            if not scope and state.get("workingSubscription"):
                scope = state["workingSubscription"]

            if action == "list_subscriptions":
                subs = await self.client.list_subscriptions()
                result = {"type": "subscriptions", "items": subs}
            elif action == "choose_subscription":
                # 개선된 구독 선택 로직
                cand = args.get("candidate") or args.get("subscription") or args.get("id") or args.get("subscriptionId") or args.get("subscription_id") or ""
                user_input = str(cand).strip()
                subs = await self.client.list_subscriptions()
                
                print(f"DEBUG: choose_subscription called with candidate='{cand}', user_input='{user_input}'")
                sub_names = [f"{i+1}. {s['name']}" for i, s in enumerate(subs)]
                print(f"DEBUG: Available subscriptions: {sub_names}")

                matched_sub = None
                
                # 1순위: 목록 순번 매칭 (1번, 2번, 3번)
                import re
                list_number_match = re.search(r'^(\d+)\s*번?$', user_input)
                print(f"DEBUG: list_number_match = {list_number_match}")
                if list_number_match:
                        try:
                            number = int(list_number_match.group(1))
                            index = number - 1  # 1-based to 0-based
                            print(f"DEBUG: number={number}, index={index}, len(subs)={len(subs)}")
                            
                            # 유효한 범위인지 확인 (1~3번만 허용)
                            if number < 1 or number > len(subs):
                                print(f"DEBUG: Number {number} is out of range (1-{len(subs)})")
                                # 숫자가 범위를 벗어나면 이름/키워드 매칭으로 넘어감
                            else:
                                # Prefer the order that was shown to the user in the previous subscriptions list
                                last_res = state.get("lastResult") or {}
                                last_items = last_res.get("items") if last_res.get("type") == "subscriptions" else None
                                source = last_items if isinstance(last_items, list) and last_items else subs
                                if 0 <= index < len(source):
                                    matched_sub = source[index]
                                    print(f"DEBUG: Matched subscription from {'last_items' if source is last_items else 'fresh subs'}: {matched_sub['name']}")
                        except (ValueError, IndexError) as e:
                            print(f"DEBUG: Number matching error: {e}")
                            pass
                
                # 2순위: 정확한 구독 이름 매칭
                if not matched_sub:
                    for sub in subs:
                        if user_input.lower() == sub["name"].lower():
                            matched_sub = sub
                            break
                
                # 3순위: 전체 ID 매칭
                if not matched_sub:
                    for sub in subs:
                        if user_input.lower() in sub["id"].lower():
                            matched_sub = sub
                            break
                
                # 4순위: 구독 이름에 포함된 숫자나 키워드 매칭 (예: "145", "icstr")
                if not matched_sub:
                    # 숫자 추출 (145, 144 등)
                    numbers = re.findall(r'\d+', user_input)
                    # 키워드 추출 (icstr, dev 등)
                    keywords = re.findall(r'[a-zA-Z]+', user_input.lower())
                    
                    # 정확한 숫자 매칭을 위해 더 엄격하게 검사
                    for sub in subs:
                        sub_name = sub["name"].lower()
                        # 숫자 매칭 - 끝에 오는 숫자로 정확히 매칭
                        for num in numbers:
                            if sub_name.endswith(f"-{num}") or f"-{num}-" in sub_name:
                                matched_sub = sub
                                break
                        if matched_sub:
                            break
                        # 키워드 매칭
                        for keyword in keywords:
                            if len(keyword) > 2 and keyword in sub_name:  # 2글자 이상만
                                matched_sub = sub
                                break
                        if matched_sub:
                            break

                if not matched_sub:
                    # 마지막 시도: 부분 문자열 매칭
                    for sub in subs:
                        if user_input.lower() in sub["name"].lower() or user_input in sub["id"]:
                            matched_sub = sub
                            print(f"DEBUG: Final fallback match: {matched_sub['name']}")
                            break
                    
                if not matched_sub:
                    result = {"error": "subscription not found", "hint": f"'{cand}' 구독을 찾을 수 없습니다. 정확한 이름이나 번호를 입력해주세요."}
                else:
                    # 확인이 필요한지 판단
                    needs_confirmation = False
                    
                    # 명확하지 않은 매칭인 경우에만 확인 필요
                    # 1순위(번호 매칭)나 2순위(정확한 이름 매칭)는 명확하므로 바로 선택
                    # 3순위(ID 매칭)나 4순위(부분 매칭)는 모호할 수 있으므로 확인 필요
                    if not (list_number_match or user_input.lower() == matched_sub["name"].lower()):
                        # ID 매칭이나 부분 매칭인 경우 확인 필요
                        needs_confirmation = True
                    
                    # 명시적 확인 플래그가 있는 경우 바로 선택
                    if args.get("confirmed"):
                        needs_confirmation = False
                    
                    if needs_confirmation:
                        # 확인 요청
                        result = {
                            "type": "confirmation", 
                            "message": f"이 구독을 선택하시겠습니까?\n• 이름: {matched_sub['name']}\n• ID: {matched_sub['id']}\n\n'네' 또는 '예'라고 답하시면 선택됩니다.",
                            "pendingSubscription": matched_sub
                        }
                    else:
                        # 바로 선택
                        state["workingSubscription"] = matched_sub["id"]
                        result = {"ok": True, "workingSubscription": matched_sub["id"], "subscriptionName": matched_sub["name"]}

                    if args.get("listAfter") and not needs_confirmation:
                        rgs = await self.client.list_resource_groups(matched_sub["id"])
                        state["workingSubscription"] = matched_sub["id"]
                        rg_candidate = args.get("candidate") or ""
                        matched_rg = _match_resource_group_name(rgs, rg_candidate)
                        if matched_rg:
                            state["workingResourceGroup"] = matched_rg
                            result = {
                                "ok": True,
                                "workingSubscription": matched_sub["id"],
                                "subscriptionName": matched_sub["name"],
                                "workingResourceGroup": matched_rg,
                                "note": f"리소스 그룹 '{matched_rg}'도 같이 설정했어요."
                            }
                        else:
                            result = {
                                "type": "resourceGroups",
                                "items": rgs,
                                "workingSubscription": matched_sub["id"],
                                "subscriptionName": matched_sub["name"],
                                "autoSelected": False
                            }
            elif action == "list_rgs":
                sub = state.get("workingSubscription")
                if not sub:
                    # 구독이 선택되지 않았으면 모든 구독의 리소스 그룹을 보여줌
                    subs = await self.client.list_subscriptions()
                    if subs:
                        # 첫 번째 구독을 기본으로 선택하고 리소스 그룹 조회
                        default_sub = subs[0]["id"]
                        state["workingSubscription"] = default_sub
                        rgs = await self.client.list_resource_groups(default_sub)
                        result = {
                            "type": "resourceGroups", 
                            "items": rgs, 
                            "workingSubscription": default_sub,
                            "subscriptionName": subs[0]["name"],
                            "autoSelected": True
                        }
                    else:
                        result = {"error": "사용 가능한 구독이 없습니다."}
                else:
                    rgs = await self.client.list_resource_groups(sub)
                    result = {"type": "resourceGroups", "items": rgs}
            elif action == "choose_rg":
                try:
                    print(f"DEBUG: choose_rg action - candidate: {args.get('candidate')}")
                    sub = state.get("workingSubscription")
                    print(f"DEBUG: choose_rg - workingSubscription: {sub}")
                    if not sub:
                        result = {"error": "먼저 구독을 선택해주세요."}
                    else:
                        cand = (args.get("candidate") or "").strip()
                        print(f"DEBUG: choose_rg - candidate after strip: '{cand}'")
                        rgs = await self.client.list_resource_groups(sub)
                        print(f"DEBUG: choose_rg - found {len(rgs)} resource groups")
                        target = None
                        lc = cand.lower()
                        # 1) 이름 토큰 추출(rg- 접두 토큰들)
                        import re
                        name_tokens = re.findall(r"\b(rg[-A-Za-z0-9_.\-]+)\b", lc)
                        rg_names = [rg.get("name", "") for rg in rgs]
                        rg_names_l = [n.lower() for n in rg_names]
                        
                        # 1-a) 토큰과 정확히 일치하는 RG 우선 선택
                        exact_matches = []
                        for tok in name_tokens:
                            tok_l = tok.lower()
                            for n_l, n in zip(rg_names_l, rg_names):
                                if tok_l == n_l:
                                    exact_matches.append(n)
                        if exact_matches:
                            # 여러 개인 경우 길이가 긴 이름을 우선(더 구체적)
                            target = max(exact_matches, key=lambda x: len(x))
                        
                        # 1-b) 정확 일치가 없다면, 문장 내 포함된 RG들 중 가장 긴 이름 선택
                        if not target:
                            contained = []
                            for n_l, n in zip(rg_names_l, rg_names):
                                if n_l and n_l in lc:
                                    contained.append(n)
                            if contained:
                                target = max(contained, key=lambda x: len(x))
                        
                        # 2) 일반 토큰 분리 후 마지막 키워드 기준 부분 일치(보조)
                        if not target and rgs:
                            toks = [x for x in re.split(r"\s+|,|/|\\|\\(|\\)", lc) if x]
                            if toks:
                                key = toks[-1]
                                part_matches = [rg.get("name", "") for rg in rgs if key in rg.get("name", "").lower()]
                                if part_matches:
                                    target = max(part_matches, key=lambda x: len(x))
                        
                        if target:
                            state["workingResourceGroup"] = target
                            print(f"DEBUG: Resource group selected: {target}")
                            result = {"ok": True, "workingResourceGroup": target}
                        else:
                            result = {"error": "리소스그룹을 찾지 못했습니다."}
                except Exception as e:
                    print(f"ERROR: choose_rg failed: {e}")
                    result = {"error": f"리소스 그룹 선택 중 오류가 발생했습니다: {str(e)}"}
            elif action == "find_resources":
                scope = args.get("scope")
                # 스마트 scope 자동 설정
                if not scope:
                    working_rg = state.get("workingResourceGroup")
                    working_sub = state.get("workingSubscription")
                    if working_rg and working_sub:
                        scope = f"{working_sub}/resourceGroups/{working_rg}"
                        print(f"DEBUG: Auto-set scope for find_resources: {scope}")
                    elif working_sub:
                        scope = working_sub
                        print(f"DEBUG: Auto-set scope for find_resources (sub only): {scope}")
                
                resource_type = args.get("resourceType")
                keyword = args.get("keyword", "")
                if not scope:
                    result = {"error": "scope가 필요합니다. 먼저 구독이나 리소스 그룹을 선택해주세요."}
                else:
                    # azs.list_resources 툴 사용
                    list_res = await self._call_tool("azs.list_resources", {"scope": scope, "resourceType": resource_type})
                    if list_res.get("is_error"):
                        result = {"error": f"리소스 조회 실패: {list_res}"}
                    else:
                        resources = list_res.get("content", [{}])[0].get("resources", [])
                        if not resources:
                            result = {"answer": f"{keyword.upper()} 리소스가 이 구독에서 발견되지 않았습니다."}
                        else:
                            # 리소스 그룹별로 그룹화
                            rg_map = {}
                            for res in resources:
                                res_id = res.get("id", "")
                                # /subscriptions/.../resourceGroups/rg-name/... 에서 rg-name 추출
                                if "/resourceGroups/" in res_id:
                                    parts = res_id.split("/resourceGroups/")
                                    if len(parts) > 1:
                                        rg_name = parts[1].split("/")[0]
                                        if rg_name not in rg_map:
                                            rg_map[rg_name] = []
                                        rg_map[rg_name].append(res.get("name", ""))
                            
                            if rg_map:
                                lines = [f"🔍 {keyword.upper()} 리소스 검색 결과:"]
                                for rg_name, resource_names in rg_map.items():
                                    lines.append(f"📁 {rg_name}:")
                                    for i, res_name in enumerate(resource_names, 1):
                                        lines.append(f"  {i}. {res_name}")
                                result = {"answer": "\n".join(lines)}
            elif action == "list_resources":
                # List all resources under current scope (subscription or RG)
                scope = args.get("scope")
                if not scope:
                    sub = state.get("workingSubscription")
                    rg = state.get("workingResourceGroup")
                    if not sub:
                        result = {"error": "먼저 구독을 선택해주세요."}
                    else:
                        scope = sub if not rg else f"{sub}/resourceGroups/{rg}"
                if scope:
                    list_res = await self._call_tool("azs.list_resources", {"scope": scope})
                    if list_res.get("is_error"):
                        result = {"error": f"리소스 목록 조회 실패: {list_res}"}
                    else:
                        items = list_res.get("content", [{}])[0].get("resources", [])
                        if not items:
                            result = {"answer": "이 스코프에서 리소스를 찾지 못했습니다."}
                        else:
                            # Group by type and show counts + samples
                            type_map = {}
                            for it in items:
                                typ = it.get("type", "(unknown)")
                                name = it.get("name", "")
                                type_map.setdefault(typ, []).append(name)
                            lines = ["📦 리소스 요약:"]
                            for typ, names in sorted(type_map.items()):
                                sample = ", ".join(names[:5])
                                more = " ..." if len(names) > 5 else ""
                                lines.append(f"- {typ}: {len(names)}개 — {sample}{more}")
                            result = {"answer": "\n".join(lines)}
            elif action == "check_policy_and_ask":
                scope = args.get("scope")
                resource_type = args.get("resourceType")
                user_text = args.get("user_text", "")
                
                if not scope:
                    result = {"error": "scope가 필요합니다."}
                else:
                    # 정책 제약사항 조회 (constraints 모드)
                    constraints_res = await self._call_tool("azs.plan", {"scope": scope, "mode": "constraints"})
                    
                    if constraints_res.get("is_error"):
                        result = {"error": f"정책 조회 실패: {constraints_res}"}
                    else:
                        constraints = constraints_res.get("content", [{}])[0]
                        allowed_locations = constraints.get("allowedLocations", [])
                        required_tags = constraints.get("requiredTags", [])
                        
                        # 리소스 그룹 이름 추출
                        import re
                        rg_name_match = re.search(r'\b(rg-[\w-]+)\b', user_text)
                        if rg_name_match:
                            rg_name = rg_name_match.group(1)
                        else:
                            # 간단한 패턴으로 이름 추출
                            words = user_text.split()
                            rg_name = None
                            for i, word in enumerate(words):
                                if word in ["리소스그룹", "만들어줘", "생성해줘"] and i > 0:
                                    rg_name = words[i-1]
                                    break
                        
                        if not rg_name:
                            rg_name = "새-리소스그룹"
                        
                        # 정책 정보를 바탕으로 사용자에게 확인 요청
                        if allowed_locations:
                            if len(allowed_locations) == 1:
                                location = allowed_locations[0]
                                msg = f"현재 구독의 정책에 따라 리소스는 **{location}** 리전에만 생성 가능합니다.\n\n"
                                msg += f"'{rg_name}' 리소스 그룹을 {location} 리전에 생성하시겠습니까?\n\n"
                                msg += "**진행하려면 '진행해줘' 또는 '생성해줘'라고 입력하세요.**"
                            else:
                                locations_str = ", ".join(allowed_locations)
                                msg = f"현재 구독의 정책에 따라 다음 리전에서만 생성 가능합니다:\n**{locations_str}**\n\n"
                                msg += f"'{rg_name}' 리소스 그룹을 어느 리전에 생성하시겠습니까? (예: {ENFORCED_LOCATION_VALUE})"
                        else:
                            msg = f"'{rg_name}' 리소스 그룹 생성을 진행하시겠습니까?\n\n**리전을 지정해주세요 (예: {ENFORCED_LOCATION_VALUE})**"
                        
                        # pending 상태로 저장
                        state["pendingTool"] = "azs.apply"
                        state["pendingArgs"] = {
                            "scope": scope, 
                            "resourceType": resource_type, 
                            "action": "create", 
                            "params": {"name": rg_name}
                        }
                        # 회사 정책: 모든 리소스는 지정된 리전에만 생성 가능
                        state["pendingArgs"]["params"]["location"] = ENFORCED_LOCATION_VALUE
                        state["pendingMissing"] = []  # location 자동 설정됨
                        print("DEBUG: Auto-set location via policy for resource group creation")
                        
                        result = {"answer": msg}
            elif action == "plan":
                # 스마트 scope 자동 설정
                if not scope:
                    working_rg = state.get("workingResourceGroup")
                    working_sub = state.get("workingSubscription")
                    if working_rg and working_sub:
                        scope = f"{working_sub}/resourceGroups/{working_rg}"
                        print(f"DEBUG: Auto-set scope for plan: {scope}")
                    elif working_sub:
                        scope = working_sub
                        print(f"DEBUG: Auto-set scope for plan (sub only): {scope}")
                
                if not scope:
                    result = {"error": "scope가 필요합니다. 먼저 구독이나 리소스 그룹을 선택해주세요."}
                else:
                    mode = args.get("mode", "capabilities")
                    plan_res = await self._call_tool("azs.plan", {"scope": scope, "mode": mode, **{k: v for k, v in args.items() if k not in ["scope", "mode"]}})
                    result = {"tool": "azs.plan", "response": plan_res}
            elif action == "apply":
                # 스마트 scope 자동 설정
                print(f"DEBUG: apply action - initial scope: {scope}")
                if not scope:
                    # 1. workingResourceGroup이 있으면 RG scope 사용
                    working_rg = state.get("workingResourceGroup")
                    working_sub = state.get("workingSubscription")
                    
                    if working_rg and working_sub:
                        scope = f"{working_sub}/resourceGroups/{working_rg}"
                        print(f"DEBUG: Auto-set scope from workingResourceGroup: {scope}")
                    # 2. params.resourceGroup이 있으면 해당 RG scope 사용  
                    elif args.get("params", {}).get("resourceGroup") and working_sub:
                        rg_name = args["params"]["resourceGroup"]
                        scope = f"{working_sub}/resourceGroups/{rg_name}"
                        print(f"DEBUG: Auto-set scope from params.resourceGroup: {scope}")
                    # 3. 구독만 있으면 구독 scope 사용
                    elif working_sub:
                        scope = working_sub
                        print(f"DEBUG: Auto-set scope from workingSubscription: {scope}")
                else:
                    print(f"DEBUG: Using provided scope: {scope}")
                
                if not scope:
                    result = {"error": "scope가 필요합니다. 먼저 구독이나 리소스 그룹을 선택해주세요."}
                else:
                    # 기본값: action=create
                    apply_args = {"action": "create", **{k: v for k, v in args.items() if k != "scope"}, "scope": scope}
                    print(f"DEBUG: apply_args created: {apply_args}")
                    # 작업 중인 리소스그룹을 자동 보강 (RG 외 리소스 생성 시)
                    rt = apply_args.get("resourceType")
                    if rt and rt != "Microsoft.Resources/resourceGroups":
                        p = apply_args.setdefault("params", {}) or {}
                        if not p.get("resourceGroup") and state.get("workingResourceGroup"):
                            p["resourceGroup"] = state["workingResourceGroup"]
                    # 간단 슬롯 채우기 + pending 병합
                    if apply_args.get("resourceType") == "Microsoft.Resources/resourceGroups" and apply_args.get("action", "create") == "create":
                        p = apply_args.setdefault("params", {}) or {}
                        # merge pending if exists
                        if state.get("pendingArgs") and isinstance(state["pendingArgs"], dict):
                            pp = state["pendingArgs"].get("params") if isinstance(state["pendingArgs"], dict) else None
                            if isinstance(pp, dict):
                                p.setdefault("name", pp.get("name"))
                                p.setdefault("location", pp.get("location"))
                        missing = []
                        if not p.get("name"):
                            missing.append("name")
                        policy_note_rg = None
                        if not p.get("location"):
                            p["location"] = ENFORCED_LOCATION_VALUE
                            policy_note_rg = ENFORCED_LOCATION_MESSAGE
                            print("DEBUG: Auto-set location via policy for resource group")
                        if missing:
                            state["pendingTool"] = "azs.apply"
                            state["pendingArgs"] = apply_args
                            state["pendingMissing"] = missing
                            ask = []
                            if "name" in missing:
                                ask.append("name(리소스 그룹 이름)")
                            if "location" in missing:
                                ask.append("location(리전)")
                            message = f"리소스 그룹 생성에 필요한 {', '.join(ask)} 값을 알려주세요."
                            if policy_note_rg:
                                message += f"\n\nℹ️ {policy_note_rg}"
                            message += "\n\n예시: name=rg-demo"
                            result = {"answer": message}
                            state["lastResult"] = result
                            return state
                        else:
                            # clear pending on success path
                            state.pop("pendingTool", None)
                            state.pop("pendingArgs", None)
                            state.pop("pendingMissing", None)
                    # Generic missing-parameter handling for non-RG resources using azs.spec
                    if apply_args.get("resourceType") and apply_args.get("resourceType") != "Microsoft.Resources/resourceGroups" and apply_args.get("action", "create") == "create":
                        try:
                            spec_res = await self._call_tool("azs.spec", {"resourceType": apply_args.get("resourceType")})
                            spec = (spec_res.get("content") or [{}])[0].get("spec") if isinstance(spec_res, dict) else None
                        except Exception:
                            spec = None
                        if isinstance(spec, dict):
                            p = apply_args.setdefault("params", {}) or {}
                            required = [x.get("key") for x in spec.get("params", []) if x.get("required")]
                            missing = [k for k in required if not p.get(k)]
                            
                            policy_note_general = None
                            if "location" in missing:
                                if p.get("location") != ENFORCED_LOCATION_VALUE:
                                    p["location"] = ENFORCED_LOCATION_VALUE
                                    policy_note_general = ENFORCED_LOCATION_MESSAGE
                                    print("DEBUG: Auto-set location via policy for general resource")
                                missing = [k for k in missing if k != "location"]

                            if missing:
                                asks = _format_param_prompts(spec, missing)
                                if not asks:
                                    asks = [f"- {key}" for key in missing]
                                msg_lines = ["리소스 생성에 필요한 값들이 부족합니다. 아래 값을 알려주세요:", ""]
                                msg_lines.extend(asks)
                                if policy_note_general:
                                    msg_lines.extend(["", f"ℹ️ {policy_note_general}"])
                                example_keys = ", ".join(f"{key}=값" for key in missing[:2]) or "param=값"
                                msg_lines.extend(["", "여러 값을 쉼표로 구분해 한 줄로 입력해주세요.", f"예시 형식: {example_keys}"])
                                msg = "\n".join(msg_lines)
                                state["pendingTool"] = "azs.apply"
                                state["pendingArgs"] = apply_args
                                state["pendingMissing"] = missing
                                result = {"answer": msg}
                                state["lastResult"] = result
                                return state
                    # 리소스 그룹 생성은 확인 없이 바로 실행 (회사 정책상 특정 리전만 허용)
                    rt = apply_args.get("resourceType", "")
                    is_rg_creation = rt == "Microsoft.Resources/resourceGroups" and apply_args.get("action") == "create"
                    
                    print(f"DEBUG: apply_args resourceType={rt}, action={apply_args.get('action')}, is_rg_creation={is_rg_creation}")
                    
                    # Confirmation gate for create/delete/update (리소스 그룹 제외)
                    if (apply_args.get("action") in ("create", "delete", "update") and 
                        not args.get("confirmed") and 
                        not is_rg_creation):
                        # Build summary
                        p = apply_args.get("params", {}) or {}
                        name = p.get("name") or p.get("resourceGroup") or "(이름 미정)"
                        rg = p.get("resourceGroup") or state.get("workingResourceGroup") or "(RG 미정)"
                        loc = p.get("location") or ENFORCED_LOCATION_VALUE
                        act_kor = "생성" if apply_args.get("action") == "create" else "삭제"
                        message = (
                            f"다음 작업을 {act_kor}할까요?\n"
                            f"• 타입: {rt}\n"
                            f"• 이름: {name}\n"
                            f"• 리소스 그룹: {rg}\n"
                            f"• 위치: {loc}\n\n"
                            "예/아니오 버튼으로 선택해주세요."
                        )
                        # 확인 대기 상태 저장
                        state["pendingTool"] = "azs.apply"
                        state["pendingArgs"] = apply_args
                        result = {"type": "confirmation", "message": message, "pendingApply": apply_args}
                    else:
                        # 바로 실행 (리소스 그룹 생성 또는 confirmed=True)
                        print(f"DEBUG: About to call azs.apply with args: {apply_args}")
                        apply_res = await self._call_tool("azs.apply", apply_args)
                        print(f"DEBUG: azs.apply response: {apply_res}")
                        
                        # 정책 위반 에러 감지 시 자동으로 ServiceNow 신청서 생성
                        if (apply_res.get("status") == "error" and 
                            "RequestDisallowedByPolicy" in str(apply_res.get("error", ""))):
                            
                            print("DEBUG: Policy violation detected, creating ServiceNow request")
                            
                            # ServiceNow 신청서 생성
                            request_args = {
                                "blockedItems": [apply_res.get("error", "정책 위반")],
                                "scope": apply_args.get("scope", ""),
                                "resourceType": apply_args.get("resourceType", ""),
                                "resourceName": apply_args.get("params", {}).get("name", ""),
                                "location": apply_args.get("params", {}).get("location", ENFORCED_LOCATION_VALUE),
                                "reason": "정책 위반으로 인한 리소스 생성 실패"
                            }
                            
                            servicenow_res = await self._call_tool("azs.request", request_args)
                            print(f"DEBUG: ServiceNow request response: {servicenow_res}")
                            
                            # 정책 위반 메시지 + ServiceNow 신청서 정보 결합
                            error_msg = str(apply_res.get("error", ""))
                            if "RequestDisallowedByPolicy" in error_msg:
                                # 정책 위반 상세 정보 파싱
                                policy_msg = "정책에 의해 리소스 생성이 거부되었습니다."
                                if "Azure Key Vault should have firewall enabled" in error_msg:
                                    policy_msg = "Key Vault 생성이 정책에 의해 거부됨 (RequestDisallowedByPolicy).\n"
                                    policy_msg += "적용 정책: \"[Deny] Azure Key Vault should have firewall enabled\" (효과: Deny, 버전 3.3.0, 범위: Management Group KT).\n"
                                    policy_msg += "위반 사유: 방화벽 미활성화(현재 defaultAction=Allow, 요구=Deny) 및/또는 Public Network Access 미비활성화(publicNetworkAccess ≠ Disabled).\n"
                                    policy_msg += "조치: Key Vault 방화벽 활성화(네트워크 기본 동작 Deny로 설정) 또는 Public Network Access를 Disabled로 설정 후 재배포."
                                
                                # ServiceNow 신청서 결과 확인
                                if servicenow_res.get("status") == "success" or "request" in str(servicenow_res):
                                    policy_msg += f"\n\n✅ **ServiceNow 신청서가 자동 생성되었습니다.**"
                                    if "ticketNumber" in str(servicenow_res):
                                        ticket_match = re.search(r'"ticketNumber"\s*:\s*"([^"]+)"', str(servicenow_res))
                                        if ticket_match:
                                            policy_msg += f"\n📋 티켓 번호: {ticket_match.group(1)}"
                                    policy_msg += f"\n🔗 신청서 내용: 정책 예외 요청 - {apply_args.get('resourceType', '')} 리소스 생성 허용"
                                else:
                                    policy_msg += f"\n\n⚠️ ServiceNow 신청서 생성 실패: {servicenow_res.get('error', '알 수 없는 오류')}"
                                
                                result = {"tool": "azs.apply", "response": {"status": "error", "error": policy_msg, "policy_violation": True, "servicenow_created": True}}
                            else:
                                result = {"tool": "azs.apply", "response": apply_res}
                        else:
                            result = {"tool": "azs.apply", "response": apply_res}
            elif action == "request":
                req = await self._call_tool("azs.request", args)
                result = {"tool": "azs.request", "response": req}
            # MCP 툴 직접 호출 (LLM이 MCP 툴 이름을 반환한 경우)
            elif action.startswith("azs."):
                tool_result = await self._call_tool(action, args)
                result = {"tool": action, "response": tool_result}
            else:
                # LLM이 다양한 키를 사용할 수 있음 (text, message, content 등)
                answer_text = args.get("text") or args.get("message") or args.get("content") or "알겠습니다."
                result = {"answer": answer_text}

            state["lastResult"] = result
            return state

        async def respond(state: AgentState) -> AgentState:
            res = state.get("lastResult") or {}
            text = None
            if "answer" in res:
                text = res["answer"]
            elif res.get("type") == "subscriptions":
                items = res.get("items", [])
                names = []
                for i, s in enumerate(items, 1):
                    names.append(f"{i}. {s['name']}\n   ID: {s['id']}")
                text = "📋 구독 목록:\n" + "\n".join(names) + "\n\n💡 구독을 선택하려면:\n• 번호로 선택: '1번', '2번', '3번'\n• 이름으로 선택: 'sub-az01-co001501-sbox-poc-145'\n• 자연스럽게: '145번 구독으로 할게'"
            elif res.get("type") == "resourceGroups":
                items = res.get("items", [])
                names = []
                for i, rg in enumerate(items, 1):
                    names.append(f"{i}. {rg['name']} ({rg.get('location', 'unknown')})")
                working_sub_name = res.get("subscriptionName", "")
                auto_selected = res.get("autoSelected", False)
                
                guidance = "\n\n💡 원하는 리소스 그룹 이름을 입력하거나 번호로 선택해 주세요." if items else ""
                if auto_selected:
                    sub_info = f" (자동 선택된 구독: {working_sub_name})"
                    text = (
                        f"📁 리소스 그룹 목록{sub_info}:\n" + "\n".join(names) +
                        "\n\n💡 다른 구독의 리소스 그룹을 보려면 먼저 구독을 선택해주세요." + guidance
                    )
                else:
                    sub_info = f" (구독: {working_sub_name})" if working_sub_name else ""
                    text = f"📁 리소스 그룹 목록{sub_info}:\n" + "\n".join(names) + guidance
            elif res.get("type") == "confirmation":
                text = f"❓ {res.get('message', '확인이 필요합니다.')}"
            elif res.get("ok") and res.get("workingSubscription"):
                subscription_name = res.get("subscriptionName", "")
                lines = ["✅ 구독이 선택되었습니다:"]
                if subscription_name:
                    lines.append(f"• 이름: {subscription_name}")
                lines.append(f"• ID: {res['workingSubscription']}")
                if res.get("workingResourceGroup"):
                    lines.append(f"• 리소스 그룹: {res['workingResourceGroup']}")
                note = res.get("note")
                if note:
                    lines.append(f"\nℹ️ {note}")
                else:
                    lines.append("\nℹ️ 삭제나 생성 등 다음 작업을 계속하려면 리소스 그룹과 리소스 정보를 알려주세요.")
                text = "\n".join(lines)
            elif res.get("ok") and res.get("workingResourceGroup"):
                text = f"✅ 리소스 그룹이 선택되었습니다: {res['workingResourceGroup']}"
            elif res.get("tool"):
                # MCP 툴 응답 요약 + 원문 저장
                resp = res.get("response", {})
                payload = resp
                if isinstance(resp, dict) and "content" in resp and isinstance(resp["content"], list) and resp["content"]:
                    payload = resp["content"][0]
                # 저장해 두어 '원문' 요청 시 제공
                state["lastRaw"] = payload if isinstance(payload, dict) else {"data": payload}
                
                # 리소스 목록의 경우 요약하지 않고 원본 표시
                if isinstance(payload, dict) and "resources" in payload:
                    # 리소스 목록 포맷팅
                    resources = payload.get("resources", [])
                    if resources:
                        text = f"📋 리소스 목록 (총 {len(resources)}개):\n\n"
                        for i, resource in enumerate(resources, 1):
                            name = resource.get("name", "unknown")
                            resource_type = resource.get("type", "unknown")
                            location = resource.get("location", "unknown")
                            text += f"{i}. **{name}** ({resource_type})\n   📍 위치: {location}\n\n"
                        text += "(원문 JSON이 필요하면 '원문'이라고 입력하세요)"
                    else:
                        text = "📋 리소스 목록이 비어있습니다."
                else:
                    # 기본 요약
                    summary = None
                    if self.llm and isinstance(payload, dict):
                        try:
                            r = self.llm.chat.completions.create(
                                model=os.getenv('OPENAI_MODEL', 'gpt-5'),
                                messages=[
                                    {"role":"system","content":"아래 JSON을 한국어로 3-5줄 핵심만 요약. 목록은 불릿."},
                                    {"role":"user","content": json.dumps(payload, ensure_ascii=False)}
                                ],
                            )
                            summary = r.choices[0].message.content
                        except Exception:
                            summary = None
                if not summary and isinstance(payload, dict):
                    if "allowedActions" in payload:  # capabilities
                        roles = ", ".join([r.get("name","?") for r in payload.get("effectiveRoles", [])]) or "-"
                        acts = payload.get("allowedActions", [])
                        not_acts = payload.get("notActions", [])
                        star = " (와일드카드)" if ("*" in acts or "*/read" in acts) else ""
                        sample = ", ".join(acts[:5]) if acts else "-"
                        
                        # VM 관련 제한사항 강조
                        vm_restrictions = [act for act in not_acts if "virtualMachines" in act or "virtualMachineScaleSets" in act]
                        net_restrictions = [act for act in not_acts if act.startswith("Microsoft.Network/") or act.startswith("microsoft.network/")]
                        cost_restrictions = [act for act in not_acts if "CostManagement" in act or "Consumption" in act]
                        auth_restrictions = [act for act in not_acts if "Authorization" in act or "Policy" in act]
                        
                        summary = f"- 역할: {roles}\n- 허용 액션: {len(acts)}개{star}\n- 예시: {sample}"
                        if not_acts:
                            summary += f"\n- 제한 액션: {len(not_acts)}개"
                            if vm_restrictions:
                                summary += f"\n  • VM 관련 제한: {len(vm_restrictions)}개 (runCommand, SerialConsole 등)"
                            if net_restrictions:
                                summary += f"\n  • 네트워크 관련 제한: {len(net_restrictions)}개 (ExpressRoute/VPN 등)"
                            if cost_restrictions:
                                summary += f"\n  • 비용 관리 제한: {len(cost_restrictions)}개 (CostManagement, Consumption)"
                            if auth_restrictions:
                                summary += f"\n  • 권한/정책 제한: {len(auth_restrictions)}개 (Authorization, Policy)"
                            
                            # 주요 제한사항 예시 (VM, 비용, 권한 관련)
                            key_restrictions = []
                            if vm_restrictions:
                                key_restrictions.extend(vm_restrictions[:2])
                            if net_restrictions:
                                key_restrictions.extend(net_restrictions[:2])
                            if cost_restrictions:
                                key_restrictions.extend(cost_restrictions[:2])
                            if auth_restrictions:
                                key_restrictions.extend(auth_restrictions[:2])
                            
                            if key_restrictions:
                                summary += f"\n- 주요 제한 예시: {', '.join(key_restrictions[:5])}"

                        # 역할별 상세가 있으면, 각 역할별로 간단 요약 블록 추가
                        role_details = payload.get("roleDetails") or []
                        if role_details:
                            blocks = []
                            for rd in role_details:
                                rname = (rd.get("role") or {}).get("name") or "(이름 없음)"
                                racts = rd.get("actions") or []
                                rnacts = rd.get("notActions") or []
                                r_star = " (와일드카드)" if ("*" in racts or "*/read" in racts) else ""
                                # 카테고리 요약
                                r_vm = [a for a in rnacts if "virtualMachines" in a or "virtualMachineScaleSets" in a]
                                r_net = [a for a in rnacts if a.startswith("Microsoft.Network/") or a.startswith("microsoft.network/")]
                                r_cost = [a for a in rnacts if "CostManagement" in a or "Consumption" in a]
                                r_auth = [a for a in rnacts if "Authorization" in a or "Policy" in a]
                                r_sample = ", ".join(racts[:3]) if racts else "-"
                                block = f"\n▶ 역할: {rname}\n  - 허용: {len(racts)}개{r_star} (예: {r_sample})\n  - 제한: {len(rnacts)}개"
                                if r_vm:
                                    block += f"\n    · VM: {len(r_vm)}개"
                                if r_net:
                                    block += f"\n    · 네트워크: {len(r_net)}개"
                                if r_cost:
                                    block += f"\n    · 비용: {len(r_cost)}개"
                                if r_auth:
                                    block += f"\n    · 권한/정책: {len(r_auth)}개"
                                # 대표 제한 2개
                                if rnacts:
                                    block += f"\n  - 제한 예시: {', '.join(rnacts[:2])}"
                                blocks.append(block)
                            if blocks:
                                summary += "\n" + "\n".join(blocks)
                    elif "allowedLocations" in payload or "requiredTags" in payload:  # constraints
                        locs = ", ".join(payload.get("allowedLocations", []) or []) or "(제약 없음)"
                        tags = ", ".join([t.get("key","?") for t in payload.get("requiredTags", [])]) or "(필수 태그 없음)"
                        denies = len(payload.get("denies", []))
                        summary = f"- 허용 리전: {locs}\n- 필수 태그: {tags}\n- 거부 규칙: {denies}개"
                    elif "status" in payload:  # check
                        status = payload.get("status")
                        reasons = payload.get("reasons", [])
                        hints = payload.get("hints", [])
                        rs = "\n  - ".join(reasons) if reasons else "없음"
                        hs = "\n  - ".join(hints) if hints else "없음"
                        summary = f"- 상태: {status}\n- 사유:\n  - {rs}\n- 힌트:\n  - {hs}"
                policy_note = None
                if isinstance(payload, dict):
                    policy_note = payload.get("policyNote")
                text = summary or json.dumps(payload, ensure_ascii=False, indent=2)
                if policy_note:
                    text += f"\n\nℹ️ {policy_note}"
                text += "\n\n(원문 JSON이 필요하면 ‘원문’이라고 입력하세요)"
            else:
                text = json.dumps(res, ensure_ascii=False)

            # append AI message
            msgs = state.get("messages", [])
            msgs.append(AIMessage(content=text))
            state["messages"] = msgs
            return state

        g.add_node("decide", decide)
        g.add_node("act", act)
        g.add_node("respond", respond)
        g.set_entry_point("decide")
        g.add_edge("decide", "act")
        g.add_edge("act", "respond")
        g.add_edge("respond", END)
        return g.compile(checkpointer=self.memory)

    async def handle_message(self, session_id: str, message: str) -> Dict[str, Any]:
        # 세션 초기화 신호 처리
        if message == "__INIT_SESSION__":
            self.sessions[session_id] = {
                "messages": [],
                "workingSubscription": None,
                "workingResourceGroup": None,
            }
            return {
                "session_id": session_id,
                "reply": "세션이 초기화되었습니다.",
                "workingSubscription": None,
                "workingResourceGroup": None,
            }
        
        # initialize state if new
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "messages": [],
                "workingSubscription": None,
                "workingResourceGroup": None,
            }
        state = self.sessions[session_id]
        state["messages"].append(HumanMessage(content=message))
        out = await self.graph.ainvoke(state, config={"configurable": {"thread_id": session_id}})
        # store updated state
        self.sessions[session_id] = out
        last_ai = out["messages"][-1].content if out.get("messages") else ""
        # Detect confirmation payload for UI buttons
        last_res = out.get("lastResult") or {}
        confirmation = last_res if isinstance(last_res, dict) and last_res.get("type") == "confirmation" else None
        
        working_rg = out.get("workingResourceGroup")
        print(f"DEBUG: handle_message response - workingResourceGroup: {working_rg}")
        
        return {
            "session_id": session_id,
            "reply": last_ai,
            "workingSubscription": out.get("workingSubscription"),
            "workingResourceGroup": working_rg,
            "confirmation": confirmation,
        }

    async def _call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke MCP tool via stdio if enabled; otherwise via HTTP bridge."""
        if self._use_stdio:
            async with MCPStdioClient(self._stdio_cmd) as c:
                return await c.call_tool(name, arguments)
        # HTTP bridge
        return await self.client.tool(name, arguments)

    async def _get_resource_spec(self, resource_type: Optional[str]) -> Optional[Dict[str, Any]]:
        if not resource_type:
            return None
        if resource_type in self._spec_cache:
            return self._spec_cache[resource_type]
        try:
            spec_res = await self._call_tool("azs.spec", {"resourceType": resource_type})
        except Exception:
            return None

        if isinstance(spec_res, dict):
            content = spec_res.get("content")
            if isinstance(content, list) and content:
                spec = content[0].get("spec")
            else:
                spec = None
        else:
            spec = None

        if isinstance(spec, dict):
            self._spec_cache[resource_type] = spec
            return spec
        return None

    # --- Session helpers (for dropdown UI) ---
    def ensure_session(self, session_id: str) -> AgentState:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "messages": [],
                "workingSubscription": None,
                "workingResourceGroup": None,
            }
        return self.sessions[session_id]

    def set_working_subscription(self, session_id: str, sub: str) -> None:
        st = self.ensure_session(session_id)
        if not sub.startswith("/subscriptions/"):
            sub = f"/subscriptions/{sub}"
        st["workingSubscription"] = sub

    def set_working_resource_group(self, session_id: str, rg: str) -> None:
        st = self.ensure_session(session_id)
        st["workingResourceGroup"] = rg
