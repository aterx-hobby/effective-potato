#!/usr/bin/env bash
set -euo pipefail

# Inputs via environment (do not echo secrets):
# DEV_OPENWEBAPI_URL or DEV_OPENWEB_URL   - Base URL to the OpenWeb server (e.g., https://openweb.example.com)
# DEV_OPENWEBAPI_SCHEMA or DEV_OPENWEB_SCHEMA - URL to OpenAPI schema (optional; not required)
# DEV_OPENWEBAPI_KEY or DEV_OPENWEB_KEY   - API key (secret)
# MODEL_NAME           - Model name to export (default: effective-potato)
# OUTPUT_DIR           - Directory to write export (default: ./openweb_exports/<model>)

MODEL_NAME="${MODEL_NAME:-effective-potato}"
BASE_URL="${DEV_OPENWEBAPI_URL:-${DEV_OPENWEB_URL:-}}"
API_KEY="${DEV_OPENWEBAPI_KEY:-${DEV_OPENWEB_KEY:-}}"
SCHEMA_URL="${DEV_OPENWEBAPI_SCHEMA:-${DEV_OPENWEB_SCHEMA:-}}"

if [[ -z "$BASE_URL" ]]; then
  echo "ERROR: DEV_OPENWEBAPI_URL is required" >&2
  exit 2
fi

base_trimmed="${BASE_URL%/}"
export_url="$base_trimmed/api/v1/models/export"

auth_header=()
if [[ -n "$API_KEY" ]]; then
  auth_header=("-H" "Authorization: Bearer $API_KEY")
fi

# Fetch export (JSON array of models) and filter by name
tmpjson=$(mktemp)
code=$(curl -fsSL -w "%{http_code}" -o "$tmpjson" "${auth_header[@]}" -H 'Accept: application/json' "$export_url" || true)
if [[ "$code" != "200" ]]; then
  echo "ERROR: Export endpoint not available at $export_url (status $code)" >&2
  rm -f "$tmpjson"
  exit 3
fi

# Extract the desired model by name using a small Python filter (avoid jq dependency)
tmpout=$(mktemp)
python3 - "$MODEL_NAME" "$tmpjson" "$tmpout" << 'PY'
import json, sys
name, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, 'r', encoding='utf-8') as f:
    data = json.load(f)
# data is expected to be a list of models; select first with matching name
target = None
if isinstance(data, list):
    # try common fields for name
    for item in data:
        for key in ('name', 'model_name', 'id', 'label'):
            if isinstance(item, dict) and str(item.get(key, '')).strip() == name:
                target = item
                break
        if target is not None:
            break
else:
    # if server returns a dict, attempt direct mapping
    target = data
if target is None:
    print(f"ERROR: Model '{name}' not found in export list", file=sys.stderr)
    sys.exit(4)
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(target, f, ensure_ascii=False, indent=2)
print(dst)
PY
status=$?
if [[ "$status" -ne 0 ]]; then
  rm -f "$tmpjson" "$tmpout"
  exit "$status"
fi

safe_name="${MODEL_NAME//\//-}"
outdir="${OUTPUT_DIR:-./openweb_exports/$safe_name}"
mkdir -p "$outdir"

ts=$(date -u +%Y%m%dT%H%M%SZ)
outfile="$outdir/${safe_name}_$ts.json"
mv "$tmpout" "$outfile"
rm -f "$tmpjson"
echo "Exported: $outfile"
