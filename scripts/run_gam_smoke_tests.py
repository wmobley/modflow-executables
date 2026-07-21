#!/usr/bin/env python3
"""Submit a smoke-test job for each MODFLOW GAM archive-URL Tapis app, one
after another, waiting for each to reach a terminal state before submitting
the next. Prints a pass/fail summary table at the end.

Each test case downloads a real GAM archive from TWDB (hundreds of MB to a
few GB) and runs the actual MODFLOW binary — this consumes real TACC
allocation time. NTGAM alone took over an hour on its first successful run;
running all four here unattended can take several hours total. Consider
starting with --only to test one GAM before running the full set.

Usage:
    TAPIS_USERNAME=wmobley python3 run_gam_smoke_tests.py
    TAPIS_USERNAME=wmobley python3 run_gam_smoke_tests.py --only carrizo-wilcox-central
    TAPIS_USERNAME=wmobley python3 run_gam_smoke_tests.py --only yegua-jackson,trinity-hill-country
    python3 run_gam_smoke_tests.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from getpass import getpass

BASE_URL = os.environ.get("TAPIS_BASE_URL", "https://portals.tapis.io").rstrip("/")
TERMINAL_STATES = {"FINISHED", "FAILED", "CANCELLED", "STOPPED"}

# Which appArg name each Tapis app expects for its archive-URL input.
ARCHIVE_URL_ARG_BY_APP = {
    "modflow6-simulation": "mf6ArchiveUrl",
    "modflow-usg-simulation": "mfusgArchiveUrl",
    "modflow-2000-simulation": "mf2000ArchiveUrl",
    "modflow-96-simulation": "mf96ArchiveUrl",
}

# Confirmed download URLs — see docs/reference/2026-07-21-twdb-gam-modflow-versions.md
GAM_TEST_CASES = [
    {
        "label": "ntgam",
        "app_id": "modflow6-simulation",
        "app_version": "0.0.dc13cc2",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_n/trnt_n_v301/NTGAM_Final_model_2025.7z",
        "max_minutes": 240,
        "note": "Already confirmed working once (2026-07-21) — included here for regression coverage.",
    },
    {
        "label": "carrizo-wilcox-central",
        "app_id": "modflow-usg-simulation",
        "app_version": "0.0.e5fea89",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/czwx_c/czwx_c_qcsp_v3.02_model_files.zip",
        "max_minutes": 240,
        "note": "Untested. Joint Carrizo-Wilcox/Queen City/Sparta model, 712MB zipped.",
    },
    {
        "label": "yegua-jackson",
        "app_id": "modflow-2000-simulation",
        "app_version": "0.0.e5fea89",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/ygjk/Yegua_Jackson_Model_Only.zip",
        "max_minutes": 240,
        "note": "Untested. Filename suggests a clean native-only archive, 149MB zipped.",
    },
    {
        "label": "trinity-hill-country",
        "app_id": "modflow-96-simulation",
        "app_version": "0.0.e5fea89",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_h/trnt_h_v3.01/Final/trnt_h_v3.01_Model_Files.7z",
        "max_minutes": 240,
        "note": "Untested. Has a sibling Supplemental_Data.7z archive — watch for missing files.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated list of labels to run (default: all). Labels: "
        + ", ".join(c["label"] for c in GAM_TEST_CASES),
    )
    parser.add_argument(
        "--poll-seconds", type=int, default=30, help="Seconds between status polls."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be submitted; make no Tapis calls."
    )
    return parser.parse_args()


def get_token() -> str:
    token = os.environ.get("TAPIS_TOKEN", "").strip()
    if token:
        return token

    username = os.environ.get("TAPIS_USERNAME", "").strip() or input("Tapis username: ").strip()
    password = os.environ.get("TAPIS_PASSWORD", "") or getpass("Tapis password: ")
    body = json.dumps({"username": username, "password": password, "grant_type": "password"}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/v3/oauth2/tokens",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    payload = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return payload["result"]["access_token"]["access_token"]


def submit(token: str, case: dict) -> str:
    arg_name = ARCHIVE_URL_ARG_BY_APP[case["app_id"]]
    job_name = f"gam-smoke-{case['label']}-{int(time.time())}"
    body: dict = {
        "name": job_name,
        "appId": case["app_id"],
        "appVersion": case["app_version"],
        "parameterSet": {
            "appArgs": [
                {"name": arg_name, "arg": case["archive_url"]},
            ],
        },
        "maxMinutes": case["max_minutes"],
    }

    req = urllib.request.Request(
        f"{BASE_URL}/v3/jobs/submit",
        data=json.dumps(body).encode(),
        headers={"X-Tapis-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise SystemExit(f"Job submission failed for {case['label']} ({exc.code}): {detail}")

    uuid = resp["result"]["uuid"]
    print(f"  Submitted {job_name}: {uuid}")
    print(f"    {BASE_URL}/v3/jobs/{uuid}")
    return uuid


def watch(token: str, uuid: str, poll_seconds: int) -> dict:
    last_status = None
    start = time.time()
    while True:
        req = urllib.request.Request(
            f"{BASE_URL}/v3/jobs/{uuid}",
            headers={"X-Tapis-Token": token},
        )
        result = json.loads(urllib.request.urlopen(req, timeout=30).read())["result"]
        status = result.get("status", "UNKNOWN")
        if status != last_status:
            elapsed_min = (time.time() - start) / 60
            print(f"    [{time.strftime('%H:%M:%S')}] {status} (+{elapsed_min:.1f} min)")
            last_status = status
        if status in TERMINAL_STATES:
            return result
        time.sleep(poll_seconds)


def fetch_log_tail(token: str, uuid: str, max_chars: int = 800) -> str:
    req = urllib.request.Request(
        f"{BASE_URL}/v3/jobs/{uuid}/output/download/tapisjob.out",
        headers={"X-Tapis-Token": token},
    )
    try:
        text = urllib.request.urlopen(req, timeout=30).read().decode(errors="replace")
    except urllib.error.HTTPError:
        return "(tapisjob.out not available)"
    return text[-max_chars:]


def main() -> int:
    args = parse_args()
    only = {label.strip() for label in args.only.split(",")} if args.only else None
    cases = [c for c in GAM_TEST_CASES if only is None or c["label"] in only]

    if not cases:
        print(f"No matching labels for --only={args.only!r}")
        return 2

    if args.dry_run:
        print("Dry run — would submit these jobs in order:")
        for case in cases:
            print(f"  {case['label']}: {case['app_id']} @ {case['app_version']}")
            print(f"    archive: {case['archive_url']}")
            print(f"    note: {case['note']}")
        return 0

    token = get_token()
    results = []

    for i, case in enumerate(cases, start=1):
        print(f"\n=== [{i}/{len(cases)}] {case['label']} ({case['app_id']}) ===")
        start = time.time()
        try:
            uuid = submit(token, case)
            final = watch(token, uuid, args.poll_seconds)
            duration_min = (time.time() - start) / 60
            status = final.get("status", "UNKNOWN")
            results.append(
                {
                    "label": case["label"],
                    "status": status,
                    "duration_min": duration_min,
                    "uuid": uuid,
                    "last_message": final.get("lastMessage", ""),
                }
            )
            if status != "FINISHED":
                print(f"    lastMessage: {final.get('lastMessage')}")
                print("    tapisjob.out tail:")
                print("    " + fetch_log_tail(token, uuid).replace("\n", "\n    "))
        except Exception as exc:  # noqa: BLE001 - report and continue to next case
            duration_min = (time.time() - start) / 60
            print(f"    ERROR: {exc}")
            results.append(
                {
                    "label": case["label"],
                    "status": "ERROR",
                    "duration_min": duration_min,
                    "uuid": None,
                    "last_message": str(exc),
                }
            )

    print("\n=== Summary ===")
    print(f"{'GAM':<24} {'Status':<12} {'Duration (min)':<16} UUID")
    all_ok = True
    for r in results:
        if r["status"] != "FINISHED":
            all_ok = False
        print(f"{r['label']:<24} {r['status']:<12} {r['duration_min']:<16.1f} {r['uuid'] or '-'}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
