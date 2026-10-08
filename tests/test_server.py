"""MCP server tests — protocol shape and dispatch over JSON-RPC."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

from winmcp import __version__
from winmcp.server import PROTOCOL_VERSION, handle
from winmcp.tools import build_registry

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")


def _req(method: str, params: dict | None = None, rid: int = 1) -> dict:
    out = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        out["params"] = params
    return out


# -- protocol ---------------------------------------------------------------
def test_initialize_reports_server_identity() -> None:
    resp = handle(_req("initialize"), build_registry())
    result = resp["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "winmcp"
    assert result["serverInfo"]["version"] == __version__


def test_initialize_declares_tools_capability() -> None:
    resp = handle(_req("initialize"), build_registry())
    assert "tools" in resp["result"]["capabilities"]


def test_initialized_notification_has_no_response() -> None:
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, build_registry()) is None


def test_ping_returns_empty_result() -> None:
    assert handle(_req("ping"), build_registry())["result"] == {}


def test_tools_list_returns_all_eighteen() -> None:
    resp = handle(_req("tools/list"), build_registry())
    assert len(resp["result"]["tools"]) == 18


def test_tools_list_entries_have_the_mcp_shape() -> None:
    tools = handle(_req("tools/list"), build_registry())["result"]["tools"]
    for t in tools:
        assert set(t) == {"name", "description", "inputSchema"}
        assert t["inputSchema"]["type"] == "object"


def test_unknown_method_returns_jsonrpc_error() -> None:
    resp = handle(_req("no/such/method"), build_registry())
    assert resp["error"]["code"] == -32601


# -- tools/call -------------------------------------------------------------
def test_tools_call_success() -> None:
    resp = handle(
        _req("tools/call", {"name": "DisplayInventory", "arguments": {}}), build_registry()
    )
    result = resp["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert "display" in result["content"][0]["text"].lower()


def test_tools_call_unknown_tool_is_an_error_not_a_crash() -> None:
    resp = handle(_req("tools/call", {"name": "NoSuchTool", "arguments": {}}), build_registry())
    assert resp["result"]["isError"] is True


def test_tools_call_bad_arguments_are_reported() -> None:
    resp = handle(
        _req("tools/call", {"name": "FileSystem", "arguments": {"mode": "read"}}), build_registry()
    )
    assert resp["result"]["isError"] is True


def test_tools_call_returns_image_as_base64() -> None:
    resp = handle(_req("tools/call", {"name": "Screenshot", "arguments": {}}), build_registry())
    blocks = resp["result"]["content"]
    images = [b for b in blocks if b["type"] == "image"]
    assert images, "Screenshot must return an image block"
    raw = base64.b64decode(images[0]["data"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert images[0]["mimeType"] == "image/png"


def test_tools_call_missing_params_is_handled() -> None:
    resp = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}, build_registry())
    assert resp["result"]["isError"] is True


def test_unexpected_tool_exception_does_not_escape() -> None:
    """Regression: an exception that is not a ToolError used to kill the server.

    One broken tool must not end the session — it should come back as a normal
    error result so the client can keep going.
    """
    from winmcp.tools import Risk, Tool

    reg = build_registry()

    def boom(**_kwargs: object):
        raise RuntimeError("something unexpected")

    reg.add(Tool("Boom", "always fails", {"type": "object", "properties": {}}, boom, Risk.LOW))
    resp = handle(_req("tools/call", {"name": "Boom", "arguments": {}}), reg)
    assert resp["result"]["isError"] is True
    assert "RuntimeError" in resp["result"]["content"][0]["text"]


def test_handle_returns_error_rather_than_raising() -> None:
    """``handle`` must never raise for a well-formed request."""
    for method in ("tools/list", "ping", "initialize", "no/such/method"):
        handle(_req(method), build_registry())


def test_server_emits_utf8_regardless_of_locale() -> None:
    """Regression: a non-ASCII tool error used to be written in the console code page.

    Tool errors and paths are not ASCII. If the server writes them using the ANSI
    code page, a client reading UTF-8 cannot decode the stream and loses every
    response in the batch — not just the offending one.
    """
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    # Do not let the environment paper over the bug: with these set the interpreter
    # would force UTF-8 for us and the test would pass even if the server did not.
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)

    payload = (
        "\n".join(
            json.dumps(m)
            for m in [
                _req("initialize", rid=1),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                # an unknown tool produces a non-ASCII error message
                _req("tools/call", {"name": "NoSuchTool", "arguments": {}}, rid=2),
                _req("ping", rid=3),
            ]
        )
        + "\n"
    ).encode("utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "winmcp.cli", "serve"],
        input=payload,  # bytes in, bytes out — we assert on the encoding itself
        capture_output=True,
        timeout=90,
        cwd=str(ROOT),
        env=env,
    )
    decoded = proc.stdout.decode("utf-8")  # raises UnicodeDecodeError on cp936 output
    responses = [json.loads(line) for line in decoded.splitlines() if line.strip()]
    by_id = {r.get("id"): r for r in responses}
    assert by_id[2]["result"]["isError"] is True
    assert by_id[3].get("result") == {}, "the batch must not be truncated by one bad message"


# -- end-to-end over stdio --------------------------------------------------
def _run_server(messages: list[dict], args: list[str] | None = None) -> list[dict]:
    import os

    payload = "\n".join(json.dumps(m) for m in messages) + "\n"
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    proc = subprocess.run(
        [sys.executable, "-m", "winmcp.cli", "serve", *(args or [])],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        cwd=str(ROOT),
        env=env,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_stdio_handshake_and_listing() -> None:
    responses = _run_server(
        [
            _req("initialize", rid=1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _req("tools/list", rid=2),
        ]
    )
    assert len(responses) == 2
    assert responses[0]["id"] == 1
    assert len(responses[1]["result"]["tools"]) == 18


def test_stdio_tool_whitelist_trims_the_surface() -> None:
    """--tools must narrow what the server advertises."""
    responses = _run_server(
        [_req("initialize", rid=1), _req("tools/list", rid=2)],
        args=["--tools", "Screenshot", "Click"],
    )
    names = [t["name"] for t in responses[1]["result"]["tools"]]
    assert names == ["Screenshot", "Click"]


def test_stdio_exclude_tools_removes_from_the_surface() -> None:
    responses = _run_server(
        [_req("initialize", rid=1), _req("tools/list", rid=2)],
        args=["--exclude-tools", "FileSystem", "Process"],
    )
    names = [t["name"] for t in responses[1]["result"]["tools"]]
    assert "FileSystem" not in names
    assert "Screenshot" in names


def test_stdio_survives_a_failing_tool() -> None:
    """A failing tool call must not end the session — the next request still works."""
    responses = _run_server(
        [
            _req("initialize", rid=1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _req("tools/call", {"name": "NoSuchTool", "arguments": {}}, rid=2),
            _req("ping", rid=3),
        ]
    )
    by_id = {r.get("id"): r for r in responses}
    assert by_id[2]["result"]["isError"] is True
    assert by_id[3].get("result") == {}, "server should still answer after a failed call"


def test_stdio_survives_garbage_on_stdin() -> None:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    proc = subprocess.run(
        [sys.executable, "-m", "winmcp.cli", "serve"],
        input="not json at all\n" + json.dumps(_req("ping", rid=7)) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        cwd=str(ROOT),
        env=env,
    )
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert responses and responses[0]["id"] == 7
