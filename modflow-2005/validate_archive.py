#!/usr/bin/env python3
"""Validate archive paths and size before extraction."""
from __future__ import annotations

import sys
import zipfile

MAX_ENTRIES = 20000
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024


def unsafe(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return normalized.startswith("/") or ".." in normalized.split("/")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_archive.py <archive>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries:
                raise ValueError("archive contains no entries")
            if len(entries) > MAX_ENTRIES:
                raise ValueError("archive contains too many entries")
            total = 0
            for entry in entries:
                if unsafe(entry.filename):
                    raise ValueError(f"unsafe path in archive: {entry.filename}")
                total += entry.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("archive is larger than the safety limit")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"[unsafe] {exc}", file=sys.stderr)
        return 1
    print("zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
