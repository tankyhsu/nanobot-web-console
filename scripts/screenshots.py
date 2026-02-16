"""Generate desensitized screenshots of nanobot web console for README."""
import asyncio
import json
import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from playwright.async_api import async_playwright

SCREENSHOTS_DIR = "screenshots"
PORT = 18799

MOCK_SESSIONS = [
    {"name": "feishu_group_chat", "display": "group_chat", "messages": 128, "updated": "2026-02-16T10:30:00"},
    {"name": "ws_ws_console", "display": "ws_console", "messages": 42, "updated": "2026-02-16T13:22:00"},
    {"name": "api_home_assistant", "display": "home_assistant", "messages": 15, "updated": "2026-02-15T22:10:00"},
    {"name": "ws_iot_device1", "display": "iot_device1", "messages": 8, "updated": "2026-02-15T18:45:00"},
    {"name": "feishu_dm_test", "display": "dm_test", "messages": 67, "updated": "2026-02-14T09:00:00"},
    {"name": "api_voice_assistant", "display": "voice_assistant", "messages": 203, "updated": "2026-02-13T14:20:00"},
    {"name": "cli_debug", "display": "debug", "messages": 5, "updated": "2026-02-12T11:00:00"},
]

MOCK_MESSAGES = [
    {"role": "user", "content": "Help me check the disk usage and memory status", "timestamp": "2026-02-16T13:20:00"},
    {"role": "assistant", "content": "I'll check the disk and memory status for you.\n\n**Disk Usage:**\n| Filesystem | Size | Used | Avail | Use% |\n|---|---|---|---|---|\n| /dev/mmcblk0p2 | 29G | 2.7G | 25G | 10% |\n\n**Memory:**\n- Total: 3.7 GB\n- Used: 1.2 GB (32%)\n- Available: 2.1 GB\n\nEverything looks healthy! Disk is at 10% and memory usage is moderate at 32%.", "timestamp": "2026-02-16T13:20:15"},
    {"role": "user", "content": "What services are currently running?", "timestamp": "2026-02-16T13:21:00"},
    {"role": "assistant", "content": "Here are the active services:\n\n1. **nanobot** - AI Agent core (active)\n2. **nanobot-api** - HTTP/WebSocket API server on port 18790\n3. **voice-assistant** - Voice interaction service\n4. **OpenViking** - Knowledge base (AGFS on port 1833)\n5. **ShellCrash** - Network proxy\n6. **tailscaled** - Tailscale VPN\n7. **sshd** - SSH server\n\nAll core services are running normally.", "timestamp": "2026-02-16T13:21:30"},
]

MOCK_HEALTH = {"status": "ok", "agent_ready": True, "viking_ready": True}

MOCK_VIKING_LS = {"result": "\u76ee\u5f55 viking://resources/:\n  \U0001f4c1 rpi_services (0b)\n  \U0001f4c1 project_docs (0b)\n  \U0001f4c4 setup_guide.md (4523b)\n  \U0001f4c4 network_config.md (2108b)"}

MOCK_CONFIG = {
    "model": "openai/MiniMax-M2.5",
    "max_tokens": 8192,
    "temperature": 0.7,
    "max_tool_iterations": 50,
    "provider": "openai",
    "workspace": "/root/.nanobot/workspace",
    "tools": [
        {"name": "read_file", "description": "Read the contents of a file at the given path.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "write_file", "description": "Write content to a file. Creates parent directories if needed.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
        {"name": "edit_file", "description": "Edit a file by replacing old_text with new_text.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
        {"name": "list_dir", "description": "List the contents of a directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "exec", "description": "Execute a shell command and return its output.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"name": "web_search", "description": "Search the web. Returns titles, URLs, and snippets.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "count": {"type": "integer"}}, "required": ["query"]}},
        {"name": "web_fetch", "description": "Fetch URL and extract readable content (HTML to markdown).", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
        {"name": "message", "description": "Send a message to the user via a channel.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
        {"name": "spawn", "description": "Spawn a subagent to handle a background task.", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}},
        {"name": "cron", "description": "Schedule reminders and recurring tasks.", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}},
    ],
    "skills": [
        {"name": "memos-logger", "description": "Auto-log quick memos to Memos service when user says keywords like 'note this' or 'quick memo'.", "content": "# Memos Logger\n\nAutomatically creates public memos..."},
    ],
    "prompt_files": {
        "SOUL.md": "# Soul\n\nI am an AI assistant running on Raspberry Pi 4B...\n\n## Personality\n- Pragmatic and efficient\n- Explain intent before executing\n- Ask when uncertain\n- Default to Chinese\n",
        "AGENTS.md": "# Agent Guidelines\n\n## Environment\n- Host: Raspberry Pi 4B, 4GB RAM\n- Identity: root user\n\n## Workflow\n1. Understand intent\n2. Explain planned action\n3. Execute with appropriate tools\n4. Report results clearly\n",
        "USER.md": "# User Info\n\n- Language: Chinese\n- Tech level: Advanced Linux user\n- Communication: Direct, no fluff\n",
    },
    "memory": "# Long-term Memory\n\n## System Config\n- OS: Debian Trixie arm64\n- LLM: MiniMax M2.5\n- Feishu channel enabled\n",
}

MOCK_VIKING_SEARCH = {"result": "\u641c\u7d22 'raspberry pi' \u627e\u5230 3 \u6761\u7ed3\u679c:\n\n[\u8d44\u6e90:rpi_services] Raspberry Pi 4B running core services including nanobot AI agent, voice assistant, and OpenViking knowledge base. Hardware: BCM2711 SoC, 4GB RAM, 32GB SD card.\n\n[\u8d44\u6e90:setup_guide] Initial setup guide covering system configuration, service deployment, network settings, and Tailscale VPN integration.\n\n[\u8bb0\u5fc6] The device runs Debian 13 (Trixie) with kernel 6.12, connected via both LAN and Tailscale VPN."}


def start_server():
    """Serve index.html on localhost."""
    handler = SimpleHTTPRequestHandler
    httpd = HTTPServer(("127.0.0.1", PORT), handler)
    httpd.serve_forever()


async def mock_route(route, request):
    """Intercept API calls and return mock data."""
    url = request.url
    if "/health" in url:
        await route.fulfill(json=MOCK_HEALTH)
    elif "/api/sessions/" in url and request.method == "GET":
        await route.fulfill(json=MOCK_MESSAGES)
    elif "/api/sessions" in url and request.method == "GET":
        await route.fulfill(json=MOCK_SESSIONS)
    elif "/api/viking/status" in url:
        await route.fulfill(json={"viking_ready": True, "data_dir": "/data/viking"})
    elif "/api/viking/ls" in url:
        await route.fulfill(json=MOCK_VIKING_LS)
    elif "/api/viking/search" in url or "/api/viking/find" in url:
        await route.fulfill(json=MOCK_VIKING_SEARCH)
    elif "/api/config" in url and request.method == "GET":
        await route.fulfill(json=MOCK_CONFIG)
    elif "/api/config" in url and request.method == "POST":
        await route.fulfill(json={"status": "updated", "changed": ["model"], "note": "Restart to apply"})
    else:
        await route.fulfill(json={})


async def take_screenshots():
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    base = f"http://127.0.0.1:{PORT}/index.html"

    async with async_playwright() as p:
        browser = await p.chromium.launch()

        # === 1. Dark theme - Session view ===
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.route("**/api/**", mock_route)
        await page.route("**/health", mock_route)
        await page.route("**/ws/**", lambda route, req: route.abort())  # no WS
        await page.goto(base)
        await page.wait_for_timeout(1500)
        # Click on ws_console session
        items = await page.query_selector_all('.session-item')
        if len(items) >= 2:
            await items[1].click()
            await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{SCREENSHOTS_DIR}/01-session-dark.png")
        print("1/7 Dark theme session view")

        # === 2. Light theme ===
        await page.click('#themeBtn')
        await page.wait_for_timeout(400)
        await page.screenshot(path=f"{SCREENSHOTS_DIR}/02-session-light.png")
        print("2/7 Light theme session view")

        # === 3. Live Chat with tool events ===
        await page.click('#themeBtn')  # back to dark
        await page.wait_for_timeout(200)
        await page.click('#liveChatBtn')
        await page.wait_for_timeout(500)
        # Inject live chat demo content
        await page.evaluate("""() => {
            const area = document.getElementById('chatArea');
            area.innerHTML = '';
            isLiveChat = true;

            area.insertAdjacentHTML('beforeend',
                '<div class="msg user"><div class="bubble">What\\'s the current CPU temperature?</div><div class="time">13:25:00</div></div>');

            area.insertAdjacentHTML('beforeend',
                '<div class="stream-event ev-thinking">Thinking... (iteration 1)</div>');

            if (typeof appendToolCall === 'function') {
                appendToolCall('exec', '{"command": "cat /sys/class/thermal/thermal_zone0/temp"}');
                appendToolResult('exec', '45200');

                area.insertAdjacentHTML('beforeend',
                    '<div class="stream-event ev-thinking">Thinking... (iteration 2)</div>');

                appendToolCall('exec', '{"command": "vcgencmd measure_clock arm"}');
                appendToolResult('exec', 'frequency(48)=1500000000');

                area.insertAdjacentHTML('beforeend',
                    '<div class="stream-event ev-thinking">Thinking... (iteration 3)</div>');

                appendToolCall('viking_search', '{"query": "CPU temperature thresholds", "limit": 3}');
                appendToolResult('viking_search', 'Search found 1 result:\\n[Resource:rpi_services] RPi 4B thermal throttling starts at 80\\u00b0C, max safe temp 85\\u00b0C.');
            }

            area.insertAdjacentHTML('beforeend',
                '<div class="msg assistant"><div class="bubble">' +
                marked.parse('The CPU temperature is **45.2\\u00b0C** \\u2014 well within safe range.\\n\\n- Current: 45.2\\u00b0C\\n- Throttle threshold: 80\\u00b0C\\n- CPU clock: 1.5 GHz (full speed)\\n\\nNo thermal throttling is occurring.') +
                '</div><div class="time">13:25:12</div></div>');

            area.scrollTop = area.scrollHeight;
        }""")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{SCREENSHOTS_DIR}/03-livechat-tools.png")
        print("3/7 Live Chat with tool events")

        # === 4. Expanded tool details ===
        toggles = await page.query_selector_all('.tool-event-header')
        for t in toggles[:4]:
            await t.click()
            await page.wait_for_timeout(100)
        await page.wait_for_timeout(300)
        await page.evaluate("document.getElementById('chatArea').scrollTop = 80")
        await page.wait_for_timeout(200)
        await page.screenshot(path=f"{SCREENSHOTS_DIR}/04-livechat-expanded.png")
        print("4/7 Live Chat expanded tool details")

        # === 5. Viking Knowledge Base browser ===
        await page.click('#liveChatBtn')  # close live
        await page.wait_for_timeout(300)
        await page.click('#vikingBtn')
        await page.wait_for_timeout(1200)
        await page.screenshot(path=f"{SCREENSHOTS_DIR}/05-viking-browser.png")
        print("5/7 Viking Knowledge Base browser")

        # === 6. Viking search ===
        search_input = await page.query_selector('#vikingSearchInput')
        if search_input:
            await search_input.fill('raspberry pi')
            btn = await page.query_selector('.viking-search button')
            if btn:
                await btn.click()
            await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{SCREENSHOTS_DIR}/06-viking-search.png")
        print("6/7 Viking search results")

        # === 7. Settings panel ===
        await page.click('#vikingBtn')  # close viking
        await page.wait_for_timeout(300)
        await page.click('#settingsBtn')
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{SCREENSHOTS_DIR}/07-settings.png")
        print("7/8 Settings panel")

        # === 8. Mobile view with session ===
        mobile = await browser.new_page(viewport={"width": 390, "height": 844})
        await mobile.route("**/api/**", mock_route)
        await mobile.route("**/health", mock_route)
        await mobile.route("**/ws/**", lambda route, req: route.abort())
        await mobile.goto(base)
        await mobile.wait_for_timeout(1500)
        # Click session directly (on mobile sidebar may not show, need hamburger)
        menu_btn = await mobile.query_selector('.menu-btn')
        if menu_btn:
            await menu_btn.click()
            await mobile.wait_for_timeout(500)
        items = await mobile.query_selector_all('.session-item')
        if len(items) >= 2:
            await items[1].click()
            await mobile.wait_for_timeout(1000)
        await mobile.screenshot(path=f"{SCREENSHOTS_DIR}/08-mobile.png")
        print("8/8 Mobile view")

        await browser.close()
        print(f"\nAll screenshots saved to {SCREENSHOTS_DIR}/")


# Start local HTTP server in background
server_thread = threading.Thread(target=start_server, daemon=True)
server_thread.start()

asyncio.run(take_screenshots())
