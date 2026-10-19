"""Build the ADSfolio landing page and documentation for static hosting."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    """Copy site assets and build strict Sphinx documentation under /docs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("_site"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output == root or output in (root / "src", root / "docs", root / "website"):
        parser.error("Output must be a separate build directory")
    output.mkdir(parents=True, exist_ok=True)
    for source in (root / "website").iterdir():
        if source.is_file():
            shutil.copy2(source, output / source.name)
    shutil.copy2(root / "src/adsfolio/gui/assets/adsfolio.svg", output / "adsfolio.svg")
    (output / ".nojekyll").touch()
    subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "sphinx-build",
            "-n",
            "-W",
            "--keep-going",
            "-b",
            "html",
            str(root / "docs"),
            str(output / "docs"),
        ],
        cwd=root,
        check=True,
    )


if __name__ == "__main__":
    main()
