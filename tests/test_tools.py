"""Tool registry tests — coverage, schema shape, and dispatch."""

from __future__ import annotations

import pytest

from winmcp.tools import TOOL_NAMES, Risk, ToolError, build_registry


@pytest.fixture
def reg():
    return build_registry()


def test_all_eighteen_tools_registered(reg) -> None:
    assert len(reg.names()) == 18


def test_tool_names_match_the_expected_surface(reg) -> None:
    """Every capability in the reference surface must be present."""
    missing = set(TOOL_NAMES) - set(reg.names())
    assert not missing, f"missing tools: {sorted(missing)}"


def test_registration_covers_the_expected_names(reg) -> None:
    assert set(reg.names()) == set(TOOL_NAMES)


def test_registration_order_is_deterministic() -> None:
    """Stable ordering matters: the tool list is part of the prompt, and a changing
    order defeats prompt caching."""
    assert build_registry().names() == build_registry().names()


def test_every_tool_has_description_and_schema(reg) -> None:
    for t in reg.all():
        assert t.description.strip(), f"{t.name} has no description"
        assert t.schema.get("type") == "object", f"{t.name} schema root must be an object"
        assert "properties" in t.schema, f"{t.name} schema has no properties"


def test_descriptions_stay_concise(reg) -> None:
    """Descriptions go into the prompt on every turn — a runaway description is a
    permanent tax. 600 chars is a generous ceiling."""
    for t in reg.all():
        assert len(t.description) <= 600, f"{t.name} description is {len(t.description)} chars"


def test_required_fields_reference_real_properties(reg) -> None:
    for t in reg.all():
        props = set(t.schema.get("properties", {}))
        for req in t.schema.get("required", []):
            assert req in props, f"{t.name}: required {req!r} is not a declared property"


def test_risk_levels_are_assigned(reg) -> None:
    for t in reg.all():
        assert isinstance(t.risk, Risk)


def test_read_only_tools_are_low_risk(reg) -> None:
    for name in ("Screenshot", "Snapshot", "DisplayInventory", "Wait", "WaitFor"):
        assert reg.get(name).risk is Risk.LOW


def test_mutating_tools_are_high_risk(reg) -> None:
    for name in ("Click", "Type", "FileSystem", "Process"):
        assert reg.get(name).risk is Risk.HIGH


def test_mcp_schema_shape(reg) -> None:
    for entry in reg.schemas():
        assert set(entry) == {"name", "description", "inputSchema"}


def test_subset_narrows_the_registry(reg) -> None:
    sub = reg.subset(["Screenshot", "Click"])
    assert sub.names() == ["Screenshot", "Click"]
    assert sub.schema_chars() < reg.schema_chars()


def test_subset_ignores_unknown_names(reg) -> None:
    assert reg.subset(["Screenshot", "NoSuchTool"]).names() == ["Screenshot"]


def test_duplicate_registration_is_rejected(reg) -> None:
    with pytest.raises(ValueError):
        reg.add(reg.get("Screenshot"))


def test_invoke_unknown_tool_raises(reg) -> None:
    with pytest.raises(ToolError) as exc:
        reg.invoke("NoSuchTool")
    assert "NoSuchTool" in str(exc.value)


def test_invoke_wraps_handler_errors(reg) -> None:
    """Handler failures must surface as ToolError with a useful message."""
    with pytest.raises(ToolError):
        reg.invoke("FileSystem", {"mode": "read", "path": "C:/definitely/not/here.xyz"})


def test_invoke_bad_arguments_raises_tool_error(reg) -> None:
    with pytest.raises(ToolError):
        reg.invoke("Click", {"no_such_argument": 1})


def test_display_inventory_runs(reg) -> None:
    result = reg.invoke("DisplayInventory")
    assert "monitor_count" in result.data
    assert result.data["monitor_count"] >= 1


def test_snapshot_without_image_is_cheap(reg) -> None:
    """The cheap path must not capture the screen."""
    result = reg.invoke("Snapshot", {"image": False})
    assert result.image_png is None
    assert "active_window" in result.data


def test_screenshot_returns_png(reg) -> None:
    result = reg.invoke("Screenshot")
    assert result.image_png is not None
    assert result.image_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_clipboard_roundtrip(reg) -> None:
    import time

    before = reg.invoke("Clipboard", {"mode": "get"}).data["text"]
    try:
        # The clipboard is shared with every other process on the machine, so a
        # single attempt is inherently racy — retry like a real caller would.
        got = ""
        for _ in range(5):
            reg.invoke("Clipboard", {"mode": "set", "text": "winmcp-test-value"})
            got = reg.invoke("Clipboard", {"mode": "get"}).data["text"]
            if got == "winmcp-test-value":
                break
            time.sleep(0.08)
        assert got == "winmcp-test-value"
    finally:
        if before:
            reg.invoke("Clipboard", {"mode": "set", "text": before})
        else:
            reg.invoke("Clipboard", {"mode": "clear"})


def test_clipboard_bad_mode_raises(reg) -> None:
    with pytest.raises(ToolError):
        reg.invoke("Clipboard", {"mode": "frobnicate"})


def test_wait_clamps_absurd_durations() -> None:
    """An unbounded sleep is an easy way for a model to stall the loop."""
    from winmcp.tools.system import MAX_WAIT_SECONDS, clamp_wait

    assert clamp_wait(999) == MAX_WAIT_SECONDS
    assert clamp_wait(-5) == 0.0
    assert clamp_wait(0.5) == 0.5


def test_wait_rejects_non_numeric() -> None:
    from winmcp.tools.system import clamp_wait

    with pytest.raises(ToolError):
        clamp_wait("soon")


def test_wait_actually_pauses(reg) -> None:
    result = reg.invoke("Wait", {"seconds": 0.05})
    assert result.data["seconds"] == 0.05


def test_wait_for_unknown_condition_raises(reg) -> None:
    with pytest.raises(ToolError):
        reg.invoke("WaitFor", {"condition": "whenever", "timeout": 0.2})


def test_wait_for_title_times_out_with_a_clear_message(reg) -> None:
    with pytest.raises(ToolError) as exc:
        reg.invoke("WaitFor", {"condition": "title", "text": "zzz-no-such-window", "timeout": 0.4})
    assert "timed out" in str(exc.value).lower() or "超时" in str(exc.value)


def test_filesystem_write_read_delete(tmp_path) -> None:
    reg = build_registry()
    target = tmp_path / "hello.txt"

    reg.invoke("FileSystem", {"mode": "write", "path": str(target), "content": "line1\nline2\n"})
    assert target.read_text(encoding="utf-8") == "line1\nline2\n"

    read = reg.invoke("FileSystem", {"mode": "read", "path": str(target)})
    assert "line1" in read.data["content"]
    assert read.data["total_lines"] == 2

    reg.invoke("FileSystem", {"mode": "delete", "path": str(target)})
    assert not target.exists()


def test_filesystem_append(tmp_path) -> None:
    reg = build_registry()
    f = tmp_path / "a.txt"
    reg.invoke("FileSystem", {"mode": "write", "path": str(f), "content": "a"})
    reg.invoke("FileSystem", {"mode": "append", "path": str(f), "content": "b"})
    assert f.read_text(encoding="utf-8") == "ab"


def test_filesystem_directory_delete_requires_recursive(tmp_path) -> None:
    """Destructive operations must be explicit — no silent recursive removal."""
    reg = build_registry()
    d = tmp_path / "sub"
    (d / "nested").mkdir(parents=True)
    (d / "nested" / "f.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ToolError) as exc:
        reg.invoke("FileSystem", {"mode": "delete", "path": str(d)})
    assert "recursive" in str(exc.value)

    reg.invoke("FileSystem", {"mode": "delete", "path": str(d), "recursive": True})
    assert not d.exists()


def test_filesystem_list_and_search(tmp_path) -> None:
    reg = build_registry()
    (tmp_path / "one.log").write_text("1", encoding="utf-8")
    (tmp_path / "two.txt").write_text("2", encoding="utf-8")

    listing = reg.invoke("FileSystem", {"mode": "list", "path": str(tmp_path)})
    assert listing.data["count"] == 2

    found = reg.invoke("FileSystem", {"mode": "search", "path": str(tmp_path), "pattern": "*.log"})
    assert len(found.data["matches"]) == 1


def test_filesystem_move_and_copy(tmp_path) -> None:
    reg = build_registry()
    src = tmp_path / "src.txt"
    src.write_text("data", encoding="utf-8")

    dst = tmp_path / "dst.txt"
    reg.invoke("FileSystem", {"mode": "copy", "path": str(src), "destination": str(dst)})
    assert src.exists() and dst.exists()

    moved = tmp_path / "moved.txt"
    reg.invoke("FileSystem", {"mode": "move", "path": str(dst), "destination": str(moved)})
    assert not dst.exists() and moved.exists()


def test_filesystem_rejects_empty_path() -> None:
    with pytest.raises(ToolError):
        build_registry().invoke("FileSystem", {"mode": "read", "path": "  "})


def test_scrape_rejects_non_http_url() -> None:
    with pytest.raises(ToolError):
        build_registry().invoke("Scrape", {"url": "file:///etc/passwd"})


def test_scrape_rejects_unreachable_host() -> None:
    with pytest.raises(ToolError):
        build_registry().invoke(
            "Scrape", {"url": "https://nonexistent.invalid.example.test/", "limit": 100}
        )


def test_tool_result_str_is_the_text(reg) -> None:
    result = reg.invoke("DisplayInventory")
    assert str(result) == result.text
