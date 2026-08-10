#!/usr/bin/env python3
"""Strict host-only validation for OpenXRay iOS capture-v2 evidence.

This tool deliberately proves capture identity and experimental controls, not
pixels or a lighting cause.  Its A/B success token is consumed by the future
phone batch and must remain its only stdout on success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Any, Iterable


SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TOKEN_RE = re.compile(r"^([0-9a-f]{32}):([1-9][0-9]*)$")
SCENES = {"gameplay", "loading", "menu"}
INPUT_STATES = {"none", "active", "released", "cancelled"}
U32_MAX = (1 << 32) - 1
U64_MAX = (1 << 64) - 1
# Capture-v2 serializes original float values with max_digits10. These bounds
# cover several float operations plus extrapolation from A/B, while remaining
# far below a meaningful environment-zone discontinuity.
FINAL_LIGHT_ABS_TOLERANCE = 2e-6
FINAL_LIGHT_REL_TOLERANCE = 2e-6
FINAL_LIGHT_WEIGHT_EPSILON = 1e-6
FINAL_LIGHT_MIN_WEIGHT_RATIO = 0.5
FINAL_LIGHT_MAX_WEIGHT_RATIO = 2.0
FINAL_LIGHT_GATES = {"ambient": 3, "hemi": 3}
FINAL_LIGHT_OBSERVATION_REASONS = {
    "hemi_alpha": "position modifiers update hemi RGB only; alpha is not a position-modifier control",
    "sun": "dynamic sun can multiply color nonlinearly and the capture does not identify the active sun branch",
    "sun_direction": "dynamic solar motion and normalized static interpolation are nonlinear",
}


class EvidenceError(ValueError):
    pass


def fail(message: str) -> None:
    raise EvidenceError(message)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    fail(f"non-finite JSON number: {value}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        fail(f"cannot read metadata {path}: {error}")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError) as error:
        fail(f"invalid metadata JSON {path}: {error}")
    if type(value) is not dict:
        fail("metadata root is not an object")
    return value


def exact_fields(value: dict[str, Any], names: Iterable[str], context: str) -> None:
    expected = set(names)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        fail(f"{context} fields mismatch: missing={missing} unknown={unknown}")


def is_int(value: Any) -> bool:
    return type(value) is int


def require_int(value: Any, context: str, minimum: int | None = None, maximum: int | None = None) -> int:
    if not is_int(value):
        fail(f"{context} is not an integer")
    if minimum is not None and value < minimum:
        fail(f"{context} is below {minimum}")
    if maximum is not None and value > maximum:
        fail(f"{context} exceeds {maximum}")
    return value


def require_float(value: Any, context: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        fail(f"{context} is not a number")
    result = float(value)
    if not math.isfinite(result):
        fail(f"{context} is not finite")
    if minimum is not None and result < minimum:
        fail(f"{context} is below {minimum}")
    if maximum is not None and result > maximum:
        fail(f"{context} exceeds {maximum}")
    return result


def require_text(value: Any, context: str, maximum_bytes: int | None = None) -> str:
    if type(value) is not str or not value or any(ord(character) < 0x20 for character in value):
        fail(f"{context} is not nonempty safe text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        fail(f"{context} is not valid Unicode: {error}")
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        fail(f"{context} exceeds {maximum_bytes} UTF-8 bytes")
    return value


def require_bool(value: Any, context: str) -> bool:
    if type(value) is not bool:
        fail(f"{context} is not boolean")
    return value


def require_vector(value: Any, context: str, count: int) -> list[float]:
    if type(value) is not list or len(value) != count:
        fail(f"{context} is not a {count}-vector")
    return [require_float(item, f"{context}[{index}]") for index, item in enumerate(value)]


def validate_event(value: Any, context: str) -> dict[str, int] | None:
    if value is None:
        return None
    if type(value) is not dict:
        fail(f"{context} is neither null nor object")
    exact_fields(value, ("frame", "continual_ms", "sdl_ms"), context)
    return {
        "frame": require_int(value["frame"], f"{context}.frame", 0, U32_MAX),
        "continual_ms": require_int(value["continual_ms"], f"{context}.continual_ms", 0, U32_MAX),
        "sdl_ms": require_int(value["sdl_ms"], f"{context}.sdl_ms", 0, U32_MAX),
    }


def parse_capture_token(value: Any, context: str) -> tuple[str, int]:
    if type(value) is not str:
        fail(f"{context} is not text")
    match = TOKEN_RE.fullmatch(value)
    if not match:
        fail(f"{context} is not canonical session:sequence")
    sequence = int(match.group(2))
    if sequence > U64_MAX:
        fail(f"{context} sequence exceeds uint64")
    return match.group(1), sequence


def validate_capture(value: dict[str, Any]) -> dict[str, Any]:
    exact_fields(value, ("schema", "capture", "view", "world", "environment", "input"), "root")
    if value["schema"] != "openxray.capture.v2":
        fail("unsupported capture schema")

    capture = value["capture"]
    if type(capture) is not dict:
        fail("capture is not an object")
    exact_fields(capture, ("token", "session", "sequence", "pid", "frame", "continual_ms", "width", "height",
        "period_ms", "scene", "paused"), "capture")
    session = capture["session"]
    if type(session) is not str or not SESSION_RE.fullmatch(session):
        fail("capture.session is not canonical lowercase hex")
    sequence = require_int(capture["sequence"], "capture.sequence", 1, U64_MAX)
    token_session, token_sequence = parse_capture_token(capture["token"], "capture.token")
    if capture["token"] != f"{session}:{sequence}" or token_session != session or token_sequence != sequence:
        fail("capture.token does not equal canonical session:sequence")
    require_int(capture["pid"], "capture.pid", 1, U32_MAX)
    require_int(capture["frame"], "capture.frame", 0, U32_MAX)
    require_int(capture["continual_ms"], "capture.continual_ms", 0, U32_MAX)
    require_int(capture["width"], "capture.width", 1, 16384)
    require_int(capture["height"], "capture.height", 1, 16384)
    if require_int(capture["period_ms"], "capture.period_ms", 0, U32_MAX) != 5000:
        fail("capture.period_ms must be 5000")
    if type(capture["scene"]) is not str or capture["scene"] not in SCENES:
        fail("capture.scene is invalid")
    require_bool(capture["paused"], "capture.paused")

    view = value["view"]
    if type(view) is not dict:
        fail("view is not an object")
    exact_fields(view, ("position", "direction", "fov"), "view")
    require_vector(view["position"], "view.position", 3)
    require_vector(view["direction"], "view.direction", 3)
    require_float(view["fov"], "view.fov", 0.000001, 180.0)

    world = value["world"]
    if world is not None:
        if type(world) is not dict:
            fail("world is neither null nor object")
        exact_fields(world, ("level", "epoch", "sector"), "world")
        require_text(world["level"], "world.level", 127)
        require_int(world["epoch"], "world.epoch", 1, U64_MAX)
        require_int(world["sector"], "world.sector", 0, U64_MAX)

    environment = value["environment"]
    if environment is not None:
        if type(environment) is not dict:
            fail("environment is neither null nor object")
        exact_fields(environment, ("game_time_ms", "day_time_s", "time_factor", "cycle", "weather", "weather_fx",
            "descriptor0", "descriptor1", "weight", "ambient", "hemi", "sun", "sun_direction"), "environment")
        require_int(environment["game_time_ms"], "environment.game_time_ms", 0, U64_MAX)
        require_float(environment["day_time_s"], "environment.day_time_s", 0.0, 86400.0)
        require_float(environment["time_factor"], "environment.time_factor", 0.000001, 10000.0)
        require_text(environment["cycle"], "environment.cycle", 127)
        require_text(environment["weather"], "environment.weather", 127)
        require_bool(environment["weather_fx"], "environment.weather_fx")
        require_text(environment["descriptor0"], "environment.descriptor0", 127)
        require_text(environment["descriptor1"], "environment.descriptor1", 127)
        require_float(environment["weight"], "environment.weight", 0.0, 1.0)
        require_vector(environment["ambient"], "environment.ambient", 3)
        require_vector(environment["hemi"], "environment.hemi", 4)
        require_vector(environment["sun"], "environment.sun", 3)
        require_vector(environment["sun_direction"], "environment.sun_direction", 3)

    input_value = value["input"]
    if type(input_value) is not dict:
        fail("input is not an object")
    exact_fields(input_value, ("generation", "state", "request_id", "key", "scancode", "duration_ms", "accepted",
        "released"), "input")
    generation = require_int(input_value["generation"], "input.generation", 0, U64_MAX)
    state = input_value["state"]
    if type(state) is not str or state not in INPUT_STATES:
        fail("input.state is invalid")
    accepted = validate_event(input_value["accepted"], "input.accepted")
    released = validate_event(input_value["released"], "input.released")
    duration_ms = require_int(input_value["duration_ms"], "input.duration_ms", 0, 30000)
    if state == "none":
        if generation != 0 or input_value["request_id"] is not None or input_value["key"] is not None \
                or input_value["scancode"] is not None or duration_ms != 0 \
                or accepted is not None or released is not None:
            fail("input none contains stale request data")
    else:
        if generation == 0 or type(input_value["request_id"]) is not str \
                or not UUID_RE.fullmatch(input_value["request_id"]):
            fail("input request_id is not canonical lowercase UUID")
        require_text(input_value["key"], "input.key", 31)
        require_int(input_value["scancode"], "input.scancode", -1, 512)
        if state == "active" and released is not None:
            fail("active input has a release event")
        if state == "released":
            if accepted is None or released is None:
                fail("released input needs accepted and released events")
            if any(released[field] < accepted[field] for field in ("frame", "continual_ms", "sdl_ms")):
                fail("released event predates acceptance")
        if state == "cancelled" and released is not None:
            fail("cancelled input has a release event")
    for label, event in (("accepted", accepted), ("released", released)):
        if event is not None and (event["frame"] > capture["frame"]
                or event["continual_ms"] > capture["continual_ms"]):
            fail(f"input.{label} postdates the capture")
    if capture["scene"] in {"menu", "loading"} and (world is not None or environment is not None):
        fail("menu/loading capture cannot carry world or environment")
    if capture["scene"] == "gameplay" and world is None:
        fail("gameplay capture must carry world state")
    if environment is not None and world is None:
        fail("environment cannot be present without world")
    return value


def parse_ppm(path: Path) -> tuple[int, int, str, bytes]:
    try:
        data = path.read_bytes()
    except OSError as error:
        fail(f"cannot read PPM {path}: {error}")
    index = 0
    comments: list[bytes] = []

    def token() -> bytes:
        nonlocal index
        while index < len(data):
            if data[index:index + 1] in b" \t\r\n":
                index += 1
                continue
            if data[index:index + 1] == b"#":
                line_end = data.find(b"\n", index)
                if line_end < 0:
                    fail("unterminated PPM comment")
                comments.append(data[index + 1:line_end])
                index = line_end + 1
                continue
            break
        start = index
        while index < len(data) and data[index:index + 1] not in b" \t\r\n#":
            index += 1
        if start == index:
            fail("truncated PPM header")
        return data[start:index]

    magic = token()
    width_token = token()
    height_token = token()
    max_token = token()
    if index >= len(data) or data[index:index + 1] != b"\n":
        fail("PPM maxval must be followed by exactly one newline")
    index += 1
    if magic != b"P6" or max_token != b"255":
        fail("PPM must be P6/RGB/255")
    try:
        width, height = int(width_token), int(height_token)
    except ValueError:
        fail("PPM dimensions are not integers")
    if not (1 <= width <= 16384 and 1 <= height <= 16384):
        fail("PPM dimensions are out of range")
    expected = width * height * 3
    if len(data) != index + expected:
        fail("PPM is truncated or has trailing bytes")
    prefix = b" openxray-capture-v2 token="
    try:
        tokens = [comment[len(prefix):].decode("ascii", "strict") for comment in comments if comment.startswith(prefix)]
    except UnicodeDecodeError as error:
        fail(f"PPM capture token is not ASCII: {error}")
    if len(tokens) != 1:
        fail("PPM has no unique canonical capture-v2 token")
    parse_capture_token(tokens[0], "PPM capture token")
    return width, height, tokens[0], data[index:]


def png_chunk(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xffffffff)


def write_bytes_exclusive(destination: Path, payload: bytes) -> None:
    """Create one complete local evidence file without following or replacing a path."""
    descriptor: int | None = None
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o600)
        created = True
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail(f"short write creating {destination}")
            offset += written
        os.close(descriptor)
        descriptor = None
    except (OSError, EvidenceError) as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        if isinstance(error, EvidenceError):
            raise
        fail(f"cannot exclusively create {destination}: {error}")


def verify_png(path: Path, expected_token: str | None = None, expected_dimensions: tuple[int, int] | None = None) -> tuple[int, int, str]:
    try:
        data = path.read_bytes()
    except OSError as error:
        fail(f"cannot read PNG {path}: {error}")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        fail("PNG signature is invalid")
    index = 8
    seen_ihdr = False
    seen_iend = False
    seen_idat = False
    idat_parts: list[bytes] = []
    token: str | None = None
    width = height = 0
    while index < len(data):
        if index + 12 > len(data):
            fail("truncated PNG chunk")
        size = struct.unpack(">I", data[index:index + 4])[0]
        end = index + 12 + size
        if end > len(data):
            fail("truncated PNG payload")
        chunk_type = data[index + 4:index + 8]
        payload = data[index + 8:index + 8 + size]
        crc = struct.unpack(">I", data[index + 8 + size:end])[0]
        if zlib.crc32(chunk_type + payload) & 0xffffffff != crc:
            fail("PNG CRC mismatch")
        if chunk_type == b"IHDR":
            if seen_ihdr or len(payload) != 13:
                fail("PNG must start with IHDR")
            width, height, bit_depth, colour_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", payload)
            if (width < 1 or height < 1 or width > 16384 or height > 16384
                    or bit_depth != 8 or colour_type != 2 or compression != 0
                    or filter_method != 0 or interlace != 0):
                fail("PNG format is not canonical RGB8")
            seen_ihdr = True
        elif not seen_ihdr:
            fail("PNG must start with IHDR")
        elif chunk_type == b"IDAT":
            if token is None:
                fail("PNG token tEXt must precede IDAT")
            idat_parts.append(payload)
            seen_idat = True
        elif chunk_type == b"tEXt":
            if seen_idat:
                fail("PNG token tEXt must precede IDAT")
            key, separator, value = payload.partition(b"\0")
            if key != b"openxray-capture-v2" or not separator or token is not None:
                fail("PNG requires one canonical capture token text chunk")
            try:
                token = value.decode("ascii", "strict")
            except UnicodeDecodeError as error:
                fail(f"PNG capture token is not ASCII: {error}")
        elif chunk_type == b"IEND":
            if payload or seen_iend:
                fail("PNG IEND is malformed")
            seen_iend = True
            if end != len(data):
                fail("PNG has trailing bytes")
            break
        else:
            fail("PNG contains a non-canonical chunk")
        index = end
    if not seen_iend or token is None or not idat_parts:
        fail("PNG lacks canonical final token/IEND")
    parse_capture_token(token, "PNG capture token")
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(b"".join(idat_parts)) + decompressor.flush()
    except zlib.error as error:
        fail(f"PNG IDAT does not decompress: {error}")
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        fail("PNG IDAT has trailing or incomplete compressed data")
    row_size = width * 3 + 1
    if len(raw) != row_size * height:
        fail("PNG decompressed RGB scanline length is invalid")
    if any(raw[row * row_size] != 0 for row in range(height)):
        fail("PNG scanline filter is not the canonical zero filter")
    if expected_token is not None and token != expected_token:
        fail("PNG token does not match metadata")
    if expected_dimensions is not None and (width, height) != expected_dimensions:
        fail("PNG dimensions do not match metadata")
    return width, height, token


def validate_capture_pair(metadata: Path, ppm: Path) -> dict[str, Any]:
    value = validate_capture(load_json(metadata))
    width, height, token, _ = parse_ppm(ppm)
    capture = value["capture"]
    if token != capture["token"] or (width, height) != (capture["width"], capture["height"]):
        fail("PPM token or dimensions do not match metadata")
    return value


def command_validate_capture(arguments: argparse.Namespace) -> None:
    value = validate_capture_pair(arguments.metadata, arguments.ppm)
    if arguments.png:
        capture = value["capture"]
        verify_png(arguments.png, capture["token"], (capture["width"], capture["height"]))


def command_metadata_token(arguments: argparse.Namespace) -> None:
    print(validate_capture(load_json(arguments.metadata))["capture"]["token"])


def command_token_order(arguments: argparse.Namespace) -> None:
    expected_session, expected_sequence = parse_capture_token(arguments.expected, "expected token")
    actual_session, actual_sequence = parse_capture_token(arguments.actual, "actual token")
    if expected_session != actual_session:
        print("session-mismatch")
    elif actual_sequence < expected_sequence:
        print("before")
    elif actual_sequence > expected_sequence:
        print("after")
    else:
        print("equal")


def command_add_sequence(arguments: argparse.Namespace) -> None:
    session, current_sequence = parse_capture_token(arguments.token, "token")
    if arguments.increment < 0:
        fail("sequence increment cannot be negative")
    sequence = current_sequence + arguments.increment
    if sequence > U64_MAX:
        fail("capture sequence overflow")
    print(f"{session}:{sequence}")


def command_convert_ppm(arguments: argparse.Namespace) -> None:
    width, height, token, pixels = parse_ppm(arguments.ppm)
    output = arguments.output
    if arguments.no_clobber and os.path.lexists(output):
        fail(f"output already exists: {output}")
    raw = b"".join(b"\0" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    png = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"tEXt", b"openxray-capture-v2\0" + token.encode("ascii"))
        + png_chunk(b"IDAT", zlib.compress(raw, 6)) + png_chunk(b"IEND", b""))
    if arguments.no_clobber:
        write_bytes_exclusive(output, png)
    else:
        try:
            if output.write_bytes(png) != len(png):
                fail(f"short write creating PNG {output}")
        except OSError as error:
            fail(f"cannot write PNG {output}: {error}")
    try:
        verify_png(output, token, (width, height))
    except EvidenceError:
        if arguments.no_clobber:
            output.unlink(missing_ok=True)
        raise


def command_copy_file_exclusive(arguments: argparse.Namespace) -> None:
    """Copy a locally frozen artifact without a check-then-clobber race.

    The source lives in shot.sh's private mktemp directory, but the evidence
    destination is user-visible and must never overwrite a pre-existing file
    (including a dangling symlink). O_EXCL is the publication decision.
    """
    try:
        payload = arguments.source.read_bytes()
    except OSError as error:
        fail(f"cannot read {arguments.source}: {error}")
    write_bytes_exclusive(arguments.destination, payload)


def distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def direction_degrees(first: list[float], second: list[float]) -> float:
    first_length = math.sqrt(sum(value * value for value in first))
    second_length = math.sqrt(sum(value * value for value in second))
    if first_length == 0.0 or second_length == 0.0:
        fail("camera direction has zero length")
    cosine = max(-1.0, min(1.0, sum(a * b for a, b in zip(first, second)) / (first_length * second_length)))
    return math.degrees(math.acos(cosine))


def require_strictly_increasing(values: list[int], label: str) -> None:
    if any(next_value <= current for current, next_value in zip(values, values[1:])):
        fail(f"{label} is not strictly increasing")


def subtract_vectors(first: list[float], second: list[float]) -> list[float]:
    return [left - right for left, right in zip(first, second)]


def final_lighting_report(environments: list[dict[str, Any]], weights: list[float]) -> dict[str, Any]:
    """Gate only position-modified RGB inputs; retain other values as observations.

    CEnvDescriptorMixer applies position modifiers to ambient RGB and hemi RGB.
    Stationary A/B evolution predicts those C components. Hemi alpha, sun and
    sun direction are retained for audit but cannot reject the packet because
    their runtime mechanisms are not position-modifier linearity controls.
    """
    weight_ab = weights[1] - weights[0]
    weight_bc = weights[2] - weights[1]
    if weight_ab <= FINAL_LIGHT_WEIGHT_EPSILON:
        if weight_bc > FINAL_LIGHT_WEIGHT_EPSILON:
            fail("stationary A/B weight delta is too small to predict moving C")
        mode = "constant_weight"
        ratio = 0.0
    elif weight_bc <= FINAL_LIGHT_WEIGHT_EPSILON:
        mode = "constant_at_c"
        ratio = 0.0
    else:
        ratio = weight_bc / weight_ab
        if not FINAL_LIGHT_MIN_WEIGHT_RATIO <= ratio <= FINAL_LIGHT_MAX_WEIGHT_RATIO:
            fail(f"environment weight arms are not comparable: ratio {ratio}")
        mode = "linear_weight"

    report: dict[str, Any] = {
        "absolute_tolerance": FINAL_LIGHT_ABS_TOLERANCE,
        "relative_tolerance": FINAL_LIGHT_REL_TOLERANCE,
        "weight_epsilon": FINAL_LIGHT_WEIGHT_EPSILON,
        "comparable_weight_ratio": {"minimum": FINAL_LIGHT_MIN_WEIGHT_RATIO,
            "maximum": FINAL_LIGHT_MAX_WEIGHT_RATIO},
        "policy": {
            "evidence_boundary": "correlation controls only; no lighting or streaming causal claim",
            "gating": {"ambient_rgb": "linear residual against stationary A/B",
                "hemi_rgb": "linear residual against stationary A/B"},
            "non_gating": {key: {"policy": "observation_only", "reason": reason}
                for key, reason in FINAL_LIGHT_OBSERVATION_REASONS.items()},
        },
        "weight": {"actual": {"A": weights[0], "B": weights[1], "C": weights[2]},
            "delta": {"A_B": weight_ab, "B_C": weight_bc}, "mode": mode, "prediction_ratio": ratio},
        "gated_vectors": {},
        "non_gating_observations": {},
    }
    for key, component_count in FINAL_LIGHT_GATES.items():
        values = [environment[key][:component_count] for environment in environments]
        delta_ab = subtract_vectors(values[1], values[0])
        delta_bc = subtract_vectors(values[2], values[1])
        if mode == "constant_weight":
            # No usable independent-variable movement: both B and C must agree
            # with A within numeric tolerance.
            expected_delta_bc = [0.0 for _ in values[0]]
            predicted_c = list(values[0])
            residual = subtract_vectors(values[2], predicted_c)
            stationary_residual = subtract_vectors(values[1], values[0])
        else:
            expected_delta_bc = [component * ratio for component in delta_ab]
            predicted_c = [component + expected for component, expected in zip(values[1], expected_delta_bc)]
            residual = subtract_vectors(values[2], predicted_c)
            stationary_residual = [0.0 for _ in values[0]]
        tolerances = []
        for index, component_residual in enumerate(residual):
            scale = max(1.0, abs(values[0][index]), abs(values[1][index]), abs(values[2][index]),
                abs(predicted_c[index]))
            tolerance = FINAL_LIGHT_ABS_TOLERANCE \
                + FINAL_LIGHT_REL_TOLERANCE * scale * (1.0 + abs(ratio))
            tolerances.append(tolerance)
            if abs(stationary_residual[index]) > tolerance:
                fail(f"final environment {key}[{index}] changed at constant A/B weight")
            if abs(component_residual) > tolerance:
                fail(f"final environment {key}[{index}] C residual {component_residual} exceeds {tolerance}")
        report["gated_vectors"][key] = {
            "policy": "linear_residual_gate",
            "components": ["r", "g", "b"],
            "actual": {"A": values[0], "B": values[1], "C": values[2]},
            "actual_delta": {"A_B": delta_ab, "B_C": delta_bc},
            "expected_delta_B_C": expected_delta_bc,
            "predicted_C": predicted_c,
            "residual_C": residual,
            "tolerance_C": tolerances,
        }

    hemi_alpha = [environment["hemi"][3] for environment in environments]
    report["non_gating_observations"]["hemi_alpha"] = {
        "policy": "observation_only",
        "reason": FINAL_LIGHT_OBSERVATION_REASONS["hemi_alpha"],
        "actual": {"A": hemi_alpha[0], "B": hemi_alpha[1], "C": hemi_alpha[2]},
        "actual_delta": {"A_B": hemi_alpha[1] - hemi_alpha[0], "B_C": hemi_alpha[2] - hemi_alpha[1]},
    }
    for key in ("sun", "sun_direction"):
        values = [environment[key] for environment in environments]
        report["non_gating_observations"][key] = {
            "policy": "observation_only",
            "reason": FINAL_LIGHT_OBSERVATION_REASONS[key],
            "actual": {"A": values[0], "B": values[1], "C": values[2]},
            "actual_delta": {"A_B": subtract_vectors(values[1], values[0]),
                "B_C": subtract_vectors(values[2], values[1])},
        }
    return report


def image_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_ab(arguments: argparse.Namespace) -> dict[str, Any]:
    captures = [validate_capture(load_json(path)) for path in (arguments.a, arguments.b, arguments.c)]
    names = ("A", "B", "C")
    for name, capture in zip(names, captures):
        info = capture["capture"]
        if info["scene"] != "gameplay" or info["paused"] or info["width"] != 1864 or info["height"] != 860:
            fail(f"{name} is not an unpaused 1864x860 gameplay capture")
        if capture["world"] is None or capture["environment"] is None:
            fail(f"{name} lacks ready world/environment state")

    capture_info = [value["capture"] for value in captures]
    worlds = [value["world"] for value in captures]
    environments = [value["environment"] for value in captures]
    views = [value["view"] for value in captures]
    if len({info["session"] for info in capture_info}) != 1 or len({info["pid"] for info in capture_info}) != 1:
        fail("A/B/C do not share session and PID")
    if len({world["level"] for world in worlds}) != 1 or len({world["epoch"] for world in worlds}) != 1:
        fail("A/B/C do not share level and epoch")
    sequences = [info["sequence"] for info in capture_info]
    if sequences[1] - sequences[0] != 3 or sequences[2] - sequences[1] != 3:
        fail("A/B/C sequences are not +3/+3")
    continual = [info["continual_ms"] for info in capture_info]
    arms = [continual[1] - continual[0], continual[2] - continual[1]]
    if any(abs(value - 15000) > 1000 for value in arms) or abs(arms[0] - arms[1]) > 750:
        fail("A/B arm durations are outside controlled timing bounds")
    stationary_distance = distance(views[0]["position"], views[1]["position"])
    forward_distance = distance(views[1]["position"], views[2]["position"])
    if stationary_distance > 0.20 or not 1.0 <= forward_distance <= 100.0:
        fail("A/B displacement control failed")
    angles = {
        "A_B": direction_degrees(views[0]["direction"], views[1]["direction"]),
        "A_C": direction_degrees(views[0]["direction"], views[2]["direction"]),
        "B_C": direction_degrees(views[1]["direction"], views[2]["direction"]),
    }
    if any(value > 2.0 for value in angles.values()) or len({view["fov"] for view in views}) != 1:
        fail("camera direction/FOV control failed")
    if worlds[0]["sector"] != worlds[1]["sector"]:
        fail("stationary arm changed sector")
    require_strictly_increasing([info["frame"] for info in capture_info], "frame")
    require_strictly_increasing(sequences, "sequence")
    require_strictly_increasing(continual, "continual_ms")
    game_times = [environment["game_time_ms"] for environment in environments]
    require_strictly_increasing(game_times, "environment.game_time_ms")
    if len({environment["cycle"] for environment in environments}) != 1 \
            or len({environment["weather"] for environment in environments}) != 1 \
            or len({(environment["descriptor0"], environment["descriptor1"]) for environment in environments}) != 1 \
            or any(environment["weather_fx"] for environment in environments):
        fail("weather/cycle/descriptor control failed")
    factors = [environment["time_factor"] for environment in environments]
    if max(factors) - min(factors) > 1e-6:
        fail("environment time factor changed")
    weights = [environment["weight"] for environment in environments]
    if any(next_value < current for current, next_value in zip(weights, weights[1:])):
        fail("environment blend weight is not monotonic")
    game_deltas = [game_times[1] - game_times[0], game_times[2] - game_times[1]]
    if abs(game_deltas[0] - game_deltas[1]) > max(1000, int(max(game_deltas) * 0.15)):
        fail("environment time increments are not comparable")

    final_lighting = final_lighting_report(environments, weights)

    input_a = captures[0]["input"]
    input_b = captures[1]["input"]
    # Capture validation already proves every event in A predates (or coincides
    # with) A. Exact snapshot equality then proves that no diagnostic command
    # was accepted between stationary A and B.
    if input_a != input_b:
        fail("stationary A/B input snapshot changed")
    if input_a["state"] == "active":
        fail("stationary A/B retained active diagnostic input")
    if input_a["request_id"] == arguments.request_id:
        fail("stationary A/B already contain the forward request UUID")
    input_c = captures[2]["input"]
    if input_c["state"] != "released" or input_c["request_id"] != arguments.request_id \
            or input_c["key"] != "w" or input_c["scancode"] != 26 or input_c["duration_ms"] != 12000:
        fail("C does not contain the exact completed W request")
    if input_c["generation"] != input_b["generation"] + 1:
        fail("C input generation is not exactly one newer request than B")
    accepted = input_c["accepted"]
    released = input_c["released"]
    assert accepted is not None and released is not None
    if accepted["frame"] <= capture_info[1]["frame"] or accepted["continual_ms"] <= continual[1]:
        fail("input acceptance was not after B")
    if released["frame"] <= accepted["frame"] or released["continual_ms"] <= accepted["continual_ms"] \
            or released["frame"] >= capture_info[2]["frame"] or released["continual_ms"] >= continual[2]:
        fail("input release was not strictly between acceptance and C")
    hold_ms = released["sdl_ms"] - accepted["sdl_ms"]
    if not 12000 <= hold_ms <= 14000:
        fail("measured SDL hold is not 12000..14000 ms")

    report: dict[str, Any] = {
        "schema": "openxray.lighting-ab.v1",
        "session": capture_info[0]["session"],
        "pid": capture_info[0]["pid"],
        "level": worlds[0]["level"],
        "epoch": worlds[0]["epoch"],
        "sequences": sequences,
        "continual_arm_ms": arms,
        "game_time_arm_ms": game_deltas,
        "stationary_distance": stationary_distance,
        "forward_distance": forward_distance,
        "direction_degrees": angles,
        "sectors": [world["sector"] for world in worlds],
        "environment": {"cycle": environments[0]["cycle"], "weather": environments[0]["weather"],
            "descriptor0": environments[0]["descriptor0"], "descriptor1": environments[0]["descriptor1"],
            "weights": weights, "time_factor": factors[0]},
        "final_lighting": final_lighting,
        "input": {"request_id": arguments.request_id, "sdl_hold_ms": hold_ms},
    }
    images = (arguments.a_image, arguments.b_image, arguments.c_image)
    if any(images):
        if not all(images):
            fail("all three image paths are required when any image is supplied")
        report["image_sha256"] = {}
        for name, image, info in zip(names, images, capture_info):
            verify_png(image, info["token"], (info["width"], info["height"]))
            report["image_sha256"][name] = image_sha256(image)
    return report


def command_verify_ab(arguments: argparse.Namespace) -> None:
    report = verify_ab(arguments)
    if arguments.report:
        report_bytes = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        write_bytes_exclusive(arguments.report, report_bytes)
    print("READY_FOR_DEVICE_VISUAL_COMPARISON")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-capture")
    validate.add_argument("--metadata", type=Path, required=True)
    validate.add_argument("--ppm", type=Path, required=True)
    validate.add_argument("--png", type=Path)
    validate.set_defaults(function=command_validate_capture)
    token = commands.add_parser("metadata-token")
    token.add_argument("--metadata", type=Path, required=True)
    token.set_defaults(function=command_metadata_token)
    order = commands.add_parser("token-order")
    order.add_argument("--expected", required=True)
    order.add_argument("--actual", required=True)
    order.set_defaults(function=command_token_order)
    add_sequence = commands.add_parser("add-sequence")
    add_sequence.add_argument("--token", required=True)
    add_sequence.add_argument("--increment", type=int, required=True)
    add_sequence.set_defaults(function=command_add_sequence)
    convert = commands.add_parser("convert-ppm")
    convert.add_argument("--ppm", type=Path, required=True)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--no-clobber", action="store_true")
    convert.set_defaults(function=command_convert_ppm)
    copy_file = commands.add_parser("copy-file-exclusive")
    copy_file.add_argument("--source", type=Path, required=True)
    copy_file.add_argument("--destination", type=Path, required=True)
    copy_file.set_defaults(function=command_copy_file_exclusive)
    verify = commands.add_parser("verify-ab")
    for name in ("a", "b", "c"):
        verify.add_argument(f"--{name}", type=Path, required=True)
        verify.add_argument(f"--{name}-image", type=Path)
    verify.add_argument("--request-id", required=True)
    verify.add_argument("--report", type=Path)
    verify.set_defaults(function=command_verify_ab)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        arguments.function(arguments)
    except EvidenceError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
