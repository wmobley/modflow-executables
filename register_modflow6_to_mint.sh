#!/usr/bin/env bash
set -euo pipefail

# Register MODFLOW 6 as a model in MINT Models API.
#
# Required:
#   TOKEN or MINT_API_TOKEN
#
# Optional:
#   MINT_MODELS_API_BASE        (default: https://api.models.mint.tacc.utexas.edu)
#   MODEL_CREATE_ENDPOINT       (default: auto-detect via OpenAPI, fallback /models)
#   MODEL_SHORT_NAME            (default: MODFLOW6)
#   MODEL_NAME                  (default: MODFLOW 6)
#   MODEL_DESCRIPTION           (default: built-in text)
#   MODEL_VERSION               (default: 6)
#   MODEL_CODE_REPOSITORY       (default: https://github.com/MODFLOW-USGS/modflow6)
#   MODEL_LICENSE               (default: https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits)
#   MODEL_WEBSITE               (default: https://www.usgs.gov/software/modflow-6-usgs-modular-hydrologic-model)
#   OWNER_ORG                   (optional, for custom endpoints)
#   DRY_RUN                     (true|false, default: false)
#
# Example:
#   TOKEN="$TOKEN" modflow6/register_modflow6_to_mint.sh

API_BASE="${MINT_MODELS_API_BASE:-https://api.models.mint.tacc.utexas.edu}"
OPENAPI_URL="${API_BASE}/openapi.json"
MODEL_CREATE_ENDPOINT="${MODEL_CREATE_ENDPOINT:-}"

MODEL_SHORT_NAME="${MODEL_SHORT_NAME:-MODFLOW6}"
MODEL_NAME="${MODEL_NAME:-MODFLOW 6}"
MODEL_DESCRIPTION="${MODEL_DESCRIPTION:-USGS MODFLOW 6 groundwater flow model for structured and unstructured grids.}"
MODEL_VERSION="${MODEL_VERSION:-6}"
MODEL_CODE_REPOSITORY="${MODEL_CODE_REPOSITORY:-https://github.com/MODFLOW-USGS/modflow6}"
MODEL_LICENSE="${MODEL_LICENSE:-https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits}"
MODEL_WEBSITE="${MODEL_WEBSITE:-https://www.usgs.gov/software/modflow-6-usgs-modular-hydrologic-model}"
OWNER_ORG="${OWNER_ORG:-}"
DRY_RUN="${DRY_RUN:-false}"

AUTH_TOKEN="${MINT_API_TOKEN:-${TOKEN:-}}"
if [[ -z "${AUTH_TOKEN}" ]]; then
  echo "ERROR: set TOKEN or MINT_API_TOKEN." >&2
  exit 1
fi

detect_endpoint() {
  local openapi_json="$1"
  local detected

  detected="$(jq -r '
    .paths as $p
    | if ($p["/models"].post != null) then "/models"
      elif ($p["/custom/models"].post != null) then "/custom/models"
      elif ($p["/model"].post != null) then "/model"
      else
        (
          [ $p | to_entries[] | select(.value.post != null) | .key
            | select(test("model"; "i")) ] | .[0] // ""
        )
      end
  ' <<<"${openapi_json}")"

  echo "${detected}"
}

if [[ -z "${MODEL_CREATE_ENDPOINT}" ]]; then
  echo "Discovering model create endpoint from ${OPENAPI_URL}"
  openapi_json="$(curl -fsSL "${OPENAPI_URL}")"
  MODEL_CREATE_ENDPOINT="$(detect_endpoint "${openapi_json}")"
  if [[ -z "${MODEL_CREATE_ENDPOINT}" ]]; then
    MODEL_CREATE_ENDPOINT="/models"
  fi
fi

CREATE_URL="${API_BASE}${MODEL_CREATE_ENDPOINT}"

payload="$(jq -nc \
  --arg name "${MODEL_NAME}" \
  --arg short "${MODEL_SHORT_NAME}" \
  --arg desc "${MODEL_DESCRIPTION}" \
  --arg ver "${MODEL_VERSION}" \
  --arg repo "${MODEL_CODE_REPOSITORY}" \
  --arg lic "${MODEL_LICENSE}" \
  --arg web "${MODEL_WEBSITE}" \
  --arg owner "${OWNER_ORG}" \
  '{
    type: ["Model"],
    name: [$name],
    has_short_name: [$short],
    description: [$desc],
    version_info: [$ver],
    code_repository: [$repo],
    license: [$lic],
    website: [$web]
  } + (if $owner != "" then {owner_org: $owner} else {} end)')"

echo "Create endpoint: ${CREATE_URL}"
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY_RUN] Payload:"
  echo "${payload}" | jq .
  exit 0
fi

response="$(curl -fsS -X POST "${CREATE_URL}" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${payload}")"

echo "Model registration response:"
echo "${response}" | jq .
