#!/usr/bin/env python3
"""Host-only mutation tests for simulator_ui_navigation.py.

No test starts CoreSimulator, invokes xcrun, or accesses a physical device.
The engine side is emulated solely by deterministic local file writes.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "misc/ios/simulator_ui_navigation.py"
SPEC = importlib.util.spec_from_file_location("simulator_ui_navigation", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ui = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ui
SPEC.loader.exec_module(ui)

CAPTURE_MODULE_PATH = REPO_ROOT / "misc/ios/ui_capture_evidence.py"
CAPTURE_SPEC = importlib.util.spec_from_file_location("ui_capture_evidence_for_navigation_tests", CAPTURE_MODULE_PATH)
assert CAPTURE_SPEC is not None and CAPTURE_SPEC.loader is not None
capture_ui = importlib.util.module_from_spec(CAPTURE_SPEC)
CAPTURE_SPEC.loader.exec_module(capture_ui)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.wall_ns = 1_000_000_000

    def monotonic(self) -> float:
        return self.now

    def time_ns(self) -> int:
        return self.wall_ns

    def sleep(self, duration: float) -> None:
        self.now += duration
        self.wall_ns += int(duration * 1_000_000_000)


class EngineFixture:
    """A local file-only model of ACK -> marker -> release for seven requests."""

    def __init__(self, documents: Path, log: Path, clock: FakeClock, *, coalesce_release: bool = False) -> None:
        self.documents = documents
        self.appdata = documents / "_appdata_"
        self.trigger = self.appdata / "autoinput.txt"
        self.ack = self.appdata / "autoinput_ack.txt"
        self.log = log
        self.clock = clock
        self.stage = 0
        self.transition = 0
        self.request_id = ""
        self.pid = 4242
        self.seq = 10
        self.frame = 100
        self.expected = [step.expected_state for step in ui.SEQUENCE]
        self.coalesce_release = coalesce_release

    def append(self, line: str) -> None:
        with self.log.open("a", encoding="utf-8") as output:
            output.write(line + "\n")

    def accept(self, request_id: str, key: str = "i", hold_ms: int = 100) -> None:
        self.ack.write_text(f"{request_id} accepted\n", encoding="ascii")
        self.append(f"* iOS diag: autoinput request {request_id} press/hold '{key}' (scancode 12) for {hold_ms} ms")

    def hook(self) -> None:
        if not self.trigger.exists():
            return
        parts = self.trigger.read_text(encoding="ascii").split()
        self.request_id = parts[1]
        if self.stage == 0:
            self.accept(self.request_id, parts[2], int(parts[3]))
            self.stage = 1
        elif self.stage == 1:
            self.seq += 1
            self.frame += 10
            self.append(f"* iOS UI state v1 pid={self.pid} seq={self.seq} frame={self.frame} state={self.expected[self.transition]}")
            if self.coalesce_release:
                self.append(f"* iOS diag: autoinput request {self.request_id} released scancode 12")
                self.trigger.unlink()
                self.stage = 0
                self.transition += 1
            else:
                self.stage = 2
        elif self.stage == 2:
            self.append(f"* iOS diag: autoinput request {self.request_id} released scancode 12")
            self.trigger.unlink()
            self.stage = 0
            self.transition += 1


class UiNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="openxray-ui-nav-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.documents = self.root / "Documents"
        self.appdata = self.documents / "_appdata_"
        self.appdata.mkdir(parents=True)
        self.log = self.documents / "xr_boot.log"
        self.log.write_text(
            "* iOS UI state v1 pid=4242 seq=10 frame=100 state=world\n"
            "* End of synchronization A[1] R[1]\n", encoding="utf-8",
        )
        self.clock = FakeClock()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def controller(self, hook=None, **kwargs):
        values = {
            "hold_ms": 100,
            "expected_pid": 4242,
            "timeout_seconds": 1.0,
            "dwell_seconds": 0.02,
            "poll_seconds": 0.01,
            "uuid_factory": self.uuid_factory(),
            "clock": self.clock.monotonic,
            "wall_clock_ns": self.clock.time_ns,
            "sleep": self.clock.sleep,
            "liveness": lambda _pid: True,
            "poll_hook": hook,
        }
        values.update(kwargs)
        return ui.UiNavigationController(self.documents, self.log, **values)

    @staticmethod
    def uuid_factory():
        values = iter(f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 20))
        return lambda: next(values)

    def full_fixture(self) -> EngineFixture:
        return EngineFixture(self.documents, self.log, self.clock)

    def capture_closeout_fixture(self):
        fixture = self.full_fixture()
        result = self.controller(fixture.hook).run()
        snapshot = self.root / "capture-snapshot.log"
        report = self.root / "capture-pre-report.json"
        run_root = self.root / "ui-captures" / "00000000-0000-4000-8000-000000000001"
        run_root.mkdir(parents=True)

        class CaptureReport:
            root = run_root
            expected_level = "zaton"
            selected: list[dict[str, object]] = []
            provenance = {
                "simulator_udid": "00000000-0000-0000-0000-000000000001",
                "runtime": "27.0",
                "renderer": "Apple-Software-Renderer",
                "git_revision": "a" * 40,
                "source_tree_sha256": "b" * 64,
            }

        source = ui.read_stable_regular_nofollow(self.log, "runtime log")
        ui.write_snapshot(snapshot, source)
        ui.write_pre_termination_report(report, source, result, 4242, CaptureReport())
        return snapshot, report, run_root / "manifest.json"

    def assert_fails(self, expression, message: str) -> None:
        with self.assertRaisesRegex(ui.NavigationError, message):
            expression()

    def test_full_sequence_is_exact_and_finalization_is_fresh(self) -> None:
        fixture = self.full_fixture()
        result = self.controller(fixture.hook).run()
        self.assertEqual([item.key for item in result], ["i", "i", "p", "e", "escape", "m", "escape"])
        self.assertEqual([item.state for item in result], [step.expected_state for step in ui.SEQUENCE])
        self.assertEqual([item.seq for item in result], list(range(11, 18)))
        snapshot = self.root / "snapshot.log"
        report = self.root / "pre-report.json"
        final_report = self.root / "final-report.json"
        source = ui.read_stable_regular_nofollow(self.log, "runtime log")
        ui.write_snapshot(snapshot, source)
        ui.write_pre_termination_report(report, source, result, 4242)
        self.assertEqual(
            json.loads(report.read_text(encoding="utf-8"))["scope"],
            ui.SEMANTIC_SCOPE,
        )
        ui.finalize_after_termination(self.log, snapshot, report, final_report)
        document = json.loads(final_report.read_text(encoding="utf-8"))
        self.assertEqual(document["result"], "PASS")
        self.assertEqual(document["scope"], ui.SEMANTIC_SCOPE)
        self.assert_fails(lambda: ui.write_snapshot(snapshot, source), "must not pre-exist")

    def test_native_capture_waits_before_the_next_request_and_keeps_all_seven_steps(self) -> None:
        fixture = self.full_fixture()
        events: list[tuple[str, int]] = []

        class PartialWriter:
            def __init__(self, outer) -> None:
                self.outer = outer
                self.selected: list[dict[str, object]] = []

            def baseline(self) -> str:
                step = len(self.selected)
                events.append(("baseline", step))
                return f"{'a' * 32}:{step + 1}"

            def observe(self, transition: dict[str, object], baseline: str) -> None:
                # A following request would recreate this trigger.  Holding the
                # controlled writer here proves the controller cannot overlap it.
                self.outer.assertFalse((self.outer.appdata / "autoinput.txt").exists())
                events.append(("observe", int(transition["step"])))
                self.outer.clock.sleep(0.10)
                self.selected.append({"step": transition["step"], "token": baseline})

        writer = PartialWriter(self)
        result = self.controller(fixture.hook, capture_observer=writer).run()
        self.assertEqual(len(result), 7)
        self.assertEqual(events, [("baseline", 0), ("observe", 1), ("baseline", 1), ("observe", 3),
                                  ("baseline", 2), ("observe", 4), ("baseline", 3), ("observe", 6)])

    def test_capture_closeout_preexisting_final_report_never_publishes_manifest(self) -> None:
        snapshot, report, manifest = self.capture_closeout_fixture()
        finalize = mock.Mock()
        with mock.patch.dict(sys.modules, {"ui_capture_evidence": capture_ui}), mock.patch.object(
                capture_ui, "finalize_run_with_identity", finalize):
            existing = self.root / "preexisting-final.json"
            existing.write_text("sentinel", encoding="utf-8")
            with self.assertRaisesRegex(ui.NavigationError, "must not pre-exist"):
                ui.finalize_after_termination(self.log, snapshot, report, existing)
            self.assertEqual(existing.read_text(encoding="utf-8"), "sentinel")
            self.assertFalse(manifest.exists())

            target = self.root / "outside-final-target"
            target.write_text("unchanged", encoding="utf-8")
            symlink = self.root / "symlink-final.json"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(ui.NavigationError, "must not pre-exist"):
                ui.finalize_after_termination(self.log, snapshot, report, symlink)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")
            self.assertFalse(manifest.exists())
        finalize.assert_not_called()

    def test_capture_closeout_report_failure_rolls_back_only_exact_manifest(self) -> None:
        snapshot, report, manifest = self.capture_closeout_fixture()

        def publish_manifest(*_args, **_kwargs):
            device, inode = capture_ui._new_file(manifest, b"owned manifest\n", "test manifest")
            return capture_ui.ManifestPublication(manifest, device, inode)

        with mock.patch.dict(sys.modules, {"ui_capture_evidence": capture_ui}), mock.patch.object(
                capture_ui, "finalize_run_with_identity", side_effect=publish_manifest), mock.patch.object(
                ui, "write_new_regular", side_effect=OSError("forced final-report failure")):
            final_report = self.root / "failed-final.json"
            with self.assertRaisesRegex(OSError, "forced final-report failure"):
                ui.finalize_after_termination(self.log, snapshot, report, final_report)
            self.assertFalse(final_report.exists())
            self.assertFalse(manifest.exists())

        def replace_then_fail(*_args, **_kwargs):
            manifest.unlink()
            manifest.write_text("replacement", encoding="utf-8")
            raise ui.NavigationError("forced replacement failure")

        with mock.patch.dict(sys.modules, {"ui_capture_evidence": capture_ui}), mock.patch.object(
                capture_ui, "finalize_run_with_identity", side_effect=publish_manifest), mock.patch.object(
                ui, "write_new_regular", side_effect=replace_then_fail):
            final_report = self.root / "replacement-failed-final.json"
            with self.assertRaisesRegex(ui.NavigationError, "forced replacement failure"):
                ui.finalize_after_termination(self.log, snapshot, report, final_report)
            self.assertFalse(final_report.exists())
            self.assertEqual(manifest.read_text(encoding="utf-8"), "replacement")

    def test_verify_log_is_read_only_and_checks_whole_history(self) -> None:
        fixture = self.full_fixture()
        result = self.controller(fixture.hook).run()
        before = self.log.read_bytes()
        with mock.patch.object(sys, "argv", ["oracle", "verify-log", "--log", str(self.log)]):
            self.assertEqual(ui.main(), 0)
        self.assertEqual(self.log.read_bytes(), before)
        self.assertEqual(len(result), 7)

    def test_accepts_ordered_marker_and_release_in_one_poll_snapshot(self) -> None:
        fixture = EngineFixture(self.documents, self.log, self.clock, coalesce_release=True)
        result = self.controller(fixture.hook).run()
        self.assertEqual([item.seq for item in result], list(range(11, 18)))

    def test_rejects_initial_state_other_than_world(self) -> None:
        self.log.write_text("* iOS UI state v1 pid=4242 seq=10 frame=100 state=inventory\n* End of synchronization A[1] R[1]\n")
        self.assert_fails(lambda: self.controller(self.full_fixture().hook).run(), "must start in world")

    def test_rejects_wrong_expected_pid_at_baseline(self) -> None:
        self.assert_fails(
            lambda: self.controller(self.full_fixture().hook, expected_pid=99).run(),
            "does not match expected PID 99",
        )

    def test_rejects_dead_process_at_baseline(self) -> None:
        self.assert_fails(
            lambda: self.controller(self.full_fixture().hook, liveness=lambda _pid: False).run(),
            "not live during baseline",
        )

    def test_rejects_preexisting_ack_and_trigger_hazards(self) -> None:
        (self.appdata / "autoinput_ack.txt").write_text("stale\n")
        self.assert_fails(lambda: self.controller().run(), "initial autoinput ACK")
        (self.appdata / "autoinput_ack.txt").unlink()
        (self.appdata / "autoinput.txt").symlink_to(self.root / "target")
        self.assert_fails(lambda: self.controller().run(), "initial autoinput trigger")

    def test_rejects_report_and_output_symlinks(self) -> None:
        fixture = self.full_fixture()
        result = self.controller(fixture.hook).run()
        target = self.root / "target"
        target.write_text("target")
        report = self.root / "report.json"
        report.symlink_to(target)
        source = ui.read_stable_regular_nofollow(self.log, "runtime log")
        self.assert_fails(lambda: ui.write_pre_termination_report(report, source, result, 4242), "must not pre-exist")

    def test_rejects_log_symlink(self) -> None:
        target = self.root / "actual.log"
        shutil.copy2(self.log, target)
        self.log.unlink()
        self.log.symlink_to(target)
        self.assert_fails(lambda: self.controller().run(), "runtime log must be a regular non-symlink")

    def test_live_log_reader_accepts_append_during_read_or_stats(self) -> None:
        for timing in ("read", "stats"):
            with self.subTest(timing=timing):
                self.setUp_clean_log()
                prefix = self.log.read_bytes()
                appended = False
                if timing == "read":
                    real_read = ui.os.read

                    def append_on_first_read(descriptor: int, length: int) -> bytes:
                        nonlocal appended
                        if not appended:
                            appended = True
                            with self.log.open("ab") as output:
                                output.write(b"live append during read\\n")
                        return real_read(descriptor, length)

                    patcher = mock.patch.object(ui.os, "read", side_effect=append_on_first_read)
                else:
                    real_fstat = ui.os.fstat
                    fstat_calls = 0

                    def append_between_stats(descriptor: int):
                        nonlocal appended, fstat_calls
                        result = real_fstat(descriptor)
                        fstat_calls += 1
                        if fstat_calls == 2:
                            appended = True
                            with self.log.open("ab") as output:
                                output.write(b"live append between stats\\n")
                        return result

                    patcher = mock.patch.object(ui.os, "fstat", side_effect=append_between_stats)
                with patcher:
                    snapshot = ui.read_live_appendable_regular_nofollow(self.log, "runtime log")
                self.assertTrue(appended)
                self.assertEqual(snapshot.data, prefix)
                self.assertEqual(snapshot.size, len(prefix))

    def test_live_log_reader_accepts_append_between_lstat_and_open(self) -> None:
        prefix = self.log.read_bytes()
        real_open = ui.os.open
        appended = False

        def append_before_guard_open(path: Path, flags: int, *args: int) -> int:
            nonlocal appended
            if not appended and os.fspath(path) == os.fspath(self.log):
                appended = True
                writer = real_open(self.log, os.O_WRONLY | os.O_APPEND)
                try:
                    os.write(writer, b"live append before open\\n")
                finally:
                    os.close(writer)
            return real_open(path, flags, *args)

        with mock.patch.object(ui.os, "open", side_effect=append_before_guard_open):
            snapshot = ui.read_live_appendable_regular_nofollow(self.log, "runtime log")
        self.assertTrue(appended)
        self.assertEqual(snapshot.data, prefix + b"live append before open\\n")
        self.assertEqual(snapshot.size, len(snapshot.data))

    def test_live_log_reader_rejects_rotation_shrink_and_rewrite(self) -> None:
        for mode in ("rotation", "shrink", "rewrite"):
            with self.subTest(mode=mode):
                self.setUp_clean_log()
                real_read = ui.os.read
                original = self.log.read_bytes()
                changed = False

                def mutate_on_first_read(descriptor: int, length: int) -> bytes:
                    nonlocal changed
                    if not changed:
                        changed = True
                        if mode == "rotation":
                            self.log.replace(self.root / "rotated.log")
                            self.log.write_bytes(b"replacement\\n")
                        elif mode == "shrink":
                            self.log.write_bytes(b"")
                        else:
                            self.log.write_bytes(b"x" * len(original))
                    return real_read(descriptor, length)

                with mock.patch.object(ui.os, "read", side_effect=mutate_on_first_read):
                    with self.assertRaises(ui.NavigationError):
                        ui.read_live_appendable_regular_nofollow(self.log, "runtime log")

    def test_rejects_ack_symlink_after_request(self) -> None:
        def hook():
            trigger = self.appdata / "autoinput.txt"
            if trigger.exists() and not (self.appdata / "autoinput_ack.txt").exists():
                (self.appdata / "autoinput_ack.txt").symlink_to(self.root / "target")
        (self.root / "target").write_text("x")
        self.assert_fails(lambda: self.controller(hook).run(), "autoinput ACK must be a regular non-symlink")

    def test_rejects_stale_wrong_or_malformed_ack(self) -> None:
        def malformed():
            if (self.appdata / "autoinput.txt").exists():
                (self.appdata / "autoinput_ack.txt").write_text("not-an-ack\n")
        self.assert_fails(lambda: self.controller(malformed).run(), "malformed autoinput ACK")

        self.setUp_clean_log()
        def wrong():
            if (self.appdata / "autoinput.txt").exists():
                (self.appdata / "autoinput_ack.txt").write_text("00000000-0000-4000-8000-999999999999 accepted\n")
        self.assert_fails(lambda: self.controller(wrong).run(), "UUID does not match")

    def setUp_clean_log(self) -> None:
        for item in self.appdata.iterdir():
            item.unlink()
        self.log.write_text(
            "* iOS UI state v1 pid=4242 seq=10 frame=100 state=world\n"
            "* End of synchronization A[1] R[1]\n",
        )
        self.clock = FakeClock()

    def accept_from_trigger(self) -> str:
        parts = (self.appdata / "autoinput.txt").read_text().split()
        request_id, key, held_ms = parts[1], parts[2], int(parts[3])
        (self.appdata / "autoinput_ack.txt").write_text(f"{request_id} accepted\n")
        with self.log.open("a") as output:
            output.write(f"* iOS diag: autoinput request {request_id} press/hold '{key}' (scancode 12) for {held_ms} ms\n")
        return request_id

    def test_rejects_marker_or_release_before_ack(self) -> None:
        def marker_first():
            trigger = self.appdata / "autoinput.txt"
            if trigger.exists():
                with self.log.open("a") as output:
                    output.write("* iOS UI state v1 pid=4242 seq=11 frame=110 state=inventory\n")
        self.assert_fails(lambda: self.controller(marker_first).run(), "before a matching accepted ACK")

        self.setUp_clean_log()
        def release_first():
            trigger = self.appdata / "autoinput.txt"
            if trigger.exists():
                request_id = trigger.read_text().split()[1]
                with self.log.open("a") as output:
                    output.write(f"* iOS diag: autoinput request {request_id} released scancode 12\n")
        self.assert_fails(lambda: self.controller(release_first).run(), "before a matching accepted ACK")

    def test_rejects_wrong_state_duplicate_skipped_and_pid_changed_markers(self) -> None:
        for suffix, message in (
            ("* iOS UI state v1 pid=4242 seq=11 frame=110 state=pda_tasks\n", "expected inventory"),
            ("* iOS UI state v1 pid=4242 seq=12 frame=110 state=inventory\n", "sequence is not exact"),
            ("* iOS UI state v1 pid=99 seq=11 frame=110 state=inventory\n", "PID changed"),
            ("* iOS UI state v1 pid=4242 seq=11 frame=100 state=inventory\n", "frame did not increase"),
        ):
            with self.subTest(message=message):
                self.setUp_clean_log()
                stage = {"value": 0}
                def hook(suffix=suffix):
                    trigger = self.appdata / "autoinput.txt"
                    if not trigger.exists():
                        return
                    request_id = trigger.read_text().split()[1]
                    if stage["value"] == 0:
                        request_id = self.accept_from_trigger()
                        stage["value"] = 1
                    else:
                        with self.log.open("a") as output:
                            output.write(suffix)
                self.assert_fails(lambda: self.controller(hook).run(), message)

    def test_rejects_malformed_marker_and_failure_marker(self) -> None:
        self.log.write_text("* iOS UI state v1 pid=4242 seq=10 frame=100 state=world extra\n* End of synchronization A[1] R[1]\n")
        self.assert_fails(lambda: self.controller().run(), "malformed UI state marker")
        self.setUp_clean_log()
        with self.log.open("a") as output:
            output.write("FATAL: test failure\n")
        self.assert_fails(lambda: self.controller().run(), "forbidden failure marker")

    def test_rejects_release_before_marker_duplicate_release_and_dwell_marker(self) -> None:
        for mode, message in (("release", "before the expected"), ("duplicate", "duplicate autoinput release"), ("dwell", "unexpected extra UI state marker")):
            with self.subTest(mode=mode):
                self.setUp_clean_log()
                stage = {"value": 0}
                def hook(mode=mode):
                    trigger = self.appdata / "autoinput.txt"
                    if not trigger.exists():
                        return
                    request_id = trigger.read_text().split()[1]
                    if stage["value"] == 0:
                        request_id = self.accept_from_trigger()
                    elif stage["value"] == 1:
                        with self.log.open("a") as output:
                            if mode == "release":
                                output.write(f"* iOS diag: autoinput request {request_id} released scancode 12\n")
                            else:
                                output.write("* iOS UI state v1 pid=4242 seq=11 frame=110 state=inventory\n")
                    elif stage["value"] == 2:
                        with self.log.open("a") as output:
                            output.write(f"* iOS diag: autoinput request {request_id} released scancode 12\n")
                    elif stage["value"] == 3:
                        with self.log.open("a") as output:
                            if mode == "duplicate":
                                output.write(f"* iOS diag: autoinput request {request_id} released scancode 12\n")
                            else:
                                output.write("* iOS UI state v1 pid=4242 seq=12 frame=120 state=world\n")
                    stage["value"] += 1
                self.assert_fails(lambda: self.controller(hook).run(), message)

    def test_rejects_log_rotation_truncation_and_rewrite(self) -> None:
        for mode, message in (("rotation", "rotated"), ("truncation", "truncated"), ("rewrite", "rewritten")):
            with self.subTest(mode=mode):
                self.setUp_clean_log()
                stage = {"value": 0}
                def hook(mode=mode):
                    if not (self.appdata / "autoinput.txt").exists() or stage["value"]:
                        return
                    stage["value"] = 1
                    if mode == "rotation":
                        replacement = self.root / "replacement.log"
                        replacement.write_text(self.log.read_text())
                        replacement.replace(self.log)
                    elif mode == "truncation":
                        self.log.write_text("")
                    else:
                        self.log.write_text("* iOS UI state v1 pid=4242 seq=10 frame=100 state=world rewritten\n")
                self.assert_fails(lambda: self.controller(hook).run(), message)

    def test_timeout_bounds_missing_events(self) -> None:
        self.assert_fails(lambda: self.controller().run(), "timed out waiting for ACK")

    def test_rejects_process_death_after_marker_before_release(self) -> None:
        fixture = self.full_fixture()
        alive = {"value": True}
        def hook():
            if fixture.stage == 2:
                alive["value"] = False
                return
            fixture.hook()
        self.assert_fails(
            lambda: self.controller(hook, liveness=lambda _pid: alive["value"]).run(),
            "not live during transition i pending",
        )

    def test_rejects_process_death_during_release_dwell(self) -> None:
        fixture = self.full_fixture()
        alive = {"value": True}
        def hook():
            if fixture.stage == 0 and fixture.transition == 1:
                alive["value"] = False
                return
            fixture.hook()
        self.assert_fails(
            lambda: self.controller(hook, liveness=lambda _pid: alive["value"]).run(),
            "not live during release dwell",
        )

    def test_cli_requires_explicit_integration_and_report_is_new(self) -> None:
        report = self.root / "report.json"
        args = ui.parser().parse_args([
            "run", "--simulator-udid", "00000000-0000-0000-0000-000000000001",
            "--expected-pid", "4242", "--documents", str(self.documents), "--snapshot", str(self.root / "snapshot.log"), "--report", str(report),
        ])
        self.assert_fails(lambda: ui.command_run(args), "requires explicit --integration")
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                ui.parser().parse_args([
                    "run", "--integration", "--simulator-udid", "00000000-0000-0000-0000-000000000001",
                    "--documents", str(self.documents), "--snapshot", str(self.root / "required-snapshot.log"),
                    "--report", str(self.root / "required-report.json"),
                ])
        invalid_pid = ui.parser().parse_args([
            "run", "--integration", "--simulator-udid", "00000000-0000-0000-0000-000000000001",
            "--expected-pid", "0", "--documents", str(self.documents),
            "--snapshot", str(self.root / "zero-snapshot.log"), "--report", str(self.root / "zero-report.json"),
        ])
        self.assert_fails(lambda: ui.command_run(invalid_pid), "PID must be positive")
        report.write_text("old")
        source = ui.read_stable_regular_nofollow(self.log, "runtime log")
        self.assert_fails(lambda: ui.write_pre_termination_report(report, source, (), 4242), "must not pre-exist")

    def test_run_parser_defaults_to_isolated_simulator_bundle(self) -> None:
        args = ui.parser().parse_args([
            "run", "--integration", "--simulator-udid", "00000000-0000-0000-0000-000000000001",
            "--expected-pid", "4242", "--documents", str(self.documents),
            "--snapshot", str(self.root / "default-snapshot.log"),
            "--report", str(self.root / "default-report.json"),
        ])
        self.assertEqual(args.bundle_id, "io.github.tryk016.openxray")

    def test_rejects_missing_anchor_and_post_termination_mutation(self) -> None:
        self.log.write_text("* iOS UI state v1 pid=4242 seq=10 frame=100 state=world\n")
        self.assert_fails(lambda: self.controller().run(), "synchronization anchor")
        self.setUp_clean_log()
        fixture = self.full_fixture()
        result = self.controller(fixture.hook).run()
        snapshot = self.root / "snapshot.log"
        report = self.root / "pre-report.json"
        source = ui.read_stable_regular_nofollow(self.log, "runtime log")
        ui.write_snapshot(snapshot, source)
        ui.write_pre_termination_report(report, source, result, 4242)
        with self.log.open("a") as output:
            output.write("* iOS UI state v1 pid=4242 seq=18 frame=180 state=inventory\n")
        self.assert_fails(
            lambda: ui.finalize_after_termination(self.log, snapshot, report, self.root / "final.json"),
            "unexpected UI state after the final dwell",
        )

    def test_rejects_pre_termination_report_with_wrong_scope(self) -> None:
        fixture = self.full_fixture()
        result = self.controller(fixture.hook).run()
        snapshot = self.root / "scope-snapshot.log"
        report = self.root / "scope-pre-report.json"
        source = ui.read_stable_regular_nofollow(self.log, "runtime log")
        ui.write_snapshot(snapshot, source)
        ui.write_pre_termination_report(report, source, result, 4242)
        document = json.loads(report.read_text(encoding="utf-8"))
        document["scope"] = "semantic-ui-navigation-only; stale scope"
        report.write_text(json.dumps(document), encoding="utf-8")
        self.assert_fails(
            lambda: ui.finalize_after_termination(self.log, snapshot, report, self.root / "scope-final.json"),
            "invalid schema",
        )

    def test_post_termination_rejects_inode_truncation_and_rewrite(self) -> None:
        for mode, message in (("inode", "changed inode"), ("truncation", "truncated or rewritten"), ("rewrite", "truncated or rewritten")):
            with self.subTest(mode=mode):
                self.setUp_clean_log()
                fixture = self.full_fixture()
                result = self.controller(fixture.hook).run()
                snapshot = self.root / f"{mode}-snapshot.log"
                report = self.root / f"{mode}-pre-report.json"
                source = ui.read_stable_regular_nofollow(self.log, "runtime log")
                ui.write_snapshot(snapshot, source)
                ui.write_pre_termination_report(report, source, result, 4242)
                if mode == "inode":
                    replacement = self.root / f"{mode}-replacement.log"
                    replacement.write_bytes(source.data)
                    replacement.replace(self.log)
                elif mode == "truncation":
                    self.log.write_bytes(source.data[:-1])
                else:
                    self.log.write_bytes(source.data.replace(b"state=world", b"state=other", 1))
                self.assert_fails(
                    lambda: ui.finalize_after_termination(self.log, snapshot, report, self.root / f"{mode}-final.json"),
                    message,
                )

    def test_post_termination_rejects_unterminated_fatal_and_ui_marker(self) -> None:
        payloads = (
            (b"FATAL: late crash", "fatal"),
            (b"* iOS UI state v1 pid=4242 seq=17 frame=170 state=inventory", "marker"),
        )
        for payload, name in payloads:
            with self.subTest(name=name):
                self.setUp_clean_log()
                fixture = self.full_fixture()
                result = self.controller(fixture.hook).run()
                snapshot = self.root / f"unterminated-{name}-snapshot.log"
                report = self.root / f"unterminated-{name}-pre-report.json"
                source = ui.read_stable_regular_nofollow(self.log, "runtime log")
                ui.write_snapshot(snapshot, source)
                ui.write_pre_termination_report(report, source, result, 4242)
                with self.log.open("ab") as output:
                    output.write(payload)
                self.assert_fails(
                    lambda: ui.finalize_after_termination(
                        self.log, snapshot, report, self.root / f"unterminated-{name}-final.json",
                    ),
                    "unterminated trailing record",
                )

    def test_rejects_malformed_or_mismatched_press_log(self) -> None:
        for line, message in (
            ("* iOS diag: autoinput request {id} press bad\n", "malformed autoinput press log"),
            ("* iOS diag: autoinput request {id} press/hold 'p' (scancode 12) for 100 ms\n", "does not match the current request"),
        ):
            with self.subTest(line=line):
                self.setUp_clean_log()
                stage = {"value": 0}
                def hook(line=line):
                    trigger = self.appdata / "autoinput.txt"
                    if not trigger.exists() or stage["value"]:
                        return
                    request_id = trigger.read_text().split()[1]
                    (self.appdata / "autoinput_ack.txt").write_text(f"{request_id} accepted\n")
                    with self.log.open("a") as output:
                        output.write(line.format(id=request_id))
                    stage["value"] = 1
                self.assert_fails(lambda: self.controller(hook).run(), message)

    def test_simctl_resolution_is_explicit_and_mocked(self) -> None:
        container = self.root / "container"
        (container / "Documents").mkdir(parents=True)
        completed = subprocess.CompletedProcess(("xcrun",), 0, str(container).encode(), b"")
        with mock.patch.object(ui.subprocess, "run", return_value=completed) as mocked:
            resolved = ui.resolve_simulator_documents("00000000-0000-0000-0000-000000000001", "bundle")
        self.assertEqual(resolved, container / "Documents")
        mocked.assert_called_once()

    def test_rejects_invalid_limits_uuid_and_simulator_id(self) -> None:
        self.assert_fails(lambda: self.controller(hold_ms=1), "hold duration")
        self.assert_fails(lambda: ui.validate_request_id("not-a-uuid"), "request UUID")
        self.assert_fails(lambda: ui.resolve_simulator_documents("not-a-udid", "bundle"), "invalid Simulator UDID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
