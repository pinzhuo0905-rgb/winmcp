"""winmcp 工具集：Windows-MCP 全部 18 项能力的原生实现。

    >>> from winmcp.tools import build_registry
    >>> reg = build_registry()
    >>> reg.names()[:3]
    ['Screenshot', 'Snapshot', 'DisplayInventory']

所有工具在**本进程内**执行——没有子进程、没有 JSON-RPC、没有序列化往返。
这是「内置」相对「起一个 MCP 服务器」最直接的速度优势。
"""

from __future__ import annotations

from .base import Registry, Risk, Tool, ToolError, ToolResult

#: 工具清单——与 Windows-MCP 4.0.11 的能力面一一对应
TOOL_NAMES: tuple[str, ...] = (
    "Screenshot", "Snapshot", "DisplayInventory",
    "Click", "Type", "Scroll", "Move", "Shortcut", "MultiSelect", "MultiEdit",
    "App", "Process", "Clipboard", "Notification", "Wait", "WaitFor",
    "FileSystem", "Scrape",
)


def build_registry() -> Registry:
    """构建包含全部 18 个工具的注册表。"""
    from . import files, observe, system, web
    from . import input as input_tools

    reg = Registry()
    observe.register(reg)
    input_tools.register(reg)
    system.register(reg)
    files.register(reg)
    web.register(reg)
    return reg


__all__ = [
    "TOOL_NAMES",
    "Registry",
    "Risk",
    "Tool",
    "ToolError",
    "ToolResult",
    "build_registry",
]
