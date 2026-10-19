import os
import subprocess
import sys
from pathlib import Path


def test_cli_module_entrypoint_does_not_emit_runpy_warning():
    source_root = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(source_root)}

    result = subprocess.run(
        [sys.executable, "-m", "adsfolio.cli.commands", "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "RuntimeWarning" not in result.stderr
