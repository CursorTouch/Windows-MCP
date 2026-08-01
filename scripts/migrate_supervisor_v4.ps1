param(
    [switch]$Execute,
    [string]$ProjectRoot = 'D:\Projetos\WINDOWS-MCP-TEST',
    [string]$SupervisorTask = 'Windows-MCP-GPT-Supervisor',
    [string]$ProfileName = 'windows-mcp-gpt-managed',
    [int]$TimeoutSeconds = 180,
    [int]$StabilitySeconds = 60
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
$evidenceDir = Join-Path $ProjectRoot '.orquestrador\evidencias\migration'
New-Item $evidenceDir -ItemType Directory -Force | Out-Null
$evidence = Join-Path $evidenceDir "supervisor-v4-migration-$stamp.json"
$progress = Join-Path $evidenceDir 'supervisor-v4-migration-progress.json'
$healthFile = Join-Path $env:USERPROFILE '.local\state\tunnel-client\health\windows-mcp-gpt.url'
$stateFile = Join-Path $ProjectRoot '.orquestrador\supervisor\state.json'
$lockFile = Join-Path $ProjectRoot '.orquestrador\supervisor\lock.json'
$heartbeatFile = Join-Path $ProjectRoot '.orquestrador\supervisor\heartbeat.json'
$profileFile = Join-Path $ProjectRoot '.tunnel-client\profiles\windows-mcp-gpt-managed.yaml'
$expectedCommand = 'D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio'
$expectedTtl = '336h'
$result = [ordered]@{
    started_at = $started.ToString('o')
    stage = 'started'
    old_tunnel_pid = $null
    new_tunnel_pid = $null
    recovery_seconds = $null
    stability_seconds = $StabilitySeconds
    max_tunnel_count = 0
    duplicate_samples = 0
    profile_command = ''
    supervisor_pid = $null
    legacy_tasks = @()
    heartbeat = $null
    runtime = $null
    error = ''
    completed_at = $null
    passed = $false
}
function Save-Json([string]$Path, $Value) {
    $tmp = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 30 | Set-Content $tmp -Encoding UTF8
    Move-Item $tmp $Path -Force
}
function Set-Stage([string]$Stage) {
    $result.stage = $Stage
    Save-Json $progress $result
}
function Disable-LegacyRuntimeTasks {
    $legacyNames = @(
        'Windows MCP GPT Watchdog',
        'Windows MCP GPT HTTP Server',
        'Windows MCP GPT HTTP Migration Once'
    )
    $taskEvidenceDir = Join-Path $evidenceDir "legacy-tasks-$stamp"
    New-Item -ItemType Directory -Path $taskEvidenceDir -Force | Out-Null
    foreach($name in $legacyNames){
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if(-not $task){continue}
        $safeName = ($name -replace '[^A-Za-z0-9.-]', '_') + '.xml'
        Export-ScheduledTask -TaskName $name | Set-Content (Join-Path $taskEvidenceDir $safeName) -Encoding UTF8
        if($task.Settings.Enabled){
            Disable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
        }
        $current = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        if($current.Settings.Enabled){throw "legacy conflicting task remained enabled: $name"}
        $result.legacy_tasks += [pscustomobject]@{
            name = $name
            enabled = [bool]$current.Settings.Enabled
            state = [string]$current.State
            xml = (Join-Path $taskEvidenceDir $safeName)
        }
    }
}
function Get-Tunnels {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'tunnel-client.exe' -and $_.CommandLine -match $ProfileName
    })
}
function Get-Runtime {
    $runtime = [ordered]@{ok=$false;base_url='';health='';ready='';command='';mcp_pid=0;probe='';error=''}
    try {
        if(-not (Test-Path $healthFile)){throw 'health URL file missing'}
        $base = (Get-Content $healthFile -Raw).Trim().TrimEnd('/')
        $live = (Invoke-WebRequest "$base/healthz" -UseBasicParsing -TimeoutSec 5).Content.Trim()
        $ready = (Invoke-WebRequest "$base/readyz" -UseBasicParsing -TimeoutSec 5).Content.Trim()
        $status = Invoke-RestMethod "$base/api/status" -TimeoutSec 5
        $system = Invoke-RestMethod "$base/api/system" -TimeoutSec 5
        $main = @($status.channels | Where-Object name -eq 'main')[0]
        $details = @{}
        foreach($item in @($main.details)){$details[[string]$item.key]=[string]$item.value}
        $runtime.base_url=$base
        $runtime.health=$live
        $runtime.ready=$ready
        $runtime.command=$details.command
        $runtime.mcp_pid=[int]$details.pid
        $runtime.probe=[string]$system.main_channel_probe_status
        $runtime.ok=[bool]($live -eq 'live' -and $ready -eq 'ready' -and $runtime.command -eq $expectedCommand -and $runtime.probe -eq 'ok' -and (Get-Process -Id $runtime.mcp_pid -ErrorAction SilentlyContinue))
    } catch {$runtime.error=$_.Exception.Message}
    [pscustomobject]$runtime
}
try {
    Set-Stage 'legacy_tasks'
    Disable-LegacyRuntimeTasks
    Set-Stage 'legacy_tasks_disabled'
    Set-Stage 'preflight'
    $before = @(Get-Tunnels)
    if($before.Count -ne 1){throw "expected one tunnel before migration, found $($before.Count)"}
    $result.old_tunnel_pid = [int]$before[0].ProcessId
    $serverPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if(-not (Test-Path $serverPython)){throw "missing server Python: $serverPython"}
    $profile = Get-Content $profileFile -Raw | ConvertFrom-Json
    $backup = "$profileFile.backup.$stamp"
    Copy-Item $profileFile $backup -Force
    $profile.mcp.commands = @([pscustomobject]@{channel='main';command=$expectedCommand})
    $profile.mcp | Add-Member -NotePropertyName connection_max_ttl -NotePropertyValue $expectedTtl -Force
    $profile | ConvertTo-Json -Depth 20 | Set-Content "$profileFile.tmp" -Encoding UTF8
    Move-Item "$profileFile.tmp" $profileFile -Force
    $result.profile_command = $expectedCommand
    $result.mcp_connection_max_ttl = $expectedTtl
    Set-Stage 'profile_migrated'
    Stop-ScheduledTask -TaskName $SupervisorTask -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 4
    if(Test-Path $lockFile){
        try {
            $lock = Get-Content $lockFile -Raw | ConvertFrom-Json
            if(-not (Get-Process -Id ([int]$lock.pid) -ErrorAction SilentlyContinue)){Remove-Item $lockFile -Force}
        } catch {Remove-Item $lockFile -Force -ErrorAction SilentlyContinue}
    }
    if(Test-Path $stateFile){
        $state = Get-Content $stateFile -Raw | ConvertFrom-Json
        $state | Add-Member -NotePropertyName restart_history -NotePropertyValue @() -Force
        $state | Add-Member -NotePropertyName unhealthy_cycles -NotePropertyValue 0 -Force
        $state | Add-Member -NotePropertyName healthy_cycles -NotePropertyValue 0 -Force
        $state | Add-Member -NotePropertyName last_error -NotePropertyValue '' -Force
        $state | ConvertTo-Json -Depth 30 | Set-Content "$stateFile.tmp" -Encoding UTF8
        Move-Item "$stateFile.tmp" $stateFile -Force
    }
    Set-Stage 'old_supervisor_stopped'
    $old = @(Get-Tunnels)
    foreach($tunnel in $old){Stop-Process -Id ([int]$tunnel.ProcessId) -Force -ErrorAction SilentlyContinue}
    Remove-Item $healthFile -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Set-Stage 'old_tunnel_stopped'
    Start-ScheduledTask -TaskName $SupervisorTask
    Set-Stage 'new_supervisor_started'
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Seconds 2
        $tunnels = @(Get-Tunnels)
        if($tunnels.Count -gt $result.max_tunnel_count){$result.max_tunnel_count=$tunnels.Count}
        if($tunnels.Count -gt 1){$result.duplicate_samples++}
        $runtime = Get-Runtime
        if($tunnels.Count -eq 1 -and [int]$tunnels[0].ProcessId -ne $result.old_tunnel_pid -and $runtime.ok){
            $result.new_tunnel_pid=[int]$tunnels[0].ProcessId
            $result.runtime=$runtime
            $result.recovery_seconds=[math]::Round(((Get-Date)-$started).TotalSeconds,3)
            break
        }
    } until((Get-Date) -ge $deadline)
    if(-not $result.new_tunnel_pid){throw 'new transport-aware tunnel did not become healthy within timeout'}
    Set-Stage 'recovered'
    $stableUntil=(Get-Date).AddSeconds($StabilitySeconds)
    do {
        Start-Sleep -Seconds 3
        $tunnels=@(Get-Tunnels)
        if($tunnels.Count -gt $result.max_tunnel_count){$result.max_tunnel_count=$tunnels.Count}
        if($tunnels.Count -gt 1){$result.duplicate_samples++}
        $runtime=Get-Runtime
        if($tunnels.Count -ne 1 -or [int]$tunnels[0].ProcessId -ne $result.new_tunnel_pid -or -not $runtime.ok){throw 'new tunnel failed during stability window'}
    } until((Get-Date) -ge $stableUntil)
    if($result.duplicate_samples -ne 0){throw 'duplicate tunnel detected during migration'}
    if(Test-Path $heartbeatFile){$result.heartbeat=Get-Content $heartbeatFile -Raw | ConvertFrom-Json;$result.supervisor_pid=[int]$result.heartbeat.pid}
    $result.stage='completed'
    $result.passed=$true
} catch {
    $result.error=$_.Exception.Message
    $result.stage='failed'
}
$result.completed_at=(Get-Date).ToString('o')
Save-Json $evidence $result
Save-Json $progress $result
$result | ConvertTo-Json -Depth 30
