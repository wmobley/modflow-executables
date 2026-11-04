"""Read-only regression tests for the SUBSIDE OPERA MINT registration payloads.

Exercises payload builders and the dry-run path only; no MINT, Hasura or Tapis
service is contacted.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import register_subside_to_mint as reg  # noqa: E402
import register_to_mint as R  # noqa: E402

APP_DIRS = {"opera-disp-s1-h2i-lab": "h2i_lab", "opera-disp-s1-werc": "werc"}
# Env keys that are not catalog parameters: file input path, and the on-demand conda refresh.
NON_PARAM_ENV_KEYS = {"AOI_GEOJSON_PATH", "UPDATE_CONDA_ENV",
    # Platform-provided at submit time by the svo-adapter; never a catalog parameter.
    "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD",
}


class OperaRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = reg.load_yaml()
        cls.configs = {c["id"]: c for c in reg.iter_configs(cls.model)}

    def app_env_keys(self, cfg_id: str) -> set[str]:
        app_path = reg.YAML_PATH.parent / APP_DIRS[cfg_id] / "app-cpu.json"
        app = json.loads(app_path.read_text())
        return {e["key"] for e in app["jobAttributes"]["parameterSet"]["envVariables"]}

    def test_both_configurations_present(self) -> None:
        self.assertEqual(set(self.configs), set(APP_DIRS))
        self.assertEqual(self.model["id"], "opera")
        self.assertEqual(self.model["hasVersion"][0]["id"], "opera-disp-s1-analysis")

    def test_typed_outputs_and_first_output(self) -> None:
        expected = {
            "h2i-output-displacement-cog": ("land_surface__displacement", "m"),
            "werc-output-cumulative-tif": ("land_surface__displacement", "mm"),
            "werc-output-velocity-tif": ("land_surface__displacement_rate", "mm/yr"),
        }
        typed = {}
        for cfg in self.configs.values():
            for spec in cfg["hasOutput"]:
                vp = reg.build_presentation(spec)
                if vp:
                    typed[spec["id"]] = (vp["has_standard_variable"][0].rsplit("/", 1)[1],
                                         spec["variablePresentation"]["unit"])
                    self.assertEqual(vp["uses_unit"], [reg._unit_iri(reg.UNITS[spec["variablePresentation"]["unit"]])])
        self.assertEqual(typed, expected)
        first = {cid: c["hasOutput"][0]["id"] for cid, c in self.configs.items()}
        self.assertEqual(first["opera-disp-s1-h2i-lab"], "h2i-output-displacement-cog")
        self.assertEqual(first["opera-disp-s1-werc"], "werc-output-cumulative-tif")
        for cfg in self.configs.values():
            self.assertEqual([_o["position"][0] for _o in cfg["hasOutput"]],
                             list(range(1, len(cfg["hasOutput"]) + 1)))

    def test_no_netrc_input(self) -> None:
        for cfg in self.configs.values():
            ids = [i["id"] for i in cfg["hasInput"]]
            self.assertEqual(len(ids), 1)
            self.assertTrue(ids[0].endswith("-input-aoi"))
            self.assertTrue(cfg["hasInput"][0]["isOptional"])
            self.assertNotIn("netrc", json.dumps(cfg["hasInput"]).lower())

    def test_parameter_names_equal_app_env_keys(self) -> None:
        for cfg_id, cfg in self.configs.items():
            with self.subTest(cfg=cfg_id):
                payload = reg.build_config(cfg)
                names = [p["label"][0] for p in payload["hasParameter"]]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(set(names), self.app_env_keys(cfg_id) - NON_PARAM_ENV_KEYS)

    def test_component_location_and_tapis_bindings(self) -> None:
        for cfg_id, cfg in self.configs.items():
            payload = reg.build_config(cfg)
            app = f"subside-{APP_DIRS[cfg_id].split('_')[0]}-opera-analysis"
            self.assertEqual(payload["has_component_location"],
                             [f"https://portals.tapis.io/v3/apps/{app}/0.1.1"])
            self.assertEqual(payload["tapis_app_id"], [app])
            self.assertEqual(payload["tapis_app_version"], ["0.1.1"])
            self.assertEqual(payload["has_software_image"], [f"docker://ghcr.io/wmobley/{app}:main"])

    def test_nested_hierarchy_ids(self) -> None:
        tree = reg.build_software_tree(self.model)
        self.assertEqual(tree["id"], R.uri("opera"))
        version = tree["hasVersion"][0]
        self.assertEqual(version["id"], R.uri("opera-disp-s1-analysis"))
        self.assertEqual({c["id"] for c in version["hasConfiguration"]},
                         {R.uri(c) for c in APP_DIRS})
        self.assertIsInstance(tree["keywords"][0], str)

    def test_legacy_wire_payload_drops_tapis_ids_but_patch_plan_restores_them(self) -> None:
        tree = reg.build_software_tree(self.model)
        legacy = R._legacy_payload(tree)
        wire = json.dumps(legacy)
        self.assertNotIn("tapis_app_id", wire)
        self.assertNotIn("tapis_app_version", wire)
        self.assertIn("tapis_app_id", json.dumps(tree))
        mutations = "\n".join(m for _, m in reg.build_patch_plan(self.model))
        for app in ("subside-h2i-opera-analysis", "subside-werc-opera-analysis"):
            self.assertIn(f'tapis_app_id:"{app}"', mutations)
        self.assertIn("has_component_location", mutations)
        self.assertIn("has_standard_variable", mutations)
        self.assertIn("same_as", mutations)

    def test_reset_mutation_scoped_to_opera_ids(self) -> None:
        mutation = reg.build_reset_mutation(self.model)
        self.assertIn(R.uri("opera-disp-s1-werc"), mutation)
        self.assertNotIn("%", mutation)
        self.assertNotIn("delete_modelcatalog_standard_variable", mutation)

    def test_dry_run_makes_no_network_calls(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = reg.main(["--dry-run", "--reset", "--legacy-api-schema"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("POST http://localhost:3001/v2.0.0/models", text)
        self.assertIn("HASURA reset", text)
        self.assertIn("(dry-run, no changes made)", text)
        R.LEGACY_API_SCHEMA = False


if __name__ == "__main__":
    unittest.main()


def test_legacy_wire_payload_uses_scalar_min_max_but_keeps_accepted_values_list() -> None:
    import json as _json
    model = reg.load_yaml()
    tree = reg.legacy_parameter_scalars(reg.build_software_tree(model))
    wire = _json.loads(_json.dumps(reg.R._legacy_payload(tree)))
    params = [p for c in wire["hasVersion"][0]["hasConfiguration"] for p in c.get("hasParameter", [])]
    assert params, "no parameters in the wire payload"
    seen_minmax = seen_accepted = False
    for p in params:
        for k in reg.LEGACY_SCALAR_PARAMETER_KEYS:
            if k in p:
                seen_minmax = True
                assert not isinstance(p[k], list), (p["id"], k, p[k])
        if "has_accepted_values" in p:
            seen_accepted = True
            assert isinstance(p["has_accepted_values"], list)
    assert seen_minmax and seen_accepted


def test_patch_plan_sets_hierarchy_parent_fks() -> None:
    model = reg.load_yaml()
    plan = dict(reg.build_patch_plan(model))
    fk = plan["hierarchy parent FKs"]
    assert "update_modelcatalog_software_version" in fk and "software_id:" in fk
    assert fk.count("update_modelcatalog_configuration") == 2 and "software_version_id:" in fk
    assert reg.uri(model["id"]) in fk and reg.uri(model["hasVersion"][0]["id"]) in fk
