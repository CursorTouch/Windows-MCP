"""Registry get mode shows a value's type and its stored form.

Get-ItemProperty expands the %VARIABLES% of an ExpandString value and prints a
Binary value one decimal byte per line, which reads the same as a MultiString
of numbers. Get mode used to pass both on and did not name the type, so a value
read with get and written back with set lost its variables.
"""

import sys
import uuid
import winreg

import pytest

from windows_mcp import registry

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="writes the Windows registry")

SUBKEY_PREFIX = r"Software\windows-mcp-test-"


@pytest.fixture
def key():
    subkey = SUBKEY_PREFIX + uuid.uuid4().hex
    yield subkey
    registry.delete_entry(path=f"HKCU:\\{subkey}")


def _set(subkey: str, name: str, value: str, reg_type: str) -> None:
    result = registry.set_value(path=f"HKCU:\\{subkey}", name=name, value=value, reg_type=reg_type)
    assert "set to" in result, result


def _stored(subkey: str, name: str):
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
def test_get_shows_type_and_stored_value(key, reg_type, value, shown):
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
def test_get_output_writes_back_unchanged(key, reg_type, value):
    _set(key, "V", value, reg_type)
    before = _stored(key, "V")

    result = registry.get_value(path=f"HKCU:\\{key}", name="V")
    _set(key, "V", result.split(" = ", 1)[1], reg_type)

    assert _stored(key, "V") == before


def test_default_value_is_read(key):
    _set(key, "(default)", "dflt", "String")

    result = registry.get_value(path=f"HKCU:\\{key}", name="(default)")

    assert result == f'Registry value [HKCU:\\{key}] "(default)" (type: String) = dflt'
