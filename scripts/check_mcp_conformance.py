"""MCP protocol conformance check.

Spawns ``winmcp serve`` and exercises the handshake the way a real MCP client would,
verifying that the server speaks JSON-RPC 2.0 correctly over stdio.

    python scripts/check_mcp_conformance.py
    python scripts/check_mcp_conformance.py --tools Screenshot Click

Exits non-zero on the first failure, with the reason.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from winmcp.tools import TOOL_NAMES  # noqa: E402


class ConformanceError(AssertionError):
    pass


def _exchange(messages: list[dict], args: list[str] | None = None) -> list[dict]:
    payload = "\n".join(json.dumps(m) for m in messages) + "\n"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "winmcp.cli", "serve", *(args or [])],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConformanceError(
            f"server did not exit within 90s; partial stdout:\n{(exc.stdout or '')[:2000]}"
        ) from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if not stdout.strip():
        raise ConformanceError(
            f"server produced no output (exit {proc.returncode})\nstderr:\n{stderr[-2000:]}"
        )

    out: list[dict] = []
    for line in stdout.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ConformanceError(
                    f"non-JSON on stdout: {line[:160]!r}\nstderr:\n{stderr[-2000:]}"
                ) from exc
    return out


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        raise ConformanceError(label)


INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "conformance", "version": "1.0"},
    },
}
READY = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def phase(name: str, messages: list[dict], args: list[str] | None) -> dict[int, dict]:
    """Run one exchange and return the responses keyed by request id.

    Phases are deliberately small. A single large batch makes a dropped response
    hard to attribute; a few small batches pinpoint which request went unanswered.
    """
    print(f"\n[{name}]")
    responses = _exchange([INIT, READY, *messages], args)
    by_id = {r.get("id"): r for r in responses if r.get("id") is not None}

    expected = [m["id"] for m in messages]
    missing = [i for i in expected if i not in by_id]
    if missing:
        raise ConformanceError(
            f"{name}: no response for id(s) {missing}; "
            f"received {sorted(by_id)}; extra={[r for r in responses if r.get('id') is None]}"
        )
    print(f"  received responses for ids {sorted(by_id)}")
    return by_id


def main() -> int:
    ap = argparse.ArgumentParser(description="MCP conformance check")
    ap.add_argument("--tools", nargs="*", default=[], help="pass --tools through to the server")
    args = ap.parse_args()

    print("MCP protocol conformance")
    print("-" * 60)

    # --- handshake ---
    hs = phase(
        "handshake",
        [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ],
        args.tools,
    )
    check("initialize returns serverInfo", "result" in hs[1], str(hs[1])[:200])
    info = hs[1]["result"]["serverInfo"]
    check("server identifies as winmcp", info.get("name") == "winmcp", str(info))
    check("protocol version is reported", bool(hs[1]["result"].get("protocolVersion")))
    check("tools capability declared", "tools" in hs[1]["result"]["capabilities"])

    tools = hs[2]["result"]["tools"]
    expected = len(args.tools) if args.tools else len(TOOL_NAMES)
    check(f"tools/list returns {expected} tools", len(tools) == expected, f"got {len(tools)}")
    for t in tools:
        check(f"{t['name']}: schema is an object", t["inputSchema"].get("type") == "object")
        check(f"{t['name']}: has a description", bool(t.get("description")))

    check("ping returns an empty result", hs[3].get("result") == {})

    # --- error handling ---
    errs = phase(
        "error handling",
        [
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "NoSuchTool", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "no/such/method"},
        ],
        args.tools,
    )
    check("unknown tool reports isError", errs[4]["result"].get("isError") is True)
    check("unknown method returns a JSON-RPC error", "error" in errs[5])
    check("unknown method uses code -32601", errs[5]["error"].get("code") == -32601)

    # --- a real tool call ---
    calls = phase(
        "tool execution",
        [
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "DisplayInventory", "arguments": {}}},
        ],
        args.tools,
    )
    call = calls[6]["result"]
    check("tools/call succeeds", call.get("isError") is False, str(call)[:200])
    check("result carries text content", call["content"][0]["type"] == "text")

    # --- image content ---
    imgs = phase(
        "image content",
        [
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "Screenshot", "arguments": {}}},
        ],
        args.tools,
    )
    blocks = imgs[7]["result"]["content"]
    images = [b for b in blocks if b.get("type") == "image"]
    check("Screenshot returns an image block", bool(images))
    raw = base64.b64decode(images[0]["data"])
    check("image payload is a PNG", raw[:8] == b"\x89PNG\r\n\x1a\n")
    check("image mimeType is image/png", images[0]["mimeType"] == "image/png")

    print()
    print("-" * 60)
    print("all conformance checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
