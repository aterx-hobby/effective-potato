#!/bin/bash

set -euo pipefail

ROOTDIR=$(dirname "$BASH_SOURCE")
cd "$ROOTDIR"

# Prepare environment (creates venv and installs package if needed)
./build.sh >/dev/null

# Activate venv
# shellcheck source=/dev/null
source venv/bin/activate

# Launch the MCP server with unbuffered output for immediate log streaming
export PYTHONUNBUFFERED=1
effective-potato "$@"
