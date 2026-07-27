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
    return SVO_VAR_NS + name


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
        pres = build_presentation(code, vslug, f"in_{code}")
        if pres:
            spec["hasPresentation"] = [pres]
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


def build_setup_nodes(meta_variant: dict[str, Any]) -> list[dict[str, Any]]:
    """Setup nodes (nested under a config's hasSetup, which sets model_configuration_id)."""
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
                      component_url: str) -> dict[str, Any]:
    """ModelConfiguration node nested under a version's hasConfiguration (sets
    software_version_id). Carries the executable wiring + full I/O + setups."""
    vslug = meta_variant["version_slug"]
    return {
        "id": uri(f"{vslug}_cfg"),
        "type": ["ModelConfiguration"],
        "label": arr(f"{meta_variant['label']} configuration"),
        "description": arr(
            f"Executable configuration for {meta_variant['label']} "
            f"(Tapis app '{app['id']}', image {app['containerImage']})."
        ),
        "keywords": arr(", ".join(_META["software"].get("keywords", []))),
        "usage_notes": arr(meta_variant.get("usage_notes", "")),
        "has_software_image": arr(app["containerImage"]),
        "has_component_location": arr(component_url),
        "hasInput": build_inputs(variant, app, meta_variant),
        "hasOutput": build_outputs(meta_variant),
        "hasParameter": build_config_parameters(app, meta_variant),
        "hasSetup": build_setup_nodes(meta_variant),
    }


def build_version_node(variant: str, app: dict[str, Any], meta_variant: dict[str, Any],
                       component_url: str) -> dict[str, Any]:
    """SoftwareVersion node nested under the software's hasVersion (sets software_id)."""
    return {
        "id": uri(meta_variant["version_slug"]),
        "type": ["SoftwareVersion"],
        "label": arr(meta_variant["label"]),
        "description": arr(meta_variant["description"]),
        "version_id": arr(meta_variant["version_id"]),
        "has_usage_notes": arr(meta_variant.get("usage_notes", "")),
        "has_source_code": arr(meta_variant["source_code"]),
        "hasConfiguration": [build_config_node(variant, app, meta_variant, component_url)],
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
    parts = [
        f"s{i}: update_modelcatalog_standard_variable("
        f"where:{{id:{{_eq:{json.dumps(svid)}}}}}, _set:{{same_as:[{json.dumps(svo)}]}}"
        "){affected_rows}"
        for i, (svid, svo) in enumerate(pairs)
    ]
    req = urllib.request.Request(
        hasura_url, data=json.dumps({"query": "mutation { " + " ".join(parts) + " }"}).encode(),
        method="POST", headers={"Content-Type": "application/json", "x-hasura-admin-secret": secret},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    if body.get("errors"):
        raise SystemExit(f"same_as patch failed: {body['errors']}")
    print(f"[svo] set same_as -> SVO IRI on {sum(v['affected_rows'] for v in body['data'].values())} standard variables")


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
    token = os.environ.get("MINT_API_TOKEN") or os.environ.get("TOKEN") or "e2e-test"
    variants = args.variant or ["modflow6", "modflow-usg", "modflow-2000", "modflow-96"]

    if args.reset and not args.dry_run:
        reset_catalog()

    # SVO StandardVariables + Units first, so the VariablePresentations attached to
    # the model I/O resolve to real entities (labels/units).
    for unit in build_units():
        post(args.api_base, "units", unit, token, args.dry_run)
    for sv in build_standard_variables():
        post(args.api_base, "standardvariables", sv, token, args.dry_run)

    # Build ONE top-down nested tree rooted at the Model, so the parent->child
    # relationships (hasVersion/hasConfiguration/hasSetup) set the FK columns. A
    # single POST /models creates the whole hierarchy.
    if not args.no_components and not args.dry_run:
        COMPONENTS_DIR.mkdir(exist_ok=True)

    software = build_software_payload(_META)
    version_nodes = []
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
        version_nodes.append(build_version_node(variant, app, mv, component_url))
    software["hasVersion"] = version_nodes

    post(args.api_base, "models", software, token, args.dry_run)

    # Link the just-created VariablePresentations to their SVO StandardVariable +
    # Unit (scalar columns the create path leaves unset).
    if not args.dry_run:
        vps: list[tuple[str, str, str]] = []
        _collect_vps(software, vps)
        patch_presentation_links(vps)
        patch_standard_variable_same_as()

    print("\nDone." + (" (dry-run, no changes made)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
