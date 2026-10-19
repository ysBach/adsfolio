"""Generate a Homebrew cask from verified release checksums."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def render_cask(version: str, repository: str, checksums: str) -> str:
    """Render a macOS cask with separate ARM and Intel archive checksums."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Homebrew requires a stable major.minor.patch version")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repository must be OWNER/NAME")
    sums = {}
    for line in checksums.splitlines():
        if not line.strip():
            continue
        digest, filename = line.split(maxsplit=1)
        if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise ValueError(f"Invalid checksum for {filename}")
        filename = filename.lstrip("*")
        if filename in sums:
            raise ValueError(f"Duplicate checksum for {filename}")
        sums[filename] = digest.lower()
    arm = sums["adsfolio-macos-arm64.zip"]
    intel = sums["adsfolio-macos-x86_64.zip"]
    return """cask "adsfolio" do
  arch arm: "arm64", intel: "x86_64"

  version "%s"
  sha256 arm: "%s",
         intel: "%s"

  url "https://github.com/%s/releases/download/v#{version}/adsfolio-macos-#{arch}.zip"
  name "ADSfolio"
  desc "Research PDF library with ADS, DOI metadata, and local references"
  homepage "https://github.com/%s"

  depends_on macos: ">= :sequoia"

  app "ADSfolio.app"
  binary "#{appdir}/ADSfolio.app/Contents/MacOS/adsfolio-cli", target: "adsfolio"

  uninstall quit: "io.github.ysbach.adsfolio"
end
""" % (version, arm, intel, repository, repository)


def main() -> None:
    """Write a cask using the release's SHA256SUMS file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--repository", default="ysBach/adsfolio")
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rendered = render_cask(args.version, args.repository, args.checksums.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
