#!/bin/bash
set -euo pipefail

# -----------------------------------------------------------------------------
# MODFLOW 6 job assembly configuration.
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
	#   $1 = mf6DefaultDir  (baseline directory path, existing)
	#   $2 = mf6ArchiveUrl  (optional https URL to a model zip, new)
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
	local staged_item

	if [[ ! -d "$support_dir" ]]; then
		return
	fi

	shopt -s nullglob dotglob
	for support_item in "$support_dir"/*; do
		if [[ -d "$support_item" ]]; then
			for staged_item in "$support_item"/*; do
				cp -RL "$staged_item" "$RUN_ROOT/"
			done
		else
			cp -RL "$support_item" "$RUN_ROOT/"
		fi
	done
	shopt -u nullglob dotglob
	if [[ ! -e "$RUN_ROOT/array_data" && -f "$RUN_ROOT/delr.txt" && -f "$RUN_ROOT/delc.txt" ]]; then
		mkdir -p "$RUN_ROOT/array_data"
		cp -RL "$RUN_ROOT/delr.txt" "$RUN_ROOT/array_data/delr.txt"
		cp -RL "$RUN_ROOT/delc.txt" "$RUN_ROOT/array_data/delc.txt"
		log "Created array_data/ compatibility copies from staged delr.txt and delc.txt"
	fi
	rm -rf "$support_dir"
}

function normalize_array_data_layout() {
	local provided_array_dir="$RUN_ROOT/provided/array_data"
	local root_array_dir="$RUN_ROOT/array_data"
	local root_txt
	local target_txt
	local copied_txt_count=0

	if [[ -d "$provided_array_dir" && ! -e "$root_array_dir" ]]; then
		cp -RL "$provided_array_dir" "$root_array_dir"
		log "Promoted provided/array_data to array_data for MF6 external array resolution"
	fi

	if [[ -d "$root_array_dir" ]]; then
		shopt -s nullglob
		for root_txt in "$RUN_ROOT"/*.txt; do
			target_txt="$root_array_dir/$(basename "$root_txt")"
			if [[ -e "$target_txt" ]]; then
				continue
			fi
			cp -RL "$root_txt" "$target_txt"
			copied_txt_count=$((copied_txt_count + 1))
		done
		shopt -u nullglob
		if ((copied_txt_count > 0)); then
			log "Populated array_data with $copied_txt_count staged root-level .txt file(s) for MF6 path compatibility"
		fi
	fi
}

function normalize_support_slot_filenames() {
	local slot_path
	local expected_name

	for slot_path in "$RUN_ROOT"/support-[0-9][0-9]; do
		[[ -f "$slot_path" ]] || continue
		case "$(basename "$slot_path")" in
			support-02)
				expected_name="$RUN_ROOT/gma14.irr"
				;;
			support-03)
				expected_name="$RUN_ROOT/gma14.csub.obs"
				;;
			*)
				expected_name=""
				;;
		esac

		if [[ -n "$expected_name" && ! -e "$expected_name" ]]; then
			cp -RL "$slot_path" "$expected_name"
			log "Mapped $(basename "$slot_path") to $(basename "$expected_name") for MF6 support-file compatibility"
		fi
	done
}

function stage_default_data_dir() {
	if [[ -z "$DEFAULT_DATA_DIR" ]]; then
		return
	fi
	log "Overlaying baseline files from $DEFAULT_DATA_DIR into $RUN_ROOT"
	copy_tree_contents "$DEFAULT_DATA_DIR" "$RUN_ROOT"
	mkdir -p "$DEFAULT_STAGE_DIR"
	log "Staging baseline MF6 files from $DEFAULT_DATA_DIR into $DEFAULT_STAGE_DIR"
	copy_tree_contents "$DEFAULT_DATA_DIR" "$DEFAULT_STAGE_DIR"
}

function resolve_extracted_root() {
	# If a directory's only content is a single subdirectory, descend into it
	# (repeatedly) and return that as the effective root. Handles archives that
	# wrap their real content in one or more outer folders (e.g. "Model/data/"
	# instead of the files directly at the archive root), which would otherwise
	# break MF6's relative OPEN/CLOSE file references between sibling packages.
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
	local dest="$SCRATCH_DIR/mf6_archive_download"

	if [[ -z "$url" ]]; then
		return 0
	fi

	if [[ ! "$url" =~ ^https://[A-Za-z0-9.-]+(/.*)?$ ]]; then
		log "ERROR: mf6ArchiveUrl rejected — only https:// URLs are supported ($url)"
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
# MODFLOW 6 name-file resolution.
# -----------------------------------------------------------------------------
function resolve_sim_nam_path() {
	python3 "$SCRIPT_DIR/resolve_sim_nam.py" "$RUN_ROOT"
}

function log_staged_inputs() {
	log "Staged MODFLOW 6 input files:"
	find "$RUN_ROOT" -maxdepth 3 -mindepth 1 -print | sort | sed "s#^$RUN_ROOT/#  #"
}

function log_dir_status() {
	local path="$1"
	local label="$2"
	local file_count

	if [[ -d "$path" ]]; then
		file_count="$(find "$path" -type f | wc -l | tr -d ' ')"
		log "Setup check: $label exists ($path, files=$file_count)"
	else
		log "Setup check: $label missing ($path)"
	fi
}

function log_file_status() {
	local path="$1"
	local label="$2"

	if [[ -f "$path" ]]; then
		log "Setup check: $label found ($path)"
	else
		log "Setup check: $label missing ($path)"
	fi
}

function log_external_reference_checks() {
	local package_file
	local line
	local token
	local token_upper
	local ref_path
	local capture_next=0
	local missing_count=0
	local missing_logged=0
	local missing_log_limit=60
	local ref_tmp
	local base_ref
	local base_path

	ref_tmp="$(mktemp)"

	while IFS= read -r package_file; do
		while IFS= read -r line; do
			if [[ "$line" == "OPEN/CLOSE "* || "$line" == "FILEIN "* || "$line" == *" OPEN/CLOSE "* || "$line" == *" FILEIN "* ]]; then
				capture_next=0
				for token in $line; do
					token_upper="$(printf '%s' "$token" | tr '[:lower:]' '[:upper:]')"
					case "$token_upper" in
						OPEN/CLOSE|FILEIN)
							capture_next=1
							continue
							;;
					esac
					if ((capture_next == 1)); then
						capture_next=0
						ref_path="$token"
						ref_path="${ref_path%\'}"
						ref_path="${ref_path#\'}"
						ref_path="${ref_path%\"}"
						ref_path="${ref_path#\"}"
						ref_path="${ref_path%,}"
						ref_path="${ref_path%)}"
						ref_path="${ref_path#(}"
						if [[ "$ref_path" == *"="* ]]; then
							ref_path="${ref_path#*=}"
						fi
						ref_path="${ref_path//$'\r'/}"
						ref_path="${ref_path%\'}"
						ref_path="${ref_path#\'}"
						ref_path="${ref_path%\"}"
						ref_path="${ref_path#\"}"
						if [[ -n "$ref_path" ]]; then
							echo "$package_file|$ref_path" >>"$ref_tmp"
						fi
					fi
				done
			fi
		done <"$package_file"
	done < <(find "$RUN_ROOT" -maxdepth 3 -type f \( -name "*.dis" -o -name "*.disv" -o -name "*.disu" -o -name "*.npf" -o -name "*.sto" -o -name "*.ic" -o -name "*.rcha" -o -name "*.rch" -o -name "*.wel" -o -name "*.drn" -o -name "*.riv" -o -name "*.ghb" -o -name "*.csub" -o -name "*.ims" -o -name "*.tdis" -o -name "*.nam" \) | sort)

	if [[ ! -s "$ref_tmp" ]]; then
		log "Setup check: no OPEN/CLOSE or FILEIN references detected in scanned package files"
		rm -f "$ref_tmp"
		return
	fi

	log "Setup check: validating referenced external paths from package files"
	while IFS="|" read -r package_file ref_path; do
		base_ref="$(basename "$ref_path")"
		if [[ "$base_ref" == "REPLACE" || "$base_ref" == "BINARY" ]]; then
			continue
		fi
		if [[ "$ref_path" == "REPLACE" || "$ref_path" == "BINARY" ]]; then
			continue
		fi

		base_path="$(dirname "$package_file")/$ref_path"
		if [[ -e "$RUN_ROOT/$ref_path" || -e "$base_path" ]]; then
			continue
		fi

		missing_count=$((missing_count + 1))
		if ((missing_logged < missing_log_limit)); then
			log "Setup check: missing external ref [$ref_path] (referenced by ${package_file#$RUN_ROOT/})"
			missing_logged=$((missing_logged + 1))
		fi
	done < <(sort -u "$ref_tmp")

	if ((missing_count == 0)); then
		log "Setup check: all detected external references resolved"
	else
		if ((missing_count > missing_logged)); then
			log "Setup check: additional missing refs not shown=$((missing_count - missing_logged))"
		fi
		log "Setup check: unresolved external references count=$missing_count"
	fi

	rm -f "$ref_tmp"
}

function log_setup_diagnostics() {
	log "Running MF6 setup diagnostics"
	log "Setup context: RUN_ROOT=$RUN_ROOT INPUTS_DIR=$INPUTS_DIR DEFAULT_DATA_DIR=${DEFAULT_DATA_DIR:-__NONE__}"
	log_dir_status "$RUN_ROOT/provided" "provided input directory"
	log_dir_status "$RUN_ROOT/array_data" "array_data directory"
	log_dir_status "$RUN_ROOT/default_data" "default_data directory"
	log_file_status "$RUN_ROOT/array_data/delr.txt" "array_data/delr.txt"
	log_file_status "$RUN_ROOT/array_data/delc.txt" "array_data/delc.txt"
	log_external_reference_checks
}

function log_solver_failure_summary() {
	local lst_file
	local has_lst=0
	local pattern='converg|failed|failure|diverg|maximum (outer|inner) iterations|did not converge|residual'

	log "MF6 exited with a non-zero status; collecting solver diagnostics"

	if [[ -f "$RUN_ROOT/mfsim.lst" ]]; then
		log "Tail of mfsim.lst (last 80 lines):"
		tail -n 80 "$RUN_ROOT/mfsim.lst" | sed 's/^/  /'
	fi

	shopt -s nullglob
	for lst_file in "$RUN_ROOT"/*.lst "$RUN_ROOT"/provided/*.lst; do
		has_lst=1
		if grep -Einq "$pattern" "$lst_file"; then
			log "Convergence-related lines from $(basename "$lst_file"):"
			grep -Ein "$pattern" "$lst_file" | tail -n 40 | sed 's/^/  /'
		fi
	done
	shopt -u nullglob

	if ((has_lst == 0)); then
		log "No .lst files were found to summarize solver diagnostics"
	fi
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
	normalize_support_slot_filenames
	normalize_array_data_layout
}

function run_modflow_simulation() {
	local sim_nam_path
	local rc=0

	log_staged_inputs
	log_setup_diagnostics
	log "Resolving MODFLOW 6 simulation name file from staged inputs"
	sim_nam_path="$(resolve_sim_nam_path)"
	log "Using simulation name file: $sim_nam_path"

	python3 "$SCRIPT_DIR/modflow.py" "$sim_nam_path" || rc=$?
	if ((rc != 0)); then
		log_solver_failure_summary
		return "$rc"
	fi
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
	log "MODFLOW 6 run completed"
}

main "$@"
