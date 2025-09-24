import os
import httpx
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from agent.graph import AgentOrchestrator
from agent.config import MCP_SERVER_BASE

load_dotenv()

# 디버깅: 환경변수 확인
print(f"OPENAI_API_KEY loaded: {bool(os.getenv('OPENAI_API_KEY'))}")
print(f"MCP_SERVER_BASE: {os.getenv('MCP_SERVER_BASE', 'NOT_SET')}")

app = FastAPI(title="MCP Client — LangGraph Chat")
orchestrator = AgentOrchestrator()

class ChatIn(BaseModel):
    session_id: str
    message: str

@app.post("/chat")
async def chat(inb: ChatIn) -> Dict[str, Any]:
    try:
        return await orchestrator.handle_message(inb.session_id, inb.message)
    except Exception as e:
        raise HTTPException(500, f"chat error: {e}")

@app.get("/healthz")
def healthz():
    return {"ok": True}

# --- Helper APIs for dropdowns ---
@app.get("/api/subscriptions")
async def api_subscriptions():
    try:
        return await orchestrator.client.list_subscriptions()
    except Exception as e:
        raise HTTPException(500, f"subscriptions error: {e}")

@app.get("/api/resource-groups")
async def api_rgs(subscription_id: str):
    try:
        return await orchestrator.client.list_resource_groups(subscription_id)
    except Exception as e:
        raise HTTPException(500, f"resource-groups error: {e}")

class SetWorkingIn(BaseModel):
    session_id: str
    value: str

@app.post("/api/session/working-subscription")
async def set_working_subscription(body: SetWorkingIn):
    try:
        orchestrator.set_working_subscription(body.session_id, body.value)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"set working subscription error: {e}")

@app.post("/api/session/working-resource-group")
async def set_working_rg(body: SetWorkingIn):
    try:
        orchestrator.set_working_resource_group(body.session_id, body.value)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"set working RG error: {e}")

# --- Azure 인증 관련 API ---
@app.get("/api/auth/status")
async def check_auth_status():
    """Azure 로그인 상태 확인"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{MCP_SERVER_BASE}/auth/status")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"authenticated": False, "error": str(e)}

@app.post("/api/auth/login")
async def azure_login():
    """Azure 로그인 실행"""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{MCP_SERVER_BASE}/auth/login")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

# Static web UI
app.mount("/", StaticFiles(directory="web", html=True), name="web")
