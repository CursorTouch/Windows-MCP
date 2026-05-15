# setup.ps1 — ONE-TIME bring-up of a fresh dockur Win11 VM.
#
# Idempotent. Runs elevated (must). Does the following and only the following:
#
#   1. Install Python 3.13 (pre-staged installer in tests/manual/vm_e2e/bin/).
#   2. Install uv (pre-staged binary).
#   3. Persist $env:Path so the user's PATH at login includes uv.
#   4. Mirror the repo to C:\windows-mcp and run `uv sync` (installs windows-mcp
#      into a local .venv).
#   5. Set UAC registry values (EnableLUA=1, ConsentPromptBehaviorAdmin=2,
#      PromptOnSecureDesktop=1) so the test exercises a real Secure Desktop
#      consent flow.
#   6. Install the LocalSystem host service with `windows-mcp service
#      secure-desktop install --policy allow_all --allow-user-binary-path`.
#   7. Install the MCP server ONLOGON scheduled task with `windows-mcp install
#      --transport streamable-http --host 127.0.0.1 --port 8000`.
#   8. Register a one-shot ONLOGON scheduled task (`windows-mcp-test`,
#      non-elevated) that runs tests/manual/vm_e2e/test.ps1.
#   9. Reboot.
#
# AFTER REBOOT — independent of this script:
#   - SCM auto-starts WindowsMCPHost.
#   - `windows-mcp-server` task fires; the MCP server listens on
#     http://127.0.0.1:8000/mcp/.
#   - `windows-mcp-test` task fires test.ps1 (non-elevated) which connects
#     to the running MCP server, runs the assertions, and writes results.json.
#
# This script does NOT spawn windows-mcp at boot. It only configures the
# Windows-side mechanisms that windows-mcp itself provides for self-start.

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$Repo       = "\\host.lan\Data\Windows-MCP"
$LocalRepo  = "C:\windows-mcp"
$ResultsDir = Join-Path $Repo "tests\manual\vm_e2e"
$LocalLog   = "$env:TEMP\windows-mcp-setup.log"
$ShareLog   = Join-Path $ResultsDir "setup.log"

function Log($msg) {
    $ts = (Get-Date).ToString("HH:mm:ss")
    Add-Content -Path $LocalLog -Value "[$ts] $msg"
    Write-Host "[$ts] $msg"
    try { Copy-Item -Force $LocalLog $ShareLog -ErrorAction Stop } catch { }
}

function Invoke-Native {
    param([string]$LogName, [scriptblock]$Block)
    $localPath = "$env:TEMP\$LogName"
    $sharePath = Join-Path $ResultsDir $LogName
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Block 2>&1 | Tee-Object -FilePath $localPath | Out-Host
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    try { Copy-Item -Force $localPath $sharePath -ErrorAction Stop } catch { }
    if ($rc -ne 0) {
        throw "Native command in $LogName exited with $rc (see $sharePath)."
    }
}

function Ensure-Python {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:ProgramFiles\Python313\python.exe"
    )) {
        if (Test-Path $candidate) {
            $verOut = & $candidate --version 2>&1 | Out-String
            if ($verOut -match 'Python 3\.13') {
                $env:Path = "$([System.IO.Path]::GetDirectoryName($candidate));$env:Path"
                Log "python already installed: $candidate"
                return
            }
        }
    }
    $stagedInstaller = Join-Path $Repo "tests\manual\vm_e2e\bin\python-install.exe"
    if (-not (Test-Path $stagedInstaller)) {
        throw "Pre-staged Python installer missing at $stagedInstaller. Re-stage from host."
    }
    Log "Installing Python (quiet, per-user, PrependPath)…"
    & $stagedInstaller /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1 | Out-Null
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
    )) {
        if (Test-Path $candidate) {
            $env:Path = "$([System.IO.Path]::GetDirectoryName($candidate));$env:Path"
            Log "python installed: $candidate"
            return
        }
    }
    throw "Python installer ran but python.exe missing."
}

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Log "uv present: $(uv --version)"
        return
    }
    $sharedUv = Join-Path $Repo "tests\manual\vm_e2e\bin\uv.exe"
    if (-not (Test-Path $sharedUv)) {
        throw "Pre-staged uv.exe missing at $sharedUv. Re-stage from host."
    }
    $dest = "$env:USERPROFILE\.local\bin"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Force $sharedUv "$dest\uv.exe"
    $env:Path = "$dest;$env:Path"
    Log "uv installed at $dest\uv.exe"
}

function Persist-Path-For-User {
    # Append %USERPROFILE%\.local\bin to the *user* PATH (HKCU\Environment) so
    # uv and windows-mcp resolve after reboot. Use [Environment]::SetEnvironmentVariable
    # with User scope to also fire WM_SETTINGCHANGE.
    $uvBin = "$env:USERPROFILE\.local\bin"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $userPath) { $userPath = "" }
    if ($userPath -notlike "*$uvBin*") {
        $newPath = if ($userPath) { "$uvBin;$userPath" } else { $uvBin }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Log "Appended $uvBin to HKCU Path."
    } else {
        Log "User PATH already includes $uvBin."
    }
}

function Stage-Repo {
    if (Get-Service WindowsMCPHost -ErrorAction SilentlyContinue) {
        Log "Stopping/removing prior WindowsMCPHost before re-staging…"
        try { Stop-Service WindowsMCPHost -Force -ErrorAction SilentlyContinue } catch { }
        # Wait up to 20s for SCM to mark the service Stopped — Stop-Service
        # is async and uv sync will fail with "Access is denied" if the
        # service process still has windows-mcp.exe open under .venv.
        for ($i = 0; $i -lt 40; $i++) {
            $svc = Get-Service WindowsMCPHost -ErrorAction SilentlyContinue
            if (-not $svc -or $svc.Status -eq "Stopped") { break }
            Start-Sleep -Milliseconds 500
        }
        try { sc.exe delete WindowsMCPHost | Out-Null } catch { }
        # Belt-and-braces: nuke any leftover python.exe that's still holding
        # files in C:\windows-mcp (e.g. the SCM marked the service Stopped
        # but the host.py worker thread is mid-shutdown).
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path -like "$LocalRepo\.venv\*" } |
            ForEach-Object {
                Log "Killing leftover venv process pid=$($_.Id) name=$($_.ProcessName)"
                try { $_.Kill() } catch { }
            }
        Start-Sleep -Seconds 1
    }
    if (Test-Path $LocalRepo) {
        Log "Refreshing $LocalRepo"
        Remove-Item -Recurse -Force "$LocalRepo\*" -ErrorAction SilentlyContinue
    } else {
        New-Item -ItemType Directory -Path $LocalRepo | Out-Null
    }
    robocopy $Repo $LocalRepo /MIR /XD .git .venv tests\manual\vm_e2e\.work | Out-Null
}

function Uv-Sync {
    Push-Location $LocalRepo
    try {
        $env:UV_INSECURE_HOST = "pypi.org files.pythonhosted.org github.com astral.sh objects.githubusercontent.com"
        Log "uv sync (UV_INSECURE_HOST set for the sandbox MITM proxy)"
        Invoke-Native "uv_sync.log" { & uv sync }
    } finally {
        Pop-Location
    }
}

function Set-Uac-Config {
    $luaKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    Set-ItemProperty -Path $luaKey -Name EnableLUA                  -Type DWord -Value 1
    Set-ItemProperty -Path $luaKey -Name ConsentPromptBehaviorAdmin -Type DWord -Value 2
    Set-ItemProperty -Path $luaKey -Name PromptOnSecureDesktop      -Type DWord -Value 1
    Log "UAC: EnableLUA=1 ConsentPromptBehaviorAdmin=2 PromptOnSecureDesktop=1"
}

function Install-Host-Service {
    Push-Location $LocalRepo
    try {
        Log "Installing host service (allow-user-binary-path because this is a VM)..."
        # --self-sign-uia-worker drives the production build+self-sign+install
        # flow shipped in the wheel: ensures PyInstaller, freezes the worker
        # with the embedded uiAccess manifest, generates a self-signed cert,
        # plants it in LocalMachine\Root + \TrustedPublisher, signs the exe,
        # and copies into %ProgramFiles%\WindowsMCP\. Same code path an end
        # user gets when they answer 'y' to the interactive prompt.
        #
        # PIP_TRUSTED_HOST: the sandbox MITM proxy in the test harness breaks
        # TLS verification; pip ships its own resolver (not uv), so the
        # UV_INSECURE_HOST we set earlier doesn't carry over. Setting
        # PIP_TRUSTED_HOST is the pip equivalent. Production users with
        # direct PyPI access don't need it.
        $env:PIP_TRUSTED_HOST = "pypi.org files.pythonhosted.org"
        Invoke-Native "install-host.log" {
            & uv run windows-mcp service secure-desktop install `
                --policy allow_all --allow-user-binary-path `
                --self-sign-uia-worker --force
        }
    } finally { Pop-Location }
}

function Install-Server-AutoStart {
    Push-Location $LocalRepo
    try {
        Log "Installing 'windows-mcp install' ONLOGON task (server on 127.0.0.1:8000)…"
        # windows-mcp's own install command — registers windows-mcp-server task.
        # Force reinstall so any stale entry is replaced.
        Invoke-Native "install-server.log" {
            & uv run windows-mcp install `
                --transport streamable-http --host 127.0.0.1 --port 8000 --force
        }
    } finally { Pop-Location }
}

function Register-Test-Task {
    $task = "windows-mcp-test"
    $tr = "powershell.exe -ExecutionPolicy Bypass -File \\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\test.ps1"
    cmd.exe /c "schtasks.exe /Delete /TN $task /F >nul 2>&1" | Out-Null
    # Non-elevated: omit /RL HIGHEST. /IT ensures it runs interactively with
    # the user's standard token so the trigger inside test.ps1 fires real UAC.
    cmd.exe /c "schtasks.exe /Create /TN $task /SC ONLOGON /RU Docker /IT /TR `"$tr`" /F" | Out-Null
    Log "Registered test task '$task' (non-elevated, ONLOGON)."
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if (-not (Test-Path $ResultsDir)) { New-Item -ItemType Directory -Path $ResultsDir | Out-Null }
Set-Content -Path $LocalLog -Value "setup.ps1 started $(Get-Date -Format o)"
try { Copy-Item -Force $LocalLog $ShareLog -ErrorAction Stop } catch { }

try {
    Ensure-Python
    Ensure-Uv
    Persist-Path-For-User
    Stage-Repo
    Uv-Sync
    Set-Uac-Config
    Install-Host-Service
    Install-Server-AutoStart
    Register-Test-Task
    Log "SETUP DONE. Rebooting so SCM auto-start + ONLOGON tasks fire from a clean boot."
    Start-Sleep -Seconds 2
    shutdown.exe /r /t 5 /c "windows-mcp test setup complete, rebooting to validate auto-start"
    exit 0
} catch {
    Log "SETUP FAILED: $($_.Exception.Message)"
    exit 1
}
