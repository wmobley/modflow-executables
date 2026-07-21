#!/usr/bin/env python3
"""Submit a MODFLOW 6 Tapis job using the mf6ArchiveUrl input.

Fetches a model archive by URL (downloaded and unzipped inside the job,
validated for path traversal / symlinks / size) instead of uploading every
input file individually.

Usage:
    TAPIS_TOKEN=... python3 submit_mf6_archive_job.py \\
        --archive-url https://gw-models.s3.amazonaws.com/Download_GAMs/trnt_n/trnt_n_v301/NTGAM_model_files.zip

    # Get a token via username/password if TAPIS_TOKEN is not set:
    TAPIS_USERNAME=wmobley TAPIS_PASSWORD=... python3 submit_mf6_archive_job.py \\
        --archive-url https://... --watch

    # Also stage an override file from a Tapis system (optional, repeatable):
    python3 submit_mf6_archive_job.py --archive-url https://... \\
        --file-input mf6-rcha=tapis://ptdatax.project.PTDATAX-272/workingGAMs/overrides/custom.rcha
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
DEFAULT_APP_ID = "modflow6-simulation"
DEFAULT_APP_VERSION = "0.0.dc13cc2"
TERMINAL_STATES = {"FINISHED", "FAILED", "CANCELLED", "STOPPED"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--archive-url",
        required=True,
        help="https:// URL to a zip archive of the MODFLOW 6 model.",
    )
    parser.add_argument(
        "--app-id", default=DEFAULT_APP_ID, help=f"Tapis app id. Defaults to {DEFAULT_APP_ID}."
    )
    parser.add_argument(
        "--app-version",
        default=DEFAULT_APP_VERSION,
        help=f"Tapis app version. Defaults to {DEFAULT_APP_VERSION}.",
    )
    parser.add_argument("--job-name", default=None, help="Job name. Defaults to an auto-generated name.")
    parser.add_argument(
        "--file-input",
        action="append",
        default=[],
        metavar="NAME=tapis://...",
        help=(
            "Optional override file input, e.g. mf6-wel=tapis://<system>/<path>/model.wel. "
            "Repeatable."
        ),
    )
    parser.add_argument(
        "--max-minutes",
        type=int,
        default=None,
        help=(
            "Override the job's wall-clock time limit in minutes for this submission only "
            "(the app's registered default is 60, which is too short for a full NTGAM run)."
        ),
    )
    parser.add_argument("--watch", action="store_true", help="Poll the job until it reaches a terminal state.")
    parser.add_argument("--poll-seconds", type=int, default=20, help="Seconds between status polls with --watch.")
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


def parse_file_inputs(raw_inputs: list[str]) -> list[dict]:
    file_inputs = []
    for raw in raw_inputs:
        if "=" not in raw:
            raise ValueError(f"--file-input must be NAME=sourceUrl, got: {raw}")
        name, source_url = raw.split("=", 1)
        file_inputs.append({"name": name.strip(), "sourceUrl": source_url.strip()})
    return file_inputs


def submit(token: str, args: argparse.Namespace) -> str:
    if not args.archive_url.startswith("https://"):
        raise ValueError("--archive-url must be an https:// URL (the app rejects anything else).")

    job_name = args.job_name or f"mf6-archive-{int(time.time())}"
    body: dict = {
        "name": job_name,
        "appId": args.app_id,
        "appVersion": args.app_version,
        "parameterSet": {
            "appArgs": [
                {"name": "mf6ArchiveUrl", "arg": args.archive_url},
            ],
        },
    }

    file_inputs = parse_file_inputs(args.file_input)
    if file_inputs:
        body["fileInputs"] = file_inputs

    if args.max_minutes:
        body["maxMinutes"] = args.max_minutes

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
        raise SystemExit(f"Job submission failed ({exc.code}): {detail}")

    uuid = resp["result"]["uuid"]
    print(f"Submitted job {job_name}: {uuid}")
    print(f"  {BASE_URL}/v3/jobs/{uuid}")
    return uuid


def watch(token: str, uuid: str, poll_seconds: int) -> None:
    print(f"Watching job {uuid} (polling every {poll_seconds}s)...")
    last_status = None
    while True:
        req = urllib.request.Request(
            f"{BASE_URL}/v3/jobs/{uuid}",
            headers={"X-Tapis-Token": token},
        )
        result = json.loads(urllib.request.urlopen(req, timeout=30).read())["result"]
        status = result.get("status", "UNKNOWN")
        if status != last_status:
            print(f"  [{time.strftime('%H:%M:%S')}] {status}")
            last_status = status
        if status in TERMINAL_STATES:
            if status != "FINISHED":
                print(f"  lastMessage: {result.get('lastMessage')}")
            break
        time.sleep(poll_seconds)


def main() -> int:
    args = parse_args()
    token = get_token()
    uuid = submit(token, args)
    if args.watch:
        watch(token, uuid, args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
