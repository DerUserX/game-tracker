# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller GameTracker.spec --distpath release   (oder einfach build.bat)
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['gametracker.pyw'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.')] + collect_data_files('customtkinter'),
    hiddenimports=['pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GameTracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
