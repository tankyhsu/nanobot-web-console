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

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from nanobot.config.loader import load_config
from nanobot.config.paths import get_data_dir
from nanobot.bus.queue import MessageBus
from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import SessionManager

# OpenViking memory layer (optional) — install with: pip install openviking
import queue as _queue
import subprocess as _subprocess
import threading as _threading


def _openviking_importable():
    try:
        import openviking  # noqa: F401
        return True
    except ImportError:
        return False


def _openviking_cli_available():
    try:
        r = _subprocess.run(["openviking", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


VIKING_AVAILABLE = _openviking_importable() or _openviking_cli_available()


class VikingService:
    """Inline OpenViking service — auto-detects SDK vs CLI mode.

    SDK mode (pip install openviking + SyncOpenViking available):
        Uses SyncOpenViking directly in a worker thread.
    CLI mode (openviking binary on PATH, e.g. running as a separate service):
        Uses subprocess to call `openviking` CLI commands.
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or os.path.expanduser("~/.openviking/data")
        self._ov = None          # SyncOpenViking instance (SDK mode)
        self._cli_mode = False   # True = use CLI subprocess
        self._ready = False
        self._q: _queue.Queue = _queue.Queue()
        self._worker_thread = None

    # ------------------------------------------------------------------ #
    #  Worker thread                                                       #
    # ------------------------------------------------------------------ #

    def start_worker(self):
        self._worker_thread = _threading.Thread(
            target=self._worker_loop, daemon=True, name="viking-worker"
        )
        self._worker_thread.start()

    def _worker_loop(self):
        # Try SDK first; fall back to CLI
        try:
            from openviking import SyncOpenViking
            ov_config = os.environ.get(
                "OPENVIKING_CONFIG_FILE",
                os.path.expanduser("~/.openviking/ov.conf"),
            )
            os.environ.setdefault("OPENVIKING_CONFIG_FILE", ov_config)
            self._ov = SyncOpenViking(data_dir=self.data_dir)
            self._ov.initialize()
            self._ready = True
            logger.info("OpenViking: SDK mode initialized")
        except Exception as sdk_err:
            logger.warning(f"OpenViking SDK init failed ({sdk_err}), trying CLI mode")
            try:
                r = _subprocess.run(
                    ["openviking", "ls", "viking://resources"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    self._cli_mode = True
                    self._ready = True
                    logger.info("OpenViking: CLI mode ready")
                else:
                    logger.warning("OpenViking CLI not responding — memory layer disabled")
            except Exception as cli_err:
                logger.warning(f"OpenViking CLI unavailable ({cli_err}) — memory layer disabled")

        while True:
            try:
                req = self._q.get(timeout=60)
                if req is None:
                    break
                try:
                    req["result"] = req["fn"](*req["args"])
                except Exception as e:
                    req["error"] = e
                    logger.error(f"Viking worker error: {e}")
                finally:
                    req["event"].set()
            except _queue.Empty:
                continue

    @property
    def ready(self) -> bool:
        return self._ready

    async def _run(self, fn, *args, timeout=15.0):
        req = {"fn": fn, "args": args, "event": _threading.Event(), "result": None, "error": None}
        self._q.put(req)
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, req["event"].wait), timeout=timeout
            )
            if req["error"]:
                raise req["error"]
            return req["result"]
        except (asyncio.TimeoutError, Exception):
            return None

    # ------------------------------------------------------------------ #
    #  SDK-mode sync helpers                                              #
    # ------------------------------------------------------------------ #

    def _sdk_search(self, query, limit=5):
        r = self._ov.search(query, limit=limit)
        out = []
        for m in (r.memories or []):
            out.append(f"[记忆] {getattr(m,'content',str(m))[:300]}")
        for res in (r.resources or []):
            content = getattr(res, 'content', '') or getattr(res, 'abstract', '')
            out.append(f"[资源:{getattr(res,'uri','')}] {content[:300]}")
        return "\n\n".join(out) or f"搜索'{query}'无结果"

    def _sdk_find(self, query, limit=10):
        r = self._ov.find(query, limit=limit)
        out = []
        for m in (getattr(r, 'memories', None) or []):
            out.append(f"[记忆] {getattr(m,'content',str(m))[:300]}")
        for res in (getattr(r, 'resources', None) or []):
            content = getattr(res, 'content', '') or getattr(res, 'abstract', '')
            out.append(f"[资源:{getattr(res,'uri','')}] {content[:300]}")
        return "\n\n".join(out) or f"深度搜索'{query}'无结果"

    def _sdk_ls(self, uri="viking://resources/"):
        items = self._ov.ls(uri)
        if not items:
            return f"目录{uri}为空"
        lines = [f"  [{'D' if i.get('isDir') else 'F'}] {i.get('name','')} ({i.get('size',0)}b)" for i in items]
        return f"目录{uri}:\n" + "\n".join(lines)

    def _sdk_add_resource(self, path):
        if not os.path.exists(path):
            return f"文件不存在: {path}"
        r = self._ov.add_resource(path, wait=True, timeout=120)
        errors = r.get("errors", [])
        return f"添加失败: {', '.join(errors)}" if errors else f"已添加: {r.get('root_uri','')}"

    def _sdk_read_resource(self, uri):
        try:
            content = self._ov.read(uri)
            return content if isinstance(content, str) else str(content)
        except Exception as e:
            return f"读取失败: {e}"

    def _sdk_delete_resource(self, uri):
        try:
            self._ov.rm(uri)
            return f"已删除: {uri}"
        except Exception as e:
            return f"删除失败: {e}"

    def _sdk_list_sessions(self):
        sessions = self._ov.list_sessions()
        if not sessions:
            return "暂无会话记录"
        lines = [f"  - {s.get('session_id','') if isinstance(s, dict) else s}" for s in sessions[:20]]
        return "会话列表:\n" + "\n".join(lines)

    def _sdk_retrieve_context(self, query, limit=3):
        r = self._ov.search(query, limit=limit)
        parts = []
        for m in (r.memories or [])[:3]:
            c = getattr(m, 'content', str(m))
            if c:
                parts.append(f"[记忆] {c}")
        for res in (r.resources or [])[:3]:
            content = getattr(res, 'content', '') or getattr(res, 'abstract', '')
            title = getattr(res, 'title', getattr(res, 'uri', ''))
            if content:
                parts.append(f"[知识库:{title}] {content[:500]}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ #
    #  CLI-mode sync helpers                                              #
    # ------------------------------------------------------------------ #

    def _cli_run(self, *args, timeout=30):
        try:
            r = _subprocess.run(
                ["openviking"] + list(args),
                capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout if r.returncode == 0 else None
        except Exception:
            return None

    @staticmethod
    def _parse_ov_fixed_width(output):
        """Parse openviking fixed-width column output into list of dicts.

        Handles:
        - Dynamic column widths (openviking adjusts to terminal/content width)
        - CJK characters (display width 2 per char) in URI columns
        - Multi-line rows where abstract continues on indented continuation lines
        """
        import unicodedata as _ud

        def _disp_width(s):
            w = 0
            for c in s:
                ea = _ud.east_asian_width(c)
                w += 2 if ea in ("W", "F") else 1
            return w

        def _char_idx(line, target_disp):
            """Return char index corresponding to display position target_disp."""
            pos = 0
            for i, c in enumerate(line):
                if pos >= target_disp:
                    return i
                ea = _ud.east_asian_width(c)
                pos += 2 if ea in ("W", "F") else 1
            return len(line)

        def _extract(line, start_disp, end_disp):
            s = _char_idx(line, start_disp)
            if end_disp is None:
                return line[s:].strip()
            e = _char_idx(line, end_disp)
            return line[s:e].strip()

        lines = output.splitlines()
        if not lines:
            return []

        # Detect column start positions from header (display widths)
        header = lines[0]
        col_positions = []
        i, disp = 0, 0
        while i < len(header):
            c = header[i]
            if not c.isspace():
                col_start = disp
                col_name = ""
                while i < len(header) and not header[i].isspace():
                    col_name += header[i]
                    ea = _ud.east_asian_width(header[i])
                    disp += 2 if ea in ("W", "F") else 1
                    i += 1
                col_positions.append((col_start, col_name))
            else:
                ea = _ud.east_asian_width(c)
                disp += 2 if ea in ("W", "F") else 1
                i += 1

        if not col_positions:
            return []

        cols = [
            (name, col_positions[j][0], col_positions[j + 1][0] if j + 1 < len(col_positions) else None)
            for j, (_, name) in enumerate(col_positions)
        ]

        abstract_start = next((s for n, s, e in cols if n == "abstract"), None)
        abstract_end = next((e for n, s, e in cols if n == "abstract"), None)
        uri_start = cols[0][1]
        uri_end = cols[0][2]

        records = []
        current = None
        for line in lines[1:]:
            if not line.strip():
                continue
            uri_val = _extract(line, uri_start, uri_end)
            if uri_val:
                if current:
                    records.append(current)
                current = {name: _extract(line, s, e) for name, s, e in cols}
            else:
                # Continuation line — append to abstract
                if current and abstract_start is not None:
                    cont = _extract(line, abstract_start, abstract_end)
                    if cont:
                        current["abstract"] = current.get("abstract", "") + " " + cont
        if current:
            records.append(current)
        return records

    def _cli_search(self, query, limit=5):
        """Run openviking find and return JSON-compatible dict."""
        import re as _re
        out = self._cli_run("find", query, "-n", str(limit))
        if not out or not out.strip():
            return {"ok": True, "result": {"resources": [], "memories": [], "skills": [], "total": 0}}

        # Pre-extract scores per data line using regex (avoids fixed-width column misalignment)
        # Each result row starts with a URI; score appears as a standalone decimal like 0.58511
        line_scores = {}
        for line in out.splitlines()[1:]:  # skip header
            if not line.strip() or line.startswith(" " * 20):
                continue
            m = _re.search(r"\b(0\.\d+)", line)
            if m:
                # Map by the URI prefix of the line
                uri_part = line.split()[0] if line.split() else ""
                line_scores[uri_part] = float(m.group(1))

        rows = self._parse_ov_fixed_width(out)
        resources = []
        for row in rows:
            uri = row.get("uri", "")
            if not uri:
                continue
            abstract = row.get("abstract", "")
            context_type = row.get("context_type", "resource")
            score = line_scores.get(uri, 0.0)
            resources.append({"uri": uri, "content": abstract[:300], "score": score, "context_type": context_type})
        return {"ok": True, "result": {"resources": resources, "memories": [], "skills": [], "total": len(resources)}}

    def _cli_find(self, query, limit=10):
        """Same as _cli_search but larger limit (used by deep search)."""
        return self._cli_search(query, limit=limit)

    def _cli_ls(self, uri="viking://resources"):
        """Run openviking ls and return JSON-compatible dict."""
        out = self._cli_run("ls", uri)
        if not out or not out.strip():
            return {"ok": True, "result": []}
        rows = self._parse_ov_fixed_width(out)
        items = []
        for row in rows:
            item_uri = row.get("uri", "")
            if not item_uri:
                continue
            try:
                size = int(row.get("size", 0) or 0)
            except ValueError:
                size = 0
            is_dir = row.get("isDir", "").lower() == "true"
            items.append({
                "uri": item_uri,
                "isDir": is_dir,
                "size": size,
                "abstract": row.get("abstract", "")[:200],
            })
        return {"ok": True, "result": items}

    def _cli_add_resource(self, path):
        out = self._cli_run("add-resource", path, timeout=120)
        return out or f"添加失败: {path}"

    def _cli_read_resource(self, uri):
        try:
            r = _subprocess.run(
                ["openviking", "read", uri],
                capture_output=True, timeout=30,
            )
            content = r.stdout.decode("utf-8", errors="replace").strip()
            return content if content else f"（内容为空）"
        except Exception as e:
            return f"读取失败: {e}"

    def _cli_delete_resource(self, uri):
        try:
            _subprocess.run(
                ["openviking", "rm", "-r", uri],
                capture_output=True, check=True, timeout=30,
            )
            return f"已删除: {uri}"
        except _subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else ""
            return f"删除失败: {stderr or uri}"
        except Exception as e:
            return f"删除失败: {e}"

    def _cli_list_sessions(self):
        out = self._cli_run("list-sessions")
        return out or "暂无会话记录"

    def _cli_retrieve_context(self, query, limit=3):
        result = self._cli_search(query, limit=limit)
        if isinstance(result, dict) and result.get("ok"):
            resources = result.get("result", {}).get("resources", [])
            parts = [f"[知识库:{r['uri']}] {r['content']}" for r in resources[:3] if r.get("content")]
            return "\n\n".join(parts)
        return ""

    # ------------------------------------------------------------------ #
    #  Dispatch (SDK vs CLI)                                              #
    # ------------------------------------------------------------------ #

    def _dispatch(self, sdk_fn, cli_fn, *args):
        return sdk_fn(*args) if not self._cli_mode else cli_fn(*args)

    # ------------------------------------------------------------------ #
    #  Public async API                                                   #
    # ------------------------------------------------------------------ #

    async def search(self, query, limit=5):
        return await self._run(self._dispatch, self._sdk_search, self._cli_search, query, limit) or f"搜索'{query}'超时"

    async def find(self, query, limit=10):
        return await self._run(self._dispatch, self._sdk_find, self._cli_find, query, limit, timeout=30) or f"深度搜索'{query}'超时"

    async def ls(self, uri="viking://resources/"):
        return await self._run(self._dispatch, self._sdk_ls, self._cli_ls, uri) or f"列目录{uri}超时"

    async def add_resource(self, path):
        return await self._run(self._dispatch, self._sdk_add_resource, self._cli_add_resource, path, timeout=120) or "添加超时"

    async def read_resource(self, uri):
        return await self._run(self._dispatch, self._sdk_read_resource, self._cli_read_resource, uri, timeout=30) or "（读取超时）"

    async def delete_resource(self, uri):
        return await self._run(self._dispatch, self._sdk_delete_resource, self._cli_delete_resource, uri, timeout=30) or "删除超时"

    async def list_sessions(self):
        return await self._run(self._dispatch, self._sdk_list_sessions, self._cli_list_sessions) or "获取会话列表超时"

    async def retrieve_context(self, query, limit=3):
        return await self._run(self._dispatch, self._sdk_retrieve_context, self._cli_retrieve_context, query, limit, timeout=10) or ""

    def close(self):
        self._q.put(None)
        if self._ov:
            try:
                self._ov.close()
            except Exception:
                pass

logger = logging.getLogger("nanobot-api")


SESSIONS_DIR = None  # Set in lifespan from config workspace
LEGACY_SESSIONS_DIR = Path.home() / ".nanobot" / "sessions"
_parent = Path(__file__).parent
CONSOLE_HTML = _parent / "index.html" if (_parent / "index.html").exists() else _parent / "console.html"

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
    """Create LLM provider from config -- mirrors nanobot CLI logic.

    Routing is driven by ProviderSpec.backend in the registry.
    Returns (provider, model_name) where model_name may have the provider
    prefix stripped for direct API providers.
    """
    from nanobot.providers.base import GenerationSettings
    from nanobot.providers.registry import find_by_name

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)
    spec = find_by_name(provider_name) if provider_name else None
    backend = spec.backend if spec else "openai_compat"

    # Strip "provider/" prefix for direct API providers that don't handle it.
    # e.g. "zhipu/glm-4.6v" -> "glm-4.6v" for Zhipu's OpenAI-compatible API.
    if spec and not spec.is_gateway and not spec.strip_model_prefix and "/" in model:
        model = model.split("/", 1)[1]

    if backend == "azure_openai":
        from nanobot.providers.azure_openai_provider import AzureOpenAIProvider
        if not p or not p.api_key or not p.api_base:
            raise RuntimeError("Azure OpenAI requires api_key and api_base in config")
        provider = AzureOpenAIProvider(
            api_key=p.api_key, api_base=p.api_base, default_model=model,
        )
    elif backend == "anthropic":
        from nanobot.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    elif backend == "openai_codex":
        from nanobot.providers.openai_codex_provider import OpenAICodexProvider
        provider = OpenAICodexProvider(default_model=model)
    elif backend == "github_copilot":
        from nanobot.providers.github_copilot_provider import GitHubCopilotProvider
        provider = GitHubCopilotProvider(default_model=model)
    else:
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider
        needs_key = not (p and p.api_key)
        exempt = spec and (getattr(spec, 'is_oauth', False) or getattr(spec, 'is_local', False) or getattr(spec, 'is_direct', False))
        if needs_key and not exempt and not model.startswith("bedrock/"):
            raise RuntimeError("No API key configured in ~/.nanobot/config.json")
        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            spec=spec,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider, model


agent: AgentLoop = None
bus: MessageBus = None
viking = None
_config = None
_feishu_client = None

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
    provider, model = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    agent_kwargs = dict(
        bus=bus, provider=provider, workspace=config.workspace_path,
        model=model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_search_config=config.tools.web.search,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=getattr(config.tools, 'mcp_servers', None),
        channels_config=config.channels,
        timezone=getattr(config.agents.defaults, 'timezone', None),
    )
    agent = AgentLoop(**agent_kwargs)
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
    yield
    _dispatcher_task.cancel()
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
    use_kb: bool = True  # whether to inject knowledge base context

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
    result = await agent.process_direct(
        content=augmented, session_key=session_key, channel="api", chat_id="api",
    )
    response = result.content if result else ""
    clean = _clean_for_tts(response)
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
    # Memory augmentation (skip if use_kb=False)
    augmented = await _augment_with_memory(req.message) if req.use_kb else req.message
    content = f"{augmented}\n\n（回复要求：{req.constraint}）" if req.constraint else augmented
    result = await agent.process_direct(
        content=content, session_key=req.session, channel="api", chat_id=req.session,
    )
    response = result.content if result else ""
    clean = _clean_for_tts(response)
    emotion = _detect_emotion(clean)
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
            use_kb = data.get("use_kb", True)
            # Memory augmentation (skip if use_kb=False)
            augmented = await _augment_with_memory(message) if use_kb else message
            content = f"{augmented}\n\n（回复要求：{constraint}）" if constraint else augmented

            async def _on_progress(text: str, tool_hint: bool = False):
                try:
                    event = {"type": "tool_call" if tool_hint else "thinking", "content": text}
                    await ws.send_json(_enrich_event(event))
                except Exception:
                    pass

            async def _on_stream(delta: str):
                try:
                    await ws.send_json({"type": "stream", "delta": delta})
                except Exception:
                    pass

            async def _on_stream_end(*, resuming: bool = False):
                try:
                    await ws.send_json({"type": "stream_end", "resuming": resuming})
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
                result = await agent.process_direct(
                    content=content, session_key=session, channel="ws", chat_id=session,
                    on_progress=_on_progress,
                    on_stream=_on_stream,
                    on_stream_end=_on_stream_end,
                )
                response = result.content if result else ""
                clean = _clean_for_tts(response)
                emotion = _detect_emotion(clean)
                await ws.send_json({
                    "type": "final",
                    "content": clean,
                    "emotion": emotion,
                    "session": session,
                    "timestamp": time.time(),
                })
            except Exception as e:
                await ws.send_json({"type": "error", "message": str(e)})
            finally:
                hb_task.cancel()
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
async def viking_ls(uri: str = "viking://resources/", mock_no_viking: int = 0):
    """List resources. Pass ?mock_no_viking=1 to simulate Viking-not-installed (for onboarding test)."""
    if mock_no_viking:
        raise HTTPException(503, "OpenViking not initialized (mock)")
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


@app.post("/api/viking/upload")
async def viking_upload(file: UploadFile = File(...)):
    """Upload a file to Viking knowledge base.

    Saves the uploaded file to a temp directory under Viking's data dir,
    then calls add_resource to import it.
    """
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    import tempfile
    # Use a persistent staging dir so Viking can still read after add_resource
    staging_dir = Path(os.path.expanduser("~/.nanobot/viking_uploads"))
    staging_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name if file.filename else "upload"
    dest = staging_dir / safe_name
    try:
        content = await file.read()
        dest.write_bytes(content)
        result = await viking.add_resource(str(dest))
        return {"result": result, "filename": safe_name}
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {e}")


class VikingDeleteRequest(BaseModel):
    uri: str


@app.post("/api/viking/delete")
async def viking_delete(req: VikingDeleteRequest):
    """Delete a resource from Viking knowledge base by URI."""
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    result = await viking.delete_resource(req.uri)
    return {"result": result}


@app.get("/api/viking/get")
async def viking_get(uri: str, mock_no_viking: int = 0):
    """Read full content of a Viking resource by URI."""
    if mock_no_viking:
        raise HTTPException(503, "OpenViking not available (mock)")
    if not viking or not viking.ready:
        raise HTTPException(503, "OpenViking not initialized")
    result = await viking.read_resource(uri)
    return {"ok": True, "content": result}


# ---- Config / Tools / Skills API ----

CONFIG_PATH = Path.home() / ".nanobot" / "config.json"
WORKSPACE_DIR = Path.home() / ".nanobot" / "workspace"
SKILLS_DIR = WORKSPACE_DIR / "skills"


@app.get("/api/config")
async def get_config():
    """Get current agent configuration (model, tools, skills, channels, providers)."""
    config_data = {}
    if _config:
        config_data = {
            "model": _config.agents.defaults.model,
            "max_tokens": _config.agents.defaults.max_tokens,
            "temperature": _config.agents.defaults.temperature,
            "max_tool_iterations": _config.agents.defaults.max_tool_iterations,
            "context_window_tokens": _config.agents.defaults.context_window_tokens,
            "provider": _config.get_provider_name(),
            "workspace": str(_config.workspace_path),
        }
        # Channels config
        config_data["channels"] = {
            "send_progress": _config.channels.send_progress,
            "send_tool_hints": _config.channels.send_tool_hints,
        }
        # Providers — include all with any config set
        providers = []
        for field_name in _config.providers.model_fields:
            p = getattr(_config.providers, field_name, None)
            if p and hasattr(p, 'api_key'):
                providers.append({
                    "name": field_name,
                    "api_key": p.api_key or "",
                    "api_base": p.api_base or "",
                })
        config_data["providers"] = providers
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
    context_window_tokens: int | None = None
    send_progress: bool | None = None
    send_tool_hints: bool | None = None


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    """Update agent configuration. Requires service restart to take effect."""
    if not CONFIG_PATH.exists():
        raise HTTPException(404, "Config file not found")
    try:
        raw = json.loads(CONFIG_PATH.read_text())
        defaults = raw.setdefault("agents", {}).setdefault("defaults", {})
        channels = raw.setdefault("channels", {})
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
        if req.context_window_tokens is not None:
            defaults["contextWindowTokens"] = req.context_window_tokens
            changed.append(f"contextWindowTokens={req.context_window_tokens}")
        if req.send_progress is not None:
            channels["sendProgress"] = req.send_progress
            changed.append(f"sendProgress={req.send_progress}")
        if req.send_tool_hints is not None:
            channels["sendToolHints"] = req.send_tool_hints
            changed.append(f"sendToolHints={req.send_tool_hints}")
        if not changed:
            return {"status": "no changes"}
        CONFIG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
        return {"status": "updated", "changed": changed}
    except Exception as e:
        raise HTTPException(500, f"Failed to update config: {e}")


class ProviderUpdateRequest(BaseModel):
    name: str
    api_key: str | None = None
    api_base: str | None = None


@app.post("/api/config/provider")
async def update_provider(req: ProviderUpdateRequest):
    """Update a provider's API key and/or base URL."""
    if not CONFIG_PATH.exists():
        raise HTTPException(404, "Config file not found")
    try:
        raw = json.loads(CONFIG_PATH.read_text())
        p = raw.setdefault("providers", {}).setdefault(req.name, {})
        changed = []
        if req.api_key is not None:
            p["apiKey"] = req.api_key
            changed.append("apiKey")
        if req.api_base is not None:
            p["apiBase"] = req.api_base if req.api_base else None
            changed.append("apiBase")
        if not changed:
            return {"status": "no changes"}
        CONFIG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
        return {"status": "updated", "provider": req.name, "changed": changed}
    except Exception as e:
        raise HTTPException(500, f"Failed to update provider: {e}")


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


# ---- Cron API ----
# Reads and writes jobs.json directly — no in-memory CronService.
# Execution is handled entirely by nanobot.service (gateway); nanobot-api
# only provides the management interface.

def _cron_jobs_path() -> Path:
    return get_data_dir() / "cron" / "jobs.json"


def _read_cron_jobs() -> list[dict]:
    """Read all jobs from jobs.json. Always reads from disk."""
    p = _cron_jobs_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("jobs", [])
    except Exception:
        return []


def _write_cron_jobs(jobs: list[dict]) -> None:
    """Atomically overwrite jobs.json with updated job list."""
    p = _cron_jobs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"version": 1, "jobs": jobs}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _compute_next_run_ms(schedule: dict) -> int | None:
    """Compute nextRunAtMs for a new or re-enabled job.

    Accepts nanobot's camelCase schedule keys (atMs, everyMs, expr, tz, kind).
    """
    kind = schedule.get("kind")
    now_ms = int(time.time() * 1000)
    if kind == "at":
        at = schedule.get("atMs")
        return at if at and at > now_ms else None
    if kind == "every":
        every = schedule.get("everyMs")
        return now_ms + every if every and every > 0 else None
    if kind == "cron":
        expr = schedule.get("expr")
        if not expr:
            return None
        try:
            from croniter import croniter
            from zoneinfo import ZoneInfo
            from datetime import datetime
            tz_name = schedule.get("tz")
            tz = ZoneInfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(now_ms / 1000, tz=tz)
            return int(croniter(expr, base_dt).get_next(datetime).timestamp() * 1000)
        except Exception:
            return None
    return None


def _normalize_job(j: dict) -> dict:
    """Normalize a raw jobs.json entry (camelCase) to the API response shape (snake_case)."""
    state = j.get("state") or {}
    schedule = j.get("schedule") or {}
    payload = j.get("payload") or {}
    return {
        "id": j.get("id"),
        "name": j.get("name"),
        "enabled": j.get("enabled", True),
        "schedule": {
            "kind": schedule.get("kind"),
            "expr": schedule.get("expr"),
            "every_ms": schedule.get("everyMs"),
            "at_ms": schedule.get("atMs"),
            "tz": schedule.get("tz"),
        },
        "payload": {
            "message": payload.get("message", ""),
            "deliver": payload.get("deliver", False),
            "channel": payload.get("channel"),
            "to": payload.get("to"),
        },
        "state": {
            "next_run_at_ms": state.get("nextRunAtMs"),
            "last_run_at_ms": state.get("lastRunAtMs"),
            "last_status": state.get("lastStatus"),
            "last_error": state.get("lastError"),
        },
        "delete_after_run": j.get("deleteAfterRun", False),
        "created_at_ms": j.get("createdAtMs", 0),
    }


@app.get("/api/cron/jobs")
async def list_cron_jobs():
    return [_normalize_job(j) for j in _read_cron_jobs()]


class CronJobCreateRequest(BaseModel):
    name: str
    schedule_kind: str  # "cron" | "every" | "at"
    expr: str | None = None
    every_ms: int | None = None
    at_ms: int | None = None
    tz: str | None = None
    message: str
    deliver: bool = False
    channel: str | None = None
    to: str | None = None
    delete_after_run: bool = False


@app.post("/api/cron/jobs")
async def create_cron_job(req: CronJobCreateRequest):
    # Use nanobot's camelCase format so the file stays compatible with nanobot.service
    schedule = {
        "kind": req.schedule_kind,
        "expr": req.expr,
        "everyMs": req.every_ms,
        "atMs": req.at_ms,
        "tz": req.tz,
    }
    now_ms = int(time.time() * 1000)
    job = {
        "id": str(uuid.uuid4())[:8],
        "name": req.name,
        "enabled": True,
        "schedule": schedule,
        "payload": {
            "kind": "agent_turn",
            "message": req.message,
            "deliver": req.deliver,
            "channel": req.channel,
            "to": req.to,
        },
        "state": {
            "nextRunAtMs": _compute_next_run_ms(schedule),
            "lastRunAtMs": None,
            "lastStatus": None,
            "lastError": None,
        },
        "createdAtMs": now_ms,
        "updatedAtMs": now_ms,
        "deleteAfterRun": req.delete_after_run,
    }
    jobs = _read_cron_jobs()
    jobs.append(job)
    _write_cron_jobs(jobs)

    return _normalize_job(job)


@app.delete("/api/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str):
    jobs = _read_cron_jobs()
    new_jobs = [j for j in jobs if j["id"] != job_id]
    if len(new_jobs) == len(jobs):
        raise HTTPException(404, "Job not found")
    _write_cron_jobs(new_jobs)

    return {"status": "deleted"}


@app.post("/api/cron/jobs/{job_id}/toggle")
async def toggle_cron_job(job_id: str):
    jobs = _read_cron_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "Job not found")
    job["enabled"] = not job.get("enabled", True)
    job["updatedAtMs"] = int(time.time() * 1000)
    if job["enabled"]:
        job.setdefault("state", {})["nextRunAtMs"] = _compute_next_run_ms(job["schedule"])
    else:
        job.setdefault("state", {})["nextRunAtMs"] = None
    _write_cron_jobs(jobs)

    return _normalize_job(job)


@app.post("/api/cron/jobs/{job_id}/run")
async def run_cron_job(job_id: str):
    """Manually trigger a cron job by dispatching its message through the agent."""
    jobs = _read_cron_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "Job not found")
    if not agent:
        raise HTTPException(503, "Agent not ready")
    message = job.get("payload", {}).get("message", "")
    asyncio.create_task(
        agent.process_direct(message, session_key="cron:manual", channel="cron", chat_id="manual")
    )
    return {"status": "triggered"}


@app.get("/api/cron/system")
async def get_system_crontab():
    import shutil
    import subprocess
    if not shutil.which("crontab"):
        return {"entries": [], "unsupported": True}
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        raw_lines = r.stdout.splitlines() if r.stdout.strip() else []
        entries = []
        for i, line in enumerate(raw_lines):
            stripped = line.strip()
            if not stripped:
                continue
            is_comment_only = stripped.startswith('#') and not _is_commented_cron(stripped)
            entries.append({
                "index": i,
                "content": line,
                "enabled": not stripped.startswith('#'),
                "comment_only": is_comment_only,
            })
        return {"entries": entries}
    except Exception as e:
        raise HTTPException(500, str(e))


def _is_commented_cron(line: str) -> bool:
    """Check if a commented line looks like a disabled cron entry."""
    import re
    stripped = line.lstrip('#').strip()
    return bool(re.match(r'^(\*|[0-9])', stripped))


class SystemCronUpdateRequest(BaseModel):
    entries: list[dict]  # list of {content, enabled}


@app.post("/api/cron/system")
async def update_system_crontab(req: SystemCronUpdateRequest):
    import shutil
    import subprocess
    if not shutil.which("crontab"):
        raise HTTPException(400, "crontab not available on this platform")
    try:
        lines = []
        for e in req.entries:
            content = e.get("content", "").rstrip()
            enabled = e.get("enabled", True)
            stripped = content.lstrip('#').strip()
            if not enabled and not content.startswith('#'):
                content = '# ' + content
            elif enabled and content.startswith('#') and _is_commented_cron(content):
                content = content.lstrip('#').strip()
            lines.append(content)
        text = '\n'.join(lines) + '\n'
        proc = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)
        if proc.returncode != 0:
            raise HTTPException(400, proc.stderr or "crontab error")
        return {"status": "updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/cron/system/{index}")
async def delete_system_cron(index: int):
    import shutil
    import subprocess
    if not shutil.which("crontab"):
        raise HTTPException(400, "crontab not available on this platform")
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines = r.stdout.splitlines() if r.stdout.strip() else []
        non_empty = [(i, l) for i, l in enumerate(lines) if l.strip()]
        # index refers to position in non_empty list
        if index < 0 or index >= len(non_empty):
            raise HTTPException(404, "Entry not found")
        orig_idx = non_empty[index][0]
        lines.pop(orig_idx)
        text = '\n'.join(lines) + '\n'
        subprocess.run(["crontab", "-"], input=text, text=True)
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/health")
async def health(mock_no_viking: int = 0):
    """Health check. Pass ?mock_no_viking=1 to test onboarding UI (Viking OFF state)."""
    return {
        "status": "ok",
        "agent_ready": agent is not None,
        "viking_ready": (viking is not None and viking.ready) if not mock_no_viking else False,
    }


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": "nanobot", "object": "model", "owned_by": "local"}]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18790)
