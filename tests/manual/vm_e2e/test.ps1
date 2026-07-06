# test.ps1 — runs after reboot, NON-elevated. Connects to the *already-running*
# windows-mcp MCP server over HTTP and runs the per-assertion suite.
#
# This script must NOT start the MCP server. Windows-MCP installs an ONLOGON
# scheduled task during setup that starts it on its own — the test verifies
# the system reached a working state without harness intervention.
#
# Pre-conditions (set up once by setup.ps1):
#   - WindowsMCPHost service registered with SERVICE_AUTO_START
#   - windows-mcp-server scheduled task registered (windows-mcp install)
#   - User's PATH persisted so the task can resolve uv/windows-mcp
#   - UAC: EnableLUA=1, ConsentPromptBehaviorAdmin=2, PromptOnSecureDesktop=1
#   - This task itself (windows-mcp-test) registered to fire ONLOGON,
#     non-elevated.

$ErrorActionPreference = "Stop"
$ProgressPreference   = "SilentlyContinue"

$Repo       = "\\host.lan\Data\Windows-MCP"
$ResultsDir = Join-Path $Repo "tests\manual\vm_e2e"
$ResultsJson = Join-Path $ResultsDir "results.json"
$LocalLog   = "$env:TEMP\windows-mcp-test.log"
$ShareLog   = Join-Path $ResultsDir "test.log"

# Where the MCP server should be listening, per setup.ps1.
$McpUrl = "http://127.0.0.1:8000/mcp/"

function Log($msg) {
    $ts = (Get-Date).ToString("HH:mm:ss")
    Add-Content -Path $LocalLog -Value "[$ts] $msg"
    Write-Host "[$ts] $msg"
    try { Copy-Item -Force $LocalLog $ShareLog -ErrorAction Stop } catch { }
}

function Copy-HostLog {
    # The host service now logs to %ProgramData%\windows-mcp (Users-readable),
    # so this non-elevated test can copy it to the share for diagnosis instead
    # of needing an elevated dump. Best-effort.
    try {
        $hostLog = Join-Path $env:ProgramData "windows-mcp\windows-mcp-host.log"
        if (Test-Path $hostLog) {
            Copy-Item -Force $hostLog (Join-Path $Repo "windows-mcp-host.log")
            Log "Copied host service log to share."
        } else {
            Log "Host service log not found at $hostLog."
        }
    } catch { Log "host log copy failed: $($_.Exception.Message)" }
}

function Wait-For-Url($url, $timeoutSec) {
    # Probe at the TCP layer. We don't care that the streamable-http MCP server
    # answers 406 on plain GET ("Client must accept text/event-stream") — we
    # just need it bound and accepting connections so the real MCP client can
    # negotiate. Invoke-WebRequest's WebException path interacts badly with
    # -ErrorAction SilentlyContinue (the catch block never sees the response
    # in Windows PowerShell 5.1, which is what test.ps1 runs under).
    $uri = [Uri]$url
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $task = $client.ConnectAsync($uri.Host, $uri.Port)
            if ($task.Wait(2000) -and $client.Connected) { return $true }
        } catch { } finally {
            try { $client.Close() } catch { }
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

# Set-Content opens the file in shared mode that Copy-Item dislikes; quick poll.
Set-Content -Path $LocalLog -Value "test.ps1 started $(Get-Date -Format o)"
try { Copy-Item -Force $LocalLog $ShareLog -ErrorAction Stop } catch { }

try {
    # ----- 1. Verify host service self-started --------------------------------
    # On slow VMs SCM can take 30-60s to bring up the auto-start service after
    # the user logs in. Retry for up to 60s before declaring failure — the
    # assertion is "the service eventually self-starts," not "it's already
    # Running by the moment we check."
    $svc = $null
    for ($i = 0; $i -lt 30; $i++) {
        $svc = Get-Service WindowsMCPHost -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") { break }
        Start-Sleep -Seconds 2
    }
    if ($null -eq $svc) {
        throw "WindowsMCPHost service is not registered. Did setup.ps1 run?"
    }
    if ($svc.Status -ne "Running") {
        throw "WindowsMCPHost service did not auto-start within 60s. Current status: $($svc.Status)"
    }
    Log "WindowsMCPHost is Running (self-started)."

    # ----- 2. Wait for MCP server to come up on its own -----------------------
    # The MCP server's first `windows-mcp serve` cold-start pulls in a heavy
    # import chain (comtypes, pywin32, pillow, numpy, fastmcp, uvicorn). On a
    # KVM-less TCG VM (~10x slower) that can take well past two minutes, so the
    # old 120s deadline gave up while the server was still importing. Give it
    # 300s. On failure, dump the server's own logs + task state to the share so
    # the run is self-diagnosing (no VNC archaeology needed).
    $serverWaitSec = 300
    Log "Waiting up to ${serverWaitSec}s for MCP server at $McpUrl …"
    if (-not (Wait-For-Url $McpUrl $serverWaitSec)) {
        $cfg = "$env:USERPROFILE\.windows-mcp"
        $diag = Join-Path $ResultsDir "server-diag.txt"
        try {
            "=== server-not-up diagnostics $(Get-Date -Format o) ===" | Set-Content -Path $diag
            "--- schtasks windows-mcp-server ---" | Add-Content $diag
            (schtasks /Query /TN windows-mcp-server /V /FO LIST 2>&1) | Add-Content $diag
            "--- server.error.log (tail 80) ---" | Add-Content $diag
            if (Test-Path "$cfg\server.error.log") { Get-Content "$cfg\server.error.log" -Tail 80 | Add-Content $diag } else { "NO server.error.log" | Add-Content $diag }
            "--- server.log (tail 40) ---" | Add-Content $diag
            if (Test-Path "$cfg\server.log") { Get-Content "$cfg\server.log" -Tail 40 | Add-Content $diag } else { "NO server.log" | Add-Content $diag }
            "--- port 8000 ---" | Add-Content $diag
            (netstat -ano | Select-String ':8000') | Add-Content $diag
            Log "Wrote server-not-up diagnostics to $diag"
        } catch { Log "diag dump failed: $($_.Exception.Message)" }
        throw "MCP server never came up at $McpUrl in ${serverWaitSec}s. See server-diag.txt on the share."
    }
    Log "MCP server reachable."

    # ----- 3. Run the mcp_client tests against the running server -------------
    # We're already at medium integrity (this task was registered without
    # /RL HIGHEST), so the trigger Start-Process -Verb RunAs will fire real UAC.
    $localRepo = "C:\windows-mcp"
    # Sync the test client from the share every run so harness edits land
    # without re-running setup.ps1's full robocopy.
    Copy-Item -Force `
        (Join-Path $ResultsDir "mcp_client.py") `
        (Join-Path $localRepo "tests\manual\vm_e2e\mcp_client.py") `
        -ErrorAction SilentlyContinue
    Push-Location $localRepo
    try {
        $allowJson = Join-Path $ResultsDir "results-allow_all.json"
        Remove-Item -Force $allowJson -ErrorAction SilentlyContinue
        Log "Running mcp_client.py --mode allow_all against $McpUrl …"
        # uv run from medium integrity. uv reads venv from local repo.
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & uv run python tests\manual\vm_e2e\mcp_client.py `
            --http $McpUrl --results $allowJson --mode allow_all 2>&1 |
            Tee-Object -FilePath "$env:TEMP\mcp_client.out" | Out-Host
        $rc = $LASTEXITCODE
        $ErrorActionPreference = $prev
        Copy-Item -Force "$env:TEMP\mcp_client.out" `
            (Join-Path $ResultsDir "mcp_client-allow_all.log")
        # rc may be 1 if assertions fail — that's a result, not a script error.
        Log "mcp_client exited with $rc."
    } finally {
        Pop-Location
    }

    # ----- 4. Combine + cleanup ----------------------------------------------
    if (Test-Path $allowJson) {
        $allow = Get-Content $allowJson -Raw | ConvertFrom-Json
        $combined = [pscustomobject]@{
            started_at  = $allow.started_at
            finished_at = $allow.finished_at
            transport   = $allow.transport
            phases      = @{ allow_all = $allow }
            summary     = $allow.summary
        }
        $combined | ConvertTo-Json -Depth 8 | Set-Content -Path $ResultsJson
        Log "results.json written: total=$($combined.summary.total) passed=$($combined.summary.passed) failed=$($combined.summary.failed)"
    } else {
        throw "mcp_client did not write $allowJson."
    }
    Copy-HostLog
    Log "DONE"
} catch {
    Log "FAILED: $($_.Exception.Message)"
    @{
        started_at  = (Get-Date).ToString("o")
        finished_at = (Get-Date).ToString("o")
        transport   = "(test-failed)"
        results = @(@{
            name       = "test.ps1 driver"
            passed     = $false
            detail     = $_.Exception.Message
            duration_s = 0
        })
        summary = @{ total = 1; passed = 0; failed = 1 }
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $ResultsJson
    Copy-HostLog
    exit 1
}
