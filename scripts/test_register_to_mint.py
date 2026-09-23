"""Read-only regression tests for the MODFLOW MINT registration payloads.

These tests deliberately exercise payload builders and the dry-run path only.  They
must not require a MINT, Hasura, Tapis, or CKAN service.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import register_to_mint as registration  # noqa: E402


VARIANTS = ("modflow6", "modflow-usg", "modflow-2000", "modflow-96")


class RegistrationPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        registration._META = registration._load(registration.METADATA_PATH)

    def app_and_meta(self, variant: str) -> tuple[dict, dict]:
        meta = registration._META["variants"][variant]
        return registration._load(REPO / meta["dir"] / "app.json"), meta

    def test_config_payload_binds_to_exact_manifest_app(self) -> None:
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                app, meta = self.app_and_meta(variant)
                config = registration.build_config_node(
                    variant,
                    app,
                    meta,
                    f"https://components.example/{variant}.json",
                )
                self.assertEqual(config["tapis_app_id"], [app["id"]])
                self.assertEqual(config["tapis_app_version"], [app["version"]])
                self.assertEqual(config["has_software_image"], [app["containerImage"]])

    def test_modflow6_has_no_legacy_baseline_parameter(self) -> None:
        app, meta = self.app_and_meta("modflow6")
        config = registration.build_config_node(
            "modflow6", app, meta, "https://components.example/modflow6.json"
        )

        self.assertEqual(config["hasParameter"], [])
        self.assertNotIn("mf6DefaultDir", json.dumps(config))
        self.assertNotIn("Baseline data directory", json.dumps(config))

        app_args = app["jobAttributes"]["parameterSet"]["appArgs"]
        self.assertNotIn("mf6DefaultDir", {arg["name"] for arg in app_args})
        archive = next(
            item for item in app["jobAttributes"]["fileInputs"]
            if item["name"] == "mf6-simulation-archive"
        )
        self.assertEqual(archive["inputMode"], "REQUIRED")
        self.assertEqual(archive["targetPath"], "simulation.zip")
        self.assertNotIn("baseline directory", app["notes"]["helpText"].lower())

    def test_setup_payload_repeats_exact_manifest_app_binding(self) -> None:
        for variant in VARIANTS:
            app, meta = self.app_and_meta(variant)
            with self.subTest(variant=variant):
                for setup in registration.build_setup_nodes(
                    variant, app, meta, f"https://components.example/{variant}.json"
                ):
                    self.assertEqual(setup["tapis_app_id"], [app["id"]])
                    self.assertEqual(setup["tapis_app_version"], [app["version"]])
                    self.assertEqual(setup["has_software_image"], [app["containerImage"]])

    def test_archive_is_required_and_package_overrides_are_optional(self) -> None:
        """The archive is required; WEL/RCH/RCHA/RCHB overrides remain optional."""
        for variant in VARIANTS:
            app, meta = self.app_and_meta(variant)
            specs = registration.build_inputs(variant, app, meta)
            by_code = {spec["id"].rsplit("_input_", 1)[1]: spec for spec in specs}
            self.assertIn("simulation-archive", by_code, variant)
            override_codes = {code for code in by_code if code in {"wel", "rch", "rcha", "rchb"}}
            self.assertTrue(override_codes, variant)
            allowed_codes = {"simulation-archive", "wel", "rch", "rcha", "rchb", "rcha-02", "rcha-03"}
            self.assertEqual(set(by_code) - allowed_codes, set(), variant)
            self.assertFalse(by_code["simulation-archive"]["isOptional"])
            for code in override_codes:
                with self.subTest(variant=variant, code=code):
                    self.assertTrue(by_code[code]["isOptional"])

    def test_modflow_2000_label_is_not_the_legacy_2001_label(self) -> None:
        meta = registration._META["variants"]["modflow-2000"]
        app, _ = self.app_and_meta("modflow-2000")
        config = registration.build_config_node(
            "modflow-2000", app, meta, "https://components.example/modflow-2000.json"
        )
        self.assertEqual(meta["label"], "MODFLOW-2000")
        self.assertEqual(config["label"], ["MODFLOW-2000 configuration"])
        self.assertNotIn("MODFLOW-2001", json.dumps(config))

    def test_svo_links_use_live_fragment_namespace_and_specific_mappings(self) -> None:
        expected = {
            "wel": "groundwater_well__pumping_volume_flow_rate",
            "rch": "land_subsurface_water__recharge_volume_flux",
        }
        for code, variable in expected.items():
            with self.subTest(code=code):
                presentation = registration.build_presentation(
                    code, "test_variant", f"in_{code}"
                )
                self.assertIsNotNone(presentation)
                self.assertEqual(
                    presentation["has_standard_variable"],
                    [registration.uri(variable)],
                )
                self.assertTrue(
                    registration._svo_iri(variable).startswith(
                        "http://www.geoscienceontology.org/svo/svl/variable#"
                    )
                )

    def test_archive_input_accepts_semantic_variables_from_complete_app(self) -> None:
        expected = {
            "modflow6": {
                "groundwater__hydraulic_head",
                "aquifer__hydraulic_conductivity",
                "aquifer__storativity",
                "land_subsurface_water__recharge_volume_flux",
                "groundwater_well__pumping_volume_flow_rate",
            },
            "modflow-usg": {
                "groundwater__hydraulic_head",
                "aquifer__hydraulic_conductivity",
                "land_subsurface_water__recharge_volume_flux",
                "groundwater_well__pumping_volume_flow_rate",
                "land_surface_water__evapotranspiration_volume_flux",
            },
            "modflow-2000": {
                "groundwater__hydraulic_head",
                "aquifer__hydraulic_conductivity",
                "land_subsurface_water__recharge_volume_flux",
                "groundwater_well__pumping_volume_flow_rate",
                "land_surface_water__evapotranspiration_volume_flux",
            },
            "modflow-96": {
                "groundwater__hydraulic_head",
                "aquifer__hydraulic_conductivity",
                "land_subsurface_water__recharge_volume_flux",
                "groundwater_well__pumping_volume_flow_rate",
            },
        }
        for variant, variables in expected.items():
            app, meta = self.app_and_meta(variant)
            specs = registration.build_inputs(variant, app, meta)
            archive = next(
                spec for spec in specs
                if spec["id"].endswith("_input_simulation-archive")
            )
            actual = {
                presentation["has_standard_variable"][0].rsplit("/", 1)[-1]
                for presentation in archive["hasPresentation"]
            }
            with self.subTest(variant=variant):
                self.assertEqual(actual, variables)

    def test_archive_presentations_have_distinct_stable_ids(self) -> None:
        for variant in VARIANTS:
            app, meta = self.app_and_meta(variant)
            archive = next(
                spec for spec in registration.build_inputs(variant, app, meta)
                if spec["id"].endswith("_input_simulation-archive")
            )
            ids = [presentation["id"] for presentation in archive["hasPresentation"]]
            with self.subTest(variant=variant):
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all("in_simulation_archive_" in value for value in ids))

    def test_archive_presentations_are_linked_in_one_complete_put(self) -> None:
        calls = []
        original_post = registration.post
        original_put = registration._put
        registration.post = lambda *args: calls.append(("post", args))
        registration._put = lambda *args: calls.append(("put", args))
        try:
            registration.register_presentations(
                "https://catalog.example/v2.0.0", "token", ["modflow6"], False
            )
        finally:
            registration.post = original_post
            registration._put = original_put

        archive_spec_id = registration.uri("modflow6_input_simulation-archive")
        archive_puts = [
            args for kind, args in calls
            if kind == "put" and args[1] == "datasetspecifications" and args[2] == archive_spec_id
        ]
        self.assertEqual(len(archive_puts), 1)
        linked_ids = {item["id"] for item in archive_puts[0][3]["hasPresentation"]}
        app, meta = self.app_and_meta("modflow6")
        archive = next(
            spec for spec in registration.build_inputs("modflow6", app, meta)
            if spec["id"] == archive_spec_id
        )
        self.assertEqual(linked_ids, {item["id"] for item in archive["hasPresentation"]})

    def test_existing_repairs_have_explicit_live_ids_and_usg_is_new_only(self) -> None:
        """Prevent a rerun from creating replacement IDs for live configurations.

        The exact 2000/96 IDs must be filled from the read-only live inventory before
        any write is approved.  This test intentionally fails while the script has no
        explicit repair map.
        """
        repair_ids = getattr(registration, "LIVE_CONFIG_IDS", None)
        self.assertIsInstance(repair_ids, dict)
        self.assertEqual(set(repair_ids), {"modflow6", "modflow-2000", "modflow-96"})
        for variant, config_id in repair_ids.items():
            with self.subTest(variant=variant):
                self.assertTrue(config_id)
                self.assertNotEqual(config_id, registration.uri(f"{registration._META['variants'][variant]['version_slug']}_cfg"))

        new_variants = getattr(registration, "NEW_CONFIG_VARIANTS", None)
        self.assertEqual(new_variants, ("modflow-usg",))

    def test_dev_repair_map_targets_existing_configurations_only(self) -> None:
        self.assertEqual(
            registration.LIVE_CONFIG_IDS,
            {
                "modflow6": (
                    registration.uri("ce445698-1d76-4833-95e8-a12eb3da2488"),
                ),
                "modflow-2000": (
                    registration.uri("b8fa91f0-c000-4d5c-ade9-fa6eb8f0147b"),
                ),
                "modflow-96": (
                    registration.uri("dcd878ae-5e7d-44c4-805b-7bd3f3fc1637"),
                ),
            },
        )
        self.assertEqual(
            registration.LIVE_CONFIG_VERSION_IDS["modflow-2000"],
            (registration.uri("0179ee86-5c8e-4198-8961-9142dcf13d24"),),
        )

    def test_fk_repairs_use_scalar_fields(self) -> None:
        payload = registration._repair_config_payload(
            {"id": "cfg", "label": ["config"]}, "version"
        )
        self.assertEqual(payload["softwareVersionId"], ["version"])
        self.assertNotIn("softwareVersion", payload)

    def test_same_as_graphql_patch_uses_scalar_text(self) -> None:
        pairs = [("sv-id", "http://example.org/svo#recharge")]
        mutation = registration.build_same_as_mutation(pairs)
        self.assertIn(
            '_set:{same_as:"{\\"http://example.org/svo#recharge\\"}"}',
            mutation,
        )
        self.assertNotIn("same_as:[", mutation)

    def test_dry_run_is_network_free_and_covers_all_variants(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = registration.main(["--dry-run"])
        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Done. (dry-run, no changes made)", text)
        for variant in VARIANTS:
            self.assertIn(f"{variant}.json", text)

    def test_dry_run_repairs_existing_configs_and_creates_only_usg(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            registration.main(["--dry-run"])
        text = output.getvalue()
        for variant, config_ids in registration.LIVE_CONFIG_IDS.items():
            for config_id in config_ids:
                encoded = registration.urllib.request.quote(config_id, safe="")
                self.assertIn(
                    f"PUT http://localhost:3001/v2.0.0/modelconfigurations/{encoded}",
                    text,
                )
                self.assertNotIn(
                    f"POST http://localhost:3001/v2.0.0/modelconfigurations  ({config_id})",
                    text,
                )
        self.assertIn(
            "POST http://localhost:3001/v2.0.0/modelconfigurations  "
            f"({registration.uri('modflow_usg_cfg')})",
            text,
        )

    def test_dry_run_uses_requested_component_base_for_config_bindings(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            registration.main(
                [
                    "--variant",
                    "modflow6",
                    "--component-base-url",
                    "https://components.example/v2",
                    "--dry-run",
                ]
            )
        text = output.getvalue()
        self.assertIn("https://components.example/v2/modflow6.json", text)
        self.assertNotIn(
            "https://raw.githubusercontent.com/wmobley/modflow-suite/main/"
            "modflow-executables/scripts/components/modflow6.json",
            text,
        )


if __name__ == "__main__":
    unittest.main()
