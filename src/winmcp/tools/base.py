"""工具协议与注册表。

每个工具声明四样东西：名字、描述、JSON Schema、风险级别。
描述与 schema 会被序列化进模型上下文，所以**要短**——这是上下文成本的大头。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Risk(str, Enum):
    """动作风险级别，决定是否需要操作后验证。"""

    LOW = "low"
    """只读或只影响视图，错了容易重来。"""

    HIGH = "high"
    """会改变状态或写入内容，错了代价大。"""


class ToolError(RuntimeError):
    """工具执行失败。消息会原样返回给模型，所以要写清楚。"""


@dataclass
class ToolResult:
    """一次工具调用的结果。"""

    text: str
    """给模型读的文本摘要。"""

    data: dict[str, Any] = field(default_factory=dict)
    """结构化数据，供程序消费。"""

    image_png: bytes | None = None
    """观察类工具产出的截图（PNG 字节）。"""

    def __str__(self) -> str:
        return self.text


@dataclass
class Tool:
    """一个工具的定义。"""

    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[..., ToolResult]
    risk: Risk = Risk.HIGH
    category: str = "misc"

    def to_mcp(self) -> dict[str, Any]:
        """转成 MCP ``tools/list`` 的条目格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
        }

    def invoke(self, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = dict(arguments or {})
        try:
            return self.handler(**args)
        except ToolError:
            raise
        except TypeError as exc:
            raise ToolError(f"{self.name} 参数不对：{exc}") from exc
        except Exception as exc:
            raise ToolError(f"{self.name} 执行失败：{type(exc).__name__}: {exc}") from exc


class Registry:
    """工具注册表，同时充当快速分发器。

    没有进程边界、没有 JSON-RPC、没有序列化——直接函数调用。
    这是「内置」相对「起一个 MCP 服务器进程」最直接的速度优势。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._order: list[str] = []

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool
        self._order.append(tool.name)
        return tool

    def tool(
        self,
        name: str,
        description: str,
        schema: dict[str, Any],
        risk: Risk = Risk.HIGH,
        category: str = "misc",
    ) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
        """装饰器写法注册工具。"""

        def deco(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
            self.add(
                Tool(
                    name=name,
                    description=description,
                    schema=schema,
                    handler=fn,
                    risk=risk,
                    category=category,
                )
            )
            return fn

        return deco

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._order)

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in self._order]

    def by_category(self, category: str) -> list[Tool]:
        return [t for t in self.all() if t.category == category]

    def invoke(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"没有这个工具：{name}")
        return tool.invoke(arguments)

    def subset(self, names: list[str]) -> Registry:
        """按名字裁出一个子注册表——用于按任务只暴露需要的工具。"""
        sub = Registry()
        for n in names:
            t = self._tools.get(n)
            if t is not None:
                sub.add(t)
        return sub

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_mcp() for t in self.all()]

    def schema_chars(self) -> int:
        import json

        return len(json.dumps(self.schemas(), ensure_ascii=False))


def prop(type_: str, description: str = "", **extra: Any) -> dict[str, Any]:
    """构造 JSON Schema 属性——省掉重复的样板。"""
    out: dict[str, Any] = {"type": type_}
    if description:
        out["description"] = description
    out.update(extra)
    return out


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """构造根为 object 的 JSON Schema。"""
    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


__all__ = [
    "Registry",
    "Risk",
    "Tool",
    "ToolError",
    "ToolResult",
    "obj",
    "prop",
]
