"""Issue #495: optional merging of per-glyph Word leaves into line runs."""

from dataclasses import dataclass

import pytest

from windows_mcp.tree.word_runs import merge_word_nodes_enabled, merge_word_runs


@dataclass
class R:
    left: int
    top: int
    right: int
    bottom: int


def test_cjk_glyphs_on_one_line_merge_without_spaces():
    words = [("微", R(100, 10, 116, 30)), ("信", R(116, 10, 132, 30)), ("号", R(133, 10, 149, 30))]
    assert merge_word_runs(words) == [("微信号", R(100, 10, 149, 30))]


def test_latin_words_merge_with_a_space():
    words = [("Hello", R(0, 0, 40, 16)), ("world", R(45, 0, 85, 16))]
    assert merge_word_runs(words) == [("Hello world", R(0, 0, 85, 16))]


def test_separate_lines_stay_separate():
    words = [("one", R(0, 0, 30, 16)), ("two", R(0, 20, 30, 36))]
    assert merge_word_runs(words) == words


def test_large_gap_on_same_line_stays_separate():
    words = [("left", R(0, 0, 30, 16)), ("right", R(300, 0, 340, 16))]
    assert merge_word_runs(words) == words


def test_out_of_order_word_starts_new_run():
    words = [("b", R(50, 0, 60, 16)), ("a", R(0, 0, 10, 16))]
    assert merge_word_runs(words) == words


def test_empty_input():
    assert merge_word_runs([]) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), (" ON ", True), ("0", False), ("", False), ("no", False)],
)
def test_env_flag(value, expected):
    assert merge_word_nodes_enabled({"WINDOWS_MCP_MERGE_WORD_NODES": value}) is expected


def test_env_flag_unset():
    assert merge_word_nodes_enabled({}) is False
