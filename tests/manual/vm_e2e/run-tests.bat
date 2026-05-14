@echo off
REM Kickoff for the in-VM test harness. Launched via Win+R as a UNC path so
REM the typing surface is short and unambiguous: \\host.lan\Data\Windows-MCP\kickoff.bat
start "" powershell -ExecutionPolicy Bypass -File \\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\run_all.ps1
