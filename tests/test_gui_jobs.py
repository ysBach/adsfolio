"""CLI invocation and real process output for desktop jobs."""

from __future__ import annotations

import sys

import pytest

from adsfolio.gui.jobs import CommandJob, cli_argv


def test_source_cli_uses_current_interpreter(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert cli_argv("pdf", "status", "Papers with spaces") == [
        sys.executable,
        "-m",
        "adsfolio.cli",
        "pdf",
        "status",
        "Papers with spaces",
    ]


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_frozen_cli_uses_sibling_console_helper(
    platform, monkeypatch, tmp_path
) -> None:
    executable = tmp_path / ("adsfolio.exe" if platform == "win32" else "adsfolio")
    helper = executable.with_name(
        "adsfolio-cli.exe" if platform == "win32" else "adsfolio-cli"
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert cli_argv("pdf", "status", "Papers 한글") == [
        str(helper),
        "pdf",
        "status",
        "Papers 한글",
    ]


def test_command_job_streams_cli_output(qtbot, tmp_path) -> None:
    job = CommandJob(cli_argv("--version"), working_directory=tmp_path)
    lines: list[str] = []
    job.output.connect(lines.append)
    with qtbot.waitSignal(job.finished, timeout=10000) as result:
        job.start()
    assert result.args == [0]
    assert any("ADSfolio, version" in line for line in lines)


def test_missing_helper_reports_start_failure(qtbot, tmp_path) -> None:
    job = CommandJob([str(tmp_path / "missing-helper")])
    with qtbot.waitSignal(job.failed, timeout=5000) as result:
        job.start()
    assert result.args[0]
    assert not job.running
