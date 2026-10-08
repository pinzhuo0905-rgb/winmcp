"""观察类工具：Screenshot / Snapshot / DisplayInventory。

这三个是 agent 的「眼睛」。设计要点：

- **默认少给**：``Snapshot`` 的元素树很占上下文，默认不开，需要时才要
- **PNG 而非 BMP**：同样画面 164 KB vs 4 MB
- **结构化 + 文本双份**：文本给模型读，结构化数据给程序用
"""

from __future__ import annotations

from typing import Any

from ..win32 import screen, windows
from .base import Registry, Risk, ToolResult, obj, prop


def register(reg: Registry) -> None:
    @reg.tool(
        name="Screenshot",
        description=(
            "Capture the screen as an image. Use this to see the current state of "
            "the desktop before acting. Returns cursor position and the active window."
        ),
        schema=obj(
            {
                "display": prop("integer", "Monitor index; omit for the whole virtual desktop"),
                "region": prop(
                    "array",
                    "Optional [x, y, width, height] to capture only part of the screen",
                    items={"type": "integer"},
                ),
            }
        ),
        risk=Risk.LOW,
        category="observe",
    )
    def screenshot(display: int | None = None, region: list[int] | None = None) -> ToolResult:
        from ..win32.input import cursor_position

        if region and len(region) == 4:
            cap = screen.capture_region(int(region[0]), int(region[1]), int(region[2]), int(region[3]))
        else:
            cap = screen.capture_screen(display)

        cx, cy = cursor_position()
        fg = windows.foreground_window()
        title = fg.title if fg else "(none)"
        png = cap.to_png()

        return ToolResult(
            text=(
                f"Captured {cap.width}x{cap.height} at ({cap.origin_x}, {cap.origin_y}). "
                f"Cursor at ({cx}, {cy}). Active window: {title!r}. "
                f"Image {len(png) // 1024} KB."
            ),
            data={
                "width": cap.width,
                "height": cap.height,
                "origin": [cap.origin_x, cap.origin_y],
                "cursor": [cx, cy],
                "active_window": title,
                "png_bytes": len(png),
            },
            image_png=png,
        )

    @reg.tool(
        name="Snapshot",
        description=(
            "Inspect the desktop: active window, open windows, and optionally the "
            "interactive elements inside the foreground window. Element ids can be "
            "passed to Click/Type/Scroll as `label` to target controls semantically "
            "instead of by coordinates. Enable `elements` only when you need to locate "
            "a control, since the list is long."
        ),
        schema=obj(
            {
                "elements": prop(
                    "boolean", "Include the interactive element tree of the active window"
                ),
                "all_windows": prop(
                    "boolean", "List every open window, not just the active one"
                ),
                "image": prop("boolean", "Also return a screenshot (default true)"),
            }
        ),
        risk=Risk.LOW,
        category="observe",
    )
    def snapshot(
        elements: bool = False,
        all_windows: bool = False,
        image: bool = True,
    ) -> ToolResult:
        from ..win32.input import cursor_position

        fg = windows.foreground_window()
        wins = windows.list_windows() if all_windows else ([fg] if fg else [])

        lines: list[str] = []
        active_title = fg.title if fg else "(none)"
        lines.append(f"Active window: {active_title!r}")

        if all_windows:
            lines.append(f"Open windows ({len(wins)}):")
            for w in wins:
                flag = " [active]" if w.foreground else ""
                mini = " [minimized]" if w.minimized else ""
                lines.append(f"  - {w.title!r} pid={w.pid}{flag}{mini}")

        data: dict[str, Any] = {
            "active_window": active_title,
            "windows": [w.as_dict() for w in wins],
        }

        if elements and fg:
            els = windows.enum_elements(fg.hwnd)
            interactive = [e for e in els if e.interactive]
            lines.append(f"Interactive elements in {active_title!r} ({len(interactive)}):")
            for e in interactive[:40]:
                label = e.text or e.class_name
                lines.append(f"  id={e.id} {label!r} class={e.class_name} center={e.center}")
            if not interactive:
                lines.append(
                    "  (none found — this application may not expose a control tree; "
                    "fall back to coordinates from the screenshot)"
                )
            data["elements"] = [e.as_dict() for e in interactive]

        png: bytes | None = None
        if image:
            cap = screen.capture_screen()
            png = cap.to_png()
            cx, cy = cursor_position()
            lines.append(f"Screen {cap.width}x{cap.height}, cursor at ({cx}, {cy}).")
            data["screen"] = {"width": cap.width, "height": cap.height, "cursor": [cx, cy]}

        return ToolResult(text="\n".join(lines), data=data, image_png=png)

    @reg.tool(
        name="DisplayInventory",
        description=(
            "List displays with bounds, work area, DPI and scale factor. Use this to "
            "convert between screenshot pixels and screen coordinates on multi-monitor "
            "or high-DPI setups."
        ),
        schema=obj({}),
        risk=Risk.LOW,
        category="observe",
    )
    def display_inventory() -> ToolResult:
        info = screen.virtual_bounds()
        lines = [f"Virtual desktop: {info['bounds']} across {info['monitor_count']} display(s)"]
        for d in info["displays"]:
            flag = " [primary]" if d["primary"] else ""
            lines.append(
                f"  display {d['index']}: {d['bounds'][2]}x{d['bounds'][3]} "
                f"at ({d['bounds'][0]}, {d['bounds'][1]}) "
                f"dpi={d['dpi']} scale={d['scale']}{flag}"
            )
        return ToolResult(text="\n".join(lines), data=info)


__all__ = ["register"]
