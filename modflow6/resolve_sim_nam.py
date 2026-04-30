#!/usr/bin/env python3
"""Resolve or generate the MODFLOW 6 simulation name file."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path


IGNORE_NAMES = {
    "simulation.zip",
    "package_show.json",
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
    ".imslst",
    ".tdislst",
}

PACKAGE_MAP = {
    ".dis": "DIS6",
    ".disv": "DISV6",
    ".disu": "DISU6",
    ".ic": "IC6",
    ".npf": "NPF6",
    ".sto": "STO6",
    ".oc": "OC6",
    ".wel": "WEL6",
    ".rch": "RCH6",
    ".rcha": "RCHA6",
    ".drn": "DRN6",
    ".riv": "RIV6",
    ".ghb": "GHB6",
    ".chd": "CHD6",
    ".csub": "CSUB6",
    ".obs": "OBS6",
}


def is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def priority(path: Path, provided_root: Path, default_root: Path) -> tuple[int, str]:
    if is_relative_to(path, provided_root):
        bucket = 0
    elif is_relative_to(path, default_root):
        bucket = 2
    else:
        bucket = 1
    return bucket, path.as_posix()


def is_candidate_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name in IGNORE_NAMES:
        return False
    if path.suffix.lower() in IGNORE_SUFFIXES:
        return False
    return True


def first_match(candidates: list[Path], provided_root: Path, default_root: Path) -> Path | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: priority(path, provided_root, default_root))[0]


def rel(path: Path, run_root: Path) -> str:
    return path.relative_to(run_root).as_posix()


def write_generated_model_nam(
    run_root: Path,
    package_files: dict[str, list[Path]],
    user_files: list[Path],
    provided_root: Path,
    default_root: Path,
) -> Path:
    generated_model = run_root / "generated.model.nam"
    package_lines: list[str] = []

    for pkg_name in sorted(package_files):
        all_matches = sorted(
            package_files[pkg_name],
            key=lambda path: priority(path, provided_root, default_root),
        )
        user_matches = [path for path in all_matches if path in user_files]
        matches = user_matches or all_matches

        for idx, match in enumerate(matches, start=1):
            if match == generated_model:
                continue
            line = f"  {pkg_name}  {rel(match, run_root)}"
            if len(matches) > 1:
                pname = f"{match.stem.replace('.', '_')}_{idx}"
                line += f"  {pname}"
            package_lines.append(line)

    if not package_lines:
        raise SystemExit(
            "Unable to generate a model name file: no recognizable MODFLOW 6 package files were found."
        )

    generated_model.write_text(
        "\n".join(
            [
                "BEGIN OPTIONS",
                "  LIST generated.model.lst",
                "END OPTIONS",
                "",
                "BEGIN PACKAGES",
                *package_lines,
                "END PACKAGES",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return generated_model


def write_generated_sim_nam(
    run_root: Path,
    model_nam_path: Path,
    tdis_path: Path,
    ims_path: Path,
) -> Path:
    model_name = model_nam_path.stem.replace(".", "_") or "model"
    if model_name == "generated_model":
        model_name = "model"
    generated_sim = run_root / "mfsim.nam"
    generated_sim.write_text(
        "\n".join(
            [
                "BEGIN OPTIONS",
                "END OPTIONS",
                "",
                "BEGIN TIMING",
                f"  TDIS6  {rel(tdis_path, run_root)}",
                "END TIMING",
                "",
                "BEGIN MODELS",
                f"  GWF6  {rel(model_nam_path, run_root)}  {model_name}",
                "END MODELS",
                "",
                "BEGIN SOLUTIONGROUP 1",
                f"  IMS6  {rel(ims_path, run_root)}  {model_name}",
                "END SOLUTIONGROUP",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return generated_sim


def resolve_sim_nam_path(run_root: Path) -> Path:
    default_root = run_root / "default_data"
    provided_root = run_root / "provided"

    files = [path for path in sorted(run_root.rglob("*")) if is_candidate_file(path)]
    user_files = [path for path in files if not is_relative_to(path, default_root)]

    sim_nams = [path for path in files if path.name.lower() == "mfsim.nam"]
    user_sim_nams = [path for path in sim_nams if path in user_files]

    model_nams = [path for path in files if path.suffix.lower() == ".nam" and path.name.lower() != "mfsim.nam"]
    user_model_nams = [path for path in model_nams if path in user_files]

    tdis_files = [path for path in files if path.suffix.lower() == ".tdis"]
    ims_files = [path for path in files if path.suffix.lower() == ".ims"]

    package_files: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        if path.name.lower().endswith(".csub.obs"):
            continue
        pkg = PACKAGE_MAP.get(path.suffix.lower())
        if pkg:
            package_files[pkg].append(path)

    has_user_packages = any(path in user_files for paths in package_files.values() for path in paths)
    has_user_support_files = any(path in user_files for path in [*tdis_files, *ims_files])

    explicit_user_sim = first_match(user_sim_nams, provided_root, default_root)
    if explicit_user_sim is not None and not has_user_packages and not has_user_support_files:
        return explicit_user_sim

    existing_sim = first_match(sim_nams, provided_root, default_root)
    selected_model_nam = first_match(user_model_nams, provided_root, default_root) or first_match(
        model_nams, provided_root, default_root
    )

    need_generated_model = selected_model_nam is None or has_user_packages
    if need_generated_model:
        selected_model_nam = write_generated_model_nam(
            run_root,
            package_files,
            user_files,
            provided_root,
            default_root,
        )

    if selected_model_nam is None:
        raise SystemExit("Unable to locate or generate a MODFLOW 6 model name file.")

    if existing_sim is not None and not has_user_packages and not has_user_support_files and not user_model_nams:
        return existing_sim

    tdis_path = first_match(tdis_files, provided_root, default_root)
    ims_path = first_match(ims_files, provided_root, default_root)
    if tdis_path is None:
        raise SystemExit("Unable to locate a MODFLOW 6 TDIS file (*.tdis).")
    if ims_path is None:
        raise SystemExit("Unable to locate a MODFLOW 6 IMS file (*.ims).")

    return write_generated_sim_nam(run_root, selected_model_nam, tdis_path, ims_path)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: resolve_sim_nam.py <run_root>", file=sys.stderr)
        return 1

    run_root = Path(sys.argv[1]).resolve()
    sim_nam_path = resolve_sim_nam_path(run_root)
    print(sim_nam_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
