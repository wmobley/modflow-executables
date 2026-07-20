#!/usr/bin/env python3
"""Validate a zip archive is safe to extract before staging it into a MODFLOW 6 run directory."""
from __future__ import annotations

import stat
import sys
import zipfile

MAX_ENTRIES = 20000
MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB


def is_unsafe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    return ".." in normalized.split("/")


def is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def validate(path: str) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if not infos:
                return False, "archive contains no entries"
            if len(infos) > MAX_ENTRIES:
                return False, f"entry count {len(infos)} exceeds cap {MAX_ENTRIES}"

            total_uncompressed = 0
            for info in infos:
                if is_unsafe_name(info.filename):
                    return False, f"unsafe path in archive: {info.filename}"
                if is_symlink(info):
                    return False, f"symlink entry rejected: {info.filename}"
                total_uncompressed += info.file_size

            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                return False, (
                    f"uncompressed size {total_uncompressed} exceeds cap "
                    f"{MAX_UNCOMPRESSED_BYTES}"
                )
    except zipfile.BadZipFile as exc:
        return False, f"not a valid zip file: {exc}"

    return True, "ok"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_zip.py <zip-path>", file=sys.stderr)
        return 2

    ok, message = validate(sys.argv[1])
    if not ok:
        print(f"[unsafe] {message}", file=sys.stderr)
        return 1

    print(f"[ok] {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
