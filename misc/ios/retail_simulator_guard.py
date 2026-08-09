#!/usr/bin/env python3
"""Integrity and path guards for the isolated retail iOS Simulator workflow.

The runner deliberately keeps the policy here dependency-free: this module is
also used by regression fixtures on hosts without Xcode or CoreSimulator.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Iterable


OPENAL_CONTRACT_PATH = Path(__file__).with_name("openal_provider_contract.py")
OPENAL_CONTRACT_SPEC = importlib.util.spec_from_file_location(
    "openal_provider_contract", OPENAL_CONTRACT_PATH
)
if OPENAL_CONTRACT_SPEC is None or OPENAL_CONTRACT_SPEC.loader is None:
    raise RuntimeError(f"could not load OpenAL provider contract: {OPENAL_CONTRACT_PATH}")
OPENAL_CONTRACT = importlib.util.module_from_spec(OPENAL_CONTRACT_SPEC)
OPENAL_CONTRACT_SPEC.loader.exec_module(OPENAL_CONTRACT)


CHUNK = 1024 * 1024
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


def autoload_config(name: str, *, ios_autoinput: bool = False) -> bytes:
    validate_autoload_name(name)
    return (
        "keypress_on_start 0\n"
        "ios_diagnostics 0\n"
        f"ios_autoinput {1 if ios_autoinput else 0}\n"
        f"start server({name}/single/alife/load) client(localhost)\n"
    ).encode("ascii")


def write_new_regular(path: Path, data: bytes) -> None:
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
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size != len(data):
            fail(f"generated file is not exact regular content: {path}")
    finally:
        os.close(descriptor)


def write_autoload_config(documents: Path, name: str, evidence: Path, manifest: Path,
                          *, ios_autoinput: bool = False) -> None:
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
    contents = autoload_config(name, ios_autoinput=ios_autoinput)
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


def autoload_sync_complete(lines: list[str], name: str) -> tuple[bool, str | None]:
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
        mismatched = [index for index in any_save_success if index > start and not save_pattern.fullmatch(lines[index])]
        if mismatched:
            fail("autoload log contains a mismatched saved-game success marker")
        save = first_after(
            [index for index, line in enumerate(lines) if save_pattern.fullmatch(line)], start
        )
        if save is None:
            continue
        accepted = first_after(accepted_indices, save)
        if accepted is None:
            continue
        sync = first_after(sync_indices, accepted)
        memory = first_after(memory_indices, sync if sync is not None else accepted)
        if sync is None or memory is None:
            continue
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


def read_stable_regular_nofollow(path: Path, label: str) -> tuple[tuple[int, int], bytes]:
    """Read one final path component without following links or accepting races."""

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
        if not stat.S_ISREG(opened.st_mode):
            fail(f"{label} must be a regular non-symlink file")
        contents = bytearray()
        while block := os.read(descriptor, CHUNK):
            contents.extend(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
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


def copy_runtime_log_snapshot(source: Path, copied: Path,
                              previous: tuple[tuple[int, int], bytes] | None) -> tuple[tuple[int, int], bytes]:
    """Copy a monotonic, non-symlinked runtime-log snapshot.

    A later snapshot must retain the earlier byte prefix on the same inode.  A
    log rotation, truncation, or rewrite therefore fails closed rather than
    allowing a previously observed readiness sequence to stand on its own.
    """

    identity, data = read_stable_regular_nofollow(source, "runtime log")
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
        copy_runtime_log_snapshot(source_log, copied_log, previous_snapshot)
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
                         proof_metadata: Path, autoload_save: str | None) -> None:
    identity, previous_data, expected_level, expected_pid = parse_runtime_snapshot_manifest(
        snapshot_manifest, copied_log, autoload_save,
    )
    final_snapshot = copy_runtime_log_snapshot(
        source_log, copied_log, (identity, previous_data),
    )
    final_ready, final_level = runtime_log_state(
        final_snapshot[1].decode("utf-8", errors="replace"), autoload_save,
        expected_pid,
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
                      expected_pid: int | None = None) -> tuple[bool, str | None]:
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
    ready, level = autoload_sync_complete(lines, autoload_save)
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
            snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot)
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
            snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot)
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
        snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot)
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
    snapshot = copy_runtime_log_snapshot(source_log, copied_log, snapshot)
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
                 recovery_screenshot: Path | None = None) -> None:
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
    if initial_pid_path is not None:
        write_new_regular(initial_pid_path, f"{pid}\n".encode("ascii"))

    deadline = time.monotonic() + timeout
    stability_window = min(1.0, max(0.1, poll))
    sync_complete_since: float | None = None
    sync_level: str | None = None
    log_snapshot: tuple[tuple[int, int], bytes] | None = None
    with stderr_path.open("ab") as stderr:
        while time.monotonic() < deadline:
            if source_log.exists() or source_log.is_symlink():
                log_snapshot = copy_runtime_log_snapshot(source_log, copied_log, log_snapshot)
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
                    shot = subprocess.run(
                        ("xcrun", "simctl", "io", udid, "screenshot", str(screenshot)),
                        stdout=stderr,
                        stderr=stderr,
                        check=False,
                    )
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
                    log_snapshot = copy_runtime_log_snapshot(source_log, copied_log, log_snapshot)
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
        if target_fd >= 0:
            os.close(target_fd)
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
    finalize = commands.add_parser("finalize-log")
    finalize.add_argument("--source-log", required=True)
    finalize.add_argument("--copied-log", required=True)
    finalize.add_argument("--snapshot-manifest", required=True)
    finalize.add_argument("--proof-metadata", required=True)
    finalize.add_argument("--autoload-save")
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
    state = commands.add_parser("selected-save-state")
    state.add_argument("--file", required=True)
    state.add_argument("--output", required=True)
    mutation = commands.add_parser("selected-save-mutation")
    mutation.add_argument("--file", required=True)
    mutation.add_argument("--before", required=True)
    mutation.add_argument("--after", required=True)
    mutation.add_argument("--report", required=True)
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
            )
        elif args.command == "finalize-log":
            autoload_save = validate_autoload_name(args.autoload_save) if args.autoload_save else None
            finalize_runtime_log(
                absolute_unresolved(args.source_log), absolute_unresolved(args.copied_log),
                absolute_unresolved(args.snapshot_manifest),
                absolute_unresolved(args.proof_metadata), autoload_save,
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
                                  ios_autoinput=args.ios_autoinput)
        elif args.command == "selected-save-state":
            write_selected_save_state(resolved(args.file), Path(args.output).expanduser())
        elif args.command == "selected-save-mutation":
            compare_selected_save_state(
                resolved(args.file), resolved(args.before), Path(args.after).expanduser(),
                Path(args.report).expanduser(),
            )
        elif args.command == "paths":
            repo, backup, manifest, base = map(resolved, (args.repo, args.backup, args.manifest, args.work_base))
            require_not_inside(base, repo, backup, manifest)
        elif args.command == "work-root":
            repo, backup, manifest, work = map(
                resolved, (args.repo, args.backup, args.manifest, args.work_root)
            )
            require_external(work, repo, backup, manifest)
        else:
            fail("unknown command")
    except (GuardError, OPENAL_CONTRACT.ContractError, OSError, UnicodeError) as error:
        print(f"retail simulator guard: FAIL: {error}", file=sys.stderr)
        return 1
    print("retail simulator guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
