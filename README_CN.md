# nanobot Web Console

**[English](README.md) | [中文](README_CN.md)**

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

### 必需的 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查，返回 `{agent_ready, viking_ready}` |
| `/api/sessions` | GET | 列出所有会话 `[{name, display, messages, updated}]` |
| `/api/sessions/{name}` | GET | 获取会话消息 `[{role, content, timestamp}]` |
| `/api/sessions/{name}` | DELETE | 删除会话 |
| `/ws/chat` | WebSocket | 流式对话（协议见下） |
| `/api/config` | GET | 获取 Agent 配置（模型、工具、技能、提示词） |
| `/api/config` | POST | 更新模型配置 |
| `/api/config/prompt` | POST | 更新系统提示词文件 |
| `/api/viking/*` | * | 知识库接口 *（可选）* |

### WebSocket 协议

客户端发送：
```json
{"message": "用户消息", "session": "ws:device-id", "constraint": "可选约束"}
```

服务端依次推送事件：
```json
{"type": "thinking", "iteration": 1}
{"type": "tool_call", "name": "exec", "arguments": "{\"command\": \"df -h\"}"}
{"type": "tool_result", "name": "exec", "result": "Filesystem  Size  Used..."}
{"type": "final", "content": "磁盘使用情况是...", "session": "ws:device-id"}
{"type": "error", "message": "错误描述"}
```

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

## 文件结构

```
index.html          # 完整的 Web 控制台（单文件）
scripts/
  screenshots.py    # 截图生成脚本（playwright）
screenshots/        # README 示例截图
```

## SiliconFlow 免费模型

如果您使用 [SiliconFlow（硅基流动）](https://siliconflow.cn) 作为 LLM 服务商，Viking 知识库集成使用的向量模型（`BAAI/bge-m3`）和视觉模型（`DeepSeek-OCR`）均可在**免费额度**内使用。

通过推荐链接注册可获得额外赠送额度：**https://cloud.siliconflow.cn/i/UzI0F3Xv**

<img src="screenshots/siliconflow-qr.png" alt="SiliconFlow 二维码" width="200">

## 许可证

MIT
