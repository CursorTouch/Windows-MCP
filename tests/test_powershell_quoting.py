"""ps_quote() literals must reach PowerShell unchanged.

PowerShell ends a single-quoted string at the typographic single quotes U+2018 to
U+201B as well as at the ASCII apostrophe. ps_quote() used to double only the
apostrophe, so a value with a typographic one (most often U+2019, the apostrophe
in a typed "Don't") ended the literal early: the Notification script no longer
parsed, Registry calls failed, and the rest of the value was read as PowerShell code.
"""

import sys

import pytest

from windows_mcp.powershell.service import PowerShellExecutor
from windows_mcp.powershell.utils import ps_quote

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="runs PowerShell")

TEXTS = [
    "it's",
    "Don\u2019t forget",
    "\u2018single\u2019 and \u201alow\u201b",
    "''\u2019\u2019",
    "a\u2019; Write-Output b; #",
]


@pytest.mark.parametrize("shell", [None, "powershell"])
@pytest.mark.parametrize("text", TEXTS)
def test_value_round_trips(text, shell):
    output, code = PowerShellExecutor.execute_command(f"Write-Output {ps_quote(text)}", shell=shell)
    assert (output.strip(), code) == (text, 0)
