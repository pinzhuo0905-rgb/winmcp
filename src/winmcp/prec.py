"""Pre-response execution check (PREC).

Most agents that *can* operate a computer still answer as if they cannot. Asked to
install something, they describe the installation. Asked to fix a broken environment,
they list troubleshooting steps. The user set up desktop control precisely so the
agent would act — handing back instructions inverts that intent.

This module inserts one check **before a response is generated**:

    1. environment gate   is there a workspace and an available capability?
    2. intent             does the user want it done, or explained?
    3. capability match   can the available tools cover it?
    4. execute            do it, rather than describing it

Plus a **draft audit**: after writing a reply, check whether it hands back steps when
execution was required, and reject it if so.

    >>> from winmcp.prec import classify, Intent
    >>> classify("install this for me").intent is Intent.DO_IT
    True
    >>> classify("how do I install this").intent is Intent.EXPLAIN
    True
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .keywords import expanded_tokens


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------
class Intent(str, Enum):
    """What the user is asking for."""

    DO_IT = "do_it"
    """They want it done — execute."""

    EXPLAIN = "explain"
    """They asked how — give the steps."""

    KNOWLEDGE = "knowledge"
    """A factual question — answer it."""

    CHITCHAT = "chitchat"
    """Greeting or acknowledgement."""


@dataclass(frozen=True)
class IntentVerdict:
    """The result of one classification."""

    intent: Intent
    confidence: float
    rule: str
    ambiguous: bool = False

    @property
    def wants_execution(self) -> bool:
        return self.intent is Intent.DO_IT

    def describe(self) -> str:
        flag = " (ambiguous, defaulted to execute)" if self.ambiguous else ""
        return f"{self.intent.value} · confidence {self.confidence:.2f} · matched {self.rule!r}{flag}"


# Explicit delegation wins over everything else: "帮我看看怎么装" is a request to do it,
# not a question about how.
_DELEGATION = [
    (r"帮我|帮忙|帮个忙", "delegation: 帮我"),
    (r"给我(?!讲|说|解释|介绍)", "delegation: 给我"),
    (r"麻烦你|辛苦你", "delegation: 麻烦你"),
    (r"你去|你来|替我", "delegation: 替我做"),
    (r"\bplease\s+(do|run|install|open|fix|set|clean|move|rename|delete|create|make)\b",
     "delegation: please <verb>"),
    (r"\bcan\s+you\s+(please\s+)?(just\s+)?(do|run|install|open|fix|set|clean|move|rename|delete|create)\b",
     "delegation: can you <verb>"),
]

_METHOD = [
    (r"(怎么|如何|怎样|咋)(才|能|可以|去|来|弄|搞)?\s*\S", "method: 怎么…"),
    (r"步骤|流程|教程|操作步骤|怎么做", "method: asks for steps"),
    (r"教我|教一下|带我", "method: asks to be taught"),
    (r"\bhow\s+(do|to|can|should|would)\b", "method: how do I"),
    (r"\bwhat\s+are\s+the\s+steps\b", "method: what are the steps"),
    (r"\bteach\s+me\b|\bwalk\s+me\s+through\b", "method: teach me"),
]

# Asking "can you" is a request to act, not a request for instructions.
_FEASIBILITY = [
    (r"能不能|可不可以|能否|可以吗|行不行", "feasibility: 能不能"),
    (r"\bcan\s+you\b|\bcould\s+you\b|\bis\s+it\s+possible\b|\bare\s+you\s+able\b",
     "feasibility: can you"),
]

# A statement of a problem implies "please fix it".
_PROBLEM = [
    (r"打不开|用不了|装不上|连不上|起不来|跑不起来|没反应|卡住|卡死|崩溃|闪退|报错|出错|坏了|挂了",
     "problem statement (zh)"),
    (r"\b(won'?t|can'?t|cannot|doesn'?t|isn'?t|keeps?\s+fail\w*|fails?|broken|"
     r"crash(ed|es)?|stuck|not\s+working|doesn'?t\s+work)\b",
     "problem statement (en)"),
]

_KNOWLEDGE = [
    (r"是什么|什么是|什么意思|区别|差异|原理", "knowledge question (zh)"),
    (r"\bwhat\s+is\b|\bwhat\s+are\b|\bdifference\s+between\b|\bwhy\s+(is|does|do)\b|\bexplain\b",
     "knowledge question (en)"),
]

# Longer alternatives must come first, or "早上好" gets cut short by "早".
_CHITCHAT = [
    (r"^(早上好|中午好|下午好|晚上好|早安|晚安|你好|您好|在吗|hi|hello|hey)[\s!！。.~]*$",
     "greeting"),
    (r"^(谢谢|多谢|thanks|thank\s+you|thx)[\s!！。.~]*$", "thanks"),
    (r"^(好的|嗯|ok|okay|收到|明白|了解)[\s!！。.~]*$", "acknowledgement"),
]

_IMPERATIVE = [
    (r"^把\s*\S+", "imperative: 把-sentence"),
    (r"^(打开|关闭|启动|运行|安装|卸载|下载|上传|复制|粘贴|删除|重命名|整理|清理|打包|编译|"
     r"构建|部署|测试|检查|修复|重启|刷新|更新|创建|新建|生成|写|改|修改|调整|设置|配置|"
     r"连接|断开|同步|备份|恢复|导出|导入|转换|压缩|解压)\S*",
     "imperative (zh)"),
    (r"^(open|close|start|run|launch|install|uninstall|download|upload|copy|paste|delete|"
     r"remove|rename|organize|clean|build|compile|deploy|test|check|fix|restart|refresh|"
     r"update|create|make|write|edit|modify|configure|connect|sync|backup|restore|export|"
     r"import|convert|zip|unzip)\b",
     "imperative (en)"),
]


def _first_match(text: str, table: list[tuple[str, str]]) -> str | None:
    for pattern, label in table:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


def classify(message: str) -> IntentVerdict:
    """Classify a user message into one of four intents.

    Ambiguous input defaults to :attr:`Intent.DO_IT` with ``ambiguous=True``.
    Acting on a wrong guess costs one clarification; explaining when the user wanted
    action costs a wasted turn and the impression that the agent cannot help.
    """
    text = (message or "").strip()
    if not text:
        return IntentVerdict(Intent.CHITCHAT, 0.5, "empty input")

    if hit := _first_match(text, _CHITCHAT):
        return IntentVerdict(Intent.CHITCHAT, 0.95, hit)
    if hit := _first_match(text, _DELEGATION):
        return IntentVerdict(Intent.DO_IT, 0.95, hit)
    if hit := _first_match(text, _METHOD):
        return IntentVerdict(Intent.EXPLAIN, 0.85, hit)
    if hit := _first_match(text, _FEASIBILITY):
        return IntentVerdict(Intent.DO_IT, 0.88, hit)
    if hit := _first_match(text, _PROBLEM):
        return IntentVerdict(Intent.DO_IT, 0.82, hit)
    if hit := _first_match(text, _KNOWLEDGE):
        return IntentVerdict(Intent.KNOWLEDGE, 0.8, hit)
    if hit := _first_match(text, _IMPERATIVE):
        return IntentVerdict(Intent.DO_IT, 0.85, hit)

    return IntentVerdict(Intent.DO_IT, 0.5, "default (execute-first)", ambiguous=True)


# ---------------------------------------------------------------------------
# Capability matching
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolInfo:
    """Minimal tool description — enough to decide whether it covers a request."""

    name: str
    description: str = ""

    def keywords(self) -> set[str]:
        # Goes through the CN/EN bridge, otherwise a Chinese request and an English
        # tool description never intersect.
        return expanded_tokens(f"{self.name} {self.description}")


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------
class Action(str, Enum):
    """What to do with this turn."""

    EXECUTE = "execute"
    EXPLAIN = "explain"
    ANSWER = "answer"
    BLOCKED = "blocked"
    """Wanted to execute but no capability matched — say what is missing, do not
    substitute instructions for a missing capability."""


@dataclass
class Decision:
    """The full result of a pre-response check."""

    action: Action
    intent: IntentVerdict
    reason: str
    gate_passed: bool = True
    matched_tools: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def should_execute(self) -> bool:
        return self.action is Action.EXECUTE

    def describe(self) -> str:
        lines = [
            f"action: {self.action.value}",
            f"reason: {self.reason}",
            f"intent: {self.intent.describe()}",
        ]
        if not self.gate_passed:
            lines.append("gate: not passed (no workspace or no tools)")
        if self.matched_tools:
            lines.append(f"matched tools: {', '.join(self.matched_tools)}")
        if self.missing:
            lines.append(f"missing: {', '.join(self.missing)}")
        lines.extend(self.notes)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Draft audit
# ---------------------------------------------------------------------------
_INSTRUCTION_MARKERS = [
    (r"你可以(这样|按以下|通过)", "'you can do this by…' (zh)"),
    (r"^步骤\s*[:：]?\s*$|^操作步骤", "steps heading"),
    (r"^\s*(第\s*[一二三四五六七八九十\d]+\s*步|[1-9]\d*[.、)]\s*\S)", "numbered steps (zh)"),
    (r"你(需要|得|要)先", "'you need to first…'"),
    (r"手动(操作|执行|完成)", "'manually' (zh)"),
    (r"自己(跑|运行|执行)一下", "'run it yourself'"),
    (r"\byou\s+can\s+(do|run|use|try)\b", "'you can…' (en)"),
    (r"^\s*step\s*\d+\s*[:：.]?\s", "numbered steps (en)"),
    (r"^\s*[1-9]\d*\s*[:：]\s+\S", "numbered list with colon"),
    (r"\bmanually\b", "'manually' (en)"),
]


def looks_like_instructions(text: str) -> tuple[bool, str]:
    """Detect whether a draft reply hands the task back as steps.

    Returns:
        ``(looks_like_steps, matched_marker)``
    """
    for pattern, label in _INSTRUCTION_MARKERS:
        if re.search(pattern, text or "", re.IGNORECASE | re.MULTILINE):
            return True, label
    return False, ""


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
class PreResponseCheck:
    """Run before generating a response.

    Args:
        tools: available capabilities. Empty means the environment gate fails.
        workspace_bound: whether the session is bound to a workspace folder.
        mcp_configured: whether a capability provider is configured.
    """

    def __init__(
        self,
        tools: list[ToolInfo] | None = None,
        workspace_bound: bool = True,
        mcp_configured: bool = True,
    ) -> None:
        self.tools: list[ToolInfo] = list(tools or [])
        self.workspace_bound = workspace_bound
        self.mcp_configured = mcp_configured
        self._index: dict[str, set[str]] = {t.name: t.keywords() for t in self.tools}

    # -- 1. environment gate ------------------------------------------------
    def gate_passed(self) -> bool:
        """All three must hold. Together they constitute standing authorization:
        a user who bound a workspace and enabled desktop control has already said
        "operate this machine for me"."""
        return bool(self.workspace_bound and self.mcp_configured and self.tools)

    # -- 3. capability match ------------------------------------------------
    def match_tools(self, message: str, limit: int = 6) -> tuple[str, ...]:
        """Recall relevant tools by keyword overlap."""
        if not self.tools:
            return ()
        tokens = expanded_tokens(message)
        if not tokens:
            return ()
        scored = [
            (len(tokens & kws), name)
            for name, kws in self._index.items()
            if tokens & kws
        ]
        scored.sort(key=lambda x: (-x[0], x[1]))
        return tuple(name for _, name in scored[:limit])

    # -- the flow -----------------------------------------------------------
    def evaluate(self, message: str) -> Decision:
        """Run all four steps and return a decision."""
        if not self.gate_passed():
            missing = []
            if not self.workspace_bound:
                missing.append("workspace folder")
            if not self.mcp_configured:
                missing.append("capability provider")
            if not self.tools:
                missing.append("available tools")
            return Decision(
                action=Action.ANSWER,
                intent=classify(message),
                reason=f"environment gate failed, missing: {', '.join(missing)}",
                gate_passed=False,
            )

        verdict = classify(message)

        if verdict.intent is Intent.CHITCHAT:
            return Decision(Action.ANSWER, verdict, "greeting")
        if verdict.intent is Intent.KNOWLEDGE:
            return Decision(Action.ANSWER, verdict, "knowledge question")
        if verdict.intent is Intent.EXPLAIN:
            return Decision(
                Action.EXPLAIN, verdict, "user asked how — giving steps is correct here"
            )

        matched = self.match_tools(message)
        if matched:
            return Decision(
                action=Action.EXECUTE,
                intent=verdict,
                reason="user wants it done and the tools cover it",
                matched_tools=matched,
            )

        return Decision(
            action=Action.BLOCKED,
            intent=verdict,
            reason="user wants it done but no available tool covers it",
            missing=("a tool that can perform this task",),
            notes=["State what is missing. Do not substitute instructions for a capability."],
        )

    # -- draft audit --------------------------------------------------------
    def audit_draft(self, draft: str, decision: Decision) -> Decision:
        """Check a draft reply before sending it.

        If the draft hands back steps while the decision called for execution,
        the decision is rewritten to demand a redo.
        """
        is_steps, marker = looks_like_instructions(draft)
        if is_steps and decision.action is Action.EXECUTE:
            decision.action = Action.BLOCKED
            decision.reason = (
                f"draft gives steps (matched {marker!r}) but execution was required — redo"
            )
            decision.notes.append("Discard the draft and execute instead.")
        elif is_steps and decision.action is Action.EXPLAIN:
            decision.notes.append(f"draft contains steps (matched {marker!r}), as intended")
        return decision


__all__ = [
    "Action",
    "Decision",
    "Intent",
    "IntentVerdict",
    "PreResponseCheck",
    "ToolInfo",
    "classify",
    "looks_like_instructions",
]
