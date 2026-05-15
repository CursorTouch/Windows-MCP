# build-and-sign-uia-worker.ps1 — VM-only helper.
#
# Produces a fully-functional UIAccess worker .exe inside the dockur VM
# without dragging in a commercial code-signing cert. The trick is that
# Windows accepts any Authenticode signature whose root CA is trusted on
# the local box — so we generate a one-shot self-signed cert, plant it
# in the machine's Trusted Root + Trusted Publisher stores, and sign
# with that.
#
# We also flip HKLM\...\Policies\System\EnableSecureUIAPaths = 0 so the
# binary can live anywhere (not just %ProgramFiles%). Production deploys
# should NOT do this — they should ship a real Authenticode-signed
# worker into %ProgramFiles%\WindowsMCP\.
#
# Inputs:
#   $LocalRepo : C:\windows-mcp (already mirrored from the share by setup.ps1)
#
# Outputs:
#   $LocalRepo\dist\windows-mcp-uia-worker.exe  (signed)
#
# Prints the path to stdout on success.

param(
    [string]$LocalRepo = "C:\windows-mcp"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

function Log($msg) { Write-Host "[uia-sign] $msg" }

# ----- 1. install PyInstaller into the venv --------------------------------
Log "Installing PyInstaller into $LocalRepo\.venv"
Push-Location $LocalRepo
try {
    $env:UV_INSECURE_HOST = "pypi.org files.pythonhosted.org github.com astral.sh objects.githubusercontent.com"
    & uv pip install pyinstaller 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "uv pip install pyinstaller failed ($LASTEXITCODE)" }
} finally { Pop-Location }

# ----- 2. build the unsigned worker ---------------------------------------
Log "Building windows-mcp-uia-worker.exe via PyInstaller"
Push-Location (Join-Path $LocalRepo "packaging")
try {
    & "$LocalRepo\.venv\Scripts\pyinstaller.exe" uia_worker.spec --clean --noconfirm 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally { Pop-Location }

$exe = Join-Path $LocalRepo "packaging\dist\windows-mcp-uia-worker.exe"
if (-not (Test-Path $exe)) { throw "Build succeeded but $exe not found." }
Log "Built: $exe"

# ----- 3. self-signed code-signing cert -----------------------------------
$certSubject = "CN=WindowsMCP-Dev-Test-Only"
$existing = Get-ChildItem Cert:\LocalMachine\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $certSubject } | Select-Object -First 1
if ($existing) {
    Log "Reusing existing cert thumbprint=$($existing.Thumbprint)"
    $cert = $existing
} else {
    Log "Creating self-signed code-signing cert ($certSubject)"
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $certSubject `
        -KeyUsage DigitalSignature `
        -KeyAlgorithm RSA -KeyLength 2048 `
        -HashAlgorithm SHA256 `
        -NotAfter (Get-Date).AddYears(5) `
        -CertStoreLocation Cert:\LocalMachine\My `
        -KeyExportPolicy Exportable
    Log "Created cert thumbprint=$($cert.Thumbprint)"
}

# Make sure the cert is trusted by the local machine: Trusted Root +
# Trusted Publisher. Both stores need the same cert for Authenticode
# to be considered "trusted by the local OS" during UIAccess checks.
foreach ($store in @("Root", "TrustedPublisher")) {
    $storeObj = New-Object System.Security.Cryptography.X509Certificates.X509Store `
        $store, "LocalMachine"
    $storeObj.Open("ReadWrite")
    if (-not ($storeObj.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint })) {
        $storeObj.Add($cert)
        Log "Added cert to LocalMachine\$store"
    }
    $storeObj.Close()
}

# ----- 4. sign the exe ----------------------------------------------------
Log "Signing $exe"
$sig = Set-AuthenticodeSignature -FilePath $exe -Certificate $cert `
    -HashAlgorithm SHA256 -IncludeChain All
if ($sig.Status -ne "Valid") {
    throw "Set-AuthenticodeSignature returned Status=$($sig.Status): $($sig.StatusMessage)"
}
Log "Signature status: $($sig.Status)"

# ----- 5. flip the trusted-path requirement off --------------------------
# UIAccess on Win10+ also requires the binary to live in a "trusted path"
# (Program Files / WinDir). Setting EnableSecureUIAPaths=0 lifts that
# restriction so we can run from $LocalRepo\packaging\dist\. Production
# deployment installs into %ProgramFiles%\WindowsMCP\ instead and leaves
# this policy alone.
$polKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
$cur = (Get-ItemProperty -Path $polKey -Name EnableSecureUIAPaths -ErrorAction SilentlyContinue).EnableSecureUIAPaths
if ($cur -ne 0) {
    Log "Setting EnableSecureUIAPaths=0 (was $cur) — VM-only override"
    Set-ItemProperty -Path $polKey -Name EnableSecureUIAPaths -Type DWord -Value 0
}

# ----- 6. report path to caller ------------------------------------------
Write-Output $exe
