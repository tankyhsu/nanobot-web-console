#!/usr/bin/env python3
"""Backward-compatible entry point.

Preferred usage:
    pip install nanobot-web-console
    nanobot-console

Or from a cloned repo:
    pip install .
    nanobot-console
"""

from nanobot_web_console.server import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18790)
