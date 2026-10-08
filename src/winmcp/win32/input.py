"""输入层：鼠标与键盘，全部走 SendInput。

SendInput 比已废弃的 mouse_event / keybd_event 可靠，而且支持 Unicode 注入
（``KEYEVENTF_UNICODE``），所以中文、emoji 都能输入。
"""

from __future__ import annotations

import time

from .const import (
    _INPUTUNION,
    INPUT,
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    KEYBDINPUT,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MIDDLEDOWN,
    MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL,
    MOUSEINPUT,
    POINT,
    SM_CXVIRTUALSCREEN,
    SM_CYVIRTUALSCREEN,
    SM_XVIRTUALSCREEN,
    SM_YVIRTUALSCREEN,
    WHEEL_DELTA,
    user32,
)
from .const import (
    INPUT as _INPUT,  # noqa: F401 - 保持导出名一致
)

# ---------------------------------------------------------------------------
# 虚拟键码表
# ---------------------------------------------------------------------------
VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "pause": 0x13,
    "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
    "semicolon": 0xBA, "equals": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "backtick": 0xC0,
    "lbracket": 0xDB, "backslash": 0xDC, "rbracket": 0xDD, "quote": 0xDE,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i  # F1=0x70


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, union=_INPUTUNION(mi=MOUSEINPUT(dx, dy, data, flags, 0, 0)))


def _key(vk: int, flags: int = 0) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, union=_INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))


def _unicode(ch: str, flags: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUTUNION(ki=KEYBDINPUT(0, ord(ch), flags | KEYEVENTF_UNICODE, 0, 0)),
    )


def _send(*inputs: INPUT) -> None:
    """一次 SendInput 发出多个事件——比逐个发快，且不会被中途打断。"""
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, __import__("ctypes").sizeof(INPUT))
    if sent != len(inputs):
        import ctypes

        raise OSError(
            f"SendInput 只发送了 {sent}/{len(inputs)} 个事件"
            f"（错误码 {ctypes.get_last_error()}）"
        )


def virtual_screen() -> tuple[int, int, int, int]:
    """虚拟屏幕（含多显示器）的 (x, y, width, height)。"""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def to_absolute(x: int, y: int) -> tuple[int, int]:
    """把屏幕坐标转成 SendInput 要的 0–65535 归一化坐标。"""
    vx, vy, vw, vh = virtual_screen()
    nx = round((x - vx) * 65535 / max(vw - 1, 1))
    ny = round((y - vy) * 65535 / max(vh - 1, 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def cursor_position() -> tuple[int, int]:
    pt = POINT()
    user32.GetCursorPos(pt)
    return pt.x, pt.y


# ---------------------------------------------------------------------------
# 鼠标
# ---------------------------------------------------------------------------
def move(x: int, y: int, duration: float = 0.0, steps: int = 1) -> None:
    """移动鼠标。``duration`` > 0 时平滑移动——某些拖拽操作需要中间帧。"""
    if duration <= 0 or steps <= 1:
        nx, ny = to_absolute(x, y)
        _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))
        return

    sx, sy = cursor_position()
    for i in range(1, steps + 1):
        t = i / steps
        nx, ny = to_absolute(round(sx + (x - sx) * t), round(sy + (y - sy) * t))
        _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))
        time.sleep(duration / steps)


def _button_flags(button: str) -> tuple[int, int]:
    b = (button or "left").lower()
    if b == "right":
        return MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    if b == "middle":
        return MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    return MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP


def click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    clicks: int = 1,
) -> None:
    """点击。``clicks=2`` 是双击，``clicks=0`` 只悬停不点。"""
    if x is not None and y is not None:
        move(x, y)
    if clicks <= 0:
        return
    down, up = _button_flags(button)
    interval = user32.GetDoubleClickTime() / 1000.0 if clicks > 1 else 0.0
    for i in range(clicks):
        _send(_mouse(down), _mouse(up))
        if i < clicks - 1 and interval:
            time.sleep(min(interval / 2, 0.1))


def scroll(direction: str = "down", amount: int = 1, horizontal: bool = False) -> None:
    """滚轮。``amount`` 是「格数」，正数向上/向左。"""
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    delta = WHEEL_DELTA * (1 if amount >= 0 else -1)
    events = [_mouse(flag, 0, 0, delta) for _ in range(max(1, abs(amount)))]
    _send(*events)


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
    """按住左键从 (x1,y1) 拖到 (x2,y2)。"""
    move(x1, y1)
    time.sleep(0.05)
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))
    move(x2, y2, duration=duration, steps=12)
    time.sleep(0.05)
    _send(_mouse(MOUSEEVENTF_LEFTUP))


# ---------------------------------------------------------------------------
# 键盘
# ---------------------------------------------------------------------------
def type_text(text: str, interval: float = 0.0) -> None:
    """输入文本。走 Unicode 注入，所以中文、emoji 都能打。"""
    events: list[INPUT] = []
    for ch in text:
        if ch == "\n":
            events += [_key(VK["enter"]), _key(VK["enter"], KEYEVENTF_KEYUP)]
        elif ch == "\t":
            events += [_key(VK["tab"]), _key(VK["tab"], KEYEVENTF_KEYUP)]
        else:
            events += [_unicode(ch), _unicode(ch, KEYEVENTF_KEYUP)]
        if interval > 0:
            _send(*events)
            events = []
            time.sleep(interval)
    _send(*events)


def press_keys(keys: list[int], hold: float = 0.0) -> None:
    """按下并释放一组虚拟键码。"""
    for vk in keys:
        _send(_key(vk))
    if hold:
        time.sleep(hold)
    for vk in reversed(keys):
        _send(_key(vk, KEYEVENTF_KEYUP))


def shortcut(combo: str) -> None:
    """按 ``"ctrl+shift+s"`` 这种写法触发快捷键。"""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("快捷键为空")
    vks = []
    for p in parts:
        if p not in VK:
            raise ValueError(f"不认识的按键：{p}")
        vks.append(VK[p])
    press_keys(vks)


def wait_ms(milliseconds: int) -> None:
    """短暂停顿。点击后界面需要一点时间响应，完全不停会点空。"""
    if milliseconds > 0:
        time.sleep(milliseconds / 1000.0)


__all__ = [
    "VK",
    "click",
    "cursor_position",
    "drag",
    "move",
    "press_keys",
    "scroll",
    "shortcut",
    "to_absolute",
    "type_text",
    "virtual_screen",
    "wait_ms",
]
