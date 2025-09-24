# 변경 이력 (Changelog)
이 프로젝트의 모든 주요 변경 사항을 기록합니다.
형식: Keep a Changelog / 버전: Semantic Versioning.

## [v0.3.1] - 2025-01-12
### 추가됨
- **GET `/mcp/tools`**: 사용 가능한 MCP 툴 목록 및 설명 조회 엔드포인트
- **동적 툴 정보**: 클라이언트에서 실시간 툴 목록 조회 지원
- **툴 메타데이터**: 각 툴의 설명, 파라미터 정보 구조화

### 변경됨
- **MCP 툴 정보 노출**: 하드코딩된 툴 설명 → 동적 툴 정보 제공
- **클라이언트 통합**: LLM이 실제 툴 정보를 기반으로 판단 가능

### 참고
- LLM 기반 클라이언트와의 완전한 통합 지원

## [v0.3.0] - 2025-01-11
### 추가됨
- **v0.3.0 MCP 툴 + Apply 확장**: Azure 서비스 계획/적용/요청을 MCP 툴로 노출
  - `POST /mcp/tools`: MCP 툴 요청 처리 엔드포인트
  - **MCP 툴 3종**: `azs.plan()`, `azs.apply()`, `azs.request()` 
  - **Apply 확장**: AKS, ACR, Key Vault, Event Hubs 생성 기능(az CLI 기반 PoC)
  - **ServiceNow 신청서**: JSON/Markdown 형식 자동 생성
- **새 모듈**: `mcp_tools.py` (MCP 툴 핸들러, 리소스 생성기)
 - 문서: 클라이언트 플로우/상태 관리 가이드(README), 삭제 예시 보강, `docs/SPEC_next.md` 추가

### 변경됨
- FastAPI 제목: "AZ Servant — v0.3.0 (MCP Tools + Apply)"
- 서버에서 LLM 사용 제거(요약/권고는 LangGraph 에이전트로 이동)
- `requirements.txt`: OpenAI 의존성 제거
- MCP 툴에서 기존 v0.2.0 API 로직 재사용 (최소 변경 원칙)

### 참고
- 로컬 PoC: 승인 토큰 요구 없음 (실환경에서는 승인/러너 게이팅 도입 권장)

## [v0.2.0] - 2025-09-11
### 추가됨
- **v0.2.0 RBAC + Policy 점검 API**: Azure 리소스 배포 전 권한 및 정책 사전 점검 기능
  - `GET /plan/capabilities`: 스코프별 허용 액션 및 유효 역할 조회
  - `GET /plan/constraints`: 정책 제약 (허용 리전, 필수 태그, 거부 정책) 조회  
  - `GET /plan/check`: 리소스 생성 계획 종합 점검 (RBAC + Policy 통합 평가)
- **새 모듈**: `scopes.py` (스코프 파서), `authz.py` (RBAC 평가), `policy_eval.py` (정책 평가)
- **Pydantic 모델**: `CapabilitySummary`, `PolicySummary`, `PlanDecision` 응답 스키마
- **Azure SDK 의존성**: `azure-mgmt-authorization`, `azure-mgmt-policyinsights` 추가
- **실제 사용자 인증**: JWT 토큰 파싱을 통한 실제 사용자 OID 추출 및 권한 조회
- **25개 리소스 타입 지원**: AKS, VNet, PostgreSQL, Redis 등 실제 구독 리소스 기반 확장
- 버전별 Cursor 프롬프트 디렉터리(`docs/prompts/`) 추가
- v0.2.0 프롬프트 초안: RBAC + Policy 점검 (`docs/prompts/v0.2.0.md`)
- 릴리스 노트 템플릿 추가(`docs/releases/_template.md`) 및 규칙(.cursorrules)
 - 리소스 그룹 Apply API (`POST/DELETE /apply/resource-group`) — 승인 토큰 필요
 - TTL 캐시(60s) 도입 (RBAC/Policy 조회)

### 변경됨
- 문서 체계 정리: 중복/초안 파일 제거 (docs/guide.md, docs/prompt.md, docs/SPEC_walking_skeleton.md)
- 저장소 위생: `.gitignore` 추가 (__pycache__/, .venv/, .env 등 무시)
- README 최신 구조로 보정(빠른 시작/현재 구현 상태 추가)
- 문서 한글화: PR 템플릿, CHANGELOG 머리말, .env.example 주석
- Cursor 규칙 강화: 기록 보존/Append 원칙, 최소 변경, 문서 기본 언어(한국어)
 - Policy 평가: initiative 분해/파라미터 해석 보완(Allowed locations/Require tag/Deny)

## [v0.2.0] - 2025-09-11
### 추가됨
- Plan API: `/plan/capabilities`, `/plan/constraints`, `/plan/check`
- Apply API(리소스 그룹): `POST/DELETE /apply/resource-group` (승인 토큰)
- TTL 캐시(60s)
### 변경됨
- Policy 규칙 파서 개선(initiative 멤버·파라미터 매핑, 대표 패턴 지원)
### 참고
- 인증: DefaultAzureCredential (로컬 `az login`)
### 수정됨
- 

## [v0.1.0] - 2025-09-10
### 추가됨
- Walking Skeleton: /me/subscriptions, /me/resource-groups, /me/scopes, /healthz
### 참고
- 인증: DefaultAzureCredential (로컬 `az login`)
## [v0.2.2] - 2025-09-11
### 변경됨
- 로컬 PoC 보안을 단순화: Apply 엔드포인트 승인 토큰 요구 제거(문서/예시 일치)
### 참고
- 실환경 전환 시 승인/러너 게이팅 도입 권장
