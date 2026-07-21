#!/bin/bash
set -euo pipefail

# -----------------------------------------------------------------------------
# MODFLOW-USG job assembly configuration.
# -----------------------------------------------------------------------------
INPUTS_DIR="${_tapisExecSystemInputDir:-/tapis/input}"
OUTPUTS_DIR="${_tapisExecSystemOutputDir:-/tapis/output}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ROOT="$PWD/run"
SCRATCH_DIR="$PWD/scratch"
DEFAULT_DATA_DIR=""
DEFAULT_STAGE_DIR="$RUN_ROOT/default_data"
DEFAULT_DATA_DIR_ARG=""
ARCHIVE_URL_ARG=""
ARCHIVE_DOWNLOAD_MAX_BYTES=$((8 * 1024 * 1024 * 1024))  # 8 GiB, matches validate_archive.py cap

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
function normalize_arg() {
	case "${1:-}" in
		""|"__NONE__"|"NONE"|"none"|"null"|"NULL")
			printf ''
			;;
		*)
			printf '%s' "$1"
			;;
	esac
}

function parse_args() {
	# Positional app args, per app.json's parameterSet.appArgs order:
	#   $1 = mfusgDefaultDir  (baseline directory path, existing)
	#   $2 = mfusgArchiveUrl  (optional https URL to a model zip/7z, new)
	DEFAULT_DATA_DIR_ARG="$(normalize_arg "${1:-}")"
	ARCHIVE_URL_ARG="$(normalize_arg "${2:-}")"
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
	log "Staging baseline MODFLOW-USG files from $DEFAULT_DATA_DIR into $DEFAULT_STAGE_DIR"
	copy_tree_contents "$DEFAULT_DATA_DIR" "$DEFAULT_STAGE_DIR"
}

function resolve_extracted_root() {
	# If a directory's only content is a single subdirectory, descend into it
	# (repeatedly) and return that as the effective root. Handles archives that
	# wrap their real content in one or more outer folders instead of the
	# files directly at the archive root, which would otherwise break relative
	# file references between sibling packages.
	local current="$1"
	local item
	local entry_count
	local only_entry

	while true; do
		entry_count=0
		only_entry=""
		shopt -s nullglob dotglob
		for item in "$current"/*; do
			entry_count=$((entry_count + 1))
			only_entry="$item"
		done
		shopt -u nullglob dotglob

		if ((entry_count == 1)) && [[ -d "$only_entry" ]]; then
			current="$only_entry"
			continue
		fi
		break
	done

	printf '%s' "$current"
}

function safe_extract() {
	local archive_path="$1"
	local dest_dir="$2"
	local label="${3:-$(basename "$archive_path")}"
	local stage_dir
	local archive_format
	local content_root

	log "Validating $label before extraction"
	if ! archive_format="$(python3 "$SCRIPT_DIR/validate_archive.py" "$archive_path")"; then
		log "ERROR: $label failed safety validation (unsafe path or oversized archive); refusing to extract"
		return 1
	fi

	mkdir -p "$SCRATCH_DIR"
	stage_dir="$(mktemp -d "$SCRATCH_DIR/extract.XXXXXX")"

	log "Unpacking $label ($archive_format) into a staging area for inspection"
	case "$archive_format" in
		zip)
			unzip -q "$archive_path" -d "$stage_dir"
			;;
		7z)
			7z x -y -o"$stage_dir" "$archive_path" > /dev/null
			;;
		*)
			log "ERROR: unrecognized archive format for $label"
			rm -rf "$stage_dir"
			return 1
			;;
	esac

	if find "$stage_dir" -type l -print -quit | grep -q .; then
		log "ERROR: $label contains a symlink entry after extraction; refusing to stage it"
		rm -rf "$stage_dir"
		return 1
	fi

	content_root="$(resolve_extracted_root "$stage_dir")"
	if [[ "$content_root" != "$stage_dir" ]]; then
		log "Unwrapping outer folder(s) in $label; using $(basename "$content_root") as content root"
	fi

	log "Staging validated contents of $label into $dest_dir"
	mkdir -p "$dest_dir"
	cp -RP "$content_root/." "$dest_dir/"
	rm -rf "$stage_dir"
}

function fetch_archive_from_url() {
	local url="$ARCHIVE_URL_ARG"
	local dest="$SCRATCH_DIR/mfusg_archive_download"

	if [[ -z "$url" ]]; then
		return 0
	fi

	if [[ ! "$url" =~ ^https://[A-Za-z0-9.-]+(/.*)?$ ]]; then
		log "ERROR: mfusgArchiveUrl rejected — only https:// URLs are supported ($url)"
		return 1
	fi

	mkdir -p "$SCRATCH_DIR"
	rm -f "$dest"
	log "Downloading model archive from $url into $SCRATCH_DIR"
	if ! curl -fsSL --max-filesize "$ARCHIVE_DOWNLOAD_MAX_BYTES" --max-time 1800 -o "$dest" "$url"; then
		log "ERROR: failed to download archive from $url"
		rm -f "$dest"
		return 1
	fi

	if ! safe_extract "$dest" "$RUN_ROOT" "archive downloaded from $url"; then
		rm -f "$dest"
		return 1
	fi

	rm -f "$dest"
}

function stage_user_inputs() {
	local sim_archive="$INPUTS_DIR/simulation.zip"
	local archive
	local archives=()

	rm -rf "$RUN_ROOT"
	mkdir -p "$RUN_ROOT"

	fetch_archive_from_url

	if [[ -d "$INPUTS_DIR" ]]; then
		log "Copying staged inputs from $INPUTS_DIR into $RUN_ROOT"
		copy_staged_inputs "$INPUTS_DIR" "$RUN_ROOT"
	fi

	if [[ -f "$sim_archive" ]]; then
		safe_extract "$sim_archive" "$RUN_ROOT" "simulation.zip"
	else
		shopt -s nullglob
		archives=("$INPUTS_DIR"/*.zip)
		shopt -u nullglob
		if ((${#archives[@]} > 0)); then
			for archive in "${archives[@]}"; do
				if [[ "$archive" == "$sim_archive" ]]; then
					continue
				fi
				safe_extract "$archive" "$RUN_ROOT" "$(basename "$archive")"
			done
		fi
	fi
}

# -----------------------------------------------------------------------------
# MODFLOW-USG name-file resolution.
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

	log "Resolving MODFLOW-USG name file from staged inputs"
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
	log "MODFLOW-USG run completed"
}

main "$@"
