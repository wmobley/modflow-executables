#!/usr/bin/env python3
"""Fetch status history and stdout/stderr for a Tapis job, to diagnose a failure.

Usage:
    TAPIS_TOKEN=... python3 fetch_job_output.py <job-uuid>

    # Get a token via username/password if TAPIS_TOKEN is not set:
    TAPIS_USERNAME=wmobley TAPIS_PASSWORD=... python3 fetch_job_output.py <job-uuid>
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from getpass import getpass

BASE_URL = os.environ.get("TAPIS_BASE_URL", "https://portals.tapis.io").rstrip("/")
LOG_CANDIDATES = ("tapisjob.out", "tapisjob.err")


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


def _get(token: str, path: str) -> dict:
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"X-Tapis-Token": token})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _get_text(token: str, path: str) -> str | None:
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"X-Tapis-Token": token})
    try:
        return urllib.request.urlopen(req, timeout=30).read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace") if exc.fp else ""
        print(f"  (HTTP {exc.code} fetching {path}: {detail[:500]})")
        return None


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: fetch_job_output.py <job-uuid>", file=sys.stderr)
        return 2
    job_uuid = sys.argv[1]

    token = get_token()

    print("=== Job detail ===")
    detail = _get(token, f"/v3/jobs/{job_uuid}").get("result", {})
    for key in ("status", "lastMessage", "remoteStarted", "remoteEnded", "appId", "appVersion"):
        print(f"  {key}: {detail.get(key)}")

    print("\n=== Status history ===")
    history = _get(token, f"/v3/jobs/{job_uuid}/history").get("result", [])
    for event in history:
        print(f"  [{event.get('created')}] {event.get('event')}: {event.get('description')}")

    print("\n=== Output listing (job root) ===")
    try:
        listing = _get(token, f"/v3/jobs/{job_uuid}/output/list/").get("result", [])
        for item in listing:
            print(f"  {item.get('name')} ({item.get('size')} bytes)")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace") if exc.fp else ""
        print(f"  (could not list output: HTTP {exc.code}: {detail[:500]})")

    for name in LOG_CANDIDATES:
        print(f"\n=== {name} ===")
        text = _get_text(token, f"/v3/jobs/{job_uuid}/output/download/{name}")
        if text is None:
            print(f"  ({name} not found)")
            continue
        print(text[-6000:])  # tail, in case it's long

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
