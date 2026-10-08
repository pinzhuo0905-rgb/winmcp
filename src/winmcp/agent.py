"""High-level in-process API.

    >>> from winmcp import Agent, AgentConfig
    >>> agent = Agent(AgentConfig(workspace="."))
    >>> agent.plan("install this for me").should_execute
    True
    >>> agent.call("DisplayInventory").data["monitor_count"]
    1

Nothing here spawns a process or speaks JSON-RPC — tool calls are plain function
calls. That is the difference between "built in" and "connected to".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .guards import FocusGuard, TieredVerifier
from .prec import Decision, PreResponseCheck, ToolInfo
from .tools import Registry, ToolError, ToolResult, build_registry


@dataclass
class AgentConfig:
    """Agent configuration.

    Args:
        workspace: bound workspace folder. A bound workspace plus available tools is
            treated as standing authorization to act.
        include: expose only these tools. Empty means all.
        exclude: drop these tools.
        focus_strict: raise instead of warn when focus does not match before typing.
    """

    workspace: str | Path | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    focus_strict: bool = True

    def build_registry(self) -> Registry:
        reg = build_registry()
        if self.include:
            unknown = set(self.include) - set(reg.names())
            if unknown:
                raise ValueError(f"unknown tool(s): {sorted(unknown)}")
            reg = reg.subset(list(self.include))
        if self.exclude:
            reg = reg.subset([n for n in reg.names() if n not in set(self.exclude)])
        return reg


class Agent:
    """Desktop control with a pre-response intent check built in."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self.registry = self.config.build_registry()
        self.verifier = TieredVerifier()

        from .win32.windows import foreground_title

        self.focus = FocusGuard(probe=foreground_title, strict=self.config.focus_strict)
        self._check = PreResponseCheck(
            tools=[ToolInfo(t.name, t.description) for t in self.registry.all()],
            workspace_bound=self.config.workspace is not None,
            mcp_configured=True,
        )

    # -- intent ------------------------------------------------------------
    def plan(self, message: str) -> Decision:
        """Run the pre-response check for a user message."""
        return self._check.evaluate(message)

    def audit(self, draft: str, decision: Decision) -> Decision:
        """Check a draft reply: is it the result of work, or steps for the user?"""
        return self._check.audit_draft(draft, decision)

    # -- capability --------------------------------------------------------
    def recall(self, message: str, limit: int = 6) -> list[str]:
        """Tools relevant to a request."""
        return list(self._check.match_tools(message, limit=limit))

    def schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """Tool schemas, optionally narrowed — this is what goes into the prompt."""
        reg = self.registry.subset(names) if names else self.registry
        return reg.schemas()

    def prompt_tokens(self, names: list[str] | None = None) -> int:
        """Rough token cost of the tool schemas that would be sent."""
        import json

        payload = self.schemas(names)
        return round(len(json.dumps(payload, ensure_ascii=False)) / 3.3)

    # -- execution ---------------------------------------------------------
    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Invoke a tool in-process."""
        return self.registry.invoke(tool, arguments)

    def try_call(
        self, tool: str, arguments: dict[str, Any] | None = None
    ) -> tuple[bool, ToolResult | str]:
        """Invoke a tool without raising. Returns ``(ok, result_or_error)``."""
        try:
            return True, self.call(tool, arguments)
        except ToolError as exc:
            return False, str(exc)

    def type_safely(
        self,
        text: str,
        expect_window: str = "",
        loc: list[int] | None = None,
        clear: bool = False,
        press_enter: bool = False,
    ) -> ToolResult:
        """Type only if focus matches ``expect_window``.

        This is the guard that prevents the single most common mistake in desktop
        automation: text landing in whatever window happened to have focus.
        """
        if expect_window:
            self.focus.ensure(expect_window)
        return self.call(
            "Type",
            {
                "text": text,
                "loc": loc,
                "clear": clear,
                "press_enter": press_enter,
            },
        )

    def should_verify(self, tool: str) -> bool:
        """Whether to re-observe after this action, per the tiered policy."""
        return self.verifier.after(tool).verify_now

    # -- introspection -----------------------------------------------------
    def report(self) -> dict[str, Any]:
        """Runtime state and cost figures."""
        full = self.prompt_tokens()
        return {
            "version": __version__,
            "workspace": str(self.config.workspace) if self.config.workspace else None,
            "tools": self.registry.names(),
            "tool_count": len(self.registry.names()),
            "schema_tokens_full": full,
            "focus_mismatches": self.focus.mismatches,
            "verification_pending": self.verifier.pending,
        }

    def cost_table(self, sample_requests: list[str]) -> list[dict[str, Any]]:
        """Compare full-schema cost against per-request recall.

        Useful for showing how much context tool trimming actually saves.
        """
        full = self.prompt_tokens()
        rows = []
        for req in sample_requests:
            picked = self.recall(req)
            trimmed = self.prompt_tokens(picked)
            rows.append(
                {
                    "request": req,
                    "tools": picked,
                    "tool_count": len(picked),
                    "tokens": trimmed,
                    "saved_pct": round((1 - trimmed / full) * 100) if full else 0,
                }
            )
        return rows


__all__ = ["Agent", "AgentConfig"]
