# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for iOS27FakeRun standalone Windows GUI."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = [
    ("static", "static"),
]
for pkg in ("webview", "clr_loader", "pythonnet", "pymobiledevice3", "pytun_pmd3", "certifi"):
    datas += collect_data_files(pkg)

for dist_pkg in (
    "pyimg4",
    "ipsw_parser",
    "pymobiledevice3",
    "developer_disk_image",
    "pmd-pytcp",
    "pydantic",
    "websockets",
    "click",
    "urllib3",
    "opentelemetry-api",
    "pygments",
    "fastapi",
    "uvicorn",
    "starlette",
    "httpx",
    "pywebview",
    "cryptography",
):
    try:
        datas += copy_metadata(dist_pkg)
    except Exception:
        pass

hiddenimports = []
for pkg in (
    "uvicorn",
    "webview.platforms",
    "pmd_pytcp",
    "pmd_net_addr",
    "pmd_net_proto",
    "qh3",
    "sslpsk_pmd3",
    "pytun_pmd3",
):
    hiddenimports += collect_submodules(pkg)

hiddenimports += [
    "pymobiledevice3.services.dvt.instruments.dvt_provider",
    "pymobiledevice3.services.dvt.instruments.location_simulation",
    "pymobiledevice3.services.simulate_location",
    "pymobiledevice3.services.mobile_image_mounter",
    "pymobiledevice3.services.cryptexd",
    "pymobiledevice3.services.amfi",
    "pymobiledevice3.remote.userspace_tunnel",
    "pymobiledevice3.remote.tunnel_service",
    "pymobiledevice3.tunneld.api",
    "backend.api",
    "backend.coords",
    "backend.device_manager",
    "backend.env_check",
    "backend.geocode",
    "backend.paths",
    "backend.route_utils",
    "backend.simulation_engine",
    "backend.storage",
]

excludes = [
    "IPython",
    "av",
    "PIL",
    "matplotlib",
    "jedi",
    "pytest",
    "tkinter",
    "numpy",
    "scipy",
]

a = Analysis(
    ["desktop_app.pyw"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="iOS27FakeRun",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="iOS27FakeRun",
)
