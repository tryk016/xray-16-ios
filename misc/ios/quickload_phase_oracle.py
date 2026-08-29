#!/usr/bin/env python3
"""Validate one iOS QuickLoad phase-marker sequence, fail-closed by default."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


SCHEMA = "openxray.ios-quickload-phase.v1"
CANDIDATE_PREFIX = "* iOS quickload phase"
MAX_UINT64 = (1 << 64) - 1
MAX_UINT32 = (1 << 32) - 1
MAX_PID = (1 << 31) - 1
PHASES = (
    "deferred",
    "event_begin",
    "objects_removed",
    "restart_begin",
    "old_alife_destroyed",
    "ids_cleared",
    "alife_construct_begin",
    "new_alife_constructed",
    "precache_complete",
    "restart_complete",
    "event_complete",
)
MEMORY_PHASES = frozenset((
    "event_begin",
    "objects_removed",
    "old_alife_destroyed",
    "alife_construct_begin",
    "new_alife_constructed",
    "precache_complete",
    "restart_complete",
))
ZERO_FIELDS = (
    "phys_current_k",
    "phys_peak_k",
    "resident_current_k",
    "resident_peak_k",
    "compressed_current_k",
    "compressed_peak_k",
)
INTEGER = r"(?:0|[1-9][0-9]*)"
MARKER_RE = re.compile(
    rf"^\* iOS quickload phase v1 "
    rf"pid=(?P<pid>[1-9][0-9]*) "
    rf"request=(?P<request>[1-9][0-9]*) "
    rf"frame=(?P<frame>{INTEGER}) "
    rf"phase=(?P<phase>{'|'.join(PHASES)}) "
    rf"memory=(?P<memory>none|task_vm_info|unavailable) "
    rf"phys_current_k=(?P<phys_current_k>{INTEGER}) "
    rf"phys_peak_k=(?P<phys_peak_k>{INTEGER}) "
    rf"resident_current_k=(?P<resident_current_k>{INTEGER}) "
    rf"resident_peak_k=(?P<resident_peak_k>{INTEGER}) "
    rf"compressed_current_k=(?P<compressed_current_k>{INTEGER}) "
    rf"compressed_peak_k=(?P<compressed_peak_k>{INTEGER})$"
)


class OracleError(ValueError):
    """A QuickLoad marker sequence is malformed or does not meet its contract."""


def positive_uint64(value: str) -> int:
    parsed = int(value)
    if parsed <= 0 or parsed > MAX_UINT64:
        raise argparse.ArgumentTypeError("must be a positive uint64")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="OpenXRay text log containing one v1 QuickLoad sequence")
    parser.add_argument("--expected-pid", type=positive_uint64, required=True)
    parser.add_argument("--expected-request", type=positive_uint64, required=True)
    parser.add_argument(
        "--allow-truncated",
        action="store_true",
        help="accept a well-formed proper phase prefix and report its next expected phase",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="write the report to a new, previously nonexistent regular path",
    )
    return parser.parse_args()


def parse_uint(value: str, maximum: int, field: str, line_number: int) -> int:
    parsed = int(value)
    if parsed > maximum:
        raise OracleError(f"line {line_number}: {field} exceeds its canonical range")
    return parsed


def parse_marker(line: str, line_number: int) -> dict[str, object]:
    matched = MARKER_RE.fullmatch(line)
    if matched is None:
        raise OracleError(f"line {line_number}: malformed iOS quickload phase v1 marker")

    fields = matched.groupdict()
    marker: dict[str, object] = {
        "pid": parse_uint(fields["pid"], MAX_PID, "pid", line_number),
        "request": parse_uint(fields["request"], MAX_UINT64, "request", line_number),
        "frame": parse_uint(fields["frame"], MAX_UINT32, "frame", line_number),
        "phase": fields["phase"],
        "memory": fields["memory"],
    }
    for field in ZERO_FIELDS:
        marker[field] = parse_uint(fields[field], MAX_UINT64, field, line_number)
    validate_memory(marker, line_number)
    return marker


def validate_memory(marker: dict[str, object], line_number: int) -> None:
    phase = marker["phase"]
    memory = marker["memory"]
    values = [marker[field] for field in ZERO_FIELDS]
    if not all(isinstance(value, int) for value in values):
        raise OracleError(f"line {line_number}: memory values are not integers")
    has_values = any(values)
    if phase in MEMORY_PHASES:
        if memory == "none":
            raise OracleError(f"line {line_number}: phase {phase} must capture task_vm_info")
        if memory == "unavailable" and has_values:
            raise OracleError(f"line {line_number}: unavailable memory fields must be zero")
        return
    if memory != "none" or has_values:
        raise OracleError(f"line {line_number}: phase {phase} must use memory=none and zero fields")


def collect_markers(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as error:
        raise OracleError(f"cannot read log: {error}") from error
    except UnicodeError as error:
        raise OracleError("cannot read log as UTF-8") from error

    markers: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, 1):
        if line.startswith(CANDIDATE_PREFIX):
            markers.append(parse_marker(line, line_number))
    if not markers:
        raise OracleError("no iOS quickload phase v1 markers")
    return markers


def validate_sequence(markers: list[dict[str, object]], expected_pid: int,
                      expected_request: int, allow_truncated: bool) -> dict[str, object]:
    selected = [
        marker for marker in markers
        if marker["pid"] == expected_pid and marker["request"] == expected_request
    ]
    if not selected:
        raise OracleError("no markers match --expected-pid/--expected-request")

    frames = [int(marker["frame"]) for marker in selected]
    if any(current < previous for previous, current in zip(frames, frames[1:])):
        raise OracleError("selected marker frame regression")

    observed = [str(marker["phase"]) for marker in selected]
    expected_prefix = list(PHASES[:len(observed)])
    if observed != expected_prefix:
        raise OracleError(f"phase order mismatch: expected prefix {expected_prefix}, got {observed}")
    if len(observed) > len(PHASES):
        raise OracleError("too many phase markers")
    complete = len(observed) == len(PHASES)
    if not complete and not allow_truncated:
        raise OracleError(
            f"incomplete phase sequence: last_completed={observed[-1]} "
            f"next_expected={PHASES[len(observed)]}"
        )

    report: dict[str, object] = {
        "allow_truncated": allow_truncated,
        "last_completed": observed[-1],
        "markers": selected,
        "next_expected": None if complete else PHASES[len(observed)],
        "phase_count": len(observed),
        "pid": expected_pid,
        "request": expected_request,
        "result": "PASS" if complete else "TRUNCATED",
        "schema": SCHEMA,
    }
    return report


def canonical_json(value: dict[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def write_new_output(path: Path, payload: bytes) -> None:
    if path.parent == path or not path.parent.is_dir():
        raise OracleError("json output parent is unavailable")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        raise OracleError(f"cannot create json output: {error}") from error
    metadata: os.stat_result | None = None
    write_error: OracleError | None = None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_nlink != 1:
            raise OracleError("json output is not a private regular file")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OracleError("cannot write json output: short write")
            offset += written
        os.fsync(descriptor)
    except OSError as error:
        write_error = OracleError(f"cannot write json output: {error}")
    except OracleError as error:
        write_error = error
    finally:
        try:
            os.close(descriptor)
        except OSError as error:
            if write_error is None:
                write_error = OracleError(f"cannot close json output: {error}")
    if write_error is None:
        return

    try:
        current = os.stat(path, follow_symlinks=False)
        if (metadata is not None and stat.S_ISREG(current.st_mode)
                and current.st_uid == metadata.st_uid and current.st_dev == metadata.st_dev
                and current.st_ino == metadata.st_ino and current.st_nlink == 1):
            os.unlink(path)
    except OSError:
        # Preserve the original write failure: a foreign replacement must never
        # turn cleanup into authority over a path we did not create.
        pass
    raise write_error


def main() -> int:
    arguments = parse_args()
    try:
        markers = collect_markers(arguments.log)
        report = validate_sequence(
            markers, arguments.expected_pid, arguments.expected_request, arguments.allow_truncated,
        )
        payload = canonical_json(report)
        if arguments.json_output is not None:
            write_new_output(arguments.json_output, payload)
        sys.stdout.buffer.write(payload)
        return 0
    except OracleError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
