"""Process-local, non-authoritative raw event sink for Phase 0B telemetry.

Selected test processes receive only ``XRAY_FEEDBACK_RAW_EVENT_FD``.  It names
an append-only mode-0600 regular file and is deliberately *not* a capability:
the parent owns the entrypoint and later maps each accepted raw ID to that
fixed origin.  This module never reads a telemetry context, directory, nonce,
run ID or profile, and it never imports the trusted telemetry runtime.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
import unittest
from pathlib import Path
from typing import Any


_RAW_MARKER = "XRAY_FEEDBACK_RAW_EVENT_FD"
_CONTEXT_MARKER = "XRAY_FEEDBACK_CONTEXT_FD"
_PREFIX = "OPENXRAY_TEST_FEEDBACK_"
_ROOT = Path(__file__).resolve().parents[2]
_RAW_SCHEMA = "openxray.test-feedback.raw.v1"
_MAX_RAW_BYTES = 1 << 20
_MAX_RAW_EVENTS = 4096
_raw_fd: int | None = None
_raw_count = 0
_installed: object | None = None


def _scrub_untrusted_markers() -> str:
    """Remove every secret-shaped marker without ever dereferencing it."""
    marker = os.environ.pop(_RAW_MARKER, "")
    os.environ.pop(_CONTEXT_MARKER, None)
    for key in tuple(os.environ):
        if key.startswith(_PREFIX):
            os.environ.pop(key, None)
    return marker


def _capture_raw_sink() -> int | None:
    """Accept one bounded owner regular sink; hostile descriptors are ignored."""
    marker = _scrub_untrusted_markers()
    try:
        descriptor = int(marker)
        if descriptor < 3 or str(descriptor) != marker:
            return None
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_size > _MAX_RAW_BYTES):
            return None
        return descriptor
    except (OSError, ValueError):
        return None


def _stable_id(case: unittest.TestCase) -> str | None:
    module = sys.modules.get(case.__class__.__module__)
    file_name = getattr(module, "__file__", None) or sys.argv[0]
    try:
        relative = Path(file_name).resolve().relative_to(_ROOT).as_posix()
    except (OSError, ValueError):
        return None
    method = case._testMethodName
    if relative == "misc/ios/test_archive_completed_artifacts.py":
        function = getattr(case, method, None)
        semantic = getattr(function, "__doc__", None)
        if isinstance(semantic, str) and semantic and "\n" not in semantic:
            return f"py:{relative}::ArchivePolicyTests::{semantic}"
    return f"py:{relative}::{case.__class__.__name__}.{method}"


def _length(result: object, name: str) -> int:
    try:
        return len(getattr(result, name, ()))
    except TypeError:
        return 0


def _outcome(result: object, before: tuple[int, int, int, int, int]) -> tuple[str, str | None]:
    names = ("failures", "errors", "skipped", "expectedFailures", "unexpectedSuccesses")
    delta = tuple(max(0, _length(result, name) - old) for name, old in zip(names, before))
    if delta[0]:
        return "FAIL", None
    if delta[1] or delta[4]:
        return "ERROR", "unexpected-success" if delta[4] else None
    if delta[2]:
        skipped = getattr(result, "skipped", ())
        return "SKIP", str(skipped[-1][1]) if skipped else "unittest skip"
    if delta[3]:
        return "PASS", "expected-failure"
    return "PASS", None


def emit_raw(identifier: str, result: str, started_ns: int,
             ended_ns: int | None = None, detail: str | None = None) -> bool:
    """Append one canonical, bounded raw record without affecting the test."""
    global _raw_count
    descriptor = _raw_fd
    try:
        if (descriptor is None or _raw_count >= _MAX_RAW_EVENTS
                or not isinstance(identifier, str) or not identifier or len(identifier) > 1024
                or result not in {"PASS", "FAIL", "ERROR", "SKIP", "TIMEOUT", "PLATFORM_SKIP"}
                or not isinstance(started_ns, int) or started_ns < 0):
            return False
        ended = time.monotonic_ns() if ended_ns is None else ended_ns
        if not isinstance(ended, int) or ended < started_ns:
            return False
        if detail is not None and (not isinstance(detail, str) or len(detail) > 2048):
            return False
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o600):
            return False
        record = {"detail": detail, "ended_monotonic_ns": ended,
                  "result": result, "schema": _RAW_SCHEMA,
                  "started_monotonic_ns": started_ns, "test_id": identifier}
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        if len(payload) > 8192 or details.st_size + len(payload) > _MAX_RAW_BYTES:
            return False
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                return False
            offset += written
        _raw_count += 1
        return True
    except BaseException:
        return False


def install_from_environment(_entrypoint: str, _argv: list[str] | None = None) -> object | None:
    """Install a raw unittest observer; malformed input is observationally off."""
    global _installed, _raw_fd
    if _installed is not None:
        return _installed
    try:
        _raw_fd = _capture_raw_sink()
        if _raw_fd is None:
            return None
        original_run = unittest.TestCase.run

        def observed_run(case: unittest.TestCase, result: unittest.TestResult | None = None):
            identifier = _stable_id(case)
            started = time.monotonic_ns()
            baseline = result if result is not None else unittest.TestResult()
            before = tuple(_length(baseline, name) for name in
                           ("failures", "errors", "skipped", "expectedFailures", "unexpectedSuccesses"))
            returned = None
            thrown: BaseException | None = None
            try:
                returned = original_run(case, result)
                return returned
            except BaseException as error:
                thrown = error
                raise
            finally:
                if identifier is not None:
                    observed = baseline if result is not None else returned
                    outcome, detail = (("ERROR", type(thrown).__name__)
                                       if thrown is not None or observed is None
                                       else _outcome(observed, before))
                    emit_raw(identifier, outcome, started, detail=detail)

        unittest.TestCase.run = observed_run
        _installed = object()
        return _installed
    except BaseException:
        return None


def emit_case(_authentication: object | None, identifier: str, result: str,
              started_ns: int) -> None:
    """Compatibility shim for checkers; the ignored argument carries no authority."""
    emit_raw(identifier, result, started_ns)


def _main() -> int:
    """Tiny ``python3 -S`` shell-oracle adapter; never returns authority."""
    if len(sys.argv) not in {5, 6} or sys.argv[1] != "--raw-emit":
        return 2
    try:
        global _raw_fd
        _raw_fd = _capture_raw_sink()
        if _raw_fd is None:
            return 0
        started = int(sys.argv[4])
        detail = sys.argv[5] if len(sys.argv) == 6 else None
        emit_raw(sys.argv[2], sys.argv[3], started, detail=detail)
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
