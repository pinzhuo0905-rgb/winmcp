"""MCP stdio server: exposes the 18 tools to any MCP client.

winmcp runs the tools in-process, so this server exists for compatibility rather than
for speed — it lets existing MCP clients (Claude Desktop, Cursor, VS Code) use winmcp
as a drop-in replacement for a separate desktop-control server.

Protocol: JSON-RPC 2.0, newline-delimited, over stdin/stdout.
"""

from __future__ import annotations

import base64
import json
import sys
from typing import Any

from . import __version__
from .tools import Registry, ToolError, build_registry

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "winmcp"

#: Tools this server exposes. ``--tools`` narrows it; ``--exclude-tools`` removes.
DEFAULT_TOOLS: tuple[str, ...] = ()


def _content(result: Any) -> list[dict[str, Any]]:
    """Convert a ToolResult into MCP content blocks."""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": result.text}]
    if result.image_png:
        blocks.append(
            {
                "type": "image",
                "data": base64.b64encode(result.image_png).decode("ascii"),
                "mimeType": "image/png",
            }
        )
    return blocks


def _select_tools(reg: Registry, include: list[str], exclude: list[str]) -> Registry:
    names = reg.names()
    if include:
        unknown = set(include) - set(names)
        if unknown:
            raise SystemExit(f"unknown tool(s) in --tools: {sorted(unknown)}")
        names = [n for n in names if n in set(include)]
    if exclude:
        drop = set(exclude)
        names = [n for n in names if n not in drop]
    return reg.subset(names)


def handle(
    request: dict[str, Any],
    reg: Registry,
    *,
    log: bool = False,
) -> dict[str, Any] | None:
    """Handle one JSON-RPC request. Returns None for notifications."""
    method = request.get("method")
    rid = request.get("id")

    if log:
        print(f"[winmcp] {method}", file=sys.stderr, flush=True)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": reg.schemas()}}

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            result = reg.invoke(name, args)
        except ToolError as exc:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": _content(result), "isError": False},
        }

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    log: bool = False,
) -> int:
    """Run the stdio loop until stdin closes."""
    reg = _select_tools(build_registry(), list(include or []), list(exclude or []))

    if log:
        print(
            f"[winmcp] serving {len(reg.names())} tools: {', '.join(reg.names())}",
            file=sys.stderr,
            flush=True,
        )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print("[winmcp] skipped a non-JSON line on stdin", file=sys.stderr, flush=True)
            continue

        response = handle(request, reg, log=log)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    return 0


__all__ = ["DEFAULT_TOOLS", "PROTOCOL_VERSION", "SERVER_NAME", "handle", "serve"]
