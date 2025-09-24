# SPEC (Next Target) — v0.4.x

## Goals
- Stateful MCP: 세션의 `workingSubscription`/`workingResourceGroup` 활용 강화를 표준화
- Delete 확대: `azs.apply(action=delete)` 리소스별 매핑(ACR/KV/EH/RG/AKS/Storage/Redis 등)
- SDK 전환: 가능 영역 az CLI → Azure SDK로 단계적 이전(권한/속도/일관성)
- Policy 확장: IfNotExists/DeployIfNotExists/anyOf 등 고급 규칙 최소 커버리지
- RBAC 확장: 그룹/앱/서비스 주체 상속 반영, 액션 매핑 테이블 보강
- 보안/감사: Apply dry-run, 승인 토큰·내부 러너 게이트 재도입, 표준 태깅/증빙 번들
- Observability: 요청 ID, Activity Log 링크, 최소 메트릭/로그

## API/MCP
- `scope` 미지정 시 세션 상태로 보충하는 어댑터 규약(클라이언트)
- `azs.apply`: delete 액션 보강(존재하지 않아도 idempotent success)
- `azs.request`: 템플릿 확장(역할/스코프/근거/리전/태그)

## Server
- 표준 MCP 서버(FastMCP stdio/WebSocket) 어댑터 유지·강화(HTTP 브리지 병행)
- CLI 호출 공통 에러 포맷: `code`, `message`, `stderr`, `suggestion`

## Client (LangGraph)
- 노드: Plan → Confirm → Apply → Verify → (Fail: Request)
- 메모리: `workingSubscription`, `workingResourceGroup`, 최근 Plan 결과 캐시
- 요약/권고: LLM 수행, 툴 호출 근거 로그 남김

## Out of Scope
- 조직 전역 정책/관리그룹 상속 전부(부분 지원 유지)
- 비용·할당량 계산(후속 단계)
