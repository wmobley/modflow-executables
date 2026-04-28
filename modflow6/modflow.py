#!/usr/bin/env python3
"""Run MODFLOW 6 using a provided name file path or directory."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def resolve_nam_path(args: list[str]) -> Path:
    if args:
        candidate = args[0]
    else:
        candidate = os.environ.get("MF6_NAMFILE") or os.environ.get("MFSIM_NAM") or "mfsim.nam"

    path = Path(candidate)
    if path.is_dir():
        path = path / "mfsim.nam"
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    return path


def main() -> int:
    nam_path = resolve_nam_path(sys.argv[1:])
    if not nam_path.exists():
        print(f"Name file not found at {nam_path}", file=sys.stderr)
        return 1

    sim_dir = nam_path.parent
    os.chdir(sim_dir)

    exe = os.environ.get("MF6_EXE", "mf6")
    extra_args = sys.argv[2:] if len(sys.argv) > 1 else []
    cmd = [exe, *extra_args]

    print(f"Running MODFLOW 6 in {sim_dir} using {nam_path.name}")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print(f"MODFLOW 6 executable not found: {exe}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
