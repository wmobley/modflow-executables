#!/usr/bin/env python3
"""Register the MODFLOW engines into the MINT v2 model catalog with full metadata
and wire each to its existing Tapis app so the Ensemble Manager can run it.

Builds, per variant, the catalog hierarchy:

    Software (MODFLOW)
      -> SoftwareVersion (MODFLOW 6 / -USG / -2000 / -96)
           -> ModelConfiguration  (has_software_image + has_component_location)
                -> DatasetSpecification inputs  (one per app.json fileInput)
                -> DatasetSpecification outputs (from models_metadata.json)
                -> Parameter (baseline data directory)
                -> ModelConfigurationSetup (per named GAM, with region + baseline dir)

Inputs come straight from each variant's app.json `fileInputs`; everything else
(authors, license, outputs, regions/GAMs, descriptions) comes from
models_metadata.json. A minimal component descriptor (id = the Tapis app id) is
generated per variant under scripts/components/ and referenced by
has_component_location -- the bridge the EM's Tapis path follows to the app.

Writes go through the model-catalog-api REST (/v2.0.0). All scalar fields are
arrays (RDF convention); relationships use hasInput/hasOutput/hasParameter etc.
Nested create is supported, so a ModelConfiguration POST creates its I/O+params
inline. Re-runs are safe (the API inserts ON CONFLICT DO NOTHING).

Usage:
    # dry-run everything (no network, prints payloads):
    python3 scripts/register_to_mint.py --dry-run

    # register all four against a local stack:
    MINT_API_TOKEN=e2e-test python3 scripts/register_to_mint.py \
        --api-base http://localhost:3001/v2.0.0

    # one variant:
    python3 scripts/register_to_mint.py --variant modflow6 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent  # modflow-executables/
METADATA_PATH = SCRIPTS_DIR / "models_metadata.json"
COMPONENTS_DIR = SCRIPTS_DIR / "components"

URI_BASE = "https://w3id.org/okn/i/mint/"
SVO_VAR_NS = "https://www.geoscienceontology.org/svo/svl/variable/"  # SVO variable IRIs
UNIT_NS = "https://w3id.org/okn/i/mint/unit/"
DEFAULT_API_BASE = os.environ.get("MINT_CATALOG_API_BASE", "http://localhost:3001/v2.0.0")
DEFAULT_COMPONENT_BASE_URL = os.environ.get(
    "MODFLOW_COMPONENT_BASE_URL",
    "https://raw.githubusercontent.com/wmobley/modflow-suite/main/modflow-executables/scripts/components",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def uri(slug: str) -> str:
    return f"{URI_BASE}{slug}"


# Read-only inventory captured from the current MINT dev catalog on 2026-09-21.
# Existing records are updated in place; only MODFLOW-USG uses the create path.
# The public catalog has additional historical duplicates, but the dev UI
# exposes one executable configuration for each repaired engine. Do not use
# public-catalog duplicate IDs here: the dev database does not contain them.
LIVE_CONFIG_IDS: dict[str, tuple[str, ...]] = {
    "modflow6": (
        uri("ce445698-1d76-4833-95e8-a12eb3da2488"),
    ),
    "modflow-2000": (uri("b8fa91f0-c000-4d5c-ade9-fa6eb8f0147b"),),
    "modflow-96": (uri("dcd878ae-5e7d-44c4-805b-7bd3f3fc1637"),),
}
LIVE_SOFTWARE_VERSION_IDS: dict[str, tuple[str, ...]] = {
    "modflow6": (uri("46073c82-1169-4854-94d2-a53d24732921"),),
    "modflow-2000": (
        uri("f6b90f6f-1d66-4993-a3bf-5338fa76b383"),
        uri("0179ee86-5c8e-4198-8961-9142dcf13d24"),
        uri("3d3093de-7bd4-4abd-b013-6bc13c58aba3"),
    ),
    "modflow-96": (
        uri("9fc65e72-413b-4e4e-b453-069efacefd69"),
        uri("35d1c232-b8ca-4a20-a2fa-bfb3a392ae2d"),
    ),
}
# Version currently attached to each executable configuration in the dev UI.
# This is intentionally separate from LIVE_SOFTWARE_VERSION_IDS above, which
# also includes the two unreferenced historical MODFLOW-2000 versions whose
# labels must be corrected.
LIVE_CONFIG_VERSION_IDS: dict[str, tuple[str, ...]] = {
    "modflow6": (uri("46073c82-1169-4854-94d2-a53d24732921"),),
    "modflow-2000": (uri("0179ee86-5c8e-4198-8961-9142dcf13d24"),),
    "modflow-96": (uri("35d1c232-b8ca-4a20-a2fa-bfb3a392ae2d"),),
}
LIVE_SOFTWARE_IDS: tuple[str, ...] = (
    uri("bc90aeb9-a5e6-4def-a574-9e45337849e4"),
    uri("69864dbb-68e5-4481-8d65-b8a2b861f956"),
    uri("044308bf-48f1-414e-b0ce-d3fb4b2408b7"),
)
NEW_CONFIG_VARIANTS = ("modflow-usg",)

# The archive is the baseline contract. Only these file inputs are exposed as
# catalog I/O; other Tapis slots remain implementation details of the app.
CATALOG_INPUT_CODES = {"simulation-archive", "wel", "rch", "rcha", "rchb", "rcha-02", "rcha-03"}


def arr(value: Any) -> list[Any]:
    """Wrap a scalar in the single-element array the catalog API expects."""
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def code_from_input_name(name: str) -> str:
    """`mf6-simulation-archive` -> `simulation-archive`, `mfusg-sms` -> `sms`."""
    return name.split("-", 1)[1] if "-" in name else name


def label_for_input(code: str, target_path: str, package_labels: dict[str, str]) -> str:
    base = re.sub(r"-\d+$", "", code)  # support-01 -> support
    base = {"model-nam": "model.nam", "sim-nam": "mfsim.nam"}.get(base, base)
    label = package_labels.get(base, base.upper())
    fname = os.path.basename(target_path)
    return f"{label} — {fname}" if fname else label


def fmt_for_input(target_path: str, code: str) -> str:
    base = os.path.basename(target_path)
    if "." in base:
        return base.rsplit(".", 1)[1]
    return re.sub(r"-\d+$", "", code)  # support-01 -> support


def _svo_iri(name: str) -> str:
    # Match the live MINT StandardVariable sameAs convention: HTTP namespace +
    # fragment. This is intentionally not treated as equivalent to the HTTPS
    # path form used by older local payloads.
    return "http://www.geoscienceontology.org/svo/svl/variable#" + name


def _unit_iri(slug: str) -> str:
    return UNIT_NS + slug


def build_presentation(code: str, vslug: str, suffix: str) -> dict[str, Any] | None:
    """A VariablePresentation binding an SVO StandardVariable + Unit to an I/O,
    if the package/output `code` has an svo_bindings entry. has_standard_variable
    and uses_unit are scalar URI columns (the standard_variable/unit entities are
    registered separately so the relationships resolve)."""
    base = re.sub(r"-\d+$", "", code)
    b = (_META.get("svo_bindings") or {}).get(base)
    if not b:
        return None
    vp = {
        "id": uri(f"{vslug}_vp_{suffix}"),
        "type": ["VariablePresentation"],
        "label": arr(b.get("long_name") or base),
        # StandardVariable is minted in the catalog namespace (w3id), with same_as
        # -> the SVO IRI on the StandardVariable entity itself.
        "has_standard_variable": arr(uri(b["variable"])),
        "uses_unit": arr(_unit_iri(b["unit"])),
    }
    if b.get("long_name"):
        vp["has_long_name"] = arr(b["long_name"])
    if b.get("short_name"):
        vp["has_short_name"] = arr(b["short_name"])
    return vp


def build_input_presentations(
    code: str,
    vslug: str,
    app: dict[str, Any],
    meta_variant: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the semantic presentations accepted by one catalog input.

    A package override has one presentation.  The simulation archive is
    different: it is the complete model bundle, so it may contain any of the
    semantically understood package variables exposed by the app.  Present
    each distinct variable once, in manifest order, so the UI can discover a
    reviewed archive by any variable it contains.
    """
    if code != "simulation-archive":
        presentation = build_presentation(code, vslug, f"in_{code}")
        return [presentation] if presentation else []

    reclass = set(meta_variant.get("reclassify_as_output", []))
    seen_variables: set[str] = set()
    presentations: list[dict[str, Any]] = []
    for file_input in app["jobAttributes"]["fileInputs"]:
        if file_input["name"] in reclass:
            continue
        package_code = code_from_input_name(file_input["name"])
        binding = (_META.get("svo_bindings") or {}).get(
            re.sub(r"-\d+$", "", package_code)
        )
        if not binding or binding["variable"] in seen_variables:
            continue
        presentation = build_presentation(
            package_code,
            vslug,
            f"in_simulation_archive_{binding['variable']}",
        )
        if presentation:
            presentations.append(presentation)
            seen_variables.add(binding["variable"])
    return presentations


def build_standard_variables() -> list[dict[str, Any]]:
    # id in the catalog namespace (matches the rest of the catalog + pre-existing
    # entries); same_as carries the canonical SVO IRI. reuse_existing entries
    # already exist and are reused (ON CONFLICT DO NOTHING) rather than duplicated.
    # same_as is a Postgres array column; setting it via the API's scalar-unwrap
    # path fails, so it's applied separately (patch_standard_variable_same_as).
    return [
        {"id": uri(sv["name"]), "type": ["StandardVariable"],
         "label": arr(sv["label"]), "description": arr(sv.get("description", ""))}
        for sv in _META.get("standard_variables", [])
    ]


def build_units() -> list[dict[str, Any]]:
    return [
        {"id": _unit_iri(u["slug"]), "type": ["Unit"], "label": arr(u["label"])}
        for u in _META.get("units", [])
    ]


# --------------------------------------------------------------------------- #
# payload builders
# --------------------------------------------------------------------------- #
def build_software_payload(meta: dict[str, Any]) -> dict[str, Any]:
    sw = meta["software"]
    authors = [
        {
            "id": uri(a["slug"]),
            "type": ["Person"],
            "label": arr(a["name"]),
            "name": arr(a["name"]),
        }
        for a in sw.get("authors", [])
    ]
    return {
        "id": uri(sw["slug"]),
        "type": ["Model"],
        "label": arr(sw["label"]),
        "description": arr(sw["description"]),
        # `keywords` is a scalar TEXT column -> single comma-joined string.
        "keywords": arr(", ".join(sw.get("keywords", []))),
        "license": arr(sw["license"]),
        "website": arr(sw["website"]),
        "authors": authors,
    }


def build_inputs(variant: str, app: dict[str, Any], meta_variant: dict[str, Any]) -> list[dict[str, Any]]:
    package_labels = _META["package_labels"]
    reclass = set(meta_variant.get("reclassify_as_output", []))
    file_inputs = app["jobAttributes"]["fileInputs"]
    vslug = meta_variant["version_slug"]
    out = []
    pos = 0
    for fi in file_inputs:
        name = fi["name"]
        if name in reclass:
            continue
        code = code_from_input_name(name)
        if code not in CATALOG_INPUT_CODES:
            continue
        target = fi.get("targetPath", "")
        pos += 1
        spec = {
            "id": uri(f"{vslug}_input_{code}"),
            "type": ["DatasetSpecification"],
            "label": arr(label_for_input(code, target, package_labels)),
            "description": arr(f"{name} -> {target} (Tapis fileInput on app '{app['id']}')."),
            "has_format": arr(fmt_for_input(target, code)),
            "position": arr(str(pos)),
            "isOptional": fi.get("inputMode", "OPTIONAL") == "OPTIONAL",
        }
        presentations = build_input_presentations(code, vslug, app, meta_variant)
        if presentations:
            spec["hasPresentation"] = presentations
        out.append(spec)
    return out


def build_outputs(meta_variant: dict[str, Any]) -> list[dict[str, Any]]:
    vslug = meta_variant["version_slug"]
    out = []
    for i, o in enumerate(meta_variant.get("outputs", []), start=1):
        spec = {
            "id": uri(f"{vslug}_output_{o['code']}"),
            "type": ["DatasetSpecification"],
            "label": arr(o["label"]),
            "has_format": arr(o["ext"]),
            "position": arr(str(i)),
        }
        pres = build_presentation(o["code"], vslug, f"out_{o['code']}")
        if pres:
            spec["hasPresentation"] = [pres]
        out.append(spec)
    return out


def _default_dir_value(app: dict[str, Any], param_name: str) -> str | None:
    for a in app["jobAttributes"].get("parameterSet", {}).get("appArgs", []):
        if a.get("name") == param_name:
            val = a.get("arg")
            return None if val in (None, "", "__NONE__") else val
    return None


def build_config_parameters(app: dict[str, Any], meta_variant: dict[str, Any]) -> list[dict[str, Any]]:
    vslug = meta_variant["version_slug"]
    default_dir = _default_dir_value(app, meta_variant.get("default_dir_param", ""))
    param = {
        "id": uri(f"{vslug}_param_baseline_dir"),
        "type": ["Parameter"],
        "label": arr("Baseline data directory"),
        "description": arr(
            "Path to a baseline/default MODFLOW dataset on the execution system. "
            "Missing input files are filled from here. Physical model parameters "
            "(hydraulic conductivity, storage, stresses) live inside the package "
            "input files, not as catalog parameters."
        ),
        "has_data_type": arr("string"),
        "parameter_type": arr("model_param"),
        "position": arr("1"),
    }
    if default_dir:
        param["has_default_value"] = arr(default_dir)
    return [param]


def build_setup_nodes(variant: str, app: dict[str, Any], meta_variant: dict[str, Any],
                      component_url: str) -> list[dict[str, Any]]:
    """Setup nodes (nested under a config's hasSetup, which sets model_configuration_id).

    Each setup inherits the Tapis app bindings from its parent configuration so the
    Ensemble Manager can execute the GAM-specific model variant."""
    vslug = meta_variant["version_slug"]
    nodes = []
    for s in meta_variant.get("setups", []):
        nodes.append({
            "id": uri(f"{vslug}_setup_{s['slug']}"),
            "type": ["ModelConfigurationSetup"],
            "label": arr(s["label"]),
            "description": arr(
                f"Region-specific setup of {meta_variant['label']} for "
                f"{s['region']}, pinned to the baseline GAM dataset on TACC."
            ),
            "has_region": arr(s["region"]),
            # Tapis execution bindings (inherited from parent config)
            "tapis_app_id": arr(app["id"]),
            "tapis_app_version": arr(app["version"]),
            "has_software_image": arr(app["containerImage"]),
            "has_component_location": arr(component_url),
            "hasParameter": [{
                "id": uri(f"{vslug}_param_baseline_{s['slug']}"),
                "type": ["Parameter"],
                "label": arr("Baseline data directory"),
                "has_data_type": arr("string"),
                "has_default_value": arr(s["baseline_dir"]),
                "parameter_type": arr("model_param"),
                "position": arr("1"),
            }],
        })
    return nodes


def build_config_node(variant: str, app: dict[str, Any], meta_variant: dict[str, Any],
                      component_url: str, config_id: str | None = None) -> dict[str, Any]:
    """ModelConfiguration node — standalone (not nested under a version).
    HasParameter and hasSetup use child-FK relationships which work with nested
    create. hasInput/hasOutput use junction relationships and must be linked
    separately via PUT to work around the API's junction-relationship bug."""
    vslug = meta_variant["version_slug"]
    return {
        "id": config_id or uri(f"{vslug}_cfg"),
        "type": ["ModelConfiguration"],
        "label": arr(f"{meta_variant['label']} configuration"),
        "description": arr(
            f"Executable configuration for {meta_variant['label']} "
            f"(Tapis app '{app['id']}', image {app['containerImage']})."
        ),
        "keywords": arr(", ".join(_META["software"].get("keywords", []))),
        "usage_notes": arr(meta_variant.get("usage_notes", "")),
        # Tapis execution bindings
        "tapis_app_id": arr(app["id"]),
        "tapis_app_version": arr(app["version"]),
        "has_software_image": arr(app["containerImage"]),
        "has_component_location": arr(component_url),
        "hasParameter": build_config_parameters(app, meta_variant),
        "hasSetup": build_setup_nodes(variant, app, meta_variant, component_url),
    }


def build_version_node(variant: str, app: dict[str, Any], meta_variant: dict[str, Any],
                       component_url: str) -> dict[str, Any]:
    """SoftwareVersion node nested under the software's hasVersion (sets software_id).
    Configurations are NOT nested here — they're created separately to work around
    the API's junction-relationship bug (POST can't cascade-create child entities
    through junction tables)."""
    return {
        "id": uri(meta_variant["version_slug"]),
        "type": ["SoftwareVersion"],
        "label": arr(meta_variant["label"]),
        "description": arr(meta_variant["description"]),
        "version_id": arr(meta_variant["version_id"]),
        "has_usage_notes": arr(meta_variant.get("usage_notes", "")),
        "has_source_code": arr(meta_variant["source_code"]),
    }


def build_component(app: dict[str, Any], meta_variant: dict[str, Any]) -> dict[str, Any]:
    """Minimal component descriptor bridging the catalog config to the Tapis app.
    id/version resolve to the registered Tapis app; inputs map to its fileInputs."""
    ja = app["jobAttributes"]
    reclass = set(meta_variant.get("reclassify_as_output", []))
    inputs = [
        {"id": fi["name"], "role": "input", "path": fi.get("targetPath", ""),
         "optional": fi.get("inputMode", "OPTIONAL") == "OPTIONAL"}
        for fi in ja["fileInputs"] if fi["name"] not in reclass
    ]
    outputs = [{"id": o["code"], "role": "output"} for o in meta_variant.get("outputs", [])]
    return {
        "id": app["id"],
        "version": app["version"],
        "name": meta_variant["label"],
        "softwareImage": app["containerImage"],
        "execSystemId": ja.get("execSystemId"),
        "execSystemLogicalQueue": ja.get("execSystemLogicalQueue"),
        "inputs": inputs,
        "outputs": outputs,
    }


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def post(api_base: str, resource: str, payload: dict[str, Any], token: str, dry_run: bool) -> None:
    label = (payload.get("label") or [payload.get("id")])[0]
    if dry_run:
        print(f"[dry-run] POST {api_base}/{resource}  ({payload['id']})")
        print(json.dumps(payload, indent=2))
        return
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{api_base}/{resource}", data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(f"[ok {resp.status}] {resource}: {label}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        # Already-exists is fine (reused entities like the pre-existing SVOs, and
        # idempotent re-runs): tolerate 409 + uniqueness violations.
        if e.code == 409 or "Uniqueness violation" in body or "duplicate key" in body:
            print(f"[exists] {resource}: {label}")
            return
        print(f"[HTTP {e.code}] {resource}: {label}\n   {body}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach {api_base} ({e.reason}). Is the model-catalog-api running?")


def reset_catalog() -> None:
    """Delete the MODFLOW catalog rows (+ junctions, persons) directly via Hasura,
    so a re-run starts clean (the API inserts ON CONFLICT DO NOTHING)."""
    hasura_url = os.environ.get("HASURA_GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    secret = os.environ.get("HASURA_ADMIN_SECRET", "localdev")
    like = "%/modflow%"
    # Net-new standard variables only (reuse_existing ones — e.g. the pre-existing
    # groundwater__hydraulic_head / aquifer__storativity — are preserved, not deleted).
    new_sv_ids = json.dumps([uri(sv["name"]) for sv in _META.get("standard_variables", [])
                             if not sv.get("reuse_existing")])
    unit_ids = json.dumps([_unit_iri(u["slug"]) for u in _META.get("units", [])])
    mutation = (
        "mutation {"
        f' p: delete_modelcatalog_dataset_specification_presentation(where:{{dataset_specification_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' a: delete_modelcatalog_configuration_input(where:{{configuration_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' b: delete_modelcatalog_configuration_output(where:{{configuration_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' c: delete_modelcatalog_configuration_parameter(where:{{configuration_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' d: delete_modelcatalog_configuration(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' e: delete_modelcatalog_software_version(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' f: delete_modelcatalog_dataset_specification(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' g: delete_modelcatalog_parameter(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' vp: delete_modelcatalog_variable_presentation(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        ' svext: delete_modelcatalog_standard_variable(where:{id:{_like:"%geoscienceontology.org%"}}){affected_rows}'
        f' sv: delete_modelcatalog_standard_variable(where:{{id:{{_in:{new_sv_ids}}}}}){{affected_rows}}'
        f' un: delete_modelcatalog_unit(where:{{id:{{_in:{unit_ids}}}}}){{affected_rows}}'
        ' h: delete_modelcatalog_software_author(where:{software_id:{_eq:"https://w3id.org/okn/i/mint/MODFLOW"}}){affected_rows}'
        ' i: delete_modelcatalog_software(where:{id:{_eq:"https://w3id.org/okn/i/mint/MODFLOW"}}){affected_rows}'
        ' j: delete_modelcatalog_person(where:{id:{_like:"%/person-%"}}){affected_rows}'
        "}"
    )
    req = urllib.request.Request(
        hasura_url, data=json.dumps({"query": mutation}).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-hasura-admin-secret": secret},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    if body.get("errors"):
        raise SystemExit(f"reset failed: {body['errors']}")
    print("[reset] cleared existing MODFLOW catalog rows")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def _collect_vps(node: Any, acc: list[tuple[str, str, str]]) -> None:
    """Walk the nested tree and collect (vp_id, svo_iri, unit_iri) from every
    hasPresentation entry, so we can set the scalar link columns post-create."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "hasPresentation":
                for vp in val:
                    acc.append((vp["id"], vp["has_standard_variable"][0], vp["uses_unit"][0]))
            else:
                _collect_vps(val, acc)
    elif isinstance(node, list):
        for item in node:
            _collect_vps(item, acc)


def _collect_vps_from_variants(variants: list[str], acc: list[tuple[str, str, str]]) -> None:
    """Collect VP patches from all variants' inputs/outputs (works without nested tree)."""
    for variant in variants:
        mv = _META["variants"][variant]
        app = _load(REPO_ROOT / mv["dir"] / "app.json")
        for spec in build_inputs(variant, app, mv) + build_outputs(mv):
            for pres in spec.get("hasPresentation", []):
                acc.append((pres["id"], pres["has_standard_variable"][0], pres["uses_unit"][0]))


def patch_presentation_links(patches: list[tuple[str, str, str]]) -> None:
    """Set has_standard_variable + uses_unit scalar columns on the created
    VariablePresentations via Hasura (the API treats them as object relationships
    and won't link an existing StandardVariable/Unit by id on create)."""
    if not patches:
        return
    hasura_url = os.environ.get("HASURA_GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    secret = os.environ.get("HASURA_ADMIN_SECRET", "localdev")
    groups: dict[tuple[str, str], list[str]] = {}
    for vp_id, sv, un in patches:
        groups.setdefault((sv, un), []).append(vp_id)
    parts = []
    for i, ((sv, un), ids) in enumerate(groups.items()):
        parts.append(
            f"g{i}: update_modelcatalog_variable_presentation("
            f"where:{{id:{{_in:{json.dumps(ids)}}}}}, "
            f"_set:{{has_standard_variable:{json.dumps(sv)}, uses_unit:{json.dumps(un)}}}"
            "){affected_rows}"
        )
    mutation = "mutation { " + " ".join(parts) + " }"
    req = urllib.request.Request(
        hasura_url, data=json.dumps({"query": mutation}).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-hasura-admin-secret": secret},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    if body.get("errors"):
        raise SystemExit(f"presentation link patch failed: {body['errors']}")
    total = sum(v["affected_rows"] for v in body["data"].values())
    print(f"[svo] linked {total} variable presentations -> StandardVariable + Unit")


def patch_standard_variable_same_as() -> None:
    """Set same_as = the canonical SVO IRI on every StandardVariable we manage
    (covers reused entities that ON CONFLICT DO NOTHING won't update, and guards
    against the create path dropping the scalar)."""
    pairs = [(uri(sv["name"]), _svo_iri(sv["name"])) for sv in _META.get("standard_variables", [])]
    if not pairs:
        return
    hasura_url = os.environ.get("HASURA_GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    secret = os.environ.get("HASURA_ADMIN_SECRET", "localdev")
    mutation = build_same_as_mutation(pairs)
    req = urllib.request.Request(
        hasura_url, data=json.dumps({"query": mutation}).encode(),
        method="POST", headers={"Content-Type": "application/json", "x-hasura-admin-secret": secret},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    if body.get("errors"):
        raise SystemExit(f"same_as patch failed: {body['errors']}")
    print(f"[svo] set same_as -> SVO IRI on {sum(v['affected_rows'] for v in body['data'].values())} standard variables")


def build_same_as_mutation(pairs: list[tuple[str, str]]) -> str:
    """Build the Hasura mutation for the PostgreSQL text[] same_as column.

    Hasura exposes PostgreSQL text[] as the `_text` scalar. Its GraphQL value
    must therefore be a PostgreSQL array-literal string, not a JSON array and
    not the bare SVO URL.
    """
    parts = []
    for i, (svid, svo) in enumerate(pairs):
        escaped_svo = svo.replace("\\", "\\\\").replace('"', '\\"')
        array_literal = '{"' + escaped_svo + '"}'
        parts.append(
            f"s{i}: update_modelcatalog_standard_variable("
            f"where:{{id:{{_eq:{json.dumps(svid)}}}}}, "
            f"_set:{{same_as:{json.dumps(array_literal)}}}"
            "){affected_rows}"
        )
    return "mutation { " + " ".join(parts) + " }"


# --------------------------------------------------------------------------- #
# Phase 2: standalone entity creation + junction linking
# --------------------------------------------------------------------------- #
def _put(api_base: str, resource: str, entity_id: str, payload: dict[str, Any],
         token: str, dry_run: bool) -> None:
    """PUT /resources/{encoded_id} to update an existing entity (e.g. link junctions)."""
    encoded = urllib.request.quote(entity_id, safe="")
    label = (payload.get("label") or [entity_id])[0]
    if dry_run:
        print(f"[dry-run] PUT {api_base}/{resource}/{encoded}  ({entity_id})")
        return
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{api_base}/{resource}/{encoded}", data=data, method="PUT",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(f"[ok {resp.status}] PUT {resource}: {label}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        if e.code == 409 or "Uniqueness violation" in body or "duplicate key" in body:
            print(f"[exists] PUT {resource}: {label}")
            return
        print(f"[HTTP {e.code}] PUT {resource}: {label}\n   {body}", file=sys.stderr)
        raise


def _repair_config_payload(config: dict[str, Any], software_version_id: str) -> dict[str, Any]:
    """Keep scalar execution metadata while avoiding nested relationship rewrites."""
    payload = {
        key: value for key, value in config.items()
        if key not in ("hasParameter", "hasSetup")
    }
    # software_version_id is a scalar FK on modelcatalog_configuration. Sending
    # the scalar avoids the object-FK path, which is not consistently supported
    # by the deployed dev REST mapper.
    payload["softwareVersionId"] = [software_version_id]
    return payload


def register_configurations(api_base: str, token: str, variants: list[str], dry_run: bool,
                            component_base_url: str) -> None:
    """Phase 2a: repair known configs; create only the new MODFLOW-USG config."""
    for variant in variants:
        mv = _META["variants"][variant]
        app = _load(REPO_ROOT / mv["dir"] / "app.json")
        component_url = f"{component_base_url.rstrip('/')}/{variant}.json"
        if variant in NEW_CONFIG_VARIANTS:
            config = build_config_node(variant, app, mv, component_url)
            post(api_base, "modelconfigurations", config, token, dry_run)
            _put(api_base, "modelconfigurations", config["id"], {
                "id": config["id"],
                "softwareVersionId": [uri(mv["version_slug"])],
            }, token, dry_run)
            continue

        target_ids = LIVE_CONFIG_IDS.get(variant, ())
        version_ids = LIVE_CONFIG_VERSION_IDS.get(variant, ())
        if len(target_ids) != len(version_ids):
            raise SystemExit(f"repair inventory mismatch for {variant}: configs={len(target_ids)} versions={len(version_ids)}")
        for target_id, version_id in zip(target_ids, version_ids):
            config = build_config_node(variant, app, mv, component_url, config_id=target_id)
            _put(api_base, "modelconfigurations", target_id,
                 _repair_config_payload(config, version_id), token, dry_run)


def repair_software_labels(api_base: str, token: str, dry_run: bool) -> None:
    """Correct the shared software/version labels used by the MF2000 duplicates."""
    for software_id in LIVE_SOFTWARE_IDS:
        _put(api_base, "models", software_id,
             {"id": software_id, "label": ["MODFLOW-2000"]}, token, dry_run)
    for version_id in LIVE_SOFTWARE_VERSION_IDS["modflow-2000"]:
        _put(api_base, "softwareversions", version_id,
             {"id": version_id, "label": ["MODFLOW-2000 MF2000"]}, token, dry_run)


def register_inputs_outputs(api_base: str, token: str, variants: list[str], dry_run: bool) -> None:
    """Phase 2b: create specs once, then replace each target config's I/O junctions."""
    for variant in variants:
        mv = _META["variants"][variant]
        app = _load(REPO_ROOT / mv["dir"] / "app.json")
        inputs = build_inputs(variant, app, mv)
        outputs = build_outputs(mv)
        all_specs = inputs + outputs
        for spec in all_specs:
            # Strip hasPresentation — we create VPs separately and link via junction
            spec_clean = {k: v for k, v in spec.items() if k != "hasPresentation"}
            post(api_base, "datasetspecifications", spec_clean, token, dry_run)
        target_ids = (uri(f"{mv['version_slug']}_cfg"),) if variant in NEW_CONFIG_VARIANTS else LIVE_CONFIG_IDS[variant]
        for config_id in target_ids:
            if inputs:
                _put(api_base, "modelconfigurations", config_id, {
                    "id": config_id,
                    "hasInput": [{"id": s["id"], "isOptional": s.get("isOptional", False)} for s in inputs],
                }, token, dry_run)
            if outputs:
                _put(api_base, "modelconfigurations", config_id, {
                    "id": config_id,
                    "hasOutput": [{"id": s["id"]} for s in outputs],
                }, token, dry_run)


def register_presentations(api_base: str, token: str, variants: list[str], dry_run: bool) -> None:
    """Phase 2c: Create standalone VariablePresentations, then link to specs via PUT."""
    for variant in variants:
        mv = _META["variants"][variant]
        app = _load(REPO_ROOT / mv["dir"] / "app.json")
        inputs = build_inputs(variant, app, mv)
        outputs = build_outputs(mv)
        all_specs = inputs + outputs
        for spec in all_specs:
            pres_list = spec.get("hasPresentation", [])
            for pres in pres_list:
                # Strip scalar link columns — patched post-create via Hasura
                pres_clean = {k: v for k, v in pres.items()
                              if k not in ("has_standard_variable", "uses_unit")}
                post(api_base, "variablepresentations", pres_clean, token, dry_run)
            # Link all presentations in one PUT.  Sending one presentation per
            # request would make a multi-variable archive depend on whether the
            # REST mapper appends or replaces the relationship array.
            if pres_list:
                _put(api_base, "datasetspecifications", spec["id"], {
                    "id": spec["id"],
                    "hasPresentation": [{"id": pres["id"]} for pres in pres_list],
                }, token, dry_run)


_META: dict[str, Any] = {}


def main(argv: list[str] | None = None) -> int:
    global _META
    parser = argparse.ArgumentParser(description="Register MODFLOW engines into the MINT v2 catalog.")
    parser.add_argument("--variant", action="append",
                        choices=["modflow6", "modflow-usg", "modflow-2000", "modflow-96"],
                        help="Variant(s) to register (default: all).")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="model-catalog-api /v2.0.0 base URL.")
    parser.add_argument("--component-base-url", default=DEFAULT_COMPONENT_BASE_URL,
                        help="Base URL where the generated component descriptors are served.")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads; make no network calls.")
    parser.add_argument("--no-components", action="store_true", help="Do not (re)write component descriptors.")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing MODFLOW catalog rows via Hasura before registering "
                             "(needs HASURA_GRAPHQL_URL + HASURA_ADMIN_SECRET; the API inserts "
                             "ON CONFLICT DO NOTHING, so a reset is required to re-link/refresh).")
    args = parser.parse_args(argv)

    _META = _load(METADATA_PATH)
    token = os.environ.get("MINT_API_TOKEN") or os.environ.get("TOKEN")
    if not token and not args.dry_run and not args.api_base.startswith(
        ("http://localhost", "http://127.0.0.1")
    ):
        raise SystemExit(
            "MINT_API_TOKEN (or TOKEN) is required for non-local MINT API writes; "
            "refusing to use the local e2e-test token."
        )
    token = token or "e2e-test"
    variants = args.variant or ["modflow6", "modflow-usg", "modflow-2000", "modflow-96"]

    if args.reset and not args.dry_run:
        reset_catalog()

    # Phase 1: SVO StandardVariables + Units
    for unit in build_units():
        post(args.api_base, "units", unit, token, args.dry_run)
    for sv in build_standard_variables():
        post(args.api_base, "standardvariables", sv, token, args.dry_run)

    # Phase 1: ensure the software grouping exists without nesting existing or
    # net-new versions into a potentially-conflicting parent POST.
    if not args.no_components and not args.dry_run:
        COMPONENTS_DIR.mkdir(exist_ok=True)

    software = build_software_payload(_META)
    new_version_nodes = []
    for variant in variants:
        mv = _META["variants"][variant]
        app = _load(REPO_ROOT / mv["dir"] / "app.json")
        component = build_component(app, mv)
        component_url = f"{args.component_base_url.rstrip('/')}/{variant}.json"
        if args.dry_run:
            print(f"[dry-run] component {variant}.json -> {component_url}")
        elif not args.no_components:
            (COMPONENTS_DIR / f"{variant}.json").write_text(json.dumps(component, indent=2) + "\n")
            print(f"[wrote] components/{variant}.json")
    post(args.api_base, "models", software, token, args.dry_run)

    # Create only the net-new USG version and attach it to the existing MODFLOW
    # software node explicitly. This avoids losing the version when the parent
    # model already exists and its POST is treated as a conflict.
    for variant in NEW_CONFIG_VARIANTS:
        mv = _META["variants"][variant]
        app = _load(REPO_ROOT / mv["dir"] / "app.json")
        component_url = f"{args.component_base_url.rstrip('/')}/{variant}.json"
        version = build_version_node(variant, app, mv, component_url)
        # software_id is a scalar FK on modelcatalog_software_version. Using
        # it directly keeps the new version attached to the MODFLOW model on
        # the deployed REST mapper.
        version["softwareId"] = [uri(_META["software"]["slug"])]
        new_version_nodes.append(version)
    for version in new_version_nodes:
        post(args.api_base, "softwareversions", version, token, args.dry_run)

    # Phase 1b: repair the mislabeled live MF2000 software/version records.
    repair_software_labels(args.api_base, token, args.dry_run)

    # Phase 2a: repair known configurations; create only MODFLOW-USG.
    print("\n--- Phase 2a: repair/create configurations ---")
    register_configurations(args.api_base, token, variants, args.dry_run, args.component_base_url)

    # Phase 2b: Create dataset specifications + link to configurations
    print("\n--- Phase 2b: inputs/outputs ---")
    register_inputs_outputs(args.api_base, token, variants, args.dry_run)

    # Phase 2c: Create variable presentations + link to specs
    print("\n--- Phase 2c: variable presentations ---")
    register_presentations(args.api_base, token, variants, args.dry_run)

    # Phase 3: Patch SVO scalar links (has_standard_variable, uses_unit, same_as)
    if not args.dry_run:
        print("\n--- Phase 3: SVO patches ---")
        vps: list[tuple[str, str, str]] = []
        _collect_vps_from_variants(variants, vps)
        patch_presentation_links(vps)
        patch_standard_variable_same_as()

    print("\nDone." + (" (dry-run, no changes made)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
