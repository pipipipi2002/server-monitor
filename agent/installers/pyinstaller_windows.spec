# PyInstaller spec for the Windows agent.
# Build (on a Windows host, ideally Server 2019 to be safe for older deploys):
#   pyinstaller --clean --distpath .\agents-dist agent\installers\pyinstaller_windows.spec

block_cipher = None

a = Analysis(
    ["..\\server_monitor_agent\\__main__.py"],
    pathex=["..\\"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "server_monitor_agent.collect_windows",
        "server_monitor_agent.service_windows",
        # Pywin32 service host. The first four are imported by service_windows;
        # the rest are typical pywin32 deps that PyInstaller's static analysis
        # sometimes misses. pythoncom + pywintypes are particularly important
        # because servicemanager.StartServiceCtrlDispatcher uses them at runtime
        # via dynamic loading.
        "win32ts",
        "win32serviceutil",
        "win32service",
        "win32event",
        "servicemanager",
        "win32api",
        "pythoncom",
        "pywintypes",
        "win32timezone",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["server_monitor_agent.collect_linux"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="agent-windows",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console=True, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
