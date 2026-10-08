"""系统类工具：App / Process / Clipboard / Notification / Wait / WaitFor。

其中 ``WaitFor`` 是稳定性关键——**用条件等待替代盲等**，
这是「界面还没稳定就操作」这类错误的直接解法。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from ..win32 import clipboard as clip
from ..win32 import process as proc
from ..win32 import screen, windows
from .base import Registry, Risk, ToolError, ToolResult, obj, prop


def _launch_target(name: str) -> None:
    """启动程序。优先当作可执行路径，否则交给 shell 解析。"""
    p = Path(name)
    if p.is_file():
        subprocess.Popen([str(p)], close_fds=True)
        return
    # 交给系统 shell 解析——这样 "notepad"、"calc" 这类短名也能用
    os.startfile(name)  # type: ignore[attr-defined]


#: Upper bound on a single Wait. An unbounded sleep is an easy way for a model to
#: stall the whole loop; anything longer should be expressed as a WaitFor condition.
MAX_WAIT_SECONDS = 30.0


def clamp_wait(seconds: float) -> float:
    """Clamp a requested wait into ``[0, MAX_WAIT_SECONDS]``."""
    try:
        value = float(seconds)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"seconds 必须是数字，收到 {seconds!r}") from exc
    if value != value:  # NaN
        raise ToolError("seconds 不能是 NaN")
    return max(0.0, min(value, MAX_WAIT_SECONDS))


def register(reg: Registry) -> None:
    # -----------------------------------------------------------------------
    @reg.tool(
        name="App",
        description=(
            "Launch an application, or manage windows. mode=launch starts a program "
            "by name ('notepad') or full path. mode=switch focuses a window by title. "
            "mode=minimize/maximize/restore change window state."
        ),
        schema=obj(
            {
                "mode": prop(
                    "string",
                    "launch (default), switch, minimize, maximize, restore, close",
                ),
                "name": prop("string", "Program name/path to launch, or window title"),
                "args": prop("array", "Arguments for launch", items={"type": "string"}),
                "wait": prop(
                    "number", "Seconds to wait for the window to appear after launch"
                ),
            }
        ),
        risk=Risk.HIGH,
        category="system",
    )
    def app(
        mode: str = "launch",
        name: str = "",
        args: list[str] | None = None,
        wait: float = 0.0,
    ) -> ToolResult:
        m = (mode or "launch").lower()

        if m == "launch":
            if not name:
                raise ToolError("launch 需要 name")
            if args and Path(name).is_file():
                subprocess.Popen([name, *args], close_fds=True)
            else:
                _launch_target(name)

            detail = f"Launched {name!r}"
            if wait and wait > 0:
                deadline = time.time() + float(wait)
                found = None
                while time.time() < deadline:
                    found = windows.find_window(Path(name).stem)
                    if found:
                        break
                    time.sleep(0.25)
                detail += (
                    f"; window {found.title!r} appeared" if found else f"; waited {wait}s"
                )
            return ToolResult(text=detail, data={"mode": m, "name": name})

        w = windows.find_window(name) if name else windows.foreground_window()
        if not w:
            raise ToolError(f"找不到窗口：{name!r}。用 Snapshot(all_windows=true) 看有哪些。")

        if m == "switch":
            ok = windows.activate(w.hwnd)
            if not ok:
                raise ToolError(f"无法激活 {w.title!r}（前台锁被别的程序占用）")
            time.sleep(0.15)
            return ToolResult(
                text=f"Focused {w.title!r}", data={"title": w.title, "hwnd": w.hwnd}
            )
        if m == "minimize":
            windows.minimize(w.hwnd)
            return ToolResult(text=f"Minimized {w.title!r}")
        if m == "maximize":
            windows.maximize(w.hwnd)
            return ToolResult(text=f"Maximized {w.title!r}")
        if m == "restore":
            windows.activate(w.hwnd)
            return ToolResult(text=f"Restored {w.title!r}")
        if m == "close":
            from ..win32.const import user32

            user32.PostMessageW(w.hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return ToolResult(text=f"Asked {w.title!r} to close")

        raise ToolError(f"不认识的 mode：{mode}")

    # -----------------------------------------------------------------------
    @reg.tool(
        name="Process",
        description=(
            "List running processes or terminate one. mode=list shows pid, name and "
            "thread count; filter by `name`. mode=kill terminates by pid (or by name "
            "when pid is omitted)."
        ),
        schema=obj(
            {
                "mode": prop("string", "list (default) or kill"),
                "name": prop("string", "Filter by process name substring, or target for kill"),
                "pid": prop("integer", "Process id (required for a precise kill)"),
                "limit": prop("integer", "Max rows for list, default 60"),
            },
            required=["mode"],
        ),
        risk=Risk.HIGH,
        category="system",
    )
    def process(
        mode: str,
        name: str = "",
        pid: int = 0,
        limit: int = 60,
    ) -> ToolResult:
        m = (mode or "list").lower()

        if m == "list":
            rows = proc.list_processes(name_filter=name, limit=int(limit))
            lines = [f"{len(rows)} process(es)" + (f" matching {name!r}" if name else "")]
            for p in rows:
                extra = f"  # {p.description}" if p.description else ""
                lines.append(f"  pid={p.pid:<7} {p.name}{extra}")
            return ToolResult(
                text="\n".join(lines),
                data={"count": len(rows), "processes": [p.as_dict() for p in rows]},
            )

        if m == "kill":
            targets: list[proc.Process] = []
            if int(pid) > 0:
                targets = [p for p in proc.list_processes() if p.pid == int(pid)]
                if not targets:
                    raise ToolError(f"没有 pid={pid} 的进程")
            elif name:
                targets = proc.find_processes(name)
                if not targets:
                    raise ToolError(f"没有名字含 {name!r} 的进程")
            else:
                raise ToolError("kill 需要 pid 或 name")

            killed, failed = [], []
            for t in targets:
                (killed if proc.kill(t.pid) else failed).append(f"{t.name}({t.pid})")
            text = f"Terminated: {', '.join(killed) or 'none'}"
            if failed:
                text += f". Failed (access denied?): {', '.join(failed)}"
            return ToolResult(text=text, data={"killed": killed, "failed": failed})

        raise ToolError(f"不认识的 mode：{mode}")

    # -----------------------------------------------------------------------
    @reg.tool(
        name="Clipboard",
        description=(
            "Read or write the clipboard. mode=get returns the current text; "
            "mode=set replaces it with `text`; mode=clear empties it. Useful for "
            "moving large blocks of text without typing them out."
        ),
        schema=obj(
            {
                "mode": prop("string", "get, set, or clear"),
                "text": prop("string", "Text to put on the clipboard (mode=set)"),
            },
            required=["mode"],
        ),
        risk=Risk.HIGH,
        category="system",
    )
    def clipboard(mode: str, text: str = "") -> ToolResult:
        m = (mode or "get").lower()
        if m == "get":
            content = clip.get_text()
            preview = content[:500] + ("…" if len(content) > 500 else "")
            return ToolResult(
                text=f"Clipboard ({len(content)} chars): {preview!r}",
                data={"text": content, "length": len(content)},
            )
        if m == "set":
            clip.set_text(text)
            return ToolResult(
                text=f"Clipboard set to {len(text)} chars", data={"length": len(text)}
            )
        if m == "clear":
            clip.clear()
            return ToolResult(text="Clipboard cleared")
        raise ToolError(f"不认识的 mode：{mode}")

    # -----------------------------------------------------------------------
    @reg.tool(
        name="Notification",
        description="Show a desktop notification with a title and message.",
        schema=obj(
            {"title": prop("string", "Notification title"), "message": prop("string", "Body text")},
            required=["title", "message"],
        ),
        risk=Risk.LOW,
        category="system",
    )
    def notification(title: str, message: str) -> ToolResult:
        import ctypes

        from ..win32.const import user32

        # MessageBoxTimeoutW 是 user32 里的非公开导出，但各版本 Windows 都有。
        # 它能让弹窗自己消失，不必阻塞等用户点确定。
        try:
            fn = user32.MessageBoxTimeoutW
            fn.argtypes = [
                ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                ctypes.c_uint, ctypes.c_ushort, ctypes.c_uint,
            ]
            fn.restype = ctypes.c_int
            threading.Thread(
                target=fn, args=(0, message, title, 0x40, 0, 4000), daemon=True
            ).start()
            return ToolResult(text=f"Notified: {title!r}", data={"mode": "toast-timeout"})
        except AttributeError:
            def _show() -> None:
                user32.MessageBoxW(0, message, title, 0x40)

            threading.Thread(target=_show, daemon=True).start()
            return ToolResult(
                text=f"Notified: {title!r} (dismiss manually)", data={"mode": "messagebox"}
            )

    # -----------------------------------------------------------------------
    @reg.tool(
        name="Wait",
        description=(
            "Pause for a fixed number of seconds. Prefer WaitFor when you know what "
            "you are waiting for — a fixed sleep is either too short (click lands on a "
            "half-drawn window) or too long (wasted time)."
        ),
        schema=obj({"seconds": prop("number", "Seconds to pause, max 30")}, required=["seconds"]),
        risk=Risk.LOW,
        category="system",
    )
    def wait(seconds: float) -> ToolResult:
        s = clamp_wait(seconds)
        time.sleep(s)
        return ToolResult(text=f"Waited {s}s", data={"seconds": s})

    # -----------------------------------------------------------------------
    @reg.tool(
        name="WaitFor",
        description=(
            "Wait until a condition becomes true, polling internally. Far more reliable "
            "than Wait. Conditions: window (a window whose title contains `text` exists), "
            "gone (that window disappeared), element (a control matching `text` exists in "
            "the active window), title (the active window title contains `text`), "
            "change (the screen differs from now)."
        ),
        schema=obj(
            {
                "condition": prop(
                    "string", "window, gone, element, title, or change"
                ),
                "text": prop("string", "Text to match (window title or element label)"),
                "timeout": prop("number", "Max seconds to wait, default 10"),
                "interval": prop("number", "Poll interval in seconds, default 0.3"),
            },
            required=["condition"],
        ),
        risk=Risk.LOW,
        category="system",
    )
    def wait_for(
        condition: str,
        text: str = "",
        timeout: float = 10.0,
        interval: float = 0.3,
    ) -> ToolResult:
        cond = (condition or "").strip().lower()
        deadline = time.time() + max(0.1, float(timeout))
        step = max(0.05, float(interval))

        baseline: bytes | None = None
        if cond == "change":
            baseline = screen.capture_screen().to_png()

        polls = 0
        last = ""
        while time.time() < deadline:
            polls += 1

            if cond in ("window", "gone"):
                found = windows.find_window(text) if text else None
                if cond == "window" and found:
                    return ToolResult(
                        text=f"Window {found.title!r} appeared after {polls} polls",
                        data={"condition": cond, "polls": polls, "title": found.title},
                    )
                if cond == "gone" and not found:
                    return ToolResult(
                        text=f"Window matching {text!r} is gone after {polls} polls",
                        data={"condition": cond, "polls": polls},
                    )

            elif cond == "title":
                last = windows.foreground_title()
                if text.lower() in last.lower():
                    return ToolResult(
                        text=f"Active window is now {last!r} after {polls} polls",
                        data={"condition": cond, "polls": polls, "title": last},
                    )

            elif cond == "element":
                fg = windows.foreground_window()
                if fg:
                    hit = windows.resolve_element(fg.hwnd, text)
                    if hit is not None:
                        return ToolResult(
                            text=(
                                f"Element {text!r} appeared as {hit.class_name!r} "
                                f"at {hit.center} after {polls} polls"
                            ),
                            data={"condition": cond, "polls": polls, "center": list(hit.center)},
                        )

            elif cond == "change":
                now = screen.capture_screen().to_png()
                if now != baseline:
                    return ToolResult(
                        text=f"Screen changed after {polls} polls",
                        data={"condition": cond, "polls": polls},
                    )
            else:
                raise ToolError(
                    f"不认识的 condition：{condition!r}。"
                    "可用：window / gone / element / title / change"
                )

            time.sleep(step)

        raise ToolError(
            f"等待超时（{timeout}s，轮询 {polls} 次）：condition={cond!r} text={text!r}。"
            + (f" 当前焦点窗口是 {last!r}。" if last else "")
        )


__all__ = ["register"]
