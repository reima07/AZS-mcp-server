# Troubleshooting

## /me/subscriptions 결과가 비어있음
- `az account show`로 테넌트/구독 확인
- 해당 구독에 Reader 이상 권한 필요

## DefaultAzureCredential 오류
- `az login` 재시도, 프록시/WSL 환경 확인
- `exclude_managed_identity_credential=True` 옵션 유지

## 느린 첫 호출
- SDK warm-up; 캐시 전략은 다음 버전에 고려
