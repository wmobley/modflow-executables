#!/usr/bin/env python3
"""Local runtime smoke tests for all four MODFLOW app runners.

The tests use a small ZIP archive and fake solver executables. They verify the
part that is easy to regress without a TACC account: archive extraction,
provided-file precedence, and engine-specific name-file generation.
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


APP_CASES = {
    "modflow-2000": {
        "exe_env": "MF2000_EXE",
        "archive_files": {
            "model.nam": "LIST 7 model.lst\nBAS6 1 archive.bas\nRCH 19 archive.rch\nWEL 20 archive.wel\n",
            "archive.bas": "archive-bas\n",
            "archive.rch": "archive-rch\n",
            "archive.wel": "archive-wel\n",
        },
        "overrides": {"provided/model.rch": "provided-rch\n", "provided/model.wel": "provided-wel\n"},
        "generated_name": "generated.model.nam",
        "expected_tokens": ("provided/model.rch", "provided/model.wel"),
        "fake_solver": "#!/bin/sh\nprintf '%s\\n' \"$1\" > fake-solver.ok\n",
    },
    "modflow-96": {
        "exe_env": "MF96_EXE",
        "archive_files": {
            "model.nam": "LIST 7 model.lst\nBAS 1 bas.dat\nRCH 19 rch.dat\nWEL 20 wel.dat\n",
            "bas.dat": "archive-bas\n",
            "rch.dat": "archive-rch\n",
            "wel.dat": "archive-wel\n",
        },
        "overrides": {"provided/rch.dat": "provided-rch\n", "provided/wel.dat": "provided-wel\n"},
        "generated_name": "generated.model.nam",
        "expected_tokens": ("provided/rch.dat", "provided/wel.dat"),
        "fake_solver": "#!/bin/sh\nread name\nprintf '%s\\n' \"$name\" > fake-solver.ok\n",
    },
    "modflow-usg": {
        "exe_env": "MFUSG_EXE",
        "archive_files": {
            "model.nam": "LIST 7 model.lst\nBAS6 1 archive.bas\nRCH 19 archive.rch\nWEL 20 archive.wel\n",
            "archive.bas": "archive-bas\n",
            "archive.rch": "archive-rch\n",
            "archive.wel": "archive-wel\n",
        },
        "overrides": {"provided/model.rch": "provided-rch\n", "provided/model.wel": "provided-wel\n"},
        "generated_name": "generated.model.nam",
        "expected_tokens": ("provided/model.rch", "provided/model.wel"),
        "fake_solver": "#!/bin/sh\nprintf '%s\\n' \"$1\" > fake-solver.ok\n",
    },
    "modflow6": {
        "exe_env": "MF6_EXE",
        "archive_files": {
            "mfsim.nam": "BEGIN SIMULATION\nEND SIMULATION\n",
            "model.nam": "BEGIN PACKAGES\n  DIS6 model.dis\n  NPF6 model.npf\n  RCH6 model.rcha\n  WEL6 model.wel\nEND PACKAGES\n",
            "model.tdis": "tdis\n",
            "model.ims": "ims\n",
            "model.dis": "dis\n",
            "model.npf": "npf\n",
            "model.rcha": "archive-rcha\n",
            "model.wel": "archive-wel\n",
        },
        "overrides": {"provided/model.rchb": "provided-rchb\n", "provided/model.wel": "provided-wel\n"},
        "generated_name": "generated.model.nam",
        "expected_tokens": ("RCH6  provided/model.rchb", "WEL6  provided/model.wel"),
        "fake_solver": "#!/bin/sh\nprintf '%s\\n' mf6 > mfsim.lst\n",
    },
}


def write_fake_solver(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class AppRuntimeTests(unittest.TestCase):
    def test_all_apps_unpack_archive_and_apply_overrides(self) -> None:
        for app_name, case in APP_CASES.items():
            with self.subTest(app=app_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                inputs = root / "inputs"
                outputs = root / "outputs"
                inputs.mkdir()
                (inputs / "provided").mkdir()
                archive_path = inputs / "simulation.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    for name, content in case["archive_files"].items():
                        archive.writestr(name, content)
                for name, content in case["overrides"].items():
                    (inputs / name).write_text(content, encoding="utf-8")

                fake_solver = root / "fake-solver"
                write_fake_solver(fake_solver, case["fake_solver"])
                env = os.environ.copy()
                env.update(
                    {
                        "_tapisExecSystemInputDir": str(inputs),
                        "_tapisExecSystemOutputDir": str(outputs),
                        case["exe_env"]: str(fake_solver),
                        # The test host may have an incompatible Intel-only
                        # /usr/local/bin/python3 ahead of the system Python.
                        # Tapis images provide a normal python3 on PATH.
                        "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
                    }
                )
                run_script = REPO / app_name / "run.sh"
                result = subprocess.run(
                    ["bash", str(run_script)],
                    cwd=root,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{app_name} failed:\nstdout={result.stdout}\nstderr={result.stderr}",
                )
                generated = root / "run" / case["generated_name"]
                self.assertTrue(generated.exists(), f"{app_name} did not generate {generated.name}")
                generated_text = generated.read_text(encoding="utf-8")
                for expected in case["expected_tokens"]:
                    self.assertIn(expected, generated_text, f"{app_name}: {generated_text}")
                self.assertTrue((outputs / case["generated_name"]).exists())


if __name__ == "__main__":
    unittest.main()
