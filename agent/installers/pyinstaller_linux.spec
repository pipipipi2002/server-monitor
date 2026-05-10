# PyInstaller spec for the Linux agent.
# Build:
#   pyinstaller --clean --distpath ./agents-dist agent/installers/pyinstaller_linux.spec

block_cipher = None

a = Analysis(
    ["../server_monitor_agent/__main__.py"],
    pathex=["../"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "server_monitor_agent.collect_linux",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["server_monitor_agent.collect_windows", "server_monitor_agent.service_windows"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

import platform
arch = "x86_64" if platform.machine() in ("x86_64", "amd64") else "aarch64"

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name=f"agent-linux-{arch}",
    debug=False, bootloader_ignore_signals=False, strip=True, upx=False,
    runtime_tmpdir=None, console=True, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
