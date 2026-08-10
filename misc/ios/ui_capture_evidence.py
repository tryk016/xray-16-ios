#!/usr/bin/env python3
"""Fail-closed native UI-capture evidence for the isolated iOS 27 Simulator.

This is intentionally independent from lighting_ab_evidence.py: it validates
the producer's capture-v2 sidecar only as a binding source for four native UI
frames.  PPM is authoritative; the PNG is a deterministic RGB8 derivation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import time
import zlib
from typing import Any, Callable, Iterable, NamedTuple


SCHEMA = "openxray-ios-ui-captures-v1"
TOKEN_RE = re.compile(r"^([0-9a-f]{32}):([1-9][0-9]*)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SIMULATOR_UDID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
MARKER_RE = re.compile(r"^\* iOS UI state v1 pid=([1-9][0-9]*) seq=([1-9][0-9]*) frame=([0-9]+) state=(world|inventory|pda_tasks|pda_map|other)$")
TARGETS = {1: "inventory", 3: "pda_tasks", 4: "other", 6: "pda_tasks"}
WIDTH, HEIGHT = 1864, 860
STABLE_SECONDS, TIMEOUT_SECONDS = 0.200, 15.0
APPROVED_RUNTIME = "27.0"
APPROVED_RENDERER = "Apple-Software-Renderer"
PROVENANCE_FIELDS = {"simulator_udid", "runtime", "renderer", "git_revision", "source_tree_sha256"}
CAPTURE_FIELDS = {"token", "session", "sequence", "pid", "frame", "continual_ms", "width", "height",
                  "period_ms", "scene", "paused"}
INPUT_FIELDS = {"generation", "state", "request_id", "key", "scancode", "duration_ms", "accepted", "released"}
EVENT_FIELDS = {"frame", "continual_ms", "sdl_ms"}
WORLD_FIELDS = {"level", "epoch", "sector"}
VIEW_FIELDS = {"position", "direction", "fov"}
ENVIRONMENT_FIELDS = {"game_time_ms", "day_time_s", "time_factor", "cycle", "weather", "weather_fx",
                      "descriptor0", "descriptor1", "weight", "ambient", "hemi", "sun", "sun_direction"}
RECORD_FIELDS = {"step", "state", "request_id", "marker", "baseline_token", "token", "capture",
                 "input_timing", "paths", "sizes", "sha256"}
TRANSITION_FIELDS = {"request_id", "key", "state", "pid", "seq", "frame", "release_scancode"}


class UiCaptureError(RuntimeError):
    pass


class RetryableUiCaptureError(UiCaptureError):
    """A live producer packet is complete but demonstrably not target-ready."""


class ManifestPublication(NamedTuple):
    path: Path
    device: int
    inode: int


def fail(message: str) -> None:
    raise UiCaptureError(message)


def retry(message: str) -> None:
    raise RetryableUiCaptureError(message)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_object,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        fail(f"invalid {label} JSON: {error}")
    if type(value) is not dict:
        fail(f"{label} JSON root is not an object")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        fail(f"{label} is not a valid integer")
    return value


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        fail(f"{label} is not a finite number")
    return float(value)


def _vector(value: Any, label: str, count: int) -> None:
    if type(value) is not list or len(value) != count:
        fail(f"{label} is not a {count}-component vector")
    for index, item in enumerate(value):
        _number(item, f"{label}[{index}]")


def validate_provenance(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != PROVENANCE_FIELDS or not all(type(item) is str for item in value.values()):
        fail("UI capture provenance schema is invalid")
    if SIMULATOR_UDID_RE.fullmatch(value["simulator_udid"]) is None:
        fail("UI capture provenance Simulator UDID is invalid")
    if value["runtime"] != APPROVED_RUNTIME or value["renderer"] != APPROVED_RENDERER:
        fail("UI capture provenance runtime/renderer is outside the approved scope")
    if re.fullmatch(r"[0-9a-f]{40}", value["git_revision"]) is None:
        fail("UI capture provenance git revision is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", value["source_tree_sha256"]) is None:
        fail("UI capture provenance source-tree digest is invalid")
    return value


def _token(value: Any, label: str) -> tuple[str, int]:
    if type(value) is not str:
        fail(f"{label} is not a canonical token")
    match = TOKEN_RE.fullmatch(value)
    if match is None:
        fail(f"{label} is not a canonical token")
    return match.group(1), int(match.group(2))


def _stable(path: Path, label: str, *, live: bool = False) -> bytes:
    def unstable(message: str) -> None:
        if live:
            retry(message)
        fail(message)
    try:
        first = path.lstat()
    except FileNotFoundError:
        unstable(f"{label} is missing")
    if stat.S_ISLNK(first.st_mode) or not stat.S_ISREG(first.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        unstable(f"{label} disappeared before it could be opened")
    except OSError as error:
        fail(f"cannot safely open {label}: {error}")
    try:
        opened = os.fstat(fd)
        data = bytearray()
        while chunk := os.read(fd, 1024 * 1024):
            data.extend(chunk)
        closed = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        final = path.lstat()
    except FileNotFoundError:
        unstable(f"{label} disappeared while being read")
    states = (first, opened, closed, final)
    if (len({(item.st_dev, item.st_ino) for item in states}) != 1
            or len({item.st_size for item in states}) != 1
            or len({item.st_mtime_ns for item in states}) != 1
            or stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode)
            or len(data) != closed.st_size):
        unstable(f"{label} changed while being read")
    return bytes(data)


def _new_file(path: Path, data: bytes, label: str) -> tuple[int, int]:
    if os.path.lexists(path):
        fail(f"{label} destination already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    identity: tuple[int, int] | None = None
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as error:
        fail(f"cannot create {label}: {error}")
    try:
        opened = os.fstat(fd)
        identity = (opened.st_dev, opened.st_ino)
        view = memoryview(data)
        while view:
            wrote = os.write(fd, view)
            if wrote <= 0:
                fail(f"short write creating {label}")
            view = view[wrote:]
        os.fsync(fd)
        if os.fstat(fd).st_size != len(data):
            fail(f"short {label} publication")
    except (OSError, UiCaptureError) as error:
        try:
            current = path.lstat()
            if identity is not None and stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
                path.unlink()
        except OSError:
            pass
        if isinstance(error, UiCaptureError):
            raise
        fail(f"cannot complete {label}: {error}")
    finally:
        os.close(fd)
    assert identity is not None
    try:
        published = path.lstat()
    except FileNotFoundError:
        fail(f"{label} disappeared after publication")
    if (stat.S_ISLNK(published.st_mode) or not stat.S_ISREG(published.st_mode)
            or (published.st_dev, published.st_ino) != identity
            or published.st_size != len(data)):
        fail(f"{label} was replaced after publication")
    return identity


def remove_exact_manifest(publication: ManifestPublication) -> bool:
    """Remove only the manifest inode returned by this process's publication."""

    path = publication.path
    try:
        initial = path.lstat()
    except FileNotFoundError:
        return True
    identity = (publication.device, publication.inode)
    if (stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode)
            or (initial.st_dev, initial.st_ino) != identity):
        return False
    rollback_dir: Path | None = None
    for attempt in range(16):
        candidate = path.parent / f".manifest-rollback-{os.getpid()}-{time.time_ns()}-{attempt}"
        try:
            os.mkdir(candidate, 0o700)
            rollback_dir = candidate
            break
        except FileExistsError:
            continue
        except OSError as error:
            fail(f"cannot create private UI capture manifest rollback directory: {error}")
    if rollback_dir is None:
        fail("cannot reserve a private UI capture manifest rollback directory")
    moved = rollback_dir / "manifest.json"
    try:
        os.rename(path, moved)
    except FileNotFoundError:
        rollback_dir.rmdir()
        return True
    except OSError as error:
        rollback_dir.rmdir()
        fail(f"cannot isolate UI capture manifest for rollback: {error}")
    moved_details = moved.lstat()
    if (stat.S_ISREG(moved_details.st_mode)
            and (moved_details.st_dev, moved_details.st_ino) == identity):
        try:
            moved.unlink()
            rollback_dir.rmdir()
        except OSError as error:
            fail(f"cannot roll back UI capture manifest: {error}")
        return True
    # A replacement raced with rollback. Restore that exact object without
    # overwriting any newer name; if the canonical name is occupied, retain
    # the replacement under the private rollback directory rather than delete it.
    try:
        os.link(moved, path, follow_symlinks=False)
    except OSError:
        return False
    try:
        moved.unlink()
        rollback_dir.rmdir()
    except OSError:
        return False
    return False


def _real_dir(path: Path, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        fail(f"{label} must be a real non-symlink directory")


def _parse_ppm(data: bytes) -> tuple[str, bytes]:
    # The generated producer has a deliberately small P6 header.  Parsing it
    # here also rejects mixed sidecar/PPM pairs and appended pixels.
    match = re.match(rb"\AP6\n# openxray-capture-v2 token=([0-9a-f]{32}:[1-9][0-9]*)\n([0-9]+) ([0-9]+)\n255\n", data)
    if match is None:
        fail("PPM is not canonical P6/RGB with one capture token")
    token = match.group(1).decode("ascii")
    _token(token, "PPM token")
    width, height = int(match.group(2)), int(match.group(3))
    if (width, height) != (WIDTH, HEIGHT):
        fail("PPM dimensions are not exactly 1864x860")
    pixels = data[match.end():]
    if len(pixels) != WIDTH * HEIGHT * 3:
        fail("PPM pixels are truncated or have trailing bytes")
    return token, pixels


def _png(token: str, pixels: bytes) -> bytes:
    def chunk(name: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xffffffff)
    raw = b"".join(b"\0" + pixels[index:index + WIDTH * 3] for index in range(0, len(pixels), WIDTH * 3))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"openxray-ui-capture-token\0" + token.encode("ascii"))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def verify_png_bytes(data: bytes, token: str, pixels: bytes) -> None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        fail("PNG signature is invalid")
    index, parts, text, seen_ihdr = 8, [], None, False
    while index < len(data):
        if index + 12 > len(data):
            fail("PNG is truncated")
        length = struct.unpack(">I", data[index:index + 4])[0]
        end = index + 12 + length
        if end > len(data):
            fail("PNG payload is truncated")
        name, payload = data[index + 4:index + 8], data[index + 8:index + 8 + length]
        if struct.unpack(">I", data[end - 4:end])[0] != zlib.crc32(name + payload) & 0xffffffff:
            fail("PNG CRC mismatch")
        if name == b"IHDR":
            if seen_ihdr or payload != struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0):
                fail("PNG is not canonical 1864x860 RGB8")
            seen_ihdr = True
        elif name == b"tEXt":
            if payload != b"openxray-ui-capture-token\0" + token.encode("ascii") or text is not None:
                fail("PNG token binding is invalid")
            text = token
        elif name == b"IDAT":
            parts.append(payload)
        elif name == b"IEND":
            if payload or end != len(data):
                fail("PNG end is invalid")
            break
        else:
            fail("PNG has a non-canonical chunk")
        index = end
    if not seen_ihdr or text != token or not parts:
        fail("PNG lacks required evidence chunks")
    try:
        raw = zlib.decompress(b"".join(parts))
    except zlib.error as error:
        fail(f"PNG cannot be decompressed: {error}")
    expected = b"".join(b"\0" + pixels[index:index + WIDTH * 3] for index in range(0, len(pixels), WIDTH * 3))
    if raw != expected:
        fail("PNG RGB pixels do not exactly equal authoritative PPM")


def _markers(lines: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in lines:
        match = MARKER_RE.fullmatch(line)
        if match:
            result.append({"pid": int(match.group(1)), "seq": int(match.group(2)), "frame": int(match.group(3)), "state": match.group(4)})
    return result


def _event(value: Any, label: str, *, required: bool) -> dict[str, int] | None:
    if value is None:
        if required:
            fail(f"capture input lacks {label} timing")
        return None
    if type(value) is not dict or set(value) != EVENT_FIELDS:
        fail(f"capture input.{label} timing schema is invalid")
    for key in EVENT_FIELDS:
        _integer(value[key], f"capture input.{label}.{key}")
    return value


def _validate_sidecar(value: dict[str, Any], *, expected_pid: int, expected_level: str,
                      allow_loading: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    if re.fullmatch(r"[a-z0-9_]{1,64}", expected_level) is None:
        fail("expected capture level is unsafe")
    if set(value) != {"schema", "capture", "view", "world", "environment", "input"} or value.get("schema") != "openxray.capture.v2":
        fail("capture sidecar schema is invalid")
    capture, view, world, environment, input_value = (
        value.get("capture"), value.get("view"), value.get("world"), value.get("environment"), value.get("input")
    )
    if type(capture) is not dict or set(capture) != CAPTURE_FIELDS:
        fail("capture object fields are not canonical")
    session, sequence = _token(capture["token"], "capture token")
    capture_sequence = _integer(capture["sequence"], "capture.sequence", 1)
    if capture["session"] != session or capture_sequence != sequence:
        fail("capture token/session/sequence binding is invalid")
    if _integer(capture["pid"], "capture.pid", 1) != expected_pid:
        fail("capture PID does not match launched PID")
    for key in ("frame", "continual_ms"):
        _integer(capture[key], f"capture.{key}")
    if (_integer(capture["width"], "capture.width", 1), _integer(capture["height"], "capture.height", 1)) != (WIDTH, HEIGHT):
        fail("capture dimensions are not exactly 1864x860")
    if _integer(capture["period_ms"], "capture.period_ms") != 5000:
        fail("capture period is not exactly 5000 ms")
    allowed_scenes = {"gameplay", "loading"} if allow_loading else {"gameplay"}
    if type(capture["scene"]) is not str or capture["scene"] not in allowed_scenes or type(capture["paused"]) is not bool:
        fail("capture scene/paused fields are invalid")

    if type(view) is not dict or set(view) != VIEW_FIELDS:
        fail("capture view fields are not canonical")
    _vector(view["position"], "capture view.position", 3)
    _vector(view["direction"], "capture view.direction", 3)
    if not 0.0 < _number(view["fov"], "capture view.fov") <= 180.0:
        fail("capture view.fov is out of range")

    if capture["scene"] == "loading":
        if world is not None or environment is not None:
            fail("loading baseline capture carries world/environment state")
    else:
        if type(world) is not dict or set(world) != WORLD_FIELDS:
            fail("gameplay capture world is not canonical and non-null")
        if type(world["level"]) is not str or world["level"] != expected_level:
            fail("gameplay capture level does not match synchronized level")
        _integer(world["epoch"], "capture world.epoch", 1)
        _integer(world["sector"], "capture world.sector")

    if environment is not None:
        if type(environment) is not dict or set(environment) != ENVIRONMENT_FIELDS:
            fail("capture environment fields are not canonical")
        for key in ("game_time_ms",):
            _integer(environment[key], f"capture environment.{key}")
        for key in ("day_time_s", "time_factor", "weight"):
            _number(environment[key], f"capture environment.{key}")
        for key in ("cycle", "weather", "descriptor0", "descriptor1"):
            if type(environment[key]) is not str:
                fail(f"capture environment.{key} is not text")
        if type(environment["weather_fx"]) is not bool:
            fail("capture environment.weather_fx is not Boolean")
        for key, count in (("ambient", 3), ("hemi", 4), ("sun", 3), ("sun_direction", 3)):
            _vector(environment[key], f"capture environment.{key}", count)

    if type(input_value) is not dict or set(input_value) != INPUT_FIELDS:
        fail("capture input fields are not canonical")
    state = input_value["state"]
    if type(state) is not str or state not in {"none", "active", "released", "cancelled"}:
        fail("capture input state is invalid")
    generation = _integer(input_value["generation"], "capture input.generation")
    if state == "none":
        if (generation != 0 or input_value["request_id"] is not None or input_value["key"] is not None
                or input_value["scancode"] is not None or input_value["duration_ms"] != 0
                or input_value["accepted"] is not None or input_value["released"] is not None):
            fail("capture input none state carries stale request data")
        return capture, input_value
    if generation <= 0:
        fail("capture input generation must be positive")
    if type(input_value["request_id"]) is not str or UUID_RE.fullmatch(input_value["request_id"]) is None:
        fail("capture input request UUID is invalid")
    if type(input_value["key"]) is not str or not re.fullmatch(r"[a-z]+", input_value["key"]):
        fail("capture input key is invalid")
    _integer(input_value["scancode"], "capture input.scancode", 1)
    _integer(input_value["duration_ms"], "capture input.duration_ms", 1)
    accepted = _event(input_value["accepted"], "accepted", required=True)
    released = _event(input_value["released"], "released", required=state == "released")
    assert accepted is not None
    if state in {"active", "cancelled"} and released is not None:
        fail(f"capture input {state} state has a release event")
    if released is not None and any(released[key] < accepted[key] for key in EVENT_FIELDS):
        fail("capture input release predates acceptance")
    for event, label in ((accepted, "accepted"), (released, "released")):
        if event is not None and (event["frame"] > capture["frame"] or event["continual_ms"] > capture["continual_ms"]):
            fail(f"capture input.{label} postdates capture")
    return capture, input_value


def _capture(value: dict[str, Any], transition: dict[str, Any], baseline: str,
             prior: str | None, expected_pid: int, expected_level: str,
             marker_lines: list[str] | None) -> dict[str, Any]:
    capture, input_value = _validate_sidecar(value, expected_pid=expected_pid, expected_level=expected_level)
    session, sequence = _token(capture["token"], "capture token")
    baseline_session, baseline_sequence = _token(baseline, "baseline token")
    if session != baseline_session or sequence < baseline_sequence:
        fail("capture token is non-monotonic or from another session")
    if sequence == baseline_sequence:
        retry("capture producer has not advanced beyond the pre-request baseline")
    if prior is not None:
        prior_session, prior_sequence = _token(prior, "prior selected token")
        if session != prior_session or sequence <= prior_sequence:
            fail("capture token does not advance from prior selected token")
    marker = {key: transition[key] for key in ("pid", "seq", "frame", "state")}
    if marker["pid"] != expected_pid or marker["state"] != TARGETS[transition["step"]]:
        fail("transition marker does not match capture target")
    observed_markers = _markers(marker_lines) if marker_lines is not None else []
    if marker_lines is not None and marker not in observed_markers:
        fail("matching post-DoRenderDialogs UI marker is absent from the runtime log")
    if capture["frame"] <= marker["frame"]:
        retry("capture packet is newer than baseline but predates the target marker")
    if input_value["state"] == "cancelled":
        fail("capture input request is cancelled")
    exact_request = (input_value["request_id"] == transition["request_id"]
                     and input_value["key"] == transition["key"]
                     and input_value["scancode"] == transition["release_scancode"]
                     and input_value["duration_ms"] == transition["hold_ms"])
    if input_value["state"] == "active" and exact_request:
        retry("current request capture is active before release")
    if input_value["state"] != "released":
        fail("post-target capture input is not released")
    if not exact_request:
        fail("post-target capture input UUID/key/scancode/duration does not match request")
    accepted, released = input_value["accepted"], input_value["released"]
    assert type(accepted) is dict and type(released) is dict
    if not (accepted["frame"] <= marker["frame"] < released["frame"] <= capture["frame"]):
        fail("accepted/marker/released/capture frame ordering is invalid")
    for later in observed_markers:
        if later["seq"] > marker["seq"] and later["frame"] <= capture["frame"]:
            fail("later UI marker is not safely after capture")
    return {"step": transition["step"], "state": marker["state"], "request_id": transition["request_id"],
            "marker": marker, "baseline_token": baseline, "token": capture["token"],
            "capture": {**{key: capture[key] for key in ("session", "sequence", "pid", "frame", "continual_ms",
                                                            "width", "height", "period_ms", "scene", "paused")},
                        "level": value["world"]["level"], "epoch": value["world"]["epoch"],
                        "sector": value["world"]["sector"]},
            "input_timing": {"scancode": input_value["scancode"], "duration_ms": input_value["duration_ms"], "accepted": accepted, "released": released}}


class UiCaptureObserver:
    def __init__(self, documents: Path, log_path: Path, root: Path, run_uuid: str, expected_pid: int,
                 expected_level: str, provenance: dict[str, str],
                 *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        if UUID_RE.fullmatch(run_uuid) is None or expected_pid <= 0:
            fail("UI capture run UUID or PID is invalid")
        _real_dir(documents, "Simulator Documents")
        _real_dir(root, "UI capture parent")
        self.metadata, self.ppm, self.log_path = documents / "xr_shot_meta.txt", documents / "xr_shot.ppm", log_path
        self.root, self.expected_pid, self.expected_level = root / run_uuid, expected_pid, expected_level
        self.provenance = dict(validate_provenance(provenance))
        if re.fullmatch(r"[a-z0-9_]{1,64}", expected_level) is None:
            fail("expected UI capture level is unsafe")
        self.clock, self.sleep = clock, sleep
        if os.path.lexists(self.root):
            fail("UI capture run destination already exists")
        os.mkdir(self.root, 0o700)
        os.mkdir(self.root / "raw", 0o700)
        os.mkdir(self.root / "png", 0o700)
        self.selected: list[dict[str, Any]] = []

    def _pair(self) -> tuple[bytes, bytes]:
        first_meta = _stable(self.metadata, "live capture metadata", live=True)
        first_ppm = _stable(self.ppm, "live capture PPM", live=True)
        first_meta_after_ppm = _stable(self.metadata, "live capture metadata", live=True)
        self.sleep(STABLE_SECONDS)
        second_meta = _stable(self.metadata, "live capture metadata", live=True)
        second_ppm = _stable(self.ppm, "live capture PPM", live=True)
        second_meta_after_ppm = _stable(self.metadata, "live capture metadata", live=True)
        if (first_meta != first_meta_after_ppm or second_meta != second_meta_after_ppm
                or first_meta != second_meta or first_ppm != second_ppm):
            retry("live capture metadata/PPM pair is unstable")
        return second_meta, second_ppm

    def baseline(self) -> str:
        deadline = self.clock() + TIMEOUT_SECONDS
        while self.clock() <= deadline:
            try:
                metadata, ppm = self._pair()
                value = _load(metadata, "baseline capture")
                token, _ = _parse_ppm(ppm)
                if value.get("capture", {}).get("token") != token:
                    fail("baseline metadata/PPM tokens differ")
                capture, _ = _validate_sidecar(value, expected_pid=self.expected_pid,
                                                expected_level=self.expected_level, allow_loading=True)
                session, sequence = _token(token, "baseline token")
                if capture["session"] != session or capture["sequence"] != sequence:
                    fail("baseline token/session binding is invalid")
                if capture["scene"] == "loading":
                    retry("baseline capture is still a valid loading packet")
                return token
            except RetryableUiCaptureError:
                if self.clock() >= deadline:
                    raise
                self.sleep(0.05)
        fail("timed out waiting for stable baseline capture")

    def observe(self, transition: dict[str, Any], baseline: str) -> dict[str, Any]:
        if transition["step"] not in TARGETS:
            fail("attempted native capture for semantic-only transition")
        deadline = self.clock() + TIMEOUT_SECONDS
        prior = self.selected[-1]["token"] if self.selected else None
        while self.clock() <= deadline:
            try:
                metadata, ppm = self._pair()
                value = _load(metadata, "live capture")
                token, pixels = _parse_ppm(ppm)
                if value.get("capture", {}).get("token") != token:
                    fail("live metadata/PPM tokens differ")
                lines = _stable(self.log_path, "runtime log").decode("utf-8", "strict").splitlines()
                record = _capture(value, transition, baseline, prior, self.expected_pid,
                                  self.expected_level, lines)
                step = f"step-{transition['step']}-{transition['state']}"
                raw_metadata, raw_ppm, png = self.root / "raw" / f"{step}.json", self.root / "raw" / f"{step}.ppm", self.root / "png" / f"{step}.png"
                if any(os.path.lexists(path) for path in (raw_metadata, raw_ppm, png)):
                    fail("native UI capture output destination already exists")
                _new_file(raw_metadata, metadata, "raw metadata")
                _new_file(raw_ppm, ppm, "raw PPM")
                png_bytes = _png(token, pixels)
                verify_png_bytes(png_bytes, token, pixels)
                _new_file(png, png_bytes, "derived PNG")
                record["paths"] = {"metadata": str(raw_metadata), "ppm": str(raw_ppm), "png": str(png)}
                record["sizes"] = {"metadata": len(metadata), "ppm": len(ppm), "png": len(png_bytes)}
                record["sha256"] = {"metadata": hashlib.sha256(metadata).hexdigest(), "ppm": hashlib.sha256(ppm).hexdigest(), "png": hashlib.sha256(png_bytes).hexdigest()}
                self.selected.append(record)
                return record
            except RetryableUiCaptureError:
                if self.clock() >= deadline:
                    raise
                self.sleep(0.05)
        fail("timed out waiting for native UI capture")


def finalize_run_with_identity(root: Path, transitions: list[dict[str, Any]], captures: list[dict[str, Any]],
                               launched_pid: int, expected_level: str,
                               provenance: dict[str, str]) -> ManifestPublication:
    if type(launched_pid) is not int or launched_pid <= 0:
        fail("UI capture launched PID is invalid")
    try:
        canonical_root = root.resolve(strict=True)
    except OSError as error:
        fail(f"UI capture run root cannot be resolved: {error}")
    if (not root.is_absolute() or root.name == "" or UUID_RE.fullmatch(root.name) is None
            or root.parent.name != "ui-captures" or canonical_root != root):
        fail("UI capture run root/UUID is not canonical")
    _real_dir(root.parent, "UI capture parent")
    _real_dir(root, "UI capture run")
    _real_dir(root / "raw", "UI capture raw directory")
    _real_dir(root / "png", "UI capture PNG directory")
    validate_provenance(provenance)
    if (type(transitions) is not list or len(transitions) != 7
            or any(type(item) is not dict or set(item) != TRANSITION_FIELDS for item in transitions)):
        fail("UI capture finalization has malformed transition evidence")
    expected_sequence = (("i", "inventory"), ("i", "world"), ("p", "pda_tasks"), ("e", "other"),
                         ("escape", "world"), ("m", "pda_tasks"), ("escape", "world"))
    for index, (transition, (key, state)) in enumerate(zip(transitions, expected_sequence)):
        if (transition["key"] != key or transition["state"] != state
                or type(transition["request_id"]) is not str
                or UUID_RE.fullmatch(transition["request_id"]) is None
                or type(transition["pid"]) is not int or transition["pid"] != launched_pid
                or type(transition["seq"]) is not int or transition["seq"] <= 0
                or type(transition["frame"]) is not int or transition["frame"] < 0
                or type(transition["release_scancode"]) is not int or transition["release_scancode"] <= 0):
            fail("UI capture finalization transition sequence is invalid")
        if index and (transition["seq"] != transitions[index - 1]["seq"] + 1
                      or transition["frame"] <= transitions[index - 1]["frame"]):
            fail("UI capture finalization transitions are not one ordered same-PID sequence")
    if (type(captures) is not list or len(captures) != 4
            or any(type(item) is not dict or set(item) != RECORD_FIELDS for item in captures)
            or [item["step"] for item in captures] != [1, 3, 4, 6]):
        fail("UI capture finalization has incomplete semantic/capture sequence")
    prior: str | None = None
    transition_by_step = {index: item for index, item in enumerate(transitions, 1)}
    for capture in captures:
        paths, digests = capture.get("paths"), capture.get("sha256")
        sizes = capture.get("sizes")
        if (type(paths) is not dict or set(paths) != {"metadata", "ppm", "png"}
                or type(digests) is not dict or set(digests) != {"metadata", "ppm", "png"}
                or type(sizes) is not dict or set(sizes) != {"metadata", "ppm", "png"}
                or not all(type(paths[key]) is str for key in paths)):
            fail("UI capture artifact paths/hashes are missing")
        if (type(capture["marker"]) is not dict or set(capture["marker"]) != {"pid", "seq", "frame", "state"}
                or type(capture["capture"]) is not dict
                or set(capture["capture"]) != {"session", "sequence", "pid", "frame", "continual_ms", "width", "height",
                                                     "period_ms", "scene", "paused", "level", "epoch", "sector"}
                or type(capture["input_timing"]) is not dict
                or set(capture["input_timing"]) != {"scancode", "duration_ms", "accepted", "released"}):
            fail("saved UI capture nested record schema is invalid")
        metadata, ppm, png = (Path(paths[key]) for key in ("metadata", "ppm", "png"))
        expected_stem = f"step-{capture['step']}-{capture['state']}"
        expected_paths = (root / "raw" / f"{expected_stem}.json", root / "raw" / f"{expected_stem}.ppm",
                          root / "png" / f"{expected_stem}.png")
        if (metadata, ppm, png) != expected_paths:
            fail("UI capture artifact path role escaped its canonical destination")
        raw_metadata, raw_ppm, raw_png = _stable(metadata, "saved raw metadata"), _stable(ppm, "saved raw PPM"), _stable(png, "saved PNG")
        token, pixels = _parse_ppm(raw_ppm)
        value = _load(raw_metadata, "saved raw metadata")
        if value.get("capture", {}).get("token") != token or token != capture.get("token"):
            fail("saved UI capture token changed")
        step = capture.get("step")
        if type(step) is not int or step not in TARGETS or step not in transition_by_step:
            fail("saved UI capture has an invalid transition step")
        transition = dict(transition_by_step[step])
        transition.update({"step": step, "hold_ms": capture.get("input_timing", {}).get("duration_ms")})
        rebound = _capture(value, transition, capture.get("baseline_token"), prior, launched_pid,
                           expected_level, None)
        for key in ("step", "state", "request_id", "marker", "baseline_token", "token", "capture", "input_timing"):
            if rebound[key] != capture.get(key):
                fail("saved UI capture no longer binds its transition/input timing")
        prior = token
        verify_png_bytes(raw_png, token, pixels)
        for key, data in (("metadata", raw_metadata), ("ppm", raw_ppm), ("png", raw_png)):
            if type(sizes[key]) is not int or sizes[key] != len(data):
                fail("saved UI capture artifact size changed")
            if type(digests[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", digests[key]) is None:
                fail("saved UI capture artifact hash is malformed")
            if hashlib.sha256(data).hexdigest() != digests.get(key):
                fail("saved UI capture artifact hash changed")
    document = {"schema": SCHEMA, "run_uuid": root.name, "launched_pid": launched_pid,
                "provenance": provenance, "transitions": transitions, "captures": captures,
                "post_stop_revalidated": True,
                "scope": "iOS-27.0-Simulator-Apple-Software-Renderer-only; native diagnostic readback only; not iPhone/readability/color/performance proof"}
    manifest = root / "manifest.json"
    device, inode = _new_file(
        manifest,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        "UI capture manifest",
    )
    return ManifestPublication(manifest, device, inode)


def finalize_run(root: Path, transitions: list[dict[str, Any]], captures: list[dict[str, Any]],
                 launched_pid: int, expected_level: str, provenance: dict[str, str]) -> Path:
    return finalize_run_with_identity(root, transitions, captures, launched_pid,
                                      expected_level, provenance).path
