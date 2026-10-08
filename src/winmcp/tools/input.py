"""输入类工具：Click / Type / Scroll / Move / Shortcut / MultiSelect / MultiEdit。

## 定位策略：语义优先，坐标兜底

每个工具都同时接受两种定位方式：

    Click(loc=[100, 200])     坐标——界面一变就失效
    Click(label="Save")       语义——按控件文本/类名找，界面挪了也不会错

**label 会先在当前焦点窗口里找，找不到再遍历所有窗口**，
所以即使目标不在前台也能定位到（但点击前仍会提醒先激活窗口）。
"""

from __future__ import annotations

from ..win32 import input as winput
from ..win32 import windows
from .base import Registry, Risk, ToolError, ToolResult, obj, prop

_LOC = prop(
    "array",
    "Screen coordinates [x, y]. Prefer `label` when the target is a named control.",
    items={"type": "integer"},
)
_LABEL = prop(
    "string",
    "Element id or text/class fragment of the target control, e.g. \"3\" or \"Save\". "
    "Resolved against the active window first, then all windows.",
)


def _resolve(loc: list[int] | None, label: int | str | None) -> tuple[int, int, str]:
    """把 loc / label 解析成屏幕坐标。

    Returns:
        (x, y, 定位方式说明)
    """
    if label is not None and str(label).strip():
        fg = windows.foreground_window()
        candidates = [fg.hwnd] if fg else []
        candidates += [w.hwnd for w in windows.list_windows() if not fg or w.hwnd != fg.hwnd]
        for hwnd in candidates:
            el = windows.resolve_element(hwnd, label)
            if el is not None:
                x, y = el.center
                name = el.text or el.class_name
                return x, y, f"label={label!r} -> {name!r} ({el.class_name})"

        raise ToolError(
            f"找不到 label={label!r} 对应的控件。"
            "先用 Snapshot(elements=true) 看有哪些元素，或改用 loc 坐标。"
        )

    if loc and len(loc) >= 2:
        return int(loc[0]), int(loc[1]), f"loc={[int(loc[0]), int(loc[1])]}"

    x, y = winput.cursor_position()
    return x, y, f"cursor=({x}, {y})"


def register(reg: Registry) -> None:
    @reg.tool(
        name="Click",
        description=(
            "Click the mouse. Target by `label` (preferred) or `loc`; with neither, "
            "clicks at the current cursor position. clicks=2 is a double click, "
            "clicks=0 only hovers."
        ),
        schema=obj(
            {
                "loc": _LOC,
                "label": _LABEL,
                "button": prop("string", "left (default), right, or middle"),
                "clicks": prop("integer", "Number of clicks; 0 hovers, 2 double-clicks"),
            }
        ),
        risk=Risk.HIGH,
        category="input",
    )
    def click(
        loc: list[int] | None = None,
        label: int | str | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> ToolResult:
        x, y, how = _resolve(loc, label)
        winput.click(x, y, button=button, clicks=int(clicks))
        verb = "Hovered" if int(clicks) == 0 else f"{button} clicked x{clicks}"
        return ToolResult(
            text=f"{verb} at ({x}, {y}) [{how}]",
            data={"x": x, "y": y, "button": button, "clicks": int(clicks), "resolved_by": how},
        )

    @reg.tool(
        name="Move",
        description=(
            "Move the mouse. Set drag=true to press and drag from the current position "
            "(or from_loc) to the target."
        ),
        schema=obj(
            {
                "loc": _LOC,
                "label": _LABEL,
                "drag": prop("boolean", "Press and drag instead of just moving"),
                "from_loc": prop(
                    "array", "Start point for a drag", items={"type": "integer"}
                ),
                "duration": prop("number", "Seconds for a smooth move (helps with drag)"),
            }
        ),
        risk=Risk.HIGH,
        category="input",
    )
    def move(
        loc: list[int] | None = None,
        label: int | str | None = None,
        drag: bool = False,
        from_loc: list[int] | None = None,
        duration: float = 0.0,
    ) -> ToolResult:
        x, y, how = _resolve(loc, label)
        if drag:
            if from_loc and len(from_loc) >= 2:
                sx, sy = int(from_loc[0]), int(from_loc[1])
            else:
                sx, sy = winput.cursor_position()
            winput.drag(sx, sy, x, y, duration=duration or 0.3)
            return ToolResult(
                text=f"Dragged ({sx}, {sy}) -> ({x}, {y})",
                data={"from": [sx, sy], "to": [x, y]},
            )
        winput.move(x, y, duration=float(duration or 0))
        return ToolResult(text=f"Moved to ({x}, {y}) [{how}]", data={"x": x, "y": y})

    @reg.tool(
        name="Type",
        description=(
            "Type text into the focused control. Unicode is injected directly, so "
            "non-ASCII text works. Set clear=true to select-all and replace first. "
            "If the target is not focused, pass `label` or `loc` to click it first."
        ),
        schema=obj(
            {
                "text": prop("string", "Text to type"),
                "loc": _LOC,
                "label": _LABEL,
                "clear": prop("boolean", "Select all and replace before typing"),
                "press_enter": prop("boolean", "Press Enter after typing"),
                "caret": prop("string", "start, end, or idle (default)"),
            },
            required=["text"],
        ),
        risk=Risk.HIGH,
        category="input",
    )
    def type_text(
        text: str,
        loc: list[int] | None = None,
        label: int | str | None = None,
        clear: bool = False,
        press_enter: bool = False,
        caret: str = "idle",
    ) -> ToolResult:
        how = "already focused"
        if loc or label:
            x, y, how = _resolve(loc, label)
            winput.click(x, y)
            winput.wait_ms(120)

        if caret in ("start", "end"):
            winput.shortcut("ctrl+home" if caret == "start" else "ctrl+end")
        if clear:
            winput.shortcut("ctrl+a")
            winput.wait_ms(60)

        winput.type_text(str(text))
        if press_enter:
            winput.wait_ms(60)
            winput.shortcut("enter")

        preview = str(text)[:60] + ("…" if len(str(text)) > 60 else "")
        return ToolResult(
            text=f"Typed {len(str(text))} chars into {how}: {preview!r}",
            data={"chars": len(str(text)), "target": how, "cleared": bool(clear)},
        )

    @reg.tool(
        name="Scroll",
        description=(
            "Scroll the mouse wheel. Target by `label` or `loc` to scroll over a "
            "specific area; otherwise scrolls at the cursor."
        ),
        schema=obj(
            {
                "loc": _LOC,
                "label": _LABEL,
                "direction": prop("string", "up (default), down, left, or right"),
                "amount": prop("integer", "Number of wheel notches, default 3"),
            }
        ),
        risk=Risk.LOW,
        category="input",
    )
    def scroll(
        loc: list[int] | None = None,
        label: int | str | None = None,
        direction: str = "down",
        amount: int = 3,
    ) -> ToolResult:
        how = "at cursor"
        if loc or label:
            x, y, how = _resolve(loc, label)
            winput.move(x, y)
            winput.wait_ms(40)

        d = (direction or "down").lower()
        horizontal = d in ("left", "right")
        winput.scroll(d, amount=abs(int(amount)), horizontal=horizontal)
        return ToolResult(
            text=f"Scrolled {d} x{abs(int(amount))} {how}",
            data={"direction": d, "amount": abs(int(amount)), "target": how},
        )

    @reg.tool(
        name="Shortcut",
        description=(
            "Press a key combination, e.g. \"ctrl+c\", \"alt+tab\", \"win+r\", \"enter\", "
            "\"esc\", \"f5\". Keys are joined with +."
        ),
        schema=obj(
            {"shortcut": prop("string", "Key combination, e.g. ctrl+shift+s")},
            required=["shortcut"],
        ),
        risk=Risk.HIGH,
        category="input",
    )
    def shortcut(shortcut: str) -> ToolResult:
        winput.shortcut(str(shortcut))
        return ToolResult(text=f"Pressed {shortcut}", data={"shortcut": shortcut})

    @reg.tool(
        name="MultiSelect",
        description=(
            "Click several targets in sequence. Set press_ctrl=true to build a "
            "multi-selection (files, checkboxes) by holding Ctrl between clicks."
        ),
        schema=obj(
            {
                "locs": prop(
                    "array",
                    "List of [x, y] pairs",
                    items={"type": "array", "items": {"type": "integer"}},
                ),
                "labels": prop(
                    "array", "List of element labels/ids", items={"type": "string"}
                ),
                "press_ctrl": prop("boolean", "Hold Ctrl between clicks for multi-select"),
            }
        ),
        risk=Risk.HIGH,
        category="input",
    )
    def multi_select(
        locs: list[list[int]] | None = None,
        labels: list[str] | None = None,
        press_ctrl: bool = False,
    ) -> ToolResult:
        points: list[tuple[int, int]] = []
        for loc in locs or []:
            if len(loc) >= 2:
                points.append((int(loc[0]), int(loc[1])))
        for lb in labels or []:
            x, y, _ = _resolve(None, lb)
            points.append((x, y))

        if not points:
            raise ToolError("需要至少一个 locs 或 labels")

        ctrl = winput.VK["ctrl"]
        if press_ctrl:
            winput.press_keys([ctrl])
        try:
            for i, (x, y) in enumerate(points):
                winput.click(x, y)
                if press_ctrl and i < len(points) - 1:
                    winput.wait_ms(60)
        finally:
            if press_ctrl:
                winput.press_keys([ctrl])

        return ToolResult(
            text=f"Clicked {len(points)} targets" + (" with Ctrl held" if press_ctrl else ""),
            data={"count": len(points), "points": [list(p) for p in points]},
        )

    @reg.tool(
        name="MultiEdit",
        description=(
            "Fill several fields in one call. Provide locs=[[x, y, text], ...] or "
            "labels=[[label, text], ...]. Each field is clicked, cleared, then filled."
        ),
        schema=obj(
            {
                "locs": prop(
                    "array",
                    "List of [x, y, text] triples",
                    items={"type": "array"},
                ),
                "labels": prop(
                    "array", "List of [label, text] pairs", items={"type": "array"}
                ),
            }
        ),
        risk=Risk.HIGH,
        category="input",
    )
    def multi_edit(
        locs: list[list] | None = None,
        labels: list[list] | None = None,
    ) -> ToolResult:
        filled = 0
        for item in locs or []:
            if len(item) >= 3:
                winput.click(int(item[0]), int(item[1]))
                winput.wait_ms(100)
                winput.shortcut("ctrl+a")
                winput.wait_ms(50)
                winput.type_text(str(item[2]))
                filled += 1
        for item in labels or []:
            if len(item) >= 2:
                x, y, _ = _resolve(None, item[0])
                winput.click(x, y)
                winput.wait_ms(100)
                winput.shortcut("ctrl+a")
                winput.wait_ms(50)
                winput.type_text(str(item[1]))
                filled += 1

        if not filled:
            raise ToolError("需要至少一个 locs 或 labels")
        return ToolResult(text=f"Filled {filled} field(s)", data={"filled": filled})


__all__ = ["register"]
