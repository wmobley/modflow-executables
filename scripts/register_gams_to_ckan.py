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

Metadata follows ESIPFed AI-Ready Data Checklist v1.1:
  https://github.com/ESIPFed/data-readiness/blob/main/checklist-draft/ai-ready-data-checklist-v.1.1.md

Requires py7zr for Trinity (.7z archive): pip install py7zr

Usage:
    CKAN_TOKEN=... python3 register_gams_to_ckan.py
    CKAN_TOKEN=... python3 register_gams_to_ckan.py --dry-run
    CKAN_TOKEN=... python3 register_gams_to_ckan.py --gam czwx-central --gam ygjk-yegua-jackson
    CKAN_TOKEN=... python3 register_gams_to_ckan.py --gam ntgam
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
from datetime import datetime, timezone
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
# ESIPFed AI-Ready Data Checklist v1.1 metadata fields
# These are applied to every dataset and resource as CKAN extras.
# Reference: https://github.com/ESIPFed/data-readiness/blob/main/checklist-draft/ai-ready-data-checklist-v.1.1.md
# ---------------------------------------------------------------------------
ESIPFED_DEFAULTS = {
    # General Information
    "data_type": "Derived",           # Raw / Derived
    "data_source_type": "Modeled",    # Observed / Modeled / Synthetic
    "data_aggregation": "Single-source",  # Single-source / Aggregated
    # Data Quality - Timeliness
    "maintenance_and_update_frequency": "not_planned",  # ISO 8601 maintenance code
    "continuously_updated": "false",
    "data_addition_frequency": "none",
    "data_revision_available": "false",
    # Data Quality - Coverage
    "spatial_coverage_standardized": "true",
    "temporal_coverage_standardized": "true",
    "spatial_resolution_quantitative": "true",
    "temporal_resolution_quantitative": "true",
    # Data Quality - Consistency
    "self_consistent_units": "true",
    "consistent_with_similar_collections": "true",
    "consistency_monitoring": "manual_review",
    # Data Quality - Representativeness
    "representativeness_documented": "true",
    "representativeness_report_url": "https://www.twdb.texas.gov/groundwater/models/gam/index.asp",
    # Data Quality - Bias
    "known_bias": "false",
    "bias_examined": "true",
    "bias_measures": "Calibrated against observed water levels; history-matched GAM",
    "bias_reported": "No known bias",
    "published_quality_procedures": "true",
    "quality_procedures_url": "https://www.twdb.texas.gov/groundwater/models/gam/index.asp",
    "provenance_tracked": "true",
    "data_integrity_checks": "true",
    # Data Documentation
    "machine_readable_metadata": "true",
    "parameter_names_follow_standard": "true",
    "parameter_standard": "CSDMS SVO (https://www.geoscienceontology.org/svo/svl/variable/)",
    "metadata_follows_community_standard": "true",
    "metadata_standard": "DSO CKAN Scheming + ESIPFed AI-Ready",
    "metadata_machine_readable": "true",
    "spatial_temporal_extent_included": "true",
    "unique_persistent_identifier": "false",  # No DOI yet
    "contact_information_available": "true",
    "mechanism_for_user_feedback": "true",
    "example_codes_notebooks_available": "true",
    "license_standardized": "true",
    "license": "other-pd",  # Public Domain (TWDB)
    "license_machine_readable": "true",
    "previously_used_in_ai_ml": "false",
    "intended_use_recommendations": "true",
    "intended_use": "Regional groundwater planning and screening; DFC compliance evaluation",
    "not_recommended_use": "Site-specific design or detailed local analysis",
    # Data Access
    "major_file_formats": "HDS,CBC,GeoTIFF,NetCDF,Shapefile,CSV,JSON",
    "format_machine_readable": "true",
    "open_non_proprietary_format": "true",
    "format_conversion_tools_available": "true",
    "format_conversion_tools_url": "https://github.com/MODFLOW-ORG/modflow6",
    "authentication_required": "false",  # Public data
    "direct_file_download": "true",
    "api_available": "true",
    "api_open_standard": "true",
    "api_documentation_url": "https://ckan.tacc.utexas.edu/api/3/action",
    "cloud_access_available": "true",
    # Data Preparation
    "null_values_filled": "false",
    "outliers_identified": "false",
    "data_gridded": "regularly_gridded_space_time",
    "gridding_description": "Structured finite-difference grid (DIS) or unstructured grid (DISU)",
}


# ---------------------------------------------------------------------------
# GAM registry — one entry per model.
# source: local path (str) or HTTPS URL to the ZIP.
# variant: key in models_metadata.json variants dict.
# nam_file: exact NAM filename inside the ZIP (None = auto-detect *.nam).
# ckan_name: existing dataset name in CKAN (must match exactly).
# ---------------------------------------------------------------------------
GAMS: list[dict] = [
    {
        "slug":        "ntgam",
        "label":       "Northern Trinity & Woodbine (NTGAM) — MODFLOW 6 v3.01",
        "variant":     "modflow6",
        "gma_id":      "8",
        "aquifer":     "Northern Trinity; Woodbine",
        "ckan_name":   "ntgam-v301-inputs",
        "source":      "https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_n/trnt_n_v301/NTGAM_Final_model_2025.7z",
        "source_type": "url",
        "nam_file":    "mfsim.nam",
        "notes": (
            "TWDB Groundwater Availability Model for the Northern Trinity and Woodbine "
            "aquifer system (NTGAM), GMA 8. MODFLOW 6 v3.01 simulation. Source: TWDB GAM "
            "S3 archive (NTGAM_Final_model_2025.7z, 267MB). Inputs registered here are the "
            "package files required to run the model via the Tapis modflow6 app."
        ),
        "tags": ["MODFLOW-6", "Northern-Trinity", "Woodbine", "GMA-8", "GAM", "TWDB", "DFC"],
        # ESIPFed-specific overrides for NTGAM
        "esipfed_overrides": {
            "version": "3.01",
            "original_publish_date": "2026-03-01",
            "point_of_contact": "gam@twdb.texas.gov",
            "spatial_coverage": "{\"type\":\"Polygon\",\"coordinates\":[[[-99.763,29.9014],[-93.4816,29.9014],[-93.4816,34.4266],[-99.763,34.4266],[-99.763,29.9014]]]}",
            "temporal_coverage_start": "1890-01-01",
            "temporal_coverage_end": "2024-12-31",
            "spatial_resolution": "500m x 500m grid cells",
            "temporal_resolution": "Stress periods (variable, 1 day to 1 year)",
            "dataset_size": "267MB compressed, 4.5GB uncompressed",
            "citation": "Ellis, J., et al., 2025, Hydrogeology and Documentation of the NTGAM, v3.01.",
            "report_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_n/trnt_n_v301/NTGAM_Final_Report.pdf",
        },
    },
    {
        "slug":        "czwx-central",
        "label":       "CZWX Carrizo-Wilcox (central) — MODFLOW-USG v1.5",
        "variant":     "modflow-usg",
        "gma_id":      "12",
        "aquifer":     "Carrizo-Wilcox",
        "ckan_name":   "carrizo-wilcox-gam-central-portion",
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
        "esipfed_overrides": {
            "version": "3.02",
            "original_publish_date": "2020-10-01",
            "point_of_contact": "gam@twdb.texas.gov",
            "spatial_coverage": "{\"type\":\"Polygon\",\"coordinates\":[[[-100.5,29.5],[-94.0,29.5],[-94.0,33.0],[-100.5,33.0],[-100.5,29.5]]]}",
            "temporal_coverage_start": "1900-01-01",
            "temporal_coverage_end": "2020-12-31",
            "spatial_resolution": "Unstructured grid (variable cell sizes)",
            "temporal_resolution": "Stress periods (variable)",
            "dataset_size": "712MB compressed, 3.16GB uncompressed",
            "citation": "TWDB, 2020, Carrizo-Wilcox (central) GAM v3.02.",
        },
    },
    {
        "slug":        "ygjk-yegua-jackson",
        "label":       "Yegua-Jackson GAM (CD-2) — MODFLOW-2000 v1.19",
        "variant":     "modflow-2000",
        "gma_id":      "13",
        "aquifer":     "Yegua-Jackson",
        "ckan_name":   "yegua-jackson-aquifer-groundwater-availability-model-files",
        "source":      "https://gw-models.s3.amazonaws.com/Download_GAMs/ygjk/Yegua_Jackson_Model_Only.zip",
        "source_type": "url",
        "nam_file":    "ygjk_tr.nam",
        "notes": (
            "TWDB Groundwater Availability Model for the Yegua-Jackson aquifer (CD-2 "
            "transient run). MODFLOW-2000 v1.19 simulation. Source: TWDB GAM S3 archive "
            "(Yegua_Jackson_Model_Only.zip, 149 MB)."
        ),
        "tags": ["MODFLOW-2000", "Yegua-Jackson", "GMA-13", "GAM", "TWDB", "DFC"],
        "esipfed_overrides": {
            "version": "1.19",
            "original_publish_date": "2010-01-01",
            "point_of_contact": "gam@twdb.texas.gov",
            "spatial_coverage": "{\"type\":\"Polygon\",\"coordinates\":[[[-97.5,29.5],[-94.0,29.5],[-94.0,32.0],[-97.5,32.0],[-97.5,29.5]]]}",
            "temporal_coverage_start": "1900-01-01",
            "temporal_coverage_end": "2010-12-31",
            "spatial_resolution": "Structured finite-difference grid",
            "temporal_resolution": "Stress periods (variable)",
            "dataset_size": "149MB compressed, 1.8GB uncompressed",
            "citation": "TWDB, 2010, Yegua-Jackson GAM CD-2.",
        },
    },
    {
        "slug":        "trnt-trinity-hill-country",
        "label":       "Trinity Hill Country GAM (v3.01) — MODFLOW-96 v3.3",
        "variant":     "modflow-96",
        "gma_id":      "7",
        "aquifer":     "Trinity",
        "ckan_name":   "trinity-aquifer-hill-country-southern-portion",
        "source":      "https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_h/trnt_h_v3.01/Final/trnt_h_v3.01_Model_Files.7z",
        "source_type": "url",
        "nam_file":    "trnt_h_ss.nam",
        "notes": (
            "TWDB Groundwater Availability Model for the Trinity aquifer (Hill Country / "
            "southern portion), v3.01. MODFLOW-96 v3.3 simulation. Source: TWDB GAM S3 "
            "archive (trnt_h_v3.01_Model_Files.7z, 152 MB). Requires py7zr to extract."
        ),
        "tags": ["MODFLOW-96", "Trinity", "GMA-7", "GAM", "TWDB", "DFC"],
        "esipfed_overrides": {
            "version": "3.01",
            "original_publish_date": "2009-01-01",
            "point_of_contact": "gam@twdb.texas.gov",
            "spatial_coverage": "{\"type\":\"Polygon\",\"coordinates\":[[[-99.0,29.5],[-97.5,29.5],[-97.5,31.0],[-99.0,31.0],[-99.0,29.5]]]}",
            "temporal_coverage_start": "1900-01-01",
            "temporal_coverage_end": "2009-12-31",
            "spatial_resolution": "Structured finite-difference grid",
            "temporal_resolution": "Steady state",
            "dataset_size": "152MB compressed, 1.5GB uncompressed",
            "citation": "TWDB, 2009, Trinity Hill Country GAM v3.01.",
        },
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
                     description: str, stdvars: str, dry: bool,
                     gam: dict | None = None) -> None:
    """Upload a resource to CKAN with ESIPFed AI-Ready metadata."""
    label = f"{name} ({fmt})"
    if dry:
        print(f"    [DRY] upload {filepath.name} → '{name}' fmt={fmt} stdvars={stdvars!r}")
        return
    
    # Build ESIPFed resource metadata
    resource_extras = {
        "program_area": "Groundwater",
        "categories": "Groundwater",
        "collection_method": "Model Output",
        "quality_control_level": "Calibrated, history-matched GAM",
        "data_contact_email": "gam@twdb.texas.gov",
        "caveats_usage": "Regional groundwater planning and screening; not for site-specific design.",
    }
    
    # Add GAM-specific temporal coverage if available
    if gam and gam.get("esipfed_overrides"):
        overrides = gam["esipfed_overrides"]
        if "temporal_coverage_start" in overrides:
            resource_extras["temporal_coverage_start"] = overrides["temporal_coverage_start"]
        if "temporal_coverage_end" in overrides:
            resource_extras["temporal_coverage_end"] = overrides["temporal_coverage_end"]
    
    base_fields = [
        ("name", name),
        ("format", fmt),
        ("description", description),
        ("mint_standard_variables", stdvars),
    ]
    
    # Add ESIPFed extras as individual fields
    for key, value in resource_extras.items():
        base_fields.append((key, value))
    
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


def _build_extras(gam: dict) -> list[dict]:
    """Build CKAN extras list merging GAM-specific and ESIPFed AI-Ready metadata."""
    extras = [
        {"key": "modflow_variant",  "value": gam["variant"]},
        {"key": "gma_id",           "value": gam["gma_id"]},
        {"key": "aquifer",          "value": gam["aquifer"]},
        {"key": "twdb_gam",         "value": "true"},
        {"key": "data_source_url",  "value": gam.get("source", "")},
    ]
    
    # Add ESIPFed AI-Ready Data Checklist v1.1 defaults
    for key, value in ESIPFED_DEFAULTS.items():
        extras.append({"key": f"esipfed_{key}", "value": value})
    
    # Override with GAM-specific ESIPFed fields
    overrides = gam.get("esipfed_overrides", {})
    for key, value in overrides.items():
        # Remove existing default if present, then add override
        extras = [e for e in extras if e["key"] != f"esipfed_{key}"]
        extras.append({"key": f"esipfed_{key}", "value": value})
    
    return extras


def _ensure_dataset(gam: dict, dry: bool) -> tuple[str, dict]:
    """Return (dataset_id, package_show_result). Creates dataset if missing."""
    name = gam["ckan_name"]
    try:
        resp = _api_get("package_show", f"id={name}")
        if resp.get("success"):
            print(f"  Dataset exists: {name}")
            # Update extras if dataset already exists
            pkg = resp["result"]
            existing_extras = {e["key"]: e["value"] for e in pkg.get("extras", [])}
            new_extras = _build_extras(gam)
            extras_to_add = [e for e in new_extras if e["key"] not in existing_extras]
            extras_to_update = [e for e in new_extras if e["key"] in existing_extras 
                               and existing_extras[e["key"]] != e["value"]]
            if extras_to_add or extras_to_update:
                print(f"  Updating {len(extras_to_add)} new + {len(extras_to_update)} changed extras")
                if not dry:
                    update_payload = {
                        "id": pkg["id"],
                        "name": pkg["name"],
                        "title": pkg.get("title", ""),
                        "notes": pkg.get("notes", ""),
                        "private": pkg.get("private", False),
                        "owner_org": pkg.get("owner_org"),
                        "extras": pkg.get("extras", []) + extras_to_add,
                    }
                    # Update changed extras
                    for e in update_payload["extras"]:
                        for new_e in extras_to_update:
                            if e["key"] == new_e["key"]:
                                e["value"] = new_e["value"]
                    _api_post_json("package_update", update_payload)
            return pkg["id"], pkg
    except Exception as e:
        print(f"  WARNING: could not read/update existing dataset '{name}': {e}")

    tags = [{"name": t} for t in gam["tags"]]
    payload = {
        "name":       name,
        "title":      gam["label"],
        "notes":      gam["notes"],
        "owner_org":  OWNER_ORG,
        "private":    False,
        "type":       "dataset",
        "tags":       tags,
        "extras":     _build_extras(gam),
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
    """Register a GAM to CKAN with archive URL and ESIPFed metadata.
    
    The Tapis apps (modflow6-simulation, modflow-usg-simulation, etc.) handle
    archive download, extraction, and MODFLOW execution. We only need to:
    1. Create/update the dataset with metadata
    2. Register the archive URL as a resource
    
    Individual package files are NOT uploaded — the Tapis app extracts them
    from the archive at runtime.
    """
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

    upserted = 0

    # ── 1. Simulation archive URL ──────────────────────────────────────────
    # This is the only resource the Tapis app needs.
    # For URL sources: link to the TWDB S3 archive
    # For local dirs: we could zip and upload, but TWDB S3 is preferred
    if gam["source_type"] == "url":
        rname = f"{slug} — simulation archive"
        if dry:
            print(f"    [DRY] link {gam['source']} → '{rname}' fmt=simulation-archive")
        elif rname not in existing:
            # Build ESIPFed metadata for archive resource
            archive_extras = {
                "program_area": "Groundwater",
                "categories": "Groundwater",
                "collection_method": "Model Output",
                "quality_control_level": "Calibrated, history-matched GAM",
                "data_contact_email": "gam@twdb.texas.gov",
                "caveats_usage": "Regional groundwater planning and screening; not for site-specific design.",
            }
            if gam.get("esipfed_overrides", {}).get("temporal_coverage_start"):
                archive_extras["temporal_coverage_start"] = gam["esipfed_overrides"]["temporal_coverage_start"]
            if gam.get("esipfed_overrides", {}).get("temporal_coverage_end"):
                archive_extras["temporal_coverage_end"] = gam["esipfed_overrides"]["temporal_coverage_end"]
            
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
                **archive_extras,
            }
            resp = _api_post_json("resource_create", body)
            if resp.get("success"):
                print(f"    OK  linked simulation archive → '{rname}'")
            else:
                print(f"    ERR simulation archive link: {resp.get('error', resp)}")
            time.sleep(2)
        upserted += 1
    elif gam["source_type"] == "dir":
        # For local directories, we'd need to zip and upload
        # This is expensive for large models; prefer TWDB S3 URL
        print(f"  INFO: Local directory source — use TWDB S3 URL for Tapis app")
        print(f"  SKIP: No archive URL to register")
    else:
        print(f"  SKIP: Unknown source_type: {gam['source_type']}")

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
