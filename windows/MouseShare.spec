# PyInstaller spec - build with:  python -m PyInstaller --noconfirm --clean MouseShare.spec
block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[("app.ico", ".")],          # the tray icon is loaded from the bundle
    hiddenimports=["discovery", "win_tray", "keys"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "pandas", "PIL", "test", "unittest"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="MouseShare",
    icon="app.ico",
    console=False,                      # no black window; it is a tray app
    upx=False,
    debug=False,
    strip=False,
    bootloader_ignore_signals=False,
    disable_windowed_traceback=False,
)
