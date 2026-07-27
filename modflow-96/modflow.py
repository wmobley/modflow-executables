#!/usr/bin/env python3
"""Run MODFLOW-96 using a provided name file path."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def resolve_nam_path(args: list[str]) -> Path:
    if args:
        candidate = args[0]
    else:
        candidate = os.environ.get("MF96_NAMFILE") or os.environ.get("MODFLOW_NAM") or "model.nam"

    path = Path(candidate)
    if path.is_dir():
        raise SystemExit(f"Expected a name file path, received directory: {path}")
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def main() -> int:
    nam_path = resolve_nam_path(sys.argv[1:])
    if not nam_path.exists():
        print(f"Name file not found at {nam_path}", file=sys.stderr)
        return 1

    os.chdir(nam_path.parent)

    exe = os.environ.get("MF96_EXE", "mf96")
    cmd = [exe]

    print(f"Running MODFLOW-96 in {nam_path.parent} using {nam_path.name}")
    try:
        subprocess.run(
            cmd,
            check=True,
            input=f"{nam_path.name}\n",
            text=True,
        )
    except FileNotFoundError:
        print(f"MODFLOW-96 executable not found: {exe}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
