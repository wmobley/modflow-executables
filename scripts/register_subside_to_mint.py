#!/usr/bin/env python3
"""Register the SUBSIDE OPERA DISP-S1 models into the MINT v2 model catalog.

Source of truth is the SUBSIDE catalog YAML
(subside/tapis/workflow_apps/opera-disp-s1.model-catalog.yaml). This script is
a thin loader: it turns that already-nested document into REST payloads and
reuses only the transport/patch layer of register_to_mint.py (post, _put,
_legacy_payload, uri/arr, SVO/unit IRIs, build_same_as_mutation). It does NOT
use the MODFLOW build_* builders (they consume models_metadata.json).

Hierarchy registered (YAML ids are kept; catalog ids are w3id.org/okn/i/mint/<id>):

    Software  opera
    -> SoftwareVersion  opera-disp-s1-analysis
          -> ModelConfiguration  opera-disp-s1-h2i-lab   (Tapis app subside-h2i-opera-analysis)
          -> ModelConfiguration  opera-disp-s1-werc      (Tapis app subside-werc-opera-analysis)

Phases (same shape as the MODFLOW registration):
  1. Units + StandardVariables (tolerant 409; the displacement SVOs already exist
     on dev and are reused). Units: meter(m), millimeter(mm), millimeter_per_year(mm/yr).
  2. Software -> Version -> Configuration(+Parameters) nested top-down so the
     child FKs are set. keywords is one comma-joined scalar string.
  3. DatasetSpecifications (inputs/outputs), then junction PUTs onto each
     configuration (nested I/O hits the API's junction bug), then
     VariablePresentations linked to their specs.
  4. Hasura patch phase (the REST create path cannot set these):
       - configuration tapis_app_id / tapis_app_version / has_component_location
         (the hosted legacy mapper strips tapis_app_*; see _legacy_payload)
       - variable_presentation has_standard_variable + uses_unit scalar links
       - standard_variable same_as -> SVO IRI

Parameter name matching: the Ensemble Manager builds a model parameter's `name`
from the catalog parameter LABEL (graphql_adapter.ts modelParameterFromCatalogGQL:
`name: p.label || p.id`) and TapisJobService matches `parameter.name === <app arg
name>`. So the catalog parameter `label` is set to the Tapis env key (START_DATE,
...), read from the YAML field `tapisEnvKey`. The human label from the YAML moves
into the description ("Start date: ...").

Output ordering matters: the EM's default output_name is output_files[0], so the
YAML lists the displacement GeoTIFF first.

Not registered (not representable via the REST columns used here): Regions,
Constraints, parameter units, DatasetSpecification dimensionality.

Usage:
    python3 scripts/register_subside_to_mint.py --dry-run
    MINT_API_TOKEN=... python3 scripts/register_subside_to_mint.py \\
        --api-base https://mintdevapi.pods.portals.tapis.io/v2.0.0 --legacy-api-schema
    # --reset deletes the OPERA rows (by the YAML's exact ids) via Hasura first
    # (HASURA_GRAPHQL_URL + HASURA_ADMIN_SECRET).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import register_to_mint as R  # noqa: E402

uri, arr, post, _put = R.uri, R.arr, R.post, R._put
_svo_iri, _unit_iri = R._svo_iri, R._unit_iri

REPO_ROOT = SCRIPTS_DIR.parent.parent  # modflow-suite/
YAML_PATH = REPO_ROOT / "subside" / "tapis" / "workflow_apps" / "opera-disp-s1.model-catalog.yaml"

# Unit label (as written in the YAML) -> catalog unit slug. `meter` already
# exists in the MODFLOW registration; the others are created here if missing.
UNITS = {"m": "meter", "mm": "millimeter", "mm/yr": "millimeter_per_year"}
STANDARD_VARIABLES = ["land_surface__displacement", "land_surface__displacement_rate"]


# --------------------------------------------------------------------------- #
# YAML -> payloads
# --------------------------------------------------------------------------- #
def load_yaml(path: Path = YAML_PATH) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)["model"]


def _first(node: dict[str, Any], key: str, default: Any = None) -> Any:
    val = node.get(key)
    return val[0] if isinstance(val, list) and val else (default if val in (None, []) else val)


def _text(value: Any) -> str:
    return " ".join(str(value).split())


def build_parameter(p: dict[str, Any], pos: int) -> dict[str, Any]:
    env_key = p["tapisEnvKey"]
    out = {
        "id": uri(p["id"]),
        "type": ["Parameter"],
        # label == Tapis env key: the EM matches parameter.name (== label) to it.
        "label": arr(env_key),
        "description": arr(f"{_first(p, 'label')}: {_text(_first(p, 'description', ''))}"),
        "has_data_type": arr(_first(p, "hasDataType", "string")),
        "parameter_type": arr("model_param"),
        "position": arr(str(pos)),
    }
    default = _first(p, "hasDefaultValue")
    if default not in (None, ""):
        out["has_default_value"] = arr(str(default))
    for src, dst in (("hasMinimumAcceptedValue", "has_minimum_accepted_value"),
                     ("hasMaximumAcceptedValue", "has_maximum_accepted_value")):
        if p.get(src):
            out[dst] = arr(str(_first(p, src)))
    if p.get("hasAcceptedValues"):
        out["has_accepted_values"] = [str(v) for v in p["hasAcceptedValues"]]
    return out


def build_presentation(spec: dict[str, Any]) -> dict[str, Any] | None:
    binding = spec.get("variablePresentation")
    if not binding:
        return None
    return {
        "id": uri(f"{spec['id']}-vp"),
        "type": ["VariablePresentation"],
        "label": arr(binding["longName"]),
        "has_long_name": arr(binding["longName"]),
        "has_short_name": arr(binding["shortName"]),
        "has_standard_variable": arr(uri(binding["standardVariable"])),
        "uses_unit": arr(_unit_iri(UNITS[binding["unit"]])),
    }


def build_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """A DatasetSpecification (no presentations; those are linked separately)."""
    return {
        "id": uri(spec["id"]),
        "type": ["DatasetSpecification"],
        "label": arr(_first(spec, "label")),
        "description": arr(_text(_first(spec, "description", ""))),
        "has_format": arr(", ".join(spec.get("hasFormat", []))),
        "position": arr(str(_first(spec, "position", 1))),
    }


def build_config(cfg: dict[str, Any]) -> dict[str, Any]:
    image = _first(cfg["hasSoftwareImage"][0], "availableInRegistry")
    notes = []
    for key in ("memoryRequirements", "processorRequirements", "runtimeEstimation"):
        if cfg.get(key):
            notes.append(f"{key}: {_text(_first(cfg, key))}")
    notes += [_text(n) for n in cfg.get("hasUsageNotes", [])]
    notes += [f"Limitation: {_text(n)}" for n in cfg.get("limitations", [])]
    params = sorted(cfg.get("hasParameter", []), key=lambda p: _first(p, "position", 0))
    return {
        "id": uri(cfg["id"]),
        "type": ["ModelConfiguration"],
        "label": arr(_first(cfg, "label")),
        "description": arr(_text(_first(cfg, "description"))),
        "usage_notes": arr("\n".join(notes)),
        "tapis_app_id": arr(_first(cfg, "tapisAppId")),
        "tapis_app_version": arr(_first(cfg, "tapisAppVersion")),
        "has_software_image": arr(image),
        "has_component_location": arr(_first(cfg, "hasComponentLocation")),
        "has_implementation_script_location": arr(_first(cfg, "hasImplementationScriptLocation")),
        "hasParameter": [build_parameter(p, i) for i, p in enumerate(params, 1)],
    }


# modelcatalog_parameter.has_{minimum,maximum}_accepted_value are scalar TEXT
# columns on dev, while has_accepted_values is text[]. The MODFLOW legacy
# mapper never had to send min/max, so _legacy_payload leaves them as lists.
LEGACY_SCALAR_PARAMETER_KEYS = ("has_minimum_accepted_value", "has_maximum_accepted_value")


def legacy_parameter_scalars(node: Any) -> Any:
    """Unwrap one-element min/max lists to scalars (hosted --legacy-api-schema)."""
    if isinstance(node, list):
        return [legacy_parameter_scalars(x) for x in node]
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in LEGACY_SCALAR_PARAMETER_KEYS and isinstance(v, list) and len(v) == 1:
                out[k] = v[0]
            else:
                out[k] = legacy_parameter_scalars(v)
        return out
    return node


def build_software_tree(model: dict[str, Any]) -> dict[str, Any]:
    """Software -> SoftwareVersion -> ModelConfiguration, nested top-down."""
    ver = model["hasVersion"][0]
    version = {
        "id": uri(ver["id"]),
        "type": ["SoftwareVersion"],
        "label": arr(_first(ver, "label")),
        "description": arr(_text(_first(ver, "description"))),
        "version_id": arr(_first(ver, "hasVersionId")),
        "has_source_code": arr(_first(model["hasSourceCode"][0], "codeRepository")),
        "hasConfiguration": [build_config(c) for c in ver["hasConfiguration"]],
    }
    return {
        "id": uri(model["id"]),
        "type": ["Model"],
        "label": arr(_first(model, "label")),
        "description": arr(_text(_first(model, "description"))),
        # keywords is a scalar TEXT column -> single comma-joined string.
        "keywords": arr(", ".join(model.get("keywords", []))),
        "license": arr(_first(model, "license")),
        "website": arr(_first(model, "website")),
        "hasVersion": [version],
    }


def iter_configs(model: dict[str, Any]):
    for ver in model["hasVersion"]:
        yield from ver["hasConfiguration"]


def build_units() -> list[dict[str, Any]]:
    return [{"id": _unit_iri(slug), "type": ["Unit"], "label": arr(label)} for label, slug in UNITS.items()]


def build_standard_variables() -> list[dict[str, Any]]:
    return [{"id": uri(n), "type": ["StandardVariable"], "label": arr(n)} for n in STANDARD_VARIABLES]


# --------------------------------------------------------------------------- #
# Hasura (patch + reset)
# --------------------------------------------------------------------------- #
def _hasura(mutation: str) -> dict[str, Any]:
    url = os.environ.get("HASURA_GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    secret = os.environ.get("HASURA_ADMIN_SECRET", "localdev")
    req = urllib.request.Request(
        url, data=json.dumps({"query": mutation}).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-hasura-admin-secret": secret},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    if body.get("errors"):
        raise SystemExit(f"hasura mutation failed: {body['errors']}")
    return body["data"]


def build_patch_plan(model: dict[str, Any]) -> list[tuple[str, str]]:
    """(description, Hasura mutation) pairs applied after the REST creates."""
    plan: list[tuple[str, str]] = []
    # The deployed dev REST mapper does not reliably set parent FKs from a
    # nested create (register_to_mint.py sets softwareId/softwareVersionId
    # explicitly for the same reason). Set them outright so the hierarchy can
    # never be orphaned, whichever way the mapper behaved.
    software_id = uri(model["id"])
    version_id = uri(model["hasVersion"][0]["id"])
    fk_parts = [
        "v: update_modelcatalog_software_version("
        f"where:{{id:{{_eq:{json.dumps(version_id)}}}}}, "
        f"_set:{{software_id:{json.dumps(software_id)}}}){{affected_rows}}"
    ] + [
        f"c{i}: update_modelcatalog_configuration("
        f"where:{{id:{{_eq:{json.dumps(uri(cfg['id']))}}}}}, "
        f"_set:{{software_version_id:{json.dumps(version_id)}}}){{affected_rows}}"
        for i, cfg in enumerate(iter_configs(model))
    ]
    plan.append(("hierarchy parent FKs", "mutation { " + " ".join(fk_parts) + " }"))
    for cfg in iter_configs(model):
        c = build_config(cfg)
        plan.append((f"configuration bindings {c['id']}", (
            "mutation { update_modelcatalog_configuration("
            f"where:{{id:{{_eq:{json.dumps(c['id'])}}}}}, _set:{{"
            f"tapis_app_id:{json.dumps(c['tapis_app_id'][0])}, "
            f"tapis_app_version:{json.dumps(c['tapis_app_version'][0])}, "
            f"has_component_location:{json.dumps(c['has_component_location'][0])}"
            "}){affected_rows} }")))
    groups: dict[tuple[str, str], list[str]] = {}
    for cfg in iter_configs(model):
        for spec in cfg.get("hasOutput", []) + cfg.get("hasInput", []):
            vp = build_presentation(spec)
            if vp:
                groups.setdefault((vp["has_standard_variable"][0], vp["uses_unit"][0]), []).append(vp["id"])
    parts = [
        f"g{i}: update_modelcatalog_variable_presentation(where:{{id:{{_in:{json.dumps(ids)}}}}}, "
        f"_set:{{has_standard_variable:{json.dumps(sv)}, uses_unit:{json.dumps(un)}}}){{affected_rows}}"
        for i, ((sv, un), ids) in enumerate(groups.items())
    ]
    if parts:
        plan.append(("presentation SVO/unit links", "mutation { " + " ".join(parts) + " }"))
    plan.append(("standard variable same_as", R.build_same_as_mutation(
        [(uri(n), _svo_iri(n)) for n in STANDARD_VARIABLES])))
    return plan


def build_reset_mutation(model: dict[str, Any]) -> str:
    """Delete the OPERA rows by the YAML's exact ids (units/SVOs are shared; kept)."""
    cfg_ids, spec_ids, param_ids, vp_ids = [], [], [], []
    for cfg in iter_configs(model):
        cfg_ids.append(uri(cfg["id"]))
        param_ids += [uri(p["id"]) for p in cfg.get("hasParameter", [])]
        for spec in cfg.get("hasInput", []) + cfg.get("hasOutput", []):
            spec_ids.append(uri(spec["id"]))
            if build_presentation(spec):
                vp_ids.append(uri(f"{spec['id']}-vp"))
    ver_ids = [uri(v["id"]) for v in model["hasVersion"]]
    j = json.dumps
    return (
        "mutation {"
        f" p: delete_modelcatalog_dataset_specification_presentation(where:{{dataset_specification_id:{{_in:{j(spec_ids)}}}}}){{affected_rows}}"
        f" a: delete_modelcatalog_configuration_input(where:{{configuration_id:{{_in:{j(cfg_ids)}}}}}){{affected_rows}}"
        f" b: delete_modelcatalog_configuration_output(where:{{configuration_id:{{_in:{j(cfg_ids)}}}}}){{affected_rows}}"
        f" c: delete_modelcatalog_configuration_parameter(where:{{configuration_id:{{_in:{j(cfg_ids)}}}}}){{affected_rows}}"
        f" d: delete_modelcatalog_configuration(where:{{id:{{_in:{j(cfg_ids)}}}}}){{affected_rows}}"
        f" e: delete_modelcatalog_software_version(where:{{id:{{_in:{j(ver_ids)}}}}}){{affected_rows}}"
        f" f: delete_modelcatalog_dataset_specification(where:{{id:{{_in:{j(spec_ids)}}}}}){{affected_rows}}"
        f" g: delete_modelcatalog_parameter(where:{{id:{{_in:{j(param_ids)}}}}}){{affected_rows}}"
        f" vp: delete_modelcatalog_variable_presentation(where:{{id:{{_in:{j(vp_ids)}}}}}){{affected_rows}}"
        f" i: delete_modelcatalog_software(where:{{id:{{_eq:{j(uri(model['id']))}}}}}){{affected_rows}}"
        "}"
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def register_io(api_base: str, token: str, model: dict[str, Any], dry_run: bool) -> None:
    """Create DatasetSpecifications, link them to each configuration (junction
    PUTs), then create VariablePresentations and link them to their specs."""
    for cfg in iter_configs(model):
        cfg_id = uri(cfg["id"])
        inputs, outputs = cfg.get("hasInput", []), cfg.get("hasOutput", [])
        for spec in inputs + outputs:
            post(api_base, "datasetspecifications", build_spec(spec), token, dry_run)
        if inputs:
            _put(api_base, "modelconfigurations", cfg_id, {"id": cfg_id, "hasInput": [
                {"id": uri(s["id"]), "isOptional": bool(s.get("isOptional", False))} for s in inputs]},
                token, dry_run)
        if outputs:
            _put(api_base, "modelconfigurations", cfg_id, {"id": cfg_id, "hasOutput": [
                {"id": uri(s["id"])} for s in outputs]}, token, dry_run)
        for spec in inputs + outputs:
            vp = build_presentation(spec)
            if not vp:
                continue
            # scalar link columns are patched via Hasura afterwards
            post(api_base, "variablepresentations",
                 {k: v for k, v in vp.items() if k not in ("has_standard_variable", "uses_unit")},
                 token, dry_run)
            _put(api_base, "datasetspecifications", uri(spec["id"]),
                 {"id": uri(spec["id"]), "hasPresentation": [{"id": vp["id"]}]}, token, dry_run)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Register the SUBSIDE OPERA models into the MINT v2 catalog.")
    ap.add_argument("--api-base", default=R.DEFAULT_API_BASE, help="model-catalog-api /v2.0.0 base URL.")
    ap.add_argument("--yaml", type=Path, default=YAML_PATH, help="Catalog YAML (default: the SUBSIDE one).")
    ap.add_argument("--dry-run", action="store_true", help="Print payloads and Hasura mutations; no network calls.")
    ap.add_argument("--legacy-api-schema", action="store_true",
                    help="Use hosted legacy MINT REST field names (drops tapis_app_* from the wire payload; "
                         "the Hasura patch phase restores them).")
    ap.add_argument("--reset", action="store_true",
                    help="Delete the OPERA catalog rows via Hasura before registering.")
    args = ap.parse_args(argv)
    R.LEGACY_API_SCHEMA = args.legacy_api_schema

    token = os.environ.get("MINT_API_TOKEN") or os.environ.get("TOKEN")
    if not token and not args.dry_run and not args.api_base.startswith(
        ("http://localhost", "http://127.0.0.1")
    ):
        raise SystemExit("MINT_API_TOKEN (or TOKEN) is required for non-local MINT API writes.")
    token = token or "e2e-test"

    model = load_yaml(args.yaml)

    if args.reset:
        mutation = build_reset_mutation(model)
        if args.dry_run:
            print("[dry-run] HASURA reset\n" + mutation)
        else:
            _hasura(mutation)
            print("[reset] cleared existing OPERA catalog rows")

    print("--- Phase 1: units + standard variables ---")
    for payload in build_units():
        post(args.api_base, "units", payload, token, args.dry_run)
    for payload in build_standard_variables():
        post(args.api_base, "standardvariables", payload, token, args.dry_run)

    print("\n--- Phase 2: Software -> Version -> Configurations (nested) ---")
    tree = build_software_tree(model)
    if args.legacy_api_schema:
        tree = legacy_parameter_scalars(tree)
    post(args.api_base, "models", tree, token, args.dry_run)

    print("\n--- Phase 3: inputs/outputs + presentations ---")
    register_io(args.api_base, token, model, args.dry_run)

    print("\n--- Phase 4: Hasura patches ---")
    for desc, mutation in build_patch_plan(model):
        if args.dry_run:
            print(f"[dry-run] HASURA {desc}\n{mutation}")
        else:
            _hasura(mutation)
            print(f"[patched] {desc}")

    print("\nDone." + (" (dry-run, no changes made)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
