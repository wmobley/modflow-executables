#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export GIT_REPO_URL="https://github.com/wmobley/modflow6"
export COOKBOOK_NAME="FloPy"
export COOKBOOK_CONDA_ENV="flopy"
export GIT_BRANCH="${GIT_BRANCH:-main}"
export DOWNLOAD_LATEST_VERSION="${DOWNLOAD_LATEST_VERSION:-false}"
IS_GPU_JOB=false

function export_repo_variables() {
	COOKBOOK_DIR=./
	COOKBOOK_WORKSPACE_DIR=${COOKBOOK_DIR}/${COOKBOOK_NAME}

	COOKBOOK_REPOSITORY_PARENT_DIR=${COOKBOOK_DIR}/.repository
	COOKBOOK_REPOSITORY_DIR=${COOKBOOK_REPOSITORY_PARENT_DIR}/${COOKBOOK_NAME}
	UPDATE_AVAILABLE_FILE=${COOKBOOK_WORKSPACE_DIR}/UPDATE_AVAILABLE.txt
	NODE_HOSTNAME_PREFIX=$(hostname -s) # Short Host Name  -->  name of compute node: c###-###
	NODE_HOSTNAME_DOMAIN=$(hostname -d) # DNS Name  -->  stampede2.tacc.utexas.edu
	NODE_HOSTNAME_LONG=$(hostname -f)   # Fully Qualified Domain Name  -->  c###-###.stampede2.tacc.utexas.edu
	export COOKBOOK_DIR
	export COOKBOOK_WORKSPACE_DIR
	export COOKBOOK_REPOSITORY_DIR
	export COOKBOOK_REPOSITORY_PARENT_DIR
	export UPDATE_AVAILABLE_FILE
	export NODE_HOSTNAME_PREFIX
	export NODE_HOSTNAME_DOMAIN
	export NODE_HOSTNAME_LONG
}

function install_conda() {
	echo "Checking if miniconda3 is installed..."
	if [ ! -d "$WORK/miniconda3" ]; then
		echo "Miniconda not found in $WORK..."
		echo "Installing..."
		mkdir -p "$WORK/miniconda3"
		curl https://repo.anaconda.com/miniconda/Miniconda3-py311_23.10.0-1-Linux-x86_64.sh -o "$WORK/miniconda3/miniconda.sh"
		bash "$WORK/miniconda3/miniconda.sh" -b -u -p "$WORK/miniconda3"
		rm -rf "$WORK/miniconda3/miniconda.sh"
		export PATH="$WORK/miniconda3/bin:$PATH"
		echo "Ensuring conda base environment is OFF..."
		conda config --set auto_activate_base false
	else
		export PATH="$WORK/miniconda3/bin:$PATH"
	fi
	conda init bash
	echo "Sourcing .bashrc..."
	set +u
	source ~/.bashrc
	set -u
	unset PYTHONPATH
}

function ensure_git() {
	if ! command -v git >/dev/null 2>&1; then
		if command -v module >/dev/null 2>&1; then
			module load git || true
		fi
	fi
	if ! command -v git >/dev/null 2>&1; then
		echo "ERROR: git not found in PATH. Please install git or load it via modules before running." >&2
		exit 1
	fi
}

function clone_cookbook_on_workspace() {
	DATE_FILE_SUFFIX=$(date +%Y%m%d%H%M%S)
	if [ ! -d "$COOKBOOK_WORKSPACE_DIR" ]; then
		git clone ${GIT_REPO_URL} --branch ${GIT_BRANCH} ${COOKBOOK_WORKSPACE_DIR}
	else
		if [ ${DOWNLOAD_LATEST_VERSION} = "true" ]; then
			mv ${COOKBOOK_WORKSPACE_DIR} ${COOKBOOK_WORKSPACE_DIR}-${DATE_FILE_SUFFIX}
			git clone ${GIT_REPO_URL} --branch ${GIT_BRANCH} ${COOKBOOK_WORKSPACE_DIR}
		fi
	fi
}

function init_directory() {
	mkdir -p ${COOKBOOK_REPOSITORY_PARENT_DIR}
	clone_cookbook_on_workspace
	cd ${COOKBOOK_WORKSPACE_DIR}
}


function conda_environment_exists() {
	conda env list | grep "${COOKBOOK_CONDA_ENV}"
}

function create_conda_environment() {
	if [ -f ./.binder/environment.yml ]; then
		conda env create -n ${COOKBOOK_CONDA_ENV} -f ./.binder/environment.yml --yes
		conda activate ${COOKBOOK_CONDA_ENV}
	elif  [ -f ./.binder/environment.yaml ]; then
		conda env create -n ${COOKBOOK_CONDA_ENV} -f ./.binder/environment.yaml --yes
		conda activate ${COOKBOOK_CONDA_ENV}
	fi
	if [ -f ./.binder/requirements.txt ]; then
		pip install --no-cache-dir -r ./.binder/requirements.txt
	fi
	python -m ipykernel install --user --name "${COOKBOOK_CONDA_ENV}" --display-name "Python (${COOKBOOK_CONDA_ENV})"
}


function handle_installation() {
		if { conda_environment_exists; } >/dev/null 2>&1; then
			echo "Conda environment already exists"
		else
			create_conda_environment
		fi
	
}

#Execution
install_conda
export_repo_variables
ensure_git
init_directory
handle_installation



log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

CKAN_BASE_URL="${CKAN_BASE_URL:-https://ckan.tacc.utexas.edu}"
NAM_URL="${MF6_NAM_URL:-${1:-}}"
WEL_URL="${MF6_WEL_URL:-${2:-}}"
RCH_URL="${MF6_RCH_URL:-${3:-}}"

normalize_arg_url() {
  local v="${1:-}"
  case "$v" in
    "__NONE__"|"NONE"|"none"|"null"|"NULL")
      echo ""
      ;;
    *)
      echo "$v"
      ;;
  esac
}

NAM_URL="$(normalize_arg_url "$NAM_URL")"
WEL_URL="$(normalize_arg_url "$WEL_URL")"
RCH_URL="$(normalize_arg_url "$RCH_URL")"

download_url_to() {
  local url="$1"
  local target="$2"
  mkdir -p "$(dirname "$target")"
  curl -fsSL "$url" -o "$target"
}

ckan_dataset_from_url() {
  local url="$1"
  if [[ "$url" =~ /dataset/([^/]+)/resource/[^/]+/download/[^/?#]+ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

ckan_resource_id_from_url() {
  local url="$1"
  if [[ "$url" =~ /resource/([^/]+)/download/[^/?#]+ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

url_decoded_basename() {
  local url="$1"
  python3 - "$url" <<'PY'
import os, sys
from urllib.parse import unquote, urlparse
u = sys.argv[1]
path = urlparse(u).path
print(unquote(os.path.basename(path)))
PY
}

fetch_ckan_dataset_resources() {
  local dataset="$1"
  local out_json="$2"
  curl -fsSL "${CKAN_BASE_URL}/api/3/action/package_show?id=${dataset}" -o "$out_json"
}

extract_mf6_model_nam_from_mfsim() {
  local sim_nam="$1"
  awk '
    /^[[:space:]]*#/ {next}
    {
      line=$0
      sub(/[[:space:]]*#.*/, "", line)
      n=split(line, a, /[[:space:]]+/)
      if (n >= 2 && a[1] ~ /^GWF/) {
        print a[2]
        exit
      }
    }
  ' "$sim_nam"
}

extract_expected_pkg_file() {
  local model_nam="$1"
  local pkg_regex="$2"
  awk -v pkg="$pkg_regex" '
    /^[[:space:]]*#/ {next}
    {
      line=$0
      sub(/[[:space:]]*#.*/, "", line)
      n=split(line, a, /[[:space:]]+/)
      if (n >= 2 && a[1] ~ pkg) {
        print a[2]
        exit
      }
    }
  ' "$model_nam"
}

INPUTS_DIR="${_tapisExecSystemInputDir:-/tapis/input}"
OUTPUTS_DIR="${_tapisExecSystemOutputDir:-/tapis/output}"
RUN_ROOT="$PWD/run"
DEFAULT_DATA_DIR=""

SIM_ARCHIVE="$INPUTS_DIR/simulation.zip"
shopt -s nullglob
other_archives=("$INPUTS_DIR"/*.zip)
shopt -u nullglob

USE_INPUT_ROOT=false
if [[ ! -f "$SIM_ARCHIVE" && ${#other_archives[@]} -eq 0 && "$INPUTS_DIR" == "$PWD" ]]; then
  USE_INPUT_ROOT=true
  RUN_ROOT="$PWD"
fi

stage_required_files() {
  local f
  for f in "$@"; do
    if [[ -d "$INPUTS_DIR/$f" && ! -d "$RUN_ROOT/$f" ]]; then
      cp -a "$INPUTS_DIR/$f" "$RUN_ROOT/"
    elif [[ -f "$INPUTS_DIR/$f" && ! -f "$RUN_ROOT/$f" ]]; then
      cp -a "$INPUTS_DIR/$f" "$RUN_ROOT/"
    elif [[ -n "$DEFAULT_DATA_DIR" ]]; then
      if [[ -d "$DEFAULT_DATA_DIR/$f" && ! -d "$RUN_ROOT/$f" ]]; then
        cp -a "$DEFAULT_DATA_DIR/$f" "$RUN_ROOT/"
      elif [[ -f "$DEFAULT_DATA_DIR/$f" && ! -f "$RUN_ROOT/$f" ]]; then
        cp -a "$DEFAULT_DATA_DIR/$f" "$RUN_ROOT/"
      fi
    fi
  done
}

resolve_default_data_dir() {
  local configured_dir_file="$SCRIPT_DIR/carrizo-wilcox-dir.txt"
  local configured_dir=""

  if [[ -f "$configured_dir_file" ]]; then
    configured_dir="$(head -n 1 "$configured_dir_file" | tr -d '\r' | sed 's/[[:space:]]*$//')"
    if [[ -n "$configured_dir" ]]; then
      if [[ -d "$configured_dir" ]]; then
        DEFAULT_DATA_DIR="$configured_dir"
        log "Using default data directory from $(basename "$configured_dir_file"): $DEFAULT_DATA_DIR"
        return
      fi
      log "Configured default data directory does not exist: $configured_dir"
    fi
  fi

  if [[ -d "$RUN_ROOT/default_data" ]]; then
    DEFAULT_DATA_DIR="$RUN_ROOT/default_data"
  elif [[ -d "$INPUTS_DIR/default_data" ]]; then
    DEFAULT_DATA_DIR="$INPUTS_DIR/default_data"
  fi
}

if [[ "$USE_INPUT_ROOT" == "false" ]]; then
  rm -rf "$RUN_ROOT"
  mkdir -p "$RUN_ROOT"
fi
mkdir -p "$OUTPUTS_DIR"

if [[ -n "$NAM_URL" ]]; then
  log "NAM URL provided; pulling CKAN resources from URLs."

  nam_file_name="$(url_decoded_basename "$NAM_URL")"
  if [[ -z "$nam_file_name" ]]; then
    echo "Unable to infer name-file basename from NAM URL." >&2
    exit 1
  fi
  download_url_to "$NAM_URL" "$RUN_ROOT/$nam_file_name"
  log "Downloaded name file: $nam_file_name"
  if [[ "$nam_file_name" != "mfsim.nam" ]]; then
    cp -f "$RUN_ROOT/$nam_file_name" "$RUN_ROOT/mfsim.nam"
    log "Copied $nam_file_name to mfsim.nam"
  fi

  dataset_name="$(ckan_dataset_from_url "$NAM_URL" || true)"
  nam_resource_id="$(ckan_resource_id_from_url "$NAM_URL" || true)"
  wel_resource_id="$(ckan_resource_id_from_url "$WEL_URL" || true)"
  rch_resource_id="$(ckan_resource_id_from_url "$RCH_URL" || true)"

  if [[ -z "$dataset_name" ]]; then
    echo "NAM URL must be a CKAN dataset resource download URL." >&2
    exit 1
  fi

  pkg_json="$RUN_ROOT/package_show.json"
  fetch_ckan_dataset_resources "$dataset_name" "$pkg_json"

  python3 - "$pkg_json" "${nam_resource_id:-}" "${wel_resource_id:-}" "${rch_resource_id:-}" <<'PY' \
    | while IFS=$'\t' read -r rid rurl; do
import json, sys
from urllib.parse import urlparse

pkg_json = sys.argv[1]
skip_ids = {x for x in sys.argv[2:] if x}

with open(pkg_json, "r", encoding="utf-8") as f:
    payload = json.load(f)

if not payload.get("success"):
    raise SystemExit("CKAN package_show failed")

for r in payload["result"].get("resources", []):
    rid = r.get("id", "")
    if rid in skip_ids:
        continue
    url = r.get("url", "")
    if not url:
        continue
    if not urlparse(url).scheme:
        continue
    print(f"{rid}\t{url}")
PY
      fname="$(url_decoded_basename "$rurl")"
      lower="${fname,,}"
      if [[ "$fname" == "$nam_file_name" || "$fname" == "mfsim.nam" ]]; then
        continue
      fi
      if [[ "$lower" == *.wel || "$lower" == *.rch || "$lower" == *.rcha ]]; then
        continue
      fi
      if [[ -f "$RUN_ROOT/$fname" ]]; then
        continue
      fi
      log "Downloading dataset resource: $fname"
      download_url_to "$rurl" "$RUN_ROOT/$fname"
    done

  # Resolve expected package filenames from the uploaded simulation/model name files.
  sim_nam_path="$RUN_ROOT/$nam_file_name"
  model_nam_rel="$(extract_mf6_model_nam_from_mfsim "$sim_nam_path" || true)"
  if [[ -n "$model_nam_rel" && -f "$RUN_ROOT/$model_nam_rel" ]]; then
    expected_wel="$(extract_expected_pkg_file "$RUN_ROOT/$model_nam_rel" "^WEL" || true)"
    expected_rch="$(extract_expected_pkg_file "$RUN_ROOT/$model_nam_rel" "^(RCH|RCHA)" || true)"
  else
    expected_wel=""
    expected_rch=""
  fi

  if [[ -n "$WEL_URL" ]]; then
    wel_target="${expected_wel:-$(url_decoded_basename "$WEL_URL")}"
    log "Downloading WEL URL as $wel_target"
    download_url_to "$WEL_URL" "$RUN_ROOT/$wel_target"
  fi
  if [[ -n "$RCH_URL" ]]; then
    rch_target="${expected_rch:-$(url_decoded_basename "$RCH_URL")}"
    log "Downloading RCH URL as $rch_target"
    download_url_to "$RCH_URL" "$RUN_ROOT/$rch_target"
  fi
fi

if [[ -f "$SIM_ARCHIVE" ]]; then
  log "Unpacking simulation.zip into $RUN_ROOT"
  unzip -q "$SIM_ARCHIVE" -d "$RUN_ROOT"
else
  if [[ ${#other_archives[@]} -gt 0 ]]; then
    log "simulation.zip not found; unpacking ${other_archives[0]} into $RUN_ROOT"
    unzip -q "${other_archives[0]}" -d "$RUN_ROOT"
  else
    if [[ "$USE_INPUT_ROOT" == "false" ]]; then
      log "Copying inputs from $INPUTS_DIR into $RUN_ROOT"
      cp -a "${INPUTS_DIR}/." "$RUN_ROOT/" 2>/dev/null || true
    else
      log "Using inputs directly from $INPUTS_DIR"
    fi
  fi
fi

resolve_default_data_dir

stage_required_files "mfsim.nam" "gma14.nam"
stage_required_files "override_wel.pkg" "override_rch.pkg"

SIM_DIR="$RUN_ROOT"
if [[ ! -f "$SIM_DIR/mfsim.nam" ]]; then
  # Allow nested directory structures and skip noise like __MACOSX.
  mapfile -t sim_matches < <(find "$RUN_ROOT" -type f -name 'mfsim.nam' -print 2>/dev/null)
  if [[ ${#sim_matches[@]} -gt 0 ]]; then
    SIM_DIR="$(dirname "${sim_matches[0]}")"
  fi
fi

if [[ ! -f "$SIM_DIR/mfsim.nam" ]]; then
  echo "Unable to locate mfsim.nam in the provided inputs or default directory." >&2
  exit 1
fi

stage_required_files \
  "array_data" \
  "gma14.dis" \
  "gma14.ic" \
  "gma14.oc" \
  "gma14.npf" \
  "gma14.drn" \
  "gma14.riv" \
  "gma14.ghb" \
  "gma14.wel" \
  "gma14.irr" \
  "gma14.tdis" \
  "gma14.ims" \
  "gma14_rch_oc.rcha" \
  "gma14_rch_sc.rcha" \
  "gma14.csub" \
  "gma14.sto" \
  "gma14.obs" \
  "gma14.csub.obs"

# Apply local uploaded override package files, if provided.
model_nam_rel="$(extract_mf6_model_nam_from_mfsim "$SIM_DIR/mfsim.nam" || true)"
if [[ -n "$model_nam_rel" && -f "$SIM_DIR/$model_nam_rel" ]]; then
  expected_wel="$(extract_expected_pkg_file "$SIM_DIR/$model_nam_rel" "^WEL" || true)"
  expected_rch="$(extract_expected_pkg_file "$SIM_DIR/$model_nam_rel" "^(RCH|RCHA)" || true)"
else
  expected_wel=""
  expected_rch=""
fi

if [[ -f "$RUN_ROOT/override_wel.pkg" ]]; then
  wel_target="${expected_wel:-gma14.wel}"
  log "Applying uploaded WEL override as $wel_target"
  cp -f "$RUN_ROOT/override_wel.pkg" "$SIM_DIR/$wel_target"
fi
if [[ -f "$RUN_ROOT/override_rch.pkg" ]]; then
  rch_target="${expected_rch:-gma14_rch_oc.rcha}"
  log "Applying uploaded RCH override as $rch_target"
  cp -f "$RUN_ROOT/override_rch.pkg" "$SIM_DIR/$rch_target"
fi

python modflow.py "$SIM_DIR/mfsim.nam"

log "Copying simulation results to $OUTPUTS_DIR"
cp -a "$SIM_DIR/." "$OUTPUTS_DIR/"

log "MODFLOW 6 run completed"
