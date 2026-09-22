# MODFLOW app runtime contract

All four app manifests (`modflow6`, `modflow-usg`, `modflow-2000`, and
`modflow-96`) require one `simulation.zip` file input. The runner validates the
archive, unpacks it into the run directory, and keeps its relative file layout.
Archives uploaded with a `.7z` or `.zipx` suffix are also accepted by the local
stager; the archive contents are still validated before extraction.

Well and recharge inputs are optional overrides. They are staged below
`provided/` and take precedence over same-package files from the archive or
the configured baseline directory. The MODFLOW 6 runner recognizes `rch`,
`rcha`, and `rchb` as `RCH6` packages and can retain multiple recharge files
when they are present in the archive.

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
