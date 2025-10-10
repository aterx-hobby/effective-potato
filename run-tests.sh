#!/bin/bash
# Test harness for effective-potato
# - Uses build.sh to prepare the virtualenv and editable install
# - Runs pytest with pass-through args
# - Optional: RUN_INTEGRATION_TESTS=1 to include integration tests

set -euo pipefail

ROOTDIR=$(dirname "$BASH_SOURCE")
cd "$ROOTDIR"

# Prepare environment and install deps via build.sh
./build.sh >/dev/null

# Activate venv
# shellcheck source=/dev/null
source venv/bin/activate

echo "Running static analysis (ruff, mypy)..."

# Ruff: linting and import sorting checks
ruff check .

# Mypy: type checking (config via pyproject.toml)
# Default: enforce; set POTATO_ENFORCE_MYPY=0 to make non-blocking
export POTATO_ENFORCE_MYPY=${POTATO_ENFORCE_MYPY:-1}
if [ "$POTATO_ENFORCE_MYPY" = "1" ]; then
	mypy src/effective_potato
else
	echo "Running mypy (non-blocking)..."
	mypy src/effective_potato || echo "mypy reported issues (non-blocking). Set POTATO_ENFORCE_MYPY=1 to enforce."
fi

echo "Running pytest (including integration tests)..."
# Force-enable integration tests by default
export POTATO_IT_ENABLE=${POTATO_IT_ENABLE:-1}
export RUN_INTEGRATION_TESTS=${RUN_INTEGRATION_TESTS:-1}

# Run full suite
python -m pytest -q "$@"
