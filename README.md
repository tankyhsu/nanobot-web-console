# nanobot Web Console

**[English](README.md) | [中文](README_CN.md)**

A single-file web console for [nanobot](https://github.com/pinkponk/nanobot) — an AI Agent framework. Provides real-time chat, session history browsing, agent configuration management, and optionally integrates with [OpenViking](https://github.com/pinkponk/openviking) for knowledge base management.

> **Note:** The Knowledge Base feature requires [nanobot-viking](https://github.com/tankyhsu/nanobot-viking) — a separate integration project. Without it, the console works perfectly for session browsing, live chat, and settings; the Knowledge button simply hides when Viking is not available.

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

## Features

- **Session History** — Browse all chat sessions with channel filtering (Feishu/API/WS/CLI)
- **Live Chat** — Real-time WebSocket conversation with streaming event display
  - Thinking indicators with iteration count
  - Tool call events with icon, name, and argument summary (expandable)
  - Tool result events with success/error status (expandable)
  - Content persists when navigating away and back
  - WebSocket stays connected in background
- **Settings Panel** — View and manage agent configuration
  - Model settings (model, temperature, max tokens, max iterations)
  - Registered tools list with parameters
  - Skills list with expandable details
  - System prompt editor (SOUL.md, AGENTS.md, USER.md)
  - Long-term memory viewer
- **Knowledge Base** *(optional, requires [nanobot-viking](https://github.com/tankyhsu/nanobot-viking))*
  - Browse `viking://` virtual filesystem
  - Semantic search across resources and memories
  - Breadcrumb navigation
  - Auto-hidden when Viking is not available
- **Dark / Light Theme** — Toggle with persistence via localStorage
- **Mobile Responsive** — Hamburger menu, touch-friendly, iOS zoom prevention
- **URL Routing** — Deep link to sessions or modes (`?session=xxx`, `?mode=live`, `?mode=viking`, `?mode=settings`)
- **Session Management** — Delete sessions with confirmation dialog
- **IME Compatible** — Chinese/Japanese/Korean input method support
- **Markdown Rendering** — Full GFM support in chat bubbles (tables, code blocks, lists, etc.)

## Integration with nanobot

### Prerequisites

- A running [nanobot](https://github.com/pinkponk/nanobot) instance
- A FastAPI wrapper server (`nanobot-api`) that exposes the required HTTP/WebSocket endpoints

### Required API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check, returns `{agent_ready, viking_ready}` |
| `/api/sessions` | GET | List all sessions `[{name, display, messages, updated}]` |
| `/api/sessions/{name}` | GET | Get session messages `[{role, content, timestamp}]` |
| `/api/sessions/{name}` | DELETE | Delete a session |
| `/ws/chat` | WebSocket | Streaming chat (see protocol below) |
| `/api/config` | GET | Get agent configuration (model, tools, skills, prompts) |
| `/api/config` | POST | Update model configuration |
| `/api/config/prompt` | POST | Update system prompt file |
| `/api/viking/*` | * | Knowledge base endpoints *(optional)* |

### WebSocket Protocol

Client sends:
```json
{"message": "user text", "session": "ws:device-id", "constraint": "optional"}
```

Server pushes events in order:
```json
{"type": "thinking", "iteration": 1}
{"type": "tool_call", "name": "exec", "arguments": "{\"command\": \"df -h\"}"}
{"type": "tool_result", "name": "exec", "result": "Filesystem  Size  Used..."}
{"type": "final", "content": "The disk usage is...", "session": "ws:device-id"}
{"type": "error", "message": "error description"}
```

### Deployment

The console is a single HTML file. Serve it as the root route of your FastAPI app:

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def console_page():
    return open("index.html").read()
```

Or place it behind any static file server / reverse proxy — just ensure the API endpoints are on the same origin (or configure CORS).

## Tech Stack

- **Zero dependencies** — Single HTML file, no build step
- [marked.js](https://github.com/markedjs/marked) (CDN) — Markdown rendering
- Vanilla JavaScript — No framework
- CSS Custom Properties — Theme system
- WebSocket API — Streaming communication

## File Structure

```
index.html          # The complete web console (single file)
scripts/
  screenshots.py    # Screenshot generation script (playwright)
screenshots/        # Demo screenshots for README
```

## SiliconFlow Free Models

If you use [SiliconFlow](https://siliconflow.cn) as your LLM provider, the embedding model (`BAAI/bge-m3`) and VLM model (`DeepSeek-OCR`) used by the Viking knowledge base integration are **available on the free tier**.

Register via referral link for bonus credits: **https://cloud.siliconflow.cn/i/UzI0F3Xv**

<img src="screenshots/siliconflow-qr.png" alt="SiliconFlow QR Code" width="200">

## License

MIT
