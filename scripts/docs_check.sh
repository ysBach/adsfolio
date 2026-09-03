#!/usr/bin/env bash
set -euo pipefail

uv run sphinx-build -n -W --keep-going -b html docs docs/_build/html
