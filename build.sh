#!/bin/bash

set -euo pipefail

ROOTDIR=$(dirname "$BASH_SOURCE")
cd "$ROOTDIR"

# Ensure venv exists
if [ ! -d "venv" ]; then
	echo "Creating Python virtual environment (venv)"
	python3 -m venv venv
fi

# Activate venv
# shellcheck source=/dev/null
source venv/bin/activate

# Ensure pip tooling is up-to-date (quiet)
python -m pip install --upgrade --quiet pip setuptools wheel

# Editable install with dev extras (PEP 517 via pyproject.toml)
pip install -e '.[dev]'

echo "effective-potato installed (editable) with dev extras in venv."
