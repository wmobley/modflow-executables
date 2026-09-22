# Memory

> Chronological action log. Hooks and AI append to this file automatically.
> Old sessions are consolidated by the daemon weekly.

## Session: 2026-07-20 13:58

| Time | Action | File(s) | Outcome | ~Tokens |
|------|--------|---------|---------|--------|

## Session: 2026-07-27 08:07

| Time | Action | File(s) | Outcome | ~Tokens |
|------|--------|---------|---------|--------|

## Session: 2026-07-27 08:09

| Time | Action | File(s) | Outcome | ~Tokens |
|------|--------|---------|---------|--------|
| 12:20 | Changed all four MODFLOW archive file inputs from OPTIONAL to REQUIRED and updated the registration regression test | modflow6/app.json, modflow-2000/app.json, modflow-96/app.json, modflow-usg/app.json, scripts/test_register_to_mint.py | Patch applied; tests pending | ~600 |
| 12:25 | Validated archive-required payloads and traced archive dataset discovery to missing resource-level CKAN SVO annotations | scripts/register_to_mint.py, scripts/register_gams_to_ckan.py, monorepo/docs/runbook-ckan-svo-dataset-registration.md | JSON validation, 12 unit tests, and dry-run passed; CKAN/MINT metadata work remains | ~900 |

| 19:44 | Added archive-first runtime coverage across all four MODFLOW apps, including MF6 RCHB support and a local fake-solver harness | modflow6, modflow-2000, modflow-96, modflow-usg, scripts/check_apps.py, scripts/test_app_runtime.py, docs/app-runtime.md | Initial local test exposed an incompatible host `/usr/local/bin/python3`; harness now uses the system Python path and all runtime/registration tests pass | ~1200 |
| 19:48 | Completed final validation of the app/runtime and registration changes | scripts/test_app_runtime.py, scripts/test_register_to_mint.py, scripts/register_to_mint.py, all four run.sh files | 16 unittest tests, registration dry-run, manifest JSON checks, shell syntax checks, and scoped diff checks passed; no remote jobs or registrations performed | ~500 |
