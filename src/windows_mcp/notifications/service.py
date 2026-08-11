from windows_mcp.powershell import PowerShellExecutor
from windows_mcp.powershell.utils import ps_quote, ps_quote_for_xml

__all__ = [
    "send_notification",
]


def send_notification(title: str, message: str, app_id: str) -> str:
    """Send a Windows toast notification with a title and message.

    Args:
        title: The title of the notification.
        message: The message of the notification.
        app_id: The valid Application User Model ID of the toast notification.
            Required to display the notification in a specific app.

    Returns:
        A string indicating the result of the notification.

    Notes:
        The MCP client MUST provide an App ID because Windows uses it as the
        app identity for desktop toast notifications, and it MUST match a
        registered shortcut/AppUserModelID.
    """
    safe_title = ps_quote_for_xml(title)
    safe_message = ps_quote_for_xml(message)
    safe_app_id = ps_quote(app_id)

    ps_script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
        f"$notifTitle = {safe_title}\n"
        f"$notifMessage = {safe_message}\n"
        f"$appId = {safe_app_id}\n"
        '$template = @"\n'
        "<toast>\n"
        "    <visual>\n"
        '        <binding template="ToastGeneric">\n'
        "            <text>$notifTitle</text>\n"
        "            <text>$notifMessage</text>\n"
        "        </binding>\n"
        "    </visual>\n"
        "</toast>\n"
        '"@\n'
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "$xml.LoadXml($template)\n"
        "$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)\n"
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml\n"
        "$notifier.Show($toast)"
    )
    # Use Windows PowerShell (5.1) explicitly because the WinRT toast APIs are not available in PowerShell 7+ (pwsh).
    response, status = PowerShellExecutor.execute_command(ps_script, shell="powershell")
    if status == 0:
        return f'Notification sent: "{title}" - {message}'
    else:
        return f"Notification may have been sent. PowerShell output: {response[:200]}"
