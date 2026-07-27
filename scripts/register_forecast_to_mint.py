#!/usr/bin/env python3
"""Register the SUBSIDE subsidence forecast as a MINT model, SVO-typed so its
inputs line up with the MODFLOW outputs already in the catalog.

The forecast (subside/analysis/subsidence/forecast.py + model.py) is an in-process
parametric aquifer screening model: ~24 scalar inputs -> annual subsidence + a
0-10 risk score. Here we register it as Software -> SoftwareVersion ->
ModelConfiguration where the integration-relevant inputs are DatasetSpecifications
carrying the SAME StandardVariables MODFLOW produces:

    groundwater__hydraulic_head   (MODFLOW heads)        -> forecast water levels
    aquifer__storativity          (MODFLOW STO)          -> storage coefficient
    land_surface__elevation       (DEM / geology)        -> land surface

so the svo-adapter can match MODFLOW outputs to forecast inputs. Outputs add two
new SVO variables (land_surface__subsidence, ...__subsidence_hazard_index). The
remaining scalar config (years, compressibilities, porosity, trend, lithology)
are registered as Parameters with the model's defaults.

Reuses the low-level helpers + conventions from register_to_mint.py (w3id-namespace
StandardVariables with same_as -> SVO IRI; presentations linked via Hasura; the
API's create path leaves the scalar SVO/unit columns unset, so we patch them).

Usage:
    python3 scripts/register_forecast_to_mint.py --dry-run
    MINT_API_TOKEN=e2e-test python3 scripts/register_forecast_to_mint.py \
        --api-base http://localhost:3009/v2.0.0 --reset
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import register_to_mint as R  # reuse uri/arr/_svo_iri/_unit_iri/post  # noqa: E402

uri, arr, post = R.uri, R.arr, R.post
_svo_iri, _unit_iri = R._svo_iri, R._unit_iri

# --- SVO standard variables (reuse existing where possible) ------------------
# reuse: groundwater__hydraulic_head, aquifer__storativity, land_surface__elevation
NEW_STANDARD_VARIABLES = [
    {"name": "land_surface__subsidence", "label": "land_surface__subsidence",
     "description": "Vertical subsidence (downward displacement) of the land surface."},
    {"name": "land_surface__subsidence_hazard_index", "label": "land_surface__subsidence_hazard_index",
     "description": "Weighted 0-10 subsidence hazard/risk index from the SUBSIDE screening model."},
]
ALL_FORECAST_SVOS = NEW_STANDARD_VARIABLES + [
    {"name": "groundwater__hydraulic_head"}, {"name": "aquifer__storativity"},
    {"name": "land_surface__elevation"},
]

NEW_UNITS = [
    {"slug": "foot", "label": "ft"},
    {"slug": "foot_per_year", "label": "ft/yr"},
    {"slug": "dimensionless", "label": "1"},
]

SW = "subside-forecast"
VER = "subside-forecast-1"
CFG = "subside-forecast-cfg"

# (dataset-spec inputs that carry an SVO, fed by MODFLOW / data)
SVO_INPUTS = [
    {"slug": "water_level", "label": "Groundwater water level (hydraulic head)",
     "svo": "groundwater__hydraulic_head", "unit": "foot", "format": "geotiff",
     "long": "groundwater hydraulic head / water level", "short": "head"},
    {"slug": "storage_coefficient", "label": "Aquifer storage coefficient",
     "svo": "aquifer__storativity", "unit": "dimensionless", "format": "geotiff",
     "long": "aquifer storativity", "short": "S"},
    {"slug": "land_surface", "label": "Land surface elevation",
     "svo": "land_surface__elevation", "unit": "foot", "format": "geotiff",
     "long": "land surface elevation", "short": "land_surface"},
]

SVO_OUTPUTS = [
    {"slug": "subsidence", "label": "Projected land subsidence",
     "svo": "land_surface__subsidence", "unit": "foot", "format": "json",
     "long": "projected annual land subsidence", "short": "subsidence"},
    {"slug": "risk_score", "label": "Subsidence risk score (0-10)",
     "svo": "land_surface__subsidence_hazard_index", "unit": "dimensionless", "format": "json",
     "long": "weighted subsidence risk score", "short": "risk"},
]

# scalar config -> Parameters (label, data_type, default, unit-note)
PARAMETERS = [
    {"slug": "start_year", "label": "Start year", "type": "int", "default": "2010"},
    {"slug": "end_year", "label": "End year", "type": "int", "default": "2070"},
    {"slug": "water_level_trend_ft_per_year", "label": "Water level trend (ft/yr)", "type": "float", "default": "0.0"},
    {"slug": "aquifer_thickness_ft", "label": "Aquifer thickness (ft)", "type": "float", "default": ""},
    {"slug": "clay_thickness_ft", "label": "Clay thickness (ft)", "type": "float", "default": ""},
    {"slug": "aquifer_porosity_pct", "label": "Aquifer porosity (%)", "type": "float", "default": "35.0"},
    {"slug": "clay_porosity_pct", "label": "Clay porosity (%)", "type": "float", "default": "50.0"},
    {"slug": "aq_comp_min_psi_inv", "label": "Aquifer compressibility min (1/psi)", "type": "float", "default": "5.2e-08"},
    {"slug": "aq_comp_max_psi_inv", "label": "Aquifer compressibility max (1/psi)", "type": "float", "default": "1.0e-07"},
    {"slug": "clay_comp_min_psi_inv", "label": "Clay compressibility min (1/psi)", "type": "float", "default": "2.6e-07"},
    {"slug": "clay_comp_max_psi_inv", "label": "Clay compressibility max (1/psi)", "type": "float", "default": "2.0e-06"},
    {"slug": "aquifer_lithology", "label": "Aquifer lithology", "type": "string", "default": "Unconsolidated Clastic"},
    {"slug": "water_level_method", "label": "Water level method", "type": "string", "default": "Current and Trend"},
]


def _spec(side: str, s: dict[str, Any], pos: int) -> dict[str, Any]:
    """A DatasetSpecification (input/output) with an SVO VariablePresentation."""
    return {
        "id": uri(f"{CFG}_{side}_{s['slug']}"),
        "type": ["DatasetSpecification"],
        "label": arr(s["label"]),
        "has_format": arr(s["format"]),
        "position": arr(str(pos)),
        "hasPresentation": [{
            "id": uri(f"{CFG}_vp_{side}_{s['slug']}"),
            "type": ["VariablePresentation"],
            "label": arr(s["long"]),
            "has_long_name": arr(s["long"]),
            "has_short_name": arr(s["short"]),
            "has_standard_variable": arr(uri(s["svo"])),  # w3id id; same_as patched after
            "uses_unit": arr(_unit_iri(s["unit"])),
        }],
    }


def _param(p: dict[str, Any], pos: int) -> dict[str, Any]:
    out = {
        "id": uri(f"{CFG}_param_{p['slug']}"),
        "type": ["Parameter"],
        "label": arr(p["label"]),
        "has_data_type": arr(p["type"]),
        "parameter_type": arr("model_param"),
        "position": arr(str(pos)),
    }
    if p.get("default") not in (None, ""):
        out["has_default_value"] = arr(p["default"])
    return out


def build_software_tree() -> dict[str, Any]:
    inputs = [_spec("input", s, i) for i, s in enumerate(SVO_INPUTS, 1)]
    outputs = [_spec("output", s, i) for i, s in enumerate(SVO_OUTPUTS, 1)]
    params = [_param(p, i) for i, p in enumerate(PARAMETERS, 1)]
    config = {
        "id": uri(CFG),
        "type": ["ModelConfiguration"],
        "label": arr("SUBSIDE subsidence forecast configuration"),
        "description": arr(
            "In-process parametric aquifer subsidence screening model. Inputs are "
            "SVO-typed so MODFLOW outputs (hydraulic head, storativity) can supply "
            "them via the svo-adapter; outputs are an annual subsidence projection "
            "and a 0-10 risk score."
        ),
        "keywords": arr("subsidence, groundwater, hazard, screening, SUBSIDE"),
        # In-process Python / API model (not a Tapis job): point at the entrypoint + API.
        "has_implementation_script_location": arr("analysis.subsidence.forecast.run_forecast"),
        "has_component_location": arr("https://subside-api.pods.portals.tapis.io/api/subside/forecast"),
        "hasInput": inputs,
        "hasOutput": outputs,
        "hasParameter": params,
    }
    version = {
        "id": uri(VER), "type": ["SoftwareVersion"],
        "label": arr("SUBSIDE Subsidence Forecast 1.0"),
        "description": arr("Parametric aquifer-system subsidence screening (annual projection + 0-10 risk)."),
        "version_id": arr("1.0"),
        "has_source_code": arr("https://github.com/wmobley/modflow-suite/tree/main/subside/analysis/subsidence"),
        "hasConfiguration": [config],
    }
    return {
        "id": uri(SW), "type": ["Software"],
        "label": arr("SUBSIDE Subsidence Forecast"),
        "description": arr(
            "Aquifer-system subsidence screening model (SUBSIDE). Projects annual "
            "land subsidence and a 0-10 risk score from water levels + aquifer properties."
        ),
        "keywords": arr("subsidence, groundwater, hazard, SUBSIDE, Texas"),
        "license": arr("https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits"),
        "website": arr("https://github.com/wmobley/modflow-suite/tree/main/subside"),
        "hasVersion": [version],
    }


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


def reset() -> None:
    like = "%/subside-forecast%"
    new_sv = json.dumps([uri(s["name"]) for s in NEW_STANDARD_VARIABLES])
    units = json.dumps([_unit_iri(u["slug"]) for u in NEW_UNITS])
    _hasura(
        "mutation {"
        f' a: delete_modelcatalog_configuration_input(where:{{configuration_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' b: delete_modelcatalog_configuration_output(where:{{configuration_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' c: delete_modelcatalog_configuration_parameter(where:{{configuration_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' p: delete_modelcatalog_dataset_specification_presentation(where:{{dataset_specification_id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' cfg: delete_modelcatalog_configuration(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' ver: delete_modelcatalog_software_version(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' ds: delete_modelcatalog_dataset_specification(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' par: delete_modelcatalog_parameter(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' vp: delete_modelcatalog_variable_presentation(where:{{id:{{_like:"{like}"}}}}){{affected_rows}}'
        f' sv: delete_modelcatalog_standard_variable(where:{{id:{{_in:{new_sv}}}}}){{affected_rows}}'
        f' un: delete_modelcatalog_unit(where:{{id:{{_in:{units}}}}}){{affected_rows}}'
        f' sw: delete_modelcatalog_software(where:{{id:{{_eq:"{uri(SW)}"}}}}){{affected_rows}}'
        "}"
    )
    print("[reset] cleared existing subside-forecast rows")


def patch_links() -> None:
    """Set has_standard_variable + uses_unit on the forecast presentations, and
    same_as on the standard variables (create path leaves these unset)."""
    parts = []
    for side, specs in (("input", SVO_INPUTS), ("output", SVO_OUTPUTS)):
        for i, s in enumerate(specs):
            vp = uri(f"{CFG}_vp_{side}_{s['slug']}")
            parts.append(
                f"v_{side}_{i}: update_modelcatalog_variable_presentation("
                f"where:{{id:{{_eq:{json.dumps(vp)}}}}}, _set:{{"
                f"has_standard_variable:{json.dumps(uri(s['svo']))}, uses_unit:{json.dumps(_unit_iri(s['unit']))}}}"
                "){affected_rows}"
            )
    for i, sv in enumerate(ALL_FORECAST_SVOS):
        parts.append(
            f"s_{i}: update_modelcatalog_standard_variable("
            f"where:{{id:{{_eq:{json.dumps(uri(sv['name']))}}}}}, _set:{{same_as:[{json.dumps(_svo_iri(sv['name']))}]}}"
            "){affected_rows}"
        )
    _hasura("mutation { " + " ".join(parts) + " }")
    print("[svo] linked forecast presentations + set same_as on standard variables")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Register the SUBSIDE forecast into the MINT catalog.")
    ap.add_argument("--api-base", default=R.DEFAULT_API_BASE)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args(argv)
    token = os.environ.get("MINT_API_TOKEN") or os.environ.get("TOKEN") or "e2e-test"

    if args.reset and not args.dry_run:
        reset()

    for u in NEW_UNITS:
        post(args.api_base, "units", {"id": _unit_iri(u["slug"]), "type": ["Unit"], "label": arr(u["label"])},
             token, args.dry_run)
    for sv in NEW_STANDARD_VARIABLES:
        post(args.api_base, "standardvariables",
             {"id": uri(sv["name"]), "type": ["StandardVariable"], "label": arr(sv["label"]),
              "description": arr(sv.get("description", ""))}, token, args.dry_run)

    post(args.api_base, "softwares", build_software_tree(), token, args.dry_run)
    if not args.dry_run:
        patch_links()
    print("\nDone." + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
