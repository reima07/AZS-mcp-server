# PR Check List
- [x] * Self Code Review finished
- [x] * Formatting finished  
- [x] * Lint/Unit/Module Test finished

## What does this PR do
**유저스토리 1: Azure 운영 엔지니어 기본 운영 작업 지원**

Azure 운영 엔지니어가 포털에 들어가지 않고도 기본 운영 작업을 즉시 처리할 수 있도록 MCP 서버 백엔드 기능을 구현했습니다.

### 주요 기능
- **구독/리소스 그룹 목록 조회**: RBAC 범위 내 구독 및 리소스 그룹 목록 제공
- **리소스 그룹 생성**: 이름만 제공하면 koreacentral 리전으로 자동 생성
- **리소스 그룹 삭제**: 유효한 대상에 대해서만 성공하고, 존재하지 않거나 권한이 없으면 실패 로그 JSON 반환
- **네트워크/인증 문제 처리**: ZTNA, az 로그인 문제 발생 시 관련 로그 반환
- **ARM 응답 처리**: 모든 ARM의 응답을 유효한 값만 추출해 성공여부와 로그를 에이전트에 전달

### 구현된 API 엔드포인트
- `GET /me/subscriptions` - 구독 목록 조회
- `GET /me/resource-groups` - 리소스 그룹 목록 조회  
- `POST /apply/resource-group` - 리소스 그룹 생성
- `DELETE /apply/resource-group` - 리소스 그룹 삭제
- `GET /auth/status` - Azure 로그인 상태 확인
- `GET /healthz` - 헬스체크

## 테스트방법

### Unit Test
```bash
cd mcpserver
python3 -m venv .venv && source .venv/bin/activate
pip install pytest httpx
python -m pytest tests/test_user_story_1.py -v
```

### API Test
```bash
# 서버 실행
uvicorn app:app --reload --port 8080

# API 테스트
curl -X GET "http://localhost:8080/healthz"
curl -X GET "http://localhost:8080/me/subscriptions"
curl -X GET "http://localhost:8080/me/resource-groups?subscription_id=<SUB_ID>"
```

### 테스트 시나리오
1. **구독 목록 조회**: `GET /me/subscriptions` → 실제 보유 구독만 표시
2. **리소스 그룹 목록**: `GET /me/resource-groups?subscription_id=<ID>` → 해당 구독의 RG만 표시
3. **리소스 그룹 생성**: `POST /apply/resource-group` → koreacentral로 생성
4. **리소스 그룹 삭제**: `DELETE /apply/resource-group?scope=<SCOPE>` → 권한 확인 후 삭제
5. **에러 처리**: ZTNA/로그인 문제 시 적절한 에러 메시지 반환

## 테스트화면

### 성공적인 API 응답
```json
// GET /me/subscriptions
[
  {
    "id": "/subscriptions/216abd39-c22a-4064-9129-c204d9afc156",
    "name": "sub-az01-co001501-sbox-poc-144"
  },
  {
    "id": "/subscriptions/56de1b0c-949f-4a33-adde-3845e8dbb2e4", 
    "name": "sub-az01-bg013601-dev-icstr"
  }
]

// GET /me/resource-groups
[
  {
    "id": "/subscriptions/56de1b0c-949f-4a33-adde-3845e8dbb2e4/resourceGroups/rg-jiwoo-test",
    "name": "rg-jiwoo-test",
    "location": "koreacentral",
    "tags": null
  }
]
```

### 테스트 결과
- **단위 테스트**: 11/14 통과 (78.6%)
- **API 테스트**: 모든 핵심 기능 정상 동작
- **실제 Azure API 연동**: 구독 3개, 리소스 그룹 6개 조회 성공

## Related Issues
- 유저스토리 1: Azure 운영 엔지니어 기본 운영 작업 지원
- MCP 서버 백엔드 기능 구현
- 기본적인 에러 처리 및 로깅
