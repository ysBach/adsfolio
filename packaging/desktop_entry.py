"""Frozen entry point shared by the desktop and console executables."""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()
    stem = Path(sys.executable).stem
    if stem == "adsfolio-cli":
        # PyInstaller's embedded interpreter ignores PYTHONUNBUFFERED.
        # Flush each line so the GUI can stream progress from its console helper.
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.reconfigure(encoding="utf-8", line_buffering=True)
        from adsfolio.cli.commands import main

        prog = "adsfolio" if stem == "adsfolio-cli" else "adsfolio"
        main(prog_name=prog)
    else:
        from adsfolio.gui.app import run

        raise SystemExit(run())
