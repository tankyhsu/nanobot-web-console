# nanobot Web Console

**[English](README.md) | [中文](README_CN.md)**

A web console + API server for [nanobot](https://github.com/HKUDS/nanobot) — an ultra-lightweight personal AI assistant framework.

nanobot provides a powerful agent core (tools, skills, memory, channels) but no HTTP API or web UI. This project adds both: a FastAPI server (`server.py`) and a single-file web console (`index.html`).

## Screenshots

| Session History | Live Chat |
|----------------|-----------|
| ![Dark Theme](screenshots/01-session-dark.png) | ![Live Chat](screenshots/03-livechat-tools.png) |

| Settings | Mobile |
|----------|--------|
| ![Settings](screenshots/07-settings.png) | ![Mobile](screenshots/08-mobile.png) |

## Quick Start

**Prerequisites:** Python 3.11+, nanobot installed and configured (`~/.nanobot/config.json`)

```bash
pip install nanobot-ai fastapi uvicorn pydantic
git clone https://github.com/tankyhsu/nanobot-web-console.git
cd nanobot-web-console
python server.py
```

Open `http://localhost:18790`.

### Run as systemd service

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

## Features

### Web Console

- **Session History** — Browse all sessions with channel filtering (Feishu / API / WS / CLI). Tool call and result messages render as collapsible cards.
- **Live Chat** — Real-time WebSocket chat with streaming thinking indicators, tool call events, and results. Content persists when navigating away.
- **Settings** — 6-tab configuration panel:
  - **Agent** — Model, temperature, max tokens, iterations, memory window
  - **Channels** — `sendProgress` / `sendToolHints` toggles
  - **Providers** — Edit API keys and base URLs per provider
  - **Cron** — Manage nanobot scheduled jobs (add / toggle / trigger / delete) and system crontab. Changes automatically sync to the nanobot gateway.
  - **Prompts** — Edit SOUL.md, AGENTS.md, USER.md in-browser
  - **Info** — Tools, skills, memory viewer
- **Knowledge Base** *(optional, requires [nanobot-viking](https://github.com/tankyhsu/nanobot-viking))* — Browse `viking://` filesystem, semantic search
- **Dark / Light theme**, mobile responsive, URL routing, IME-compatible input

### API Server

- `POST /api/chat` — Simple chat with emotion detection
- `POST /v1/chat/completions` — OpenAI-compatible endpoint
- `WS /ws/chat` — Streaming chat (thinking / tool_call / tool_result / final events + 15s heartbeat)
- `GET/DELETE /api/sessions/{name}` — Session management
- `GET/POST /api/config` — View and update agent config
- `GET/POST /api/cron/jobs` — Nanobot cron job management
- `/api/viking/*` — Knowledge base *(optional)*

## WebSocket Protocol

Client sends:
```json
{"message": "user text", "session": "ws:device-id", "constraint": "optional"}
```

Server pushes:
```json
{"type": "thinking", "iteration": 1}
{"type": "tool_call", "name": "exec", "arguments": "{\"command\": \"df -h\"}"}
{"type": "tool_result", "name": "exec", "result": "..."}
{"type": "heartbeat", "timestamp": 1739800015.0}
{"type": "final", "content": "...", "emotion": "happy", "session": "ws:device-id"}
```

## Knowledge Base (Optional)

```bash
# Copy viking_service.py to the same directory as server.py
cp /path/to/nanobot-viking/viking_service.py .
```

Detected and initialized automatically on startup.

## File Structure

```
server.py      # FastAPI server — nanobot web layer
index.html     # Web console (single file, zero dependencies)
screenshots/   # Demo screenshots
```

## Tech Stack

- **server.py** — FastAPI + Uvicorn, imports nanobot internals directly
- **index.html** — Vanilla JS, CSS custom properties, WebSocket API, [marked.js](https://github.com/markedjs/marked) (CDN)

## SiliconFlow Free Models

If you use [SiliconFlow](https://siliconflow.cn), the embedding (`BAAI/bge-m3`) and VLM (`DeepSeek-OCR`) models used by Viking are available on the free tier.

Referral link for bonus credits: **https://cloud.siliconflow.cn/i/UzI0F3Xv**

## License

MIT
