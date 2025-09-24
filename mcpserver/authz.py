"""
Azure RBAC 권한 평가 모듈
"""
import re
import time
import os
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.identity import DefaultAzureCredential
from scopes import parse_scope


@dataclass
class RoleAssignment:
    """역할 할당 정보"""
    id: str
    name: str
    principal_id: str
    role_definition_id: str
    scope: str


@dataclass
class RoleDefinition:
    """역할 정의 정보"""
    id: str
    name: str
    actions: List[str]
    not_actions: List[str]


class RBACEvaluator:
    """RBAC 권한 평가기"""
    
    def __init__(self, credential: DefaultAzureCredential):
        self.credential = credential
        # TTL 캐시(초) - 디버깅용으로 캐시 비활성화
        self._ttl: int = 0
        self._assignments_cache: Dict[str, Tuple[float, List[RoleAssignment]]] = {}
        self._definitions_cache: Dict[str, Tuple[float, RoleDefinition]] = {}
    
    def _get_current_user_oid(self) -> str:
        """현재 사용자의 Object ID를 반환"""
        try:
            from azure.identity import AzureCliCredential
            import base64
            import json
            
            # Azure CLI 토큰 획득
            token = AzureCliCredential().get_token("https://management.azure.com/.default")
            
            # JWT 토큰 파싱 (header.payload.signature)
            token_parts = token.token.split('.')
            if len(token_parts) != 3:
                raise ValueError("Invalid JWT token format")
            
            # Payload 디코딩
            payload = token_parts[1]
            # Base64 패딩 추가
            payload += '=' * (4 - len(payload) % 4)
            decoded_payload = base64.urlsafe_b64decode(payload)
            payload_data = json.loads(decoded_payload)
            
            # Object ID 추출
            oid = payload_data.get('oid')
            sub = payload_data.get('sub', 'unknown-user')
            print(f"DEBUG: JWT payload - oid: {oid}, sub: {sub}")
            if oid:
                print(f"DEBUG: Using oid: {oid}")
                return oid
            else:
                # oid가 없으면 sub (subject) 사용
                print(f"DEBUG: Using sub: {sub}")
                return sub
                
        except Exception as e:
            print(f"사용자 OID 추출 실패: {e}")
            return "unknown-user"
    
    def _get_role_assignments(self, scope: str) -> List[RoleAssignment]:
        """스코프에 대한 역할 할당을 조회 (Azure CLI 사용)"""
        now = time.time()
        ts_val = self._assignments_cache.get(scope)
        if ts_val and now - ts_val[0] < self._ttl:
            return ts_val[1]
        
        assignments = []
        try:
            import subprocess
            import json
            
            # Azure CLI로 role assignment 조회 (정확한 scope만)
            cmd = ["az", "role", "assignment", "list", "--scope", scope, "--output", "json"]
            print(f"DEBUG: running Azure CLI: {' '.join(cmd)}")
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            
            print(f"DEBUG: Azure CLI returned {len(data)} assignments")
            for assignment_data in data:
                print(f"DEBUG: found assignment - principal_id: {assignment_data['principalId']}, role_def: {assignment_data['roleDefinitionId']}")
                assignments.append(RoleAssignment(
                    id=assignment_data['id'],
                    name=assignment_data['name'],
                    principal_id=assignment_data['principalId'],
                    role_definition_id=assignment_data['roleDefinitionId'],
                    scope=assignment_data['scope']
                ))
            print(f"DEBUG: total assignments processed: {len(assignments)}")
        except Exception as e:
            print(f"역할 할당 조회 실패: {e}")
        
        self._assignments_cache[scope] = (now, assignments)
        return assignments
    
    def _get_role_definition(self, role_definition_id: str) -> Optional[RoleDefinition]:
        """역할 정의를 조회"""
        now = time.time()
        ts_val = self._definitions_cache.get(role_definition_id)
        if ts_val and now - ts_val[0] < self._ttl:
            return ts_val[1]
        
        # 역할 정의 ID에서 구독 ID 추출
        parts = role_definition_id.split('/')
        subscription_id = parts[2] if len(parts) > 2 else None
        
        if not subscription_id:
            return None
        
        client = AuthorizationManagementClient(self.credential, subscription_id)
        
        try:
            definition = client.role_definitions.get_by_id(role_definition_id)
            print(f"DEBUG: Role definition {definition.role_name} - permissions count: {len(definition.permissions) if definition.permissions else 0}")
            if definition.permissions:
                print(f"DEBUG: First permission - actions: {len(definition.permissions[0].actions) if definition.permissions[0].actions else 0}")
                print(f"DEBUG: First permission - not_actions: {len(definition.permissions[0].not_actions) if definition.permissions[0].not_actions else 0}")
                print(f"DEBUG: Sample not_actions: {list(definition.permissions[0].not_actions)[:3] if definition.permissions[0].not_actions else []}")
            
            role_def = RoleDefinition(
                id=definition.id,
                name=definition.role_name,
                actions=definition.permissions[0].actions if definition.permissions else [],
                not_actions=definition.permissions[0].not_actions if definition.permissions else []
            )
            print(f"DEBUG: Created RoleDefinition - actions: {len(role_def.actions)}, not_actions: {len(role_def.not_actions)}")
            self._definitions_cache[role_definition_id] = (now, role_def)
            return role_def
        except Exception as e:
            print(f"역할 정의 조회 실패: {e}")
            return None
    
    def _can_perform_action(self, action: str, actions: List[str], not_actions: List[str]) -> bool:
        """특정 액션을 수행할 수 있는지 확인"""
        # NotActions에 포함되면 거부
        for not_action in not_actions:
            if self._matches_pattern(action, not_action):
                return False
        
        # Actions에 포함되면 허용
        for allowed_action in actions:
            if self._matches_pattern(action, allowed_action):
                return True
        
        return False
    
    def _matches_pattern(self, action: str, pattern: str) -> bool:
        """액션과 패턴이 매치되는지 확인 (와일드카드 지원)"""
        if pattern == "*":
            return True
        
        # 와일드카드 변환
        regex_pattern = pattern.replace("*", ".*").replace("?", ".")
        return bool(re.match(f"^{regex_pattern}$", action))
    
    def get_effective_roles(self, scope: str, principal_id: Optional[str] = None) -> List[Dict[str, str]]:
        """유효한 역할 목록을 반환"""
        if principal_id is None:
            principal_id = self._get_current_user_oid()
        
        print(f"DEBUG: get_effective_roles - scope: {scope}")
        print(f"DEBUG: get_effective_roles - principal_id: {principal_id}")
        
        assignments = self._get_role_assignments(scope)
        effective_roles = []
        seen_roles = set()  # 중복 제거를 위한 set
        
        print(f"DEBUG: get_effective_roles - found {len(assignments)} assignments")
        for assignment in assignments:
            print(f"DEBUG: assignment principal_id: {assignment.principal_id}, role_def_id: {assignment.role_definition_id}")
            if assignment.principal_id == principal_id:
                definition = self._get_role_definition(assignment.role_definition_id)
                if definition:
                    print(f"DEBUG: found matching assignment - role: {definition.name}, role_id: {definition.id}")
                    # 중복 제거: 같은 역할 ID가 이미 추가되었는지 확인
                    if definition.id not in seen_roles:
                        effective_roles.append({
                            "id": definition.id,
                            "name": definition.name
                        })
                        seen_roles.add(definition.id)
                        print(f"DEBUG: added role: {definition.name}")
                    else:
                        print(f"DEBUG: skipped duplicate role: {definition.name}")
                else:
                    print(f"DEBUG: could not get role definition for: {assignment.role_definition_id}")
        
        print(f"DEBUG: get_effective_roles - returning {len(effective_roles)} roles")
        return effective_roles
    
    def get_allowed_actions(self, scope: str, principal_id: Optional[str] = None) -> List[str]:
        """허용된 액션 목록을 반환"""
        if principal_id is None:
            principal_id = self._get_current_user_oid()
        
        assignments = self._get_role_assignments(scope)
        all_actions = set()
        
        for assignment in assignments:
            if assignment.principal_id == principal_id:
                definition = self._get_role_definition(assignment.role_definition_id)
                if definition:
                    all_actions.update(definition.actions)
        
        return list(all_actions)
    
    def get_detailed_permissions(self, scope: str, principal_id: Optional[str] = None) -> Dict:
        """허용된 액션과 제한된 액션을 모두 반환"""
        if principal_id is None:
            principal_id = self._get_current_user_oid()
        
        assignments = self._get_role_assignments(scope)
        all_actions = set()
        all_not_actions = set()
        
        print(f"DEBUG: get_detailed_permissions - found {len(assignments)} assignments")
        
        for assignment in assignments:
            if assignment.principal_id == principal_id:
                definition = self._get_role_definition(assignment.role_definition_id)
                if definition:
                    print(f"DEBUG: Role {definition.name} - actions: {len(definition.actions)}, not_actions: {len(definition.not_actions)}")
                    print(f"DEBUG: not_actions sample: {list(definition.not_actions)[:5]}")
                    all_actions.update(definition.actions)
                    all_not_actions.update(definition.not_actions)
        
        print(f"DEBUG: Total not_actions: {len(all_not_actions)}")
        print(f"DEBUG: Sample not_actions: {list(all_not_actions)[:5]}")
        
        result = {
            "allowedActions": list(all_actions),
            "notActions": list(all_not_actions),
            "effectiveRoles": self.get_effective_roles(scope, principal_id)
        }
        
        print(f"DEBUG: Final result keys: {list(result.keys())}")
        print(f"DEBUG: notActions in result: {'notActions' in result}")
        
        return result
    
    def can_perform_action(self, scope: str, action: str, principal_id: Optional[str] = None) -> bool:
        """특정 액션을 수행할 수 있는지 확인"""
        if principal_id is None:
            principal_id = self._get_current_user_oid()
        
        assignments = self._get_role_assignments(scope)
        
        for assignment in assignments:
            if assignment.principal_id == principal_id:
                definition = self._get_role_definition(assignment.role_definition_id)
                if definition and self._can_perform_action(action, definition.actions, definition.not_actions):
                    return True
        
        return False
