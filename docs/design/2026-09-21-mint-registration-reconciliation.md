# MODFLOW MINT registration reconciliation

Status: Implemented

## Objective

Make the MODFLOW registration workflow repair existing MINT configurations safely, register
MODFLOW-USG as a new configuration, correct the MODFLOW software label, and produce a clean
read-only dry-run before any live MINT write.

## User need

**Primary user:** MINT facilitators and groundwater modelers selecting executable MODFLOW
configurations and compatible GAM archives.

**Secondary users:** maintainers of the MODFLOW Tapis apps and MINT catalog operators.

**Job-to-be-done:** Select a MODFLOW executable whose Tapis app, archive baseline, optional WEL/RCH
overrides, and SVO presentations agree.

**Current pain:** Existing records contain stale app bindings, duplicate configurations, a wrong
software label, inconsistent required/optional inputs, and SVO identifiers that do not match the
live catalog convention.

**Definition of success:** The dry-run targets the known existing configuration IDs for repair,
uses the new app versions, creates only the missing MODFLOW-USG configuration, uses confirmed SVO
links, and makes no live mutation until separately approved.

## Current code/system summary

`scripts/register_to_mint.py` currently creates deterministic local IDs for all four variants and
uses POST followed by relationship PUTs. The `mintdevui` catalog contains one executable
configuration each for MODFLOW 6, MODFLOW-2000, and MODFLOW-96, plus the newly created
MODFLOW-USG configuration. The public catalog has additional historical duplicates, but those
records are not part of this dev-UI reconciliation.

The app manifests provide archive URL arguments plus optional simulation archive, WEL, and RCH
file inputs. The intended archive-based contract is a fixed archive baseline with optional WEL/RCH
overrides.

## Proposed design

1. Add an explicit live-record repair map for the existing MODFLOW 6, MODFLOW-2000, and MODFLOW-96
   configuration IDs. The dry-run must show updates to those IDs rather than new replacement IDs.
2. Keep MODFLOW-USG on the deterministic new-registration path.
3. Separate catalog-facing archive input metadata from the Tapis URL app argument. The archive
   baseline remains the executable configuration contract; WEL and RCH/RCHA are optional override
   specifications.
4. Normalize StandardVariable catalog IDs and `sameAs` values to the convention observed in the
   live catalog. Do not silently substitute the generic WEL/RCH variables for the more specific
   pumping/recharge variables; validate the canonical mapping first.
5. Use the corrected software label `MODFLOW-2000` for every affected MODFLOW-2000 software node
   targeted by the repair.
6. Target the dev-UI configuration IDs only, and write the `software_id` and
   `software_version_id` scalar foreign keys directly instead of relying on the deployed
   REST mapper's object-FK path.
6. Add focused payload-builder tests that fail if existing IDs, app bindings, optional flags, or
   SVO links regress.
7. Treat the complete simulation archive as a multi-variable input. Derive its presentations
   from the app manifest's supported semantic package bindings, deduplicate by StandardVariable,
   and link the complete presentation list in one DatasetSpecification update. WEL/RCH overrides
   remain separate optional inputs.

## Files likely affected

- `scripts/register_to_mint.py`
- `scripts/models_metadata.json`
- `scripts/test_register_to_mint.py` or the repository's existing registration test location
- `docs/design/2026-09-21-mint-registration-reconciliation.md`

## API/schema changes

No model-catalog-api or database schema changes are planned. The implementation changes only the
registration payload construction and live-record targeting used by the existing MINT API.

## Data flow

`app.json` + `models_metadata.json` → registration payload builder → dry-run repair/create plan →
focused validation against live MINT GET responses → separately approved live MINT writes.

## Risks and tradeoffs

- Public-catalog duplicate records make an unrestricted upsert unsafe; explicit dev IDs are
  required.
- SVO labels can look semantically plausible while using a different canonical URI. The dry-run
  must report unresolved mappings instead of creating them silently.
- Restricting inputs to archive/WEL/RCH improves the intended UI contract but must preserve the
  Tapis app's archive URL behavior.
- Live repairs can affect MINT discovery immediately, so the write phase requires a separate
  approval and a captured before-state for rollback.

## Alternatives considered

- Re-running the current script unchanged: rejected because it creates new configuration IDs and
  would preserve the live duplicates and SVO mismatch.
- Changing model-catalog-api: rejected; the existing UI/API path is working and the defect is in
  registration targeting and metadata.
- Deleting live duplicates automatically: deferred; this pass will repair known records without
  destructive deletion.

## Test plan

- Run the registration script in dry-run mode for all four variants.
- Assert the three existing repair IDs appear as update targets and MODFLOW-USG appears as the only
  create target.
- Assert new Tapis app IDs/versions and optional archive/WEL/RCH input flags.
- Assert each archive input has the expected distinct semantic presentation set and that all archive
  presentations are linked in one complete relationship update.
- Assert the corrected MODFLOW-2000 label and canonical SVO IDs/`sameAs` values.
- Compare the dry-run plan with read-only live GET responses; do not call POST/PUT during tests.

## Documentation plan

Document the repair/create distinction and the dry-run command in the registration script help or
adjacent runbook. No model-catalog-api documentation changes are needed.

## Rollout/rollback plan

First commit the registration/test changes and review the dry-run. Before live mutation, capture
the current configuration/software/input records. Apply updates and the USG create only after
explicit approval. Roll back by PUT-ing the captured prior payloads; do not delete duplicate
records in this change.

## Open questions

- The public catalog's duplicate MODFLOW-2000 and MODFLOW-96 records remain out of scope for
  the dev-UI pass.
- The archive is represented as both a required MINT DatasetSpecification and the Tapis app's
  `simulation.zip` file input. Its MINT specification carries multiple semantic presentations;
  WEL/RCH remain separate optional override specifications.
- Should WEL use the specific pumping SVO and RCH use the land-subsurface SVO, or should these be
  mapped to the already-existing generic MINT variables for compatibility?

## Decisions

- The target is the authenticated `mintdevapi` catalog backing `mintdevui`, not the public
  `api.models.mint.tacc.utexas.edu` catalog.
- No model-catalog-api changes are part of this work.
- Existing records are repaired by explicit ID; duplicate deletion is out of scope.
- The user explicitly said “run it” after the dry-run findings; this is treated as approval to
  implement the local repair and validation plan, not as approval for the later live MINT write.
- The dev inventory contains one executable MODFLOW-2000 and one executable MODFLOW-96 config;
  only those IDs are targeted. Public-catalog duplicates are not sent to the dev API.
- Scalar `software_id` and `software_version_id` fields are used because the deployed dev REST
  mapper's object-FK path did not persist the newly created USG version link reliably.
- The SVO `sameAs` form follows the live catalog's HTTP fragment convention; the semantic WEL/RCH
  terms remain the specific pumping and land-subsurface recharge terms from the local metadata.
- 2026-09-21: The complete archive input carries the distinct semantic package variables supported
  by each executable manifest. The registration batches all archive presentation links in one PUT,
  and the live dev catalog was read back with 5/5/5/4 archive presentations for MF6/USG/2000/96.

## User feedback / decisions

- User requested a dry-run against the current MINT catalog.
- User requested repair of MODFLOW 6, MODFLOW-2000, and MODFLOW-96, new MODFLOW-USG registration,
  and correction of the `MODFLOW-2001` label.
- Local implementation and dev-base dry-run are complete. The first approved live attempt
  partially updated scalar metadata and stopped before the relationship phase because the
  original map contained public-catalog IDs absent from dev; the map and FK payloads were then
  corrected without deleting records.
