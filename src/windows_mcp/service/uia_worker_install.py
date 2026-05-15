"""Build + self-sign + install the UIAccess worker.

Driven by ``windows-mcp service secure-desktop install``. The whole flow:

  1. Ensure PyInstaller is importable (auto-installs into the current Python
     environment if it isn't — service-mode install is opt-in and already
     requires admin, so a one-time PyPI fetch is acceptable here).
  2. Freeze ``windows_mcp.service.user_session_worker`` into a single
     ``windows-mcp-uia-worker.exe`` with the manifest from this package
     (uiAccess="true", requireAdministrator) embedded.
  3. Generate a self-signed code-signing certificate in
     ``Cert:\\LocalMachine\\My``, copy it to ``Root`` and
     ``TrustedPublisher`` so Windows treats the resulting signature as
     trusted on *this* machine only.
  4. Sign the frozen exe with that cert.
  5. Copy the signed binary into ``%ProgramFiles%\\WindowsMCP\\``, lock
     the directory's ACL down to Administrators + SYSTEM.
  6. Return the installed absolute path so the caller can persist it in
     HKLM via ``policy.write_uia_worker_path``.

All cert + signing logic is shelled out to PowerShell (`New-SelfSignedCertificate`,
`Set-AuthenticodeSignature`) because those cmdlets are present on every
modern Windows install and we'd otherwise be hand-rolling crypt32 ctypes
calls for no good reason.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

# Lives next to this module; copied in as package_data via pyproject.toml.
_MANIFEST_NAME = "_uia_worker.manifest"
_WORKER_EXE_NAME = "windows-mcp-uia-worker.exe"
_INSTALL_DIR = Path(
    os.environ.get("ProgramFiles", r"C:\Program Files")
) / "WindowsMCP"
_CERT_SUBJECT = "CN=WindowsMCP-Local-UiaWorker"


# ---------------------------------------------------------------------------
# PyInstaller bootstrap
# ---------------------------------------------------------------------------

def _have_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_pyinstaller(progress: callable | None = None) -> None:
    """Install PyInstaller into the current interpreter if not present."""
    if _have_pyinstaller():
        return
    if progress:
        progress("Installing PyInstaller (one-time, ~25 MB)…")
    rc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "pyinstaller"],
        check=False,
    ).returncode
    if rc != 0 or not _have_pyinstaller():
        raise RuntimeError(
            "pip install pyinstaller failed. Install it manually with: "
            f"{sys.executable} -m pip install pyinstaller"
        )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _resolve_manifest() -> Path:
    """Return the absolute path to ``_uia_worker.manifest`` shipped with this package."""
    here = Path(__file__).resolve().parent
    manifest = here / _MANIFEST_NAME
    if not manifest.is_file():
        raise FileNotFoundError(
            f"Bundled UIAccess manifest missing at {manifest}. "
            "The wheel may be corrupt or the package data exclude is wrong."
        )
    return manifest


def _resolve_worker_script() -> Path:
    from windows_mcp.service import user_session_worker
    return Path(user_session_worker.__file__).resolve()


def build_worker(workdir: Path, progress: callable | None = None) -> Path:
    """Run PyInstaller to freeze the worker into a single exe.

    Returns the absolute path to the built exe.
    """
    ensure_pyinstaller(progress=progress)
    manifest = _resolve_manifest()
    script = _resolve_worker_script()
    src_root = script.parents[2]  # .../site-packages

    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    dist_dir = workdir / "dist"
    build_dir = workdir / "build"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--log-level", "WARN",
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(workdir),
        "--name", "windows-mcp-uia-worker",
        "--manifest", str(manifest),
        "--paths", str(src_root),
        # comtypes generates COM proxy modules at runtime; PyInstaller's
        # static analysis can't see them, so collect the whole subtree.
        "--collect-submodules", "comtypes",
        "--hidden-import", "windows_mcp.service.secure_desktop",
        # Trim things the worker doesn't actually need; keeps the exe under
        # ~15 MB instead of pulling in fastmcp / mcp / uvicorn / pydantic.
        "--exclude-module", "fastmcp",
        "--exclude-module", "mcp",
        "--exclude-module", "starlette",
        "--exclude-module", "uvicorn",
        "--exclude-module", "sse_starlette",
        "--exclude-module", "pydantic",
        "--exclude-module", "posthog",
        str(script),
    ]
    if progress:
        progress("Building UIA worker .exe (this takes 60–120s; PyInstaller output follows)…")
    rc = subprocess.run(cmd, check=False).returncode
    exe = dist_dir / _WORKER_EXE_NAME
    if rc != 0 or not exe.is_file():
        raise RuntimeError(
            f"PyInstaller build failed (exit={rc}). See output above for details."
        )
    return exe


# ---------------------------------------------------------------------------
# Cert + sign (PowerShell)
# ---------------------------------------------------------------------------

_GENERATE_CERT_PS = textwrap.dedent(
    r"""
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Subject)
    $ErrorActionPreference = 'Stop'
    # Reuse an existing cert if one already exists with this subject
    # (idempotent re-runs of install). Otherwise create a fresh one.
    $existing = Get-ChildItem Cert:\LocalMachine\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $Subject } | Select-Object -First 1
    if ($existing) {
        $cert = $existing
    } else {
        $cert = New-SelfSignedCertificate `
            -Type CodeSigningCert `
            -Subject $Subject `
            -KeyUsage DigitalSignature `
            -KeyAlgorithm RSA -KeyLength 2048 `
            -HashAlgorithm SHA256 `
            -NotAfter (Get-Date).AddYears(5) `
            -CertStoreLocation Cert:\LocalMachine\My `
            -KeyExportPolicy NonExportable
    }
    # Plant a public copy in Root + TrustedPublisher so the OS treats
    # the signed binary as trusted at runtime.
    foreach ($store in @('Root','TrustedPublisher')) {
        $s = New-Object System.Security.Cryptography.X509Certificates.X509Store $store,'LocalMachine'
        $s.Open('ReadWrite')
        if (-not ($s.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint })) {
            $s.Add($cert)
        }
        $s.Close()
    }
    Write-Output $cert.Thumbprint
    """
)


_SIGN_PS = textwrap.dedent(
    r"""
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Thumbprint,
        [Parameter(Mandatory)][string]$ExePath
    )
    $ErrorActionPreference = 'Stop'
    $cert = Get-Item "Cert:\LocalMachine\My\$Thumbprint"
    $sig  = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert `
        -HashAlgorithm SHA256 -IncludeChain All
    if ($sig.Status -ne 'Valid') {
        throw "Signature status is '$($sig.Status)': $($sig.StatusMessage)"
    }
    """
)


def _run_powershell(script: str, *params: tuple[str, str]) -> str:
    """Run a PowerShell script with named parameters via a temp .ps1 file.

    *params* is a sequence of ``(name, value)`` pairs that map onto the
    script's declared ``param(...)`` block. Using ``-File`` (not
    ``-Command``) and a real script file is the only reliable way to
    pass strings containing arbitrary characters into PowerShell from
    subprocess; ``-Command "<script>" arg`` does *not* populate
    ``$args`` consistently across PowerShell versions.

    Returns the trimmed stdout. Raises ``RuntimeError`` with stderr on
    non-zero exit.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(script)
        script_path = tmp.name
    try:
        cmd = [
            "powershell.exe",
            "-NoLogo", "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", script_path,
        ]
        for name, value in params:
            cmd.extend([f"-{name}", value])
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if p.returncode != 0:
            raise RuntimeError(
                f"PowerShell call failed (exit={p.returncode}): "
                f"{p.stderr.strip() or p.stdout.strip()}"
            )
        return p.stdout.strip()
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def generate_local_cert(progress: callable | None = None) -> str:
    """Generate (or reuse) a self-signed code-signing cert and plant it in
    LocalMachine\\Root + LocalMachine\\TrustedPublisher. Returns the thumbprint.
    """
    if progress:
        progress(f"Generating self-signed code-signing cert ({_CERT_SUBJECT})…")
    return _run_powershell(_GENERATE_CERT_PS, ("Subject", _CERT_SUBJECT))


def sign_worker(exe_path: Path, thumbprint: str, progress: callable | None = None) -> None:
    """Authenticode-sign *exe_path* with the cert identified by *thumbprint*."""
    if progress:
        progress(f"Signing {exe_path.name} with self-signed cert…")
    _run_powershell(_SIGN_PS, ("Thumbprint", thumbprint), ("ExePath", str(exe_path)))


# ---------------------------------------------------------------------------
# Install to Program Files
# ---------------------------------------------------------------------------

def install_signed_worker(exe_path: Path, progress: callable | None = None) -> Path:
    """Copy the signed exe into ``%ProgramFiles%\\WindowsMCP\\`` and lock the
    directory's ACL to Administrators + SYSTEM. Returns the installed path.
    """
    if progress:
        progress(f"Installing to {_INSTALL_DIR}…")
    _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    dest = _INSTALL_DIR / _WORKER_EXE_NAME
    shutil.copy2(exe_path, dest)
    # Best-effort tighten ACLs. Failure here is non-fatal: the binary works,
    # just with default Program Files ACLs (Authenticated Users: Read &
    # Execute) — which is already fine because Authenticated Users can't
    # write to Program Files.
    for cmd in (
        ["icacls", str(_INSTALL_DIR), "/inheritance:r"],
        ["icacls", str(_INSTALL_DIR), "/grant", "*S-1-5-18:(OI)(CI)F"],     # NT AUTHORITY\SYSTEM
        ["icacls", str(_INSTALL_DIR), "/grant", "*S-1-5-32-544:(OI)(CI)F"], # BUILTIN\Administrators
    ):
        try:
            subprocess.run(cmd, capture_output=True, check=False)
        except Exception:
            pass
    return dest


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def build_sign_and_install(progress: callable | None = None) -> Path:
    """One-shot: ensure PyInstaller, build, generate cert, sign, install. Returns the
    installed path.
    """
    progress = progress or (lambda _msg: None)
    with tempfile.TemporaryDirectory(prefix="windows-mcp-uia-build-") as tmpdir:
        exe = build_worker(Path(tmpdir), progress=progress)
        thumbprint = generate_local_cert(progress=progress)
        sign_worker(exe, thumbprint, progress=progress)
        return install_signed_worker(exe, progress=progress)
