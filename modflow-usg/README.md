# MODFLOW-USG App

This folder contains the Tapis app that runs MODFLOW-USG models.

Current target models:
- `Carrizo-Wilcox-central/gmv-modflow-usg-Modified`

Why this is separate from `modflow6`:
- MODFLOW-USG uses classic `*.nam` name files rather than MF6 `mfsim.nam`.
- The model package set is different from MF6 and includes USG-specific inputs such as `*.sms` and `*.gnc`.
- The container will need a MODFLOW-USG executable instead of the `mf6` binary used by the `modflow6` app.

Runtime contract:
- `simulation.zip` is required and is unpacked before execution.
- Optional `provided/model.wel` and `provided/model.rch` inputs override the
  corresponding package files from the archive or baseline.
- The runner resolves or generates a classic `*.nam` file before launching
  `mfusg`.

Implementation notes:
- `gma12.nam` and `gma12.mod.nam` should be treated as engine-native entrypoints, not converted to MF6.
- The current `modflow6` `resolve_sim_nam.py` logic should not be copied directly because it is specific to MF6 simulation assembly.
