#!/usr/bin/env bash
set -euo pipefail

# Inputs via environment (do not echo secrets):
# DEV_OPENWEBAPI_URL or DEV_OPENWEB_URL   - Base URL to the OpenWeb server
# DEV_OPENWEBAPI_KEY or DEV_OPENWEB_KEY   - API key (secret)
# MODEL_FILE        - Path to exported model JSON (required)
# MODEL_NAME        - Fallback model name if JSON lacks one
# NEW_MODEL_NAME    - Deploy under this alternative name (optional)
# DELETE_EXISTING   - If "1", delete existing model with target name before import

BASE_URL="${DEV_OPENWEB_URL:-${DEV_OPENWEBAPI_URL:-}}"
API_KEY="${DEV_OPENWEB_KEY:-${DEV_OPENWEBAPI_KEY:-}}"
MODEL_FILE="${MODEL_FILE:-}"
FALLBACK_NAME="${MODEL_NAME:-}"
ALT_NAME="${NEW_MODEL_NAME:-}"
DELETE_EXISTING="${DELETE_EXISTING:-0}"

if [[ -z "$BASE_URL" ]]; then
  echo "ERROR: DEV_OPENWEBAPI_URL/DEV_OPENWEB_URL is required" >&2
  exit 2
fi
if [[ -z "$MODEL_FILE" ]]; then
  echo "ERROR: MODEL_FILE is required (path to exported JSON)" >&2
  exit 2
fi
if [[ ! -f "$MODEL_FILE" ]]; then
  echo "ERROR: MODEL_FILE not found: $MODEL_FILE" >&2
  exit 2
fi

auth_header=()
if [[ -n "$API_KEY" ]]; then
  auth_header=("-H" "Authorization: Bearer $API_KEY")
fi

base_trimmed="${BASE_URL%/}"
list_url="$base_trimmed/api/v1/models"
import_url="$base_trimmed/api/v1/models/import"
delete_url="$base_trimmed/api/v1/models/model/delete"

# parse model JSON and determine deploy name
tmpnorm=$(mktemp)
deploy_name=$(python3 - "$MODEL_FILE" "$FALLBACK_NAME" "$ALT_NAME" "$tmpnorm" << 'PY'
import json, sys
src, fallback, alt, dst = sys.argv[1:5]
with open(src, 'r', encoding='utf-8') as f:
    data = json.load(f)
name = None
if isinstance(data, dict):
    for k in ('name','model_name','label','id'):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
if not name:
    if fallback:
        name = fallback
    else:
        print('ERROR: Unable to determine model name; set MODEL_NAME or ensure JSON has a name field', file=sys.stderr)
        sys.exit(3)
deploy = alt or name
# If alternative, set name fields accordingly
if isinstance(data, dict):
  # Always set display/name fields to the deploy target
  for k in ('name','model_name','label'):
    data[k] = deploy
  # Remove possible immutable identifiers
  for k in ('id','_id','uuid'):
    if k in data:
      data.pop(k, None)
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False)
print(deploy)
PY

)


# check if model with deploy_name exists
tmpmodels=$(mktemp)
code=$(curl -fsSL -w "%{http_code}" -o "$tmpmodels" "${auth_header[@]}" -H 'Accept: application/json' "$list_url" || true)
if [[ "$code" != "200" ]]; then
  # fallback to export list
  list_url="$base_trimmed/api/v1/models/export"
  code=$(curl -fsSL -w "%{http_code}" -o "$tmpmodels" "${auth_header[@]}" -H 'Accept: application/json' "$list_url" || true)
fi

existing_id=""
if [[ "$code" == "200" ]]; then
  existing_id=$(python3 - "$tmpmodels" "$deploy_name" << 'PY'
import json, sys
src, name = sys.argv[1:3]
with open(src, 'r', encoding='utf-8') as f:
    data = json.load(f)
if isinstance(data, list):
    for item in data:
        if isinstance(item, dict):
            nm = str(item.get('name') or item.get('model_name') or '').strip()
            if nm == name:
                print(str(item.get('id') or ''))
                break
PY
  )
fi


if [[ -n "$existing_id" && "$DELETE_EXISTING" != "1" ]]; then
  echo "WARNING: Model '$deploy_name' already exists and will not be overwritten. Set DELETE_EXISTING=1 to replace." >&2
  rm -f "$tmpnorm" "$tmpmodels"
  exit 0
fi

if [[ -n "$existing_id" && "$DELETE_EXISTING" == "1" ]]; then
  # delete existing by id
  delcode=$(curl -fsSL -o /dev/null -w "%{http_code}" "${auth_header[@]}" -X DELETE "$delete_url?id=$existing_id" || true)
  if [[ "$delcode" != "200" && "$delcode" != "204" ]]; then
    echo "ERROR: Failed to delete existing model id=$existing_id (status $delcode)" >&2
    rm -f "$tmpnorm" "$tmpmodels"
    exit 5
  fi
fi

# import using normalized JSON (with rename if provided)
:
impbody=$(mktemp)
impcode=$(curl -sS -o "$impbody" -w "%{http_code}" "${auth_header[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json' --data @"$tmpnorm" "$import_url" || true)
:
rm -f "$tmpmodels" "$impbody"
if [[ "$impcode" != "200" && "$impcode" != "201" ]]; then
  if [[ "$impcode" == "409" || "$impcode" == "422" ]]; then
    # Try alternate schema: wrap payload as {"models": [data]}
    tmpwrap=$(mktemp)
    python3 - "$tmpnorm" "$tmpwrap" << 'PY'
import json, sys
src, dst = sys.argv[1:3]
with open(src, 'r', encoding='utf-8') as f:
    data = json.load(f)
with open(dst, 'w', encoding='utf-8') as f:
    json.dump({"models": [data]}, f)
PY
  :
    impbody2=$(mktemp)
    impcode2=$(curl -sS -o "$impbody2" -w "%{http_code}" "${auth_header[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json' --data @"$tmpwrap" "$import_url" || true)
    :
    rm -f "$impbody2" "$tmpwrap"
    if [[ "$impcode2" == "200" || "$impcode2" == "201" ]]; then
      rm -f "$tmpnorm"
      echo "Imported: $deploy_name"
      exit 0
    fi
    # Treat as conflict/validation: check if model exists with target name, then consider success (idempotent)
    chk=$(curl -sS "${auth_header[@]}" -H 'Accept: application/json' "$base_trimmed/api/v1/models" || true)
    if echo "$chk" | python3 - "$deploy_name" - << 'PY'
import json, sys
name = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if isinstance(data, list):
    for item in data:
        if isinstance(item, dict) and (item.get('name') == name or item.get('model_name') == name):
            sys.exit(0)
sys.exit(2)
PY
    then
      echo "WARNING: Model '$deploy_name' already exists and will not be overwritten. Set DELETE_EXISTING=1 to replace." >&2
      rm -f "$tmpnorm"
      exit 0
    fi
    # Try a minimal import payload as fallback
    mini=$(printf '{"name":"%s"}' "$deploy_name")
  :
    imp2body=$(mktemp)
    imp2=$(curl -sS -o "$imp2body" -w "%{http_code}" "${auth_header[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json' --data "$mini" "$import_url" || true)
    :
    rm -f "$imp2body"
    if [[ "$imp2" == "200" || "$imp2" == "201" ]]; then
      rm -f "$tmpnorm"
      echo "Imported: $deploy_name"
      exit 0
    fi
    # Try wrapped minimal payload
    miniwrap=$(printf '{"models":[{"name":"%s"}]}' "$deploy_name")
    imp3body=$(mktemp)
    imp3=$(curl -sS -o "$imp3body" -w "%{http_code}" "${auth_header[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json' --data "$miniwrap" "$import_url" || true)
    :
    rm -f "$imp3body"
    if [[ "$imp3" == "200" || "$imp3" == "201" ]]; then
      rm -f "$tmpnorm"
      echo "Imported: $deploy_name"
      exit 0
    fi
  fi
  echo "ERROR: Import failed for '$deploy_name' (status $impcode)" >&2
  rm -f "$tmpnorm"
  exit 6
fi
rm -f "$tmpnorm"
echo "Imported: $deploy_name"
