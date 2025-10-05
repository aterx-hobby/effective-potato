#!/usr/bin/env bash
set -euo pipefail

# Inputs via environment:
# DEV_OPENWEB_URL or DEV_OPENWEBAPI_URL
# DEV_OPENWEB_KEY or DEV_OPENWEBAPI_KEY
# MODEL_NAME (required if NEW_MODEL_NAME not set; inferred from MODEL_FILE if omitted)
# NEW_MODEL_NAME (optional; overrides MODEL_NAME for the deployed/display name)
# MODEL_FILE (optional; path to exported JSON to copy params/meta/base_model_id/access_control)
# BASE_MODEL_ID (optional; used if not present in MODEL_FILE)
# DESCRIPTION (optional; used if not present in MODEL_FILE.meta)
# ACTIVATE=1 (optional; default 1)

BASE_URL="${DEV_OPENWEB_URL:-${DEV_OPENWEBAPI_URL:-}}"
API_KEY="${DEV_OPENWEB_KEY:-${DEV_OPENWEBAPI_KEY:-}}"
MODEL_NAME="${MODEL_NAME:-}"
NEW_MODEL_NAME="${NEW_MODEL_NAME:-}"
MODEL_FILE="${MODEL_FILE:-}"
BASE_MODEL_ID="${BASE_MODEL_ID:-}"
DESCRIPTION="${DESCRIPTION:-Imported via effective-potato}" 
ACTIVATE="${ACTIVATE:-1}"

if [[ -z "$BASE_URL" ]]; then
  echo "ERROR: DEV_OPENWEB_URL/DEV_OPENWEBAPI_URL is required" >&2
  exit 2
fi
if [[ -z "$MODEL_NAME" && -z "$NEW_MODEL_NAME" && -z "$MODEL_FILE" ]]; then
  echo "ERROR: Provide MODEL_NAME or MODEL_FILE (with optional NEW_MODEL_NAME)" >&2
  exit 2
fi

auth_header=()
if [[ -n "$API_KEY" ]]; then
  auth_header=("-H" "Authorization: Bearer $API_KEY")
fi

base_trimmed="${BASE_URL%/}"
create_url="$base_trimmed/api/v1/models/create"
update_url="$base_trimmed/api/v1/models/model/update"

payload=$(mktemp)
python3 - "$payload" << 'PY'
import json, os, sys
out=sys.argv[1]
export_path=os.environ.get('MODEL_FILE') or ''
model_name=os.environ.get('NEW_MODEL_NAME') or os.environ.get('MODEL_NAME') or ''
base=os.environ.get('BASE_MODEL_ID') or None
desc=os.environ.get('DESCRIPTION')
activate=(os.environ.get('ACTIVATE','1')=='1')
data={}
if export_path:
    try:
        with open(export_path,'r',encoding='utf-8') as f:
            data=json.load(f)
    except Exception:
        data={}
# Infer name from file if missing
if not model_name:
  if isinstance(data, dict):
    for k in ('name','model_name','label','id'):
      v=data.get(k)
      if isinstance(v,str) and v.strip():
        model_name=v.strip()
        break
  if not model_name:
    print('ERROR: MODEL_NAME/NEW_MODEL_NAME required or present in MODEL_FILE', file=sys.stderr)
    sys.exit(2)
meta=data.get('meta') if isinstance(data,dict) else None
if not isinstance(meta, dict):
    meta={}
if desc:
    meta['description']=desc
params=data.get('params') if isinstance(data,dict) else None
if not isinstance(params, dict):
    params={}
base_id=None
if isinstance(data,dict):
  # common keys
  for key in ('base_model_id','baseModelId','base_model'):
    if key in data:
      v=data.get(key)
      if isinstance(v, dict):
        base_id=v.get('id') or v.get('name') or v.get('model_name')
      elif isinstance(v, str) and v.strip():
        base_id=v.strip()
      if base_id:
        break
if base and not base_id:
  base_id=base
access=data.get('access_control') if isinstance(data,dict) else None
doc={
  'id': model_name,
  'name': model_name,
  'meta': meta,
  'params': params,
  'is_active': activate,
}
if base_id:
  doc['base_model_id']=base_id
if isinstance(access, dict):
  doc['access_control']=access
with open(out,'w',encoding='utf-8') as f:
  json.dump(doc,f)
print(out)
PY

body=$(mktemp)
code=$(curl -sS -o "$body" -w "%{http_code}" "${auth_header[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json' --data @"$payload" "$create_url" || true)
if [[ "$code" == "200" ]]; then
  echo "Registered in UI: ${NEW_MODEL_NAME:-$MODEL_NAME}"
else
  # If already registered, try update
  if grep -q "already registered" "$body" 2>/dev/null; then
    TARGET_NAME="${NEW_MODEL_NAME:-$MODEL_NAME}"
    code2=$(curl -sS -o /dev/null -w "%{http_code}" "${auth_header[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json' --data @"$payload" "$update_url?id=$TARGET_NAME" || true)
    if [[ "$code2" != "200" ]]; then
      echo "ERROR: Failed to update UI model (status $code2)" >&2
      rm -f "$payload" "$body"
      exit 4
    fi
    echo "Updated UI model: ${NEW_MODEL_NAME:-$MODEL_NAME}"
  else
    echo "ERROR: Failed to create/register model in UI (status $code)" >&2
    cat "$body" >&2 || true
    rm -f "$payload" "$body"
    exit 4
  fi
fi
rm -f "$payload" "$body"

TARGET_NAME="${NEW_MODEL_NAME:-$MODEL_NAME}"

# Verify presence in UI models list before publishing
tmpverify=$(mktemp)
vcode=$(curl -fsSL -o "$tmpverify" "${auth_header[@]}" -H 'Accept: application/json' "$base_trimmed/api/v1/models" || true)
present=$(python3 - "$TARGET_NAME" "$tmpverify" << 'PY'
import json,sys
target=sys.argv[1]
path=sys.argv[2]
try:
  with open(path,'rb') as f:
    data=json.load(f)
  arr=data.get('data') if isinstance(data,dict) else data
  if isinstance(arr,list):
    for m in arr:
      if isinstance(m,dict) and (m.get('name')==target or m.get('model_name')==target or m.get('id')==target):
        print('yes'); raise SystemExit(0)
except Exception:
  pass
print('no')
PY
)
rm -f "$tmpverify"

if [[ "$present" != "yes" ]]; then
  echo "ERROR: Model '$TARGET_NAME' not present in /api/v1/models after register/update. Skipping workspace publish." >&2
  exit 5
fi

# Place it into workspace order if present
if [[ -n "$TARGET_NAME" ]]; then
  if [[ "${SET_DEFAULT:-0}" == "1" ]]; then
    MODEL_NAME="$TARGET_NAME" SET_DEFAULT=1 POSITION="front" bash "$(dirname "$0")/publish_openweb_model.sh" >/dev/null || true
  else
    MODEL_NAME="$TARGET_NAME" POSITION="front" bash "$(dirname "$0")/publish_openweb_model.sh" >/dev/null || true
  fi
  echo "Published to workspace order: $TARGET_NAME"
fi
