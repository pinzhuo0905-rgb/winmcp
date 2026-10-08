"""Win32 layer tests — the parts that can be verified without touching the desktop.

Anything that moves the mouse or types is deliberately excluded: a test suite must
not hijack the machine it runs on.
"""

from __future__ import annotations

import struct
import zlib

from winmcp.win32 import clipboard, process, screen, windows
from winmcp.win32.input import VK, to_absolute, virtual_screen


# -- PNG encoding -----------------------------------------------------------
def test_png_has_a_valid_signature_and_chunks() -> None:
    rows = [bytes([255, 0, 0] * 4) for _ in range(4)]
    png = screen.encode_png(rows, 4, 4)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"IHDR" in png
    assert b"IDAT" in png
    assert png.endswith(b"IEND\xaeB`\x82")


def test_png_header_records_dimensions() -> None:
    rows = [bytes([0, 0, 0] * 7) for _ in range(3)]
    png = screen.encode_png(rows, 7, 3)
    # IHDR payload starts at byte 16: width, height as big-endian uint32
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (7, 3)


def test_png_declares_truecolor() -> None:
    png = screen.encode_png([bytes([1, 2, 3])], 1, 1)
    bit_depth, color_type = png[24], png[25]
    assert bit_depth == 8
    assert color_type == 2  # RGB


def test_png_payload_roundtrips_through_zlib() -> None:
    rows = [bytes([10, 20, 30] * 2), bytes([40, 50, 60] * 2)]
    png = screen.encode_png(rows, 2, 2)
    start = png.index(b"IDAT") + 4
    length = struct.unpack(">I", png[start - 8 : start - 4])[0]
    raw = zlib.decompress(png[start : start + length])
    # Each scanline is a filter byte followed by RGB triples
    assert raw[0] == 0
    assert raw[1:7] == bytes([10, 20, 30] * 2)
    assert raw[7] == 0
    assert raw[8:14] == bytes([40, 50, 60] * 2)


def test_bgra_to_rgb_reorders_channels() -> None:
    # one pixel: B=3, G=2, R=1, A=255
    rows = screen.bgra_to_rgb_rows(bytes([3, 2, 1, 255]), 1, 1, 4)
    assert rows == [bytes([1, 2, 3])]


def test_bgra_handles_multiple_rows_and_stride_padding() -> None:
    buf = bytes([3, 2, 1, 255]) + bytes([6, 5, 4, 255])
    rows = screen.bgra_to_rgb_rows(buf, 1, 2, 4)
    assert rows == [bytes([1, 2, 3]), bytes([4, 5, 6])]


def test_capture_region_rejects_invalid_size() -> None:
    import pytest

    with pytest.raises(ValueError):
        screen.capture_region(0, 0, 0, 10)


# -- capture ----------------------------------------------------------------
def test_capture_screen_produces_matching_dimensions() -> None:
    cap = screen.capture_screen()
    x, y, w, h = virtual_screen()
    assert (cap.width, cap.height) == (w, h)
    assert (cap.origin_x, cap.origin_y) == (x, y)


def test_capture_rows_match_height() -> None:
    assert len(screen.capture_screen().rows) == virtual_screen()[3]


def test_capture_to_png_is_smaller_than_raw() -> None:
    """The whole point of PNG over BMP."""
    cap = screen.capture_screen()
    raw_size = cap.width * cap.height * 4
    assert len(cap.to_png()) < raw_size / 5


def test_displays_are_listed() -> None:
    displays = screen.list_displays()
    assert len(displays) >= 1
    assert sum(1 for d in displays if d.primary) == 1


def test_display_scale_is_derived_from_dpi() -> None:
    for d in screen.list_displays():
        assert d.scale == round(d.dpi / 96.0, 2)


def test_virtual_bounds_has_expected_keys() -> None:
    info = screen.virtual_bounds()
    assert {"bounds", "monitor_count", "displays"} <= set(info)


# -- input helpers (no actual input is sent) --------------------------------
def test_vk_table_covers_common_keys() -> None:
    for key in ("ctrl", "alt", "shift", "enter", "esc", "tab", "f1", "a", "5"):
        assert key in VK


def test_to_absolute_stays_in_range() -> None:
    for x, y in ((0, 0), (1920, 1080), (-1920, 0), (99999, 99999)):
        nx, ny = to_absolute(x, y)
        assert 0 <= nx <= 65535
        assert 0 <= ny <= 65535


# -- clipboard --------------------------------------------------------------
def _clipboard_roundtrip(text: str, attempts: int = 5) -> str:
    """Set then read back, retrying.

    The clipboard is a single global resource — any other process on the machine can
    take it between our two calls. Retrying at the test level reflects that reality
    rather than pretending it does not exist.
    """
    import time

    last = ""
    for _ in range(attempts):
        try:
            clipboard.set_text(text)
            last = clipboard.get_text()
            if last == text:
                return last
        except OSError as exc:
            last = f"<{exc}>"
        time.sleep(0.08)
    return last


def test_clipboard_roundtrip() -> None:
    before = clipboard.get_text()
    try:
        assert _clipboard_roundtrip("winmcp-roundtrip") == "winmcp-roundtrip"
    finally:
        clipboard.set_text(before) if before else clipboard.clear()


def test_clipboard_handles_unicode() -> None:
    """Non-BMP characters are two UTF-16 units — sizing the buffer with len() truncates."""
    before = clipboard.get_text()
    try:
        text = "中文、emoji 🚀 和 symbols ±"
        assert _clipboard_roundtrip(text) == text
    finally:
        clipboard.set_text(before) if before else clipboard.clear()


# -- process ----------------------------------------------------------------
def test_processes_are_listed() -> None:
    procs = process.list_processes()
    assert len(procs) > 0
    assert all(p.pid >= 0 for p in procs)


def test_process_filter_matches_substring() -> None:
    for p in process.find_processes("system"):
        assert "system" in p.name.lower()


def test_process_limit_is_respected() -> None:
    assert len(process.list_processes(limit=5)) <= 5


def test_known_processes_get_descriptions() -> None:
    names = {p.name.lower() for p in process.list_processes()}
    if "explorer.exe" in names:
        hit = next(p for p in process.list_processes() if p.name.lower() == "explorer.exe")
        assert hit.description


def test_kill_invalid_pid_returns_false() -> None:
    assert process.kill(999_999_999) is False


# -- windows ----------------------------------------------------------------
def test_windows_are_enumerated() -> None:
    assert isinstance(windows.list_windows(), list)


def test_foreground_title_is_a_string() -> None:
    assert isinstance(windows.foreground_title(), str)


def test_foreground_window_has_plausible_fields() -> None:
    fg = windows.foreground_window()
    if fg is not None:
        assert isinstance(fg.title, str)
        assert len(fg.rect) == 4
        assert fg.hwnd != 0


def test_find_window_returns_none_for_nonsense() -> None:
    assert windows.find_window("zzz-no-such-window-zzz") is None


def test_find_window_empty_name_returns_none() -> None:
    assert windows.find_window("") is None


def test_element_tree_is_a_list() -> None:
    fg = windows.foreground_window()
    if fg is not None:
        assert isinstance(windows.enum_elements(fg.hwnd), list)


def test_element_ids_are_one_based_and_contiguous() -> None:
    fg = windows.foreground_window()
    if fg is None:
        return
    els = windows.enum_elements(fg.hwnd, include_containers=True)
    assert [e.id for e in els] == list(range(1, len(els) + 1))


def test_element_center_is_inside_its_rect() -> None:
    fg = windows.foreground_window()
    if fg is None:
        return
    for e in windows.enum_elements(fg.hwnd, include_containers=True):
        x1, y1, x2, y2 = e.rect
        cx, cy = e.center
        assert x1 <= cx <= x2
        assert y1 <= cy <= y2


def test_interactive_class_detection_covers_modern_names() -> None:
    """Modern frameworks use descriptive class names, not the classic Win32 ones."""
    assert windows.is_interactive("NotepadTextBox") is True
    assert windows.is_interactive("RichEditD2DPT") is True
    assert windows.is_interactive("Button") is True
    assert windows.is_interactive("Microsoft.UI.Content.DesktopChildSiteBridge") is False


def test_element_matches_class_substring() -> None:
    el = windows.Element(
        id=1, hwnd=1, class_name="NotepadTextBox", text="", rect=[0, 0, 10, 10],
        interactive=True, visible=True, enabled=True,
    )
    assert el.matches("textbox") is True
    assert el.matches("nope") is False


def test_element_matches_text() -> None:
    el = windows.Element(
        id=1, hwnd=1, class_name="Button", text="Save As", rect=[0, 0, 10, 10],
        interactive=True, visible=True, enabled=True,
    )
    assert el.matches("save") is True


def test_resolve_element_by_id_and_label() -> None:
    fg = windows.foreground_window()
    if fg is None:
        return
    els = windows.enum_elements(fg.hwnd, include_containers=True)
    if not els:
        return
    first = els[0]
    assert windows.resolve_element(fg.hwnd, first.id) is not None
    assert windows.resolve_element(fg.hwnd, str(first.id)) is not None
    assert windows.resolve_element(fg.hwnd, 999_999) is None
