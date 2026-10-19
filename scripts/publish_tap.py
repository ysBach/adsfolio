"""Publish a stable release cask to the project's Homebrew tap."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from homebrew_cask import render_cask


def main() -> None:
    """Download release checksums and commit the generated cask when requested."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--repository", default="ysBach/adsfolio")
    parser.add_argument("--tap", default="ysBach/homebrew-adsfolio")
    parser.add_argument(
        "--create", action="store_true", help="Create a new public tap repository first"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for value in (args.repository, args.tap):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            parser.error("Repository and tap must be OWNER/NAME")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        parser.error("The tap accepts stable major.minor.patch releases")
    logging.info(
        "Publish ADSfolio %s from %s to %s/Casks/adsfolio.rb",
        args.version,
        args.repository,
        args.tap,
    )
    if not args.apply:
        logging.info(
            "Preview only. Supply --apply to download, commit, and push. Use --create for a new tap."
        )
        return
    subprocess.run(["gh", "auth", "status"], check=True)
    release = json.loads(
        subprocess.check_output(
            [
                "gh",
                "release",
                "view",
                f"v{args.version}",
                "--repo",
                args.repository,
                "--json",
                "isDraft,isPrerelease,tagName",
            ],
            text=True,
        )
    )
    if (
        release["isDraft"]
        or release["isPrerelease"]
        or release["tagName"] != f"v{args.version}"
    ):
        parser.error("The selected release must be published and stable")
    with tempfile.TemporaryDirectory(prefix="adsfolio-tap-") as temporary:
        root = Path(temporary)
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                f"v{args.version}",
                "--repo",
                args.repository,
                "--pattern",
                "SHA256SUMS.txt",
                "--dir",
                str(root),
            ],
            check=True,
        )
        cask = render_cask(
            args.version, args.repository, (root / "SHA256SUMS.txt").read_text()
        )
        if args.create:
            subprocess.run(
                [
                    "gh",
                    "repo",
                    "create",
                    args.tap,
                    "--public",
                    "--description",
                    "ADSfolio Homebrew cask",
                ],
                check=True,
            )
        checkout = root / "tap"
        subprocess.run(["gh", "repo", "clone", args.tap, str(checkout)], check=True)
        output = checkout / "Casks/adsfolio.rb"
        output.parent.mkdir(exist_ok=True)
        output.write_text(cask, encoding="utf-8", newline="\n")
        subprocess.run(["ruby", "-c", str(output)], check=True)
        subprocess.run(["git", "add", "Casks/adsfolio.rb"], cwd=checkout, check=True)
        if (
            subprocess.run(
                ["git", "diff", "--cached", "--quiet"], cwd=checkout
            ).returncode
            == 0
        ):
            logging.info("The tap already contains this cask")
            return
        subprocess.run(
            ["git", "commit", "-m", f"ADSfolio {args.version}"],
            cwd=checkout,
            check=True,
        )
        subprocess.run(["git", "push", "origin", "HEAD"], cwd=checkout, check=True)


if __name__ == "__main__":
    main()
