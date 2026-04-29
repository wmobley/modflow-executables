# MODFLOW-96 App

This folder contains the Tapis app that runs legacy MODFLOW-96-style models.

Current target models:
- `Trinity_hill_country/Trinity_hill_country_model_only/modfl_96/ststate`
- `Trinity_hill_country/Trinity_hill_country_model_only/modfl_96/trans`

Why this is separate from newer MODFLOW apps:
- These inputs predate MODFLOW 2000/2005/MF6 conventions.
- The model entrypoints and executable requirements are engine-specific.
- Legacy naming and package conventions make direct reuse of the `modflow6` app inappropriate.

Contents:
- `app.json` for the MODFLOW-96 Tapis app
- `Dockerfile` that builds MODFLOW-96 from the official USGS source archive
- `run.sh` that stages inputs, generates or resolves the legacy `*.nam` file, and launches the model

Implementation notes:
- `trnt_h_ss.nam` appears in multiple legacy layouts and should be resolved relative to the selected run directory.
- The Docker image installs the compiled MODFLOW-96 executable as `mf96`.
