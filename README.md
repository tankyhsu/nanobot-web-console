# nanobot Web Console

**[English](README.md) | [中文](README_CN.md)**

A web console + API server for [nanobot](https://github.com/HKUDS/nanobot) — an ultra-lightweight personal AI assistant framework. This project provides everything nanobot lacks out of the box: an HTTP/WebSocket API layer and a full-featured web UI.

**nanobot** itself only provides a CLI and channel-based interaction (Feishu, Telegram, Discord, etc.). This project wraps it with a FastAPI server (`server.py`) and a single-file web console (`index.html`), enabling:

- Browser-based real-time chat with streaming tool execution visualization
- HTTP API and OpenAI-compatible endpoint for external integrations
- Session history browsing across all channels
- Agent configuration management (model, tools, skills, prompts)
- Optional knowledge base integration via [nanobot-viking](https://github.com/tankyhsu/nanobot-viking)

## Screenshots

### Session History (Dark / Light Theme)

| Dark | Light |
|------|-------|
| ![Dark Theme](screenshots/01-session-dark.png) | ![Light Theme](screenshots/02-session-light.png) |

### Live Chat with Streaming Tool Events

Real-time WebSocket chat showing the agent's thinking process, tool calls, and results:

![Live Chat](screenshots/03-livechat-tools.png)

Click any tool event to expand and see full details:

![Expanded Tools](screenshots/04-livechat-expanded.png)

### Settings Panel

View and edit model configuration, browse registered tools and skills, edit system prompts:

![Settings](screenshots/07-settings.png)

### Knowledge Base (OpenViking) — Optional

Requires [nanobot-viking](https://github.com/tankyhsu/nanobot-viking). Browse the knowledge base directory structure and search for content:

| File Browser | Semantic Search |
|-------------|-----------------|
| ![Browser](screenshots/05-viking-browser.png) | ![Search](screenshots/06-viking-search.png) |

### Mobile Responsive

![Mobile](screenshots/08-mobile.png)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 server.py (FastAPI)                   │
│                                                       │
│  GET  /              ──→ Serve index.html (Console)  │
│  GET  /health        ──→ Health check                │
│  GET  /api/sessions  ──→ List all sessions (JSONL)   │
│  POST /api/chat      ──→ Simple chat (with emotion)  │
│  POST /v1/chat/completions ──→ OpenAI-compatible API │
│  WS   /ws/chat       ──→ Streaming chat + events     │
│  GET  /api/config    ──→ Model, tools, skills, prompts│
│  POST /api/config    ──→ Update model config          │
│  /api/viking/*       ──→ Knowledge base (optional)   │
│                                                       │
│  Imports:                                             │
│    nanobot.agent.loop.AgentLoop                       │
│    nanobot.bus.queue.MessageBus                       │
│    nanobot.session.manager.SessionManager             │
│    nanobot.config.loader.load_config                  │
│    viking_service.VikingService (optional)            │
└─────────────────────────────────────────────────────┘
```

`server.py` is the bridge between nanobot's Python internals and the web. It initializes nanobot's `AgentLoop`, `MessageBus`, `SessionManager`, and `CronService`, then exposes them as HTTP/WebSocket endpoints. The web console (`index.html`) talks to these endpoints.

## Quick Start

### Prerequisites

- Python 3.11+
- [nanobot](https://github.com/HKUDS/nanobot) installed and configured (`~/.nanobot/config.json`)

```bash
pip install nanobot-ai
```

### 1. Clone this repo

```bash
git clone https://github.com/tankyhsu/nanobot-web-console.git
cd nanobot-web-console
```

### 2. Install dependencies

```bash
pip install fastapi uvicorn pydantic
```

### 3. Run

```bash
python server.py
```

Open `http://localhost:18790` in your browser.

### 4. Run as systemd service (optional)

```ini
# /etc/systemd/system/nanobot-api.service
[Unit]
Description=nanobot API Server
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/server.py
WorkingDirectory=/path/to/nanobot-web-console
Environment=HOME=/root
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now nanobot-api
```

## How It Works with nanobot

### What nanobot provides (and what it doesn't)

[nanobot](https://github.com/HKUDS/nanobot) is a ~4,000-line AI agent framework. It provides:

- `AgentLoop` — LLM reasoning loop with tool execution
- `MessageBus` — Internal message routing
- `SessionManager` — JSONL-based conversation persistence
- `CronService` — Scheduled tasks
- Channels — Feishu, Telegram, Discord, Slack, QQ, Email, etc.
- Tools — exec, read_file, write_file, web_search, etc.
- Skills — Custom automation scripts
- Memory — Long-term memory system

What nanobot does **NOT** provide:
- No HTTP API server
- No Web UI
- No WebSocket streaming interface
- No OpenAI-compatible endpoint

This project fills that gap.

### What server.py does

`server.py` imports nanobot's internal modules and wraps them in a FastAPI server:

```python
from nanobot.config.loader import load_config
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.session.manager import SessionManager
from nanobot.cron.service import CronService
```

On startup (`lifespan`), it:
1. Loads nanobot config from `~/.nanobot/config.json`
2. Creates an LLM provider (via `LiteLLMProvider`)
3. Initializes `AgentLoop` with all tools and settings
4. Optionally starts `VikingService` for knowledge base
5. Starts `CronService` for scheduled tasks

### nanobot config.json structure

`server.py` reads the standard nanobot config at `~/.nanobot/config.json`:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot/workspace",
      "model": "openai/your-model-name",
      "maxTokens": 8192,
      "temperature": 0.7,
      "maxToolIterations": 50
    }
  },
  "providers": {
    "openai": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "tools": {
    "web": { "search": { "apiKey": "brave-api-key", "maxResults": 5 } },
    "exec": { "timeout": 60 },
    "restrictToWorkspace": false
  },
  "channels": {
    "feishu": { "enabled": true, "appId": "...", "appSecret": "..." }
  }
}
```

No additional configuration is needed — `server.py` reuses everything from nanobot's config.

## Features

### Web Console (index.html)

- **Session History** — Browse all chat sessions with channel filtering (Feishu/API/WS/CLI)
- **Live Chat** — Real-time WebSocket conversation with streaming event display
  - Thinking indicators with iteration count
  - Tool call events with expandable argument details and results
  - Content persists when navigating away and back
- **Settings Panel** — View and manage agent configuration
  - Model settings (model, temperature, max tokens, max iterations)
  - Registered tools list with parameters
  - Skills list with expandable details
  - System prompt editor (SOUL.md, AGENTS.md, USER.md)
  - Long-term memory viewer
- **Knowledge Base** *(optional, requires [nanobot-viking](https://github.com/tankyhsu/nanobot-viking))*
  - Browse `viking://` virtual filesystem
  - Semantic search across resources and memories
- **Dark / Light Theme** — Toggle with localStorage persistence
- **Mobile Responsive** — Hamburger menu, touch-friendly
- **URL Routing** — `?session=xxx`, `?mode=live`, `?mode=viking`, `?mode=settings`
- **IME Compatible** — CJK input method support (Enter key doesn't trigger send during composition)

### API Server (server.py)

- **Simple Chat** — `POST /api/chat` with emotion detection
- **OpenAI-compatible** — `POST /v1/chat/completions` for integration with tools expecting OpenAI API
- **WebSocket Streaming** — `WS /ws/chat` with thinking, tool_call, tool_result, final events
- **Session Management** — List, view, delete sessions via REST API
- **Config API** — View/update model settings, browse tools and skills
- **RAG Augmentation** — Automatically enriches user messages with knowledge base context (when Viking is available)
- **Emotion Detection** — Keyword-based emotion tagging for TTS/avatar integration
- **TTS-friendly Output** — Strips markdown formatting for voice output

## API Reference

### HTTP Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web console (index.html) |
| `/health` | GET | `{status, agent_ready, viking_ready}` |
| `/api/sessions` | GET | List all sessions |
| `/api/sessions/{name}` | GET | Get session messages |
| `/api/sessions/{name}` | DELETE | Delete a session |
| `/api/chat` | POST | Simple chat with emotion |
| `/v1/chat/completions` | POST | OpenAI-compatible chat |
| `/v1/models` | GET | List available models |
| `/api/config` | GET | Agent config (model, tools, skills, prompts, memory) |
| `/api/config` | POST | Update model config |
| `/api/config/prompt` | POST | Update system prompt file |
| `/api/viking/*` | * | Knowledge base *(optional)* |

### WebSocket Protocol (`/ws/chat`)

Client sends:
```json
{"message": "user text", "session": "ws:device-id", "constraint": "optional"}
```

Server pushes events:
```json
{"type": "thinking", "iteration": 1, "emotion": "thinking"}
{"type": "tool_call", "name": "exec", "arguments": "{\"command\": \"df -h\"}", "emotion": "gear"}
{"type": "tool_result", "name": "exec", "result": "...", "emotion": "cool"}
{"type": "final", "content": "...", "emotion": "happy", "session": "ws:device-id"}
```

### Simple Chat (`POST /api/chat`)

```bash
curl -X POST http://localhost:18790/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What services are running?", "session": "api:test"}'
```

Response:
```json
{
  "response": "Currently running services include...",
  "session": "api:test",
  "timestamp": 1739800000.0,
  "emotion": "happy"
}
```

## Optional: Knowledge Base Integration

For RAG (Retrieval-Augmented Generation), add [nanobot-viking](https://github.com/tankyhsu/nanobot-viking):

```bash
# Copy viking_service.py to the same directory as server.py
cp /path/to/nanobot-viking/viking_service.py .
```

`server.py` will automatically detect and initialize `VikingService` on startup. If Viking is not available, the server runs normally without it.

## File Structure

```
server.py           # FastAPI server — the bridge between nanobot and the web
index.html          # Web console UI (single file, zero dependencies)
scripts/
  screenshots.py    # Screenshot generation script (playwright, for README)
screenshots/        # Demo screenshots
```

## Tech Stack

- **server.py** — FastAPI + Uvicorn, imports nanobot internals directly
- **index.html** — Single HTML file, no build step, no npm
  - [marked.js](https://github.com/markedjs/marked) (CDN) for Markdown rendering
  - Vanilla JavaScript, CSS Custom Properties for theming
  - WebSocket API for streaming

## SiliconFlow Free Models

If you use [SiliconFlow](https://siliconflow.cn) as your LLM provider, the embedding model (`BAAI/bge-m3`) and VLM model (`DeepSeek-OCR`) used by the Viking knowledge base integration are **available on the free tier**.

Register via referral link for bonus credits: **https://cloud.siliconflow.cn/i/UzI0F3Xv**

<img src="screenshots/siliconflow-qr.png" alt="SiliconFlow QR Code" width="200">

## Changelog

### v0.2.0

- **Compatible with nanobot 0.1.4+** — `StreamingAgentLoop` overrides `_run_agent_loop` for real-time WebSocket streaming of thinking, tool_call, and tool_result events. Supports new AgentLoop params: `temperature`, `max_tokens`, `mcp_servers`.
- **WebSocket auto-reconnect** — Exponential backoff reconnection (1s→30s) when the WebSocket connection drops during live chat.
- **Viking is now fully optional** — `viking_service.py` import is wrapped in try/except. The server starts cleanly without it.

### v0.0.2

- **i18n support** — Chinese/English toggle with auto-detection of browser language. All UI text is translatable. Language preference persists in localStorage.

### v0.0.1

- Initial release: web console (`index.html`) + API server (`server.py`)
- Session history browsing with channel filtering
- Live chat with streaming tool event visualization
- Settings panel (model config, tools, skills, prompt editor, memory viewer)
- Knowledge base browser and semantic search (optional, via nanobot-viking)
- Dark/light theme toggle
- Mobile responsive layout
- URL routing and IME-compatible input

## License

MIT
