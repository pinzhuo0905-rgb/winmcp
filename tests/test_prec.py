"""Pre-response check tests — the mechanism that stops the agent handing work back."""

from __future__ import annotations

import pytest

from winmcp.prec import (
    Action,
    Intent,
    PreResponseCheck,
    ToolInfo,
    classify,
    looks_like_instructions,
)

TOOLS = [
    ToolInfo("Snapshot", "Inspect the desktop: active window, open windows, elements"),
    ToolInfo("Click", "Click the mouse at coordinates or by label"),
    ToolInfo("Type", "Type text into the focused control"),
    ToolInfo("FileSystem", "File operations: read, write, list, delete, move, copy"),
    ToolInfo("App", "Launch an application or manage windows"),
]


def _check(**kwargs: object) -> PreResponseCheck:
    defaults: dict[str, object] = {
        "tools": TOOLS,
        "workspace_bound": True,
        "mcp_configured": True,
    }
    defaults.update(kwargs)
    return PreResponseCheck(**defaults)  # type: ignore[arg-type]


# -- intent -----------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        "install this for me",
        "帮我装一下这个",
        "给我打开记事本",
        "把桌面上的文件按日期分类",
        "open Chrome",
        "please install the dependencies",
    ],
)
def test_delegation_is_do_it(message: str) -> None:
    assert classify(message).intent is Intent.DO_IT


@pytest.mark.parametrize(
    "message",
    [
        "how do I install this",
        "怎么安装这个",
        "what are the steps to configure it",
        "教我怎么装",
        "walk me through the setup",
    ],
)
def test_method_questions_are_explain(message: str) -> None:
    assert classify(message).intent is Intent.EXPLAIN


@pytest.mark.parametrize(
    "message",
    [
        "the build is broken",
        "软件打不开了",
        "pip keeps failing",
        "程序闪退了",
    ],
)
def test_problem_statements_are_do_it(message: str) -> None:
    """A statement of a problem implies "please fix it" — the easiest case to misread."""
    assert classify(message).intent is Intent.DO_IT


@pytest.mark.parametrize("message", ["can you fix this", "能不能帮我装一下", "可以自动整理吗"])
def test_feasibility_questions_are_do_it(message: str) -> None:
    assert classify(message).intent is Intent.DO_IT


@pytest.mark.parametrize("message", ["what is MCP", "MCP 是什么", "why is this slow"])
def test_knowledge_questions(message: str) -> None:
    assert classify(message).intent is Intent.KNOWLEDGE


@pytest.mark.parametrize("message", ["hello", "你好", "thanks", "ok"])
def test_chitchat(message: str) -> None:
    assert classify(message).intent is Intent.CHITCHAT


def test_explicit_delegation_beats_method_question() -> None:
    """'帮我看看怎么装' is a request to act, not a question about how."""
    verdict = classify("帮我看看怎么装这个")
    assert verdict.intent is Intent.DO_IT


def test_ambiguous_input_defaults_to_execution() -> None:
    verdict = classify("那个东西")
    assert verdict.intent is Intent.DO_IT
    assert verdict.ambiguous is True


def test_empty_input_is_chitchat() -> None:
    assert classify("").intent is Intent.CHITCHAT


def test_verdict_is_explainable() -> None:
    v = classify("install this for me")
    assert v.rule and 0.0 <= v.confidence <= 1.0
    assert "do_it" in v.describe()


# -- gate -------------------------------------------------------------------
def test_gate_requires_all_three() -> None:
    assert _check().gate_passed() is True
    assert _check(workspace_bound=False).gate_passed() is False
    assert _check(mcp_configured=False).gate_passed() is False
    assert _check(tools=[]).gate_passed() is False


def test_gate_failure_answers_normally() -> None:
    decision = _check(workspace_bound=False).evaluate("install this for me")
    assert decision.action is Action.ANSWER
    assert decision.gate_passed is False


# -- decision ---------------------------------------------------------------
def test_do_it_with_capability_executes() -> None:
    decision = _check().evaluate("rename the files in this folder")
    assert decision.action is Action.EXECUTE
    assert decision.should_execute is True
    assert decision.matched_tools


def test_explain_when_user_asked_how() -> None:
    assert _check().evaluate("how do I install this").action is Action.EXPLAIN


def test_knowledge_answered() -> None:
    assert _check().evaluate("what is MCP").action is Action.ANSWER


def test_blocked_when_no_tool_covers() -> None:
    decision = _check().evaluate("migrate the database to a new schema")
    if decision.action is Action.BLOCKED:
        assert decision.missing
        assert any("Do not substitute" in n for n in decision.notes)


def test_match_tools_uses_the_bridge() -> None:
    """Chinese request, English tool descriptions — the bridge must connect them."""
    assert "Snapshot" in _check().match_tools("帮我截个图看看屏幕")


def test_match_tools_empty_for_unrelated_text() -> None:
    assert _check().match_tools("quantum entanglement") == ()


def test_decision_describe_is_readable() -> None:
    text = _check().evaluate("rename the files").describe()
    assert "action:" in text and "intent:" in text


# -- draft audit ------------------------------------------------------------
@pytest.mark.parametrize(
    "draft",
    [
        "You can do this by opening the settings panel.",
        "你可以这样做：先打开设置，然后点击高级选项。",
        "Step 1: open a terminal",
        "1: run the installer",
        "步骤：\n1. 打开终端\n2. 运行 pip install",
        "You will need to do this manually.",
    ],
)
def test_detects_instruction_drafts(draft: str) -> None:
    is_steps, marker = looks_like_instructions(draft)
    assert is_steps is True
    assert marker


@pytest.mark.parametrize(
    "draft",
    [
        "Installed successfully, version 1.2.3.",
        "已装好，版本 2.0。",
        "Found it: the proxy port was wrong. Fixed.",
        "Renamed 12 files.",
    ],
)
def test_passes_result_drafts(draft: str) -> None:
    is_steps, _ = looks_like_instructions(draft)
    assert is_steps is False


def test_audit_rejects_step_draft_when_execution_required() -> None:
    """The core protection: execution was required, the draft hands back steps."""
    check = _check()
    decision = check.evaluate("rename the files in this folder")
    assert decision.action is Action.EXECUTE

    audited = check.audit_draft("You can do this by opening a terminal and running ren a b", decision)
    assert audited.action is Action.BLOCKED
    assert "redo" in audited.reason


def test_audit_allows_step_draft_when_user_asked_how() -> None:
    check = _check()
    decision = check.evaluate("how do I install this")
    assert decision.action is Action.EXPLAIN

    audited = check.audit_draft("Step 1: open a terminal\nStep 2: run the installer", decision)
    assert audited.action is Action.EXPLAIN


def test_audit_passes_a_result_draft() -> None:
    check = _check()
    decision = check.evaluate("rename the files in this folder")
    audited = check.audit_draft("Renamed 12 files.", decision)
    assert audited.action is Action.EXECUTE
