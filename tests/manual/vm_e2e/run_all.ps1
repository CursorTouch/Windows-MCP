# In-VM test orchestrator (Path A).
#
# Kicked off from the host via vncdotool keystroke. Picks up at first-boot,
# installs Python+uv if needed, registers the secure-desktop service, then
# runs the MCP client tests and writes results.json back to the share.

$ErrorActionPreference = "Stop"
$ProgressPreference   = "SilentlyContinue"

$Repo       = "\\host.lan\Data\Windows-MCP"
$LocalRepo  = "C:\windows-mcp"
$ResultsDir = Join-Path $Repo "tests\manual\vm_e2e"
$ResultsJson = Join-Path $ResultsDir "results.json"

# Write the log LOCALLY during execution; copy to the share at the end.
# Set-Content/Add-Content directly to a UNC path is flaky on Win11 (saw
# transient FileNotFoundException on a fresh path) — local writes are not.
$LocalLog   = "$env:TEMP\windows-mcp-run_all.log"
$ShareLog   = Join-Path $ResultsDir "run_all.log"

function Log($msg) {
    $ts = (Get-Date).ToString("HH:mm:ss")
    Add-Content -Path $LocalLog -Value "[$ts] $msg"
    Write-Host "[$ts] $msg"
    # Best-effort live mirror to the share. Failure to mirror does not stop the run.
    try { Copy-Item -Force $LocalLog $ShareLog -ErrorAction Stop } catch { }
}

# Run a native command with stderr merged into stdout and Tee'd to a log,
# locally suppressing PowerShell's "stderr lines = error" treatment. Throws
# only on non-zero exit code, not on stderr noise.
function Invoke-Native {
    param([string]$LogName, [scriptblock]$Block)
    $localLog = "$env:TEMP\$LogName"
    $shareLog = Join-Path $ResultsDir $LogName
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Block 2>&1 | Tee-Object -FilePath $localLog
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    try { Copy-Item -Force $localLog $shareLog -ErrorAction Stop } catch { }
    if ($rc -ne 0) {
        throw "Native command in $LogName exited with $rc (see $shareLog)"
    }
}

# -----------------------------------------------------------------------------
# 1) Bootstrap. Skip Windows-managed Python entirely — winget on a fresh
#    dockur Win11 has a broken source ("Data required by the source is
#    missing") and the python on PATH is an MS Store stub.
#
#    Instead, install uv (self-contained binary, ~15 MB) directly from
#    Astral's CDN, then let uv install and manage its own Python via
#    `uv python install 3.13`. Zero Windows-side Python machinery needed.
# -----------------------------------------------------------------------------
function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Log "uv present: $(uv --version)"
        return
    }
    # We pre-stage uv.exe in the share at tests/manual/vm_e2e/bin/uv.exe so we
    # don't depend on Windows being able to reach Astral's CDN. (In the
    # disposable test VM the sandbox network MITMs TLS and the VM doesn't
    # trust the intercept CA — outbound HTTPS from Windows is unreliable.)
    $sharedUv = Join-Path $Repo "tests\manual\vm_e2e\bin\uv.exe"
    $dest = "$env:USERPROFILE\.local\bin"
    if (-not (Test-Path $sharedUv)) {
        throw "Expected pre-staged uv.exe at $sharedUv but it was missing. Re-stage from the host: curl -sL -o /tmp/u.zip https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip && unzip -o /tmp/u.zip -d <repo>/tests/manual/vm_e2e/bin/"
    }
    Log "Copying pre-staged uv.exe from share to $dest…"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Force $sharedUv "$dest\uv.exe"
    $env:Path = "$dest;$env:Path"
    Log "uv ready: $(& uv --version)"
}

function Ensure-Python {
    # Detect a real (non-Store-stub) Python 3.13 if already installed.
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:ProgramFiles\Python313\python.exe"
    )) {
        if (Test-Path $candidate) {
            $verOut = & $candidate --version 2>&1 | Out-String
            if ($verOut -match 'Python 3\.13') {
                $env:Path = "$([System.IO.Path]::GetDirectoryName($candidate));$env:Path"
                Log "python already installed: $candidate ($($verOut.Trim()))"
                return
            }
        }
    }
    # Install via the pre-staged python.org installer (avoids winget + outbound HTTPS).
    $stagedInstaller = Join-Path $Repo "tests\manual\vm_e2e\bin\python-install.exe"
    if (-not (Test-Path $stagedInstaller)) {
        throw "Expected pre-staged Python installer at $stagedInstaller but it was missing."
    }
    Log "Running pre-staged Python installer (quiet, per-user, add to PATH)…"
    & $stagedInstaller /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Python installer exited with $LASTEXITCODE"
    }
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:ProgramFiles\Python313\python.exe"
    )) {
        if (Test-Path $candidate) {
            $env:Path = "$([System.IO.Path]::GetDirectoryName($candidate));$env:Path"
            Log "python installed: $candidate"
            return
        }
    }
    throw "Python installer ran (exit 0) but python.exe not found in expected paths."
}

# -----------------------------------------------------------------------------
# 2) Stage the repo locally so uv sync can write its venv on a normal drive
#    (uv refuses to write into UNC shares).
# -----------------------------------------------------------------------------
function Stage-Repo {
    # If a previous run left the service installed and running, its
    # python.exe is locked by the service process — Remove-Item would skip
    # those files, leaving a stale .venv that uv mistakes for a fresh one.
    # Stop and remove the service first so we can wipe cleanly.
    if (Get-Service WindowsMCPHost -ErrorAction SilentlyContinue) {
        Log "Stopping/removing prior WindowsMCPHost service before re-staging…"
        try { Stop-Service WindowsMCPHost -Force -ErrorAction SilentlyContinue } catch { }
        try { sc.exe delete WindowsMCPHost | Out-Null } catch { }
        Start-Sleep -Seconds 2  # let SCM finish + file handles release
    }
    if (Test-Path $LocalRepo) {
        Log "Refreshing $LocalRepo"
        Remove-Item -Recurse -Force "$LocalRepo\*" -ErrorAction SilentlyContinue
    } else {
        Log "Creating $LocalRepo"
        New-Item -ItemType Directory -Path $LocalRepo | Out-Null
    }
    robocopy $Repo $LocalRepo /MIR /XD .git .venv tests\manual\vm_e2e\.work | Out-Null
}

# -----------------------------------------------------------------------------
# 3) uv sync + install the host service
# -----------------------------------------------------------------------------
function Setup-Project {
    Push-Location $LocalRepo
    try {
        # Sandbox MITM proxy: tell uv to skip cert verification for PyPI hosts.
        # Same disposable-VM caveat as the .NET cert bypass earlier.
        $env:UV_INSECURE_HOST = "pypi.org files.pythonhosted.org github.com astral.sh objects.githubusercontent.com"
        Log "uv sync (UV_INSECURE_HOST set for MITM proxy)"
        Invoke-Native "uv_sync.log" { & uv sync }
        Log "Installing the host service (allow-user-binary-path because this is a VM)…"
        Invoke-Native "install.log" {
            & uv run windows-mcp service secure-desktop install `
                --policy allow_all --allow-user-binary-path --force
        }
    } finally {
        Pop-Location
    }
}

# -----------------------------------------------------------------------------
# 4) Verify the service is RUNNING, then run the MCP client.
# -----------------------------------------------------------------------------
function Verify-Service {
    $svc = Get-Service WindowsMCPHost -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        throw "Service WindowsMCPHost not registered"
    }
    if ($svc.Status -ne "Running") {
        throw "Service WindowsMCPHost not running: $($svc.Status)"
    }
    Log "Service WindowsMCPHost is Running"
}

function Run-MCP-Tests {
    Push-Location $LocalRepo
    try {
        # ----- phase 1: allow_all (clicks Yes, asserts UAC dismissed) -----
        Log "Setting policy=allow_all"
        Invoke-Native "set-policy-allow_all.log" {
            & uv run windows-mcp service secure-desktop set-policy allow_all
        }
        Log "Running mcp_client.py --mode allow_all (basic-user token via runas /trustlevel)"
        $allowJson = Join-Path $ResultsDir "results-allow_all.json"
        Invoke-Native "mcp_client-allow_all.log" {
            # Run the broker (and its child MCP server) at medium integrity so
            # Start-Process -Verb RunAs from inside the test actually fires UAC
            # rather than auto-elevating. runas /trustlevel:0x20000 strips the
            # admin token from the same user — no password required.
            & cmd.exe /c ("runas /trustlevel:0x20000 " +
                "`"uv run python tests\manual\vm_e2e\mcp_client.py " +
                "--results `"$allowJson`" --mode allow_all`"")
        }

        # ----- phase 2: block (asserts click is refused) -----
        Log "Setting policy=block"
        Invoke-Native "set-policy-block.log" {
            & uv run windows-mcp service secure-desktop set-policy block
        }
        Log "Running mcp_client.py --mode block (basic-user token via runas /trustlevel)"
        $blockJson = Join-Path $ResultsDir "results-block.json"
        Invoke-Native "mcp_client-block.log" {
            & cmd.exe /c ("runas /trustlevel:0x20000 " +
                "`"uv run python tests\manual\vm_e2e\mcp_client.py " +
                "--results `"$blockJson`" --mode block`"")
        }

        # ----- combined report ------------------------------------------------
        $allow = Get-Content $allowJson -Raw | ConvertFrom-Json
        $block = Get-Content $blockJson -Raw | ConvertFrom-Json
        $combined = [pscustomobject]@{
            started_at  = $allow.started_at
            finished_at = $block.finished_at
            transport   = $allow.transport
            phases      = @{
                allow_all = $allow
                block     = $block
            }
            summary = @{
                total  = ($allow.summary.total + $block.summary.total)
                passed = ($allow.summary.passed + $block.summary.passed)
                failed = ($allow.summary.failed + $block.summary.failed)
            }
        }
        $combined | ConvertTo-Json -Depth 8 | Set-Content -Path $ResultsJson
        Log "Combined results.json written: total=$($combined.summary.total) passed=$($combined.summary.passed) failed=$($combined.summary.failed)"
    } finally {
        Pop-Location
    }
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if (-not (Test-Path $ResultsDir)) {
    New-Item -ItemType Directory -Path $ResultsDir | Out-Null
}
Set-Content -Path $LocalLog -Value "run_all.ps1 started $(Get-Date -Format o)"
try { Copy-Item -Force $LocalLog $ShareLog -ErrorAction Stop } catch { }

try {
    # Pre-flight: dockur's autounattend disables UAC entirely
    # (EnableLUA=false). The whole point of this test is UAC handling, so we
    # need it on. If it's off, turn it on, schedule run_all.ps1 to fire on
    # next login, and reboot. The next boot's auto-login + scheduled task
    # will resume here with UAC active.
    $luaKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    $lua    = (Get-ItemProperty -Path $luaKey -Name EnableLUA -ErrorAction SilentlyContinue).EnableLUA
    if ($lua -ne 1) {
        Log "EnableLUA=$lua. Enabling UAC, scheduling re-run on next login, and rebooting…"
        Set-ItemProperty -Path $luaKey -Name EnableLUA -Type DWord -Value 1
        Set-ItemProperty -Path $luaKey -Name ConsentPromptBehaviorAdmin -Type DWord -Value 5
        $task = "windows-mcp-test-resume"
        # Use cmd to swallow schtasks's stderr-on-not-found that would otherwise
        # be promoted to a fatal error by $ErrorActionPreference=Stop.
        cmd.exe /c "schtasks.exe /Delete /TN $task /F >nul 2>&1" | Out-Null
        $tr = "powershell.exe -ExecutionPolicy Bypass -File \\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\run_all.ps1"
        cmd.exe /c "schtasks.exe /Create /TN $task /SC ONLOGON /RL HIGHEST /RU Docker /TR `"$tr`" /F" | Out-Null
        Log "Scheduled task $task. Rebooting in 5s…"
        Start-Sleep -Seconds 2
        shutdown.exe /r /t 5 /c "Enabling UAC for windows-mcp test"
        exit 0
    }
    # If we just resumed via scheduled task, remove the task so future logins
    # don't re-trigger the harness.
    cmd.exe /c "schtasks.exe /Delete /TN windows-mcp-test-resume /F >nul 2>&1" | Out-Null

    Ensure-Python
    Ensure-Uv
    Stage-Repo
    Setup-Project
    Verify-Service
    Run-MCP-Tests
    Log "DONE"
} catch {
    Log "FAILED: $($_.Exception.Message)"
    @{
        started_at = (Get-Date).ToString("o")
        finished_at = (Get-Date).ToString("o")
        transport  = "(bootstrap-failed)"
        results = @(@{
            name = "run_all.ps1 bootstrap"
            passed = $false
            detail = $_.Exception.Message
            duration_s = 0
        })
        summary = @{ total = 1; passed = 0; failed = 1 }
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $ResultsJson
    exit 1
}
