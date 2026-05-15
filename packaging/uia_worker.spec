# PyInstaller spec for the UIAccess-enabled worker binary.
#
# Build:
#     uv run pyinstaller packaging/uia_worker.spec --clean
#
# Output:
#     dist/windows-mcp-uia-worker.exe   (single-file, manifested)
#
# Sign (in CI or release pipeline; do NOT check the cert in):
#     signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 \
#         dist/windows-mcp-uia-worker.exe
#
# Install (run elevated on the target machine):
#     uv run windows-mcp service secure-desktop install \
#         --policy block \
#         --uia-worker dist\windows-mcp-uia-worker.exe
#
# That last command copies the signed worker into
#   %ProgramFiles%\WindowsMCP\windows-mcp-uia-worker.exe
# (a trusted path) and records the absolute path in HKLM so the host service
# knows to spawn it instead of `python -m windows_mcp.service.user_session_worker`.

# ruff: noqa  -- PyInstaller specs run as Python with the spec API in scope.

import os

block_cipher = None

a = Analysis(
    ['../src/windows_mcp/service/user_session_worker.py'],
    pathex=[os.path.abspath('../src')],
    binaries=[],
    datas=[],
    hiddenimports=[
        'windows_mcp',
        'windows_mcp.service',
        'windows_mcp.service.secure_desktop',
        'comtypes',
        'comtypes.client',
        'comtypes.gen',
        'win32api',
        'win32con',
        'win32process',
        'win32security',
        'win32ts',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Keep the worker as small as possible — it just walks UIA and prints
        # JSON. No need for the rest of the windows_mcp tool surface.
        'fastmcp',
        'mcp',
        'starlette',
        'uvicorn',
        'sse_starlette',
        'pydantic',
        'posthog',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='windows-mcp-uia-worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # console exe — service captures stdout/stderr.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest='uia_worker.manifest',  # uiAccess=true, requireAdministrator
)
