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
# 1) Bootstrap Python + uv if not already on PATH
# -----------------------------------------------------------------------------
function Ensure-Python {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        Log "python present: $(python --version)"
        return
    }
    Log "Installing Python via winget…"
    winget install --id Python.Python.3.13 --source winget --silent `
        --accept-source-agreements --accept-package-agreements | Out-Null
    $env:Path = "$env:LOCALAPPDATA\Programs\Python\Python313\;$env:Path"
}

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Log "uv present: $(uv --version)"
        return
    }
    Log "Installing uv…"
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
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
