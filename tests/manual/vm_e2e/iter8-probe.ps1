# Probe-only validator: skip the install pipeline (CPB=4 already set by
# iter8-test.ps1), just fire UAC + read input desktop with the fixed
# IterEightDesk type. Use this for fast re-iteration after touching the
# probe code; use iter8-test.ps1 for a full clean install + probe.

$Share  = '\\host.lan\Data\Windows-MCP'
$Report = "$Share\iter8-probe.log"
$Marker = "$Share\iter8-probe.marker"

Remove-Item -Force $Report, $Marker -ErrorAction SilentlyContinue
"=== iter8-probe started $(Get-Date -Format o) ===" | Set-Content $Report

function Log($msg) {
    $line = "[$(Get-Date -Format HH:mm:ss)] $msg"
    Add-Content -Path $Report -Value $line
    Write-Host $line
}

# Confirm policy is still iter-8 (CPB=4 + POSD=0)
$uac = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' `
        -Name EnableLUA, ConsentPromptBehaviorAdmin, PromptOnSecureDesktop
Log "registry: EnableLUA=$($uac.EnableLUA) CPB=$($uac.ConsentPromptBehaviorAdmin) POSD=$($uac.PromptOnSecureDesktop)"
if ($uac.ConsentPromptBehaviorAdmin -ne 4 -or $uac.PromptOnSecureDesktop -ne 0) {
    Log "FAIL: policy is not iter-8 -- run iter8-test.ps1 first"
    "FAIL" | Set-Content $Marker
    exit 1
}

# Trigger UAC from medium integrity
$evidenceFile = 'C:\iter8-probe-fired.txt'
Remove-Item -Force $evidenceFile -ErrorAction SilentlyContinue
$trigger = @"
Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-Command',('Set-Content -Path $evidenceFile -Value (`"fired at `" + (Get-Date -Format o))')
"@
$trigPath = "$env:TEMP\iter8-probe-trigger.ps1"
Set-Content -Path $trigPath -Value $trigger
schtasks /Delete /TN iter8-probe-trigger /F 2>&1 | Out-Null
schtasks /Create /TN iter8-probe-trigger /TR "powershell.exe -ExecutionPolicy Bypass -File $trigPath" /SC ONCE /ST 23:59 /RL LIMITED /F | Out-Null
Log "Triggering UAC ..."
schtasks /Run /TN iter8-probe-trigger | Out-Null

# Poll for consent.exe (slow KVM-disabled QEMU needs ~60s)
$consent = $null
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    $consent = Get-Process consent -ErrorAction SilentlyContinue
    if ($consent) { break }
    Start-Sleep -Milliseconds 200
}
if (-not $consent) {
    Log "FAIL: consent.exe did not appear within 90s"
    "FAIL" | Set-Content $Marker
    schtasks /Delete /TN iter8-probe-trigger /F 2>&1 | Out-Null
    exit 1
}
Log "consent.exe pid=$($consent.Id) appeared"

# Desktop name probe with the FIXED type (CharSet=Unicode)
if (-not ([System.Management.Automation.PSTypeName]'IterEightDesk').Type) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class IterEightDesk {
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
$deskName = [IterEightDesk]::Current()
Log "current input desktop: '$deskName'"

# Verdict
if ($deskName -match 'Winlogon' -or $deskName -match 'denied: 5') {
    Log "VERDICT: FAIL -- UAC routed to Winlogon under CPB=4"
    "FAIL" | Set-Content $Marker
} elseif ($deskName -match '^Default$') {
    Log "VERDICT: PASS -- UAC on Default. Iter-8 CPB=4 confirmed."
    "PASS" | Set-Content $Marker
} else {
    Log "VERDICT: INDETERMINATE -- desktop name was '$deskName'"
    "INDETERMINATE" | Set-Content $Marker
}

# Cleanup
try { Stop-Process -Id $consent.Id -Force -ErrorAction SilentlyContinue } catch {}
schtasks /Delete /TN iter8-probe-trigger /F 2>&1 | Out-Null
Log "=== done ==="
