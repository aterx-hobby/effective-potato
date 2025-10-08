#!/bin/bash

set -euo pipefail

ROOTDIR=$(dirname "$BASH_SOURCE")
cd "$ROOTDIR"

MODE=${POTATO_TOOLKIT:-}

if [ "${MODE}" = "review" ] || [ "${MODE}" = "review-only" ] || [ "${MODE}" = "review_only" ]; then
	echo "Starting in review-only mode (no build)" >&2
else
	# Prepare environment (creates venv and installs package if needed)
	./build.sh >/dev/null
fi

# Activate venv
# shellcheck source=/dev/null
source venv/bin/activate

# Launch the MCP server with unbuffered output for immediate log streaming
export PYTHONUNBUFFERED=1

# In review-only mode, wait for container readiness file written by full server
if [ "${MODE}" = "review" ] || [ "${MODE}" = "review-only" ] || [ "${MODE}" = "review_only" ]; then
	HOST_WS_DIR=${POTATO_WORKSPACE_DIR:-workspace}
	READY_FILE="${HOST_WS_DIR}/.agent/potato_ready.json"
	echo "Waiting for container readiness at ${READY_FILE} ..." >&2
	# Wait up to 60s by default; override with POTATO_REVIEW_WAIT_SECS
	WAIT_SECS=${POTATO_REVIEW_WAIT_SECS:-60}
	START_TS=$(date +%s)
	while true; do
		if [ -f "${READY_FILE}" ]; then
			# basic sanity check: non-empty file
			if [ -s "${READY_FILE}" ]; then
				echo "Readiness file found." >&2
				break
			fi
		fi
		NOW=$(date +%s)
		ELAPSED=$(( NOW - START_TS ))
		if [ ${ELAPSED} -ge ${WAIT_SECS} ]; then
			echo "Timeout waiting for readiness file: ${READY_FILE}" >&2
			exit 1
		fi
		sleep 1
	done
fi

effective-potato "$@"
