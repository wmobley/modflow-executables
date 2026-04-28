# MODFLOW-2000 App

This folder is reserved for the Tapis app that will run MODFLOW-2000 models.

Current target models:
- `Yequa_Jackson/Yegua_Jackson_Model_Only/CD-2_ygjk_model/Modflow_2000`

Why this is separate from `modflow6`:
- MODFLOW-2000 uses classic `*.nam` name files and package families such as `*.bcf`, `*.gmg`, and `*.str`.
- The container will need a MODFLOW-2000-compatible executable instead of the `mf6` binary used by the `modflow6` app.

Planned contents:
- `app.json` for the MODFLOW-2000 Tapis app
- `Dockerfile` that installs or bundles the MODFLOW-2000 executable
- `run.sh` that stages model inputs and runs the correct `*.nam` file
- optional FloPy-based helpers for validation and output handling

Implementation notes:
- `ygjk_tr.nam` is the likely primary entrypoint.
- This app should not inherit MF6-specific assumptions such as generated `mfsim.nam` files.
