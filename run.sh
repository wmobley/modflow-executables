#!/bin/bash
set -euo pipefail

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

INPUTS_DIR="${_tapisExecSystemInputDir:-/tapis/input}"
OUTPUTS_DIR="${_tapisExecSystemOutputDir:-/tapis/output}"
RUN_ROOT="$PWD/run"

rm -rf "$RUN_ROOT"
mkdir -p "$RUN_ROOT" "$OUTPUTS_DIR"

shopt -s nullglob
zip_inputs=("$INPUTS_DIR"/*.zip)
shopt -u nullglob

if [[ ${#zip_inputs[@]} -gt 0 ]]; then
  log "Unpacking ${zip_inputs[0]} into $RUN_ROOT"
  unzip -q "${zip_inputs[0]}" -d "$RUN_ROOT"
else
  log "Copying inputs from $INPUTS_DIR into $RUN_ROOT"
  cp -a "${INPUTS_DIR}/." "$RUN_ROOT/" 2>/dev/null || true
fi

SIM_DIR="$RUN_ROOT"
if [[ ! -f "$SIM_DIR/mfsim.nam" ]]; then
  candidate=$(find "$RUN_ROOT" -mindepth 1 -maxdepth 1 -type d -print -quit)
  if [[ -n "$candidate" && -f "$candidate/mfsim.nam" ]]; then
    SIM_DIR="$candidate"
  fi
fi

if [[ ! -f "$SIM_DIR/mfsim.nam" ]]; then
  echo "Unable to locate mfsim.nam in the provided inputs." >&2
  exit 1
fi

log "Running MODFLOW 6 in $SIM_DIR"
pushd "$SIM_DIR" >/dev/null
if command -v tee >/dev/null 2>&1; then
  mf6 2>&1 | tee "$OUTPUTS_DIR/mf6.stdout"
else
  mf6 >"$OUTPUTS_DIR/mf6.stdout" 2>&1
fi
popd >/dev/null

log "Copying simulation results to $OUTPUTS_DIR"
cp -a "$SIM_DIR/." "$OUTPUTS_DIR/"

log "MODFLOW 6 run completed"
