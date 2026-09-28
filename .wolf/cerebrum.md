# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-07-20

## User Preferences

<!-- How the user likes things done. Code style, tools, patterns, communication. -->

## Key Learnings

- **Project:** modflow-executables
- The MINT registration script derives DatasetSpecification optionality directly from each app manifest's file-input `inputMode`; archive requiredness must therefore be fixed in all four MODFLOW `app.json` files.
- The CKAN-to-MINT adapter imports resources only when `mint_standard_variables` is present and mapped; the model archive resource creation path currently omits that field, so archive discovery must be supplied by the evidence-backed CKAN reconciliation/resource metadata path.
- The four app runners unpack the required `simulation.zip` before applying optional files under `provided/`; MF6 package resolution maps `.rch`, `.rcha`, and `.rchb` to `RCH6`, while classic engines resolve their single legacy RCH package through the name file.
- Local app runtime verification can use synthetic ZIP archives and fake executables through `MF6_EXE`, `MFUSG_EXE`, `MF2000_EXE`, and `MF96_EXE`; this validates staging and name-file precedence without a remote Tapis/MINT execution.
- MODFLOW-2005 must be a dedicated app contract: its classic `mf2005` executable and `cbc-mf2005` output format are not interchangeable with MODFLOW-2000. The archive-first runner accepts one unambiguous `.nam` file and applies only explicit WEL/RCH overrides.

## Do-Not-Repeat

<!-- Mistakes made and corrected. Each entry prevents the same mistake recurring. -->
<!-- Format: [YYYY-MM-DD] Description of what went wrong and what to do instead. -->

## Decision Log

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->
- 2026-09-28: Preserve `modflow-2005/0.0.6` and add `modflow-2005-simulation` so catalog repointing can wait for a verified image/app registration and rollback remains possible.
