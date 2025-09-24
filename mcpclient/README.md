# MCP Client (LangGraph + Chat UI)

LangGraph로 MCP 서버의 툴을 오케스트레이션하고, 대화형 Azure 작업을 위한 최소 웹 챗 UI를 제공하는 MCP 클라이언트입니다.

## 주요 기능
- **LangGraph 에이전트**: Plan → 확인(Confirm) → Apply 흐름 오케스트레이션(미니 그래프)
- **MCP 툴 호출**: `/mcp/tools`로 `azs.plan`, `azs.apply`, `azs.request` 사용
- **편의 API**: `GET /me/subscriptions`, `GET /me/resource-groups` 프록시 사용
- **웹 UI**: FastAPI 정적 서빙(HTML/JS/CSS) + 채팅 입력
- **드롭다운**: 구독/리소스그룹 선택 → 세션 상태 반영
- **세션 상태**: `workingSubscription`, `workingResourceGroup`

## 선행 조건
- Python 3.10+
- MCP 서버 실행 중(기본: `http://localhost:8080`)

## 실행 방법
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# (선택) .env 설정
echo "MCP_SERVER_BASE=http://localhost:8080" > .env
# echo "OPENAI_API_KEY=sk-..." >> .env

uvicorn app:app --reload --port 9090
```

웹 UI 접속: http://localhost:9090/

## 채팅 예시
- “내 구독 목록 보여줘” → 구독 목록 표시
- “이 구독으로 할게 <SUB_ID>” → workingSubscription 설정
- “리소스그룹 목록” → 해당 구독 RG 나열
- “rg-foo에 스토리지 만들어줘, 지역 koreacentral” → 사전 점검 + 확인 후 Apply

## 환경변수
- `MCP_SERVER_BASE` (기본 `http://localhost:8080`) - MCP 서버 주소
- `OPENAI_API_KEY` (선택) - 설정 시 간단 의도 파싱에 사용

## 참고
- LLM은 클라이언트에서만 사용(서버는 LLM 미사용).
- Azure 인증은 MCP 서버가 실행되는 환경에서 `az login` 필요.
- PoC 용도: 영속 저장소 없음(세션 메모리 보관).

## 문서 & 버전관리
- 클라이언트 전용 규칙: `.cursorrules` 참고(문서/보안/버전 원칙).
- 문서 디렉터리: `docs/`
  - `docs/CHANGELOG.md`
  - `docs/releases/_template.md`
  - `docs/prompts/` (예: `v0.1.0.md`)
- 로컬 전용 Git 모델: `main` 커밋 + 태그(`vX.Y.Z`).
