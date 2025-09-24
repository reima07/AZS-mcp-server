# AZ Servant - 사용자 가이드

## 🚀 빠른 시작
버전/구성요소: 서버 v0.3.1, 클라이언트 v0.3.1 (상세는 ARCHITECTURE.md의 버전 매트릭스 참고)

참조 문서: 서버/클라이언트 릴리즈 노트는 각각 `mcpserver/docs/CHANGELOG.md`, `mcpclient/docs/CHANGELOG.md`에 위치합니다.

### **1. 사전 준비**

#### **필수 요구사항**
- Python 3.10+
- Azure CLI (`az login` 완료)

#### **선택 항목**
- OpenAI API 키(클라이언트 의도 파싱에 사용. 미설정 시 룰 기반 폴백)
  - 설정 시: 자연스러운 대화 및 LLM 기반 의도 파싱
  - 미설정 시: 룰 기반 폴백으로 기본 기능 제공

#### **Azure 로그인**
```bash
az login
az account set --subscription <YOUR_SUBSCRIPTION_ID>
```

### **2. MCP Server 실행**

```bash
cd /Users/jiwoo/Desktop/kltecho/project/mcpserver
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```

**확인**: http://localhost:8080/healthz → `{"ok": true}` 응답

### **3. MCP Client 실행**

```bash
cd /Users/jiwoo/Desktop/kltecho/project/mcpclient
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 환경변수 설정(선택)
echo "MCP_SERVER_BASE=http://localhost:8080" > .env
# echo "OPENAI_API_KEY=sk-your-api-key-here" >> .env

uvicorn app:app --reload --port 9090
```

**확인**: http://localhost:9090/ → 웹 UI 접속

### **4. Azure 로그인(로컬 수동 안내)**
웹 UI 우상단 "Azure 로그인" 상태를 확인하세요. 인증 필요 시 "로그인" 버튼을 누르면 터미널에서 실행할 명령어가 안내됩니다.

1) 터미널에서 안내된 `az login ...` 명령을 실행하고 브라우저 인증을 완료합니다.
2) 완료 후 웹 UI를 새로고침하면 구독 목록 드롭다운이 활성화됩니다.

## 💬 사용법 가이드

### **기본 대화**

#### **인사말**
```
사용자: 안녕
시스템: 안녕하세요! 어떤 Azure 작업을 도와드릴까요?
```

#### **일반 대화**
```
사용자: 오늘 날씨 어때?
시스템: 죄송하지만 날씨 정보는 제공할 수 없습니다. Azure 리소스 관리에 대해 도움을 드릴 수 있습니다.
```

### **Azure 작업 요청**

#### **1. 구독 관리**

**구독 목록 조회**
```
사용자: 내 구독 목록 보여줘
시스템: 구독 목록:
- sub-az01-co001501-sbox-poc-144 (/subscriptions/216abd39-c22a-4064-9129-c204d9afc156)
- sub-az01-bg013601-dev-icstr (/subscriptions/56de1b0c-949f-4a33-adde-3845e8dbb2e4)
- sub-az01-co001501-sbox-poc-145 (/subscriptions/5e05fef0-a221-416f-9d7f-82e33b9529e1)
```

**구독 선택 (자연어)**
```
사용자: dev icstr 구독에서 작업할게
시스템: 작업 대상 구독 설정: /subscriptions/56de1b0c-949f-4a33-adde-3845e8dbb2e4
```

**구독 선택 (번호)**
```
사용자: 145번 구독으로 할게
시스템: 작업 대상 구독 설정: /subscriptions/5e05fef0-a221-416f-9d7f-82e33b9529e1
```

#### **2. 리소스 그룹 관리**

**리소스 그룹 목록**
```
사용자: 리소스 그룹 목록 보여줘
시스템: 리소스 그룹 목록:
- rg-aks-demo (koreacentral)
- rg-storage-demo (koreacentral)
```

#### **3. 권한 및 정책 확인**

**권한 확인**
```
사용자: 내 권한 확인해줘
시스템: 현재 구독에서 허용된 액션:
- Microsoft.Storage/storageAccounts/write
- Microsoft.ContainerRegistry/registries/write
- Microsoft.KeyVault/vaults/write
```

**정책 확인**
```
사용자: 정책 제약사항 확인해줘
시스템: 정책 제약사항:
- 허용 리전: koreacentral, eastus
- 필수 태그: Environment, Project
```

#### **4. 리소스 생성 계획**

**스토리지 계정 생성 계획**
```
사용자: 스토리지 계정 만들어줘
시스템: 스토리지 계정 생성 계획:
- 상태: allowed
- 권한: OK
- 정책: OK
- 위치: koreacentral
생성하시겠습니까?
```

**AKS 클러스터 생성 계획**
```
사용자: AKS 클러스터 생성 가능한지 확인해줘
시스템: AKS 클러스터 생성 계획:
- 상태: conditional
- 권한: OK
- 정책: 태그 필요
- 위치: koreacentral
필요한 태그를 추가하시겠습니까?
```

#### **5. 리소스 생성**

**확인 후 생성**
```
사용자: 응 만들어
시스템: 스토리지 계정 생성 중...
생성 완료: /subscriptions/xxx/resourceGroups/xxx/providers/Microsoft.Storage/storageAccounts/stg-demo
```

#### **6. 리소스 삭제**

**리소스 그룹 삭제(자연어/정확 명시)**
```
사용자: rg-aks-demo 삭제해줘
시스템: 리소스 그룹 삭제 계획: 권한 확인 OK. 삭제하시겠습니까?
사용자: 예
시스템: 삭제 완료: /subscriptions/<id>/resourceGroups/rg-aks-demo
```

## 🧪 테스트 시나리오

### **시나리오 1: 기본 대화 테스트**

1. **인사말 테스트**
   - 입력: "안녕"
   - 예상: 친근한 인사말 응답 (LLM 기반 자연스러운 대화)

2. **일반 대화 테스트**
   - 입력: "오늘 날씨 어때?"
   - 예상: Azure 관련 도움 제공 안내 (LLM이 Azure 작업으로 유도)

### **시나리오 2: 구독 관리 테스트**

1. **구독 목록 조회**
   - 입력: "내 구독 목록 보여줘"
   - 예상: 사용 가능한 구독 목록 표시

2. **자연어 구독 선택**
   - 입력: "dev icstr 구독에서 작업할게"
   - 예상: 해당 구독 ID로 설정

3. **번호 기반 구독 선택**
   - 입력: "145번 구독으로 할게"
   - 예상: 145가 포함된 구독 ID로 설정

### **시나리오 3: 리소스 생성 테스트**

1. **권한 확인**
   - 입력: "내 권한 확인해줘"
   - 예상: 현재 구독의 허용된 액션 목록

2. **리소스 생성 계획**
   - 입력: "스토리지 계정 만들어줘"
   - 예상: 생성 가능성 확인 및 계획 수립

3. **리소스 생성 실행**
   - 입력: "응 만들어"
   - 예상: 실제 리소스 생성 및 결과 반환

### **시나리오 4: 오류 처리 테스트**

1. **권한 부족**
   - 입력: "VM 만들어줘" (권한 없는 리소스)
   - 예상: 권한 부족 메시지 및 ServiceNow 신청서 생성

2. **정책 위반**
   - 입력: "미국 동부에 스토리지 만들어줘" (허용되지 않은 리전)
   - 예상: 정책 위반 메시지 및 허용 리전 안내

### **시나리오 5: 승인(Confirm) UI 흐름**

1. **계획 제안 후 확인 질문**
   - 입력: "AKS 클러스터 만들어줘"
   - 예상: 파라미터 스펙/기본값 제안 + "진행할까요?" 버튼
2. **예/아니오 클릭**
   - 예 클릭 시: Apply 호출, 결과 요약 출력(+ 필요시 RG 드롭다운 갱신)
   - 아니오 클릭 시: "작업이 취소되었습니다" 안내

## 🔧 문제 해결

### **자주 발생하는 문제**

#### **1. "No LLM available" 안내**
**원인**: OpenAI API 키 미설정(정상 동작입니다 — 룰 기반으로 폴백)
**선택적 설정**:
```bash
echo "OPENAI_API_KEY=sk-your-api-key-here" >> mcpclient/.env
```
**참고**: LLM 설정 시 자연스러운 대화와 의도 파싱이 향상됩니다.

#### **2. "구독이 없습니다" 에러**
**원인**: Azure 로그인이 안됨
**해결**:
```bash
az login
az account list
az account set --subscription <YOUR_SUBSCRIPTION_ID>
```

#### **3. "MCP 서버 연결 실패" 에러**
**원인**: MCP 서버가 실행되지 않음
**해결**:
```bash
# MCP 서버 실행 확인
curl http://localhost:8080/healthz
# {"ok": true} 응답 확인
```

#### **4. "엔터키 중복 입력" 문제**
**원인**: JavaScript 이벤트 중복
**해결**: 브라우저 새로고침 (F5)

### **디버깅 방법**

#### **1. 터미널 로그 확인**
```bash
# MCP Client 터미널에서 확인
LLM analyzing: 사용자 입력
LLM Response: {"action": "...", "args": {...}}
```

#### **2. 네트워크 요청 확인**
- 브라우저 개발자 도구 → Network 탭
- `/chat` 요청 및 응답 확인

#### **3. 환경변수 확인**
```bash
# MCP Client 터미널에서
echo $OPENAI_API_KEY
echo $MCP_SERVER_BASE
```

## 📚 고급 사용법

### **복잡한 리소스 생성**

**AKS 클러스터 생성**
```
사용자: AKS 클러스터 만들어줘, 이름은 my-cluster, 노드 3개, Standard_D2s_v2
시스템: AKS 클러스터 생성 계획:
- 이름: my-cluster
- 노드 수: 3
- VM 크기: Standard_D2s_v2
- 위치: koreacentral
생성하시겠습니까?
```

**Key Vault 생성**
```
사용자: Key Vault 만들어줘, 이름은 my-vault
시스템: Key Vault 생성 계획:
- 이름: my-vault
- 위치: koreacentral
- SKU: standard
생성하시겠습니까?
```

### **일괄 작업**

**여러 리소스 생성**
```
사용자: 스토리지 계정과 Key Vault를 한번에 만들어줘
시스템: 여러 리소스 생성 계획:
1. 스토리지 계정 생성
2. Key Vault 생성
순차적으로 생성하시겠습니까?
```

## 🎯 팁과 요령

### **효율적인 사용법**

1. **구독 먼저 선택**: 작업 전에 구독을 먼저 선택하세요
2. **자연스러운 표현**: "dev icstr" 같은 자연스러운 표현 사용 가능
3. **단계별 확인**: 리소스 생성 전에 계획을 확인하세요
4. **오류 메시지 활용**: 권한 부족 시 ServiceNow 신청서 자동 생성
5. **세션 초기화 버튼**: 좌측 상단 "세션 초기화"로 대화/상태를 리셋
6. **스펙 확인**: `azs.spec(resourceType)`로 필수/옵션 파라미터 확인 가능
7. **LLM 활용**: OpenAI API 키 설정 시 더 자연스러운 대화 가능
8. **동적 툴 정보**: 서버에서 실시간으로 최신 MCP 툴 정보 제공

### **권장 워크플로우**

1. **환경 설정**: 구독 선택 → 리소스 그룹 선택
2. **권한 확인**: "내 권한 확인해줘"
3. **리소스 계획**: "스토리지 계정 만들어줘"
4. **생성 실행**: "응 만들어"
5. **결과 확인**: 생성된 리소스 ID 확인

## 📞 지원

### **문제 신고**
- GitHub Issues에 문제 신고
- 터미널 로그와 함께 상세한 재현 단계 제공

### **기능 요청**
- 새로운 리소스 타입 지원 요청
- UI/UX 개선 제안
- 새로운 자연어 표현 지원 요청
