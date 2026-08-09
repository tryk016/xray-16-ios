#!/usr/bin/env python3
"""Validate and summarize versioned iOS startup-sector evidence markers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import stat
import sys


CANDIDATE_STEM = "* iOS sector startup"
INVALID_SECTOR = 0xFFFFFFFF
MAX_EPOCH = 0xFFFFFFFFFFFFFFFF
MAX_FRAME = 0xFFFFFFFF
ALLOWED_FALLBACK_RADII = {"0.5", "1.0", "2.0", "4.0", "8.0", "16.0", "32.0"}
FALLBACK_DIRECTIONS = (
    (Decimal("1"), Decimal("0")),
    (Decimal("-1"), Decimal("0")),
    (Decimal("0"), Decimal("1")),
    (Decimal("0"), Decimal("-1")),
    (Decimal("0.70710678"), Decimal("0.70710678")),
    (Decimal("-0.70710678"), Decimal("0.70710678")),
    (Decimal("0.70710678"), Decimal("-0.70710678")),
    (Decimal("-0.70710678"), Decimal("-0.70710678")),
)
FALLBACK_COMPONENT_TOLERANCE = Decimal("0.0011")
COORDINATE = r"-?(?:0|[1-9][0-9]*)\.[0-9]{3}"
RADIUS = r"(?:0|[1-9][0-9]*)\.[0-9]"
MARKER_RE = re.compile(
    rf"^\* iOS sector startup v1 "
    rf"pid=(?P<pid>[1-9][0-9]*) "
    rf"epoch=(?P<epoch>[1-9][0-9]*) "
    rf"frame=(?P<frame>0|[1-9][0-9]*) "
    rf"level=(?P<level>[A-Za-z0-9_-]{{1,63}}) "
    rf"trigger=(?P<trigger>level_load|quick_load) "
    rf"status=(?P<status>resolved|unresolved) "
    rf"method=(?P<method>exact|fallback|retained|none) "
    rf"sector=(?P<sector>0|[1-9][0-9]*) "
    rf"camera=\((?P<cx>{COORDINATE}),(?P<cy>{COORDINATE}),(?P<cz>{COORDINATE})\) "
    rf"probe=\((?P<px>{COORDINATE}),(?P<py>{COORDINATE}),(?P<pz>{COORDINATE})\) "
    rf"radius=(?P<radius>{RADIUS})$"
)


class OracleError(ValueError):
    """Raised when startup-sector evidence is malformed or contradictory."""


@dataclass(frozen=True)
class Marker:
    pid: int
    epoch: int
    frame: int
    level: str
    trigger: str
    status: str
    method: str
    sector: int
    camera_text: tuple[str, str, str]
    probe_text: tuple[str, str, str]
    radius_text: str


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def epoch_anchor(value: str) -> int:
    parsed = int(value)
    if parsed < 0 or parsed > MAX_EPOCH:
        raise argparse.ArgumentTypeError("must be a uint64 epoch, including zero")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="OpenXRay text log containing v1 markers")
    parser.add_argument("--expected-pid", type=positive_int, required=True)
    parser.add_argument("--after-epoch", type=epoch_anchor, required=True)
    parser.add_argument(
        "--expect-trigger",
        action="append",
        choices=("level_load", "quick_load"),
        dest="expected_triggers",
        required=True,
        help="expected startup trigger after the anchor; repeat for an exact ordered batch",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="write the report to this new, previously nonexistent regular path",
    )
    return parser.parse_args()


def parse_marker(line: str, line_number: int) -> Marker:
    match = MARKER_RE.fullmatch(line)
    if match is None:
        raise OracleError(f"line {line_number}: malformed v1 marker")

    fields = match.groupdict()
    pid = int(fields["pid"])
    epoch = int(fields["epoch"])
    frame = int(fields["frame"])
    sector = int(fields["sector"])
    if epoch > MAX_EPOCH:
        raise OracleError(f"line {line_number}: epoch exceeds uint64")
    if frame > MAX_FRAME:
        raise OracleError(f"line {line_number}: frame exceeds uint32")
    if sector > INVALID_SECTOR:
        raise OracleError(f"line {line_number}: sector exceeds uint32")

    camera_text = (fields["cx"], fields["cy"], fields["cz"])
    probe_text = (fields["px"], fields["py"], fields["pz"])
    marker = Marker(
        pid=pid,
        epoch=epoch,
        frame=frame,
        level=fields["level"],
        trigger=fields["trigger"],
        status=fields["status"],
        method=fields["method"],
        sector=sector,
        camera_text=camera_text,
        probe_text=probe_text,
        radius_text=fields["radius"],
    )
    validate_marker(marker, line_number)
    return marker


def validate_marker(marker: Marker, line_number: int) -> None:
    same_position = marker.camera_text == marker.probe_text
    if marker.status == "resolved":
        if marker.sector == INVALID_SECTOR:
            raise OracleError(f"line {line_number}: resolved marker has the invalid sector")
        if marker.method == "exact":
            if not same_position or marker.radius_text != "0.0":
                raise OracleError(f"line {line_number}: exact marker metadata is contradictory")
        elif marker.method == "fallback":
            if marker.radius_text not in ALLOWED_FALLBACK_RADII:
                raise OracleError(f"line {line_number}: fallback radius is not in the policy")
            if not valid_fallback_geometry(marker):
                raise OracleError(f"line {line_number}: fallback probe does not match a policy direction")
        elif marker.method == "retained":
            if marker.trigger != "quick_load":
                raise OracleError(f"line {line_number}: retained sector is valid only for quick_load")
            if not same_position or marker.radius_text != "0.0":
                raise OracleError(f"line {line_number}: retained marker metadata is contradictory")
        else:
            raise OracleError(f"line {line_number}: resolved marker cannot use method={marker.method}")
        return

    if marker.method not in {"fallback", "none"}:
        raise OracleError(f"line {line_number}: unresolved marker cannot use method={marker.method}")
    if marker.sector != INVALID_SECTOR:
        raise OracleError(f"line {line_number}: unresolved marker must use the invalid sector")
    if not same_position or marker.radius_text != "0.0":
        raise OracleError(f"line {line_number}: unresolved marker metadata is contradictory")


def valid_fallback_geometry(marker: Marker) -> bool:
    camera = tuple(Decimal(component) for component in marker.camera_text)
    probe = tuple(Decimal(component) for component in marker.probe_text)
    radius = Decimal(marker.radius_text)
    delta = tuple(probe[index] - camera[index] for index in range(3))
    for direction_x, direction_z in FALLBACK_DIRECTIONS:
        expected = (direction_x * radius, Decimal("0"), direction_z * radius)
        if all(
            abs(delta[index] - expected[index]) <= FALLBACK_COMPONENT_TOLERANCE
            for index in range(3)
        ):
            return True
    return False


def classify_epoch(markers: list[Marker]) -> dict[str, object]:
    first = markers[0]
    if len(markers) == 1:
        if first.status == "resolved":
            return {
                "classification": first.method,
                "method": first.method,
            }
        return {
            "classification": "unresolved",
            "unresolved_method": first.method,
        }

    second = markers[1]
    if (
        len(markers) != 2
        or first.status != "unresolved"
        or second.status != "resolved"
        or second.method not in {"exact", "fallback"}
    ):
        raise OracleError(f"epoch {first.epoch}: only unresolved -> detected resolved is permitted")
    return {
        "classification": "recovered",
        "recovery_method": second.method,
        "unresolved_method": first.method,
    }


def analyze(lines: list[str], expected_pid: int | None = None) -> dict[str, object]:
    markers: list[Marker] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r\n")
        if line.startswith(CANDIDATE_STEM):
            markers.append(parse_marker(line, line_number))

    if not markers:
        raise OracleError("no iOS sector startup v1 markers found")

    pids = {marker.pid for marker in markers}
    if len(pids) != 1:
        raise OracleError(f"marker PID changed: {sorted(pids)}")
    pid = next(iter(pids))
    if expected_pid is not None and pid != expected_pid:
        raise OracleError(f"marker PID {pid} does not match expected PID {expected_pid}")

    previous_frame: int | None = None
    current_epoch: int | None = None
    grouped: list[list[Marker]] = []
    for marker in markers:
        if previous_frame is not None and marker.frame <= previous_frame:
            raise OracleError(
                f"frame order is not strictly increasing: {previous_frame} -> {marker.frame}"
            )
        previous_frame = marker.frame

        if current_epoch is None or marker.epoch != current_epoch:
            if current_epoch is not None:
                if marker.epoch <= current_epoch:
                    raise OracleError(f"epoch returned from {current_epoch} to {marker.epoch}")
                expected_epoch = current_epoch + 1
                if marker.epoch != expected_epoch:
                    raise OracleError(
                        f"epoch gap after {current_epoch}: expected {expected_epoch}, got {marker.epoch}"
                    )
            current_epoch = marker.epoch
            grouped.append([marker])
            continue

        epoch_markers = grouped[-1]
        if marker.level != epoch_markers[0].level:
            raise OracleError(f"epoch {marker.epoch}: level changed within the epoch")
        if marker.trigger != epoch_markers[0].trigger:
            raise OracleError(f"epoch {marker.epoch}: trigger changed within the epoch")
        epoch_markers.append(marker)

    epochs: list[dict[str, object]] = []
    for epoch_markers in grouped:
        first = epoch_markers[0]
        for marker in epoch_markers[1:]:
            if marker.level != first.level or marker.trigger != first.trigger:
                raise OracleError(f"epoch {first.epoch}: marker metadata changed within the epoch")
        classification = classify_epoch(epoch_markers)
        epochs.append(
            {
                "epoch": first.epoch,
                "frames": [marker.frame for marker in epoch_markers],
                "level": first.level,
                "markers": len(epoch_markers),
                "trigger": first.trigger,
                **classification,
            }
        )

    return {
        "epochs": epochs,
        "markers": len(markers),
        "pid": pid,
        "result": "PASS",
    }


def validate_expected_suffix(
    report: dict[str, object], after_epoch: int, expected_triggers: list[str]
) -> dict[str, object]:
    if not expected_triggers:
        raise OracleError("at least one expected startup trigger is required")
    if len(expected_triggers) > MAX_EPOCH - after_epoch:
        raise OracleError("expected epoch range exceeds uint64")

    expected_epochs = list(
        range(after_epoch + 1, after_epoch + len(expected_triggers) + 1)
    )
    report_epochs = report["epochs"]
    if not isinstance(report_epochs, list):
        raise OracleError("internal report epoch structure is invalid")
    actual_suffix = [
        epoch for epoch in report_epochs
        if isinstance(epoch, dict) and epoch.get("epoch", -1) > after_epoch
    ]
    actual_epochs = [epoch.get("epoch") for epoch in actual_suffix]
    actual_triggers = [epoch.get("trigger") for epoch in actual_suffix]
    if actual_epochs != expected_epochs:
        raise OracleError(
            f"expected anchored epochs {expected_epochs}, got {actual_epochs}"
        )
    if actual_triggers != expected_triggers:
        raise OracleError(
            f"expected trigger sequence {expected_triggers}, got {actual_triggers}"
        )

    return {
        **report,
        "expectation": {
            "after_epoch": after_epoch,
            "epochs": expected_epochs,
            "triggers": list(expected_triggers),
        },
    }


DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def open_parent_directory_no_follow(path: Path) -> tuple[int, str]:
    raw_path = os.fspath(path)
    if not raw_path or "\0" in raw_path:
        raise OracleError(f"invalid path: {path}")

    parts = Path(raw_path).parts
    if not parts:
        raise OracleError(f"invalid path: {path}")
    absolute = os.path.isabs(raw_path)
    components = list(parts[1:] if absolute else parts)
    if not components or components[-1] in {"", ".", ".."}:
        raise OracleError(f"path must name a file: {path}")
    if any(component in {"", ".", ".."} for component in components):
        raise OracleError(f"path traversal components are forbidden: {path}")

    directory_fd = os.open("/" if absolute else ".", DIRECTORY_OPEN_FLAGS)
    try:
        for component in components[:-1]:
            next_fd = os.open(component, DIRECTORY_OPEN_FLAGS, dir_fd=directory_fd)
            details = os.fstat(next_fd)
            if not stat.S_ISDIR(details.st_mode):
                os.close(next_fd)
                raise OracleError(f"path component is not a directory: {component}")
            os.close(directory_fd)
            directory_fd = next_fd
    except OSError as error:
        os.close(directory_fd)
        raise OracleError(f"path component is not an accessible non-symlink directory: {error}") from error
    except Exception:
        os.close(directory_fd)
        raise
    return directory_fd, components[-1]


def read_regular_file_no_follow(path: Path) -> str:
    parent_fd, name = open_parent_directory_no_follow(path)
    file_fd = -1
    try:
        file_fd = os.open(name, FILE_READ_FLAGS, dir_fd=parent_fd)
        details = os.fstat(file_fd)
        if not stat.S_ISREG(details.st_mode):
            raise OracleError(f"log must be a regular non-symlink file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    except OSError as error:
        raise OracleError(f"could not open regular non-symlink log: {error}") from error
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def write_new_report(path: Path, rendered: str, *, after_parent_open=None) -> None:
    parent_fd, name = open_parent_directory_no_follow(path)
    file_fd = -1
    try:
        if after_parent_open is not None:
            after_parent_open()
        file_fd = os.open(name, FILE_CREATE_FLAGS, 0o600, dir_fd=parent_fd)
        created_details = os.fstat(file_fd)
        if not stat.S_ISREG(created_details.st_mode):
            raise OracleError(f"JSON output is not a regular file: {path}")

        encoded = (rendered + "\n").encode("utf-8")
        offset = 0
        while offset < len(encoded):
            written = os.write(file_fd, encoded[offset:])
            if written <= 0:
                raise OSError("short write while creating JSON output")
            offset += written
    except FileExistsError as error:
        raise OracleError(f"JSON output already exists: {path}") from error
    except (OSError, OracleError) as error:
        if isinstance(error, OracleError):
            raise
        raise OracleError(f"could not create JSON output: {error}") from error
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def main() -> int:
    args = parse_args()
    try:
        log_text = read_regular_file_no_follow(args.log)
        report = analyze(log_text.splitlines(), args.expected_pid)
        report = validate_expected_suffix(
            report, args.after_epoch, args.expected_triggers
        )
        rendered = json.dumps(report, indent=2, sort_keys=True)
        if args.json_output is not None:
            write_new_report(args.json_output, rendered)
    except (OSError, UnicodeError, OracleError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2

    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
