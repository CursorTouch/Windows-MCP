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
$Log         = Join-Path $ResultsDir "run_all.log"

function Log($msg) {
    $ts = (Get-Date).ToString("HH:mm:ss")
    Add-Content -Path $Log -Value "[$ts] $msg"
    Write-Host "[$ts] $msg"
}

# -----------------------------------------------------------------------------
# 1) Bootstrap Python + uv. Don't trust `Get-Command python` — Win11 ships a
#    Microsoft Store *stub* of that name that opens the Store and does nothing.
#    Always check for a real interpreter (or install one) before proceeding.
# -----------------------------------------------------------------------------
function Test-RealPython {
    # The Store stub returns immediately with no output. A real interpreter
    # prints its version. Capture stdout/stderr explicitly.
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return $null }
    try {
        $ver = (& python --version 2>&1) | Out-String
        if ($ver -match 'Python (\d+\.\d+)') { return $cmd.Source }
    } catch { }
    return $null
}

function Ensure-Python {
    $real = Test-RealPython
    if ($real) {
        Log "python present: $real"
        return
    }
    Log "Installing Python via winget…"
    winget install --id Python.Python.3.13 --source winget --silent `
        --accept-source-agreements --accept-package-agreements 2>&1 |
        Out-File -FilePath (Join-Path $ResultsDir "winget-python.log") -Append

    # winget puts user-scope Python under %LOCALAPPDATA%\Programs\Python\…
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313",
        "$env:ProgramFiles\Python313",
        "$env:LOCALAPPDATA\Programs\Python\Python312"
    )) {
        if (Test-Path "$candidate\python.exe") {
            $env:Path = "$candidate;$candidate\Scripts;$env:Path"
            Log "python after install: $candidate\python.exe"
            return
        }
    }
    throw "Python installed via winget but python.exe not found in any expected path."
}

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Log "uv present: $(uv --version)"
        return
    }
    Log "Installing uv (in-process, no subshell)…"
    # Win11 PowerShell 5.1 defaults to TLS 1.0/1.1 for outbound HTTPS, which
    # Astral's CDN rejects with "Could not establish trust relationship". Force
    # TLS 1.2 before downloading the install script.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor `
        [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls
    # `irm | iex` in the SAME process so any env changes the installer makes
    # persist into our session.
    Invoke-Expression (Invoke-RestMethod -Uri https://astral.sh/uv/install.ps1)

    # Astral's installer drops uv.exe at $env:USERPROFILE\.local\bin per docs.
    foreach ($candidate in @(
        "$env:USERPROFILE\.local\bin",
        "$env:LOCALAPPDATA\uv\bin",
        "$env:LOCALAPPDATA\Programs\uv"
    )) {
        if (Test-Path "$candidate\uv.exe") {
            $env:Path = "$candidate;$env:Path"
            Log "uv after install: $candidate\uv.exe"
            return
        }
    }
    throw "uv install ran but uv.exe was not found in any expected path."
}

# -----------------------------------------------------------------------------
# 2) Stage the repo locally so uv sync can write its venv on a normal drive
#    (uv refuses to write into UNC shares).
# -----------------------------------------------------------------------------
function Stage-Repo {
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
        Log "uv sync"
        uv sync 2>&1 | Tee-Object -FilePath (Join-Path $ResultsDir "uv_sync.log")
        Log "Installing the host service (allow-user-binary-path because this is a VM)…"
        uv run windows-mcp service secure-desktop install `
            --policy allow_all --allow-user-binary-path --force 2>&1 |
            Tee-Object -FilePath (Join-Path $ResultsDir "install.log")
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
        uv run windows-mcp service secure-desktop set-policy allow_all 2>&1 |
            Tee-Object -FilePath (Join-Path $ResultsDir "set-policy-allow_all.log") | Out-Null
        Log "Running mcp_client.py --mode allow_all"
        $allowJson = Join-Path $ResultsDir "results-allow_all.json"
        uv run python tests\manual\vm_e2e\mcp_client.py `
            --results $allowJson --mode allow_all 2>&1 |
            Tee-Object -FilePath (Join-Path $ResultsDir "mcp_client-allow_all.log")

        # ----- phase 2: block (asserts click is refused) -----
        Log "Setting policy=block"
        uv run windows-mcp service secure-desktop set-policy block 2>&1 |
            Tee-Object -FilePath (Join-Path $ResultsDir "set-policy-block.log") | Out-Null
        Log "Running mcp_client.py --mode block"
        $blockJson = Join-Path $ResultsDir "results-block.json"
        uv run python tests\manual\vm_e2e\mcp_client.py `
            --results $blockJson --mode block 2>&1 |
            Tee-Object -FilePath (Join-Path $ResultsDir "mcp_client-block.log")

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
Set-Content -Path $Log -Value "run_all.ps1 started $(Get-Date -Format o)"

try {
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
