# nanobot Web Console

[English](#english) | [中文](#中文)

---

<a name="english"></a>

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

---

<a name="中文"></a>

# nanobot Web Console（中文）

一个为 [nanobot](https://github.com/pinkponk/nanobot) AI Agent 框架打造的单文件 Web 控制台。支持实时对话、会话历史浏览、Agent 配置管理，并可选集成 [OpenViking](https://github.com/pinkponk/openviking) 知识库。

> **说明：** 知识库功能需要 [nanobot-viking](https://github.com/tankyhsu/nanobot-viking) 集成项目。不安装时，控制台的会话浏览、实时对话、设置面板等功能完全正常使用，知识库按钮会在 Viking 不可用时自动隐藏。

## 截图

### 会话历史（深色 / 浅色主题）

| 深色 | 浅色 |
|------|------|
| ![深色主题](screenshots/01-session-dark.png) | ![浅色主题](screenshots/02-session-light.png) |

### 实时对话（工具调用流式展示）

实时 WebSocket 对话，展示 Agent 的思考过程、工具调用和执行结果：

![实时对话](screenshots/03-livechat-tools.png)

点击任意工具事件卡片可展开查看完整细节：

![展开详情](screenshots/04-livechat-expanded.png)

### 设置面板

查看和编辑模型配置，浏览已注册的工具和技能，编辑系统提示词：

![设置](screenshots/07-settings.png)

### 知识库浏览器（OpenViking）— 可选

需要 [nanobot-viking](https://github.com/tankyhsu/nanobot-viking)。浏览知识库目录结构和语义搜索：

| 文件浏览 | 语义搜索 |
|----------|----------|
| ![浏览器](screenshots/05-viking-browser.png) | ![搜索](screenshots/06-viking-search.png) |

### 移动端适配

![移动端](screenshots/08-mobile.png)

## 功能特性

- **会话历史** — 浏览所有对话记录，支持按渠道筛选（飞书/API/WebSocket/CLI）
- **实时对话** — WebSocket 实时流式对话
  - 思考状态指示（含迭代次数）
  - 工具调用事件卡片（可展开查看参数和结果）
  - 对话内容在页面切换时保持不丢失
  - WebSocket 后台保持连接
- **设置面板** — 查看和管理 Agent 配置
  - 模型设置（模型名称、温度、最大 token、最大迭代次数）
  - 已注册工具列表及参数说明
  - 技能列表（可展开查看详情）
  - 系统提示词编辑器（SOUL.md、AGENTS.md、USER.md）
  - 长期记忆查看
- **知识库** *（可选，需 [nanobot-viking](https://github.com/tankyhsu/nanobot-viking)）*
  - 浏览 `viking://` 虚拟文件系统
  - 跨资源和记忆的语义搜索
  - 面包屑导航
  - Viking 不可用时自动隐藏
- **深色 / 浅色主题** — 一键切换，通过 localStorage 持久化
- **移动端适配** — 汉堡菜单、触控友好、iOS 缩放防护
- **URL 路由** — 深链接支持（`?session=xxx`、`?mode=live`、`?mode=viking`、`?mode=settings`）
- **会话管理** — 支持删除会话（带确认对话框）
- **输入法兼容** — 中日韩输入法回车键不会误触发发送
- **Markdown 渲染** — 完整 GFM 支持（表格、代码块、列表等）

## 与 nanobot 集成

### 前提条件

- 一个运行中的 [nanobot](https://github.com/pinkponk/nanobot) 实例
- 一个暴露 HTTP/WebSocket 接口的 FastAPI 服务（`nanobot-api`）

### 部署

控制台是一个单 HTML 文件，作为 FastAPI 的根路由提供服务即可：

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def console_page():
    return open("index.html").read()
```

也可以放在任何静态文件服务器 / 反向代理后面，确保 API 接口在同源即可。

## 技术栈

- **零依赖** — 单 HTML 文件，无需构建
- [marked.js](https://github.com/markedjs/marked)（CDN）— Markdown 渲染
- 原生 JavaScript — 无框架
- CSS 自定义属性 — 主题系统
- WebSocket API — 流式通信

## SiliconFlow 免费模型

如果您使用 [SiliconFlow（硅基流动）](https://siliconflow.cn) 作为 LLM 服务商，Viking 知识库集成使用的向量模型（`BAAI/bge-m3`）和视觉模型（`DeepSeek-OCR`）均可在**免费额度**内使用。

通过推荐链接注册可获得额外赠送额度：**https://cloud.siliconflow.cn/i/UzI0F3Xv**

<img src="screenshots/siliconflow-qr.png" alt="SiliconFlow 二维码" width="200">

## 许可证

MIT
