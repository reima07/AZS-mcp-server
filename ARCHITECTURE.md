# AZ Servant - 프로젝트 아키텍처 문서

## 📋 프로젝트 개요

**AZ Servant**는 MCP (Model Context Protocol) 기반의 Azure 리소스 관리 시스템으로, 대화형 에이전트를 통해 Azure 리소스를 관리합니다. LLM은 클라이언트(에이전트) 층에서만 선택적으로 사용되며, 서버는 규칙/SDK/CLI 기반으로 결정·실행합니다.

## 🔖 버전 매트릭스(요약)
- MCP Server: v0.3.1 — MCP 툴 + Apply(서버 LLM 미사용)
- MCP Client: v0.3.1 — LangGraph + 웹 챗 UI(LLM 선택)

참조 문서
- MCP 툴 카탈로그: `mcpserver/specs/tools.yaml`
- 리소스 타입 스펙: `mcpserver/specs/resource_types.yaml`
- 정책 구성: `mcpserver/config/policy.yaml`
- 서버 릴리즈 노트: `mcpserver/docs/CHANGELOG.md`
- 클라이언트 릴리즈 노트: `mcpclient/docs/CHANGELOG.md`

## 🏗️ 전체 아키텍처

```
┌─────────────────┐    HTTP/REST    ┌─────────────────┐    Azure APIs    ┌─────────────────┐
│   MCP Client    │◄──────────────►│   MCP Server    │◄────────────────►│   Azure Cloud   │
│  (LangGraph)    │                 │  (FastAPI)      │                  │                 │
└─────────────────┘                 └─────────────────┘                  └─────────────────┘
         │                                   │
         │                                   │
         ▼                                   ▼
┌─────────────────┐                 ┌─────────────────┐
│   OpenAI API    │                 │   Azure CLI     │
│  (GPT-4o-mini)  │                 │   (az login)    │
└─────────────────┘                 └─────────────────┘
```

## 🔧 컴포넌트 상세

### **1. MCP Client (포트 9090)**

**역할**: 대화형 프론트엔드 및 에이전트 오케스트레이션(LLM은 선택)

**주요 구성요소**:
- **LangGraph 에이전트**: Plan → Confirm → Apply 워크플로우 관리
- **(선택) OpenAI GPT-4o-mini**: 자연어 의도 파싱(없으면 룰 기반 폴백)
- **웹 UI**: FastAPI 기반 실시간 채팅 인터페이스
- **세션 관리**: 사용자별 작업 상태 보관

**핵심 기능**:
- 🤖 **자연어 처리(선택)**: "dev icstr 구독에서 스토리지 만들어줘" → MCP 툴 호출
- 📋 **구독/리소스그룹 선택**: 드롭다운/명령으로 작업 대상 지정
- 💬 **대화형 흐름**: Plan → 확인(Confirm) → Apply
- 🔄 **동적 MCP 툴 정보**: 서버에서 실시간 툴 목록 및 설명 조회
- 🎯 **LLM 기반 의도 파싱**: OpenAI GPT-4o-mini 통합으로 자연스러운 대화 지원

### **2. MCP Server (포트 8080)**

**역할**: Azure 리소스 관리 백엔드 및 MCP 툴 제공

**주요 구성요소**:
- **FastAPI 서버**: RESTful API 엔드포인트 제공
- **MCP 툴 핸들러**: `azs.plan`, `azs.apply`, `azs.request`, `azs.spec` 및 목록 조회
- **표준 MCP 서버(선택)**: `mcpserver/mcp_std_server.py` — stdio 기반 FastMCP 툴 노출
- **Azure SDK**: RBAC, Policy, 리소스 관리
- **Azure CLI**: 리소스 생성/삭제 (PoC)

**핵심 기능**:
- 🔍 **권한 분석**: RBAC 기반 사용자 권한 확인(capabilities: allowedActions, notActions, effectiveRoles)
- 📋 **정책 검사**: Azure Policy 제약사항(허용 리전, 필수 태그, deny) 추출
- 🛠️ **리소스 관리**:
  - 생성: AKS, ACR, Key Vault, Event Hubs, Storage, Redis, Managed Disks, VNet 등(az CLI 기반 PoC)
  - 삭제: 리소스 그룹(HTTP) + 일부 리소스 MCP delete 매핑 진행 중
- 🧾 **스펙 제공**: `azs.spec(resourceType)`로 파라미터 스펙 제공
- 📄 **신청서 생성**: ServiceNow 형식 권한 요청서 자동 생성(JSON/Markdown + agent v1)
- 🔄 **동적 MCP 툴 카탈로그**: `GET /mcp/tools`로 실시간 툴 정보 제공
- 📊 **25개 리소스 타입 지원**: PostgreSQL, Redis, VNet 등 확장된 리소스 지원

## 🔄 데이터 흐름

### **1. 자연어 대화 흐름**

```
사용자 입력 → LLM 의도 파싱 → MCP 툴 선택 → 서버 API 호출 → Azure 작업 수행 → 결과 반환
```

### **2. 구독 선택 흐름**

```
"dev icstr 구독에서 작업할게" 
→ LLM이 구독 목록에서 매칭 
→ choose_subscription 액션 
→ workingSubscription 설정
```

### **3. 리소스 생성 흐름**

```
"스토리지 계정 만들어줘" 
→ azs.plan (권한/정책 확인) 
→ 사용자 확인 
→ azs.apply (실제 생성) 
→ 결과 반환

### **4. 인증/로그인 흐름(로컬)**

```
클라이언트 /api/auth/status → 서버 /auth/status (az account show)
로그인 필요 시 /api/auth/login → 서버 /auth/login(수동 명령 안내) → 사용자는 터미널에서 az login 수행
```
```

## 🛠️ 기술 스택

### **MCP Client**
- **Backend**: FastAPI, LangGraph, OpenAI API
- **Frontend**: Vanilla JavaScript, HTML/CSS
- **AI/Agent**: LangGraph, OpenAI GPT-4o-mini
- **Protocol**: MCP (Model Context Protocol, HTTP 브리지 + stdio 서버 옵션)

### **MCP Server**
- **Backend**: FastAPI, Azure SDK, Azure CLI
- **Authentication**: DefaultAzureCredential (az login)
- **Storage**: 메모리 기반 (PoC)
- **APIs**: RESTful, MCP 툴 인터페이스(+ MCP stdio)

## 📁 프로젝트 구조

```
/Users/jiwoo/Desktop/kltecho/project/
├── mcpclient/                    # MCP 클라이언트
│   ├── agent/                    # LangGraph 에이전트
│   │   ├── graph.py             # 에이전트 오케스트레이션
│   │   ├── mcp_client.py        # MCP 서버 통신
│   │   └── config.py            # 설정 관리
│   ├── web/                     # 웹 UI
│   │   ├── index.html           # 메인 페이지
│   │   ├── app.js               # 클라이언트 로직
│   │   └── styles.css           # 스타일링
│   ├── app.py                   # FastAPI 서버
│   ├── requirements.txt         # Python 의존성
│   └── README.md                # 클라이언트 문서
├── mcpserver/                   # MCP 서버
│   ├── app.py                   # FastAPI 서버
│   ├── mcp_tools.py             # MCP 툴 핸들러
│   ├── mcp_std_server.py        # 표준 MCP stdio 서버(FastMCP)
│   ├── catalog.py               # YAML 기반 카탈로그 로더
│   ├── authz.py                 # RBAC 권한 평가
│   ├── policy_eval.py           # Azure Policy 평가
│   ├── scopes.py                # Azure 스코프 파싱
│   ├── config/
│   │   └── policy.yaml          # 위치/확인 정책 설정
│   ├── specs/
│   │   ├── resource_types.yaml  # 리소스 파라미터/별칭 스펙
│   │   └── tools.yaml           # MCP 툴 메타데이터
│   ├── requirements.txt         # Python 의존성
│   └── README.md                # 서버 문서
└── ARCHITECTURE.md              # 이 문서
```

## 🔐 보안 및 인증

### **인증 방식**
- **Azure**: DefaultAzureCredential (az login 토큰 기반)
- **API**: JWT 토큰 파싱을 통한 실제 사용자 OID 추출
- **로컬 PoC**: 승인 토큰 없이 동작 (실환경에서는 승인/러너 게이팅 도입 권장)

### **권한 모델**
- **Plan용**: 읽기 전용 권한
- **Apply용**: 최소 쓰기 권한 (리소스별 커스텀 롤)
- **분리 권장**: Plan/Apply 권한 분리, 승인 토큰·내부 러너 게이트(실환경)

## 🚀 배포 모델

### **현재 (로컬 PoC)**
- **MCP Client**: localhost:9090
- **MCP Server**: localhost:8080
- **인증**: az login (로컬)
- **저장소**: 메모리 기반

### **권장 (실환경)**
- **내부 VNet 러너 + Managed Identity**
- **컨트롤러 ↔ 큐/버스 ↔ 내부 러너**
- **인바운드 없음, 러너는 폴링 방식**
- **최소 egress**: AAD, ARM, KV, ACR, Service Bus/Queue, ServiceNow

## 📊 성능 및 확장성

### **현재 제한사항**
- **세션 저장**: 메모리 기반 (서버 재시작 시 손실)
- **동시 사용자**: 단일 인스턴스
- **리소스 타입**: 25개 지원 (확장 가능)

### **확장 계획**
- **영속 저장소**: Redis/Database 도입
- **로드 밸런싱**: 다중 인스턴스 지원
- **캐싱**: TTL 기반 권한/정책 캐시
- **모니터링**: 로깅 및 메트릭 수집

## 🔄 버전 관리

### **현재 버전**
- **MCP Server**: v0.3.1 (동적 MCP 툴 카탈로그, Apply 확장)
- **MCP Client**: v0.3.1 (LangGraph + 웹 UI, LLM 기반 자연어 처리)

### **버전 관리 규칙**
- **단순 모드**: 바로 `main`에 커밋
- **버전**: 로컬 annotated tag (`vX.Y.Z`)
- **문서 갱신**: 코드 변경 시 CHANGELOG/문서도 함께 수정

## 🎯 향후 계획

### **단기 (v0.4.0)**
- **영속 저장소**: 세션 데이터 영속화
- **UI 개선**: 더 직관적인 리소스 생성 폼
- **테스트**: 단위 테스트 및 통합 테스트 추가

### **중기 (v0.5.0)**
- **승인 시스템**: 실환경용 승인/러너 게이팅
- **모니터링**: 로깅 및 메트릭 수집
- **보안 강화**: RBAC 기반 접근 제어

### **장기 (v1.0.0)**
- **멀티 테넌트**: 여러 Azure 테넌트 지원
- **워크플로우**: 복잡한 배포 파이프라인 지원
- **통합**: ServiceNow, Slack 등 외부 시스템 연동
