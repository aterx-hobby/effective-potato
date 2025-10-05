#!/usr/bin/env bash
set -euo pipefail

# Inputs via environment:
# DEV_OPENWEB_URL or DEV_OPENWEBAPI_URL  - Base URL
# DEV_OPENWEB_KEY or DEV_OPENWEBAPI_KEY  - API key
# MODEL_NAME           - Model name to publish (required)
# SET_DEFAULT          - If "1", set as DEFAULT_MODELS (optional)
# POSITION             - "front" to prepend, "back" to append (default: back)

BASE_URL="${DEV_OPENWEB_URL:-${DEV_OPENWEBAPI_URL:-}}"
API_KEY="${DEV_OPENWEB_KEY:-${DEV_OPENWEBAPI_KEY:-}}"
MODEL_NAME="${MODEL_NAME:-}"
SET_DEFAULT="${SET_DEFAULT:-0}"
POSITION="${POSITION:-back}"

if [[ -z "$BASE_URL" ]]; then
  echo "ERROR: DEV_OPENWEB_URL/DEV_OPENWEBAPI_URL is required" >&2
  exit 2
fi
if [[ -z "$MODEL_NAME" ]]; then
  echo "ERROR: MODEL_NAME is required" >&2
  exit 2
fi

auth_header=()
if [[ -n "$API_KEY" ]]; then
  auth_header=("-H" "Authorization: Bearer $API_KEY")
fi

base_trimmed="${BASE_URL%/}"
cfg_url="$base_trimmed/api/v1/configs/models"

tmpcfg=$(mktemp)
code=$(curl -fsSL -w "%{http_code}" -o "$tmpcfg" "${auth_header[@]}" -H 'Accept: application/json' "$cfg_url" || true)
if [[ "$code" != "200" ]]; then
  echo "ERROR: Failed to GET models config (status $code)" >&2
  rm -f "$tmpcfg"
  exit 3
fi

tmpout=$(mktemp)
python3 - "$tmpcfg" "$MODEL_NAME" "$SET_DEFAULT" "$POSITION" "$tmpout" << 'PY'
import json, sys
src, name, set_default, position, dst = sys.argv[1:6]
with open(src, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
# Normalize fields
default = cfg.get('DEFAULT_MODELS')
order = cfg.get('MODEL_ORDER_LIST')
if order is None:
    order = []
if not isinstance(order, list):
    order = []
if default is not None and not isinstance(default, str):
    default = None

# Insert name
order = [m for m in order if m != name]
if position == 'front':
    order = [name] + order
else:
    order = order + [name]

if set_default == '1':
    default = name

out = {
    'DEFAULT_MODELS': default,
    'MODEL_ORDER_LIST': order,
}
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(out, f)
PY

code2=$(curl -sS -o /dev/null -w "%{http_code}" "${auth_header[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json' --data @"$tmpout" "$cfg_url" || true)
rm -f "$tmpcfg" "$tmpout"
if [[ "$code2" != "200" ]]; then
  echo "ERROR: Failed to POST models config (status $code2)" >&2
  exit 4
fi
echo "Published to workspace: $MODEL_NAME"
