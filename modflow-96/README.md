# MODFLOW-96 App

This folder is reserved for the Tapis app that will run legacy MODFLOW-96-style models.

Current target models:
- `Trinity_hill_country/Trinity_hill_country_model_only/modfl_96/ststate`
- `Trinity_hill_country/Trinity_hill_country_model_only/modfl_96/trans`

Why this is separate from newer MODFLOW apps:
- These inputs predate MODFLOW 2000/2005/MF6 conventions.
- The model entrypoints and executable requirements are engine-specific.
- Legacy naming and package conventions make direct reuse of the `modflow6` app inappropriate.

Planned contents:
- `app.json` for the MODFLOW-96 Tapis app
- `Dockerfile` that installs or bundles the required legacy executable
- `run.sh` that stages the target directory and launches the legacy `*.nam` file

Implementation notes:
- `trnt_h_ss.nam` appears in multiple legacy layouts and should be resolved relative to the selected run directory.
- This app will likely require the most engine-specific handling of the group.
