# MODFLOW-USG App

This folder is reserved for the Tapis app that will run MODFLOW-USG models.

Current target models:
- `Carrizo-Wilcox-central/gmv-modflow-usg-Modified`

Why this is separate from `modflow6`:
- MODFLOW-USG uses classic `*.nam` name files rather than MF6 `mfsim.nam`.
- The model package set is different from MF6 and includes USG-specific inputs such as `*.sms` and `*.gnc`.
- The container will need a MODFLOW-USG executable instead of the `mf6` binary used by the `modflow6` app.

Planned contents:
- `app.json` for the MODFLOW-USG Tapis app
- `Dockerfile` that installs the MODFLOW-USG executable
- `run.sh` that stages inputs and launches the USG engine
- optional FloPy helpers where they improve staging or validation

Implementation notes:
- `gma12.nam` and `gma12.mod.nam` should be treated as engine-native entrypoints, not converted to MF6.
- The current `modflow6` `resolve_sim_nam.py` logic should not be copied directly because it is specific to MF6 simulation assembly.
