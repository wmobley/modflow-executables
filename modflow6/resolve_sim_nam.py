#!/usr/bin/env python3
"""Resolve or generate the MODFLOW 6 simulation name file."""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from dataclasses import dataclass
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
    ".rcha": "RCH6",
    ".rchb": "RCH6",
    ".drn": "DRN6",
    ".riv": "RIV6",
    ".ghb": "GHB6",
    ".chd": "CHD6",
    ".csub": "CSUB6",
}

MODEL_OBSERVATION_NAMES = {"model.obs", "model-02.obs", "model-03.obs"}
OVERRIDE_NAMES = {
    "WEL6": ("model.wel",),
    "RCH6": ("model.rch", "model.rcha", "model.rchb"),
}


@dataclass(frozen=True)
class ModelDeclaration:
    line_index: int
    path: Path


def _without_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _relative_path(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix()


def discover_overrides(run_root: Path) -> dict[str, Path]:
    provided_root = run_root / "provided"
    overrides: dict[str, Path] = {}

    for package_type, names in OVERRIDE_NAMES.items():
        matches = [provided_root / name for name in names if (provided_root / name).is_file()]
        if len(matches) > 1:
            names_text = ", ".join(path.name for path in matches)
            raise SystemExit(
                f"Multiple {package_type} override files were supplied: {names_text}. "
                "Supply only one override for each package family."
            )
        if matches:
            overrides[package_type] = matches[0]

    return overrides


def parse_model_declarations(sim_nam_path: Path) -> list[ModelDeclaration]:
    declarations: list[ModelDeclaration] = []
    in_models = False

    for line_index, line in enumerate(sim_nam_path.read_text(encoding="utf-8").splitlines(keepends=True)):
        content = _without_comment(line)
        upper = content.upper()
        if upper == "BEGIN MODELS":
            in_models = True
            continue
        if upper == "END MODELS":
            in_models = False
            continue
        if not in_models or not content:
            continue

        tokens = content.split()
        if len(tokens) < 2:
            raise SystemExit(
                f"Malformed model declaration in {sim_nam_path} at line {line_index + 1}."
            )
        declarations.append(
            ModelDeclaration(
                line_index=line_index,
                path=(sim_nam_path.parent / tokens[1]).resolve(),
            )
        )

    return declarations


def replace_second_token(line: str, replacement: str) -> str:
    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    leading = body[: len(body) - len(body.lstrip())]
    tokens = body.strip().split(None, 2)
    if len(tokens) < 2:
        raise ValueError("Cannot replace the second token in a malformed name-file line.")
    rebuilt = f"{leading}{tokens[0]} {replacement}"
    if len(tokens) == 3:
        rebuilt += f" {tokens[2]}"
    return rebuilt + newline


def override_model_name_file(model_nam_path: Path, overrides: dict[str, Path]) -> Path:
    lines = model_nam_path.read_text(encoding="utf-8").splitlines(keepends=True)
    output_lines: list[str] = []
    in_packages = False
    inserted: set[str] = set()
    package_block_found = False

    for line in lines:
        content = _without_comment(line)
        upper = content.upper()
        if upper == "BEGIN PACKAGES":
            in_packages = True
            package_block_found = True
            output_lines.append(line)
            continue
        if upper == "END PACKAGES":
            for package_type, override_path in overrides.items():
                if package_type in inserted:
                    continue
                relative = _relative_path(override_path.resolve(), model_nam_path.parent.resolve())
                output_lines.append(f"  {package_type}  {relative}  {package_type.lower()}_override\n")
            in_packages = False
            output_lines.append(line)
            continue

        if in_packages and content:
            package_type = content.split()[0].upper()
            if package_type in overrides:
                if package_type not in inserted:
                    indent = line[: len(line) - len(line.lstrip())]
                    relative = _relative_path(
                        overrides[package_type].resolve(), model_nam_path.parent.resolve()
                    )
                    output_lines.append(
                        f"{indent}{package_type} {relative} {package_type.lower()}_override\n"
                    )
                    inserted.add(package_type)
                continue

        output_lines.append(line)

    if not package_block_found:
        raise SystemExit(
            f"Explicit model name file {model_nam_path} has no BEGIN PACKAGES section; "
            "cannot apply WEL/RCH overrides safely."
        )

    generated_model = model_nam_path.parent / "generated.model.nam"
    if generated_model == model_nam_path:
        generated_model = model_nam_path.parent / "resolved.model.nam"
    generated_model.write_text("".join(output_lines), encoding="utf-8")
    return generated_model


def override_simulation_name_file(
    sim_nam_path: Path,
    model_declaration: ModelDeclaration,
    resolved_model_path: Path,
) -> Path:
    lines = sim_nam_path.read_text(encoding="utf-8").splitlines(keepends=True)
    relative_model = _relative_path(resolved_model_path.resolve(), sim_nam_path.parent.resolve())
    lines[model_declaration.line_index] = replace_second_token(lines[model_declaration.line_index], relative_model)
    # MODFLOW 6 always opens mfsim.nam in the working directory; keep that
    # entrypoint and patch the staged copy rather than returning a sidecar name.
    sim_nam_path.write_text("".join(lines), encoding="utf-8")
    return sim_nam_path


def is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def priority(path: Path, provided_root: Path) -> tuple[int, str]:
    if is_relative_to(path, provided_root):
        bucket = 0
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


def first_match(candidates: list[Path], provided_root: Path) -> Path | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: priority(path, provided_root))[0]


def rel(path: Path, run_root: Path) -> str:
    return path.relative_to(run_root).as_posix()


def write_generated_model_nam(
    run_root: Path,
    package_files: dict[str, list[Path]],
    user_files: list[Path],
    provided_root: Path,
    overrides: dict[str, Path] | None = None,
) -> Path:
    generated_model = run_root / "generated.model.nam"
    package_lines: list[str] = []
    overrides = overrides or {}

    for pkg_name in sorted(set(package_files) | set(overrides)):
        if pkg_name in overrides:
            package_lines.append(
                f"  {pkg_name}  {_relative_path(overrides[pkg_name].resolve(), run_root.resolve())}  "
                f"{pkg_name.lower()}_override"
            )
            continue
        all_matches = sorted(
            package_files[pkg_name],
            key=lambda path: priority(path, provided_root),
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
                "BEGIN EXCHANGES",
                "END EXCHANGES",
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
    provided_root = run_root / "provided"
    legacy_default_root = run_root / "default_data"

    files = [
        path
        for path in sorted(run_root.rglob("*"))
        if is_candidate_file(path) and not is_relative_to(path, legacy_default_root)
    ]
    user_files = files

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
        if path.suffix.lower() == ".obs":
            if path.name.lower() in MODEL_OBSERVATION_NAMES:
                package_files["OBS6"].append(path)
            continue
        pkg = PACKAGE_MAP.get(path.suffix.lower())
        if pkg:
            package_files[pkg].append(path)

    has_user_packages = any(path in user_files for paths in package_files.values() for path in paths)
    has_user_support_files = any(path in user_files for path in [*tdis_files, *ims_files])
    has_provided_packages = any(
        is_relative_to(path, provided_root)
        for paths in package_files.values()
        for path in paths
    )
    has_provided_support_files = any(
        is_relative_to(path, provided_root) for path in [*tdis_files, *ims_files]
    )
    overrides = discover_overrides(run_root)

    explicit_user_sim = first_match(user_sim_nams, provided_root)
    if (
        explicit_user_sim is not None
        and not overrides
        and not has_provided_packages
        and not has_provided_support_files
    ):
        return explicit_user_sim

    if explicit_user_sim is not None and overrides:
        declarations = parse_model_declarations(explicit_user_sim)
        if len(declarations) != 1:
            raise SystemExit(
                f"WEL/RCH overrides require exactly one model declaration in {explicit_user_sim}; "
                f"found {len(declarations)}."
            )
        model_path = declarations[0].path
        if not model_path.is_file():
            raise SystemExit(
                f"Model name file referenced by {explicit_user_sim} does not exist: {model_path}"
            )
        resolved_model = override_model_name_file(model_path, overrides)
        return override_simulation_name_file(explicit_user_sim, declarations[0], resolved_model)

    existing_sim = first_match(sim_nams, provided_root)
    selected_model_nam = first_match(user_model_nams, provided_root) or first_match(
        model_nams, provided_root
    )

    need_generated_model = selected_model_nam is None or has_provided_packages
    if need_generated_model:
        selected_model_nam = write_generated_model_nam(
            run_root,
            package_files,
            user_files,
            provided_root,
            overrides,
        )
    elif overrides:
        if not selected_model_nam.is_file():
            raise SystemExit(f"Explicit model name file does not exist: {selected_model_nam}")
        selected_model_nam = override_model_name_file(selected_model_nam, overrides)

    if selected_model_nam is None:
        raise SystemExit("Unable to locate or generate a MODFLOW 6 model name file.")

    if existing_sim is not None and not has_user_packages and not has_user_support_files and not user_model_nams:
        return existing_sim

    tdis_path = first_match(tdis_files, provided_root)
    ims_path = first_match(ims_files, provided_root)
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
