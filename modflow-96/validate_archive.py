#!/usr/bin/env python3
"""Validate a zip or 7z archive's entry paths before extraction.

Only path-traversal/absolute-path safety and size/entry caps are checked here
(archive-format symlink attributes have proven unreliable to trust across
zip/7z builds). Symlink safety is enforced separately by the caller, by
inspecting the actual extracted files on disk after extraction.

On success, prints the detected archive format ("zip" or "7z") to stdout so
the caller knows which tool to use to extract it, and exits 0. On failure,
prints the reason to stderr and exits 1.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile

MAX_ENTRIES = 20000
MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB

ZIP_MAGIC = b"PK"
SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"


def is_unsafe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    return ".." in normalized.split("/")


def sniff_format(path: str) -> str:
    with open(path, "rb") as fh:
        header = fh.read(8)
    if header.startswith(SEVENZ_MAGIC):
        return "7z"
    if header.startswith(ZIP_MAGIC):
        return "zip"
    return "unknown"


def validate_zip(path: str) -> tuple[bool, str]:
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
                total_uncompressed += info.file_size

            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                return False, (
                    f"uncompressed size {total_uncompressed} exceeds cap "
                    f"{MAX_UNCOMPRESSED_BYTES}"
                )
    except zipfile.BadZipFile as exc:
        return False, f"not a valid zip file: {exc}"

    return True, "ok"


def validate_7z(path: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["7z", "l", "-slt", path],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except FileNotFoundError:
        return False, "7z binary not available to validate .7z archive"
    except subprocess.CalledProcessError as exc:
        return False, f"not a valid 7z file: {exc.stderr.strip()[:300]}"
    except subprocess.TimeoutExpired:
        return False, "7z listing timed out"

    marker = "----------"
    if marker not in result.stdout:
        return False, "archive contains no entries"
    entries_section = result.stdout.split(marker, 1)[1]

    entry_count = 0
    total_size = 0
    current_path: str | None = None
    for raw_line in entries_section.splitlines():
        line = raw_line.rstrip()
        if line.startswith("Path = "):
            current_path = line[len("Path = ") :]
            entry_count += 1
            if is_unsafe_name(current_path):
                return False, f"unsafe path in archive: {current_path}"
        elif line.startswith("Size = ") and current_path is not None:
            try:
                total_size += int(line[len("Size = ") :])
            except ValueError:
                pass

    if entry_count == 0:
        return False, "archive contains no entries"
    if entry_count > MAX_ENTRIES:
        return False, f"entry count {entry_count} exceeds cap {MAX_ENTRIES}"
    if total_size > MAX_UNCOMPRESSED_BYTES:
        return False, f"uncompressed size {total_size} exceeds cap {MAX_UNCOMPRESSED_BYTES}"

    return True, "ok"


def validate(path: str) -> tuple[bool, str, str]:
    fmt = sniff_format(path)
    if fmt == "zip":
        ok, message = validate_zip(path)
    elif fmt == "7z":
        ok, message = validate_7z(path)
    else:
        ok, message = False, "unrecognized archive format (expected zip or 7z)"
    return ok, message, fmt


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_archive.py <archive-path>", file=sys.stderr)
        return 2

    ok, message, fmt = validate(sys.argv[1])
    if not ok:
        print(f"[unsafe] {message}", file=sys.stderr)
        return 1

    print(fmt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
