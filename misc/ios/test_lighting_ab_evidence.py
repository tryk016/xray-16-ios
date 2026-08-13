#!/usr/bin/env python3
"""Host-only mutation coverage for capture-v2 and stationary/forward A/B."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_lighting_ab_evidence.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import copy
import contextlib
import importlib.util
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("lighting_ab_evidence", ROOT / "misc/ios/lighting_ab_evidence.py")
assert SPEC and SPEC.loader
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)

SESSION = "0123456789abcdef0123456789abcdef"
REQUEST = "12345678-1234-4abc-8def-1234567890ab"
LIGHTING_BASE = {
    "ambient": [0.08, 0.09, 0.10],
    "hemi": [0.35, 0.40, 0.45],
}
LIGHTING_SLOPE = {
    "ambient": [0.12, 0.08, 0.04],
    "hemi": [0.10, 0.05, -0.02],
}


def set_realistic_lighting(value: dict, weight: float) -> None:
    value["environment"]["weight"] = weight
    for key, base in LIGHTING_BASE.items():
        value["environment"][key] = [component + LIGHTING_SLOPE[key][index] * weight
            for index, component in enumerate(base)]
    value["environment"]["hemi"].append(0.70 + 0.03 * weight * weight)
    value["environment"]["sun"] = [0.72 + 0.18 * weight - 0.08 * weight * weight,
        0.68 + 0.12 * weight + 0.04 * weight * weight,
        0.58 - 0.10 * weight + 0.06 * weight * weight]
    angle = 0.12 + 0.45 * weight + 0.20 * weight * weight
    value["environment"]["sun_direction"] = [math.sin(angle), -math.cos(angle), 0.0]


def capture(sequence: int = 10, frame: int = 100, continual: int = 1000, game_time: int = 10000) -> dict:
    return {
        "schema": "openxray.capture.v2",
        "capture": {"token": f"{SESSION}:{sequence}", "session": SESSION, "sequence": sequence, "pid": 42,
            "frame": frame, "continual_ms": continual, "width": 1864, "height": 860, "period_ms": 5000,
            "scene": "gameplay", "paused": False},
        "view": {"position": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0], "fov": 67.5},
        "world": {"level": "zaton", "epoch": 2, "sector": 115},
        "environment": {"game_time_ms": game_time, "day_time_s": 43200.0, "time_factor": 10.0,
            "cycle": "default", "weather": "default", "weather_fx": False, "descriptor0": "12:00:00",
            "descriptor1": "13:00:00", "weight": 0.1, "ambient": [0.1, 0.2, 0.3],
            "hemi": [0.4, 0.5, 0.6, 0.7], "sun": [0.8, 0.9, 1.0], "sun_direction": [0.0, -1.0, 0.0]},
        "input": {"generation": 0, "state": "none", "request_id": None, "key": None, "scancode": None,
            "duration_ms": 0, "accepted": None, "released": None},
    }


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")


def make_ppm(path: Path, token: str = f"{SESSION}:10", width: int = 2, height: int = 1, pixels: bytes | None = None) -> None:
    pixels = pixels if pixels is not None else b"\x01\x02\x03" * (width * height)
    path.write_bytes(f"P6\n# openxray-capture-v2 token={token}\n{width} {height}\n255\n".encode() + pixels)


class CaptureSchemaTests(unittest.TestCase):
    def reject(self, value: dict) -> None:
        with self.assertRaises(EVIDENCE.EvidenceError):
            EVIDENCE.validate_capture(value)

    def test_every_object_field_is_required_and_unknown_fields_rejected(self) -> None:
        value = capture()
        EVIDENCE.validate_capture(value)

        def visit(node: object, path: tuple[str, ...] = ()):
            if isinstance(node, dict):
                for key, child in node.items():
                    yield path, key
                    yield from visit(child, path + (key,))

        for parent_path, key in visit(value):
            missing = copy.deepcopy(value)
            parent = missing
            for segment in parent_path:
                parent = parent[segment]
            del parent[key]
            self.reject(missing)
            unknown = copy.deepcopy(value)
            parent = unknown
            for segment in parent_path:
                parent = parent[segment]
            parent["unexpected"] = 1
            self.reject(unknown)

    def test_every_leaf_or_vector_rejects_a_wrong_type(self) -> None:
        value = capture()

        def leaves(node: object, path: tuple[object, ...] = ()):
            if isinstance(node, dict):
                for key, child in node.items():
                    yield from leaves(child, path + (key,))
            else:
                yield path

        for path in leaves(value):
            with self.subTest(path=path):
                mutated = copy.deepcopy(value)
                parent = mutated
                for segment in path[:-1]:
                    parent = parent[segment]
                parent[path[-1]] = {"wrong": "type"}
                self.reject(mutated)

    def test_wrong_leaf_types_and_relations_are_rejected(self) -> None:
        for path in (("schema",), ("capture", "sequence"), ("capture", "pid"), ("view", "fov"),
                     ("world", "epoch"), ("environment", "game_time_ms"), ("input", "generation")):
            value = copy.deepcopy(capture())
            target = value
            for segment in path[:-1]:
                target = target[segment]
            target[path[-1]] = []
            self.reject(value)
        value = capture()
        value["capture"]["token"] = f"{SESSION}:11"
        self.reject(value)
        value = capture()
        value["capture"]["frame"] = 1 << 32
        self.reject(value)
        value = capture()
        value["capture"]["width"] = 16385
        self.reject(value)
        value = capture()
        value["capture"]["sequence"] = 1 << 64
        self.reject(value)
        value = capture()
        value["world"]["sector"] = 1 << 64
        self.reject(value)
        value = capture()
        value["environment"]["game_time_ms"] = 1 << 64
        self.reject(value)
        value = capture()
        value["capture"]["scene"] = "loading"
        self.reject(value)
        value = capture()
        value["world"] = None
        self.reject(value)
        value = capture()
        value["input"] = {"generation": 1, "state": "released", "request_id": REQUEST, "key": "w",
            "scancode": 26, "duration_ms": 12000, "accepted": {"frame": 101, "continual_ms": 1001, "sdl_ms": 1},
            "released": {"frame": 100, "continual_ms": 1000, "sdl_ms": 2}}
        self.reject(value)
        value = capture()
        value["input"] = {"generation": 1, "state": "cancelled", "request_id": REQUEST, "key": "tap",
            "scancode": -1, "duration_ms": 0, "accepted": None, "released": None}
        EVIDENCE.validate_capture(value)
        value["input"]["accepted"] = {"frame": 101, "continual_ms": 1001, "sdl_ms": 1}
        self.reject(value)
        value = capture()
        value["world"]["level"] = "x" * 128
        self.reject(value)
        value = capture()
        value["input"]["duration_ms"] = 0.0
        self.reject(value)

    def test_nonfinite_duplicates_scene_and_input_timestamps_are_rejected(self) -> None:
        path = Path(tempfile.mkstemp(prefix="capture-json-", suffix=".json")[1])
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text('{"schema":"openxray.capture.v2","schema":"openxray.capture.v2"}', encoding="utf-8")
        with self.assertRaises(EVIDENCE.EvidenceError):
            EVIDENCE.load_json(path)
        for literal in ("NaN", "Infinity", "-Infinity"):
            path.write_text('{"value":' + literal + '}', encoding="utf-8")
            with self.assertRaises(EVIDENCE.EvidenceError, msg=literal):
                EVIDENCE.load_json(path)
        for scene in ("menu", "loading"):
            value = capture()
            value["capture"]["scene"] = scene
            self.reject(value)
        value = capture()
        value["environment"] = None
        EVIDENCE.validate_capture(value)
        value = capture()
        value["world"] = None
        value["environment"] = copy.deepcopy(capture()["environment"])
        self.reject(value)
        for event_name in ("accepted", "released"):
            value = capture()
            value["input"] = {"generation": 1, "state": "released", "request_id": REQUEST, "key": "w",
                "scancode": 26, "duration_ms": 12000,
                "accepted": {"frame": 99, "continual_ms": 999, "sdl_ms": 1},
                "released": {"frame": 100, "continual_ms": 1000, "sdl_ms": 2}}
            value["input"][event_name]["frame"] = 101
            self.reject(value)


class ImageFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="capture-v2-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.work)

    def test_ppm_token_dimensions_truncation_and_trailing_are_strict(self) -> None:
        ppm = self.work / "valid.ppm"
        make_ppm(ppm)
        self.assertEqual(EVIDENCE.parse_ppm(ppm)[:3], (2, 1, f"{SESSION}:10"))
        for name, transform in {
            "bad-token": lambda raw: raw.replace(SESSION.encode(), b"F" + b"f" * 31, 1),
            "overflow-token": lambda raw: raw.replace(b":10", f":{1 << 64}".encode(), 1),
            "bad-dimensions": lambda raw: raw.replace(b"2 1", b"0 1", 1),
            "truncated": lambda raw: raw[:-1],
            "trailing": lambda raw: raw + b"x",
        }.items():
            path = self.work / f"{name}.ppm"
            path.write_bytes(transform(ppm.read_bytes()))
            with self.assertRaises(EVIDENCE.EvidenceError, msg=name):
                EVIDENCE.parse_ppm(path)

    def test_png_crc_dimensions_truncation_and_trailing_are_strict(self) -> None:
        ppm = self.work / "input.ppm"
        png = self.work / "output.png"
        make_ppm(ppm)
        EVIDENCE.command_convert_ppm(SimpleNamespace(ppm=ppm, output=png, no_clobber=True))
        self.assertEqual(EVIDENCE.verify_png(png), (2, 1, f"{SESSION}:10"))
        raw = png.read_bytes()
        corrupted = self.work / "crc.png"
        corrupted.write_bytes(raw[:20] + bytes([raw[20] ^ 1]) + raw[21:])
        truncated = self.work / "truncated.png"
        truncated.write_bytes(raw[:-1])
        trailing = self.work / "trailing.png"
        trailing.write_bytes(raw + b"x")
        dimensions = self.work / "dimensions.png"
        dimensions.write_bytes(raw[:16] + b"\x00\x00\x00\x03" + raw[20:])
        for path in (corrupted, truncated, trailing, dimensions):
            with self.assertRaises(EVIDENCE.EvidenceError, msg=path.name):
                EVIDENCE.verify_png(path)

        chunks = raw[:8] + EVIDENCE.png_chunk(b"IHDR", raw[16:29]) \
            + EVIDENCE.png_chunk(b"tEXt", b"other\\0value") + raw[33:]
        other_text = self.work / "other-text.png"
        other_text.write_bytes(chunks)
        with self.assertRaises(EVIDENCE.EvidenceError):
            EVIDENCE.verify_png(other_text)

        missing_idat = self.work / "missing-idat.png"
        missing_idat.write_bytes(raw[:8] + raw[8:33] + raw[33:33 + 12 + len(b"openxray-capture-v2\\0" + f"{SESSION}:10".encode())] + EVIDENCE.png_chunk(b"IEND", b""))
        with self.assertRaises(EVIDENCE.EvidenceError):
            EVIDENCE.verify_png(missing_idat)

    def test_dangling_symlink_is_not_an_available_output(self) -> None:
        ppm = self.work / "input.ppm"
        output = self.work / "dangling.png"
        make_ppm(ppm)
        os.symlink("missing-target", output)
        with self.assertRaises(EVIDENCE.EvidenceError):
            EVIDENCE.command_convert_ppm(SimpleNamespace(ppm=ppm, output=output, no_clobber=True))

        source = self.work / "metadata.json"
        destination = self.work / "dangling-metadata.json"
        source.write_text("{}", encoding="utf-8")
        destination.symlink_to("missing-metadata")
        with self.assertRaises(EVIDENCE.EvidenceError):
            EVIDENCE.command_copy_file_exclusive(SimpleNamespace(source=source, destination=destination))


class LiveCaptureCommandTests(unittest.TestCase):
    """Mutation coverage for the local Simulator capture-v2 parser contract."""

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="capture-live-test-"))
        self.metadata = self.work / "xr_shot_meta.txt"
        self.ppm = self.work / "xr_shot.ppm"
        self.output = self.work / "out"
        self.output.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.work)

    def live_capture(self, sequence: int, frame: int, continual: int) -> dict:
        value = capture(sequence, frame, continual, 10000 + continual)
        value["world"]["sector"] = 0  # Zero is valid and must not be rejected.
        return value

    def write_live_pair(self, value: dict, *, token: str | None = None,
                        width: int | None = None, height: int | None = None) -> None:
        write_json(self.metadata, value)
        make_ppm(self.ppm, token or value["capture"]["token"],
                 width or value["capture"]["width"], height or value["capture"]["height"],
                 b"\0" * ((width or value["capture"]["width"]) * (height or value["capture"]["height"]) * 3))

    def observe(self, output: Path, **extra: object) -> None:
        arguments = {"metadata": self.metadata, "output": output, "expected_pid": 42,
                     "after_token": None, "expected_session": None, "allow_absent": False}
        arguments.update(extra)
        with contextlib.redirect_stdout(io.StringIO()):
            EVIDENCE.command_observe_live_metadata(SimpleNamespace(**arguments))

    def snapshot(self, baseline: Path, root: Path, **extra: object) -> None:
        arguments = {"metadata": self.metadata, "ppm": self.ppm, "baseline": baseline,
                     "metadata_output": root / "capture.json", "ppm_output": root / "capture.ppm",
                     "proof_output": root / "capture-proof.json", "expected_pid": 42,
                     "expected_level": "zaton", "after_token": f"{SESSION}:10",
                     "expected_session": SESSION, "boundary_token": "null"}
        arguments.update(extra)
        with contextlib.redirect_stdout(io.StringIO()):
            EVIDENCE.command_snapshot_live_capture(SimpleNamespace(**arguments))

    def snapshot_cli(self, baseline: Path, root: Path, *, after_token: str = f"{SESSION}:10",
                     expected_session: str = SESSION) -> subprocess.CompletedProcess[str]:
        return subprocess.run((
            sys.executable, str(ROOT / "misc/ios/lighting_ab_evidence.py"),
            "snapshot-live-capture", "--metadata", str(self.metadata), "--ppm", str(self.ppm),
            "--baseline", str(baseline), "--metadata-output", str(root / "capture.json"),
            "--ppm-output", str(root / "capture.ppm"),
            "--proof-output", str(root / "capture-proof.json"), "--expected-pid", "42",
            "--expected-level", "zaton", "--after-token", after_token,
            "--expected-session", expected_session, "--boundary-token", "null",
        ), text=True, capture_output=True, check=False)

    def assert_exact_retry(self, callback) -> EVIDENCE.RetryableEvidenceError:
        with self.assertRaises(EVIDENCE.RetryableEvidenceError) as raised:
            callback()
        self.assertIs(type(raised.exception), EVIDENCE.RetryableEvidenceError)
        return raised.exception

    def assert_exact_fatal(self, callback) -> EVIDENCE.EvidenceError:
        with self.assertRaises(EVIDENCE.EvidenceError) as raised:
            callback()
        self.assertIs(type(raised.exception), EVIDENCE.EvidenceError)
        return raised.exception

    def verify_set(self, root: Path) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            EVIDENCE.command_verify_simulator_capture_set(SimpleNamespace(
                boundary=root / "boundary-watermark.json", baseline=root / "baseline.json",
                metadata=root / "capture.json", ppm=root / "capture.ppm",
                proof=root / "capture-proof.json", expected_pid=42, expected_level="zaton",
            ))

    def test_observe_missing_race_bad_source_and_dangling_destination_are_distinct(self) -> None:
        with self.assertRaises(EVIDENCE.RetryableEvidenceError):
            self.observe(self.output / "missing.json")
        self.metadata.symlink_to("missing.json")
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.observe(self.output / "symlink.json")
        self.metadata.unlink()
        self.metadata.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.observe(self.output / "malformed.json")
        self.metadata.unlink()
        write_json(self.metadata, self.live_capture(10, 100, 1000))
        destination = self.output / "dangling.json"
        destination.symlink_to("not-there")
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.observe(destination)

    def test_observe_explicit_absent_is_distinct_from_a_preopen_disappearance_race(self) -> None:
        output = self.output / "boundary.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            EVIDENCE.command_observe_live_metadata(SimpleNamespace(
                metadata=self.metadata, output=output, expected_pid=42,
                after_token=None, expected_session=None, allow_absent=True,
            ))
        self.assertEqual(stdout.getvalue(), "ABSENT\n")
        self.assertFalse(output.exists())

        write_json(self.metadata, self.live_capture(10, 100, 1000))
        with mock.patch.object(EVIDENCE.os, "open", side_effect=FileNotFoundError()):
            with self.assertRaises(EVIDENCE.RetryableEvidenceError):
                EVIDENCE.command_observe_live_metadata(SimpleNamespace(
                    metadata=self.metadata, output=output, expected_pid=42,
                    after_token=None, expected_session=None, allow_absent=True,
                ))
        self.assertFalse(output.exists())

    def test_loading_t0_and_inflight_loading_t1_use_only_identity_and_order(self) -> None:
        t0 = self.live_capture(10, 100, 1000)
        t0["capture"]["scene"] = "loading"
        t0["capture"]["paused"] = True
        t0["world"] = None
        t0["environment"] = None
        t0["input"] = {"generation": 1, "state": "active", "request_id": REQUEST,
            "key": "w", "scancode": 26, "duration_ms": 12000,
            "accepted": {"frame": 99, "continual_ms": 999, "sdl_ms": 1}, "released": None}
        write_json(self.metadata, t0)
        boundary = self.output / "boundary.json"
        self.observe(boundary)

        t1 = copy.deepcopy(t0)
        t1["capture"].update(token=f"{SESSION}:11", sequence=11, frame=101, continual_ms=1001)
        write_json(self.metadata, t1)
        baseline = self.output / "baseline.json"
        self.observe(baseline, after_token=t0["capture"]["token"], expected_session=SESSION)
        self.assertEqual(EVIDENCE.validate_capture(EVIDENCE.load_json(baseline)), t1)

        t2 = copy.deepcopy(t1)
        t2["capture"].update(token=f"{SESSION}:12", sequence=12, frame=102, continual_ms=1002)
        t2["input"] = {"generation": 0, "state": "none", "request_id": None, "key": None,
            "scancode": None, "duration_ms": 0, "accepted": None, "released": None}
        write_json(self.metadata, t2)
        make_ppm(self.ppm, t2["capture"]["token"], 1864, 860, b"\0" * (1864 * 860 * 3))
        root = self.output / "loading-t2"
        root.mkdir()
        self.assert_exact_retry(
            lambda: self.snapshot(baseline, root, after_token=t1["capture"]["token"])
        )
        self.assertEqual(list(root.iterdir()), [])

    def test_observe_requires_runtime_identity_and_only_publishes_revalidated_copy(self) -> None:
        value = self.live_capture(10, 100, 1000)
        write_json(self.metadata, value)
        output = self.output / "boundary.json"
        self.observe(output)
        self.assertEqual(EVIDENCE.validate_capture(EVIDENCE.load_json(output)), value)
        stale = self.output / "stale.json"
        with self.assertRaises(EVIDENCE.RetryableEvidenceError):
            self.observe(stale, after_token=value["capture"]["token"], expected_session=SESSION)
        self.assertFalse(stale.exists())
        wrong_pid = self.output / "pid.json"
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.observe(wrong_pid, expected_pid=43)
        self.assertFalse(wrong_pid.exists())

    def test_snapshot_commits_metadata_ppm_then_proof_and_hashes_exact_outputs(self) -> None:
        baseline_value = self.live_capture(10, 100, 1000)
        baseline = self.output / "baseline.json"
        write_json(baseline, baseline_value)
        value = self.live_capture(11, 110, 6000)
        self.write_live_pair(value)
        root = self.output / "capture"
        root.mkdir()
        self.snapshot(baseline, root)
        metadata = root / "capture.json"
        ppm = root / "capture.ppm"
        proof = root / "capture-proof.json"
        self.assertEqual(EVIDENCE.validate_capture_pair(metadata, ppm), value)
        proof_value = json.loads(proof.read_text())
        self.assertEqual(proof_value["token"], value["capture"]["token"])
        self.assertEqual(proof_value["boundary_token"], "null")
        self.assertEqual(proof_value["baseline_token"], f"{SESSION}:10")
        self.assertEqual(proof_value["pid"], 42)
        self.assertEqual(proof_value["sector"], 0)
        self.assertIn("no causal, pixel, iPhone, or performance claim", proof_value["evidence_boundary"])

    def test_complete_set_revalidation_detects_post_snapshot_ppm_and_proof_mutation(self) -> None:
        root = self.output / "complete"
        root.mkdir()
        (root / "boundary-watermark.json").write_bytes(
            b'{"schema":"openxray.simulator-capture-v2-boundary.v1","token":null}\n'
        )
        baseline = root / "baseline.json"
        write_json(baseline, self.live_capture(10, 100, 1000))
        self.write_live_pair(self.live_capture(11, 110, 6000))
        self.snapshot(baseline, root)
        self.verify_set(root)

        original_ppm = (root / "capture.ppm").read_bytes()
        (root / "capture.ppm").write_bytes(original_ppm + b"x")
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify_set(root)
        (root / "capture.ppm").write_bytes(original_ppm)
        (root / "capture-proof.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify_set(root)

    def test_owned_inode_cleanup_never_removes_a_replacement(self) -> None:
        destination = self.output / "owned.bin"
        original_write = EVIDENCE.os.write

        def replace_then_fail(descriptor: int, payload: bytes) -> int:
            written = original_write(descriptor, payload)
            destination.unlink()
            destination.write_bytes(b"replacement")
            raise OSError("injected write failure")

        with mock.patch.object(EVIDENCE.os, "write", side_effect=replace_then_fail):
            with self.assertRaises(EVIDENCE.EvidenceError):
                EVIDENCE.write_bytes_exclusive(destination, b"owned")
        self.assertEqual(destination.read_bytes(), b"replacement")

    def test_snapshot_failure_after_first_or_second_output_cleans_only_attempt_outputs(self) -> None:
        baseline = self.output / "baseline.json"
        write_json(baseline, self.live_capture(10, 100, 1000))
        self.write_live_pair(self.live_capture(11, 110, 6000))
        original_writer = EVIDENCE.write_bytes_exclusive
        for failure_call in (2, 3):
            with self.subTest(failure_call=failure_call):
                root = self.output / f"failure-{failure_call}"
                root.mkdir()
                calls = 0

                def injected(path: Path, payload: bytes) -> tuple[int, int]:
                    nonlocal calls
                    calls += 1
                    if calls == failure_call:
                        raise EVIDENCE.EvidenceError("injected publication failure")
                    return original_writer(path, payload)

                with mock.patch.object(EVIDENCE, "write_bytes_exclusive", side_effect=injected):
                    with self.assertRaises(EVIDENCE.EvidenceError):
                        self.snapshot(baseline, root)
                self.assertEqual(list(root.iterdir()), [])

    def test_snapshot_cleanup_preserves_replaced_first_output_inode(self) -> None:
        baseline = self.output / "baseline.json"
        write_json(baseline, self.live_capture(10, 100, 1000))
        self.write_live_pair(self.live_capture(11, 110, 6000))
        root = self.output / "replacement-cleanup"
        root.mkdir()
        original_writer = EVIDENCE.write_bytes_exclusive
        calls = 0

        def replace_first_then_fail(path: Path, payload: bytes) -> tuple[int, int]:
            nonlocal calls
            calls += 1
            if calls == 1:
                identity = original_writer(path, payload)
                path.unlink()
                path.write_bytes(b"replacement")
                return identity
            raise EVIDENCE.EvidenceError("injected second-output failure")

        with mock.patch.object(EVIDENCE, "write_bytes_exclusive", side_effect=replace_first_then_fail):
            with self.assertRaises(EVIDENCE.EvidenceError):
                self.snapshot(baseline, root)
        self.assertEqual((root / "capture.json").read_bytes(), b"replacement")
        self.assertEqual([path.name for path in root.iterdir()], ["capture.json"])

    def test_snapshot_retries_inflight_or_old_and_never_leaves_partial_artifacts(self) -> None:
        baseline_value = self.live_capture(10, 100, 1000)
        baseline = self.output / "baseline.json"
        write_json(baseline, baseline_value)
        root = self.output / "retry"
        root.mkdir()
        stale = self.live_capture(10, 100, 1000)
        self.write_live_pair(stale)
        self.assert_exact_retry(lambda: self.snapshot(baseline, root))
        self.assertEqual(list(root.iterdir()), [])
        value = self.live_capture(11, 110, 6000)
        self.write_live_pair(value, token=f"{SESSION}:12")
        self.assert_exact_retry(lambda: self.snapshot(baseline, root))
        self.assertEqual(list(root.iterdir()), [])
        self.write_live_pair(value, width=1863)
        self.assert_exact_retry(lambda: self.snapshot(baseline, root))
        self.assertEqual(list(root.iterdir()), [])
        for field, stale_value in (("frame", 100), ("continual_ms", 1000)):
            with self.subTest(nonincreasing=field):
                candidate = self.live_capture(11, 110, 6000)
                candidate["capture"][field] = stale_value
                self.write_live_pair(candidate)
                self.assert_exact_fatal(lambda: self.snapshot(baseline, root))
                self.assertEqual(list(root.iterdir()), [])

    def test_saved_baseline_pid_or_session_mismatch_is_exact_fatal_cli_1(self) -> None:
        candidate = self.live_capture(11, 110, 6000)
        self.write_live_pair(candidate)
        other_session = "f" * 32
        for name in ("pid", "session"):
            with self.subTest(name=name):
                baseline_value = self.live_capture(10, 100, 1000)
                if name == "pid":
                    baseline_value["capture"]["pid"] = 43
                else:
                    baseline_value["capture"].update(
                        session=other_session, token=f"{other_session}:10")

                invariant_error = self.assert_exact_fatal(lambda: EVIDENCE.verify_local_capture_invariants(
                    candidate, expected_pid=42, expected_level="zaton", after_token=None,
                    expected_session=SESSION, baseline=baseline_value,
                ))
                self.assertIn("does not match the saved baseline", str(invariant_error))

                baseline = self.output / f"baseline-{name}.json"
                write_json(baseline, baseline_value)
                after_token = baseline_value["capture"]["token"]
                direct_root = self.output / f"baseline-{name}-direct"
                direct_root.mkdir()
                self.assert_exact_fatal(lambda: self.snapshot(
                    baseline, direct_root, after_token=after_token, expected_session=SESSION,
                ))
                self.assertEqual(list(direct_root.iterdir()), [])

                cli_root = self.output / f"baseline-{name}-cli"
                cli_root.mkdir()
                cli = self.snapshot_cli(
                    baseline, cli_root, after_token=after_token, expected_session=SESSION,
                )
                self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
                self.assertTrue(cli.stderr.startswith("FAIL:"), cli.stderr)
                self.assertNotIn("RETRY:", cli.stderr)
                self.assertEqual(list(cli_root.iterdir()), [])

    def test_snapshot_rejects_runtime_contract_destinations_and_proof_preexistence(self) -> None:
        baseline = self.output / "baseline.json"
        write_json(baseline, self.live_capture(10, 100, 1000))
        value = self.live_capture(11, 110, 6000)
        self.write_live_pair(value)
        root = self.output / "bad"
        root.mkdir()
        (root / "capture-proof.json").symlink_to("missing-proof")
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.snapshot(baseline, root)
        self.assertFalse((root / "capture.json").exists())
        self.assertFalse((root / "capture.ppm").exists())
        self.assertTrue((root / "capture-proof.json").is_symlink())
        (root / "capture-proof.json").unlink()
        self.assertEqual(list(root.iterdir()), [])

    def test_snapshot_rejects_stable_malformed_ppm_and_every_runtime_field_boundary(self) -> None:
        baseline = self.output / "baseline.json"
        write_json(baseline, self.live_capture(10, 100, 1000))
        value = self.live_capture(11, 110, 6000)
        self.write_live_pair(value)
        self.ppm.write_bytes(b"not-a-ppm")
        malformed_root = self.output / "malformed"
        malformed_root.mkdir()
        self.assert_exact_fatal(lambda: self.snapshot(baseline, malformed_root))
        self.assertEqual(list(malformed_root.iterdir()), [])

        other_session = "f" * 32
        mutations = {
            "pid": lambda candidate: candidate["capture"].__setitem__("pid", 43),
            "session": lambda candidate: candidate["capture"].update(
                session=other_session, token=f"{other_session}:11"),
            "menu": lambda candidate: (candidate["capture"].__setitem__("scene", "menu"),
                candidate.__setitem__("world", None), candidate.__setitem__("environment", None)),
            "width": lambda candidate: candidate["capture"].__setitem__("width", 1863),
            "height": lambda candidate: candidate["capture"].__setitem__("height", 859),
            "period": lambda candidate: candidate["capture"].__setitem__("period_ms", 4999),
            "level": lambda candidate: candidate["world"].__setitem__("level", "jupiter"),
            "epoch": lambda candidate: candidate["world"].__setitem__("epoch", 0),
            "world-null": lambda candidate: candidate.__setitem__("world", None),
            "input": lambda candidate: candidate.__setitem__("input", {"generation": 1, "state": "active",
                "request_id": REQUEST, "key": "w", "scancode": 26, "duration_ms": 12000,
                "accepted": {"frame": 101, "continual_ms": 5000, "sdl_ms": 1}, "released": None}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                candidate = self.live_capture(11, 110, 6000)
                mutate(candidate)
                write_json(self.metadata, candidate)
                make_ppm(self.ppm, candidate["capture"]["token"], 1864, 860,
                         b"\0" * (1864 * 860 * 3))
                root = self.output / name
                root.mkdir()
                self.assert_exact_fatal(lambda: self.snapshot(baseline, root))
                self.assertEqual(list(root.iterdir()), [])

        menu = self.live_capture(11, 110, 6000)
        menu["capture"]["scene"] = "menu"
        menu["world"] = None
        menu["environment"] = None
        self.write_live_pair(menu)
        cli_root = self.output / "fatal-cli"
        cli_root.mkdir()
        cli = self.snapshot_cli(baseline, cli_root)
        self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
        self.assertTrue(cli.stderr.startswith("FAIL:"), cli.stderr)
        self.assertNotIn("RETRY:", cli.stderr)
        self.assertEqual(list(cli_root.iterdir()), [])

    def test_live_t2_loading_paused_and_environment_null_are_exact_retry_75(self) -> None:
        baseline = self.output / "baseline.json"
        write_json(baseline, self.live_capture(10, 100, 1000))
        candidates: dict[str, dict] = {}

        loading = self.live_capture(11, 110, 6000)
        loading["capture"]["scene"] = "loading"
        loading["world"] = None
        loading["environment"] = None
        candidates["loading"] = loading

        paused = self.live_capture(11, 110, 6000)
        paused["capture"]["paused"] = True
        candidates["paused"] = paused

        environment_null = self.live_capture(11, 110, 6000)
        environment_null["environment"] = None
        candidates["environment-null"] = environment_null

        for name, candidate in candidates.items():
            with self.subTest(name=name):
                self.write_live_pair(candidate)
                direct_root = self.output / f"retry-{name}"
                direct_root.mkdir()
                self.assert_exact_retry(lambda: self.snapshot(baseline, direct_root))
                self.assertEqual(list(direct_root.iterdir()), [])

                cli_root = self.output / f"retry-cli-{name}"
                cli_root.mkdir()
                cli = self.snapshot_cli(baseline, cli_root)
                self.assertEqual(cli.returncode, 75, cli.stdout + cli.stderr)
                self.assertTrue(cli.stderr.startswith("RETRY:"), cli.stderr)
                self.assertNotIn("FAIL:", cli.stderr)
                self.assertEqual(list(cli_root.iterdir()), [])

    def test_post_stop_verifier_escalates_retryable_loading_to_exact_fatal_exit_1(self) -> None:
        root = self.output / "post-stop-loading"
        root.mkdir()
        (root / "boundary-watermark.json").write_bytes(
            b'{"schema":"openxray.simulator-capture-v2-boundary.v1","token":null}\n'
        )
        write_json(root / "baseline.json", self.live_capture(10, 100, 1000))
        loading = self.live_capture(11, 110, 6000)
        loading["capture"]["scene"] = "loading"
        loading["world"] = None
        loading["environment"] = None
        write_json(root / "capture.json", loading)
        make_ppm(root / "capture.ppm", loading["capture"]["token"], 1864, 860,
                 b"\0" * (1864 * 860 * 3))
        (root / "capture-proof.json").write_text("{}\n", encoding="utf-8")
        arguments = SimpleNamespace(
            boundary=root / "boundary-watermark.json", baseline=root / "baseline.json",
            metadata=root / "capture.json", ppm=root / "capture.ppm",
            proof=root / "capture-proof.json", expected_pid=42, expected_level="zaton",
        )
        error = self.assert_exact_fatal(
            lambda: EVIDENCE.command_verify_simulator_capture_set(arguments)
        )
        self.assertIn("post-stop capture set retained a retryable state", str(error))

        cli = subprocess.run((
            sys.executable, str(ROOT / "misc/ios/lighting_ab_evidence.py"),
            "verify-simulator-capture-set", "--boundary", str(arguments.boundary),
            "--baseline", str(arguments.baseline), "--metadata", str(arguments.metadata),
            "--ppm", str(arguments.ppm), "--proof", str(arguments.proof),
            "--expected-pid", "42", "--expected-level", "zaton",
        ), text=True, capture_output=True, check=False)
        self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
        self.assertTrue(cli.stderr.startswith("FAIL:"), cli.stderr)
        self.assertNotIn("RETRY:", cli.stderr)


class ABTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="capture-ab-test-"))
        self.a = capture(10, 100, 1000, 10000)
        self.b = capture(13, 200, 16000, 160000)
        self.c = capture(16, 300, 31000, 310000)
        self.b["view"]["position"] = [0.1, 0.0, 0.0]
        self.c["view"]["position"] = [5.0, 0.0, 0.0]
        for value, weight in ((self.a, 0.1), (self.b, 0.2), (self.c, 0.3)):
            set_realistic_lighting(value, weight)
        self.c["input"] = {"generation": 1, "state": "released", "request_id": REQUEST, "key": "w",
            "scancode": 26, "duration_ms": 12000,
            "accepted": {"frame": 201, "continual_ms": 16001, "sdl_ms": 100},
            "released": {"frame": 250, "continual_ms": 28001, "sdl_ms": 12100}}

    def tearDown(self) -> None:
        shutil.rmtree(self.work)

    def verify(self, a: dict | None = None, b: dict | None = None, c: dict | None = None) -> dict:
        paths = (self.work / "A.json", self.work / "B.json", self.work / "C.json")
        for path, value in zip(paths, (a or self.a, b or self.b, c or self.c)):
            write_json(path, value)
        return EVIDENCE.verify_ab(SimpleNamespace(a=paths[0], b=paths[1], c=paths[2], request_id=REQUEST,
            report=None, a_image=None, b_image=None, c_image=None))

    def test_controlled_ab_passes_and_reports_no_causal_claim(self) -> None:
        report = self.verify()
        self.assertEqual(report["sequences"], [10, 13, 16])
        lighting = report["final_lighting"]
        self.assertEqual(lighting["weight"]["mode"], "linear_weight")
        self.assertEqual(lighting["gated_vectors"]["ambient"]["actual"]["A"],
            self.a["environment"]["ambient"])
        for vector in lighting["gated_vectors"].values():
            self.assertEqual(len(vector["actual_delta"]["A_B"]), len(vector["predicted_C"]))
            self.assertTrue(all(abs(residual) <= tolerance
                for residual, tolerance in zip(vector["residual_C"], vector["tolerance_C"])))
        self.assertEqual(lighting["non_gating_observations"]["sun"]["policy"], "observation_only")
        self.assertEqual(lighting["non_gating_observations"]["sun_direction"]["policy"], "observation_only")
        self.assertIn("no lighting or streaming causal claim", lighting["policy"]["evidence_boundary"])
        c = copy.deepcopy(self.c)
        c["environment"]["ambient"][0] += 1e-6
        self.verify(c=c)
        self.assertNotIn("pixel", report)
        self.assertNotIn("cause", report)

    def test_replaced_generation_and_ac_direction_drift_fail(self) -> None:
        c = copy.deepcopy(self.c)
        c["input"]["generation"] = 2
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(c=c)

    def test_stationary_input_snapshot_must_not_change_between_a_and_b(self) -> None:
        old = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        intervening = {"generation": 8, "state": "released", "request_id": old, "key": "escape",
            "scancode": 41, "duration_ms": 100,
            "accepted": {"frame": 150, "continual_ms": 12000, "sdl_ms": 100},
            "released": {"frame": 151, "continual_ms": 12100, "sdl_ms": 200}}
        b = copy.deepcopy(self.b)
        c = copy.deepcopy(self.c)
        b["input"] = intervening
        c["input"]["generation"] = 9
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(b=b, c=c)

        # A retained older request is legal only when it already existed at A.
        # Its events must therefore predate A as well as B.
        old_snapshot = copy.deepcopy(intervening)
        old_snapshot["accepted"] = {"frame": 50, "continual_ms": 500, "sdl_ms": 100}
        old_snapshot["released"] = {"frame": 51, "continual_ms": 600, "sdl_ms": 200}
        a = copy.deepcopy(self.a)
        b = copy.deepcopy(self.b)
        a["input"] = copy.deepcopy(old_snapshot)
        b["input"] = copy.deepcopy(old_snapshot)
        c["input"]["generation"] = 9
        self.verify(a=a, b=b, c=c)

        b["input"]["request_id"] = REQUEST
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(a=a, b=b, c=c)

    def test_linear_position_modified_rgb_rejects_isolated_c_discontinuities(self) -> None:
        for key, index in (("ambient", 0), ("hemi", 2)):
            a, b, c = copy.deepcopy(self.a), copy.deepcopy(self.b), copy.deepcopy(self.c)
            c["environment"][key][index] += 0.01
            with self.subTest(key=key, index=index):
                with self.assertRaises(EVIDENCE.EvidenceError):
                    self.verify(a=a, b=b, c=c)

    def test_nonlinear_sun_and_other_observations_are_visible_but_non_gating(self) -> None:
        c = copy.deepcopy(self.c)
        c["environment"]["hemi"][3] += 0.2
        c["environment"]["sun"][0] += 0.25
        c["environment"]["sun_direction"][1] += 0.15
        report = self.verify(c=c)["final_lighting"]
        observations = report["non_gating_observations"]
        self.assertEqual(observations["hemi_alpha"]["actual"]["C"], c["environment"]["hemi"][3])
        self.assertEqual(observations["sun"]["actual_delta"]["B_C"][0],
            c["environment"]["sun"][0] - self.b["environment"]["sun"][0])
        self.assertEqual(observations["sun_direction"]["actual"]["C"],
            c["environment"]["sun_direction"])
        self.assertTrue(all(observation["policy"] == "observation_only"
            for observation in observations.values()))

    def test_zero_and_tiny_weight_deltas_are_explicit_and_fail_closed(self) -> None:
        a, b, c = copy.deepcopy(self.a), copy.deepcopy(self.b), copy.deepcopy(self.c)
        for value in (a, b, c):
            set_realistic_lighting(value, 0.2)
        report = self.verify(a=a, b=b, c=c)
        self.assertEqual(report["final_lighting"]["weight"]["mode"], "constant_weight")

        a, b, c = copy.deepcopy(self.a), copy.deepcopy(self.b), copy.deepcopy(self.c)
        set_realistic_lighting(a, 0.1)
        set_realistic_lighting(b, 0.2)
        set_realistic_lighting(c, 0.2000005)
        report = self.verify(a=a, b=b, c=c)
        self.assertEqual(report["final_lighting"]["weight"]["mode"], "constant_at_c")

        a, b, c = copy.deepcopy(self.a), copy.deepcopy(self.b), copy.deepcopy(self.c)
        set_realistic_lighting(a, 0.2)
        set_realistic_lighting(b, 0.2000005)
        set_realistic_lighting(c, 0.3)
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(a=a, b=b, c=c)

        a, b, c = copy.deepcopy(self.a), copy.deepcopy(self.b), copy.deepcopy(self.c)
        set_realistic_lighting(a, 0.1)
        set_realistic_lighting(b, 0.11)
        set_realistic_lighting(c, 0.2)
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(a=a, b=b, c=c)

    def test_matching_active_stationary_input_is_rejected(self) -> None:
        active = {"generation": 8, "state": "active",
            "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "key": "w", "scancode": 26,
            "duration_ms": 12000, "accepted": {"frame": 50, "continual_ms": 500, "sdl_ms": 100},
            "released": None}
        a, b, c = copy.deepcopy(self.a), copy.deepcopy(self.b), copy.deepcopy(self.c)
        a["input"] = copy.deepcopy(active)
        b["input"] = copy.deepcopy(active)
        c["input"]["generation"] = 9
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(a=a, b=b, c=c)

    def test_prior_stationary_request_is_legal_but_new_uuid_is_not(self) -> None:
        b = copy.deepcopy(self.b)
        a = copy.deepcopy(self.a)
        c = copy.deepcopy(self.c)
        old = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        snapshot = {"generation": 8, "state": "released", "request_id": old, "key": "escape",
            "scancode": 41, "duration_ms": 100,
            "accepted": {"frame": 50, "continual_ms": 500, "sdl_ms": 100},
            "released": {"frame": 51, "continual_ms": 600, "sdl_ms": 200}}
        a["input"] = copy.deepcopy(snapshot)
        b["input"] = copy.deepcopy(snapshot)
        c["input"]["generation"] = 9
        self.verify(a=a, b=b, c=c)
        b["input"]["request_id"] = REQUEST
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(a=a, b=b, c=c)

    def test_every_ab_control_relation_rejects_a_mutation(self) -> None:
        mutations = {
            "session": lambda a, b, c: b["capture"].update(session="f" * 32, token="f" * 32 + ":13"),
            "pid": lambda a, b, c: b["capture"].update(pid=43),
            "level": lambda a, b, c: b["world"].update(level="jupiter"),
            "epoch": lambda a, b, c: b["world"].update(epoch=3),
            "sequence": lambda a, b, c: b["capture"].update(sequence=12, token=f"{SESSION}:12"),
            "duration": lambda a, b, c: b["capture"].update(continual_ms=19000),
            "arm-duration-difference": lambda a, b, c: b["capture"].update(continual_ms=16500),
            "resolution": lambda a, b, c: b["capture"].update(width=932),
            "paused": lambda a, b, c: b["capture"].update(paused=True),
            "scene": lambda a, b, c: b["capture"].update(scene="loading"),
            "stationary-distance": lambda a, b, c: b["view"].update(position=[0.21, 0.0, 0.0]),
            "forward-distance": lambda a, b, c: c["view"].update(position=[0.9, 0.0, 0.0]),
            "forward-distance-upper": lambda a, b, c: c["view"].update(position=[101.0, 0.0, 0.0]),
            "direction-a-b": lambda a, b, c: b["view"].update(direction=[0.052335956, 0.0, 0.998629535]),
            "direction": lambda a, b, c: c["view"].update(direction=[0.052335956, 0.0, 0.998629535]),
            "fov": lambda a, b, c: c["view"].update(fov=68.0),
            "stationary-sector": lambda a, b, c: b["world"].update(sector=116),
            "frame": lambda a, b, c: c["capture"].update(frame=200),
            "continual": lambda a, b, c: c["capture"].update(continual_ms=16000),
            "game-time": lambda a, b, c: c["environment"].update(game_time_ms=160000),
            "cycle": lambda a, b, c: c["environment"].update(cycle="other"),
            "weather": lambda a, b, c: c["environment"].update(weather="rain"),
            "descriptor": lambda a, b, c: c["environment"].update(descriptor1="14:00:00"),
            "weather-fx": lambda a, b, c: c["environment"].update(weather_fx=True),
            "time-factor": lambda a, b, c: c["environment"].update(time_factor=9.0),
            "weight": lambda a, b, c: c["environment"].update(weight=0.05),
            "comparable-delta": lambda a, b, c: c["environment"].update(game_time_ms=500000),
            "stationary-input-generation": lambda a, b, c: b["input"].update(generation=1),
            "request-id": lambda a, b, c: c["input"].update(request_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "input-state": lambda a, b, c: c["input"].update(state="active", released=None),
            "key": lambda a, b, c: c["input"].update(key="s"),
            "scancode": lambda a, b, c: c["input"].update(scancode=25),
            "duration-ms": lambda a, b, c: c["input"].update(duration_ms=11999),
            "generation": lambda a, b, c: c["input"].update(generation=2),
            "accept-after-b": lambda a, b, c: c["input"]["accepted"].update(frame=200),
            "accept-time-after-b": lambda a, b, c: c["input"]["accepted"].update(continual_ms=16000),
            "release-before-c": lambda a, b, c: c["input"]["released"].update(frame=300),
            "release-time-before-c": lambda a, b, c: c["input"]["released"].update(continual_ms=31000),
            "hold": lambda a, b, c: c["input"]["released"].update(sdl_ms=15000),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                a, b, c = copy.deepcopy(self.a), copy.deepcopy(self.b), copy.deepcopy(self.c)
                mutate(a, b, c)
                with self.assertRaises(EVIDENCE.EvidenceError):
                    self.verify(a, b, c)
        b, c = copy.deepcopy(self.b), copy.deepcopy(self.c)
        b["view"]["direction"] = [0.026176948, 0.0, 0.999657325]  # 1.5 degrees from A
        c["view"]["direction"] = [0.052335956, 0.0, 0.998629535]  # 1.5 from B, 3 from A
        with self.assertRaises(EVIDENCE.EvidenceError):
            self.verify(b=b, c=c)


if __name__ == "__main__":
    unittest.main()
