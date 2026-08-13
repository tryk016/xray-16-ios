#!/usr/bin/env python3
"""Phase 0A test inventory and Phase 0B fail-open runtime telemetry.

The catalog remains offline and has no selection/cache authority.  Runtime
telemetry is observational: it receives a one-shot context over an inherited
file descriptor, writes private evidence only, and cannot alter a gate result.
"""
from __future__ import annotations

import argparse
import ast
import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = Path(__file__).with_name("test_feedback_catalog.json")
SCHEMA = "openxray.test-feedback-catalog.v1"
RUNTIME_SCHEMA = "openxray.test-feedback.v1"
PYTHON_COMMAND = re.compile(
    r'^\s*(?:feedback_stage\s+"stage::python::misc/ios/test_[^\"]+\.py"\s+)?'
    r"(?:python3|feedback_selected_python)\s+(misc/ios/test_(?!feedback\.py)[^\s\\]+\.py)"
)
HOST_FEEDBACK_TOOLING_PATH = "misc/ios/test_retail_test_profiles.py"
HOST_FEEDBACK_SUPPORT_PATH = "misc/ios/test_retail_fixture_contract.py"
CASE_RANGE = re.compile(r"^([A-Z]+)-\{index:02d\}$")
EXPECTED_BUILD_STAGE_EXCLUSIONS = frozenset({
    "build-stage::artifact-input-hash-after",
    "build-stage::artifact-input-hash-before",
    "build-stage::artifact-platform-debug-verification",
    "build-stage::bundle-hash",
    "build-stage::cmake-config-hash",
    "build-stage::cmake-configure",
    "build-stage::cpp-policy-compilation",
    "build-stage::dependency-discovery",
    "build-stage::dependency-discovery-linux",
    "build-stage::gate-hash-helper",
    "build-stage::glslang-realpath-probe",
    "build-stage::glslang-toolchain-probe",
    "build-stage::host-compiler-probe",
    "build-stage::input-hashing",
    "build-stage::python-version-probe",
    "build-stage::release-engine-build",
    "build-stage::resource-sync",
    "build-stage::stamp-publication",
})


class CatalogError(RuntimeError):
    pass


# Runtime is deliberately observational.  Selection is computed once by the
# parent and frozen in selection.json.  Writers never import test modules,
# enumerate shaders, spawn subprocesses, or make selection/cache decisions.
_ENV_DIR = "OPENXRAY_TEST_FEEDBACK_DIR"
_ENV_NONCE = "OPENXRAY_TEST_FEEDBACK_NONCE"
_ENV_RUN = "OPENXRAY_TEST_FEEDBACK_RUN_ID"
_ENV_PROFILE = "OPENXRAY_TEST_FEEDBACK_PROFILE"
_ENV_CONTEXT_FD = "XRAY_FEEDBACK_CONTEXT_FD"
_RAW_EVENT_SCHEMA = "openxray.test-feedback.raw.v1"
_MAX_RAW_EVENT_BYTES = 1 << 20
_MAX_RAW_EVENT_COUNT = 4096
_RESULTS = frozenset({"PASS", "FAIL", "ERROR", "SKIP", "TIMEOUT", "PLATFORM_SKIP"})
_CACHE_ROOT = ROOT / "build/ios-engine-iphoneos/.ios_gate_cache"
_CACHE_OUTPUT_TEMPLATE = "{cache_root}/{stage}/{key}.out"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def _sha256_regular_file(path: Path) -> str:
    """Hash one descriptor-bound, non-symlink regular file."""
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise CatalogError("runtime input is not a regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise CatalogError("runtime input identity changed")
        value = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1 << 20)
            if not block:
                break
            value.update(block)
        finished = os.fstat(descriptor)
        rebound = path.lstat()
        if (not _same_file_snapshot(opened, finished)
                or not _same_file_snapshot(opened, rebound)):
            raise CatalogError("runtime input changed while hashing")
        return value.hexdigest()
    finally:
        os.close(descriptor)


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (_same_identity(left, right) and left.st_size == right.st_size
            and left.st_mtime_ns == right.st_mtime_ns)


def _validate_directory_stat(detail: os.stat_result, *, private: bool) -> None:
    if stat.S_ISLNK(detail.st_mode) or not stat.S_ISDIR(detail.st_mode):
        raise CatalogError("runtime directory chain is unsafe")
    if private:
        if detail.st_uid != os.getuid() or stat.S_IMODE(detail.st_mode) != 0o700:
            raise CatalogError("runtime directory must be owner-bound mode 0700")
    elif detail.st_uid not in {0, os.getuid()} or stat.S_IMODE(detail.st_mode) & 0o022:
        raise CatalogError("runtime directory chain ownership/mode is unsafe")


def _open_directory_at(parent_fd: int, name: str, *, private: bool) -> int:
    """Open one directory component and bind both lookup names to its inode."""
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    _validate_directory_stat(named, private=private)
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        opened = os.fstat(child)
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        _validate_directory_stat(opened, private=private)
        _validate_directory_stat(rebound, private=private)
        if not _same_identity(named, opened) or not _same_identity(opened, rebound):
            raise CatalogError("runtime directory identity changed")
        return child
    except Exception:
        os.close(child)
        raise


def _open_private_directory(path: Path) -> int:
    """Open an absolute, symlink-free, owner-bound private directory chain."""
    path = Path(path)
    if not path.is_absolute():
        raise CatalogError("runtime directory must be absolute")
    path = Path(os.path.abspath(path))
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = path.parts[1:]
        if not parts:
            raise CatalogError("runtime directory cannot be a filesystem root")
        for index, component in enumerate(parts):
            child = _open_directory_at(descriptor, component, private=index == len(parts) - 1)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_safe_directory(path: Path) -> int:
    """Open an absolute non-writable directory chain with name/inode binding."""
    path = Path(path)
    if not path.is_absolute():
        raise CatalogError("runtime directory must be absolute")
    normalized = Path(os.path.abspath(path))
    parts = normalized.parts[1:]
    descriptor = os.open(normalized.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts:
            child = _open_directory_at(descriptor, component, private=False)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _snapshot_regular_file(path: Path) -> tuple[str, os.stat_result]:
    """Hash a regular file through a bound parent and return its fixed identity."""
    absolute = Path(os.path.abspath(path))
    parent_fd = _open_safe_directory(absolute.parent)
    descriptor = -1
    try:
        named = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        if (stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode)
                or named.st_uid != os.getuid()):
            raise CatalogError("cache output is not an owner-bound regular file")
        descriptor = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        rebound = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_identity(named, opened) or not _same_identity(opened, rebound):
            raise CatalogError("cache output identity changed")
        value = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1 << 20)
            if not block:
                break
            value.update(block)
        finished = os.fstat(descriptor)
        final_rebound = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        if (not _same_file_snapshot(opened, finished)
                or not _same_file_snapshot(opened, final_rebound)):
            raise CatalogError("cache output changed while hashing")
        return value.hexdigest(), opened
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _open_child_directory(parent_fd: int, name: str) -> int:
    if not re.fullmatch(r"[A-Za-z0-9-]+", name):
        raise CatalogError("invalid runtime child directory name")
    return _open_directory_at(parent_fd, name, private=True)


def _create_private_runtime_directory(path: Path) -> int:
    """Create only the final runtime root through an already-bound parent."""
    path = Path(path)
    if not path.is_absolute():
        raise CatalogError("runtime directory must be absolute")
    normalized = Path(os.path.abspath(path))
    parts = normalized.parts[1:]
    if not parts:
        raise CatalogError("runtime directory cannot be a filesystem root")
    parent_fd = os.open(normalized.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            child = _open_directory_at(parent_fd, component, private=False)
            os.close(parent_fd)
            parent_fd = child
        os.mkdir(parts[-1], 0o700, dir_fd=parent_fd)
        root_fd = _open_directory_at(parent_fd, parts[-1], private=True)
        return root_fd
    finally:
        os.close(parent_fd)


def _create_private_child(parent_fd: int, name: str) -> int:
    if not re.fullmatch(r"[A-Za-z0-9-]+", name):
        raise CatalogError("invalid runtime child directory name")
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    return _open_directory_at(parent_fd, name, private=True)


def _publish_json_at(directory_fd: int, name: str, value: dict[str, Any]) -> None:
    """Descriptor-bound O_EXCL pending -> no-clobber link transaction."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.json", name) or name.startswith("."):
        raise CatalogError("invalid runtime record name")
    pending = f".{name}.{os.getpid()}.{secrets.token_hex(12)}.pending"
    descriptor = -1
    published = False
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        pending_named = os.stat(pending, dir_fd=directory_fd, follow_symlinks=False)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or not _same_identity(opened, pending_named)):
            raise CatalogError("runtime pending record is unsafe")
        data = (canonical(value) + "\n").encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "zero-length runtime write")
            offset += written
        os.fsync(descriptor)
        finished = os.fstat(descriptor)
        pending_rebound = os.stat(pending, dir_fd=directory_fd, follow_symlinks=False)
        if (not _same_file_snapshot(finished, pending_rebound)
                or not _same_identity(opened, finished)
                or finished.st_size != len(data)
                or finished.st_uid != os.getuid()
                or stat.S_IMODE(finished.st_mode) != 0o600):
            raise CatalogError("runtime pending record changed while writing")
        os.close(descriptor)
        descriptor = -1
        os.link(pending, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                follow_symlinks=False)
        final_named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        pending_linked = os.stat(pending, dir_fd=directory_fd, follow_symlinks=False)
        if (not _same_file_snapshot(finished, final_named)
                or not _same_file_snapshot(finished, pending_linked)
                or not stat.S_ISREG(final_named.st_mode)
                or final_named.st_uid != os.getuid()
                or stat.S_IMODE(final_named.st_mode) != 0o600):
            raise CatalogError("runtime final record identity changed")
        os.unlink(pending, dir_fd=directory_fd)
        final_rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_file_snapshot(finished, final_rebound):
            raise CatalogError("runtime final record changed after publication")
        os.fsync(directory_fd)
        published = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(pending, dir_fd=directory_fd)
            except OSError:
                pass


def _read_json_at(directory_fd: int, name: str) -> dict[str, Any]:
    named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (not stat.S_ISREG(named.st_mode) or stat.S_ISLNK(named.st_mode)
            or named.st_uid != os.getuid() or stat.S_IMODE(named.st_mode) != 0o600):
        raise CatalogError("runtime record mode/type is unsafe")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (not _same_identity(opened, named) or not _same_identity(opened, rebound)
                or opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) != 0o600):
            raise CatalogError("runtime record identity changed")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1 << 20)
            if not block:
                break
            chunks.append(block)
        finished = os.fstat(descriptor)
        final_rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (not _same_file_snapshot(opened, finished)
                or not _same_file_snapshot(opened, final_rebound)):
            raise CatalogError("runtime record changed while reading")
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError("runtime record is corrupt JSON") from error
    if raw != (canonical(value) + "\n").encode("utf-8"):
        raise CatalogError("runtime record is not canonical JSON")
    if not isinstance(value, dict):
        raise CatalogError("runtime record is not an object")
    return value


def _list_final_json(directory_fd: int) -> tuple[list[str], bool, bool]:
    """Return canonical event names and flag incomplete or foreign contents.

    Event directories are private append-only journals.  Treating an unknown
    filename as invisible would let a completed-looking trace mask a writer or
    filesystem failure, so every non-record name is evidence of corruption.
    """
    names = os.listdir(directory_fd)
    pending = any(name.startswith(".") or name.endswith(".pending") for name in names)
    finals = sorted(name for name in names if name.endswith(".json") and not name.startswith("."))
    foreign = any(name not in finals and not name.startswith(".") for name in names)
    return finals, pending, foreign


def _runtime_context(context: dict[str, str] | None = None) -> dict[str, str]:
    if context is not None:
        return dict(context)
    marker = os.environ.pop(_ENV_CONTEXT_FD, None)
    for key in (_ENV_DIR, _ENV_NONCE, _ENV_RUN, _ENV_PROFILE):
        os.environ.pop(key, None)
    descriptor = -1
    try:
        if marker is None or not re.fullmatch(r"[3-9][0-9]*", marker):
            raise CatalogError("runtime context descriptor is invalid")
        descriptor = int(marker)
        details = os.fstat(descriptor)
        if not (stat.S_ISREG(details.st_mode) or stat.S_ISFIFO(details.st_mode)):
            raise CatalogError("runtime context descriptor is not a regular file or closed pipe")
        if stat.S_ISREG(details.st_mode) and details.st_size > 16384:
            raise CatalogError("runtime context is too large")
        chunks: list[bytes] = []
        remaining = 16384
        while True:
            if stat.S_ISFIFO(details.st_mode):
                ready, _, _ = select.select([descriptor], [], [], 0.25)
                if not ready:
                    raise CatalogError("runtime context pipe did not close promptly")
            block = os.read(descriptor, min(4096, remaining + 1))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
            if remaining < 0:
                raise CatalogError("runtime context is too large")
        os.close(descriptor)
        descriptor = -1
        raw = b"".join(chunks)
        if not raw.endswith(b"\n") or b"\r" in raw:
            raise CatalogError("runtime context wire format is invalid")
        lines = raw[:-1].split(b"\n")
        if len(lines) != 4 or any(not line for line in lines):
            raise CatalogError("runtime context requires four non-empty lines")
        directory, nonce, run_id, profile = (line.decode("utf-8") for line in lines)
        return {"directory": directory, "nonce": nonce, "run_id": run_id, "profile": profile}
    except Exception:
        try:
            if descriptor >= 0:
                os.close(descriptor)
        except OSError:
            pass
        return {"directory": "", "nonce": "", "run_id": "", "profile": ""}


def _selection_payload_hash(value: dict[str, Any]) -> str:
    copy = dict(value)
    expected = copy.pop("selection_sha256", None)
    copy.pop("auth_tag", None)
    actual = _hash_bytes((canonical(copy) + "\n").encode("utf-8"))
    if expected is not None and expected != actual:
        raise CatalogError("selection snapshot hash mismatch")
    return actual


def _normalized_runtime_path(directory: str | Path) -> str:
    """Return the stable, non-resolving path bound into a runtime HMAC key."""
    return os.path.normpath(os.path.abspath(os.fspath(directory)))


def _runtime_auth_key(context: dict[str, str]) -> bytes:
    """Derive a per-runtime signing key without persisting the nonce anywhere."""
    nonce = context.get("nonce")
    run_id = context.get("run_id")
    profile = context.get("profile")
    directory = context.get("directory")
    if not all(isinstance(item, str) and item for item in (nonce, run_id, profile, directory)):
        raise CatalogError("runtime authentication context is incomplete")
    bound = "\0".join((RUNTIME_SCHEMA, run_id, profile, _normalized_runtime_path(directory))).encode("utf-8")
    return hmac.new(nonce.encode("utf-8"), b"openxray.test-feedback.v1/runtime-key\0" + bound,
                    hashlib.sha256).digest()


def _auth_tag(context: dict[str, str], domain: str, value: dict[str, Any]) -> str:
    """Domain-separate every persistent telemetry record's authentication tag."""
    if not isinstance(domain, str) or not domain or "\0" in domain:
        raise CatalogError("runtime authentication domain is invalid")
    body = dict(value)
    body.pop("auth_tag", None)
    payload = (canonical(body) + "\n").encode("utf-8")
    return hmac.new(_runtime_auth_key(context),
                    b"openxray.test-feedback.v1/record\0" + domain.encode("utf-8") + b"\0" + payload,
                    hashlib.sha256).hexdigest()


def _signed_record(context: dict[str, str], domain: str, value: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    if "auth_tag" in payload:
        raise CatalogError("runtime record already carries authentication")
    payload["auth_tag"] = _auth_tag(context, domain, payload)
    return payload


def _verify_record_auth(context: dict[str, str], domain: str, value: dict[str, Any]) -> None:
    tag = value.get("auth_tag")
    if not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{64}", tag):
        raise CatalogError("runtime record authentication is missing")
    expected = _auth_tag(context, domain, value)
    if not hmac.compare_digest(tag, expected):
        raise CatalogError("runtime record authentication failed")


def _validate_selection_snapshot(selection: dict[str, Any]) -> None:
    """Validate the immutable parent-produced index before any writer trusts it."""
    if (selection.get("schema") != RUNTIME_SCHEMA
            or selection.get("selection_authority") != "NONE"
            or selection.get("cache_authority") != "NONE"
            or not isinstance(selection.get("entrypoints"), list)
            or not isinstance(selection.get("cases"), list)
            or not isinstance(selection.get("stages"), list)
            or not isinstance(selection.get("cache_contracts"), list)):
        raise CatalogError("selection snapshot schema is invalid")
    for collection, key in ((selection["entrypoints"], "entrypoint_id"),
                            (selection["cases"], "test_id"),
                            (selection["stages"], "stage_id")):
        identifiers = [item.get(key) for item in collection if isinstance(item, dict)]
        if (len(identifiers) != len(collection)
                or any(not isinstance(item, str) or not item for item in identifiers)
                or len(identifiers) != len(set(identifiers))):
            raise CatalogError(f"selection snapshot has duplicate/invalid {key}")
    for collection in (selection["cases"], selection["stages"]):
        ordinals = [item.get("ordinal") for item in collection]
        if (ordinals != list(range(1, len(collection) + 1))
                or len(ordinals) != len(set(ordinals))):
            raise CatalogError("selection snapshot ordinals are invalid")
        for item in collection:
            required = ("parent_stage_id", "kind", "labels", "resources",
                        "timeout_seconds", "timeout_policy", "explicit_inputs",
                        "input_sha256", "input_mapping_complete")
            if (any(field not in item for field in required)
                    or item["timeout_policy"] != "report-only"
                    or not isinstance(item["labels"], list)
                    or not isinstance(item["resources"], list)
                    or not isinstance(item["explicit_inputs"], list)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["input_sha256"] or "")):
                raise CatalogError("selection snapshot metadata is invalid")
    case_ids = [item["test_id"] for item in selection["cases"]]
    stage_ids = [item["stage_id"] for item in selection["stages"]]
    if (selection.get("case_ids_sha256") != digest(case_ids)
            or selection.get("stage_ids_sha256") != digest(stage_ids)):
        raise CatalogError("selection snapshot inventory digest is invalid")
    selected_stage_ids = set(stage_ids)
    for item in selection["cases"]:
        if item["parent_stage_id"] not in selected_stage_ids:
            raise CatalogError("selected case lacks selected parent stage")
    expected_contract_stages = {
        "stage::shader::glsl-es", "stage::shader::link"
    } & selected_stage_ids
    contracts = {item.get("stage_id"): item for item in selection["cache_contracts"]
                 if isinstance(item, dict)}
    if len(contracts) != len(selection["cache_contracts"]) or set(contracts) != expected_contract_stages:
        raise CatalogError("selection cache contracts are invalid")
    for stage_id, contract in contracts.items():
        expected_prefix = "shader:" if stage_id.endswith("glsl-es") else "shader-link:"
        expected = [item["test_id"] for item in selection["cases"]
                    if item["test_id"].startswith(expected_prefix)]
        if (contract.get("catalog_sha256") != selection.get("catalog_sha256")
                or contract.get("catalog_schema") != SCHEMA
                or contract.get("runtime_schema") != RUNTIME_SCHEMA
                or contract.get("covered_ids") != expected
                or contract.get("covered_count") != len(expected)
                or contract.get("covered_sha256") != digest(expected)
                or contract.get("list_ids_sha256") != digest(expected)
                or not isinstance(contract.get("cache_root"), str)
                or not Path(contract["cache_root"]).is_absolute()
                or contract.get("output_template") != _CACHE_OUTPUT_TEMPLATE
                or not isinstance(contract.get("checker_path"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", contract.get("checker_sha256", ""))):
            raise CatalogError("selection cache contract metadata is invalid")


def _load_selection(context: dict[str, str]) -> tuple[int, dict[str, Any]]:
    if not all(context.get(key) for key in ("directory", "nonce", "run_id", "profile")):
        raise CatalogError("runtime context incomplete")
    root_fd = _open_private_directory(Path(context["directory"]))
    try:
        selection = _read_json_at(root_fd, "selection.json")
        _selection_payload_hash(selection)
        _validate_selection_snapshot(selection)
        _verify_record_auth(context, "selection", selection)
        if (selection.get("schema") != RUNTIME_SCHEMA
                or selection.get("run_id") != context["run_id"]
                or selection.get("profile") != context["profile"]
                or "nonce" in selection
                or selection.get("selection_authority") != "NONE"
                or selection.get("cache_authority") != "NONE"):
            raise CatalogError("selection authentication failed")
        return root_fd, selection
    except Exception:
        os.close(root_fd)
        raise


def _exact_target_argv(entrypoint: str, argv: list[str], cwd: Path) -> bool:
    """Accept only the command shapes the existing gate actually executes."""
    if cwd.resolve() != ROOT or not argv:
        return False
    try:
        target = Path(argv[0]).resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return False
    expected = {
        "shader::glsl-es": "misc/ios/shadercheck/glsl_es_check.py",
        "shader::link": "misc/ios/shadercheck/link_check.py",
    }.get(entrypoint, entrypoint.removeprefix("python::"))
    if target != expected:
        return False
    arguments = argv[1:]
    if entrypoint.startswith("python::"):
        return not arguments
    if entrypoint == "shader::link":
        return arguments == ["--strict"]
    if entrypoint == "shader::glsl-es":
        return (len(arguments) == 3 and arguments[0] == "--glslang"
                and arguments[1] and arguments[2] == "--strict")
    return False


def authenticate_target(context: dict[str, str], entrypoint: str,
                        argv: list[str] | None = None, cwd: Path | None = None) -> dict[str, Any] | None:
    """Authenticate an explicitly opted-in entrypoint against frozen selection."""
    try:
        observed_argv = list(sys.argv if argv is None else argv)
        observed_cwd = Path.cwd() if cwd is None else Path(cwd)
        if not _exact_target_argv(entrypoint, observed_argv, observed_cwd):
            return None
        root_fd, selection = _load_selection(context)
        os.close(root_fd)
        item = next((value for value in selection["entrypoints"]
                     if value["entrypoint_id"] == entrypoint), None)
        if item is None or not item["selected"]:
            return None
        return {"context": context, "entrypoint_id": entrypoint,
                "stage_id": f"stage::{entrypoint}", "kind": item["kind"]}
    except Exception:
        return None


def _path_digest(path: Path, cache: dict[str, str]) -> str:
    key = str(path)
    if key in cache:
        return cache[key]
    if path.is_symlink() or not path.exists():
        value = "missing-or-symlink"
    elif path.is_file():
        value = sha256_file(path)
    elif path.is_dir():
        records = []
        for child in sorted(path.rglob("*")):
            if child.is_symlink():
                raise CatalogError(f"input contains symlink: {child}")
            if child.is_file():
                records.append(f"{child.relative_to(path).as_posix()}\0{sha256_file(child)}")
        value = _hash_bytes(("\n".join(records) + "\n").encode("utf-8"))
    else:
        value = "unsupported"
    cache[key] = value
    return value


def _metadata(entry: dict[str, Any], cache: dict[str, str]) -> dict[str, Any]:
    inputs = []
    for item in entry["explicit_inputs"]:
        copy = dict(item)
        if item.get("type") == "path":
            copy["sha256"] = _path_digest(ROOT / item["value"], cache)
        inputs.append(copy)
    return {"kind": entry["kind"], "labels": entry["labels"], "resources": entry["resources"],
            "timeout_seconds": entry["timeout_seconds"], "timeout_policy": "report-only",
            "explicit_inputs": inputs,
            "input_sha256": _hash_bytes((canonical(inputs) + "\n").encode("utf-8")),
            "input_mapping_complete": entry["input_mapping_complete"]}


def runtime_profile_ids(profile: str) -> list[str]:
    groups = profile_ids()
    if profile not in {"engine", "shaders", "device", "full", "fast"}:
        raise CatalogError(f"unknown runtime profile: {profile}")
    unconditional, shader_only = parse_build_check_python()
    selected: list[str] = []
    for path in unconditional + ([] if profile == "engine" else shader_only):
        if path == HOST_FEEDBACK_TOOLING_PATH:
            selected.extend(host_feedback_tooling_ids(ROOT))
        else:
            selected.extend(parse_archive_generated_methods(ROOT / path)
                            if path.endswith("test_archive_completed_artifacts.py")
                            else parse_python_methods(ROOT / path))
    selected.extend(groups["cpp"])
    selected.extend(groups["shell"])
    if profile != "engine":
        selected.extend(groups["shader-baseline"] + groups["shader-variants"] + groups["shader-links"])
    if len(selected) != len(set(selected)):
        raise CatalogError("runtime selected stable IDs are not unique")
    return selected


def _case_entrypoint(identifier: str) -> str:
    if identifier.startswith("py:"):
        return "python::" + identifier[3:].split("::", 1)[0]
    if identifier.startswith("cpp:"):
        return identifier.split("@", 1)[0]
    if identifier.startswith("sh:"):
        return "shell::ui-log-oracle"
    if identifier.startswith("shader-link:"):
        return "shader::link"
    if identifier.startswith("shader:"):
        return "shader::glsl-es"
    raise CatalogError(f"case has no entrypoint: {identifier}")


def _stage_entrypoint(identifier: str, profile: str) -> str:
    """Return the sole producer allowed to record one selected stage."""
    if not identifier.startswith("stage::") and not identifier.startswith("build-stage::"):
        raise CatalogError(f"stage has no entrypoint: {identifier}")
    source = identifier.removeprefix("stage::")
    if source.startswith("python::") or source in {"shader::glsl-es", "shader::link", "shell::ui-log-oracle"}:
        return source
    if source.startswith("cpp:"):
        return source.split("@", 1)[0]
    # Validation and build stages are emitted only by their top-level gate.
    if source.startswith("validation::") or identifier.startswith("build-stage::"):
        return f"observer::{profile}"
    raise CatalogError(f"stage has no known producer: {identifier}")


def _stage_order(profile: str, catalog: dict[str, Any], root: Path = ROOT) -> list[str]:
    """Derive stage trace order directly from the executing shell wrappers.

    ``feedback_stage``, ``feedback_case`` and ``feedback_begin_manual_stage``
    are the single source of stage order.  Profile membership follows the same
    run_shaders/run_engine mode partition configured above the actual calls.
    No second hand-maintained sequence exists in Python.
    """
    if profile not in {"engine", "shaders", "device", "full", "fast"}:
        raise CatalogError(f"unknown runtime profile: {profile}")
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    markers: list[str] = []
    pattern = re.compile(r'\b(?:feedback_stage|feedback_case|feedback_begin_manual_stage)\s+"([^"]+)"')
    deferred: dict[str, list[str]] = {"run_openal_configured": [], "run_openal_artifact": []}
    active_function: str | None = None
    for line in text.splitlines():
        opened = re.match(r'^(run_openal_(?:configured|artifact))\(\)\s*\{', line)
        if opened:
            active_function = opened.group(1)
            continue
        if active_function is not None:
            for match in pattern.finditer(line):
                deferred[active_function].append(match.group(1))
            if line == "}":
                active_function = None
            continue
        invoked = re.match(r'^\s*(run_openal_(?:configured|artifact))\b', line)
        if invoked:
            markers.extend(deferred[invoked.group(1)])
            continue
        for match in pattern.finditer(line):
            markers.append(match.group(1))
        cache_call = re.match(
            r'^\s*run_shader_cache_stage (?:compile|link) "(stage::shader::(?:glsl-es|link))" ',
            line,
        )
        if cache_call:
            markers.append(cache_call.group(1))
    markers = [f"stage::{identifier}" if identifier.startswith("cpp:") else identifier
               for identifier in markers
               if identifier.startswith("cpp:") or identifier.startswith("stage::")
               or identifier.startswith("build-stage::")]
    if len(markers) != len(set(markers)):
        raise CatalogError("executing stage wrapper has duplicate marker")
    shader_stage = {"stage::shader::glsl-es", "stage::shader::link",
                    "stage::validation::shader::macro-contract"}
    shader_stage.update(item for item in markers if item.startswith("stage::python::misc/ios/test_")
                        and item.rsplit("/", 1)[-1] in {
                            "test_locator_registration_contract.py", "test_retail_simulator.py",
                            "test_shader_macro_contract.py", "test_shader_resource_contract.py"})
    engine_stage = {"build-stage::cmake-configure", "build-stage::release-engine-build",
                    "build-stage::resource-sync", "build-stage::artifact-platform-debug-verification",
                    "stage::validation::python::openal-configured",
                    "stage::validation::python::openal-artifact"}
    dual_stage = {"build-stage::artifact-input-hash-before", "build-stage::artifact-input-hash-after",
                  "build-stage::stamp-publication"}
    shader_guard = text.find('if [ "$run_shaders" = 1 ]; then')
    engine_guard = text.find('if [ "$run_engine" = 1 ]; then')
    dual_guards = [match.start() for match in re.finditer(
        r'if \[ "\$run_shaders" = 1 \] && \[ "\$run_engine" = 1 \]; then', text)]
    if shader_guard < 0 or engine_guard < 0 or len(dual_guards) != 2:
        raise CatalogError("executing stage profile guard drift")
    marker_positions = {match.group(1): match.start() for match in pattern.finditer(text)}
    for match in re.finditer(
            r'^\s*run_shader_cache_stage (?:compile|link) "(stage::shader::(?:glsl-es|link))" ',
            text, re.MULTILINE):
        marker_positions[match.group(1)] = match.start()
    for identifier in shader_stage:
        position = marker_positions.get(identifier)
        if position is None or not shader_guard < position < engine_guard:
            raise CatalogError("shader stage escaped its executing profile guard")
    for identifier in engine_stage - {"stage::validation::python::openal-configured",
                                      "stage::validation::python::openal-artifact"}:
        position = marker_positions.get(identifier)
        if position is None or not engine_guard < position < dual_guards[1]:
            raise CatalogError("engine stage escaped its executing profile guard")
    for identifier in dual_stage:
        position = marker_positions.get(identifier)
        if position is None or not (dual_guards[0] < position < shader_guard or position > dual_guards[1]):
            raise CatalogError("dual gate stage escaped its executing profile guard")
    result = []
    for identifier in markers:
        if identifier in shader_stage:
            selected = profile != "engine"
        elif identifier in engine_stage:
            selected = profile in {"engine", "device", "full", "fast"}
        elif identifier in dual_stage:
            selected = profile in {"device", "full", "fast"}
        else:
            selected = True
        if selected:
            result.append(identifier)
    if profile == "fast":
        # Read the actual FastDevice body in execution order.  Its artifact
        # hash precedes the nested shader gate; the remaining FastDevice
        # stages follow that nested trace.  Do not concatenate two independently
        # derived lists: that loses this interleaving and masks profile drift.
        fast_text = (root / "misc/ios/build_fast_device.sh").read_text(encoding="utf-8")
        anchor = '[ -d "$PREFIX_DIR" ]'
        if anchor not in fast_text or "feedback_run_nested_shaders || fail" not in fast_text:
            raise CatalogError("FastDevice executing profile anchor drift")
        body = fast_text[fast_text.index(anchor):]
        fast_markers: list[str] = []
        for line in body.splitlines():
            if "feedback_run_nested_shaders || fail" in line:
                fast_markers.extend(_stage_order("shaders", catalog, root))
                continue
            if re.match(r'^\s*run_openal_configured\b', line):
                fast_markers.append("stage::validation::python::openal-configured")
                continue
            if re.match(r'^\s*run_openal_artifact\b', line):
                fast_markers.append("stage::validation::python::openal-artifact")
                continue
            for match in re.finditer(r'\b(?:feedback_stage|feedback_begin_manual_stage)\s+"([^"]+)"', line):
                fast_markers.append(match.group(1))
        result = fast_markers
    if len(result) != len(set(result)):
        raise CatalogError("profile stage wrappers overlap")
    return result


def runtime_selection(profile: str, run_id: str, nonce: str,
                      directory: Path | str) -> dict[str, Any]:
    catalog = read_catalog()
    entries = {item["entrypoint_id"]: item for item in catalog["entrypoints"]}
    path_cache: dict[str, str] = {}
    entrypoints = []
    for item in catalog["entrypoints"]:
        selected = profile in item["selected_profiles"]
        entrypoints.append({"entrypoint_id": item["entrypoint_id"], "kind": item["kind"],
                            "selected": selected,
                            "reason": "selected by existing profile" if selected else "outside existing gate selection"})
    case_ids = runtime_profile_ids(profile)
    stages = _stage_order(profile, catalog)
    case_records = []
    for ordinal, identifier in enumerate(case_ids, 1):
        entrypoint = _case_entrypoint(identifier)
        meta = _metadata(entries[entrypoint], path_cache)
        parent = (f"stage::{identifier}" if identifier.startswith("cpp:")
                  else f"stage::{entrypoint}")
        case_records.append({"test_id": identifier, "ordinal": ordinal,
                             "parent_stage_id": parent, **meta})
    stage_records = []
    exclusions = {item["stage_id"]: item for item in catalog["build_stage_exclusions"]}
    for ordinal, identifier in enumerate(stages, 1):
        source = identifier.removeprefix("stage::")
        if source.startswith("cpp:"):
            source = source.split("@", 1)[0]
        if source in entries:
            meta = _metadata(entries[source], path_cache)
        else:
            classification = exclusions.get(identifier, {"classification": "build-stage"})
            inputs = [{"type": "path", "value": "misc/ios/build_check.sh",
                       "sha256": _path_digest(ROOT / "misc/ios/build_check.sh", path_cache)}]
            meta = {"kind": classification["classification"], "labels": ["build-stage"],
                    "resources": ["ios-build-tree"], "timeout_seconds": 1800,
                    "timeout_policy": "report-only", "explicit_inputs": inputs,
                    "input_sha256": _hash_bytes((canonical(inputs) + "\n").encode()),
                    "input_mapping_complete": False}
        stage_records.append({"stage_id": identifier, "ordinal": ordinal,
                              "parent_stage_id": None, **meta})
    build_classifications = []
    selected_build = set(stages) & EXPECTED_BUILD_STAGE_EXCLUSIONS
    for item in catalog["build_stage_exclusions"]:
        selected = item["stage_id"] in selected_build
        build_classifications.append({"stage_id": item["stage_id"], "selected": selected,
                                      "reason": "selected high-level stage" if selected
                                      else "classified but represented by parent stage or not applicable"})
    cache_contracts = []
    for stage_id, checker_relative, list_schema, prefix in (
            ("stage::shader::glsl-es", "misc/ios/shadercheck/glsl_es_check.py",
             "openxray.shader-case-list.v1", "shader:"),
            ("stage::shader::link", "misc/ios/shadercheck/link_check.py",
             "openxray.shader-link-list.v1", "shader-link:")):
        if stage_id in stages:
            covered = [item["test_id"] for item in case_records if item["test_id"].startswith(prefix)]
            cache_contracts.append({"stage_id": stage_id, "checker_path": checker_relative,
                                    "checker_sha256": _sha256_regular_file(ROOT / checker_relative),
                                    "catalog_sha256": sha256_file(CATALOG_PATH),
                                    "catalog_schema": SCHEMA, "runtime_schema": RUNTIME_SCHEMA,
                                    "list_schema": list_schema, "covered_ids": covered,
                                    "covered_count": len(covered), "covered_sha256": digest(covered),
                                    "list_ids_sha256": digest(covered),
                                    "cache_root": str(_CACHE_ROOT),
                                    "output_template": _CACHE_OUTPUT_TEMPLATE})
    base = {"schema": RUNTIME_SCHEMA, "run_id": run_id, "profile": profile,
            "selection_authority": "NONE", "cache_authority": "NONE",
            "catalog_sha256": sha256_file(CATALOG_PATH), "entrypoints": entrypoints,
            "build_stage_classifications": build_classifications,
            "cases": case_records, "stages": stage_records, "cache_contracts": cache_contracts,
            "case_ids_sha256": digest(case_ids), "stage_ids_sha256": digest(stages)}
    base["selection_sha256"] = _selection_payload_hash(base)
    return _signed_record({"directory": str(directory), "profile": profile,
                           "run_id": run_id, "nonce": nonce}, "selection", base)


def runtime_initialize(directory: Path, profile: str, run_id: str, nonce: str) -> bool:
    try:
        root_fd = _create_private_runtime_directory(directory)
        try:
            for child_name in ("case-events", "stage-events", "error-events"):
                child_fd = _create_private_child(root_fd, child_name)
                os.close(child_fd)
            selection = runtime_selection(profile, run_id, nonce, directory)
            _publish_json_at(root_fd, "selection.json", selection)
        finally:
            os.close(root_fd)
        return True
    except Exception:
        return False


def _error_marker(context: dict[str, str], operation: str, error: BaseException) -> None:
    try:
        root_fd = _open_private_directory(Path(context["directory"]))
        try:
            error_fd = _open_child_directory(root_fd, "error-events")
            try:
                payload = {"schema": RUNTIME_SCHEMA, "run_id": context.get("run_id"),
                           "profile": context.get("profile"), "operation": operation,
                           "error": type(error).__name__, "monotonic_ns": time.monotonic_ns()}
                _publish_json_at(error_fd, f"error-{time.monotonic_ns()}-{secrets.token_hex(6)}.json",
                                 _signed_record(context, "error-event", payload))
            finally:
                os.close(error_fd)
        finally:
            os.close(root_fd)
    except Exception:
        pass


def runtime_event(identifier: str, result: str, started_ns: int, *, entrypoint_id: str,
                  kind: str = "case",
                  exit_code: int | None = None, cache_hit: bool | None = None,
                  parent_stage_id: str | None = None, detail: str | None = None,
                  ended_ns: int | None = None,
                  context: dict[str, str] | None = None) -> bool:
    runtime = _runtime_context(context)
    try:
        if result not in _RESULTS or kind not in {"case", "stage"}:
            raise CatalogError("invalid runtime result/kind")
        root_fd, selection = _load_selection(runtime)
        try:
            key = "test_id" if kind == "case" else "stage_id"
            table = selection["cases" if kind == "case" else "stages"]
            selected = next((item for item in table if item[key] == identifier), None)
            if selected is None:
                raise CatalogError("event is outside immutable selection")
            expected_origin = (_case_entrypoint(identifier) if kind == "case"
                               else _stage_entrypoint(identifier, runtime["profile"]))
            if entrypoint_id != expected_origin:
                raise CatalogError("event origin does not own selected identifier")
            bucket_fd = _open_child_directory(root_fd, "case-events" if kind == "case" else "stage-events")
            try:
                ended = time.monotonic_ns() if ended_ns is None else ended_ns
                recorded = time.monotonic_ns()
                if (not isinstance(started_ns, int) or not isinstance(ended, int)
                        or started_ns < 0 or ended < started_ns or recorded < ended):
                    raise CatalogError("runtime event timing is invalid")
                payload = {"schema": RUNTIME_SCHEMA, "run_id": runtime["run_id"],
                           "profile": runtime["profile"], key: identifier,
                           "selection_authority": "NONE", "cache_authority": "NONE",
                           "parent_stage_id": selected["parent_stage_id"], "ordinal": selected["ordinal"],
                           "kind": selected["kind"], "labels": selected["labels"],
                           "resources": selected["resources"], "timeout_seconds": selected["timeout_seconds"],
                           "timeout_policy": "report-only", "explicit_inputs": selected["explicit_inputs"],
                           "input_sha256": selected["input_sha256"],
                           "input_mapping_complete": selected["input_mapping_complete"],
                           "selection_sha256": selection["selection_sha256"],
                           "started_monotonic_ns": started_ns, "ended_monotonic_ns": ended,
                           "recorded_monotonic_ns": recorded,
                           "elapsed_ms": (ended - started_ns) // 1_000_000, "result": result,
                           "exit_code": exit_code, "cache_hit": cache_hit, "shard": None, "attempt": 1,
                           "skip_reason": detail if result in {"SKIP", "PLATFORM_SKIP"} else None,
                           "result_detail": detail}
                suffix = _hash_bytes(identifier.encode())[:16]
                _publish_json_at(bucket_fd, f"{kind}-{selected['ordinal']:06d}-{suffix}.json",
                                 _signed_record(runtime, f"{kind}-event", payload))
            finally:
                os.close(bucket_fd)
        finally:
            os.close(root_fd)
        return True
    except Exception as error:
        _error_marker(runtime, f"write-{kind}", error)
        return False


def _read_raw_events(path: Path) -> list[dict[str, Any]]:
    """Read one completed child-owned raw sink through a bound descriptor."""
    absolute = Path(os.path.abspath(path))
    parent_fd = _open_safe_directory(absolute.parent)
    descriptor = -1
    try:
        named = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        if (stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode)
                or named.st_uid != os.getuid() or stat.S_IMODE(named.st_mode) != 0o600
                or named.st_size > _MAX_RAW_EVENT_BYTES):
            raise CatalogError("raw event sink is unsafe")
        descriptor = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        rebound = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_identity(named, opened) or not _same_identity(opened, rebound):
            raise CatalogError("raw event sink identity changed")
        chunks: list[bytes] = []
        remaining = _MAX_RAW_EVENT_BYTES
        while True:
            block = os.read(descriptor, min(65536, remaining + 1))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
            if remaining < 0:
                raise CatalogError("raw event sink is too large")
        finished = os.fstat(descriptor)
        final_rebound = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        if (not _same_file_snapshot(opened, finished)
                or not _same_file_snapshot(opened, final_rebound)):
            raise CatalogError("raw event sink changed while reading")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
    return _decode_raw_events(b"".join(chunks))


def _read_raw_events_fd(descriptor: int) -> list[dict[str, Any]]:
    """Read an already-unlinked raw sink through the parent's retained FD."""
    try:
        if not isinstance(descriptor, int) or descriptor < 3:
            raise CatalogError("raw event descriptor is invalid")
        initial = os.fstat(descriptor)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid()
                or stat.S_IMODE(initial.st_mode) != 0o600
                or initial.st_size > _MAX_RAW_EVENT_BYTES):
            raise CatalogError("raw event descriptor is unsafe")
        chunks: list[bytes] = []
        remaining = _MAX_RAW_EVENT_BYTES
        offset = 0
        while True:
            block = os.pread(descriptor, min(65536, remaining + 1), offset)
            if not block:
                break
            chunks.append(block)
            offset += len(block)
            remaining -= len(block)
            if remaining < 0:
                raise CatalogError("raw event sink is too large")
        finished = os.fstat(descriptor)
        if not _same_file_snapshot(initial, finished):
            raise CatalogError("raw event descriptor changed while reading")
    except OSError as error:
        raise CatalogError("raw event descriptor is unavailable") from error
    return _decode_raw_events(b"".join(chunks))


def _decode_raw_events(raw: bytes) -> list[dict[str, Any]]:
    """Parse the bounded canonical raw stream after its transport is verified."""
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise CatalogError("raw event sink is truncated")
    rows = raw.splitlines()
    if len(rows) > _MAX_RAW_EVENT_COUNT:
        raise CatalogError("raw event sink has too many events")
    records: list[dict[str, Any]] = []
    expected_keys = {"detail", "ended_monotonic_ns", "result", "schema",
                     "started_monotonic_ns", "test_id"}
    for row in rows:
        try:
            value = json.loads(row)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CatalogError("raw event sink contains corrupt JSON") from error
        if (not isinstance(value, dict) or set(value) != expected_keys
                or row != canonical(value).encode("utf-8")):
            raise CatalogError("raw event sink record is not canonical")
        identifier = value.get("test_id")
        result = value.get("result")
        started = value.get("started_monotonic_ns")
        ended = value.get("ended_monotonic_ns")
        detail = value.get("detail")
        if (value.get("schema") != _RAW_EVENT_SCHEMA or not isinstance(identifier, str)
                or not identifier or len(identifier) > 1024 or result not in _RESULTS
                or not isinstance(started, int) or not isinstance(ended, int)
                or started < 0 or ended < started
                or (detail is not None and (not isinstance(detail, str) or len(detail) > 2048))):
            raise CatalogError("raw event sink record is invalid")
        records.append(value)
    return records


def runtime_ingest(entrypoint_id: str, raw_events: Path | None = None,
                   context: dict[str, str] | None = None, *,
                   raw_fd: int | None = None) -> bool:
    """Trusted parent mapping from one raw sink to one fixed selected origin."""
    runtime = _runtime_context(context)
    try:
        root_fd, selection = _load_selection(runtime)
        try:
            selected_origin = next((item for item in selection["entrypoints"]
                                    if item["entrypoint_id"] == entrypoint_id), None)
            if selected_origin is None or not selected_origin["selected"]:
                raise CatalogError("raw ingest origin is not selected")
        finally:
            os.close(root_fd)
        if (raw_events is None) == (raw_fd is None):
            raise CatalogError("raw ingest transport is ambiguous")
        records = (_read_raw_events(raw_events) if raw_events is not None
                   else _read_raw_events_fd(raw_fd))
        for value in records:
            identifier = value["test_id"]
            if _case_entrypoint(identifier) != entrypoint_id:
                raise CatalogError("raw event claims a different selected origin")
            result = value["result"]
            if not runtime_event(identifier, result, value["started_monotonic_ns"],
                                 entrypoint_id=entrypoint_id, kind="case",
                                 exit_code=0 if result == "PASS" else 1,
                                 detail=value["detail"], ended_ns=value["ended_monotonic_ns"],
                                 context=runtime):
                raise CatalogError("raw event could not be ingested")
        return True
    except Exception as error:
        _error_marker(runtime, "ingest", error)
        return False


def runtime_cache_certificate(stage: str, key: str, output_path: Path,
                              before: str, after: str,
                              *, entrypoint_id: str,
                              context: dict[str, str] | None = None) -> bool:
    runtime = _runtime_context(context)
    try:
        if (stage not in {"compile", "link"} or before != after or key != before
                or not re.fullmatch(r"[0-9a-f]{64}", key)):
            raise CatalogError("cache identity mismatch")
        root_fd, selection = _load_selection(runtime)
        try:
            stage_id = "stage::shader::glsl-es" if stage == "compile" else "stage::shader::link"
            if entrypoint_id != _stage_entrypoint(stage_id, runtime["profile"]):
                raise CatalogError("cache certificate origin does not own selected stage")
            selected_stage = next(item for item in selection["stages"] if item["stage_id"] == stage_id)
            contract = next((item for item in selection["cache_contracts"]
                             if item["stage_id"] == stage_id), None)
            if contract is None:
                raise CatalogError("cache stage is absent from frozen selection")
            expected_output = (Path(contract["cache_root"]) / stage / f"{key}.out")
            output_path = Path(os.path.abspath(output_path))
            if output_path != expected_output:
                raise CatalogError("cache output path does not match frozen contract")
            cached_output_sha256, output_stat = _snapshot_regular_file(output_path)
            payload = {"schema": RUNTIME_SCHEMA, "run_id": runtime["run_id"],
                       "profile": runtime["profile"], "stage_id": stage_id,
                       "selection_authority": "NONE", "cache_authority": "NONE",
                       "parent_stage_id": None, "ordinal": selected_stage["ordinal"],
                       "kind": selected_stage["kind"], "labels": selected_stage["labels"],
                       "resources": selected_stage["resources"], "timeout_seconds": selected_stage["timeout_seconds"],
                       "timeout_policy": "report-only", "explicit_inputs": selected_stage["explicit_inputs"],
                       "input_sha256": selected_stage["input_sha256"],
                       "input_mapping_complete": selected_stage["input_mapping_complete"],
                       "selection_sha256": selection["selection_sha256"], "result": "PASS", "exit_code": 0,
                       "cache_hit": True, "execution": "cache-replay", "cache_key": key,
                       "cached_output_path": str(output_path), "cached_output_dev": output_stat.st_dev,
                       "cached_output_ino": output_stat.st_ino, "cached_output_size": output_stat.st_size,
                       "cached_output_mtime_ns": output_stat.st_mtime_ns,
                       "cached_output_sha256": cached_output_sha256, "input_before": before,
                       "input_after": after, "checker_sha256": contract["checker_sha256"],
                       "catalog_sha256": contract["catalog_sha256"],
                       "catalog_schema": contract["catalog_schema"], "runtime_schema": RUNTIME_SCHEMA,
                       "list_schema": contract["list_schema"],
                       "list_tool_sha256": contract["checker_sha256"],
                       "covered_ids": contract["covered_ids"], "covered_count": contract["covered_count"],
                       "covered_sha256": contract["covered_sha256"],
                       "list_ids_sha256": contract["list_ids_sha256"],
                       "started_monotonic_ns": None,
                       "ended_monotonic_ns": None, "recorded_monotonic_ns": time.monotonic_ns(),
                       "elapsed_ms": None, "shard": None, "attempt": 1,
                       "skip_reason": None}
            bucket_fd = _open_child_directory(root_fd, "stage-events")
            try:
                _publish_json_at(bucket_fd, f"stage-{selected_stage['ordinal']:06d}-cache-{stage}.json",
                                 _signed_record(runtime, "cache-certificate", payload))
            finally:
                os.close(bucket_fd)
        finally:
            os.close(root_fd)
        return True
    except Exception as error:
        _error_marker(runtime, "cache-certificate", error)
        return False


def _validate_runtime_record(value: dict[str, Any], selected: dict[str, Any], key: str,
                             selection: dict[str, Any], context: dict[str, str]) -> None:
    domain = ("cache-certificate" if value.get("execution") == "cache-replay"
              else ("case-event" if key == "test_id" else "stage-event"))
    _verify_record_auth(context, domain, value)
    if (value.get("schema") != RUNTIME_SCHEMA or value.get(key) != selected[key]
            or value.get("ordinal") != selected["ordinal"]
            or value.get("run_id") != selection["run_id"]
            or value.get("profile") != selection["profile"]
            or "nonce" in value
            or value.get("selection_authority") != "NONE"
            or value.get("cache_authority") != "NONE"
            or value.get("selection_sha256") != selection["selection_sha256"]
            or value.get("result") not in _RESULTS or value.get("shard") is not None
            or value.get("attempt") != 1 or value.get("timeout_policy") != "report-only"):
        raise CatalogError("runtime event schema/identity mismatch")
    for field in ("parent_stage_id", "kind", "labels", "resources", "timeout_seconds",
                  "explicit_inputs", "input_sha256", "input_mapping_complete"):
        if value.get(field) != selected.get(field):
            raise CatalogError(f"runtime event metadata mismatch: {field}")
    if value.get("cache_hit") is True and key != "stage_id":
        raise CatalogError("case cannot be cache replay")
    if key == "test_id" and value.get("cache_hit") is not None:
        raise CatalogError("direct case has invalid cache state")
    started = value.get("started_monotonic_ns")
    ended = value.get("ended_monotonic_ns")
    elapsed = value.get("elapsed_ms")
    recorded = value.get("recorded_monotonic_ns")
    if value.get("execution") == "cache-replay":
        if (key != "stage_id" or selected["stage_id"] not in {
                "stage::shader::glsl-es", "stage::shader::link"}
                or value.get("cache_hit") is not True
                or any(item is not None for item in (started, ended, elapsed))
                or not isinstance(recorded, int) or recorded < 0):
            raise CatalogError("cache certificate timing/schema is invalid")
    else:
        if (not isinstance(started, int) or not isinstance(ended, int)
                or not isinstance(recorded, int) or not isinstance(elapsed, int)
                or min(started, ended, recorded, elapsed) < 0
                or ended < started or recorded < ended
                or elapsed != max(0, (ended - started) // 1_000_000)):
            raise CatalogError("runtime event timing is invalid")
        if selected.get("stage_id") in {"stage::shader::glsl-es", "stage::shader::link"}:
            if value.get("cache_hit") is not False:
                raise CatalogError("direct shader stage lacks cache miss state")
        elif value.get("cache_hit") not in {None, True}:
            raise CatalogError("runtime stage has invalid cache state")
    result = value["result"]
    if (result in {"SKIP", "PLATFORM_SKIP"}) != (value.get("skip_reason") is not None):
        raise CatalogError("runtime skip reason combination is invalid")
    if result == "PASS" and value.get("exit_code") not in {None, 0}:
        raise CatalogError("PASS event has failing exit code")
    if result in {"FAIL", "ERROR", "TIMEOUT"} and value.get("exit_code") == 0:
        raise CatalogError("failed event has successful exit code")


def _replay_current_cache_list(contract: dict[str, Any]) -> None:
    """Re-run the current list producer before accepting cached shader coverage."""
    checker = ROOT / contract["checker_path"]
    environment = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = subprocess.run(
            [sys.executable, str(checker), "--list-json"], cwd=ROOT, env=environment,
            text=True, capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CatalogError("cache list producer could not run") from error
    if completed.returncode != 0:
        raise CatalogError("cache list producer failed")
    try:
        listed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CatalogError("cache list producer emitted malformed JSON") from error
    if not isinstance(listed, dict) or listed.get("schema") != contract["list_schema"]:
        raise CatalogError("cache list producer schema changed")
    if contract["stage_id"] == "stage::shader::glsl-es":
        baseline, variants = listed.get("baseline"), listed.get("variants")
        if not (isinstance(baseline, list) and isinstance(variants, list)
                and all(isinstance(value, str) for value in baseline + variants)):
            raise CatalogError("shader cache list has invalid cases")
        actual = baseline + variants
    else:
        actual = listed.get("links")
        if not isinstance(actual, list) or not all(isinstance(value, str) for value in actual):
            raise CatalogError("link cache list has invalid cases")
    if (actual != contract["covered_ids"] or len(actual) != contract["covered_count"]
            or digest(actual) != contract["covered_sha256"]
            or digest(actual) != contract["list_ids_sha256"]):
        raise CatalogError("cache list producer coverage changed")


_RUNTIME_ROOT_BASE = frozenset({"selection.json", "case-events", "stage-events", "error-events"})


def _verify_runtime_root_layout(root_fd: int, *, published: bool) -> None:
    """Require a closed runtime namespace before and after final publication."""
    expected = set(_RUNTIME_ROOT_BASE)
    if published:
        expected.add("run.json")
    names = set(os.listdir(root_fd))
    if names != expected:
        raise CatalogError("runtime root contains incomplete or foreign artifacts")
    for name in ("case-events", "stage-events", "error-events"):
        detail = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        _validate_directory_stat(detail, private=True)
    for name in ("selection.json", *( ("run.json",) if published else ())):
        detail = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode)
                or detail.st_uid != os.getuid() or stat.S_IMODE(detail.st_mode) != 0o600):
            raise CatalogError("runtime root record is unsafe")


def runtime_finalize(directory: Path, profile: str, run_id: str, nonce: str,
                     gate_exit_code: int) -> dict[str, Any]:
    context = {"directory": str(directory), "profile": profile, "run_id": run_id, "nonce": nonce}
    base = {"status": "ERROR", "path": str(directory), "sha256": None}
    try:
        root_fd, selection = _load_selection(context)
        try:
            _verify_runtime_root_layout(root_fd, published=False)
            case_fd = _open_child_directory(root_fd, "case-events")
            stage_fd = _open_child_directory(root_fd, "stage-events")
            error_fd = _open_child_directory(root_fd, "error-events")
            try:
                case_names, case_pending, case_foreign = _list_final_json(case_fd)
                stage_names, stage_pending, stage_foreign = _list_final_json(stage_fd)
                error_names, error_pending, error_foreign = _list_final_json(error_fd)
                cases = [_read_json_at(case_fd, name) for name in case_names]
                stages = [_read_json_at(stage_fd, name) for name in stage_names]
                errors = [_read_json_at(error_fd, name) for name in error_names]
            finally:
                os.close(case_fd); os.close(stage_fd); os.close(error_fd)
            for value in errors:
                if (value.get("schema") != RUNTIME_SCHEMA
                        or value.get("run_id") != run_id
                        or value.get("profile") != profile
                        or "nonce" in value
                        or not isinstance(value.get("operation"), str)
                        or not isinstance(value.get("error"), str)
                        or not isinstance(value.get("monotonic_ns"), int)):
                    raise CatalogError("foreign or invalid runtime error marker")
                _verify_record_auth(context, "error-event", value)
            selected_cases = {item["test_id"]: item for item in selection["cases"]}
            selected_stages = {item["stage_id"]: item for item in selection["stages"]}
            direct_ids: list[str] = []
            cache_ids: list[str] = []
            for value in cases:
                identifier = value.get("test_id")
                if identifier not in selected_cases:
                    raise CatalogError("extra case event")
                _validate_runtime_record(value, selected_cases[identifier], "test_id", selection, context)
                direct_ids.append(identifier)
            stage_ids: list[str] = []
            for value in stages:
                identifier = value.get("stage_id")
                if identifier not in selected_stages:
                    raise CatalogError("extra stage event")
                _validate_runtime_record(value, selected_stages[identifier], "stage_id", selection, context)
                stage_ids.append(identifier)
                if value.get("execution") == "cache-replay":
                    covered = value.get("covered_ids")
                    expected_covered = [item["test_id"] for item in selection["cases"]
                                        if (item["test_id"].startswith("shader:")
                                            if identifier == "stage::shader::glsl-es"
                                            else item["test_id"].startswith("shader-link:"))]
                    contract = next((item for item in selection["cache_contracts"]
                                     if item["stage_id"] == identifier), None)
                    if contract is None:
                        raise CatalogError("cache replay has no frozen contract")
                    output_path = Path(value.get("cached_output_path", ""))
                    expected_output = (Path(contract["cache_root"]) /
                                       ("compile" if identifier.endswith("glsl-es") else "link") /
                                       f"{value.get('cache_key', '')}.out")
                    try:
                        if output_path != expected_output:
                            raise CatalogError("cache output path does not match frozen contract")
                        output_hash, output_stat = _snapshot_regular_file(output_path)
                    except (OSError, CatalogError) as error:
                        raise CatalogError("cache output is unavailable or unsafe") from error
                    if (not isinstance(covered, list) or value.get("covered_count") != len(covered)
                            or value.get("covered_sha256") != digest(covered)
                            or covered != expected_covered or covered != contract["covered_ids"]
                            or value.get("elapsed_ms") is not None
                            or value.get("input_before") != value.get("input_after")
                            or value.get("cache_key") != value.get("input_before")
                            or not re.fullmatch(r"[0-9a-f]{64}", value.get("cache_key", ""))
                            or value.get("catalog_sha256") != contract["catalog_sha256"]
                            or sha256_file(CATALOG_PATH) != contract["catalog_sha256"]
                            or value.get("catalog_schema") != contract["catalog_schema"]
                            or value.get("runtime_schema") != RUNTIME_SCHEMA
                            or value.get("checker_sha256") != contract["checker_sha256"]
                            or _sha256_regular_file(ROOT / contract["checker_path"]) != contract["checker_sha256"]
                            or value.get("list_tool_sha256") != contract["checker_sha256"]
                            or value.get("list_schema") != contract["list_schema"]
                            or value.get("list_ids_sha256") != contract["list_ids_sha256"]
                            or value.get("covered_sha256") != contract["covered_sha256"]
                            or output_stat.st_dev != value.get("cached_output_dev")
                            or output_stat.st_ino != value.get("cached_output_ino")
                            or output_stat.st_size != value.get("cached_output_size")
                            or output_stat.st_mtime_ns != value.get("cached_output_mtime_ns")
                            or output_hash != value.get("cached_output_sha256")):
                        raise CatalogError("invalid cache certificate")
                    _replay_current_cache_list(contract)
                    cache_ids.extend(covered)
            if (len(direct_ids) != len(set(direct_ids)) or len(stage_ids) != len(set(stage_ids))
                    or len(cache_ids) != len(set(cache_ids)) or set(direct_ids) & set(cache_ids)):
                raise CatalogError("duplicate or overlapping runtime coverage")
            observed_cases = direct_ids + cache_ids
            expected_cases = [item["test_id"] for item in selection["cases"]]
            expected_stages = [item["stage_id"] for item in selection["stages"]]
            raw_stages = sorted(stages, key=lambda item: item.get("recorded_monotonic_ns", -1))
            raw_stage_ids = [item["stage_id"] for item in raw_stages]
            stage_ordinals = [selected_stages[item]["ordinal"] for item in raw_stage_ids]
            if stage_ordinals != sorted(stage_ordinals) or len(stage_ordinals) != len(set(stage_ordinals)):
                raise CatalogError("stage trace is unordered")
            if gate_exit_code != 0 and stage_ordinals != list(range(1, len(stage_ordinals) + 1)):
                raise CatalogError("failed gate stage trace is not an ordered prefix")
            incomplete = bool(case_pending or stage_pending or error_pending or error_names
                              or case_foreign or stage_foreign or error_foreign)
            complete = (gate_exit_code == 0 and not incomplete
                        and set(observed_cases) == set(expected_cases)
                        and stage_ids == expected_stages
                        and all(value["result"] == "PASS" for value in cases + stages))
            status = "COMPLETE" if complete else "INCOMPLETE"
            payload = {"schema": RUNTIME_SCHEMA, "run_id": run_id, "profile": profile,
                       "selection_authority": "NONE", "cache_authority": "NONE",
                       "selection_sha256": selection["selection_sha256"], "gate_exit_code": gate_exit_code,
                       "telemetry_status": status, "cases": sorted(cases, key=lambda item: item["ordinal"]),
                       "stages": sorted(stages, key=lambda item: item["ordinal"]),
                       "direct_case_ids_sha256": digest(direct_ids) if direct_ids else None,
                       "cache_case_ids_sha256": digest(cache_ids) if cache_ids else None,
                       "stage_ids_sha256": digest(stage_ids) if stage_ids else None,
                       "raw_case_order_sha256": _hash_bytes(("\n".join(direct_ids) + "\n").encode()),
                       "raw_stage_order_sha256": _hash_bytes(("\n".join(raw_stage_ids) + "\n").encode()),
                       "error_marker_count": len(error_names)}
            # Recheck before publication: a failed raw/nested cleanup or a
            # concurrent writer must never be silently hidden by COMPLETE.
            _verify_runtime_root_layout(root_fd, published=False)
            _publish_json_at(root_fd, "run.json", _signed_record(context, "run", payload))
            _verify_runtime_root_layout(root_fd, published=True)
            run = _read_json_at(root_fd, "run.json")
            _verify_record_auth(context, "run", run)
            run_hash = _hash_bytes((canonical(run) + "\n").encode())
            return {"status": status, "path": str(directory), "sha256": run_hash}
        finally:
            os.close(root_fd)
    except Exception as error:
        _error_marker(context, "finalize", error)
        return base


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(values: Iterable[str]) -> str:
    """Historical audited-ID algorithm: sorted LF records with a final LF."""
    identifiers = list(values)
    if len(identifiers) != len(set(identifiers)):
        raise CatalogError("duplicate stable test ID")
    if any(not item or "\n" in item or "\r" in item for item in identifiers):
        raise CatalogError("stable test ID is empty or contains CR/LF")
    payload = ("\n".join(sorted(identifiers)) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"catalog unreadable: {error}") from error
    if catalog.get("schema") != SCHEMA:
        raise CatalogError("unexpected catalog schema")
    if catalog.get("selection_authority") != "NONE":
        raise CatalogError("Phase 0A catalog must not select tests")
    if catalog.get("cache_authority") != "NONE":
        raise CatalogError("Phase 0A catalog must not govern cache")
    if not isinstance(catalog.get("entrypoints"), list):
        raise CatalogError("catalog entrypoints missing")
    return catalog


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError as error:
        raise CatalogError(f"path outside repository: {path}") from error


def parse_python_methods(path: Path) -> list[str]:
    """Return unittest methods, including safely resolved local inheritance."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise CatalogError(f"cannot parse {relative(path)}: {error}") from error
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    cache: dict[str, tuple[bool, list[str]]] = {}

    def base_name(node: ast.AST) -> str:
        return ast.unparse(node)

    def resolve(name: str, stack: tuple[str, ...] = ()) -> tuple[bool, list[str]]:
        if name in cache:
            return cache[name]
        if name in stack:
            raise CatalogError(f"cyclic local test inheritance in {relative(path)}: {name}")
        node = classes[name]
        own = [
            child.name for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name.startswith("test_")
        ]
        # Preserve static discovery for a directly declared test-shaped class
        # with no base while still resolving every declared inheritance edge.
        # This keeps the inventory import-free and lets synthetic fixtures prove
        # that parsing cannot trigger module side effects.
        is_test = bool(own) and not node.bases
        inherited: list[str] = []
        for base in node.bases:
            rendered = base_name(base)
            if rendered in ("unittest.TestCase", "TestCase"):
                is_test = True
            elif rendered in ("object",):
                continue
            elif rendered in classes:
                base_is_test, base_methods = resolve(rendered, stack + (name,))
                is_test = is_test or base_is_test
                inherited.extend(base_methods)
            elif rendered not in {"BaseException", "Exception", "RuntimeError"}:
                raise CatalogError(
                    f"unresolved test-contributing base {rendered} for {name} in {relative(path)}"
                )
        methods_by_name = {method: method for method in inherited}
        methods_by_name.update({method: method for method in own})
        result = is_test, list(methods_by_name)
        cache[name] = result
        return result

    methods: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_test, names = resolve(node.name)
        if is_test:
            methods.extend(
                f"py:{relative(path)}::{node.name}.{name}" for name in names
            )
    return methods


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        values: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                values.append(item.value)
            else:
                return None
        return "".join(values)
    return None


def _generated_case_prefix(node: ast.AST) -> str | None:
    value = _constant_string(node)
    if value is not None:
        return value
    if not isinstance(node, ast.JoinedStr) or len(node.values) != 2:
        return None
    prefix, formatted = node.values
    if (not isinstance(prefix, ast.Constant) or not isinstance(prefix.value, str)
            or not isinstance(formatted, ast.FormattedValue)
            or not isinstance(formatted.value, ast.Name)
            or formatted.value.id != "index"):
        return None
    return prefix.value + "{index:02d}"


def _exact_index_fstring(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.JoinedStr) or len(node.values) != 2:
        return None
    prefix, formatted = node.values
    if (not isinstance(prefix, ast.Constant) or not isinstance(prefix.value, str)
            or not isinstance(formatted, ast.FormattedValue)
            or not isinstance(formatted.value, ast.Name)
            or formatted.value.id != "index" or formatted.conversion != -1
            or not isinstance(formatted.format_spec, ast.JoinedStr)
            or len(formatted.format_spec.values) != 1
            or not isinstance(formatted.format_spec.values[0], ast.Constant)
            or formatted.format_spec.values[0].value != "02d"):
        return None
    return prefix.value, formatted.value.id


def parse_archive_generated_methods(path: Path) -> list[str]:
    """Statically reconstruct CASE_BODIES' generated 131-test naming contract."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise CatalogError(f"cannot parse archive cases: {error}") from error
    assignment = next(
        (node for node in tree.body if isinstance(node, ast.Assign)
         and any(isinstance(target, ast.Name) and target.id == "CASE_BODIES"
                 for target in node.targets)),
        None,
    )
    if assignment is None or not isinstance(assignment.value, ast.Dict):
        raise CatalogError("archive CASE_BODIES assignment missing")
    cases: list[str] = []
    for expansion in assignment.value.values:
        if not isinstance(expansion, ast.DictComp) or len(expansion.generators) != 1:
            raise CatalogError("archive CASE_BODIES must contain one-generator dictionary comprehensions")
        key = _exact_index_fstring(expansion.key)
        if key is None or not re.fullmatch(r"[A-Z]+-", key[0]):
            raise CatalogError("archive generated case key must use exact PREFIX-{index:02d}")
        prefix = key[0][:-1]
        generator = expansion.generators[0]
        if (not isinstance(generator.target, ast.Name) or generator.target.id != "index"
                or generator.ifs or generator.is_async):
            raise CatalogError(f"archive generated case iterator invalid for {prefix}")
        range_call = generator.iter
        if (not isinstance(range_call, ast.Call)
                or not isinstance(range_call.func, ast.Name)
                or range_call.func.id != "range" or len(range_call.args) != 2
                or range_call.keywords):
            raise CatalogError(f"archive generated case range missing for {prefix}")
        if (not isinstance(expansion.value, ast.Call)
                or not isinstance(expansion.value.func, ast.Name)
                or expansion.value.func.id != "getattr"
                or len(expansion.value.args) != 2 or expansion.value.keywords
                or not isinstance(expansion.value.args[0], ast.Name)
                or expansion.value.args[0].id != "ArchivePolicyTests"):
            raise CatalogError(f"archive CASE_BODIES getattr contract invalid for {prefix}")
        lookup = _exact_index_fstring(expansion.value.args[1])
        if lookup != (prefix.lower() + "_", "index"):
            raise CatalogError(f"archive CASE_BODIES lookup contract invalid for {prefix}")
        start = ast.literal_eval(range_call.args[0])
        stop = ast.literal_eval(range_call.args[1])
        if not isinstance(start, int) or not isinstance(stop, int) or start != 1 or stop <= start:
            raise CatalogError(f"archive generated case range invalid for {prefix}")
        cases.extend(
            f"py:{relative(path)}::ArchivePolicyTests::{prefix}-{index:02d}"
            for index in range(start, stop)
        )
    expected_make_case = ast.parse(
        "def make_case(case_id: str, body):\n"
        "    def test(self: ArchivePolicyTests) -> None:\n"
        "        body(self)\n"
        "    test.__name__ = 'test_' + case_id.replace('-', '_')\n"
        "    test.__doc__ = case_id\n"
        "    return test\n"
    ).body[0]
    make_case = next(
        (node for node in tree.body
         if isinstance(node, ast.FunctionDef) and node.name == "make_case"), None
    )
    if make_case is None or ast.dump(make_case) != ast.dump(expected_make_case):
        raise CatalogError("archive make_case naming/doc wiring invalid")
    expected_loop = ast.parse(
        "for _case_id, _body in CASE_BODIES.items():\n"
        "    setattr(ArchivePolicyTests, 'test_' + _case_id.replace('-', '_'), "
        "make_case(_case_id, _body))\n"
    ).body[0]
    loop = next(
        (node for node in tree.body
         if isinstance(node, ast.For)
         and ast.unparse(node.iter) == "CASE_BODIES.items()"), None
    )
    if loop is None or ast.dump(loop) != ast.dump(expected_loop):
        raise CatalogError("archive CASE_BODIES loop/setattr wiring invalid")
    if len(cases) != len(set(cases)):
        raise CatalogError("archive generated cases are not unique")
    return cases


def parse_build_check_python(root: Path = ROOT) -> tuple[list[str], list[str]]:
    """Read the authoritative execution order without evaluating shell."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    before_shader, marker, after_shader = text.partition('if [ "$run_shaders" = 1 ]; then')
    if not marker:
        raise CatalogError("build_check shader branch missing")
    shader_block, engine_marker, _after_engine = after_shader.partition('if [ "$run_engine" = 1 ]; then')
    if not engine_marker:
        raise CatalogError("build_check engine branch missing after shader branch")
    unconditional: list[str] = []
    shader_only: list[str] = []
    for line in before_shader.splitlines():
        match = PYTHON_COMMAND.match(line)
        if match:
            unconditional.append(match.group(1))
    for line in shader_block.splitlines():
        match = PYTHON_COMMAND.match(line)
        if match:
            shader_only.append(match.group(1))
    if (len(unconditional) != 24 or len(shader_only) != 4
            or unconditional.count(HOST_FEEDBACK_TOOLING_PATH) != 1):
        raise CatalogError(
            f"build_check Python inventory drift: unconditional={len(unconditional)} shader={len(shader_only)}"
        )
    return unconditional, shader_only


def parse_cpp_executions(root: Path = ROOT) -> list[str]:
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    compiled = re.findall(r"misc/ios/([A-Za-z0-9_]+_test\.cpp)\s+-o\s+\"\$([A-Za-z0-9_]+)\"", text)
    result: list[str] = []
    seen_variables: set[str] = set()
    seen_identifiers: set[str] = set()
    for source, variable in compiled:
        if variable in seen_variables:
            raise CatalogError(f"duplicate C++ compile output variable: {variable}")
        seen_variables.add(variable)
        variant = "asan-ubsan" if "sanitized" in variable else "strict"
        identifier = f"cpp:misc/ios/{source.removesuffix('.cpp')}@{variant}"
        if identifier in seen_identifiers:
            raise CatalogError(f"duplicate C++ source/variant compile: {identifier}")
        seen_identifiers.add(identifier)
        escaped = re.escape(variable)
        if variant == "asan-ubsan":
            executions = re.findall(
                rf'^(?:ASAN_OPTIONS="\$[A-Za-z0-9_]+"\s+UBSAN_OPTIONS=halt_on_error=1\s+|'
                rf'feedback_case\s+"cpp:misc/ios/[A-Za-z0-9_]+_test@asan-ubsan"\s+env\s+'
                rf'ASAN_OPTIONS="\$[A-Za-z0-9_]+"\s+UBSAN_OPTIONS=halt_on_error=1\s+)'
                rf'"\${escaped}"\s+\\?$',
                text, re.MULTILINE,
            )
        else:
            executions = re.findall(
                rf'^(?:feedback_case\s+"cpp:misc/ios/[A-Za-z0-9_]+_test@strict"\s+)?'
                rf'"\${escaped}"\s+\|\|\s+fail\b.*$', text, re.MULTILINE)
        if len(executions) != 1:
            raise CatalogError(
                f"C++ binary execution mapping drift for {variable}: executions={len(executions)}"
            )
        result.append(identifier)
    if len({item.split("@", 1)[0] for item in result}) != 9 or len(result) != 14:
        raise CatalogError(f"C++ inventory drift: {len(result)} executions")
    return result


def parse_gate_validation_entrypoints(root: Path = ROOT) -> dict[str, list[str]]:
    """Discover non-unittest validation commands from authoritative gate syntax."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    exact_patterns = {
        "validation::python::openal-configured": (
            r'^\s+openal_fields=\$\(feedback_stage "stage::validation::python::openal-configured" python3 "\$REPO_ROOT/misc/ios/openal_provider_contract\.py" configured\s+\\$',
            ["engine", "device", "full", "fast"],
        ),
        "validation::python::openal-artifact": (
            r'^\s+openal_fields=\$\(feedback_stage "stage::validation::python::openal-artifact" python3 "\$REPO_ROOT/misc/ios/openal_provider_contract\.py" artifact\s+\\$',
            ["engine", "device", "full", "fast"],
        ),
        "validation::bash-n::run-gate-launcher": (
            r'^feedback_stage "stage::validation::bash-n::run-gate-launcher" bash -n misc/ios/run_gate_logged\.sh\s+\\$', ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::bash-n::capture-tools": (
            r'^feedback_stage "stage::validation::bash-n::capture-tools" bash -n misc/ios/device_lease\.sh misc/ios/input\.sh misc/ios/shot\.sh '
            r"misc/ios/lighting_ab_capture\.sh\s+\\$", ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::shellcheck::capture-tools": (
            r'^feedback_stage "stage::validation::shellcheck::capture-tools" shellcheck -x misc/ios/device_lease\.sh misc/ios/input\.sh misc/ios/shot\.sh '
            r"misc/ios/lighting_ab_capture\.sh\s+\\$", ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::python::ui-contract": (
            r'^feedback_stage "stage::validation::python::ui-contract" python3 misc/ios/ui_contract_check\.py\s+\\$', ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::shader::macro-contract": (
            r'^\s+feedback_stage "stage::validation::shader::macro-contract" python3 misc/ios/shadercheck/glsl_es_check\.py --macro-contract\s+\\$',
            ["shaders", "device", "full", "fast"],
        ),
    }
    result: dict[str, list[str]] = {}
    for identifier, (pattern, profiles) in exact_patterns.items():
        matches = re.findall(pattern, text, re.MULTILINE)
        if len(matches) != 1:
            raise CatalogError(f"gate validation entrypoint drift: {identifier} matches={len(matches)}")
        result[identifier] = profiles
    return result


def parse_gate_family_entrypoints(root: Path = ROOT) -> dict[str, list[str]]:
    """Verify test/checker families are actually invoked by build_check."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    patterns = {
        "shell::ui-log-oracle": (
            (r'^feedback_stage "stage::shell::ui-log-oracle" misc/ios/ui_automation/test_log_oracles\.sh\s+\\$',
             r'^\s*elif \[ "\$\{1:-\}" = misc/ios/ui_automation/test_log_oracles\.sh \]; then\n'
             r'\s*feedback_selected_shell "\$@"\n\s*return \$\?$',),
            ["engine", "shaders", "device", "full", "fast"],
        ),
        "shader::glsl-es": (
            (r'^\s*run_shader_cache_stage compile "stage::shader::glsl-es" shader::glsl-es "\$compile_hash" \\$',
             r'^\s*python3 misc/ios/shadercheck/glsl_es_check\.py --glslang "\$GLSLANG" --strict \\$',
             r'^\s*shader_cache_output=\$\(feedback_selected_raw "\$origin" python3 "\$REPO_ROOT/misc/ios/shader_cache\.py"'),
            ["shaders", "device", "full", "fast"],
        ),
        "shader::link": (
            (r'^\s*run_shader_cache_stage link "stage::shader::link" shader::link "\$link_hash" \\$',
             r'^\s*python3 misc/ios/shadercheck/link_check\.py --strict \\$',
             r'^\s*shader_cache_output=\$\(feedback_selected_raw "\$origin" python3 "\$REPO_ROOT/misc/ios/shader_cache\.py"'),
            ["shaders", "device", "full", "fast"],
        ),
    }
    result: dict[str, list[str]] = {}
    for identifier, (required_patterns, profiles) in patterns.items():
        counts = [len(re.findall(pattern, text, re.MULTILINE)) for pattern in required_patterns]
        if counts != [1] * len(required_patterns):
            raise CatalogError(f"gate family entrypoint drift: {identifier} matches={counts}")
        result[identifier] = profiles
    return result


def validate_build_stage_exclusions(catalog: dict[str, Any], root: Path = ROOT) -> None:
    """Machine-check non-test build stages without turning them into test IDs."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    stages = catalog.get("build_stage_exclusions")
    if not isinstance(stages, list) or not stages:
        raise CatalogError("build-stage exclusions missing")
    identifiers: set[str] = set()
    exclusion_matches: list[re.Match[str]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise CatalogError("build-stage exclusion malformed")
        identifier = stage.get("stage_id")
        pattern = stage.get("anchor_regex")
        reason = stage.get("reason")
        if (not isinstance(identifier, str) or identifier in identifiers
                or not isinstance(pattern, str) or not isinstance(reason, str) or not reason):
            raise CatalogError("build-stage exclusion metadata invalid")
        identifiers.add(identifier)
        try:
            matches = re.findall(pattern, text, re.MULTILINE)
        except re.error as error:
            raise CatalogError(f"build-stage exclusion regex invalid: {identifier}") from error
        expected_matches = stage.get("expected_matches", 1)
        if not isinstance(expected_matches, int) or expected_matches < 1:
            raise CatalogError(f"build-stage exclusion match count invalid: {identifier}")
        if len(matches) != expected_matches:
            raise CatalogError(
                f"build-stage exclusion anchor drift: {identifier} matches={len(matches)}"
            )
        exclusion_matches.extend(re.finditer(pattern, text, re.MULTILINE))
    if identifiers != EXPECTED_BUILD_STAGE_EXCLUSIONS:
        raise CatalogError(
            "build-stage exclusion inventory drift: "
            f"missing={sorted(EXPECTED_BUILD_STAGE_EXCLUSIONS - identifiers)} "
            f"extra={sorted(identifiers - EXPECTED_BUILD_STAGE_EXCLUSIONS)}"
        )

    # Every Python process in the authoritative shell must be either a test,
    # a catalogued validation entrypoint, or one of the explicit build stages.
    covered_lines: set[int] = set()
    for match in exclusion_matches:
        if "python3" in match.group(0):
            position = match.start() + match.group(0).index("python3")
            covered_lines.add(text.count("\n", 0, position) + 1)
    for pattern in (
        r'^\s*(?:feedback_stage\s+"[^"]+"\s+)?(?:python3|feedback_selected_python)\s+misc/ios/test_[^\s\\]+\.py',
        r'^\s+openal_fields=\$\(feedback_stage "[^"]+" python3 "\$REPO_ROOT/misc/ios/openal_provider_contract\.py" (?:configured|artifact)',
        r'^feedback_stage "[^"]+" python3 misc/ios/ui_contract_check\.py',
        r'^\s+feedback_stage "[^"]+" python3 misc/ios/shadercheck/glsl_es_check\.py --macro-contract',
        r'^\s+out=\$\(feedback_stage "[^"]+" python3 misc/ios/shadercheck/(?:glsl_es_check|link_check)\.py',
        r'^\s*shader_cache_output=\$\(feedback_selected_raw "\$origin" python3 "\$REPO_ROOT/misc/ios/shader_cache\.py"',
        r'^\s*python3 misc/ios/shadercheck/glsl_es_check\.py --glslang "\$GLSLANG" --strict',
        r'^\s*python3 misc/ios/shadercheck/link_check\.py --strict',
        r'^\s*(?:env\s+(?:PYTHONPATH="[^"]+"|-u PYTHONPATH)\s+)?python3 "\$@"',
        r'^\s*python3 "\$REPO_ROOT/misc/ios/test_feedback\.py" "\$@"',
    ):
        for match in re.finditer(pattern, text, re.MULTILINE):
            position = match.start() + match.group(0).index("python3")
            covered_lines.add(text.count("\n", 0, position) + 1)
    python_lines = {
        number for number, line in enumerate(text.splitlines(), 1)
        if "python3" in line and not line.lstrip().startswith("#")
    }
    if python_lines != covered_lines:
        raise CatalogError(
            f"unclassified Python gate command lines: {sorted(python_lines - covered_lines)}; "
            f"stale anchors: {sorted(covered_lines - python_lines)}"
        )


def parse_shell_cases(root: Path = ROOT) -> list[str]:
    text = (root / "misc/ios/ui_automation/test_log_oracles.sh").read_text(encoding="utf-8")
    names = re.findall(r"^\s*expect_(?:pass|fail)\s+([A-Za-z0-9_-]+)", text, re.MULTILINE)
    identifiers = [
        f"sh:misc/ios/ui_automation/test_log_oracles.sh::{name}"
        for name in names
    ]
    if len(identifiers) != 12 or len(set(identifiers)) != len(identifiers):
        raise CatalogError(f"shell oracle inventory drift: {len(identifiers)} cases")
    return identifiers


def parse_xctest_cases(root: Path = ROOT) -> list[str]:
    path = root / "misc/ios/ui_automation/OpenXRayUITests.m"
    text = path.read_text(encoding="utf-8")
    methods = re.findall(r"^-\s*\(void\)(test[A-Za-z0-9_]+)", text, re.MULTILINE)
    identifiers = [
        f"xctest:OpenXRayUITests/OpenXRayUITests/{method}"
        for method in methods
    ]
    if len(identifiers) != 4 or len(set(identifiers)) != len(identifiers):
        raise CatalogError(f"XCTest inventory drift: {len(identifiers)} cases")
    return identifiers


def command_json(path: Path, *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(path), *arguments],
        cwd=ROOT, check=False, text=True, capture_output=True,
    )
    if completed.returncode != 0:
        raise CatalogError(f"{relative(path)} {' '.join(arguments)} failed: {completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CatalogError(f"{relative(path)} did not emit JSON") from error


def parse_shader_cases(root: Path = ROOT) -> tuple[list[str], list[str], list[str]]:
    shader = command_json(root / "misc/ios/shadercheck/glsl_es_check.py", "--list-json")
    links = command_json(root / "misc/ios/shadercheck/link_check.py", "--list-json")
    baseline = shader.get("baseline")
    variants = shader.get("variants")
    link_ids = links.get("links")
    if not all(isinstance(item, list) and all(isinstance(value, str) for value in item)
               for item in (baseline, variants, link_ids)):
        raise CatalogError("shader list schema drift")
    if len(baseline) != 279 or len(variants) != 17 or len(link_ids) != 137:
        raise CatalogError(
            f"shader inventory drift: baseline={len(baseline)} variants={len(variants)} links={len(link_ids)}"
        )
    return baseline, variants, link_ids


def validate_shader_shared_specs(path: Path) -> None:
    """Prove list and execution paths reference the same variant spec objects."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise CatalogError(f"cannot parse shared shader specs: {error}") from error
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    for name in ("build_ssr_profiles", "list_cases_json", "main"):
        if name not in functions:
            raise CatalogError(f"shared shader spec function missing: {name}")

    def call_count(function: str, called: str) -> int:
        return sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == called
            for node in ast.walk(functions[function])
        )

    def name_count(function: str, referenced: str) -> int:
        return sum(
            isinstance(node, ast.Name) and node.id == referenced
            for node in ast.walk(functions[function])
        )

    if call_count("list_cases_json", "build_ssr_profiles") != 1:
        raise CatalogError("SSR list path does not consume build_ssr_profiles")
    if call_count("main", "build_ssr_profiles") != 1:
        raise CatalogError("SSR execution path does not consume build_ssr_profiles")
    for shared in ("LOW_SETTINGS_TARGETS", "SSAO_PROFILES"):
        if name_count("list_cases_json", shared) < 1 or name_count("main", shared) < 1:
            raise CatalogError(f"shader list/execution do not share {shared}")


def host_feedback_tooling_ids(root: Path = ROOT) -> list[str]:
    """Map imported fixture-contract cases to their sole gate entrypoint."""
    identifiers = parse_python_methods(root / HOST_FEEDBACK_TOOLING_PATH)
    identifiers.extend(
        identifier.replace(
            f"py:{HOST_FEEDBACK_SUPPORT_PATH}::",
            f"py:{HOST_FEEDBACK_TOOLING_PATH}::",
            1,
        )
        for identifier in parse_python_methods(root / HOST_FEEDBACK_SUPPORT_PATH)
    )
    return identifiers


def profile_ids(root: Path = ROOT) -> dict[str, list[str]]:
    unconditional, shader_only = parse_build_check_python(root)
    python_ids: list[str] = []
    for item in unconditional + shader_only:
        # This entrypoint is executed unconditionally by build_check, but its
        # five Phase 1A contract cases intentionally remain outside the frozen
        # historical central-python/legacy-python aggregates.
        if item == HOST_FEEDBACK_TOOLING_PATH:
            continue
        path = root / item
        if item.endswith("test_archive_completed_artifacts.py"):
            python_ids.extend(parse_archive_generated_methods(path))
        else:
            python_ids.extend(parse_python_methods(path))
    ci = parse_python_methods(root / "misc/ios/test_ios_ci_contract.py")
    host = (
        parse_python_methods(root / "misc/ios/test_cleanup_simulator_work.py")
        + parse_python_methods(root / "misc/ios/ui_automation/test_prepare_scheme.py")
    )
    phase0 = parse_python_methods(root / "misc/ios/test_test_feedback.py")
    retail_profiles = host_feedback_tooling_ids(root)
    baseline, variants, links = parse_shader_cases(root)
    return {
        "central-python": python_ids,
        "ci-contract": ci,
        "host-infra": host,
        "phase0-tooling": phase0,
        "host-feedback-tooling": retail_profiles,
        "cpp": parse_cpp_executions(root),
        "shell": parse_shell_cases(root),
        "shader-baseline": baseline,
        "shader-variants": variants,
        "shader-links": links,
        "xctest": parse_xctest_cases(root),
        "retail-simulator": [
            item for item in python_ids
            if "test_retail_simulator.py" in item
        ],
    }


def catalog_entrypoint_ids(catalog: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for entry in catalog["entrypoints"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("entrypoint_id"), str):
            raise CatalogError("catalog entrypoint lacks entrypoint_id")
        entrypoint = entry["entrypoint_id"]
        if entrypoint in values:
            raise CatalogError(f"duplicate catalog entrypoint: {entrypoint}")
        values.add(entrypoint)
        required = ("kind", "selected_profiles", "labels", "resources",
                    "timeout_seconds", "timeout_policy", "explicit_inputs",
                    "input_mapping_complete")
        if any(key not in entry for key in required):
            raise CatalogError(f"catalog entrypoint incomplete: {entrypoint}")
        if entry["timeout_policy"] != "report-only":
            raise CatalogError(f"catalog entrypoint timeout must be report-only: {entrypoint}")
        if entry["input_mapping_complete"] is not False:
            raise CatalogError(f"Phase 0A dependency mapping is not proven complete: {entrypoint}")
        if not entry["labels"] or not entry["resources"] or not entry["explicit_inputs"]:
            raise CatalogError(f"catalog entrypoint metadata is empty: {entrypoint}")
        for field in ("selected_profiles", "labels", "resources"):
            values_for_field = entry[field]
            if (not isinstance(values_for_field, list)
                    or any(not isinstance(value, str) or not value for value in values_for_field)
                    or len(values_for_field) != len(set(values_for_field))):
                raise CatalogError(f"catalog {field} must be unique non-empty strings: {entrypoint}")
        unknown_profiles = set(entry["selected_profiles"]) - {
            "engine", "shaders", "device", "full", "fast",
        }
        if unknown_profiles:
            raise CatalogError(f"catalog profile is not an existing gate profile: {entrypoint}")
    return values


def cpp_entrypoint_ids(root: Path = ROOT) -> set[str]:
    return {identifier.rsplit("@", 1)[0] for identifier in parse_cpp_executions(root)}


def expected_entrypoints(root: Path = ROOT) -> set[str]:
    unconditional, shader_only = parse_build_check_python(root)
    return {
        *(f"python::{path}" for path in unconditional + shader_only),
        "python::misc/ios/test_ios_ci_contract.py",
        "python::misc/ios/test_cleanup_simulator_work.py",
        "python::misc/ios/ui_automation/test_prepare_scheme.py",
        "python::misc/ios/test_test_feedback.py",
        "utility::retail-test-profiles",
        *cpp_entrypoint_ids(root),
        *parse_gate_family_entrypoints(root),
        "xctest::OpenXRayUITests",
        *parse_gate_validation_entrypoints(root),
    }


def audited_digest(name: str, ids: list[str], catalog: dict[str, Any]) -> str:
    """Freshly reproduce and compare the exact historical audited-ID digest."""
    frozen = catalog["frozen"]["groups"].get(name)
    if not isinstance(frozen, dict):
        raise CatalogError(f"missing frozen group: {name}")
    actual_count = len(ids)
    actual_digest = digest(ids)
    audit = frozen.get("audited_sha256")
    if not isinstance(audit, str) or not re.fullmatch(r"[0-9a-f]{64}", audit):
        raise CatalogError(f"invalid audited digest for {name}")
    if frozen.get("count") != actual_count or audit != actual_digest:
        raise CatalogError(
            f"audited inventory drift for {name}: count={actual_count} sha256={actual_digest}"
        )
    return actual_digest


def group_report(name: str, ids: list[str], catalog: dict[str, Any]) -> dict[str, Any]:
    frozen = catalog["frozen"]["groups"].get(name)
    if frozen is None:
        return {"count": len(ids)}
    return {"count": len(ids), "audited_sha256": audited_digest(name, ids, catalog)}


def validate(catalog: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    if catalog.get("profiles") != ["engine", "shaders", "device", "full", "fast"]:
        raise CatalogError("catalog profile vocabulary drift")
    validate_build_stage_exclusions(catalog, root)
    validate_shader_shared_specs(root / "misc/ios/shadercheck/glsl_es_check.py")
    actual_entrypoints = catalog_entrypoint_ids(catalog)
    expected_records = expected_entrypoints(root)
    if actual_entrypoints != expected_records:
        missing = sorted(expected_records - actual_entrypoints)
        extra = sorted(actual_entrypoints - expected_records)
        raise CatalogError(f"catalog entrypoint drift: missing={missing} extra={extra}")
    groups = profile_ids(root)
    expected = {
        "central-python": 711,
        "ci-contract": 79,
        "host-infra": 10,
        "cpp": 14,
        "shell": 12,
        "shader-baseline": 279,
        "shader-variants": 17,
        "shader-links": 137,
        "xctest": 4,
        "retail-simulator": 102,
    }
    if {name: len(groups[name]) for name in expected} != expected:
        raise CatalogError("inventory count drift")
    if not groups["phase0-tooling"]:
        raise CatalogError("Phase 0 tooling tests missing")
    if not groups["host-feedback-tooling"]:
        raise CatalogError("retail profile tooling tests missing")
    all_python = groups["central-python"] + groups["ci-contract"] + groups["host-infra"]
    if len(all_python) != 800:
        raise CatalogError(f"legacy Python total drift: {len(all_python)}")
    baseline = catalog["frozen"].get("baseline")
    if baseline != {
        "central_python_files": 27, "central_python_cases": 711,
        "ci_files": 1, "ci_cases": 79,
        "preexisting_host_infra_files": 2, "preexisting_host_infra_cases": 10,
        "legacy_python_files": 30, "legacy_python_cases": 800,
        "retail_simulator_cases": 102,
    }:
        raise CatalogError("frozen legacy baseline metadata drift")
    catalog_by_id = {entry["entrypoint_id"]: entry for entry in catalog["entrypoints"]}
    unconditional, shader_only = parse_build_check_python(root)
    for path in unconditional + shader_only:
        if path == HOST_FEEDBACK_TOOLING_PATH:
            identifiers = host_feedback_tooling_ids(root)
        else:
            identifiers = (
                parse_archive_generated_methods(root / path)
                if path.endswith("test_archive_completed_artifacts.py")
                else parse_python_methods(root / path)
            )
        if catalog_by_id[f"python::{path}"].get("expected_case_count") != len(identifiers):
            raise CatalogError(f"Python entrypoint case count drift: {path}")
    for path in (
        "misc/ios/test_ios_ci_contract.py",
        "misc/ios/test_cleanup_simulator_work.py",
        "misc/ios/ui_automation/test_prepare_scheme.py",
    ):
        if catalog_by_id[f"python::{path}"].get("expected_case_count") != len(
                parse_python_methods(root / path)):
            raise CatalogError(f"non-gate Python entrypoint case count drift: {path}")
    if catalog_by_id["python::misc/ios/test_test_feedback.py"].get(
            "expected_case_count") != len(groups["phase0-tooling"]):
        raise CatalogError("Phase 0 tooling case count drift")
    if catalog_by_id["python::misc/ios/test_retail_test_profiles.py"].get(
            "expected_case_count") != len(groups["host-feedback-tooling"]):
        raise CatalogError("retail profile tooling case count drift")
    for entrypoint in (
        "python::misc/ios/test_ios_ci_contract.py",
        "python::misc/ios/test_cleanup_simulator_work.py",
        "python::misc/ios/ui_automation/test_prepare_scheme.py",
        "python::misc/ios/test_test_feedback.py",
        "utility::retail-test-profiles",
        "xctest::OpenXRayUITests",
    ):
        if catalog_by_id[entrypoint]["selected_profiles"] != []:
            raise CatalogError(f"Phase 0A non-selected entrypoint was selected: {entrypoint}")
    for entrypoint in cpp_entrypoint_ids(root):
        variants = sorted(
            identifier.rsplit("@", 1)[1]
            for identifier in groups["cpp"] if identifier.startswith(entrypoint + "@")
        )
        if sorted(catalog_by_id[entrypoint].get("variants", [])) != variants:
            raise CatalogError(f"C++ variants drift: {entrypoint}")
        if catalog_by_id[entrypoint].get("expected_case_count") != len(variants):
            raise CatalogError(f"C++ execution count drift: {entrypoint}")
    expected_family_counts = {
        "shell::ui-log-oracle": len(groups["shell"]),
        "shader::glsl-es": len(groups["shader-baseline"]) + len(groups["shader-variants"]),
        "shader::link": len(groups["shader-links"]),
        "xctest::OpenXRayUITests": len(groups["xctest"]),
    }
    for entrypoint, count in expected_family_counts.items():
        if catalog_by_id[entrypoint].get("expected_case_count") != count:
            raise CatalogError(f"family entrypoint case count drift: {entrypoint}")
    all_five = ["engine", "shaders", "device", "full", "fast"]
    shader_four = ["shaders", "device", "full", "fast"]
    for path in unconditional:
        if catalog_by_id[f"python::{path}"]["selected_profiles"] != all_five:
            raise CatalogError(f"unconditional Python profile drift: {path}")
    for path in shader_only:
        if catalog_by_id[f"python::{path}"]["selected_profiles"] != shader_four:
            raise CatalogError(f"shader Python profile drift: {path}")
    for entrypoint in cpp_entrypoint_ids(root):
        if catalog_by_id[entrypoint]["selected_profiles"] != all_five:
            raise CatalogError(f"unconditional process profile drift: {entrypoint}")
    for entrypoint, profiles in parse_gate_family_entrypoints(root).items():
        if catalog_by_id[entrypoint]["selected_profiles"] != profiles:
            raise CatalogError(f"gate family profile drift: {entrypoint}")
    for entrypoint, profiles in parse_gate_validation_entrypoints(root).items():
        if catalog_by_id[entrypoint]["selected_profiles"] != profiles:
            raise CatalogError(f"validation entrypoint profile drift: {entrypoint}")
    report = {
        "schema": SCHEMA,
        "selection_authority": "NONE",
        "cache_authority": "NONE",
        "groups": {
            name: group_report(name, ids, catalog)
            for name, ids in groups.items()
        },
        "legacy_python": {
            "count": len(all_python),
            "audited_sha256": audited_digest("legacy-python", all_python, catalog),
        },
        "selected": {
            "ci-contract": False,
            "host-infra": False,
            "xctest": False,
            "reason": "Phase 0A inventory only; existing gate selection remains authoritative",
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "json", "emit", "cache-certificate", "ingest"))
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--id", dest="identifier")
    parser.add_argument("--entrypoint-id")
    parser.add_argument("--result", choices=("PASS", "FAIL", "ERROR", "SKIP", "TIMEOUT", "PLATFORM_SKIP"))
    parser.add_argument("--kind", choices=("case", "stage"), default="stage")
    parser.add_argument("--started-ns", type=int)
    parser.add_argument("--ended-ns", type=int)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--cache-hit", choices=("true", "false", "none"), default="none")
    parser.add_argument("--detail")
    parser.add_argument("--stage", choices=("compile", "link"))
    parser.add_argument("--cache-key")
    parser.add_argument("--output-file", type=Path)
    parser.add_argument("--input-before", default="")
    parser.add_argument("--input-after", default="")
    parser.add_argument("--raw-events", type=Path)
    parser.add_argument("--raw-fd", type=int)
    args = parser.parse_args()
    if args.command == "emit":
        if not args.identifier or not args.entrypoint_id or not args.result or args.started_ns is None:
            parser.error("emit requires --id, --entrypoint-id, --result and --started-ns")
        # The exit status is deliberately always zero.  Shell instrumentation
        # is observational and cannot be allowed to trip set -e / pipefail.
        runtime_event(args.identifier, args.result, args.started_ns, entrypoint_id=args.entrypoint_id,
                      kind=args.kind, exit_code=args.exit_code,
                      cache_hit={"true": True, "false": False, "none": None}[args.cache_hit],
                      detail=args.detail, ended_ns=args.ended_ns)
        return 0
    if args.command == "cache-certificate":
        if not args.stage or not args.entrypoint_id or not args.cache_key or args.output_file is None:
            parser.error("cache-certificate requires --stage, --entrypoint-id, --cache-key and --output-file")
        runtime_cache_certificate(args.stage, args.cache_key, args.output_file,
                                  args.input_before, args.input_after,
                                  entrypoint_id=args.entrypoint_id)
        return 0
    if args.command == "ingest":
        if (not args.entrypoint_id
                or (args.raw_events is None) == (args.raw_fd is None)):
            parser.error("ingest requires --entrypoint-id and exactly one raw transport")
        runtime_ingest(args.entrypoint_id, args.raw_events, raw_fd=args.raw_fd)
        # Ingestion is observer-only: malformed raw data becomes private
        # INCOMPLETE/ERROR evidence rather than a second gate authority.
        return 0
    try:
        report = validate(read_catalog(args.catalog))
    except CatalogError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if args.command == "json":
        print(canonical(report))
    else:
        for name, values in report["groups"].items():
            suffix = f" {values['audited_sha256']}" if "audited_sha256" in values else ""
            print(f"PASS: {name} {values['count']}{suffix}")
        print(f"PASS: legacy-python {report['legacy_python']['count']} "
              f"{report['legacy_python']['audited_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
