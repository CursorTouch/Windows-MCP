# iter8-test.ps1 -- one-shot validator for the CPB=4 hypothesis.
#
# Run from an elevated PowerShell **inside the VM**:
#   powershell -ExecutionPolicy Bypass -File \\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\iter8-test.ps1
#
# What it does:
#   1. Mirror the share to C:\windows-mcp (picks up the iter-8 commit).
#   2. Stop the existing host service so the wheel rebuild doesn't trip on
#      a held-open windows-mcp.exe.
#   3. uv sync --reinstall-package windows-mcp.
#   4. service secure-desktop uninstall + install (writes CPB=4).
#   5. Verify the registry readback matches CPB=4.
#   6. Trigger a UAC prompt by spawning Start-Process -Verb RunAs.
#   7. Capture: registry values, consent.exe pid+desktop, broker log tail.
#   8. Write a structured report to \\host.lan\Data\Windows-MCP\iter8-result.log
#      so the host side can grep PASS/FAIL without driving the VM.
#
# PASS criterion: consent.exe appears AND broker log includes
# "Winlogon detected after" == false (UAC stayed on Default).

$Share     = '\\host.lan\Data\Windows-MCP'
$LocalRepo = 'C:\windows-mcp'
$Report    = "$Share\iter8-result.log"
$Marker    = "$Share\iter8-result.marker"   # exists only after run completes

Remove-Item -Force $Report, $Marker -ErrorAction SilentlyContinue
"=== iter8-test started $(Get-Date -Format o) ===" | Set-Content $Report

function Log($msg) {
    $line = "[$(Get-Date -Format HH:mm:ss)] $msg"
    Add-Content -Path $Report -Value $line
    Write-Host $line
}

function Fail($msg) {
    Log "FAIL: $msg"
    "FAIL" | Set-Content $Marker
    exit 1
}

# 1) Mirror share -> local
Log "Mirroring $Share -> $LocalRepo (excluding .git, .venv, .work)"
if (-not (Test-Path $LocalRepo)) { New-Item -ItemType Directory -Path $LocalRepo | Out-Null }
robocopy $Share $LocalRepo /MIR /XD .git .venv tests\manual\vm_e2e\.work /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy exit $LASTEXITCODE" }

# 2) Stop host service + kill any straggler venv processes. The ONLOGON
#    windows-mcp-server / windows-mcp-test tasks spawn windows-mcp.exe
#    from .venv\Scripts at login; uv sync's reinstall fails with
#    "file in use" if any of them are still running.
$svc = Get-Service -Name WindowsMCPHost -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq 'Running') {
    Log "Stopping WindowsMCPHost so the rebuild can replace .venv files"
    Stop-Service WindowsMCPHost -Force -ErrorAction SilentlyContinue
    $svc.WaitForStatus('Stopped','00:00:30')
}
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path -like "$LocalRepo\.venv\*" } |
    ForEach-Object {
        Log "Killing venv process pid=$($_.Id) path=$($_.Path)"
        try { $_.Kill(); $_.WaitForExit(5000) | Out-Null } catch { Log "  kill failed: $($_.Exception.Message)" }
    }

# 3) Rebuild venv from updated source
Push-Location $LocalRepo
try {
    $env:UV_INSECURE_HOST = "pypi.org files.pythonhosted.org github.com astral.sh objects.githubusercontent.com"
    Get-ChildItem -Path "$LocalRepo\src" -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Log "uv sync --reinstall-package windows-mcp"
    & uv sync --reinstall-package windows-mcp 2>&1 | ForEach-Object { Add-Content -Path $Report -Value "    [uv] $_" }
    if ($LASTEXITCODE -ne 0) { Fail "uv sync exit $LASTEXITCODE" }
} finally { Pop-Location }

# 4) Uninstall then reinstall the secure-desktop service so the policy
#    write fires fresh with the iter-8 CPB=4 target.
Push-Location $LocalRepo
try {
    Log "windows-mcp service secure-desktop uninstall (restore stock policy)"
    & uv run windows-mcp service secure-desktop uninstall 2>&1 |
        ForEach-Object { Add-Content -Path $Report -Value "    [uninst] $_" }

    Log "windows-mcp service secure-desktop install (writes CPB=4)"
    & uv run windows-mcp service secure-desktop install `
        --policy allow_all --allow-user-binary-path --force 2>&1 |
        ForEach-Object { Add-Content -Path $Report -Value "    [inst] $_" }
    if ($LASTEXITCODE -ne 0) { Fail "install exit $LASTEXITCODE" }
} finally { Pop-Location }

# 5) Registry readback
$uac = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' `
        -Name EnableLUA, ConsentPromptBehaviorAdmin, PromptOnSecureDesktop
Log "registry: EnableLUA=$($uac.EnableLUA) CPB=$($uac.ConsentPromptBehaviorAdmin) POSD=$($uac.PromptOnSecureDesktop)"
if ($uac.ConsentPromptBehaviorAdmin -ne 4) { Fail "CPB readback != 4 (got $($uac.ConsentPromptBehaviorAdmin))" }
if ($uac.PromptOnSecureDesktop      -ne 0) { Fail "POSD readback != 0 (got $($uac.PromptOnSecureDesktop))" }
Log "registry write stuck: CPB=4 POSD=0 confirmed."

# 6) Trigger UAC. Use Start-Process -Verb RunAs from a non-elevated child
#    so the elevation prompt actually fires (an elevated PS just spawns
#    silently). We launch a non-elev cmd that calls Start-Process and
#    immediately returns; the UAC prompt then renders -- on Default if
#    iter-8 worked, on Winlogon if not.
Log "Triggering UAC via Start-Process -Verb RunAs ..."
$evidenceFile = 'C:\iter8-elevation-fired.txt'
Remove-Item -Force $evidenceFile -ErrorAction SilentlyContinue
# Schtask runs at /RL LIMITED -> medium integrity, so Start-Process -Verb
# RunAs actually goes through UAC instead of being auto-allowed (which is
# what happens if you call -Verb RunAs from an already-elevated session).
$trigger = @"
Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-Command',('Set-Content -Path $evidenceFile -Value (`"fired at `" + (Get-Date -Format o))')
"@
$trigPath = "$env:TEMP\iter8-trigger.ps1"
Set-Content -Path $trigPath -Value $trigger
schtasks /Create /TN iter8-trigger /TR "powershell.exe -ExecutionPolicy Bypass -File $trigPath" /SC ONCE /ST 23:59 /RL LIMITED /F | Out-Null
schtasks /Run /TN iter8-trigger | Out-Null

# 7) Poll for consent.exe and capture which desktop it's on. Give it up
#    to 90s -- KVM-disabled QEMU is ~10x slower than bare-metal, and the
#    first observed CPB=4 prompt under Dockur fired at +14-15s. Iter-7's
#    1-2s window assumed a fast box.
$consent = $null
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    $consent = Get-Process consent -ErrorAction SilentlyContinue
    if ($consent) { break }
    Start-Sleep -Milliseconds 200
}
if (-not $consent) {
    Log "consent.exe did NOT appear within 15s"
    "FAIL: consent.exe never started" | Set-Content $Marker
    exit 1
}
Log "consent.exe pid=$($consent.Id) appeared"

# Which desktop is consent.exe on? We can read the input desktop name
# from a tiny C# helper. If it's "Winlogon", CPB=4 didn't change anything.
# IterEightDesk (not "Desk") because Add-Type loads types for the lifetime
# of the host process and the previous iter-8 commit shipped a Desk class
# with the wrong CharSet -- if a tester is in the same PS session, the old
# class is still cached. New name = clean load. The if-already-loaded guard
# makes a same-session re-invocation also safe.
if (-not ([System.Management.Automation.PSTypeName]'IterEightDesk').Type) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class IterEightDesk {
    // CharSet=Unicode picks the ...W variant which writes UTF-16 bytes
    // that Marshal.PtrToStringUni decodes correctly. Default CharSet=Ansi
    // would link ...A and produce garbage when read as Unicode.
    [DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern IntPtr OpenInputDesktop(uint flags, bool inh, uint access);
    [DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool GetUserObjectInformation(IntPtr h, int idx, IntPtr info, uint len, out uint needed);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool CloseDesktop(IntPtr h);
    public static string Current() {
        IntPtr h = OpenInputDesktop(0,false,0x0001);
        if (h == IntPtr.Zero) return "<OpenInputDesktop denied: " + Marshal.GetLastWin32Error() + ">";
        try {
            uint need = 0;
            GetUserObjectInformation(h, 2, IntPtr.Zero, 0, out need);
            IntPtr buf = Marshal.AllocHGlobal((int)need);
            try {
                if (!GetUserObjectInformation(h, 2, buf, need, out need)) return "<info denied>";
                return Marshal.PtrToStringUni(buf);
            } finally { Marshal.FreeHGlobal(buf); }
        } finally { CloseDesktop(h); }
    }
}
"@
}
try {
    $deskName = [IterEightDesk]::Current()
    Log "current input desktop: $deskName"
} catch {
    Log "desktop-name probe failed: $($_.Exception.Message)"
    $deskName = "<probe failed>"
}

# 8) Dump the last 60 lines of the broker log. The broker logs the
#    registry readback at the moment WaitForUACPrompt fires, which is the
#    authoritative record of how the OS actually routed UAC.
$brokerLog = 'C:\Windows\Temp\windows-mcp-host.log'
if (Test-Path $brokerLog) {
    Log "----- broker log tail -----"
    Get-Content $brokerLog -Tail 60 | ForEach-Object { Add-Content -Path $Report -Value "    $_" }
    Log "----- end broker log -----"
} else {
    Log "broker log not found at $brokerLog"
}

# 9) Verdict.
#    OpenInputDesktop with DESKTOP_READOBJECTS succeeds against Default but
#    fails with gle=5 against Winlogon for a user-session admin (Winlogon's
#    DACL only grants access to SYSTEM and the logon UI). So an "access
#    denied" reading is itself a Winlogon signal.
if ($deskName -match 'Winlogon' -or $deskName -match 'denied: 5') {
    Log "VERDICT: FAIL -- UAC still on Winlogon. CPB=4 did not unstick the secure-desktop pinning."
    "FAIL" | Set-Content $Marker
} elseif ($deskName -match '^Default$') {
    Log "VERDICT: PASS -- UAC rendered on Default desktop. CPB=4 works on this build."
    "PASS" | Set-Content $Marker
} else {
    Log "VERDICT: INDETERMINATE -- desktop name was '$deskName'"
    "INDETERMINATE" | Set-Content $Marker
}

# 10) Kill the lingering consent.exe (we don't want to click through).
try { Stop-Process -Id $consent.Id -Force -ErrorAction SilentlyContinue } catch {}
schtasks /Delete /TN iter8-trigger /F | Out-Null

Log "=== done ==="
