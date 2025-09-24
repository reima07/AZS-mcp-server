"""
MCP (Model Context Protocol) 툴 핸들러
v0.3.0: Azure 서비스 계획, 적용, 요청 기능을 MCP 툴로 노출
"""

import copy
import json
from typing import Dict, List, Optional, Any, Callable
from pydantic import BaseModel
from dotenv import load_dotenv

# 기존 모듈 import
from azure.identity import DefaultAzureCredential
from authz import RBACEvaluator
from policy_eval import PolicyEvaluator
from scopes import parse_scope

from catalog import (
    common_regions,
    enforced_location_policy,
    resource_spec,
)

# 환경 변수 로드
load_dotenv()

_COMMON_REGIONS: List[str] = common_regions()

class MCPToolRequest(BaseModel):
    """MCP 툴 요청 모델"""
    name: str
    arguments: Dict[str, Any]

class MCPToolResponse(BaseModel):
    """MCP 툴 응답 모델"""
    content: List[Dict[str, Any]]
    is_error: bool = False

def _cred():
    return DefaultAzureCredential(exclude_managed_identity_credential=True)

class MCPToolHandler:
    """MCP 툴 핸들러"""
    
    def __init__(self):
        self.rbac = RBACEvaluator(_cred())
        self.policy_evaluator = PolicyEvaluator(_cred())
        self._handlers: Dict[str, Callable[[Dict[str, Any]], MCPToolResponse]] = {
            "azs.plan": self._handle_plan,
            "azs.apply": self._handle_apply,
            "azs.request": self._handle_request,
            "azs.spec": self._handle_spec,
            "azs.list_subscriptions": self._handle_list_subscriptions,
            "azs.list_resource_groups": self._handle_list_resource_groups,
            "azs.list_resources": self._handle_list_resources,
            "azs.get_resource": self._handle_get_resource,
        }

    def handle_tool(self, request: MCPToolRequest) -> MCPToolResponse:
        """MCP 툴 요청 처리"""
        handler = self._handlers.get(request.name)
        if not handler:
            return MCPToolResponse(
                content=[{"error": f"알 수 없는 툴: {request.name}"}],
                is_error=True,
            )
        try:
            return handler(request.arguments)
        except Exception as e:
            return MCPToolResponse(
                content=[{"error": f"툴 실행 실패: {str(e)}"}],
                is_error=True,
            )

    def _apply_location_policy(self, resource_type: str, params: Dict[str, Any]) -> Optional[str]:
        policy = enforced_location_policy() or {}
        if not policy.get("enabled"):
            return None

        allowed_types = policy.get("resourceTypes") or []
        if allowed_types and "*" not in allowed_types and resource_type not in allowed_types:
            return None

        target = policy.get("value")
        if not target:
            return None

        original = params.get("location")
        params["location"] = target

        message = policy.get("message")
        if original and original != target:
            suffix = f" (요청된 리전: {original} → 적용된 리전: {target})"
            base = message or f"회사 정책에 따라 리전이 {target}로 강제되었습니다."
            return base + suffix
        if not original:
            return message or f"회사 정책상 리전이 {target}로 자동 설정되었습니다."
        return None

    @staticmethod
    def _attach_policy_note(result: Dict[str, Any], note: Optional[str]) -> Dict[str, Any]:
        if note and isinstance(result, dict) and result.get("status") == "success":
            result.setdefault("policyNote", note)
        return result

    def _handle_plan(self, args: Dict[str, Any]) -> MCPToolResponse:
        """azs.plan 툴 처리"""
        scope = args.get("scope")
        mode = args.get("mode", "capabilities")
        resource_type = args.get("resourceType")
        location = args.get("location")
        tags = args.get("tags")
        
        if not scope:
            return MCPToolResponse(
                content=[{"error": "scope 파라미터가 필요합니다"}],
                is_error=True
            )
        
        try:
            if mode == "capabilities":
                # 서버 HTTP 응답과 동일하게 notActions 및 역할별 상세 포함
                detailed = self.rbac.get_detailed_permissions(scope)
                role_details = []
                for role in detailed.get("effectiveRoles", []):
                    try:
                        rd = self.rbac._get_role_definition(role.get("id", ""))  # 내부 API 사용(PoC)
                        if rd:
                            role_details.append({
                                "role": {"id": role.get("id", ""), "name": role.get("name", "")},
                                "actions": list(rd.actions or []),
                                "notActions": list(rd.not_actions or []),
                            })
                    except Exception:
                        pass
                result = {
                    "allowedActions": detailed.get("allowedActions", []),
                    "notActions": detailed.get("notActions", []),
                    "effectiveRoles": detailed.get("effectiveRoles", []),
                    "roleDetails": role_details or None,
                }
            elif mode == "constraints":
                c = self.policy_evaluator.get_policy_constraints(scope)
                result = {
                    "allowedLocations": c.allowed_locations,
                    "requiredTags": c.required_tags,
                    "denies": c.denies,
                }
            elif mode == "check":
                if not resource_type or not location:
                    return MCPToolResponse(
                        content=[{"error": "check 모드에서는 resourceType과 location이 필요합니다"}],
                        is_error=True
                    )
                
                # 태그 파싱
                parsed_tags = {}
                if tags:
                    try:
                        if isinstance(tags, str):
                            parsed_tags = json.loads(tags)
                        else:
                            parsed_tags = tags
                    except json.JSONDecodeError:
                        return MCPToolResponse(
                            content=[{"error": "tags는 유효한 JSON 형식이어야 합니다"}],
                            is_error=True
                        )
                
                result = self._check_resource_creation(
                    scope, resource_type, location, parsed_tags
                )
            else:
                return MCPToolResponse(
                    content=[{"error": f"알 수 없는 모드: {mode}"}],
                    is_error=True
                )
            
            return MCPToolResponse(content=[result])
            
        except Exception as e:
            return MCPToolResponse(
                content=[{"error": f"Plan 실행 실패: {str(e)}"}],
                is_error=True
            )

    def _handle_spec(self, args: Dict[str, Any]) -> MCPToolResponse:
        """리소스 타입별 파라미터 스펙 제공"""
        rtype = args.get("resourceType")
        if not rtype:
            return MCPToolResponse(content=[{"error": "resourceType 파라미터가 필요합니다"}], is_error=True)
        spec = self._get_resource_spec(rtype)
        if not spec:
            return MCPToolResponse(content=[{"error": f"스펙을 찾을 수 없습니다: {rtype}"}], is_error=True)
        return MCPToolResponse(content=[{"resourceType": rtype, "spec": spec}])

    def _handle_list_subscriptions(self, _: Dict[str, Any]) -> MCPToolResponse:
        return MCPToolResponse(content=[{"subscriptions": self._list_subscriptions()}])

    def _handle_list_resource_groups(self, args: Dict[str, Any]) -> MCPToolResponse:
        scope = args.get("scope")
        if not scope:
            return MCPToolResponse(content=[{"error": "scope 파라미터가 필요합니다"}], is_error=True)
        return MCPToolResponse(content=[{"resourceGroups": self._list_resource_groups(scope)}])

    def _handle_list_resources(self, args: Dict[str, Any]) -> MCPToolResponse:
        scope = args.get("scope")
        rtype = args.get("resourceType")
        if not scope:
            return MCPToolResponse(content=[{"error": "scope 파라미터가 필요합니다"}], is_error=True)
        return MCPToolResponse(content=[{"resources": self._list_resources(scope, rtype)}])

    def _handle_get_resource(self, args: Dict[str, Any]) -> MCPToolResponse:
        rid = args.get("id")
        if not rid:
            return MCPToolResponse(content=[{"error": "id 파라미터가 필요합니다"}], is_error=True)
        return MCPToolResponse(content=[{"resource": self._get_resource(rid)}])

    def _get_resource_spec(self, resource_type: str) -> Optional[Dict[str, Any]]:
        spec = resource_spec(resource_type)
        if not spec:
            return None

        # deep copy to avoid mutating shared state
        spec_copy = copy.deepcopy(spec)
        for param in spec_copy.get("params", []):
            enum_value = param.get("enum")
            if isinstance(enum_value, str) and enum_value.startswith("@"):  # reference to shared list
                if enum_value == "@commonRegions":
                    param["enum"] = _COMMON_REGIONS
                else:
                    param["enum"] = []
        return spec_copy
    
    def _handle_apply(self, args: Dict[str, Any]) -> MCPToolResponse:
        """azs.apply 툴 처리"""
        action = args.get("action")
        resource_type = args.get("resourceType")
        scope = args.get("scope")
        params = args.get("params", {})
        approval_token = args.get("approvalToken")
        
        # 로컬 개발에서는 승인 토큰 체크 생략
        # if not approval_token:
        #     return MCPToolResponse(
        #         content=[{"error": "승인 토큰이 필요합니다"}],
        #         is_error=True
        #     )
        
        if not all([action, resource_type, scope]):
            return MCPToolResponse(
                content=[{"error": "action, resourceType, scope 파라미터가 필요합니다"}],
                is_error=True
            )
        
        try:
            if action == "create":
                result = self._create_resource(resource_type, scope, params)
            elif action == "delete":
                result = self._delete_resource(resource_type, scope, params)
            elif action == "update":
                result = self._update_resource(resource_type, scope, params)
            else:
                return MCPToolResponse(
                    content=[{"error": f"알 수 없는 액션: {action}"}],
                    is_error=True
                )
            
            return MCPToolResponse(content=[result])
            
        except Exception as e:
            return MCPToolResponse(
                content=[{"error": f"Apply 실행 실패: {str(e)}"}],
                is_error=True
            )
    
    def _handle_request(self, args: Dict[str, Any]) -> MCPToolResponse:
        """azs.request 툴 처리"""
        blocked_items = args.get("blockedItems", [])
        scope = args.get("scope")
        # 선택 파라미터(신청 에이전트 호환용)
        extras = {
            "userName": args.get("userName"),
            "expiryDays": args.get("expiryDays", 90),
            "subscriptionName": args.get("subscriptionName"),
            "confidence": args.get("confidence"),
        }
        
        if not scope:
            return MCPToolResponse(
                content=[{"error": "scope 파라미터가 필요합니다"}],
                is_error=True
            )
        
        try:
            # ServiceNow 신청서 생성(+ 에이전트 호환 포맷)
            request_doc = self._generate_servicenow_request(blocked_items, scope, extras)
            return MCPToolResponse(content=[request_doc])
            
        except Exception as e:
            return MCPToolResponse(
                content=[{"error": f"Request 생성 실패: {str(e)}"}],
                is_error=True
            )
    
    def _check_resource_creation(self, scope: str, resource_type: str, 
                               location: str, tags: Dict[str, str]) -> Dict[str, Any]:
        """리소스 생성 체크 (기존 로직 재사용)"""
        # RBAC 체크
        # RBAC
        required_action = f"{resource_type}/write"
        if not self.rbac.can_perform_action(scope, required_action):
            return {"status": "blocked", "reasons": [f"권한 부족: {required_action}"], "hints": []}

        # Policy
        compliance = self.policy_evaluator.check_resource_compliance(scope, resource_type, location, tags)
        if compliance.get("compliant"):
            return {"status": "allowed", "reasons": [], "hints": compliance.get("warnings", [])}
        return {"status": "blocked", "reasons": compliance.get("violations", []), "hints": compliance.get("warnings", [])}
    
    def _create_resource(self, resource_type: str, scope: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """리소스 생성 (RG, AKS, ACR, KV, Event Hubs)"""
        subscription_id, _ = parse_scope(scope)
        policy_note = self._apply_location_policy(resource_type, params)

        if resource_type == "Microsoft.Resources/resourceGroups":
            import subprocess
            name = params.get("name")
            location = params.get("location")
            if not name or not location:
                return {"status": "error", "error": "RG 생성에는 params.name, params.location이 필요합니다"}
            try:
                subprocess.run([
                    "az", "group", "create",
                    "--name", name,
                    "--location", location,
                    "--subscription", subscription_id
                ], check=True, capture_output=True)
                result = {
                    "status": "success",
                    "resourceType": "Microsoft.Resources/resourceGroups",
                    "name": name,
                    "location": location,
                    "message": "리소스 그룹이 생성되었습니다"
                }
                return self._attach_policy_note(result, policy_note)
            except subprocess.CalledProcessError as e:
                err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
                return {"status": "error", "error": f"RG 생성 실패: {err}"}

        if resource_type == "Microsoft.ContainerService/managedClusters":
            return self._attach_policy_note(self._create_aks(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.ContainerRegistry/registries":
            return self._attach_policy_note(self._create_acr(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.KeyVault/vaults":
            return self._attach_policy_note(self._create_keyvault(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.EventHub/namespaces":
            return self._attach_policy_note(self._create_eventhub(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.DBforPostgreSQL/flexibleServers":
            return self._attach_policy_note(self._create_postgres_flexible(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.Compute/disks":
            return self._attach_policy_note(self._create_disk(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.Storage/storageAccounts":
            return self._attach_policy_note(self._create_storage_account(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.Cache/Redis":
            return self._attach_policy_note(self._create_redis(subscription_id, params), policy_note)
        elif resource_type == "Microsoft.Network/virtualNetworks":
            return self._attach_policy_note(self._create_vnet(subscription_id, params), policy_note)
        else:
            return {"error": f"지원하지 않는 리소스 타입: {resource_type}"}
    
    def _create_aks(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """AKS 클러스터 생성"""
        import subprocess
        
        name = params.get("name", "aks-cluster")
        resource_group = params.get("resourceGroup", "rg-aks")
        location = params.get("location", "koreacentral")
        node_count = params.get("nodeCount", 1)
        node_vm_size = params.get("nodeVmSize", "Standard_B2s")
        
        try:
            # 리소스 그룹 생성
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            
            # AKS 클러스터 생성
            result = subprocess.run([
                "az", "aks", "create",
                "--resource-group", resource_group,
                "--name", name,
                "--location", location,
                "--node-count", str(node_count),
                "--node-vm-size", node_vm_size,
                "--generate-ssh-keys",
                "--subscription", subscription_id
            ], check=True, capture_output=True, text=True)
            
            return {
                "status": "success",
                "resourceType": "Microsoft.ContainerService/managedClusters",
                "name": name,
                "resourceGroup": resource_group,
                "location": location,
                "message": "AKS 클러스터가 성공적으로 생성되었습니다"
            }
            
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"AKS 생성 실패: {err}"}
    
    def _create_acr(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """ACR 레지스트리 생성"""
        import subprocess
        
        name = params.get("name", "acrregistry")
        resource_group = params.get("resourceGroup", "rg-acr")
        location = params.get("location", "koreacentral")
        sku = params.get("sku", "Basic")
        
        try:
            # 리소스 그룹 생성
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            
            # ACR 생성
            result = subprocess.run([
                "az", "acr", "create",
                "--resource-group", resource_group,
                "--name", name,
                "--location", location,
                "--sku", sku,
                "--subscription", subscription_id
            ], check=True, capture_output=True, text=True)
            
            return {
                "status": "success",
                "resourceType": "Microsoft.ContainerRegistry/registries",
                "name": name,
                "resourceGroup": resource_group,
                "location": location,
                "message": "ACR 레지스트리가 성공적으로 생성되었습니다"
            }
            
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"ACR 생성 실패: {err}"}
    
    def _create_keyvault(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Key Vault 생성"""
        import subprocess
        
        name = params.get("name", "kv-vault")
        resource_group = params.get("resourceGroup", "rg-kv")
        location = params.get("location", "koreacentral")
        
        try:
            # 리소스 그룹 생성
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            
            # Key Vault 생성
            result = subprocess.run([
                "az", "keyvault", "create",
                "--resource-group", resource_group,
                "--name", name,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True, text=True)
            
            return {
                "status": "success",
                "resourceType": "Microsoft.KeyVault/vaults",
                "name": name,
                "resourceGroup": resource_group,
                "location": location,
                "message": "Key Vault가 성공적으로 생성되었습니다"
            }
            
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"Key Vault 생성 실패: {err}"}

    def _create_storage_account(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Storage Account 생성 (StorageV2, TLS1_2)"""
        import subprocess
        name = params.get("name")
        resource_group = params.get("resourceGroup")
        location = params.get("location", "koreacentral")
        sku = params.get("sku", "Standard_LRS")
        kind = params.get("kind", "StorageV2")
        if not all([name, resource_group]):
            return {"status": "error", "error": "Storage 생성에는 name, resourceGroup가 필요합니다"}
        try:
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            subprocess.run([
                "az", "storage", "account", "create",
                "--name", name,
                "--resource-group", resource_group,
                "--location", location,
                "--sku", sku,
                "--kind", kind,
                "--min-tls-version", "TLS1_2",
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            return {"status": "success", "resourceType": "Microsoft.Storage/storageAccounts", "name": name, "resourceGroup": resource_group, "location": location, "sku": sku}
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"Storage 생성 실패: {err}"}

    def _create_redis(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Azure Cache for Redis 생성"""
        import subprocess
        name = params.get("name")
        resource_group = params.get("resourceGroup")
        location = params.get("location", "koreacentral")
        sku = params.get("sku", "Basic")
        vm_size = params.get("vmSize", "c0")
        if not all([name, resource_group]):
            return {"status": "error", "error": "Redis 생성에는 name, resourceGroup가 필요합니다"}
        try:
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            subprocess.run([
                "az", "redis", "create",
                "--name", name,
                "--resource-group", resource_group,
                "--location", location,
                "--sku", sku,
                "--vm-size", vm_size,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            return {"status": "success", "resourceType": "Microsoft.Cache/Redis", "name": name, "resourceGroup": resource_group, "location": location, "sku": sku, "vmSize": vm_size}
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"Redis 생성 실패: {err}"}

    def _create_vnet(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """가상 네트워크 생성 (기본 서브넷 포함)"""
        import subprocess
        name = params.get("name")
        resource_group = params.get("resourceGroup")
        location = params.get("location", "koreacentral")
        address_prefixes = params.get("addressPrefixes", ["10.0.0.0/16"])  # list
        subnet_name = params.get("subnetName", "default")
        subnet_prefixes = params.get("subnetPrefixes", ["10.0.0.0/24"])  # list
        if not all([name, resource_group]):
            return {"status": "error", "error": "VNet 생성에는 name, resourceGroup가 필요합니다"}
        try:
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            subprocess.run([
                "az", "network", "vnet", "create",
                "--name", name,
                "--resource-group", resource_group,
                "--location", location,
                "--address-prefixes", *address_prefixes,
                "--subnet-name", subnet_name,
                "--subnet-prefixes", *subnet_prefixes,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            return {"status": "success", "resourceType": "Microsoft.Network/virtualNetworks", "name": name, "resourceGroup": resource_group, "location": location}
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"VNet 생성 실패: {err}"}

    def _update_resource(self, resource_type: str, scope: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """일반 리소스 업데이트 (tags 또는 --set 키=값)"""
        import subprocess
        subscription_id, _ = parse_scope(scope)
        rid = params.get("id")
        name = params.get("name")
        rg = params.get("resourceGroup")
        set_map = params.get("set") or {}
        tags = params.get("tags") or {}
        if not rid and (not all([name, rg, resource_type])):
            return {"status": "error", "error": "update에는 params.id 또는 (name, resourceGroup, resourceType)이 필요합니다"}
        try:
            cmd = ["az", "resource", "update"]
            if rid:
                cmd += ["--ids", rid]
            else:
                cmd += ["--name", name, "--resource-group", rg, "--resource-type", resource_type]
            # --set pairs
            if isinstance(set_map, dict):
                for k, v in set_map.items():
                    cmd += ["--set", f"{k}={v}"]
            # Tags
            if isinstance(tags, dict) and tags:
                tag_items = [f"{k}={v}" for k, v in tags.items()]
                cmd += ["--tags", *tag_items]
            cmd += ["--subscription", subscription_id]
            subprocess.run(cmd, check=True, capture_output=True)
            return {"status": "success", "resourceType": resource_type, "id": rid, "name": name, "resourceGroup": rg, "updated": True}
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"Update 실패: {err}"}
    
    def _create_eventhub(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Event Hub 네임스페이스 생성"""
        import subprocess
        
        name = params.get("name", "eh-namespace")
        resource_group = params.get("resourceGroup", "rg-eh")
        location = params.get("location", "koreacentral")
        sku = params.get("sku", "Standard")
        
        try:
            # 리소스 그룹 생성
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            
            # Event Hub 네임스페이스 생성
            result = subprocess.run([
                "az", "eventhubs", "namespace", "create",
                "--resource-group", resource_group,
                "--name", name,
                "--location", location,
                "--sku", sku,
                "--subscription", subscription_id
            ], check=True, capture_output=True, text=True)
            
            return {
                "status": "success",
                "resourceType": "Microsoft.EventHub/namespaces",
                "name": name,
                "resourceGroup": resource_group,
                "location": location,
                "message": "Event Hub 네임스페이스가 성공적으로 생성되었습니다"
            }
            
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"Event Hub 생성 실패: {err}"}

    def _create_postgres_flexible(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """PostgreSQL Flexible Server 생성"""
        import subprocess
        name = params.get("name")
        resource_group = params.get("resourceGroup")
        location = params.get("location", "koreacentral")
        admin_user = params.get("adminUser")
        admin_password = params.get("adminPassword")
        version = params.get("version", "14")
        sku_name = params.get("skuName", "Standard_B1ms")
        storage_size = params.get("storageSizeGB", 32)
        if not all([name, resource_group, admin_user, admin_password]):
            return {"status": "error", "error": "PostgreSQL 생성에는 name, resourceGroup, adminUser, adminPassword가 필요합니다"}
        try:
            # 리소스 그룹 생성 보장
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            # 서버 생성
            subprocess.run([
                "az", "postgres", "flexible-server", "create",
                "--name", name,
                "--resource-group", resource_group,
                "--location", location,
                "--admin-user", admin_user,
                "--admin-password", admin_password,
                "--version", str(version),
                "--sku-name", sku_name,
                "--storage-size", str(storage_size),
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            return {
                "status": "success",
                "resourceType": "Microsoft.DBforPostgreSQL/flexibleServers",
                "name": name,
                "resourceGroup": resource_group,
                "location": location,
                "message": "PostgreSQL Flexible Server가 성공적으로 생성되었습니다"
            }
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"PostgreSQL 생성 실패: {err}"}

    def _create_disk(self, subscription_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """관리 디스크 생성"""
        import subprocess
        name = params.get("name")
        resource_group = params.get("resourceGroup")
        location = params.get("location", "koreacentral")
        size_gb = params.get("sizeGB") or params.get("sizeGb") or params.get("size")
        sku = params.get("sku", "StandardSSD_LRS")
        if not all([name, resource_group, size_gb]):
            return {"status": "error", "error": "디스크 생성에는 name, resourceGroup, sizeGB가 필요합니다"}
        try:
            # 리소스 그룹 생성 보장
            subprocess.run([
                "az", "group", "create",
                "--name", resource_group,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            # 디스크 생성
            subprocess.run([
                "az", "disk", "create",
                "--name", name,
                "--resource-group", resource_group,
                "--size-gb", str(size_gb),
                "--sku", sku,
                "--location", location,
                "--subscription", subscription_id
            ], check=True, capture_output=True)
            return {
                "status": "success",
                "resourceType": "Microsoft.Compute/disks",
                "name": name,
                "resourceGroup": resource_group,
                "location": location,
                "sizeGB": int(size_gb),
                "sku": sku,
                "message": "관리 디스크가 성공적으로 생성되었습니다"
            }
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"디스크 생성 실패: {err}"}
    
    def _delete_resource(self, resource_type: str, scope: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """리소스 삭제"""
        import subprocess
        subscription_id, resource_group = parse_scope(scope)
        name = params.get("name") if isinstance(params, dict) else None
        
        print(f"DEBUG: _delete_resource - resource_type={resource_type}")
        print(f"DEBUG: _delete_resource - scope={scope}")
        print(f"DEBUG: _delete_resource - params={params}")
        print(f"DEBUG: _delete_resource - subscription_id={subscription_id}")
        print(f"DEBUG: _delete_resource - resource_group={resource_group}")
        print(f"DEBUG: _delete_resource - name={name}")
        try:
            if resource_type == "Microsoft.Resources/resourceGroups":
                target = name or resource_group
                if not target:
                    return {"status": "error", "error": "RG 삭제에는 scope에 RG가 포함되어 있거나 params.name이 필요합니다"}
                subprocess.run([
                    "az", "group", "delete",
                    "--name", target,
                    "--yes",
                    "--subscription", subscription_id
                ], check=True, capture_output=True)
                return {"status": "success", "deleted": target}
            if resource_type == "Microsoft.ContainerRegistry/registries":
                if not name:
                    return {"status": "error", "error": "ACR 삭제에는 params.name이 필요합니다"}
                if not resource_group:
                    return {"status": "error", "error": "ACR 삭제에는 resource_group이 필요합니다"}
                if not subscription_id:
                    return {"status": "error", "error": "ACR 삭제에는 subscription_id가 필요합니다"}
                
                print(f"DEBUG: About to run az acr delete - name={name}, rg={resource_group}, sub={subscription_id}")
                subprocess.run([
                    "az", "acr", "delete",
                    "--name", name,
                    "--resource-group", resource_group,
                    "--yes",
                    "--subscription", subscription_id
                ], check=True, capture_output=True)
                return {"status": "success", "resourceType": resource_type, "name": name, "resourceGroup": resource_group}
            return {"status": "error", "error": f"지원하지 않는 리소스 타입: {resource_type}"}
        except subprocess.CalledProcessError as e:
            err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else str(e))
            return {"status": "error", "error": f"리소스 삭제 실패: {err}"}
    
    def _generate_servicenow_request(self, blocked_items: List[Dict[str, Any]], scope: str, extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """ServiceNow 신청서 생성"""
        subscription_id, resource_group = parse_scope(scope)
        extras = extras or {}
        
        # 입력 정규화: 문자열/기타 타입도 허용하여 dict로 변환
        normalized: List[Dict[str, Any]] = []
        if blocked_items is None:
            blocked_items = []
        if not isinstance(blocked_items, list):
            blocked_items = [blocked_items]
        for it in blocked_items:
            if isinstance(it, dict):
                normalized.append({
                    "resourceType": it.get("resourceType", "Unknown"),
                    "reason": it.get("reason", "")
                })
            elif isinstance(it, str):
                # 자연어 이유만 온 경우 RBAC 또는 일반 요청으로 간주
                normalized.append({
                    "resourceType": "RBAC",
                    "reason": it
                })
            else:
                normalized.append({
                    "resourceType": "Unknown",
                    "reason": str(it)
                })

        # JSON 형식 신청서
        json_request = {
            "request_type": "Azure Resource Access Request",
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "requested_items": normalized,
            "justification": "Azure 리소스 접근 권한 요청",
            "requested_by": "System User",
            "priority": "Medium"
        }
        
        # Markdown 형식 신청서
        md_request = f"""# Azure 리소스 접근 권한 요청

## 기본 정보
- **구독 ID**: {subscription_id}
- **리소스 그룹**: {resource_group or "N/A"}
- **요청자**: System User
- **우선순위**: Medium

## 요청 항목
"""
        
        for item in normalized:
            md_request += f"- **{item.get('resourceType', 'Unknown')}**: {item.get('reason', 'No reason provided')}\n"
        
        md_request += f"""
## 정당성
Azure 리소스 접근 권한 요청

## 승인 정보
- **승인자**: TBD
- **예상 처리 시간**: 1-2 영업일
"""
        
        # 에이전트 호환 포맷(v1, 예시 스키마)
        from datetime import datetime, timezone
        sn_agent_requests: List[Dict[str, Any]] = []
        for item in normalized:
            # role 추론: 항목에 role이 있으면 사용, 없으면 RBAC 문자열에서 추정(간단)
            role_val = item.get("role")
            if not role_val and isinstance(item.get("reason"), str):
                rs = item["reason"].lower()
                if "contributor" in rs:
                    role_val = "contributor"
            sn_agent_requests.append({
                "action": "REQUEST_PERMISSIONS",
                "user_name": extras.get("userName") or "UNKNOWN",
                "subscription": extras.get("subscriptionName") or f"/subscriptions/{subscription_id}",
                "role": role_val or ("RBAC" if item.get("resourceType") == "RBAC" else item.get("resourceType", "Unknown")),
                "expiry_days": int(extras.get("expiryDays", 90)),
                "confidence": extras.get("confidence"),
                "missing_info": [],
                "details": item,
            })

        sn_agent_v1 = {
            "requests": sn_agent_requests,
            "scope": scope,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "version": "v1"
        }

        return {
            "json_format": json_request,
            "markdown_format": md_request,
            "scope": scope,
            "blocked_items_count": len(normalized),
            "sn_agent_v1": sn_agent_v1
        }

    # ---------- List/Get helpers (CLI PoC) ----------
    def _list_subscriptions(self) -> List[Dict[str, Any]]:
        import subprocess, json
        try:
            r = subprocess.run(["az","account","list","-o","json"], check=True, capture_output=True, text=True)
            data = json.loads(r.stdout)
            return [{"id": f"/subscriptions/{x['id']}", "name": x.get("name", x['id'])} for x in data]
        except Exception as e:
            return [{"error": f"구독 조회 실패: {e}"}]

    def _list_resource_groups(self, scope: str) -> List[Dict[str, Any]]:
        import subprocess, json
        sub_id, rg = parse_scope(scope)
        try:
            if rg:
                # 특정 RG만 반환
                return [{"id": f"/subscriptions/{sub_id}/resourceGroups/{rg}", "name": rg}]
            r = subprocess.run(["az","group","list","--subscription", sub_id, "-o","json"], check=True, capture_output=True, text=True)
            groups = json.loads(r.stdout)
            return [{"id": f"/subscriptions/{sub_id}/resourceGroups/{g['name']}", "name": g["name"], "location": g.get("location")} for g in groups]
        except Exception as e:
            return [{"error": f"리소스 그룹 조회 실패: {e}"}]

    def _list_resources(self, scope: str, resource_type: Optional[str]) -> List[Dict[str, Any]]:
        import subprocess, json
        sub_id, rg = parse_scope(scope)
        cmd = ["az","resource","list","--subscription", sub_id]
        if rg:
            cmd += ["--resource-group", rg]
        if resource_type:
            cmd += ["--resource-type", resource_type]
        cmd += ["-o","json"]
        try:
            r = subprocess.run(cmd, check=True, capture_output=True, text=True)
            items = json.loads(r.stdout)
            out = []
            for it in items:
                out.append({"id": it.get("id"), "name": it.get("name"), "type": it.get("type"), "location": it.get("location")})
            return out
        except Exception as e:
            return [{"error": f"리소스 목록 조회 실패: {e}"}]

    def _get_resource(self, resource_id: str) -> Dict[str, Any]:
        import subprocess, json
        try:
            r = subprocess.run(["az","resource","show","--ids", resource_id, "-o","json"], check=True, capture_output=True, text=True)
            it = json.loads(r.stdout)
            return {"id": it.get("id"), "name": it.get("name"), "type": it.get("type"), "location": it.get("location"), "properties": it.get("properties")}
        except Exception as e:
            return {"error": f"리소스 조회 실패: {e}"}

# MCP 툴 핸들러 인스턴스
mcp_handler = MCPToolHandler()
