# nanobot Web Console

**[English](README.md) | [中文](README_CN.md)**

为 [nanobot](https://github.com/HKUDS/nanobot) 打造的 Web 控制台 + API 服务。

nanobot 提供了强大的 Agent 核心（工具、技能、记忆、消息渠道），但没有 HTTP API 也没有 Web 界面。本项目补齐了这两块：一个 FastAPI 服务（`server.py`）+ 一个单文件 Web 控制台（`index.html`）。

## 截图

| 会话历史 | 实时对话 |
|---------|---------|
| ![深色主题](screenshots/01-session-dark.png) | ![实时对话](screenshots/03-livechat-tools.png) |

| 设置面板 | 移动端 |
|---------|--------|
| ![设置](screenshots/07-settings.png) | ![移动端](screenshots/08-mobile.png) |

| 知识库文件列表 | 语义搜索 |
|--------------|---------|
| ![知识库文件列表](screenshots/02-knowledge-base.png) | ![语义搜索](screenshots/03-knowledge-search.png) |

| 文件内容查看 | 新手引导 |
|------------|---------|
| ![文件内容查看](screenshots/04-knowledge-detail.png) | ![新手引导](screenshots/05-onboarding.png) |

## 快速开始

**前置要求：** Python 3.11+，nanobot 已安装并配置（`~/.nanobot/config.json`）

```bash
pip install nanobot-ai fastapi uvicorn pydantic
git clone https://github.com/tankyhsu/nanobot-web-console.git
cd nanobot-web-console
python server.py
```

浏览器打开 `http://localhost:18790`。

### 注册为系统服务

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

## 功能特性

### Web 控制台

- **会话历史** — 浏览所有对话，按渠道筛选（飞书 / API / WS / CLI）。工具调用和结果消息以可折叠卡片展示。
- **实时对话** — WebSocket 流式对话，展示思考状态、工具调用事件和结果。切换页面后内容不丢失。
- **设置面板** — 6 个 Tab 的配置中心：
  - **Agent** — 模型、温度、max tokens、最大迭代、上下文窗口
  - **消息频道** — `sendProgress` / `sendToolHints` 开关
  - **服务商** — 各 provider 的 API Key 和 Base URL
  - **定时任务** — 管理 nanobot 托管任务（新建 / 启停 / 立即执行 / 删除）及系统 crontab，修改后自动同步到 nanobot gateway
  - **系统提示** — 在线编辑 SOUL.md、AGENTS.md、USER.md
  - **信息** — 工具列表、技能列表、长期记忆查看
- **知识库** *（可选，`pip install openviking` 即可启用）* — 浏览 `viking://` 虚拟文件系统、查看文件内容、上传/删除文件、语义搜索；未安装时自动显示新手引导
- 深色 / 浅色主题、移动端适配、URL 路由、输入法兼容

### API 服务

- `POST /api/chat` — 简单对话，带情绪检测
- `POST /v1/chat/completions` — OpenAI 兼容接口
- `WS /ws/chat` — 流式对话（thinking / tool_call / tool_result / final 事件 + 15s 心跳）
- `GET/DELETE /api/sessions/{name}` — 会话管理
- `GET/POST /api/config` — 查看和更新 Agent 配置
- `GET/POST /api/cron/jobs` — nanobot 定时任务管理
- `/api/viking/*` — 知识库 *（可选，需 `pip install openviking`）*

## WebSocket 协议

客户端发送：
```json
{"message": "用户消息", "session": "ws:device-id", "constraint": "可选约束"}
```

服务端推送：
```json
{"type": "thinking", "iteration": 1}
{"type": "tool_call", "name": "exec", "arguments": "{\"command\": \"df -h\"}"}
{"type": "tool_result", "name": "exec", "result": "..."}
{"type": "heartbeat", "timestamp": 1739800015.0}
{"type": "final", "content": "...", "emotion": "happy", "session": "ws:device-id"}
```

## 知识库集成（可选）

只需一条命令安装：

```bash
pip install openviking
```

重启 `server.py` 后自动检测并初始化，无需复制任何文件。未安装时服务正常运行，并在知识库 Tab 显示安装引导。

### 知识库功能

- **文件列表浏览** — 浏览 `viking://` 虚拟文件系统，支持目录导航
- **文件内容查看** — 内置 Markdown 渲染器，直接在浏览器查看知识库文件
- **文件上传** — 拖拽或点击上传，支持多文件，自动建立向量索引
- **文件删除** — 前端二次确认 + 操作结果反馈
- **语义搜索** — 全文语义检索，支持 `{ok, result: {memories, resources, skills}}` 格式
- **实时对话 KB 开关** — 输入框旁 📚 按钮，随时切换是否启用知识库 RAG 增强

### 新用户引导

未安装 openviking 时，访问知识库 Tab 自动显示安装步骤引导。
可通过 `?mock_no_viking=1` 参数模拟未安装状态，用于测试引导页面。

## 文件结构

```
server.py      # FastAPI 服务 — nanobot 的 Web 层
index.html     # Web 控制台（单文件，零依赖）
screenshots/   # 示例截图
```

## 技术栈

- **server.py** — FastAPI + Uvicorn，直接导入 nanobot 内部模块
- **index.html** — 原生 JS、CSS 自定义属性、WebSocket API、[marked.js](https://github.com/markedjs/marked)（CDN）

## SiliconFlow 免费模型

使用 [SiliconFlow（硅基流动）](https://siliconflow.cn) 的用户：Viking 知识库集成用到的向量模型（`BAAI/bge-m3`）和视觉模型（`DeepSeek-OCR`）均在**免费额度**内可用。

推荐链接注册可获额外赠送额度：**https://cloud.siliconflow.cn/i/UzI0F3Xv**

## 许可证

MIT
