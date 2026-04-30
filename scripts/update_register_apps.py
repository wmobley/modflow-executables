#!/usr/bin/env python3
"""Update MODFLOW app image tags and register app versions in Tapis."""
from __future__ import annotations

import argparse
import json
import os
from getpass import getpass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIRS = (
    "modflow6",
    "modflow-usg",
    "modflow-2000",
    "modflow-96",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update app.json version/image tags and register them with Tapis."
    )
    parser.add_argument(
        "--sha",
        required=True,
        help="Git SHA or image tag suffix. Accepts either 'abc1234' or 'sha-abc1234'.",
    )
    parser.add_argument(
        "--version",
        help="Tapis app version. Defaults to '0.0.<sha>' after removing a leading 'sha-'.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TAPIS_BASE_URL", "https://portals.tapis.io"),
        help="Tapis base URL. Defaults to https://portals.tapis.io.",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("TAPIS_USERNAME"),
        help="Tapis username. Defaults to TAPIS_USERNAME.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("TAPIS_PASSWORD"),
        help="Tapis password. Defaults to TAPIS_PASSWORD, otherwise prompts.",
    )
    parser.add_argument(
        "--owner",
        default="wmobley",
        help="GHCR image owner/organization. Defaults to wmobley.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned app definition changes without writing files or registering them.",
    )
    return parser.parse_args()


def normalize_sha(raw_sha: str) -> str:
    sha = raw_sha.strip()
    if sha.startswith("sha-"):
        sha = sha.removeprefix("sha-")
    if not sha:
        raise ValueError("SHA cannot be empty.")
    return sha


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")


def app_json_paths() -> list[Path]:
    return [REPO_ROOT / app_dir / "app.json" for app_dir in APP_DIRS]


def update_app(path: Path, version: str, image_tag: str, owner: str, write: bool) -> dict[str, Any]:
    app = read_json(path)
    image_name = path.parent.name

    app["version"] = version
    app["containerImage"] = f"docker://ghcr.io/{owner}/{image_name}:{image_tag}"
    if write:
        write_json(path, app)
    return app


def tapis_client(base_url: str, username: str | None, password: str | None) -> Any:
    from tapipy.tapis import Tapis

    if not username:
        username = input("Tapis username: ").strip()
    if not password:
        password = getpass("Tapis password: ")

    tapis = Tapis(base_url=base_url, username=username, password=password)
    tapis.get_tokens()
    return tapis


def register_apps(tapis: Any, apps: list[dict[str, Any]]) -> None:
    for app in apps:
        app_id = app["id"]
        version = app["version"]
        image = app["containerImage"]
        print(f"Registering {app_id} {version} -> {image}")
        tapis.apps.createAppVersion(**app)


def main() -> int:
    args = parse_args()
    sha = normalize_sha(args.sha)
    version = args.version or f"0.0.{sha}"
    image_tag = f"sha-{sha}"

    apps: list[dict[str, Any]] = []
    for path in app_json_paths():
        if not path.exists():
            raise FileNotFoundError(path)
        app = update_app(path, version, image_tag, args.owner, write=not args.dry_run)
        apps.append(app)
        action = "Would update" if args.dry_run else "Updated"
        print(f"{action} {path.relative_to(REPO_ROOT)}: {version}, {app['containerImage']}")

    if args.dry_run:
        print("Dry run complete. No files were changed and no Tapis calls were made.")
        return 0

    tapis = tapis_client(args.base_url, args.username, args.password)
    register_apps(tapis, apps)
    print("Registered all app versions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
