# MODFLOW-2005 archive-first Tapis app

This application runs the official MODFLOW-2005 1.12.00 executable against a
complete classic MODFLOW simulation archive.

Runtime contract:

- `simulation.zip` is required and contains the model name file and package
  files.
- `provided/model.wel` and `provided/model.rch` are optional replacements.
- The archive is extracted first; supplied overrides take precedence.
- A supplied `provided/model.nam` is used explicitly. Otherwise the archive
  must contain one unambiguous `.nam` file.
- WEL/RCH overrides must replace package records already declared by that name
  file; the runner never invents a missing package record.
- Outputs are copied to the Tapis output directory, including `CBB`, `HDS`,
  `DDN`, and `LST` when produced by the model.

The app is intentionally a new application ID. The legacy
`modflow-2005/0.0.6` registration remains available until this image has been
built, inspected, and smoke-tested.
