#!/usr/bin/env python3
"""Integrity and path guards for the isolated retail iOS Simulator workflow.

The runner deliberately keeps the policy here dependency-free: this module is
also used by regression fixtures on hosts without Xcode or CoreSimulator.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Iterable
import uuid


OPENAL_CONTRACT_PATH = Path(__file__).with_name("openal_provider_contract.py")
OPENAL_CONTRACT_SPEC = importlib.util.spec_from_file_location(
    "openal_provider_contract", OPENAL_CONTRACT_PATH
)
if OPENAL_CONTRACT_SPEC is None or OPENAL_CONTRACT_SPEC.loader is None:
    raise RuntimeError(f"could not load OpenAL provider contract: {OPENAL_CONTRACT_PATH}")
OPENAL_CONTRACT = importlib.util.module_from_spec(OPENAL_CONTRACT_SPEC)
OPENAL_CONTRACT_SPEC.loader.exec_module(OPENAL_CONTRACT)


CHUNK = 1024 * 1024
CLONE_LEDGER_SCHEMA = "openxray.retail-clone-ledger.v2"
CLONE_DIAGNOSTIC_SCHEMA = "openxray.retail-clone-stat-diagnostics.v2"
UF_TRACKED = 0x00000040
UF_TRACKED_POLICY = {
    "name": "darwin-uf-tracked-closed-v1",
    "runtime": "27.0",
    "allowed": ["0x00000000", "0x00000040"],
    "tracked": "0x00000040",
    "removal": "forbidden",
}
FCLONEFILEAT_FLAGS = 0x001B  # NOFOLLOW|NOOWNERCOPY|NOFOLLOW_ANY|RESOLVE_BENEATH
RENAME_EXCL = 0x00000004
QUARANTINE_TXN_SCHEMA = "openxray.private-quarantine-transaction.v2"
QUARANTINE_COMPLETE_SCHEMA = "openxray.private-quarantine-completion.v2"
PUBLICATION_TXN_SCHEMA = "openxray.private-rename-publication.v2"
PUBLICATION_COMPLETE_SCHEMA = "openxray.private-rename-publication-completion.v2"
QUARANTINE_PREFIX = ".openxray-quarantine-"
PUBLICATION_PREFIX = ".openxray-publication-"
TRANSACTION_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
CLONE_DESTINATION_BOUNDARY = "validated-before-container-delete"
DEFAULT_XATTRS = frozenset({"com.apple.provenance"})
# This is deliberately not a prefix policy.  CoreSimulator's app data-container
# root may own any subset of these three names, while every other object keeps
# the default provenance-only policy below.
CORE_SIMULATOR_CONTAINER_XATTRS = frozenset({
    "com.apple.containermanager.identifier",
    "com.apple.containermanager.schema-version",
    "com.apple.containermanager.uuid",
})
BASE_ALLOWLIST = ("resources", "levels", "localization", "patches")
SAVES_ROOT = Path("_appdata_") / "savedgames"
REQUIRED_ARCHIVES = frozenset(
    [f"resources/resources.db{index}" for index in range(5)]
    + [f"levels/levels.db{index}" for index in range(2)]
)
AUTOLOAD_NAME = re.compile(r"[a-z0-9](?:[a-z0-9 _.-]{0,126}[a-z0-9])?\Z")
STRING_PATH_CAPACITY = 260
SERVER_OPTION_CAPACITY = 4096
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RUNTIME_FAILURE_MARKERS = (
    "fatal", "stack trace", "assertion", "abort trap",
    "terminating app due to uncaught exception", "termination reason",
    "application terminated", "noscenelifecycleadoption", "sigtrap",
)
MENU_FRAME_PREFIX = "* iOS main menu frame v1 "
MENU_FRAME_MARKER = re.compile(
    r"^\* iOS main menu frame v1 pid=([1-9][0-9]*) frame=(0|[1-9][0-9]*)$"
)
LIFECYCLE_PREFIX = "* iOS lifecycle v1 "
LIFECYCLE_MARKER = re.compile(
    r"^\* iOS lifecycle v1 pid=([1-9][0-9]*) seq=([1-9][0-9]*) "
    r"event=(deactivate|activate)$"
)
NAVIGATION_STEP_TIMEOUT_SECONDS = 60.0
NAVIGATION_STEP_COUNT = 7
NAVIGATION_SUBPROCESS_OVERHEAD_SECONDS = 30.0
NAVIGATION_SUBPROCESS_TIMEOUT_SECONDS = (
    NAVIGATION_STEP_TIMEOUT_SECONDS * NAVIGATION_STEP_COUNT
    + NAVIGATION_SUBPROCESS_OVERHEAD_SECONDS
)
CAPTURE_PARSER_TIMEOUT_SECONDS = 5.0
CAPTURE_TOKEN = re.compile(r"^[0-9a-f]{32}:[1-9][0-9]*$")
GPU_RENDERER_PREFIX = "* GPU vendor: "
APPLE_SOFTWARE_RENDERER_LINE = "* GPU vendor: [Apple Inc.] device: [Apple Software Renderer]"
APPLE_SOFTWARE_RENDERER = "Apple-Software-Renderer"


class GuardError(RuntimeError):
    """A caller attempted an unsafe or unverifiable operation."""


def fail(message: str) -> None:
    raise GuardError(message)


def resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=True)


def absolute_unresolved(path: str | Path) -> Path:
    """Make a path absolute without dereferencing any component."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_external(path: Path, *forbidden: Path) -> None:
    for root in forbidden:
        if path == root or is_within(path, root) or is_within(root, path):
            fail(f"path must be disjoint from protected root: {path} vs {root}")


def require_not_inside(path: Path, *forbidden: Path) -> None:
    for root in forbidden:
        if path == root or is_within(path, root):
            fail(f"path must not equal or be inside protected root: {path} vs {root}")


def safe_relative(value: str) -> Path:
    candidate = Path(value)
    if not value or candidate.is_absolute() or "\\" in value:
        fail(f"unsafe manifest path: {value!r}")
    if any(part in ("", ".", "..") for part in candidate.parts):
        fail(f"unsafe manifest path: {value!r}")
    return candidate


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while block := os.read(descriptor, CHUNK):
        digest.update(block)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def walk_files(root: Path, excluded_top: Iterable[str] = ()) -> list[Path]:
    excluded = set(excluded_top)
    collected: list[Path] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        if relative_current == Path("."):
            kept: list[str] = []
            for name in sorted(dirnames):
                if name in excluded or any(
                    pattern.endswith("*") and name.startswith(pattern[:-1])
                    for pattern in excluded
                ):
                    continue
                child = current_path / name
                if child.is_symlink():
                    fail(f"symlinked directory is not allowed: {child}")
                kept.append(name)
            dirnames[:] = kept
        else:
            for name in dirnames:
                if (current_path / name).is_symlink():
                    fail(f"symlinked directory is not allowed: {current_path / name}")
        for name in sorted(filenames):
            candidate = current_path / name
            if candidate.is_symlink():
                fail(f"symlink is not allowed: {candidate}")
            if not candidate.is_file():
                fail(f"non-regular file is not allowed: {candidate}")
            collected.append(candidate)
    return sorted(collected, key=lambda item: item.relative_to(root).as_posix())


def write_tree_manifest(root: Path, output: Path, excluded_top: Iterable[str] = ()) -> None:
    rows = [(path.relative_to(root).as_posix(), path.stat().st_size, sha256(path))
            for path in walk_files(root, excluded_top)]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as destination:
        destination.write("sha256\tbytes\tpath\n")
        for relative, size, digest in rows:
            destination.write(f"{digest}\t{size}\t{relative}\n")
    temporary.replace(output)


def write_file_manifest(path: Path, output: Path) -> None:
    if path.is_symlink() or not path.is_file():
        fail(f"single-file guard requires a regular non-symlink file: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as destination:
        destination.write("sha256\tbytes\tpath\n")
        destination.write(f"{sha256(path)}\t{path.stat().st_size}\t{path.name}\n")
    temporary.replace(output)


def parse_tree_manifest(path: Path) -> dict[str, tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"cannot read manifest {path}: {error}")
    if not lines or lines[0] != "sha256\tbytes\tpath":
        fail(f"invalid tree manifest header: {path}")
    result: dict[str, tuple[int, str]] = {}
    for line_number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 3:
            fail(f"invalid tree manifest row {line_number}: {path}")
        digest, size_text, relative = fields
        safe_relative(relative)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            fail(f"invalid sha256 at row {line_number}: {path}")
        try:
            size = int(size_text)
        except ValueError:
            fail(f"invalid byte count at row {line_number}: {path}")
        if size < 0 or relative in result:
            fail(f"duplicate or invalid path at row {line_number}: {path}")
        result[relative] = (size, digest)
    return result


def compare_tree(root: Path, expected_path: Path, excluded_top: Iterable[str] = ()) -> None:
    expected = parse_tree_manifest(expected_path)
    actual = {
        path.relative_to(root).as_posix(): (path.stat().st_size, sha256(path))
        for path in walk_files(root, excluded_top)
    }
    if actual != expected:
        fail(f"integrity mismatch for {root} against {expected_path}")


def compare_file(path: Path, expected_path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        fail(f"single-file guard requires a regular non-symlink file: {path}")
    expected = parse_tree_manifest(expected_path)
    actual = {path.name: (path.stat().st_size, sha256(path))}
    if actual != expected:
        fail(f"integrity mismatch for file {path} against {expected_path}")


def relocate_prefix(root: Path, source: Path, replacement: Path) -> None:
    pkgconfig = root / "lib" / "pkgconfig"
    files = sorted(pkgconfig.glob("*.pc")) if pkgconfig.is_dir() else []
    if not files:
        fail(f"prefix has no pkg-config metadata: {pkgconfig}")
    source_bytes = os.fsencode(str(source))
    replacement_bytes = os.fsencode(str(replacement))
    replacements = 0
    for path in files:
        if path.is_symlink() or not path.is_file():
            fail(f"invalid pkg-config metadata: {path}")
        original = path.read_bytes()
        replacements += original.count(source_bytes)
        rewritten = original.replace(source_bytes, replacement_bytes)
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        temporary.write_bytes(rewritten)
        temporary.replace(path)
    if replacements == 0:
        fail(f"pkg-config metadata did not contain source prefix: {source}")
    for path in walk_files(root):
        if source_bytes in path.read_bytes():
            fail(f"source prefix reference remains after relocation: {path}")


def parse_cache_value(lines: list[str], key: str) -> str:
    matches = [line.split("=", 1)[1] for line in lines if line.startswith(f"{key}:") and "=" in line]
    if len(matches) != 1:
        fail(f"CMakeCache must contain exactly one {key}")
    return matches[0]


def validate_cmake_cache(cache: Path, source: Path, prefix: Path, build: Path, repo: Path) -> None:
    if not is_within(cache, build):
        fail(f"CMakeCache is outside isolated build root: {cache}")
    lines = cache.read_text(encoding="utf-8", errors="strict").splitlines()
    expected = {
        "CMAKE_HOME_DIRECTORY": source,
        "CMAKE_PREFIX_PATH": prefix,
        "CMAKE_FIND_ROOT_PATH": prefix,
    }
    for key, path in expected.items():
        value = Path(parse_cache_value(lines, key)).expanduser().resolve(strict=False)
        if value != path:
            fail(f"CMakeCache {key} escaped work root: {value} != {path}")
    repo_build = repo / "build"
    for line in lines:
        if os.fsdecode(os.fsencode(str(repo_build))) in line:
            fail(f"CMakeCache retains repository build path: {line}")


def validate_data_container(path: Path, repo: Path, backup: Path, manifest: Path,
                            simulator_home: Path, udid: str) -> None:
    require_external(path, repo, backup, manifest)
    expected_root = (
        simulator_home / "Library" / "Developer" / "CoreSimulator" / "Devices"
        / udid / "data" / "Containers" / "Data" / "Application"
    ).resolve(strict=True)
    if not is_within(path, expected_root) or path == expected_root:
        fail(f"data container is outside the dedicated Simulator application root: {path}")
    if path.parent != expected_root:
        fail(f"data container must be a direct application child: {path}")


def validate_binary_contract(binary: Path) -> None:
    archs = subprocess.run(
        ("lipo", "-archs", str(binary)), check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if archs.returncode != 0 or archs.stdout.strip() != "arm64":
        fail(f"Simulator binary must contain exactly one arm64 slice: {archs.stdout.strip()}")
    build = subprocess.run(
        ("xcrun", "vtool", "-show-build", str(binary)), check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if build.returncode != 0:
        fail("could not inspect Simulator Mach-O build commands")
    lines = build.stdout.splitlines()
    platforms = [line.strip().split(maxsplit=1)[1] for line in lines if line.strip().startswith("platform ")]
    min_versions = [line.strip().split(maxsplit=1)[1] for line in lines if line.strip().startswith("minos ")]
    build_commands = [line.strip() for line in lines if line.strip().startswith("cmd LC_BUILD_VERSION")]
    legacy_commands = [line.strip() for line in lines if line.strip().startswith("cmd LC_VERSION_MIN_")]
    if platforms != ["IOSSIMULATOR"] or min_versions != ["16.4"]:
        fail(f"unexpected Simulator platform/minOS entries: platforms={platforms}, minos={min_versions}")
    if build_commands != ["cmd LC_BUILD_VERSION"] or legacy_commands:
        fail(f"unexpected Mach-O build-version commands: build={build_commands}, legacy={legacy_commands}")


def validate_openal_configured(cache: Path, prefix: Path, project: Path) -> dict[str, str]:
    """Delegate Simulator provider checks to the shared iOS verifier."""

    return OPENAL_CONTRACT.validate_configured(cache, prefix, project, "iphonesimulator")


def validate_openal_artifact(prefix: Path, binary: Path) -> dict[str, str]:
    """Verify the isolated archive and final Simulator binary without shell rules."""

    return OPENAL_CONTRACT.validate_artifact(prefix, binary, "iphonesimulator")


def validate_openal_runtime_log(log: Path) -> dict[str, str]:
    return OPENAL_CONTRACT.validate_runtime_log(log)


def parse_simctl_launch_pid(output: bytes, bundle: str) -> int:
    """Accept only the documented single-line ``bundle: PID`` launch result."""

    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        fail(f"simctl launch returned non-UTF-8 output: {error}")
    match = re.fullmatch(rf"\s*{re.escape(bundle)}:\s*([1-9][0-9]*)\s*", text)
    if match is None:
        fail(f"malformed simctl launch output: {text!r}")
    return int(match.group(1))


def require_live_app_pid(pid: int, phase: str) -> None:
    """Fail closed unless the exact PID reported by simctl still exists.

    ``ps`` is deliberately used instead of signalling PID 0: the harness must
    observe the app but never send it a signal. Simulator lifecycle cleanup is
    owned by the shell runner through ``simctl terminate/shutdown/delete``.
    """

    probe = subprocess.run(
        ("ps", "-p", str(pid), "-o", "pid="), check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        output = probe.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        fail(f"could not inspect launched Simulator PID during {phase}: {error}")
    if probe.returncode != 0 or output != str(pid):
        fail(f"launched Simulator app PID {pid} is not alive {phase}")


def append_launch_stderr(path: Path, output: bytes) -> None:
    if not output:
        return
    with path.open("ab") as destination:
        destination.write(output)
        if not output.endswith(b"\n"):
            destination.write(b"\n")


def validate_autoload_name(name: str) -> str:
    """Accept only a save name that survives the engine's lowercasing path."""

    try:
        encoded = name.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        fail("autoload save name must be lowercase ASCII")
    if not 1 <= len(encoded) <= 128 or name.endswith(".scop") or AUTOLOAD_NAME.fullmatch(name) is None:
        fail("autoload save name is outside the safe lowercase ASCII contract")
    option = f"{name}/single/alife/load"
    if len(option.encode("ascii")) + 1 > SERVER_OPTION_CAPACITY:
        fail("autoload server option exceeds conservative engine capacity")
    return name


def require_real_directory(path: Path) -> None:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        fail(f"required directory is missing, non-directory, or symlinked: {path}")


def has_casefold_collision(candidate_name: str, sibling_names: Iterable[str]) -> bool:
    folded = candidate_name.casefold()
    return any(name != candidate_name and name.casefold() == folded for name in sibling_names)


def require_save_file(documents: Path, name: str) -> Path:
    """Resolve the one mutable staged save without following any symlink."""

    validate_autoload_name(name)
    require_real_directory(documents)
    appdata = documents / "_appdata_"
    saves = appdata / "savedgames"
    require_real_directory(appdata)
    require_real_directory(saves)
    candidate = saves / f"{name}.scop"
    try:
        details = candidate.lstat()
    except FileNotFoundError:
        fail(f"autoload save is missing: {candidate.name}")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_size <= 0:
        fail(f"autoload save must be a nonempty regular non-symlink file: {candidate.name}")
    if has_casefold_collision(candidate.name, (sibling.name for sibling in saves.iterdir())):
        fail(f"autoload save has a casefold collision: {candidate.name}")
    # The engine builds full paths in fixed-size string_path buffers.  Keep a
    # conservative NUL-inclusive bound even though the current save is short.
    if len(os.fsencode(str(candidate))) + 1 > STRING_PATH_CAPACITY:
        fail("autoload save path exceeds conservative string_path capacity")
    return candidate


def autoload_config(name: str, *, ios_diagnostics: bool = False,
                    ios_autoinput: bool = False, quickload_evidence: bool = False) -> bytes:
    validate_autoload_name(name)
    if quickload_evidence and not (ios_diagnostics and ios_autoinput):
        fail("QuickLoad evidence config requires diagnostics and automatic input")
    contents = (
        "keypress_on_start 0\n"
        f"ios_diagnostics {1 if ios_diagnostics else 0}\n"
        f"ios_autoinput {1 if ios_autoinput else 0}\n"
    )
    if quickload_evidence:
        contents += "bind quick_save kF5\nbind quick_load kF9\n"
    return (contents + f"start server({name}/single/alife/load) client(localhost)\n").encode("ascii")


def write_new_regular(path: Path, data: bytes) -> tuple[int, int]:
    """Write a new 0600 file without following its final component."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(existing.st_mode):
            kind = "symlink"
        elif stat.S_ISREG(existing.st_mode):
            kind = "regular file"
        else:
            kind = "non-regular path"
        fail(f"new regular-file destination already exists as a {kind}: {path}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    owned_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            fail(f"generated file is not regular: {path}")
        owned_identity = (opened.st_dev, opened.st_ino)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail(f"short write creating generated file: {path}")
            view = view[written:]
        os.fsync(descriptor)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size != len(data):
            fail(f"generated file is not exact regular content: {path}")
        os.close(descriptor)
        descriptor = None
        return owned_identity
    except BaseException as original:
        cleanup_error: BaseException | None = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                cleanup_error = error
            descriptor = None
        if owned_identity is not None:
            try:
                _quarantine_owned_path(path.absolute(), owned_identity,
                                       f"failed generated file {path.name}")
            except BaseException as error:
                cleanup_error = error
        if cleanup_error is not None:
            fail(f"generated file failed and quarantine was incomplete: "
                 f"{original}; cleanup={cleanup_error}")
        raise


def prepare_report(path: Path, data: bytes) -> None:
    """Create a complete private report candidate before cleanup can begin."""

    if len(data) > CHUNK:
        fail("Simulator report exceeds the bounded size")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        fail(f"Simulator report is not UTF-8: {error}")
    lines = text.splitlines()
    if not lines or lines[0] != "result=PASS" or sum(line.startswith("result=") for line in lines) != 1:
        fail("Simulator report candidate has an invalid result marker")
    if "\x00" in text:
        fail("Simulator report candidate contains NUL")
    require_real_directory(path.parent)
    write_new_regular(path, data)


def publish_report(source: Path, destination: Path,
                   capture_manifest_state: Path | None = None,
                   clone_prepared: Path | None = None, clone_ledger: Path | None = None,
                   staged_manifest: Path | None = None) -> None:
    """Commit one validated single-link pending report by exclusive rename."""

    source = _absolute_unresolved_clone_path(source, "prepared Simulator report")
    destination = _absolute_unresolved_clone_path(destination, "published Simulator report")
    if source.parent != destination.parent:
        fail("Simulator report pending/final names require one trusted parent")
    clone_binding = (clone_prepared, clone_ledger, staged_manifest)
    if any(item is not None for item in clone_binding) and any(item is None for item in clone_binding):
        fail("clone report publication requires prepared root, ledger and staged manifest together")
    manifest_dependencies: dict[str, object] | None = None
    clone_dependencies: dict[str, object] | None = None
    parent_fd = -1
    try:
        if capture_manifest_state is not None:
            manifest_dependencies = _pin_capture_manifest_dependencies(
                capture_manifest_state)
        if clone_prepared is not None:
            clone_dependencies = _pin_clone_publication_dependencies(
                clone_prepared, clone_ledger, staged_manifest)
        parent_fd = _open_absolute_directory_nofollow(
            source.parent, "Simulator report publication parent")

        def validate_dependencies(state: dict[str, object], _metadata: dict[str, object],
                                  active_publication: dict[str, object] | None) -> None:
            try:
                prepare_lines = state["data"].decode("utf-8", errors="strict").splitlines()
            except UnicodeDecodeError as error:
                fail(f"prepared Simulator report is not UTF-8: {error}")
            if (not prepare_lines or prepare_lines[0] != "result=PASS"
                    or sum(line.startswith("result=") for line in prepare_lines) != 1):
                fail("prepared Simulator report has an invalid result marker")
            if manifest_dependencies is not None:
                _revalidate_capture_manifest_dependencies(manifest_dependencies)
                require_capture_manifest_report_binding(
                    prepare_lines, manifest_dependencies["binding"])
            if clone_dependencies is not None:
                _revalidate_clone_publication_dependencies(
                    clone_dependencies, state["data"], parent_fd, source.parent,
                    active_publication)

        _publish_owned_rename_at(
            parent_fd, source.parent, source.name, destination.name,
            "Simulator report", validate=validate_dependencies,
            post_commit=validate_dependencies)
    finally:
        try:
            if parent_fd >= 0:
                os.close(parent_fd)
        finally:
            try:
                if clone_dependencies is not None:
                    _close_clone_publication_dependencies(clone_dependencies)
            finally:
                if manifest_dependencies is not None:
                    _close_capture_manifest_dependencies(manifest_dependencies)


def write_autoload_config(documents: Path, name: str, evidence: Path, manifest: Path,
                          *, ios_diagnostics: bool = False, ios_autoinput: bool = False,
                          ui_captures: bool = False, quickload_evidence: bool = False) -> None:
    """Generate the only non-retail config inside a staged Simulator container."""

    require_save_file(documents, name)
    appdata = documents / "_appdata_"
    target = appdata / "user.ltx"
    if target.exists() or target.is_symlink():
        fail("generated user.ltx destination must not already exist")
    if evidence.exists() or evidence.is_symlink() or manifest.exists() or manifest.is_symlink():
        fail("autoload evidence destination must not already exist")
    require_real_directory(evidence.parent)
    require_real_directory(manifest.parent)
    if ios_diagnostics and ios_autoinput and not (ui_captures or quickload_evidence):
        fail("autoload config cannot enable diagnostics and automatic input together")
    if ui_captures and not (ios_diagnostics and ios_autoinput):
        fail("native UI captures require both diagnostics and automatic input")
    if quickload_evidence and not (ios_diagnostics and ios_autoinput):
        fail("QuickLoad evidence requires both diagnostics and automatic input")
    contents = autoload_config(name, ios_diagnostics=ios_diagnostics,
                               ios_autoinput=ios_autoinput,
                               quickload_evidence=quickload_evidence)
    write_new_regular(target, contents)
    write_new_regular(evidence, contents)
    if target.read_bytes() != contents or evidence.read_bytes() != contents:
        fail("generated user.ltx evidence mismatch")
    write_file_manifest(evidence, manifest)


def write_selected_save_state(path: Path, output: Path) -> None:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_size <= 0:
        fail(f"selected save must be a nonempty regular non-symlink file: {path.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_tree = f"sha256\tbytes\tpath\n{sha256(path)}\t{details.st_size}\t{path.name}\n"
    write_new_regular(output, write_tree.encode("utf-8"))


def parse_selected_save_state(path: Path) -> tuple[int, str, str]:
    values = parse_tree_manifest(path)
    if len(values) != 1:
        fail(f"selected save manifest must contain exactly one entry: {path}")
    relative, (size, digest) = next(iter(values.items()))
    if Path(relative).name != relative or size <= 0:
        fail(f"selected save manifest is invalid: {path}")
    return size, digest, relative


def compare_selected_save_state(path: Path, before: Path, after: Path, report: Path) -> None:
    before_size, before_digest, expected_name = parse_selected_save_state(before)
    if path.name != expected_name:
        fail("selected save path does not match its before manifest")
    write_selected_save_state(path, after)
    after_size, after_digest, _ = parse_selected_save_state(after)
    status = "unchanged" if (before_size, before_digest) == (after_size, after_digest) else "modified"
    contents = (
        f"path={path.name}\n"
        f"before_bytes={before_size}\n"
        f"before_sha256={before_digest}\n"
        f"after_bytes={after_size}\n"
        f"after_sha256={after_digest}\n"
        f"status={status}\n"
    )
    write_new_regular(report, contents.encode("utf-8"))


def runtime_metadata(path: Path, *, autoload_save: str | None, level: str | None,
                     pid: int) -> None:
    if autoload_save is None:
        contents = (
            "runtime_mode=menu\n"
            "runtime_boundary=rendered_main_menu_frame\n"
            f"pid={pid}\n"
        )
    else:
        if level is None:
            fail("sync-complete proof did not identify a level")
        contents = (
            "runtime_mode=autoload\n"
            "runtime_boundary=saved_game_sync_complete\n"
            f"pid={pid}\n"
            f"autoload_save={autoload_save}\n"
            f"level={level}\n"
        )
    write_new_regular(path, contents.encode("ascii"))


def first_after(indices: Iterable[int], after: int) -> int | None:
    return next((index for index in indices if index > after), None)


def autoload_sync_complete(lines: list[str], name: str, *,
                           allow_canonical_quickload_marker: bool = False) -> tuple[bool, str | None]:
    """Check the normal save-load path through precache completion.

    The engine itself lowercases the server option.  The external contract is
    stricter: the saved-game success line and path must identify exactly the
    staged name, and no fallback new-game marker is allowed.
    """

    negative = (
        *RUNTIME_FAILURE_MARKERS, "cannot open saved game",
        "saved game version mismatch or saved game is corrupted",
        "cannot find saved game", "cannot load saved game", "there is no saved game",
        "there is no file name specified", "alife version mismatch", "spawn version mismatch",
        "connection rejected", "connection is rejected", "creating new game...",
        "new game is successfully created!",
    )
    if any(marker in line for line in lines for marker in negative):
        fail("autoload log contains a forbidden failure or fallback marker")
    load_indices = [
        index for index, line in enumerate(lines)
        if "-----loading" in line and "/gamedata/configs/system.ltx" in line
    ]
    user_indices = [
        index for index, line in enumerate(lines)
        if "/documents/_appdata_/user.ltx] successfully loaded." in line
    ]
    start_indices = [index for index, line in enumerate(lines) if "starting engine..." in line]
    save_pattern = re.compile(
        rf"^\* game {re.escape(name)} is successfully loaded from file "
        rf"'[^']*/_appdata_/savedgames/{re.escape(name)}\.scop'(?: \([^)]*\))?$"
    )
    quickload_pattern = re.compile(
        r"^\* game player \- quicksave is successfully loaded from file "
        r"'[^']*/_appdata_/savedgames/player \- quicksave\.scop'(?: \([^)]*\))?$"
    )
    any_save_success = [index for index, line in enumerate(lines) if line.startswith("* game ")
                        and " is successfully loaded from file '" in line]
    accepted_indices = [
        index for index, line in enumerate(lines)
        if "client : connection accepted - <all ok>" in line
    ]
    hom_pattern = re.compile(r"^\* loading hom: .*/gamedata/levels/([a-z0-9_.-]+)/level\.hom$")
    sync_indices = [
        index for index, line in enumerate(lines)
        if "* end of synchronization a[1] r[1]" in line
    ]
    memory_indices = [
        index for index, line in enumerate(lines) if "* ios memory after_load:" in line
    ]
    for system in load_indices:
        user = first_after(user_indices, system)
        if user is None:
            continue
        start = first_after(start_indices, user)
        if start is None:
            continue
        initial_matches = [
            index for index in any_save_success
            if index > start and save_pattern.fullmatch(lines[index])
        ]
        quickload_matches = [
            index for index in any_save_success
            if index > start and quickload_pattern.fullmatch(lines[index])
        ] if allow_canonical_quickload_marker else []
        accepted_save_markers = set(initial_matches + quickload_matches)
        mismatched = [
            index for index in any_save_success
            if index > start and index not in accepted_save_markers
        ]
        if mismatched:
            fail("autoload log contains a mismatched saved-game success marker")
        if allow_canonical_quickload_marker and (len(initial_matches) != 1 or len(quickload_matches) != 1):
            fail("QuickLoad marker exception requires exactly one initial and one canonical quickload success marker")
        save = first_after(initial_matches, start)
        if save is None:
            continue
        accepted = first_after(accepted_indices, save)
        if accepted is None:
            continue
        sync = first_after(sync_indices, accepted)
        memory = first_after(memory_indices, sync if sync is not None else accepted)
        if sync is None or memory is None:
            continue
        if allow_canonical_quickload_marker and quickload_matches[0] <= memory:
            fail("QuickLoad success marker must follow the initial synchronized load")
        levels: set[str] = set()
        for line in lines[accepted + 1:sync]:
            match = hom_pattern.fullmatch(line)
            if match is not None:
                levels.add(match.group(1))
        if len(levels) != 1:
            if len(levels) > 1:
                fail("autoload log identified multiple levels before sync-complete")
            continue
        return True, next(iter(levels))
    return False, None


def verify_runtime_log_is_clean(lines: list[str]) -> None:
    if any(marker in line for line in lines for marker in RUNTIME_FAILURE_MARKERS):
        fail("runtime log contains a forbidden failure marker")


def _read_stable_regular_at(parent_fd: int, name: str,
                            label: str) -> tuple[tuple[int, int], bytes]:
    """Read one regular entry relative to a pinned directory descriptor."""
    try:
        initial = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, os.O_RDONLY | nofollow, dir_fd=parent_fd)
    except OSError as error:
        fail(f"cannot open {label} safely: {error}")
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            fail(f"{label} must be a regular non-symlink file")
        contents = bytearray()
        while block := os.read(descriptor, CHUNK):
            contents.extend(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        fail(f"{label} disappeared while being read")
    states = (initial, opened, after, final)
    identities = {(details.st_dev, details.st_ino) for details in states}
    sizes = {details.st_size for details in states}
    mtimes = {details.st_mtime_ns for details in states}
    if (len(identities) != 1 or len(sizes) != 1 or len(mtimes) != 1
            or stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode)):
        fail(f"{label} identity or size changed while being read")
    if len(contents) != after.st_size:
        fail(f"{label} read is truncated")
    return (after.st_dev, after.st_ino), bytes(contents)


def read_stable_regular_nofollow(path: Path, label: str) -> tuple[tuple[int, int], bytes]:
    """Read one path through a component-wise nofollow-pinned parent."""
    parent_fd = _open_absolute_directory_nofollow(path.parent, f"{label} parent")
    try:
        return _read_stable_regular_at(parent_fd, path.name, label)
    finally:
        os.close(parent_fd)


def read_live_appendable_regular_nofollow(path: Path, label: str) -> tuple[tuple[int, int], bytes]:
    """Read a stable prefix while a live regular log may only append.

    The prefix length is fixed from the opened descriptor.  A second read of
    that prefix rejects in-place rewrites; growth after opening is allowed.
    """

    try:
        initial = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as error:
        fail(f"cannot open {label} safely: {error}")
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
                or opened.st_size < initial.st_size):
            fail(f"{label} identity or size changed while being read")
        prefix_size = opened.st_size
        contents = bytearray()
        while len(contents) < prefix_size:
            block = os.read(descriptor, min(CHUNK, prefix_size - len(contents)))
            if not block:
                fail(f"{label} read is truncated")
            contents.extend(block)
        after = os.fstat(descriptor)
        if (not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or after.st_size < prefix_size):
            fail(f"{label} identity or size changed while being read")
        os.lseek(descriptor, 0, os.SEEK_SET)
        verified = bytearray()
        while len(verified) < prefix_size:
            block = os.read(descriptor, min(CHUNK, prefix_size - len(verified)))
            if not block:
                fail(f"{label} read is truncated")
            verified.extend(block)
        verified_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
    except FileNotFoundError:
        fail(f"{label} disappeared while being read")
    states = (initial, opened, after, verified_after, final)
    identities = {(details.st_dev, details.st_ino) for details in states}
    if (len(identities) != 1 or any(not stat.S_ISREG(details.st_mode) for details in states)
            or stat.S_ISLNK(final.st_mode)
            or after.st_size < prefix_size or verified_after.st_size < after.st_size
            or final.st_size < verified_after.st_size):
        fail(f"{label} identity or size changed while being read")
    if verified != contents:
        fail(f"{label} was rewritten while being read")
    stable_mtimes = {
        opened.st_mtime_ns, after.st_mtime_ns,
        verified_after.st_mtime_ns, final.st_mtime_ns,
    }
    if opened.st_size == initial.st_size:
        stable_mtimes.add(initial.st_mtime_ns)
    if (after.st_size == prefix_size and verified_after.st_size == prefix_size
            and final.st_size == prefix_size
            and len(stable_mtimes) != 1):
        fail(f"{label} was rewritten while being read")
    return (opened.st_dev, opened.st_ino), bytes(contents)


def copy_runtime_log_snapshot(source: Path, copied: Path,
                              previous: tuple[tuple[int, int], bytes] | None,
                              *, live: bool = False) -> tuple[tuple[int, int], bytes]:
    """Copy a monotonic, non-symlinked runtime-log snapshot.

    A later snapshot must retain the earlier byte prefix on the same inode.  A
    log rotation, truncation, or rewrite therefore fails closed rather than
    allowing a previously observed readiness sequence to stand on its own.
    """

    reader = read_live_appendable_regular_nofollow if live else read_stable_regular_nofollow
    identity, data = reader(source, "runtime log")
    if previous is not None:
        previous_identity, previous_data = previous
        if identity != previous_identity:
            fail("runtime log rotated during proof")
        if len(data) < len(previous_data) or not data.startswith(previous_data):
            fail("runtime log was truncated or rewritten during proof")
    try:
        copied_details = copied.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(copied_details.st_mode) or not stat.S_ISREG(copied_details.st_mode):
            fail("copied runtime log destination is not a regular non-symlink file")
    copied.parent.mkdir(parents=True, exist_ok=True)
    temporary = copied.with_name(f".{copied.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as destination:
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.replace(copied)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        fail(f"cannot copy runtime log snapshot: {error}")
    return identity, data


def fail_after_navigation_log_refresh(source_log: Path, copied_log: Path,
                                      previous_snapshot: tuple[tuple[int, int], bytes] | None,
                                      message: str) -> None:
    """Preserve the navigation failure's engine evidence before cleanup.

    The refresh deliberately uses the same monotonic snapshot contract as the
    success path.  A rotated, truncated, rewritten, missing, or unsafe source
    log therefore remains a failure instead of making the navigation timeout
    appear less severe.
    """

    try:
        copy_runtime_log_snapshot(source_log, copied_log, previous_snapshot, live=True)
    except GuardError as error:
        fail(f"{message}; runtime log refresh also failed: {error}")
    fail(message)


def write_runtime_snapshot_manifest(path: Path, snapshot: tuple[tuple[int, int], bytes],
                                    autoload_save: str | None, level: str | None,
                                    pid: int) -> None:
    identity, data = snapshot
    if not data:
        fail("runtime log snapshot is empty")
    if autoload_save is not None:
        validate_autoload_name(autoload_save)
        if level is None:
            fail("sync-complete snapshot did not identify a level")
    contents = (
        "version=1\n"
        f"device={identity[0]}\n"
        f"inode={identity[1]}\n"
        f"bytes={len(data)}\n"
        f"sha256={hashlib.sha256(data).hexdigest()}\n"
        f"runtime_mode={'autoload' if autoload_save is not None else 'menu'}\n"
        f"autoload_save={autoload_save if autoload_save is not None else '-'}\n"
        f"level={level if level is not None else '-'}\n"
        f"pid={pid}\n"
    )
    write_new_regular(path, contents.encode("ascii"))


def measured_ui_capture_renderer(snapshot: bytes, claimed_renderer: str) -> str:
    """Derive the UI-capture renderer from the saved sync-complete snapshot."""

    lines = snapshot.decode("utf-8", errors="replace").splitlines()
    renderer_lines = [line for line in lines if line.startswith(GPU_RENDERER_PREFIX)]
    if not renderer_lines:
        fail("saved-game runtime snapshot has no GPU renderer line")
    if any(line != APPLE_SOFTWARE_RENDERER_LINE for line in renderer_lines):
        fail("saved-game runtime snapshot has conflicting GPU renderer lines")
    measured = APPLE_SOFTWARE_RENDERER
    if claimed_renderer != measured:
        fail("native UI capture claimed renderer does not match saved-game runtime snapshot")
    return measured


def write_capture_manifest_state(manifest: Path, output: Path) -> None:
    """Bind a finalized native UI manifest to its exact file identity and bytes."""

    manifest = absolute_unresolved(manifest)
    identity, data = read_stable_regular_nofollow(manifest, "native UI capture manifest")
    digest = hashlib.sha256(data).hexdigest()
    contents = (
        "version=1\n"
        f"path={manifest}\n"
        f"device={identity[0]}\n"
        f"inode={identity[1]}\n"
        f"bytes={len(data)}\n"
        f"sha256={digest}\n"
    )
    write_new_regular(output, contents.encode("utf-8"))


def _parse_capture_manifest_state_bytes(
        data: bytes,
) -> tuple[Path, tuple[int, int], int, str]:
    expected_keys = ("version", "path", "device", "inode", "bytes", "sha256")
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        fail(f"native UI capture manifest state is not UTF-8: {error}")
    if len(lines) != len(expected_keys):
        fail("native UI capture manifest state has an invalid row count")
    values: dict[str, str] = {}
    for expected_key, line in zip(expected_keys, lines):
        key, separator, value = line.partition("=")
        if separator != "=" or key != expected_key or not value or "\x00" in value:
            fail("native UI capture manifest state has an invalid row")
        values[key] = value
    if values["version"] != "1" or not os.path.isabs(values["path"]):
        fail("native UI capture manifest state has an invalid version or path")
    try:
        device, inode, size = (int(values[key]) for key in ("device", "inode", "bytes"))
    except ValueError:
        fail("native UI capture manifest state has an invalid numeric value")
    digest = values["sha256"]
    if (device < 0 or inode <= 0 or size < 0 or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        fail("native UI capture manifest state has an invalid identity or digest")
    return Path(values["path"]), (device, inode), size, digest


def parse_capture_manifest_state(path: Path) -> tuple[Path, tuple[int, int], int, str]:
    _, data = read_stable_regular_nofollow(path, "native UI capture manifest state")
    return _parse_capture_manifest_state_bytes(data)


def _pin_capture_manifest_dependencies(path: Path) -> dict[str, object]:
    state = _read_pinned_regular(
        _absolute_unresolved_clone_path(path, "native UI capture manifest state"),
        "native UI capture manifest state", private=True)
    manifest_state: dict[str, object] | None = None
    try:
        binding = _parse_capture_manifest_state_bytes(state["data"])
        manifest_state = _read_pinned_regular(
            binding[0], "native UI capture manifest", private=False)
        if (manifest_state["identity"] != binding[1]
                or manifest_state["size"] != binding[2]
                or manifest_state["sha256"] != binding[3]):
            fail("native UI capture manifest no longer matches its finalized state")
        return {"state": state, "manifest": manifest_state, "binding": binding}
    except BaseException:
        if manifest_state is not None:
            _close_pinned(manifest_state)
        _close_pinned(state)
        raise


def _revalidate_capture_manifest_dependencies(binding: dict[str, object]) -> None:
    _revalidate_pinned(binding["state"], "native UI capture manifest state")
    _revalidate_pinned(binding["manifest"], "native UI capture manifest")
    expected = binding["binding"]
    manifest_state = binding["manifest"]
    if (_parse_capture_manifest_state_bytes(binding["state"]["data"]) != expected
            or manifest_state["identity"] != expected[1]
            or manifest_state["size"] != expected[2]
            or manifest_state["sha256"] != expected[3]):
        fail("native UI capture dependency binding changed")


def _close_capture_manifest_dependencies(binding: dict[str, object]) -> None:
    try:
        _close_pinned(binding["manifest"])
    finally:
        _close_pinned(binding["state"])


def validate_capture_manifest_binding(
        binding: tuple[Path, tuple[int, int], int, str],
) -> tuple[Path, tuple[int, int], int, str]:
    manifest, expected_identity, expected_size, expected_digest = binding
    identity, data = read_stable_regular_nofollow(manifest, "native UI capture manifest")
    if (identity != expected_identity or len(data) != expected_size
            or hashlib.sha256(data).hexdigest() != expected_digest):
        fail("native UI capture manifest no longer matches its finalized state")
    return manifest, identity, expected_size, expected_digest


def validate_capture_manifest_state(path: Path) -> tuple[Path, tuple[int, int], int, str]:
    return validate_capture_manifest_binding(parse_capture_manifest_state(path))


def require_capture_manifest_report_binding(
        lines: list[str], binding: tuple[Path, tuple[int, int], int, str],
) -> None:
    manifest, (_, inode), size, digest = binding
    expected = {
        "ui_capture_manifest": str(manifest),
        "ui_capture_manifest_inode": str(inode),
        "ui_capture_manifest_bytes": str(size),
        "ui_capture_manifest_sha256": digest,
    }
    actual: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator == "=" and key in expected:
            if key in actual:
                fail("prepared Simulator report has duplicate native UI capture manifest fields")
            actual[key] = value
    if actual != expected:
        fail("prepared Simulator report does not match native UI capture manifest binding")


def write_capture_manifest_report_fields(state: Path, output: Path) -> None:
    _, (_, inode), size, digest = validate_capture_manifest_state(state)
    contents = (
        f"ui_capture_manifest_inode={inode}\n"
        f"ui_capture_manifest_bytes={size}\n"
        f"ui_capture_manifest_sha256={digest}\n"
    )
    write_new_regular(output, contents.encode("ascii"))


def parse_runtime_snapshot_manifest(path: Path, copied_log: Path,
                                    autoload_save: str | None) -> tuple[tuple[int, int], bytes, str | None, int]:
    expected_keys = (
        "version", "device", "inode", "bytes", "sha256",
        "runtime_mode", "autoload_save", "level", "pid",
    )
    _, manifest_data = read_stable_regular_nofollow(path, "runtime snapshot manifest")
    try:
        lines = manifest_data.decode("ascii", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        fail(f"runtime snapshot manifest is not ASCII: {error}")
    if len(lines) != len(expected_keys):
        fail("runtime snapshot manifest has an invalid row count")
    values: dict[str, str] = {}
    for expected_key, line in zip(expected_keys, lines):
        key, separator, value = line.partition("=")
        if separator != "=" or key != expected_key or not value:
            fail("runtime snapshot manifest has an invalid row")
        values[key] = value
    if values["version"] != "1":
        fail("runtime snapshot manifest has an unsupported version")
    try:
        device = int(values["device"])
        inode = int(values["inode"])
        expected_size = int(values["bytes"])
    except ValueError:
        fail("runtime snapshot manifest has an invalid numeric value")
    expected_hash = values["sha256"]
    if (device < 0 or inode <= 0 or expected_size <= 0 or len(expected_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_hash)):
        fail("runtime snapshot manifest has an invalid identity or digest")
    expected_mode = "autoload" if autoload_save is not None else "menu"
    expected_save = autoload_save if autoload_save is not None else "-"
    if values["runtime_mode"] != expected_mode or values["autoload_save"] != expected_save:
        fail("runtime snapshot manifest does not match requested runtime mode")
    level = None if values["level"] == "-" else values["level"]
    if (autoload_save is None and level is not None) or (autoload_save is not None and level is None):
        fail("runtime snapshot manifest has an invalid level")
    _, data = read_stable_regular_nofollow(copied_log, "saved runtime log snapshot")
    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_hash:
        fail("saved runtime log snapshot does not match its manifest")
    try:
        pid = int(values["pid"])
    except ValueError:
        fail("runtime snapshot manifest has an invalid PID")
    if pid <= 0:
        fail("runtime snapshot manifest has an invalid PID")
    return (device, inode), data, level, pid


def finalize_runtime_log(source_log: Path, copied_log: Path, snapshot_manifest: Path,
                         proof_metadata: Path, autoload_save: str | None, *,
                         allow_canonical_quickload_marker: bool = False) -> None:
    identity, previous_data, expected_level, expected_pid = parse_runtime_snapshot_manifest(
        snapshot_manifest, copied_log, autoload_save,
    )
    final_snapshot = copy_runtime_log_snapshot(
        source_log, copied_log, (identity, previous_data),
    )
    final_ready, final_level = runtime_log_state(
        final_snapshot[1].decode("utf-8", errors="replace"), autoload_save,
        expected_pid, allow_canonical_quickload_marker=allow_canonical_quickload_marker,
    )
    if not final_ready or final_level != expected_level:
        fail("post-stop runtime log no longer proves the required runtime boundary")
    runtime_metadata(
        proof_metadata, autoload_save=autoload_save, level=final_level, pid=expected_pid,
    )


def menu_frame_state(lines: list[str], expected_pid: int) -> bool:
    """Accept only exact rendered-menu markers for the live launched PID."""

    matches: list[re.Match[str]] = []
    for line in lines:
        if line.startswith(MENU_FRAME_PREFIX):
            marker = MENU_FRAME_MARKER.fullmatch(line)
            if marker is None:
                fail("main-menu frame marker has an invalid grammar")
            if int(marker.group(1)) != expected_pid:
                fail("main-menu frame marker PID does not match the launched app")
            matches.append(marker)
    if len(matches) > 1:
        fail("runtime log must contain exactly one main-menu frame marker")
    return len(matches) == 1


def require_single_engine_start(lines: list[str]) -> None:
    if sum(line.strip().lower() == "starting engine..." for line in lines) > 1:
        fail("runtime log must contain exactly one engine start")


WAITING_FOR_DEACTIVATE = "WAITING_FOR_DEACTIVATE"
BACKGROUND_CONFIRMED = "BACKGROUND_CONFIRMED"
RECOVERY_COMPLETE = "RECOVERY_COMPLETE"


def lifecycle_events(data: bytes, expected_pid: int, phase: str) -> list[tuple[int, str]]:
    """Parse exact lifecycle markers for one already-launched app PID."""

    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        fail(f"{phase} runtime log is not UTF-8: {error}")
    events: list[tuple[int, str]] = []
    for line in lines:
        if line.startswith(LIFECYCLE_PREFIX):
            marker = LIFECYCLE_MARKER.fullmatch(line)
            if marker is None:
                fail("lifecycle marker has an invalid grammar")
            if int(marker.group(1)) != expected_pid:
                fail("lifecycle marker PID does not match the launched app")
            events.append((int(marker.group(2)), marker.group(3)))
    return events


def lifecycle_pre_cycle_anchor(pre_cycle: bytes, expected_pid: int) -> int:
    """Require a valid active lifecycle history before the foreground cycle."""

    events = lifecycle_events(pre_cycle, expected_pid, "pre-cycle")
    if not events:
        fail("pre-cycle runtime log does not contain an active lifecycle marker")
    previous_seq = 0
    for sequence, _ in events:
        if sequence <= previous_seq:
            fail("pre-cycle lifecycle marker sequence is not strictly increasing")
        previous_seq = sequence
    if events[-1][1] != "activate":
        fail("pre-cycle lifecycle history must end with activate")
    return events[-1][0]


def lifecycle_recovery_state(appended: bytes, expected_pid: int, anchor_seq: int) -> str:
    """Classify the only permitted lifecycle prefix after an active snapshot.

    The result is intentionally a three-state protocol rather than a Boolean:
    seeing just ``deactivate`` is the valid state in which the harness may ask
    Simulator to foreground OpenXRay.  Every other incomplete or additional
    prefix is a fail-closed oracle violation.
    """

    events = lifecycle_events(appended, expected_pid, "post-foreground")
    if not events:
        return WAITING_FOR_DEACTIVATE
    if len(events) == 1 and events[0][1] == "deactivate" and events[0][0] > anchor_seq:
        return BACKGROUND_CONFIRMED
    if (len(events) == 2 and events[0][1] == "deactivate" and events[1][1] == "activate"
            and anchor_seq < events[0][0] < events[1][0]):
        return RECOVERY_COMPLETE
    fail("foreground lifecycle markers must be exactly one ordered deactivate then activate pair after the active anchor")


def runtime_log_state(text: str, autoload_save: str | None,
                      expected_pid: int | None = None, *,
                      allow_canonical_quickload_marker: bool = False) -> tuple[bool, str | None]:
    raw_lines = text.splitlines()
    normalized = re.sub(r"/+", "/", text.lower().replace("\\", "/"))
    lines = normalized.splitlines()
    verify_runtime_log_is_clean(lines)
    system_lines = [line for line in lines if "system.ltx" in line]
    load_indices = [
        index for index, line in enumerate(lines)
        if "-----loading" in line and "/gamedata/configs/system.ltx" in line
    ]
    start_indices = [index for index, line in enumerate(lines) if "starting engine..." in line]
    archive_cache = any(
        re.search(r"^fs:\s+\d+\s+files cached 12 archives(?:\b|,)", line)
        for line in lines
    )
    loaded_system = bool(load_indices)
    engine_started = any(start > load for load in load_indices for start in start_indices)
    missing_system = any(
        marker in line
        for line in system_lines
        for marker in ("cannot", "can't", "failed", "error", "not found")
    )
    if autoload_save is None:
        if expected_pid is None:
            fail("menu runtime proof requires the launched app PID")
        require_single_engine_start(lines)
        return (
            loaded_system and engine_started and archive_cache and not missing_system
            and menu_frame_state(raw_lines, expected_pid),
            None,
        )
    ready, level = autoload_sync_complete(
        lines, autoload_save,
        allow_canonical_quickload_marker=allow_canonical_quickload_marker,
    )
    return ready and loaded_system and archive_cache and not missing_system, level


def verify_png(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        fail("Simulator screenshot is missing, empty, or non-regular PNG")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_size <= len(PNG_SIGNATURE):
        fail("Simulator screenshot is missing, empty, or non-regular PNG")
    with path.open("rb") as image:
        if image.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
            fail("Simulator screenshot is missing, empty, or non-regular PNG")


def require_absent_path(path: Path, label: str) -> None:
    """Refuse an output that could make a stale screenshot look fresh."""

    try:
        path.lstat()
    except FileNotFoundError:
        return
    fail(f"{label} destination already exists")


def run_simctl(command: tuple[str, ...], stderr, failure: str) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        fail(failure)
    stderr.write(result.stderr)
    stderr.flush()
    if result.returncode != 0:
        fail(failure)
    return result


def run_capture_parser(parser_path: Path, arguments: tuple[str, ...], deadline: float,
                       stderr, label: str, *, allow_absent: bool = False) -> str | None:
    """Run the source-snapshot capture grammar once without extending deadline.

    Exit 75 is the parser's documented transient producer race.  Everything
    else is an invariant breach; each successful command must emit one token.
    """

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        result = subprocess.run(
            (sys.executable, str(parser_path), *arguments), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
            timeout=min(CAPTURE_PARSER_TIMEOUT_SECONDS, remaining),
        )
    except subprocess.TimeoutExpired:
        fail(f"capture-v2 {label} parser exceeded the original launch deadline")
    stderr.write(result.stderr)
    stderr.flush()
    if result.returncode == 75:
        return None
    if result.returncode != 0:
        fail(f"capture-v2 {label} parser rejected the live artifact")
    try:
        token = result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        fail(f"capture-v2 {label} parser emitted non-ASCII output: {error}")
    if token == "ABSENT" and allow_absent:
        return token
    if not CAPTURE_TOKEN.fullmatch(token):
        fail(f"capture-v2 {label} parser did not emit one canonical token")
    return token


def write_null_capture_boundary(path: Path) -> None:
    """Record that no capture sidecar existed at sync completion."""

    contents = b'{"schema":"openxray.simulator-capture-v2-boundary.v1","token":null}\n'
    write_new_regular(path, contents)


def capture_v2_after_sync(parser_path: Path, root: Path, documents: Path, pid: int,
                          level: str, deadline: float, stderr, poll: float) -> None:
    """Prove T0 -> T1 -> T2 after the single saved-game sync boundary.

    The guard owns temporal/runtime identity.  The source snapshot parser owns
    capture grammar, nofollow reads and atomic artifact publication.
    """

    if level != "zaton":
        fail("capture-v2 checkpoint requires sync_level exactly zaton")
    if parser_path.is_symlink() or not parser_path.is_file():
        fail("capture-v2 parser must be a regular non-symlink source-snapshot file")
    require_real_directory(root)
    metadata = documents / "xr_shot_meta.txt"
    ppm = documents / "xr_shot.ppm"
    boundary = root / "boundary-watermark.json"
    baseline = root / "baseline.json"
    capture_json = root / "capture.json"
    capture_ppm = root / "capture.ppm"
    proof = root / "capture-proof.json"
    if any(os.path.lexists(path) for path in (boundary, baseline, capture_json, capture_ppm, proof)):
        fail("capture-v2 work-root destinations must be new")

    boundary_token: str | None = None
    while time.monotonic() < deadline:
        require_live_app_pid(pid, "before capture-v2 boundary watermark")
        boundary_result = run_capture_parser(
            parser_path,
            ("observe-live-metadata", "--metadata", str(metadata), "--output", str(boundary),
             "--expected-pid", str(pid), "--allow-absent"),
            deadline, stderr, "T0", allow_absent=True,
        )
        if boundary_result is None:
            time.sleep(min(poll, max(0.0, deadline - time.monotonic())))
            continue
        if boundary_result == "ABSENT":
            write_null_capture_boundary(boundary)
        else:
            boundary_token = boundary_result
        break
    else:
        fail("Simulator launch timed out before capture-v2 T0 boundary watermark")
    require_live_app_pid(pid, "after capture-v2 boundary watermark")

    baseline_token: str | None = None
    while time.monotonic() < deadline:
        require_live_app_pid(pid, "during capture-v2 sidecar polling")
        if baseline_token is None:
            arguments = ["observe-live-metadata", "--metadata", str(metadata), "--output", str(baseline),
                         "--expected-pid", str(pid)]
            if boundary_token is not None:
                arguments.extend(("--after-token", boundary_token, "--expected-session", boundary_token.split(":", 1)[0]))
            baseline_token = run_capture_parser(parser_path, tuple(arguments), deadline, stderr, "T1")
            if baseline_token is None:
                time.sleep(min(poll, max(0.0, deadline - time.monotonic())))
                continue
            require_live_app_pid(pid, "after capture-v2 baseline")
            continue
        token = run_capture_parser(
            parser_path,
            ("snapshot-live-capture", "--metadata", str(metadata), "--ppm", str(ppm),
             "--baseline", str(baseline), "--metadata-output", str(capture_json),
             "--ppm-output", str(capture_ppm), "--proof-output", str(proof),
             "--expected-pid", str(pid), "--expected-level", level,
             "--after-token", baseline_token, "--expected-session", baseline_token.split(":", 1)[0],
             "--boundary-token", boundary_token if boundary_token is not None else "null"),
            deadline, stderr, "T2",
        )
        if token is not None:
            require_live_app_pid(pid, "after capture-v2 snapshot")
            return
        time.sleep(min(poll, max(0.0, deadline - time.monotonic())))
    fail("Simulator launch timed out before capture-v2 T2 snapshot")


def prove_foreground_cycle(timeout: float, poll: float, stderr, stdout_path: Path,
                           source_log: Path, copied_log: Path,
                           pre_cycle_snapshot: tuple[tuple[int, int], bytes],
                           recovery_screenshot: Path, udid: str, bundle: str,
                           expected_pid: int) -> tuple[tuple[int, int], bytes]:
    """Prove background then recovery in two fail-closed lifecycle phases."""

    require_absent_path(recovery_screenshot, "foreground-recovery screenshot")
    anchor_seq = lifecycle_pre_cycle_anchor(pre_cycle_snapshot[1], expected_pid)

    run_simctl(
        ("xcrun", "simctl", "launch", udid, "com.apple.mobilesafari"), stderr,
        "could not launch MobileSafari for the controlled foreground cycle",
    )
    snapshot = pre_cycle_snapshot
    background_deadline = time.monotonic() + timeout
    while time.monotonic() < background_deadline:
        if source_log.exists() or source_log.is_symlink():
            snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot, live=True)
            text = snapshot[1].decode("utf-8", errors="replace")
            ready, _ = runtime_log_state(text, None, expected_pid)
            appended = snapshot[1][len(pre_cycle_snapshot[1]):]
            state = lifecycle_recovery_state(appended, expected_pid, anchor_seq)
            if not ready:
                fail("runtime log no longer proves the rendered main-menu boundary during background confirmation")
            if state == BACKGROUND_CONFIRMED:
                break
            if state == RECOVERY_COMPLETE:
                fail("OpenXRay emitted activate before the controlled foreground recovery launch")
        require_live_app_pid(expected_pid, "during controlled foreground background confirmation")
        time.sleep(poll)
    else:
        fail("MobileSafari did not confirm OpenXRay background transition before timeout")

    recovery = run_simctl(
        ("xcrun", "simctl", "launch", f"--stdout={stdout_path}", udid, bundle), stderr,
        "could not relaunch OpenXRay after the controlled foreground cycle",
    )
    recovery_pid = parse_simctl_launch_pid(recovery.stdout, bundle)
    if recovery_pid != expected_pid:
        fail("OpenXRay PID changed during the controlled foreground cycle")
    require_live_app_pid(expected_pid, "immediately after controlled foreground relaunch")

    recovery_deadline = time.monotonic() + timeout
    while time.monotonic() < recovery_deadline:
        if source_log.exists() or source_log.is_symlink():
            snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot, live=True)
            text = snapshot[1].decode("utf-8", errors="replace")
            ready, _ = runtime_log_state(text, None, expected_pid)
            appended = snapshot[1][len(pre_cycle_snapshot[1]):]
            state = lifecycle_recovery_state(appended, expected_pid, anchor_seq)
            if not ready:
                fail("runtime log no longer proves the rendered main-menu boundary during foreground recovery")
            if state == RECOVERY_COMPLETE:
                break
        require_live_app_pid(expected_pid, "during controlled foreground recovery")
        time.sleep(poll)
    else:
        fail("OpenXRay did not confirm foreground recovery after verified background before timeout")

    stability_deadline = time.monotonic() + min(1.0, max(0.1, poll))
    while time.monotonic() < stability_deadline:
        require_live_app_pid(expected_pid, "during foreground-recovery stability interval")
        if not source_log.exists() and not source_log.is_symlink():
            fail("runtime log disappeared during foreground-recovery stability interval")
        snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot, live=True)
        text = snapshot[1].decode("utf-8", errors="replace")
        ready, _ = runtime_log_state(text, None, expected_pid)
        appended = snapshot[1][len(pre_cycle_snapshot[1]):]
        if not ready or lifecycle_recovery_state(appended, expected_pid, anchor_seq) != RECOVERY_COMPLETE:
            fail("foreground recovery log changed during its stability interval")
        remaining = stability_deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll, remaining))

    require_live_app_pid(expected_pid, "before foreground-recovery screenshot")
    shot = run_simctl(
        ("xcrun", "simctl", "io", udid, "screenshot", str(recovery_screenshot)), stderr,
        "Simulator foreground-recovery screenshot failed",
    )
    del shot
    verify_png(recovery_screenshot)
    require_live_app_pid(expected_pid, "after foreground-recovery screenshot")
    if not source_log.exists() and not source_log.is_symlink():
        fail("runtime log disappeared before final foreground-recovery proof")
    snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot, live=True)
    text = snapshot[1].decode("utf-8", errors="replace")
    ready, _ = runtime_log_state(text, None, expected_pid)
    appended = snapshot[1][len(pre_cycle_snapshot[1]):]
    if not ready or lifecycle_recovery_state(appended, expected_pid, anchor_seq) != RECOVERY_COMPLETE:
        fail("final foreground-recovery log no longer proves the required lifecycle boundary")
    require_live_app_pid(expected_pid, "after final foreground-recovery proof")
    return snapshot


def prove_launch(timeout: float, poll: float, stdout_path: Path, stderr_path: Path,
                 copied_log: Path, screenshot: Path, source_log: Path,
                 udid: str, bundle: str, autoload_save: str | None,
                 snapshot_manifest: Path, navigation_script: Path | None,
                 navigation_documents: Path | None, navigation_snapshot: Path | None,
                 navigation_pre_report: Path | None, initial_pid_path: Path | None = None,
                 recovery_screenshot: Path | None = None,
                 capture_v2_parser: Path | None = None,
                 capture_v2_root: Path | None = None,
                 ui_capture_root: Path | None = None,
                 ui_capture_run_uuid: str | None = None,
                 ui_capture_runtime: str | None = None,
                 ui_capture_renderer: str | None = None,
                 ui_capture_git_revision: str | None = None,
                 ui_capture_source_tree_sha256: str | None = None,
                 pid_output: Path | None = None) -> None:
    navigation_values = (navigation_script, navigation_documents, navigation_snapshot, navigation_pre_report)
    navigation_requested = any(value is not None for value in navigation_values)
    if navigation_requested and any(value is None for value in navigation_values):
        fail("UI navigation launch-proof arguments must be supplied as one complete set")
    if navigation_requested:
        if autoload_save is None:
            fail("UI navigation requires an autoload save")
        assert navigation_script is not None
        script_details = navigation_script.lstat()
        if stat.S_ISLNK(script_details.st_mode) or not stat.S_ISREG(script_details.st_mode):
            fail("UI navigation controller must be a regular non-symlink script")
    capture_values = (capture_v2_parser, capture_v2_root)
    capture_requested = any(value is not None for value in capture_values)
    if capture_requested and any(value is None for value in capture_values):
        fail("capture-v2 launch-proof arguments must be supplied as one complete set")
    if capture_requested and (autoload_save is None or navigation_requested):
        fail("capture-v2 requires autoload mode and conflicts with UI navigation")
    ui_capture_values = (ui_capture_root, ui_capture_run_uuid, ui_capture_runtime, ui_capture_renderer,
                         ui_capture_git_revision, ui_capture_source_tree_sha256)
    ui_capture_requested = any(value is not None for value in ui_capture_values)
    if ui_capture_requested and (not navigation_requested or any(value is None for value in ui_capture_values)):
        fail("native UI capture launch-proof arguments require complete UI navigation arguments")
    if ui_capture_requested and (ui_capture_runtime != "27.0" or ui_capture_renderer != "Apple-Software-Renderer"
            or re.fullmatch(r"[0-9a-f]{40}", ui_capture_git_revision or "") is None
            or re.fullmatch(r"[0-9a-f]{64}", ui_capture_source_tree_sha256 or "") is None):
        fail("native UI capture provenance is invalid")
    foreground_values = (initial_pid_path, recovery_screenshot)
    foreground_requested = any(value is not None for value in foreground_values)
    if foreground_requested and any(value is None for value in foreground_values):
        fail("foreground-cycle launch-proof arguments must be supplied as one complete set")
    if foreground_requested and autoload_save is not None:
        fail("foreground-cycle proof is only defined for rendered main-menu mode")
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.touch(exist_ok=True)
    stderr_path.touch(exist_ok=True)
    launch_command = (
        "xcrun", "simctl", "launch", f"--stdout={stdout_path}",
        f"--stderr={stderr_path}", udid, bundle,
    )
    try:
        launch = subprocess.run(
            launch_command, check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        fail("simctl launch did not return promptly")
    append_launch_stderr(stderr_path, launch.stderr)
    if launch.returncode != 0:
        fail(f"simctl launch failed with exit code {launch.returncode}")
    pid = parse_simctl_launch_pid(launch.stdout, bundle)
    require_live_app_pid(pid, "immediately after launch")
    if pid_output is not None:
        write_new_regular(pid_output, f"{pid}\n".encode("ascii"))
    if initial_pid_path is not None:
        write_new_regular(initial_pid_path, f"{pid}\n".encode("ascii"))

    deadline = time.monotonic() + timeout
    stability_window = min(1.0, max(0.1, poll))
    sync_complete_since: float | None = None
    sync_level: str | None = None
    measured_renderer: str | None = None
    capture_completed = False
    log_snapshot: tuple[tuple[int, int], bytes] | None = None
    with stderr_path.open("ab") as stderr:
        while time.monotonic() < deadline:
            if source_log.exists() or source_log.is_symlink():
                log_snapshot = copy_runtime_log_snapshot(source_log, copied_log, log_snapshot, live=True)
                text = log_snapshot[1].decode("utf-8", errors="replace")
                runtime_ready, current_level = runtime_log_state(text, autoload_save, pid)
                if autoload_save is not None:
                    sync_level = current_level
                runtime_label = "autoload" if autoload_save is not None else "menu"
                stderr.write(
                    f"retail-simulator poll: mode={runtime_label} "
                    f"runtime_ready={runtime_ready}\n".encode()
                )
                stderr.flush()
                if runtime_ready:
                    if autoload_save is not None and sync_complete_since is None:
                        sync_complete_since = time.monotonic()
                        if ui_capture_requested:
                            assert ui_capture_renderer is not None
                            measured_renderer = measured_ui_capture_renderer(
                                log_snapshot[1], ui_capture_renderer,
                            )
                    if capture_requested and not capture_completed:
                        assert capture_v2_parser is not None
                        assert capture_v2_root is not None
                        if sync_level is None:
                            fail("capture-v2 sync boundary did not identify a level")
                        if sync_level != "zaton":
                            fail("capture-v2 checkpoint requires sync_level exactly zaton")
                        capture_v2_after_sync(
                            capture_v2_parser, capture_v2_root, source_log.parent, pid,
                            sync_level, deadline, stderr, poll,
                        )
                        capture_completed = True
                    if sync_complete_since is not None:
                        dwell_remaining = 0.250 - (time.monotonic() - sync_complete_since)
                        if dwell_remaining > 0:
                            require_live_app_pid(pid, "during sync-complete dwell before screenshot")
                            time.sleep(min(poll, dwell_remaining))
                            continue
                    if navigation_requested:
                        assert navigation_script is not None
                        assert navigation_documents is not None
                        assert navigation_snapshot is not None
                        assert navigation_pre_report is not None
                        navigation_command = (
                            sys.executable, str(navigation_script), "run", "--integration",
                            "--simulator-udid", udid, "--expected-pid", str(pid),
                            "--documents", str(navigation_documents), "--log", str(source_log),
                            "--snapshot", str(navigation_snapshot), "--report", str(navigation_pre_report),
                            "--timeout-seconds", str(NAVIGATION_STEP_TIMEOUT_SECONDS),
                        )
                        if ui_capture_requested:
                            assert ui_capture_root is not None and ui_capture_run_uuid is not None
                            assert ui_capture_runtime is not None and measured_renderer is not None
                            assert ui_capture_git_revision is not None and ui_capture_source_tree_sha256 is not None
                            if sync_level is None:
                                fail("native UI captures require a synchronized saved-game level")
                            navigation_command += (
                                "--ui-capture-root", str(ui_capture_root),
                                "--ui-capture-run-uuid", ui_capture_run_uuid,
                                "--ui-capture-expected-level", sync_level,
                                "--ui-capture-runtime", ui_capture_runtime,
                                "--ui-capture-renderer", measured_renderer,
                                "--ui-capture-git-revision", ui_capture_git_revision,
                                "--ui-capture-source-tree-sha256", ui_capture_source_tree_sha256,
                            )
                        try:
                            navigation = subprocess.run(
                                navigation_command, check=False, stdout=stderr, stderr=stderr,
                                timeout=NAVIGATION_SUBPROCESS_TIMEOUT_SECONDS,
                            )
                        except subprocess.TimeoutExpired:
                            fail_after_navigation_log_refresh(
                                source_log, copied_log, log_snapshot,
                                "semantic Simulator UI navigation timed out",
                            )
                        if navigation.returncode != 0:
                            fail_after_navigation_log_refresh(
                                source_log, copied_log, log_snapshot,
                                "semantic Simulator UI navigation failed",
                            )
                        require_live_app_pid(pid, "after semantic UI navigation")
                    require_live_app_pid(pid, "before screenshot")
                    screenshot_remaining = deadline - time.monotonic()
                    if screenshot_remaining <= 0:
                        fail("Simulator launch deadline expired before screenshot")
                    try:
                        shot = subprocess.run(
                            ("xcrun", "simctl", "io", udid, "screenshot", str(screenshot)),
                            stdout=stderr,
                            stderr=stderr,
                            check=False,
                            timeout=screenshot_remaining,
                        )
                    except subprocess.TimeoutExpired:
                        fail("Simulator screenshot exceeded the original launch deadline")
                    if shot.returncode != 0:
                        fail("Simulator screenshot is missing, empty, or non-regular PNG")
                    verify_png(screenshot)
                    require_live_app_pid(pid, "after screenshot")
                    stability_deadline = time.monotonic() + stability_window
                    while time.monotonic() < stability_deadline:
                        require_live_app_pid(pid, "during post-screenshot stability interval")
                        remaining = stability_deadline - time.monotonic()
                        if remaining > 0:
                            time.sleep(min(poll, remaining))
                    require_live_app_pid(pid, "before final runtime-log proof")
                    if not source_log.exists() and not source_log.is_symlink():
                        fail("runtime log disappeared before final proof")
                    log_snapshot = copy_runtime_log_snapshot(source_log, copied_log, log_snapshot, live=True)
                    final_ready, final_level = runtime_log_state(
                        log_snapshot[1].decode("utf-8", errors="replace"), autoload_save, pid,
                    )
                    if not final_ready or (autoload_save is not None and final_level != sync_level):
                        fail("final runtime log no longer proves the required runtime boundary")
                    require_live_app_pid(pid, "after final runtime-log proof")
                    if foreground_requested:
                        assert recovery_screenshot is not None
                        log_snapshot = prove_foreground_cycle(
                            timeout, poll, stderr, stdout_path, source_log, copied_log,
                            log_snapshot, recovery_screenshot, udid, bundle, pid,
                        )
                        # Re-check the exact PID after recovery; the finalizer anchors its
                        # post-stop integrity check to this recovered snapshot.
                        require_live_app_pid(pid, "after controlled foreground recovery")
                    write_runtime_snapshot_manifest(
                        snapshot_manifest, log_snapshot, autoload_save, final_level, pid,
                    )
                    return
            require_live_app_pid(pid, "before runtime proof")
            time.sleep(poll)
    fail("Simulator launch timed out before runtime proof")


def parse_files_tsv(path: Path) -> dict[str, int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "bytes\tpath":
        fail(f"invalid files.tsv header: {path}")
    entries: dict[str, int] = {}
    for number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 2:
            fail(f"invalid files.tsv row {number}")
        size_text, relative = fields
        safe_relative(relative)
        try:
            size = int(size_text)
        except ValueError:
            fail(f"invalid files.tsv size at row {number}")
        if size < 0 or relative in entries:
            fail(f"duplicate or invalid files.tsv path at row {number}")
        entries[relative] = size
    return entries


def parse_required_archives(path: Path) -> dict[str, tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "bytes\tsha256\tpath\tstatus":
        fail(f"invalid required-archives.tsv header: {path}")
    entries: dict[str, tuple[int, str]] = {}
    for number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 4:
            fail(f"invalid required archive row {number}")
        size_text, digest, relative, status = fields
        safe_relative(relative)
        try:
            size = int(size_text)
        except ValueError:
            fail(f"invalid required archive size at row {number}")
        if (size <= 0 or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or not status or relative in entries):
            fail(f"invalid required archive row {number}")
        entries[relative] = (size, digest)
    if set(entries) != REQUIRED_ARCHIVES:
        missing = sorted(REQUIRED_ARCHIVES - set(entries))
        extra = sorted(set(entries) - REQUIRED_ARCHIVES)
        fail(f"required archive set mismatch: missing={missing}, extra={extra}")
    return entries


def parse_large_files(path: Path) -> dict[str, tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "sha256\tbytes\tpath":
        fail(f"invalid large-files-sha256.tsv header: {path}")
    entries: dict[str, tuple[int, str]] = {}
    for number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 3:
            fail(f"invalid large-file row {number}")
        digest, size_text, relative = fields
        safe_relative(relative)
        try:
            size = int(size_text)
        except ValueError:
            fail(f"invalid large-file size at row {number}")
        if (size <= 0 or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or relative in entries):
            fail(f"invalid large-file row {number}")
        entries[relative] = (size, digest)
    if not entries:
        fail(f"large-files-sha256.tsv is empty: {path}")
    return entries


def require_hash_manifest_matches_files(entries: dict[str, tuple[int, str]], files: dict[str, int],
                                        label: str) -> None:
    for relative, (size, _) in entries.items():
        if files.get(relative) != size:
            fail(f"{label} entry does not match files.tsv: {relative}")


def validate_retail(backup: Path, manifest: Path) -> tuple[
        dict[str, int], dict[str, tuple[int, str]], dict[str, tuple[int, str]]]:
    if not backup.is_dir() or not manifest.is_dir():
        fail("backup and manifest must be directories")
    files = parse_files_tsv(manifest / "files.tsv")
    required = parse_required_archives(manifest / "required-archives.tsv")
    large = parse_large_files(manifest / "large-files-sha256.tsv")
    require_hash_manifest_matches_files(required, files, "required archive")
    require_hash_manifest_matches_files(large, files, "large-file")
    actual_paths = {path.relative_to(backup).as_posix(): path for path in walk_files(backup)}
    if set(actual_paths) != set(files):
        fail("backup paths do not exactly match files.tsv")
    for relative, expected_size in files.items():
        if actual_paths[relative].stat().st_size != expected_size:
            fail(f"backup size mismatch: {relative}")
    for relative, (expected_size, expected_hash) in required.items():
        candidate = actual_paths.get(relative)
        if candidate is None or candidate.stat().st_size != expected_size or sha256(candidate) != expected_hash:
            fail(f"required retail archive mismatch: {relative}")
    for relative, (expected_size, expected_hash) in large.items():
        candidate = actual_paths.get(relative)
        if candidate is None or candidate.stat().st_size != expected_size or sha256(candidate) != expected_hash:
            fail(f"large retail file mismatch: {relative}")
    return files, required, large


def allowed_retail_path(relative: Path, with_saves: bool) -> bool:
    if not relative.parts:
        return False
    if relative.parts[0] in BASE_ALLOWLIST:
        return True
    return with_saves and relative == SAVES_ROOT or (
        with_saves and SAVES_ROOT in relative.parents
    )


def secure_copy_regular(source: Path, target: Path) -> tuple[int, str]:
    """Copy one regular file without following its final symlink.

    The source and destination parent chains are trusted after the preflight
    manifests. The caller rechecks the complete backup manifest after staging;
    hostile concurrent replacement of a parent directory is outside this local
    single-user workflow, while final-component symlink replacement is rejected.
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, os.O_RDONLY | nofollow)
    target_fd = -1
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            fail(f"staging source is not a regular file: {source}")
        target_fd = os.open(
            target,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
        )
        while block := os.read(source_fd, CHUNK):
            view = memoryview(block)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
        after = os.fstat(source_fd)
        destination = os.fstat(target_fd)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            fail(f"staging source changed while copying: {source}")
        if not stat.S_ISREG(destination.st_mode) or destination.st_size != before.st_size:
            fail(f"staging destination is not an exact regular file: {target}")
        source_hash = sha256_fd(source_fd)
        if sha256_fd(target_fd) != source_hash:
            fail(f"staging destination hash mismatch: {target}")
        return before.st_size, source_hash
    finally:
        try:
            if target_fd >= 0:
                os.close(target_fd)
        finally:
            os.close(source_fd)


def stage_retail(backup: Path, manifest: Path, destination: Path, with_saves: bool,
                 output: Path) -> None:
    files, required, large = validate_retail(backup, manifest)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        fail(f"staging destination must be absent or empty: {destination}")
    selected = [safe_relative(relative) for relative in files
                if allowed_retail_path(safe_relative(relative), with_saves)]
    if not selected:
        fail("retail allowlist selected no files")
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, int, str]] = []
    for relative in selected:
        source = backup / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        size, digest = secure_copy_regular(source, target)
        rows.append((relative.as_posix(), size, digest))
    for relative, (size, digest) in required.items():
        candidate = destination / safe_relative(relative)
        if not candidate.is_file() or candidate.stat().st_size != size or sha256(candidate) != digest:
            fail(f"staged required retail archive mismatch: {relative}")
    staged = {relative: (size, digest) for relative, size, digest in rows}
    for relative, (size, digest) in large.items():
        relative_path = safe_relative(relative)
        if allowed_retail_path(relative_path, with_saves=with_saves):
            if staged.get(relative) != (size, digest):
                fail(f"staged large retail file mismatch: {relative}")
            candidate = destination / relative_path
            if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size != size or sha256(candidate) != digest:
                fail(f"staged large retail file mismatch: {relative}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as report:
        report.write("sha256\tbytes\tpath\n")
        for relative, size, digest in sorted(rows):
            report.write(f"{digest}\t{size}\t{relative}\n")


def _import_retail_import() -> object:
    """Load the prepared-root verifier lazily; importing it at module load is circular."""
    path = Path(__file__).with_name("retail_import.py")
    spec = importlib.util.spec_from_file_location("retail_import_for_clone_stage", path)
    if spec is None or spec.loader is None:
        fail("could not load prepared retail importer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _import_quickload_evidence() -> object:
    """Load the v2 QuickLoad semantic validator without a module-level cycle."""
    path = Path(__file__).with_name("simulator_quickload_evidence.py")
    spec = importlib.util.spec_from_file_location(
        "simulator_quickload_evidence_for_clone_binding", path)
    if spec is None or spec.loader is None:
        fail("could not load QuickLoad evidence validator")
    module = importlib.util.module_from_spec(spec)
    module_dir = str(path.parent)
    added_path = module_dir not in sys.path
    if added_path:
        sys.path.insert(0, module_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if added_path:
            sys.path.remove(module_dir)
    if (not callable(getattr(module, "validate_pending_manifest_bytes", None))
            or not callable(getattr(module, "validate_report_fields_bytes", None))):
        fail("QuickLoad evidence validator lacks the v2 binding API")
    return module


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


def _json_equivalent(left: object, right: object) -> bool:
    return _canonical_json(left) == _canonical_json(right)


def _absolute_unresolved_clone_path(path: Path, label: str) -> Path:
    value = absolute_unresolved(path)
    if not value.is_absolute() or value.name in ("", ".", ".."):
        fail(f"{label} must be a named absolute path")
    return value


def _open_absolute_directory_nofollow(path: Path, label: str) -> int:
    path = _absolute_unresolved_clone_path(path, label)
    current = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                      | getattr(os, "O_NOFOLLOW", 0))
    try:
        for component in path.parts[1:]:
            child = -1
            try:
                child = os.open(component, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
                os.close(current)
                current = child
                child = -1
            finally:
                if child >= 0:
                    os.close(child)
        return current
    except BaseException as error:
        os.close(current)
        if isinstance(error, OSError):
            fail(f"cannot nofollow-open {label}: {error}")
        raise


def _clone_paths(prepared: Path, destination: Path, output: Path,
                 pending_ledger: Path) -> tuple[Path, Path, Path, Path]:
    values = tuple(_absolute_unresolved_clone_path(path, label) for path, label in (
        (prepared, "prepared root"), (destination, "clone destination"),
        (output, "staged manifest"), (pending_ledger, "pending clone ledger"),
    ))
    for index, path in enumerate(values):
        for other in values[index + 1:]:
            if path == other or is_within(path, other) or is_within(other, path):
                fail(f"clone paths must be pairwise disjoint: {path} vs {other}")
    if output.parent != pending_ledger.parent:
        fail("staged manifest and pending clone ledger require one trusted parent")
    return values


def _clone_fail(error: OSError, relative: str) -> None:
    marker = errno.errorcode.get(error.errno or 0, f"errno-{error.errno}")
    if error.errno == errno.EXDEV:
        fail(f"APFS clone crosses volumes (EXDEV): {relative}")
    if error.errno == errno.ENOTSUP:
        fail(f"APFS clone is unsupported (ENOTSUP): {relative}")
    fail(f"fclonefileat clone failed ({marker}) for {relative}: {error}")


def _fclonefileat(source_fd: int, destination_parent_fd: int, basename: str,
                  relative: str) -> None:
    if sys.platform != "darwin":
        fail("prepared clone staging requires Darwin APFS fclonefileat")
    try:
        function = ctypes.CDLL(None, use_errno=True).fclonefileat
    except AttributeError:
        fail("prepared clone staging requires fclonefileat; no copy fallback is permitted")
    function.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
    function.restype = ctypes.c_int
    if function(source_fd, destination_parent_fd, os.fsencode(basename),
                FCLONEFILEAT_FLAGS) != 0:
        number = ctypes.get_errno()
        _clone_fail(OSError(number, os.strerror(number)), relative)


def _raw_xattrs(fd: int, label: str) -> dict[str, str]:
    """Read all descriptor-bound Darwin xattrs, canonically encoded for JSON."""
    if sys.platform != "darwin":
        fail(f"cannot inspect Darwin xattrs for {label}")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        listxattr = libc.flistxattr
        getxattr = libc.fgetxattr
        listxattr.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
        listxattr.restype = ctypes.c_ssize_t
        getxattr.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                             ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
        getxattr.restype = ctypes.c_ssize_t
        length = listxattr(fd, None, 0, 0)
        if length < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        raw = ctypes.create_string_buffer(length)
        if length and listxattr(fd, raw, length, 0) != length:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        names = {item.decode("utf-8", "strict") for item in raw.raw.split(b"\0") if item}
        result: dict[str, str] = {}
        for name in sorted(names):
            encoded_name = os.fsencode(name)
            value_length = getxattr(fd, encoded_name, None, 0, 0, 0)
            if value_length < 0:
                raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
            value = ctypes.create_string_buffer(value_length)
            if value_length and getxattr(fd, encoded_name, value, value_length, 0, 0) != value_length:
                raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
            result[name] = base64.b64encode(value.raw).decode("ascii")
        return result
    except (AttributeError, OSError, UnicodeError) as error:
        fail(f"cannot inspect xattrs for {label}: {error}")


def _xattrs(fd: int, label: str, *, allowed: frozenset[str] = DEFAULT_XATTRS
            ) -> dict[str, str]:
    """Return only the explicitly allowed descriptor-bound xattrs."""
    result = _raw_xattrs(fd, label)
    if set(result) - allowed:
        fail(f"unapproved xattr(s) on {label}: {sorted(result)!r}")
    return result


def _require_no_acl(fd: int, label: str) -> None:
    """Reject Darwin extended ACL entries using the descriptor-bound API."""
    if sys.platform != "darwin":
        fail(f"cannot inspect Darwin ACL for {label}")
    acl = None
    free_acl = None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        get_acl = libc.acl_get_fd_np
        get_entry = libc.acl_get_entry
        free_acl = libc.acl_free
        get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
        get_acl.restype = ctypes.c_void_p
        get_entry.argtypes = (ctypes.c_void_p, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_void_p))
        get_entry.restype = ctypes.c_int
        free_acl.argtypes = (ctypes.c_void_p,)
        free_acl.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = get_acl(fd, 0x00000100)  # ACL_TYPE_EXTENDED
        if not acl:
            number = ctypes.get_errno()
            if number == errno.ENOENT:
                return
            raise OSError(number, os.strerror(number))
        entry = ctypes.c_void_p()
        result = get_entry(acl, 0, ctypes.byref(entry))  # ACL_FIRST_ENTRY
        if result == 0:
            fail(f"unapproved ACL on {label}")
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    except (AttributeError, OSError) as error:
        fail(f"cannot inspect ACL for {label}: {error}")
    finally:
        if acl and free_acl is not None and free_acl(acl) != 0:
            fail(f"cannot release ACL inspection state for {label}")


def _directory_metadata(fd: int, label: str, *, child: bool,
                        allowed_xattrs: frozenset[str] = DEFAULT_XATTRS
                        ) -> dict[str, object]:
    info = os.fstat(fd)
    mode = stat.S_IMODE(info.st_mode)
    flags = getattr(info, "st_flags", 0)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or flags != 0 or mode & 0o022 or (child and mode != 0o700)):
        expected = "0700" if child else "current-user-owned and non-group/world-writable"
        fail(f"{label} must be a real {expected} directory without file flags")
    _require_no_acl(fd, label)
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid,
            "mode": mode, "flags": flags,
            "xattrs": _xattrs(fd, label, allowed=allowed_xattrs)}


def validate_private_parent(path: Path, label: str) -> dict[str, object]:
    """Pin and rebind one publication/transaction parent with exact 0700 policy."""
    path = _absolute_unresolved_clone_path(path, label)
    descriptor = _open_absolute_directory_nofollow(path, label)
    try:
        metadata = _directory_metadata(descriptor, label, child=True)
        identity = (metadata["dev"], metadata["ino"])
        _rebind_transaction_parent(descriptor, path, identity, f"{label} pathname rebind")
        if _directory_metadata(descriptor, label, child=True) != metadata:
            fail(f"{label} metadata changed during validation")
        return metadata
    finally:
        os.close(descriptor)


def _file_metadata(fd: int, label: str, *, digest: bool) -> dict[str, object]:
    info = os.fstat(fd)
    flags = getattr(info, "st_flags", 0)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or flags != 0):
        actual = _stat_fields(info)
        expected = {
            "type": "regular", "uid": os.geteuid(), "mode": 0o600,
            "nlink": 1, "flags": "0x00000000",
        }
        fail(f"{label} must be private regular nlink=1 uid=current mode=0600 flags=0; "
             f"actual={_canonical_json(actual).decode('ascii').strip()} "
             f"expected={_canonical_json(expected).decode('ascii').strip()}")
    _require_no_acl(fd, label)
    result: dict[str, object] = {
        "dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode), "nlink": info.st_nlink,
        "size": info.st_size, "mtime_ns": info.st_mtime_ns, "flags": flags,
        "xattrs": _xattrs(fd, label),
    }
    if digest:
        result["sha256"] = sha256_fd(fd)
    return result


def _destination_file_metadata(fd: int, label: str, *, digest: bool
                               ) -> dict[str, object]:
    """Read a post-runtime destination leaf under the closed UF_TRACKED policy.

    This is intentionally separate from ``_file_metadata``: source, stage,
    private publication and transaction artifacts retain the exact flags=0
    contract.  Only a staged destination leaf after runtime may carry the
    Darwin UF_TRACKED bit.
    """
    info = os.fstat(fd)
    flags = getattr(info, "st_flags", 0)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or flags not in {0, UF_TRACKED}):
        actual = _stat_fields(info)
        expected = {
            "type": "regular", "uid": os.geteuid(), "mode": 0o600,
            "nlink": 1, "flags": ["0x00000000", "0x00000040"],
        }
        fail(f"{label} violates the closed post-runtime destination policy; "
             f"actual={_canonical_json(actual).decode('ascii').strip()} "
             f"expected={_canonical_json(expected).decode('ascii').strip()}")
    _require_no_acl(fd, label)
    result: dict[str, object] = {
        "dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode), "nlink": info.st_nlink,
        "size": info.st_size, "mtime_ns": info.st_mtime_ns, "flags": flags,
        "xattrs": _xattrs(fd, label),
    }
    if digest:
        result["sha256"] = sha256_fd(fd)
    return result


def _uf_tracked_transition(previous: int, current: int, label: str) -> str:
    """Return the exact monotonic transition or reject it closed."""
    if type(previous) is not int or type(current) is not int:
        fail(f"{label} UF_TRACKED flags are not exact integers")
    if previous == 0 and current == 0:
        return "0->0"
    if previous == 0 and current == UF_TRACKED:
        return "0->UF_TRACKED"
    if previous == UF_TRACKED and current == UF_TRACKED:
        return "UF_TRACKED->UF_TRACKED"
    if previous == UF_TRACKED and current == 0:
        fail(f"{label} removed UF_TRACKED")
    fail(f"{label} has forbidden or combined file flags: "
         f"0x{previous:08x}->0x{current:08x}")


def _stat_type(mode: int) -> str:
    """Return a closed, human-readable file-kind value for diagnostics."""
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISCHR(mode):
        return "character"
    if stat.S_ISBLK(mode):
        return "block"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    return "unknown"


def _stat_fields(info: os.stat_result) -> dict[str, object]:
    """The complete stat surface used by clone diagnostics and race reports."""
    return {
        "type": _stat_type(info.st_mode),
        "dev": info.st_dev,
        "ino": info.st_ino,
        "uid": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode),
        "nlink": info.st_nlink,
        "flags": f"0x{getattr(info, 'st_flags', 0):08x}",
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def _ledger_stat_fields(metadata: dict[str, object]) -> dict[str, object]:
    """Translate a closed v1 ledger file record into the diagnostic schema."""
    return {
        "type": "regular",
        "dev": metadata["dev"],
        "ino": metadata["ino"],
        "uid": metadata["uid"],
        "mode": metadata["mode"],
        "nlink": metadata["nlink"],
        "flags": f"0x{metadata['flags']:08x}",
        "size": metadata["size"],
        "mtime_ns": metadata["mtime_ns"],
    }


def _stat_diff(expected: dict[str, object], actual: dict[str, object]) -> dict[str, object]:
    """Return every observed mismatch, never a partial boolean classification."""
    if set(actual) == {"error"}:
        return {"entry": {"expected": expected, "actual": actual}}
    if set(actual) != set(expected):
        return {"schema": {"expected": sorted(expected), "actual": sorted(actual)}}
    return {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in expected if expected[key] != actual[key]
    }


def _metadata_diff(expected: dict[str, object], actual: dict[str, object]) -> dict[str, object]:
    """Exact ledger metadata differences for finalizer failures."""
    keys = sorted(set(expected) | set(actual))
    return {
        key: {"expected": expected.get(key), "actual": actual.get(key)}
        for key in keys if expected.get(key) != actual.get(key)
    }


def _open_relative_directory_nofollow(root_fd: int, relative: str, label: str) -> int:
    current = os.dup(root_fd)
    try:
        if relative:
            for component in safe_relative(relative).parts:
                child = -1
                try:
                    child = os.open(component, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                    | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
                    os.close(current)
                    current = child
                    child = -1
                finally:
                    if child >= 0:
                        os.close(child)
        return current
    except BaseException as error:
        os.close(current)
        if isinstance(error, OSError):
            fail(f"cannot nofollow-open {label}: {error}")
        raise


def _open_child_directory_nofollow(parent_fd: int, name: str, label: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
    except OSError as error:
        fail(f"cannot nofollow-open {label}: {error}")


def _devino(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _require_leaf_rebound(parent_fd: int, name: str, descriptor: int, label: str) -> None:
    """Bind a just-validated open file back to its nofollow directory entry."""
    opened = os.fstat(descriptor)
    try:
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        fail(f"cannot rebind {label} to its directory entry: {error}")
    if (not stat.S_ISREG(rebound.st_mode) or _devino(rebound) != _devino(opened)):
        fail(f"{label} pathname changed after descriptor validation")


def _require_directory_rebound(parent_fd: int, name: str, descriptor: int,
                               label: str) -> None:
    opened = os.fstat(descriptor)
    try:
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        fail(f"cannot rebind {label} to its directory entry: {error}")
    if (not stat.S_ISDIR(rebound.st_mode) or _devino(rebound) != _devino(opened)):
        fail(f"{label} pathname changed after descriptor validation")


def _ensure_clone_parent(root_fd: int, relative: str) -> int:
    current = os.dup(root_fd)
    try:
        if relative:
            for component in safe_relative(relative).parts:
                try:
                    info = os.stat(component, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    os.mkdir(component, 0o700, dir_fd=current)
                    os.fsync(current)
                else:
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                        fail(f"clone destination parent is not a real directory: {relative}")
                child = os.open(component, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
                try:
                    _directory_metadata(
                        child, f"clone destination directory {relative}", child=True)
                except BaseException:
                    os.close(child)
                    raise
                os.close(current)
                current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _open_or_create_destination_root(destination: Path) -> tuple[int, dict[str, object]]:
    parent_fd = _open_absolute_directory_nofollow(destination.parent, "clone destination parent")
    root_fd = -1
    try:
        parent_metadata = _directory_metadata(
            parent_fd, "clone destination parent", child=False,
            allowed_xattrs=CORE_SIMULATOR_CONTAINER_XATTRS,
        )
        try:
            info = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.mkdir(destination.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        else:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                fail("clone destination root is not a real directory")
        root_fd = os.open(destination.name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                          | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        if os.listdir(root_fd):
            os.close(root_fd)
            root_fd = -1
            fail("clone destination root must be empty")
        _directory_metadata(root_fd, "clone destination root", child=False)
        result = root_fd
        root_fd = -1
        return result, parent_metadata
    finally:
        try:
            if root_fd >= 0:
                os.close(root_fd)
        finally:
            os.close(parent_fd)


def _rename_exclusive_between_at(source_parent_fd: int, source: str,
                                 destination_parent_fd: int, destination: str) -> None:
    if sys.platform != "darwin":
        fail("exclusive clone artifact publication requires Darwin renameatx_np")
    try:
        function = ctypes.CDLL(None, use_errno=True).renameatx_np
    except AttributeError:
        fail("exclusive clone artifact publication requires renameatx_np")
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                         ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(source_parent_fd, os.fsencode(source), destination_parent_fd,
                os.fsencode(destination), RENAME_EXCL) != 0:
        number = ctypes.get_errno()
        fail(f"exclusive clone artifact rename failed: {OSError(number, os.strerror(number))}")


def _rename_exclusive_at(parent_fd: int, source: str, destination: str) -> None:
    _rename_exclusive_between_at(parent_fd, source, parent_fd, destination)


def _transaction_leaf(name: str, label: str) -> str:
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name:
        fail(f"{label} is not a confined leaf name")
    return name


def _write_private_record_at(parent_fd: int, name: str, value: dict[str, object],
                             label: str) -> tuple[tuple[int, int], bytes]:
    """Durably create one canonical private record; failures deliberately poison."""
    name = _transaction_leaf(name, label)
    payload = _canonical_json(value)
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600,
                             dir_fd=parent_fd)
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail(f"short write creating {label}")
            view = view[written:]
        os.fsync(descriptor)
        metadata = _file_metadata(descriptor, label, digest=True)
        if metadata["size"] != len(payload) or metadata["sha256"] != hashlib.sha256(payload).hexdigest():
            fail(f"{label} failed exact private-record verification")
        identity = (metadata["dev"], metadata["ino"])
        os.close(descriptor)
        descriptor = -1
        os.fsync(parent_fd)
        return identity, payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _mark_transaction_poisoned_at(parent_fd: int, stem: str) -> None:
    """Best-effort visible poison after a completion rename reports failure."""
    name = f"{stem}.poison"
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600,
                             dir_fd=parent_fd)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError:
        return
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        os.fsync(parent_fd)
    except BaseException:
        pass


def _publish_completion_record_at(parent_fd: int, stem: str,
                                  value: dict[str, object], label: str) -> None:
    """Durably publish completion; every interrupted precommit name poisons."""
    pending = f"{stem}.completion.pending"
    complete = f"{stem}.complete.json"
    renamed = False
    try:
        _write_private_record_at(parent_fd, pending, value, f"{label} pending")
        _rename_exclusive_at(parent_fd, pending, complete)
        renamed = True
        os.fsync(parent_fd)
    except BaseException:
        complete_visible = renamed
        if not complete_visible:
            try:
                os.stat(complete, dir_fd=parent_fd, follow_symlinks=False)
                complete_visible = True
            except FileNotFoundError:
                pass
        if complete_visible:
            _mark_transaction_poisoned_at(parent_fd, stem)
        raise


def _read_transaction_record_at(parent_fd: int, name: str,
                                label: str) -> tuple[dict[str, object], dict[str, object]]:
    state = _read_pinned_regular_at(
        parent_fd, Path("/") / _transaction_leaf(name, label), label, private=True)
    try:
        metadata = _file_metadata(state["fd"], label, digest=True)
        try:
            value = json.loads(state["data"].decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            fail(f"{label} is malformed: {error}")
        if not isinstance(value, dict) or _canonical_json(value) != state["data"]:
            fail(f"{label} is not canonical closed JSON")
        _revalidate_pinned(state, label)
        return value, {"identity": state["identity"], "sha256": state["sha256"],
                       "metadata": metadata}
    finally:
        _close_pinned(state)


def _validate_metadata_record(value: object, label: str) -> dict[str, object]:
    keys = {"dev", "ino", "uid", "mode", "nlink", "size", "mtime_ns",
            "flags", "xattrs", "sha256"}
    if not isinstance(value, dict) or set(value) != keys:
        fail(f"{label} metadata schema is invalid")
    if (any(type(value[key]) is not int or value[key] < 0
            for key in keys - {"xattrs", "sha256"})
            or not isinstance(value["xattrs"], dict)
            or not _is_hex_digest(value["sha256"])):
        fail(f"{label} metadata values are invalid")
    _validate_xattr_record(value["xattrs"], label)
    return value


def _transaction_names(names: list[str], prefix: str,
                       endings: tuple[str, ...], label: str) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for name in names:
        if not name.startswith(prefix):
            continue
        match = next((ending for ending in endings if name.endswith(ending)), None)
        if match is None:
            if name.endswith(".poison"):
                fail(f"{label} is poisoned by transaction marker: {name}")
            fail(f"{label} contains an unknown transaction artifact: {name}")
        token = name[len(prefix):-len(match)]
        if not TRANSACTION_TOKEN.fullmatch(token):
            fail(f"{label} contains a malformed transaction artifact: {name}")
        groups.setdefault(token, set()).add(match)
    return groups


def _require_absent_at(parent_fd: int, name: str, label: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    fail(f"{label} unexpectedly exists as {_stat_type(info.st_mode)}")


def _active_publication_parts(
        active_publication: dict[str, object] | None,
) -> tuple[dict[str, object] | None, str | None]:
    if active_publication is None:
        return None, None
    if (not isinstance(active_publication, dict)
            or set(active_publication) != {"record", "phase"}
            or active_publication["phase"] not in {
                "precommit", "postcommit", "completed",
                "neutralized", "neutralized-completed",
            }
            or not isinstance(active_publication["record"], dict)):
        fail("active publication context has invalid closed schema")
    return active_publication["record"], active_publication["phase"]


def _validate_private_parent_metadata_record(
        value: object, label: str) -> dict[str, object]:
    keys = {"dev", "ino", "uid", "mode", "flags", "xattrs"}
    if (not isinstance(value, dict) or set(value) != keys
            or any(type(value[key]) is not int
                   for key in ("dev", "ino", "uid", "mode", "flags"))
            or value["dev"] < 0 or value["ino"] <= 0
            or value["uid"] != os.geteuid() or value["mode"] != 0o700
            or value["flags"] != 0):
        fail(f"{label} has invalid private-parent metadata")
    _validate_xattr_record(value["xattrs"], label, allowed=DEFAULT_XATTRS)
    return value


def _require_private_parent_unchanged(parent_fd: int, parent: Path,
                                      expected: dict[str, object], label: str) -> None:
    current = _directory_metadata(parent_fd, label, child=True)
    if current != expected:
        fail(f"{label} descriptor-bound metadata changed")
    _rebind_transaction_parent(
        parent_fd, parent, (expected["dev"], expected["ino"]),
        f"{label} pathname rebind")


def _validate_transaction_parent_fd(
        parent_fd: int, parent: Path, label: str, *,
        active_publication: dict[str, object] | None = None) -> dict[str, object]:
    """Reject every incomplete/foreign transaction; accept only closed states."""
    parent = _absolute_unresolved_clone_path(parent, label)
    parent_metadata = _directory_metadata(parent_fd, label, child=True)
    parent_devino = (parent_metadata["dev"], parent_metadata["ino"])
    _rebind_transaction_parent(parent_fd, parent, parent_devino,
                               f"{label} initial pathname rebind")
    names = os.listdir(parent_fd)
    parent_info = os.fstat(parent_fd)
    parent_identity = {"dev": parent_info.st_dev, "ino": parent_info.st_ino}
    active_record, active_phase = _active_publication_parts(active_publication)
    active_parent = active_record.get("parent") if active_record is not None else None
    active_here = (isinstance(active_parent, dict)
                   and active_parent.get("dev") == parent_identity["dev"]
                   and active_parent.get("ino") == parent_identity["ino"])
    active_seen = False
    quarantine = _transaction_names(
        names, QUARANTINE_PREFIX, (".txn.json", ".complete.json", ".tombstone"), label)
    publication = _transaction_names(
        names, PUBLICATION_PREFIX, (".txn.json", ".complete.json"), label)

    for token, endings in quarantine.items():
        if endings != {".txn.json", ".complete.json", ".tombstone"}:
            fail(f"{label} is poisoned by incomplete quarantine transaction {token}")
        stem = f"{QUARANTINE_PREFIX}{token}"
        txn_name, complete_name, tombstone = (
            f"{stem}.txn.json", f"{stem}.complete.json", f"{stem}.tombstone")
        txn, txn_state = _read_transaction_record_at(
            parent_fd, txn_name, f"{label} quarantine transaction {token}")
        complete, _ = _read_transaction_record_at(
            parent_fd, complete_name, f"{label} quarantine completion {token}")
        if (set(txn) != {"schema", "token", "original", "tombstone", "parent", "expected"}
                or txn["schema"] != QUARANTINE_TXN_SCHEMA or txn["token"] != token
                or txn["tombstone"] != tombstone):
            fail(f"{label} quarantine transaction {token} has invalid closed schema")
        if (_validate_private_parent_metadata_record(
                txn["parent"], "quarantine transaction parent") != parent_metadata):
            fail(f"{label} quarantine transaction {token} parent metadata changed")
        original = _transaction_leaf(txn["original"], "quarantine original")
        if original.startswith((QUARANTINE_PREFIX, PUBLICATION_PREFIX)):
            fail(f"{label} quarantine transaction {token} targets a transaction artifact")
        expected = _validate_metadata_record(txn["expected"], "quarantine expected")
        if (set(complete) != {"schema", "token", "transaction_sha256",
                             "transaction_identity", "parent", "tombstone", "final"}
                or complete["schema"] != QUARANTINE_COMPLETE_SCHEMA
                or complete["token"] != token or complete["tombstone"] != tombstone
                or complete["transaction_sha256"] != txn_state["sha256"]
                or complete["transaction_identity"] != {
                    "dev": txn_state["identity"][0], "ino": txn_state["identity"][1]}
                or _validate_private_parent_metadata_record(
                    complete["parent"], "quarantine completion parent")
                != parent_metadata):
            fail(f"{label} quarantine completion {token} is not bound to its transaction")
        final = _validate_metadata_record(complete["final"], "quarantine final")
        stable_fields = ("dev", "ino", "uid", "mode", "nlink", "flags", "xattrs")
        if (any(final[key] != expected[key] for key in stable_fields)
                or final["size"] != 0 or final["sha256"] != hashlib.sha256(b"").hexdigest()):
            fail(f"{label} quarantine completion {token} is not a zero owned tombstone")
        _require_absent_at(parent_fd, original, "completed quarantine original")
        tomb_fd = -1
        try:
            tomb_fd = os.open(tombstone, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                              dir_fd=parent_fd)
            metadata = _file_metadata(
                tomb_fd, f"{label} completed quarantine tombstone {token}", digest=True)
            _require_leaf_rebound(parent_fd, tombstone, tomb_fd,
                                  f"{label} completed quarantine tombstone {token}")
            if metadata != final:
                fail(f"{label} completed quarantine tombstone {token} changed")
        finally:
            if tomb_fd >= 0:
                os.close(tomb_fd)

    for token, endings in publication.items():
        if active_here and token == active_record.get("token"):
            expected_endings = (
                {".txn.json", ".complete.json"}
                if active_phase in {"completed", "neutralized-completed"}
                else {".txn.json"}
            )
            if endings != expected_endings:
                fail(f"{label} active publication transaction {token} has invalid artifacts")
            stem = f"{PUBLICATION_PREFIX}{token}"
            txn, txn_state = _read_transaction_record_at(
                parent_fd, f"{stem}.txn.json", f"{label} active publication transaction {token}")
            if txn != active_record:
                fail(f"{label} active publication transaction {token} changed")
            if (_validate_private_parent_metadata_record(
                    txn.get("parent"), "active publication parent") != parent_metadata):
                fail(f"{label} active publication transaction {token} parent metadata changed")
            expected = _validate_metadata_record(
                txn["expected"], "active publication expected")
            source = _transaction_leaf(txn["source"], "active publication source")
            destination = _transaction_leaf(
                txn["destination"], "active publication destination")
            if active_phase == "precommit":
                active_name, absent_name = source, destination
            elif active_phase in {"postcommit", "completed"}:
                active_name, absent_name = destination, source
            else:
                active_name = None
                _require_absent_at(
                    parent_fd, source, f"{label} neutralized publication source {token}")
                _require_absent_at(
                    parent_fd, destination,
                    f"{label} neutralized publication destination {token}")
            if active_name is not None:
                state = _read_pinned_regular_at(
                    parent_fd, Path("/") / active_name,
                    f"{label} active publication file {token}", private=True)
                try:
                    if (_file_metadata(
                            state["fd"], f"{label} active publication file {token}",
                            digest=True) != expected):
                        fail(f"{label} active publication file {token} changed")
                    _revalidate_pinned(state, f"{label} active publication file {token}")
                finally:
                    _close_pinned(state)
                _require_absent_at(
                    parent_fd, absent_name,
                    f"{label} active publication absent leaf {token}")
            if active_phase in {"completed", "neutralized-completed"}:
                complete, _ = _read_transaction_record_at(
                    parent_fd, f"{stem}.complete.json",
                    f"{label} active publication completion {token}")
                if (set(complete) != {"schema", "token", "transaction_sha256",
                                     "transaction_identity", "parent",
                                     "destination", "final"}
                        or complete["schema"] != PUBLICATION_COMPLETE_SCHEMA
                        or complete["token"] != token
                        or complete["destination"] != destination
                        or complete["transaction_sha256"] != txn_state["sha256"]
                        or complete["transaction_identity"] != {
                            "dev": txn_state["identity"][0],
                            "ino": txn_state["identity"][1]}
                        or _validate_private_parent_metadata_record(
                            complete["parent"], "active publication completion parent")
                        != parent_metadata
                        or _validate_metadata_record(
                            complete["final"], "active publication final") != expected):
                    fail(f"{label} active publication completion {token} changed")
            active_seen = True
            continue
        if endings != {".txn.json", ".complete.json"}:
            fail(f"{label} is poisoned by incomplete publication transaction {token}")
        stem = f"{PUBLICATION_PREFIX}{token}"
        txn_name, complete_name = f"{stem}.txn.json", f"{stem}.complete.json"
        txn, txn_state = _read_transaction_record_at(
            parent_fd, txn_name, f"{label} publication transaction {token}")
        complete, _ = _read_transaction_record_at(
            parent_fd, complete_name, f"{label} publication completion {token}")
        if (set(txn) != {"schema", "token", "source", "destination", "parent", "expected"}
                or txn["schema"] != PUBLICATION_TXN_SCHEMA or txn["token"] != token
                ):
            fail(f"{label} publication transaction {token} has invalid closed schema")
        if (_validate_private_parent_metadata_record(
                txn["parent"], "publication transaction parent") != parent_metadata):
            fail(f"{label} publication transaction {token} parent metadata changed")
        source = _transaction_leaf(txn["source"], "publication source")
        destination = _transaction_leaf(txn["destination"], "publication destination")
        expected = _validate_metadata_record(txn["expected"], "publication expected")
        if (source.startswith((QUARANTINE_PREFIX, PUBLICATION_PREFIX))
                or destination.startswith((QUARANTINE_PREFIX, PUBLICATION_PREFIX))
                or source == destination):
            fail(f"{label} publication transaction {token} has invalid leaves")
        if (set(complete) != {"schema", "token", "transaction_sha256",
                             "transaction_identity", "parent", "destination", "final"}
                or complete["schema"] != PUBLICATION_COMPLETE_SCHEMA
                or complete["token"] != token or complete["destination"] != destination
                or complete["transaction_sha256"] != txn_state["sha256"]
                or complete["transaction_identity"] != {
                    "dev": txn_state["identity"][0], "ino": txn_state["identity"][1]}
                or _validate_private_parent_metadata_record(
                    complete["parent"], "publication completion parent")
                != parent_metadata):
            fail(f"{label} publication completion {token} is not bound to its transaction")
        final = _validate_metadata_record(complete["final"], "publication final")
        if final != expected:
            fail(f"{label} completed publication {token} changed metadata/content")
        _require_absent_at(parent_fd, source, "completed publication source")
        final_fd = -1
        try:
            final_fd = os.open(destination, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                               dir_fd=parent_fd)
            metadata = _file_metadata(
                final_fd, f"{label} completed publication destination {token}", digest=True)
            _require_leaf_rebound(parent_fd, destination, final_fd,
                                  f"{label} completed publication destination {token}")
            if metadata != final:
                fail(f"{label} completed publication destination {token} changed")
        finally:
            if final_fd >= 0:
                os.close(final_fd)
    if active_here and not active_seen:
        fail(f"{label} active publication transaction is missing")
    if _directory_metadata(parent_fd, label, child=True) != parent_metadata:
        fail(f"{label} metadata changed during transaction validation")
    _rebind_transaction_parent(parent_fd, parent, parent_devino,
                               f"{label} final pathname rebind")
    return parent_metadata


def _rebind_transaction_parent(parent_fd: int, parent: Path, identity: tuple[int, int],
                               label: str) -> None:
    rebound = _open_absolute_directory_nofollow(parent, label)
    try:
        if _devino(os.fstat(rebound)) != identity or _devino(os.fstat(parent_fd)) != identity:
            fail(f"{label} pathname changed")
    finally:
        os.close(rebound)


def _require_active_publication_txn_exact(
        parent_fd: int, expected_parent: dict[str, object],
        active_publication: dict[str, object], label: str) -> None:
    record, phase = _active_publication_parts(active_publication)
    if (phase not in {"postcommit", "completed"} or record is None
            or set(record) != {
                "schema", "token", "source", "destination", "parent", "expected"}
            or record["schema"] != PUBLICATION_TXN_SCHEMA
            or record["parent"] != expected_parent
            or not TRANSACTION_TOKEN.fullmatch(str(record["token"]))):
        fail(f"{label} active publication context changed")
    token = record["token"]
    stem = f"{PUBLICATION_PREFIX}{token}"
    expected_names = {f"{stem}.txn.json"}
    if phase == "completed":
        expected_names.add(f"{stem}.complete.json")
    observed_names = {
        name for name in os.listdir(parent_fd) if name.startswith(stem)
    }
    if observed_names != expected_names:
        fail(f"{label} active publication artifacts changed")
    txn, txn_state = _read_transaction_record_at(
        parent_fd, f"{stem}.txn.json", f"{label} active publication transaction")
    if txn != record:
        fail(f"{label} active publication transaction changed")
    _validate_private_parent_metadata_record(txn["parent"], f"{label} txn parent")
    _validate_metadata_record(txn["expected"], f"{label} expected file")
    _transaction_leaf(txn["source"], f"{label} source")
    destination = _transaction_leaf(txn["destination"], f"{label} destination")
    if phase == "completed":
        complete, _ = _read_transaction_record_at(
            parent_fd, f"{stem}.complete.json", f"{label} active completion")
        if (set(complete) != {"schema", "token", "transaction_sha256",
                             "transaction_identity", "parent", "destination", "final"}
                or complete["schema"] != PUBLICATION_COMPLETE_SCHEMA
                or complete["token"] != token
                or complete["transaction_sha256"] != txn_state["sha256"]
                or complete["transaction_identity"] != {
                    "dev": txn_state["identity"][0], "ino": txn_state["identity"][1]}
                or complete["parent"] != expected_parent
                or complete["destination"] != destination
                or _validate_metadata_record(
                    complete["final"], f"{label} completion final")
                != txn["expected"]):
            fail(f"{label} active publication completion changed")


def _quarantine_owned_at(parent_fd: int, parent: Path, name: str,
                         identity: tuple[int, int], label: str,
                         *, pinned_fd: int | None = None,
                         active_publication: dict[str, object] | None = None,
                         allow_poisoned_parent: bool = False,
                         expected_parent: dict[str, object] | None = None) -> bool:
    """Durably rename and zero only one exactly pinned private owned inode."""
    name = _transaction_leaf(name, label)
    if allow_poisoned_parent:
        if expected_parent is None or active_publication is None:
            fail(f"{label} emergency neutralization requires original parent and active txn")
        parent_metadata = _validate_private_parent_metadata_record(
            expected_parent, f"{label} original parent")
        # These are intentionally before even stat/open of the published PASS.
        # A parent drift leaves its bytes and inode untouched under the poisoned
        # original publication transaction.
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} emergency-neutralization original parent")
        _require_active_publication_txn_exact(
            parent_fd, parent_metadata, active_publication,
            f"{label} emergency neutralization")
    else:
        if expected_parent is not None:
            fail(f"{label} unexpected parent snapshot outside emergency neutralization")
        parent_metadata = _validate_transaction_parent_fd(
            parent_fd, parent, f"{label} parent",
            active_publication=active_publication)
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    owned_fd = os.dup(pinned_fd) if pinned_fd is not None else -1
    tomb_fd = -1
    try:
        if owned_fd < 0:
            owned_fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                               dir_fd=parent_fd)
        expected = _file_metadata(owned_fd, label, digest=True)
        if (expected["dev"], expected["ino"]) != identity:
            fail(f"refusing to quarantine replaced {label}")
        _require_leaf_rebound(parent_fd, name, owned_fd, label)
        token = uuid.uuid4().hex
        stem = f"{QUARANTINE_PREFIX}{token}"
        tombstone = f"{stem}.tombstone"
        txn_name = f"{stem}.txn.json"
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} quarantine parent before transaction")
        txn_identity, txn_payload = _write_private_record_at(parent_fd, txn_name, {
            "schema": QUARANTINE_TXN_SCHEMA, "token": token,
            "original": name, "tombstone": tombstone,
            "parent": parent_metadata,
            "expected": expected,
        }, f"{label} quarantine transaction")
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} quarantine parent before rename")
        _rename_exclusive_at(parent_fd, name, tombstone)
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} quarantine parent after rename")
        tomb_fd = os.open(tombstone, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                          dir_fd=parent_fd)
        tomb_before = _file_metadata(tomb_fd, f"{label} tombstone", digest=True)
        if (tomb_before != expected or _devino(os.fstat(tomb_fd)) != _devino(os.fstat(owned_fd))):
            fail(f"{label} tombstone is not the exactly pinned source")
        _require_absent_at(parent_fd, name, f"{label} original after quarantine rename")
        _require_leaf_rebound(parent_fd, tombstone, tomb_fd, f"{label} tombstone")
        # This is deliberately the last validation before the irreversible zero.
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} quarantine parent immediately before truncate")
        if _file_metadata(owned_fd, f"{label} pinned source before truncate", digest=True) != expected:
            fail(f"{label} pinned source changed immediately before truncate")
        os.ftruncate(tomb_fd, 0)
        truncated = os.fstat(tomb_fd)
        if _devino(truncated) != identity or truncated.st_size != 0:
            fail(f"{label} tombstone did not truncate the owned inode exactly")
        os.fsync(tomb_fd)
        final = _file_metadata(tomb_fd, f"{label} zero tombstone", digest=True)
        stable_fields = ("dev", "ino", "uid", "mode", "nlink", "flags", "xattrs")
        if (any(final[key] != expected[key] for key in stable_fields)
                or (final["dev"], final["ino"]) != identity or final["size"] != 0
                or final["sha256"] != hashlib.sha256(b"").hexdigest()):
            fail(f"{label} zero tombstone failed final identity/content verification")
        _require_leaf_rebound(parent_fd, tombstone, tomb_fd, f"{label} zero tombstone")
        _require_absent_at(parent_fd, name, f"{label} original after truncate")
        os.fsync(parent_fd)
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} quarantine parent before completion")
        _publish_completion_record_at(parent_fd, stem, {
            "schema": QUARANTINE_COMPLETE_SCHEMA, "token": token,
            "transaction_sha256": hashlib.sha256(txn_payload).hexdigest(),
            "transaction_identity": {"dev": txn_identity[0], "ino": txn_identity[1]},
            "parent": parent_metadata,
            "tombstone": tombstone, "final": final,
        }, f"{label} quarantine completion")
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} quarantine parent after completion")
        completed_context = active_publication
        if active_publication is not None:
            record, phase = _active_publication_parts(active_publication)
            completed_context = {
                "record": record,
                "phase": ("neutralized-completed"
                          if phase in {"completed", "neutralized-completed"}
                          else "neutralized"),
            }
        if allow_poisoned_parent:
            _require_private_parent_unchanged(
                parent_fd, parent, parent_metadata,
                f"{label} completed emergency-neutralization parent")
        else:
            _validate_transaction_parent_fd(
                parent_fd, parent, f"{label} completed quarantine parent",
                active_publication=completed_context)
        return True
    finally:
        if tomb_fd >= 0:
            os.close(tomb_fd)
        if owned_fd >= 0:
            os.close(owned_fd)


def _quarantine_owned_path(path: Path, identity: tuple[int, int], label: str,
                           expected_parent: dict[str, object] | None = None) -> None:
    path = _absolute_unresolved_clone_path(path, label)
    parent_fd = _open_absolute_directory_nofollow(path.parent, f"{label} parent")
    try:
        if (expected_parent is not None
                and _directory_metadata(parent_fd, f"{label} parent", child=False)
                != expected_parent):
            fail(f"{label} parent identity/metadata changed before quarantine")
        _quarantine_owned_at(parent_fd, path.parent, path.name, identity, label)
    finally:
        os.close(parent_fd)


def _publish_owned_rename_at(parent_fd: int, parent: Path, source: str,
                             destination: str, label: str, *, validate=None,
                             post_commit=None,
                             expected_parent: dict[str, object] | None = None,
                             ) -> tuple[tuple[int, int], bytes]:
    """Publish one single-link pending file by exclusive same-parent rename."""
    source = _transaction_leaf(source, f"{label} source")
    destination = _transaction_leaf(destination, f"{label} destination")
    if source == destination:
        fail(f"{label} source and destination must differ")
    observed_parent = _validate_transaction_parent_fd(
        parent_fd, parent, f"{label} parent")
    if expected_parent is not None:
        expected_parent = _validate_private_parent_metadata_record(
            expected_parent, f"{label} expected parent")
        if observed_parent != expected_parent:
            fail(f"{label} parent changed before publication helper baseline")
        parent_metadata = expected_parent
    else:
        parent_metadata = observed_parent
    parent_identity = (parent_metadata["dev"], parent_metadata["ino"])
    state = _read_pinned_regular_at(
        parent_fd, parent / source, f"{label} pending source", private=True)
    renamed = False
    completed = False
    active_record: dict[str, object] | None = None
    try:
        expected = _file_metadata(state["fd"], f"{label} pending source", digest=True)
        _require_absent_at(parent_fd, destination, f"{label} destination")
        if validate is not None:
            validate(state, expected, None)
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} parent before transaction")
        token = uuid.uuid4().hex
        stem = f"{PUBLICATION_PREFIX}{token}"
        active_record = {
            "schema": PUBLICATION_TXN_SCHEMA, "token": token,
            "source": source, "destination": destination,
            "parent": parent_metadata,
            "expected": expected,
        }
        precommit = {"record": active_record, "phase": "precommit"}
        txn_identity, txn_payload = _write_private_record_at(
            parent_fd, f"{stem}.txn.json", active_record,
            f"{label} publication transaction")
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} parent after transaction")
        _revalidate_pinned(state, f"{label} pending source before commit")
        if _file_metadata(state["fd"], f"{label} pending source before commit", digest=True) != expected:
            fail(f"{label} pending source metadata changed before commit")
        if validate is not None:
            validate(state, expected, precommit)
        _revalidate_pinned(state, f"{label} pending source immediately before commit")
        if (_file_metadata(state["fd"], f"{label} pending source immediately before commit",
                           digest=True) != expected):
            fail(f"{label} pending source metadata changed immediately before commit")
        _require_absent_at(parent_fd, destination, f"{label} destination before commit")
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} parent immediately before commit")
        _rename_exclusive_at(parent_fd, source, destination)
        renamed = True
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} parent after commit")
        _require_absent_at(parent_fd, source, f"{label} pending source after commit")
        _require_leaf_rebound(parent_fd, destination, state["fd"],
                              f"{label} committed destination")
        final = _file_metadata(state["fd"], f"{label} committed destination", digest=True)
        if final != expected:
            fail(f"{label} committed destination changed")
        os.fsync(parent_fd)
        postcommit = {"record": active_record, "phase": "postcommit"}
        _validate_transaction_parent_fd(
            parent_fd, parent, f"{label} post-rename publication parent",
            active_publication=postcommit)
        if post_commit is not None:
            post_commit(state, expected, postcommit)
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} parent immediately before completion")
        _publish_completion_record_at(parent_fd, stem, {
            "schema": PUBLICATION_COMPLETE_SCHEMA, "token": token,
            "transaction_sha256": hashlib.sha256(txn_payload).hexdigest(),
            "transaction_identity": {"dev": txn_identity[0], "ino": txn_identity[1]},
            "parent": parent_metadata,
            "destination": destination, "final": final,
        }, f"{label} publication completion")
        _require_private_parent_unchanged(
            parent_fd, parent, parent_metadata,
            f"{label} parent after completion")
        completed = True
        completed_context = {"record": active_record, "phase": "completed"}
        _validate_transaction_parent_fd(
            parent_fd, parent, f"{label} completed publication parent",
            active_publication=completed_context)
        if post_commit is not None:
            post_commit(state, expected, completed_context)
        _validate_transaction_parent_fd(
            parent_fd, parent, f"{label} completed publication parent")
        return state["identity"], state["data"]
    except BaseException as original:
        cleanup_error: BaseException | None = None
        if not renamed and active_record is not None:
            try:
                _require_absent_at(
                    parent_fd, source, f"{label} source after interrupted rename")
                _require_leaf_rebound(
                    parent_fd, destination, state["fd"],
                    f"{label} destination after interrupted rename")
                renamed = True
            except (GuardError, OSError):
                pass
        if renamed and active_record is not None:
            context = {"record": active_record,
                       "phase": "completed" if completed else "postcommit"}
            try:
                _quarantine_owned_at(
                    parent_fd, parent, destination, state["identity"],
                    f"failed {label} publication", pinned_fd=state["fd"],
                    active_publication=context, allow_poisoned_parent=True,
                    expected_parent=parent_metadata)
            except BaseException as error:
                cleanup_error = error
        if cleanup_error is not None:
            if active_record is not None:
                _mark_transaction_poisoned_at(
                    parent_fd, f"{PUBLICATION_PREFIX}{active_record['token']}")
            fail(f"{label} publication failed and owned PASS neutralization was incomplete: "
                 f"{original}; cleanup={cleanup_error}")
        raise
    finally:
        _close_pinned(state)


def _atomic_publish_new(path: Path, data: bytes, label: str,
                        expected_parent: dict[str, object] | None = None,
                        validate_boundary=None) -> tuple[int, int]:
    """O_EXCL pending + exclusive rename; failures quarantine only owned data."""
    path = _absolute_unresolved_clone_path(path, label)
    parent_fd = _open_absolute_directory_nofollow(path.parent, f"{label} parent")
    temporary = f".{path.name}.txn-{os.getpid()}-{uuid.uuid4().hex}"
    descriptor = -1
    identity: tuple[int, int] | None = None
    renamed = False
    try:
        current_parent = _directory_metadata(parent_fd, f"{label} parent", child=False)
        if expected_parent is not None and current_parent != expected_parent:
            fail(f"{label} parent identity/metadata changed")
        _validate_transaction_parent_fd(parent_fd, path.parent, f"{label} parent")
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            fail(f"{label} destination already exists")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
        identity_info = os.fstat(descriptor)
        identity = (identity_info.st_dev, identity_info.st_ino)
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail(f"short write creating {label}")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_size != len(data):
            fail(f"{label} size changed before publication")
        if validate_boundary is not None:
            validate_boundary("pre-rename")
        os.close(descriptor)
        descriptor = -1
        _rename_exclusive_at(parent_fd, temporary, path.name)
        renamed = True
        if validate_boundary is not None:
            validate_boundary("post-rename")
        os.fsync(parent_fd)
        if validate_boundary is not None:
            validate_boundary("completion")
        return identity
    except BaseException as original:
        cleanup_errors: list[str] = []
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except BaseException as cleanup:
                cleanup_errors.append(f"temporary descriptor: {cleanup}")
            descriptor = -1
        if identity is not None:
            candidate = path.name if renamed else temporary
            try:
                _quarantine_owned_at(
                    parent_fd, path.parent, candidate, identity, label)
            except BaseException as cleanup:
                cleanup_errors.append(f"{candidate}: {cleanup}")
        if cleanup_errors:
            fail(f"{label} publication failed and quarantine/durability is uncertain: "
                 f"{original}; cleanup={'; '.join(cleanup_errors)}")
        raise
    finally:
        try:
            if descriptor >= 0:
                os.close(descriptor)
        finally:
            os.close(parent_fd)


def _parse_staged_bytes(data: bytes, label: str) -> dict[str, tuple[int, str]]:
    try:
        lines = data.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as error:
        fail(f"{label} is not UTF-8: {error}")
    if not lines or lines[0] != "sha256\tbytes\tpath":
        fail(f"{label} has invalid header")
    result: dict[str, tuple[int, str]] = {}
    for number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 3:
            fail(f"{label} has invalid row {number}")
        digest, size_text, relative = fields
        safe_relative(relative)
        try:
            size = int(size_text)
        except ValueError:
            fail(f"{label} has invalid byte count at row {number}")
        if (relative in result or size < 0 or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            fail(f"{label} has invalid row {number}")
        result[relative] = (size, digest)
    return result


def _read_pinned_regular_at(parent_fd: int, path: Path, label: str,
                            *, private: bool) -> dict[str, object]:
    path = _absolute_unresolved_clone_path(path, label)
    owned_parent_fd = os.dup(parent_fd)
    descriptor = -1
    try:
        descriptor = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                             dir_fd=owned_parent_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (private and (info.st_uid != os.geteuid()
                                 or stat.S_IMODE(info.st_mode) != 0o600
                                 or info.st_nlink != 1))):
            fail(f"{label} is not an acceptable regular file")
        metadata = (_file_metadata(descriptor, label, digest=True)
                    if private else None)
        data = bytearray()
        while block := os.read(descriptor, CHUNK):
            data.extend(block)
        current = os.stat(path.name, dir_fd=owned_parent_fd, follow_symlinks=False)
        if ((current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                or len(data) != info.st_size):
            fail(f"{label} changed while being pinned")
        if private and _file_metadata(
                descriptor, f"{label} repeated private metadata", digest=True) != metadata:
            fail(f"{label} private metadata changed while being pinned")
        return {"fd": descriptor, "parent_fd": owned_parent_fd, "path": path,
                "identity": (info.st_dev, info.st_ino), "size": info.st_size,
                "mtime_ns": info.st_mtime_ns, "data": bytes(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "private": private, "metadata": metadata}
    except BaseException as error:
        try:
            if descriptor >= 0:
                os.close(descriptor)
        finally:
            os.close(owned_parent_fd)
        if isinstance(error, OSError):
            fail(f"cannot nofollow-open {label}: {error}")
        raise


def _read_pinned_regular(path: Path, label: str, *, private: bool) -> dict[str, object]:
    path = _absolute_unresolved_clone_path(path, label)
    parent_fd = _open_absolute_directory_nofollow(path.parent, f"{label} parent")
    try:
        return _read_pinned_regular_at(parent_fd, path, label, private=private)
    finally:
        os.close(parent_fd)


def _close_pinned(state: dict[str, object]) -> None:
    try:
        os.close(state["fd"])
    finally:
        os.close(state["parent_fd"])


def _revalidate_pinned(state: dict[str, object], label: str) -> None:
    descriptor = state["fd"]
    os.lseek(descriptor, 0, os.SEEK_SET)
    data = bytearray()
    while block := os.read(descriptor, CHUNK):
        data.extend(block)
    info = os.fstat(descriptor)
    path_info = os.stat(state["path"].name, dir_fd=state["parent_fd"],
                        follow_symlinks=False)
    if state.get("private"):
        current_metadata = _file_metadata(
            descriptor, f"{label} private metadata", digest=True)
        if current_metadata != state.get("metadata"):
            fail(f"{label} private metadata changed around validation")
    if ((info.st_dev, info.st_ino) != state["identity"]
            or (path_info.st_dev, path_info.st_ino) != state["identity"]
            or info.st_size != state["size"] or path_info.st_size != state["size"]
            or info.st_mtime_ns != state["mtime_ns"]
            or bytes(data) != state["data"]):
        fail(f"{label} changed around validation")


def _revalidate_pinned_at_name(state: dict[str, object], parent_fd: int,
                               name: str, label: str) -> None:
    """Revalidate one pinned file after an intentional same-parent rename."""
    name = _transaction_leaf(name, label)
    descriptor = state["fd"]
    os.lseek(descriptor, 0, os.SEEK_SET)
    data = bytearray()
    while block := os.read(descriptor, CHUNK):
        data.extend(block)
    info = os.fstat(descriptor)
    path_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if state.get("private"):
        current_metadata = _file_metadata(
            descriptor, f"{label} private metadata", digest=True)
        if current_metadata != state.get("metadata"):
            fail(f"{label} private metadata changed around renamed-file validation")
    if ((info.st_dev, info.st_ino) != state["identity"]
            or (path_info.st_dev, path_info.st_ino) != state["identity"]
            or info.st_size != state["size"] or path_info.st_size != state["size"]
            or info.st_mtime_ns != state["mtime_ns"]
            or path_info.st_mtime_ns != state["mtime_ns"]
            or bytes(data) != state["data"]):
        fail(f"{label} changed around renamed-file validation")


def _is_hex_digest(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _validate_xattr_record(value: object, label: str, *,
                           allowed: frozenset[str] = DEFAULT_XATTRS) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        fail(f"{label} has invalid xattrs")
    for encoded in value.values():
        if not isinstance(encoded, str):
            fail(f"{label} has invalid xattr bytes")
        try:
            base64.b64decode(encoded, validate=True)
        except ValueError:
            fail(f"{label} has invalid xattr encoding")


def _validate_directory_record(value: object, label: str, *,
                               allowed_xattrs: frozenset[str] = DEFAULT_XATTRS
                               ) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "metadata"}:
        fail(f"{label} has invalid keys")
    path = value["path"]
    if not isinstance(path, str) or (path and safe_relative(path).as_posix() != path):
        fail(f"{label} has invalid path")
    metadata = value["metadata"]
    if (not isinstance(metadata, dict)
            or set(metadata) != {"dev", "ino", "uid", "mode", "flags", "xattrs"}
            or any(type(metadata[key]) is not int for key in ("dev", "ino", "uid", "mode", "flags"))
            or metadata["dev"] < 0 or metadata["ino"] <= 0 or metadata["flags"] != 0
            or metadata["uid"] != os.geteuid() or metadata["mode"] & 0o022):
        fail(f"{label} has invalid metadata")
    if path and metadata["mode"] != 0o700:
        fail(f"{label} child mode is not 0700")
    _validate_xattr_record(metadata["xattrs"], label, allowed=allowed_xattrs)


def _validate_file_metadata(value: object, label: str) -> None:
    required = {"dev", "ino", "uid", "mode", "nlink", "size", "mtime_ns",
                "flags", "xattrs", "sha256"}
    if (not isinstance(value, dict) or set(value) != required
            or any(type(value[key]) is not int for key in
                   ("dev", "ino", "uid", "mode", "nlink", "size", "mtime_ns", "flags"))
            or value["dev"] < 0 or value["ino"] <= 0 or value["uid"] != os.geteuid()
            or value["mode"] != 0o600 or value["nlink"] != 1 or value["size"] < 0
            or value["flags"] != 0 or not _is_hex_digest(value["sha256"])):
        fail(f"{label} has invalid private-file metadata")
    _validate_xattr_record(value["xattrs"], label)


def _validate_destination_file_metadata_record(value: object, label: str) -> None:
    required = {"dev", "ino", "uid", "mode", "nlink", "size", "mtime_ns",
                "flags", "xattrs", "sha256"}
    if (not isinstance(value, dict) or set(value) != required
            or any(type(value[key]) is not int for key in
                   ("dev", "ino", "uid", "mode", "nlink", "size", "mtime_ns", "flags"))
            or value["dev"] < 0 or value["ino"] <= 0
            or value["uid"] != os.geteuid() or value["mode"] != 0o600
            or value["nlink"] != 1 or value["size"] < 0
            or value["flags"] not in {0, UF_TRACKED}
            or not _is_hex_digest(value["sha256"])):
        fail(f"{label} has invalid post-runtime destination metadata")
    _validate_xattr_record(value["xattrs"], label)


def _validate_diagnostic_binding(value: object, label: str) -> None:
    if (not isinstance(value, dict)
            or set(value) != {"label", "path", "dev", "ino", "bytes", "sha256"}
            or value["label"] not in {"D0", "D1", "D2", "D3", "D4"}
            or not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
            or any(type(value[key]) is not int for key in ("dev", "ino", "bytes"))
            or value["dev"] < 0 or value["ino"] <= 0 or value["bytes"] <= 0
            or not _is_hex_digest(value["sha256"])):
        fail(f"{label} has invalid diagnostic identity/digest binding")


def _validate_prepared_snapshot(snapshot: object) -> None:
    if (not isinstance(snapshot, dict)
            or set(snapshot) != {"directories", "manifest", "documents"}
            or not isinstance(snapshot["directories"], list)
            or len(snapshot["directories"]) != 3
            or not isinstance(snapshot["manifest"], dict)
            or not isinstance(snapshot["documents"], dict)):
        fail("clone ledger has invalid prepared snapshot")
    for identity_value in snapshot["directories"]:
        if (not isinstance(identity_value, dict)
                or set(identity_value) != {"dev", "ino", "size", "mtime_ns"}
                or any(type(identity_value[key]) is not int for key in identity_value)):
            fail("clone ledger has invalid prepared directory identity")
    for label, entries in (("manifest", snapshot["manifest"]),
                           ("documents", snapshot["documents"])):
        for relative, record in entries.items():
            if not isinstance(relative, str):
                fail(f"clone ledger prepared {label} has invalid path")
            if label == "documents":
                safe_relative(relative)
            elif (safe_relative(relative).as_posix() != relative
                  or len(Path(relative).parts) != 1):
                fail("clone ledger prepared manifest has invalid filename")
            if (not isinstance(record, dict) or set(record) != {"identity", "sha256"}
                    or not _is_hex_digest(record["sha256"])
                    or not isinstance(record["identity"], dict)
                    or set(record["identity"]) != {"dev", "ino", "size", "mtime_ns"}
                    or any(type(record["identity"][key]) is not int
                           for key in record["identity"])):
                fail(f"clone ledger prepared {label} has invalid record")


def _validate_clone_ledger(ledger: object) -> dict[str, object]:
    top = {"schema", "stage_mode", "method", "fallback_count", "flags", "with_saves",
           "file_count", "byte_count", "filesystems", "paths", "prepared_snapshot",
           "destination_parent", "artifact_parent", "directories", "files",
           "staged_manifest_sha256", "uf_tracked_policy", "post_runtime"}
    if not isinstance(ledger, dict) or set(ledger) != top:
        fail("clone ledger v2 has unknown or missing top-level keys")
    if (ledger["schema"] != CLONE_LEDGER_SCHEMA
            or ledger["stage_mode"] != "clone-required"
            or ledger["method"] != "fclonefileat" or ledger["fallback_count"] != 0
            or type(ledger["with_saves"]) is not bool
            or type(ledger["file_count"]) is not int or ledger["file_count"] <= 0
            or type(ledger["byte_count"]) is not int or ledger["byte_count"] < 0
            or not _is_hex_digest(ledger["staged_manifest_sha256"])):
        fail("clone ledger v2 summary is invalid")
    expected_flags = {"value": FCLONEFILEAT_FLAGS, "nofollow": True,
                      "noownercopy": True, "nofollow_any": True,
                      "resolve_beneath": True}
    if ledger["flags"] != expected_flags:
        fail("clone ledger v2 clone flags are not exact")
    if ledger["uf_tracked_policy"] != UF_TRACKED_POLICY:
        fail("clone ledger v2 UF_TRACKED policy is not exact")
    if (not isinstance(ledger["filesystems"], dict)
            or set(ledger["filesystems"]) != {"prepared_dev", "destination_dev"}
            or any(type(value) is not int or value < 0
                   for value in ledger["filesystems"].values())):
        fail("clone ledger v2 filesystem identities are invalid")
    if (not isinstance(ledger["paths"], dict)
            or set(ledger["paths"]) != {"prepared", "destination", "staged_manifest",
                                        "pending_ledger"}
            or any(not isinstance(value, str) or not Path(value).is_absolute()
                   for value in ledger["paths"].values())):
        fail("clone ledger v2 paths are invalid")
    normalized_paths = {
        key: str(_absolute_unresolved_clone_path(Path(value), f"ledger {key}"))
        for key, value in ledger["paths"].items()
    }
    if normalized_paths != ledger["paths"]:
        fail("clone ledger v2 paths are not canonical")
    _clone_paths(Path(ledger["paths"]["prepared"]),
                 Path(ledger["paths"]["destination"]),
                 Path(ledger["paths"]["staged_manifest"]),
                 Path(ledger["paths"]["pending_ledger"]))
    _validate_prepared_snapshot(ledger["prepared_snapshot"])
    _validate_directory_record(
        {"path": "", "metadata": ledger["destination_parent"]},
        "destination_parent", allowed_xattrs=CORE_SIMULATOR_CONTAINER_XATTRS,
    )
    _validate_directory_record(
        {"path": "", "metadata": ledger["artifact_parent"]}, "artifact_parent",
    )
    directories = ledger["directories"]
    if not isinstance(directories, list) or not directories:
        fail("clone ledger v2 directory set is empty")
    directory_paths: list[str] = []
    for index, record in enumerate(directories):
        _validate_directory_record(record, f"clone ledger directory {index}")
        directory_paths.append(record["path"])
    if directory_paths != sorted(set(directory_paths)) or directory_paths[0] != "":
        fail("clone ledger v2 directory paths are not unique/sorted/rooted")
    files = ledger["files"]
    if not isinstance(files, list) or len(files) != ledger["file_count"]:
        fail("clone ledger v2 file count differs")
    file_paths: list[str] = []
    total = 0
    for index, record in enumerate(files):
        if (not isinstance(record, dict)
                or set(record) != {"path", "bytes", "sha256", "result", "parent",
                                   "source", "destination"}
                or not isinstance(record["path"], str)
                or safe_relative(record["path"]).as_posix() != record["path"]
                or type(record["bytes"]) is not int or record["bytes"] < 0
                or not _is_hex_digest(record["sha256"]) or record["result"] != "cloned"
                or not isinstance(record["parent"], str)
                or record["parent"] not in directory_paths):
            fail(f"clone ledger file {index} is invalid")
        expected_parent_path = Path(record["path"]).parent
        expected_parent = "" if expected_parent_path == Path(".") else expected_parent_path.as_posix()
        if expected_parent != record["parent"]:
            fail(f"clone ledger file {record['path']} has mismatched parent")
        _validate_file_metadata(record["source"], f"source {record['path']}")
        _validate_file_metadata(record["destination"], f"destination {record['path']}")
        if (record["bytes"] != record["source"]["size"]
                or record["bytes"] != record["destination"]["size"]
                or record["sha256"] != record["source"]["sha256"]
                or record["sha256"] != record["destination"]["sha256"]
                or record["source"]["dev"] != record["destination"]["dev"]
                or record["source"]["ino"] == record["destination"]["ino"]
                or record["source"]["xattrs"] != record["destination"]["xattrs"]):
            fail(f"clone ledger file {record['path']} is not an exact clone record")
        file_paths.append(record["path"])
        total += record["bytes"]
    if file_paths != sorted(set(file_paths)) or total != ledger["byte_count"]:
        fail("clone ledger v2 file paths/totals are invalid")
    prepared_paths = sorted(ledger["prepared_snapshot"]["documents"])
    expected_paths = [path for path in prepared_paths
                      if allowed_retail_path(safe_relative(path), ledger["with_saves"])]
    if file_paths != expected_paths:
        fail("clone ledger file set does not exactly derive from prepared snapshot")
    expected_directories = {""}
    for path in file_paths:
        parts = safe_relative(path).parts[:-1]
        expected_directories.update(Path(*parts[:index]).as_posix()
                                    for index in range(1, len(parts) + 1))
    if directory_paths != sorted(expected_directories):
        fail("clone ledger directory set does not exactly cover staged parents")
    if (ledger["filesystems"]["prepared_dev"] != files[0]["source"]["dev"]
            or ledger["filesystems"]["destination_dev"] != directories[0]["metadata"]["dev"]):
        fail("clone ledger filesystem summary differs from records")
    for record in files:
        prepared_record = ledger["prepared_snapshot"]["documents"][record["path"]]
        if (record["source"]["dev"] != prepared_record["identity"]["dev"]
                or record["source"]["ino"] != prepared_record["identity"]["ino"]
                or record["source"]["size"] != prepared_record["identity"]["size"]
                or record["source"]["mtime_ns"] != prepared_record["identity"]["mtime_ns"]
                or record["source"]["sha256"] != prepared_record["sha256"]):
            fail(f"clone ledger source differs from prepared snapshot: {record['path']}")
    post = ledger["post_runtime"]
    if post is not None:
        if (not isinstance(post, dict)
                or set(post) != {"destination_boundary", "mutable_save",
                                 "source_reverified", "runtime", "policy",
                                 "continuity_mode", "diagnostics", "quickload",
                                 "directories",
                                 "files", "flag_counts", "flags_digest"}
                or post["destination_boundary"] != CLONE_DESTINATION_BOUNDARY
                or post["source_reverified"] is not True
                or post["runtime"] != "27.0"
                or post["policy"] != UF_TRACKED_POLICY
                or post["continuity_mode"] not in {"final-only", "D0-D4"}
                or (post["mutable_save"] is not None and not isinstance(post["mutable_save"], str))
                or not isinstance(post["directories"], list)
                or not isinstance(post["files"], list)
                or not isinstance(post["flag_counts"], dict)
                or set(post["flag_counts"]) != {"zero", "tracked"}
                or any(type(value) is not int or value < 0
                       for value in post["flag_counts"].values())
                or not _is_hex_digest(post["flags_digest"])):
            fail("clone ledger v2 post-runtime state is invalid")
        if post["quickload"] is not None:
            _validate_quickload_binding(post["quickload"])
        if post["continuity_mode"] == "final-only":
            if post["diagnostics"] is not None:
                fail("final-only clone ledger cannot claim a diagnostic chain")
        else:
            diagnostics = post["diagnostics"]
            if (not isinstance(diagnostics, dict)
                    or set(diagnostics) != {"root", "baseline_ledger", "final_record"}
                    or not isinstance(diagnostics["root"], str)
                    or not Path(diagnostics["root"]).is_absolute()):
                fail("clone ledger D0-D4 diagnostic chain binding is invalid")
            baseline_ledger = diagnostics["baseline_ledger"]
            if (not isinstance(baseline_ledger, dict)
                    or set(baseline_ledger) != {"path", "dev", "ino", "bytes", "sha256"}
                    or not isinstance(baseline_ledger["path"], str)
                    or not Path(baseline_ledger["path"]).is_absolute()
                    or any(type(baseline_ledger[key]) is not int
                           for key in ("dev", "ino", "bytes"))
                    or baseline_ledger["dev"] < 0 or baseline_ledger["ino"] <= 0
                    or baseline_ledger["bytes"] <= 0
                    or not _is_hex_digest(baseline_ledger["sha256"])):
                fail("clone ledger diagnostic baseline-ledger binding is invalid")
            if baseline_ledger["path"] != ledger["paths"]["pending_ledger"]:
                fail("clone ledger diagnostic baseline path differs from L0")
            _validate_diagnostic_binding(
                diagnostics["final_record"], "clone ledger D4 final record")
            if (diagnostics["final_record"]["label"] != "D4"
                    or diagnostics["final_record"]["path"]
                    != str(Path(diagnostics["root"]) / "D4.json")):
                fail("clone ledger diagnostic chain does not terminate at D4")
        post_directory_paths = []
        for index, record in enumerate(post["directories"]):
            _validate_directory_record(record, f"post-runtime directory {index}")
            post_directory_paths.append(record["path"])
        if post_directory_paths != directory_paths or post["directories"] != directories:
            fail("post-runtime directory correspondence differs")
        post_paths = []
        for index, record in enumerate(post["files"]):
            if (not isinstance(record, dict)
                    or set(record) != {"path", "mutable", "destination",
                                      "previous_flags", "transition"}
                    or type(record["mutable"]) is not bool):
                fail(f"post-runtime file {index} is invalid")
            _validate_destination_file_metadata_record(
                record["destination"], f"post destination {record.get('path')}")
            if post["continuity_mode"] == "final-only":
                if record["previous_flags"] is not None or record["transition"] != "final-only":
                    fail("final-only file record makes a continuity claim")
            else:
                if type(record["previous_flags"]) is not int:
                    fail("D0-D4 file record lacks previous flags")
                expected_transition = _uf_tracked_transition(
                    record["previous_flags"], record["destination"]["flags"],
                    f"post-runtime file {record.get('path')}")
                if record["transition"] != expected_transition:
                    fail("D0-D4 file transition is not exact")
            post_paths.append(record["path"])
        if post_paths != file_paths or sum(record["mutable"] for record in post["files"]) > 1:
            fail("post-runtime file correspondence differs")
        mutable_paths = [record["path"] for record in post["files"] if record["mutable"]]
        if mutable_paths != ([] if post["mutable_save"] is None else [post["mutable_save"]]):
            fail("post-runtime mutable save correspondence differs")
        initial_by_path = {record["path"]: record for record in files}
        for record in post["files"]:
            if record["mutable"]:
                mutable_path = safe_relative(record["path"])
                if (not ledger["with_saves"] or mutable_path.parent != SAVES_ROOT
                        or mutable_path.suffix != ".scop"):
                    fail("post-runtime mutable file is not an approved staged save")
                validate_autoload_name(mutable_path.stem)
                if (record["destination"]["size"] <= 0
                        or record["destination"]["dev"]
                        != ledger["filesystems"]["destination_dev"]
                        or record["destination"]["xattrs"]
                        != initial_by_path[record["path"]]["source"]["xattrs"]):
                    fail("post-runtime mutable save metadata is invalid")
            elif record["destination"] != initial_by_path[record["path"]]["destination"]:
                expected = dict(initial_by_path[record["path"]]["destination"])
                expected["flags"] = record["destination"]["flags"]
                if record["destination"] != expected:
                    fail(f"post-runtime immutable correspondence differs: {record['path']}")
        zero = sum(record["destination"]["flags"] == 0 for record in post["files"])
        tracked = sum(record["destination"]["flags"] == UF_TRACKED
                      for record in post["files"])
        if post["flag_counts"] != {"zero": zero, "tracked": tracked}:
            fail("clone ledger UF_TRACKED counts differ from final files")
        digest_rows = [{"path": record["path"],
                        "dev": record["destination"]["dev"],
                        "ino": record["destination"]["ino"],
                        "flags": record["destination"]["flags"]}
                       for record in post["files"]]
        if hashlib.sha256(_canonical_json(digest_rows)).hexdigest() != post["flags_digest"]:
            fail("clone ledger canonical path/dev/ino/flags digest differs")
    return ledger


def _read_clone_ledger(path: Path, *, finalized: bool) -> dict[str, object]:
    pinned = _read_pinned_regular(path, "clone ledger", private=True)
    try:
        try:
            value = json.loads(pinned["data"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            fail(f"clone ledger is malformed: {error}")
        if _canonical_json(value) != pinned["data"]:
            fail("clone ledger is not canonical JSON")
        ledger = _validate_clone_ledger(value)
        if finalized != (ledger["post_runtime"] is not None):
            fail("clone ledger finalization state is unexpected")
        pinned["ledger"] = ledger
        return pinned
    except BaseException:
        _close_pinned(pinned)
        raise


def _directory_records(root_fd: int, parent_paths: list[str]) -> list[dict[str, object]]:
    records = []
    for relative in sorted(set(["", *parent_paths])):
        descriptor = _open_relative_directory_nofollow(
            root_fd, relative, f"clone destination directory {relative or '<root>'}")
        try:
            records.append({"path": relative, "metadata": _directory_metadata(
                descriptor, f"clone destination directory {relative or '<root>'}",
                child=bool(relative),
            )})
        finally:
            os.close(descriptor)
    return records


def _parent_metadata(path: Path, label: str) -> dict[str, object]:
    descriptor = _open_absolute_directory_nofollow(path.parent, f"{label} parent")
    try:
        return _directory_metadata(descriptor, f"{label} parent", child=False)
    finally:
        os.close(descriptor)


def _staged_manifest_bytes(rows: list[tuple[str, int, str]]) -> bytes:
    return ("sha256\tbytes\tpath\n" + "".join(
        f"{digest}\t{size}\t{relative}\n" for relative, size, digest in sorted(rows)
    )).encode("utf-8")


def _validate_prepared_source_records(ledger: dict[str, object], importer: object) -> None:
    """Rebind the complete prepared source state through pinned parent/root fds."""
    prepared = Path(ledger["paths"]["prepared"])
    snapshot_directories = ledger["prepared_snapshot"]["directories"]
    parent_fd = importer.open_absolute_directory_nofollow(
        prepared.parent, "prepared source parent")
    parent_identity = _devino(os.fstat(parent_fd))
    prepared_fd = -1
    documents_fd = -1

    def open_roots(anchor_fd: int, label: str) -> tuple[int, int]:
        root = _open_child_directory_nofollow(anchor_fd, prepared.name,
                                              f"{label} prepared root")
        documents = -1
        try:
            _require_directory_rebound(anchor_fd, prepared.name, root,
                                       f"{label} prepared root")
            if _devino(os.fstat(root)) != (
                    snapshot_directories[0]["dev"], snapshot_directories[0]["ino"]):
                fail(f"{label} prepared root identity differs from the ledger snapshot")
            documents = _open_child_directory_nofollow(
                root, "Documents", f"{label} prepared Documents")
            _require_directory_rebound(root, "Documents", documents,
                                       f"{label} prepared Documents")
            if _devino(os.fstat(documents)) != (
                    snapshot_directories[1]["dev"], snapshot_directories[1]["ino"]):
                fail(f"{label} prepared Documents identity differs from the ledger snapshot")
            return root, documents
        except BaseException:
            try:
                if documents >= 0:
                    os.close(documents)
            finally:
                os.close(root)
            raise

    def read_state(root_fd: int, expected_parents: dict[str, tuple[int, int]] | None,
                   label: str) -> dict[str, object]:
        parent_paths = sorted({record["parent"] for record in ledger["files"]})
        parents: dict[str, tuple[int, int]] = {}
        for relative in parent_paths:
            descriptor = _open_relative_directory_nofollow(
                root_fd, relative, f"{label} source parent {relative or '<root>'}")
            try:
                parents[relative] = _devino(os.fstat(descriptor))
            finally:
                os.close(descriptor)
            if expected_parents is not None and parents[relative] != expected_parents[relative]:
                fail(f"prepared source parent pathname changed: {relative or '<root>'}")
        files: list[dict[str, object]] = []
        for record in ledger["files"]:
            relative = safe_relative(record["path"])
            parent = _open_relative_directory_nofollow(
                root_fd, record["parent"],
                f"{label} source parent {record['parent'] or '<root>'}")
            descriptor = -1
            try:
                if _devino(os.fstat(parent)) != parents[record["parent"]]:
                    fail(f"prepared source parent changed before leaf open: {record['path']}")
                descriptor = os.open(relative.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                     dir_fd=parent)
                metadata = _file_metadata(
                    descriptor, f"{label} source file {record['path']}", digest=True)
                _require_leaf_rebound(parent, relative.name, descriptor,
                                      f"{label} source file {record['path']}")
            except OSError as error:
                fail(f"cannot nofollow-open {label} source file {record['path']}: {error}")
            finally:
                try:
                    if descriptor >= 0:
                        os.close(descriptor)
                finally:
                    os.close(parent)
            if metadata != record["source"]:
                fail(f"prepared source identity/metadata/content changed: {record['path']}")
            files.append({"path": record["path"], "metadata": metadata})
        for relative, identity in parents.items():
            descriptor = _open_relative_directory_nofollow(
                root_fd, relative, f"{label} final source parent {relative or '<root>'}")
            try:
                if _devino(os.fstat(descriptor)) != identity:
                    fail(f"prepared source parent changed after leaf validation: "
                         f"{relative or '<root>'}")
            finally:
                os.close(descriptor)
        return {"parents": parents, "files": files}

    try:
        prepared_fd, documents_fd = open_roots(parent_fd, "initial")
        initial_state = read_state(documents_fd, None, "initial prepared revalidation")
        rebound_parent_fd = importer.open_absolute_directory_nofollow(
            prepared.parent, "final prepared source parent")
        rebound_prepared_fd = -1
        rebound_documents_fd = -1
        try:
            if _devino(os.fstat(rebound_parent_fd)) != parent_identity:
                fail("prepared source parent pathname changed during validation")
            rebound_prepared_fd, rebound_documents_fd = open_roots(
                rebound_parent_fd, "final")
            final_state = read_state(
                rebound_documents_fd, initial_state["parents"], "final prepared revalidation")
            if final_state != initial_state:
                fail("prepared source complete state changed across final rebind")
            _require_directory_rebound(rebound_prepared_fd, "Documents",
                                       rebound_documents_fd, "final prepared Documents")
            _require_directory_rebound(rebound_parent_fd, prepared.name,
                                       rebound_prepared_fd, "final prepared root")
        finally:
            try:
                if rebound_documents_fd >= 0:
                    os.close(rebound_documents_fd)
            finally:
                try:
                    if rebound_prepared_fd >= 0:
                        os.close(rebound_prepared_fd)
                finally:
                    os.close(rebound_parent_fd)
        final_parent_fd = importer.open_absolute_directory_nofollow(
            prepared.parent, "prepared source parent final pathname rebind")
        try:
            if _devino(os.fstat(final_parent_fd)) != parent_identity:
                fail("prepared source parent pathname changed at final boundary")
        finally:
            os.close(final_parent_fd)
    finally:
        try:
            if documents_fd >= 0:
                os.close(documents_fd)
        finally:
            try:
                if prepared_fd >= 0:
                    os.close(prepared_fd)
            finally:
                os.close(parent_fd)


def clone_stage(prepared: Path, destination: Path, output: Path, pending_ledger: Path,
                with_saves: bool) -> None:
    """Stage verified prepared files by APFS clone; fallback_count is always zero."""
    prepared, destination, output, pending_ledger = _clone_paths(
        prepared, destination, output, pending_ledger,
    )
    importer = _import_retail_import()
    prepared_snapshot = importer.verify_prepared(prepared)
    prepared_snapshot = json.loads(_canonical_json(prepared_snapshot))
    source_root = prepared / "Documents"
    manifest = prepared / "manifest"
    files, _, _ = validate_retail(source_root, manifest)
    selected = [safe_relative(path) for path in sorted(files)
                if allowed_retail_path(safe_relative(path), with_saves)]
    if not selected:
        fail("retail allowlist selected no clone files")
    if os.path.lexists(output) or os.path.lexists(pending_ledger):
        fail("clone staging outputs must be absent")
    artifact_parent_fd = -1
    try:
        artifact_parent_fd = _open_absolute_directory_nofollow(
            output.parent, "clone artifact parent")
        artifact_parent = _directory_metadata(
            artifact_parent_fd, "clone artifact parent", child=False)
    finally:
        if artifact_parent_fd >= 0:
            os.close(artifact_parent_fd)
    destination_root_fd = -1
    source_root_fd = -1
    rows: list[tuple[str, int, str]] = []
    records: list[dict[str, object]] = []
    parent_paths: list[str] = []
    try:
        destination_root_fd, destination_parent = _open_or_create_destination_root(destination)
        source_root_fd = importer.open_absolute_directory_nofollow(
            source_root, "prepared Documents")
        for relative in selected:
            text = relative.as_posix()
            parent_text = "" if len(relative.parts) == 1 else Path(*relative.parts[:-1]).as_posix()
            parent_parts = relative.parts[:-1]
            parent_paths.extend(Path(*parent_parts[:index]).as_posix()
                                for index in range(1, len(parent_parts) + 1))
            source_fd = -1
            destination_parent_fd = -1
            destination_fd = -1
            try:
                source_fd = importer.open_relative_regular_nofollow(
                    source_root_fd, relative, os.O_RDONLY,
                    f"prepared clone source {text}")
                destination_parent_fd = _ensure_clone_parent(
                    destination_root_fd, parent_text)
                source_metadata = _file_metadata(source_fd, f"prepared clone source {text}", digest=True)
                planned = prepared_snapshot["documents"].get(text)
                if (planned is None or planned["sha256"] != source_metadata["sha256"]
                        or planned["identity"]["dev"] != source_metadata["dev"]
                        or planned["identity"]["ino"] != source_metadata["ino"]
                        or planned["identity"]["size"] != source_metadata["size"]
                        or planned["identity"]["mtime_ns"] != source_metadata["mtime_ns"]
                        or files[text] != source_metadata["size"]):
                    fail(f"prepared clone source differs from verified snapshot: {text}")
                if source_metadata["dev"] != os.fstat(destination_parent_fd).st_dev:
                    fail(f"APFS clone crosses volumes (EXDEV): {text}")
                try:
                    os.stat(relative.name, dir_fd=destination_parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    fail(f"clone destination already exists (EEXIST): {text}")
                _fclonefileat(source_fd, destination_parent_fd, relative.name, text)
                destination_fd = os.open(relative.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                         dir_fd=destination_parent_fd)
                destination_metadata = _file_metadata(
                    destination_fd, f"cloned destination {text}", digest=True)
                if (destination_metadata["dev"] != source_metadata["dev"]
                        or destination_metadata["ino"] == source_metadata["ino"]
                        or destination_metadata["size"] != source_metadata["size"]
                        or destination_metadata["sha256"] != source_metadata["sha256"]
                        or destination_metadata["xattrs"] != source_metadata["xattrs"]):
                    fail(f"cloned destination failed exact CoW verification: {text}")
                source_after = _file_metadata(source_fd, f"prepared clone source {text}", digest=True)
                if source_after != source_metadata:
                    fail(f"prepared clone source changed during clone: {text}")
                os.fsync(destination_fd)
                os.fsync(destination_parent_fd)
                rows.append((text, source_metadata["size"], source_metadata["sha256"]))
                records.append({"path": text, "bytes": source_metadata["size"],
                                "sha256": source_metadata["sha256"], "result": "cloned",
                                "parent": parent_text, "source": source_metadata,
                                "destination": destination_metadata})
            finally:
                if destination_fd >= 0:
                    os.close(destination_fd)
                if destination_parent_fd >= 0:
                    os.close(destination_parent_fd)
                if source_fd >= 0:
                    os.close(source_fd)
        directories = _directory_records(destination_root_fd, parent_paths)
        staged_data = _staged_manifest_bytes(rows)
        ledger = {
            "schema": CLONE_LEDGER_SCHEMA, "stage_mode": "clone-required",
            "method": "fclonefileat", "fallback_count": 0,
            "flags": {"value": FCLONEFILEAT_FLAGS, "nofollow": True,
                      "noownercopy": True, "nofollow_any": True,
                      "resolve_beneath": True},
            "with_saves": with_saves, "file_count": len(records),
            "byte_count": sum(record["bytes"] for record in records),
            "filesystems": {"prepared_dev": records[0]["source"]["dev"],
                            "destination_dev": directories[0]["metadata"]["dev"]},
            "paths": {"prepared": str(prepared), "destination": str(destination),
                      "staged_manifest": str(output), "pending_ledger": str(pending_ledger)},
            "prepared_snapshot": prepared_snapshot,
            "destination_parent": destination_parent, "artifact_parent": artifact_parent,
            "directories": directories, "files": records,
            "staged_manifest_sha256": hashlib.sha256(staged_data).hexdigest(),
            "uf_tracked_policy": UF_TRACKED_POLICY,
            "post_runtime": None,
        }
        _validate_clone_ledger(ledger)
        current_snapshot = json.loads(_canonical_json(importer.verify_prepared(prepared)))
        if current_snapshot != prepared_snapshot:
            fail("prepared source changed during clone staging")
        _validate_prepared_source_records(ledger, importer)
        initial_post = _validate_destination_post_runtime(ledger, None)
        if (any(record["mutable"] for record in initial_post["files"])
                or any(record["destination"]["flags"] != 0
                       for record in initial_post["files"])):
            fail("initial clone validation is not exact flags=0 immutable stage state")
        ledger_data = _canonical_json(ledger)
        ledger_identity = _atomic_publish_new(pending_ledger, ledger_data,
                                              "pending clone ledger", artifact_parent)
        try:
            _atomic_publish_new(output, staged_data, "staged-files.tsv", artifact_parent)
        except BaseException as original:
            try:
                _quarantine_owned_path(
                    pending_ledger, ledger_identity, "pending clone ledger", artifact_parent)
            except GuardError as cleanup:
                fail(f"staged-files.tsv publication failed and pending-ledger cleanup failed: "
                     f"{original}; cleanup={cleanup}")
            raise
    finally:
        if source_root_fd >= 0:
            os.close(source_root_fd)
        if destination_root_fd >= 0:
            os.close(destination_root_fd)


def _open_or_create_private_diagnostics_root(work_root_fd: int, path: Path,
                                             label: str) -> int:
    """Open the exact private diagnostics child through a pinned work root."""
    try:
        try:
            entry = os.stat(path.name, dir_fd=work_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            if label != "D0":
                fail("clone metadata diagnostics root is missing after D0")
            os.mkdir(path.name, 0o700, dir_fd=work_root_fd)
            os.fsync(work_root_fd)
        else:
            if label == "D0":
                fail("clone metadata diagnostics root must be a new D0 child")
            if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                fail("clone metadata diagnostics root is not a real directory")
        descriptor = _open_child_directory_nofollow(
            work_root_fd, path.name, "clone metadata diagnostics root")
        try:
            _require_directory_rebound(work_root_fd, path.name, descriptor,
                                       "clone metadata diagnostics root")
            _directory_metadata(descriptor, "clone metadata diagnostics root", child=True)
            expected_names = {f"D{index}.json" for index in range(int(label[1:]))}
            actual_names = set(os.listdir(descriptor))
            if actual_names != expected_names:
                fail("clone metadata diagnostics root has an unexpected prior artifact set")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    except OSError as error:
        fail(f"cannot create/open clone metadata diagnostics root: {error}")


def _write_clone_stat_snapshot(root_fd: int, root: Path, label: str,
                               value: dict[str, object]) -> tuple[tuple[int, int], bytes]:
    """Publish one forensic-only snapshot with O_EXCL and descriptor durability."""
    if label not in {"D0", "D1", "D2", "D3", "D4"}:
        fail("clone metadata diagnostics label is not closed")
    name = f"{label}.json"
    payload = _canonical_json(value)
    descriptor = -1
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600,
                             dir_fd=root_fd)
        os.fchmod(descriptor, 0o600)
        created = os.fstat(descriptor)
        identity = _devino(created)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail("short write creating clone metadata diagnostic")
            view = view[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size != len(payload) or _devino(info) != identity):
            fail("clone metadata diagnostic is not an exact private regular file")
        os.close(descriptor)
        descriptor = -1
        os.fsync(root_fd)
        return identity, payload
    except BaseException as original:
        cleanup_errors: list[str] = []
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if identity is not None:
            try:
                _quarantine_owned_at(
                    root_fd, root, name, identity,
                    f"clone metadata diagnostic {label}")
            except (OSError, GuardError) as cleanup:
                cleanup_errors.append(str(cleanup))
        if cleanup_errors:
            fail(f"clone metadata diagnostic publication failed and rollback was incomplete: "
                 f"{original}; cleanup={'; '.join(cleanup_errors)}")
        if isinstance(original, GuardError):
            raise
        fail(f"cannot publish clone metadata diagnostic {label}: {original}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _close_pinned_many(states: list[dict[str, object]]) -> None:
    while states:
        _close_pinned(states.pop())


def _pin_diagnostic_cleanup_artifacts(work_root_fd: int, work_root: Path,
                                      label: str) -> list[dict[str, object]]:
    """Pin only the transient QuickLoad files owned before the D4 boundary."""
    for name in (".report.pending", "report.txt", "clone-ledger.json"):
        try:
            os.stat(name, dir_fd=work_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        fail(f"forbidden PASS artifact exists before clone metadata {label}: {name}")
    quickload_name = "quickload-evidence"
    quickload_fd = -1
    states: list[dict[str, object]] = []
    try:
        try:
            quickload_entry = os.stat(
                quickload_name, dir_fd=work_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            if label in {"D2", "D3", "D4"}:
                fail(f"QuickLoad evidence root is missing at {label}")
            return states
        if stat.S_ISLNK(quickload_entry.st_mode) or not stat.S_ISDIR(quickload_entry.st_mode):
            fail("QuickLoad evidence root is not a real directory")
        quickload_fd = _open_child_directory_nofollow(
            work_root_fd, quickload_name, "QuickLoad diagnostic cleanup root")
        _require_directory_rebound(work_root_fd, quickload_name, quickload_fd,
                                   "QuickLoad diagnostic cleanup root")
        try:
            os.stat("manifest.json", dir_fd=quickload_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            fail("published QuickLoad manifest exists before diagnostic boundary")
        transient_names = ("manifest.pending.json", "report-fields.txt")
        if label == "D4":
            for name in transient_names:
                states.append(_read_pinned_regular_at(
                    quickload_fd, work_root / quickload_name / name,
                    f"D4 transient QuickLoad {name}", private=True))
        else:
            for name in transient_names:
                try:
                    os.stat(name, dir_fd=quickload_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                fail(f"transient QuickLoad publication file exists before D4: {name}")
        return states
    except BaseException:
        _close_pinned_many(states)
        raise
    finally:
        if quickload_fd >= 0:
            os.close(quickload_fd)


def _quickload_artifact_binding(state: dict[str, object]) -> dict[str, object]:
    return {"path": str(state["path"]), "dev": state["identity"][0],
            "ino": state["identity"][1], "bytes": state["size"],
            "sha256": state["sha256"]}


def _quickload_states_by_name(
        states: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    result = {state["path"].name: state for state in states}
    if set(result) != {"manifest.pending.json", "report-fields.txt"}:
        fail("D4 QuickLoad transient file set is not exact")
    return result


def _bind_quickload_transients(
        states: list[dict[str, object]]) -> dict[str, object]:
    values = _quickload_states_by_name(states)
    manifest = values["manifest.pending.json"]
    report_fields = values["report-fields.txt"]
    validator = _import_quickload_evidence()
    try:
        semantic = validator.validate_pending_manifest_bytes(
            manifest["data"], expected_root=manifest["path"].parent)
        validator.validate_report_fields_bytes(
            report_fields["data"], manifest["data"])
    except Exception as error:
        if isinstance(error, GuardError):
            raise
        fail(f"QuickLoad transient semantic validation failed: {error}")
    binding = {
        "contract": semantic["contract"],
        "semantic_sha256": semantic["semantic_sha256"],
        "runtime": semantic["runtime"],
        "simulator_uuid": semantic["simulator_uuid"],
        "manifest": _quickload_artifact_binding(manifest),
        "report_fields": _quickload_artifact_binding(report_fields),
    }
    _validate_quickload_binding(binding)
    return binding


def _validate_quickload_artifact_binding(value: object, label: str) -> None:
    if (not isinstance(value, dict)
            or set(value) != {"path", "dev", "ino", "bytes", "sha256"}
            or not isinstance(value["path"], str)
            or not Path(value["path"]).is_absolute()
            or any(type(value[key]) is not int for key in
                   ("dev", "ino", "bytes"))
            or value["dev"] < 0 or value["ino"] <= 0 or value["bytes"] <= 0
            or not _is_hex_digest(value["sha256"])):
        fail(f"{label} is invalid")


def _validate_quickload_binding(value: object) -> dict[str, object]:
    keys = {"contract", "semantic_sha256", "runtime", "simulator_uuid",
            "manifest", "report_fields"}
    if (not isinstance(value, dict) or set(value) != keys
            or value["contract"]
            != "openxray-ios27-quicksave-quickload-semantic-v2"
            or value["runtime"] != "27.0"
            or not isinstance(value["simulator_uuid"], str)
            or re.fullmatch(
                r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}",
                value["simulator_uuid"]) is None
            or not _is_hex_digest(value["semantic_sha256"])):
        fail("clone ledger QuickLoad semantic binding is invalid")
    _validate_quickload_artifact_binding(value["manifest"], "QuickLoad manifest binding")
    _validate_quickload_artifact_binding(
        value["report_fields"], "QuickLoad report-fields binding")
    manifest = Path(value["manifest"]["path"])
    fields = Path(value["report_fields"]["path"])
    if (manifest.name != "manifest.pending.json"
            or fields.name != "report-fields.txt"
            or manifest.parent != fields.parent
            or manifest.parent.name != "quickload-evidence"):
        fail("clone ledger QuickLoad artifact paths are not exact")
    return value


def _revalidate_quickload_transients(
        states: list[dict[str, object]], expected: dict[str, object],
        *, manifest_name: str = "manifest.pending.json") -> None:
    values = _quickload_states_by_name(states)
    manifest = values["manifest.pending.json"]
    report_fields = values["report-fields.txt"]
    _revalidate_pinned_at_name(
        manifest, manifest["parent_fd"], manifest_name,
        "bound QuickLoad manifest")
    _revalidate_pinned(report_fields, "bound QuickLoad report fields")
    validator = _import_quickload_evidence()
    try:
        semantic = validator.validate_pending_manifest_bytes(
            manifest["data"], expected_root=manifest["path"].parent)
        validator.validate_report_fields_bytes(
            report_fields["data"], manifest["data"])
    except Exception as error:
        if isinstance(error, GuardError):
            raise
        fail(f"bound QuickLoad semantic validation failed: {error}")
    current = {
        "contract": semantic["contract"],
        "semantic_sha256": semantic["semantic_sha256"],
        "runtime": semantic["runtime"],
        "simulator_uuid": semantic["simulator_uuid"],
        "manifest": _quickload_artifact_binding(manifest),
        "report_fields": _quickload_artifact_binding(report_fields),
    }
    if current != expected:
        fail("QuickLoad semantic/artifact binding changed")


def _remove_pinned_cleanup_artifacts(states: list[dict[str, object]], label: str) -> None:
    """Quarantine only the exact live-pinned inode for each transient artifact."""
    cleanup_errors: list[str] = []
    while states:
        state = states.pop()
        try:
            _quarantine_owned_at(
                state["parent_fd"], state["path"].parent, state["path"].name,
                state["identity"], f"{label} transient artifact",
                pinned_fd=state["fd"],
            )
        except (OSError, GuardError) as error:
            cleanup_errors.append(f"{state['path']}: {error}")
        finally:
            _close_pinned(state)
    if cleanup_errors:
        fail(f"{label} identity-bound cleanup was incomplete: {'; '.join(cleanup_errors)}")


def _descriptor_relative_stat(root_fd: int, relative: Path, label: str) -> dict[str, object]:
    """One nofollow stat of a ledger leaf; this deliberately never opens it."""
    parent_text = "" if len(relative.parts) == 1 else Path(*relative.parts[:-1]).as_posix()
    parent_fd = -1
    try:
        parent_fd = _open_relative_directory_nofollow(root_fd, parent_text, label)
        return _stat_fields(os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False))
    except (GuardError, OSError) as error:
        return {"error": f"{type(error).__name__}: {error}"}
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _diagnostic_file_binding(state: dict[str, object], label: str) -> dict[str, object]:
    return {"label": label, "path": str(state["path"]),
            "dev": state["identity"][0], "ino": state["identity"][1],
            "bytes": state["size"], "sha256": state["sha256"]}


def _diagnostic_ledger_binding(pinned: dict[str, object]) -> dict[str, object]:
    return {"path": str(pinned["path"]),
            "dev": pinned["identity"][0], "ino": pinned["identity"][1],
            "bytes": pinned["size"], "sha256": pinned["sha256"]}


def _diagnostic_paths(ledger: dict[str, object], work_root: Path,
                      diagnostics_root: Path) -> dict[str, str]:
    return {"prepared": ledger["paths"]["prepared"],
            "documents": ledger["paths"]["destination"],
            "staged_manifest": ledger["paths"]["staged_manifest"],
            "pending_ledger": ledger["paths"]["pending_ledger"],
            "work_root": str(work_root),
            "diagnostics_root": str(diagnostics_root)}


def _stat_immutable_diff(expected: dict[str, object], actual: dict[str, object]
                         ) -> dict[str, object]:
    if set(actual) == {"error"}:
        return {"entry": {"expected": expected, "actual": actual}}
    if set(actual) != set(expected):
        return {"schema": {"expected": sorted(expected), "actual": sorted(actual)}}
    return {key: {"expected": expected[key], "actual": actual[key]}
            for key in expected if key != "flags" and expected[key] != actual[key]}


def _diagnostic_stat_flags(value: object, label: str) -> int:
    if (not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-f]{8}", value)):
        fail(f"{label} has invalid flags encoding")
    return int(value, 16)


def _validate_clone_diagnostic_snapshot(
        value: object, *, label: str, ledger: dict[str, object],
        ledger_binding: dict[str, object], paths: dict[str, str],
        previous: dict[str, object] | None, previous_flags: dict[str, int],
        require_clean: bool,
) -> dict[str, int]:
    sequence = int(label[1:])
    top = {"schema", "diagnostic_only", "result", "label", "sequence",
           "runtime", "policy", "paths", "ledger", "previous",
           "quickload", "file_count", "files"}
    if (not isinstance(value, dict) or set(value) != top
            or value["schema"] != CLONE_DIAGNOSTIC_SCHEMA
            or value["diagnostic_only"] is not True
            or value["result"] != "diagnostic"
            or value["label"] != label or value["sequence"] != sequence
            or value["runtime"] != "27.0" or value["policy"] != UF_TRACKED_POLICY
            or value["paths"] != paths or value["ledger"] != ledger_binding
            or value["previous"] != previous
            or type(value["file_count"]) is not int
            or not isinstance(value["files"], list)
            or value["file_count"] != len(value["files"])):
        fail(f"clone metadata diagnostic {label} has invalid closed v2 schema/binding")
    if label == "D4":
        if value["quickload"] is None:
            fail("clone metadata diagnostic D4 lacks its QuickLoad baseline binding")
        _validate_quickload_binding(value["quickload"])
    elif value["quickload"] is not None:
        fail(f"clone metadata diagnostic {label} must not contain a QuickLoad binding")
    expected_paths = [record["path"] for record in ledger["files"]]
    if [record.get("path") for record in value["files"]
            if isinstance(record, dict)] != expected_paths:
        fail(f"clone metadata diagnostic {label} file set/order differs from ledger")
    if set(previous_flags) != set(expected_paths):
        fail(f"clone metadata diagnostic {label} prior file set differs from ledger")
    resulting: dict[str, int] = {}
    record_keys = {"path", "expected", "first", "second", "first_diff",
                   "second_diff", "race", "previous_flags", "actual_flags",
                   "transition", "policy_error"}
    for ledger_file, record in zip(ledger["files"], value["files"]):
        path = ledger_file["path"]
        if not isinstance(record, dict) or set(record) != record_keys:
            fail(f"clone metadata diagnostic {label} record is malformed: {path}")
        expected = _ledger_stat_fields(ledger_file["destination"])
        if record["expected"] != expected:
            fail(f"clone metadata diagnostic {label} expected state differs: {path}")
        if (not isinstance(record["first_diff"], dict)
                or not isinstance(record["second_diff"], dict)
                or type(record["race"]) is not bool
                or record["previous_flags"] != previous_flags[path]
                or type(record["actual_flags"]) is not int
                or not isinstance(record["transition"], str)
                or (record["policy_error"] is not None
                    and not isinstance(record["policy_error"], str))):
            fail(f"clone metadata diagnostic {label} transition fields are malformed: {path}")
        first_diff = _stat_immutable_diff(expected, record["first"])
        second_diff = _stat_immutable_diff(expected, record["second"])
        if record["first_diff"] != first_diff or record["second_diff"] != second_diff:
            fail(f"clone metadata diagnostic {label} exact diff changed: {path}")
        race = record["first"] != record["second"]
        if record["race"] != race:
            fail(f"clone metadata diagnostic {label} race result changed: {path}")
        policy_error = None
        transition = "invalid"
        try:
            first_flags = _diagnostic_stat_flags(
                record["first"].get("flags") if isinstance(record["first"], dict) else None,
                f"{label} first {path}")
            second_flags = _diagnostic_stat_flags(
                record["second"].get("flags") if isinstance(record["second"], dict) else None,
                f"{label} second {path}")
            if first_flags != second_flags:
                fail(f"{label} file flags raced: {path}")
            if label == "D0" and second_flags != 0:
                fail(f"D0 requires exact destination flags=0: {path}")
            transition = _uf_tracked_transition(
                previous_flags[path], second_flags, f"{label} {path}")
        except GuardError as error:
            policy_error = str(error)
            second_flags = record["actual_flags"]
        if (record["actual_flags"] != second_flags
                or record["transition"] != transition
                or record["policy_error"] != policy_error):
            fail(f"clone metadata diagnostic {label} policy result changed: {path}")
        if require_clean and (first_diff or second_diff or race or policy_error is not None):
            fail(f"clone metadata diagnostic chain is poisoned at {label}: {path}")
        resulting[path] = second_flags
    return resulting


def _read_clone_diagnostic_chain(
        diagnostics_fd: int, diagnostics_root: Path, ledger_pinned: dict[str, object],
        work_root: Path, count: int,
        ledger_binding: dict[str, object] | None = None,
) -> tuple[dict[str, int], dict[str, object] | None,
           list[dict[str, object]], dict[str, object] | None]:
    ledger = ledger_pinned["ledger"]
    if ledger_binding is None:
        ledger_binding = _diagnostic_ledger_binding(ledger_pinned)
    paths = _diagnostic_paths(ledger, work_root, diagnostics_root)
    previous = None
    previous_flags = {record["path"]: 0 for record in ledger["files"]}
    pinned_states: list[dict[str, object]] = []
    quickload_binding: dict[str, object] | None = None
    try:
        for sequence in range(count):
            label = f"D{sequence}"
            state = _read_pinned_regular_at(
                diagnostics_fd, diagnostics_root / f"{label}.json",
                f"clone metadata diagnostic chain {label}", private=True)
            pinned_states.append(state)
            try:
                value = json.loads(state["data"].decode("utf-8", "strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                fail(f"clone metadata diagnostic chain {label} is malformed: {error}")
            if _canonical_json(value) != state["data"]:
                fail(f"clone metadata diagnostic chain {label} is not canonical JSON")
            previous_flags = _validate_clone_diagnostic_snapshot(
                value, label=label, ledger=ledger, ledger_binding=ledger_binding,
                paths=paths, previous=previous, previous_flags=previous_flags,
                require_clean=True)
            if label == "D4":
                quickload_binding = value["quickload"]
            previous = _diagnostic_file_binding(state, label)
        return previous_flags, previous, pinned_states, quickload_binding
    except BaseException:
        _close_pinned_many(pinned_states)
        raise


def clone_stat_snapshot(prepared: Path, documents: Path, pending_ledger: Path,
                        work_root: Path, diagnostics_root: Path, label: str,
                        runtime: str = "27.0") -> None:
    """Write D0--D4 stat-only diagnostics and fail closed on any observed delta.

    L0 is the canonical pending clone ledger.  The leaf observation below uses
    two separate descriptor-relative ``stat(..., follow_symlinks=False)`` calls
    per staged file and never opens the files or reads their contents.
    """
    prepared = _absolute_unresolved_clone_path(prepared, "prepared root")
    documents = _absolute_unresolved_clone_path(documents, "clone Documents")
    pending_ledger = _absolute_unresolved_clone_path(pending_ledger, "pending clone ledger")
    work_root = _absolute_unresolved_clone_path(work_root, "Simulator work root")
    diagnostics_root = _absolute_unresolved_clone_path(
        diagnostics_root, "clone metadata diagnostics root")
    if label not in {"D0", "D1", "D2", "D3", "D4"}:
        fail("clone metadata diagnostics label is not closed")
    if runtime != "27.0":
        fail("clone metadata diagnostics require exact runtime 27.0")
    if pending_ledger.parent != work_root:
        fail("pending clone ledger must be a direct child of the trusted work root")
    if diagnostics_root != work_root / "clone-save-metadata-diagnostics":
        fail("clone metadata diagnostics root is not the exact trusted work-root child")
    for index, protected in enumerate((prepared, documents, work_root)):
        for other in (prepared, documents, work_root)[index + 1:]:
            if protected == other or is_within(protected, other) or is_within(other, protected):
                fail(f"prepared, Documents and work root must be pairwise disjoint: "
                     f"{protected} vs {other}")
    for protected in (prepared, documents, pending_ledger):
        if (diagnostics_root == protected or is_within(diagnostics_root, protected)
                or is_within(protected, diagnostics_root)):
            fail(f"clone metadata diagnostics root overlaps a protected artifact: {protected}")
    pinned = _read_clone_ledger(pending_ledger, finalized=False)
    work_root_fd = -1
    diagnostics_fd = -1
    documents_parent_fd = -1
    documents_fd = -1
    published: dict[str, object] | None = None
    published_identity: tuple[int, int] | None = None
    diagnostic_bound = False
    cleanup_states: list[dict[str, object]] = []
    chain_states: list[dict[str, object]] = []
    try:
        ledger = pinned["ledger"]
        expected_paths = ledger["paths"]
        if (expected_paths["prepared"] != str(prepared)
                or expected_paths["destination"] != str(documents)
                or expected_paths["pending_ledger"] != str(pending_ledger)):
            fail("clone metadata diagnostics paths differ from pending ledger")
        staged_manifest = _absolute_unresolved_clone_path(
            Path(expected_paths["staged_manifest"]), "staged manifest")
        if staged_manifest.parent != work_root:
            fail("staged manifest must be a direct child of the trusted work root")
        if (diagnostics_root == staged_manifest
                or is_within(diagnostics_root, staged_manifest)
                or is_within(staged_manifest, diagnostics_root)):
            fail("clone metadata diagnostics root overlaps the staged manifest")
        work_root_fd = _open_absolute_directory_nofollow(work_root, "Simulator work root")
        work_root_identity = _devino(os.fstat(work_root_fd))
        _directory_metadata(work_root_fd, "Simulator work root", child=True)
        _validate_transaction_parent_fd(
            work_root_fd, work_root,
            "Simulator work root before clone metadata diagnostics")
        if _devino(os.fstat(pinned["parent_fd"])) != work_root_identity:
            fail("pending clone ledger parent is not the pinned Simulator work root")
        diagnostics_fd = _open_or_create_private_diagnostics_root(
            work_root_fd, diagnostics_root, label)
        diagnostics_identity = _devino(os.fstat(diagnostics_fd))
        previous_flags, previous_binding, chain_states, _ = _read_clone_diagnostic_chain(
            diagnostics_fd, diagnostics_root, pinned, work_root, int(label[1:]))
        cleanup_states = _pin_diagnostic_cleanup_artifacts(work_root_fd, work_root, label)
        quickload_binding = (_bind_quickload_transients(cleanup_states)
                             if label == "D4" else None)
        root_error: str | None = None
        try:
            documents_parent_fd = _open_absolute_directory_nofollow(
                documents.parent, "clone metadata Documents parent")
            documents_fd = _open_child_directory_nofollow(
                documents_parent_fd, documents.name, "clone metadata Documents")
            _require_directory_rebound(documents_parent_fd, documents.name, documents_fd,
                                       "clone metadata Documents")
        except (GuardError, OSError) as error:
            root_error = f"{type(error).__name__}: {error}"
            if documents_fd >= 0:
                os.close(documents_fd)
                documents_fd = -1
        records: list[dict[str, object]] = []
        changed = root_error is not None
        for ledger_file in ledger["files"]:
            relative = safe_relative(ledger_file["path"])
            expected = _ledger_stat_fields(ledger_file["destination"])
            if root_error is None and documents_fd >= 0:
                first = _descriptor_relative_stat(documents_fd, relative,
                                                  f"clone metadata {label} first {relative}")
                second = _descriptor_relative_stat(documents_fd, relative,
                                                   f"clone metadata {label} second {relative}")
            else:
                first = {"error": root_error}
                second = {"error": root_error}
            first_diff = _stat_immutable_diff(expected, first)
            second_diff = _stat_immutable_diff(expected, second)
            race = first != second
            transition = "invalid"
            policy_error = None
            actual_flags = -1
            try:
                first_flags = _diagnostic_stat_flags(
                    first.get("flags") if isinstance(first, dict) else None,
                    f"{label} first {relative}")
                second_flags = _diagnostic_stat_flags(
                    second.get("flags") if isinstance(second, dict) else None,
                    f"{label} second {relative}")
                actual_flags = second_flags
                if first_flags != second_flags:
                    fail(f"{label} file flags raced: {relative}")
                if label == "D0" and second_flags != 0:
                    fail(f"D0 requires exact destination flags=0: {relative}")
                transition = _uf_tracked_transition(
                    previous_flags[ledger_file["path"]], second_flags,
                    f"{label} {relative}")
            except GuardError as error:
                policy_error = str(error)
            changed = (changed or bool(first_diff) or bool(second_diff) or race
                       or policy_error is not None)
            records.append({
                "path": ledger_file["path"], "expected": expected,
                "first": first, "second": second,
                "first_diff": first_diff, "second_diff": second_diff,
                "race": race, "previous_flags": previous_flags[ledger_file["path"]],
                "actual_flags": actual_flags, "transition": transition,
                "policy_error": policy_error,
            })
        snapshot = {
            "schema": CLONE_DIAGNOSTIC_SCHEMA,
            "diagnostic_only": True,
            "result": "diagnostic",
            "label": label,
            "sequence": int(label[1:]),
            "runtime": runtime,
            "policy": UF_TRACKED_POLICY,
            "paths": _diagnostic_paths(ledger, work_root, diagnostics_root),
            "ledger": _diagnostic_ledger_binding(pinned),
            "previous": previous_binding,
            "quickload": quickload_binding,
            "file_count": len(records),
            "files": records,
        }
        _revalidate_pinned(pinned, "pending clone ledger before diagnostic publication")
        published_identity, payload = _write_clone_stat_snapshot(
            diagnostics_fd, diagnostics_root, label, snapshot)
        published = _read_pinned_regular_at(
            diagnostics_fd, diagnostics_root / f"{label}.json",
            f"published clone metadata diagnostic {label}", private=True)
        if (published["identity"] != published_identity
                or published["data"] != payload
                or published["sha256"] != hashlib.sha256(payload).hexdigest()):
            fail(f"published clone metadata diagnostic {label} failed identity/content binding")
        _validate_clone_diagnostic_snapshot(
            snapshot, label=label, ledger=ledger,
            ledger_binding=_diagnostic_ledger_binding(pinned),
            paths=_diagnostic_paths(ledger, work_root, diagnostics_root),
            previous=previous_binding, previous_flags=previous_flags,
            require_clean=False)
        for state in chain_states:
            _revalidate_pinned(state, "prior clone metadata diagnostic chain boundary")
        _revalidate_pinned(published, f"published clone metadata diagnostic {label}")
        _revalidate_pinned(pinned, "pending clone ledger after diagnostic publication")
        _require_directory_rebound(work_root_fd, diagnostics_root.name, diagnostics_fd,
                                   "clone metadata diagnostics root after publication")
        if _devino(os.fstat(diagnostics_fd)) != diagnostics_identity:
            fail("clone metadata diagnostics root identity changed during publication")
        rebound_work_root_fd = _open_absolute_directory_nofollow(
            work_root, "Simulator work root final pathname rebind")
        try:
            if _devino(os.fstat(rebound_work_root_fd)) != work_root_identity:
                fail("Simulator work root pathname changed during diagnostic publication")
            _require_directory_rebound(rebound_work_root_fd, diagnostics_root.name,
                                       diagnostics_fd,
                                       "clone metadata diagnostics root final rebind")
        finally:
            os.close(rebound_work_root_fd)
        _revalidate_pinned(pinned, "pending clone ledger final diagnostic boundary")
        for state in chain_states:
            _revalidate_pinned(state, "prior clone metadata diagnostic final boundary")
        _revalidate_pinned(
            published, f"published clone metadata diagnostic {label} final boundary")
        if quickload_binding is not None:
            _revalidate_quickload_transients(
                cleanup_states, quickload_binding)
        diagnostic_bound = True
        if changed:
            _remove_pinned_cleanup_artifacts(cleanup_states, label)
            fail(f"clone metadata diagnostics {label} observed a ledger difference or stat race")
    except BaseException as original:
        cleanup_errors: list[str] = []
        if published_identity is not None and not diagnostic_bound and diagnostics_fd >= 0:
            try:
                _quarantine_owned_at(
                    diagnostics_fd, diagnostics_root, f"{label}.json", published_identity,
                    f"unbound clone metadata diagnostic {label}")
            except (OSError, GuardError) as cleanup:
                cleanup_errors.append(f"diagnostic rollback: {cleanup}")
        if cleanup_states:
            try:
                _remove_pinned_cleanup_artifacts(cleanup_states, label)
            except GuardError as cleanup:
                cleanup_errors.append(f"transient cleanup: {cleanup}")
        if cleanup_errors:
            fail(f"clone metadata diagnostic {label} failed and rollback was incomplete: "
                 f"{original}; cleanup={'; '.join(cleanup_errors)}")
        raise
    finally:
        if published is not None:
            _close_pinned(published)
        _close_pinned_many(chain_states)
        _close_pinned_many(cleanup_states)
        if documents_fd >= 0:
            os.close(documents_fd)
        if documents_parent_fd >= 0:
            os.close(documents_parent_fd)
        if diagnostics_fd >= 0:
            os.close(diagnostics_fd)
        if work_root_fd >= 0:
            os.close(work_root_fd)
        _close_pinned(pinned)


def _validate_staged_against_ledger(ledger: dict[str, object], staged_data: bytes) -> None:
    staged = _parse_staged_bytes(staged_data, "staged-files.tsv")
    expected = {record["path"]: (record["bytes"], record["sha256"])
                for record in ledger["files"]}
    if staged != expected or hashlib.sha256(staged_data).hexdigest() != ledger["staged_manifest_sha256"]:
        fail("staged-files.tsv does not exactly match clone ledger")


def _validate_destination_post_runtime(
        ledger: dict[str, object], mutable_save: str | None, *,
        diagnostic_flags: dict[str, int] | None = None,
        diagnostics: dict[str, object] | None = None,
        quickload: dict[str, object] | None = None,
) -> dict[str, object]:
    destination = Path(ledger["paths"]["destination"])
    expected_directories = ledger["directories"]
    parent_fd = _open_absolute_directory_nofollow(
        destination.parent, "post-runtime destination parent")
    try:
        parent_metadata = _directory_metadata(
            parent_fd, "post-runtime destination parent", child=False,
            allowed_xattrs=CORE_SIMULATOR_CONTAINER_XATTRS,
        )
    except BaseException:
        os.close(parent_fd)
        raise
    if parent_metadata != ledger["destination_parent"]:
        os.close(parent_fd)
        fail("clone destination parent identity/metadata changed during runtime")
    root_fd = -1

    def open_root(anchor_fd: int, label: str) -> int:
        descriptor = _open_child_directory_nofollow(
            anchor_fd, destination.name, f"{label} destination root")
        try:
            _require_directory_rebound(anchor_fd, destination.name, descriptor,
                                       f"{label} destination root")
            metadata = _directory_metadata(
                descriptor, f"{label} destination root", child=False)
            if metadata != expected_directories[0]["metadata"]:
                fail("clone destination root identity/metadata changed during runtime")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def read_state(descriptor_root: int, label: str) -> dict[str, object]:
        directory_paths = [record["path"] for record in expected_directories if record["path"]]
        directory_records = _directory_records(descriptor_root, directory_paths)
        if directory_records != expected_directories:
            fail("clone destination directory identity/metadata changed during runtime")
        post_files = []
        for record in ledger["files"]:
            relative = safe_relative(record["path"])
            parent_text = record["parent"]
            leaf_parent_fd = _open_relative_directory_nofollow(
                descriptor_root, parent_text,
                f"{label} destination parent {parent_text or '<root>'}")
            leaf_fd = -1
            try:
                try:
                    pre_open = _stat_fields(os.stat(
                        relative.name, dir_fd=leaf_parent_fd, follow_symlinks=False))
                    leaf_fd = os.open(relative.name, os.O_RDONLY
                                      | getattr(os, "O_NOFOLLOW", 0),
                                      dir_fd=leaf_parent_fd)
                except OSError as error:
                    fail(f"cannot nofollow-open {label} file {record['path']}: {error}")
                post_open = _stat_fields(os.fstat(leaf_fd))
                open_diff = _stat_diff(pre_open, post_open)
                if open_diff:
                    fail(f"{label} destination file {record['path']} changed between "
                         "descriptor-relative pre-open stat and immediate fstat: "
                         f"{_canonical_json(open_diff).decode('ascii').strip()}")
                metadata = _destination_file_metadata(
                    leaf_fd, f"{label} destination file {record['path']}", digest=True)
                _require_leaf_rebound(leaf_parent_fd, relative.name, leaf_fd,
                                      f"{label} destination file {record['path']}")
                repeated = _destination_file_metadata(
                    leaf_fd, f"{label} repeated destination file {record['path']}",
                    digest=True)
                if repeated != metadata:
                    fail(f"{label} destination file changed across descriptor-bound reads: "
                         f"{record['path']}")
            finally:
                try:
                    if leaf_fd >= 0:
                        os.close(leaf_fd)
                finally:
                    os.close(leaf_parent_fd)
            mutable = record["path"] == mutable_save
            if mutable:
                if (metadata["size"] <= 0
                        or metadata["dev"] != ledger["filesystems"]["destination_dev"]
                        or metadata["xattrs"] != record["destination"]["xattrs"]):
                    fail("selected mutable save metadata/content is invalid")
            else:
                immutable_expected = dict(record["destination"])
                immutable_expected["flags"] = metadata["flags"]
                if metadata != immutable_expected:
                    diff = _metadata_diff(immutable_expected, metadata)
                    fail(f"immutable staged file identity/metadata/content changed: "
                         f"{record['path']}; "
                         f"diff={_canonical_json(diff).decode('ascii').strip()}")
            if diagnostic_flags is None:
                previous_flags = None
                transition = "final-only"
            else:
                if record["path"] not in diagnostic_flags:
                    fail(f"D4 diagnostic file set omits {record['path']}")
                previous_flags = diagnostic_flags[record["path"]]
                transition = _uf_tracked_transition(
                    previous_flags, metadata["flags"],
                    f"D4->final destination {record['path']}")
            post_files.append({"path": record["path"], "mutable": mutable,
                               "destination": metadata,
                               "previous_flags": previous_flags,
                               "transition": transition})
        rebound_directories = _directory_records(descriptor_root, directory_paths)
        if rebound_directories != directory_records:
            fail("clone destination directories changed after leaf validation")
        if diagnostic_flags is not None and set(diagnostic_flags) != {
                record["path"] for record in ledger["files"]}:
            fail("D4 diagnostic file set differs from clone ledger")
        flag_counts = {
            "zero": sum(record["destination"]["flags"] == 0
                        for record in post_files),
            "tracked": sum(record["destination"]["flags"] == UF_TRACKED
                           for record in post_files),
        }
        digest_rows = [{"path": record["path"],
                        "dev": record["destination"]["dev"],
                        "ino": record["destination"]["ino"],
                        "flags": record["destination"]["flags"]}
                       for record in post_files]
        return {"destination_boundary": CLONE_DESTINATION_BOUNDARY,
                "mutable_save": mutable_save, "source_reverified": True,
                "runtime": "27.0", "policy": UF_TRACKED_POLICY,
                "continuity_mode": ("D0-D4" if diagnostic_flags is not None
                                    else "final-only"),
                "diagnostics": diagnostics, "quickload": quickload,
                "directories": rebound_directories, "files": post_files,
                "flag_counts": flag_counts,
                "flags_digest": hashlib.sha256(
                    _canonical_json(digest_rows)).hexdigest()}

    try:
        root_fd = open_root(parent_fd, "initial post-runtime")
        initial_state = read_state(root_fd, "initial post-runtime")
        rebound_parent_fd = _open_absolute_directory_nofollow(
            destination.parent, "final post-runtime destination parent")
        rebound_root_fd = -1
        try:
            rebound_parent_metadata = _directory_metadata(
                rebound_parent_fd, "final post-runtime destination parent", child=False,
                allowed_xattrs=CORE_SIMULATOR_CONTAINER_XATTRS,
            )
            if rebound_parent_metadata != parent_metadata:
                fail("clone destination parent pathname changed during final rebind")
            rebound_root_fd = open_root(rebound_parent_fd, "final post-runtime")
            final_state = read_state(rebound_root_fd, "final post-runtime")
            if final_state != initial_state:
                fail("clone destination complete state changed across final rebind")
            _require_directory_rebound(rebound_parent_fd, destination.name,
                                       rebound_root_fd, "final post-runtime destination root")
        finally:
            try:
                if rebound_root_fd >= 0:
                    os.close(rebound_root_fd)
            finally:
                os.close(rebound_parent_fd)
        final_parent_fd = _open_absolute_directory_nofollow(
            destination.parent, "destination parent final pathname rebind")
        try:
            if (_directory_metadata(final_parent_fd,
                                    "destination parent final pathname rebind", child=False,
                                    allowed_xattrs=CORE_SIMULATOR_CONTAINER_XATTRS)
                    != parent_metadata):
                fail("clone destination parent pathname changed at final boundary")
        finally:
            os.close(final_parent_fd)
        return initial_state
    finally:
        try:
            if root_fd >= 0:
                os.close(root_fd)
        finally:
            os.close(parent_fd)


def finalize_clone_ledger(prepared: Path, documents: Path, staged_manifest: Path,
                          pending_ledger: Path, final_ledger: Path, with_saves: bool,
                          mutable_save: str | None,
                          diagnostics_root: Path | None = None,
                          runtime: str = "27.0") -> None:
    prepared, documents, staged_manifest, pending_ledger = _clone_paths(
        prepared, documents, staged_manifest, pending_ledger,
    )
    final_ledger = _absolute_unresolved_clone_path(final_ledger, "final clone ledger")
    if (final_ledger.parent != pending_ledger.parent or final_ledger == pending_ledger
            or final_ledger == staged_manifest or final_ledger == prepared
            or is_within(final_ledger, prepared) or is_within(final_ledger, documents)):
        fail("final and pending clone ledgers require distinct names in one trusted parent")
    pinned_ledger: dict[str, object] | None = None
    pinned_staged: dict[str, object] | None = None
    final_identity: tuple[int, int] | None = None
    diagnostics_fd = -1
    diagnostic_states: list[dict[str, object]] = []
    quickload_transients: list[dict[str, object]] = []
    quickload_binding: dict[str, object] | None = None
    try:
        if runtime != "27.0":
            fail("clone finalization requires exact runtime 27.0")
        pinned_ledger = _read_clone_ledger(pending_ledger, finalized=False)
        pinned_staged = _read_pinned_regular(
            staged_manifest, "staged-files.tsv", private=True)
        try:
            os.stat("quickload-evidence", dir_fd=pinned_ledger["parent_fd"],
                    follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            quickload_transients = _pin_diagnostic_cleanup_artifacts(
                pinned_ledger["parent_fd"], pending_ledger.parent, "D4")
        ledger = pinned_ledger["ledger"]
        if (ledger["paths"] != {"prepared": str(prepared), "destination": str(documents),
                                "staged_manifest": str(staged_manifest),
                                "pending_ledger": str(pending_ledger)}
                or ledger["with_saves"] is not with_saves):
            fail("clone ledger paths/options differ from finalizer")
        _validate_staged_against_ledger(ledger, pinned_staged["data"])
        if _parent_metadata(staged_manifest, "staged-files.tsv") != ledger["artifact_parent"]:
            fail("clone artifact parent identity/metadata changed before finalization")
        importer = _import_retail_import()
        current_snapshot = json.loads(_canonical_json(importer.verify_prepared(prepared)))
        if current_snapshot != ledger["prepared_snapshot"]:
            fail("prepared source changed since clone staging")
        _validate_prepared_source_records(ledger, importer)
        mutable_relative = None
        if mutable_save is not None:
            if not with_saves:
                fail("mutable save requires --with-saves")
            validate_autoload_name(mutable_save)
            mutable_relative = (SAVES_ROOT / f"{mutable_save}.scop").as_posix()
            if mutable_relative not in {record["path"] for record in ledger["files"]}:
                fail("selected mutable save is absent from clone ledger")
        diagnostic_flags = None
        diagnostics_binding = None
        diagnostic_quickload_binding = None
        if diagnostics_root is not None:
            diagnostics_root = _absolute_unresolved_clone_path(
                diagnostics_root, "clone metadata diagnostics root")
            expected_root = pending_ledger.parent / "clone-save-metadata-diagnostics"
            if diagnostics_root != expected_root:
                fail("clone finalizer diagnostics root is not the exact work-root child")
            diagnostics_fd = _open_child_directory_nofollow(
                pinned_ledger["parent_fd"], diagnostics_root.name,
                "clone finalizer diagnostics root")
            _require_directory_rebound(
                pinned_ledger["parent_fd"], diagnostics_root.name, diagnostics_fd,
                "clone finalizer diagnostics root")
            _directory_metadata(
                diagnostics_fd, "clone finalizer diagnostics root", child=True)
            if set(os.listdir(diagnostics_fd)) != {f"D{index}.json" for index in range(5)}:
                fail("clone finalizer requires the exact D0-D4 diagnostic file set")
            (diagnostic_flags, _, diagnostic_states,
             diagnostic_quickload_binding) = _read_clone_diagnostic_chain(
                diagnostics_fd, diagnostics_root, pinned_ledger,
                pending_ledger.parent, 5)
            diagnostics_binding = {
                "root": str(diagnostics_root),
                "baseline_ledger": _diagnostic_ledger_binding(pinned_ledger),
                "final_record": _diagnostic_file_binding(diagnostic_states[-1], "D4"),
            }
        if diagnostics_root is not None:
            if diagnostic_quickload_binding is None:
                fail("clone finalizer D4 lacks its QuickLoad baseline")
            _revalidate_quickload_transients(
                quickload_transients, diagnostic_quickload_binding)
            quickload_binding = diagnostic_quickload_binding
        elif quickload_transients:
            quickload_binding = _bind_quickload_transients(quickload_transients)
        ledger["post_runtime"] = _validate_destination_post_runtime(
            ledger, mutable_relative, diagnostic_flags=diagnostic_flags,
            diagnostics=diagnostics_binding, quickload=quickload_binding)
        _validate_clone_ledger(ledger)
        _revalidate_pinned(pinned_staged, "staged-files.tsv")
        _revalidate_pinned(pinned_ledger, "pending clone ledger")
        for state in diagnostic_states:
            _revalidate_pinned(state, "clone metadata diagnostic finalization boundary")
        if quickload_binding is not None:
            _revalidate_quickload_transients(
                quickload_transients, quickload_binding)
        def validate_final_ledger_boundary(_phase: str) -> None:
            _revalidate_pinned(
                pinned_staged, "staged-files.tsv final-ledger publication boundary")
            _revalidate_pinned(
                pinned_ledger, "pending clone ledger final-ledger publication boundary")
            for state in diagnostic_states:
                _revalidate_pinned(
                    state, "clone metadata diagnostic final-ledger publication boundary")
            if quickload_binding is not None:
                _revalidate_quickload_transients(
                    quickload_transients, quickload_binding)

        final_identity = _atomic_publish_new(
            final_ledger, _canonical_json(ledger), "final clone ledger",
            ledger["artifact_parent"], validate_final_ledger_boundary)
        try:
            validate_final_ledger_boundary("post-completion")
        except BaseException:
            _quarantine_owned_path(
                final_ledger, final_identity, "final clone ledger after QuickLoad drift",
                ledger["artifact_parent"])
            final_identity = None
            raise
        try:
            if quickload_binding is not None:
                _revalidate_quickload_transients(
                    quickload_transients, quickload_binding)
            _quarantine_owned_path(
                pending_ledger, pinned_ledger["identity"],
                "pending clone ledger", ledger["artifact_parent"])
        except GuardError as original:
            try:
                _quarantine_owned_path(
                    final_ledger, final_identity, "final clone ledger",
                    ledger["artifact_parent"])
            except GuardError as cleanup:
                fail(f"pending clone ledger cleanup failed and final rollback failed: "
                     f"{original}; cleanup={cleanup}")
            raise
    except BaseException as original:
        if quickload_transients:
            try:
                _remove_pinned_cleanup_artifacts(
                    quickload_transients, "failed clone finalization")
            except GuardError as cleanup:
                fail(f"clone finalization failed and QuickLoad cleanup was incomplete: "
                     f"{original}; cleanup={cleanup}")
        raise
    finally:
        _close_pinned_many(quickload_transients)
        _close_pinned_many(diagnostic_states)
        if diagnostics_fd >= 0:
            os.close(diagnostics_fd)
        if pinned_staged is not None:
            _close_pinned(pinned_staged)
        if pinned_ledger is not None:
            _close_pinned(pinned_ledger)


def _validate_reserved_clone_report_fields(
        data: bytes, expected: dict[str, str],
        expected_quickload: dict[str, str] | None = None) -> None:
    try:
        lines = data.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as error:
        fail(f"retail report is not UTF-8: {error}")
    required = {**expected, **(expected_quickload or {})}
    observed: dict[str, list[str]] = {key: [] for key in required}
    for line in lines:
        key, separator, value = line.partition("=")
        if key.startswith(("clone_", "quickload_")) and key not in required:
            fail(f"retail report contains unknown reserved field: {key}")
        if separator and key in observed:
            observed[key].append(value)
    for key, value in required.items():
        if observed[key] != [value]:
            fail(f"retail report reserved clone field must occur exactly once with its "
                 f"bound value: {key}")


def _clone_report_fields(pinned_ledger: dict[str, object],
                         staged_sha256: str) -> dict[str, str]:
    ledger = pinned_ledger["ledger"]
    post = ledger["post_runtime"]
    if post is None:
        fail("clone report fields require a finalized v2 ledger")
    diagnostics = post["diagnostics"]
    if diagnostics is None:
        diagnostic_values = {
            "clone_diagnostics_root": "none",
            "clone_diagnostics_chain_path": "none",
            "clone_diagnostics_chain_dev": "none",
            "clone_diagnostics_chain_ino": "none",
            "clone_diagnostics_chain_bytes": "none",
            "clone_diagnostics_chain_sha256": "none",
        }
    else:
        final_record = diagnostics["final_record"]
        diagnostic_values = {
            "clone_diagnostics_root": diagnostics["root"],
            "clone_diagnostics_chain_path": final_record["path"],
            "clone_diagnostics_chain_dev": str(final_record["dev"]),
            "clone_diagnostics_chain_ino": str(final_record["ino"]),
            "clone_diagnostics_chain_bytes": str(final_record["bytes"]),
            "clone_diagnostics_chain_sha256": final_record["sha256"],
        }
    quickload = post["quickload"]
    if quickload is None:
        quickload_values = {
            "clone_quickload_contract": "none",
            "clone_quickload_semantic_sha256": "none",
            "clone_quickload_runtime": "none",
            "clone_quickload_simulator_uuid": "none",
            "clone_quickload_manifest_path": "none",
            "clone_quickload_manifest_dev": "none",
            "clone_quickload_manifest_ino": "none",
            "clone_quickload_manifest_bytes": "none",
            "clone_quickload_manifest_sha256": "none",
            "clone_quickload_report_fields_path": "none",
            "clone_quickload_report_fields_dev": "none",
            "clone_quickload_report_fields_ino": "none",
            "clone_quickload_report_fields_bytes": "none",
            "clone_quickload_report_fields_sha256": "none",
        }
    else:
        manifest, fields = quickload["manifest"], quickload["report_fields"]
        quickload_values = {
            "clone_quickload_contract": quickload["contract"],
            "clone_quickload_semantic_sha256": quickload["semantic_sha256"],
            "clone_quickload_runtime": quickload["runtime"],
            "clone_quickload_simulator_uuid": quickload["simulator_uuid"],
            "clone_quickload_manifest_path": manifest["path"],
            "clone_quickload_manifest_dev": str(manifest["dev"]),
            "clone_quickload_manifest_ino": str(manifest["ino"]),
            "clone_quickload_manifest_bytes": str(manifest["bytes"]),
            "clone_quickload_manifest_sha256": manifest["sha256"],
            "clone_quickload_report_fields_path": fields["path"],
            "clone_quickload_report_fields_dev": str(fields["dev"]),
            "clone_quickload_report_fields_ino": str(fields["ino"]),
            "clone_quickload_report_fields_bytes": str(fields["bytes"]),
            "clone_quickload_report_fields_sha256": fields["sha256"],
        }
    return {
        "stage_mode": "clone-required",
        "clone_ledger": str(pinned_ledger["path"]),
        "clone_ledger_dev": str(pinned_ledger["identity"][0]),
        "clone_ledger_ino": str(pinned_ledger["identity"][1]),
        "clone_ledger_bytes": str(pinned_ledger["size"]),
        "clone_ledger_sha256": pinned_ledger["sha256"],
        "staged_manifest_sha256": staged_sha256,
        "clone_destination_post_delete": "unavailable-container-deleted",
        "clone_runtime": post["runtime"],
        "clone_uf_tracked_policy": UF_TRACKED_POLICY["name"],
        "clone_uf_tracked_policy_sha256": hashlib.sha256(
            _canonical_json(UF_TRACKED_POLICY)).hexdigest(),
        "clone_uf_tracked_zero_count": str(post["flag_counts"]["zero"]),
        "clone_uf_tracked_tracked_count": str(post["flag_counts"]["tracked"]),
        "clone_uf_tracked_digest": post["flags_digest"],
        "clone_continuity_mode": post["continuity_mode"],
        "clone_validated_before_container_delete": "true",
        **diagnostic_values,
        **quickload_values,
    }


def print_clone_report_fields(ledger_path: Path, staged_manifest: Path) -> None:
    pinned_ledger: dict[str, object] | None = None
    pinned_staged: dict[str, object] | None = None
    try:
        pinned_ledger = _read_clone_ledger(ledger_path, finalized=True)
        pinned_staged = _read_pinned_regular(
            staged_manifest, "staged-files.tsv", private=True)
        _validate_staged_against_ledger(
            pinned_ledger["ledger"], pinned_staged["data"])
        fields = _clone_report_fields(pinned_ledger, pinned_staged["sha256"])
        for key, value in fields.items():
            print(f"{key}={value}")
        _revalidate_pinned(pinned_staged, "staged-files.tsv report fields")
        _revalidate_pinned(pinned_ledger, "final clone ledger report fields")
    finally:
        if pinned_staged is not None:
            _close_pinned(pinned_staged)
        if pinned_ledger is not None:
            _close_pinned(pinned_ledger)


def _pin_finalized_diagnostic_chain(
        pinned_ledger: dict[str, object]) -> dict[str, object] | None:
    ledger = pinned_ledger["ledger"]
    post = ledger["post_runtime"]
    if post is None or post["diagnostics"] is None:
        return None
    diagnostics = post["diagnostics"]
    root = _absolute_unresolved_clone_path(
        Path(diagnostics["root"]), "finalized clone diagnostics root")
    if root != (Path(ledger["paths"]["pending_ledger"]).parent
                / "clone-save-metadata-diagnostics"):
        fail("finalized diagnostic root is not the exact ledger-parent child")
    descriptor = -1
    states: list[dict[str, object]] = []
    try:
        descriptor = _open_child_directory_nofollow(
            pinned_ledger["parent_fd"], root.name,
            "finalized clone diagnostics root")
        _require_directory_rebound(
            pinned_ledger["parent_fd"], root.name, descriptor,
            "finalized clone diagnostics root")
        metadata = _directory_metadata(
            descriptor, "finalized clone diagnostics root", child=True)
        if set(os.listdir(descriptor)) != {f"D{index}.json" for index in range(5)}:
            fail("finalized clone diagnostics root has a non-exact D0-D4 file set")
        flags, _, states, quickload_binding = _read_clone_diagnostic_chain(
            descriptor, root, pinned_ledger, root.parent, 5,
            ledger_binding=diagnostics["baseline_ledger"])
        if (_diagnostic_file_binding(states[-1], "D4")
                != diagnostics["final_record"]):
            fail("finalized clone ledger D4 binding changed")
        expected_previous = {record["path"]: record["previous_flags"]
                             for record in post["files"]}
        if flags != expected_previous:
            fail("finalized clone ledger transitions differ from D4 flags")
        if quickload_binding != post["quickload"]:
            fail("finalized clone ledger QuickLoad binding differs from D4 baseline")
        return {"fd": descriptor, "root": root, "metadata": metadata,
                "states": states}
    except BaseException:
        _close_pinned_many(states)
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _revalidate_finalized_diagnostic_chain(binding: dict[str, object] | None,
                                           parent_fd: int) -> None:
    if binding is None:
        return
    _require_directory_rebound(
        parent_fd, binding["root"].name, binding["fd"],
        "finalized clone diagnostics root")
    if (_directory_metadata(
            binding["fd"], "finalized clone diagnostics root", child=True)
            != binding["metadata"]):
        fail("finalized clone diagnostics root metadata changed")
    if set(os.listdir(binding["fd"])) != {f"D{index}.json" for index in range(5)}:
        fail("finalized clone diagnostics file set changed")
    for state in binding["states"]:
        _revalidate_pinned(state, "finalized clone diagnostic publication boundary")


def _close_finalized_diagnostic_chain(binding: dict[str, object] | None) -> None:
    if binding is None:
        return
    try:
        _close_pinned_many(binding["states"])
    finally:
        os.close(binding["fd"])


def _pin_finalized_quickload_binding(
        pinned_ledger: dict[str, object]) -> dict[str, object] | None:
    quickload = pinned_ledger["ledger"]["post_runtime"]["quickload"]
    if quickload is None:
        return None
    _validate_quickload_binding(quickload)
    root = Path(pinned_ledger["ledger"]["paths"]["pending_ledger"]).parent
    expected_parent = root / "quickload-evidence"
    manifest_path = Path(quickload["manifest"]["path"])
    fields_path = Path(quickload["report_fields"]["path"])
    if manifest_path.parent != expected_parent or fields_path.parent != expected_parent:
        fail("finalized QuickLoad binding escapes its work-root child")
    states: list[dict[str, object]] = []
    try:
        states.append(_read_pinned_regular(
            manifest_path, "finalized pending QuickLoad manifest", private=True))
        states.append(_read_pinned_regular(
            fields_path, "finalized QuickLoad report fields", private=True))
        if _bind_quickload_transients(states) != quickload:
            fail("finalized QuickLoad files differ from clone-ledger binding")
        return {"states": states, "expected": quickload}
    except BaseException:
        _close_pinned_many(states)
        raise


def _close_finalized_quickload_binding(
        binding: dict[str, object] | None) -> None:
    if binding is not None:
        _close_pinned_many(binding["states"])


def _pin_clone_publication_dependencies(
        prepared: Path, ledger_path: Path, staged_manifest: Path,
) -> dict[str, object]:
    prepared = _absolute_unresolved_clone_path(prepared, "prepared root")
    ledger_path = _absolute_unresolved_clone_path(ledger_path, "final clone ledger")
    staged_manifest = _absolute_unresolved_clone_path(staged_manifest, "staged-files.tsv")
    pinned_ledger: dict[str, object] | None = None
    pinned_staged: dict[str, object] | None = None
    diagnostics_binding: dict[str, object] | None = None
    quickload_binding: dict[str, object] | None = None
    try:
        pinned_ledger = _read_clone_ledger(ledger_path, finalized=True)
        pinned_staged = _read_pinned_regular(
            staged_manifest, "staged-files.tsv", private=True)
        ledger = pinned_ledger["ledger"]
        if (ledger["paths"]["prepared"] != str(prepared)
                or ledger["paths"]["staged_manifest"] != str(staged_manifest)):
            fail("clone publication inputs differ from ledger paths")
        if _parent_metadata(staged_manifest, "staged-files.tsv") != ledger["artifact_parent"]:
            fail("clone artifact parent identity/metadata changed around publication")
        _validate_staged_against_ledger(ledger, pinned_staged["data"])
        importer = _import_retail_import()
        snapshot = json.loads(_canonical_json(importer.verify_prepared(prepared)))
        if snapshot != ledger["prepared_snapshot"]:
            fail("prepared source changed around report publication")
        _validate_prepared_source_records(ledger, importer)
        diagnostics_binding = _pin_finalized_diagnostic_chain(pinned_ledger)
        quickload_binding = _pin_finalized_quickload_binding(pinned_ledger)
        if quickload_binding is None:
            expected_quickload_fields = {}
        else:
            quickload_states = _quickload_states_by_name(
                quickload_binding["states"])
            validator = _import_quickload_evidence()
            expected_quickload_fields = validator.quickload_report_fields(
                quickload_states["manifest.pending.json"]["path"].with_name(
                    "manifest.json"),
                quickload_states["manifest.pending.json"]["data"])
        expected_fields = _clone_report_fields(
            pinned_ledger, pinned_staged["sha256"])
        binding = {
            "prepared": prepared, "ledger_path": ledger_path,
            "staged_manifest": staged_manifest,
            "ledger": pinned_ledger, "staged": pinned_staged,
            "diagnostics": diagnostics_binding,
            "quickload": quickload_binding,
            "expected_quickload_fields": expected_quickload_fields,
            "expected_fields": expected_fields,
        }
        pinned_ledger = None
        pinned_staged = None
        return binding
    except BaseException:
        if pinned_staged is not None:
            _close_pinned(pinned_staged)
        if pinned_ledger is not None:
            _close_pinned(pinned_ledger)
        _close_finalized_diagnostic_chain(diagnostics_binding)
        _close_finalized_quickload_binding(quickload_binding)
        raise


def _revalidate_clone_publication_dependencies(
        binding: dict[str, object], report_data: bytes,
        report_parent_fd: int, report_parent: Path,
        active_publication: dict[str, object] | None = None,
        quickload_manifest_name: str = "manifest.pending.json") -> None:
    ledger_path = binding["ledger_path"]
    staged_manifest = binding["staged_manifest"]
    pinned_ledger = binding["ledger"]
    pinned_staged = binding["staged"]
    _revalidate_finalized_diagnostic_chain(
        binding.get("diagnostics"), pinned_ledger["parent_fd"])
    if binding.get("quickload") is not None:
        _revalidate_quickload_transients(
            binding["quickload"]["states"], binding["quickload"]["expected"],
            manifest_name=quickload_manifest_name)
    _validate_transaction_parent_fd(
        pinned_ledger["parent_fd"], ledger_path.parent,
        "clone publication artifact parent",
        active_publication=active_publication)
    _validate_transaction_parent_fd(
        report_parent_fd, report_parent, "clone publication report parent",
        active_publication=active_publication)
    _revalidate_pinned(pinned_staged, "staged-files.tsv")
    _revalidate_pinned(pinned_ledger, "final clone ledger")
    ledger = pinned_ledger["ledger"]
    if _parent_metadata(staged_manifest, "staged-files.tsv") != ledger["artifact_parent"]:
        fail("clone artifact parent identity/metadata changed around publication")
    _validate_staged_against_ledger(ledger, pinned_staged["data"])
    importer = _import_retail_import()
    snapshot = json.loads(_canonical_json(importer.verify_prepared(binding["prepared"])))
    if snapshot != ledger["prepared_snapshot"]:
        fail("prepared source changed around report publication")
    _validate_prepared_source_records(ledger, importer)
    _validate_reserved_clone_report_fields(
        report_data, binding["expected_fields"],
        binding["expected_quickload_fields"])
    # Keep these adjacent and last: after this point callers may only bind the
    # already-validated publication state or return to the rename helper.
    _revalidate_pinned(pinned_staged, "staged-files.tsv final publication boundary")
    _revalidate_pinned(pinned_ledger, "final clone ledger final publication boundary")
    _revalidate_finalized_diagnostic_chain(
        binding.get("diagnostics"), pinned_ledger["parent_fd"])
    if binding.get("quickload") is not None:
        _revalidate_quickload_transients(
            binding["quickload"]["states"], binding["quickload"]["expected"],
            manifest_name=quickload_manifest_name)


def _close_clone_publication_dependencies(binding: dict[str, object]) -> None:
    try:
        _close_finalized_quickload_binding(binding.get("quickload"))
    finally:
        try:
            _close_finalized_diagnostic_chain(binding.get("diagnostics"))
        finally:
            try:
                _close_pinned(binding["staged"])
            finally:
                _close_pinned(binding["ledger"])


def validate_clone_publication(prepared: Path, ledger_path: Path, staged_manifest: Path,
                               report: Path, *, report_parent_fd: int | None = None,
                               active_publication: dict[str, object] | None = None) -> None:
    report = _absolute_unresolved_clone_path(report, "retail report")
    binding: dict[str, object] | None = None
    pinned_report: dict[str, object] | None = None
    owned_parent_fd = -1
    try:
        binding = _pin_clone_publication_dependencies(
            prepared, ledger_path, staged_manifest)
        if report_parent_fd is None:
            owned_parent_fd = _open_absolute_directory_nofollow(
                report.parent, "clone publication report parent")
            report_parent_fd = owned_parent_fd
        pinned_report = _read_pinned_regular_at(
            report_parent_fd, report, "retail report", private=True)
        _revalidate_clone_publication_dependencies(
            binding, pinned_report["data"], report_parent_fd, report.parent,
            active_publication)
        _revalidate_pinned(pinned_report, "retail report")
    finally:
        if pinned_report is not None:
            _close_pinned(pinned_report)
        if owned_parent_fd >= 0:
            os.close(owned_parent_fd)
        if binding is not None:
            _close_clone_publication_dependencies(binding)


def compare_staged(root: Path, staged_manifest: Path, required_manifest: Path, large_manifest: Path,
                   with_saves: bool, mutable_save: str | None = None) -> None:
    staged = parse_tree_manifest(staged_manifest)
    required = parse_required_archives(required_manifest)
    large = parse_large_files(large_manifest)
    if not staged:
        fail("staged-files.tsv is empty")
    mutable_relative: str | None = None
    if mutable_save is not None:
        if not with_saves:
            fail("mutable save requires --with-saves")
        validate_autoload_name(mutable_save)
        mutable_relative = (SAVES_ROOT / f"{mutable_save}.scop").as_posix()
        if mutable_relative not in staged:
            fail("selected mutable save is absent from staged manifest")
    for relative, (expected_size, expected_hash) in staged.items():
        relative_path = safe_relative(relative)
        if not allowed_retail_path(relative_path, with_saves=with_saves):
            fail(f"staged manifest contains a forbidden path: {relative}")
        candidate = root / relative_path
        if candidate.is_symlink() or not candidate.is_file():
            fail(f"staged file is missing or non-regular: {relative}")
        if relative == mutable_relative:
            if candidate.stat().st_size <= 0:
                fail(f"selected mutable save is empty: {relative}")
            continue
        if candidate.stat().st_size != expected_size or sha256(candidate) != expected_hash:
            fail(f"staged file integrity mismatch: {relative}")
    for relative, (expected_size, expected_hash) in required.items():
        if staged.get(relative) != (expected_size, expected_hash):
            fail(f"required archive is absent from staged manifest: {relative}")
    for relative, (expected_size, expected_hash) in large.items():
        if allowed_retail_path(safe_relative(relative), with_saves=with_saves):
            if staged.get(relative) != (expected_size, expected_hash):
                fail(f"large retail file is absent from staged manifest: {relative}")
            candidate = root / safe_relative(relative)
            if relative == mutable_relative:
                if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size <= 0:
                    fail(f"selected mutable large save is empty or non-regular: {relative}")
                continue
            if (candidate.is_symlink() or not candidate.is_file()
                    or candidate.stat().st_size != expected_size
                    or sha256(candidate) != expected_hash):
                fail(f"staged large file integrity mismatch: {relative}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    tree = commands.add_parser("tree-manifest")
    tree.add_argument("--root", required=True)
    tree.add_argument("--output", required=True)
    tree.add_argument("--exclude-top", action="append", default=[])
    compare = commands.add_parser("tree-compare")
    compare.add_argument("--root", required=True)
    compare.add_argument("--manifest", required=True)
    compare.add_argument("--exclude-top", action="append", default=[])
    single = commands.add_parser("file-manifest")
    single.add_argument("--file", required=True)
    single.add_argument("--output", required=True)
    single_compare = commands.add_parser("file-compare")
    single_compare.add_argument("--file", required=True)
    single_compare.add_argument("--manifest", required=True)
    relocate = commands.add_parser("relocate-prefix")
    relocate.add_argument("--root", required=True)
    relocate.add_argument("--source", required=True)
    relocate.add_argument("--replacement", required=True)
    cache = commands.add_parser("cmake-cache")
    cache.add_argument("--cache", required=True)
    cache.add_argument("--source", required=True)
    cache.add_argument("--prefix", required=True)
    cache.add_argument("--build", required=True)
    cache.add_argument("--repo", required=True)
    container = commands.add_parser("data-container")
    container.add_argument("--path", required=True)
    container.add_argument("--repo", required=True)
    container.add_argument("--backup", required=True)
    container.add_argument("--manifest", required=True)
    container.add_argument("--home", required=True)
    container.add_argument("--udid", required=True)
    binary = commands.add_parser("binary-contract")
    binary.add_argument("--binary", required=True)
    openal_configured = commands.add_parser("openal-configured")
    openal_configured.add_argument("--cache", required=True)
    openal_configured.add_argument("--prefix", required=True)
    openal_configured.add_argument("--project", required=True)
    openal_artifact = commands.add_parser("openal-artifact")
    openal_artifact.add_argument("--prefix", required=True)
    openal_artifact.add_argument("--binary", required=True)
    openal_runtime = commands.add_parser("openal-runtime-log")
    openal_runtime.add_argument("--log", required=True)
    launch = commands.add_parser("launch-proof")
    launch.add_argument("--timeout", type=float, required=True)
    launch.add_argument("--poll", type=float, required=True)
    launch.add_argument("--stdout", required=True)
    launch.add_argument("--stderr", required=True)
    launch.add_argument("--copied-log", required=True)
    launch.add_argument("--source-log", required=True)
    launch.add_argument("--screenshot", required=True)
    launch.add_argument("--udid", required=True)
    launch.add_argument("--bundle", required=True)
    launch.add_argument("--autoload-save")
    launch.add_argument("--snapshot-manifest", required=True)
    launch.add_argument("--navigation-script")
    launch.add_argument("--navigation-documents")
    launch.add_argument("--navigation-snapshot")
    launch.add_argument("--navigation-pre-report")
    launch.add_argument("--initial-pid")
    launch.add_argument("--recovery-screenshot")
    launch.add_argument("--capture-v2-parser")
    launch.add_argument("--capture-v2-root")
    launch.add_argument("--ui-capture-root")
    launch.add_argument("--ui-capture-run-uuid")
    launch.add_argument("--ui-capture-runtime")
    launch.add_argument("--ui-capture-renderer")
    launch.add_argument("--ui-capture-git-revision")
    launch.add_argument("--ui-capture-source-tree-sha256")
    launch.add_argument("--pid-output")
    finalize = commands.add_parser("finalize-log")
    finalize.add_argument("--source-log", required=True)
    finalize.add_argument("--copied-log", required=True)
    finalize.add_argument("--snapshot-manifest", required=True)
    finalize.add_argument("--proof-metadata", required=True)
    finalize.add_argument("--autoload-save")
    finalize.add_argument("--allow-canonical-quickload-marker", action="store_true")
    retail = commands.add_parser("retail-verify")
    retail.add_argument("--backup", required=True)
    retail.add_argument("--manifest", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--backup", required=True)
    stage.add_argument("--manifest", required=True)
    stage.add_argument("--repo", required=True)
    stage.add_argument("--destination", required=True)
    stage.add_argument("--output", required=True)
    stage.add_argument("--with-saves", action="store_true")
    clone_stage_parser = commands.add_parser("clone-stage")
    clone_stage_parser.add_argument("--prepared", required=True)
    clone_stage_parser.add_argument("--destination", required=True)
    clone_stage_parser.add_argument("--output", required=True)
    clone_stage_parser.add_argument("--pending-ledger", required=True)
    clone_stage_parser.add_argument("--with-saves", action="store_true")
    clone_finalize = commands.add_parser("clone-finalize")
    clone_finalize.add_argument("--prepared", required=True)
    clone_finalize.add_argument("--documents", required=True)
    clone_finalize.add_argument("--manifest", required=True)
    clone_finalize.add_argument("--staged-manifest", required=True)
    clone_finalize.add_argument("--pending-ledger", required=True)
    clone_finalize.add_argument("--ledger", required=True)
    clone_finalize.add_argument("--with-saves", action="store_true")
    clone_finalize.add_argument("--mutable-save")
    clone_finalize.add_argument("--diagnostics-root")
    clone_finalize.add_argument("--runtime", default="27.0")
    clone_stat = commands.add_parser("clone-stat-snapshot")
    clone_stat.add_argument("--prepared", required=True)
    clone_stat.add_argument("--documents", required=True)
    clone_stat.add_argument("--pending-ledger", required=True)
    clone_stat.add_argument("--work-root", required=True)
    clone_stat.add_argument("--diagnostics-root", required=True)
    clone_stat.add_argument("--label", required=True, choices=("D0", "D1", "D2", "D3", "D4"))
    clone_stat.add_argument("--runtime", default="27.0")
    clone_report = commands.add_parser("clone-report-fields")
    clone_report.add_argument("--ledger", required=True)
    clone_report.add_argument("--staged-manifest", required=True)
    clone_publish = commands.add_parser("clone-publication-verify")
    clone_publish.add_argument("--prepared", required=True)
    clone_publish.add_argument("--ledger", required=True)
    clone_publish.add_argument("--staged-manifest", required=True)
    clone_publish.add_argument("--report", required=True)
    staged = commands.add_parser("staged-compare")
    staged.add_argument("--root", required=True)
    staged.add_argument("--manifest", required=True)
    staged.add_argument("--required", required=True)
    staged.add_argument("--large", required=True)
    staged.add_argument("--with-saves", action="store_true")
    staged.add_argument("--mutable-save")
    generated = commands.add_parser("autoload-config")
    generated.add_argument("--documents", required=True)
    generated.add_argument("--name", required=True)
    generated.add_argument("--evidence", required=True)
    generated.add_argument("--manifest", required=True)
    generated.add_argument("--ios-autoinput", action="store_true",
                           help="write ios_autoinput 1 for the explicit Simulator UI workflow")
    generated.add_argument("--ios-diagnostics", action="store_true",
                           help="write ios_diagnostics 1 for the explicit capture-v2 workflow")
    generated.add_argument("--ui-captures", action="store_true",
                           help="explicitly permit diagnostics plus automatic input for native UI captures")
    generated.add_argument("--quickload-evidence", action="store_true",
                           help="write the isolated F5/F9 QuickSave/QuickLoad evidence config")
    state = commands.add_parser("selected-save-state")
    state.add_argument("--file", required=True)
    state.add_argument("--output", required=True)
    mutation = commands.add_parser("selected-save-mutation")
    mutation.add_argument("--file", required=True)
    mutation.add_argument("--before", required=True)
    mutation.add_argument("--after", required=True)
    mutation.add_argument("--report", required=True)
    report_prepare = commands.add_parser("prepare-report")
    report_prepare.add_argument("--destination", required=True)
    report_publish = commands.add_parser("publish-report")
    report_publish.add_argument("--source", required=True)
    report_publish.add_argument("--destination", required=True)
    report_publish.add_argument("--capture-manifest-state")
    report_publish.add_argument("--clone-prepared")
    report_publish.add_argument("--clone-ledger")
    report_publish.add_argument("--staged-manifest")
    capture_manifest_state = commands.add_parser("capture-manifest-state")
    capture_manifest_state.add_argument("--manifest", required=True)
    capture_manifest_state.add_argument("--output", required=True)
    capture_manifest_report = commands.add_parser("capture-manifest-report-fields")
    capture_manifest_report.add_argument("--state", required=True)
    capture_manifest_report.add_argument("--output", required=True)
    capture_manifest_verify = commands.add_parser("capture-manifest-verify")
    capture_manifest_verify.add_argument("--state", required=True)
    paths = commands.add_parser("paths")
    paths.add_argument("--repo", required=True)
    paths.add_argument("--backup", required=True)
    paths.add_argument("--manifest", required=True)
    paths.add_argument("--work-base", required=True)
    work = commands.add_parser("work-root")
    work.add_argument("--repo", required=True)
    work.add_argument("--backup", required=True)
    work.add_argument("--manifest", required=True)
    work.add_argument("--work-root", required=True)
    private_parent = commands.add_parser("private-parent")
    private_parent.add_argument("--path", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "tree-manifest":
            write_tree_manifest(resolved(args.root), Path(args.output), args.exclude_top)
        elif args.command == "tree-compare":
            compare_tree(resolved(args.root), resolved(args.manifest), args.exclude_top)
        elif args.command == "file-manifest":
            write_file_manifest(resolved(args.file), Path(args.output))
        elif args.command == "file-compare":
            compare_file(resolved(args.file), resolved(args.manifest))
        elif args.command == "relocate-prefix":
            relocate_prefix(resolved(args.root), resolved(args.source), resolved(args.replacement))
        elif args.command == "cmake-cache":
            validate_cmake_cache(
                resolved(args.cache), resolved(args.source), resolved(args.prefix),
                resolved(args.build), resolved(args.repo),
            )
        elif args.command == "data-container":
            validate_data_container(
                resolved(args.path), resolved(args.repo), resolved(args.backup),
                resolved(args.manifest), resolved(args.home), args.udid,
            )
        elif args.command == "binary-contract":
            validate_binary_contract(resolved(args.binary))
        elif args.command == "openal-configured":
            for key, value in validate_openal_configured(
                resolved(args.cache), resolved(args.prefix), resolved(args.project)
            ).items():
                print(f"{key}={value}")
        elif args.command == "openal-artifact":
            for key, value in validate_openal_artifact(
                resolved(args.prefix), resolved(args.binary)
            ).items():
                print(f"{key}={value}")
        elif args.command == "openal-runtime-log":
            for key, value in validate_openal_runtime_log(resolved(args.log)).items():
                print(f"{key}={value}")
        elif args.command == "launch-proof":
            if args.timeout <= 0 or not 0.01 <= args.poll <= 5.0:
                fail("launch timeout/poll values are outside safe bounds")
            autoload_save = validate_autoload_name(args.autoload_save) if args.autoload_save else None
            prove_launch(
                args.timeout, args.poll, Path(args.stdout), Path(args.stderr),
                Path(args.copied_log), Path(args.screenshot), Path(args.source_log),
                args.udid, args.bundle, autoload_save, Path(args.snapshot_manifest),
                (absolute_unresolved(args.navigation_script) if args.navigation_script else None),
                (absolute_unresolved(args.navigation_documents) if args.navigation_documents else None),
                (absolute_unresolved(args.navigation_snapshot) if args.navigation_snapshot else None),
                (absolute_unresolved(args.navigation_pre_report) if args.navigation_pre_report else None),
                (absolute_unresolved(args.initial_pid) if args.initial_pid else None),
                (absolute_unresolved(args.recovery_screenshot) if args.recovery_screenshot else None),
                (absolute_unresolved(args.capture_v2_parser) if args.capture_v2_parser else None),
                (absolute_unresolved(args.capture_v2_root) if args.capture_v2_root else None),
                (absolute_unresolved(args.ui_capture_root) if args.ui_capture_root else None),
                args.ui_capture_run_uuid, args.ui_capture_runtime, args.ui_capture_renderer,
                args.ui_capture_git_revision, args.ui_capture_source_tree_sha256,
                (absolute_unresolved(args.pid_output) if args.pid_output else None),
            )
        elif args.command == "finalize-log":
            autoload_save = validate_autoload_name(args.autoload_save) if args.autoload_save else None
            if args.allow_canonical_quickload_marker and autoload_save is None:
                fail("canonical QuickLoad marker exception requires --autoload-save")
            finalize_runtime_log(
                absolute_unresolved(args.source_log), absolute_unresolved(args.copied_log),
                absolute_unresolved(args.snapshot_manifest),
                absolute_unresolved(args.proof_metadata), autoload_save,
                allow_canonical_quickload_marker=args.allow_canonical_quickload_marker,
            )
        elif args.command == "retail-verify":
            validate_retail(resolved(args.backup), resolved(args.manifest))
        elif args.command == "stage":
            backup = resolved(args.backup)
            manifest = resolved(args.manifest)
            repo = resolved(args.repo)
            destination = Path(args.destination).expanduser()
            if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
                fail(f"staging destination must be absent or empty: {destination}")
            require_external(destination.resolve(strict=False), repo, backup, manifest)
            stage_retail(backup, manifest, destination, args.with_saves, Path(args.output))
        elif args.command == "clone-stage":
            clone_stage(absolute_unresolved(args.prepared), absolute_unresolved(args.destination),
                        absolute_unresolved(args.output), absolute_unresolved(args.pending_ledger),
                        args.with_saves)
        elif args.command == "clone-finalize":
            prepared = absolute_unresolved(args.prepared)
            manifest = absolute_unresolved(args.manifest)
            if manifest != prepared / "manifest":
                fail("clone ledger manifest must be the prepared manifest")
            finalize_clone_ledger(prepared, absolute_unresolved(args.documents),
                                  absolute_unresolved(args.staged_manifest),
                                  absolute_unresolved(args.pending_ledger),
                                  absolute_unresolved(args.ledger),
                                  args.with_saves, args.mutable_save,
                                  (absolute_unresolved(args.diagnostics_root)
                                   if args.diagnostics_root else None),
                                  args.runtime)
        elif args.command == "clone-stat-snapshot":
            clone_stat_snapshot(
                absolute_unresolved(args.prepared), absolute_unresolved(args.documents),
                absolute_unresolved(args.pending_ledger),
                absolute_unresolved(args.work_root),
                absolute_unresolved(args.diagnostics_root), args.label, args.runtime,
            )
        elif args.command == "clone-report-fields":
            print_clone_report_fields(
                absolute_unresolved(args.ledger),
                absolute_unresolved(args.staged_manifest))
        elif args.command == "clone-publication-verify":
            validate_clone_publication(
                absolute_unresolved(args.prepared), absolute_unresolved(args.ledger),
                absolute_unresolved(args.staged_manifest), absolute_unresolved(args.report),
            )
        elif args.command == "staged-compare":
            compare_staged(
                resolved(args.root), resolved(args.manifest), resolved(args.required),
                resolved(args.large), args.with_saves, args.mutable_save,
            )
        elif args.command == "autoload-config":
            documents = resolved(args.documents)
            evidence = Path(args.evidence).expanduser()
            manifest = Path(args.manifest).expanduser()
            write_autoload_config(documents, args.name, evidence, manifest,
                                  ios_diagnostics=args.ios_diagnostics,
                                  ios_autoinput=args.ios_autoinput,
                                  ui_captures=args.ui_captures,
                                  quickload_evidence=args.quickload_evidence)
        elif args.command == "selected-save-state":
            write_selected_save_state(resolved(args.file), Path(args.output).expanduser())
        elif args.command == "selected-save-mutation":
            compare_selected_save_state(
                resolved(args.file), resolved(args.before), Path(args.after).expanduser(),
                Path(args.report).expanduser(),
            )
        elif args.command == "prepare-report":
            payload = sys.stdin.buffer.read(CHUNK + 1)
            prepare_report(absolute_unresolved(args.destination), payload)
        elif args.command == "publish-report":
            publish_report(
                absolute_unresolved(args.source), absolute_unresolved(args.destination),
                (absolute_unresolved(args.capture_manifest_state)
                 if args.capture_manifest_state else None),
                (resolved(args.clone_prepared) if args.clone_prepared else None),
                (resolved(args.clone_ledger) if args.clone_ledger else None),
                (resolved(args.staged_manifest) if args.staged_manifest else None),
            )
        elif args.command == "capture-manifest-state":
            write_capture_manifest_state(
                absolute_unresolved(args.manifest), absolute_unresolved(args.output),
            )
        elif args.command == "capture-manifest-report-fields":
            write_capture_manifest_report_fields(
                absolute_unresolved(args.state), absolute_unresolved(args.output),
            )
        elif args.command == "capture-manifest-verify":
            validate_capture_manifest_state(absolute_unresolved(args.state))
        elif args.command == "paths":
            repo, backup, manifest, base = map(resolved, (args.repo, args.backup, args.manifest, args.work_base))
            require_not_inside(base, repo, backup, manifest)
        elif args.command == "work-root":
            repo, backup, manifest = map(resolved, (args.repo, args.backup, args.manifest))
            work = absolute_unresolved(args.work_root)
            require_external(work, repo, backup, manifest)
            validate_private_parent(work, "Simulator work root")
        elif args.command == "private-parent":
            validate_private_parent(
                absolute_unresolved(args.path), "private publication parent")
        else:
            fail("unknown command")
    except (GuardError, OPENAL_CONTRACT.ContractError, OSError, UnicodeError) as error:
        print(f"retail simulator guard: FAIL: {error}", file=sys.stderr)
        return 1
    print("retail simulator guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
