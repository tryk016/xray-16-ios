#!/usr/bin/env python3
"""Host-only mutation tests for the native UI capture evidence packet."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_ui_capture_evidence.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import importlib.util
import json
import copy
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ui_capture_evidence", ROOT / "misc/ios/ui_capture_evidence.py")
assert SPEC and SPEC.loader
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, value: float) -> None:
        self.value += max(value, 0.01)


class UiCaptureEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="openxray-ui-capture-test-")).resolve()
        self.documents = self.temp / "Documents"
        self.documents.mkdir()
        self.root = self.temp / "ui-captures"
        self.root.mkdir()
        self.log = self.documents / "xr_boot.log"
        self.log.write_text("* End of synchronization A[1] R[1]\n* iOS UI state v1 pid=4242 seq=10 frame=100 state=world\n")
        self.clock = Clock()
        self.provenance = {"simulator_udid": "00000000-0000-0000-0000-000000000001", "runtime": "27.0",
                           "renderer": "Apple-Software-Renderer", "git_revision": "a" * 40,
                           "source_tree_sha256": "b" * 64}
        self.observer = ui.UiCaptureObserver(self.documents, self.log, self.root, str(uuid.uuid4()), 4242,
                                              "zaton", self.provenance,
                                              clock=self.clock, sleep=self.clock.sleep)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def metadata(self, token: str, frame: int, *, request_id: str, key: str = "i", scancode: int = 12,
                 duration: int = 300, state: str = "released", accepted_frame: int = 105,
                 released_frame: int = 115, pid: int = 4242, session: str | None = None) -> bytes:
        session_value, sequence = token.split(":")
        value = {
            "schema": "openxray.capture.v2",
            "capture": {"token": token, "session": session or session_value, "sequence": int(sequence), "pid": pid,
                        "frame": frame, "continual_ms": frame * 10, "width": 1864, "height": 860,
                        "period_ms": 5000, "scene": "gameplay", "paused": False},
            "view": {"position": [1.0, 2.0, 3.0], "direction": [0.0, 0.0, 1.0], "fov": 67.5},
            "world": {"level": "zaton", "epoch": 1, "sector": 0}, "environment": None,
            "input": {"generation": 1, "state": state, "request_id": request_id, "key": key,
                      "scancode": scancode, "duration_ms": duration,
                      "accepted": {"frame": accepted_frame, "continual_ms": accepted_frame * 10, "sdl_ms": accepted_frame},
                      "released": None if released_frame < 0 or state != "released" else {"frame": released_frame, "continual_ms": released_frame * 10, "sdl_ms": released_frame}},
        }
        return json.dumps(value, separators=(",", ":")).encode()

    @staticmethod
    def ppm(token: str, fill: int = 17) -> bytes:
        return b"P6\n# openxray-capture-v2 token=" + token.encode() + b"\n1864 860\n255\n" + bytes([fill]) * (1864 * 860 * 3)

    def publish(self, metadata: bytes, ppm: bytes) -> None:
        (self.documents / "xr_shot_meta.txt").write_bytes(metadata)
        (self.documents / "xr_shot.ppm").write_bytes(ppm)

    def transition(self, step: int, request_id: str, *, frame: int = 110, state: str | None = None) -> dict[str, object]:
        state = state or ui.TARGETS[step]
        with self.log.open("a") as output:
            output.write(f"* iOS UI state v1 pid=4242 seq={10 + step} frame={frame} state={state}\n")
        return {"step": step, "request_id": request_id, "key": {1: "i", 3: "p", 4: "e", 6: "m"}[step],
                "state": state, "pid": 4242, "seq": 10 + step, "frame": frame,
                "release_scancode": 12, "hold_ms": 300}

    def full_packet(self) -> list[dict[str, object]]:
        session = "a" * 32
        self.publish(self.metadata(f"{session}:1", 101, request_id=str(uuid.uuid4()), state="released",
                                   accepted_frame=1, released_frame=2), self.ppm(f"{session}:1"))
        for sequence, step in enumerate((1, 3, 4, 6), 2):
            baseline = self.observer.baseline()
            request_id = str(uuid.uuid4())
            transition = self.transition(step, request_id, frame=100 + step * 10)
            self.publish(self.metadata(f"{session}:{sequence}", 200 + step, request_id=request_id,
                                       key=transition["key"], accepted_frame=90 + step * 10,
                                       released_frame=101 + step * 10), self.ppm(f"{session}:{sequence}", sequence))
            self.observer.observe(transition, baseline)
        by_step = {item["step"]: item for item in self.observer.selected}
        complete = []
        for step, (key, state) in enumerate((("i", "inventory"), ("i", "world"), ("p", "pda_tasks"),
                                             ("e", "other"), ("escape", "world"), ("m", "pda_tasks"),
                                             ("escape", "world")), 1):
            item = by_step.get(step)
            marker = item["marker"] if item else {"pid": 4242, "seq": 10 + step, "frame": 100 + step * 10}
            complete.append({"pid": marker["pid"], "seq": marker["seq"], "frame": marker["frame"],
                             "request_id": item["request_id"] if item else str(uuid.uuid4()), "key": key,
                             "state": state, "release_scancode": item["input_timing"]["scancode"] if item else 12})
        return complete

    def test_authoritative_ppm_png_and_full_four_capture_packet(self) -> None:
        complete = self.full_packet()
        self.assertEqual([item["step"] for item in self.observer.selected], [1, 3, 4, 6])
        for item in self.observer.selected:
            png = Path(item["paths"]["png"]).read_bytes()
            _, pixels = ui._parse_ppm(Path(item["paths"]["ppm"]).read_bytes())
            ui.verify_png_bytes(png, item["token"], pixels)
        manifest = ui.finalize_run(self.observer.root, complete, self.observer.selected, 4242,
                                   "zaton", self.provenance)
        value = json.loads(manifest.read_text())
        self.assertEqual(value["schema"], ui.SCHEMA)
        self.assertEqual(len(value["transitions"]), 7)
        self.assertEqual(len(value["captures"]), 4)
        self.assertIs(value["post_stop_revalidated"], True)

    def test_retries_only_pre_target_and_exact_current_active_packets(self) -> None:
        session, request_id = "f" * 32, str(uuid.uuid4())
        transition = self.transition(1, request_id)
        old_id = str(uuid.uuid4())
        pairs = [
            (self.metadata(f"{session}:1", 90, request_id=old_id, accepted_frame=1, released_frame=2), self.ppm(f"{session}:1")),
            (self.metadata(f"{session}:2", 109, request_id=old_id, state="cancelled",
                           accepted_frame=1, released_frame=-1), self.ppm(f"{session}:2")),
            (self.metadata(f"{session}:3", 109, request_id=old_id, accepted_frame=1,
                           released_frame=2), self.ppm(f"{session}:3")),
            (self.metadata(f"{session}:4", 120, request_id=request_id, state="active", accepted_frame=105,
                           released_frame=-1), self.ppm(f"{session}:4")),
            (self.metadata(f"{session}:5", 130, request_id=request_id, accepted_frame=105,
                           released_frame=115), self.ppm(f"{session}:5")),
        ]
        with mock.patch.object(self.observer, "_pair", side_effect=pairs) as pair:
            record = self.observer.observe(transition, f"{session}:1")
        self.assertEqual(pair.call_count, 5)
        self.assertEqual(record["token"], f"{session}:5")

    def test_baseline_retries_only_canonical_loading_and_sequence_is_strict_integer(self) -> None:
        session, request_id = "6" * 32, str(uuid.uuid4())
        loading = json.loads(self.metadata(f"{session}:1", 90, request_id=request_id,
                                           accepted_frame=1, released_frame=2))
        loading["capture"].update(scene="loading", paused=True)
        loading["world"] = None
        loading["environment"] = None
        loading_bytes = json.dumps(loading, separators=(",", ":")).encode()
        gameplay = (self.metadata(f"{session}:2", 101, request_id=request_id,
                                  accepted_frame=1, released_frame=2), self.ppm(f"{session}:2"))
        with mock.patch.object(self.observer, "_pair", side_effect=(
                (loading_bytes, self.ppm(f"{session}:1")), gameplay)) as pair:
            self.assertEqual(self.observer.baseline(), f"{session}:2")
        self.assertEqual(pair.call_count, 2)

        mutations = []
        wrong_pid = copy.deepcopy(loading)
        wrong_pid["capture"]["pid"] = 99
        mutations.append((wrong_pid, "PID"))
        wrong_session = copy.deepcopy(loading)
        wrong_session["capture"]["session"] = "7" * 32
        mutations.append((wrong_session, "binding"))
        bool_sequence = copy.deepcopy(loading)
        bool_sequence["capture"]["sequence"] = True
        mutations.append((bool_sequence, "sequence"))
        zero_sequence = copy.deepcopy(loading)
        zero_sequence["capture"]["sequence"] = 0
        mutations.append((zero_sequence, "sequence"))
        malformed = copy.deepcopy(loading)
        malformed["capture"]["extra"] = 1
        mutations.append((malformed, "canonical"))
        loading_world = copy.deepcopy(loading)
        loading_world["world"] = {"level": "zaton", "epoch": 1, "sector": 0}
        mutations.append((loading_world, "world/environment"))
        for value, message in mutations:
            candidate = (json.dumps(value, separators=(",", ":")).encode(), self.ppm(f"{session}:1"))
            with self.subTest(message=message), mock.patch.object(
                    self.observer, "_pair", side_effect=(candidate, gameplay)) as pair:
                with self.assertRaisesRegex(ui.UiCaptureError, message):
                    self.observer.baseline()
                self.assertEqual(pair.call_count, 1)

        transition = self.transition(1, request_id)
        with mock.patch.object(self.observer, "_pair", return_value=(loading_bytes, self.ppm(f"{session}:1"))) as pair:
            with self.assertRaisesRegex(ui.UiCaptureError, "scene"):
                self.observer.observe(transition, f"{session}:1")
        self.assertEqual(pair.call_count, 1)

    def test_wrong_identity_after_target_is_fatal_without_retry(self) -> None:
        session, request_id = "1" * 32, str(uuid.uuid4())
        transition = self.transition(1, request_id)
        wrong = (self.metadata(f"{session}:2", 130, request_id=request_id, pid=99,
                               accepted_frame=105, released_frame=115), self.ppm(f"{session}:2"))
        valid = (self.metadata(f"{session}:3", 140, request_id=request_id,
                               accepted_frame=105, released_frame=115), self.ppm(f"{session}:3"))
        with mock.patch.object(self.observer, "_pair", side_effect=(wrong, valid)) as pair:
            with self.assertRaisesRegex(ui.UiCaptureError, "PID"):
                self.observer.observe(transition, f"{session}:1")
        self.assertEqual(pair.call_count, 1)

    def test_rejects_pid_token_session_and_input_mutations(self) -> None:
        session, request_id = "b" * 32, str(uuid.uuid4())
        self.publish(self.metadata(f"{session}:1", 101, request_id=request_id, accepted_frame=1, released_frame=2), self.ppm(f"{session}:1"))
        baseline = self.observer.baseline()
        transition = self.transition(1, request_id)
        cases = (
            (self.metadata(f"{session}:1", 120, request_id=request_id, accepted_frame=105, released_frame=115), "stale"),
            (self.metadata(f"c" * 32 + ":2", 120, request_id=request_id, accepted_frame=105, released_frame=115), "another session"),
            (self.metadata(f"{session}:2", 120, request_id=request_id, pid=99, accepted_frame=105, released_frame=115), "PID"),
            (self.metadata(f"{session}:2", 120, request_id=str(uuid.uuid4()), accepted_frame=105, released_frame=115), "UUID"),
            (self.metadata(f"{session}:2", 120, request_id=request_id, key="p", accepted_frame=105, released_frame=115), "UUID/key"),
            (self.metadata(f"{session}:2", 120, request_id=request_id, scancode=13, accepted_frame=105, released_frame=115), "scancode"),
            (self.metadata(f"{session}:2", 120, request_id=request_id, duration=200, accepted_frame=105, released_frame=115), "duration"),
            (self.metadata(f"{session}:2", 120, request_id=request_id, state="cancelled", accepted_frame=105, released_frame=115), "cancelled"),
            (self.metadata(f"{session}:2", 120, request_id=request_id, released_frame=-1), "missing release"),
            (self.metadata(f"{session}:2", 115, request_id=request_id, accepted_frame=105, released_frame=116), "release-after-capture"),
        )
        for metadata, label in cases:
            with self.subTest(label=label):
                self.publish(metadata, self.ppm(f"{session}:2"))
                with self.assertRaises(ui.UiCaptureError):
                    self.observer.observe(transition, baseline)

    def test_accepts_release_in_capture_frame_after_marker(self) -> None:
        session, request_id = "8" * 32, str(uuid.uuid4())
        self.publish(self.metadata(f"{session}:1", 101, request_id=request_id,
                                   accepted_frame=1, released_frame=2), self.ppm(f"{session}:1"))
        baseline = self.observer.baseline()
        transition = self.transition(1, request_id, frame=110)
        self.publish(self.metadata(f"{session}:2", 115, request_id=request_id,
                                   accepted_frame=105, released_frame=115), self.ppm(f"{session}:2"))

        record = self.observer.observe(transition, baseline)

        self.assertEqual(record["input_timing"]["released"],
                         {"frame": 115, "continual_ms": 1150, "sdl_ms": 115})

    def test_rejects_release_in_marker_frame(self) -> None:
        session, request_id = "9" * 32, str(uuid.uuid4())
        self.publish(self.metadata(f"{session}:1", 101, request_id=request_id,
                                   accepted_frame=1, released_frame=2), self.ppm(f"{session}:1"))
        baseline = self.observer.baseline()
        transition = self.transition(1, request_id, frame=110)
        self.publish(self.metadata(f"{session}:2", 115, request_id=request_id,
                                   accepted_frame=105, released_frame=110), self.ppm(f"{session}:2"))

        with self.assertRaisesRegex(ui.UiCaptureError, "ordering"):
            self.observer.observe(transition, baseline)

    def test_rejects_wrong_marker_later_marker_and_mixed_or_invalid_artifacts(self) -> None:
        session, request_id = "d" * 32, str(uuid.uuid4())
        self.publish(self.metadata(f"{session}:1", 101, request_id=request_id, accepted_frame=1, released_frame=2), self.ppm(f"{session}:1"))
        baseline, transition = self.observer.baseline(), self.transition(1, request_id, state="other")
        self.publish(self.metadata(f"{session}:2", 120, request_id=request_id, accepted_frame=105, released_frame=115), self.ppm(f"{session}:2"))
        with self.assertRaises(ui.UiCaptureError):
            self.observer.observe(transition, baseline)
        self.log.write_text("* End of synchronization A[1] R[1]\n* iOS UI state v1 pid=4242 seq=10 frame=100 state=world\n")
        wrong_seq = self.transition(1, request_id)
        wrong_seq["seq"] = 99
        with self.assertRaises(ui.UiCaptureError):
            self.observer.observe(wrong_seq, baseline)
        self.log.write_text("* End of synchronization A[1] R[1]\n* iOS UI state v1 pid=4242 seq=10 frame=100 state=world\n")
        transition = self.transition(1, request_id)
        with self.log.open("a") as output:
            output.write("* iOS UI state v1 pid=4242 seq=12 frame=119 state=world\n")
        with self.assertRaises(ui.UiCaptureError):
            self.observer.observe(transition, baseline)
        self.publish(self.metadata(f"{session}:2", 120, request_id=request_id, accepted_frame=105, released_frame=115), b"P6\n")
        with self.assertRaises(ui.UiCaptureError):
            self.observer.observe(transition, baseline)
        self.publish(self.metadata(f"{session}:2", 120, request_id=request_id, accepted_frame=105, released_frame=115),
                     self.ppm(f"{session}:3"))
        with self.assertRaises(ui.UiCaptureError):
            self.observer.observe(transition, baseline)

    def test_rejects_exact_dimensions_ppm_and_metadata_schema_mutations(self) -> None:
        session, request_id = "2" * 32, str(uuid.uuid4())
        transition = self.transition(1, request_id)
        metadata = self.metadata(f"{session}:2", 130, request_id=request_id,
                                 accepted_frame=105, released_frame=115)
        valid_ppm = self.ppm(f"{session}:2")
        wrong_dimensions = valid_ppm.replace(b"1864 860", b"1863 860", 1)
        metadata_value = json.loads(metadata)
        period = copy.deepcopy(metadata_value)
        period["capture"]["period_ms"] = 4000
        extra = copy.deepcopy(metadata_value)
        extra["capture"]["extra"] = 1
        missing = copy.deepcopy(metadata_value)
        del missing["input"]["generation"]
        no_world = copy.deepcopy(metadata_value)
        no_world["world"] = None
        bad_scene = copy.deepcopy(metadata_value)
        bad_scene["capture"]["scene"] = "menu"
        bad_paused = copy.deepcopy(metadata_value)
        bad_paused["capture"]["paused"] = 0
        bad_generation = copy.deepcopy(metadata_value)
        bad_generation["input"]["generation"] = 0
        bad_level = copy.deepcopy(metadata_value)
        bad_level["world"]["level"] = "jupiter"
        cases = (
            (metadata, wrong_dimensions, "dimensions"),
            (metadata, valid_ppm[:-1], "truncated"),
            (metadata, valid_ppm + b"x", "trailing"),
            (json.dumps(period).encode(), valid_ppm, "period"),
            (json.dumps(extra).encode(), valid_ppm, "extra"),
            (json.dumps(missing).encode(), valid_ppm, "missing"),
            (json.dumps(no_world).encode(), valid_ppm, "world"),
            (json.dumps(bad_scene).encode(), valid_ppm, "scene"),
            (json.dumps(bad_paused).encode(), valid_ppm, "paused"),
            (json.dumps(bad_generation).encode(), valid_ppm, "generation"),
            (json.dumps(bad_level).encode(), valid_ppm, "level"),
        )
        for candidate_metadata, candidate_ppm, label in cases:
            with self.subTest(label=label), mock.patch.object(
                    self.observer, "_pair", return_value=(candidate_metadata, candidate_ppm)):
                with self.assertRaises(ui.UiCaptureError):
                    self.observer.observe(transition, f"{session}:1")

    def test_partial_and_unstable_live_writer_retry_then_accept(self) -> None:
        session, request_id = "3" * 32, str(uuid.uuid4())
        transition = self.transition(1, request_id)
        accepted = (self.metadata(f"{session}:3", 130, request_id=request_id,
                                  accepted_frame=105, released_frame=115), self.ppm(f"{session}:3"))
        with mock.patch.object(self.observer, "_pair", side_effect=(
                ui.RetryableUiCaptureError("partial writer"), accepted)) as pair:
            record = self.observer.observe(transition, f"{session}:1")
        self.assertEqual(pair.call_count, 2)
        self.assertEqual(record["token"], f"{session}:3")

    def test_reused_prior_selected_token_is_fatal(self) -> None:
        session, request_id = "4" * 32, str(uuid.uuid4())
        transition = self.transition(1, request_id)
        self.observer.selected.append({"token": f"{session}:2"})
        candidate = (self.metadata(f"{session}:2", 130, request_id=request_id,
                                   accepted_frame=105, released_frame=115), self.ppm(f"{session}:2"))
        with mock.patch.object(self.observer, "_pair", return_value=candidate):
            with self.assertRaisesRegex(ui.UiCaptureError, "prior selected"):
                self.observer.observe(transition, f"{session}:1")

    def test_online_capture_refuses_preexisting_or_symlink_outputs(self) -> None:
        session, request_id = "5" * 32, str(uuid.uuid4())
        transition = self.transition(1, request_id)
        target = self.temp / "outside"
        target.write_text("unchanged")
        destination = self.observer.root / "raw/step-1-inventory.json"
        destination.symlink_to(target)
        candidate = (self.metadata(f"{session}:2", 130, request_id=request_id,
                                   accepted_frame=105, released_frame=115), self.ppm(f"{session}:2"))
        with mock.patch.object(self.observer, "_pair", return_value=candidate):
            with self.assertRaisesRegex(ui.UiCaptureError, "destination already exists"):
                self.observer.observe(transition, f"{session}:1")
        self.assertEqual(target.read_text(), "unchanged")
        self.assertFalse((self.observer.root / "manifest.json").exists())

    def test_post_stop_artifact_mutation_is_detected_and_no_manifest_preexists(self) -> None:
        self.assertFalse((self.observer.root / "manifest.json").exists())
        transitions = self.full_packet()
        first = self.observer.selected[0]
        for role in ("metadata", "ppm", "png"):
            path = Path(first["paths"][role])
            original = path.read_bytes()
            path.write_bytes(b"corrupt-post-stop")
            with self.subTest(role=role):
                with self.assertRaises(ui.UiCaptureError):
                    ui.finalize_run(self.observer.root, transitions, self.observer.selected, 4242,
                                    "zaton", self.provenance)
                self.assertFalse((self.observer.root / "manifest.json").exists())
            path.write_bytes(original)

    def test_finalizer_rejects_bad_path_size_hash_provenance_and_preexisting_manifest(self) -> None:
        transitions = self.full_packet()
        mutations = []
        escaped = copy.deepcopy(self.observer.selected)
        escaped[0]["paths"]["ppm"] = str(self.temp / "escape.ppm")
        mutations.append((escaped, self.provenance, "path"))
        bad_size = copy.deepcopy(self.observer.selected)
        bad_size[0]["sizes"]["ppm"] += 1
        mutations.append((bad_size, self.provenance, "size"))
        bad_hash = copy.deepcopy(self.observer.selected)
        bad_hash[0]["sha256"]["png"] = "0" * 64
        mutations.append((bad_hash, self.provenance, "hash"))
        bad_schema = copy.deepcopy(self.observer.selected)
        bad_schema[0]["extra"] = True
        mutations.append((bad_schema, self.provenance, "schema"))
        bad_provenance = dict(self.provenance, runtime="26.5")
        mutations.append((self.observer.selected, bad_provenance, "provenance"))
        for captures, provenance, label in mutations:
            with self.subTest(label=label):
                with self.assertRaises(ui.UiCaptureError):
                    ui.finalize_run(self.observer.root, transitions, captures, 4242, "zaton", provenance)
                self.assertFalse((self.observer.root / "manifest.json").exists())
        (self.observer.root / "manifest.json").symlink_to(self.temp / "target")
        with self.assertRaises(ui.UiCaptureError):
            ui.finalize_run(self.observer.root, transitions, self.observer.selected, 4242,
                            "zaton", self.provenance)

    def test_exact_manifest_rollback_never_removes_replacement(self) -> None:
        transitions = self.full_packet()
        publication = ui.finalize_run_with_identity(
            self.observer.root, transitions, self.observer.selected, 4242, "zaton", self.provenance,
        )
        publication.path.unlink()
        publication.path.write_text("replacement", encoding="utf-8")
        self.assertFalse(ui.remove_exact_manifest(publication))
        self.assertEqual(publication.path.read_text(encoding="utf-8"), "replacement")


if __name__ == "__main__":
    unittest.main(verbosity=2)
