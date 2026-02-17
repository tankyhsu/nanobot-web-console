"""
nanobot API Server
Exposes HTTP, WebSocket API and Console UI for nanobot agent.
Listens on 0.0.0.0:18790
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from nanobot.config.loader import load_config, get_data_dir
from nanobot.bus.queue import MessageBus
from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import SessionManager
from nanobot.cron.service import CronService

# Add viking_service to path
sys.path.insert(0, str(Path(__file__).parent))
from viking_service import VikingService

logger = logging.getLogger("nanobot-api")


SESSIONS_DIR = Path.home() / ".nanobot" / "sessions"
CONSOLE_HTML = Path(__file__).parent / "console.html"

DEFAULT_IOT_CONSTRAINT = (
    "你的回复将通过TTS语音朗读给用户听，请遵守以下规则：\n"
    "1. 回复格式：只使用纯文字连贯句子，像正常说话一样。"
    "绝对禁止使用减号、星号、数字编号、项目符号、冒号列举、括号注释、Markdown等任何非自然语言的符号和排版格式。\n"
    "2. 回复长度：对于普通问答和闲聊，控制在两三句话以内。\n"
    "3. 重要：如果用户的指令涉及执行操作（比如安装软件、运行命令、创建文件、修改配置等），"
    "你必须正常调用工具去真正执行，不要因为回复格式限制而跳过执行或编造结果。"
    "执行完成后再用简短的自然语言告知结果即可。\n"
    "4. 诚实原则：不确定的事情就说不确定，不要编造信息。"
)

# ---- Emotion Detection ----

# Emotion keyword rules: (pattern, emotion) - first match wins
_EMOTION_RULES = [
    # Negative
    (r"抱歉|对不起|不好意思|很遗憾|无法|做不到|失败|出错|错误|error|fail", "sad"),
    (r"不知道|不确定|不太清楚|不了解", "confused"),
    (r"危险|警告|注意|小心|千万不要|禁止", "shocked"),
    (r"哈哈|哈哈哈|23333|笑死|太搞笑|逗", "laughing"),
    # Positive
    (r"完成|搞定|成功|装好|已安装|已配置|已创建|已修改|已删除|已更新|好了|弄好", "happy"),
    (r"太好了|太棒了|厉害|不错|很好|恭喜|棒|赞|nice|great|awesome", "happy"),
    (r"好的|收到|明白|了解|可以|没问题|当然", "winking"),
    (r"你好|嗨|hello|hi|hey|早上好|晚上好|下午好", "happy"),
    # Content-specific
    (r"天气.*晴|阳光|温暖", "happy"),
    (r"天气.*雨|下雨|暴雨", "sad"),
    (r"好吃|美食|推荐.*餐|食谱", "delicious"),
    (r"爱|喜欢|❤|最爱|太美", "loving"),
    (r"累|疲|困了|睡觉|休息", "sleepy"),
    (r"酷|帅|牛|666|nb|强", "cool"),
    (r"嗯|让我想想|这个问题", "thinking"),
    (r"惊|wow|哇|居然|没想到|竟然", "surprised"),
    (r"尴尬|emmm|额|呃", "embarrassed"),
    (r"生气|愤怒|气死|烦|讨厌", "angry"),
]


def _detect_emotion(text: str) -> str:
    """Detect emotion from response text using keyword rules."""
    lower = text.lower()
    for pattern, emotion in _EMOTION_RULES:
        if re.search(pattern, lower):
            return emotion
    return "neutral"


# ---- Emotion-aware event enrichment ----

_EVENT_EMOTIONS = {
    "thinking": "thinking",
    "tool_call": "gear",
    "tool_result": "cool",
}


def _enrich_event(event: dict) -> dict:
    """Add emotion field to streaming events."""
    etype = event.get("type", "")
    if etype in _EVENT_EMOTIONS:
        event["emotion"] = _EVENT_EMOTIONS[etype]
    elif etype == "done":
        event["emotion"] = _detect_emotion(event.get("content", ""))
    return event


def _make_provider(config):
    """Create LiteLLMProvider from config -- mirrors nanobot CLI logic."""
    from nanobot.providers.litellm_provider import LiteLLMProvider
    p = config.get_provider()
    model = config.agents.defaults.model
    if not (p and p.api_key) and not model.startswith("bedrock/"):
        raise RuntimeError("No API key configured in ~/.nanobot/config.json")
    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=config.get_provider_name(),
    )


agent: AgentLoop = None
bus: MessageBus = None
viking: VikingService = None
_config = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, bus, viking, _config
    config = load_config()
    _config = config
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)
    agent = AgentLoop(
        bus=bus, provider=provider, workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec, cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
    )
    # Initialize OpenViking memory (worker thread handles all operations)
    try:
        viking = VikingService()
        viking.start_worker()
        logger.info("OpenViking memory layer initialized (worker thread started)")
    except Exception as e:
        logger.warning(f"OpenViking init failed (memory layer disabled): {e}")
        viking = None
    await cron.start()
    yield
    cron.stop()
    if viking:
        viking.close()


app = FastAPI(title="nanobot API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---- Data Models ----

class Message(BaseModel):
    role: str = "user"
    content: str

class ChatRequest(BaseModel):
    model: str = "nanobot"
    messages: list[Message] = []
    stream: bool = False

class SimpleRequest(BaseModel):
    message: str
    session: str = "api:default"
    constraint: str | None = None

class SimpleResponse(BaseModel):
    response: str
    session: str
    timestamp: float
    emotion: str = "neutral"


async def _augment_with_memory(message: str) -> str:
    """Prepend relevant Viking context to the user message."""
    if not viking or not viking.ready:
        return message
    try:
        context = await viking.retrieve_context(message, 3)
        if context:
            return f"[以下是从知识库中检索到的相关上下文，仅供参考]\n{context}\n[上下文结束]\n\n{message}"
    except Exception as e:
        logger.error(f"Memory augmentation failed: {e}")
    return message


async def _store_memory(session: str, user_msg: str, assistant_msg: str):
    """Disabled: Viking store blocks the single worker thread for too long.
    Conversations are already stored in nanobot's JSONL sessions.
    Use /api/viking/add to manually add important content to the knowledge base."""
    pass


def _clean_for_tts(text: str) -> str:
    clean = text.strip()
    for ch in ["-", "*", "•", "·", "—"]:
        clean = clean.replace(f"\n{ch} ", "\n")
        clean = clean.replace(f"\n{ch}", "\n")
    clean = re.sub(r'\n\d+[\.\)、]\s*', '\n', clean)
    clean = re.sub(r'\n{2,}', '\n', clean).strip()
    return clean


# ---- Console UI ----

@app.get("/", response_class=HTMLResponse)
async def console_page():
    if CONSOLE_HTML.exists():
        return CONSOLE_HTML.read_text(encoding="utf-8")
    return "<h1>Console HTML not found</h1>"


# ---- Session API (for Console) ----

@app.get("/api/sessions")
async def list_sessions():
    results = []
    if not SESSIONS_DIR.exists():
        return results
    for f in sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        name = f.stem
        msg_count = 0
        updated = None
        try:
            with open(f, "r") as fh:
                for line in fh:
                    row = json.loads(line)
                    if row.get("_type") == "metadata":
                        updated = row.get("updated_at")
                    elif row.get("role") in ("user", "assistant"):
                        msg_count += 1
        except Exception:
            pass
        display = name.replace("feishu_", "Feishu: ").replace("api_", "API: ").replace("ws_", "WS: ").replace("cli_", "CLI: ")
        results.append({"name": name, "display": display, "messages": msg_count, "updated": updated, "size": f.stat().st_size})
    return results


@app.get("/api/sessions/{name}")
async def get_session(name: str):
    fpath = SESSIONS_DIR / f"{name}.jsonl"
    if not fpath.exists():
        raise HTTPException(404, "Session not found")
    messages = []
    with open(fpath, "r") as f:
        for line in f:
            try:
                row = json.loads(line)
                if row.get("_type") == "metadata":
                    continue
                messages.append(row)
            except Exception:
                pass
    return messages


@app.delete("/api/sessions/{name}")
async def delete_session(name: str):
    fpath = SESSIONS_DIR / f"{name}.jsonl"
    if not fpath.exists():
        raise HTTPException(404, "Session not found")
    fpath.unlink()
    return {"status": "ok", "deleted": name}


# ---- Chat Endpoints ----

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    if not agent:
        raise HTTPException(503, "Agent not ready")
    user_msg = ""
    for m in reversed(req.messages):
        if m.role == "user":
            user_msg = m.content
            break
    if not user_msg:
        raise HTTPException(400, "No user message found")
    session_key = f"api:{uuid.uuid4().hex[:8]}"
    augmented = await _augment_with_memory(user_msg)
    response = await agent.process_direct(
        content=augmented, session_key=session_key, channel="api", chat_id="api",
    )
    clean = _clean_for_tts(response)
    asyncio.create_task(_store_memory(session_key, user_msg, clean))
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion", "created": int(time.time()), "model": "nanobot",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": clean}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }


@app.post("/api/chat")
async def simple_chat(req: SimpleRequest):
    if not agent:
        raise HTTPException(503, "Agent not ready")
    # Memory augmentation
    augmented = await _augment_with_memory(req.message)
    content = f"{augmented}\n\n（回复要求：{req.constraint}）" if req.constraint else augmented
    response = await agent.process_direct(
        content=content, session_key=req.session, channel="api", chat_id=req.session,
    )
    clean = _clean_for_tts(response)
    emotion = _detect_emotion(clean)
    # Store memory (fire and forget)
    asyncio.create_task(_store_memory(req.session, req.message, clean))
    return SimpleResponse(response=clean, session=req.session, timestamp=time.time(), emotion=emotion)


# ---- WebSocket ----

@app.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue
            message = data.get("message", "").strip()
            if not message:
                await ws.send_json({"type": "error", "message": "Empty message"})
                continue
            if not agent:
                await ws.send_json({"type": "error", "message": "Agent not ready"})
                continue
            session = data.get("session", "ws:default")
            constraint = data.get("constraint", None)
            # Memory augmentation
            augmented = await _augment_with_memory(message)
            content = f"{augmented}\n\n（回复要求：{constraint}）" if constraint else augmented

            async def on_event(event: dict):
                try:
                    await ws.send_json(_enrich_event(event))
                except Exception:
                    pass

            try:
                response = await agent.process_direct(
                    content=content, session_key=session, channel="ws", chat_id=session,
                    event_callback=on_event,
                )
                clean = _clean_for_tts(response)
                emotion = _detect_emotion(clean)
                await ws.send_json({
                    "type": "final",
                    "content": clean,
                    "emotion": emotion,
                    "session": session,
                    "timestamp": time.time(),
                })
                # Store memory (fire and forget)
                asyncio.create_task(_store_memory(session, message, clean))
            except Exception as e:
                await ws.send_json({"type": "error", "message": str(e)})
    except WebSocketDisconnect:
        pass


# ---- Viking Knowledge Base API ----

@app.get("/api/viking/status")
async def viking_status():
    if not viking or not viking.ready:
        return {"status": "disabled", "message": "OpenViking not initialized"}
    return {"status": "ok", "ready": True}


class VikingSearchRequest(BaseModel):
    query: str
    limit: int = 5


@app.post("/api/viking/search")
async def viking_search(req: VikingSearchRequest):
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    result = await viking.search(req.query, req.limit)
    return {"result": result}


@app.post("/api/viking/find")
async def viking_find(req: VikingSearchRequest):
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    result = await viking.find(req.query, req.limit)
    return {"result": result}


class VikingAddRequest(BaseModel):
    path: str


@app.post("/api/viking/add")
async def viking_add(req: VikingAddRequest):
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    result = await viking.add_resource(req.path)
    return {"result": result}


@app.get("/api/viking/ls")
async def viking_ls(uri: str = "viking://resources/"):
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    result = await viking.ls(uri)
    return {"result": result}


@app.get("/api/viking/sessions")
async def viking_sessions():
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    result = await viking.list_sessions()
    return {"result": result}


# ---- Config / Tools / Skills API ----

CONFIG_PATH = Path.home() / ".nanobot" / "config.json"
WORKSPACE_DIR = Path.home() / ".nanobot" / "workspace"
SKILLS_DIR = WORKSPACE_DIR / "skills"


@app.get("/api/config")
async def get_config():
    """Get current agent configuration (model, tools, skills)."""
    config_data = {}
    if _config:
        config_data = {
            "model": _config.agents.defaults.model,
            "max_tokens": _config.agents.defaults.max_tokens,
            "temperature": _config.agents.defaults.temperature,
            "max_tool_iterations": _config.agents.defaults.max_tool_iterations,
            "provider": _config.get_provider_name(),
            "workspace": str(_config.workspace_path),
        }
    # Tools
    tools = []
    if agent and hasattr(agent, 'tools'):
        for t_name in agent.tools.tool_names:
            tool = agent.tools.get(t_name)
            if tool:
                tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                })
    config_data["tools"] = tools
    # Skills
    skills = []
    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                skill_info = {"name": skill_dir.name, "description": ""}
                if skill_md.exists():
                    text = skill_md.read_text()
                    # Extract description from frontmatter
                    fm_match = re.search(r"description:\s*(.+)", text)
                    if fm_match:
                        skill_info["description"] = fm_match.group(1).strip()
                    skill_info["content"] = text
                skills.append(skill_info)
    config_data["skills"] = skills
    # System prompt files
    prompt_files = {}
    for fname in ["SOUL.md", "AGENTS.md", "USER.md"]:
        fpath = WORKSPACE_DIR / fname
        if fpath.exists():
            prompt_files[fname] = fpath.read_text()
    config_data["prompt_files"] = prompt_files
    # Memory
    memory_file = WORKSPACE_DIR / "memory" / "MEMORY.md"
    if memory_file.exists():
        config_data["memory"] = memory_file.read_text()
    return config_data


class ConfigUpdateRequest(BaseModel):
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_tool_iterations: int | None = None


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    """Update agent model configuration. Requires service restart to take effect."""
    if not CONFIG_PATH.exists():
        raise HTTPException(404, "Config file not found")
    try:
        raw = json.loads(CONFIG_PATH.read_text())
        defaults = raw.setdefault("agents", {}).setdefault("defaults", {})
        changed = []
        if req.model is not None:
            defaults["model"] = req.model
            changed.append(f"model={req.model}")
        if req.temperature is not None:
            defaults["temperature"] = req.temperature
            changed.append(f"temperature={req.temperature}")
        if req.max_tokens is not None:
            defaults["maxTokens"] = req.max_tokens
            changed.append(f"maxTokens={req.max_tokens}")
        if req.max_tool_iterations is not None:
            defaults["maxToolIterations"] = req.max_tool_iterations
            changed.append(f"maxToolIterations={req.max_tool_iterations}")
        if not changed:
            return {"status": "no changes"}
        CONFIG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
        return {"status": "updated", "changed": changed, "note": "Restart service to apply changes"}
    except Exception as e:
        raise HTTPException(500, f"Failed to update config: {e}")


class PromptFileUpdateRequest(BaseModel):
    filename: str
    content: str


@app.post("/api/config/prompt")
async def update_prompt_file(req: PromptFileUpdateRequest):
    """Update a system prompt file (SOUL.md, AGENTS.md, USER.md)."""
    allowed = {"SOUL.md", "AGENTS.md", "USER.md"}
    if req.filename not in allowed:
        raise HTTPException(400, f"Only {allowed} can be updated")
    fpath = WORKSPACE_DIR / req.filename
    try:
        fpath.write_text(req.content)
        return {"status": "updated", "file": req.filename, "size": len(req.content)}
    except Exception as e:
        raise HTTPException(500, f"Failed to write {req.filename}: {e}")


@app.get("/health")
async def health():
    return {"status": "ok", "agent_ready": agent is not None, "viking_ready": viking is not None and viking.ready}


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": "nanobot", "object": "model", "owned_by": "local"}]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18790)
