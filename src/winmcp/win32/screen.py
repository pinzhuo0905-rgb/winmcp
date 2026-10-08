"""屏幕层：GDI 截图 + 纯 Python PNG 编码 + 显示器清单。

## 为什么自己写 PNG 编码

GDI 只能给你原始像素。存成 BMP 的话，1366×768 一张就是 **4 MB**；
同样的画面存 PNG 只有 **约 150 KB**——差 20 倍以上。

而 PNG 只需要 zlib（标准库）+ 十几行格式代码，不值得为此引入 Pillow。
"""

from __future__ import annotations

import ctypes
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from .const import (
    BI_RGB,
    BITMAPINFOHEADER,
    CAPTUREBLT,
    DIB_RGB_COLORS,
    MDT_EFFECTIVE_DPI,
    POINT,
    RECT,
    SM_CMONITORS,
    SRCCOPY,
    gdi32,
    user32,
)

# ---------------------------------------------------------------------------
# PNG 编码
# ---------------------------------------------------------------------------
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(rgb_rows: list[bytes], width: int, height: int) -> bytes:
    """把 RGB 扫描线编码成 PNG。

    Args:
        rgb_rows: 每行 ``width * 3`` 字节的 RGB 数据。
        width: 像素宽。
        height: 像素高。
    """
    raw = bytearray()
    for row in rgb_rows:
        raw.append(0)  # 过滤器类型 0（None）——截图内容重复度高，zlib 已经压得很好
        raw += row

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit truecolor RGB
    return (
        _PNG_SIG
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def bgra_to_rgb_rows(buffer: bytes, width: int, height: int, stride: int) -> list[bytes]:
    """把 GDI 给的 BGRA 缓冲转成 RGB 扫描线。

    用 bytearray 切片赋值而不是逐像素循环——1366×768 有一百万像素，
    逐像素在 Python 里要好几秒，切片只要几十毫秒。
    """
    rows: list[bytes] = []
    for y in range(height):
        row = buffer[y * stride : (y + 1) * stride]
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::4]  # R
        rgb[1::3] = row[1::4]  # G
        rgb[2::3] = row[0::4]  # B
        rows.append(bytes(rgb))
    return rows


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------
@dataclass
class Capture:
    """一次截图的原始结果。"""

    width: int
    height: int
    origin_x: int
    origin_y: int
    rows: list[bytes]

    def to_png(self) -> bytes:
        return encode_png(self.rows, self.width, self.height)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.to_png())
        return p

    @property
    def png_bytes(self) -> int:
        return len(self.to_png())


def capture_region(x: int, y: int, width: int, height: int) -> Capture:
    """用 GDI BitBlt 抓一块屏幕区域。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"截图区域非法：{width}×{height}")

    hdc = user32.GetDC(0)
    if not hdc:
        raise OSError("GetDC 失败")
    memdc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    try:
        gdi32.SelectObject(memdc, bitmap)
        # CAPTUREBLT 让分层窗口（部分现代 UI）也能被抓到
        if not gdi32.BitBlt(memdc, 0, 0, width, height, hdc, x, y, SRCCOPY | CAPTUREBLT):
            raise OSError("BitBlt 失败")

        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # 负数 = 自上而下
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB

        stride = width * 4
        buf = ctypes.create_string_buffer(stride * height)
        if not gdi32.GetDIBits(
            memdc, bitmap, 0, height, buf, ctypes.byref(header), DIB_RGB_COLORS
        ):
            raise OSError("GetDIBits 失败")
        raw = buf.raw
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(0, hdc)

    return Capture(
        width=width,
        height=height,
        origin_x=x,
        origin_y=y,
        rows=bgra_to_rgb_rows(raw, width, height, stride),
    )


def capture_screen(display: int | None = None) -> Capture:
    """抓全屏（虚拟屏幕，含所有显示器）。``display`` 指定则只抓某一块。"""
    if display is None:
        from .input import virtual_screen

        x, y, w, h = virtual_screen()
        return capture_region(x, y, w, h)

    monitors = list_displays()
    if display < 0 or display >= len(monitors):
        raise ValueError(f"显示器索引越界：{display}（共 {len(monitors)} 块）")
    m = monitors[display]
    return capture_region(m.x, m.y, m.width, m.height)


# ---------------------------------------------------------------------------
# 显示器清单
# ---------------------------------------------------------------------------
@dataclass
class Display:
    index: int
    x: int
    y: int
    width: int
    height: int
    dpi: int
    primary: bool
    work_area: list[int]

    @property
    def scale(self) -> float:
        return round(self.dpi / 96.0, 2)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "bounds": [self.x, self.y, self.width, self.height],
            "work_area": self.work_area,
            "dpi": self.dpi,
            "scale": self.scale,
            "primary": self.primary,
        }


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
        ("szDevice", ctypes.c_wchar * 32),
    )


def list_displays() -> list[Display]:
    """列出所有显示器，含 DPI 与缩放比例。

    用 EnumDisplayMonitors + GetDpiForMonitor——多屏、混合 DPI 都能拿到，
    这比只看主屏尺寸靠谱得多。
    """
    try:
        shcore = ctypes.WinDLL("shcore")
        has_shcore = True
    except OSError:
        has_shcore = False

    monitors: list[Display] = []
    primary_hwnd = user32.GetForegroundWindow()
    _ = primary_hwnd  # 仅用于确认 user32 可用

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_double
    )

    def _cb(hmon: int, _hdc: int, _rect: object, _data: float) -> int:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            dpi_x, dpi_y = ctypes.c_uint(96), ctypes.c_uint(96)
            if has_shcore:
                try:
                    shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                except Exception:
                    pass
            idx = len(monitors)
            monitors.append(
                Display(
                    index=idx,
                    x=info.rcMonitor.left,
                    y=info.rcMonitor.top,
                    width=info.rcMonitor.width,
                    height=info.rcMonitor.height,
                    dpi=int(dpi_x.value),
                    primary=bool(info.dwFlags & 1),
                    work_area=info.rcWork.as_list(),
                )
            )
        return 1

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_cb), 0)
    monitors.sort(key=lambda m: (not m.primary, m.x, m.y))
    for i, m in enumerate(monitors):
        m.index = i
    return monitors


def virtual_bounds() -> dict:
    """虚拟屏幕整体信息——多屏时的总边界。"""
    from .input import virtual_screen

    x, y, w, h = virtual_screen()
    return {
        "bounds": [x, y, w, h],
        "monitor_count": user32.GetSystemMetrics(SM_CMONITORS),
        "displays": [m.as_dict() for m in list_displays()],
    }


def window_at(x: int, y: int) -> int:
    """某个屏幕坐标下最上层的窗口句柄。"""
    pt = POINT(x, y)
    return user32.WindowFromPoint(pt) or 0


__all__ = [
    "Capture",
    "Display",
    "bgra_to_rgb_rows",
    "capture_region",
    "capture_screen",
    "encode_png",
    "list_displays",
    "virtual_bounds",
    "window_at",
]
