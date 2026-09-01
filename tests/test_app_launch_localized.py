import sys

import pytest

if sys.platform == "win32":
    from windows_mcp.desktop.service import Desktop
    from windows_mcp.powershell import PowerShellExecutor
else:
    pytestmark = pytest.mark.skip(reason="Desktop imports Windows-only pywin32 modules")


def test_start_menu_apps_include_localized_appsfolder_name(monkeypatch: pytest.MonkeyPatch) -> None:
    app_id = "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
    commands: list[str] = []

    def execute_command(command: str, *_: object) -> tuple[str, int]:
        commands.append(command)
        if "Get-StartApps" in command:
            return f'"Name","AppID"\n"Notepad","{app_id}"\n', 0
        if "4234d49b-0245-4df3-b780-3893943456e1" in command:
            return f'"Name","AppID"\n"记事本","{app_id}"\n', 0
        if command == f"Start-Process 'shell:AppsFolder\\{app_id}'":
            return "", 0
        pytest.fail(f"unexpected PowerShell command: {command}")

    monkeypatch.setattr(PowerShellExecutor, "execute_command", staticmethod(execute_command))

    desktop = Desktop.__new__(Desktop)
    monkeypatch.setattr(desktop, "_check_app_exists", lambda _: True)

    assert desktop.launch_app("Notepad") == ("", 0, 0)
    assert desktop.launch_app("记事本") == ("", 0, 0)
    assert commands[-1] == f"Start-Process 'shell:AppsFolder\\{app_id}'"


def test_start_menu_shortcut_fallback_is_preserved_when_appsfolder_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shortcut_apps = {
        "legacy app": r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Legacy.lnk"
    }

    monkeypatch.setattr(
        PowerShellExecutor,
        "execute_command",
        staticmethod(lambda *_: ("", 1)),
    )
    desktop = Desktop.__new__(Desktop)
    monkeypatch.setattr(desktop, "_get_apps_from_shortcuts", lambda: shortcut_apps)

    assert desktop.get_apps_from_start_menu() == shortcut_apps
