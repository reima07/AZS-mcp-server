# PR Check List

## ✅ Self Code Review finished
- [x] 코드 리뷰 완료
- [x] 유저스토리 1 요구사항 충족 확인
- [x] API 엔드포인트 동작 확인
- [x] 에러 처리 및 예외 상황 대응 확인

## ✅ Formatting finished
- [x] Python 코드 포맷팅 (PEP 8 준수)
- [x] FastAPI 엔드포인트 구조화
- [x] Pydantic 모델 정의 완료
- [x] 타입 힌트 적용

## ✅ Lint/Unit/Module Test finished
- [x] pytest 기반 단위 테스트 작성
- [x] 유저스토리 1 테스트 케이스 구현
- [x] API 엔드포인트 테스트 실행
- [x] 백엔드 응답 검증 완료

## What does this PR do
**유저스토리 1: Azure 운영 엔지니어 기본 운영 작업 지원**

Azure 운영 엔지니어가 포털에 들어가지 않고도 기본 운영 작업을 즉시 처리할 수 있도록 MCP 서버 백엔드 기능을 구현했습니다.

### 주요 기능
- **구독/리소스 그룹 목록 조회**: RBAC 범위 내 구독 및 리소스 그룹 목록 제공
- **리소스 그룹 생성**: 이름만 제공하면 koreacentral 리전으로 자동 생성
- **리소스 그룹 삭제**: 권한 확인 후 안전한 삭제 처리
- **권한 및 정책 검증**: RBAC 권한 및 Azure Policy 사전 점검
- **에러 처리**: 네트워크/인증 문제 시 적절한 로그 및 안내 제공

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

### 에러 처리
```json
// 권한 부족 시
{
  "detail": "RBAC 권한 부족: Microsoft.Resources/resourceGroups/write"
}

// 정책 위반 시  
{
  "detail": "허용되지 않은 리전: eastus"
}
```

## Related Issues
- 유저스토리 1: Azure 운영 엔지니어 기본 운영 작업 지원
- MCP 서버 백엔드 기능 구현
- RBAC 권한 및 Azure Policy 통합 검증
