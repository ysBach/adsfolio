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


def test_command_job_runs_follow_up_after_success(qtbot, tmp_path) -> None:
    first = [sys.executable, "-c", "print('first')"]
    second = [sys.executable, "-c", "print('second')"]
    job = CommandJob(first, working_directory=tmp_path, follow_up_argv=[second])
    lines = []
    job.output.connect(lines.append)
    with qtbot.waitSignal(job.finished, timeout=10000) as result:
        job.start()
    assert result.args == [0]
    assert lines == ["first", "second"]
    assert not job.running


def test_command_job_stops_follow_up_after_failure(qtbot, tmp_path) -> None:
    marker = tmp_path / "should-not-run"
    job = CommandJob(
        [sys.executable, "-c", "raise SystemExit(7)"],
        follow_up_argv=[[sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]],
    )
    with qtbot.waitSignal(job.finished, timeout=10000) as result:
        job.start()
    assert result.args == [7]
    assert not marker.exists()


def test_cancel_prevents_follow_up_even_when_process_exits_zero(
    qtbot, tmp_path
) -> None:
    marker = tmp_path / "should-not-run"
    script = "import signal, sys, time; signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); print('ready', flush=True); time.sleep(30)"
    job = CommandJob(
        [sys.executable, "-c", script],
        follow_up_argv=[[sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]],
    )
    with qtbot.waitSignal(job.output, timeout=10000):
        job.start()
    with qtbot.waitSignal(job.finished, timeout=10000) as result:
        job.cancel()
    assert result.args[0] != 0
    assert not marker.exists()


def test_follow_up_start_failure_reports_failure(qtbot, tmp_path) -> None:
    job = CommandJob(
        [sys.executable, "-c", "print('first')"],
        follow_up_argv=[[str(tmp_path / "missing-helper")]],
    )
    with qtbot.waitSignal(job.failed, timeout=10000):
        job.start()
    assert not job.running


def test_crash_with_zero_exit_code_does_not_run_follow_up(qtbot, monkeypatch) -> None:
    from PySide6.QtCore import QProcess

    job = CommandJob([sys.executable], follow_up_argv=[[sys.executable]])
    job._pending_commands = [[sys.executable]]
    started = []
    results = []
    monkeypatch.setattr(job, "_read_output", lambda: None)
    monkeypatch.setattr(job._process, "start", lambda *args: started.append(args))
    job.finished.connect(results.append)
    job._on_finished(0, QProcess.CrashExit)
    assert started == []
    assert results == [-1]
