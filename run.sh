#!/bin/bash
set -euo pipefail


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

INPUTS_DIR="${_tapisExecSystemInputDir:-/tapis/input}"
OUTPUTS_DIR="${_tapisExecSystemOutputDir:-/tapis/output}"
RUN_ROOT="$PWD/run"

create_mfsim_nam() {
  local nam_path="$RUN_ROOT/mfsim.nam"
  if [[ -f "$nam_path" ]]; then
    return 0
  fi

  local packages=()
  local file

  add_package_if_present() {
    local line="$1"
    local filename="$2"
    if [[ -f "$RUN_ROOT/$filename" ]]; then
      packages+=("$line")
      return 0
    fi
    if [[ -f "$INPUTS_DIR/$filename" ]]; then
      cp -a "$INPUTS_DIR/$filename" "$RUN_ROOT/"
      packages+=("$line")
      return 0
    fi
    return 1
  }

  add_package_if_present "  DIS6  gma14.dis  dis" "gma14.dis"
  add_package_if_present "  IC6  gma14.ic  ic" "gma14.ic"
  add_package_if_present "  OC6  gma14.oc  oc" "gma14.oc"
  add_package_if_present "  NPF6  gma14.npf  npf" "gma14.npf"
  add_package_if_present "  DRN6  gma14.drn  drn_0" "gma14.drn"
  add_package_if_present "  RIV6  gma14.riv  riv_0" "gma14.riv"
  add_package_if_present "  GHB6  gma14.ghb  ghb_0" "gma14.ghb"
  add_package_if_present "  WEL6  gma14.wel  wel_0" "gma14.wel"
  add_package_if_present "  WEL6  gma14.irr  irr" "gma14.irr"
  add_package_if_present "  RCH6  gma14_rch_oc.rcha  rch_oc" "gma14_rch_oc.rcha"
  add_package_if_present "  RCH6  gma14_rch_sc.rcha  rch_sc" "gma14_rch_sc.rcha"
  add_package_if_present "  CSUB6 gma14.csub  csub" "gma14.csub"
  add_package_if_present "  STO6  gma14.sto  sto" "gma14.sto"
  add_package_if_present "  OBS6  gma14.obs  obs" "gma14.obs"

  {
    cat <<'EOF'
BEGIN packages
EOF
    if [[ ${#packages[@]} -gt 0 ]]; then
      printf '%s\n' "${packages[@]}"
    fi
    cat <<'EOF'
END packages
EOF
  } >"$nam_path"
}

rm -rf "$RUN_ROOT"
mkdir -p "$RUN_ROOT" "$OUTPUTS_DIR"

SIM_ARCHIVE="$INPUTS_DIR/simulation.zip"

if [[ -f "$SIM_ARCHIVE" ]]; then
  log "Unpacking simulation.zip into $RUN_ROOT"
  unzip -q "$SIM_ARCHIVE" -d "$RUN_ROOT"
else
  shopt -s nullglob
  other_archives=("$INPUTS_DIR"/*.zip)
  shopt -u nullglob

  if [[ ${#other_archives[@]} -gt 0 ]]; then
    log "simulation.zip not found; unpacking ${other_archives[0]} into $RUN_ROOT"
    unzip -q "${other_archives[0]}" -d "$RUN_ROOT"
  else
    log "Copying inputs from $INPUTS_DIR into $RUN_ROOT"
    cp -a "${INPUTS_DIR}/." "$RUN_ROOT/" 2>/dev/null || true
  fi
fi

SIM_DIR="$RUN_ROOT"
if [[ ! -f "$SIM_DIR/mfsim.nam" ]]; then
  # Allow nested directory structures and skip noise like __MACOSX.
  mapfile -t sim_matches < <(find "$RUN_ROOT" -type f -name 'mfsim.nam' -print 2>/dev/null)
  if [[ ${#sim_matches[@]} -gt 0 ]]; then
    SIM_DIR="$(dirname "${sim_matches[0]}")"
  fi
fi

if [[ ! -f "$SIM_DIR/mfsim.nam" ]]; then
  if ! create_mfsim_nam; then
    echo "Unable to locate or create mfsim.nam in the provided inputs." >&2
    exit 1
  fi
  SIM_DIR="$RUN_ROOT"
fi

python modflow.py "$SIM_DIR/mfsim.nam"

log "Copying simulation results to $OUTPUTS_DIR"
cp -a "$SIM_DIR/." "$OUTPUTS_DIR/"

log "MODFLOW 6 run completed"
