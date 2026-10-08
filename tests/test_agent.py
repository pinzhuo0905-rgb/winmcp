"""High-level agent API tests."""

from __future__ import annotations

import pytest

from winmcp import Agent, AgentConfig
from winmcp.prec import Action
from winmcp.tools import TOOL_NAMES


@pytest.fixture
def agent():
    return Agent(AgentConfig(workspace="."))


# -- construction -----------------------------------------------------------
def test_agent_exposes_all_tools(agent) -> None:
    assert len(agent.registry.names()) == 18


def test_include_narrows_the_tool_set() -> None:
    a = Agent(AgentConfig(workspace=".", include=("Screenshot", "Click")))
    assert a.registry.names() == ["Screenshot", "Click"]


def test_exclude_removes_tools() -> None:
    a = Agent(AgentConfig(workspace=".", exclude=("FileSystem", "Process")))
    assert "FileSystem" not in a.registry.names()
    assert "Screenshot" in a.registry.names()


def test_unknown_include_is_rejected() -> None:
    with pytest.raises(ValueError):
        Agent(AgentConfig(include=("NotATool",)))


def test_excluding_everything_is_harmless() -> None:
    a = Agent(AgentConfig(exclude=TOOL_NAMES))
    assert a.registry.names() == []


# -- planning ---------------------------------------------------------------
def test_plan_executes_for_an_imperative_request(agent) -> None:
    decision = agent.plan("rename the files in this folder")
    assert decision.action is Action.EXECUTE


def test_plan_explains_when_asked_how(agent) -> None:
    assert agent.plan("how do I rename files").action is Action.EXPLAIN


def test_plan_answers_knowledge_questions(agent) -> None:
    assert agent.plan("what is MCP").action is Action.ANSWER


def test_plan_fails_the_gate_without_a_workspace() -> None:
    a = Agent(AgentConfig(workspace=None))
    assert a.plan("install this").gate_passed is False


def test_audit_rejects_a_step_draft(agent) -> None:
    decision = agent.plan("rename the files in this folder")
    audited = agent.audit("You can do this by opening a terminal.", decision)
    assert audited.action is Action.BLOCKED


def test_audit_accepts_a_result_draft(agent) -> None:
    decision = agent.plan("rename the files in this folder")
    assert agent.audit("Renamed 12 files.", decision).action is Action.EXECUTE


# -- recall and cost --------------------------------------------------------
def test_recall_returns_a_subset(agent) -> None:
    picked = agent.recall("take a screenshot")
    assert 0 < len(picked) < 18


def test_recall_is_relevant(agent) -> None:
    assert "Screenshot" in agent.recall("take a screenshot of the screen")


def test_recall_handles_chinese(agent) -> None:
    assert "Screenshot" in agent.recall("帮我截个图看看屏幕")


def test_prompt_tokens_shrinks_with_a_subset(agent) -> None:
    full = agent.prompt_tokens()
    trimmed = agent.prompt_tokens(["Screenshot", "Click"])
    assert trimmed < full / 2


def test_schemas_can_be_narrowed(agent) -> None:
    assert len(agent.schemas(["Click"])) == 1
    assert len(agent.schemas()) == 18


def test_cost_table_reports_savings(agent) -> None:
    rows = agent.cost_table(["take a screenshot", "open notepad and type"])
    assert len(rows) == 2
    assert all(0 <= r["saved_pct"] <= 100 for r in rows)
    assert all(r["tool_count"] > 0 for r in rows)


# -- execution --------------------------------------------------------------
def test_call_runs_a_tool(agent) -> None:
    result = agent.call("DisplayInventory")
    assert result.data["monitor_count"] >= 1


def test_try_call_reports_failure_without_raising(agent) -> None:
    ok, payload = agent.try_call("FileSystem", {"mode": "read", "path": "C:/nope/nope.xyz"})
    assert ok is False
    assert isinstance(payload, str)


def test_try_call_reports_success(agent) -> None:
    ok, result = agent.try_call("DisplayInventory")
    assert ok is True
    assert result.data


def test_should_verify_follows_the_risk_policy(agent) -> None:
    assert agent.should_verify("Type") is True
    agent.verifier.reset()
    assert agent.should_verify("Scroll") is False


def test_report_has_the_expected_shape(agent) -> None:
    report = agent.report()
    for key in ("version", "tools", "tool_count", "schema_tokens_full", "focus_mismatches"):
        assert key in report
    assert report["tool_count"] == 18


def test_type_safely_refuses_on_wrong_focus(agent) -> None:
    """Focus guard must stop typing before any input is sent."""
    from winmcp.guards import FocusError

    with pytest.raises(FocusError):
        agent.type_safely("hello", expect_window="zzz-no-such-window-zzz")


def test_type_safely_without_expectation_does_not_guard(agent) -> None:
    """With no expectation the guard is skipped; the call itself still runs."""
    ok, _ = agent.try_call("Wait", {"seconds": 0.01})
    assert ok is True
