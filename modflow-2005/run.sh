#!/usr/bin/env bash
set -euo pipefail

INPUTS_DIR="${_tapisExecSystemInputDir:-/tapis/input}"
OUTPUTS_DIR="${_tapisExecSystemOutputDir:-/tapis/output}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ROOT="$PWD/run"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

copy_inputs() {
    local item name
    mkdir -p "$2"
    shopt -s nullglob dotglob
    for item in "$1"/*; do
        name="$(basename "$item")"
        case "$name" in simulation.zip|*.zip|*.zipx|*.7z|run|output|work|home|scratch) continue ;; esac
        if [[ -L "$item" ]] || find "$item" -type l -print -quit | grep -q .; then
            log "ERROR: staged input contains a symlink: $item"
            return 1
        fi
        cp -RP "$item" "$2/"
    done
    shopt -u nullglob dotglob
}

extract_archive() {
    local archive="$1" destination="$2" stage
    python3 "$SCRIPT_DIR/validate_archive.py" "$archive" >/dev/null
    stage="$(mktemp -d "$PWD/mf2005-extract.XXXXXX")"
    if ! unzip -q "$archive" -d "$stage"; then
        rm -rf "$stage"
        mkdir -p "$stage"
        7z x -y -o"$stage" "$archive" >/dev/null
    fi
    if find "$stage" -type l -print -quit | grep -q .; then
        rm -rf "$stage"
        log "ERROR: archive contains symlinks"
        return 1
    fi
    local current="$stage" item count only
    while true; do
        count=0; only=""
        shopt -s nullglob dotglob
        for item in "$current"/*; do count=$((count + 1)); only="$item"; done
        shopt -u nullglob dotglob
        if [[ "$count" == 1 && -d "$only" ]]; then current="$only"; else break; fi
    done
    mkdir -p "$destination"
    cp -RP "$current/." "$destination/"
    rm -rf "$stage"
}

prepare() {
    local archive="$INPUTS_DIR/simulation.zip"
    rm -rf "$RUN_ROOT"
    mkdir -p "$RUN_ROOT" "$OUTPUTS_DIR"
    if [[ ! -f "$archive" ]]; then
        log "ERROR: required MODFLOW-2005 input archive simulation.zip is missing"
        return 1
    fi
    log "Extracting MODFLOW-2005 simulation archive"
    extract_archive "$archive" "$RUN_ROOT"
    copy_inputs "$INPUTS_DIR" "$RUN_ROOT"
}

main() {
    prepare
    local nam
    nam="$(python3 "$SCRIPT_DIR/resolve_nam.py" "$RUN_ROOT")"
    log "Using MODFLOW-2005 name file: $nam"
    python3 "$SCRIPT_DIR/modflow.py" "$nam"
    cp -RP "$RUN_ROOT/." "$OUTPUTS_DIR/"
    log "MODFLOW-2005 run completed"
}

main "$@"
