"""Validate the launch metadata and executables of a built macOS app."""

from __future__ import annotations

import argparse
import logging
import os
import plistlib
from pathlib import Path

logger = logging.getLogger(__name__)


def check_bundle(bundle: Path) -> None:
    """Validate a foreground desktop bundle before distribution.

    Parameters
    ----------
    bundle : `Path`
        Built macOS application directory.

    Raises
    ------
    ValueError
        If the bundle cannot launch its GUI as a foreground application.
    OSError
        If bundle metadata cannot be read.
    """
    with (bundle / "Contents" / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    if info.get("CFBundlePackageType") != "APPL":
        raise ValueError("Bundle must have application package type APPL")
    if info.get("LSBackgroundOnly") or info.get("LSUIElement"):
        raise ValueError("Desktop bundle must launch as a foreground application")
    if info.get("CFBundleDisplayName") != "ADSfolio":
        raise ValueError("Bundle display name must be ADSfolio")
    if info.get("CFBundleExecutable") != "adsfolio":
        raise ValueError("Bundle CFBundleExecutable must be adsfolio")
    for name in ("adsfolio", "adsfolio-cli"):
        executable = bundle / "Contents" / "MacOS" / name
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"Bundle executable is missing or not executable: {name}")
    logger.info("macOS bundle foreground launch metadata and executables passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    logging.basicConfig(level=logging.INFO)
    check_bundle(parser.parse_args().bundle)
