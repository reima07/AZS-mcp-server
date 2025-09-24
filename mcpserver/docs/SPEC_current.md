# SPEC (Current) — v0.3.1

서버 타이틀: "AZ Servant — v0.3.0 (MCP Tools + Apply)"

## Scope
- Auth: DefaultAzureCredential (로컬 `az login` 토큰 사용), 인증/로그인 보조 엔드포인트
- Read APIs: `/me/*` 조회 세트(구독/리소스그룹/스코프), `/healthz`
- Plan APIs: RBAC/Policy 기반 사전 점검(capabilities/constraints/check)
- Apply APIs: 리소스 그룹 생성/삭제(HTTP), 리소스 생성/삭제(일부) MCP 툴 경유
- MCP Tools: `azs.plan`, `azs.apply`, `azs.request`, `azs.spec`, 목록 조회 `GET /mcp/tools`

## Endpoints
- GET `/healthz` — 서버 상태
- GET `/me/subscriptions` — 구독 목록
- GET `/me/resource-groups?subscription_id=<SUB_ID|/subscriptions/<id>>` — 리소스 그룹 목록
- GET `/me/scopes` — 구독 + 리소스그룹 요약
- GET `/plan/capabilities?scope=<SCOPE>` — 허용 액션, NotActions, 유효 역할(+역할별 상세)
- GET `/plan/constraints?scope=<SCOPE>` — 허용 리전, 필수 태그, deny 요약
- GET `/plan/check?scope=<SCOPE>&resourceType=<TYPE>&location=<LOC>&tags=<JSON>` — 종합 판정
- POST `/apply/resource-group` — 리소스 그룹 생성
- DELETE `/apply/resource-group?scope=/subscriptions/<id>/resourceGroups/<name>` — 리소스 그룹 삭제
- POST `/mcp/tools` — MCP 툴 실행(요청 스키마: `{name, arguments}` / 응답: `{content[], is_error}`)
- GET `/mcp/tools` — 사용 가능한 MCP 툴 카탈로그(JSON)
- GET `/auth/status` — 현재 `az account show` 기반 인증 상태 확인
- POST `/auth/login` — 수동 로그인 안내(명령어/단계)

## MCP Tools (catalog)
- `azs.plan(scope, mode, resourceType?, location?, tags?)`
  - mode=`capabilities|constraints|check`
  - capabilities: `allowedActions`, `notActions`, `effectiveRoles`, `roleDetails`
  - constraints: `allowedLocations`, `requiredTags`, `denies`
  - check: `status=allowed|blocked|conditional`, `reasons[]`, `hints[]`
- `azs.apply(action, resourceType, scope, params)`
  - action=`create|delete|update`(PoC)
  - 지원: AKS/ACR/KeyVault/Event Hubs/Storage/Redis/Managed Disks/VNet 등 일부 리소스(az CLI 기반)
- `azs.request(blockedItems[], scope, ...)` — ServiceNow 양식(JSON/Markdown + agent v1)
- `azs.spec(resourceType)` — 리소스 타입별 파라미터 스펙
- `azs.list_subscriptions()` / `azs.list_resource_groups(scope)` / `azs.list_resources(scope, resourceType?)` / `azs.get_resource(id)`

## Acceptance Criteria
- 모든 엔드포인트 2xx 정상 동작, 오류 시 4xx/5xx + 명확 메시지
- RBAC/Policy 조회는 실제 계정/역할/정책을 반영하여 합리적 결과 제공
- MCP 툴 카탈로그는 서버 상태와 일치하며 클라이언트가 이를 기반으로 의사결정 가능
- P50 < 1.5s (네트워크/CLI 지연 변동성 고려)

## Out of Scope
- 승인/러너 게이팅(실환경) — v0.4+ 재도입
- SDK 전환(az CLI 의존 최소화) — 점진 전환
- 조직 전역 정책/관리그룹 상속 전부 — 대표 패턴 우선
