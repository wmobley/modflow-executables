#!/usr/bin/env python3
"""Resolve a MODFLOW-2005 name file and apply WEL/RCH overrides."""
from __future__ import annotations

import sys
from pathlib import Path


IGNORE_NAMES = {"simulation.zip"}
IGNORE_SUFFIXES = {".zip", ".zipx", ".7z", ".out", ".stdout", ".stderr", ".log", ".json", ".csv"}
PREFERRED_NAME_FILES = ("model.nam", "ygjk_tr.nam")
PACKAGE_CONFIG = {
    "bas": {"tokens": ("BAS6", "BAS"), "path_keys": ("model.bas", ".bas", ".ba6"), "unit": "1"},
    "bcf": {"tokens": ("BCF6", "BCF"), "path_keys": ("model.bcf", ".bcf", ".bc6"), "unit": "15"},
    "dis": {"tokens": ("DIS",), "path_keys": ("model.dis", ".dis"), "unit": "11"},
    "drn": {"tokens": ("DRN",), "path_keys": ("model.drn", ".drn"), "unit": "21"},
    "hfb": {"tokens": ("HFB6", "HFB"), "path_keys": ("model.hfb", ".hfb", ".hf6"), "unit": "26"},
    "oc": {"tokens": ("OC",), "path_keys": ("model.oc", ".oc"), "unit": "14"},
    "rch": {"tokens": ("RCH",), "path_keys": ("model.rch", ".rch"), "unit": "19"},
    "sip": {"tokens": ("SIP",), "path_keys": ("model.sip", ".sip"), "unit": "23"},
    "wel": {"tokens": ("WEL",), "path_keys": ("model.wel", ".wel"), "unit": "20"},
}


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def candidate(path: Path) -> bool:
    return path.is_file() and path.name not in IGNORE_NAMES and path.suffix.lower() not in IGNORE_SUFFIXES


def preferred(paths: list[Path], provided: Path, root: Path) -> Path | None:
    if not paths:
        return None
    return sorted(
        paths,
        key=lambda path: (
            0 if inside(path, provided) else 1,
            PREFERRED_NAME_FILES.index(path.name) if path.name in PREFERRED_NAME_FILES else len(PREFERRED_NAME_FILES),
            path.as_posix(),
        ),
    )[0]


def package_key(path: Path) -> str | None:
    name = path.name.lower()
    suffix = path.suffix.lower()
    for key, config in PACKAGE_CONFIG.items():
        if name in config["path_keys"] or suffix in config["path_keys"]:
            return key
    return None


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def render(template: Path, packages: dict[str, Path], root: Path) -> Path:
    output = root / "generated.model.nam"
    lines: list[str] = []
    for line in template.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            lines.append(line)
            continue
        fields = stripped.split()
        token = fields[0].upper()
        key = next(
            (name for name, config in PACKAGE_CONFIG.items() if token in config["tokens"] and name in packages),
            None,
        )
        if key is None or len(fields) < 3:
            lines.append(line)
            continue
        lines.append(" ".join([fields[0], fields[1], rel(packages[key], root), *fields[3:]]))
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def resolve(root: Path) -> Path:
    provided = root / "provided"
    files = [path for path in sorted(root.rglob("*")) if candidate(path)]
    name_files = [path for path in files if path.suffix.lower() == ".nam"]
    provided_names = [path for path in name_files if inside(path, provided)]
    template = preferred(provided_names, provided, root)
    if template is None:
        if len(name_files) > 1:
            raise SystemExit("multiple MODFLOW-2005 name files found; provide provided/model.nam")
        if not name_files:
            raise SystemExit("MODFLOW-2005 simulation archive must contain an explicit .nam name file")
        template = name_files[0]

    packages: dict[str, Path] = {}
    for key in PACKAGE_CONFIG:
        matches = [path for path in files if package_key(path) == key]
        selected = preferred(matches, provided, root)
        if selected is not None:
            if inside(selected, provided) and key in {"wel", "rch"}:
                declared_tokens = {
                    token
                    for line in template.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if (fields := line.strip().split()) and len(fields) >= 3
                    for token in [fields[0].upper()]
                }
                declared = any(
                    declared_token in PACKAGE_CONFIG[key]["tokens"] for declared_token in declared_tokens
                )
                if not declared:
                    raise SystemExit(
                        f"provided {key.upper()} override has no corresponding {key.upper()} package record"
                    )
            packages[key] = selected

    if not any(inside(path, provided) for path in packages.values()):
        return template
    return render(template, packages, root)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: resolve_nam.py <run-root>")
    print(resolve(Path(sys.argv[1]).resolve()))
