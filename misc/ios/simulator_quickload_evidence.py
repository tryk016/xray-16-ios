#!/usr/bin/env python3
"""Fail-closed QuickSave -> QuickLoad evidence for one isolated iOS 27 Simulator.

The module deliberately owns the F5/F9 contract instead of widening
``lighting_ab_evidence``: its post-load capture is required to retain the F9
input identity, while the lighting A/B contract correctly requires ``none``.
It never launches, terminates or deletes a Simulator; the retail runner owns
that lifecycle and calls ``finalize`` only after termination.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

import lighting_ab_evidence as capture
import sector_startup_oracle as sector


SCHEMA = "openxray-ios-simulator-quickload-evidence-v1"
# The release log retains the logical save name.  LocatorAPI lowercases the
# physical filename before writing it, including the path printed by the log.
LOGICAL_QUICKSAVE_STEM = "Player - quicksave"
LOGICAL_QUICKSAVE = f"{LOGICAL_QUICKSAVE_STEM}.scop"
PHYSICAL_QUICKSAVE_STEM = "player - quicksave"
PHYSICAL_QUICKSAVE = f"{PHYSICAL_QUICKSAVE_STEM}.scop"
PHYSICAL_QUICKSAVE_DDS = f"{PHYSICAL_QUICKSAVE_STEM}.dds"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PRESS_RE = re.compile(r"^\* iOS diag: autoinput request ([0-9a-f-]{36}) press/hold '(f5|f9)' \(scancode ([1-9][0-9]*)\) for (100) ms$")
RELEASE_RE = re.compile(r"^\* iOS diag: autoinput request ([0-9a-f-]{36}) released scancode ([1-9][0-9]*)$")
FAILURE = ("fatal", "stack trace", "assertion", "abort trap", "termination reason", "application terminated")
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class EvidenceError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise EvidenceError(message)


def _real_dir(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as error:
        fail(f"{label} is missing: {error}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"{label} is not a real directory")
    return path.absolute()


def _read_regular(path: Path, label: str, *, stable: bool) -> tuple[tuple[int, int], bytes]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            fail(f"{label} is not a regular non-symlink file")
        fd = os.open(path, os.O_RDONLY | O_NOFOLLOW)
    except OSError as error:
        fail(f"cannot open {label}: {error}")
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            fail(f"{label} changed identity while opening")
        data = bytearray()
        while chunk := os.read(fd, 1024 * 1024):
            data.extend(chunk)
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        after = path.lstat()
    except OSError as error:
        fail(f"{label} disappeared while reading: {error}")
    if (after_fd.st_dev, after_fd.st_ino, after_fd.st_size) != (before.st_dev, before.st_ino, before.st_size) or (after.st_dev, after.st_ino, after.st_size) != (before.st_dev, before.st_ino, before.st_size):
        fail(f"{label} changed while being read")
    if stable and len(data) != before.st_size:
        fail(f"{label} short read")
    return (before.st_dev, before.st_ino), bytes(data)


def _read_live_regular(path: Path, label: str) -> tuple[tuple[int, int], bytes]:
    """Read one append-only prefix without rejecting a concurrent append."""

    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            fail(f"{label} is not a regular non-symlink file")
        fd = os.open(path, os.O_RDONLY | O_NOFOLLOW)
    except OSError as error:
        fail(f"cannot open {label}: {error}")
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            fail(f"{label} changed identity while opening")
        remaining = before.st_size
        data = bytearray()
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                fail(f"{label} shrank while being read")
            data.extend(chunk)
            remaining -= len(chunk)
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        after = path.lstat()
    except OSError as error:
        fail(f"{label} disappeared while reading: {error}")
    if ((after_fd.st_dev, after_fd.st_ino) != (before.st_dev, before.st_ino)
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or after_fd.st_size < before.st_size or after.st_size < before.st_size):
        fail(f"{label} was replaced or truncated while being read")
    return (before.st_dev, before.st_ino), bytes(data)


def _write_new(path: Path, data: bytes, label: str) -> tuple[int, int]:
    if os.path.lexists(path):
        fail(f"{label} destination already exists")
    _real_dir(path.parent, f"{label} parent")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600)
    except OSError as error:
        fail(f"cannot create {label}: {error}")
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                fail(f"short write creating {label}")
            view = view[written:]
        os.fsync(fd)
        details = os.fstat(fd)
        if not stat.S_ISREG(details.st_mode) or details.st_size != len(data):
            fail(f"{label} is not exact regular content")
        return details.st_dev, details.st_ino
    finally:
        os.close(fd)


def _stable_pair(metadata: Path, ppm: Path) -> tuple[dict, bytes, bytes]:
    _, meta_a = _read_regular(metadata, "live capture metadata", stable=True)
    _, ppm_a = _read_regular(ppm, "live capture PPM", stable=True)
    _, meta_b = _read_regular(metadata, "live capture metadata", stable=True)
    if meta_a != meta_b:
        fail("capture metadata changed around PPM")
    value = capture.validate_capture_pair_bytes(meta_a, ppm_a, str(metadata), str(ppm))
    return value, meta_a, ppm_a


def _token(value: dict) -> tuple[str, int]:
    return capture.parse_capture_token(value["capture"]["token"], "capture token")


def _capture_contract(value: dict, *, pid: int, epoch: int, sector_id: int,
                      input_id: str | None, key: str | None, scancode: int | None,
                      min_frame: int, prior: tuple[str, int] | None) -> None:
    capture.verify_local_capture_readiness(value)
    details, input_state = value["capture"], value["input"]
    if (details["pid"] != pid or details["width"] != 1864 or details["height"] != 860
            or details["scene"] != "gameplay" or details["paused"] is not False):
        fail("capture PID/dimensions/gameplay contract is invalid")
    world = value["world"]
    if type(world) is not dict or (world["level"], world["epoch"], world["sector"]) != ("zaton", epoch, sector_id):
        fail("capture world epoch/level/sector does not match terminal sector")
    session, sequence = _token(value)
    if prior is not None and (session != prior[0] or sequence <= prior[1]):
        fail("capture token/session does not advance")
    if details["frame"] <= min_frame:
        fail("capture frame is not strictly after required marker")
    if input_id is None:
        if input_state["state"] != "none":
            fail("baseline capture must have no active diagnostic input")
        return
    if (input_state["state"], input_state["request_id"], input_state["key"], input_state["scancode"], input_state["duration_ms"]) != ("released", input_id, key, scancode, 100):
        fail("capture input does not exactly bind its F5/F9 request")
    accepted, released = input_state["accepted"], input_state["released"]
    if type(accepted) is not dict or type(released) is not dict or released["frame"] < accepted["frame"]:
        fail("capture input events are incomplete or inverted")
    if details["frame"] <= released["frame"]:
        fail("capture is not strictly after input release")


def _save_state(path: Path, label: str) -> dict:
    identity, data = _read_regular(path, label, stable=True)
    if not data:
        fail(f"{label} is empty")
    info = path.lstat()
    return {"name": path.name, "device": identity[0], "inode": identity[1],
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _settled_save_state(path: Path, label: str) -> dict:
    """Require two independent nofollow reads with one exact inode/byte state."""

    first = _save_state(path, label)
    second = _save_state(path, label)
    if first != second:
        fail(f"{label} did not stabilize across two reads")
    return first


def _verify_save(path: Path, expected: dict, label: str, *, inode_required: bool = False) -> None:
    actual = _save_state(path, label)
    for key in ("name", "bytes", "sha256"):
        if actual[key] != expected[key]:
            fail(f"{label} changed")
    if inode_required and (actual["device"], actual["inode"]) != (expected["device"], expected["inode"]):
        fail(f"{label} changed inode")


def _assert_absent(path: Path, label: str) -> None:
    if os.path.lexists(path):
        fail(f"{label} must be absent")


def _savedgame_entry_names(saves: Path, label: str) -> list[str]:
    """Return literal directory-entry spellings without pathname resolution."""

    _real_dir(saves, label)
    try:
        with os.scandir(saves) as entries:
            names = [entry.name for entry in entries]
    except OSError as error:
        fail(f"cannot scan {label}: {error}")
    if any(not name or "/" in name or "\\" in name for name in names):
        fail(f"{label} has an invalid directory entry name")
    return names


def _require_no_casefold_collision(names: list[str], label: str) -> None:
    if len(names) != len(set(names)) or len({name.casefold() for name in names}) != len(names):
        fail(f"{label} has a casefold collision")


def _assert_absent_savedgame_entry(saves: Path, name: str, label: str) -> None:
    names = _savedgame_entry_names(saves, "savedgames")
    _require_no_casefold_collision(names, "savedgames")
    if any(entry.casefold() == name.casefold() for entry in names):
        fail(f"{label} must be absent")


def _require_exact_savedgame_entry(path: Path, label: str) -> None:
    names = _savedgame_entry_names(path.parent, "savedgames")
    _require_no_casefold_collision(names, "savedgames")
    exact = names.count(path.name)
    casefold = sum(entry.casefold() == path.name.casefold() for entry in names)
    if exact != 1 or casefold != 1:
        fail(f"{label} does not have exactly one literal physical directory entry")


def _verify_savedgames_entries(names: list[str], expected: set[str]) -> None:
    """Check literal savedgames names from ``os.scandir``, not Path lookup."""

    _require_no_casefold_collision(names, "savedgames")
    if names.count(PHYSICAL_QUICKSAVE) != 1:
        fail("savedgames does not contain exactly one lowercase physical quicksave")
    if sum(name.casefold() == PHYSICAL_QUICKSAVE.casefold() for name in names) != 1:
        fail("savedgames has a QuickSave casefold collision")
    if set(names) != expected | {PHYSICAL_QUICKSAVE}:
        fail("savedgames contains a forbidden added/deleted save or directory")


def _engine_log_path(path: Path) -> str:
    return str(path.absolute()).replace("/", "\\")


def _expected_save_line(physical_quicksave: Path) -> str:
    return f"* Game {LOGICAL_QUICKSAVE} is successfully saved to file '{_engine_log_path(physical_quicksave)}'"


def _expected_load_line(physical_quicksave: Path) -> re.Pattern[str]:
    prefix = (
        f"* Game {LOGICAL_QUICKSAVE_STEM} is successfully loaded from file "
        f"'{_engine_log_path(physical_quicksave)}' "
    )
    return re.compile(rf"^{re.escape(prefix)}\([0-9]+\.[0-9]{{3}}s\)$")


def _rejected_line(label: str, line: str) -> None:
    fail(f"{label}: {json.dumps(line, ensure_ascii=True)}")


def _complete_lines(data: bytes, label: str) -> list[str]:
    if not data.endswith(b"\n"):
        fail(f"{label} is not newline terminated")
    try:
        lines = data.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as error:
        fail(f"{label} is not UTF-8: {error}")
    if any(any(marker in line.lower() for marker in FAILURE) for line in lines):
        fail(f"{label} contains an engine failure marker")
    return lines


def _read_log(path: Path, previous: bytes | None, *, final: bool = False) -> tuple[tuple[int, int], bytes, list[str]]:
    if final:
        identity, data = _read_regular(path, "runtime log", stable=True)
    else:
        identity, data = _read_live_regular(path, "runtime log")
        if not data.endswith(b"\n"):
            newline = data.rfind(b"\n")
            if newline < 0:
                fail("runtime log has no complete line")
            data = data[:newline + 1]
    if previous is not None and not data.startswith(previous):
        fail("runtime log was rewritten, truncated, or rotated")
    return identity, data, _complete_lines(data, "runtime log")


def _require_live(pid: int, phase: str) -> None:
    try:
        os.kill(pid, 0)
    except OSError as error:
        fail(f"expected Simulator PID is not live during {phase}: {error}")


def _marker_epoch(lines: list[str], pid: int) -> tuple[dict, dict]:
    try:
        report = sector.analyze(lines, expected_pid=pid)
    except sector.OracleError as error:
        fail(f"startup-sector evidence is invalid: {error}")
    epochs = report["epochs"]
    if not isinstance(epochs, list) or not epochs:
        fail("startup-sector evidence has no completed epoch")
    terminal = epochs[-1]
    if not isinstance(terminal, dict) or terminal.get("trigger") != "level_load":
        fail("baseline sector epoch is not terminal level_load")
    if terminal.get("classification") not in {"exact", "fallback", "retained"}:
        fail("baseline sector epoch is unresolved or recovered")
    if terminal.get("level") != "zaton" or type(terminal.get("epoch")) is not int:
        fail("baseline sector epoch is not Zaton")
    return report, terminal


def _request_log_events(lines: list[str], request_id: str, key: str, *,
                        watermark_line: int, reject_other_requests: bool,
                        require_complete: bool) -> dict | None:
    """Validate one exact request after its pre-trigger log watermark."""

    if type(request_id) is not str or UUID_RE.fullmatch(request_id) is None:
        fail("autoinput request UUID is malformed")
    if key not in {"f5", "f9"}:
        fail("autoinput request key is malformed")
    if type(watermark_line) is not int or watermark_line < 0 or watermark_line > len(lines):
        fail("autoinput request watermark is malformed")
    expected_scancode = 62 if key == "f5" else 66
    press: list[tuple[re.Match[str], int]] = []
    release: list[tuple[re.Match[str], int]] = []
    for line_index, line in enumerate(lines[watermark_line:], watermark_line):
        if not line.startswith("* iOS diag: autoinput request "):
            continue
        if "press/hold" in line:
            match = PRESS_RE.fullmatch(line)
            if match is None:
                fail("autoinput press payload is malformed")
            if match.group(1) != request_id:
                if reject_other_requests:
                    fail("autoinput press has an unexpected UUID")
                continue
            press.append((match, line_index))
        elif " released scancode " in line:
            match = RELEASE_RE.fullmatch(line)
            if match is None:
                fail("autoinput release payload is malformed")
            if match.group(1) != request_id:
                if reject_other_requests:
                    fail("autoinput release has an unexpected UUID")
                continue
            release.append((match, line_index))
    if len(press) > 1 or len(release) > 1:
        fail("autoinput request has duplicate press or release")
    if len(press) != 1 or len(release) != 1:
        if require_complete:
            fail("autoinput request must have exactly one press and release")
        return None
    press_match, press_line = press[0]
    release_match, release_line = release[0]
    if (press_match.group(2) != key
            or int(press_match.group(3)) != expected_scancode
            or int(release_match.group(2)) != expected_scancode
            or release_line <= press_line):
        fail("autoinput press/release is not the canonical F5/F9 mapping")
    return {
        "request_id": request_id,
        "watermark_line": watermark_line,
        "ack": f"{request_id} accepted",
        "press": press_match.group(0), "press_line": press_line,
        "release": release_match.group(0), "release_line": release_line,
    }


def _revalidate_request_events(lines: list[str], request_id: str, key: str, event: object) -> dict:
    if type(event) is not dict:
        fail("stored autoinput request event is malformed")
    watermark = event.get("watermark_line")
    if (event.get("request_id") != request_id or event.get("ack") != f"{request_id} accepted"
            or type(watermark) is not int):
        fail("stored autoinput request identity is malformed")
    actual = _request_log_events(lines, request_id, key, watermark_line=watermark,
                                 reject_other_requests=False, require_complete=True)
    if actual is None:
        fail("post-stop autoinput request event is incomplete")
    for field in ("request_id", "watermark_line", "ack", "press", "press_line", "release", "release_line"):
        if event.get(field) != actual[field]:
            fail("post-stop autoinput request event changed")
    return actual


def _request(appdata: Path, log: Path, prior: bytes, *, key: str, expected_pid: int,
             timeout: float, poll: float) -> tuple[str, bytes, list[str], dict]:
    request_id = str(uuid.uuid4())
    if UUID_RE.fullmatch(request_id) is None:
        fail("generated autoinput UUID is malformed")
    ack = appdata / "autoinput_ack.txt"
    trigger = appdata / "autoinput.txt"
    if os.path.lexists(ack):
        info = ack.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            fail("previous autoinput ACK is unsafe")
        ack.unlink()
    _assert_absent(trigger, "autoinput trigger")
    watermark_line = len(_complete_lines(prior, "runtime log watermark"))
    _write_new(trigger, f"id {request_id} {key} 100\n".encode("ascii"), "autoinput trigger")
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        _require_live(expected_pid, f"{key} request")
        _, current, lines = _read_log(log, prior)
        if os.path.lexists(ack):
            _, ack_data = _read_regular(ack, "autoinput ACK", stable=True)
            if ack_data != f"{request_id} accepted\n".encode("ascii"):
                fail("autoinput ACK is malformed, stale, or not accepted")
            events = _request_log_events(lines, request_id, key,
                                         watermark_line=watermark_line,
                                         reject_other_requests=True,
                                         require_complete=False)
            if events is not None:
                return request_id, current, lines, events
        time.sleep(poll)
    fail(f"timed out waiting for exact {key} ACK/press/release")


def _wait_quicksave(log: Path, watermark: bytes, current: bytes, quicksave: Path, *,
                    watermark_line: int, expected_pid: int, timeout: float,
                    poll: float) -> tuple[bytes, list[str], dict, int]:
    """Wait for the queued M_SAVE_GAME packet after the F5 request watermark."""

    deadline = time.monotonic() + timeout
    if watermark_line != len(_complete_lines(watermark, "runtime log watermark")):
        fail("F5 request watermark no longer matches its log prefix")
    while time.monotonic() <= deadline:
        _require_live(expected_pid, "QuickSave completion")
        _, current, lines = _read_log(log, current)
        save_index = _quicksave_success_index(
            lines, quicksave, watermark_line=watermark_line, before_line=None,
        )
        if save_index is not None:
            _require_exact_savedgame_entry(quicksave, "quicksave after F5")
            return current, lines, _settled_save_state(quicksave, "quicksave after F5"), save_index
        time.sleep(poll)
    fail("timed out waiting for one post-watermark quicksave success and file")


def _success_window(lines: list[str], *, watermark_line: int, end_line: int | None,
                    label: str) -> tuple[int, int]:
    if type(watermark_line) is not int or watermark_line < 0 or watermark_line > len(lines):
        fail(f"{label} request watermark is outside the runtime log")
    if end_line is None:
        return watermark_line, len(lines)
    if type(end_line) is not int or end_line < watermark_line or end_line > len(lines):
        fail(f"{label} request window endpoint is outside the runtime log")
    return watermark_line, end_line


def _quicksave_success_index(lines: list[str], quicksave: Path, *, watermark_line: int,
                             before_line: int | None) -> int | None:
    start, end = _success_window(lines, watermark_line=watermark_line,
                                 end_line=before_line, label="F5")
    candidates = [
        (index, line) for index, line in enumerate(lines[start:end], start)
        if " is successfully saved to file '" in line
    ]
    if len(candidates) > 1:
        fail("F5 produced duplicate saved-game success lines")
    if not candidates:
        return None
    index, line = candidates[0]
    if line != _expected_save_line(quicksave):
        _rejected_line("F5 produced a non-canonical saved-game success line", line)
    return index


def _quickload_success_index(lines: list[str], quicksave: Path, *, watermark_line: int,
                             terminal_line: int) -> int:
    start, end = _success_window(lines, watermark_line=watermark_line,
                                 end_line=terminal_line, label="F9")
    candidates = [
        (index, line) for index, line in enumerate(lines[start:end], start)
        if " is successfully loaded from file '" in line
    ]
    if len(candidates) != 1:
        fail("F9 must produce exactly one quicksave success line")
    index, line = candidates[0]
    if _expected_load_line(quicksave).fullmatch(line) is None:
        _rejected_line("F9 produced a non-canonical quickload success line", line)
    return index


def _copy_capture(root: Path, stem: str, value: dict, metadata: bytes, ppm: bytes) -> dict:
    meta_path, ppm_path = root / f"{stem}.json", root / f"{stem}.ppm"
    _write_new(meta_path, metadata, f"{stem} metadata")
    _write_new(ppm_path, ppm, f"{stem} PPM")
    return {"token": value["capture"]["token"], "frame": value["capture"]["frame"],
            "metadata": str(meta_path), "ppm": str(ppm_path),
            "metadata_sha256": hashlib.sha256(metadata).hexdigest(), "ppm_sha256": hashlib.sha256(ppm).hexdigest()}


def _wait_capture(metadata: Path, ppm: Path, *, pid: int, epoch: int, sector_id: int,
                  request_id: str | None, key: str | None, scancode: int | None,
                  min_frame: int, prior: tuple[str, int] | None, timeout: float, poll: float) -> tuple[dict, bytes, bytes]:
    deadline = time.monotonic() + timeout
    last_error = "capture absent"
    while time.monotonic() <= deadline:
        try:
            value, meta, pixels = _stable_pair(metadata, ppm)
            _capture_contract(value, pid=pid, epoch=epoch, sector_id=sector_id, input_id=request_id,
                              key=key, scancode=scancode, min_frame=min_frame, prior=prior)
            return value, meta, pixels
        except (EvidenceError, capture.EvidenceError) as error:
            last_error = str(error)
            time.sleep(poll)
    fail(f"timed out waiting for capture: {last_error}")


def _savedgames_contract(documents: Path, staged_manifest: Path, original: Path) -> None:
    expected: dict[str, tuple[int, str]] = {}
    _, manifest_data = _read_regular(staged_manifest, "staged manifest", stable=True)
    try:
        manifest_text = manifest_data.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        fail(f"staged manifest is not UTF-8: {error}")
    if not manifest_text.endswith("\n"):
        fail("staged manifest is not newline terminated")
    rows = manifest_text.splitlines()
    if not rows or rows[0] != "sha256\tbytes\tpath":
        fail("staged manifest has an invalid header")
    seen_paths: set[str] = set()
    seen_casefold: set[str] = set()
    for raw in rows[1:]:
        fields = raw.split("\t")
        if len(fields) != 3:
            fail("staged manifest has an invalid row")
        digest, size, relative = fields
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            fail("staged manifest has a non-canonical SHA-256")
        if re.fullmatch(r"0|[1-9][0-9]*", size) is None:
            fail("staged manifest has a non-canonical byte count")
        parts = relative.split("/")
        if (not relative or relative.startswith("/") or "\\" in relative
                or any(part in {"", ".", ".."} for part in parts)):
            fail("staged manifest has a non-canonical relative path")
        folded = relative.casefold()
        if relative in seen_paths or folded in seen_casefold:
            fail("staged manifest has a duplicate or casefold-colliding path")
        seen_paths.add(relative)
        seen_casefold.add(folded)
        prefix = "_appdata_/savedgames/"
        if relative.startswith(prefix):
            name = relative[len(prefix):]
            if "/" in name or int(size) <= 0:
                fail("staged manifest has an invalid saved-game record")
            expected[name] = (int(size), digest)
    if original.name not in expected:
        fail("original save is absent from the staged manifest")
    reserved = {PHYSICAL_QUICKSAVE.casefold(), PHYSICAL_QUICKSAVE_DDS.casefold()}
    if any(name.casefold() in reserved for name in expected):
        fail("staged manifest already contains a reserved QuickSave artifact")
    saves = documents / "_appdata_" / "savedgames"
    _real_dir(saves, "savedgames")
    names = _savedgame_entry_names(saves, "savedgames")
    _verify_savedgames_entries(names, set(expected))
    for name, (size, digest) in expected.items():
        entry = saves / name
        _, data = _read_regular(entry, f"original staged save {name}", stable=True)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            fail(f"original staged save changed: {name}")
    quicksave = saves / PHYSICAL_QUICKSAVE
    _require_exact_savedgame_entry(quicksave, "quicksave")
    _save_state(quicksave, "quicksave")
    _assert_absent_savedgame_entry(saves, PHYSICAL_QUICKSAVE_DDS, "quicksave DDS")


def run(args: argparse.Namespace) -> None:
    documents, root = _real_dir(Path(args.documents), "Simulator Documents"), _real_dir(Path(args.root), "QuickLoad evidence root")
    if any(root.iterdir()):
        fail("QuickLoad evidence root must be empty")
    appdata, saves = documents / "_appdata_", documents / "_appdata_" / "savedgames"
    _real_dir(appdata, "Simulator app-data")
    _real_dir(saves, "savedgames")
    original = Path(args.original_save).absolute()
    if original.parent != saves.absolute():
        fail("original save escapes Simulator savedgames")
    original_state = _save_state(original, "original save")
    quicksave = saves / PHYSICAL_QUICKSAVE
    _assert_absent_savedgame_entry(saves, PHYSICAL_QUICKSAVE, "quicksave before F5")
    _assert_absent_savedgame_entry(saves, PHYSICAL_QUICKSAVE_DDS, "quicksave DDS before F5")
    log = Path(args.log).absolute()
    _require_live(args.expected_pid, "baseline")
    identity, watermark, lines = _read_log(log, None)
    _, baseline_epoch = _marker_epoch(lines, args.expected_pid)
    baseline_log_lines = len(lines)
    e0, sector_id = baseline_epoch["epoch"], baseline_epoch.get("sector")
    if type(sector_id) is not int:
        # Parse the canonical marker directly; its final fragment is authoritative.
        parsed = [sector.parse_marker(line, number) for number, line in enumerate(lines, 1) if line.startswith(sector.CANDIDATE_STEM)]
        sector_id = parsed[-1].sector
    if type(sector_id) is not int or sector_id == sector.INVALID_SECTOR:
        fail("baseline terminal sector is invalid")
    metadata, ppm = documents / "xr_shot_meta.txt", documents / "xr_shot.ppm"
    b0_value, b0_meta, b0_ppm = _wait_capture(metadata, ppm, pid=args.expected_pid, epoch=e0, sector_id=sector_id,
                                                request_id=None, key=None, scancode=None, min_frame=-1, prior=None,
                                                timeout=args.timeout, poll=args.poll)
    b0 = _copy_capture(root, "baseline-b0", b0_value, b0_meta, b0_ppm)
    qsave, after_f5, lines, f5_events = _request(appdata, log, watermark, key="f5", expected_pid=args.expected_pid,
                                                   timeout=args.timeout, poll=args.poll)
    # The key press only queues M_SAVE_GAME. The actual file write and success
    # marker complete asynchronously on a later engine iteration.
    after_f5, lines, quick_state, save_line = _wait_quicksave(
        log, watermark, after_f5, quicksave, watermark_line=f5_events["watermark_line"],
        expected_pid=args.expected_pid, timeout=args.timeout, poll=args.poll,
    )
    f5_events["success_line"] = save_line
    _, quick_bytes = _read_regular(quicksave, "quicksave after F5", stable=True)
    quick_copy = root / PHYSICAL_QUICKSAVE
    _write_new(quick_copy, quick_bytes, "private quicksave evidence copy")
    _verify_save(quick_copy, quick_state, "private quicksave evidence copy")
    quick_copy_state = _settled_save_state(quick_copy, "private quicksave evidence copy")
    b1_value, b1_meta, b1_ppm = _wait_capture(metadata, ppm, pid=args.expected_pid, epoch=e0, sector_id=sector_id,
                                                request_id=qsave, key="f5", scancode=62,
                                                min_frame=b0_value["capture"]["frame"] + 1, prior=_token(b0_value),
                                                timeout=args.timeout, poll=args.poll)
    # "Settled" binds the stable save to a strictly post-release input
    # identity; it does not claim multi-frame pixel stabilization.
    _assert_absent_savedgame_entry(saves, PHYSICAL_QUICKSAVE_DDS, "quicksave DDS after F5")
    b1 = _copy_capture(root, "settled-b1", b1_value, b1_meta, b1_ppm)
    qload, after_f9, final_lines, f9_events = _request(appdata, log, after_f5, key="f9", expected_pid=args.expected_pid,
                                                         timeout=args.timeout, poll=args.poll)
    if qload == qsave:
        fail("QuickLoad UUID must differ from QuickSave UUID")
    deadline = time.monotonic() + args.timeout
    terminal: dict | None = None
    current = after_f9
    while time.monotonic() <= deadline:
        _require_live(args.expected_pid, "QuickLoad epoch")
        _, current, current_lines = _read_log(log, current)
        try:
            oracle = sector.validate_expected_suffix(sector.analyze(current_lines, args.expected_pid), e0, ["quick_load"])
            candidate = oracle["epochs"][-1]
            if isinstance(candidate, dict) and candidate.get("classification") in {"exact", "fallback", "retained"}:
                terminal = candidate
                final_lines = current_lines
                break
            fail("QuickLoad epoch is unresolved or recovered")
        except sector.OracleError:
            time.sleep(args.poll)
    if terminal is None:
        fail("timed out waiting for one resolved anchored quick_load epoch")
    e1 = terminal["epoch"]
    parsed = [
        (index, sector.parse_marker(line, index + 1))
        for index, line in enumerate(final_lines) if line.startswith(sector.CANDIDATE_STEM)
    ]
    terminal_line, terminal_marker = [item for item in parsed if item[1].epoch == e1][-1]
    save_line = _quicksave_success_index(
        final_lines, quicksave, watermark_line=f5_events["watermark_line"],
        before_line=f9_events["watermark_line"],
    )
    if save_line != f5_events["success_line"]:
        fail("F5 save-success index changed before the F9 request")
    load_line = _quickload_success_index(
        final_lines, quicksave, watermark_line=f9_events["watermark_line"], terminal_line=terminal_line,
    )
    f9_events["success_line"] = load_line
    f9_events["terminal_line"] = terminal_line
    _require_exact_savedgame_entry(quicksave, "quicksave after F9")
    _verify_save(quicksave, quick_state, "quicksave after F9", inode_required=True)
    _assert_absent_savedgame_entry(saves, PHYSICAL_QUICKSAVE_DDS, "quicksave DDS after F9")
    c_value, c_meta, c_ppm = _wait_capture(metadata, ppm, pid=args.expected_pid, epoch=e1, sector_id=terminal_marker.sector,
                                             request_id=qload, key="f9", scancode=66,
                                             min_frame=max(b1_value["capture"]["frame"], terminal_marker.frame),
                                             prior=_token(b1_value), timeout=args.timeout, poll=args.poll)
    c = _copy_capture(root, "post-quickload-c", c_value, c_meta, c_ppm)
    _savedgames_contract(documents, Path(args.staged_manifest), original)
    _require_exact_savedgame_entry(quicksave, "quicksave before stop")
    _verify_save(quicksave, quick_state, "quicksave before stop", inode_required=True)
    _assert_absent_savedgame_entry(saves, PHYSICAL_QUICKSAVE_DDS, "quicksave DDS before stop")
    pre = {"schema": SCHEMA, "phase": "pre-stop", "pid": args.expected_pid,
           "log_device": identity[0], "log_inode": identity[1], "log_sha256": hashlib.sha256(current).hexdigest(),
           "baseline_log_lines": baseline_log_lines,
           "baseline_epoch": baseline_epoch, "quickload_epoch": terminal,
           "qsave": qsave, "qload": qload, "f5_events": f5_events, "f9_events": f9_events,
           "original_save": original_state, "quicksave": quick_state,
           "quicksave_copy": quick_copy_state,
           "captures": {"b0": b0, "b1": b1, "c": c},
           "dds_status": "expected_absent_gl_sm_for_gamesave_noop"}
    _write_new(root / "log-pre-stop.txt", current, "pre-stop runtime log")
    pre["log_snapshot"] = str(root / "log-pre-stop.txt")
    _write_new(root / "pre-stop.json", (json.dumps(pre, sort_keys=True, separators=(",", ":")) + "\n").encode(), "pre-stop QuickLoad evidence")


def finalize(args: argparse.Namespace) -> None:
    documents, root = _real_dir(Path(args.documents), "Simulator Documents"), _real_dir(Path(args.root), "QuickLoad evidence root")
    _, raw = _read_regular(root / "pre-stop.json", "pre-stop QuickLoad evidence", stable=True)
    try:
        pre = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"pre-stop QuickLoad evidence is malformed: {error}")
    if pre.get("schema") != SCHEMA or pre.get("phase") != "pre-stop" or pre.get("pid") != args.expected_pid:
        fail("pre-stop QuickLoad evidence identity is invalid")
    identity, final, lines = _read_log(Path(args.log).absolute(), None, final=True)
    if (identity[0], identity[1]) != (pre.get("log_device"), pre.get("log_inode")):
        fail("post-stop runtime log changed inode")
    # Require the complete pre-stop payload, rather than merely a matching digest.
    pre_digest = pre.get("log_sha256")
    snapshot_path = Path(pre.get("log_snapshot", ""))
    _, pre_log = _read_regular(snapshot_path, "pre-stop runtime log", stable=True)
    if type(pre_digest) is not str or hashlib.sha256(pre_log).hexdigest() != pre_digest or not final.startswith(pre_log):
        fail("pre-stop log binding is malformed")
    try:
        oracle = sector.validate_expected_suffix(sector.analyze(lines, args.expected_pid), pre["baseline_epoch"]["epoch"], ["quick_load"])
    except (sector.OracleError, KeyError, TypeError) as error:
        fail(f"post-stop QuickLoad sector suffix is invalid: {error}")
    terminal = oracle["epochs"][-1]
    if terminal != pre.get("quickload_epoch"):
        fail("post-stop QuickLoad terminal epoch changed")
    original = Path(args.original_save).absolute()
    _verify_save(original, pre["original_save"], "original save after stop")
    _savedgames_contract(documents, Path(args.staged_manifest), original)
    quicksave = documents / "_appdata_" / "savedgames" / PHYSICAL_QUICKSAVE
    try:
        baseline_lines = pre["baseline_log_lines"]
        f5_events, f9_events = pre["f5_events"], pre["f9_events"]
        indexed = (
            baseline_lines, f5_events["watermark_line"], f5_events["press_line"], f5_events["success_line"],
            f9_events["watermark_line"], f9_events["press_line"], f9_events["success_line"], f9_events["terminal_line"],
        )
    except (KeyError, TypeError):
        fail("pre-stop log event indices are missing")
    if any(type(value) is not int or value < 0 for value in indexed):
        fail("pre-stop log event indices are malformed")
    _revalidate_request_events(lines, pre["qsave"], "f5", f5_events)
    _revalidate_request_events(lines, pre["qload"], "f9", f9_events)
    if f5_events["watermark_line"] != baseline_lines:
        fail("post-stop F5 watermark changed from the baseline log boundary")
    save_index = _quicksave_success_index(
        lines, quicksave, watermark_line=f5_events["watermark_line"],
        before_line=f9_events["watermark_line"],
    )
    if save_index != f5_events["success_line"]:
        fail("post-stop F5 save-success line/order changed")
    load_index = _quickload_success_index(
        lines, quicksave, watermark_line=f9_events["watermark_line"],
        terminal_line=f9_events["terminal_line"],
    )
    if load_index != f9_events["success_line"]:
        fail("post-stop F9 quickload-success index changed")
    terminal_lines = [
        index for index, line in enumerate(lines)
        if line.startswith(sector.CANDIDATE_STEM)
        and sector.parse_marker(line, index + 1).epoch == terminal["epoch"]
    ]
    if not terminal_lines or terminal_lines[-1] != f9_events["terminal_line"]:
        fail("post-stop quick_load terminal marker index changed")
    terminal_marker = sector.parse_marker(
        lines[terminal_lines[-1]], terminal_lines[-1] + 1,
    )
    _require_exact_savedgame_entry(quicksave, "quicksave after stop")
    _verify_save(quicksave, pre["quicksave"], "quicksave after stop", inode_required=True)
    quick_copy = root / PHYSICAL_QUICKSAVE
    try:
        quick_copy_state = pre["quicksave_copy"]
    except (KeyError, TypeError):
        fail("private quicksave evidence binding is missing")
    _verify_save(quick_copy, quick_copy_state, "private quicksave evidence copy after stop",
                 inode_required=True)
    if any(quick_copy_state.get(key) != pre["quicksave"].get(key)
           for key in ("name", "bytes", "sha256")):
        fail("private quicksave evidence copy is not bound to the live QuickSave")
    _assert_absent_savedgame_entry(documents / "_appdata_" / "savedgames", PHYSICAL_QUICKSAVE_DDS,
                                   "quicksave DDS after stop")
    captures = pre.get("captures")
    if not isinstance(captures, dict) or set(captures) != {"b0", "b1", "c"}:
        fail("post-stop evidence must contain exactly b0, b1 and c captures")
    expected_paths = {
        "b0": (root / "baseline-b0.json", root / "baseline-b0.ppm"),
        "b1": (root / "settled-b1.json", root / "settled-b1.ppm"),
        "c": (root / "post-quickload-c.json", root / "post-quickload-c.ppm"),
    }
    validated_captures: dict[str, dict] = {}
    # Revalidate the captured immutable copies rather than live producer files.
    for name in ("b0", "b1", "c"):
        record = captures[name]
        if not isinstance(record, dict):
            fail(f"saved {name} capture record is malformed")
        if not isinstance(record.get("metadata"), str) or not isinstance(record.get("ppm"), str):
            fail(f"saved {name} capture paths are malformed")
        meta, ppm = map(Path, (record["metadata"], record["ppm"]))
        if (meta.absolute(), ppm.absolute()) != tuple(path.absolute() for path in expected_paths[name]):
            fail(f"saved {name} capture path changed")
        _, meta_bytes = _read_regular(meta, f"saved {name} metadata", stable=True)
        _, ppm_bytes = _read_regular(ppm, f"saved {name} PPM", stable=True)
        if hashlib.sha256(meta_bytes).hexdigest() != record["metadata_sha256"] or hashlib.sha256(ppm_bytes).hexdigest() != record["ppm_sha256"]:
            fail(f"saved {name} capture changed")
        value = capture.validate_capture_pair_bytes(meta_bytes, ppm_bytes, str(meta), str(ppm))
        if value["capture"]["token"] != record["token"] or value["capture"]["frame"] != record["frame"]:
            fail(f"saved {name} capture identity changed")
        validated_captures[name] = value
    baseline_epoch_id = pre["baseline_epoch"]["epoch"]
    baseline_markers = [
        sector.parse_marker(line, index + 1)
        for index, line in enumerate(lines) if line.startswith(sector.CANDIDATE_STEM)
        and sector.parse_marker(line, index + 1).epoch == baseline_epoch_id
    ]
    if len(baseline_markers) != 1:
        fail("post-stop baseline sector marker count changed")
    baseline_marker = baseline_markers[0]
    _capture_contract(
        validated_captures["b0"], pid=args.expected_pid, epoch=baseline_epoch_id,
        sector_id=baseline_marker.sector, input_id=None, key=None, scancode=None,
        min_frame=baseline_marker.frame, prior=None,
    )
    _capture_contract(
        validated_captures["b1"], pid=args.expected_pid, epoch=baseline_epoch_id,
        sector_id=baseline_marker.sector, input_id=pre["qsave"], key="f5", scancode=62,
        min_frame=validated_captures["b0"]["capture"]["frame"],
        prior=_token(validated_captures["b0"]),
    )
    _capture_contract(
        validated_captures["c"], pid=args.expected_pid, epoch=terminal_marker.epoch,
        sector_id=terminal_marker.sector, input_id=pre["qload"], key="f9", scancode=66,
        min_frame=max(validated_captures["b1"]["capture"]["frame"], terminal_marker.frame),
        prior=_token(validated_captures["b1"]),
    )
    # report.txt is the sole authoritative PASS artifact and is hard-linked
    # last by publish().  This manifest deliberately cannot claim PASS alone.
    manifest = {"schema": SCHEMA, "artifact": "post-stop-revalidated-evidence",
                "post_stop_revalidated": True,
                "pid": args.expected_pid, "baseline_epoch": pre["baseline_epoch"],
                "quickload_epoch": terminal, "qsave": pre["qsave"], "qload": pre["qload"],
                "f5_events": pre["f5_events"], "f9_events": pre["f9_events"],
                "original_save": pre["original_save"], "quicksave": pre["quicksave"],
                "quicksave_copy": quick_copy_state,
                "captures": pre["captures"], "dds_status": pre["dds_status"],
                "scope": "iOS-27.0-Simulator-Apple-Software-Renderer-only; normal F5/F9 path; not iPhone/pixel-quality/performance/other-content proof"}
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _write_new(root / "manifest.pending.json", payload, "pending QuickLoad manifest")
    digest = hashlib.sha256(payload).hexdigest()
    _write_new(root / "report-fields.txt", f"quickload_manifest_sha256={digest}\nquickload_manifest_bytes={len(payload)}\n".encode("ascii"), "QuickLoad report fields")


def publish(args: argparse.Namespace) -> None:
    pending, manifest, report_pending, report = map(Path, (args.pending_manifest, args.manifest, args.report_pending, args.report))
    identity, payload = _read_regular(pending, "pending QuickLoad manifest", stable=True)
    digest = hashlib.sha256(payload).hexdigest()
    report_identity, report_payload = _read_regular(report_pending, "pending retail report", stable=True)
    if report_payload.count(f"quickload_manifest_sha256={digest}\n".encode("ascii")) != 1:
        fail("pending retail report is not bound to the pending QuickLoad manifest")
    if os.path.lexists(manifest) or os.path.lexists(report):
        fail("QuickLoad final publication destination already exists")
    try:
        os.link(pending, manifest, follow_symlinks=False)
    except OSError as error:
        fail(f"could not publish QuickLoad manifest: {error}")
    try:
        published_identity, published = _read_regular(manifest, "published QuickLoad manifest", stable=True)
        if published_identity != identity or published != payload:
            fail("published QuickLoad manifest identity/content changed")
        # The report contains the only result=PASS claim.  Linking it last is
        # the publication commit point: an interruption before this line can
        # leave evidence, but never a standalone PASS artifact.
        os.link(report_pending, report, follow_symlinks=False)
        published_report_identity, published_report = _read_regular(report, "published retail report", stable=True)
        if published_report_identity != report_identity or published_report != report_payload:
            fail("published retail report changed")
        _, revalidated = _read_regular(manifest, "revalidated QuickLoad manifest", stable=True)
        if revalidated != payload:
            fail("QuickLoad manifest changed while publishing report")
        for path, expected_identity, label in (
            (pending, identity, "pending QuickLoad manifest"),
            (report_pending, report_identity, "pending retail report"),
        ):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != expected_identity:
                fail(f"{label} changed before unlink")
            path.unlink()
        if os.path.lexists(pending) or os.path.lexists(report_pending):
            fail("pending publication links survived successful publication")
        _, revalidated = _read_regular(manifest, "final QuickLoad manifest", stable=True)
        _, revalidated_report = _read_regular(report, "final retail report", stable=True)
        if revalidated != payload or revalidated_report != report_payload:
            fail("final QuickLoad publication changed after pending-link cleanup")
    except Exception:
        try:
            info = report.lstat()
            if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == report_identity:
                report.unlink()
        except OSError:
            pass
        try:
            info = manifest.lstat()
            if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                manifest.unlink()
        except OSError:
            pass
        raise


def _preserve_run_failure(args: argparse.Namespace) -> None:
    """Best-effort forensic snapshot before the runner deletes its Simulator."""

    try:
        root = _real_dir(Path(args.root), "QuickLoad evidence root")
        destination = root / "failure-log.txt"
        if os.path.lexists(destination):
            return
        _, data = _read_live_regular(Path(args.log).absolute(), "failed runtime log")
        _write_new(destination, data, "failed runtime log snapshot")
    except (EvidenceError, OSError, UnicodeError, ValueError):
        pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("run", "finalize"):
        item = commands.add_parser(name)
        item.add_argument("--documents", required=True)
        item.add_argument("--log", required=True)
        item.add_argument("--expected-pid", type=int, required=True)
        item.add_argument("--staged-manifest", required=True)
        item.add_argument("--root", required=True)
        item.add_argument("--original-save", required=True)
        if name == "run":
            item.add_argument("--timeout", type=float, required=True)
            item.add_argument("--poll", type=float, required=True)
    item = commands.add_parser("publish")
    item.add_argument("--pending-manifest", required=True)
    item.add_argument("--manifest", required=True)
    item.add_argument("--report-pending", required=True)
    item.add_argument("--report", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "run":
            if args.expected_pid <= 0 or args.timeout <= 0 or not 0.01 <= args.poll <= 5:
                fail("run timeout, poll, or expected PID is outside safe bounds")
            run(args)
        elif args.command == "finalize":
            if args.expected_pid <= 0:
                fail("expected PID is outside safe bounds")
            finalize(args)
        else:
            publish(args)
    except (EvidenceError, capture.EvidenceError, OSError, UnicodeError, ValueError) as error:
        if args.command == "run":
            _preserve_run_failure(args)
        print(f"quickload evidence: FAIL: {error}", file=sys.stderr)
        return 1
    print("quickload evidence: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
