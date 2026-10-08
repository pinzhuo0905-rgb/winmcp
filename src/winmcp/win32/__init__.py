"""winmcp.win32：Win32 API 的 ctypes 封装。

按能力分五个子模块：

    const.py     常量、结构体、函数签名声明
    input.py     鼠标与键盘（SendInput）
    screen.py    截图（GDI + 纯 Python PNG 编码）、显示器清单
    windows.py   窗口枚举、聚焦、元素树（语义定位）
    clipboard.py 剪贴板读写
    process.py   进程枚举与终止

这一层不含任何业务逻辑，也不做安全校验——那是 tools/ 的事。
"""

from . import clipboard, input, process, screen, windows

__all__ = ["clipboard", "input", "process", "screen", "windows"]
