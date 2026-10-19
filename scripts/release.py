"""Prepare repositories and publish version tags through the release checks."""

from __future__ import annotations

import argparse
import ast
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def current_version() -> str:
    """Read the package version without importing application dependencies."""
    tree = ast.parse((ROOT / "src/adsfolio/__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise ValueError("Package version is missing")


def run_checks() -> None:
    """Run local release gates and build the Python distributions."""
    commands = [
        ["uv", "sync", "--locked", "--all-extras", "--group", "docs"],
        [
            "uv",
            "run",
            "--no-sync",
            "ruff",
            "check",
            "--select",
            "E4,E7,E9,F",
            "src",
            "tests",
            "packaging",
            "scripts",
        ],
        [
            "uv",
            "run",
            "--no-sync",
            "ruff",
            "format",
            "--check",
            "src",
            "tests",
            "packaging",
            "scripts",
        ],
        [
            "uv",
            "run",
            "--no-sync",
            "isort",
            "--check-only",
            "src",
            "tests",
            "packaging",
            "scripts",
        ],
        ["uv", "run", "--no-sync", "pytest", "-q"],
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
            "docs",
            "docs/_build/html",
        ],
        ["uv", "build"],
        ["git", "diff", "--check"],
    ]
    for command in commands:
        subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )


def repository_commands(repository: str, visibility: str) -> list[list[str]]:
    """Plan repository creation without discarding existing Git history."""
    commands = [["gh", "auth", "status"]]
    if not (ROOT / ".git").exists():
        commands.append(["git", "init", "-b", "main"])
        remotes = []
    else:
        remotes = subprocess.check_output(
            ["git", "remote"], cwd=ROOT, text=True
        ).split()
    if "origin" in remotes:
        origin = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True
        ).strip()
        accepted = {
            f"https://github.com/{repository}.git",
            f"https://github.com/{repository}",
            f"git@github.com:{repository}.git",
        }
        if origin in accepted:
            commands.append(
                [
                    "gh",
                    "repo",
                    "view",
                    repository,
                    "--json",
                    "nameWithOwner,url,isEmpty",
                ]
            )
            return commands
    if "previous-origin" in remotes and "origin" in remotes:
        raise ValueError(
            "Both origin and previous-origin exist; inspect them before changing remotes"
        )
    commands.append(
        [
            "gh",
            "repo",
            "create",
            repository,
            f"--{visibility}",
            "--description",
            "ADSfolio research PDF library",
        ]
    )
    if "origin" in remotes:
        commands.append(["git", "remote", "rename", "origin", "previous-origin"])
    commands.append(
        ["git", "remote", "add", "origin", f"https://github.com/{repository}.git"]
    )
    return commands


def main() -> None:
    """Preview remote changes unless the caller supplies --apply."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Run local release checks")
    version_parser = sub.add_parser(
        "version", help="Set the next package version and update the lockfile"
    )
    version_parser.add_argument("value")
    version_parser.add_argument("--apply", action="store_true")
    repository = sub.add_parser(
        "repository", help="Create a remote and configure origin, preserving history"
    )
    repository.add_argument("--repository", default="ysBach/adsfolio")
    repository.add_argument(
        "--visibility", choices=["public", "private"], default="public"
    )
    repository.add_argument("--apply", action="store_true")
    tag = sub.add_parser(
        "tag", help="Check and push a version tag that starts native release builds"
    )
    tag.add_argument("--repository", default="ysBach/adsfolio")
    tag.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "check":
        run_checks()
        return
    if args.command == "version":
        if not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+|\.dev[0-9]+)?", args.value
        ):
            parser.error("Use major.minor.patch with an optional a/b/rc/dev suffix")
        logger.info(
            "Version %s -> %s; update src/adsfolio/__init__.py and uv.lock",
            current_version(),
            args.value,
        )
        if args.apply:
            source = ROOT / "src/adsfolio/__init__.py"
            source.write_text(
                re.sub(
                    r'__version__ = "[^"]+"',
                    f'__version__ = "{args.value}"',
                    source.read_text(),
                )
            )
            subprocess.run(["uv", "lock"], cwd=ROOT, check=True)
        return
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("Repository must be OWNER/NAME")
    if args.command == "repository":
        commands = repository_commands(args.repository, args.visibility)
    else:
        version = current_version()
        if not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+|\.dev[0-9]+)?", version
        ):
            parser.error(
                "Version must be major.minor.patch with an optional a/b/rc/dev suffix"
            )
        if subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip():
            parser.error("Commit the reviewed changes before creating a release tag")
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip()
        if branch != "main":
            parser.error("Release tags must come from main")
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True
        ).strip()
        accepted = {
            f"https://github.com/{args.repository}.git",
            f"https://github.com/{args.repository}",
            f"git@github.com:{args.repository}.git",
        }
        if remote not in accepted:
            parser.error("origin does not match the requested repository")
        commands = [
            ["git", "tag", "-a", f"v{version}", "-m", f"ADSfolio {version}"],
            [
                "git",
                "push",
                "--atomic",
                "origin",
                "HEAD:refs/heads/main",
                f"refs/tags/v{version}",
            ],
        ]
    for command in commands:
        logger.info("%s", subprocess.list2cmdline(command))
    if args.apply:
        if args.command == "tag":
            run_checks()
            if subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip():
                parser.error(
                    "Release checks changed files; review and commit them first"
                )
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True)
        if args.command == "repository":
            logger.info("Repository configured: https://github.com/%s", args.repository)
            logger.info(
                "No commits were pushed. Review and commit the intended files, then run: git push -u origin main"
            )
    else:
        logger.info("Preview only. Supply --apply to execute these commands.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        logger.error("%s", exc)
        sys.exit(1)
