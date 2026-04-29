#!/bin/bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Cookbook/runtime bootstrap configuration.
# -----------------------------------------------------------------------------
export GIT_REPO_URL="https://github.com/wmobley/modflow6"
export COOKBOOK_NAME="FloPy"
export COOKBOOK_CONDA_ENV="flopy"
export GIT_BRANCH="${GIT_BRANCH:-main}"
export DOWNLOAD_LATEST_VERSION="${DOWNLOAD_LATEST_VERSION:-false}"
IS_GPU_JOB=false

# -----------------------------------------------------------------------------
# Cookbook/runtime bootstrap helpers.
# These functions prepare the FloPy runtime used by the Tapis job.
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# MODFLOW 6 job assembly configuration.
# These variables control where staged inputs, defaults, and outputs live.
# -----------------------------------------------------------------------------
function log() {
	printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

function copy_tree_contents() {
	local source_dir="$1"
	local target_dir="$2"

	mkdir -p "$target_dir"
	cp -RL "$source_dir/." "$target_dir/"
}

INPUTS_DIR="${_tapisExecSystemInputDir:-/tapis/input}"
OUTPUTS_DIR="${_tapisExecSystemOutputDir:-/tapis/output}"
RUN_ROOT="$PWD/run"
DEFAULT_DATA_DIR=""
DEFAULT_STAGE_DIR="$RUN_ROOT/default_data"
DEFAULT_DATA_DIR_ARG=""

# -----------------------------------------------------------------------------
# Argument parsing and input staging.
# -----------------------------------------------------------------------------
function parse_args() {
	local arg

	DEFAULT_DATA_DIR_ARG=""
	for arg in "$@"; do
		case "$arg" in
			""|"__NONE__"|"NONE"|"none"|"null"|"NULL")
				;;
			*)
				if [[ -z "$DEFAULT_DATA_DIR_ARG" ]]; then
					DEFAULT_DATA_DIR_ARG="$arg"
				fi
				;;
		esac
	done
}

function resolve_default_data_dir() {
	local configured_dir="${DEFAULT_DATA_DIR_ARG:-}"

	if [[ -n "$configured_dir" ]]; then
		if [[ -d "$configured_dir" ]]; then
			DEFAULT_DATA_DIR="$configured_dir"
			log "Using default data directory from app arg: $DEFAULT_DATA_DIR"
			return
		fi
		log "Configured default data directory does not exist: $configured_dir"
	fi

	if [[ -d "$RUN_ROOT/default_data" ]]; then
		DEFAULT_DATA_DIR="$RUN_ROOT/default_data"
	elif [[ -d "$INPUTS_DIR/default_data" ]]; then
		DEFAULT_DATA_DIR="$INPUTS_DIR/default_data"
	fi
}

# Copy any configured baseline directory into a separate tree so uploaded files
# can override it without mutating the original source directory.
function stage_default_data_dir() {
	if [[ -z "$DEFAULT_DATA_DIR" ]]; then
		return
	fi
	mkdir -p "$DEFAULT_STAGE_DIR"
	log "Staging baseline MF6 files from $DEFAULT_DATA_DIR into $DEFAULT_STAGE_DIR"
	copy_tree_contents "$DEFAULT_DATA_DIR" "$DEFAULT_STAGE_DIR"
}

# Stage the uploaded inputs into a clean working directory and expand ZIP files
# so all subsequent resolution can work from a single filesystem tree.
function stage_user_inputs() {
	local sim_archive="$INPUTS_DIR/simulation.zip"
	local archive

	rm -rf "$RUN_ROOT"
	mkdir -p "$RUN_ROOT"

	if [[ -d "$INPUTS_DIR" ]]; then
		log "Copying staged inputs from $INPUTS_DIR into $RUN_ROOT"
		copy_tree_contents "$INPUTS_DIR" "$RUN_ROOT" 2>/dev/null || true
	fi

	if [[ -f "$sim_archive" ]]; then
		log "Unpacking simulation.zip into $RUN_ROOT"
		unzip -q "$sim_archive" -d "$RUN_ROOT"
	else
		shopt -s nullglob
		local archives=("$INPUTS_DIR"/*.zip)
		shopt -u nullglob
		for archive in "${archives[@]}"; do
			if [[ "$archive" == "$sim_archive" ]]; then
				continue
			fi
			log "Unpacking $(basename "$archive") into $RUN_ROOT"
			unzip -q "$archive" -d "$RUN_ROOT"
		done
	fi
}

# -----------------------------------------------------------------------------
# MODFLOW 6 name-file resolution.
# This shell wrapper delegates MF6 file discovery and name-file generation to a
# dedicated Python script so run.sh remains focused on orchestration.
# -----------------------------------------------------------------------------
function resolve_sim_nam_path() {
	python3 resolve_sim_nam.py "$RUN_ROOT"
}

# -----------------------------------------------------------------------------
# High-level workflow helpers.
# -----------------------------------------------------------------------------
function prepare_runtime_environment() {
	install_conda
	export_repo_variables
	ensure_git
	init_directory
	handle_installation
}

function prepare_run() {
	mkdir -p "$OUTPUTS_DIR"
	stage_user_inputs
	resolve_default_data_dir
	stage_default_data_dir
}

# Resolve the simulation entrypoint and launch the MODFLOW 6 executable.
function run_modflow_simulation() {
	local sim_nam_path

	log "Resolving MODFLOW 6 simulation name file from staged inputs"
	sim_nam_path="$(resolve_sim_nam_path)"
	log "Using simulation name file: $sim_nam_path"

	python modflow.py "$sim_nam_path"
}

# Persist the entire run directory so generated name files and model outputs
# are available in the archived Tapis job results.
function archive_results() {
	log "Copying simulation results to $OUTPUTS_DIR"
	copy_tree_contents "$RUN_ROOT" "$OUTPUTS_DIR"
}

function main() {
	prepare_runtime_environment
	parse_args "$@"
	prepare_run
	run_modflow_simulation
	archive_results
	log "MODFLOW 6 run completed"
}

main "$@"
