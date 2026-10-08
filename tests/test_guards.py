"""Guard tests: focus confirmation and tiered verification."""

from __future__ import annotations

import pytest

from winmcp.guards import (
    FocusError,
    FocusGuard,
    Risk,
    TieredVerifier,
    guarded,
    risk_of,
)


# -- focus ------------------------------------------------------------------
def _guard(title: str, **kw: object) -> FocusGuard:
    return FocusGuard(probe=lambda: title, **kw)  # type: ignore[arg-type]


def test_focus_matches_by_substring() -> None:
    """Window titles carry document names, so equality would never match."""
    assert _guard("Untitled - Notepad").check("Notepad").ok is True


def test_focus_mismatch_detected() -> None:
    guard = _guard("Chrome - GitHub")
    result = guard.check("Notepad")
    assert result.ok is False
    assert "Chrome" in result.actual
    assert guard.mismatches == 1


def test_ensure_raises_when_strict() -> None:
    with pytest.raises(FocusError) as exc:
        _guard("Chrome").ensure("Notepad")
    assert "focus" in str(exc.value).lower()


def test_ensure_does_not_raise_when_lenient() -> None:
    assert _guard("Chrome", strict=False).ensure("Notepad").ok is False


def test_empty_expectation_skips_the_check() -> None:
    assert _guard("anything").check("").ok is True


def test_probe_failure_does_not_crash() -> None:
    def boom() -> str:
        raise RuntimeError("probe exploded")

    assert FocusGuard(probe=boom, strict=False).check("Notepad").ok is False


def test_guarded_action_blocks_on_wrong_focus() -> None:
    """The protection that matters: the action never runs against the wrong window."""
    calls: list[str] = []
    guard = _guard("Chrome")
    wrapped = guarded(guard, lambda t: calls.append(t), "Notepad")
    with pytest.raises(FocusError):
        wrapped("hello")
    assert calls == []


def test_guarded_action_runs_on_correct_focus() -> None:
    calls: list[str] = []
    guard = _guard("Untitled - Notepad")
    guarded(guard, lambda t: calls.append(t), "Notepad")("hello")
    assert calls == ["hello"]


# -- tiered verification ----------------------------------------------------
def test_low_risk_actions_batch() -> None:
    v = TieredVerifier(batch_size=3)
    assert v.after("Scroll").verify_now is False
    assert v.after("Move").verify_now is False
    assert v.after("Wait").verify_now is True


def test_high_risk_verifies_immediately() -> None:
    assert TieredVerifier(batch_size=10).after("Type").verify_now is True


def test_high_risk_clears_the_batch() -> None:
    v = TieredVerifier(batch_size=5)
    v.after("Scroll")
    v.after("Move")
    assert v.pending == 2
    v.after("Type")
    assert v.pending == 0


def test_flush_verifies_leftovers() -> None:
    v = TieredVerifier(batch_size=10)
    v.after("Scroll")
    assert v.flush().verify_now is True
    assert v.flush().verify_now is False


def test_unknown_tool_defaults_to_high_risk() -> None:
    """Unknown means verify, not assume."""
    assert risk_of("SomeFutureTool") is Risk.HIGH
    assert TieredVerifier().after("SomeFutureTool").verify_now is True


@pytest.mark.parametrize("tool", ["Screenshot", "Snapshot", "DisplayInventory", "Scroll", "Wait"])
def test_known_low_risk(tool: str) -> None:
    assert risk_of(tool) is Risk.LOW


@pytest.mark.parametrize("tool", ["Click", "Type", "Shortcut", "FileSystem", "Process"])
def test_known_high_risk(tool: str) -> None:
    assert risk_of(tool) is Risk.HIGH


def test_reset_clears_state() -> None:
    v = TieredVerifier()
    v.after("Scroll")
    v.reset()
    assert v.pending == 0 and v.history == []
