#!/usr/bin/env python3
"""Resolve the best available MODFLOW-2000 name file."""
from __future__ import annotations

import sys
from pathlib import Path


IGNORE_NAMES = {
    "simulation.zip",
}

IGNORE_SUFFIXES = {
    ".zip",
    ".out",
    ".stdout",
    ".stderr",
    ".log",
    ".json",
    ".csv",
    ".hds",
    ".hed",
    ".bud",
    ".cbc",
    ".ucn",
    ".grb",
    ".lst",
}

PREFERRED_NAME_FILES = (
    "ygjk_tr.nam",
    "model.nam",
)


def is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def is_candidate_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name in IGNORE_NAMES:
        return False
    if path.suffix.lower() in IGNORE_SUFFIXES:
        return False
    return True


def priority(path: Path, provided_root: Path, default_root: Path) -> tuple[int, int, str]:
    if is_relative_to(path, provided_root):
        bucket = 0
    elif is_relative_to(path, default_root):
        bucket = 2
    else:
        bucket = 1

    try:
        preferred_rank = PREFERRED_NAME_FILES.index(path.name)
    except ValueError:
        preferred_rank = len(PREFERRED_NAME_FILES)

    return bucket, preferred_rank, path.as_posix()


def resolve_nam_path(run_root: Path) -> Path:
    default_root = run_root / "default_data"
    provided_root = run_root / "provided"

    files = [path for path in sorted(run_root.rglob("*")) if is_candidate_file(path)]
    name_files = [path for path in files if path.suffix.lower() == ".nam"]
    if not name_files:
        raise SystemExit("Unable to locate a MODFLOW-2000 name file (*.nam).")

    return sorted(name_files, key=lambda path: priority(path, provided_root, default_root))[0]


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: resolve_nam.py <run_root>", file=sys.stderr)
        return 1

    run_root = Path(sys.argv[1]).resolve()
    print(resolve_nam_path(run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
