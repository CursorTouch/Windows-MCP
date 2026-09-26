"""Constants used by the text cursor implementation."""

MAX_TEXT_UNIT_MOVE = 2_147_483_647

# Upper bound on the selected-text string embedded in a snapshot. Unlike the
# surrounding context (capped by context_chars), the selection can be the whole
# document (e.g. after select_all), so cap it here to keep the MCP payload small.
MAX_SELECTED_TEXT_CHARS = 4096
