#!/usr/bin/env bash
set -euo pipefail

# One-shot redeploy: export → import → register → publish
#
# Inputs via environment (do not echo secrets):
# - DEV_OPENWEB_URL or DEV_OPENWEBAPI_URL  Base API URL
# - DEV_OPENWEB_KEY or DEV_OPENWEBAPI_KEY  API key (secret)
# - MODEL_NAME            Source model name to export (required unless MODEL_FILE provided)
# - OUTPUT_DIR            Optional export directory (defaults to ./openweb_exports/<MODEL_NAME>)
# - MODEL_FILE            Optional path to a pre-existing export JSON (skips export stage)
# - NEW_MODEL_NAME        Target model name on destination
# - DELETE_EXISTING       If "1", delete existing target model before import (default: 0)
# - SET_DEFAULT           If "1", set as default in workspace after register (default: 0)
# - BASE_MODEL_ID         Optional base model id to force during register when not in export JSON
# - DESCRIPTION           Optional description override
# - ACTIVATE              1/0 whether the model should be active in UI registry (default: 1)

BASE_URL="${DEV_OPENWEB_URL:-${DEV_OPENWEBAPI_URL:-}}"
API_KEY="${DEV_OPENWEB_KEY:-${DEV_OPENWEBAPI_KEY:-}}"
MODEL_NAME="${MODEL_NAME:-}"
MODEL_FILE="${MODEL_FILE:-}"
NEW_MODEL_NAME="${NEW_MODEL_NAME:-}"
DELETE_EXISTING="${DELETE_EXISTING:-0}"
SET_DEFAULT="${SET_DEFAULT:-0}"
BASE_MODEL_ID="${BASE_MODEL_ID:-}"
DESCRIPTION="${DESCRIPTION:-}"
ACTIVATE="${ACTIVATE:-1}"
OUTPUT_DIR="${OUTPUT_DIR:-}"

if [[ -z "$BASE_URL" ]]; then
  echo "ERROR: DEV_OPENWEB_URL/DEV_OPENWEBAPI_URL is required" >&2
  exit 2
fi

# Stage 1: Export (unless MODEL_FILE provided)
exported_file="$MODEL_FILE"
if [[ -z "$exported_file" ]]; then
  if [[ -z "$MODEL_NAME" ]]; then
    echo "ERROR: MODEL_NAME is required when MODEL_FILE is not provided" >&2
    exit 2
  fi
  # Use provided OUTPUT_DIR, else default to ./openweb_exports/<MODEL_NAME>
  # shellcheck disable=SC2016
  if [[ -n "$OUTPUT_DIR" ]]; then
    DEV_OPENWEB_URL="$BASE_URL" DEV_OPENWEBAPI_URL="$BASE_URL" \
    DEV_OPENWEB_KEY="$API_KEY" DEV_OPENWEBAPI_KEY="$API_KEY" \
    MODEL_NAME="$MODEL_NAME" OUTPUT_DIR="$OUTPUT_DIR" \
    bash "$(dirname "$0")/export_openweb_model.sh" | {
      last=""; while IFS= read -r line; do last="$line"; done; echo "$last"; } > /tmp/redeploy_export_last.txt
  else
    DEV_OPENWEB_URL="$BASE_URL" DEV_OPENWEBAPI_URL="$BASE_URL" \
    DEV_OPENWEB_KEY="$API_KEY" DEV_OPENWEBAPI_KEY="$API_KEY" \
    MODEL_NAME="$MODEL_NAME" \
    bash "$(dirname "$0")/export_openweb_model.sh" | {
      last=""; while IFS= read -r line; do last="$line"; done; echo "$last"; } > /tmp/redeploy_export_last.txt
  fi
  last_line=$(cat /tmp/redeploy_export_last.txt 2>/dev/null || true)
  rm -f /tmp/redeploy_export_last.txt || true
  if [[ "$last_line" =~ ^Exported:\ (.*)$ ]]; then
    exported_file="${BASH_REMATCH[1]}"
  else
    echo "ERROR: Failed to parse export output for file path" >&2
    exit 3
  fi
fi

if [[ ! -f "$exported_file" ]]; then
  echo "ERROR: Exported MODEL_FILE not found: $exported_file" >&2
  exit 3
fi

# Determine target deploy name (NEW_MODEL_NAME preferred)
TARGET_NAME="$NEW_MODEL_NAME"
if [[ -z "$TARGET_NAME" ]]; then
  # Infer from MODEL_FILE content or fallback to MODEL_NAME
  TARGET_NAME=$(python3 - "$exported_file" "${MODEL_NAME:-}" << 'PY'
import json, sys
src, fallback = sys.argv[1], sys.argv[2]
try:
  with open(src,'r',encoding='utf-8') as f:
    data=json.load(f)
  name=None
  if isinstance(data,dict):
    for k in ('name','model_name','label','id'):
      v=data.get(k)
      if isinstance(v,str) and v.strip():
        name=v.strip(); break
  print(name or fallback)
except Exception:
  print(fallback)
PY
)
fi
if [[ -z "$TARGET_NAME" ]]; then
  echo "ERROR: Unable to determine target model name; set NEW_MODEL_NAME or MODEL_NAME" >&2
  exit 2
fi

# Stage 2: Import into destination under TARGET_NAME
DEV_OPENWEB_URL="$BASE_URL" DEV_OPENWEBAPI_URL="$BASE_URL" \
DEV_OPENWEB_KEY="$API_KEY" DEV_OPENWEBAPI_KEY="$API_KEY" \
MODEL_FILE="$exported_file" NEW_MODEL_NAME="$TARGET_NAME" DELETE_EXISTING="$DELETE_EXISTING" \
bash "$(dirname "$0")/import_openweb_model.sh"

# Stage 3: Register in UI (create or update) and publish into workspace
envs=(
  "DEV_OPENWEB_URL=$BASE_URL"
  "DEV_OPENWEBAPI_URL=$BASE_URL"
  "DEV_OPENWEB_KEY=$API_KEY"
  "DEV_OPENWEBAPI_KEY=$API_KEY"
  "MODEL_FILE=$exported_file"
  "NEW_MODEL_NAME=$TARGET_NAME"
)
[[ -n "$BASE_MODEL_ID" ]] && envs+=("BASE_MODEL_ID=$BASE_MODEL_ID")
[[ -n "$DESCRIPTION" ]] && envs+=("DESCRIPTION=$DESCRIPTION")
[[ -n "$ACTIVATE" ]] && envs+=("ACTIVATE=$ACTIVATE")
[[ -n "$SET_DEFAULT" ]] && envs+=("SET_DEFAULT=$SET_DEFAULT")

"bash" "$(dirname "$0")/register_openweb_model.sh" >/dev/null 2>&1 & # shellcheck disable=SC2069
(
  # Launch register with env map without leaking secrets to stdout
  # Use a subshell to export envs and run quietly, then report final status
  set -e
  for kv in "${envs[@]}"; do export "$kv"; done
  bash "$(dirname "$0")/register_openweb_model.sh"
) 1>/dev/null

echo "Redeployed: $TARGET_NAME"
