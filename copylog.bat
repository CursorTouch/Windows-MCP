@echo off
echo === searching for broker log ===
dir /S /B C:\Windows\windows-mcp-host.log 2>nul
dir /S /B C:\Windows\Temp\windows-mcp-host.log 2>nul
dir /S /B C:\Windows\System32\config\systemprofile\AppData\Local\Temp\windows-mcp-host.log 2>nul
dir /S /B "%TEMP%\windows-mcp-host.log" 2>nul
dir /S /B C:\Windows\ServiceProfiles\LocalSystem\AppData\Local\Temp\windows-mcp-host.log 2>nul

echo === copying any matches ===
copy /Y C:\Windows\Temp\windows-mcp-host.log \\host.lan\Data\Windows-MCP\windows-mcp-host.log 2>&1
copy /Y C:\Windows\System32\config\systemprofile\AppData\Local\Temp\windows-mcp-host.log \\host.lan\Data\Windows-MCP\windows-mcp-host-sysprofile.log 2>&1

echo === done ===
pause
