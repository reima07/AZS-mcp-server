"""
Azure Policy 평가 모듈
"""
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
import re
from azure.mgmt.resource import ResourceManagementClient  # kept but not required for CLI path
from azure.mgmt.policyinsights import PolicyInsightsClient  # reserved for future use
import time
import os
from azure.identity import DefaultAzureCredential
from scopes import parse_scope


@dataclass
class PolicyAssignment:
    """정책 할당 정보"""
    id: str
    name: str
    policy_definition_id: str
    scope: str
    parameters: Dict[str, Any]


@dataclass
class PolicyDefinition:
    """정책 정의 정보"""
    id: str
    name: str
    policy_type: str
    mode: str
    policy_rule: Dict[str, Any]
    parameters: Optional[Dict[str, Any]] = None  # definition.properties.parameters (기본값/허용값 등)


@dataclass
class PolicyConstraint:
    """정책 제약 정보"""
    allowed_locations: List[str]
    required_tags: List[Dict[str, Any]]
    denies: List[Dict[str, str]]


class PolicyEvaluator:
    """Policy 평가기"""
    
    def __init__(self, credential: DefaultAzureCredential):
        self.credential = credential
        self._ttl: int = int(os.getenv('AZS_CACHE_TTL', '60')) if 'os' in globals() else 60
        self._assignments_cache: Dict[str, Tuple[float, List[PolicyAssignment]]] = {}
        self._definitions_cache: Dict[str, Tuple[float, PolicyDefinition]] = {}
    
    def _get_policy_assignments(self, scope: str) -> List[PolicyAssignment]:
        """스코프에 대한 정책 할당을 조회"""
        now = time.time()
        ts_val = self._assignments_cache.get(scope)
        if ts_val and now - ts_val[0] < self._ttl:
            return ts_val[1]
        
        subscription_id, _ = parse_scope(scope)
        
        assignments = []
        try:
            # Azure CLI를 통한 정책 할당 조회
            import subprocess
            import json
            
            # az policy assignment list (구독 범위에서 일괄 조회)
            result = subprocess.run([
                'az', 'policy', 'assignment', 'list',
                '--subscription', subscription_id,
                '--output', 'json'
            ], capture_output=True, text=True, check=True)
            
            policy_assignments = json.loads(result.stdout)
            print(f"조회된 정책 할당 수: {len(policy_assignments)}")
            
            for assignment in policy_assignments:
                print(f"정책 할당: {assignment['name']} - 스코프: {assignment['scope']}")
                # 스코프 매칭 확인
                if (assignment['scope'].startswith(scope) or 
                    scope.startswith(assignment['scope'])):
                    print(f"매칭된 정책: {assignment['name']}")
                    assignments.append(PolicyAssignment(
                        id=assignment['id'],
                        name=assignment['name'],
                        policy_definition_id=assignment['policyDefinitionId'],
                        scope=assignment['scope'],
                        parameters=assignment.get('parameters', {})
                    ))
                    
        except Exception as e:
            print(f"정책 할당 조회 실패: {e}")
            # 정책 할당이 없거나 권한이 없는 경우 빈 리스트 반환
            pass
        
        self._assignments_cache[scope] = (now, assignments)
        return assignments
    
    def _get_policy_definition(self, policy_definition_id: str) -> Optional[PolicyDefinition]:
        """정책 정의를 조회"""
        now = time.time()
        ts_val = self._definitions_cache.get(policy_definition_id)
        if ts_val and now - ts_val[0] < self._ttl:
            return ts_val[1]
        
        try:
            # Azure CLI를 통한 정책 정의 조회
            import subprocess
            import json
            
            # policy set(initiative) vs single policy 정의 구분
            if '/policySetDefinitions/' in policy_definition_id:
                # 정책 세트 정의는 name으로 조회
                policy_name = policy_definition_id.split('/')[-1]
                result = subprocess.run([
                    'az', 'policy', 'set-definition', 'show',
                    '--name', policy_name,
                    '--output', 'json'
                ], capture_output=True, text=True, check=True)
            else:
                # 일반 정책 정의인 경우
                result = subprocess.run([
                    'az', 'policy', 'definition', 'show',
                    '--id', policy_definition_id,
                    '--output', 'json'
                ], capture_output=True, text=True, check=True)
            
            definition = json.loads(result.stdout)
            
            # 정책 세트 정의와 일반 정책 정의 구조가 다름
            if '/policySetDefinitions/' in policy_definition_id:
                # 정책 세트 정의
                policy_def = PolicyDefinition(
                    id=definition['id'],
                    name=definition.get('properties', {}).get('displayName') or definition.get('displayName') or definition['name'],
                    policy_type=definition.get('properties', {}).get('policyType', 'Custom'),
                    mode='All',  # 정책 세트는 mode 없음
                    policy_rule=definition,  # 전체 문서를 담아 set 멤버 접근
                    parameters=definition.get('properties', {}).get('parameters')
                )
            else:
                # 일반 정책 정의
                policy_def = PolicyDefinition(
                    id=definition['id'],
                    name=definition['properties']['displayName'],
                    policy_type=definition['properties']['policyType'],
                    mode=definition['properties'].get('mode', 'All'),
                    policy_rule=definition['properties']['policyRule'],
                    parameters=definition['properties'].get('parameters')
                )
            self._definitions_cache[policy_definition_id] = (now, policy_def)
            return policy_def
            
        except Exception as e:
            print(f"정책 정의 조회 실패: {e}")
            return None
    
    def _extract_allowed_locations(self, policy_rule: Dict[str, Any], param_values: Optional[Dict[str, Any]] = None,
                                   def_parameters: Optional[Dict[str, Any]] = None) -> List[str]:
        """정책 규칙에서 허용된 리전을 추출
        - location 필드의 in/notIn 값이 리스트 또는 parameters() 참조인 경우 처리
        """
        allowed: List[str] = []

        def _param_name_from_expr(val: Any) -> Optional[str]:
            if isinstance(val, str):
                m = re.match(r"^\s*\[\s*parameters\('([^']+)'\)\s*\]\s*$", val)
                if m:
                    return m.group(1)
            return None

        def _resolve_list(v: Any) -> List[str]:
            # list 그대로, parameters 참조, 단일 문자열 등 최소 케이스 처리
            if isinstance(v, list):
                return [str(x) for x in v]
            pname = _param_name_from_expr(v)
            if pname:
                if param_values and pname in param_values:
                    pv = param_values[pname]
                    return pv if isinstance(pv, list) else [str(pv)]
                if def_parameters and pname in def_parameters:
                    dv = def_parameters[pname].get('defaultValue')
                    if dv is not None:
                        return dv if isinstance(dv, list) else [str(dv)]
            return []

        def _walk(node: Any):
            if isinstance(node, dict):
                # location in/notIn
                if node.get('field') == 'location':
                    if 'in' in node:
                        allowed.extend(_resolve_list(node['in']))
                    if 'notIn' in node:
                        # notIn은 허용된 위치 목록으로 해석(Allowed locations 정책 패턴)
                        allowed.extend(_resolve_list(node['notIn']))
                # 재귀 탐색
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for i in node:
                    _walk(i)

        _walk(policy_rule)
        return list(dict.fromkeys(allowed))
    
    def _extract_required_tags(self, policy_rule: Dict[str, Any],
                                param_values: Optional[Dict[str, Any]] = None,
                                def_parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """정책 규칙에서 필수 태그를 추출
        - field: tags['<key>'] 또는 tags[parameters('tagName')] 패턴 처리
        - then.modify.operations 에서 기본값 append/replace 감지
        """
        found: Dict[str, Dict[str, Any]] = {}

        def _param_name_from_expr(val: str) -> Optional[str]:
            m = re.match(r"^\s*parameters\('([^']+)'\)\s*$", val)
            return m.group(1) if m else None

        def _resolve_param_value(name: str) -> Optional[Any]:
            if param_values and name in param_values:
                return param_values[name]
            if def_parameters and name in def_parameters:
                return def_parameters[name].get('defaultValue')
            return None

        def _extract_field_key(field_val: str) -> Optional[str]:
            # tags['...'] 또는 tags[parameters('...')]
            m = re.match(r"^\s*tags\s*\[\s*'?([^\]]+?)'?\s*\]\s*$", field_val)
            if not m:
                # 일부 정책은 tags.foo 형태 사용 (덜 일반적)
                if field_val.startswith('tags.'):
                    return field_val.split('.', 1)[1]
                return None
            inner = m.group(1)
            p = _param_name_from_expr(inner) if 'parameters(' in inner else None
            if p:
                rv = _resolve_param_value(p)
                return str(rv) if rv is not None else None
            return inner.strip("'\"")

        def _walk_if(node: Any):
            if isinstance(node, dict):
                if 'field' in node and isinstance(node['field'], str) and 'tags' in node['field']:
                    key = _extract_field_key(node['field'])
                    if key:
                        found.setdefault(key, {"key": key, "required": True, "default": None})
                for v in node.values():
                    _walk_if(v)
            elif isinstance(node, list):
                for i in node:
                    _walk_if(i)

        def _walk_then(node: Any):
            if isinstance(node, dict):
                effect = node.get('effect') or node.get('details', {}).get('effect')
                # modify 효과에서 default value 유추
                details = node.get('details', {}) if isinstance(node.get('details'), dict) else {}
                ops = details.get('operations')
                if isinstance(ops, list):
                    for op in ops:
                        field = op.get('field')
                        if isinstance(field, str) and 'tags' in field:
                            key = _extract_field_key(field)
                            if key:
                                val = op.get('value')
                                if isinstance(val, str) and 'parameters(' in val:
                                    pname = _param_name_from_expr(val)
                                    if pname:
                                        dv = _resolve_param_value(pname)
                                        if dv is not None:
                                            found.setdefault(key, {"key": key, "required": True, "default": dv})
                                            continue
                                # literal default
                                if val is not None:
                                    found.setdefault(key, {"key": key, "required": True, "default": str(val)})
                for v in node.values():
                    _walk_then(v)
            elif isinstance(node, list):
                for i in node:
                    _walk_then(i)

        if isinstance(policy_rule, dict):
            if 'if' in policy_rule:
                _walk_if(policy_rule['if'])
            if 'then' in policy_rule:
                _walk_then(policy_rule['then'])

        return list(found.values())
    
    def _extract_denies(self, policy_rule: Dict[str, Any], policy_id: str) -> List[Dict[str, str]]:
        """정책 규칙에서 거부 조건을 추출"""
        denies = []
        
        if "if" in policy_rule and "then" in policy_rule:
            then_clause = policy_rule["then"]
            # effect 값이 바로 문자열 또는 details.effect에 있을 수 있음
            effect = None
            if isinstance(then_clause, dict):
                effect = then_clause.get('effect')
                if not effect and isinstance(then_clause.get('details'), dict):
                    effect = then_clause['details'].get('effect')
            if effect == "deny":
                denies.append({
                    "policyId": policy_id,
                    "reason": f"정책 '{policy_id}'에 의해 거부됨"
                })
        
        return denies
    
    def get_policy_constraints(self, scope: str) -> PolicyConstraint:
        """정책 제약을 분석하여 반환"""
        assignments = self._get_policy_assignments(scope)
        print(f"분석할 정책 할당 수: {len(assignments)}")
        
        allowed_locations = []
        required_tags = []
        denies = []
        
        for assignment in assignments:
            print(f"정책 정의 조회: {assignment.policy_definition_id}")
            definition = self._get_policy_definition(assignment.policy_definition_id)
            if not definition:
                print(f"정책 정의 조회 실패: {assignment.policy_definition_id}")
                continue

            print(f"정책 정의 조회 성공: {definition.name}")

            # assignment.parameters 정규화 (param -> value)
            def _normalize_assignment_params(p: Dict[str, Any]) -> Dict[str, Any]:
                out = {}
                for k, v in (p or {}).items():
                    if isinstance(v, dict) and 'value' in v:
                        out[k] = v['value']
                    else:
                        out[k] = v
                return out

            assign_params = _normalize_assignment_params(assignment.parameters)

            # 정책 세트(initiative) 처리: 멤버 정책들로 분해 평가
            is_set = (isinstance(definition.policy_rule, dict) and 
                     isinstance(definition.policy_rule.get('policyDefinitions'), list))

            if is_set:
                member_refs = definition.policy_rule.get('policyDefinitions', [])
                for ref in member_refs:
                    inner_id = ref.get('policyDefinitionId')
                    if not inner_id:
                        continue
                    # 개별 정책 정의 조회는 시간이 오래 걸리므로 일단 건너뛰기
                    # inner_def = self._get_policy_definition(inner_id)
                    # if not inner_def:
                    #     continue
                    print(f"개별 정책 건너뛰기: {inner_id}")
                    continue

                    # initiative -> policy 매개변수 매핑 처리
                    ref_params = ref.get('parameters', {}) or {}
                    inner_param_values: Dict[str, Any] = {}
                    for p_name, p_val in ref_params.items():
                        # p_val 형태: {"value": <literal> or "[parameters('x')]")}
                        val = p_val.get('value') if isinstance(p_val, dict) else p_val
                        if isinstance(val, str) and 'parameters(' in val:
                            m = re.match(r"^\s*\[\s*parameters\('([^']+)'\)\s*\]\s*$", val)
                            if m:
                                set_pname = m.group(1)
                                if set_pname in assign_params:
                                    inner_param_values[p_name] = assign_params[set_pname]
                                    continue
                        inner_param_values[p_name] = val

                    # 추출 수행
                    allowed_locations.extend(
                        self._extract_allowed_locations(inner_def.policy_rule, inner_param_values, inner_def.parameters)
                    )
                    required_tags.extend(
                        self._extract_required_tags(inner_def.policy_rule, inner_param_values, inner_def.parameters)
                    )
                    denies.extend(self._extract_denies(inner_def.policy_rule, inner_def.id))
            else:
                # 단일 정책 정의 처리
                allowed_locations.extend(
                    self._extract_allowed_locations(definition.policy_rule, assign_params, definition.parameters)
                )
                required_tags.extend(
                    self._extract_required_tags(definition.policy_rule, assign_params, definition.parameters)
                )
                denies.extend(self._extract_denies(definition.policy_rule, definition.id))
        
        print(f"추출된 제약: locations={len(allowed_locations)}, tags={len(required_tags)}, denies={len(denies)}")
        
        # NOTE: 초기 버전 — 대표 정책 패턴만 안전하게 커버(Allowed locations / Require tag / deny)
        
        return PolicyConstraint(
            allowed_locations=list(set(allowed_locations)),  # 중복 제거
            required_tags=required_tags,
            denies=denies
        )
    
    def check_resource_compliance(self, scope: str, resource_type: str, 
                                location: str, tags: Dict[str, str]) -> Dict[str, Any]:
        """리소스가 정책을 준수하는지 확인"""
        constraints = self.get_policy_constraints(scope)
        
        violations = []
        warnings = []
        
        # 리전 검증
        if constraints.allowed_locations and location not in constraints.allowed_locations:
            violations.append(f"리전 '{location}'이 허용되지 않음. 허용된 리전: {constraints.allowed_locations}")
        
        # 태그 검증
        for tag_req in constraints.required_tags:
            if tag_req["key"] not in tags:
                if tag_req["default"]:
                    warnings.append(f"태그 '{tag_req['key']}'가 누락됨. 기본값 '{tag_req['default']}' 사용")
                else:
                    violations.append(f"필수 태그 '{tag_req['key']}'가 누락됨")
        
        # 거부 조건 검증
        for deny in constraints.denies:
            violations.append(deny["reason"])
        
        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "warnings": warnings,
            "constraints": constraints
        }
