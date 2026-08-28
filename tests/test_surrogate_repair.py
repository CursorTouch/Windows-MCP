"""Regression tests for issue #382 — Snapshot dying on emoji in the UI tree.

UIA returns UTF-16, so an astral character can arrive as a raw surrogate pair.
Python stores those surrogates without complaint, but the strict UTF-8 JSON
encoder that serializes the tool response rejects them, and the entire Snapshot
fails over a single emoji in an element name or window title.
"""

import pytest

from windows_mcp.desktop.utils import repair_surrogates
from windows_mcp.tree.views import BoundingBox, Center, TreeElementNode, TreeState

# U+1F437 PIG FACE, as UIA hands it over: an uncombined UTF-16 surrogate pair.
PIG_SURROGATES = "\ud83d\udc37"
PIG = "\U0001f437"


def utf8_encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


class TestRepairSurrogates:
    def test_surrogate_pair_becomes_the_real_character(self):
        assert repair_surrogates(PIG_SURROGATES) == PIG

    def test_plain_text_is_returned_unchanged(self):
        text = "File  Edit  View"
        assert repair_surrogates(text) is text

    def test_already_valid_astral_text_is_untouched(self):
        assert repair_surrogates(f"chat {PIG}") == f"chat {PIG}"

    def test_lone_high_surrogate_is_replaced(self):
        assert repair_surrogates("a\ud83db") == "a\ufffdb"

    def test_lone_low_surrogate_is_replaced(self):
        assert repair_surrogates("a\udc37b") == "a\ufffdb"

    def test_trailing_high_surrogate_is_replaced(self):
        """A pair cut in half by truncation must not run off the end."""
        assert repair_surrogates("abc\ud83d") == "abc\ufffd"

    def test_surrounding_text_is_preserved(self):
        assert repair_surrogates(f"Chat with {PIG_SURROGATES} now") == f"Chat with {PIG} now"

    def test_consecutive_pairs(self):
        assert repair_surrogates(PIG_SURROGATES * 3) == PIG * 3

    def test_empty_string(self):
        assert repair_surrogates("") == ""

    @pytest.mark.parametrize(
        "raw",
        [PIG_SURROGATES, "a\ud83db", "a\udc37b", "abc\ud83d", f"x{PIG_SURROGATES}y"],
    )
    def test_result_always_encodes_to_utf8(self, raw):
        assert not utf8_encodable(raw), "test input should be the broken case"
        assert utf8_encodable(repair_surrogates(raw))


def make_node(name: str, window_name: str = "Chat") -> TreeElementNode:
    box = BoundingBox(left=10, top=20, right=110, bottom=60, width=100, height=40)
    return TreeElementNode(
        bounding_box=box,
        center=Center(x=60, y=40),
        name=name,
        control_type="Button",
        window_name=window_name,
    )


class TestUITreeSerialization:
    """The reported failure: a synthetic node whose name contains an emoji."""

    def test_rendered_tree_with_emoji_node_is_serializable(self):
        state = TreeState(interactive_nodes=[make_node(f"Contact {PIG_SURROGATES}")])
        rendered = state.interactive_elements_to_string()

        assert not utf8_encodable(rendered), "raw render should still carry the surrogates"
        repaired = repair_surrogates(rendered)
        assert utf8_encodable(repaired)
        assert PIG in repaired
        assert "Contact" in repaired

    def test_emoji_in_window_title_is_serializable(self):
        state = TreeState(
            interactive_nodes=[make_node("Send", window_name=f"WeChat {PIG_SURROGATES}")]
        )
        repaired = repair_surrogates(state.interactive_elements_to_string())
        assert utf8_encodable(repaired)
        assert f"WeChat {PIG}" in repaired

    def test_emoji_in_metadata_value_is_serializable(self):
        node = make_node("Message")
        node.metadata = {"value": PIG_SURROGATES}
        repaired = repair_surrogates(
            TreeState(interactive_nodes=[node]).interactive_elements_to_string()
        )
        assert utf8_encodable(repaired)
        assert PIG in repaired

    def test_tree_without_emoji_is_unaffected(self):
        state = TreeState(interactive_nodes=[make_node("Send")])
        rendered = state.interactive_elements_to_string()
        assert repair_surrogates(rendered) == rendered
