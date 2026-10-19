"""Build and verify a macOS drag-to-install disk image without changing the app."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def build_dmg(bundle: Path, output: Path) -> None:
    """Package ADSfolio.app and an Applications shortcut in a compressed DMG.

    Parameters
    ----------
    bundle : Path
        Built application. Internal symbolic links and permissions are preserved.
    output : Path
        New disk image path. An existing file is never overwritten.

    Notes
    -----
    Requires macOS hdiutil. The image is verified and mounted read-only to check
    its installation layout before returning. Temporary staging and mount
    directories are removed, and the source application is not modified.
    """
    bundle = bundle.resolve()
    output = output.parent.resolve() / output.name
    if bundle.name != "ADSfolio.app" or not bundle.is_dir():
        raise ValueError("Bundle must be an existing ADSfolio.app directory")
    if output.suffix != ".dmg":
        raise ValueError("Output must have a .dmg suffix")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if output.is_relative_to(bundle):
        raise ValueError("Disk image must be outside the application bundle")
    with tempfile.TemporaryDirectory(prefix="adsfolio-dmg-") as directory:
        root = Path(directory)
        staging = root / "staging"
        staging.mkdir()
        shutil.copytree(bundle, staging / "ADSfolio.app", symlinks=True)
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "ADSfolio",
                "-srcfolder",
                str(staging),
                "-fs",
                "HFS+",
                "-format",
                "UDZO",
                str(output),
            ],
            check=True,
        )
        subprocess.run(["hdiutil", "verify", str(output)], check=True)
        mount = root / "mounted"
        mount.mkdir()
        subprocess.run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                str(mount),
                str(output),
            ],
            check=True,
        )
        try:
            if not (mount / "ADSfolio.app").is_dir():
                raise ValueError("Disk image is missing ADSfolio.app")
            shortcut = mount / "Applications"
            if not shortcut.is_symlink() or os.readlink(shortcut) != "/Applications":
                raise ValueError("Disk image must have an Applications shortcut")
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True)
    logger.info("Verified disk image: %s", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    logging.basicConfig(level=logging.INFO)
    args = parser.parse_args()
    build_dmg(args.bundle, args.output)
