import os
import subprocess
import json
from typing import List, Dict, Any, Literal, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from dotenv import load_dotenv

from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import SubscriptionClient, ResourceManagementClient

# v0.2.0: RBAC + Policy 모듈
from scopes import parse_scope, validate_scope_format
from authz import RBACEvaluator
from policy_eval import PolicyEvaluator

# v0.3.0: MCP 툴
from mcp_tools import MCPToolHandler, MCPToolRequest
from catalog import load_tool_catalog, enforced_location_policy

load_dotenv()

# (server) LLM 비사용 — LangGraph 에이전트에서만 사용

app = FastAPI(title="AZ Servant — v0.3.0 (MCP Tools + Apply)")

DEFAULT_RG_LOCATION = "koreacentral"

def cred():
    """
    Local PoC: use az login token cache via AzureCliCredential inside DefaultAzureCredential.
    Managed Identity is excluded on local to avoid accidental use.
    """
    return DefaultAzureCredential(exclude_managed_identity_credential=True)

# Apply approval — Local PoC: no token required (prod will reintroduce approval)

# ---------- Pydantic models ----------
class SubscriptionOut(BaseModel):
    id: str
    name: str

class ResourceGroupOut(BaseModel):
    id: str
    name: str
    location: str
    tags: Dict[str, Any] | None = None

class ScopesOut(BaseModel):
    subscriptions: List[SubscriptionOut]
    resourceGroups: List[ResourceGroupOut]

# v0.2.0: RBAC + Policy 모델
class RoleInfo(BaseModel):
    id: str
    name: str

class CapabilitySummary(BaseModel):
    allowedActions: List[str]
    notActions: List[str]
    effectiveRoles: List[RoleInfo]
    # 선택: 역할별 상세 권한
    class RoleDetail(BaseModel):
        role: RoleInfo
        actions: List[str]
        notActions: List[str]
    roleDetails: List[RoleDetail] | None = None

class RequiredTag(BaseModel):
    key: str
    required: bool
    default: Optional[str] = None

class PolicyDeny(BaseModel):
    policyId: str
    reason: str

class PolicySummary(BaseModel):
    allowedLocations: List[str]
    requiredTags: List[RequiredTag]
    denies: List[PolicyDeny]

class PlanDecision(BaseModel):
    status: Literal['allowed', 'blocked', 'conditional']
    reasons: List[str]
    hints: List[str]


# ---------- endpoints ----------
@app.get("/me/subscriptions", response_model=List[SubscriptionOut])
def list_subscriptions():
    subs = SubscriptionClient(cred()).subscriptions.list()
    out: List[SubscriptionOut] = []
    for s in subs:
        out.append(SubscriptionOut(id=f"/subscriptions/{s.subscription_id}", name=s.display_name or s.subscription_id))
    if not out:
        raise HTTPException(403, "구독이 없습니다. az login 계정/테넌트를 확인하세요.")
    return out

@app.get("/me/resource-groups", response_model=List[ResourceGroupOut])
def list_resource_groups(subscription_id: str = Query(..., description="GUID 또는 /subscriptions/<id> 형식")):
    sub_id = subscription_id.strip()
    if not sub_id.startswith("/subscriptions/"):
        sub_id = f"/subscriptions/{sub_id}"
    sub_guid = sub_id.split("/")[2]
    rgc = ResourceManagementClient(cred(), sub_guid)
    rgs = rgc.resource_groups.list()
    return [
        ResourceGroupOut(
            id=f"/subscriptions/{sub_guid}/resourceGroups/{rg.name}",
            name=rg.name,
            location=rg.location,
            tags=getattr(rg, "tags", None) or None
        )
        for rg in rgs
    ]

@app.get("/me/scopes", response_model=ScopesOut)
async def list_scopes():
    subscriptions = list_subscriptions()
    all_rgs: List[ResourceGroupOut] = []
    for sub in subscriptions:
        sub_guid = sub.id.split("/")[2]
        all_rgs.extend(list_resource_groups(sub_guid))
    # 요약은 LangGraph 에이전트에서 수행 (서버 비활성)
    return ScopesOut(subscriptions=subscriptions, resourceGroups=all_rgs)

# v0.2.0: RBAC + Policy 엔드포인트
@app.get("/plan/capabilities", response_model=CapabilitySummary)
def get_capabilities(scope: str = Query(..., description="Azure 스코프 (예: /subscriptions/<id> 또는 /subscriptions/<id>/resourceGroups/<name>)")):
    """스코프에서 허용된 액션과 유효한 역할을 반환"""
    if not validate_scope_format(scope):
        raise HTTPException(400, "잘못된 스코프 형식")
    
    try:
        evaluator = RBACEvaluator(cred())
        detailed = evaluator.get_detailed_permissions(scope)

        # 역할별 상세 권한 수집
        role_details: List[CapabilitySummary.RoleDetail] = []
        for role in detailed.get("effectiveRoles", []):
            try:
                rd = evaluator._get_role_definition(role.get("id", ""))  # 내부 API 사용(PoC)
                if rd:
                    role_details.append(
                        CapabilitySummary.RoleDetail(
                            role=RoleInfo(id=role.get("id",""), name=role.get("name","")),
                            actions=list(rd.actions or []),
                            notActions=list(rd.not_actions or []),
                        )
                    )
            except Exception:
                # 일부 역할 정의 조회 실패 시 무시하고 계속
                pass

        return CapabilitySummary(
            allowedActions=detailed.get("allowedActions", []),
            notActions=detailed.get("notActions", []),
            effectiveRoles=[RoleInfo(id=role.get("id",""), name=role.get("name","")) for role in detailed.get("effectiveRoles", [])],
            roleDetails=role_details or None,
        )
    except Exception as e:
        raise HTTPException(500, f"권한 조회 실패: {str(e)}")

@app.get("/plan/constraints", response_model=PolicySummary)
def get_constraints(scope: str = Query(..., description="Azure 스코프")):
    """스코프의 정책 제약을 반환"""
    if not validate_scope_format(scope):
        raise HTTPException(400, "잘못된 스코프 형식")
    
    try:
        evaluator = PolicyEvaluator(cred())
        constraints = evaluator.get_policy_constraints(scope)
        
        return PolicySummary(
            allowedLocations=constraints.allowed_locations,
            requiredTags=[RequiredTag(key=tag["key"], required=tag["required"], default=tag.get("default")) 
                         for tag in constraints.required_tags],
            denies=[PolicyDeny(policyId=deny["policyId"], reason=deny["reason"]) 
                   for deny in constraints.denies]
        )
    except Exception as e:
        raise HTTPException(500, f"정책 조회 실패: {str(e)}")

@app.get("/plan/check", response_model=PlanDecision)
def check_plan(scope: str = Query(..., description="Azure 스코프"),
               resourceType: str = Query(..., description="리소스 타입 (예: Microsoft.Storage/storageAccounts)"),
               location: str = Query(..., description="리소스 위치"),
               tags: Optional[str] = Query(default="", description="리소스 태그 (JSON 형식)")):
    """리소스 생성 계획을 종합 점검"""
    if not validate_scope_format(scope):
        raise HTTPException(400, "잘못된 스코프 형식")
    
    try:
        # 태그 파싱
        import json
        parsed_tags = {}
        if tags:
            try:
                parsed_tags = json.loads(tags)
            except json.JSONDecodeError:
                raise HTTPException(400, "태그 형식이 잘못되었습니다. JSON 형식을 사용하세요.")
        
        # RBAC 권한 확인
        rbac_evaluator = RBACEvaluator(cred())
        policy_evaluator = PolicyEvaluator(cred())
        
        # 리소스 타입별 필요 액션 매핑 (실제 구독 리소스 기반 확장)
        action_mapping = {
            # 기본 리소스
            "Microsoft.Resources/resourceGroups": "Microsoft.Resources/resourceGroups/write",
            
            # 스토리지 & 데이터베이스
            "Microsoft.Storage/storageAccounts": "Microsoft.Storage/storageAccounts/write",
            "Microsoft.KeyVault/vaults": "Microsoft.KeyVault/vaults/write",
            "Microsoft.DBforPostgreSQL/flexibleServers": "Microsoft.DBforPostgreSQL/flexibleServers/write",
            "Microsoft.Cache/Redis": "Microsoft.Cache/Redis/write",
            
            # 컨테이너 & 오케스트레이션
            "Microsoft.ContainerRegistry/registries": "Microsoft.ContainerRegistry/registries/write",
            "Microsoft.ContainerService/managedClusters": "Microsoft.ContainerService/managedClusters/write",
            
            # 네트워킹
            "Microsoft.Network/virtualNetworks": "Microsoft.Network/virtualNetworks/write",
            "Microsoft.Network/networkSecurityGroups": "Microsoft.Network/networkSecurityGroups/write",
            "Microsoft.Network/routeTables": "Microsoft.Network/routeTables/write",
            "Microsoft.Network/privateEndpoints": "Microsoft.Network/privateEndpoints/write",
            "Microsoft.Network/publicIPAddresses": "Microsoft.Network/publicIPAddresses/write",
            "Microsoft.Network/loadBalancers": "Microsoft.Network/loadBalancers/write",
            "Microsoft.Network/networkWatchers": "Microsoft.Network/networkWatchers/write",
            
            # 컴퓨팅
            "Microsoft.Compute/virtualMachineScaleSets": "Microsoft.Compute/virtualMachineScaleSets/write",
            "Microsoft.Compute/disks": "Microsoft.Compute/disks/write",
            
            # 메시징 & 이벤트
            "Microsoft.EventHub/namespaces": "Microsoft.EventHub/namespaces/write",
            "Microsoft.EventGrid/systemTopics": "Microsoft.EventGrid/systemTopics/write",
            
            # 모니터링 & 관리
            "Microsoft.ManagedIdentity/userAssignedIdentities": "Microsoft.ManagedIdentity/userAssignedIdentities/write",
            "Microsoft.Insights/dataCollectionEndpoints": "Microsoft.Insights/dataCollectionEndpoints/write",
            "Microsoft.Insights/dataCollectionRules": "Microsoft.Insights/dataCollectionRules/write",
            "Microsoft.AlertsManagement/prometheusRuleGroups": "Microsoft.AlertsManagement/prometheusRuleGroups/write",
            "microsoft.operationalinsights/workspaces": "microsoft.operationalinsights/workspaces/write",
            "microsoft.monitor/accounts": "microsoft.monitor/accounts/write"
        }
        
        required_action = action_mapping.get(resourceType, f"{resourceType}/write")
        has_rbac_permission = rbac_evaluator.can_perform_action(scope, required_action)
        
        # 정책 준수 확인
        compliance_result = policy_evaluator.check_resource_compliance(scope, resourceType, location, parsed_tags)
        
        # 종합 판정
        reasons = []
        hints = []
        
        if not has_rbac_permission:
            reasons.append(f"RBAC 권한 부족: {required_action} 액션을 수행할 권한이 없습니다")
        
        if not compliance_result["compliant"]:
            reasons.extend(compliance_result["violations"])
            hints.extend(compliance_result["warnings"])
        
        # 상태 결정
        if reasons:
            status = "blocked"
        elif hints:
            status = "conditional"
        else:
            status = "allowed"
        
        return PlanDecision(
            status=status,
            reasons=reasons,
            hints=hints
        )
        
    except Exception as e:
        raise HTTPException(500, f"계획 점검 실패: {str(e)}")

# ---------- Apply (minimal RG create/delete) ----------
class RGCreateRequest(BaseModel):
    scope: str  # /subscriptions/<id>
    name: str
    location: Optional[str] = None
    tags: Dict[str, str] | None = None

@app.post("/apply/resource-group")
def create_resource_group(req: RGCreateRequest):
    if not validate_scope_format(req.scope):
        raise HTTPException(400, "잘못된 스코프 형식")
    try:
        original_location = (req.location or "").strip()
        target_location = DEFAULT_RG_LOCATION

        # 사전 점검(RBAC/Policy)
        rbac = RBACEvaluator(cred())
        required_action = "Microsoft.Resources/resourceGroups/write"
        if not rbac.can_perform_action(req.scope, required_action):
            raise HTTPException(403, f"RBAC 권한 부족: {required_action}")

        policy = PolicyEvaluator(cred())
        compliance = policy.check_resource_compliance(
            req.scope,
            "Microsoft.Resources/resourceGroups",
            target_location,
            req.tags or {}
        )
        if not compliance["compliant"]:
            raise HTTPException(403, "; ".join(compliance["violations"]))

        # 생성
        sub_id, _ = parse_scope(req.scope)
        rgc = ResourceManagementClient(cred(), sub_id)
        rgc.resource_groups.create_or_update(
            req.name,
            {"location": target_location, "tags": req.tags or {}}
        )

        policy_note: Optional[str] = None
        policy_cfg = enforced_location_policy()
        if policy_cfg.get("enabled"):
            if not original_location or original_location.lower() != target_location.lower():
                policy_note = policy_cfg.get("message") or f"회사 정책상 리전이 {target_location}로 자동 적용되었습니다."

        response = {
            "ok": True,
            "id": f"/subscriptions/{sub_id}/resourceGroups/{req.name}",
            "location": target_location,
            "tags": req.tags or {}
        }
        if policy_note:
            response["policyNote"] = policy_note
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"리소스 그룹 생성 실패: {str(e)}")

@app.delete("/apply/resource-group")
def delete_resource_group(scope: str = Query(..., description="/subscriptions/<id>/resourceGroups/<name>")):
    if not validate_scope_format(scope):
        raise HTTPException(400, "잘못된 스코프 형식")
    try:
        sub_id, rg_name = parse_scope(scope)
        if not rg_name:
            raise HTTPException(400, "리소스 그룹 스코프가 필요합니다")

        # 권한 점검(삭제)
        rbac = RBACEvaluator(cred())
        required_action = "Microsoft.Resources/resourceGroups/delete"
        if not rbac.can_perform_action(f"/subscriptions/{sub_id}/resourceGroups/{rg_name}", required_action):
            raise HTTPException(403, f"RBAC 권한 부족: {required_action}")

        rgc = ResourceManagementClient(cred(), sub_id)
        poller = rgc.resource_groups.begin_delete(rg_name)
        # 비동기 삭제 시작; 즉시 반환
        return {"ok": True, "deleted": rg_name, "subscription": sub_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"리소스 그룹 삭제 실패: {str(e)}")

# v0.3.0: MCP 툴 엔드포인트
mcp_handler = MCPToolHandler()

@app.post("/mcp/tools")
def mcp_tools(request: MCPToolRequest):
    """MCP 툴 요청 처리"""
    return mcp_handler.handle_tool(request)

@app.get("/mcp/tools")
def list_mcp_tools():
    """사용 가능한 MCP 툴 목록 조회"""
    tools = load_tool_catalog()
    return {"tools": tools}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/auth/status")
def check_auth_status():
    """Azure 로그인 상태 확인"""
    try:
        # az account show로 현재 로그인 상태 확인
        result = subprocess.run(["az", "account", "show"], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            account_info = json.loads(result.stdout)
            return {
                "authenticated": True,
                "user": account_info.get("user", {}).get("name"),
                "subscription": {
                    "id": account_info.get("id"),
                    "name": account_info.get("name")
                }
            }
        else:
            return {"authenticated": False, "error": result.stderr}
    except subprocess.TimeoutExpired:
        return {"authenticated": False, "error": "타임아웃"}
    except Exception as e:
        return {"authenticated": False, "error": str(e)}

@app.post("/auth/login")
def azure_login():
    """수동 로그인 안내 (터미널에서 직접 실행)"""
    manual_command = 'az logout && az login --tenant "e6c9ec09-8430-4a99-bf15-242bc089b409" --scope "https://management.azure.com/.default"'
    return {
        "success": False,
        "manual_required": True,
        "manual_command": manual_command,
        "instruction": "Conditional Access 정책으로 인해 터미널에서 수동 로그인이 필요합니다.",
        "steps": [
            "1. 터미널을 열어주세요",
            "2. 아래 명령어를 복사해서 실행하세요",
            "3. 디바이스 코드 인증을 완료하세요", 
            "4. 브라우저를 새로고침하세요"
        ]
    }
