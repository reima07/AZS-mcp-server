# AZ Servant (AZS)

**사내 권한·정책·네트워크(FW) 가시화 & 승인 기반 자동 실행 에이전트**

---

## ✨ 한 줄 요약
개발자가 오늘 당장 *“이 구독 / 리소스그룹 / 리소스에서 무엇을 할 수 있는지”*를 한 화면에 보여주고,  
- 가능한 건 → 승인 후 자동 실행,  
- 불가능한 건 → 근거 포함 ServiceNow 신청서로 전환.  

---

## 🚩 왜 필요한가
- **배포 지연**: 권한·Policy·FW 제약이 사전 가시화 없이 배포 단계에서 드러남 → 반복 대기 & 티켓 폭증  
- **비용·리스크**: 재작업, 운영팀 부담 증가, 근거 추적 어려움  
- **개선 방향**:  
  - 사전 판정(가능/불가/조건부 + 근거)  
  - 승인 절차 내장(무분별 실행 방지)  
  - 신청 페이로드 자동화(ServiceNow)  

---

## 🛠 무엇을 만드는가
- **Agent 중심**: 규칙·API 기반으로 판정 → 승인 → 실행까지 이어지는 표준 업무 에이전트  
- **MCP 인터페이스**: IDE(Cursor/VSCode), Slack, 사내 포털에서 툴처럼 호출  
- **MVP 리소스 대상**  
  1. ACR (카탈로그/태그/Push/Pull)  
  2. Event Hubs (네임스페이스/허브 생성)  
  3. Key Vault (데이터 권한, 시크릿 가이드)  

---

## 🔄 동작 방식 (Plan → Consent → Apply → Request)

### Plan (v0.2.0)
- Effective permissions 조회 (상속 반영)  
- Policy assignments 분석 (리전/태그/SKU/암호화)  
- → 출력: 가능/불가/조건부 표 + 근거

### Consent (v0.3.0)
- MCP 툴을 통한 계획 수립 및 승인 체크
- 로컬 PoC: 승인 토큰 없이 동작 (실환경에서는 승인/러너 게이팅 도입)

### Apply (v0.3.0)
- AKS, ACR, Key Vault, Event Hubs 생성 지원
- MCP 툴 `azs.apply()`를 통한 리소스 생성
- 결과: 리소스 ID, Activity Log, 표준 태그, 증빙 번들

### Request
- 불가 항목을 ServiceNow JSON/MD 양식으로 생성 (역할/스코프/사유/근거/FW 포함)  

---

## 🏗 배포 모델
- **내부 VNet 러너 + Managed Identity(MI)** 권장  
- 컨트롤러(Plan/승인/UI/MCP) ↔ 큐/버스 ↔ 내부 러너(MI 실행)  
- 인바운드 없음, 러너는 폴링 방식  
- 최소 egress: AAD, ARM, KV, ACR, Service Bus/Queue, ServiceNow  

---

## 🔐 권한 모델
- Plan용 MI: 읽기 전용  
- Apply용 MI: 최소 쓰기 권한(리소스별 커스텀 롤)  
- Plan/Apply MI 분리 권장  

---

## 🤖 LLM 사용 범위
- 서버: LLM 미사용(판정/실행은 규칙·SDK·CLI 기반)
- 에이전트(LangGraph): 결과 요약/권고문 생성, 워크플로 분기 결정

---

## 🧑‍💻 MCP 툴 설계
- `azs.plan(scope, repo?, requirements?)`  
- `azs.apply(plan_id, approved_steps[], mode=...)`  
- `azs.request(blocked_items[], plan_id)`  
- 로컬 PoC: 승인 토큰 없음, 실환경에서는 승인/러너 게이팅 도입  

---

## 🚶 사용자 여정 예시
1. 사용자가: “`rg-...-144`에서 Event Hubs 만들고 싶어요. 이름 `ehns-az01-sbox-144`, 지역 `koreacentral`.”  
2. Plan → 권한 OK, Policy 태그 필요, 네트워크 불필요  
3. 승인(실환경) → 체크 후 승인 토큰/내부 게이트 통과  
4. Apply → 내부 러너 실행, 결과 리소스 ID 제공  
5. 불가했다면 → ServiceNow 신청서 생성  

---

## 📊 기대 효과
- 리드타임 단축 (배포 실패 사전 예방)  
- 심사 효율 ↑ (근거 포함 신청서)  
- 표준 준수 강화 (태그·리전·SKU·암호화)  
- DevEx 향상 (즉시 “가능한 경로” 확보)  
- MCP 재사용 (IDE/Slack/Hub 어디서든 동일 경험)  

---

## 📌 범위 밖 (초기 버전)
- 자동 승인/제출 (운영 정책상 분리 유지)  
- 전 사내 모든 리소스 타입(초기엔 ACR/Event Hubs/KV)  
- 런타임 트래픽 튜닝(앱 레벨 최적화)  

---

## 📚 문서 구조
- **README.md**: 프로젝트 개요/설치/실행  
- **docs/CHANGELOG.md**: 버전별 변경 내역  
- **docs/releases/**: 버전별 릴리스 노트  
- **docs/TROUBLESHOOTING.md**: 문제 해결집  
- **docs/decisions/**: 주요 아키텍처 결정(ADR)  
- **docs/SPEC_current.md**: 현재 목표 스펙  

---

## 🔖 버전 관리 규칙
- 단순 모드: 바로 `main`에 커밋(또는 짧은 로컬 브랜치 후 로컬 머지)  
- 버전은 로컬 annotated tag(`v0.1.0` 등)로 관리. 원격 없음.  
- 문서 갱신: 코드 변경 시 CHANGELOG/문서도 함께 수정  

---

## 🚀 빠른 시작 (로컬 PoC)
- Python 3.10+, Azure CLI(`az login`) 필요

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
az login && az account set --subscription <SUB_ID>
uvicorn app:app --reload --port 8080
```

서버는 LLM을 사용하지 않습니다(요약/권고는 LangGraph 에이전트에서 수행).

```bash
cp .env.example .env  # 선택
```

## 🧪 현재 구현 상태 (v0.3.1 — MCP Tools + LLM Integration)

### 기본 조회 API (v0.1.0)
- GET `/me/subscriptions` - 구독 목록
- GET `/me/resource-groups?subscription_id=<SUB_ID>` - 리소스 그룹 목록  
- GET `/me/scopes` - 구독 + 리소스 그룹 통합 조회
- GET `/healthz` - 헬스체크

### 권한 및 정책 점검 API (v0.2.0 신규)
- GET `/plan/capabilities?scope=<SCOPE>` - 허용 액션 및 유효 역할 조회
- GET `/plan/constraints?scope=<SCOPE>` - 정책 제약 (허용 리전, 필수 태그, 거부 정책)
- GET `/plan/check?scope=<SCOPE>&resourceType=<TYPE>&location=<LOC>&tags=JSON` - 종합 점검

**지원 리소스 타입 (25개):**
- **컴퓨팅**: AKS, VMSS, Managed Disks
- **네트워킹**: VNet, NSG, Load Balancer, Private Endpoint, Public IP
- **스토리지**: Storage Accounts, Key Vault
- **데이터베이스**: PostgreSQL, Redis Cache
- **컨테이너**: Container Registry
- **메시징**: Event Hub, Event Grid
- **모니터링**: Log Analytics, Monitor, Prometheus
- **관리**: Managed Identity, Resource Groups

**인증 방식:**
- JWT 토큰 파싱을 통한 실제 사용자 OID 추출
- Azure CLI (`az login`) 토큰 기반 권한 조회
- 실제 Azure RBAC 역할 및 권한 정확한 반영

### Apply (리소스 그룹) API (v0.2.2 신규)
- POST `/apply/resource-group` — 리소스 그룹 생성
- DELETE `/apply/resource-group?scope=/subscriptions/<SUB_ID>/resourceGroups/<RG_NAME>` — 리소스 그룹 삭제

환경 변수(로컬 PoC 최소): 별도 필수 항목 없음. `.env.example` 참조(캐시 TTL 등).

### 사용 예시

```bash
# 기본 조회
curl "http://localhost:8080/me/subscriptions"
curl "http://localhost:8080/me/resource-groups?subscription_id=<SUB_ID>"

# 권한 점검
curl "http://localhost:8080/plan/capabilities?scope=/subscriptions/<SUB_ID>"
curl "http://localhost:8080/plan/constraints?scope=/subscriptions/<SUB_ID>"

# 리소스 생성 계획 점검 (태그 없이)
curl "http://localhost:8080/plan/check?scope=/subscriptions/<SUB_ID>&resourceType=Microsoft.Storage/storageAccounts&location=koreacentral"

# 리소스 생성 계획 점검 (태그 포함, URL 인코딩 필요)
curl "http://localhost:8080/plan/check?scope=/subscriptions/<SUB_ID>&resourceType=Microsoft.Storage/storageAccounts&location=koreacentral&tags=%7B%22Environment%22%3A%22dev%22%7D"

# Apply (리소스 그룹 생성/삭제)
curl -H "Content-Type: application/json" \
     -d '{"scope":"/subscriptions/<SUB_ID>","name":"rg-azs-demo","location":"koreacentral","tags":{"env":"dev"}}' \
     http://localhost:8080/apply/resource-group

curl -X DELETE \
     "http://localhost:8080/apply/resource-group?scope=/subscriptions/<SUB_ID>/resourceGroups/rg-azs-demo"
```

### MCP 툴 API (v0.3.0 신규)
- POST `/mcp/tools` - MCP 툴 실행
- **GET `/mcp/tools` - 사용 가능한 MCP 툴 목록 조회 (v0.3.1 신규)**

### MCP 툴 사용 예시 (v0.3.0 신규)

```bash
# MCP 툴 요청 (JSON-RPC 형식)
curl -X POST "http://localhost:8080/mcp/tools" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "azs.plan",
       "arguments": {
         "scope": "/subscriptions/<SUB_ID>",
         "mode": "capabilities"
       }
     }'

# 정책 제약 조회
curl -X POST "http://localhost:8080/mcp/tools" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "azs.plan",
       "arguments": {
         "scope": "/subscriptions/<SUB_ID>",
         "mode": "constraints"
       }
     }'

# 리소스 생성 체크
curl -X POST "http://localhost:8080/mcp/tools" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "azs.plan",
       "arguments": {
         "scope": "/subscriptions/<SUB_ID>",
         "mode": "check",
         "resourceType": "Microsoft.ContainerService/managedClusters",
         "location": "koreacentral"
       }
     }'

# AKS 클러스터 생성
curl -X POST "http://localhost:8080/mcp/tools" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "azs.apply",
       "arguments": {
         "action": "create",
         "resourceType": "Microsoft.ContainerService/managedClusters",
         "scope": "/subscriptions/<SUB_ID>",
         "params": {
           "name": "aks-demo",
           "resourceGroup": "rg-aks-demo",
           "location": "koreacentral",
           "nodeCount": 1,
           "nodeVmSize": "Standard_B2s"
         }
       }
     }'

# ServiceNow 신청서 생성
curl -X POST "http://localhost:8080/mcp/tools" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "azs.request",
       "arguments": {
         "scope": "/subscriptions/<SUB_ID>",
         "blockedItems": [
           {
             "resourceType": "Microsoft.Compute/virtualMachines",
             "reason": "권한 부족: Microsoft.Compute/virtualMachines/write"
           }
         ]
       }
     }'

# (참고) 삭제 예시
# 현재 MCP 툴의 delete 액션은 리소스별 매핑 확장 예정입니다.
# 리소스 그룹 삭제는 HTTP 엔드포인트를 사용할 수 있습니다.
curl -X DELETE \
     "http://localhost:8080/apply/resource-group?scope=/subscriptions/<SUB_ID>/resourceGroups/<RG_NAME>"

# MCP 툴 목록 조회 (v0.3.1 신규)
curl -X GET "http://localhost:8080/mcp/tools"
```

## 🗺️ 클라이언트(에이전트) 플로우 권장
- 로그인: 사용자가 직접 `az login`(브라우저) 실행. 실패 시 친절 메시지(테넌트/구독 선택 안내).
- 구독 선택: `GET /me/subscriptions` → 하나 선택 → 세션에 `workingSubscription` 저장.
- 리소스그룹 선택: `GET /me/resource-groups?subscription_id=<SUB_ID>` → 선택 또는 새 RG 제안 → `workingResourceGroup` 저장.
- 계획(Plan): MCP `azs.plan`으로 capabilities/constraints/check 호출. 결과를 사용자에게 요약(에이전트 LLM) + 수정 힌트 제공.
- 확인(Consent): “가능합니다. 생성할까요?” 질문 → 사용자가 확인하면 Apply 수행.
- 적용(Apply): MCP `azs.apply` 또는 HTTP `/apply/*` 호출. 성공 시 리소스 ID/증빙 출력.
- 실패(Request): 차단 사유를 `azs.request`로 신청서 초안 생성, 사용자에게 전달.

권장 상태 변수(클라이언트 세션)
- `workingSubscription`: `/subscriptions/<id>`
- `workingResourceGroup`: `rg-name` (필요 시 위치/태그 동반)

```

> **v0.3.0 주요 기능:**
> - **MCP 툴**: `azs.plan()`, `azs.apply()`, `azs.request()` 3종 (서버 측 LLM 없음)
> - **Apply 확장**: AKS, ACR, Key Vault, Event Hubs 생성 지원
> - **로컬 PoC**: 승인 토큰 요구 없음 (실환경에서는 승인/러너 게이팅 도입 권장)
