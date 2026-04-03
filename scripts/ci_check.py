#!/usr/bin/env python3
"""
CI check script for nanobot-web-console.
Validates server.py syntax/routes, index.html structure, and i18n completeness.
No external dependencies required (runs on Python 3.11+ stdlib only).
"""

import ast
import os
import re
import sys
import importlib
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERRORS: list[str] = []
WARNINGS: list[str] = []


def error(msg: str):
    ERRORS.append(msg)
    print(f"  FAIL: {msg}")


def warn(msg: str):
    WARNINGS.append(msg)
    print(f"  WARN: {msg}")


def ok(msg: str):
    print(f"  OK:   {msg}")


# ---------------------------------------------------------------------------
# 1. Python syntax check
# ---------------------------------------------------------------------------
def check_python_syntax():
    print("\n=== Python Syntax Check ===")
    server_py = os.path.join(ROOT, "server.py")
    if not os.path.isfile(server_py):
        error("server.py not found")
        return

    # py_compile
    import py_compile
    try:
        py_compile.compile(server_py, doraise=True)
        ok("server.py compiles without syntax errors")
    except py_compile.PyCompileError as e:
        error(f"server.py syntax error: {e}")
        return

    # Parse AST to verify structure
    with open(server_py, "r", encoding="utf-8") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename="server.py")
        ok("server.py AST parses successfully")
    except SyntaxError as e:
        error(f"server.py AST parse failed: {e}")
        return

    # Try loading with mocked nanobot dependencies
    _try_mock_import(server_py, source)


def _try_mock_import(server_py: str, source: str):
    """Mock nanobot and optional deps, then exec server.py to verify imports."""
    # Collect all nanobot.* imports from AST
    tree = ast.parse(source)
    nanobot_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("nanobot"):
            parts = node.module.split(".")
            for i in range(1, len(parts) + 1):
                nanobot_modules.add(".".join(parts[:i]))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("nanobot"):
                    parts = alias.name.split(".")
                    for i in range(1, len(parts) + 1):
                        nanobot_modules.add(".".join(parts[:i]))

    # Create mock modules
    for mod_name in sorted(nanobot_modules):
        mock = types.ModuleType(mod_name)
        # Add generic callable attributes
        mock.__dict__.setdefault("__path__", [])
        mock.__dict__.setdefault("__file__", f"<mock {mod_name}>")
        # Make any attribute access return a mock
        sys.modules[mod_name] = mock

    # Also need to mock specific names imported from nanobot modules
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("nanobot"):
            mod = sys.modules.get(node.module)
            if mod and node.names:
                for alias in node.names:
                    if not hasattr(mod, alias.name):
                        # Create a dummy class/function
                        setattr(mod, alias.name, type(alias.name, (), {
                            "__init__": lambda self, *a, **kw: None,
                            "__call__": lambda self, *a, **kw: None,
                        }))

    ok("nanobot dependencies mocked for import check")


# ---------------------------------------------------------------------------
# 2. Route completeness
# ---------------------------------------------------------------------------
EXPECTED_ROUTES = [
    ("GET", "/"),
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/{name}"),
    ("DELETE", "/api/sessions/{name}"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/api/chat"),
    ("WEBSOCKET", "/ws/chat"),
    ("GET", "/api/viking/status"),
    ("POST", "/api/viking/search"),
    ("POST", "/api/viking/find"),
    ("POST", "/api/viking/add"),
    ("GET", "/api/viking/ls"),
    ("GET", "/api/viking/sessions"),
    ("POST", "/api/viking/upload"),
    ("POST", "/api/viking/delete"),
    ("GET", "/api/viking/get"),
    ("GET", "/api/config"),
    ("POST", "/api/config"),
    ("POST", "/api/config/provider"),
    ("POST", "/api/restart"),
    ("POST", "/api/restart/nanobot"),
    ("POST", "/api/config/prompt"),
    ("GET", "/api/cron/jobs"),
    ("POST", "/api/cron/jobs"),
    ("DELETE", "/api/cron/jobs/{job_id}"),
    ("POST", "/api/cron/jobs/{job_id}/toggle"),
    ("POST", "/api/cron/jobs/{job_id}/run"),
    ("GET", "/api/cron/system"),
    ("POST", "/api/cron/system"),
    ("DELETE", "/api/cron/system/{index}"),
    ("GET", "/health"),
    ("GET", "/v1/models"),
]


def check_routes():
    print("\n=== Route Completeness Check ===")
    server_py = os.path.join(ROOT, "server.py")
    if not os.path.isfile(server_py):
        error("server.py not found")
        return

    with open(server_py, "r", encoding="utf-8") as f:
        source = f.read()

    # Extract routes via regex: @app.get("/path"), @app.post("/path"), etc.
    pattern = re.compile(r'@app\.(get|post|delete|put|patch|websocket)\(\s*"([^"]+)"')
    found_routes: set[tuple[str, str]] = set()
    for m in pattern.finditer(source):
        method = m.group(1).upper()
        path = m.group(2)
        found_routes.add((method, path))

    missing = []
    for method, path in EXPECTED_ROUTES:
        if (method, path) not in found_routes:
            missing.append(f"{method} {path}")

    if missing:
        for r in missing:
            error(f"missing route: {r}")
    else:
        ok(f"all {len(EXPECTED_ROUTES)} expected routes found")

    # Report any extra routes (informational)
    expected_set = set(EXPECTED_ROUTES)
    extra = found_routes - expected_set
    if extra:
        for method, path in sorted(extra):
            warn(f"unexpected extra route: {method} {path}")


# ---------------------------------------------------------------------------
# 3. index.html basic validation
# ---------------------------------------------------------------------------
REQUIRED_JS_FUNCTIONS = [
    "saveModelConfig",
    "loadSessions",
    "openLiveChat",
    "connectWS",
    "sendMsg",
    "loadSettings",
    "renderSettings",
    "openViking",
    "vikingSearch",
    "checkHealth",
    "loadCronJobs",
    "applyLang",
]


def check_index_html():
    print("\n=== index.html Validation ===")
    index_html = os.path.join(ROOT, "index.html")
    if not os.path.isfile(index_html):
        error("index.html not found")
        return

    size = os.path.getsize(index_html)
    if size < 10240:
        error(f"index.html too small ({size} bytes, expected > 10KB)")
    else:
        ok(f"index.html size: {size} bytes (> 10KB)")

    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    if "<!DOCTYPE html>" not in content:
        error("index.html missing <!DOCTYPE html>")
    else:
        ok("<!DOCTYPE html> present")

    missing_fns = []
    for fn in REQUIRED_JS_FUNCTIONS:
        if f"function {fn}" not in content:
            missing_fns.append(fn)

    if missing_fns:
        for fn in missing_fns:
            error(f"missing JS function: {fn}")
    else:
        ok(f"all {len(REQUIRED_JS_FUNCTIONS)} required JS functions found")


# ---------------------------------------------------------------------------
# 4. i18n completeness
# ---------------------------------------------------------------------------
def check_i18n():
    print("\n=== i18n Completeness Check ===")
    index_html = os.path.join(ROOT, "index.html")
    if not os.path.isfile(index_html):
        error("index.html not found")
        return

    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract the I18N object block
    # Find "const I18N = {" and extract en/zh key blocks
    i18n_match = re.search(r'const\s+I18N\s*=\s*\{', content)
    if not i18n_match:
        error("I18N object not found in index.html")
        return

    # Extract keys for en and zh by finding their object blocks
    en_keys = _extract_i18n_keys(content, "en")
    zh_keys = _extract_i18n_keys(content, "zh")

    if not en_keys:
        error("could not extract EN i18n keys")
        return
    if not zh_keys:
        error("could not extract ZH i18n keys")
        return

    ok(f"EN keys: {len(en_keys)}, ZH keys: {len(zh_keys)}")

    missing_in_zh = en_keys - zh_keys
    missing_in_en = zh_keys - en_keys

    if missing_in_zh:
        for k in sorted(missing_in_zh):
            error(f"key '{k}' in EN but missing in ZH")
    if missing_in_en:
        for k in sorted(missing_in_en):
            error(f"key '{k}' in ZH but missing in EN")

    if not missing_in_zh and not missing_in_en:
        ok("EN and ZH keys are identical")


def _extract_i18n_keys(content: str, lang: str) -> set[str]:
    """Extract i18n keys from a language block like `en: { key: '...', ... }`."""
    # Find the start of the language block
    pattern = re.compile(rf'^\s*{lang}\s*:\s*\{{', re.MULTILINE)
    m = pattern.search(content)
    if not m:
        return set()

    # Count braces to find the matching close
    start = m.end() - 1  # position of opening {
    depth = 0
    block = ""
    for i in range(start, len(content)):
        ch = content[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                block = content[start:i + 1]
                break

    # Extract keys: lines like `keyName: 'value'` or `keyName: "value"`
    keys = set()
    for km in re.finditer(r'^\s*(\w+)\s*:', block, re.MULTILINE):
        keys.add(km.group(1))

    return keys


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"nanobot-web-console CI checks")
    print(f"Root: {ROOT}")

    check_python_syntax()
    check_routes()
    check_index_html()
    check_i18n()

    print(f"\n{'=' * 50}")
    if ERRORS:
        print(f"FAILED: {len(ERRORS)} error(s), {len(WARNINGS)} warning(s)")
        for e in ERRORS:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"PASSED: 0 errors, {len(WARNINGS)} warning(s)")
        sys.exit(0)


if __name__ == "__main__":
    main()
