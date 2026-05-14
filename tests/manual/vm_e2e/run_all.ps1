# run_all.ps1 — dispatcher.
#
# Detects whether the VM has been set up. If not, runs setup.ps1 (which
# reboots). If setup is done, runs test.ps1 directly.
#
# In normal operation the test.ps1 fires from the windows-mcp-test ONLOGON
# scheduled task after a reboot, NOT from this dispatcher. This file is a
# convenience for the human driver who wants a single entry point.
#
# IMPORTANT: this script never starts the windows-mcp services itself.
# windows-mcp must come up on its own after reboot via:
#   - SCM auto-start for WindowsMCPHost (set by setup.ps1)
#   - ONLOGON scheduled task `windows-mcp-server` (set by setup.ps1 via
#     `windows-mcp install`)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSCommandPath
$setup = Join-Path $here "setup.ps1"
$test  = Join-Path $here "test.ps1"

# Marker that setup has completed at least once. We treat "host service
# registered AND windows-mcp-server task registered" as the marker — both
# come from setup.ps1.
function Setup-Done {
    if (-not (Get-Service WindowsMCPHost -ErrorAction SilentlyContinue)) { return $false }
    $task = schtasks.exe /Query /TN windows-mcp-server 2>$null
    return $LASTEXITCODE -eq 0
}

if (Setup-Done) {
    Write-Host "Setup detected — running test.ps1 (verify-only, non-elevated path runs via ONLOGON task)."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $test
    exit $LASTEXITCODE
} else {
    Write-Host "No setup detected — running setup.ps1 (will reboot)."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setup
    exit $LASTEXITCODE
}
