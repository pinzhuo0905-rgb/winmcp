"""剪贴板层：读写 Unicode 文本。

剪贴板是全局独占资源，用不好会卡死别的程序。这里统一用 try/finally 保证
OpenClipboard 一定被 Close——否则整个系统剪贴板会锁死。
"""

from __future__ import annotations

import ctypes
import time

from .const import (
    CF_UNICODETEXT,
    GMEM_MOVEABLE,
    kernel32,
    user32,
)

#: The clipboard is a single global resource. Another process may hold it for a few
#: milliseconds, so a failed OpenClipboard is normal and worth retrying rather than
#: surfacing as an error.
_OPEN_ATTEMPTS = 12
_OPEN_DELAY = 0.03


def _open_clipboard() -> bool:
    for attempt in range(_OPEN_ATTEMPTS):
        if user32.OpenClipboard(0):
            return True
        if attempt < _OPEN_ATTEMPTS - 1:
            time.sleep(_OPEN_DELAY)
    return False


def _clipboard_busy() -> OSError:
    return OSError(
        f"剪贴板被别的程序占用（重试 {_OPEN_ATTEMPTS} 次仍失败）。"
        "稍后重试，或先关闭可能占用剪贴板的程序。"
    )


def get_text() -> str:
    """读剪贴板文本。没有文本内容时返回空串。"""
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    if not _open_clipboard():
        raise _clipboard_busy()
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> None:
    """写文本到剪贴板。

    整个「打开 → 清空 → 写入」序列都要重试，不能只重试打开：
    别的程序可能在 EmptyClipboard 之后、SetClipboardData 之前抢走剪贴板，
    这时写入会失败——重来一次就好。
    """
    last_error: str = ""
    for attempt in range(_OPEN_ATTEMPTS):
        if not user32.OpenClipboard(0):
            last_error = "OpenClipboard"
            time.sleep(_OPEN_DELAY)
            continue
        try:
            user32.EmptyClipboard()

            # 必须按 UTF-16 代码单元算长度，不能用 len(text)。
            # 非 BMP 字符（emoji、部分汉字扩展区）在 UTF-16 里占两个单元，
            # 用 len() 会少分配一个 wchar，末尾被截断。
            units = len(text.encode("utf-16-le")) // 2
            size = (units + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                last_error = "GlobalAlloc"
                continue
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                last_error = "GlobalLock"
                continue
            try:
                ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), size)
            finally:
                kernel32.GlobalUnlock(handle)

            if user32.SetClipboardData(CF_UNICODETEXT, handle):
                # 成功交出所有权后不能再 GlobalFree——系统接管了这块内存
                return
            kernel32.GlobalFree(handle)
            last_error = "SetClipboardData"
        finally:
            user32.CloseClipboard()

        if attempt < _OPEN_ATTEMPTS - 1:
            time.sleep(_OPEN_DELAY)

    raise OSError(
        f"写入剪贴板失败（{last_error}，重试 {_OPEN_ATTEMPTS} 次）。"
        "剪贴板被别的程序持续占用时会发生，稍后重试。"
    )


def clear() -> None:
    """清空剪贴板。"""
    if not _open_clipboard():
        raise _clipboard_busy()
    try:
        user32.EmptyClipboard()
    finally:
        user32.CloseClipboard()


__all__ = ["clear", "get_text", "set_text"]
