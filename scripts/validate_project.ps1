[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$uv = 'C:\Users\andre\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe'
$evidenceDir = Join-Path $ProjectRoot '.orquestrador\evidencias'
$timestamp = "{0}-{1}-{2}" -f (Get-Date -Format 'yyyyMMdd-HHmmssfff'), $PID, ([guid]::NewGuid().ToString('N').Substring(0, 8))
$logPath = Join-Path $evidenceDir "validacao-$timestamp.log"
$summaryPath = Join-Path $evidenceDir 'ultima-validacao.json'

New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

function Write-AtomicJson {
    param(
        [Parameter(Mandatory)] $Value,
        [Parameter(Mandatory)] [string]$Path
    )

    $temporary = "$Path.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $Value | ConvertTo-Json -Depth 6 | Set-Content -Path $temporary -Encoding utf8
        Move-Item -Path $temporary -Destination $Path -Force
    }
    finally {
        Remove-Item $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Add-ValidationText {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string]$Text)

    $payload = $Text + [Environment]::NewLine
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($payload)
    $deadline = (Get-Date).AddSeconds(10)
    $delayMilliseconds = 25
    while ($true) {
        $stream = $null
        try {
            $stream = [IO.FileStream]::new(
                $logPath,
                [IO.FileMode]::OpenOrCreate,
                [IO.FileAccess]::Write,
                [IO.FileShare]::ReadWrite
            )
            [void]$stream.Seek(0, [IO.SeekOrigin]::End)
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
            break
        }
        catch [IO.IOException] {
            if ((Get-Date) -ge $deadline) { throw }
            Start-Sleep -Milliseconds $delayMilliseconds
            $delayMilliseconds = [Math]::Min($delayMilliseconds * 2, 500)
        }
        catch [UnauthorizedAccessException] {
            if ((Get-Date) -ge $deadline) { throw }
            Start-Sleep -Milliseconds $delayMilliseconds
            $delayMilliseconds = [Math]::Min($delayMilliseconds * 2, 500)
        }
        finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
    }
    [Console]::Out.Write($payload)
}

function Write-ValidationLine {
    param([Parameter(Mandatory)] $Value)

    Add-ValidationText -Text ([string]$Value)
}

function Invoke-RedirectedNativeStep {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$ArgumentList,
        [Parameter(Mandatory)] [string]$WorkingDirectory
    )

    Write-ValidationLine "=== $Name ==="
    $started = Get-Date
    $stdoutPath = Join-Path $evidenceDir "$Name-$timestamp.stdout.log"
    $stderrPath = Join-Path $evidenceDir "$Name-$timestamp.stderr.log"
    Remove-Item $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    foreach ($path in @($stdoutPath, $stderrPath)) {
        if (Test-Path $path) {
            $content = [IO.File]::ReadAllText($path)
            if (-not [string]::IsNullOrEmpty($content)) {
                Add-ValidationText -Text $content.TrimEnd("`r", "`n")
            }
        }
    }

    $duration = [math]::Round(((Get-Date) - $started).TotalSeconds, 2)
    if ($process.ExitCode -ne 0) {
        throw "$Name falhou com código $($process.ExitCode) após $duration segundos."
    }

    [pscustomobject]@{
        name = $Name
        status = 'passed'
        duration_seconds = $duration
        stdout_log = $stdoutPath
        stderr_log = $stderrPath
    }
}

function Invoke-ValidationStep {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [scriptblock]$Action
    )

    Write-ValidationLine "=== $Name ==="
    $started = Get-Date
    & $Action 2>&1 | ForEach-Object { Write-ValidationLine $_ }
    $exitCode = $LASTEXITCODE
    $duration = [math]::Round(((Get-Date) - $started).TotalSeconds, 2)

    if ($exitCode -ne 0) {
        throw "$Name falhou com código $exitCode após $duration segundos."
    }

    [pscustomobject]@{
        name = $Name
        status = 'passed'
        duration_seconds = $duration
    }
}

if (-not (Test-Path $python -PathType Leaf)) {
    throw "Python do ambiente virtual não encontrado: $python"
}
if (-not (Test-Path $uv -PathType Leaf)) {
    throw "uv não encontrado: $uv"
}

$results = @()
try {
    $results += Invoke-ValidationStep -Name 'ruff' -Action {
        & $python -m ruff check (Join-Path $ProjectRoot 'src') (Join-Path $ProjectRoot 'tests') (Join-Path $ProjectRoot 'scripts')
    }
    $results += Invoke-ValidationStep -Name 'pytest' -Action {
        $pytestTemp = Join-Path $ProjectRoot ".orquestrador\pytest-temp\$timestamp"
        New-Item -ItemType Directory -Path $pytestTemp -Force | Out-Null
        & $python -m pytest -q --basetemp $pytestTemp (Join-Path $ProjectRoot 'tests')
    }
    $results += Invoke-RedirectedNativeStep `
        -Name 'build' `
        -FilePath $uv `
        -ArgumentList @('build') `
        -WorkingDirectory $ProjectRoot

    $summary = [ordered]@{
        checked_at = (Get-Date).ToString('o')
        status = 'passed'
        project_root = $ProjectRoot
        results = $results
        log = $logPath
    }
    Write-AtomicJson -Value $summary -Path $summaryPath
    Write-Output "VALIDATION_PASSED"
    Write-Output $summaryPath
}
catch {
    $summary = [ordered]@{
        checked_at = (Get-Date).ToString('o')
        status = 'failed'
        project_root = $ProjectRoot
        error = $_.Exception.Message
        results = $results
        log = $logPath
    }
    Write-AtomicJson -Value $summary -Path $summaryPath
    Write-Error $_
    exit 1
}
