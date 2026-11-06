"""The PowerShell child must keep the values the server inherited.

_prepare_env() fills in variables missing from the server's environment from
the registry. On Windows os.environ.copy() upper-cases every name, while the
registry keeps the case a variable was created with (OneDrive, PSModulePath,
windir). Comparing the two with case kept added a second key for the same
variable, and the child saw the registry value instead of the inherited one.
"""

import sys

import pytest

from windows_mcp.powershell import service
from windows_mcp.powershell.service import PowerShellExecutor

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows environment block")

NAME = "WinMcpCaseProbe"


@pytest.fixture
def registry_env(monkeypatch):
    """Registry environment as _read_reg_env() returns it, mixed-case names and all."""
    machine = {"WinMcpMachineOnly": "machine"}
    user = {NAME: "registry"}

    def read(hkey, subkey):
        values = user if hkey == service.winreg.HKEY_CURRENT_USER else machine
        return dict(values), "", ""

    monkeypatch.setattr(service, "_read_reg_env", read)
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.delenv("WinMcpMachineOnly", raising=False)


def _values(env, name):
    return [value for key, value in env.items() if key.upper() == name.upper()]


def test_inherited_value_is_kept(registry_env, monkeypatch):
    monkeypatch.setenv(NAME, "inherited")
    assert _values(service._prepare_env(), NAME) == ["inherited"]


def test_empty_inherited_value_is_filled_once(registry_env, monkeypatch):
    monkeypatch.setenv(NAME, "")
    assert _values(service._prepare_env(), NAME) == ["registry"]


def test_missing_values_are_still_added(registry_env):
    env = service._prepare_env()
    assert _values(env, NAME) == ["registry"]
    assert _values(env, "WinMcpMachineOnly") == ["machine"]


def test_child_sees_the_inherited_value(registry_env, monkeypatch):
    monkeypatch.setenv(NAME, "inherited")
    output, code = PowerShellExecutor.execute_command(f"$env:{NAME}", shell="powershell")
    assert (output.strip(), code) == ("inherited", 0)
