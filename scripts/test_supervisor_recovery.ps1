param(
    [switch]$Execute,
    [string]$ProjectRoot = 'D:\Projetos\WINDOWS-MCP-TEST',
    [string]$ProfileName = 'windows-mcp-gpt-managed',
    [int]$TimeoutSeconds = 180,
    [int]$StabilitySeconds = 30
)
$ErrorActionPreference = 'Stop'
if(-not $Execute){
    $guardDir = Join-Path 'D:\Projetos\WINDOWS-MCP-TEST' '.orquestrador\evidencias\guards'
    New-Item $guardDir -ItemType Directory -Force | Out-Null
    [pscustomobject]@{script=$MyInvocation.MyCommand.Path;status='SKIPPED_NO_EXECUTE';timestamp=(Get-Date).ToString('o')} | ConvertTo-Json | Add-Content (Join-Path $guardDir 'destructive-task-guard.log') -Encoding UTF8
    exit 0
}
$started = Get-Date
$stamp = $started.ToString('yyyyMMdd-HHmmss')
$evidenceDir = Join-Path $ProjectRoot '.orquestrador\evidencias\recovery'
New-Item $evidenceDir -ItemType Directory -Force | Out-Null
$evidence = Join-Path $evidenceDir "supervisor-recovery-$stamp.json"
$tmp = "$evidence.tmp"
$healthFile = Join-Path $env:USERPROFILE '.local\state\tunnel-client\health\windows-mcp-gpt.url'
$heartbeatFile = Join-Path $ProjectRoot '.orquestrador\supervisor\heartbeat.json'
$expectedCommand = 'D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio'
function Get-Tunnels {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'tunnel-client.exe' -and $_.CommandLine -match $ProfileName
    })
}
function Read-Runtime {
    $result = [ordered]@{
        ok = $false
        base_url = ''
        health = ''
        ready = ''
        command = ''
        mcp_pid = 0
        probe = ''
        error = ''
    }
    try {
        if(-not (Test-Path $healthFile)){throw 'health URL file missing'}
        $base = (Get-Content $healthFile -Raw).Trim().TrimEnd('/')
        $status = Invoke-RestMethod "$base/api/status" -TimeoutSec 5
        $system = Invoke-RestMethod "$base/api/system" -TimeoutSec 5
        $live = (Invoke-WebRequest "$base/healthz" -UseBasicParsing -TimeoutSec 5).Content.Trim()
        $ready = (Invoke-WebRequest "$base/readyz" -UseBasicParsing -TimeoutSec 5).Content.Trim()
        $main = @($status.channels | Where-Object name -eq 'main')[0]
        $details = @{}
        foreach($item in @($main.details)){$details[[string]$item.key]=[string]$item.value}
        $result.base_url = $base
        $result.health = $live
        $result.ready = $ready
        $result.command = $details.command
        $result.mcp_pid = [int]$details.pid
        $result.probe = [string]$system.main_channel_probe_status
        $result.ok = ($live -eq 'live' -and $ready -eq 'ready' -and $result.command -eq $expectedCommand -and $result.probe -eq 'ok' -and (Get-Process -Id $result.mcp_pid -ErrorAction SilentlyContinue))
    } catch {
        $result.error = $_.Exception.Message
    }
    [pscustomobject]$result
}
$result = [ordered]@{
    started_at = $started.ToString('o')
    old_tunnel_pid = $null
    new_tunnel_pid = $null
    recovery_seconds = $null
    recovered = $false
    stable = $false
    max_tunnel_count = 0
    duplicate_samples = 0
    runtime = $null
    heartbeat = $null
    process_tree = @()
    error = ''
}
try {
    $before = @(Get-Tunnels)
    if($before.Count -ne 1){throw "expected one tunnel before test, found $($before.Count)"}
    $oldPid = [int]$before[0].ProcessId
    $result.old_tunnel_pid = $oldPid
    Start-Sleep -Seconds 8
    Stop-Process -Id $oldPid -Force
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Seconds 2
        $tunnels = @(Get-Tunnels)
        if($tunnels.Count -gt $result.max_tunnel_count){$result.max_tunnel_count=$tunnels.Count}
        if($tunnels.Count -gt 1){$result.duplicate_samples++}
        $runtime = Read-Runtime
        if($tunnels.Count -eq 1 -and [int]$tunnels[0].ProcessId -ne $oldPid -and $runtime.ok){
            $result.new_tunnel_pid = [int]$tunnels[0].ProcessId
            $result.runtime = $runtime
            $result.recovery_seconds = [math]::Round(((Get-Date)-$started).TotalSeconds-8,3)
            $result.recovered = $true
            break
        }
    } until((Get-Date) -ge $deadline)
    if(-not $result.recovered){throw 'supervisor did not restore a healthy tunnel within timeout'}
    $stableUntil = (Get-Date).AddSeconds($StabilitySeconds)
    $stable = $true
    do {
        Start-Sleep -Seconds 3
        $tunnels = @(Get-Tunnels)
        if($tunnels.Count -gt $result.max_tunnel_count){$result.max_tunnel_count=$tunnels.Count}
        if($tunnels.Count -gt 1){$result.duplicate_samples++}
        $runtime = Read-Runtime
        if($tunnels.Count -ne 1 -or [int]$tunnels[0].ProcessId -ne $result.new_tunnel_pid -or -not $runtime.ok){$stable=$false}
    } until((Get-Date) -ge $stableUntil)
    $result.stable = $stable
    if(Test-Path $heartbeatFile){$result.heartbeat=Get-Content $heartbeatFile -Raw | ConvertFrom-Json}
    $all = Get-CimInstance Win32_Process
    $front = @([int]$result.new_tunnel_pid)
    $rows = @($all | Where-Object ProcessId -eq $result.new_tunnel_pid)
    while($front.Count){
        $children = @($all | Where-Object {$front -contains $_.ParentProcessId})
        $rows += $children
        $front = @($children.ProcessId)
    }
    $result.process_tree = @($rows | Select-Object Name,ProcessId,ParentProcessId,CommandLine)
    if(-not $result.stable){throw 'recovered tunnel did not remain stable during observation'}
    if($result.duplicate_samples -ne 0){throw 'duplicate tunnel process detected during recovery'}
} catch {
    $result.error = $_.Exception.Message
}
$result['completed_at'] = (Get-Date).ToString('o')
$result['passed'] = [bool]($result.recovered -and $result.stable -and $result.duplicate_samples -eq 0 -and -not $result.error)
$result | ConvertTo-Json -Depth 20 | Set-Content $tmp -Encoding UTF8
Move-Item $tmp $evidence -Force
$result | ConvertTo-Json -Depth 20
