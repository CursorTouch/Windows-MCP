"""Registry get mode shows a value's type and its stored form.

Get-ItemProperty expands the %VARIABLES% of an ExpandString value and prints a
Binary value one decimal byte per line, which reads the same as a MultiString
of numbers. Get mode used to pass both on and did not name the type, so a value
read with get and written back with set lost its variables.
"""

import sys
import uuid
import winreg
from collections.abc import Iterator

import pytest

from windows_mcp import registry

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="writes the Windows registry")

SUBKEY_PREFIX = r"Software\windows-mcp-test-"


@pytest.fixture
def key() -> Iterator[str]:
    subkey = SUBKEY_PREFIX + uuid.uuid4().hex
    yield subkey
    registry.delete_entry(path=f"HKCU:\\{subkey}")


def _set(subkey: str, name: str, value: str, reg_type: str) -> None:
    result = registry.set_value(path=f"HKCU:\\{subkey}", name=name, value=value, reg_type=reg_type)
    assert "set to" in result, result


def _stored(subkey: str, name: str) -> tuple[object, int]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as handle:
        return winreg.QueryValueEx(handle, name)


@pytest.mark.parametrize(
    ("reg_type", "value", "shown"),
    [
        ("ExpandString", r"%USERPROFILE%\bin", r"%USERPROFILE%\bin"),
        ("String", r"%USERPROFILE%\bin", r"%USERPROFILE%\bin"),
        ("Binary", "1, 2, 255", "hex:01,02,ff"),
        ("Binary", "", "hex:"),
        ("MultiString", "1\n2\n255", "1\n2\n255"),
        ("DWord", "4294967295", "4294967295"),
        ("QWord", "18446744073709551615", "18446744073709551615"),
    ],
)
def test_get_shows_type_and_stored_value(key: str, reg_type: str, value: str, shown: str) -> None:
    _set(key, "V", value, reg_type)

    result = registry.get_value(path=f"HKCU:\\{key}", name="V")

    assert result == f'Registry value [HKCU:\\{key}] "V" (type: {reg_type}) = {shown}'


@pytest.mark.parametrize(
    ("reg_type", "value"),
    [
        ("ExpandString", r"%SystemRoot%\system32;%USERPROFILE%\bin"),
        ("Binary", "hex:00,7f,80,ff"),
        ("MultiString", "a\nb c"),
        ("DWord", "3000000000"),
    ],
)
def test_get_output_writes_back_unchanged(key: str, reg_type: str, value: str) -> None:
    _set(key, "V", value, reg_type)
    before = _stored(key, "V")

    result = registry.get_value(path=f"HKCU:\\{key}", name="V")
    _set(key, "V", result.split(" = ", 1)[1], reg_type)

    assert _stored(key, "V") == before


def test_default_value_is_read(key: str) -> None:
    _set(key, "(default)", "dflt", "String")

    result = registry.get_value(path=f"HKCU:\\{key}", name="(default)")

    assert result == f'Registry value [HKCU:\\{key}] "(default)" (type: String) = dflt'


def test_value_ending_in_a_line_break_keeps_it(key: str) -> None:
    # Only the line break PowerShell adds after the value is dropped.
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, "V", 0, winreg.REG_SZ, "line\r\n")

    result = registry.get_value(path=f"HKCU:\\{key}", name="V")

    assert result == f'Registry value [HKCU:\\{key}] "V" (type: String) = line\r\n'
