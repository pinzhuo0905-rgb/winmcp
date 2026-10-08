"""窗口层：枚举、聚焦、以及元素树（用于语义定位）。

## 元素树怎么来的

Windows-MCP 用 UIAutomation 拿无障碍树。UIA 在纯 ctypes 下要手写 COM vtable
调用，非常容易出错。这里改用 **Win32 控件树**：``EnumChildWindows`` 能枚举出
窗口里的每个子控件，配合 ``GetClassNameW`` / ``GetWindowTextW`` / ``GetWindowRect``
就能拿到「类名 + 文本 + 坐标」三元组。

覆盖范围：传统 Win32 程序的按钮、输入框、列表、菜单等——够用且零依赖。
**局限**：UWP / WinUI / 部分 Electron 应用把控件画在单个表面上，控件树是空的。
这种情况下退回坐标定位。这个取舍在 README 里有说明。
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass, field

from .const import (
    GW_OWNER,
    RECT,
    SW_MAXIMIZE,
    SW_MINIMIZE,
    SW_RESTORE,
    SW_SHOW,
    WS_EX_TOOLWINDOW,
    WS_VISIBLE,
    user32,
)

#: 这些类名是容器而非可交互控件，出现在元素树里只会干扰定位
_CONTAINER_CLASSES = {
    "Static", "SysHeader32", "ToolbarWindow32", "ReBarWindow32", "msctls_statusbar32",
    "#32770", "MDIClient", "ScrollBar", "SysPager", "SysTabControl32",
}

#: 这些类名代表可交互控件（传统 Win32 命名）
INTERACTIVE_CLASSES = {
    "Button", "Edit", "ComboBox", "ListBox", "msctls_trackbar32",
    "msctls_updown32", "SysListView32", "SysTreeView32", "RichEdit20W",
    "RichEdit50W", "ComboBoxEx32", "msctls_progress32",
}

#: 现代框架（WinUI / WPF / UWP）用的是描述性类名，如 NotepadTextBox、
#: RichEditD2DPT。光靠精确类名匹配会全部漏掉，所以再按关键词兜一层。
_INTERACTIVE_PATTERN = re.compile(
    r"edit|textbox|textfield|button|combobox|listbox|listview|treeview|"
    r"checkbox|radio|trackbar|slider|updown|spinner|hyperlink|link|"
    r"menubar|menuitem|tabitem|scrollbar",
    re.IGNORECASE,
)


def is_interactive(class_name: str) -> bool:
    """判断某个类名是不是可交互控件。"""
    return class_name in INTERACTIVE_CLASSES or bool(_INTERACTIVE_PATTERN.search(class_name))


@dataclass
class Element:
    """窗口树里的一个元素。"""

    id: int
    """从 1 开始的稳定序号——这就是 ``label`` 参数要传的值。"""

    hwnd: int
    class_name: str
    text: str
    rect: list[int]
    interactive: bool
    visible: bool
    enabled: bool

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.rect
        return (x1 + x2) // 2, (y1 + y2) // 2

    def matches(self, needle: str) -> bool:
        """按文本或类名匹配。

        类名用子串匹配——现代框架的类名很长（``NotepadTextBox``），
        要求精确相等等于永远匹配不上。
        """
        n = (needle or "").strip().lower()
        if not n:
            return False
        return n in self.text.lower() or n in self.class_name.lower()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "class": self.class_name,
            "text": self.text,
            "rect": self.rect,
            "center": list(self.center),
            "interactive": self.interactive,
            "enabled": self.enabled,
        }


@dataclass
class WindowInfo:
    """一个顶层窗口。"""

    hwnd: int
    title: str
    class_name: str
    pid: int
    rect: list[int]
    visible: bool
    minimized: bool
    foreground: bool
    z_order: int
    elements: list[Element] = field(default_factory=list)

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.rect
        return (x1 + x2) // 2, (y1 + y2) // 2

    def as_dict(self, with_elements: bool = False) -> dict:
        out = {
            "title": self.title,
            "class": self.class_name,
            "pid": self.pid,
            "rect": self.rect,
            "visible": self.visible,
            "minimized": self.minimized,
            "foreground": self.foreground,
            "z_order": self.z_order,
        }
        if with_elements:
            out["elements"] = [e.as_dict() for e in self.elements]
        return out


# ---------------------------------------------------------------------------
def _window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_rect(hwnd: int) -> list[int]:
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return [0, 0, 0, 0]
    return r.as_list()


def _is_alt_tab_worthy(hwnd: int) -> bool:
    """过滤掉工具窗口和没有标题的隐藏窗口——它们不该出现在列表里。"""
    if not user32.IsWindowVisible(hwnd):
        return False
    if user32.GetWindowLongW(hwnd, -20) & WS_EX_TOOLWINDOW:
        return False
    if user32.GetWindow(hwnd, GW_OWNER) and not _window_text(hwnd):
        return False
    return bool(_window_text(hwnd))


def list_windows(include_elements: bool = False, only_visible: bool = True) -> list[WindowInfo]:
    """按 Z 序枚举顶层窗口（最前面的排最前）。"""
    fg = user32.GetForegroundWindow()
    out: list[WindowInfo] = []
    order = 0

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd: int, _lp: int) -> int:
        nonlocal order
        if only_visible and not _is_alt_tab_worthy(hwnd):
            return 1
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(
            WindowInfo(
                hwnd=hwnd,
                title=_window_text(hwnd),
                class_name=_class_name(hwnd),
                pid=int(pid.value),
                rect=_window_rect(hwnd),
                visible=bool(user32.IsWindowVisible(hwnd)),
                minimized=bool(user32.IsIconic(hwnd)),
                foreground=(hwnd == fg),
                z_order=order,
            )
        )
        order += 1
        return 1

    user32.EnumWindows(EnumProc(_cb), 0)

    if include_elements:
        for w in out:
            w.elements = enum_elements(w.hwnd)
    return out


def find_window(name: str) -> WindowInfo | None:
    """按标题（子串、忽略大小写）找窗口。找不到返回 None。"""
    if not name:
        return None
    needle = name.strip().lower()
    windows = list_windows()
    # 精确匹配优先，避免「记事本」误命中「记事本 - 帮助」
    for w in windows:
        if w.title.lower() == needle:
            return w
    for w in windows:
        if needle in w.title.lower():
            return w
    return None


def foreground_window() -> WindowInfo | None:
    """当前焦点窗口。"""
    fg = user32.GetForegroundWindow()
    if not fg:
        return None
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    return WindowInfo(
        hwnd=fg,
        title=_window_text(fg),
        class_name=_class_name(fg),
        pid=int(pid.value),
        rect=_window_rect(fg),
        visible=bool(user32.IsWindowVisible(fg)),
        minimized=bool(user32.IsIconic(fg)),
        foreground=True,
        z_order=0,
    )


def foreground_title() -> str:
    """只要标题——焦点守卫每次操作前都会调它，所以要走最短路径。"""
    return _window_text(user32.GetForegroundWindow())


# ---------------------------------------------------------------------------
def enum_elements(hwnd: int, include_containers: bool = False) -> list[Element]:
    """枚举窗口内的控件，返回带序号、类名、文本、坐标的元素列表。

    这些元素的 ``id`` 就是工具里 ``label`` 参数要传的值——
    用语义定位替代坐标，界面挪动几像素也不会点错。
    """
    found: list[tuple[int, str, str, list[int], bool, bool]] = []

    EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(child: int, _lp: int) -> int:
        if not user32.IsWindowVisible(child):
            return 1
        cls = _class_name(child)
        text = _window_text(child)
        if not include_containers and cls in _CONTAINER_CLASSES and not text:
            return 1
        rect = _window_rect(child)
        if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
            return 1
        found.append(
            (
                child,
                cls,
                text,
                rect,
                is_interactive(cls),
                bool(user32.IsWindowEnabled(child)),
            )
        )
        return 1

    user32.EnumChildWindows(hwnd, EnumChildProc(_cb), 0)

    return [
        Element(
            id=i,
            hwnd=h,
            class_name=c,
            text=t,
            rect=r,
            interactive=inter,
            visible=True,
            enabled=en,
        )
        for i, (h, c, t, r, inter, en) in enumerate(found, start=1)
    ]


def resolve_element(hwnd: int, label: int | str) -> Element | None:
    """把 ``label``（序号或文本）解析成元素。

    支持三种写法：序号 ``3``、序号字符串 ``"3"``、文本 ``"保存"``。
    """
    elements = enum_elements(hwnd, include_containers=True)
    if isinstance(label, int) or (isinstance(label, str) and label.strip().isdigit()):
        want = int(label)
        for e in elements:
            if e.id == want:
                return e
        return None
    needle = str(label).strip()
    for e in elements:
        if e.matches(needle):
            return e
    return None


# ---------------------------------------------------------------------------
def activate(hwnd: int, restore: bool = True) -> bool:
    """把窗口带到前台。最小化的先还原。"""
    if restore and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)

    if user32.SetForegroundWindow(hwnd):
        return True

    # SetForegroundWindow 在前台锁被别的进程持有时会失败。
    # 常见的绕过办法是 AttachThreadInput 挂到当前前台线程上再设。
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    if fg_thread and fg_thread != cur_thread:
        user32.AttachThreadInput(fg_thread, cur_thread, True)
        ok = bool(user32.SetForegroundWindow(hwnd))
        user32.AttachThreadInput(fg_thread, cur_thread, False)
        return ok
    return False


def minimize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_MINIMIZE)


def maximize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd)) and bool(
        user32.GetWindowLongW(hwnd, -16) & WS_VISIBLE
    )


__all__ = [
    "INTERACTIVE_CLASSES",
    "Element",
    "WindowInfo",
    "activate",
    "enum_elements",
    "find_window",
    "foreground_title",
    "foreground_window",
    "is_interactive",
    "is_visible",
    "list_windows",
    "maximize",
    "minimize",
    "resolve_element",
]
