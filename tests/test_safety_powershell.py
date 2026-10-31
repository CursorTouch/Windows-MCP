import pytest

from windows_mcp.safety.config import PowerShellSafetyConfig
from windows_mcp.safety.powershell import check_powershell

CFG = PowerShellSafetyConfig(mode="allowlist", allowed_commands=["Get-ChildItem", "Get-Process"])


@pytest.mark.parametrize(
    "command",
    [
        "Get-ChildItem",
        "get-childitem C:\\Users\\vince\\Documents",
        "Get-Process -Name notepad",
        "Get-ChildItem -Filter *.txt -Recurse",
    ],
)
def test_allowed(command):
    assert check_powershell(command, CFG) is None


@pytest.mark.parametrize(
    "command",
    [
        "Get-ChildItem; Remove-Item C:\\x",
        "Get-ChildItem | Remove-Item",
        "Get-ChildItem && whoami",
        "Get-ChildItem `\nwhoami",
        "Get-ChildItem $(Remove-Item x)",
        "Get-ChildItem $env:TEMP",
        "Get-ChildItem > C:\\out.txt",
        "Get-ChildItem 'a'",
        'Get-ChildItem "a"',
        "Get-ChildItem @(1)",
        "Get-ChildItem {x}",
        "Get-ChildItem [System.IO.File]::Delete('x')",
        "Get-ChildItem\nRemove-Item x",
        "Get-ChildItem -EncodedCommand AAAA",
        "Get-Process -Name x -e AAAA",
        "Invoke-Expression x",
        "iex x",
        "Remove-Item C:\\x",
        "powershell -Command Remove-Item x",
        "cmd /c del x",
        "Get-ChildItem %TEMP%",
        "Get-ChildItem --% del x",
        "",
        "   ",
        "Get-ChildItem " + "a" * 600,
    ],
)
def test_rejected(command):
    assert check_powershell(command, CFG) is not None


def test_disabled_mode_rejects_everything():
    assert check_powershell("Get-ChildItem", PowerShellSafetyConfig()) is not None


def test_unrestricted_mode_passes():
    assert check_powershell("a | b; c", PowerShellSafetyConfig(mode="unrestricted")) is None
