#!/usr/bin/env python3
"""Fail-closed semantic UI-navigation oracle for an isolated iOS Simulator.

This module deliberately does not know about a physical device.  ``run`` is an
explicit Simulator-integration mode: it writes the existing file-driven iOS
autoinput request into one supplied Simulator Documents directory and observes
only its local ACK and engine-log files.  ``verify-log`` is read-only and is
useful while wiring the later engine marker.

The semantic marker is intentionally narrow and must remain exactly::

    * iOS UI state v1 pid=1234 seq=7 frame=1842 state=pda_tasks

It proves navigation and the render-path observation point, not pixels,
presented frames, performance, or physical-device behaviour.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Sequence
import uuid


CHUNK = 1024 * 1024
DEFAULT_BUNDLE_ID = "io.github.tryk016.openxray"
DEFAULT_HOLD_MS = 300
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_DWELL_SECONDS = 0.25
DEFAULT_POLL_SECONDS = 0.05
MAX_HOLD_MS = 2_000
MIN_HOLD_MS = 50
SEMANTIC_SCOPE = (
    "semantic-ui-navigation-only; CoP eptTasks is the combined tasks/map surface; "
    "not pixel, readability, performance, or physical-device proof"
)
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
SIMULATOR_UDID_RE = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z"
)
STATE_RE = re.compile(
    r"\* iOS UI state v1 pid=([1-9][0-9]*) seq=([1-9][0-9]*) "
    r"frame=([0-9]+) state=(world|inventory|pda_tasks|pda_map|other)\Z"
)
STATE_PREFIX = "* iOS UI state"
SYNC_COMPLETE_LINE = "* End of synchronization A[1] R[1]"
ACK_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) accepted\n\Z"
)
RELEASE_RE = re.compile(
    r"\* iOS diag: autoinput request "
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) "
    r"released scancode ([1-9][0-9]*)\Z"
)
PRESS_RE = re.compile(
    r"\* iOS diag: autoinput request "
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) "
    r"press/hold '([a-z]+)' \(scancode ([1-9][0-9]*)\) for ([1-9][0-9]*) ms\Z"
)
RUNTIME_FAILURE_MARKERS = (
    "fatal",
    "stack trace",
    "assertion",
    "abort trap",
    "terminating app due to uncaught exception",
    "termination reason",
    "application terminated",
)


class NavigationError(RuntimeError):
    """The local Simulator evidence is unsafe, incomplete, or contradictory."""


def fail(message: str) -> None:
    raise NavigationError(message)


@dataclass(frozen=True)
class UiStateMarker:
    pid: int
    seq: int
    frame: int
    state: str


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    size: int
    mtime_ns: int
    data: bytes


@dataclass(frozen=True)
class NavigationStep:
    key: str
    expected_state: str


@dataclass(frozen=True)
class TransitionEvidence:
    request_id: str
    key: str
    state: str
    pid: int
    seq: int
    frame: int
    release_scancode: int


@dataclass(frozen=True)
class PreTerminationProof:
    log_device: int
    log_inode: int
    expected_pid: int
    snapshot_sha256: str
    evidence: tuple[TransitionEvidence, ...]
    captures: tuple[dict[str, Any], ...]
    ui_capture_root: str | None
    expected_level: str | None
    provenance: dict[str, str] | None


SEQUENCE: tuple[NavigationStep, ...] = (
    NavigationStep("i", "inventory"),
    NavigationStep("i", "world"),
    NavigationStep("p", "pda_tasks"),
    NavigationStep("e", "other"),
    NavigationStep("escape", "world"),
    NavigationStep("m", "pda_tasks"),
    NavigationStep("escape", "world"),
)


def require_real_directory(path: Path, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing: {path}")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        fail(f"{label} must be a real non-symlink directory: {path}")


def require_safe_child(parent: Path, name: str, label: str) -> Path:
    require_real_directory(parent, f"{label} parent")
    if not name or "/" in name or name in {".", ".."}:
        fail(f"unsafe {label} child name: {name!r}")
    return parent / name


def read_stable_regular_nofollow(path: Path, label: str) -> FileSnapshot:
    """Read one file descriptor without accepting symlinks, rewrites, or races."""

    try:
        initial = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing: {path}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        fail(f"{label} must be a regular non-symlink file: {path}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as error:
        fail(f"cannot open {label} safely: {error}")
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            fail(f"{label} must be a regular non-symlink file: {path}")
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
        fail(f"{label} identity, size, or timestamp changed while being read")
    if len(contents) != after.st_size:
        fail(f"{label} read is truncated")
    return FileSnapshot(after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, bytes(contents))


def read_live_appendable_regular_nofollow(path: Path, label: str) -> FileSnapshot:
    """Read a stable prefix while a live regular log may only append.

    The prefix length is fixed from the opened descriptor.  A second read of
    that prefix rejects in-place rewrites; growth after opening is allowed.
    """

    try:
        initial = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing: {path}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        fail(f"{label} must be a regular non-symlink file: {path}")
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
            fail(f"{label} identity, size, or timestamp changed while being read")
        prefix_size = opened.st_size
        contents = bytearray()
        while len(contents) < prefix_size:
            block = os.read(descriptor, min(CHUNK, prefix_size - len(contents)))
            if not block:
                fail(f"{label} read is truncated")
            contents.extend(block)
        after = os.fstat(descriptor)
        if (not stat.S_ISREG(after.st_mode)
                or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or after.st_size < prefix_size):
            fail(f"{label} identity, size, or timestamp changed while being read")
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
        fail(f"{label} identity, size, or timestamp changed while being read")
    if verified != contents:
        fail(f"{label} was rewritten while being read")
    stable_mtimes = {
        opened.st_mtime_ns, after.st_mtime_ns,
        verified_after.st_mtime_ns, final.st_mtime_ns,
    }
    if opened.st_size == initial.st_size:
        stable_mtimes.add(initial.st_mtime_ns)
    if (after.st_size == prefix_size and verified_after.st_size == prefix_size
            and final.st_size == prefix_size and len(stable_mtimes) != 1):
        fail(f"{label} was rewritten while being read")
    return FileSnapshot(opened.st_dev, opened.st_ino, prefix_size, opened.st_mtime_ns, bytes(contents))


def complete_lines(snapshot: FileSnapshot, label: str) -> list[str]:
    """Decode complete log lines; an in-flight final fragment is retried later."""

    chunks = snapshot.data.split(b"\n")
    # ``split`` leaves one empty element after a trailing separator; it is not
    # a log record and must not shift the incremental event cursor.
    complete = chunks[:-1]
    lines: list[str] = []
    for raw in complete:
        try:
            lines.append(raw.decode("utf-8", errors="strict").rstrip("\r"))
        except UnicodeDecodeError as error:
            fail(f"{label} contains non-UTF-8 complete line: {error}")
    return lines


def terminated_lines(snapshot: FileSnapshot, label: str) -> list[str]:
    """Decode a stopped process log without tolerating an in-flight record."""

    if snapshot.data and not snapshot.data.endswith(b"\n"):
        fail(f"{label} contains an unterminated trailing record after process termination")
    return complete_lines(snapshot, label)


def parse_marker(line: str) -> UiStateMarker | None:
    if not line.startswith(STATE_PREFIX):
        return None
    match = STATE_RE.fullmatch(line)
    if match is None:
        fail(f"malformed UI state marker: {line!r}")
    return UiStateMarker(
        pid=int(match.group(1)), seq=int(match.group(2)),
        frame=int(match.group(3)), state=match.group(4),
    )


def verify_log_lines(lines: Iterable[str]) -> list[UiStateMarker]:
    """Validate all complete marker history, not merely the desired next state."""

    markers: list[UiStateMarker] = []
    for line in lines:
        lower = line.lower()
        if any(token in lower for token in RUNTIME_FAILURE_MARKERS):
            fail(f"runtime log contains forbidden failure marker: {line!r}")
        marker = parse_marker(line)
        if marker is not None:
            markers.append(marker)
    if not markers:
        fail("runtime log contains no complete iOS UI state marker")
    pid = markers[0].pid
    previous = markers[0]
    for marker in markers[1:]:
        if marker.pid != pid:
            fail(f"UI state marker PID changed: {pid} -> {marker.pid}")
        if marker.seq != previous.seq + 1:
            fail(f"UI state marker sequence is not exact +1: {previous.seq} -> {marker.seq}")
        if marker.frame <= previous.frame:
            fail(f"UI state marker frame did not increase: {previous.frame} -> {marker.frame}")
        previous = marker
    return markers


def check_monotonic_log(previous: FileSnapshot, current: FileSnapshot) -> None:
    if (previous.device, previous.inode) != (current.device, current.inode):
        fail("runtime log rotated or changed inode during UI navigation")
    if current.size < previous.size or not current.data.startswith(previous.data):
        fail("runtime log was truncated or rewritten during UI navigation")


def write_new_regular(path: Path, payload: bytes, label: str) -> None:
    """Publish a never-overwritten output while refusing a symlink collision."""

    require_real_directory(path.parent, f"{label} parent")
    try:
        existing = path.lstat()
    except FileNotFoundError:
        pass
    else:
        kind = "symlink" if stat.S_ISLNK(existing.st_mode) else "existing file"
        fail(f"{label} must not pre-exist ({kind}): {path}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
    except OSError as error:
        fail(f"cannot create fresh {label}: {error}")
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_size != len(payload):
        fail(f"fresh {label} was replaced or truncated")


def remove_regular_nofollow(path: Path, label: str) -> None:
    """Remove only a known regular file; a hostile replacement fails closed."""

    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        fail(f"{label} is not a removable regular non-symlink file: {path}")
    try:
        path.unlink()
    except OSError as error:
        fail(f"cannot remove {label}: {error}")


def require_absent_output(path: Path, label: str) -> None:
    """A run never adopts output left by an earlier or concurrent run."""

    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    kind = "symlink" if stat.S_ISLNK(details.st_mode) else "existing file"
    fail(f"{label} must not pre-exist ({kind}): {path}")


def parse_ack(snapshot: FileSnapshot, request_id: str, request_wall_ns: int) -> None:
    if snapshot.mtime_ns < request_wall_ns:
        fail("autoinput ACK predates the request and is stale or replayed")
    try:
        text = snapshot.data.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        fail(f"autoinput ACK is not ASCII: {error}")
    match = ACK_RE.fullmatch(text)
    if match is None:
        fail(f"malformed autoinput ACK: {text!r}")
    if match.group(1) != request_id:
        fail(f"autoinput ACK UUID does not match current request: {match.group(1)}")


def parse_release(line: str) -> tuple[str, int] | None:
    if "* iOS diag: autoinput request " not in line:
        return None
    match = RELEASE_RE.fullmatch(line)
    if match is None:
        fail(f"malformed autoinput release log: {line!r}")
    return match.group(1), int(match.group(2))


def parse_press(line: str) -> tuple[str, str, int, int] | None:
    if "* iOS diag: autoinput request " not in line:
        return None
    match = PRESS_RE.fullmatch(line)
    if match is None:
        # A release is handled by the caller.  Any third request-shaped line
        # would otherwise permit an unrecognized engine action into the proof.
        if RELEASE_RE.fullmatch(line) is None:
            fail(f"malformed autoinput press log: {line!r}")
        return None
    return match.group(1), match.group(2), int(match.group(3)), int(match.group(4))


def fresh_uuid() -> str:
    return str(uuid.uuid4())


def validate_request_id(request_id: str) -> str:
    if UUID_RE.fullmatch(request_id) is None:
        fail(f"request UUID is invalid: {request_id!r}")
    return request_id


def validate_expected_pid(pid: int) -> int:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        fail(f"expected process PID must be positive: {pid!r}")
    return pid


def require_live_process(pid: int) -> bool:
    """Apply the conservative ``kill(pid, 0)`` liveness contract."""

    validate_expected_pid(pid)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError) as error:
        fail(f"expected Simulator process PID {pid} is not live: {error}")
    return True


class UiNavigationController:
    """One deterministic run over a local Simulator Documents directory.

    ``poll_hook`` is intentionally test-only dependency injection.  Production
    callers leave it as ``None``; tests use it to emulate engine writes without
    launching CoreSimulator or any external process.
    """

    def __init__(self, documents: Path, log_path: Path, *, expected_pid: int,
                 hold_ms: int = DEFAULT_HOLD_MS,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 dwell_seconds: float = DEFAULT_DWELL_SECONDS,
                 poll_seconds: float = DEFAULT_POLL_SECONDS,
                 uuid_factory: Callable[[], str] = fresh_uuid,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock_ns: Callable[[], int] = time.time_ns,
                 sleep: Callable[[float], None] = time.sleep,
                 liveness: Callable[[int], bool | None] = require_live_process,
                 poll_hook: Callable[[], None] | None = None,
                 capture_observer: Any | None = None) -> None:
        if not MIN_HOLD_MS <= hold_ms <= MAX_HOLD_MS:
            fail(f"hold duration must be {MIN_HOLD_MS}..{MAX_HOLD_MS} ms")
        if timeout_seconds <= 0 or dwell_seconds < 0 or poll_seconds < 0:
            fail("timeout must be positive; dwell and poll durations must be non-negative")
        validate_expected_pid(expected_pid)
        self.documents = documents.absolute()
        require_real_directory(self.documents, "Simulator Documents")
        self.appdata = require_safe_child(self.documents, "_appdata_", "Simulator app-data")
        require_real_directory(self.appdata, "Simulator app-data")
        self.log_path = log_path.absolute()
        self.trigger_path = require_safe_child(self.appdata, "autoinput.txt", "autoinput trigger")
        self.ack_path = require_safe_child(self.appdata, "autoinput_ack.txt", "autoinput ACK")
        self.hold_ms = hold_ms
        self.expected_pid = expected_pid
        self.timeout_seconds = timeout_seconds
        self.dwell_seconds = dwell_seconds
        self.poll_seconds = poll_seconds
        self.uuid_factory = uuid_factory
        self.clock = clock
        self.wall_clock_ns = wall_clock_ns
        self.sleep = sleep
        self.liveness = liveness
        self.poll_hook = poll_hook
        self.capture_observer = capture_observer

    def require_live(self, phase: str) -> None:
        try:
            result = self.liveness(self.expected_pid)
        except NavigationError:
            raise
        except (ProcessLookupError, PermissionError, OSError) as error:
            fail(f"expected Simulator process PID {self.expected_pid} is not live during {phase}: {error}")
        except Exception as error:
            fail(f"could not determine expected Simulator process PID {self.expected_pid} liveness during {phase}: {error}")
        if result is False:
            fail(f"expected Simulator process PID {self.expected_pid} is not live during {phase}")

    def _read_log(self, previous: FileSnapshot | None) -> tuple[FileSnapshot, list[str], list[UiStateMarker]]:
        snapshot = read_live_appendable_regular_nofollow(self.log_path, "runtime log")
        if previous is not None:
            check_monotonic_log(previous, snapshot)
        lines = complete_lines(snapshot, "runtime log")
        markers = verify_log_lines(lines)
        return snapshot, lines, markers

    def _wait_transition(self, step: NavigationStep, step_number: int, baseline: FileSnapshot,
                         baseline_lines: list[str], baseline_markers: list[UiStateMarker]) -> tuple[FileSnapshot, list[str], list[UiStateMarker], TransitionEvidence]:
        self.require_live(f"transition {step.key} request")
        capture_baseline = self.capture_observer.baseline() if (
            self.capture_observer is not None and step_number in {1, 3, 4, 6}
        ) else None
        remove_regular_nofollow(self.ack_path, "previous autoinput ACK")
        require_absent_output(self.trigger_path, "autoinput trigger")
        request_id = validate_request_id(self.uuid_factory().lower())
        request_wall_ns = self.wall_clock_ns()
        write_new_regular(self.trigger_path, f"id {request_id} {step.key} {self.hold_ms}\n".encode("ascii"), "autoinput trigger")

        current = baseline
        current_lines = baseline_lines
        current_markers = baseline_markers
        processed_lines = len(baseline_lines)
        acknowledged = False
        pressed = False
        observed_marker: UiStateMarker | None = None
        release_scancode: int | None = None
        release_at: float | None = None
        deadline = self.clock() + self.timeout_seconds

        while self.clock() <= deadline:
            pending_phase = "release dwell" if release_at is not None else f"transition {step.key} pending"
            self.require_live(pending_phase)
            if self.poll_hook is not None:
                self.poll_hook()
            self.require_live(pending_phase)
            current, current_lines, current_markers = self._read_log(current)
            new_lines = current_lines[processed_lines:]
            processed_lines = len(current_lines)

            ack: FileSnapshot | None = None
            try:
                ack = read_stable_regular_nofollow(self.ack_path, "autoinput ACK")
            except NavigationError as error:
                if "is missing" not in str(error):
                    raise
            if ack is not None:
                parse_ack(ack, request_id, request_wall_ns)
                acknowledged = True

            request_events = [line for line in new_lines if "* iOS diag: autoinput request " in line]
            if not acknowledged and (any(parse_marker(line) is not None for line in new_lines) or request_events):
                fail("UI state marker or autoinput log arrived before a matching accepted ACK")

            for line in new_lines:
                marker = parse_marker(line)
                if marker is not None:
                    if not acknowledged or not pressed:
                        fail("UI state marker arrived before accepted ACK and matching press log")
                    if observed_marker is not None:
                        fail("unexpected extra UI state marker during one transition")
                    previous = baseline_markers[-1]
                    if marker.pid != previous.pid:
                        fail("UI state marker PID changed during transition")
                    if marker.seq != previous.seq + 1 or marker.frame <= previous.frame:
                        fail("UI state marker did not make one ordered transition")
                    if marker.state != step.expected_state:
                        fail(f"UI state transition expected {step.expected_state}, got {marker.state}")
                    observed_marker = marker
                    self.require_live(f"transition {step.key} after expected UI marker")
                    continue
                press = parse_press(line)
                if press is not None:
                    press_id, key, _scancode, held_ms = press
                    if press_id != request_id or key != step.key or held_ms != self.hold_ms:
                        fail("autoinput press log does not match the current request")
                    if pressed:
                        fail("duplicate autoinput press for one request")
                    pressed = True
                    continue
                release = parse_release(line)
                if release is None:
                    continue
                release_id, scancode = release
                if release_id != request_id:
                    fail(f"concurrent or stale autoinput release observed: {release_id}")
                if not pressed:
                    fail("autoinput release arrived before the matching press log")
                if observed_marker is None:
                    fail("autoinput release arrived before the expected UI state marker")
                if release_scancode is not None:
                    fail("duplicate autoinput release for one request")
                release_scancode = scancode
                release_at = self.clock()
                self.require_live("release dwell")

            if release_at is not None:
                if self.clock() - release_at >= self.dwell_seconds:
                    self.require_live("end release dwell")
                    if observed_marker is None or release_scancode is None:
                        fail("internal transition evidence is incomplete")
                    result = TransitionEvidence(
                        request_id=request_id, key=step.key, state=observed_marker.state,
                        pid=observed_marker.pid, seq=observed_marker.seq,
                        frame=observed_marker.frame, release_scancode=release_scancode,
                    )
                    if capture_baseline is not None:
                        self.capture_observer.observe(
                            {**asdict(result), "step": step_number, "hold_ms": self.hold_ms},
                            capture_baseline,
                        )
                    return current, current_lines, current_markers, result
            if self.poll_seconds:
                self.sleep(self.poll_seconds)

        missing = ("ACK" if not acknowledged else "press log" if not pressed
                   else "state marker" if observed_marker is None else "release/stable dwell")
        fail(f"timed out waiting for {missing} during {step.key} -> {step.expected_state}")

    def run(self) -> list[TransitionEvidence]:
        self.require_live("baseline")
        baseline, lines, markers = self._read_log(None)
        if SYNC_COMPLETE_LINE not in lines:
            fail("runtime log prefix lacks saved-game synchronization anchor")
        if markers[-1].state != "world":
            fail(f"UI navigation must start in world, got {markers[-1].state}")
        if markers[-1].pid != self.expected_pid:
            fail(f"baseline UI state marker PID {markers[-1].pid} does not match expected PID {self.expected_pid}")
        self.require_live("baseline marker")
        require_absent_output(self.ack_path, "initial autoinput ACK")
        require_absent_output(self.trigger_path, "initial autoinput trigger")
        evidence: list[TransitionEvidence] = []
        for step_number, step in enumerate(SEQUENCE, 1):
            baseline, lines, markers, result = self._wait_transition(step, step_number, baseline, lines, markers)
            evidence.append(result)
        remove_regular_nofollow(self.ack_path, "final autoinput ACK")
        remove_regular_nofollow(self.trigger_path, "final autoinput trigger")
        return evidence


def write_snapshot(path: Path, snapshot: FileSnapshot) -> None:
    write_new_regular(path.absolute(), snapshot.data, "navigation log snapshot")


def snapshot_digest(snapshot: FileSnapshot) -> str:
    return hashlib.sha256(snapshot.data).hexdigest()


def write_pre_termination_report(path: Path, snapshot: FileSnapshot,
                                 evidence: Sequence[TransitionEvidence], expected_pid: int,
                                 capture_observer: Any | None = None) -> None:
    validate_expected_pid(expected_pid)
    if any(item.pid != expected_pid for item in evidence):
        fail("navigation evidence PID does not match the expected launch PID")
    document: dict[str, Any] = {
        "version": 1,
        "result": "PASS",
        "phase": "pre-termination",
        "log_device": snapshot.device,
        "log_inode": snapshot.inode,
        "expected_pid": expected_pid,
        "snapshot_sha256": snapshot_digest(snapshot),
        "evidence": [asdict(item) for item in evidence],
        "scope": SEMANTIC_SCOPE,
    }
    if capture_observer is not None:
        document.update({
            "version": 2,
            "ui_capture_root": str(capture_observer.root.absolute()),
            "expected_level": capture_observer.expected_level,
            "captures": capture_observer.selected,
            "provenance": capture_observer.provenance,
        })
    write_new_regular(path.absolute(), (json.dumps(document, sort_keys=True) + "\n").encode("utf-8"), "navigation report")


def _parse_pre_termination_report(path: Path) -> PreTerminationProof:
    data = read_stable_regular_nofollow(path.absolute(), "pre-termination navigation report").data
    try:
        value = json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"pre-termination navigation report is invalid JSON: {error}")
    required = {"version", "result", "phase", "log_device", "log_inode", "expected_pid", "snapshot_sha256", "evidence", "scope"}
    capture_fields = {"ui_capture_root", "expected_level", "captures", "provenance"}
    if (set(value) not in (required, required | capture_fields) or value["version"] not in (1, 2) or value["result"] != "PASS"
            or value["phase"] != "pre-termination" or value["scope"] != SEMANTIC_SCOPE):
        fail("pre-termination navigation report has an invalid schema")
    if (not isinstance(value["log_device"], int) or value["log_device"] < 0
            or not isinstance(value["log_inode"], int) or value["log_inode"] <= 0
            or not isinstance(value["expected_pid"], int) or isinstance(value["expected_pid"], bool)
            or value["expected_pid"] <= 0):
        fail("pre-termination navigation report has invalid log identity")
    digest = value["snapshot_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        fail("pre-termination navigation report has invalid snapshot digest")
    if not isinstance(value["evidence"], list) or len(value["evidence"]) != len(SEQUENCE):
        fail("pre-termination navigation report has invalid evidence count")
    parsed: list[TransitionEvidence] = []
    for expected, item in zip(SEQUENCE, value["evidence"]):
        if not isinstance(item, dict) or set(item) != {"request_id", "key", "state", "pid", "seq", "frame", "release_scancode"}:
            fail("pre-termination navigation report has malformed evidence")
        try:
            evidence = TransitionEvidence(**item)
        except TypeError as error:
            fail(f"pre-termination navigation report has invalid evidence: {error}")
        validate_request_id(evidence.request_id)
        if (evidence.key != expected.key or evidence.state != expected.expected_state or evidence.pid != value["expected_pid"]
                or not all(isinstance(getattr(evidence, field), int) for field in ("pid", "seq", "frame", "release_scancode"))
                or evidence.pid <= 0 or evidence.seq <= 0 or evidence.frame < 0 or evidence.release_scancode <= 0):
            fail("pre-termination navigation report evidence violates the approved sequence")
        parsed.append(evidence)
    for previous, current in zip(parsed, parsed[1:]):
        if (current.pid != previous.pid or current.seq != previous.seq + 1 or current.frame <= previous.frame):
            fail("pre-termination navigation report evidence is not one ordered same-PID sequence")
    captures: tuple[dict[str, Any], ...] = ()
    capture_root: str | None = None
    expected_level: str | None = None
    provenance: dict[str, str] | None = None
    if value["version"] == 2:
        if (set(value) != required | capture_fields or not isinstance(value["ui_capture_root"], str)
                or not isinstance(value["expected_level"], str)
                or not isinstance(value["captures"], list) or type(value["provenance"]) is not dict
                or not all(type(key) is str and type(item) is str for key, item in value["provenance"].items())):
            fail("pre-termination UI capture report has invalid schema")
        captures = tuple(value["captures"])
        capture_root, expected_level, provenance = value["ui_capture_root"], value["expected_level"], value["provenance"]
    elif set(value) != required:
        fail("pre-termination navigation report has an invalid schema")
    return PreTerminationProof(value["log_device"], value["log_inode"], value["expected_pid"], digest,
                               tuple(parsed), captures, capture_root, expected_level, provenance)


def finalize_after_termination(log_path: Path, snapshot_path: Path, pre_report_path: Path,
                               final_report_path: Path) -> None:
    """Revalidate the immutable pre-stop prefix after the caller stopped the app."""

    final_report_path = final_report_path.absolute()
    require_absent_output(final_report_path, "post-termination navigation report")
    proof = _parse_pre_termination_report(pre_report_path)
    snapshot = read_stable_regular_nofollow(snapshot_path.absolute(), "navigation log snapshot")
    if snapshot_digest(snapshot) != proof.snapshot_sha256:
        fail("navigation log snapshot does not match pre-termination report")
    final = read_stable_regular_nofollow(log_path.absolute(), "post-termination runtime log")
    if (final.device, final.inode) != (proof.log_device, proof.log_inode):
        fail("post-termination runtime log changed inode")
    if final.size < snapshot.size or not final.data.startswith(snapshot.data):
        fail("post-termination runtime log was truncated or rewritten")
    lines = terminated_lines(final, "post-termination runtime log")
    markers = verify_log_lines(lines)
    if SYNC_COMPLETE_LINE not in lines:
        fail("post-termination runtime log lacks saved-game synchronization anchor")
    expected_markers = [(item.pid, item.seq, item.frame, item.state) for item in proof.evidence]
    observed = [(item.pid, item.seq, item.frame, item.state) for item in markers]
    cursor = 0
    for expected in expected_markers:
        try:
            cursor = observed.index(expected, cursor) + 1
        except ValueError:
            fail("post-termination runtime log does not retain command UI evidence")
    final_marker = markers[-1]
    if (final_marker.pid, final_marker.seq, final_marker.frame, final_marker.state) != expected_markers[-1]:
        fail("post-termination runtime log contains an unexpected UI state after the final dwell")
    manifest: str | None = None
    manifest_publication: Any | None = None
    capture_module: Any | None = None
    if proof.ui_capture_root is not None:
        try:
            import ui_capture_evidence
            capture_module = ui_capture_evidence
            manifest_publication = ui_capture_evidence.finalize_run_with_identity(
                Path(proof.ui_capture_root), [asdict(item) for item in proof.evidence],
                list(proof.captures), proof.expected_pid, proof.expected_level or "", proof.provenance or {},
            )
            manifest = str(manifest_publication.path)
        except (ImportError, RuntimeError) as error:
            fail(f"post-termination native UI capture proof failed: {error}")
    document = {
        "version": 1,
        "result": "PASS",
        "phase": "post-termination",
        "pre_report": str(pre_report_path.absolute()),
        "snapshot": str(snapshot_path.absolute()),
        "final_log_sha256": snapshot_digest(final),
        "evidence": [asdict(item) for item in proof.evidence],
        "scope": SEMANTIC_SCOPE,
    }
    if manifest is not None:
        document["ui_capture_manifest"] = manifest
    try:
        write_new_regular(final_report_path, (json.dumps(document, sort_keys=True) + "\n").encode("utf-8"),
                          "post-termination navigation report")
    except Exception as report_error:
        if manifest_publication is not None and capture_module is not None:
            try:
                capture_module.remove_exact_manifest(manifest_publication)
            except Exception as cleanup_error:
                fail(f"{report_error}; exact UI capture manifest rollback failed: {cleanup_error}")
        raise


def resolve_simulator_documents(udid: str, bundle_id: str) -> Path:
    """Resolve a Simulator-only container via simctl; no device tool is involved."""

    if SIMULATOR_UDID_RE.fullmatch(udid) is None:
        fail(f"invalid Simulator UDID: {udid!r}")
    result = subprocess.run(
        ("xcrun", "simctl", "get_app_container", udid, bundle_id, "data"),
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        fail("simctl could not resolve the supplied Simulator application container")
    try:
        path = result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        fail(f"simctl container path is not UTF-8: {error}")
    if not path or "\n" in path:
        fail(f"simctl returned malformed application container path: {path!r}")
    container = Path(path).absolute()
    require_real_directory(container, "Simulator application container")
    documents = require_safe_child(container, "Documents", "Simulator Documents")
    require_real_directory(documents, "Simulator Documents")
    return documents


def command_verify_log(args: argparse.Namespace) -> int:
    snapshot = read_stable_regular_nofollow(Path(args.log).absolute(), "runtime log")
    markers = verify_log_lines(complete_lines(snapshot, "runtime log"))
    print(json.dumps({"result": "PASS", "markers": [asdict(marker) for marker in markers]}, sort_keys=True))
    return 0


def command_run(args: argparse.Namespace) -> int:
    if not args.integration:
        fail("run requires explicit --integration; it is reserved for an isolated Simulator")
    validate_expected_pid(args.expected_pid)
    documents = Path(args.documents).absolute() if args.documents else resolve_simulator_documents(args.simulator_udid, args.bundle_id)
    if args.documents and SIMULATOR_UDID_RE.fullmatch(args.simulator_udid) is None:
        fail(f"invalid Simulator UDID: {args.simulator_udid!r}")
    log_path = Path(args.log).absolute() if args.log else documents / "xr_boot.log"
    capture_values = (args.ui_capture_root, args.ui_capture_run_uuid, args.ui_capture_expected_level,
                      args.ui_capture_runtime, args.ui_capture_renderer, args.ui_capture_git_revision,
                      args.ui_capture_source_tree_sha256)
    if any(capture_values) and any(value is None for value in capture_values):
        fail("native UI capture arguments must be supplied as one complete set")
    capture_observer = None
    provenance = None
    if args.ui_capture_root is not None:
        try:
            import ui_capture_evidence
            provenance = {"simulator_udid": args.simulator_udid,
                          "runtime": args.ui_capture_runtime,
                          "renderer": args.ui_capture_renderer,
                          "git_revision": args.ui_capture_git_revision,
                          "source_tree_sha256": args.ui_capture_source_tree_sha256}
            capture_observer = ui_capture_evidence.UiCaptureObserver(
                documents, log_path, Path(args.ui_capture_root).absolute(),
                args.ui_capture_run_uuid, args.expected_pid, args.ui_capture_expected_level,
                provenance,
            )
        except (ImportError, RuntimeError) as error:
            fail(f"native UI capture setup failed: {error}")
    controller = UiNavigationController(
        documents, log_path, expected_pid=args.expected_pid, hold_ms=args.hold_ms, timeout_seconds=args.timeout_seconds,
        dwell_seconds=args.dwell_seconds, poll_seconds=args.poll_seconds,
        capture_observer=capture_observer,
    )
    evidence = controller.run()
    snapshot = read_live_appendable_regular_nofollow(log_path, "runtime log")
    controller.require_live("snapshot publication")
    write_snapshot(Path(args.snapshot), snapshot)
    controller.require_live("report publication")
    write_pre_termination_report(Path(args.report), snapshot, evidence, args.expected_pid,
                                 capture_observer)
    print(f"PASS — semantic Simulator UI navigation: {args.report}")
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    finalize_after_termination(Path(args.log), Path(args.snapshot), Path(args.pre_report), Path(args.final_report))
    print(f"PASS — post-termination semantic Simulator UI navigation: {args.final_report}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify-log", help="read-only marker-stream validation")
    verify.add_argument("--log", required=True)
    verify.set_defaults(handler=command_verify_log)

    run = commands.add_parser("run", help="explicit isolated-Simulator navigation controller")
    run.add_argument("--integration", action="store_true", help="acknowledge local Simulator writes")
    run.add_argument("--simulator-udid", required=True)
    run.add_argument("--expected-pid", type=int, required=True,
                     help="positive PID returned by the one Simulator launch proof")
    run.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID)
    run.add_argument("--documents", help="explicit local Simulator Documents directory; skips simctl lookup")
    run.add_argument("--log", help="local engine log; defaults to Documents/xr_boot.log")
    run.add_argument("--snapshot", required=True, help="new pre-termination log snapshot; existing files are refused")
    run.add_argument("--report", required=True, help="new pre-termination report path; existing files are refused")
    run.add_argument("--ui-capture-root", help="new-only parent directory for native UI captures")
    run.add_argument("--ui-capture-run-uuid")
    run.add_argument("--ui-capture-expected-level")
    run.add_argument("--ui-capture-runtime")
    run.add_argument("--ui-capture-renderer")
    run.add_argument("--ui-capture-git-revision")
    run.add_argument("--ui-capture-source-tree-sha256")
    run.add_argument("--hold-ms", type=int, default=DEFAULT_HOLD_MS)
    run.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    run.add_argument("--dwell-seconds", type=float, default=DEFAULT_DWELL_SECONDS)
    run.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    run.set_defaults(handler=command_run)
    finalize = commands.add_parser("finalize", help="post-termination log revalidation")
    finalize.add_argument("--log", required=True)
    finalize.add_argument("--snapshot", required=True)
    finalize.add_argument("--pre-report", required=True)
    finalize.add_argument("--final-report", required=True)
    finalize.set_defaults(handler=command_finalize)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        return args.handler(args)
    except NavigationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
