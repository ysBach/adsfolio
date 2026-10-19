"""Install a macOS launcher that runs this checkout in its local environment."""

from __future__ import annotations

import argparse
import logging
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def install_source_app(checkout: Path, destination: Path) -> Path | None:
    """Install a source launcher and retain an existing app in a dated backup.

    The launcher uses the checkout's `.venv` and the existing ADSfolio user
    data. Source changes take effect after the application exits and restarts.
    Dependencies must be synchronized separately with ``uv sync``.
    """
    if sys.platform != "darwin":
        raise RuntimeError("This launcher requires macOS")
    checkout = checkout.resolve()
    interpreter = checkout / ".venv/bin/python"
    source = checkout / "src"
    if not interpreter.is_file() or not (source / "adsfolio/gui/app.py").is_file():
        raise ValueError("The checkout needs its local .venv and ADSfolio GUI source")
    subprocess.run(
        [str(interpreter), "-c", "import PySide6, adsfolio"],
        cwd=checkout,
        check=True,
    )
    if destination.name != "ADSfolio.app":
        raise ValueError("Destination must be named ADSfolio.app")
    if destination.is_symlink():
        raise ValueError("The destination is a symlink; select its actual app path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(
        prefix="adsfolio-launcher-", dir=destination.parent
    ) as directory:
        app = Path(directory) / "ADSfolio.app"
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir()
        shutil.copy2(
            source / "adsfolio/gui/assets/adsfolio.icns",
            contents / "Resources/adsfolio.icns",
        )
        with (contents / "Info.plist").open("wb") as handle:
            plistlib.dump(
                {
                    "CFBundleExecutable": "adsfolio-source",
                    "CFBundleIdentifier": "io.github.ysbach.adsfolio.source",
                    "CFBundleName": "ADSfolio",
                    "CFBundleDisplayName": "ADSfolio",
                    "CFBundlePackageType": "APPL",
                    "CFBundleIconFile": "adsfolio.icns",
                    "NSHighResolutionCapable": True,
                    "ADSfolioSourceCheckout": str(checkout),
                },
                handle,
            )
        launcher = contents / "MacOS/adsfolio-source"
        launcher.write_text(
            "#!/bin/zsh\n"
            'mkdir -p "$HOME/Library/Logs/ADSfolio"\n'
            'exec >>"$HOME/Library/Logs/ADSfolio/source-launch.log" 2>&1\n'
            f"export PYTHONPATH={shlex.quote(str(source))}\n"
            "export PYTHONUNBUFFERED=1\n"
            "export ADSFOLIO_SOURCE_LAUNCH=1\n"
            f"cd {shlex.quote(str(checkout))} || exit 1\n"
            f"{shlex.quote(str(interpreter))} -m adsfolio.gui.app\n"
            "result=$?\n"
            'if [ "$result" -ne 0 ]; then\n'
            '  /usr/bin/osascript -e \'display alert "ADSfolio could not start" message "Check ~/Library/Logs/ADSfolio/source-launch.log. Restore the checkout and run uv sync --all-extras if dependencies changed." as critical\'\n'
            "fi\n"
            'exit "$result"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
        subprocess.run(["/bin/zsh", "-n", str(launcher)], check=True)
        if destination.exists():
            backup_root = Path.home() / "Library/Application Support/ADSfolio Backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_directory = Path(
                tempfile.mkdtemp(
                    prefix=datetime.now(timezone.utc).strftime(
                        "before-source-launcher-%Y%m%d-%H%M%S-"
                    ),
                    dir=backup_root,
                )
            )
            backup = backup_directory / "ADSfolio.app"
            shutil.move(str(destination), str(backup))
        try:
            shutil.move(str(app), str(destination))
        except Exception:
            if backup is not None and not destination.exists():
                shutil.move(str(backup), str(destination))
            raise
    logger.info("Installed source launcher: %s", destination)
    if backup is not None:
        logger.info("Previous app: %s", backup)
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--destination", type=Path, default=Path("/Applications/ADSfolio.app")
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    install_source_app(args.checkout, args.destination)
