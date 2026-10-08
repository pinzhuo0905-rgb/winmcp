"""winmcp — native Windows desktop control, fully built in.

The complete Windows-MCP capability surface implemented in-process with ctypes and
the standard library: no separate server process, no JSON-RPC on the hot path, no
third-party runtime dependencies.

Three ways to use it:

    # 1. Python API — tool calls are plain function calls
    from winmcp import Agent, AgentConfig
    agent = Agent(AgentConfig(workspace="."))
    agent.call("DisplayInventory")

    # 2. MCP server — for existing MCP clients
    #    $ winmcp serve

    # 3. CLI — for shell scripts and quick checks
    #    $ winmcp call Snapshot --arg image=false

"""

__version__ = "1.0.0"

from .agent import Agent, AgentConfig
from .guards import FocusGuard, Risk, TieredVerifier, risk_of
from .prec import Action, Decision, Intent, IntentVerdict, PreResponseCheck, classify
from .tools import TOOL_NAMES, Registry, ToolError, ToolResult, build_registry

__all__ = [
    "TOOL_NAMES",
    "Action",
    "Agent",
    "AgentConfig",
    "Decision",
    "FocusGuard",
    "Intent",
    "IntentVerdict",
    "PreResponseCheck",
    "Registry",
    "Risk",
    "TieredVerifier",
    "ToolError",
    "ToolResult",
    "__version__",
    "build_registry",
    "classify",
    "risk_of",
]
