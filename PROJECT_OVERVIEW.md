# AZ Servant — Project Overview (v0.3.1)

AZ Servant is an Azure resource management agent built around MCP (Model Context Protocol). The server enforces rules and executes via SDK/CLI; the client orchestrates conversations (LLM optional) and calls MCP tools.

## What It Does
- Plan → Confirm → Apply flow for Azure operations
- Pre-check with RBAC + Policy before changes
- Create common resources (AKS, ACR, Key Vault, Event Hubs, Storage, Redis, Disks, VNet)
- Generate ServiceNow-style request payloads for blocked actions
- Expose standardized MCP tools over HTTP and stdio (FastMCP)

## Components
- MCP Server (FastAPI, port 8080)
  - APIs: `/me/*`, `/plan/*`, `/apply/resource-group`, `/mcp/tools`, `/auth/*`
  - MCP tools: `azs.plan`, `azs.apply`, `azs.request`, `azs.spec`, list/get helpers
  - RBAC evaluator: `mcpserver/authz.py`
  - Policy evaluator: `mcpserver/policy_eval.py`
  - Stdio MCP server: `mcpserver/mcp_std_server.py`
- MCP Client (FastAPI + Web UI, port 9090)
  - Agent: LangGraph mini-graph; optional OpenAI for NLU and slot-filling
  - Web UI: chat, subscription/RG dropdowns, confirm UI, session reset, Azure login helper
  - LLM Integration: GPT-4o-mini for natural language processing and intent parsing
  - Dynamic Tool Info: Real-time MCP tool catalog from server

## Architecture At A Glance
Client (LangGraph + Web UI) ⇄ Server (FastAPI + MCP tools) ⇄ Azure (ARM/RBAC/Policy)

## Quick Start
1) Server
```
cd mcpserver
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```
Health: http://localhost:8080/healthz → {"ok": true}

2) Client
```
cd mcpclient
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "MCP_SERVER_BASE=http://localhost:8080" > .env  # optional
uvicorn app:app --reload --port 9090
```
Open: http://localhost:9090/

3) Azure Login (local)
- Click "로그인" in the UI to see the terminal command
- Run the suggested `az login ...` and refresh the page to enable dropdowns

## MCP Tools (Catalog)
- 정의 소스: `mcpserver/specs/tools.yaml`
- `azs.plan(scope, mode=capabilities|constraints|check, resourceType?, location?, tags?)`
  - capabilities → allowedActions, notActions, effectiveRoles(+roleDetails)
  - constraints → allowedLocations, requiredTags, denies
  - check → status, reasons, hints
- `azs.apply(action=create|delete|update, resourceType, scope, params)`
  - PoC implementations for AKS/ACR/KeyVault/Event Hubs/Storage/Redis/Disks/VNet
- `azs.request(blockedItems[], scope, …)` → ServiceNow JSON/Markdown + agent v1
- `azs.spec(resourceType)` → parameter spec per resource type
- Helpers: `azs.list_subscriptions`, `azs.list_resource_groups(scope)`, `azs.list_resources(scope, type?)`, `azs.get_resource(id)`

HTTP endpoints
- GET `/mcp/tools` → dynamic catalog
- POST `/mcp/tools` → tool execution: `{name, arguments}` → `{content[], is_error}`

## REST API Summary
- GET `/me/subscriptions` | `/me/resource-groups?subscription_id=…` | `/me/scopes`
- GET `/plan/capabilities|constraints|check` (RBAC + Policy pre-check)
- POST `/apply/resource-group` | DELETE `/apply/resource-group?scope=…`
- GET `/auth/status` | POST `/auth/login` (manual login guidance)

## Common Flows
- Natural language create
  - "rg-xxx에 스토리지 만들어줘" → plan(capabilities/constraints) → confirm → apply → result
- Subscription/RG selection
  - Choose from dropdowns or natural language; session keeps `workingSubscription`, `workingResourceGroup`
- Deletion
  - "rg-foo 삭제" → confirm UI → apply delete
- Blocked path
  - If RBAC/Policy blocks an action, generate ServiceNow request via `azs.request`

## Configuration & Environment
- Server: DefaultAzureCredential (local `az login`), Azure CLI required
- Policy config: `mcpserver/config/policy.yaml`
- Resource spec: `mcpserver/specs/resource_types.yaml`
- Client: `.env` supports `MCP_SERVER_BASE`, optional `OPENAI_API_KEY`
- No persistent storage (PoC); session state in-memory on client

## Security Model
- Server uses no LLM; decisions executed via SDK/CLI
- Plan vs Apply separation recommended (least privilege)
- Production: reintroduce approval token + internal runner gate, MI on private runner

## Versioning
- Server: v0.3.1 — track updates in `mcpserver/docs/CHANGELOG.md`
- Client: v0.3.1 — track updates in `mcpclient/docs/CHANGELOG.md`
- Update docs + CHANGELOG alongside code changes; tag `vX.Y.Z`

## Roadmap (v0.4.x)
- Stronger delete coverage via MCP tools
- More Azure SDK (less CLI), richer Policy parsing, group/app role inheritance
- Dry-run, approval token, evidence bundle, Activity Log links
- Enhanced LLM integration and natural language processing
- Improved UI/UX with better conversation flow

## Troubleshooting Cheatsheet
- Subscriptions empty → run `az login`, set correct subscription
- DefaultAzureCredential error → ensure CLI auth; WSL/proxy considerations
- Slow first call → warm-up and CLI latency; caching planned
- UI shows not authenticated → click 로그인, follow terminal command

## Repository Map
```
mcpserver/
  app.py                # FastAPI server
  mcp_tools.py          # MCP tool handler (create/delete/spec/list/get)
  mcp_std_server.py     # FastMCP stdio server
  catalog.py            # YAML catalog loader (tools/resources/policy)
  authz.py              # RBAC evaluator (actions/notActions/effective roles)
  policy_eval.py        # Policy evaluator (allowed locations/tags/denies)
  scopes.py             # Scope parsing helpers
  config/policy.yaml    # Location enforcement & confirmation rules
  specs/tools.yaml      # MCP tool catalog exposed to clients
  specs/resource_types.yaml  # Resource params + aliases shared with agent
mcpclient/
  app.py                # Chat backend + helper APIs
  agent/                # LangGraph agent, MCP clients (HTTP/stdio)
  web/                  # Chat UI (HTML/JS/CSS)
ARCHITECTURE.md         # High-level architecture (kept up to date)
USER_GUIDE.md           # End-user guide & scenarios
```

## Known Limitations
- az CLI used for several operations; moving to SDK incrementally
- In-memory state; multi-user scaling requires backing store
- Policy coverage focuses on common patterns (Allowed locations, Require tag, deny)
- LLM integration is optional; fallback to rule-based processing when not configured

## Development Notes
- Keep docs in sync with features (SPEC_current, README, USER_GUIDE)
- Prefer minimal, focused changes; avoid unrelated refactors
- Test small before broad; add tests where framework exists
