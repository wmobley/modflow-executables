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
import base64
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
        "note": "Confirmed working 2026-07-21: FINISHED in 130.6 min (needs maxMinutes above the app default of 60).",
    },
    {
        "label": "carrizo-wilcox-central",
        "app_id": "modflow-usg-simulation",
        "app_version": "0.0.e5fea89",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/czwx_c/czwx_c_qcsp_v3.02_model_files.zip",
        "max_minutes": 240,
        "note": "Confirmed working 2026-07-21: FINISHED in 12.7 min. Joint Carrizo-Wilcox/Queen City/Sparta model, 712MB zipped.",
    },
    {
        "label": "yegua-jackson",
        "app_id": "modflow-2000-simulation",
        "app_version": "0.0.e5fea89",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/ygjk/Yegua_Jackson_Model_Only.zip",
        "max_minutes": 240,
        "note": "Confirmed working 2026-07-21: FINISHED in 6.6 min, the fastest of the four.",
    },
    {
        "label": "trinity-hill-country",
        "app_id": "modflow-96-simulation",
        "app_version": "0.0.e5fea89",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_h/trnt_h_v3.01/Final/trnt_h_v3.01_Model_Files.7z",
        "max_minutes": 240,
        "note": "Confirmed working 2026-07-21: FINISHED in 3.6 min. Ran clean without the sibling Supplemental_Data.7z archive.",
    },
    # --- Second batch (2026-07-21): 12 newly CKAN-registered GAMs. ---
    # app_version 0.0.5f35915 is the first version on all 4 variants with the
    # raised 20GiB uncompressed-size cap (needed for Seymour's 16.3GB archive).
    # Untested as of this writing. Not included below (with reasons):
    #   - gulf-coast-southern-superseded: no standalone archive exists anymore;
    #     TWDB merged it into the central+southern combined model.
    #   - high-plains-aquifer-system: real archive URL exists but there is no
    #     modflow-nwt-simulation Tapis app to run it against.
    {
        "label": "edwards-bfz-northern",
        "app_id": "modflow-usg-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/ebfz_n/ebfz_n_v2.1/ebfz_n_v2.1_ModelFiles.7z",
        "max_minutes": 180,
        "note": "Untested. MODFLOW-USG beta, 22.5MB zipped / 2.5GB unzipped.",
    },
    {
        "label": "edwards-bfz-barton-springs",
        "app_id": "modflow-2000-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/ebfz_b/Edwards_BFZ_Barton_Springs.zip",
        "max_minutes": 120,
        "note": "Untested. 14.7MB zipped / 1.1GB unzipped.",
    },
    {
        "label": "edwards-bfz-san-antonio",
        "app_id": "modflow-2000-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/ebfz_s/Edwards_BFZ_San_Antonio_GWSIM.zip",
        "max_minutes": 60,
        "note": (
            "Untested. Only one archive exists for this GAM (named 'GWSIM') despite the model "
            "being described as MODFLOW-96/MODFLOW-2000 'mixed' — guessing MODFLOW-2000 app first. "
            "If this fails on package/name-file resolution, retry with --app-id modflow-96-simulation "
            "(app_version may differ; check registered versions first). 0.7MB zipped / 13.6MB unzipped."
        ),
    },
    {
        "label": "edwards-trinity-plateau-pecos-valley",
        "app_id": "modflow-2000-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/eddt_r/Edwards_Trinity_Plateau_Model_Only.zip",
        "max_minutes": 240,
        "note": "Untested. 152MB zipped / 4.6GB unzipped.",
    },
    {
        "label": "gulf-coast-central-southern",
        "app_id": "modflow-usg-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/glfc_c_s/July19_glfc_c_s_ModelFiles.7z",
        "max_minutes": 240,
        "note": "Untested. 1.2GB zipped / 4.3GB unzipped.",
    },
    {
        "label": "gulf-coast-northern",
        "app_id": "modflow6-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/glfc_n/glfc_n_v4.1/glfc_n_v4.1_model_files.zip",
        "max_minutes": 90,
        "note": "Untested. v4.1, 43.9MB zipped / 500MB unzipped.",
    },
    {
        "label": "seymour-and-blaine",
        "app_id": "modflow-2000-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/symr/Seymour_Model_Only.zip",
        "max_minutes": 600,
        "note": (
            "Untested. 459MB zipped / 16.3GB unzipped — exceeds the old 8GiB cap, requires the "
            "20GiB-cap app version (0.0.5f35915) confirmed registered. Largest archive tested so far; "
            "max_minutes set generously (10 hours)."
        ),
    },
    {
        "label": "lipan",
        "app_id": "modflow-96-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/lipn/Lipan_Model_Only.zip",
        "max_minutes": 90,
        "note": "Untested. 92MB zipped / 880MB unzipped.",
    },
    {
        "label": "nacatoch",
        "app_id": "modflow-2000-simulation",
        "app_version": "0.0.5f35915",
        "archive_url": "https://gw-models.s3.amazonaws.com/Download_GAMs/nctc/Nacatoch_Model_Only.zip",
        "max_minutes": 180,
        "note": "Untested. 76MB zipped / 3.3GB unzipped.",
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


def _jwt_exp(token: str) -> float | None:
    """Decode a JWT's exp claim (unix seconds) without verifying signature."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        return float(claims["exp"])
    except Exception:  # noqa: BLE001 - if we can't tell, caller should refresh anyway
        return None


class TokenBox:
    """Holds a Tapis access token and re-authenticates from scratch when it's
    close to expiring. Tapis access tokens issued by this portal's client are
    short-lived (observed ~4 hours) and this client does not appear to receive
    a refresh_token, so the only reliable renewal is a fresh password grant.
    Username/password are kept in memory only, for this process's lifetime,
    never printed or logged.
    """

    REFRESH_MARGIN_SECONDS = 300  # refresh if within 5 minutes of expiry

    def __init__(self) -> None:
        self._static_token = os.environ.get("TAPIS_TOKEN", "").strip()
        self.username = os.environ.get("TAPIS_USERNAME", "").strip() or input("Tapis username: ").strip()
        self.password = os.environ.get("TAPIS_PASSWORD", "") or getpass("Tapis password: ")
        self.token = self._static_token or ""
        self.expires_at: float | None = None
        if not self._static_token:
            self._authenticate()

    def _authenticate(self) -> None:
        body = json.dumps(
            {"username": self.username, "password": self.password, "grant_type": "password"}
        ).encode()
        req = urllib.request.Request(
            f"{BASE_URL}/v3/oauth2/tokens",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        payload = json.loads(urllib.request.urlopen(req, timeout=30).read())
        self.token = payload["result"]["access_token"]["access_token"]
        self.expires_at = _jwt_exp(self.token)
        if self.expires_at:
            remaining_min = (self.expires_at - time.time()) / 60
            print(f"  (Tapis token refreshed, valid ~{remaining_min:.0f} more minutes)")

    def ensure_fresh(self) -> str:
        if self._static_token:
            return self.token  # user-supplied token: nothing we can do to refresh it
        needs_refresh = (
            self.expires_at is None
            or time.time() > self.expires_at - self.REFRESH_MARGIN_SECONDS
        )
        if needs_refresh:
            self._authenticate()
        return self.token

    def force_refresh(self) -> str:
        if self._static_token:
            raise RuntimeError(
                "Tapis token expired and TAPIS_TOKEN was set statically; re-run with "
                "TAPIS_USERNAME/TAPIS_PASSWORD instead so this script can refresh it."
            )
        self._authenticate()
        return self.token


def submit(box: TokenBox, case: dict) -> str:
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

    for attempt in range(2):  # one retry after a forced token refresh on 401
        token = box.ensure_fresh()
        req = urllib.request.Request(
            f"{BASE_URL}/v3/jobs/submit",
            data=json.dumps(body).encode(),
            headers={"X-Tapis-Token": token, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            if exc.code == 401 and attempt == 0:
                print("    (token expired mid-run; refreshing and retrying submit)")
                box.force_refresh()
                continue
            raise SystemExit(f"Job submission failed for {case['label']} ({exc.code}): {detail}")
    else:
        raise SystemExit(f"Job submission failed for {case['label']}: retries exhausted")

    uuid = resp["result"]["uuid"]
    print(f"  Submitted {job_name}: {uuid}")
    print(f"    {BASE_URL}/v3/jobs/{uuid}")
    return uuid


def watch(box: TokenBox, uuid: str, poll_seconds: int) -> dict:
    last_status = None
    start = time.time()
    while True:
        token = box.ensure_fresh()
        req = urllib.request.Request(
            f"{BASE_URL}/v3/jobs/{uuid}",
            headers={"X-Tapis-Token": token},
        )
        try:
            result = json.loads(urllib.request.urlopen(req, timeout=30).read())["result"]
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                print("    (token expired mid-poll; refreshing)")
                box.force_refresh()
                continue
            raise
        status = result.get("status", "UNKNOWN")
        if status != last_status:
            elapsed_min = (time.time() - start) / 60
            print(f"    [{time.strftime('%H:%M:%S')}] {status} (+{elapsed_min:.1f} min)")
            last_status = status
        if status in TERMINAL_STATES:
            return result
        time.sleep(poll_seconds)


def fetch_log_tail(box: TokenBox, uuid: str, max_chars: int = 800) -> str:
    token = box.ensure_fresh()
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

    box = TokenBox()
    results = []

    for i, case in enumerate(cases, start=1):
        print(f"\n=== [{i}/{len(cases)}] {case['label']} ({case['app_id']}) ===")
        start = time.time()
        try:
            uuid = submit(box, case)
            final = watch(box, uuid, args.poll_seconds)
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
                print("    " + fetch_log_tail(box, uuid).replace("\n", "\n    "))
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
