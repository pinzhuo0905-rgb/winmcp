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
    proc = subprocess.run(
        [sys.executable, "-m", "winmcp.cli", "serve", *(args or [])],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        cwd=str(ROOT),
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise ConformanceError(f"server exited {proc.returncode}\nstderr:\n{proc.stderr}")
    out: list[dict] = []
    for line in proc.stdout.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ConformanceError(f"non-JSON on stdout: {line[:120]!r}") from exc
    return out


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        raise ConformanceError(label)


def main() -> int:
    ap = argparse.ArgumentParser(description="MCP conformance check")
    ap.add_argument("--tools", nargs="*", default=[], help="pass --tools through to the server")
    args = ap.parse_args()

    print("MCP protocol conformance")
    print("-" * 60)

    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "conformance", "version": "1.0"},
        },
    }
    responses = _exchange(
        [
            init,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "DisplayInventory", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "NoSuchTool", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 6, "method": "no/such/method"},
        ],
        args.tools,
    )

    # the initialized notification must not produce a response
    check("notification produces no response", len(responses) == 6, f"got {len(responses)}")

    by_id = {r.get("id"): r for r in responses}

    check("initialize returns serverInfo", "result" in by_id.get(1, {}))
    info = by_id[1]["result"]["serverInfo"]
    check("server identifies as winmcp", info.get("name") == "winmcp", str(info))
    check("protocol version is reported", bool(by_id[1]["result"].get("protocolVersion")))
    check("tools capability declared", "tools" in by_id[1]["result"]["capabilities"])

    tools = by_id[2]["result"]["tools"]
    expected = len(args.tools) if args.tools else len(TOOL_NAMES)
    check(f"tools/list returns {expected} tools", len(tools) == expected, f"got {len(tools)}")
    for t in tools:
        check(f"{t['name']}: schema is an object", t["inputSchema"].get("type") == "object")
        check(f"{t['name']}: has a description", bool(t.get("description")))

    check("ping returns an empty result", by_id[3].get("result") == {})

    call = by_id[4]["result"]
    check("tools/call succeeds", call.get("isError") is False)
    check("result carries text content", call["content"][0]["type"] == "text")

    check("unknown tool reports isError", by_id[5]["result"].get("isError") is True)
    check("unknown method returns a JSON-RPC error", "error" in by_id[6])
    check("unknown method uses code -32601", by_id[6]["error"].get("code") == -32601)

    # image content
    img = _exchange(
        [init, {"jsonrpc": "2.0", "method": "notifications/initialized"},
         {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
          "params": {"name": "Screenshot", "arguments": {}}}]
    )
    blocks = img[1]["result"]["content"]
    images = [b for b in blocks if b.get("type") == "image"]
    check("Screenshot returns an image block", bool(images))
    raw = base64.b64decode(images[0]["data"])
    check("image payload is a PNG", raw[:8] == b"\x89PNG\r\n\x1a\n")
    check("image mimeType is image/png", images[0]["mimeType"] == "image/png")

    print("-" * 60)
    print("all conformance checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
