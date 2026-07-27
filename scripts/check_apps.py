#!/usr/bin/env python3
"""Check that the three MODFLOW Tapis apps are registered, enabled, and on the
expected image version. Optionally submit a smoke-test job for each.

Usage:
    TAPIS_TOKEN=... python3 check_apps.py
    TAPIS_TOKEN=... python3 check_apps.py --submit   # also submits test jobs
    TAPIS_TOKEN=... python3 check_apps.py --app modflow-usg-simulation

    # Get a token via username/password if TAPIS_TOKEN is not set:
    TAPIS_USERNAME=wmobley TAPIS_PASSWORD=... python3 check_apps.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from getpass import getpass
from pathlib import Path

BASE_URL = os.environ.get("TAPIS_BASE_URL", "https://portals.tapis.io").rstrip("/")
TAPIS_TOKEN = os.environ.get("TAPIS_TOKEN", "")
SYSTEM_ID = "ptdatax.project.PTDATAX-272"
MODEL_ROOT = "workingGAMs"
EXPECTED_ALLOCATION = "PT2050-DataX"

APPS = [
    {
        "id":          "modflow-usg-simulation",
        "image":       "modflow-usg",
        "baseline_dir": f"{MODEL_ROOT}/Carrizo-Wilcox-central/gmv-modflow-usg-Modified",
        "test_inputs": {
            "mfusg-bas":        "gma12.bas",
            "mfusg-dis":        "gma12.dis",
            "mfusg-drn":        "gma12.drn",
            "mfusg-evt":        "gma12.evt",
            "mfusg-ghb":        "gma12.ghb",
            "mfusg-gnc":        "gma12.gnc",
            "mfusg-hfb":        "gma12.hfb",
            "mfusg-lpf":        "gma12.mod.lpf",
            "mfusg-oc":         "gma12.oc",
            "mfusg-rch":        "gma12.rch",
            "mfusg-riv":        "gma12.riv",
            "mfusg-sms":        "gma12.sms",
            "mfusg-wel":        "gma12.wel",
            "mfusg-support-01": "evt.depth.ref",
            "mfusg-support-02": "evt.nodes.ref",
            "mfusg-support-03": "evt.rate.ref",
            "mfusg-support-04": "evt.top.ref",
        },
    },
    {
        "id":          "modflow-2000-simulation",
        "image":       "modflow-2000",
        "baseline_dir": (
            f"{MODEL_ROOT}/Yequa_Jackson/Yegua_Jackson_Model_Only"
            "/CD-2_ygjk_model/Modflow_2000"
        ),
        "test_inputs": {
            "mf2000-bas": "ygjk_tr.bas",
            "mf2000-bcf": "ygjk_tr.bcf",
            "mf2000-dis": "ygjk_tr.dis",
            "mf2000-drn": "ygjk_tr.drn",
            "mf2000-evt": "ygjk_tr.evt",
            "mf2000-ghb": "ygjk_tr.ghb",
            "mf2000-gmg": "ygjk_tr.gmg",
            "mf2000-oc":  "ygjk_tr.oc",
            "mf2000-rch": "ygjk_tr.rch",
            "mf2000-res": "ygjk_tr.res",
            "mf2000-str": "ygjk_tr.str",
            "mf2000-wel": "ygjk_tr.wel",
        },
    },
    {
        "id":          "modflow-96-simulation",
        "image":       "modflow-96",
        "baseline_dir": (
            f"{MODEL_ROOT}/Trinity_hill_country"
            "/Trinity_hill_country_model_only/modfl_96/ststate"
        ),
        "test_inputs": {
            "mf96-bas":      "bas.dat",
            "mf96-bcf":      "bcf.dat",
            "mf96-discret":  "discret.dat",
            "mf96-drn":      "drn.dat",
            "mf96-ghb":      "ghb.dat",
            "mf96-oc":       "oc.dat",
            "mf96-output":   "output.dat",
            "mf96-rch":      "rch.dat",
            "mf96-riv":      "riv.dat",
            "mf96-sor":      "sor.dat",
            "mf96-wel":      "wel.dat",
            "mf96-budget":   "budget.dat",
            "mf96-heads":    "heads.dat",
            "mf96-ddown":    "ddown.dat",
            "mf96-mt3d-flo": "mt3d.flo",
        },
    },
]


def _headers() -> dict[str, str]:
    return {"X-Tapis-Token": TAPIS_TOKEN, "Content-Type": "application/json"}


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=_headers())
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data,
                                  headers=_headers(), method="POST")
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())


def _get_token_via_password() -> str:
    username = os.environ.get("TAPIS_USERNAME", "").strip() or input("Tapis username: ").strip()
    password = os.environ.get("TAPIS_PASSWORD", "") or getpass("Tapis password: ")
    body = {"username": username, "password": password, "grant_type": "password"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/v3/oauth2/tokens",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    payload = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return payload["result"]["access_token"]["access_token"]


def _app_file_input_names(app_def: dict) -> set[str]:
    job_attrs = app_def.get("jobAttributes", {})
    return {item["name"] for item in job_attrs.get("fileInputs", [])}


def _scheduler_option_args(app_def: dict) -> list[str]:
    parameter_set = app_def.get("jobAttributes", {}).get("parameterSet", {})
    return [item.get("arg", "") for item in parameter_set.get("schedulerOptions", [])]


# ---------------------------------------------------------------------------
# App check
# ---------------------------------------------------------------------------
def check_app(app: dict) -> tuple[bool, dict]:
    """Return (ok, app_def)."""
    app_id = app["id"]
    try:
        resp = _get(f"/v3/apps/{app_id}")
        result = resp.get("result", {})
    except Exception as exc:
        print(f"  FAIL  {app_id}: {exc}")
        return False, {}

    enabled      = result.get("enabled", False)
    image        = result.get("containerImage", "")
    version      = result.get("version", "")
    ok_image     = app["image"] in image
    alloc_ok     = f"-A {EXPECTED_ALLOCATION}" in _scheduler_option_args(result)
    reg_inputs   = _app_file_input_names(result)
    missing_ins  = sorted(set(app["test_inputs"]) - reg_inputs)

    status = "OK  " if (enabled and ok_image and not missing_ins) else "WARN"
    print(f"  {status}  {app_id}")
    print(f"        enabled={enabled}  version={version}")
    print(f"        image={image}")
    if not enabled:
        print(f"        ↳ app is disabled")
    if not ok_image:
        print(f"        ↳ expected image containing '{app['image']}'")
    if not alloc_ok:
        print(f"        ↳ allocation '{EXPECTED_ALLOCATION}' not in scheduler options — will inject at job level")
    if missing_ins:
        print(f"        ↳ app is missing these registered file inputs: {missing_ins}")

    ok = enabled and ok_image and not missing_ins
    return ok, result


# ---------------------------------------------------------------------------
# Job submission (smoke test)
# ---------------------------------------------------------------------------
def submit_job(app: dict, app_def: dict) -> str | None:
    """Submit using the version actually registered in Tapis."""
    app_id   = app["id"]
    base_dir = app["baseline_dir"]
    version  = app_def.get("version", "")
    alloc_ok = f"-A {EXPECTED_ALLOCATION}" in _scheduler_option_args(app_def)

    file_inputs = [
        {
            "name":      name,
            "sourceUrl": f"tapis://{SYSTEM_ID}/{base_dir}/{fname}",
        }
        for name, fname in app["test_inputs"].items()
    ]

    body: dict = {
        "name":       f"check-{app_id}",
        "appId":      app_id,
        "appVersion": version,
        "fileInputs": file_inputs,
    }
    if not alloc_ok:
        body["parameterSet"] = {
            "schedulerOptions": [{"name": "TACC Allocation",
                                   "arg": f"-A {EXPECTED_ALLOCATION}"}]
        }
    try:
        resp = _post("/v3/jobs/submit", body)
        uuid = resp.get("result", {}).get("uuid")
        print(f"    Submitted: {uuid}")
        return uuid
    except Exception as exc:
        # Try to surface the Tapis error body
        err = str(exc)
        if hasattr(exc, "read"):
            try:
                err = exc.read().decode()
            except Exception:
                pass
        print(f"    Submit failed: {err}")
        return None


def poll_job(uuid: str, timeout: int = 1800) -> str:
    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        try:
            resp  = _get(f"/v3/jobs/{uuid}/status")
            state = resp.get("result", {}).get("status", "")
        except Exception as exc:
            print(f"    Poll error: {exc}")
            time.sleep(15)
            continue
        if state != last_status:
            print(f"    Status: {state}")
            last_status = state
        if state in ("FINISHED", "FAILED", "CANCELLED", "STOPPED"):
            return state
        sleep = 30 if last_status == "RUNNING" else 60
        time.sleep(sleep)
    return "TIMEOUT"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    global BASE_URL, TAPIS_TOKEN

    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true",
                    help="Also submit a smoke-test job for each app")
    ap.add_argument("--app", dest="apps", action="append",
                    help="Limit to specific app IDs (repeat to select multiple)")
    ap.add_argument("--base-url", default=BASE_URL)
    args = ap.parse_args(argv)

    BASE_URL    = args.base_url
    TAPIS_TOKEN = os.environ.get("TAPIS_TOKEN", "")
    if not TAPIS_TOKEN:
        print("TAPIS_TOKEN not set — fetching via username/password")
        try:
            TAPIS_TOKEN = _get_token_via_password()
        except Exception as exc:
            print(f"Auth failed: {exc}", file=sys.stderr)
            return 2

    selected = [a for a in APPS if not args.apps or a["id"] in args.apps]
    print(f"Checking {len(selected)} app(s) on {BASE_URL}\n")

    all_ok = True
    for app in selected:
        ok, app_def = check_app(app)
        all_ok = all_ok and ok

        if args.submit and ok:
            print(f"    Submitting smoke-test job for {app['id']} …")
            uuid = submit_job(app, app_def)
            if uuid:
                result = poll_job(uuid)
                mark = "PASS" if result == "FINISHED" else "FAIL"
                print(f"    {mark}  job ended: {result}")
                if result != "FINISHED":
                    all_ok = False
        print()

    print("Result:", "ALL OK" if all_ok else "ISSUES FOUND")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
