#!/usr/bin/env python3
"""Fail-closed parsers for the physical UI-automation runner contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath
import re
import sys
from typing import Any
from urllib.parse import unquote_to_bytes, urlsplit


LIFECYCLE_RE = re.compile(
    rb"^\* iOS lifecycle v1 pid=([1-9][0-9]*) seq=([1-9][0-9]*) event=(deactivate|activate)$"
)
SECTOR_STARTUP_RE = re.compile(
    rb"^\* iOS sector startup v1 "
    rb"pid=(?P<pid>[1-9][0-9]*) "
    rb"epoch=[1-9][0-9]* "
    rb"frame=(?:0|[1-9][0-9]*) "
    rb"level=[A-Za-z0-9_-]{1,63} "
    rb"trigger=(?:level_load|quick_load) "
    rb"status=(?P<status>resolved|unresolved) "
    rb"method=(?:exact|fallback|retained|none) "
    rb"sector=(?:0|[1-9][0-9]*) "
    rb"camera=\(-?(?:0|[1-9][0-9]*)\.[0-9]{3},-?(?:0|[1-9][0-9]*)\.[0-9]{3},"
    rb"-?(?:0|[1-9][0-9]*)\.[0-9]{3}\) "
    rb"probe=\(-?(?:0|[1-9][0-9]*)\.[0-9]{3},-?(?:0|[1-9][0-9]*)\.[0-9]{3},"
    rb"-?(?:0|[1-9][0-9]*)\.[0-9]{3}\) "
    rb"radius=(?:0|[1-9][0-9]*)\.[0-9]$"
)


class ContractError(RuntimeError):
    """A device-control response did not meet the runner's evidence contract."""


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be a JSON object")
    return value


def _positive_integer(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError(f"{name} must be a positive integer")
    return value


def load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"could not read JSON output {path!r}: {error}") from error
    return _require_mapping(value, "devicectl JSON root")


def successful_result(document: dict[str, Any]) -> dict[str, Any]:
    info = _require_mapping(document.get("info"), "devicectl JSON info")
    if info.get("outcome") != "success":
        raise ContractError("devicectl JSON outcome is not success")
    return _require_mapping(document.get("result"), "devicectl JSON result")


def launch_pid(document: dict[str, Any]) -> int:
    """Return the exact positive PID from `device process launch` JSON."""

    result = successful_result(document)
    process = _require_mapping(result.get("process"), "launch result process")
    return _positive_integer(process.get("processIdentifier"), "launch processIdentifier")


def file_uri_path(value: Any, name: str) -> PurePosixPath:
    """Parse one absolute local ``file:`` URI without accepting an authority."""

    if not isinstance(value, str):
        raise ContractError(f"{name} must be a file URI string")
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ContractError(f"{name} has an invalid percent escape")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ContractError(f"{name} is not a valid file URI") from error
    if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
        raise ContractError(f"{name} must be a local file URI without host, query, or fragment")
    try:
        decoded = unquote_to_bytes(parsed.path).decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ContractError(f"{name} is not UTF-8 after percent decoding") from error
    if not decoded.startswith("/") or "\x00" in decoded:
        raise ContractError(f"{name} must decode to an absolute local path")
    path = PurePosixPath(decoded)
    if any(component in {".", ".."} for component in path.parts):
        raise ContractError(f"{name} must not contain dot path components")
    return path


def verify_running_process(
    document: dict[str, Any], *, expected_pid: int, executable: str
) -> int:
    """Require exactly one matching `runningProcesses` record for the launched app."""

    _positive_integer(expected_pid, "expected PID")
    if "/" in executable or executable in {".", ".."}:
        raise ContractError("expected process identity is invalid")

    result = successful_result(document)
    processes = result.get("runningProcesses")
    if not isinstance(processes, list):
        raise ContractError("runningProcesses must be a JSON array")

    matching_pid: list[dict[str, Any]] = []
    for index, value in enumerate(processes):
        process = _require_mapping(value, f"runningProcesses[{index}]")
        pid = _positive_integer(process.get("processIdentifier"),
                                f"runningProcesses[{index}].processIdentifier")
        if pid == expected_pid:
            matching_pid.append(process)

    if len(matching_pid) != 1:
        raise ContractError(
            f"runningProcesses must contain exactly one record for launched PID {expected_pid}; "
            f"found {len(matching_pid)}"
        )

    process = matching_pid[0]
    executable_path = file_uri_path(process.get("executable"), f"running PID {expected_pid} executable")
    if executable_path.name != executable:
        raise ContractError(
            f"running PID {expected_pid} executable is not {executable!r}: {str(executable_path)!r}"
        )
    return expected_pid


def cleanup_process_state(
    document: dict[str, Any], *, expected_pid: int, executable: str
) -> str:
    """Classify one fresh process snapshot for exact-PID cleanup.

    ``gone`` and ``reused`` are the only non-running terminal states.  A
    malformed snapshot, duplicate PID record, or exact executable binding is
    deliberately distinct so the shell runner never mistakes an invalid query
    for proof that it may stop cleanup.
    """

    _positive_integer(expected_pid, "expected PID")
    if not executable or "/" in executable or executable in {".", ".."}:
        raise ContractError("expected process identity is invalid")

    result = successful_result(document)
    processes = result.get("runningProcesses")
    if not isinstance(processes, list):
        raise ContractError("runningProcesses must be a JSON array")

    matching_pid: list[PurePosixPath] = []
    for index, value in enumerate(processes):
        process = _require_mapping(value, f"runningProcesses[{index}]")
        pid = _positive_integer(
            process.get("processIdentifier"), f"runningProcesses[{index}].processIdentifier"
        )
        path = file_uri_path(process.get("executable"), f"runningProcesses[{index}].executable")
        if pid == expected_pid:
            matching_pid.append(path)

    if not matching_pid:
        return "gone"
    if len(matching_pid) != 1:
        raise ContractError(
            f"runningProcesses must contain at most one record for cleanup PID {expected_pid}; "
            f"found {len(matching_pid)}"
        )
    return "bound" if matching_pid[0].name == executable else "reused"


def unique_running_executable_pid(document: dict[str, Any], *, executable: str) -> int:
    """Return one unique PID for an exact executable basename, or fail closed."""

    if not executable or "/" in executable or executable in {".", ".."}:
        raise ContractError("expected process identity is invalid")
    result = successful_result(document)
    processes = result.get("runningProcesses")
    if not isinstance(processes, list):
        raise ContractError("runningProcesses must be a JSON array")

    matches: list[int] = []
    for index, value in enumerate(processes):
        process = _require_mapping(value, f"runningProcesses[{index}]")
        pid = _positive_integer(process.get("processIdentifier"),
                                f"runningProcesses[{index}].processIdentifier")
        path = file_uri_path(process.get("executable"), f"runningProcesses[{index}].executable")
        if path.name == executable:
            matches.append(pid)
    if len(matches) != 1:
        raise ContractError(
            f"runningProcesses must contain exactly one {executable!r} executable; found {len(matches)}"
        )
    return matches[0]


def has_newline_complete_readiness(
    log_path: str, *, expected_pid: int, require_resolved_sector: bool = False
) -> bool:
    """Require complete same-PID activate and, when requested, resolved-sector markers."""

    _positive_integer(expected_pid, "expected PID")
    try:
        with open(log_path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise ContractError(f"could not read engine log {log_path!r}: {error}") from error

    # The last record may be concurrently appended.  Only a record terminated by
    # LF is usable evidence; a CR remains part of the record and is rejected.
    activated = False
    sector_resolved = False
    for line in data.split(b"\n")[:-1]:
        lifecycle = LIFECYCLE_RE.fullmatch(line)
        if lifecycle is not None:
            activated = activated or (
                int(lifecycle.group(1)) == expected_pid and lifecycle.group(3) == b"activate"
            )
        sector = SECTOR_STARTUP_RE.fullmatch(line)
        if sector is not None:
            sector_resolved = sector_resolved or (
                int(sector.group("pid")) == expected_pid and sector.group("status") == b"resolved"
            )
    return activated and (sector_resolved or not require_resolved_sector)


def lifecycle_sequence_anchor(log_path: str, *, expected_pid: int) -> int:
    """Return the final complete marker sequence for one PID, rejecting regressions."""

    _positive_integer(expected_pid, "expected PID")
    try:
        with open(log_path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise ContractError(f"could not read engine log {log_path!r}: {error}") from error

    anchor = 0
    for line in data.split(b"\n")[:-1]:
        if not line.startswith(b"* iOS lifecycle v1 "):
            continue
        match = LIFECYCLE_RE.fullmatch(line)
        if match is None:
            raise ContractError("engine log has an invalid versioned lifecycle marker")
        if int(match.group(1)) != expected_pid:
            continue
        sequence = int(match.group(2))
        if sequence <= anchor:
            raise ContractError(
                f"engine log lifecycle sequence {sequence} is not strictly increasing after {anchor}"
            )
        anchor = sequence
    if anchor == 0:
        raise ContractError(f"engine log has no complete lifecycle marker for expected PID {expected_pid}")
    return anchor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    launch = commands.add_parser("launch-pid")
    launch.add_argument("--json", required=True)

    running = commands.add_parser("running-process")
    running.add_argument("--json", required=True)
    running.add_argument("--pid", required=True, type=int)
    running.add_argument("--executable", required=True)

    cleanup_state = commands.add_parser("cleanup-process-state")
    cleanup_state.add_argument("--json", required=True)
    cleanup_state.add_argument("--pid", required=True, type=int)
    cleanup_state.add_argument("--executable", required=True)

    unique = commands.add_parser("unique-running-pid")
    unique.add_argument("--json", required=True)
    unique.add_argument("--executable", required=True)

    ready = commands.add_parser("log-ready")
    ready.add_argument("--log", required=True)
    ready.add_argument("--pid", required=True, type=int)
    ready.add_argument("--require-resolved-sector", action="store_true")

    sequence = commands.add_parser("log-sequence")
    sequence.add_argument("--log", required=True)
    sequence.add_argument("--pid", required=True, type=int)
    return parser


def command_main(arguments: list[str]) -> int:
    args = _parser().parse_args(arguments)
    if args.command == "launch-pid":
        print(launch_pid(load_json(args.json)))
        return 0
    if args.command == "running-process":
        print(verify_running_process(
            load_json(args.json), expected_pid=args.pid, executable=args.executable,
        ))
        return 0
    if args.command == "cleanup-process-state":
        print(cleanup_process_state(
            load_json(args.json), expected_pid=args.pid, executable=args.executable,
        ))
        return 0
    if args.command == "unique-running-pid":
        print(unique_running_executable_pid(load_json(args.json), executable=args.executable))
        return 0
    if args.command == "log-ready":
        return 0 if has_newline_complete_readiness(
            args.log, expected_pid=args.pid,
            require_resolved_sector=args.require_resolved_sector,
        ) else 1
    if args.command == "log-sequence":
        print(lifecycle_sequence_anchor(args.log, expected_pid=args.pid))
        return 0
    raise AssertionError(f"unknown command {args.command!r}")


def main() -> int:
    try:
        return command_main(sys.argv[1:])
    except ContractError as error:
        print(f"FAIL: devicectl contract: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
