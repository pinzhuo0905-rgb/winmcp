"""Command-line interface.

    winmcp serve                     run as an MCP stdio server
    winmcp tools                     list the available tools
    winmcp call <Tool> [--arg k=v]   invoke a tool once
    winmcp plan "<message>"          run the pre-response intent check
    winmcp bench                     measure startup, capture and recall cost
    winmcp info                      environment and capability report

Uses argparse from the standard library — the CLI must not add a dependency the
library itself does not need.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from . import __version__
from .agent import Agent, AgentConfig
from .server import serve as serve_mcp
from .tools import ToolError


def _parse_value(raw: str) -> Any:
    """Interpret a CLI value: JSON if it parses, otherwise a plain string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _parse_args(pairs: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--arg expects key=value, got {item!r}")
        key, _, value = item.partition("=")
        out[key.strip()] = _parse_value(value.strip())
    return out


# ---------------------------------------------------------------------------
def cmd_serve(args: argparse.Namespace) -> int:
    return serve_mcp(include=args.tools, exclude=args.exclude, log=args.log)


def cmd_tools(args: argparse.Namespace) -> int:
    agent = Agent(AgentConfig())
    if args.json:
        print(json.dumps(agent.schemas(), ensure_ascii=False, indent=2))
        return 0

    tools = agent.registry.all()
    print(f"{len(tools)} tools — full schema ≈ {agent.prompt_tokens():,} tokens\n")
    print(f"{'tool':<18}{'category':<10}{'risk':<7}{'schema':>8}{'desc':>7}")
    print("-" * 52)
    for t in tools:
        sc = len(json.dumps(t.schema, ensure_ascii=False))
        print(f"{t.name:<18}{t.category:<10}{t.risk.value:<7}{sc:>8}{len(t.description):>7}")
    print("-" * 52)
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    agent = Agent(AgentConfig())
    arguments = _parse_args(args.arg)

    started = time.perf_counter()
    try:
        result = agent.call(args.tool, arguments)
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    elapsed = (time.perf_counter() - started) * 1000

    if args.json:
        print(json.dumps({"text": result.text, "data": result.data}, ensure_ascii=False, indent=2))
    else:
        print(result.text)

    if result.image_png and args.save:
        from pathlib import Path

        p = Path(args.save)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(result.image_png)
        print(f"\nsaved image: {p} ({len(result.image_png) // 1024} KB)")

    print(f"\n({elapsed:.0f} ms)", file=sys.stderr)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    agent = Agent(AgentConfig(workspace="."))
    decision = agent.plan(args.message)
    print(decision.describe())
    if args.audit:
        audited = agent.audit(args.audit, decision)
        print("\n--- after draft audit ---")
        print(audited.describe())
    return 0 if decision.should_execute or decision.action.value in ("explain", "answer") else 1


def cmd_bench(args: argparse.Namespace) -> int:
    from .win32 import screen, windows

    print("=" * 66)
    print(f"winmcp {__version__} — performance")
    print("=" * 66)

    t0 = time.perf_counter()
    agent = Agent(AgentConfig(workspace="."))
    t1 = time.perf_counter()
    print(f"\nstartup (registry + agent)   {(t1 - t0) * 1000:>8.1f} ms")

    t0 = time.perf_counter()
    cap = screen.capture_screen()
    t1 = time.perf_counter()
    png = cap.to_png()
    t2 = time.perf_counter()
    print(f"screen capture (GDI)         {(t1 - t0) * 1000:>8.1f} ms   {cap.width}x{cap.height}")
    print(f"PNG encode                   {(t2 - t1) * 1000:>8.1f} ms   "
          f"{len(png) / 1024:.0f} KB (BMP would be {cap.width * cap.height * 4 / 1024 / 1024:.1f} MB)")

    t0 = time.perf_counter()
    wins = windows.list_windows()
    t1 = time.perf_counter()
    print(f"enumerate windows            {(t1 - t0) * 1000:>8.1f} ms   {len(wins)} visible")

    if wins:
        t0 = time.perf_counter()
        els = windows.enum_elements(wins[0].hwnd)
        t1 = time.perf_counter()
        print(f"enumerate elements           {(t1 - t0) * 1000:>8.1f} ms   "
              f"{len(els)} in {wins[0].title[:28]!r}")

    from .win32 import clipboard as clip

    t0 = time.perf_counter()
    clip.get_text()
    t1 = time.perf_counter()
    print(f"clipboard read               {(t1 - t0) * 1000:>8.1f} ms")

    from .win32 import process as proc

    t0 = time.perf_counter()
    procs = proc.list_processes()
    t1 = time.perf_counter()
    print(f"enumerate processes          {(t1 - t0) * 1000:>8.1f} ms   {len(procs)} running")

    print()
    print("-" * 66)
    print("tool dispatch (in-process, no IPC)")
    print("-" * 66)
    for name, payload in (
        ("DisplayInventory", {}),
        ("Snapshot", {"image": False}),
        ("Snapshot", {"image": True, "elements": True}),
    ):
        t0 = time.perf_counter()
        try:
            res = agent.call(name, payload)
            ok = True
        except ToolError as exc:
            res, ok = str(exc), False
        t1 = time.perf_counter()
        size = f"{len(res.image_png) // 1024} KB image" if ok and res.image_png else "text only"
        print(f"  {name:<16}{payload!s:<34}{(t1 - t0) * 1000:>7.1f} ms  {size}")

    print()
    print("-" * 66)
    print("context cost")
    print("-" * 66)
    full = agent.prompt_tokens()
    print(f"  all {len(agent.registry.names())} tools: {full:,} tokens")
    for req in (
        "take a screenshot",
        "open notepad and type something",
        "rename the files in this folder",
    ):
        picked = agent.recall(req)
        trimmed = agent.prompt_tokens(picked)
        pct = round((1 - trimmed / full) * 100) if full else 0
        print(f"  {req:<36} {len(picked):>2} tools  {trimmed:>6,} tokens  (-{pct}%)")

    return 0


def cmd_info(args: argparse.Namespace) -> int:
    agent = Agent(AgentConfig(workspace="."))
    report = agent.report()
    report["python"] = sys.version.split()[0]
    report["platform"] = sys.platform
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"winmcp {report['version']}")
    print(f"  python      {report['python']} on {report['platform']}")
    print(f"  tools       {report['tool_count']}")
    print(f"  schema      ≈ {report['schema_tokens_full']:,} tokens (all tools)")
    print(f"  workspace   {report['workspace']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="winmcp",
        description="Native Windows desktop control: the full Windows-MCP capability "
        "surface, built in.",
    )
    p.add_argument("--version", action="version", version=f"winmcp {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run as an MCP stdio server")
    s.add_argument("--tools", nargs="*", default=[], help="expose only these tools")
    s.add_argument("--exclude-tools", dest="exclude", nargs="*", default=[],
                   help="drop these tools")
    s.add_argument("--log", action="store_true", help="log requests to stderr")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("tools", help="list available tools")
    s.add_argument("--json", action="store_true", help="emit full JSON schemas")
    s.set_defaults(func=cmd_tools)

    s = sub.add_parser("call", help="invoke one tool")
    s.add_argument("tool", help="tool name, e.g. DisplayInventory")
    s.add_argument("--arg", action="append", help="key=value (repeatable); values parsed as JSON")
    s.add_argument("--json", action="store_true", help="print structured output")
    s.add_argument("--save", help="write a returned image to this path")
    s.set_defaults(func=cmd_call)

    s = sub.add_parser("plan", help="run the pre-response intent check")
    s.add_argument("message", help="the user message to classify")
    s.add_argument("--audit", help="also audit this draft reply")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("bench", help="measure startup and operation latency")
    s.set_defaults(func=cmd_bench)

    s = sub.add_parser("info", help="environment and capability report")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_info)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
