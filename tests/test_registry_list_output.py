"""Registry list mode against real registry keys, read-only.

``list_key()`` formats a key's values with Format-List, and three parts of that
pipeline changed what the caller got back:

- ``-ExcludeProperty PS*`` was meant to hide the PSPath, PSParentPath,
  PSChildName, PSDrive and PSProvider properties that Get-ItemProperty adds, but
  it also hid every value whose name starts with "PS", such as PSModulePath.
- Format-List shows the first four items of a binary or multi-string value
  ($FormatEnumerationLimit) and then an ellipsis.
- Windows PowerShell 5.1 wraps at the console width (120 columns by default), so
  longer values came back split over several lines, mid-word where there is no
  space.

The keys read here exist on every Windows install. Nothing is written.
"""

import sys
import winreg
from functools import partial

import pytest

from windows_mcp import registry
from windows_mcp.powershell import PowerShellExecutor

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")

ENVIRONMENT = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
SERVICE_GROUP_ORDER = r"SYSTEM\CurrentControlSet\Control\ServiceGroupOrder"
TIME_ZONE = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
PROVIDER_PROPERTIES = ("PSPath", "PSParentPath", "PSChildName", "PSDrive", "PSProvider")


@pytest.fixture(params=[None, "powershell"], ids=["default-shell", "windows-powershell"])
def shell(request, monkeypatch):
    """Run the registry functions with the default shell (pwsh when it is
    installed) and with Windows PowerShell 5.1."""
    if request.param:
        execute = partial(PowerShellExecutor.execute_command, shell=request.param)
        monkeypatch.setattr(PowerShellExecutor, "execute_command", staticmethod(execute))
    return request.param


def _hklm(subkey: str) -> str:
    return f"HKLM:\\{subkey}"


def _listed_values(subkey: str) -> dict[str, str]:
    """Map each value name in the list_key() output to the text after ' : '."""
    output = registry.list_key(_hklm(subkey))
    assert "Values:" in output, output
    values = {}
    for line in output.splitlines():
        name, sep, text = line.partition(" : ")
        # Lines that start with whitespace continue the value above them.
        if sep and not line[:1].isspace():
            values[name.rstrip()] = text
    return values


def _value_names(subkey: str) -> list[str]:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
        return [winreg.EnumValue(key, i)[0] for i in range(winreg.QueryInfoKey(key)[1])]


def _value(subkey: str, name: str):
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
        return winreg.QueryValueEx(key, name)[0]


def test_values_named_ps_are_listed(shell):
    names = _value_names(ENVIRONMENT)
    assert "PSModulePath" in names

    listed = _listed_values(ENVIRONMENT)

    assert [name for name in names if name not in listed] == []
    assert [name for name in PROVIDER_PROPERTIES if name in listed] == []


def test_multi_string_value_is_listed_in_full(shell):
    groups = _value(SERVICE_GROUP_ORDER, "List")
    assert len(groups) > 4

    listed = _listed_values(SERVICE_GROUP_ORDER)

    assert listed["List"] == "{" + ", ".join(groups) + "}"


def test_binary_value_is_listed_in_full(shell):
    # A SYSTEMTIME: 16 bytes.
    standard_start = _value(TIME_ZONE, "StandardStart")
    assert len(standard_start) > 4

    listed = _listed_values(TIME_ZONE)

    assert listed["StandardStart"] == "{" + ", ".join(map(str, standard_start)) + "}"


def test_long_value_stays_on_one_line(shell):
    # The system Path does not fit in a 120-column line next to the value names.
    expected = registry.get_value(_hklm(ENVIRONMENT), "Path").split(" = ", 1)[1]

    listed = _listed_values(ENVIRONMENT)

    assert listed["Path"] == expected
