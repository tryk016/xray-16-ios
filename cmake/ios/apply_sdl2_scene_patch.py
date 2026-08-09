#!/usr/bin/env python3
"""Fail-closed applier for the OpenXRay SDL2 UIKit-scene backport.

The ExternalProject source tree is disposable.  This tool refuses to patch an
unknown, partially patched, or otherwise altered tree, so a changed upstream
archive cannot silently acquire a near-match UIKit lifecycle implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ContractError(RuntimeError):
    """The controlled SDL2 source state does not satisfy the patch contract."""


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_count: int
    lines: tuple[str, ...]


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot load manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ContractError("unsupported SDL2 scene patch manifest")
    return manifest


def require_string(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"manifest field {key!r} must be a nonempty string")
    return value


def checked_files(manifest: dict[str, object]) -> dict[str, dict[str, str]]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise ContractError("manifest files must be a nonempty object")

    files: dict[str, dict[str, str]] = {}
    for relative, raw_entry in raw_files.items():
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ContractError("manifest contains an unsafe source path")
        if not isinstance(raw_entry, dict):
            raise ContractError(f"manifest entry {relative!r} is not an object")
        preimage = require_string(raw_entry, "preimage")
        postimage = require_string(raw_entry, "postimage")
        if not re.fullmatch(r"[0-9a-f]{64}", preimage):
            raise ContractError(f"manifest preimage is not SHA-256: {relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", postimage):
            raise ContractError(f"manifest postimage is not SHA-256: {relative}")
        files[relative] = {"preimage": preimage, "postimage": postimage}
    return files


def source_file(source_dir: Path, relative: str) -> Path:
    source_root = source_dir.resolve()
    candidate = (source_root / relative).resolve()
    try:
        candidate.relative_to(source_root)
    except ValueError as error:
        raise ContractError(f"manifest path escapes source tree: {relative}") from error
    if not candidate.is_file():
        raise ContractError(f"controlled SDL2 source is missing: {relative}")
    return candidate


def check_inputs(
    manifest: dict[str, object], patch: Path, expected_url: str, expected_url_sha256: str
) -> dict[str, dict[str, str]]:
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ContractError("manifest source is missing")
    if require_string(source, "url") != expected_url:
        raise ContractError("SDL2 URL differs from the repository-controlled manifest")
    if require_string(source, "sha256") != expected_url_sha256:
        raise ContractError("SDL2 archive SHA-256 differs from the repository-controlled manifest")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_url_sha256):
        raise ContractError("SDL2 archive SHA-256 must be lowercase hexadecimal")
    expected_patch_sha = require_string(manifest, "patch_sha256")
    actual_patch_sha = sha256_file(patch)
    if actual_patch_sha != expected_patch_sha:
        raise ContractError("SDL2 scene patch SHA-256 does not match the manifest")
    return checked_files(manifest)


def parse_patch(patch: Path) -> dict[str, tuple[Hunk, ...]]:
    try:
        lines = patch.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as error:
        raise ContractError(f"cannot read SDL2 scene patch: {error}") from error

    by_file: dict[str, list[Hunk]] = {}
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            raise ContractError(f"patch line {index + 1}: expected old-file header")
        old_path = lines[index][4:].rstrip("\n")
        index += 1
        if index == len(lines) or not lines[index].startswith("+++ "):
            raise ContractError(f"patch line {index + 1}: expected new-file header")
        new_path = lines[index][4:].rstrip("\n")
        index += 1
        if not old_path.startswith("a/") or not new_path.startswith("b/"):
            raise ContractError("patch must use a/ and b/ paths")
        relative = old_path[2:]
        if relative != new_path[2:] or not relative:
            raise ContractError("patch rename/delete/add is not allowed")
        if relative in by_file:
            raise ContractError(f"patch has duplicate file section: {relative}")

        hunks: list[Hunk] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            match = _HUNK.match(lines[index])
            if match is None:
                raise ContractError(f"patch line {index + 1}: expected hunk header")
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_count = int(match.group(4) or "1")
            index += 1
            hunk_lines: list[str] = []
            while index < len(lines) and not lines[index].startswith(("@@", "--- ")):
                line = lines[index]
                if line.startswith("\\ No newline at end of file"):
                    raise ContractError("patches without final newlines are not supported")
                if not line or line[0] not in (" ", "+", "-"):
                    raise ContractError(f"patch line {index + 1}: invalid hunk line")
                hunk_lines.append(line)
                index += 1
            old_lines = sum(line[0] != "+" for line in hunk_lines)
            new_lines = sum(line[0] != "-" for line in hunk_lines)
            if old_lines != old_count or new_lines != new_count:
                raise ContractError(f"patch hunk count mismatch for {relative}")
            hunks.append(Hunk(old_start, old_count, new_count, tuple(hunk_lines)))
        if not hunks:
            raise ContractError(f"patch has no hunks for {relative}")
        by_file[relative] = hunks
    return {relative: tuple(hunks) for relative, hunks in by_file.items()}


def apply_hunks(current: str, relative: str, hunks: tuple[Hunk, ...]) -> str:
    lines = current.splitlines(keepends=True)
    offset = 0
    for hunk in hunks:
        start = hunk.old_start - 1 + offset
        expected = [line[1:] for line in hunk.lines if line[0] != "+"]
        replacement = [line[1:] for line in hunk.lines if line[0] != "-"]
        if start < 0 or lines[start : start + len(expected)] != expected:
            raise ContractError(f"patch context does not match controlled SDL2 source: {relative}")
        lines[start : start + len(expected)] = replacement
        offset += len(replacement) - len(expected)
    return "".join(lines)


def atomic_write(path: Path, data: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def apply_patch(
    source_dir: Path,
    manifest: dict[str, object],
    patch: Path,
    expected_url: str,
    expected_url_sha256: str,
    verify_only: bool,
) -> str:
    files = check_inputs(manifest, patch, expected_url, expected_url_sha256)
    actual_hashes = {
        relative: sha256_file(source_file(source_dir, relative))
        for relative in files
    }
    preimage = all(actual_hashes[path] == entry["preimage"] for path, entry in files.items())
    postimage = all(actual_hashes[path] == entry["postimage"] for path, entry in files.items())
    if postimage:
        return "postimage"
    if not preimage:
        raise ContractError("SDL2 source is mixed, foreign, or only partially patched")
    if verify_only:
        return "preimage"

    patch_hunks = parse_patch(patch)
    changed = {path for path, entry in files.items() if entry["preimage"] != entry["postimage"]}
    if set(patch_hunks) != changed:
        raise ContractError("patch paths do not exactly match changed manifest paths")

    outputs: dict[Path, bytes] = {}
    for relative, entry in files.items():
        source = source_file(source_dir, relative)
        original = source.read_bytes()
        if relative in patch_hunks:
            try:
                updated = apply_hunks(original.decode("utf-8"), relative, patch_hunks[relative]).encode("utf-8")
            except UnicodeDecodeError as error:
                raise ContractError(f"controlled SDL2 source is not UTF-8: {relative}") from error
        else:
            updated = original
        if hashlib.sha256(updated).hexdigest() != entry["postimage"]:
            raise ContractError(f"postimage SHA-256 mismatch for {relative}")
        outputs[source] = updated

    for source, updated in outputs.items():
        atomic_write(source, updated)
    return "patched"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--url-sha256", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        state = apply_patch(
            args.source,
            manifest,
            args.patch,
            args.url,
            args.url_sha256,
            args.verify_only,
        )
    except (ContractError, OSError) as error:
        print(f"SDL2 UIKit scene patch: FAIL: {error}", file=sys.stderr)
        return 1
    print(f"SDL2 UIKit scene patch: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
