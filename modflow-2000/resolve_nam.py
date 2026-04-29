#!/usr/bin/env python3
"""Resolve or generate a MODFLOW-2000 name file from staged inputs."""
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

PACKAGE_CONFIG = {
    "bas": {"tokens": ("BAS6", "BAS"), "path_keys": ("model.bas", ".bas"), "fallback_unit": "1"},
    "bcf": {"tokens": ("BCF6", "BCF"), "path_keys": ("model.bcf", ".bcf"), "fallback_unit": "15"},
    "dis": {"tokens": ("DIS",), "path_keys": ("model.dis", ".dis"), "fallback_unit": "11"},
    "drn": {"tokens": ("DRN",), "path_keys": ("model.drn", ".drn"), "fallback_unit": "21"},
    "evt": {"tokens": ("EVT",), "path_keys": ("model.evt", ".evt"), "fallback_unit": "22"},
    "ghb": {"tokens": ("GHB",), "path_keys": ("model.ghb", ".ghb"), "fallback_unit": "23"},
    "gmg": {"tokens": ("GMG",), "path_keys": ("model.gmg", ".gmg"), "fallback_unit": "27"},
    "oc": {"tokens": ("OC",), "path_keys": ("model.oc", ".oc"), "fallback_unit": "14"},
    "rch": {"tokens": ("RCH",), "path_keys": ("model.rch", ".rch"), "fallback_unit": "19"},
    "str": {"tokens": ("STR",), "path_keys": ("model.str", ".str"), "fallback_unit": "24"},
    "wel": {"tokens": ("WEL",), "path_keys": ("model.wel", ".wel"), "fallback_unit": "20"},
}


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


def first_match(paths: list[Path], provided_root: Path, default_root: Path) -> Path | None:
    if not paths:
        return None
    return sorted(paths, key=lambda path: priority(path, provided_root, default_root))[0]


def rel(path: Path, run_root: Path) -> str:
    return path.relative_to(run_root).as_posix()


def match_package_key(path: Path) -> str | None:
    path_name = path.name.lower()
    path_suffix = path.suffix.lower()
    for package_key, config in PACKAGE_CONFIG.items():
        if path_name in config["path_keys"] or path_suffix in config["path_keys"]:
            return package_key
    return None


def collect_selected_packages(
    files: list[Path],
    provided_root: Path,
    default_root: Path,
) -> tuple[dict[str, Path], bool]:
    selected: dict[str, Path] = {}
    has_user_packages = False

    for package_key in PACKAGE_CONFIG:
        matches = [path for path in files if match_package_key(path) == package_key]
        if not matches:
            continue
        chosen = first_match(matches, provided_root, default_root)
        if chosen is None:
            continue
        selected[package_key] = chosen
        if is_relative_to(chosen, provided_root) or not is_relative_to(chosen, default_root):
            has_user_packages = True

    return selected, has_user_packages


def render_generated_nam(
    template_nam: Path | None,
    package_paths: dict[str, Path],
    run_root: Path,
) -> Path:
    generated_nam = run_root / "generated.model.nam"
    used_keys: set[str] = set()

    if template_nam is not None:
        rendered_lines: list[str] = []
        for line in template_nam.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "!")):
                rendered_lines.append(line)
                continue

            parts = stripped.split()
            token = parts[0].upper()
            replacement_key = next(
                (
                    package_key
                    for package_key, config in PACKAGE_CONFIG.items()
                    if token in config["tokens"] and package_key in package_paths
                ),
                None,
            )

            if replacement_key is None or len(parts) < 3:
                rendered_lines.append(line)
                continue

            used_keys.add(replacement_key)
            replacement_path = rel(package_paths[replacement_key], run_root)
            replacement = [parts[0], parts[1], replacement_path, *parts[3:]]
            rendered_lines.append(" ".join(replacement))

        for package_key, package_path in package_paths.items():
            if package_key in used_keys:
                continue
            config = PACKAGE_CONFIG[package_key]
            rendered_lines.append(
                f"{config['tokens'][0]} {config['fallback_unit']} {rel(package_path, run_root)}"
            )

        generated_nam.write_text("\n".join(rendered_lines) + "\n", encoding="utf-8")
        return generated_nam

    generated_lines = ["LIST 7 generated.model.lst"]
    for package_key, package_path in package_paths.items():
        config = PACKAGE_CONFIG[package_key]
        generated_lines.append(
            f"{config['tokens'][0]} {config['fallback_unit']} {rel(package_path, run_root)}"
        )

    generated_nam.write_text("\n".join(generated_lines) + "\n", encoding="utf-8")
    return generated_nam


def resolve_nam_path(run_root: Path) -> Path:
    default_root = run_root / "default_data"
    provided_root = run_root / "provided"

    files = [path for path in sorted(run_root.rglob("*")) if is_candidate_file(path)]
    name_files = [path for path in files if path.suffix.lower() == ".nam"]
    user_name_files = [path for path in name_files if is_relative_to(path, provided_root)]
    explicit_user_nam = first_match(user_name_files, provided_root, default_root)
    if explicit_user_nam is not None:
        return explicit_user_nam

    selected_packages, has_user_packages = collect_selected_packages(files, provided_root, default_root)
    template_nam = first_match(name_files, provided_root, default_root)

    if has_user_packages and selected_packages:
        return render_generated_nam(template_nam, selected_packages, run_root)

    if template_nam is not None:
        return template_nam

    if selected_packages:
        return render_generated_nam(None, selected_packages, run_root)

    raise SystemExit("Unable to locate or generate a MODFLOW-2000 name file.")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: resolve_nam.py <run_root>", file=sys.stderr)
        return 1

    run_root = Path(sys.argv[1]).resolve()
    print(resolve_nam_path(run_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
