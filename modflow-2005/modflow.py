#!/usr/bin/env python3
"""Run MODFLOW-2005 with an explicit classic name file."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: modflow.py <name-file>", file=sys.stderr)
        return 2

    nam_path = Path(sys.argv[1]).resolve()
    if not nam_path.is_file():
        print(f"MODFLOW-2005 name file not found: {nam_path}", file=sys.stderr)
        return 1

    os.chdir(nam_path.parent)
    executable = os.environ.get("MF2005_EXE", "mf2005")
    print(f"Running MODFLOW-2005 in {nam_path.parent} using {nam_path.name}")
    try:
        subprocess.run([executable, nam_path.name], check=True)
    except FileNotFoundError:
        print(f"MODFLOW-2005 executable not found: {executable}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
