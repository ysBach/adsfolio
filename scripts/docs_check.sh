#!/usr/bin/env bash
set -euo pipefail

uv run --extra gui --group docs sphinx-build -n -W --keep-going -b html docs docs/_build/html
