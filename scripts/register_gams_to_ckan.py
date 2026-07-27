#!/usr/bin/env python3
"""Register TWDB GAM input packages to CKAN.

For each configured GAM:
  1. Downloads the simulation archive from the TWDB S3 bucket (or reads locally)
  2. Extracts it (ZIP or 7z) to a temp directory
  3. Uploads the full simulation archive as one CKAN resource
  4. Uploads the NAM file as its own resource
  5. Uploads any package file whose extension maps to a MINT standard variable
     (from models_metadata.json svo_bindings), tagging each with
     mint_standard_variables so the svo-adapter CKAN sync can discover them

Requires py7zr for Trinity (.7z archive): pip install py7zr

Usage:
    CKAN_TOKEN=... python3 register_gams_to_ckan.py
    CKAN_TOKEN=... python3 register_gams_to_ckan.py --dry-run
    CKAN_TOKEN=... python3 register_gams_to_ckan.py --gam czwx-central --gam ygjk-yegua-jackson
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from zipfile import ZipFile

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]

CKAN_URL = os.environ.get("CKAN_URL", "https://ckan.tacc.utexas.edu")
CKAN_TOKEN = os.environ.get("CKAN_TOKEN", "")
OWNER_ORG = os.environ.get("OWNER_ORG", "dynamo")

# ---------------------------------------------------------------------------
# Load svo_bindings + package_labels from models_metadata.json
# ---------------------------------------------------------------------------
_META = json.loads((SCRIPT_DIR / "models_metadata.json").read_text())
SVO_BINDINGS: dict[str, dict] = _META["svo_bindings"]      # ext -> {variable, unit, ...}
PKG_LABELS: dict[str, str]    = _META["package_labels"]     # ext -> human label

SVO_NS = "https://w3id.org/okn/i/mint/"

# ---------------------------------------------------------------------------
# GAM registry — one entry per model.
# source: local path (str) or HTTPS URL to the ZIP.
# variant: key in models_metadata.json variants dict.
# nam_file: exact NAM filename inside the ZIP (None = auto-detect *.nam).
# ---------------------------------------------------------------------------
GAMS: list[dict] = [
    {
        "slug":        "czwx-central",
        "label":       "CZWX Carrizo-Wilcox (central) — MODFLOW-USG v1.5",
        "variant":     "modflow-usg",
        "gma_id":      "12",
        "aquifer":     "Carrizo-Wilcox",
        "ckan_name":   "twdb-gam-czwx-carrizo-wilcox-central",
        "source":      str(REPO_ROOT / "gams" / "czwx_c_qcsp_v3.02_model_files"),
        "source_type": "dir",
        "nam_file":    None,
        "notes": (
            "TWDB Groundwater Availability Model for the Carrizo-Wilcox aquifer "
            "(central region), GMA 12. MODFLOW-USG v1.5 simulation with unstructured "
            "grid, SMS solver, and GNC package. Inputs registered here are the package "
            "files required to run the model via the Tapis modflow-usg app."
        ),
        "tags": ["MODFLOW-USG", "Carrizo-Wilcox", "GMA-12", "GAM", "TWDB", "DFC"],
    },
    {
        "slug":        "ygjk-yegua-jackson",
        "label":       "Yegua-Jackson GAM (CD-2) — MODFLOW-2000 v1.19",
        "variant":     "modflow-2000",
        "gma_id":      "13",
        "aquifer":     "Yegua-Jackson",
        "ckan_name":   "twdb-gam-ygjk-yegua-jackson",
        "source":      "https://gw-models.s3.amazonaws.com/Download_GAMs/ygjk/Yegua_Jackson_Model_Only.zip",
        "source_type": "url",
        "nam_file":    "ygjk_tr.nam",
        "notes": (
            "TWDB Groundwater Availability Model for the Yegua-Jackson aquifer (CD-2 "
            "transient run). MODFLOW-2000 v1.19 simulation. Source: TWDB GAM S3 archive "
            "(Yegua_Jackson_Model_Only.zip, 149 MB)."
        ),
        "tags": ["MODFLOW-2000", "Yegua-Jackson", "GMA-13", "GAM", "TWDB", "DFC"],
    },
    {
        "slug":        "trnt-trinity-hill-country",
        "label":       "Trinity Hill Country GAM (v3.01) — MODFLOW-96 v3.3",
        "variant":     "modflow-96",
        "gma_id":      "7",
        "aquifer":     "Trinity",
        "ckan_name":   "twdb-gam-trnt-trinity-hill-country",
        "source":      "https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_h/trnt_h_v3.01/Final/trnt_h_v3.01_Model_Files.7z",
        "source_type": "url",
        "nam_file":    "trnt_h_ss.nam",
        "notes": (
            "TWDB Groundwater Availability Model for the Trinity aquifer (Hill Country / "
            "southern portion), v3.01. MODFLOW-96 v3.3 simulation. Source: TWDB GAM S3 "
            "archive (trnt_h_v3.01_Model_Files.7z, 152 MB). Requires py7zr to extract."
        ),
        "tags": ["MODFLOW-96", "Trinity", "GMA-7", "GAM", "TWDB", "DFC"],
    },
]


# ---------------------------------------------------------------------------
# CKAN helpers
# ---------------------------------------------------------------------------
def _headers() -> dict[str, str]:
    return {"Authorization": CKAN_TOKEN}


def _api_get(path: str, params: str = "") -> dict:
    url = f"{CKAN_URL}/api/3/action/{path}" + (f"?{params}" if params else "")
    req = urllib.request.Request(url, headers=_headers())
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _api_post_json(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    h = {**_headers(), "Content-Type": "application/json"}
    req = urllib.request.Request(f"{CKAN_URL}/api/3/action/{path}", data=data,
                                  headers=h, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _upload_resource(package_id: str, existing_id: str | None,
                     filepath: Path, name: str, fmt: str,
                     description: str, stdvars: str, dry: bool) -> None:
    label = f"{name} ({fmt})"
    if dry:
        print(f"    [DRY] upload {filepath.name} → '{name}' fmt={fmt} stdvars={stdvars!r}")
        return
    base_fields = [
        ("name", name),
        ("format", fmt),
        ("description", description),
        ("mint_standard_variables", stdvars),
    ]
    if existing_id:
        fields = [("id", existing_id)] + base_fields
        action = "resource_update"
    else:
        fields = [("package_id", package_id)] + base_fields
        action = "resource_create"

    cmd = ["curl", "-sS", "-X", "POST",
           "-H", f"Authorization: {CKAN_TOKEN}"]
    for k, v in fields:
        cmd += ["-F", f"{k}={v}"]
    cmd += ["-F", f"upload=@{filepath}",
            f"{CKAN_URL}/api/3/action/{action}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    resp = json.loads(result.stdout)
    if resp.get("success"):
        print(f"    OK  {filepath.name} → '{name}'")
    else:
        print(f"    ERR {filepath.name}: {resp.get('error', resp)}")
    time.sleep(2)


def _ensure_dataset(gam: dict, dry: bool) -> tuple[str, dict]:
    """Return (dataset_id, package_show_result). Creates dataset if missing."""
    name = gam["ckan_name"]
    try:
        resp = _api_get("package_show", f"id={name}")
        if resp.get("success"):
            print(f"  Dataset exists: {name}")
            return resp["result"]["id"], resp["result"]
    except Exception:
        pass

    tags = [{"name": t} for t in gam["tags"]]
    payload = {
        "name":       name,
        "title":      gam["label"],
        "notes":      gam["notes"],
        "owner_org":  OWNER_ORG,
        "private":    False,
        "type":       "dataset",
        "tags":       tags,
        "extras": [
            {"key": "modflow_variant",  "value": gam["variant"]},
            {"key": "gma_id",           "value": gam["gma_id"]},
            {"key": "aquifer",          "value": gam["aquifer"]},
            {"key": "twdb_gam",         "value": "true"},
        ],
    }
    if dry:
        print(f"  [DRY] would create dataset: {name}")
        return "DRY_RUN_ID", {}
    resp = _api_post_json("package_create", payload)
    if not resp.get("success"):
        raise RuntimeError(f"package_create failed: {resp}")
    pkg_id = resp["result"]["id"]
    resp2 = _api_get("package_show", f"id={name}")
    print(f"  Created dataset: {pkg_id}")
    return pkg_id, resp2["result"]


def _existing_resources(pkg: dict) -> dict[str, str]:
    """Return {resource_name: resource_id} for quick lookup."""
    return {r["name"]: r["id"] for r in pkg.get("resources", [])}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def _find_nam(directory: Path, explicit: str | None) -> Path | None:
    if explicit:
        p = directory / explicit
        return p if p.exists() else None
    candidates = sorted(directory.rglob("*.nam"))
    return candidates[0] if candidates else None


def _ext(path: Path) -> str:
    """Return lowercased extension without dot, e.g. 'bcf'."""
    return path.suffix.lstrip(".").lower()


def _download(url: str, dest: Path) -> None:
    """Download url to dest with a progress indicator."""
    print(f"  Downloading {url.split('/')[-1]} …", end="", flush=True)

    def _reporthook(count: int, block: int, total: int) -> None:
        if total > 0:
            pct = min(100, count * block * 100 // total)
            print(f"\r  Downloading {url.split('/')[-1]} … {pct}%", end="", flush=True)

    urllib.request.urlretrieve(url, dest, reporthook=_reporthook)
    print()  # newline after progress


def _extract(archive: Path, dest: Path) -> None:
    """Extract a .zip or .7z archive to dest."""
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        with ZipFile(archive) as zf:
            zf.extractall(dest)
    elif suffix == ".7z":
        try:
            import py7zr
            with py7zr.SevenZipFile(archive, mode="r") as sz:
                sz.extractall(path=dest)
        except ImportError:
            # Fall back to system 7z command (p7zip)
            result = subprocess.run(
                ["7z", "x", str(archive), f"-o{dest}", "-y"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"7z extraction failed. Install py7zr (`pip install py7zr`) "
                    f"or p7zip (`brew install p7zip`).\n{result.stderr}"
                )
    else:
        raise ValueError(f"Unsupported archive format: {suffix}")


def _files_for_gam(source: str, source_type: str,
                   tmpdir: Path) -> tuple[Path, Path | None, list[Path]]:
    """Return (root_dir, archive_path_or_None, all_files_list).

    archive_path is the original downloaded/local archive file (for uploading
    as the simulation-archive resource). None for dir sources (we zip on-the-fly).
    """
    if source_type == "dir":
        root = Path(source)
        files = [f for f in root.rglob("*") if f.is_file()
                 and f.suffix.lower() not in (".cbb", ".hds", ".lst", ".cbc")]
        return root, None, files

    if source_type == "url":
        suffix = "." + source.split(".")[-1].lower()
        archive = tmpdir / f"model{suffix}"
        _download(source, archive)
        source = str(archive)
        source_type = "archive"

    if source_type in ("zip", "archive"):
        archive = Path(source)
        extract_dir = tmpdir / "extracted"
        extract_dir.mkdir()
        print(f"  Extracting {archive.name} …")
        _extract(archive, extract_dir)
        # Find actual root (some archives nest under one top-level folder)
        top_dirs = [p for p in extract_dir.iterdir() if p.is_dir()]
        top_files = [p for p in extract_dir.iterdir() if p.is_file()]
        root = top_dirs[0] if len(top_dirs) == 1 and not top_files else extract_dir
        files = [f for f in root.rglob("*") if f.is_file()]
        return root, archive, files

    raise ValueError(f"Unknown source_type: {source_type!r}")


# ---------------------------------------------------------------------------
# Per-GAM registration
# ---------------------------------------------------------------------------
def register_gam(gam: dict, dry: bool) -> int:
    slug = gam["slug"]
    print(f"\n{'='*60}")
    print(f"GAM: {gam['label']}")
    print(f"  variant={gam['variant']}  source={gam['source']!r}")

    if not gam["source"]:
        print(f"  SKIP — source not set. "
              f"Set env var {slug.upper().replace('-','_')}_ZIP=/path/to/file.zip")
        return 0

    pkg_id, pkg = _ensure_dataset(gam, dry)
    existing = _existing_resources(pkg)

    variant_meta = _META["variants"].get(gam["variant"], {})
    # CBC/HDS output codes for this variant (e.g. "hds", "cbc")
    output_exts = {o["code"] for o in variant_meta.get("outputs", [])}

    upserted = 0
    with tempfile.TemporaryDirectory() as td:
        root, archive_path, all_files = _files_for_gam(
            gam["source"], gam["source_type"], Path(td)
        )

        if not all_files:
            print("  ERROR: no files found")
            return 0

        # ── 1. NAM file ──────────────────────────────────────────────────────
        nam = _find_nam(root, gam.get("nam_file"))
        if nam:
            rname = f"{slug} — Name file ({nam.name})"
            _upload_resource(
                pkg_id, existing.get(rname), nam,
                name=rname, fmt="nam",
                description=(
                    f"MODFLOW name file for the {gam['label']} simulation. "
                    "Lists active packages and unit numbers; passed as the "
                    "primary input to the Tapis MODFLOW app."
                ),
                stdvars="",
                dry=dry,
            )
            upserted += 1
        else:
            print("  WARNING: no .nam file found")

        # ── 2. Simulation archive link (URL sources only — no re-upload) ────────
        if gam["source_type"] == "url":
            rname = f"{slug} — simulation archive"
            if dry:
                print(f"    [DRY] link {gam['source']} → '{rname}' fmt=simulation-archive")
            elif rname not in existing:
                body = {
                    "package_id": pkg_id,
                    "name": rname,
                    "url": gam["source"],
                    "format": "simulation-archive",
                    "description": (
                        f"Full {gam['label']} simulation bundle (all input files). "
                        "Original TWDB S3 archive; stage as the simulation-archive "
                        f"file input to the Tapis {gam['variant']} app."
                    ),
                }
                resp = _api_post_json("resource_create", body)
                if resp.get("success"):
                    print(f"    OK  linked simulation archive → '{rname}'")
                else:
                    print(f"    ERR simulation archive link: {resp.get('error', resp)}")
                time.sleep(2)
            upserted += 1

        # ── 3. Individual package files with SVO bindings ────────────────────
        registered_exts: set[str] = set()
        for fpath in sorted(all_files):
            ex = _ext(fpath)
            if ex in ("nam", "lst", "zip", "exe", "py", "sh", "md", "txt",
                      "pdf", "png", "jpg", "ds_store", "gitignore"):
                continue
            if ex in output_exts:
                continue  # skip output files (hds, cbc) — they don't exist yet
            if ex in registered_exts:
                continue  # register one file per extension type
            binding = SVO_BINDINGS.get(ex)
            if binding is None:
                continue  # no standard variable for this file type
            registered_exts.add(ex)

            stdvar_name = binding["variable"]
            unit = binding.get("unit", "")
            long_name = binding.get("long_name", ex)
            pkg_label = PKG_LABELS.get(ex, ex.upper())
            rname = f"{slug} — {pkg_label} ({ex})"
            desc = (
                f"{long_name.capitalize()} for the {gam['label']} simulation. "
                f"MODFLOW {ex.upper()} package; unit: {unit}. "
                f"Standard variable: {stdvar_name}."
            )
            _upload_resource(
                pkg_id, existing.get(rname), fpath,
                name=rname,
                fmt=ex,
                description=desc,
                stdvars=stdvar_name,
                dry=dry,
            )
            upserted += 1

    print(f"  {upserted} resources upserted for {slug}")
    return upserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    global CKAN_URL, OWNER_ORG
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gam", action="append", dest="gams",
                    help="Slug(s) to register (default: all). Repeat to select multiple.")
    ap.add_argument("--ckan-url", default=CKAN_URL)
    ap.add_argument("--org",      default=OWNER_ORG)
    args = ap.parse_args(argv)

    CKAN_URL   = args.ckan_url
    OWNER_ORG  = args.org

    token = CKAN_TOKEN
    if not token and not args.dry_run:
        print("CKAN_TOKEN required (export CKAN_TOKEN=...)", file=sys.stderr)
        return 2

    target_slugs = set(args.gams) if args.gams else None
    selected = [g for g in GAMS if target_slugs is None or g["slug"] in target_slugs]
    if not selected:
        print(f"No GAMs matched {args.gams}", file=sys.stderr)
        return 1

    print(f"Registering {len(selected)} GAM(s) to {CKAN_URL} (dry={args.dry_run})")
    total = 0
    for gam in selected:
        total += register_gam(gam, args.dry_run)

    print(f"\nDone. {total} total resources upserted.")
    print(
        "\nNext: POST /admin/sync-from-ckan on the svo-adapter to pull the\n"
        "new data objects into the BFS graph."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
