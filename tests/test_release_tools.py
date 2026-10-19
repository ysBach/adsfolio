"""Release metadata and Homebrew checksum validation."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "homebrew_cask", ROOT / "scripts/homebrew_cask.py"
)
cask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cask)


def test_cask_uses_platform_specific_checksums() -> None:
    rendered = cask.render_cask(
        "1.2.3",
        "owner/adsfolio",
        "a" * 64
        + "  adsfolio-macos-arm64.zip\n"
        + "b" * 64
        + "  adsfolio-macos-x86_64.zip\n",
    )
    assert 'sha256 arm: "' + "a" * 64 + '"' in rendered
    assert 'intel: "' + "b" * 64 + '"' in rendered
    assert "/owner/adsfolio/releases/download/v#{version}/" in rendered
    assert 'version "1.2.3"' in rendered


@pytest.mark.parametrize(
    "version,repository",
    [("1.2.3rc1", "owner/app"), ("1.2.3", 'bad"/repo'), ("x", "owner/app")],
)
def test_cask_rejects_invalid_release_identity(version: str, repository: str) -> None:
    with pytest.raises(ValueError):
        cask.render_cask(version, repository, "")


def test_cask_requires_both_native_builds() -> None:
    with pytest.raises(KeyError):
        cask.render_cask(
            "1.2.3", "owner/app", "a" * 64 + "  adsfolio-macos-arm64.zip\n"
        )


@pytest.fixture
def repository_checkout(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts/release.py", scripts / "release.py")
    subprocess.run(
        ["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True
    )
    return tmp_path


def test_repository_preview_does_not_create_remote(repository_checkout: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(repository_checkout / "scripts/release.py"), "repository"],
        cwd=repository_checkout,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Preview only" in result.stderr
    assert "gh repo create ysBach/adsfolio" in result.stderr
    assert (
        subprocess.check_output(["git", "remote"], cwd=repository_checkout, text=True)
        == ""
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/ysBach/adsfolio.git",
        "https://github.com/ysBach/adsfolio",
        "git@github.com:ysBach/adsfolio.git",
    ],
)
def test_repository_preview_accepts_completed_setup(
    repository_checkout: Path, origin: str
) -> None:
    for name, url in (
        ("origin", origin),
        ("previous-origin", "https://github.com/owner/earlier.git"),
    ):
        subprocess.run(
            ["git", "remote", "add", name, url], cwd=repository_checkout, check=True
        )
    before = subprocess.check_output(
        ["git", "remote", "-v"], cwd=repository_checkout, text=True
    )
    result = subprocess.run(
        [sys.executable, str(repository_checkout / "scripts/release.py"), "repository"],
        cwd=repository_checkout,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "gh repo view ysBach/adsfolio" in result.stderr
    assert "Preview only" in result.stderr
    assert (
        subprocess.check_output(
            ["git", "remote", "-v"], cwd=repository_checkout, text=True
        )
        == before
    )


def test_repository_preview_rejects_conflicting_remotes(
    repository_checkout: Path,
) -> None:
    for name in ("origin", "previous-origin"):
        subprocess.run(
            ["git", "remote", "add", name, f"https://github.com/owner/{name}.git"],
            cwd=repository_checkout,
            check=True,
        )
    result = subprocess.run(
        [sys.executable, str(repository_checkout / "scripts/release.py"), "repository"],
        cwd=repository_checkout,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "inspect them before changing remotes" in result.stderr
