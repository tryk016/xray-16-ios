#!/usr/bin/env python3
"""Host-only mutation coverage for capture-v2 and stationary/forward A/B."""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import shutil
import tempfile
import unittest
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
