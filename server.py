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

# ClawWork economic tracking (optional)
try:
    from clawmode_integration import ClawWorkAgentLoop, ClawWorkState
    from clawmode_integration.config import load_clawwork_config
    from livebench.agent.economic_tracker import EconomicTracker
    CLAWWORK_AVAILABLE = True
except ImportError:
    CLAWWORK_AVAILABLE = False

# Add viking_service to path (optional)
sys.path.insert(0, str(Path(__file__).parent))
try:
    from viking_service import VikingService
    VIKING_AVAILABLE = True
except ImportError:
    VIKING_AVAILABLE = False

logger = logging.getLogger("nanobot-api")


# ---- Streaming AgentLoop (structured WS events via _run_agent_loop override) ----

def _make_streaming_class(base_cls):
    """Create a StreamingAgentLoop that emits structured events for WebSocket."""

    class StreamingAgentLoop(base_cls):
        """Overrides _run_agent_loop to emit thinking/tool_call/tool_result events."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._event_callback = None

        def set_event_callback(self, cb):
            self._event_callback = cb

        def clear_event_callback(self):
            self._event_callback = None

        async def _emit(self, event):
            if self._event_callback:
                try:
                    await self._event_callback(event)
                except Exception:
                    pass

        async def _run_agent_loop(self, initial_messages, on_progress=None):
            """Override to emit structured events when _event_callback is set."""
            if not self._event_callback:
                return await super()._run_agent_loop(initial_messages, on_progress=on_progress)

            messages = initial_messages
            iteration = 0
            final_content = None
            tools_used = []

            while iteration < self.max_iterations:
                iteration += 1
                await self._emit({"type": "thinking", "iteration": iteration})

                response = await self.provider.chat(
                    messages=messages,
                    tools=self.tools.get_definitions(),
                    model=self.model,
                    temperature=getattr(self, 'temperature', 0.7),
                    max_tokens=getattr(self, 'max_tokens', 4096),
                )

                if response.has_tool_calls:
                    tool_call_dicts = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                        for tc in response.tool_calls
                    ]
                    messages = self.context.add_assistant_message(
                        messages, response.content, tool_call_dicts,
                        reasoning_content=response.reasoning_content,
                    )
                    for tool_call in response.tool_calls:
                        tools_used.append(tool_call.name)
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        await self._emit({"type": "tool_call", "name": tool_call.name, "arguments": args_str})
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        await self._emit({"type": "tool_result", "name": tool_call.name, "result": result})
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name, result,
                        )
                else:
                    final_content = self._strip_think(response.content) if hasattr(self, '_strip_think') else response.content
                    break

            return final_content, tools_used, messages

    StreamingAgentLoop.__name__ = f"Streaming{base_cls.__name__}"
    return StreamingAgentLoop


SESSIONS_DIR = None  # Set in lifespan from config workspace
LEGACY_SESSIONS_DIR = Path.home() / ".nanobot" / "sessions"
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
viking = None
_config = None
_feishu_client = None
_chat_counter = 0  # Track conversations for periodic memory consolidation


# ---- Feishu Outbound Dispatcher ----

def _init_feishu_client(config):
    """Initialize Feishu lark-oapi client from nanobot config."""
    global _feishu_client
    try:
        import lark_oapi as lark
        cfg_path = Path.home() / ".nanobot" / "config.json"
        raw = json.loads(cfg_path.read_text())
        fc = raw.get("channels", {}).get("feishu", {})
        app_id = fc.get("appId", "")
        app_secret = fc.get("appSecret", "")
        if not (app_id and app_secret):
            print("[nanobot-api] Feishu credentials not found, outbound messaging disabled")
            return
        _feishu_client = lark.Client.builder() \
            .app_id(app_id).app_secret(app_secret) \
            .log_level(lark.LogLevel.WARNING).build()
        print(f"[nanobot-api] Feishu client initialized (app_id={app_id[:8]}...)")
    except ImportError:
        print("[nanobot-api] lark-oapi not installed, outbound messaging disabled")
    except Exception as e:
        print(f"[nanobot-api] Feishu client init failed: {e}")


def _send_feishu_message(receive_id: str, content: str) -> bool:
    """Send a message via Feishu API. Returns True on success."""
    if not _feishu_client:
        return False
    try:
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest, CreateMessageRequestBody,
        )
        receive_id_type = "chat_id" if receive_id.startswith("oc_") else "open_id"
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": content}],
        }
        request = CreateMessageRequest.builder() \
            .receive_id_type(receive_id_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            ).build()
        response = _feishu_client.im.v1.message.create(request)
        if response.success():
            logger.info(f"Feishu message sent to {receive_id}")
            return True
        else:
            logger.error(f"Feishu send failed: code={response.code}, msg={response.msg}")
            return False
    except Exception as e:
        logger.error(f"Feishu send error: {e}")
        return False


async def _feishu_outbound_handler(msg):
    """Handle outbound messages destined for Feishu."""
    print(f"[nanobot-api] Dispatching Feishu message to {msg.chat_id}")
    result = _send_feishu_message(msg.chat_id, msg.content)
    print(f"[nanobot-api] Feishu send result: {result}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, bus, viking, _config, SESSIONS_DIR
    config = load_config()
    _config = config
    SESSIONS_DIR = config.workspace_path / "sessions"
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)
    agent_kwargs = dict(
        bus=bus, provider=provider, workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec, cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=getattr(config.tools, 'mcp_servers', None),
    )
    # Try ClawWork economic tracking
    if CLAWWORK_AVAILABLE:
        cw_cfg = load_clawwork_config()
        if cw_cfg.enabled:
            data_path = str(Path(cw_cfg.data_path).expanduser())
            tracker = EconomicTracker(
                signature=cw_cfg.signature,
                initial_balance=cw_cfg.initial_balance,
                input_token_price=cw_cfg.token_pricing.input_price,
                output_token_price=cw_cfg.token_pricing.output_price,
                data_path=data_path,
            )
            # Try importing optional heavy deps (evaluator needs OpenAI, task_manager needs pandas)
            evaluator = None
            task_mgr = None
            try:
                from livebench.work.evaluator import WorkEvaluator
                p = config.get_provider()
                if p and p.api_key:
                    os.environ.setdefault("OPENAI_API_KEY", p.api_key)
                api_base = config.get_api_base()
                if api_base:
                    os.environ.setdefault("OPENAI_API_BASE", api_base)
                os.environ.setdefault("EVALUATION_MODEL", config.agents.defaults.model.split("/")[-1])
                evaluator = WorkEvaluator(
                    data_path=data_path,
                    meta_prompts_dir=str(Path("/opt/ClawWork/eval/meta_prompts")),
                )
            except Exception as e:
                print(f"[nanobot-api] ClawWork evaluator not available: {e}")
            try:
                from livebench.work.task_manager import TaskManager
                task_mgr = TaskManager(
                    task_source_type="inline", inline_tasks=[],
                    task_data_path=data_path,
                )
            except Exception as e:
                print(f"[nanobot-api] ClawWork task manager not available: {e}")
            cw_state = ClawWorkState(
                economic_tracker=tracker,
                task_manager=task_mgr,
                evaluator=evaluator,
                signature=cw_cfg.signature,
                data_path=data_path,
            )
            StreamingCW = _make_streaming_class(ClawWorkAgentLoop)
            agent = StreamingCW(clawwork_state=cw_state, **agent_kwargs)
            print(f"[nanobot-api] ClawWork enabled (balance=${cw_cfg.initial_balance:.0f}, sig={cw_cfg.signature})")
        else:
            StreamingBase = _make_streaming_class(AgentLoop)
            agent = StreamingBase(**agent_kwargs)
    else:
        StreamingBase = _make_streaming_class(AgentLoop)
        agent = StreamingBase(**agent_kwargs)
    # Initialize Feishu client and start outbound message dispatcher
    _init_feishu_client(config)

    async def _outbound_dispatcher():
        while True:
            try:
                msg = await bus.consume_outbound()
                if msg.channel == "feishu" and _feishu_client:
                    await _feishu_outbound_handler(msg)
            except Exception as e:
                logger.error(f"Outbound dispatch error: {e}")

    _dispatcher_task = asyncio.create_task(_outbound_dispatcher())
    # Initialize OpenViking memory (worker thread handles all operations)
    if VIKING_AVAILABLE:
        try:
            viking = VikingService()
            viking.start_worker()
            logger.info("OpenViking memory layer initialized (worker thread started)")
        except Exception as e:
            logger.warning(f"OpenViking init failed (memory layer disabled): {e}")
            viking = None
    await cron.start()
    yield
    _dispatcher_task.cancel()
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
    """Append conversation to HISTORY.md and periodically consolidate MEMORY.md."""
    global _chat_counter
    try:
        workspace = _config.workspace_path if _config else Path.home() / ".nanobot" / "workspace"
        history_file = workspace / "memory" / "HISTORY.md"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        # Truncate long messages for history
        q = user_msg[:200].replace("\n", " ")
        a = assistant_msg[:300].replace("\n", " ")
        entry = f"[{ts}] ({session}) Q: {q} | A: {a}\n\n"
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.error(f"History append failed: {e}")

    # Every 10 conversations, consolidate HISTORY.md into MEMORY.md via LLM
    _chat_counter += 1
    if _chat_counter % 10 == 0:
        asyncio.create_task(_consolidate_long_term_memory())


async def _consolidate_long_term_memory():
    """Use LLM to distill recent HISTORY.md entries into MEMORY.md."""
    try:
        workspace = _config.workspace_path if _config else Path.home() / ".nanobot" / "workspace"
        memory_dir = workspace / "memory"
        history_file = memory_dir / "HISTORY.md"
        memory_file = memory_dir / "MEMORY.md"
        if not history_file.exists():
            return
        history = history_file.read_text(encoding="utf-8")
        # Only consolidate last 50 entries to keep prompt small
        entries = history.strip().split("\n\n")
        recent = "\n\n".join(entries[-50:])
        current_memory = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""

        prompt = (
            "你是记忆整理助手。根据最近的对话记录更新长期记忆。\n\n"
            "规则：\n"
            "- 保留所有已有的重要事实（用户偏好、系统配置、项目信息等）\n"
            "- 从新对话中提取值得长期记忆的信息（新装的软件、新发现的问题、用户提到的偏好等）\n"
            "- 删除过时信息（如版本号已更新）\n"
            "- 保持简洁，用 Markdown 格式\n"
            "- 直接输出更新后的完整 MEMORY.md 内容，不要其他解释\n\n"
            f"## 当前 MEMORY.md\n{current_memory or '(空)'}\n\n"
            f"## 最近对话记录\n{recent}"
        )
        from nanobot.providers.litellm_provider import LiteLLMProvider
        response = await agent.provider.chat(
            messages=[{"role": "user", "content": prompt}],
            model=agent.model,
        )
        if response.content and response.content.strip():
            new_memory = response.content.strip()
            # Strip markdown fences if present
            if new_memory.startswith("```"):
                new_memory = new_memory.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            memory_file.write_text(new_memory, encoding="utf-8")
            logger.info(f"Long-term memory consolidated ({len(new_memory)} chars)")
    except Exception as e:
        logger.error(f"Memory consolidation failed: {e}")


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
    seen = set()
    # Scan both current and legacy session directories
    dirs = [d for d in [SESSIONS_DIR, LEGACY_SESSIONS_DIR] if d and d.exists()]
    all_files = []
    for d in dirs:
        for f in d.glob("*.jsonl"):
            if f.stem not in seen:
                seen.add(f.stem)
                all_files.append(f)
    for f in sorted(all_files, key=lambda p: p.stat().st_mtime, reverse=True):
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


def _find_session_file(name: str) -> Path | None:
    """Find a session file in current or legacy directory."""
    for d in [SESSIONS_DIR, LEGACY_SESSIONS_DIR]:
        if d:
            p = d / f"{name}.jsonl"
            if p.exists():
                return p
    return None


@app.get("/api/sessions/{name}")
async def get_session(name: str):
    fpath = _find_session_file(name)
    if not fpath:
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
    fpath = _find_session_file(name)
    if not fpath:
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

            async def heartbeat():
                """Send periodic heartbeat while agent is processing."""
                try:
                    while True:
                        await asyncio.sleep(15)
                        await ws.send_json({"type": "heartbeat", "timestamp": time.time()})
                except (Exception, asyncio.CancelledError):
                    pass

            hb_task = asyncio.create_task(heartbeat())
            try:
                agent.set_event_callback(on_event)
                response = await agent.process_direct(
                    content=content, session_key=session, channel="ws", chat_id=session,
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
            finally:
                hb_task.cancel()
                agent.clear_event_callback()
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
