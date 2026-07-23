"""Constants used by the text cursor implementation."""

UIA_TEXT_PATTERN_ID = 10014

CLSID_CUIAUTOMATION8 = "{E22AD333-B25F-460C-83D0-0581107395C9}"
CLSID_CUIAUTOMATION = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"

MAX_TEXT_UNIT_MOVE = 2_147_483_647

# Upper bound on the selected-text string embedded in a snapshot. Unlike the
# surrounding context (capped by context_chars), the selection can be the whole
# document (e.g. after select_all), so cap it here to keep the MCP payload small.
MAX_SELECTED_TEXT_CHARS = 4096
