"""Validate DMG installation layout and packaging failure handling."""

import hashlib
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "build_macos_dmg", ROOT / "packaging/build_macos_dmg.py"
)
dmg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dmg)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    app = tmp_path / "ADSfolio.app"
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True)
    binary = resources / "binary"
    binary.write_bytes(b"test executable")
    binary.chmod(0o755)
    (resources / "link").symlink_to("binary")
    return app


@pytest.mark.parametrize("invalid_layout", ["", "app", "shortcut", "target"])
def test_dmg_stages_links_and_detaches_after_validation(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_layout: str
) -> None:
    commands = []
    staging = None

    def run(command: list[str], *, check: bool) -> None:
        nonlocal staging
        assert check
        commands.append(command)
        if command[1] == "create":
            staging = Path(command[command.index("-srcfolder") + 1])
            assert {p.name for p in staging.iterdir()} == {
                "ADSfolio.app",
                "Applications",
            }
            assert os.readlink(staging / "Applications") == "/Applications"
            resources = staging / "ADSfolio.app/Contents/Resources"
            assert os.readlink(resources / "link") == "binary"
            assert os.access(resources / "binary", os.X_OK)
        elif command[1] == "attach" and invalid_layout != "app":
            shutil.copytree(
                staging,
                command[command.index("-mountpoint") + 1],
                symlinks=True,
                dirs_exist_ok=True,
            )

            mount = Path(command[command.index("-mountpoint") + 1])
            if invalid_layout in {"shortcut", "target"}:
                (mount / "Applications").unlink()
            if invalid_layout == "target":
                (mount / "Applications").symlink_to("/tmp")

    monkeypatch.setattr(dmg.subprocess, "run", run)
    if invalid_layout:
        with pytest.raises(
            ValueError, match="missing ADSfolio.app|Applications shortcut"
        ):
            dmg.build_dmg(bundle, tmp_path / "installer.dmg")
    else:
        dmg.build_dmg(bundle, tmp_path / "installer.dmg")
    assert [command[1] for command in commands] == [
        "create",
        "verify",
        "attach",
        "detach",
    ]
    assert not staging.exists()
    assert (bundle / "Contents/Resources/binary").read_bytes() == b"test executable"


def test_dmg_preserves_existing_image(bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "installer.dmg"
    output.write_bytes(b"existing image")
    with pytest.raises(FileExistsError):
        dmg.build_dmg(bundle, output)
    assert output.read_bytes() == b"existing image"


def test_dmg_rejects_output_inside_bundle(bundle: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        dmg.build_dmg(bundle, bundle / "installer.dmg")


@pytest.mark.parametrize("operation", ["create", "verify", "attach"])
def test_dmg_propagates_tool_failures(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    def run(command: list[str], *, check: bool) -> None:
        if command[1] == operation:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(dmg.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        dmg.build_dmg(bundle, tmp_path / "installer.dmg")


def test_dmg_rejects_output_through_bundle_symlink(
    bundle: Path, tmp_path: Path
) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(bundle, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        dmg.build_dmg(bundle, alias / "installer.dmg")


@pytest.mark.parametrize(
    "platform,suffixes",
    [
        ("macos-arm64", (".dmg", ".zip")),
        ("macos-x86_64", (".dmg", ".zip")),
        ("windows-x86_64", (".zip",)),
        ("linux-x86_64", (".tar.gz",)),
    ],
)
def test_workflow_checksums_cover_archives_on_rerun(
    tmp_path: Path, platform: str, suffixes: tuple[str, ...]
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["build-desktop"]["steps"]
        if step.get("name") == "Generate SHA-256 checksums"
    )
    command = shlex.split(step["run"].replace("${{ matrix.platform }}", platform))
    script = command[command.index("-c") + 1]
    expected = []
    for suffix in suffixes:
        filename = f"adsfolio-{platform}{suffix}"
        content = filename.encode()
        (tmp_path / filename).write_bytes(content)
        expected.append(f"{hashlib.sha256(content).hexdigest()}  {filename}")
    checksum_file = tmp_path / f"adsfolio-{platform}.sha256"
    checksum_file.write_text("stale checksum manifest")
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert checksum_file.read_text() == "\n".join(expected) + "\n"
