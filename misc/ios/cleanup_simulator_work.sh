#!/usr/bin/env bash
set -euo pipefail

# The Python body keeps pathname, PID and descriptor checks in one process.
# The default mode is deliberately read-only; deletion requires --apply.
exec python3 - "$@" <<'PY'
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid


DEFAULT_ROOT = "/Users/patryk/openxray-handoff"
NAME_RE = re.compile(
    r"simulator-work-(?P<stamp>[0-9]{8}-[0-9]{6})-(?P<pid>[1-9][0-9]*)\Z"
)
DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
    os, "O_NOFOLLOW", 0
)
RENAME_EXCL = 0x00000004
QUARANTINE_PREFIX = ".openxray-cleanup-quarantine-"
TEST_MODE_TOKEN = "openxray-cleanup-test-v1"


class CleanupError(RuntimeError):
    pass


@dataclass
class Candidate:
    name: str
    path: Path
    pid: int
    identity: tuple[int, int]
    created_time: float
    effective_time: float
    age_hours: float
    approx_bytes: int
    live_pid: bool
    raced: bool = False
    status: str = ""


def nonnegative_int(value: str) -> int:
    try:
        result = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if result < 0 or str(result) != value:
        raise argparse.ArgumentTypeError("must be a canonical non-negative integer")
    return result


def nonnegative_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if result < 0 or not result < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Safely prune old OpenXRay iOS Simulator work directories."
    )
    result.add_argument("--apply", action="store_true",
                        help="perform deletions; without this flag only report")
    result.add_argument("--keep", type=nonnegative_int, default=3,
                        help="retain this many newest non-live candidates (default: 3)")
    result.add_argument("--min-age-hours", type=nonnegative_float, default=24.0,
                        help="delete only candidates at least this old (default: 24)")
    result.add_argument("--root", default=DEFAULT_ROOT,
                        help=f"work-directory parent (default: {DEFAULT_ROOT})")
    return result


def open_absolute_directory_nofollow(raw: str) -> tuple[Path, int]:
    if not raw or "\n" in raw or "\r" in raw or not os.path.isabs(raw):
        raise CleanupError("root must be a clean absolute path")
    if os.path.normpath(raw) != raw:
        raise CleanupError("root path must be lexically normalized")
    root = Path(raw)
    current = os.open("/", DIRECTORY_FLAGS)
    try:
        for component in root.parts[1:]:
            following = -1
            try:
                following = os.open(component, DIRECTORY_FLAGS, dir_fd=current)
                os.close(current)
                current = following
                following = -1
            finally:
                if following >= 0:
                    os.close(following)
        details = os.fstat(current)
        if not stat.S_ISDIR(details.st_mode):
            raise CleanupError("root is not an existing directory")
        return root, current
    except BaseException as error:
        os.close(current)
        if isinstance(error, FileNotFoundError):
            raise CleanupError("root is not an existing directory") from error
        if isinstance(error, OSError):
            raise CleanupError(f"cannot nofollow-open root: {error}") from error
        raise


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        if error.errno == errno.ESRCH:
            return False
        # Uncertainty is protection: the cleaner may never assume such a PID dead.
        return True
    return True


def lock_cleaner(root_fd: int) -> None:
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise CleanupError("another Simulator work cleaner holds the root lock") from error


def rename_exclusive_between(source_fd: int, source: str,
                             destination_fd: int, destination: str) -> None:
    function = getattr(ctypes.CDLL(None, use_errno=True), "renameatx_np", None)
    if function is None:
        raise CleanupError("macOS renameatx_np is unavailable; refusing apply mode")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    if function(source_fd, os.fsencode(source), destination_fd,
                os.fsencode(destination), RENAME_EXCL) != 0:
        number = ctypes.get_errno()
        raise CleanupError(
            f"exclusive quarantine rename failed: {OSError(number, os.strerror(number))}"
        )


def private_leaf(value: str, label: str, *, test_only: bool = False) -> str:
    if (not value or value in {".", ".."} or Path(value).name != value
            or "/" in value or "\n" in value or "\r" in value):
        raise CleanupError(f"{label} is not a confined leaf")
    if test_only and not value.startswith(".openxray-cleanup-test-"):
        raise CleanupError(f"{label} is outside the private test namespace")
    return value


def create_private_quarantine(root_fd: int) -> tuple[str, int]:
    for _ in range(8):
        name = f"{QUARANTINE_PREFIX}{uuid.uuid4().hex}"
        try:
            os.mkdir(name, 0o700, dir_fd=root_fd)
        except FileExistsError:
            continue
        descriptor = -1
        try:
            descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
            os.fchmod(descriptor, 0o700)
            details = os.fstat(descriptor)
            rebound = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid()
                    or stat.S_IMODE(details.st_mode) != 0o700
                    or (details.st_dev, details.st_ino)
                    != (rebound.st_dev, rebound.st_ino)):
                raise CleanupError("private quarantine identity or metadata is invalid")
            os.fsync(descriptor)
            os.fsync(root_fd)
            return name, descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise
    raise CleanupError("could not allocate an unpredictable private quarantine")


def maybe_run_test_swap(root: Path, root_fd: int, item: Candidate) -> None:
    candidate = os.environ.get("OPENXRAY_CLEANUP_TEST_SWAP_CANDIDATE")
    if candidate is None or candidate != item.name:
        return
    if os.environ.get("OPENXRAY_CLEANUP_TEST_MODE") != TEST_MODE_TOKEN:
        raise CleanupError("cleanup swap hook requires the exact private test token")
    temporary = Path(tempfile.gettempdir()).resolve()
    try:
        root.relative_to(temporary)
    except ValueError as error:
        raise CleanupError("cleanup swap hook is confined to the host temp root") from error
    root_details = os.fstat(root_fd)
    if (root_details.st_uid != os.geteuid()
            or stat.S_IMODE(root_details.st_mode) != 0o700):
        raise CleanupError("cleanup swap hook requires a private 0700 test root")
    replacement = private_leaf(
        os.environ.get("OPENXRAY_CLEANUP_TEST_SWAP_REPLACEMENT", ""),
        "cleanup test replacement", test_only=True)
    displaced = private_leaf(
        os.environ.get("OPENXRAY_CLEANUP_TEST_SWAP_DISPLACED", ""),
        "cleanup test displaced candidate", test_only=True)
    rename_exclusive_between(root_fd, item.name, root_fd, displaced)
    rename_exclusive_between(root_fd, replacement, root_fd, item.name)
    os.fsync(root_fd)


def allocated_bytes(details: os.stat_result) -> int:
    blocks = getattr(details, "st_blocks", 0)
    return blocks * 512 if blocks else details.st_size


def approximate_tree_bytes(root_fd: int, name: str,
                           expected: tuple[int, int]) -> tuple[int, bool]:
    descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != expected:
            return 0, True
        total = 0
        for _, _, _, walk_fd in os.fwalk(
                ".", topdown=True, follow_symlinks=False, dir_fd=descriptor):
            total += allocated_bytes(os.fstat(walk_fd))
            with os.scandir(walk_fd) as entries:
                for entry in entries:
                    details = entry.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(details.st_mode):
                        total += allocated_bytes(details)
        rebound = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        raced = ((rebound.st_dev, rebound.st_ino) != expected
                 or not stat.S_ISDIR(rebound.st_mode))
        return total, raced
    finally:
        os.close(descriptor)


def encoded_time(stamp: str) -> float:
    try:
        parsed = datetime.strptime(stamp, "%Y%m%d-%H%M%S")
    except ValueError as error:
        raise CleanupError("invalid timestamp") from error
    if parsed.strftime("%Y%m%d-%H%M%S") != stamp:
        raise CleanupError("non-canonical timestamp")
    return parsed.timestamp()


def quoted(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=True)


def scan(root: Path, root_fd: int, now: float
         ) -> tuple[list[Candidate], list[tuple[str, str]]]:
    candidates: list[Candidate] = []
    skipped: list[tuple[str, str]] = []
    with os.scandir(root_fd) as entries:
        for entry in entries:
            if not entry.name.startswith("simulator-work-"):
                continue
            match = NAME_RE.fullmatch(entry.name)
            if match is None:
                skipped.append((entry.name, "INVALID_FORMAT"))
                continue
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError:
                skipped.append((entry.name, "STAT_RACE"))
                continue
            if stat.S_ISLNK(details.st_mode):
                skipped.append((entry.name, "SYMLINK"))
                continue
            if not stat.S_ISDIR(details.st_mode):
                skipped.append((entry.name, "NOT_DIRECTORY"))
                continue
            try:
                created = encoded_time(match.group("stamp"))
            except CleanupError:
                skipped.append((entry.name, "INVALID_TIMESTAMP"))
                continue
            identity = (details.st_dev, details.st_ino)
            try:
                size, raced = approximate_tree_bytes(root_fd, entry.name, identity)
            except OSError:
                size, raced = 0, True
            effective = max(created, details.st_mtime)
            candidates.append(Candidate(
                name=entry.name,
                path=root / entry.name,
                pid=int(match.group("pid"), 10),
                identity=identity,
                created_time=created,
                effective_time=effective,
                age_hours=max(0.0, (now - effective) / 3600.0),
                approx_bytes=size,
                live_pid=pid_alive(int(match.group("pid"), 10)),
                raced=raced,
            ))
    return candidates, skipped


def classify(candidates: list[Candidate], keep: int, min_age: float) -> None:
    available = sorted(
        (item for item in candidates if not item.live_pid and not item.raced),
        key=lambda item: (item.effective_time, item.name), reverse=True,
    )
    retained = {item.name for item in available[:keep]}
    for item in candidates:
        if item.live_pid:
            item.status = "PROTECTED_LIVE_PID"
        elif item.raced:
            item.status = "PROTECTED_RACE"
        elif item.name in retained:
            item.status = "RETAINED"
        elif item.age_hours < min_age:
            item.status = "TOO_YOUNG"
        else:
            item.status = "DELETE"


def remove_candidate(root: Path, root_fd: int, quarantine_fd: int,
                     item: Candidate, min_age: float) -> str:
    if pid_alive(item.pid):
        return "PROTECTED_LIVE_PID_RECHECK"
    candidate_fd = -1
    tomb_fd = -1
    try:
        details = os.stat(item.name, dir_fd=root_fd, follow_symlinks=False)
    except OSError:
        return "PROTECTED_RACE"
    if (not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode)
            or (details.st_dev, details.st_ino) != item.identity):
        return "PROTECTED_RACE"
    current_age = max(
        0.0, (time.time() - max(item.created_time, details.st_mtime)) / 3600.0)
    if current_age < min_age:
        return "TOO_YOUNG_RECHECK"
    try:
        candidate_fd = os.open(item.name, DIRECTORY_FLAGS, dir_fd=root_fd)
        pinned = os.fstat(candidate_fd)
        if (pinned.st_dev, pinned.st_ino) != item.identity:
            return "PROTECTED_RACE"
        # This explicit, temp-root-only hook deterministically exercises the
        # exact former TOCTOU window. Production has no enabled mutation hook.
        maybe_run_test_swap(root, root_fd, item)
        tombstone = f"candidate-{uuid.uuid4().hex}"
        rename_exclusive_between(
            root_fd, item.name, quarantine_fd, tombstone)
        os.fsync(root_fd)
        os.fsync(quarantine_fd)
        tomb_fd = os.open(tombstone, DIRECTORY_FLAGS, dir_fd=quarantine_fd)
        tomb = os.fstat(tomb_fd)
        if ((tomb.st_dev, tomb.st_ino) != item.identity
                or (tomb.st_dev, tomb.st_ino)
                != (pinned.st_dev, pinned.st_ino)):
            return "QUARANTINED_IDENTITY_MISMATCH"
        rebound = os.stat(tombstone, dir_fd=quarantine_fd,
                          follow_symlinks=False)
        if ((rebound.st_dev, rebound.st_ino) != item.identity
                or not stat.S_ISDIR(rebound.st_mode)):
            return "QUARANTINED_IDENTITY_RACE"
        if pid_alive(item.pid):
            try:
                rename_exclusive_between(
                    quarantine_fd, tombstone, root_fd, item.name)
                os.fsync(quarantine_fd)
                os.fsync(root_fd)
                return "PROTECTED_LIVE_PID_RECHECK"
            except CleanupError:
                return "PROTECTED_LIVE_PID_QUARANTINED"
        current_age = max(
            0.0, (time.time() - max(item.created_time, tomb.st_mtime)) / 3600.0)
        if current_age < min_age:
            try:
                rename_exclusive_between(
                    quarantine_fd, tombstone, root_fd, item.name)
                os.fsync(quarantine_fd)
                os.fsync(root_fd)
                return "TOO_YOUNG_RECHECK"
            except CleanupError:
                return "TOO_YOUNG_QUARANTINED"
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            return "QUARANTINED_NO_SAFE_RMTREE"
        # The public name is already absent. This second identity check binds
        # rmtree to an unpredictable leaf inside our private run quarantine.
        final_name = os.stat(
            tombstone, dir_fd=quarantine_fd, follow_symlinks=False)
        if (final_name.st_dev, final_name.st_ino) != item.identity:
            return "QUARANTINED_IDENTITY_RACE"
        try:
            shutil.rmtree(tombstone, dir_fd=quarantine_fd)
        except OSError:
            return "QUARANTINED_DELETE_ERROR"
        try:
            os.stat(tombstone, dir_fd=quarantine_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.fsync(quarantine_fd)
            return "DELETED"
        return "QUARANTINED_DELETE_INCOMPLETE"
    finally:
        if tomb_fd >= 0:
            os.close(tomb_fd)
        if candidate_fd >= 0:
            os.close(candidate_fd)


def main() -> int:
    args = parser().parse_args()
    root_fd = -1
    quarantine_fd = -1
    quarantine_name: str | None = None
    try:
        root, root_fd = open_absolute_directory_nofollow(args.root)
        lock_cleaner(root_fd)
        candidates, skipped = scan(root, root_fd, time.time())
        classify(candidates, args.keep, args.min_age_hours)
        if args.apply and any(item.status == "DELETE" for item in candidates):
            quarantine_name, quarantine_fd = create_private_quarantine(root_fd)
        deleted_bytes = 0
        would_delete = 0
        deleted = 0
        for name, reason in sorted(skipped):
            print(f"SKIPPED path={quoted(root / name)} reason={reason}")
        for item in sorted(candidates,
                           key=lambda value: (value.effective_time, value.name),
                           reverse=True):
            status = item.status
            if status == "DELETE":
                if args.apply:
                    assert quarantine_fd >= 0
                    status = remove_candidate(
                        root, root_fd, quarantine_fd, item, args.min_age_hours)
                    if status == "DELETED":
                        deleted += 1
                        deleted_bytes += item.approx_bytes
                else:
                    status = "WOULD_DELETE"
                    would_delete += 1
            item.status = status
            print(
                f"CANDIDATE path={quoted(item.path)} status={status} pid={item.pid} "
                f"age_hours={item.age_hours:.2f} approx_bytes={item.approx_bytes}"
            )
        total_bytes = sum(item.approx_bytes for item in candidates)
        quarantined = sum(
            item.status.startswith(("QUARANTINED_", "TOO_YOUNG_QUARANTINED"))
            or item.status == "PROTECTED_LIVE_PID_QUARANTINED"
            for item in candidates)
        if quarantine_fd >= 0:
            entries = os.listdir(quarantine_fd)
            if entries:
                print(
                    f"QUARANTINE path={quoted(root / quarantine_name)} "
                    f"preserved_entries={len(entries)}"
                )
            else:
                os.close(quarantine_fd)
                quarantine_fd = -1
                os.rmdir(quarantine_name, dir_fd=root_fd)
                os.fsync(root_fd)
                quarantine_name = None
        protected = sum(
            item.status.startswith("PROTECTED_LIVE_PID") for item in candidates)
        retained = sum(item.status == "RETAINED" for item in candidates)
        too_young = sum(item.status.startswith("TOO_YOUNG") for item in candidates)
        print(
            f"SUMMARY mode={'APPLY' if args.apply else 'DRY_RUN'} "
            f"candidates={len(candidates)} deleted={deleted} "
            f"would_delete={would_delete} protected={protected} "
            f"retained={retained} too_young={too_young} quarantined={quarantined} "
            f"skipped={len(skipped)} "
            f"approx_candidate_bytes={total_bytes} approx_deleted_bytes={deleted_bytes}"
        )
        return 0
    except (CleanupError, OSError) as error:
        print(f"cleanup simulator work: FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if root_fd >= 0:
            os.close(root_fd)


raise SystemExit(main())
PY
