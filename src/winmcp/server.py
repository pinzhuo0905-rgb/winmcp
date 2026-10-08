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
import traceback
from typing import Any

from . import __version__
from .tools import Registry, ToolError, build_registry

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "winmcp"

#: Tools this server exposes. ``--tools`` narrows it; ``--exclude-tools`` removes.
DEFAULT_TOOLS: tuple[str, ...] = ()


def force_utf8_stdio() -> None:
    """Pin stdout/stderr to UTF-8.

    This matters more than it looks. On Windows the default encoding of a piped
    stdout is the ANSI code page (cp936 on a Chinese install, cp1252 on a Western
    one), not UTF-8. Tool errors and tool output routinely contain non-ASCII text —
    a path with an accented character, a Chinese error message — and writing those
    as cp936 produces bytes that any conforming MCP client, reading UTF-8, cannot
    decode. The client then loses the entire response stream.

    Pinning the encoding here makes the wire format independent of the host locale.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - stream not reconfigurable
                pass


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
        except Exception as exc:
            # ToolError is the expected failure. Anything else is a bug in a tool —
            # still reported as a normal MCP error result so the client can continue.
            detail = str(exc) if isinstance(exc, ToolError) else f"{type(exc).__name__}: {exc}"
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": detail}],
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
    force_utf8_stdio()
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

        # A single bad tool call must never take the server down. Anything that
        # escapes handle() becomes a JSON-RPC error response instead of a crash —
        # otherwise one failing tool ends the whole session.
        try:
            response = handle(request, reg, log=log)
        except Exception as exc:
            rid = request.get("id")
            if log:
                traceback.print_exc(file=sys.stderr)
            response = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32603,
                    "message": f"internal error: {type(exc).__name__}: {exc}",
                },
            }
            if rid is None:
                continue  # a notification has no reply

        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    return 0


__all__ = ["DEFAULT_TOOLS", "PROTOCOL_VERSION", "SERVER_NAME", "handle", "serve"]
