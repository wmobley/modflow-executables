# MODFLOW-2000 App

This folder contains the Tapis app that runs MODFLOW-2000 models.

Current target models:
- `Yequa_Jackson/Yegua_Jackson_Model_Only/CD-2_ygjk_model/Modflow_2000`

Why this is separate from `modflow6`:
- MODFLOW-2000 uses classic `*.nam` name files and package families such as `*.bcf`, `*.gmg`, and `*.str`.
- The container will need a MODFLOW-2000-compatible executable instead of the `mf6` binary used by the `modflow6` app.

Runtime contract:
- `simulation.zip` is required and is unpacked before execution.
- Optional `provided/model.wel` and `provided/model.rch` inputs override the
  corresponding package files from the archive or baseline.
- The runner resolves or generates a classic `*.nam` file before launching
  `mf2000`.

Implementation notes:
- `ygjk_tr.nam` is the likely primary entrypoint.
- This app should not inherit MF6-specific assumptions such as generated `mfsim.nam` files.
