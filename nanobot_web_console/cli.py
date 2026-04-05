"""CLI entry point for nanobot-web-console."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="nanobot-console",
        description="nanobot Web Console — HTTP API and web UI for nanobot",
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=18790,
        help="listen port (default: 18790)",
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="enable auto-reload for development",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="show version and exit",
    )
    args = parser.parse_args()

    if args.version:
        from nanobot_web_console import __version__
        print(f"nanobot-web-console {__version__}")
        sys.exit(0)

    import uvicorn
    uvicorn.run(
        "nanobot_web_console.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
