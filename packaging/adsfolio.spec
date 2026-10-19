# -*- mode: python ; coding: utf-8 -*-
"""Native desktop bundle with a console helper sharing the same dependencies."""

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

from packaging.version import Version

root = Path(SPECPATH).parent
parser = argparse.ArgumentParser()
parser.add_argument("--with-smoke-test", action="store_true")
options = parser.parse_args()
entry = root / "packaging" / "desktop_entry.py"
smoke_entry = root / "packaging" / "smoke_entry.py"
wordnet = root / "build" / "nltk_data" / "corpora" / "wordnet.zip"
if not wordnet.is_file():
    raise SystemExit(
        "Prepare ISO4 data first: uv run --no-sync python -m nltk.downloader "
        "-d build/nltk_data wordnet"
    )
metadata = []
for distribution in ("adsfolio", "PySide6", "PyMuPDF", "watchdog", "iso4"):
    metadata += copy_metadata(distribution, recursive=True)

assets_dir = root / "src" / "adsfolio" / "gui" / "assets"
extra_datas = []
if assets_dir.is_dir():
    extra_datas.append((str(assets_dir), "adsfolio/gui/assets"))

macos_icon = assets_dir / "adsfolio.icns"
windows_icon = assets_dir / "adsfolio.ico"
if not macos_icon.is_file():
    raise SystemExit(f"Missing required macOS icon: {macos_icon}")
if not windows_icon.is_file():
    raise SystemExit(f"Missing required Windows icon: {windows_icon}")
macos_icon_str = str(macos_icon)
app_icon = str(macos_icon if sys.platform == "darwin" else windows_icon)

analysis = Analysis(
    [str(entry)] + ([str(smoke_entry)] if options.with_smoke_test else []),
    pathex=[str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "LICENSE"), "."),
        (str(wordnet), "nltk_data/corpora"),
    ]
    + collect_data_files("iso4")
    + extra_datas
    + metadata,
    hiddenimports=[],
    hookspath=[str(root / "packaging" / "hooks")],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
application_scripts = [item for item in analysis.scripts if item[1] != str(smoke_entry)]
gui = EXE(
    pyz,
    application_scripts,
    [],
    exclude_binaries=True,
    name="adsfolio",
    icon=app_icon,
    console=False,
    strip=False,
    upx=False,
)
cli = EXE(
    pyz,
    application_scripts,
    [],
    exclude_binaries=True,
    name="adsfolio-cli",
    console=True,
    strip=False,
    upx=False,
)
collection = COLLECT(
    gui,
    cli,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="adsfolio",
)
if sys.platform == "darwin":
    dist_ver = Version(version("adsfolio")).base_version
    app = BUNDLE(
        collection,
        name="ADSfolio.app",
        bundle_identifier="io.github.ysbach.adsfolio",
        icon=macos_icon_str,
        info_plist={
            "CFBundleDisplayName": "ADSfolio",
            "CFBundleName": "ADSfolio",
            "CFBundleExecutable": "adsfolio",
            "CFBundleShortVersionString": dist_ver,
            "CFBundleVersion": dist_ver,
            # COLLECT inherits console=True from the CLI helper. The app itself
            # must remain a foreground GUI when launched through Finder.
            "LSBackgroundOnly": False,
            "NSHighResolutionCapable": True,
        },
    )

if options.with_smoke_test:
    # This separate validation executable is never put in the application archive.
    smoke = EXE(
        pyz,
        [item for item in analysis.scripts if item[1] != str(entry)],
        [],
        exclude_binaries=True,
        name="adsfolio-smoke",
        console=True,
        strip=False,
        upx=False,
    )
    smoke_collection = COLLECT(
        smoke,
        cli,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name="adsfolio-smoke",
    )
