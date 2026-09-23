# Runnable MODFLOW apps with archive and package overrides

**Status:** Implemented

## Objective

Make each of the four MODFLOW Tapis apps in this repository runnable from a
complete simulation archive while also supporting optional well and recharge
package overrides. The behavior must cover the different recharge file
conventions used by the engines, including MODFLOW 6 `rch`, `rcha`, and
`rchb` files.

## User need

- **Primary user:** A MINT/Tapis workflow submitting a MODFLOW model run.
- **Job-to-be-done:** Supply a simulation archive and optionally replace the
  well or recharge package without reconstructing the entire model bundle.
- **Current pain:** The app manifests, staging scripts, and smoke checker do
  not share one tested contract; the four apps are not covered uniformly.
- **Definition of success:** Every app declares a required archive, unpacks a
  safe ZIP/7z archive, applies package overrides with the documented precedence,
  resolves the correct name file, and passes a local end-to-end staging test.

## Current code/system summary

Each app has a required `simulation.zip` input in the working tree changes and
an engine-specific resolver. The runners already validate archives and support
some optional package inputs, but archive discovery is ZIP-only in the local
input fallback, MODFLOW 6 does not recognize `rchb`, and `scripts/check_apps.py`
still describes only three apps and submits tests without the now-required
archive.

## Proposed design

1. Keep `simulation.zip` as a required Tapis file input for all four apps.
2. Keep package files optional. A supplied package is staged under `provided/`
   and takes precedence over the corresponding package from the archive or
   baseline directory.
3. Extend MODFLOW 6 recharge resolution to recognize `rch`, `rcha`, and
   `rchb`, and declare the `rchb` override input alongside the existing recharge
   slots. All recognized recharge files map to the MODFLOW 6 `RCH6` package;
   explicit model name files remain authoritative, and a supplied recharge
   override replaces the active `RCH6` family.
4. Make local archive staging accept both `.zip` and `.7z` filenames while
   retaining archive-format validation and symlink/path protections.
5. Update the registration metadata and read-only smoke checker so all four apps
   are represented and a submitted smoke job includes the required archive.
6. Add a local test harness that runs each `run.sh` against a synthetic archive
   and fake executable, verifying archive unpacking, override precedence, and
   name-file generation without a remote model execution.

## Files likely affected

- `modflow6/app.json`, `modflow6/run.sh`, `modflow6/resolve_sim_nam.py`
- `modflow-2000/run.sh`, `modflow-96/run.sh`, `modflow-usg/run.sh`
- `scripts/models_metadata.json`, `scripts/register_to_mint.py`
- `scripts/check_apps.py`
- `scripts/test_app_runtime.py` and focused registration tests
- app README files and this design document

## API/schema changes

No service API or database schema changes. The Tapis app contract gains one
optional MODFLOW 6 `rchb` package input, and the generated registration payloads
gain the matching package metadata/presentation.

## Data flow

```text
required simulation.zip/7z
        |
        v
validate archive -> extract baseline -> copy provided package inputs
        |                                      |
        +------------------+-------------------+
                           v
              engine-specific name-file resolver
                           |
                           v
                    MODFLOW executable
```

Provided WEL/RCH/RCHA/RCHB files win over archive and baseline files for the
same package; unrelated archive files remain available to the run.

## Risks and tradeoffs

- A generic `rchb` filename is treated as a MODFLOW 6 recharge override;
  the resolver permits only one override per recharge family and cannot
  validate the package's internal semantics without running MODFLOW.
- Remote Tapis smoke tests remain opt-in and require a real archive source and
  credentials; local tests intentionally use fake binaries.
- Existing uncommitted registration and app changes are preserved rather than
  regenerated wholesale.

## Alternatives considered

- Requiring every individual package as a Tapis input would make archive-based
  runs impossible and duplicate model-specific file inventories.
- Adding one hard-coded model configuration per app would not support arbitrary
  archives or package naming conventions.
- Running a real MODFLOW job in CI would require remote credentials and model
  data, so the local harness covers assembly while remote execution remains a
  separate opt-in check.

## Test plan

- Parse all four `app.json` files and assert required archive plus optional
  package inputs.
- Run existing registration payload tests.
- Run a local synthetic archive through each app runner using a fake executable.
- Run shell syntax checks and resolver unit checks.
- Keep remote `scripts/check_apps.py --submit` opt-in; do not invoke it here.

## Documentation plan

Document the archive-first contract, optional override paths, and local test
command in the app documentation and checker help text.

## Rollout/rollback plan

Build and register the four app manifests only after local checks pass. If a
registered app fails, restore the prior app version and image tag; no database
or remote model state is modified by this change.

## Open questions

- The exact recharge package names in a user archive may differ from the
  conventional extensions. The resolver supports the explicit registered
  override slots and preserves explicit archive name files; arbitrary package
  discovery remains a model-specific concern.

## Decisions

- The user's explicit request to make all four app variants runnable is treated
  as approval to implement this archive-plus-override design.
- Archive input remains required; well and recharge overrides remain optional.
- Package-level observation files remain attached to their owning package and
  are not promoted to model-level `OBS6` declarations.

## User feedback / decisions

- User requested that all `modflow-executables` apps be runnable and accept well
  and recharge files in the engine-appropriate form, with an archive that is
  unpacked first.
- Implemented with the existing app version/image changes preserved in the
  working tree. No remote registration, model execution, commit, or push was
  performed.
