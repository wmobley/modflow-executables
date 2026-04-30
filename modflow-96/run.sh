#!/bin/bash
set -euo pipefail

# -----------------------------------------------------------------------------
# MODFLOW-96 job assembly configuration.
# -----------------------------------------------------------------------------
INPUTS_DIR="${_tapisExecSystemInputDir:-/tapis/input}"
OUTPUTS_DIR="${_tapisExecSystemOutputDir:-/tapis/output}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ROOT="$PWD/run"
DEFAULT_DATA_DIR=""
DEFAULT_STAGE_DIR="$RUN_ROOT/default_data"
DEFAULT_DATA_DIR_ARG=""

function log() {
	printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

function copy_tree_contents() {
	local source_dir="$1"
	local target_dir="$2"

	mkdir -p "$target_dir"
	cp -RL "$source_dir/." "$target_dir/"
}

function copy_staged_inputs() {
	local source_dir="$1"
	local target_dir="$2"
	local item
	local item_name

	mkdir -p "$target_dir"
	shopt -s nullglob dotglob
	for item in "$source_dir"/*; do
		item_name="$(basename "$item")"
		case "$item_name" in
			run|output|work|home|scratch)
				continue
				;;
		esac
		cp -RL "$item" "$target_dir/"
	done
	shopt -u nullglob dotglob
}

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

function flatten_support_inputs() {
	local support_dir="$RUN_ROOT/support"
	local support_item

	if [[ ! -d "$support_dir" ]]; then
		return
	fi

	shopt -s nullglob dotglob
	for support_item in "$support_dir"/*; do
		mv "$support_item" "$RUN_ROOT/"
	done
	shopt -u nullglob dotglob
	rmdir "$support_dir" 2>/dev/null || true
}

function stage_default_data_dir() {
	if [[ -z "$DEFAULT_DATA_DIR" ]]; then
		return
	fi
	log "Overlaying baseline files from $DEFAULT_DATA_DIR into $RUN_ROOT"
	copy_tree_contents "$DEFAULT_DATA_DIR" "$RUN_ROOT"
	mkdir -p "$DEFAULT_STAGE_DIR"
	log "Staging baseline MODFLOW-96 files from $DEFAULT_DATA_DIR into $DEFAULT_STAGE_DIR"
	copy_tree_contents "$DEFAULT_DATA_DIR" "$DEFAULT_STAGE_DIR"
}

function stage_user_inputs() {
	local sim_archive="$INPUTS_DIR/simulation.zip"
	local archive
	local archives=()

	rm -rf "$RUN_ROOT"
	mkdir -p "$RUN_ROOT"

	if [[ -d "$INPUTS_DIR" ]]; then
		log "Copying staged inputs from $INPUTS_DIR into $RUN_ROOT"
		copy_staged_inputs "$INPUTS_DIR" "$RUN_ROOT"
	fi

	if [[ -f "$sim_archive" ]]; then
		log "Unpacking simulation.zip into $RUN_ROOT"
		unzip -q "$sim_archive" -d "$RUN_ROOT"
	else
		shopt -s nullglob
		archives=("$INPUTS_DIR"/*.zip)
		shopt -u nullglob
		if ((${#archives[@]} > 0)); then
			for archive in "${archives[@]}"; do
				if [[ "$archive" == "$sim_archive" ]]; then
					continue
				fi
				log "Unpacking $(basename "$archive") into $RUN_ROOT"
				unzip -q "$archive" -d "$RUN_ROOT"
			done
		fi
	fi
}

# -----------------------------------------------------------------------------
# MODFLOW-96 name-file resolution.
# -----------------------------------------------------------------------------
function resolve_sim_nam_path() {
	python3 "$SCRIPT_DIR/resolve_nam.py" "$RUN_ROOT"
}

# -----------------------------------------------------------------------------
# High-level workflow helpers.
# -----------------------------------------------------------------------------
function prepare_run() {
	mkdir -p "$OUTPUTS_DIR"
	stage_user_inputs
	resolve_default_data_dir
	stage_default_data_dir
	copy_staged_inputs "$INPUTS_DIR" "$RUN_ROOT"
	flatten_support_inputs
}

function run_modflow_simulation() {
	local sim_nam_path

	log "Resolving MODFLOW-96 name file from staged inputs"
	sim_nam_path="$(resolve_sim_nam_path)"
	log "Using name file: $sim_nam_path"

	python3 "$SCRIPT_DIR/modflow.py" "$sim_nam_path"
}

function archive_results() {
	log "Copying simulation results to $OUTPUTS_DIR"
	copy_tree_contents "$RUN_ROOT" "$OUTPUTS_DIR"
}

function main() {
	parse_args "$@"
	prepare_run
	run_modflow_simulation
	archive_results
	log "MODFLOW-96 run completed"
}

main "$@"
