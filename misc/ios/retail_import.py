#!/usr/bin/env python3
"""Prepare a verified local retail backup for ``retail_simulator.sh``.

This is deliberately a small, stdlib-only, host-side importer.  It never
touches the supplied backup or its manifest and it never replaces a prepared
destination.  A durable plan and recovery record live next to (not in) the
destination so the published root is exactly ``Documents/`` and ``manifest/``.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unicodedata
from typing import Any

CHUNK = 1024 * 1024
MAX_INT = (1 << 63) - 1
PLAN_SCHEMA = "openxray.retail-import-plan.v2"
RECOVERY_SCHEMA = "openxray.retail-import-recovery.v1"
REQUIRED_MANIFEST_NAMES = ("files.tsv", "required-archives.tsv", "large-files-sha256.tsv")
GENERATED_MANIFEST = "prepared-files.tsv"
GENERATED_MANIFEST_FILES = "prepared-manifest-files.tsv"
RENAME_EXCL = 0x00000004


class ImportError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ImportError(message)


def load_guard() -> Any:
    path = Path(__file__).with_name("retail_simulator_guard.py")
    spec = importlib.util.spec_from_file_location("retail_simulator_guard_for_import", path)
    if spec is None or spec.loader is None:
        fail("could not load retail simulator guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = load_guard()


def canonical(path: str | Path, *, existing: bool) -> Path:
    unresolved = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    value = unresolved.resolve(strict=False)
    if value != unresolved:
        fail(f"path must already be canonical and symlink-free: {unresolved}")
    if existing and not value.exists():
        fail(f"required path does not exist: {value}")
    return value


def lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as error:
        fail(f"cannot stat {label} {path}: {error}")


def require_real_directory(path: Path, label: str) -> None:
    entry = lstat(path, label)
    if not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode):
        fail(f"{label} must be a real directory: {path}")


def require_private_directory_stat(entry: os.stat_result, label: str) -> None:
    if (not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode)
            or entry.st_uid != os.geteuid() or stat.S_IMODE(entry.st_mode) != 0o700):
        fail(f"{label} must be current-user-owned private mode 0700")


def require_private_directory(path: Path, label: str) -> None:
    require_private_directory_stat(lstat(path, label), label)


def disjoint(path: Path, protected: Path) -> bool:
    try:
        path.relative_to(protected)
        return False
    except ValueError:
        try:
            protected.relative_to(path)
            return False
        except ValueError:
            return True


def safe_relative(value: str) -> Path:
    try:
        return GUARD.safe_relative(value)
    except Exception as error:
        fail(str(error))


def safe_manifest_name(value: str) -> str:
    if any(character in value for character in ("\t", "\n", "\r", "\0")):
        fail(f"unsafe manifest filename: {value!r}")
    relative = safe_relative(value)
    if len(relative.parts) != 1 or relative.as_posix() != value:
        fail(f"unsafe manifest filename: {value!r}")
    return value


def collision_key(relative: str) -> str:
    return unicodedata.normalize("NFC", relative).casefold()


def sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        block = os.read(fd, CHUNK)
        if not block:
            break
        digest.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def open_absolute_directory_nofollow(path: Path, label: str) -> int:
    """Open every absolute path component with openat/O_NOFOLLOW."""
    if not path.is_absolute():
        fail(f"{label} path must be absolute: {path}")
    try:
        current = os.open("/", directory_flags())
    except OSError as error:
        fail(f"cannot anchor {label} at filesystem root: {error}")
    try:
        for component in path.parts[1:]:
            try:
                following = os.open(component, directory_flags(), dir_fd=current)
            except OSError as error:
                fail(f"cannot nofollow-open {label} component {component!r}: {error}")
            os.close(current)
            current = following
        info = os.fstat(current)
        if not stat.S_ISDIR(info.st_mode):
            fail(f"{label} is not a directory")
        return current
    except BaseException:
        os.close(current)
        raise


def open_relative_regular_nofollow(root_fd: int, relative: Path, flags: int, label: str) -> int:
    """Open a regular file below an anchored directory without following any symlink."""
    parts = relative.parts
    if not parts:
        fail(f"empty relative path for {label}")
    current = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            try:
                following = os.open(component, directory_flags(), dir_fd=current)
            except OSError as error:
                fail(f"cannot nofollow-open {label} directory {component!r}: {error}")
            os.close(current)
            current = following
        try:
            result = os.open(parts[-1], flags | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
        except OSError as error:
            fail(f"cannot nofollow-open {label}: {error}")
        return result
    finally:
        os.close(current)


def inventory_fd(root_fd: int, label: str, *, private: bool = False) -> dict[str, os.stat_result]:
    """Inventory a directory tree through anchored descriptors only."""
    seen_names: dict[str, str] = {}
    result: dict[str, os.stat_result] = {}
    stack: list[tuple[str, int]] = [("", os.dup(root_fd))]
    try:
        while stack:
            prefix, current = stack.pop()
            try:
                names = sorted(os.listdir(current))
                for name in names:
                    relative = f"{prefix}/{name}" if prefix else name
                    safe_relative(relative)
                    collision = collision_key(relative)
                    previous = seen_names.setdefault(collision, relative)
                    if previous != relative:
                        fail(f"Unicode NFC/casefold collision: {previous!r} vs {relative!r}")
                    try:
                        info = os.stat(name, dir_fd=current, follow_symlinks=False)
                    except OSError as error:
                        fail(f"cannot stat {label} entry {relative}: {error}")
                    if stat.S_ISLNK(info.st_mode):
                        fail(f"symlink is not allowed in {label}: {relative}")
                    if stat.S_ISDIR(info.st_mode):
                        if private:
                            require_private_directory_stat(info, f"{label} directory {relative}")
                        try:
                            child = os.open(name, directory_flags(), dir_fd=current)
                        except OSError as error:
                            fail(f"cannot nofollow-open {label} directory {relative}: {error}")
                        stack.append((relative, child))
                    elif stat.S_ISREG(info.st_mode):
                        if private:
                            require_private_regular(info, f"{label} file {relative}")
                        else:
                            require_regular(info, f"{label} file {relative}")
                        result[relative] = info
                    else:
                        fail(f"special file is not allowed in {label}: {relative}")
            finally:
                os.close(current)
    except BaseException:
        for _, descriptor in stack:
            os.close(descriptor)
        raise
    return dict(sorted(result.items()))


def identity(entry: os.stat_result) -> dict[str, int]:
    return {"dev": entry.st_dev, "ino": entry.st_ino, "size": entry.st_size,
            "mtime_ns": entry.st_mtime_ns}


def require_regular(entry: os.stat_result, label: str, *, hardlink: bool = True) -> None:
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    if hardlink and entry.st_nlink != 1:
        fail(f"{label} must not be a hard link")
    if entry.st_size < 0 or entry.st_size > MAX_INT:
        fail(f"{label} size is outside supported bounds")


def require_private_regular(entry: os.stat_result, label: str) -> None:
    require_regular(entry, label)
    if entry.st_uid != os.geteuid() or stat.S_IMODE(entry.st_mode) != 0o600:
        fail(f"{label} must be current-user-owned private mode 0600")


def stable_file(path: Path, label: str, *, capture: bool = True) -> tuple[dict[str, int], str, bytes]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        fail(f"cannot nofollow-open {label}: {error}")
    try:
        before = os.fstat(fd)
        require_regular(before, label)
        pieces: list[bytes] = []
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, CHUNK)
            if not block:
                break
            if capture:
                pieces.append(block)
            digest.update(block)
        after = os.fstat(fd)
        if identity(before) != identity(after):
            fail(f"{label} changed while being read")
        return identity(before), digest.hexdigest(), b"".join(pieces) if capture else b""
    finally:
        os.close(fd)


def stable_file_at(root_fd: int, relative: Path, label: str, *, capture: bool = True
                   ) -> tuple[dict[str, int], str, bytes]:
    fd = open_relative_regular_nofollow(root_fd, relative, os.O_RDONLY, label)
    try:
        before = os.fstat(fd)
        require_regular(before, label)
        pieces: list[bytes] = []
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, CHUNK)
            if not block:
                break
            if capture:
                pieces.append(block)
            digest.update(block)
        after = os.fstat(fd)
        if identity(before) != identity(after):
            fail(f"{label} changed while being read")
        return identity(before), digest.hexdigest(), b"".join(pieces) if capture else b""
    finally:
        os.close(fd)


def inventory(root: Path) -> list[Path]:
    """Return every regular file, rejecting aliases and hostile entry types."""
    root_fd = open_absolute_directory_nofollow(root, "inventory root")
    try:
        return [root / safe_relative(relative) for relative in inventory_fd(root_fd, "inventory")]
    finally:
        os.close(root_fd)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


def parse_canonical_json(path: Path, schema: str, *, private: bool = False) -> dict[str, Any]:
    if private:
        require_private_regular(lstat(path, path.name), path.name)
    _, _, data = stable_file(path, path.name)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"malformed state {path}: {error}")
    if not isinstance(value, dict) or value.get("schema") != schema or canonical_json(value) != data:
        fail(f"unknown or non-canonical state schema: {path}")
    return value


def write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as error:
        fail(f"cannot create {path}: {error}")
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    fd = open_absolute_directory_nofollow(path, "durability directory")
    try:
        os.fsync(fd)
    except OSError as error:
        fail(f"cannot fsync directory {path}: {error}")
    finally:
        os.close(fd)


def sidecars(final: Path) -> dict[str, Path]:
    base = final.name
    return {key: final.with_name(f".{base}.retail-import.{key}")
            for key in ("plan.json", "recovery.json", "partial", "lock")}


def sidecar_names(final_name: str) -> dict[str, str]:
    return {key: f".{final_name}.retail-import.{key}"
            for key in ("plan.json", "recovery.json", "partial", "lock")}


class DestinationAnchor:
    """Pinned destination parent used for every sidecar and publication operation."""

    def __init__(self, parent: Path):
        self.parent = parent
        self.fd = open_absolute_directory_nofollow(parent, "destination parent")
        info = os.fstat(self.fd)
        self.identity = (info.st_dev, info.st_ino)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def require_path_current(self) -> None:
        try:
            info = self.parent.lstat()
        except OSError as error:
            fail(f"destination parent path became unavailable: {error}")
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (info.st_dev, info.st_ino) != self.identity):
            fail("destination parent path no longer matches its pinned descriptor")

    def stat(self, name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as error:
            fail(f"cannot stat destination entry {name}: {error}")

    def exists(self, name: str) -> bool:
        return self.stat(name) is not None

    def open_directory(self, name: str, label: str) -> int:
        try:
            descriptor = os.open(name, directory_flags(), dir_fd=self.fd)
        except OSError as error:
            fail(f"cannot open {label}: {error}")
        try:
            require_private_directory_stat(os.fstat(descriptor), label)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def ensure_directory(self, name: str, label: str) -> tuple[int, bool]:
        info = self.stat(name)
        created = False
        if info is None:
            try:
                os.mkdir(name, 0o700, dir_fd=self.fd)
                created = True
            except OSError as error:
                fail(f"cannot create {label}: {error}")
            os.fsync(self.fd)
        elif not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            fail(f"{label} must be a real directory")
        try:
            descriptor = os.open(name, directory_flags(), dir_fd=self.fd)
        except OSError as error:
            fail(f"cannot open {label}: {error}")
        try:
            if created:
                os.fchmod(descriptor, 0o700)
                os.fsync(descriptor)
            require_private_directory_stat(os.fstat(descriptor), label)
            return descriptor, created
        except OSError as error:
            os.close(descriptor)
            fail(f"cannot set private mode on {label}: {error}")
        except BaseException:
            os.close(descriptor)
            raise


def stat_at(parent_fd: int, name: str, label: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        fail(f"cannot stat {label}: {error}")


def stable_file_name_at(parent_fd: int, name: str, label: str, *, private: bool = True
                        ) -> tuple[dict[str, int], str, bytes]:
    fd = open_relative_regular_nofollow(parent_fd, Path(name), os.O_RDONLY, label)
    try:
        before = os.fstat(fd)
        if private:
            require_private_regular(before, label)
        else:
            require_regular(before, label)
        digest = hashlib.sha256()
        pieces: list[bytes] = []
        while True:
            block = os.read(fd, CHUNK)
            if not block:
                break
            pieces.append(block)
            digest.update(block)
        after = os.fstat(fd)
        if identity(before) != identity(after):
            fail(f"{label} changed while being read")
        return identity(before), digest.hexdigest(), b"".join(pieces)
    finally:
        os.close(fd)


def write_new_name_at(parent_fd: int, name: str, data: bytes, label: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
    except OSError as error:
        fail(f"cannot create {label}: {error}")
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent_fd)


def parse_canonical_json_at(parent_fd: int, name: str, schema: str, label: str) -> dict[str, Any]:
    _, _, data = stable_file_name_at(parent_fd, name, label, private=True)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"malformed state {label}: {error}")
    if not isinstance(value, dict) or value.get("schema") != schema or canonical_json(value) != data:
        fail(f"unknown or non-canonical state schema: {label}")
    return value


def prepared_rows(plan: dict[str, Any]) -> list[tuple[str, int, str]]:
    return [(entry["path"], entry["identity"]["size"], entry["sha256"])
            for entry in plan["files"]]


def rendered_prepared_manifest(plan: dict[str, Any]) -> bytes:
    rows = prepared_rows(plan)
    lines = ["sha256\tbytes\tpath"] + [f"{digest}\t{size}\t{relative}"
                                             for relative, size, digest in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def rendered_runner_files_manifest(plan: dict[str, Any]) -> bytes:
    """The runner schema requires files.tsv to describe the staged subset."""
    lines = ["bytes\tpath"] + [f"{size}\t{relative}"
                                  for relative, size, _ in prepared_rows(plan)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def rendered_runner_large_manifest(plan: dict[str, Any]) -> bytes:
    """Filter the source large-file contract to paths that were actually staged."""
    source = next(item for item in plan["manifest_files"]
                  if item["name"] == "large-files-sha256.tsv")
    try:
        lines = base64.b64decode(source["bytes_b64"], validate=True).decode("utf-8", "strict").splitlines()
    except (UnicodeDecodeError, ValueError) as error:
        fail(f"immutable large-file manifest is malformed: {error}")
    if not lines or lines[0] != "sha256\tbytes\tpath":
        fail("immutable large-file manifest has invalid header")
    selected = {entry["path"] for entry in plan["files"]}
    rows: list[tuple[str, int, str]] = []
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 3:
            fail("immutable large-file manifest has invalid row")
        digest, size_text, relative = fields
        if relative not in selected:
            continue
        try:
            size = int(size_text)
        except ValueError:
            fail("immutable large-file manifest has invalid size")
        rows.append((relative, size, digest))
    if not rows:
        fail("selected retail plan has no runner-compatible large-file entry")
    return ("sha256\tbytes\tpath\n" + "".join(
        f"{digest}\t{size}\t{relative}\n" for relative, size, digest in sorted(rows)
    )).encode("utf-8")


def rendered_manifest_inventory(entries: dict[str, bytes]) -> bytes:
    rows = [(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in entries.items()]
    return ("sha256\tbytes\tpath\n" + "".join(
        f"{digest}\t{size}\t{name}\n" for name, size, digest in sorted(rows)
    )).encode("utf-8")


def output_manifest_contents(plan: dict[str, Any]) -> dict[str, bytes]:
    expected: dict[str, bytes] = {entry["name"]: base64.b64decode(entry["bytes_b64"])
                                  for entry in plan["manifest_files"]}
    expected.update({GENERATED_MANIFEST: rendered_prepared_manifest(plan),
                     "files.tsv": rendered_runner_files_manifest(plan),
                     "large-files-sha256.tsv": rendered_runner_large_manifest(plan)})
    expected[GENERATED_MANIFEST_FILES] = rendered_manifest_inventory(expected)
    return expected


def source_manifest_plan(manifest: Path) -> tuple[dict[str, int], list[dict[str, Any]]]:
    manifest_fd = open_absolute_directory_nofollow(manifest, "source manifest")
    try:
        root_before = os.fstat(manifest_fd)
        names = sorted(os.listdir(manifest_fd))
        if not set(REQUIRED_MANIFEST_NAMES) <= set(names):
            fail("source manifest is missing a required retail manifest file")
        result = []
        for raw_name in names:
            name = safe_manifest_name(raw_name)
            item, digest, data = stable_file_at(
                manifest_fd, Path(name), f"source manifest {name}", capture=True,
            )
            result.append({"name": name, "identity": item, "sha256": digest,
                           "bytes_b64": base64.b64encode(data).decode("ascii")})
        root_after = os.fstat(manifest_fd)
        if (root_before.st_dev, root_before.st_ino) != (root_after.st_dev, root_after.st_ino):
            fail("source manifest root changed while planning")
        return {"dev": root_before.st_dev, "ino": root_before.st_ino}, result
    finally:
        os.close(manifest_fd)


def captured_files_tsv(manifest_files: list[dict[str, Any]]) -> dict[str, int]:
    matches = [entry for entry in manifest_files if entry.get("name") == "files.tsv"]
    if len(matches) != 1:
        fail("immutable plan must capture exactly one files.tsv")
    try:
        data = base64.b64decode(matches[0]["bytes_b64"], validate=True)
        lines = data.decode("utf-8", errors="strict").splitlines()
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        fail(f"immutable captured files.tsv is malformed: {error}")
    if not lines or lines[0] != "bytes\tpath":
        fail("immutable captured files.tsv has invalid header")
    result: dict[str, int] = {}
    for line_number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != 2:
            fail(f"immutable captured files.tsv has invalid row {line_number}")
        size_text, relative = fields
        safe_relative(relative)
        try:
            size = int(size_text)
        except ValueError:
            fail(f"immutable captured files.tsv has invalid size at row {line_number}")
        if size < 0 or size > MAX_INT or relative in result:
            fail(f"immutable captured files.tsv has duplicate or invalid row {line_number}")
        result[relative] = size
    return result


def make_plan(backup: Path, manifest: Path, with_saves: bool) -> dict[str, Any]:
    # The old guard remains authoritative for the retail schema and allowlist.
    try:
        files, _, _ = GUARD.validate_retail(backup, manifest)
    except Exception as error:
        fail(str(error))
    backup_fd = open_absolute_directory_nofollow(backup, "retail backup")
    try:
        backup_root = os.fstat(backup_fd)
        source_files = inventory_fd(backup_fd, "retail backup")
        if set(source_files) != set(files):
            fail("retail inventory does not exactly match source manifest")
        if any(source_files[relative].st_size != files[relative] for relative in files):
            fail("retail inventory sizes do not exactly match source manifest")
        selected = []
        total = 0
        for relative in sorted(files):
            relative_path = safe_relative(relative)
            if not GUARD.allowed_retail_path(relative_path, with_saves):
                continue
            item, digest, _ = stable_file_at(
                backup_fd, relative_path, f"retail source {relative}", capture=False,
            )
            if item["size"] != files[relative]:
                fail(f"retail source size changed while planning: {relative}")
            if total > MAX_INT - item["size"]:
                fail("retail byte total overflows supported bounds")
            total += item["size"]
            selected.append({"path": relative, "identity": item, "sha256": digest})
        backup_after = os.fstat(backup_fd)
        if (backup_root.st_dev, backup_root.st_ino) != (backup_after.st_dev, backup_after.st_ino):
            fail("retail backup root changed while planning")
    finally:
        os.close(backup_fd)
    if not selected:
        fail("retail allowlist selected no files")
    manifest_root, manifest_files = source_manifest_plan(manifest)
    plan = {"schema": PLAN_SCHEMA,
            "backup_root": {"dev": backup_root.st_dev, "ino": backup_root.st_ino},
            "manifest_root": manifest_root,
            "with_saves": with_saves, "files": selected, "file_count": len(selected),
            "byte_count": total, "manifest_files": manifest_files}
    validate_plan(plan)
    captured_files = captured_files_tsv(manifest_files)
    try:
        guarded_files, _, _ = GUARD.validate_retail(backup, manifest)
    except Exception as error:
        fail(f"post-capture retail guard rejected source snapshot: {error}")
    if guarded_files != captured_files:
        fail("post-capture retail guard did not validate the captured files.tsv snapshot")
    verify_source(plan, backup, manifest)
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    required = {"schema", "backup_root", "manifest_root", "with_saves", "files", "file_count",
                "byte_count", "manifest_files"}
    if set(plan) != required or plan["schema"] != PLAN_SCHEMA:
        fail("unknown retail import plan schema")
    for key, label in (("backup_root", "backup"), ("manifest_root", "manifest")):
        root = plan[key]
        if (not isinstance(root, dict) or set(root) != {"dev", "ino"}
                or any(not isinstance(root[field], int) or root[field] < 0 for field in root)):
            fail(f"retail import plan has invalid {label} identity")
    if not isinstance(plan["with_saves"], bool) or not isinstance(plan["files"], list):
        fail("retail import plan has invalid fields")
    if plan["file_count"] != len(plan["files"]) or not 0 <= plan["byte_count"] <= MAX_INT:
        fail("retail import plan has invalid totals")
    paths = []
    total = 0
    for entry in plan["files"]:
        if (not isinstance(entry, dict) or set(entry) != {"path", "identity", "sha256"}
                or not isinstance(entry["path"], str)):
            fail("retail import plan has an invalid file entry")
        safe_relative(entry["path"])
        paths.append(entry["path"])
        item = entry["identity"]
        if (not isinstance(item, dict) or set(item) != {"dev", "ino", "size", "mtime_ns"}
                or any(not isinstance(item[key], int) for key in item) or item["size"] < 0
                or item["size"] > MAX_INT or not isinstance(entry["sha256"], str)
                or len(entry["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in entry["sha256"])):
            fail("retail import plan has invalid file identity")
        if total > MAX_INT - item["size"]:
            fail("retail import plan total overflows")
        total += item["size"]
    if paths != sorted(paths) or len(paths) != len(set(paths)) or total != plan["byte_count"]:
        fail("retail import plan has non-deterministic files")
    manifests = plan["manifest_files"]
    if (not isinstance(manifests, list) or not manifests
            or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                   for item in manifests)):
        fail("retail import plan has invalid manifest files")
    manifest_names = [item["name"] for item in manifests]
    if (manifest_names != sorted(manifest_names) or len(manifest_names) != len(set(manifest_names))
            or not set(REQUIRED_MANIFEST_NAMES) <= set(manifest_names)):
        fail("retail import plan has invalid manifest files")
    for item in manifests:
        if (set(item) != {"name", "identity", "sha256", "bytes_b64"}
                or not isinstance(item["sha256"], str) or len(item["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in item["sha256"])
                or not isinstance(item["identity"], dict)
                or set(item["identity"]) != {"dev", "ino", "size", "mtime_ns"}
                or any(not isinstance(item["identity"][key], int)
                       for key in item["identity"])):
            fail("retail import plan has invalid manifest file entry")
        safe_manifest_name(item["name"])
        try:
            data = base64.b64decode(item["bytes_b64"], validate=True)
        except (TypeError, ValueError):
            fail("retail import plan has invalid manifest bytes")
        if hashlib.sha256(data).hexdigest() != item["sha256"] or len(data) != item["identity"]["size"]:
            fail("retail import plan has invalid manifest binding")
    captured_files = captured_files_tsv(manifests)
    expected_paths = [
        relative for relative in sorted(captured_files)
        if GUARD.allowed_retail_path(safe_relative(relative), plan["with_saves"])
    ]
    if paths != expected_paths:
        fail("retail import plan paths do not exactly derive from captured files.tsv")
    for entry in plan["files"]:
        if entry["identity"]["size"] != captured_files[entry["path"]]:
            fail(f"retail import plan size differs from captured files.tsv: {entry['path']}")


def load_plan(path: Path) -> tuple[dict[str, Any], str]:
    plan = parse_canonical_json(path, PLAN_SCHEMA, private=True)
    validate_plan(plan)
    data = canonical_json(plan)
    return plan, hashlib.sha256(data).hexdigest()


def load_plan_at(parent_fd: int, name: str) -> tuple[dict[str, Any], str]:
    plan = parse_canonical_json_at(parent_fd, name, PLAN_SCHEMA, "immutable plan")
    validate_plan(plan)
    data = canonical_json(plan)
    return plan, hashlib.sha256(data).hexdigest()


def verify_source(plan: dict[str, Any], backup: Path, manifest: Path) -> None:
    backup_fd = open_absolute_directory_nofollow(backup, "retail backup")
    manifest_fd = -1
    try:
        root = os.fstat(backup_fd)
        if {"dev": root.st_dev, "ino": root.st_ino} != plan["backup_root"]:
            fail("retail backup root identity changed since planning")
        captured_files = captured_files_tsv(plan["manifest_files"])
        source_inventory = inventory_fd(backup_fd, "retail backup")
        if set(source_inventory) != set(captured_files):
            fail("retail backup paths differ from immutable captured files.tsv")
        if any(source_inventory[relative].st_size != captured_files[relative]
               for relative in captured_files):
            fail("retail backup sizes differ from immutable captured files.tsv")
        inventory_snapshot = {
            relative: identity(info) for relative, info in source_inventory.items()
        }
        for entry in plan["files"]:
            item, digest, _ = stable_file_at(
                backup_fd, safe_relative(entry["path"]), f"retail source {entry['path']}",
                capture=False,
            )
            if item != entry["identity"] or digest != entry["sha256"]:
                fail(f"retail source changed since planning: {entry['path']}")
        manifest_fd = open_absolute_directory_nofollow(manifest, "source manifest")
        manifest_root = os.fstat(manifest_fd)
        if {"dev": manifest_root.st_dev, "ino": manifest_root.st_ino} != plan["manifest_root"]:
            fail("source manifest root identity changed since planning")
        manifest_names = sorted(os.listdir(manifest_fd))
        if manifest_names != [entry["name"] for entry in plan["manifest_files"]]:
            fail("source manifest entries changed since planning")
        for entry in plan["manifest_files"]:
            item, digest, data = stable_file_at(
                manifest_fd, Path(safe_manifest_name(entry["name"])),
                f"source manifest {entry['name']}", capture=True,
            )
            if (item != entry["identity"] or digest != entry["sha256"]
                    or base64.b64encode(data).decode("ascii") != entry["bytes_b64"]):
                fail(f"source manifest changed since planning: {entry['name']}")
        source_inventory_after = inventory_fd(backup_fd, "retail backup")
        if set(source_inventory_after) != set(captured_files):
            fail("retail backup paths changed during immutable source verification")
        if any(source_inventory_after[relative].st_size != captured_files[relative]
               for relative in captured_files):
            fail("retail backup sizes changed during immutable source verification")
        if {relative: identity(info) for relative, info in source_inventory_after.items()} \
                != inventory_snapshot:
            fail("retail backup inventory identity changed during source verification")
        root_after = os.fstat(backup_fd)
        manifest_after = os.fstat(manifest_fd)
        if ((root_after.st_dev, root_after.st_ino) != (root.st_dev, root.st_ino)
                or (manifest_after.st_dev, manifest_after.st_ino)
                != (manifest_root.st_dev, manifest_root.st_ino)):
            fail("source root changed during verification")
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
        os.close(backup_fd)


def mkdir_private(path: Path) -> None:
    created = False
    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        require_private_directory(path, "import output directory")
    except OSError as error:
        fail(f"cannot create directory {path}: {error}")
    if created:
        try:
            os.chmod(path, 0o700, follow_symlinks=False)
        except OSError as error:
            fail(f"cannot set private directory mode {path}: {error}")
        fsync_directory(path.parent)


def ensure_private_directory_at(parent_fd: int, name: str, label: str) -> tuple[int, bool]:
    info = stat_at(parent_fd, name, label)
    created = False
    if info is None:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except OSError as error:
            fail(f"cannot create {label}: {error}")
        os.fsync(parent_fd)
    elif not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        fail(f"{label} must be a real directory")
    try:
        descriptor = os.open(name, directory_flags(), dir_fd=parent_fd)
    except OSError as error:
        fail(f"cannot open {label}: {error}")
    try:
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
        require_private_directory_stat(os.fstat(descriptor), label)
        return descriptor, created
    except OSError as error:
        os.close(descriptor)
        fail(f"cannot set private mode on {label}: {error}")
    except BaseException:
        os.close(descriptor)
        raise


def ensure_private_path_at(root_fd: int, relative: Path, label: str) -> int:
    current = os.dup(root_fd)
    try:
        for component in relative.parts:
            following, _ = ensure_private_directory_at(current, component, label)
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def write_exact_new(path: Path, data: bytes) -> None:
    write_new(path, data)


def ensure_output_manifest(partial: Path, plan: dict[str, Any]) -> None:
    manifest = partial / "manifest"
    mkdir_private(manifest)
    expected = output_manifest_contents(plan)
    current = sorted(item.name for item in os.scandir(manifest))
    if current and current != sorted(expected):
        fail("partial manifest contains unknown entries")
    for name, data in expected.items():
        target = manifest / name
        if target.exists() or target.is_symlink():
            require_private_regular(lstat(target, f"partial manifest {name}"),
                                    f"partial manifest {name}")
            _, digest, current_data = stable_file(target, f"partial manifest {name}")
            if current_data != data or digest != hashlib.sha256(data).hexdigest():
                fail(f"partial manifest does not match immutable plan: {name}")
        else:
            write_exact_new(target, data)
    fsync_directory(manifest)


def ensure_output_manifest_fd(partial_fd: int, plan: dict[str, Any]) -> None:
    manifest_fd, _ = ensure_private_directory_at(partial_fd, "manifest", "partial manifest")
    try:
        expected = output_manifest_contents(plan)
        current = sorted(os.listdir(manifest_fd))
        if current and current != sorted(expected):
            fail("partial manifest contains unknown entries")
        for name, data in expected.items():
            safe_manifest_name(name)
            info = stat_at(manifest_fd, name, f"partial manifest {name}")
            if info is not None:
                require_private_regular(info, f"partial manifest {name}")
                _, digest, current_data = stable_file_name_at(
                    manifest_fd, name, f"partial manifest {name}", private=True,
                )
                if current_data != data or digest != hashlib.sha256(data).hexdigest():
                    fail(f"partial manifest does not match immutable plan: {name}")
            else:
                write_new_name_at(manifest_fd, name, data, f"partial manifest {name}")
        os.fsync(manifest_fd)
    finally:
        os.close(manifest_fd)


def verify_prefix(source_fd: int, target_fd: int, count: int, label: str) -> None:
    os.lseek(source_fd, 0, os.SEEK_SET)
    os.lseek(target_fd, 0, os.SEEK_SET)
    remaining = count
    while remaining:
        want = min(CHUNK, remaining)
        source = os.read(source_fd, want)
        target = os.read(target_fd, want)
        if source != target or len(source) != want:
            fail(f"partial prefix is corrupt: {label}")
        remaining -= want


def copy_one(backup: Path, partial: Path | int, entry: dict[str, Any]) -> None:
    relative = safe_relative(entry["path"])
    if isinstance(partial, int):
        partial_fd = os.dup(partial)
    else:
        partial_fd = open_absolute_directory_nofollow(partial, "partial root")
    target_parent_fd = -1
    source_fd = -1
    target_fd = -1
    try:
        require_private_directory_stat(os.fstat(partial_fd), "partial root")
        parent_relative = Path("Documents", *relative.parts[:-1])
        target_parent_fd = ensure_private_path_at(
            partial_fd, parent_relative, f"partial parent for {relative}",
        )
        target_name = relative.parts[-1]
        target_info = stat_at(target_parent_fd, target_name, f"partial file {relative}")
        if target_info is not None and stat.S_ISLNK(target_info.st_mode):
            fail(f"partial file is a symlink: {relative}")
        backup_fd = open_absolute_directory_nofollow(backup, "retail backup")
        try:
            source_fd = open_relative_regular_nofollow(
                backup_fd, relative, os.O_RDONLY, f"retail source {relative}",
            )
        finally:
            os.close(backup_fd)
        before = os.fstat(source_fd)
        require_regular(before, f"retail source {relative}")
        if identity(before) != entry["identity"] or sha256_fd(source_fd) != entry["sha256"]:
            fail(f"retail source changed since planning: {relative}")
        if target_info is not None:
            try:
                target_fd = os.open(
                    target_name, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=target_parent_fd,
                )
            except OSError as error:
                fail(f"cannot open partial file {relative}: {error}")
            existing = os.fstat(target_fd)
            require_private_regular(existing, f"partial file {relative}")
            if existing.st_size > entry["identity"]["size"]:
                fail(f"partial file is longer than planned: {relative}")
            verify_prefix(source_fd, target_fd, existing.st_size, relative.as_posix())
            if existing.st_size == entry["identity"]["size"]:
                if sha256_fd(target_fd) != entry["sha256"]:
                    fail(f"complete partial file hash mismatch: {relative}")
                after = os.fstat(source_fd)
                if identity(after) != entry["identity"] or sha256_fd(source_fd) != entry["sha256"]:
                    fail(f"retail source changed while rechecking complete partial: {relative}")
                os.fsync(target_fd)
                os.fsync(target_parent_fd)
                return
            os.lseek(source_fd, existing.st_size, os.SEEK_SET)
            os.lseek(target_fd, existing.st_size, os.SEEK_SET)
        else:
            try:
                target_fd = os.open(
                    target_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=target_parent_fd,
                )
                os.fchmod(target_fd, 0o600)
            except OSError as error:
                fail(f"cannot create partial file {relative}: {error}")
        while True:
            block = os.read(source_fd, CHUNK)
            if not block:
                break
            view = memoryview(block)
            while view:
                view = view[os.write(target_fd, view):]
        os.fsync(target_fd)
        after = os.fstat(source_fd)
        if identity(after) != entry["identity"] or sha256_fd(source_fd) != entry["sha256"]:
            fail(f"retail source changed while copying: {relative}")
        destination = os.fstat(target_fd)
        if destination.st_size != entry["identity"]["size"] or sha256_fd(target_fd) != entry["sha256"]:
            fail(f"partial copy integrity mismatch: {relative}")
        os.fsync(target_parent_fd)
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        if source_fd >= 0:
            os.close(source_fd)
        if target_parent_fd >= 0:
            os.close(target_parent_fd)
        os.close(partial_fd)


def require_copy_space(partial: Path, plan: dict[str, Any]) -> None:
    """Reject impossible copies before changing a resumable partial tree."""
    current = 0
    documents = partial / "Documents"
    for item in inventory(documents):
        size = lstat(item, "partial file").st_size
        if size < 0 or current > MAX_INT - size:
            fail("partial byte total overflows supported bounds")
        current += size
    if current > plan["byte_count"]:
        fail("partial byte total exceeds immutable plan")
    try:
        stats = os.statvfs(partial)
        free = stats.f_bavail * stats.f_frsize
    except OSError as error:
        fail(f"cannot determine destination free space: {error}")
    if free < plan["byte_count"] - current:
        fail("insufficient destination free space for immutable retail plan")


def require_copy_space_fd(partial_fd: int, plan: dict[str, Any]) -> None:
    documents_fd, _ = ensure_private_directory_at(partial_fd, "Documents", "partial Documents")
    try:
        current = 0
        for info in inventory_fd(documents_fd, "partial Documents", private=True).values():
            if info.st_size < 0 or current > MAX_INT - info.st_size:
                fail("partial byte total overflows supported bounds")
            current += info.st_size
        if current > plan["byte_count"]:
            fail("partial byte total exceeds immutable plan")
        try:
            stats = os.fstatvfs(partial_fd)
        except OSError as error:
            fail(f"cannot determine destination free space: {error}")
        if stats.f_bavail * stats.f_frsize < plan["byte_count"] - current:
            fail("insufficient destination free space for immutable retail plan")
    finally:
        os.close(documents_fd)


def validate_partial(partial: Path | int, plan: dict[str, Any], *, complete: bool) -> None:
    if isinstance(partial, int):
        partial_fd = os.dup(partial)
    else:
        partial_fd = open_absolute_directory_nofollow(partial, "partial root")
    documents_fd = -1
    manifest_fd = -1
    try:
        require_private_directory_stat(os.fstat(partial_fd), "partial root")
        if sorted(os.listdir(partial_fd)) != ["Documents", "manifest"]:
            fail("partial root contains unknown entries")
        documents_fd, _ = ensure_private_directory_at(partial_fd, "Documents", "partial Documents")
        manifest_fd, _ = ensure_private_directory_at(partial_fd, "manifest", "partial manifest")
        expected = {entry["path"]: entry for entry in plan["files"]}
        require_private_directory_stat(os.fstat(documents_fd), "partial Documents")
        actual = inventory_fd(documents_fd, "partial Documents", private=True)
        for relative, info_stat in actual.items():
            if relative not in expected:
                fail(f"partial Documents contains unknown file: {relative}")
            entry = expected[relative]
            info, digest, _ = stable_file_at(
                documents_fd, safe_relative(relative), f"partial file {relative}", capture=False,
            )
            if info != identity(info_stat):
                fail(f"partial file identity changed while validating: {relative}")
            if info["size"] > entry["identity"]["size"]:
                fail(f"partial file is longer than planned: {relative}")
            if complete and (info["size"] != entry["identity"]["size"] or digest != entry["sha256"]):
                fail(f"partial file is incomplete or corrupt: {relative}")
        if complete and len(actual) != len(expected):
            fail("partial Documents is missing planned files")
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
        if documents_fd >= 0:
            os.close(documents_fd)
        os.close(partial_fd)


def parse_prepared_manifest(data: bytes) -> dict[str, tuple[int, str]]:
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        fail(f"prepared manifest is not UTF-8: {error}")
    if not lines or lines[0] != "sha256\tbytes\tpath":
        fail("prepared manifest has invalid header")
    result: dict[str, tuple[int, str]] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 3:
            fail("prepared manifest has invalid row")
        digest, text_size, relative = fields
        safe_relative(relative)
        try:
            size = int(text_size)
        except ValueError:
            fail("prepared manifest has invalid byte count")
        if (size < 0 or size > MAX_INT or len(digest) != 64 or
                any(char not in "0123456789abcdef" for char in digest) or relative in result):
            fail("prepared manifest has invalid row")
        result[relative] = (size, digest)
    return result


def validate_runner_guard_fd(root_fd: int) -> None:
    """Run the existing runner validator with cwd pinned to the opened root."""
    try:
        previous_fd = os.open(".", directory_flags())
    except OSError as error:
        fail(f"cannot pin current directory for runner compatibility check: {error}")
    try:
        try:
            os.fchdir(root_fd)
        except OSError as error:
            fail(f"cannot anchor runner compatibility check: {error}")
        try:
            GUARD.validate_retail(Path("Documents"), Path("manifest"))
        except Exception as error:
            fail(f"prepared output is not runner compatible: {error}")
    finally:
        try:
            os.fchdir(previous_fd)
        except OSError as error:
            os.close(previous_fd)
            fail(f"cannot restore current directory after runner compatibility check: {error}")
        os.close(previous_fd)


def verify_prepared_exact_fd(final_fd: int, plan: dict[str, Any] | None) -> dict[str, Any]:
    """Hash and identity-bind the complete prepared tree through one root fd."""
    documents_fd = -1
    manifest_fd = -1
    try:
        root_before = os.fstat(final_fd)
        require_private_directory_stat(root_before, "prepared root")
        if sorted(os.listdir(final_fd)) != ["Documents", "manifest"]:
            fail("prepared root must contain only Documents and manifest")
        try:
            documents_fd = os.open("Documents", directory_flags(), dir_fd=final_fd)
            manifest_fd = os.open("manifest", directory_flags(), dir_fd=final_fd)
        except OSError as error:
            fail(f"cannot open prepared child directory: {error}")
        documents_before = os.fstat(documents_fd)
        manifest_before = os.fstat(manifest_fd)
        require_private_directory_stat(documents_before, "prepared Documents")
        require_private_directory_stat(manifest_before, "prepared manifest")
        names = sorted(os.listdir(manifest_fd))
        for name in names:
            safe_manifest_name(name)
        if not set((*REQUIRED_MANIFEST_NAMES, GENERATED_MANIFEST, GENERATED_MANIFEST_FILES)) <= set(names):
            fail("prepared manifest contains unknown entries")
        manifest_entries = inventory_fd(manifest_fd, "prepared manifest", private=True)
        if set(manifest_entries) != set(names):
            fail("prepared manifest must contain only top-level regular files")
        inventory_info, inventory_digest, manifest_inventory = stable_file_at(
            manifest_fd, Path(GENERATED_MANIFEST_FILES), "prepared manifest inventory",
        )
        if inventory_info != identity(manifest_entries[GENERATED_MANIFEST_FILES]):
            fail("prepared manifest inventory identity changed during verification")
        manifest_snapshot: dict[str, dict[str, Any]] = {
            GENERATED_MANIFEST_FILES: {
                "identity": inventory_info, "sha256": inventory_digest,
            },
        }
        manifest_contents = {GENERATED_MANIFEST_FILES: manifest_inventory}
        expected_manifest = parse_prepared_manifest(manifest_inventory)
        if set(expected_manifest) != set(names) - {GENERATED_MANIFEST_FILES}:
            fail("prepared manifest contains unknown entries")
        for name, (size, digest) in expected_manifest.items():
            info, actual_digest, actual_data = stable_file_at(
                manifest_fd, Path(safe_manifest_name(name)), f"prepared manifest file {name}",
                capture=True,
            )
            if info != identity(manifest_entries[name]):
                fail(f"prepared manifest file identity changed during verification: {name}")
            if info["size"] != size or actual_digest != digest:
                fail(f"prepared manifest integrity mismatch: {name}")
            manifest_snapshot[name] = {"identity": info, "sha256": actual_digest}
            manifest_contents[name] = actual_data
        data = manifest_contents[GENERATED_MANIFEST]
        expected = parse_prepared_manifest(data)
        actual = inventory_fd(documents_fd, "prepared Documents", private=True)
        if set(actual) != set(expected):
            fail("prepared Documents paths do not exactly match prepared manifest")
        document_snapshot: dict[str, dict[str, Any]] = {}
        for relative, (size, digest) in expected.items():
            info, actual_digest, _ = stable_file_at(
                documents_fd, safe_relative(relative), f"prepared file {relative}", capture=False,
            )
            if info != identity(actual[relative]):
                fail(f"prepared file identity changed during verification: {relative}")
            if info["size"] != size or actual_digest != digest:
                fail(f"prepared file integrity mismatch: {relative}")
            document_snapshot[relative] = {"identity": info, "sha256": actual_digest}
        if plan is not None:
            projection = output_manifest_contents(plan)
            if set(projection) != set(names):
                fail("prepared manifest does not match immutable plan projection")
            for name, expected_data in projection.items():
                if manifest_contents[name] != expected_data:
                    fail(f"prepared manifest drifted from immutable plan: {name}")
        root_after = os.fstat(final_fd)
        documents_after = os.fstat(documents_fd)
        manifest_after = os.fstat(manifest_fd)
        directory_before = (identity(root_before), identity(documents_before), identity(manifest_before))
        directory_after = (identity(root_after), identity(documents_after), identity(manifest_after))
        if directory_after != directory_before:
            fail("prepared directory identity changed during exact verification")
        return {
            "directories": directory_after,
            "manifest": manifest_snapshot,
            "documents": document_snapshot,
        }
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
        if documents_fd >= 0:
            os.close(documents_fd)


def verify_prepared(final: Path | int, plan: dict[str, Any] | None = None,
                    *, guard_root: Path | None = None) -> dict[str, Any]:
    if isinstance(final, int):
        final_fd = os.dup(final)
    else:
        require_private_directory(final, "prepared root")
        final_fd = open_absolute_directory_nofollow(final, "prepared root")
    try:
        before_guard = verify_prepared_exact_fd(final_fd, plan)
        validate_runner_guard_fd(final_fd)
        after_guard = verify_prepared_exact_fd(final_fd, plan)
        if after_guard != before_guard:
            fail("prepared publication changed during runner compatibility validation")
        return after_guard
    finally:
        os.close(final_fd)


def rename_exclusive(source: Path, destination: Path) -> None:
    if sys.platform != "darwin":
        fail("exclusive renamex_np publication is unavailable on this platform")
    try:
        function = ctypes.CDLL(None, use_errno=True).renamex_np
    except AttributeError:
        fail("exclusive renamex_np publication is unavailable")
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(os.fsencode(source), os.fsencode(destination), RENAME_EXCL) != 0:
        error = ctypes.get_errno()
        fail(f"exclusive publication rename failed: {os.strerror(error)}")


def rename_exclusive_at(parent_fd: int, source_name: str, destination_name: str) -> None:
    if sys.platform != "darwin":
        fail("exclusive renameatx_np publication is unavailable on this platform")
    try:
        function = ctypes.CDLL(None, use_errno=True).renameatx_np
    except AttributeError:
        fail("exclusive renameatx_np publication is unavailable")
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                         ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(parent_fd, os.fsencode(source_name), parent_fd, os.fsencode(destination_name),
                RENAME_EXCL) != 0:
        error = ctypes.get_errno()
        fail(f"exclusive descriptor-relative publication rename failed: {os.strerror(error)}")


def reconcile_published_parent(final: Path) -> None:
    try:
        fsync_directory(final.parent)
    except ImportError as error:
        fail(f"published/durability uncertain: {error}")


def reconcile_published_parent_fd(parent_fd: int) -> None:
    try:
        os.fsync(parent_fd)
    except OSError as error:
        fail(f"published/durability uncertain: cannot fsync pinned destination parent: {error}")


def lock(path: Path) -> int:
    existing = path.exists() or path.is_symlink()
    if existing:
        info = lstat(path, "import lock")
        require_private_regular(info, "import lock")
    fd = -1
    try:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        flags |= 0 if existing else os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        if not existing:
            os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        require_private_regular(info, "import lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except ImportError:
        if fd >= 0:
            os.close(fd)
        raise
    except BlockingIOError:
        if fd >= 0:
            os.close(fd)
        fail("retail import is already running")
    except OSError as error:
        if fd >= 0:
            os.close(fd)
        fail(f"cannot acquire retail import lock: {error}")


def lock_at(parent_fd: int, name: str) -> int:
    info = stat_at(parent_fd, name, "import lock")
    existing = info is not None
    if info is not None:
        require_private_regular(info, "import lock")
    descriptor = -1
    try:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        flags |= 0 if existing else os.O_CREAT | os.O_EXCL
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        if not existing:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.fsync(parent_fd)
        require_private_regular(os.fstat(descriptor), "import lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except ImportError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except BlockingIOError:
        if descriptor >= 0:
            os.close(descriptor)
        fail("retail import is already running")
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        fail(f"cannot acquire retail import lock: {error}")


def prepared_runner_lock(prepared: Path) -> int:
    """Take the importer sidecar's shared lock for one fixed runner contract."""
    root = canonical(prepared, existing=True)
    require_private_directory(root, "prepared root")
    parent_fd = open_absolute_directory_nofollow(root.parent, "prepared importer parent")
    lock_name = sidecar_names(root.name)["lock"]
    descriptor = -1
    try:
        descriptor = os.open(lock_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                             dir_fd=parent_fd)
        require_private_regular(os.fstat(descriptor), "prepared importer lock")
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return descriptor
    except ImportError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except BlockingIOError:
        if descriptor >= 0:
            os.close(descriptor)
        fail("prepared retail importer is currently mutating the root")
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        fail(f"cannot acquire prepared importer shared lock: {error}")
    finally:
        os.close(parent_fd)


def verify_inherited_prepared_lock(prepared: Path, descriptor: int) -> None:
    """Prove an inherited fd is the exact importer sidecar and refresh LOCK_SH."""
    if not isinstance(descriptor, int) or descriptor < 3:
        fail("prepared runner lock fd is invalid")
    root = canonical(prepared, existing=True)
    require_private_directory(root, "prepared root")
    parent_fd = open_absolute_directory_nofollow(root.parent, "prepared importer parent")
    expected_fd = -1
    try:
        expected_fd = os.open(
            sidecar_names(root.name)["lock"],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd,
        )
        expected = os.fstat(expected_fd)
        inherited = os.fstat(descriptor)
        require_private_regular(expected, "prepared importer lock")
        require_private_regular(inherited, "inherited prepared importer lock")
        if (expected.st_dev, expected.st_ino) != (inherited.st_dev, inherited.st_ino):
            fail("inherited prepared runner lock fd does not identify the importer sidecar")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("inherited prepared runner lock cannot hold LOCK_SH")
        refreshed = os.fstat(descriptor)
        if (refreshed.st_dev, refreshed.st_ino) != (expected.st_dev, expected.st_ino):
            fail("inherited prepared runner lock changed while refreshing LOCK_SH")
    except OSError as error:
        fail(f"cannot verify inherited prepared runner lock: {error}")
    finally:
        if expected_fd >= 0:
            os.close(expected_fd)
        os.close(parent_fd)


def run_prepared_runner(prepared: Path, runner: Path, arguments: list[str]) -> int:
    """Verify and lock a prepared root while invoking only the sibling Simulator runner.

    This deliberately is not an execution helper: both the target script and
    its first-class ``--prepared`` argument are fixed before a child starts.
    """
    if sys.platform != "darwin":
        fail("prepared clone staging requires Darwin APFS")
    root = canonical(prepared, existing=True)
    expected_runner = Path(__file__).with_name("retail_simulator.sh").resolve(strict=True)
    actual_runner = canonical(runner, existing=True)
    if actual_runner != expected_runner or not arguments:
        fail("prepared runner wrapper only permits misc/ios/retail_simulator.sh")
    prepared_indexes = [index for index, value in enumerate(arguments) if value == "--prepared"]
    if len(prepared_indexes) != 1:
        fail("prepared runner wrapper requires exactly one child --prepared")
    prepared_index = prepared_indexes[0]
    if prepared_index + 1 >= len(arguments) or arguments[prepared_index + 1] != str(root):
        fail("prepared runner child --prepared must exactly match the verified canonical root")
    if "--backup" in arguments or "--manifest" in arguments:
        fail("prepared runner wrapper rejects legacy source arguments")
    lock_fd = prepared_runner_lock(root)
    try:
        verify_prepared(root)
        environment = dict(os.environ)
        environment.pop("OPENXRAY_PREPARED_RUNNER_LOCKED", None)
        environment["OPENXRAY_PREPARED_LOCK_FD"] = str(lock_fd)
        return subprocess.run(
            [str(actual_runner), *arguments], check=False, env=environment,
            pass_fds=(lock_fd,),
        ).returncode
    finally:
        os.close(lock_fd)


def check_paths(backup: Path, manifest: Path, repo: Path, final: Path,
                anchor: DestinationAnchor | None = None) -> dict[str, Path]:
    for root, label in ((backup, "backup"), (manifest, "manifest"), (repo, "repo")):
        require_real_directory(root, label)
    require_real_directory(final.parent, "destination parent")
    protected = (backup, manifest, repo)
    for index, root in enumerate(protected):
        for other in protected[index + 1:]:
            if not disjoint(root, other):
                fail(f"protected roots must be pairwise ancestor-disjoint: {root} vs {other}")
    paths = sidecars(final)
    final_info = anchor.stat(final.name) if anchor is not None else (
        lstat(final, "destination") if final.exists() or final.is_symlink() else None
    )
    if final_info is not None and stat.S_ISLNK(final_info.st_mode):
        fail("destination must not be a symlink")
    for candidate in [final, *paths.values()]:
        for protected_root in protected:
            if not disjoint(candidate, protected_root):
                fail(f"output/recovery path overlaps protected root: {candidate} vs {protected_root}")
    return paths


def plan_and_recovery(anchor: DestinationAnchor, names: dict[str, str], final_name: str,
                      backup: Path, manifest: Path, with_saves: bool
                      ) -> tuple[dict[str, Any], str]:
    plan_name, recovery_name = names["plan.json"], names["recovery.json"]
    recovery_info = anchor.stat(recovery_name)
    plan_info = anchor.stat(plan_name)
    if recovery_info is not None:
        if plan_info is None or stat.S_ISLNK(plan_info.st_mode):
            fail("recovery exists without immutable plan")
        plan, digest = load_plan_at(anchor.fd, plan_name)
        recovery = parse_canonical_json_at(
            anchor.fd, recovery_name, RECOVERY_SCHEMA, "recovery state",
        )
        if set(recovery) != {"schema", "plan_sha256"} or recovery["plan_sha256"] != digest:
            fail("recovery state does not bind the immutable plan")
        if plan["with_saves"] != with_saves:
            fail("recovery plan options differ; create a new destination")
        return plan, digest
    if plan_info is not None:
        if stat.S_ISLNK(plan_info.st_mode):
            fail("immutable plan without recovery must not be a symlink")
        if anchor.exists(final_name) or anchor.exists(names["partial"]):
            fail("immutable plan exists without recovery alongside publication state")
        plan, digest = load_plan_at(anchor.fd, plan_name)
        if plan["with_saves"] != with_saves:
            fail("orphan immutable plan options differ; refusing to rewrite it")
        current = make_plan(backup, manifest, with_saves)
        if canonical_json(current) != canonical_json(plan):
            fail("orphan immutable plan is not current; refusing to rewrite it")
        write_new_name_at(
            anchor.fd, recovery_name,
            canonical_json({"schema": RECOVERY_SCHEMA, "plan_sha256": digest}),
            "recovery state",
        )
        return plan, digest
    plan = make_plan(backup, manifest, with_saves)
    data = canonical_json(plan)
    digest = hashlib.sha256(data).hexdigest()
    write_new_name_at(anchor.fd, plan_name, data, "immutable plan")
    write_new_name_at(
        anchor.fd, recovery_name,
        canonical_json({"schema": RECOVERY_SCHEMA, "plan_sha256": digest}),
        "recovery state",
    )
    return plan, digest


def prepare(args: argparse.Namespace) -> None:
    backup, manifest, repo = (canonical(args.backup, existing=True), canonical(args.manifest, existing=True),
                              canonical(args.repo, existing=True))
    final = canonical(args.destination, existing=False)
    anchor = DestinationAnchor(final.parent)
    lock_fd = -1
    partial_fd = -1
    final_fd = -1
    try:
        check_paths(backup, manifest, repo, final, anchor)
        names = sidecar_names(final.name)
        # A deterministic swap injected after check_paths fails before the first sidecar write.
        anchor.require_path_current()
        lock_fd = lock_at(anchor.fd, names["lock"])
        plan, _ = plan_and_recovery(
            anchor, names, final.name, backup, manifest, args.with_saves,
        )
        final_info = anchor.stat(final.name)
        if final_info is not None:
            if not stat.S_ISDIR(final_info.st_mode) or stat.S_ISLNK(final_info.st_mode):
                fail("existing prepared destination is not a real directory")
            final_fd = anchor.open_directory(final.name, "prepared root")
            pinned_final = (final_info.st_dev, final_info.st_ino)
            if (os.fstat(final_fd).st_dev, os.fstat(final_fd).st_ino) != pinned_final:
                fail("existing prepared destination changed while opening")
            anchor.require_path_current()
            reconcile_published_parent_fd(anchor.fd)
            anchor.require_path_current()
            verify_prepared(final_fd, plan)
            after = anchor.stat(final.name)
            if after is None or (after.st_dev, after.st_ino) != pinned_final:
                fail("existing prepared destination changed during verification")
            anchor.require_path_current()
            print(f"PASS idempotent prepared retail import: {final}")
            return
        partial_info = anchor.stat(names["partial"])
        if partial_info is not None:
            partial_fd = anchor.open_directory(names["partial"], "partial root")
            validate_partial(partial_fd, plan, complete=False)
        else:
            partial_fd, _ = anchor.ensure_directory(names["partial"], "partial root")
            documents_fd, _ = ensure_private_directory_at(
                partial_fd, "Documents", "partial Documents",
            )
            os.close(documents_fd)
        partial_stat = os.fstat(partial_fd)
        pinned_partial = (partial_stat.st_dev, partial_stat.st_ino)
        ensure_output_manifest_fd(partial_fd, plan)
        require_copy_space_fd(partial_fd, plan)
        verify_source(plan, backup, manifest)
        for entry in plan["files"]:
            copy_one(backup, partial_fd, entry)
        validate_partial(partial_fd, plan, complete=True)
        anchor.require_path_current()
        verify_prepared(partial_fd, plan)
        # No output hashing or other potentially long operation may enter this window.
        verify_source(plan, backup, manifest)
        anchor.require_path_current()
        current_partial = anchor.stat(names["partial"])
        descriptor_partial = os.fstat(partial_fd)
        if (current_partial is None
                or (current_partial.st_dev, current_partial.st_ino) != pinned_partial
                or (descriptor_partial.st_dev, descriptor_partial.st_ino) != pinned_partial):
            fail("verified partial root was replaced before publication")
        rename_exclusive_at(anchor.fd, names["partial"], final.name)
        published = anchor.stat(final.name)
        if (published is None or (published.st_dev, published.st_ino) != pinned_partial):
            fail("published destination identity does not match verified partial root")
        if anchor.stat(names["partial"]) is not None:
            fail("partial root still exists after publication")
        final_fd = anchor.open_directory(final.name, "published root")
        final_stat = os.fstat(final_fd)
        if (final_stat.st_dev, final_stat.st_ino) != pinned_partial:
            fail("opened published destination does not match verified partial root")
        anchor.require_path_current()
        reconcile_published_parent_fd(anchor.fd)
        anchor.require_path_current()
        verify_prepared(final_fd, plan)
        published_after = anchor.stat(final.name)
        if published_after is None or (published_after.st_dev, published_after.st_ino) != pinned_partial:
            fail("published destination changed during post-rename verification")
        anchor.require_path_current()
        print(f"PASS prepared retail import: {final}")
    finally:
        if final_fd >= 0:
            os.close(final_fd)
        if partial_fd >= 0:
            os.close(partial_fd)
        if lock_fd >= 0:
            os.close(lock_fd)
        anchor.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--backup", required=True)
    prepare_parser.add_argument("--manifest", required=True)
    prepare_parser.add_argument("--repo", required=True)
    prepare_parser.add_argument("--destination", required=True)
    prepare_parser.add_argument("--with-saves", action="store_true")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--prepared", required=True)
    prepared_runner = commands.add_parser("prepared-runner")
    prepared_runner.add_argument("--prepared", required=True)
    prepared_runner.add_argument("--runner", required=True)
    prepared_runner.add_argument("arguments", nargs=argparse.REMAINDER)
    inherited_lock = commands.add_parser("verify-runner-lock")
    inherited_lock.add_argument("--prepared", required=True)
    inherited_lock.add_argument("--fd", required=True, type=int)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "prepare":
            prepare(args)
        elif args.command == "verify":
            verify_prepared(canonical(args.prepared, existing=True))
            print(f"PASS verified prepared retail import: {canonical(args.prepared, existing=True)}")
        elif args.command == "prepared-runner":
            if args.arguments[:1] == ["--"]:
                args.arguments = args.arguments[1:]
            return run_prepared_runner(Path(args.prepared), Path(args.runner), args.arguments)
        else:
            verify_inherited_prepared_lock(Path(args.prepared), args.fd)
            print("PASS verified inherited prepared importer lock")
    except ImportError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
