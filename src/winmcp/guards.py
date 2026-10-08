"""Operational guards: focus confirmation and tiered verification.

Two failure modes account for most of the pain in agent-driven desktop control:

1. **Typing into the wrong window.** The most embarrassing and most common mistake.
   There is no return value telling you where focus is, so it must be checked
   deliberately before any keyboard input.
2. **Discovering a mistake too late.** Verifying after every step doubles the turn
   count; never verifying means an error surfaces several steps later, after the
   work built on it is wasted.

:class:`FocusGuard` addresses the first. :class:`TieredVerifier` addresses the second
by batching low-risk actions and verifying high-risk ones immediately.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FocusCheck:
    """Result of one focus comparison."""

    ok: bool
    expected: str
    actual: str
    reason: str

    def describe(self) -> str:
        return (
            f"focus {'ok' if self.ok else 'MISMATCH'}: expected {self.expected!r}, "
            f"actual {self.actual!r} — {self.reason}"
        )


class FocusError(RuntimeError):
    """Raised when focus does not match and the guard is set to strict."""

    def __init__(self, check: FocusCheck) -> None:
        super().__init__(check.describe())
        self.check = check


class FocusGuard:
    """Verify the focused window before keyboard input.

    Args:
        probe: returns the current foreground window title. Supply
            :func:`winmcp.win32.windows.foreground_title` or any equivalent.
        strict: raise on mismatch (default) instead of only reporting it.
    """

    def __init__(self, probe: Callable[[], str], strict: bool = True) -> None:
        self.probe = probe
        self.strict = strict
        self.last: FocusCheck | None = None
        self.mismatches = 0

    def current(self) -> str:
        try:
            return self.probe() or ""
        except Exception:
            return ""

    def check(self, expected: str) -> FocusCheck:
        """Compare the foreground window against ``expected``.

        Matching is substring-based: window titles carry document names and paths,
        so requiring equality would never match.
        """
        actual = self.current()
        if not expected:
            result = FocusCheck(True, expected, actual, "no expectation given")
        elif expected.lower() in actual.lower():
            result = FocusCheck(True, expected, actual, "focus correct")
        else:
            self.mismatches += 1
            result = FocusCheck(
                False, expected, actual, f"input would land in {actual!r} instead"
            )
        self.last = result
        return result

    def ensure(self, expected: str) -> FocusCheck:
        """Check, and raise when strict and mismatched."""
        result = self.check(expected)
        if not result.ok and self.strict:
            raise FocusError(result)
        return result


def guarded(guard: FocusGuard, action: Callable[..., Any], expected_window: str) -> Callable[..., Any]:
    """Wrap a keyboard action so it refuses to run against the wrong window."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        guard.ensure(expected_window)
        return action(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Tiered verification
# ---------------------------------------------------------------------------
class Risk(str, Enum):
    LOW = "low"
    HIGH = "high"


#: Unknown tools default to HIGH — verify rather than assume.
TOOL_RISK: dict[str, Risk] = {
    "Screenshot": Risk.LOW,
    "Snapshot": Risk.LOW,
    "DisplayInventory": Risk.LOW,
    "Move": Risk.LOW,
    "Scroll": Risk.LOW,
    "Wait": Risk.LOW,
    "WaitFor": Risk.LOW,
    "Scrape": Risk.LOW,
    "Notification": Risk.LOW,
    "Click": Risk.HIGH,
    "Type": Risk.HIGH,
    "Shortcut": Risk.HIGH,
    "MultiSelect": Risk.HIGH,
    "MultiEdit": Risk.HIGH,
    "App": Risk.HIGH,
    "Process": Risk.HIGH,
    "Clipboard": Risk.HIGH,
    "FileSystem": Risk.HIGH,
}

DEFAULT_BATCH_SIZE = 3


def risk_of(tool: str) -> Risk:
    return TOOL_RISK.get(tool, Risk.HIGH)


@dataclass
class VerifyPlan:
    verify_now: bool
    reason: str
    batched: int = 0


@dataclass
class TieredVerifier:
    """Decide when to re-observe the screen.

    Batching low-risk actions keeps the turn count down; verifying high-risk ones
    immediately keeps errors from compounding. Turn count matters because task
    success scales as ``per_step_success ^ steps``.

    >>> v = TieredVerifier()
    >>> v.after("Scroll").verify_now
    False
    >>> v.after("Type").verify_now
    True
    """

    batch_size: int = DEFAULT_BATCH_SIZE
    pending: int = field(default=0, init=False)
    history: list[tuple[str, Risk]] = field(default_factory=list, init=False)

    def after(self, tool: str) -> VerifyPlan:
        r = risk_of(tool)
        self.history.append((tool, r))

        if r is Risk.HIGH:
            self.pending = 0
            return VerifyPlan(True, f"{tool} is high risk — verify now")

        self.pending += 1
        if self.pending >= self.batch_size:
            n, self.pending = self.pending, 0
            return VerifyPlan(True, f"{n} low-risk actions queued — verify now", batched=n)

        return VerifyPlan(
            False, f"{tool} is low risk — queued ({self.pending}/{self.batch_size})"
        )

    def flush(self) -> VerifyPlan:
        """Verify anything still queued, e.g. at the end of a task."""
        if self.pending:
            n, self.pending = self.pending, 0
            return VerifyPlan(True, f"flushing {n} queued low-risk action(s)", batched=n)
        return VerifyPlan(False, "nothing queued")

    def reset(self) -> None:
        self.pending = 0
        self.history.clear()


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "TOOL_RISK",
    "FocusCheck",
    "FocusError",
    "FocusGuard",
    "Risk",
    "TieredVerifier",
    "VerifyPlan",
    "guarded",
    "risk_of",
]
