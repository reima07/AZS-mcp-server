# ADR 0001 — PoC용 인증 전략
- 결정: PoC에서 Plan(읽기) 작업에 로컬 az login과 함께 DefaultAzureCredential (MI 제외) 사용
- 근거: 가장 쉬운 방법, 비밀 저장 불필요, 향후 사용자 위임 Plan과 일치
- 결과: Apply(쓰기)는 향후 MI 러너로 연기; PoC는 쓰기 작업을 수행하지 않음
