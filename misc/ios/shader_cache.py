#!/usr/bin/env python3
"""Fail-closed immutable shader-checker cache for the iOS gate.

The shell owns checker selection; this helper owns every cache pathname.  A
cache-enabled transaction takes a kernel per-key lock, validates the checker
transcript before any durable cache mutation, and publishes one immutable
receipt plus one immutable output.  ``--full`` uses ``force_direct`` and never
opens this namespace.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import gate_hash


SCHEMA = "openxray.shader-cache.v2"
ENVELOPE_MAGIC = b"OPENXRAY_SHADER_CACHE_V1\n"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "build/ios-engine-iphoneos/.ios_gate_cache"
STAGES = frozenset(("compile", "link"))
KEY_RE = re.compile(r"[0-9a-f]{64}\Z")
TEMP_RE = re.compile(r"[0-9a-f]{32}\.tmp\Z")
MAX_PAYLOAD = 32 * 1024 * 1024
MAX_RECEIPT = 16 * 1024
MAX_TEMPS = 32

# Every family matcher is deliberately broader than its success matcher.  A
# transcript carrying both the good summary and (for example) a 278/279
# summary is a contradiction, not harmless diagnostic text.
COMPILE_SUMMARY_FAMILIES = (
    (re.compile(r"\AGLSL ES 3\.00 shader check: [^\r\n]*\Z"),
     re.compile(r"\AGLSL ES 3\.00 shader check: 279/279 compile, 0 fail \([0-9]+ include-only skipped\)\Z")),
    (re.compile(r"\AGLSL ES 3\.00 low-settings profile: [^\r\n]*\Z"),
     re.compile(r"\AGLSL ES 3\.00 low-settings profile: 2/2 compile, 0 fail\Z")),
    (re.compile(r"\AGLSL ES 3\.00 SSAO branch profile: [^\r\n]*\Z"),
     re.compile(r"\AGLSL ES 3\.00 SSAO branch profile: 6/6 compile, 0 fail\Z")),
    (re.compile(r"\AGLSL ES 3\.00 SSR branch profile: [^\r\n]*\Z"),
     re.compile(r"\AGLSL ES 3\.00 SSR branch profile: 9/9 compile, 0 fail\Z")),
    (re.compile(r"\ASSAO value-macro contract: [^\r\n]*\Z"),
     re.compile(r"\ASSAO value-macro contract: PASS\Z")),
    (re.compile(r"\ANumeric feature-macro contract: [^\r\n]*\Z"),
     re.compile(r"\ANumeric feature-macro contract: PASS \(five zero fallbacks; presence debt=0; undef debt=0\)\Z")),
)
LINK_SUMMARY_FAMILIES = (
    (re.compile(r"\Avs->fs link check: [^\r\n]*\Z"),
     re.compile(r"\Avs->fs link check: 137/137 pairs clean, 0 with issues\Z")),
)


class CacheError(RuntimeError):
    """A cache invariant failed; this is never a recoverable cache miss."""


class CacheBusy(CacheError):
    """No cache lock was acquired before the caller's one deadline."""


class CheckerFailed(CacheError):
    """The checker itself failed before publication."""

    def __init__(self, status: int, payload: bytes):
        super().__init__(f"shader checker exited {status}")
        self.status = status
        self.payload = payload


class InputDrift(CacheError):
    """Inputs changed while a content-addressed transaction was active."""


class SemanticValidationError(CacheError):
    """A zero-exit checker transcript did not satisfy its immutable contract."""


class ForwardedSignal(CacheError):
    def __init__(self, signum: int):
        super().__init__(f"checker interrupted by signal {signum}")
        self.signum = signum


@dataclass(frozen=True)
class CacheResult:
    payload: bytes
    cache_hit: bool
    direct: bool
    output_path: Path | None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _mode(detail: os.stat_result) -> int:
    return stat.S_IMODE(detail.st_mode)


def _identity(detail: os.stat_result) -> tuple[int, int]:
    return detail.st_dev, detail.st_ino


def _snapshot(detail: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (detail.st_dev, detail.st_ino, detail.st_uid, detail.st_mode, detail.st_nlink,
            detail.st_size, detail.st_mtime_ns, detail.st_ctime_ns)


def _same_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return _snapshot(left) == _snapshot(right)


def _require_directory(detail: os.stat_result, *, allowed_modes: frozenset[int]) -> None:
    if (not stat.S_ISDIR(detail.st_mode) or stat.S_ISLNK(detail.st_mode)
            or detail.st_uid != os.geteuid() or _mode(detail) not in allowed_modes
            or _mode(detail) & 0o022):
        raise CacheError("cache directory type/owner/mode invariant failed")


def _require_regular(detail: os.stat_result, *, mode: int, links: frozenset[int]) -> None:
    if (not stat.S_ISREG(detail.st_mode) or stat.S_ISLNK(detail.st_mode)
            or detail.st_uid != os.geteuid() or _mode(detail) != mode
            or detail.st_nlink not in links):
        raise CacheError("cache file type/owner/mode/link invariant failed")


def _fsync(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise CacheError("could not fsync cache transaction") from error


def _read_fd(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        block = os.read(descriptor, min(65536, remaining + 1))
        if not block:
            return b"".join(chunks)
        chunks.append(block)
        remaining -= len(block)
        if remaining < 0:
            raise CacheError("cache record exceeds bounded size")


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise CacheError("short cache write")
        offset += written


def _open_absolute_directory(path: Path, *, allowed_modes: frozenset[int]) -> int:
    """Open one already-derived absolute directory without following its leaf."""
    try:
        named = os.stat(path, follow_symlinks=False)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise CacheError("could not open cache parent directory") from error
    try:
        opened = os.fstat(descriptor)
        _require_directory(named, allowed_modes=allowed_modes)
        _require_directory(opened, allowed_modes=allowed_modes)
        if _identity(named) != _identity(opened):
            raise CacheError("cache parent identity changed while opening")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_dir_at(parent_fd: int, name: str, *, create: bool,
                 allowed_modes: frozenset[int], tighten_to: int | None = None) -> int:
    """Create/open a static no-follow child and bind name to its descriptor."""
    if not name or "/" in name or name in {".", ".."}:
        raise CacheError("unsafe cache directory component")
    created = False
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        except OSError as error:
            raise CacheError(f"could not create cache directory {name}") from error
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        raise CacheError(f"could not open cache directory {name}") from error
    try:
        opened = os.fstat(descriptor)
        _require_directory(named, allowed_modes=allowed_modes)
        _require_directory(opened, allowed_modes=allowed_modes)
        if _identity(named) != _identity(opened):
            raise CacheError("cache directory identity changed while opening")
        if tighten_to is not None and _mode(opened) != tighten_to:
            os.fchmod(descriptor, tighten_to)
            _fsync(descriptor)
            opened = os.fstat(descriptor)
            rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            _require_directory(opened, allowed_modes=frozenset((tighten_to,)))
            _require_directory(rebound, allowed_modes=frozenset((tighten_to,)))
            if _identity(opened) != _identity(rebound):
                raise CacheError("cache directory identity changed while tightening")
        if created:
            _fsync(parent_fd)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_payload(stage: str, payload: bytes) -> None:
    """Reject malformed zero-exit checker output before it reaches disk."""
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise SemanticValidationError("shader checker output is not UTF-8") from error
    if "::error::" in text:
        raise SemanticValidationError("shader checker output contains CI error annotation")
    families = COMPILE_SUMMARY_FAMILIES if stage == "compile" else LINK_SUMMARY_FAMILIES
    lines = text.splitlines()
    for family, success in families:
        members = [line for line in lines if family.fullmatch(line) is not None]
        if len(members) != 1 or success.fullmatch(members[0]) is None:
            raise SemanticValidationError("shader checker summary is missing, duplicate, or conflicting")


class ShaderCache:
    """Descriptor-bound immutable namespace.

    ``allow_test_root`` is only an in-process testing seam.  The production
    CLI never enables it and accepts exactly the root derived from this file.
    """

    def __init__(self, root: Path | str = DEFAULT_ROOT, *, allow_test_root: bool = False,
                 max_payload: int = MAX_PAYLOAD) -> None:
        raw = os.fsdecode(os.fspath(root))
        # Inspect the caller spelling before pathlib/abspath can erase it.
        # A leading empty component denotes the one permitted absolute slash;
        # every later component must be nonempty and lexical, so repeated and
        # trailing separators cannot become invisible through normalisation.
        components = raw.split(os.sep)
        if (not raw or not os.path.isabs(raw) or components[0] != ""
                or any(not component or component in {".", ".."}
                       for component in components[1:])):
            raise CacheError("shader cache root spelling is not canonical")
        if not allow_test_root and raw != os.fspath(DEFAULT_ROOT):
            raise CacheError("shader cache root is not canonical")
        self.root = Path(raw)
        self.allow_test_root = allow_test_root
        self.max_payload = max_payload

    @staticmethod
    def _check_stage_key(stage: str, key: str) -> None:
        if stage not in STAGES or not KEY_RE.fullmatch(key):
            raise CacheError("invalid cache stage or content key")

    def _root_fd(self) -> int:
        if self.allow_test_root:
            parent = _open_absolute_directory(self.root.parent, allowed_modes=frozenset((0o700, 0o755)))
            try:
                return _open_dir_at(parent, self.root.name, create=True,
                                    allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            finally:
                os.close(parent)
        # The repository is derived from this helper, never from a CLI value.
        repo_fd = _open_absolute_directory(REPO_ROOT, allowed_modes=frozenset((0o700, 0o755)))
        build_fd = ios_fd = -1
        try:
            build_fd = _open_dir_at(repo_fd, "build", create=True,
                                    allowed_modes=frozenset((0o700, 0o755)))
            ios_fd = _open_dir_at(build_fd, "ios-engine-iphoneos", create=True,
                                  allowed_modes=frozenset((0o700, 0o755)))
            return _open_dir_at(ios_fd, ".ios_gate_cache", create=True,
                                allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
        finally:
            for descriptor in (ios_fd, build_fd, repo_fd):
                if descriptor >= 0:
                    os.close(descriptor)

    @staticmethod
    def _acquire(descriptor: int, deadline: float) -> None:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CacheBusy("shader cache lock busy")
                time.sleep(min(0.025, max(0.001, deadline - time.monotonic())))

    @staticmethod
    def _open_lock(directory_fd: int, name: str) -> int:
        if not (name == "bootstrap.lock" or re.fullmatch(r"[0-9a-f]{64}\.lock", name)):
            raise CacheError("invalid cache lock name")
        descriptor = -1
        # The first open must bind every contender to one inode.  Do not use a
        # stat/open pair: a concurrent first creator can otherwise disappear
        # between the probes on APFS.
        for _attempt in range(32):
            try:
                descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory_fd)
                break
            except FileExistsError:
                try:
                    descriptor = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory_fd)
                    break
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise CacheError("could not reopen cache lock") from error
            except OSError as error:
                raise CacheError("could not create cache lock") from error
        if descriptor < 0:
            raise CacheError("cache lock name changed during exclusive create")
        try:
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            opened = os.fstat(descriptor)
            _require_regular(named, mode=0o600, links=frozenset((1,)))
            _require_regular(opened, mode=0o600, links=frozenset((1,)))
            if not _same_snapshot(named, opened):
                raise CacheError("cache lock identity changed while opening")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _namespace(self, root_fd: int, stage: str, key: str,
                   deadline: float) -> tuple[int, int, int, int]:
        """Create the namespace under one bootstrap flock with the same deadline."""
        locks_fd = bootstrap_fd = -1
        stage_fd = receipts_stage_fd = temp_key_fd = lock_stage_fd = -1
        try:
            locks_fd = _open_dir_at(root_fd, "locks", create=True,
                                    allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            bootstrap_fd = self._open_lock(locks_fd, "bootstrap.lock")
            self._acquire(bootstrap_fd, deadline)
            stage_fd = _open_dir_at(root_fd, stage, create=True,
                                    allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            receipts_fd = _open_dir_at(root_fd, "receipts", create=True,
                                       allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            try:
                receipts_stage_fd = _open_dir_at(receipts_fd, stage, create=True,
                                                  allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            finally:
                os.close(receipts_fd)
            tmp_fd = _open_dir_at(root_fd, "tmp", create=True,
                                  allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            try:
                tmp_stage_fd = _open_dir_at(tmp_fd, stage, create=True,
                                            allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            finally:
                os.close(tmp_fd)
            try:
                temp_key_fd = _open_dir_at(tmp_stage_fd, key, create=True,
                                           allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            finally:
                os.close(tmp_stage_fd)
            lock_stage_fd = _open_dir_at(locks_fd, stage, create=True,
                                         allowed_modes=frozenset((0o700, 0o755)), tighten_to=0o700)
            return stage_fd, receipts_stage_fd, temp_key_fd, lock_stage_fd
        except Exception:
            for descriptor in (lock_stage_fd, temp_key_fd, receipts_stage_fd, stage_fd):
                if descriptor >= 0:
                    os.close(descriptor)
            raise
        finally:
            if bootstrap_fd >= 0:
                fcntl.flock(bootstrap_fd, fcntl.LOCK_UN)
                os.close(bootstrap_fd)
            if locks_fd >= 0:
                os.close(locks_fd)

    @staticmethod
    def _receipt_name(key: str) -> str:
        return f"{key}.json"

    @staticmethod
    def _final_name(key: str) -> str:
        return f"{key}.out"

    @staticmethod
    def _read_named_regular(directory_fd: int, name: str, *, mode: int,
                            links: frozenset[int], limit: int) -> tuple[bytes, os.stat_result]:
        try:
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except OSError as error:
            raise CacheError("cache file is unavailable") from error
        try:
            opened = os.fstat(descriptor)
            _require_regular(named, mode=mode, links=links)
            _require_regular(opened, mode=mode, links=links)
            if not _same_snapshot(named, opened):
                raise CacheError("cache file changed while opening")
            raw = _read_fd(descriptor, limit)
            after = os.fstat(descriptor)
            rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _same_snapshot(opened, after) or not _same_snapshot(opened, rebound):
                raise CacheError("cache file changed while reading")
            return raw, opened
        finally:
            os.close(descriptor)

    def _load_receipt(self, receipts_fd: int, stage: str, key: str) -> tuple[str, dict[str, object] | None, bytes | None, os.stat_result | None]:
        name = self._receipt_name(key)
        try:
            detail = os.stat(name, dir_fd=receipts_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent", None, None, None
        if _mode(detail) == 0o600:
            _require_regular(detail, mode=0o600, links=frozenset((1,)))
            return "partial", None, None, detail
        raw, opened = self._read_named_regular(receipts_fd, name, mode=0o400,
                                               links=frozenset((1,)), limit=MAX_RECEIPT)
        try:
            value = json.loads(raw.decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CacheError("immutable cache receipt is malformed") from error
        required = {"schema", "stage", "key", "final", "temp", "payload_sha256", "payload_size",
                    "envelope_sha256", "envelope_size", "file_dev", "file_ino", "file_mode", "nlink"}
        if (not isinstance(value, dict) or set(value) != required or _canonical(value) != raw
                or value["schema"] != SCHEMA or value["stage"] != stage or value["key"] != key
                or value["final"] != self._final_name(key) or not isinstance(value["temp"], str)
                or not TEMP_RE.fullmatch(value["temp"])):
            raise CacheError("immutable cache receipt schema mismatch")
        for field in ("payload_sha256", "envelope_sha256"):
            if not isinstance(value[field], str) or not KEY_RE.fullmatch(value[field]):
                raise CacheError("immutable cache receipt hash is invalid")
        for field in ("payload_size", "envelope_size", "file_dev", "file_ino", "file_mode", "nlink"):
            if not isinstance(value[field], int) or value[field] < 0:
                raise CacheError("immutable cache receipt numeric field is invalid")
        if value["file_mode"] != 0o400 or value["nlink"] != 1:
            raise CacheError("immutable cache receipt terminal attributes are invalid")
        return "valid", value, raw, opened

    @staticmethod
    def _list_temps(temp_fd: int) -> list[str]:
        names = os.listdir(temp_fd)
        if len(names) > MAX_TEMPS or any(not TEMP_RE.fullmatch(name) for name in names):
            raise CacheError("cache temporary recovery set is unsafe")
        return sorted(names)

    @staticmethod
    def _cleanup_temps(temp_fd: int, *, keep: str | None = None) -> None:
        for name in ShaderCache._list_temps(temp_fd):
            if name == keep:
                continue
            detail = os.stat(name, dir_fd=temp_fd, follow_symlinks=False)
            _require_regular(detail, mode=_mode(detail), links=frozenset((1,)))
            if _mode(detail) not in {0o600, 0o400}:
                raise CacheError("cache temporary mode is unsafe")
            os.unlink(name, dir_fd=temp_fd)
        _fsync(temp_fd)

    def _entry_payload(self, directory_fd: int, name: str, receipt: dict[str, object], *,
                       links: frozenset[int]) -> bytes:
        raw, detail = self._read_named_regular(directory_fd, name, mode=0o400,
                                               links=links, limit=self.max_payload + MAX_RECEIPT)
        if (_identity(detail) != (int(receipt["file_dev"]), int(receipt["file_ino"]))
                or detail.st_size != int(receipt["envelope_size"])
                or hashlib.sha256(raw).hexdigest() != receipt["envelope_sha256"]):
            raise CacheError("cache entry disagrees with immutable receipt")
        if not raw.startswith(ENVELOPE_MAGIC):
            raise CacheError("cache envelope magic is missing")
        try:
            metadata_raw, payload = raw[len(ENVELOPE_MAGIC):].split(b"\n", 1)
            metadata = json.loads(metadata_raw.decode("utf-8", "strict"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CacheError("cache envelope is malformed") from error
        expected = {"schema": SCHEMA, "stage": receipt["stage"], "key": receipt["key"],
                    "payload_size": receipt["payload_size"], "payload_sha256": receipt["payload_sha256"]}
        if (not isinstance(metadata, dict) or _canonical(metadata) != metadata_raw or metadata != expected
                or len(payload) != int(receipt["payload_size"])
                or hashlib.sha256(payload).hexdigest() != receipt["payload_sha256"]):
            raise CacheError("cache envelope disagrees with immutable receipt")
        _validate_payload(str(receipt["stage"]), payload)
        return payload

    def _recover_or_read(self, stage_fd: int, receipts_fd: int, temp_fd: int,
                         stage: str, key: str, *, hook: Callable[[str], None] | None = None) -> tuple[bytes | None, Path | None]:
        receipt_state, receipt, _receipt_raw, _detail = self._load_receipt(receipts_fd, stage, key)
        final_name = self._final_name(key)
        try:
            os.stat(final_name, dir_fd=stage_fd, follow_symlinks=False)
            final_exists = True
        except FileNotFoundError:
            final_exists = False
        if receipt_state == "absent":
            if final_exists:
                raise CacheError("cache final exists without immutable receipt")
            self._cleanup_temps(temp_fd)
            return None, None
        if receipt_state == "partial":
            if final_exists:
                raise CacheError("partial immutable receipt has a final entry")
            self._cleanup_temps(temp_fd)
            os.unlink(self._receipt_name(key), dir_fd=receipts_fd)
            _fsync(receipts_fd)
            return None, None
        assert receipt is not None
        temp_name = str(receipt["temp"])
        names = self._list_temps(temp_fd)
        has_temp = temp_name in names
        if final_exists and has_temp:
            # Recovery after linkat but before unlink: both names must bind the
            # receipt inode and only this exact temporary can carry link two.
            payload = self._entry_payload(stage_fd, final_name, receipt, links=frozenset((2,)))
            temp_payload = self._entry_payload(temp_fd, temp_name, receipt, links=frozenset((2,)))
            if temp_payload != payload:
                raise CacheError("linked cache payload differs across hardlinks")
            if hook:
                hook("recover-before-unlink")
            os.unlink(temp_name, dir_fd=temp_fd)
            _fsync(temp_fd)
            payload = self._entry_payload(stage_fd, final_name, receipt, links=frozenset((1,)))
            self._cleanup_temps(temp_fd)
            return payload, self.root / stage / final_name
        if final_exists:
            payload = self._entry_payload(stage_fd, final_name, receipt, links=frozenset((1,)))
            self._cleanup_temps(temp_fd)
            return payload, self.root / stage / final_name
        if not has_temp:
            raise CacheError("durable immutable receipt lost both final and temporary")
        self._entry_payload(temp_fd, temp_name, receipt, links=frozenset((1,)))
        try:
            os.link(temp_name, final_name, src_dir_fd=temp_fd, dst_dir_fd=stage_fd,
                    follow_symlinks=False)
        except FileExistsError as error:
            raise CacheError("cache no-clobber publication collided") from error
        _fsync(stage_fd)
        if hook:
            hook("recover-after-link")
        os.unlink(temp_name, dir_fd=temp_fd)
        _fsync(temp_fd)
        payload = self._entry_payload(stage_fd, final_name, receipt, links=frozenset((1,)))
        self._cleanup_temps(temp_fd)
        return payload, self.root / stage / final_name

    def _create_receipt(self, receipts_fd: int, key: str, value: dict[str, object], *,
                        hook: Callable[[str], None] | None) -> None:
        name = self._receipt_name(key)
        raw = _canonical(value)
        if len(raw) > MAX_RECEIPT:
            raise CacheError("immutable receipt exceeds bounded size")
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=receipts_fd)
        except FileExistsError as error:
            raise CacheError("immutable receipt already exists") from error
        except OSError as error:
            raise CacheError("could not create immutable receipt") from error
        try:
            _write_all(descriptor, raw)
            _fsync(descriptor)
            if hook:
                hook("after-receipt-write")
            os.fchmod(descriptor, 0o400)
            _fsync(descriptor)
            _require_regular(os.fstat(descriptor), mode=0o400, links=frozenset((1,)))
        finally:
            os.close(descriptor)
        _fsync(receipts_fd)
        if hook:
            hook("after-receipt")

    def _publish(self, stage_fd: int, receipts_fd: int, temp_fd: int, stage: str, key: str,
                 payload: bytes, *, hook: Callable[[str], None] | None) -> Path:
        if len(payload) > self.max_payload:
            raise CacheError("checker output exceeds bounded cache payload")
        _validate_payload(stage, payload)
        temp_name = f"{os.urandom(16).hex()}.tmp"
        metadata = {"schema": SCHEMA, "stage": stage, "key": key,
                    "payload_size": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest()}
        envelope = ENVELOPE_MAGIC + _canonical(metadata) + b"\n" + payload
        try:
            descriptor = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=temp_fd)
        except OSError as error:
            raise CacheError("could not create exclusive cache temporary") from error
        try:
            _write_all(descriptor, envelope)
            _fsync(descriptor)
            os.fchmod(descriptor, 0o400)
            _fsync(descriptor)
            detail = os.fstat(descriptor)
            _require_regular(detail, mode=0o400, links=frozenset((1,)))
        finally:
            os.close(descriptor)
        _fsync(temp_fd)
        if hook:
            hook("after-temp")
        receipt = {"schema": SCHEMA, "stage": stage, "key": key, "final": self._final_name(key),
                   "temp": temp_name, "payload_sha256": metadata["payload_sha256"],
                   "payload_size": len(payload), "envelope_sha256": hashlib.sha256(envelope).hexdigest(),
                   "envelope_size": len(envelope), "file_dev": detail.st_dev, "file_ino": detail.st_ino,
                   "file_mode": 0o400, "nlink": 1}
        self._create_receipt(receipts_fd, key, receipt, hook=hook)
        try:
            os.link(temp_name, self._final_name(key), src_dir_fd=temp_fd, dst_dir_fd=stage_fd,
                    follow_symlinks=False)
        except FileExistsError as error:
            raise CacheError("cache no-clobber publication collided") from error
        _fsync(stage_fd)
        if hook:
            hook("after-link")
        os.unlink(temp_name, dir_fd=temp_fd)
        _fsync(temp_fd)
        if hook:
            hook("after-unlink")
        self._entry_payload(stage_fd, self._final_name(key), receipt, links=frozenset((1,)))
        return self.root / stage / self._final_name(key)

    @staticmethod
    def _feedback_raw_fd() -> tuple[int, ...]:
        """Return one safe raw telemetry descriptor, or fail before a child starts.

        The normal shell boundary intentionally unlinks its private sink before
        exec, hence link count zero is retained as a strictly safer equivalent
        of the named singleton (one).  More than one link is never accepted.
        Once a descriptor has crossed exec there is no reliable way to recover
        whether a pathname lookup originally traversed a symlink; the bound
        descriptor's type, owner, mode, link count and write/seek capability
        are the enforceable authority boundary.
        """
        marker = os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD", "")
        if not marker:
            return ()
        if not marker.isascii() or not marker.isdecimal():
            raise CacheError("feedback raw descriptor is invalid")
        descriptor = int(marker)
        if descriptor < 3 or str(descriptor) != marker:
            raise CacheError("feedback raw descriptor is invalid")
        try:
            details = os.fstat(descriptor)
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            access = flags & os.O_ACCMODE
            if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                    or _mode(details) != 0o600 or details.st_nlink not in {0, 1}
                    or access not in {os.O_WRONLY, os.O_RDWR}):
                raise CacheError("feedback raw descriptor is unsafe")
            # SEEK_CUR probes regular-file positioning without altering either
            # the telemetry stream or the child's eventual append position.
            os.lseek(descriptor, 0, os.SEEK_CUR)
        except OSError as error:
            raise CacheError("feedback raw descriptor is unavailable") from error
        return (descriptor,)

    def _run_checker(self, command: Sequence[str], cwd: Path | str | None,
                     *, hook: Callable[[str], None] | None = None) -> bytes:
        if not command:
            raise CacheError("missing shader checker command")
        pass_fds = self._feedback_raw_fd()
        child: subprocess.Popen[bytes] | None = None
        forwarded: int | None = None

        def forward(signum: int, _frame: object) -> None:
            nonlocal forwarded
            forwarded = signum
            if child is not None and child.poll() is None:
                try:
                    os.killpg(child.pid, signum)
                except ProcessLookupError:
                    pass

        old = {number: signal.signal(number, forward) for number in (signal.SIGINT, signal.SIGTERM)}
        try:
            if hook:
                hook("checker-handler-installed")
            # A signal observed before spawn must never be turned into a child
            # launch.  A signal in the small Popen/assignment window is caught
            # by the same handler and is handled immediately below.
            if forwarded is not None:
                raise ForwardedSignal(forwarded)
            if hook:
                hook("before-spawn")
            if forwarded is not None:
                raise ForwardedSignal(forwarded)
            child = subprocess.Popen(list(command), cwd=cwd, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, start_new_session=True,
                                     close_fds=True, pass_fds=pass_fds)
            if forwarded is not None:
                # Popen has returned, so this is the only point at which the
                # child can be bound to the recorded pre-assignment signal.
                # Kill its whole session, reap it, and never expose a result.
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, forwarded)
                    except ProcessLookupError:
                        pass
                if child.stdout is not None:
                    child.stdout.close()
                child.wait()
                raise ForwardedSignal(forwarded)
            assert child.stdout is not None
            output = child.stdout.read(self.max_payload + 1)
            child.stdout.close()
            if len(output) > self.max_payload:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                raise CacheError("checker output exceeds bounded cache payload")
            status = child.wait()
        finally:
            for number, previous in old.items():
                signal.signal(number, previous)
        if forwarded is not None:
            raise ForwardedSignal(forwarded)
        if status != 0:
            raise CheckerFailed(status, output)
        return output

    @staticmethod
    def _digest(salt: str, inputs: Iterable[str]) -> str:
        try:
            return gate_hash.digest_paths(salt, list(inputs))
        except (OSError, subprocess.CalledProcessError) as error:
            raise CacheError(f"could not hash shader cache inputs: {error}") from error

    def run(self, *, stage: str, key: str, salt: str, inputs: Sequence[str],
            command: Sequence[str], cwd: Path | str | None = None, timeout: float = 300,
            force_direct: bool = False, hook: Callable[[str], None] | None = None) -> CacheResult:
        self._check_stage_key(stage, key)
        if timeout < 0:
            raise CacheError("shader cache timeout is negative")
        if self._digest(salt, inputs) != key:
            raise InputDrift("shader inputs changed before checker")
        if force_direct:
            if hook:
                hook("before-checker")
            payload = self._run_checker(command, cwd, hook=hook)
            _validate_payload(stage, payload)
            if self._digest(salt, inputs) != key:
                raise InputDrift("shader inputs changed during direct checker")
            return CacheResult(payload=payload, cache_hit=False, direct=True, output_path=None)
        deadline = time.monotonic() + timeout
        root_fd = stage_fd = receipts_fd = temp_fd = locks_stage_fd = lock_fd = -1
        try:
            root_fd = self._root_fd()
            stage_fd, receipts_fd, temp_fd, locks_stage_fd = self._namespace(root_fd, stage, key, deadline)
            lock_fd = self._open_lock(locks_stage_fd, f"{key}.lock")
            self._acquire(lock_fd, deadline)
            if self._digest(salt, inputs) != key:
                raise InputDrift("shader inputs changed while waiting for cache lock")
            payload, output_path = self._recover_or_read(stage_fd, receipts_fd, temp_fd, stage, key, hook=hook)
            if payload is not None:
                _validate_payload(stage, payload)
                if self._digest(salt, inputs) != key:
                    raise InputDrift("shader inputs changed while validating cache hit")
                return CacheResult(payload=payload, cache_hit=True, direct=False, output_path=output_path)
            if hook:
                hook("before-checker")
            payload = self._run_checker(command, cwd, hook=hook)
            _validate_payload(stage, payload)
            if self._digest(salt, inputs) != key:
                raise InputDrift("shader inputs changed during checker")
            output_path = self._publish(stage_fd, receipts_fd, temp_fd, stage, key, payload, hook=hook)
            return CacheResult(payload=payload, cache_hit=False, direct=False, output_path=output_path)
        finally:
            if lock_fd >= 0:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            for descriptor in (locks_stage_fd, temp_fd, receipts_fd, stage_fd, root_fd):
                if descriptor >= 0:
                    os.close(descriptor)


def _status(path: Path | None, result: CacheResult) -> None:
    if path is None:
        return
    payload = (f"cache_hit={int(result.cache_hit)}\n"
               f"direct={int(result.direct)}\n"
               f"output_path={result.output_path or ''}\n").encode("utf-8")
    try:
        detail = os.stat(path, follow_symlinks=False)
        _require_regular(detail, mode=0o600, links=frozenset((1,)))
        # O_TRUNC updates APFS mtime/ctime before fstat(), so bind and compare
        # the caller-created status inode before changing it ourselves.
        descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            _require_regular(opened, mode=0o600, links=frozenset((1,)))
            if not _same_snapshot(detail, opened):
                raise CacheError("cache status identity changed while opening")

            os.ftruncate(descriptor, 0)
            os.lseek(descriptor, 0, os.SEEK_SET)
            truncated = os.fstat(descriptor)
            _require_regular(truncated, mode=0o600, links=frozenset((1,)))
            if _identity(opened) != _identity(truncated):
                raise CacheError("cache status identity changed while truncating")

            _write_all(descriptor, payload)
            _fsync(descriptor)
            finished = os.fstat(descriptor)
            rebound = os.stat(path, follow_symlinks=False)
            _require_regular(finished, mode=0o600, links=frozenset((1,)))
            _require_regular(rebound, mode=0o600, links=frozenset((1,)))
            if (_identity(opened) != _identity(finished)
                    or _identity(opened) != _identity(rebound)
                    or finished.st_size != len(payload)
                    or rebound.st_size != len(payload)):
                raise CacheError("cache status changed while writing")
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CacheError("could not write cache status") from error


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    # Keep --root as raw text.  ShaderCache validates its exact lexical form
    # before any Path constructor has a chance to erase a rejected component.
    parser.add_argument("--root", default=os.fspath(DEFAULT_ROOT))
    parser.add_argument("--stage", required=True, choices=sorted(STAGES))
    parser.add_argument("--key", required=True)
    parser.add_argument("--salt", required=True)
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--force-direct", action="store_true")
    parser.add_argument("--status-file", type=Path)
    if not raw or raw[0] != "run":
        parser.error("the first argument must be the run command")
    try:
        separator = raw.index("--", 1)
    except ValueError:
        parser.error("checker command must follow --")
    args = parser.parse_args(raw[1:separator])
    try:
        result = ShaderCache(args.root).run(stage=args.stage, key=args.key, salt=args.salt,
                                            inputs=args.input, command=raw[separator + 1:], cwd=args.cwd,
                                            timeout=args.timeout, force_direct=args.force_direct)
        _status(args.status_file, result)
        sys.stdout.buffer.write(result.payload)
        return 0
    except CheckerFailed as error:
        sys.stdout.buffer.write(error.payload)
        sys.stdout.buffer.flush()
        print(f"shader cache checker failed: exit {error.status}", file=sys.stderr, flush=True)
        return error.status if 0 < error.status <= 255 else 1
    except CacheBusy as error:
        print(f"shader cache busy: {error}", file=sys.stderr)
        return 75
    except ForwardedSignal as error:
        print(f"shader cache interrupted: {error}", file=sys.stderr)
        return 128 + error.signum
    except (CacheError, OSError) as error:
        print(f"shader cache failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
