"""Chinese/English bridge tests.

Small module, but it is the reason a Chinese request can reach an English tool
description at all. Drop one term and the model concludes it lacks the capability.
"""

from __future__ import annotations

from winmcp.keywords import (
    CN_EN_BRIDGE,
    CN_EN_SHORTHAND,
    expand,
    expanded_tokens,
    tokenize,
)


def test_tokenize_splits_english_words() -> None:
    assert {"screenshot", "screen"} <= tokenize("Take a screenshot of the screen")


def test_chinese_run_is_one_token() -> None:
    """The Chinese pattern is greedy — the bridge must therefore match substrings."""
    assert len(tokenize("帮我把这个文件重命名一下")) == 1


def test_single_char_shorthand_matches_inside_a_long_token() -> None:
    """Regression: exact equality meant 装 never fired inside a sentence."""
    tokens = expanded_tokens("帮我装一下这个项目")
    assert "install" in tokens or "app" in tokens


def test_multi_char_entry_matches_as_substring() -> None:
    tokens = expanded_tokens("帮我把这个文件重命名一下")
    assert "file" in tokens
    assert "rename" in tokens


def test_open_maps_to_launch() -> None:
    assert {"open", "launch", "start"} & expanded_tokens("打开记事本")


def test_click_maps_to_mouse() -> None:
    assert "click" in expanded_tokens("点击那个按钮")


def test_wait_maps_to_wait() -> None:
    assert "wait" in expanded_tokens("等到界面出来")


def test_english_variants_are_normalized() -> None:
    assert "click" in expand({"clicks"})


def test_unrelated_text_adds_nothing() -> None:
    tokens = expanded_tokens("quantum entanglement")
    assert "click" not in tokens and "screenshot" not in tokens


def test_tables_are_well_formed() -> None:
    for table in (CN_EN_BRIDGE, CN_EN_SHORTHAND):
        for cn, ens in table.items():
            assert cn
            assert ens, f"{cn} has no English mapping"
            assert all(e.isascii() for e in ens), f"{cn} maps to non-ASCII: {ens}"


def test_shorthand_keys_are_single_characters() -> None:
    for cn in CN_EN_SHORTHAND:
        assert len(cn) == 1, f"{cn} is not a single character; move it to CN_EN_BRIDGE"


def test_bridge_has_reasonable_coverage() -> None:
    assert len(CN_EN_BRIDGE) >= 50
    assert len(CN_EN_SHORTHAND) >= 10
