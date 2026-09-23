# MODFLOW app runtime contract

All four app manifests (`modflow6`, `modflow-usg`, `modflow-2000`, and
`modflow-96`) require one `simulation.zip` file input. The runner validates the
archive, unpacks it into the run directory, and keeps its relative file layout.
Archives uploaded with a `.7z` or `.zipx` suffix are also accepted by the local
stager; the archive contents are still validated before extraction.

Well and recharge inputs are optional overrides. They are staged below
`provided/`. For MODFLOW 6, an archive's explicit `mfsim.nam` and model name
file remain authoritative; the runner patches those package declarations only
when an override is supplied. `model.wel` replaces all active `WEL6`
declarations, while one of `model.rch`, `model.rcha`, or `model.rchb` replaces
the active `RCH6` family. Supplying more than one recharge override is an
error. Package-level observation files such as `*.drn.obs` and `*.riv.obs`
remain referenced by their owning package and are not treated as model-level
`OBS6` packages.

MODFLOW 6 does not expose a host-specific `mf6DefaultDir` parameter and does
not overlay a baseline directory. Its required `simulation.zip` is the only
model baseline; the optional `mf6ArchiveUrl` app argument is retained only for
supplemental archive downloads.

## Local checks

Run the network-free runtime and registration checks from this repository:

```bash
python3 scripts/test_app_runtime.py
python3 scripts/test_register_to_mint.py
```

The runtime test uses synthetic archives and fake solver binaries. It verifies
staging and name-file assembly; it does not claim that a particular scientific
model converges. The remote checker remains opt-in:

```bash
TAPIS_TOKEN=... python3 scripts/check_apps.py
TAPIS_TOKEN=... python3 scripts/check_apps.py --submit --archive-url tapis://.../simulation.zip
```

The second command submits jobs and therefore requires a real archive URL,
Tapis credentials, and the appropriate allocation.
