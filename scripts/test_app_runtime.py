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
import sys
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
            "mfsim.nam": "BEGIN MODELS\n  GWF6 model.nam model\nEND MODELS\n",
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
        "expected_tokens": ("RCH6 provided/model.rchb", "WEL6 provided/model.wel"),
        "forbidden_tokens": ("RCH6 model.rcha", "WEL6 model.wel"),
        "fake_solver": "#!/bin/sh\nprintf '%s\\n' mf6 > mfsim.lst\n",
    },
}


def write_fake_solver(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class AppRuntimeTests(unittest.TestCase):
    def test_modflow6_requires_simulation_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inputs = root / "inputs"
            outputs = root / "outputs"
            (inputs / "provided").mkdir(parents=True)
            (inputs / "provided" / "model.wel").write_text("should-not-run\n", encoding="utf-8")
            (inputs / "default_data").mkdir()
            (inputs / "default_data" / "model.nam").write_text(
                "default data must not be used\n", encoding="utf-8"
            )

            fake_solver = root / "fake-solver"
            write_fake_solver(fake_solver, "#!/bin/sh\nprintf '%s\\n' invoked > solver-invoked\n")
            env = os.environ.copy()
            env.update(
                {
                    "_tapisExecSystemInputDir": str(inputs),
                    "_tapisExecSystemOutputDir": str(outputs),
                    "MF6_EXE": str(fake_solver),
                    "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
                }
            )

            result = subprocess.run(
                ["bash", str(REPO / "modflow6" / "run.sh")],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required MODFLOW 6 input archive is missing", result.stdout)
            self.assertFalse((root / "solver-invoked").exists())

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
                for forbidden in case.get("forbidden_tokens", ()):
                    self.assertNotIn(forbidden, generated_text, f"{app_name}: {generated_text}")
                self.assertTrue((outputs / case["generated_name"]).exists())

    def test_modflow6_preserves_explicit_name_files_without_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run" / "nested"
            run_root.mkdir(parents=True)
            (run_root / "mfsim.nam").write_text(
                "BEGIN MODELS\n  GWF6 custom.nam custom\nEND MODELS\n",
                encoding="utf-8",
            )
            original_model = (
                "BEGIN PACKAGES\n"
                "  WEL6 custom.dom dom\n"
                "  WEL6 custom.min min\n"
                "  RCH6 custom.rcha recharge\n"
                "  OBS6 custom.obs observations\n"
                "END PACKAGES\n"
            )
            (run_root / "custom.nam").write_text(original_model, encoding="utf-8")
            (run_root / "custom.dom").write_text("dom\n", encoding="utf-8")
            (run_root / "custom.min").write_text("min\n", encoding="utf-8")
            (run_root / "custom.rcha").write_text("rcha\n", encoding="utf-8")
            (run_root / "custom.obs").write_text("obs\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(REPO / "modflow6" / "resolve_sim_nam.py"), str(run_root)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), (run_root / "mfsim.nam").resolve())
            self.assertEqual((run_root / "custom.nam").read_text(encoding="utf-8"), original_model)

    def test_modflow6_repairs_package_observations_in_explicit_generated_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run"
            run_root.mkdir()
            (run_root / "mfsim.nam").write_text(
                "BEGIN MODELS\n  GWF6 generated.model.nam model\nEND MODELS\n",
                encoding="utf-8",
            )
            (run_root / "generated.model.nam").write_text(
                "BEGIN PACKAGES\n"
                "  DRN6 model.drn drn\n"
                "  OBS6 model.drn.obs drn_obs\n"
                "  OBS6 model.obs model_obs\n"
                "  RIV6 model.riv riv\n"
                "  OBS6 model.riv.obs riv_obs\n"
                "END PACKAGES\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(REPO / "modflow6" / "resolve_sim_nam.py"), str(run_root)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            resolved_sim = Path(result.stdout.strip())
            self.assertEqual(resolved_sim.name, "mfsim.nam")
            self.assertIn("resolved.model.nam", resolved_sim.read_text(encoding="utf-8"))
            resolved_model = run_root / "resolved.model.nam"
            resolved_text = resolved_model.read_text(encoding="utf-8")
            self.assertEqual(resolved_text.count("OBS6"), 1)
            self.assertIn("OBS6 model.obs model_obs", resolved_text)
            self.assertNotIn("model.drn.obs", resolved_text)
            self.assertNotIn("model.riv.obs", resolved_text)
            self.assertIn("model.drn", resolved_text)
            self.assertIn("model.riv", resolved_text)

    def test_modflow6_overrides_preserve_package_multiplicity_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run"
            provided = run_root / "provided"
            provided.mkdir(parents=True)
            (run_root / "mfsim.nam").write_text(
                "BEGIN MODELS\n  GWF6 custom.nam custom\nEND MODELS\n",
                encoding="utf-8",
            )
            (run_root / "custom.nam").write_text(
                "BEGIN PACKAGES\n"
                "  WEL6 custom.dom dom\n"
                "  WEL6 custom.min min\n"
                "  RCH6 custom.rcha recharge\n"
                "  OBS6 custom.obs observations\n"
                "END PACKAGES\n",
                encoding="utf-8",
            )
            (provided / "model.wel").write_text("replacement wells\n", encoding="utf-8")
            (provided / "model.rch").write_text("replacement recharge\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(REPO / "modflow6" / "resolve_sim_nam.py"), str(run_root)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            resolved_sim = Path(result.stdout.strip())
            self.assertEqual(resolved_sim.name, "mfsim.nam")
            self.assertIn("generated.model.nam", resolved_sim.read_text(encoding="utf-8"))
            resolved_model = run_root / "generated.model.nam"
            resolved_text = resolved_model.read_text(encoding="utf-8")
            self.assertEqual(resolved_text.count("WEL6"), 1)
            self.assertEqual(resolved_text.count("RCH6"), 1)
            self.assertEqual(resolved_text.count("OBS6"), 1)
            self.assertIn("WEL6 provided/model.wel wel6_override", resolved_text)
            self.assertIn("RCH6 provided/model.rch rch6_override", resolved_text)
            self.assertNotIn("custom.dom", resolved_text)
            self.assertNotIn("custom.min", resolved_text)
            self.assertNotIn("custom.rcha", resolved_text)

    def test_modflow6_fallback_does_not_promote_package_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_root = root / "run"
            run_root.mkdir()
            for filename in ("model.dis", "model.npf", "model.tdis", "model.ims"):
                (run_root / filename).write_text(filename, encoding="utf-8")
            for filename in ("model.obs", "model.drn.obs", "model.riv.obs"):
                (run_root / filename).write_text(filename, encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(REPO / "modflow6" / "resolve_sim_nam.py"), str(run_root)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            generated_model = run_root / "generated.model.nam"
            generated_text = generated_model.read_text(encoding="utf-8")
            self.assertEqual(generated_text.count("OBS6"), 1)
            self.assertIn("OBS6  model.obs", generated_text)
            self.assertNotIn("model.drn.obs", generated_text)
            self.assertNotIn("model.riv.obs", generated_text)


if __name__ == "__main__":
    unittest.main()
