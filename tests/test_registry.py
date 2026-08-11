from unittest.mock import patch

from windows_mcp import registry
from windows_mcp.powershell.utils import ps_quote


EXECUTE_COMMAND_PATH = "windows_mcp.powershell.PowerShellExecutor.execute_command"


class TestPsQuote:
    def test_simple_string(self):
        assert ps_quote("hello") == "'hello'"

    def test_single_quote_escaping(self):
        assert ps_quote("it's") == "'it''s'"

    def test_double_quotes_not_escaped(self):
        assert ps_quote('say "hi"') == """'say "hi"'"""

    def test_dollar_sign_not_expanded(self):
        assert ps_quote("$env:PATH") == "'$env:PATH'"

    def test_empty_string(self):
        assert ps_quote("") == "''"

    def test_registry_path(self):
        result = ps_quote("HKCU:\\Software\\Test")
        assert result == "'HKCU:\\Software\\Test'"


class TestRegistryGet:
    def test_success(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("42\n", 0)):
            result = registry.get_value(path="HKCU:\\Software\\Test", name="MyValue")
        assert 'MyValue' in result
        assert '42' in result
        assert 'Error' not in result

    def test_failure(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("Property not found", 1)):
            result = registry.get_value(path="HKCU:\\Software\\Test", name="Missing")
        assert 'Error reading registry' in result
        assert 'Property not found' in result

    def test_command_uses_ps_quote(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("val", 0)) as mock_exec:
            registry.get_value(path="HKCU:\\Software\\O'Reilly", name="key's")
        cmd = mock_exec.call_args[0][0]
        assert "HKCU:\\Software\\O''Reilly" in cmd
        assert "key''s" in cmd


class TestRegistrySet:
    def test_success(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("", 0)):
            result = registry.set_value(path="HKCU:\\Software\\Test", name="MyKey", value="hello")
        assert 'set to' in result
        assert '"hello"' in result

    def test_failure(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("Access denied", 1)):
            result = registry.set_value(path="HKLM:\\Software\\Test", name="Key", value="val")
        assert 'Error writing registry' in result

    def test_invalid_type(self):
        with patch(EXECUTE_COMMAND_PATH) as mock_exec:
            result = registry.set_value(path="HKCU:\\Test", name="Key", value="val", reg_type="Invalid")
        assert 'Error: invalid registry type' in result
        assert 'Invalid' in result
        mock_exec.assert_not_called()

    def test_all_valid_types(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("", 0)):
            for reg_type in ("String", "ExpandString", "Binary", "DWord", "MultiString", "QWord"):
                result = registry.set_value(path="HKCU:\\Test", name="K", value="V", reg_type=reg_type)
                assert 'Error' not in result

    def test_creates_key_if_missing(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("", 0)) as mock_exec:
            registry.set_value(path="HKCU:\\Software\\NewKey", name="Val", value="1")
        cmd = mock_exec.call_args[0][0]
        assert "New-Item" in cmd
        assert "Test-Path" in cmd


class TestRegistryDelete:
    def test_delete_value(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("", 0)) as mock_exec:
            result = registry.delete_entry(path="HKCU:\\Software\\Test", name="MyValue")
        assert 'deleted' in result
        assert '"MyValue"' in result
        cmd = mock_exec.call_args[0][0]
        assert "Remove-ItemProperty" in cmd

    def test_delete_key(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("", 0)) as mock_exec:
            result = registry.delete_entry(path="HKCU:\\Software\\Test", name=None)
        assert 'key' in result.lower()
        assert 'deleted' in result
        cmd = mock_exec.call_args[0][0]
        assert "Remove-Item" in cmd
        assert "-Recurse" in cmd

    def test_delete_value_failure(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("Not found", 1)):
            result = registry.delete_entry(path="HKCU:\\Software\\Test", name="Missing")
        assert 'Error deleting registry value' in result

    def test_delete_key_failure(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("Access denied", 1)):
            result = registry.delete_entry(path="HKCU:\\Software\\Protected")
        assert 'Error deleting registry key' in result


class TestRegistryList:
    def test_success(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("Values:\nMyKey : hello\n\nSub-Keys:\nChild1", 0)):
            result = registry.list_key(path="HKCU:\\Software\\Test")
        assert 'MyKey' in result
        assert 'hello' in result
        assert 'Child1' in result

    def test_failure(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("Path not found", 1)):
            result = registry.list_key(path="HKCU:\\Software\\Missing")
        assert 'Error listing registry' in result

    def test_empty(self):
        with patch(EXECUTE_COMMAND_PATH, return_value=("No values or sub-keys found.", 0)):
            result = registry.list_key(path="HKCU:\\Software\\Empty")
        assert 'No values or sub-keys found' in result
