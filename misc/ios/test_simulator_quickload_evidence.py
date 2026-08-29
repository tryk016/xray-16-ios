#!/usr/bin/env python3
"""Host-only mutation tests for isolated Simulator QuickSave/QuickLoad evidence."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_simulator_quickload_evidence.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "misc/ios/simulator_quickload_evidence.py"
sys.path.insert(0, str(SCRIPT.parent))
import retail_simulator_guard as CLONE_GUARD


def load_module():
    spec = importlib.util.spec_from_file_location("simulator_quickload_evidence", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load QuickLoad evidence module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()
SESSION = "a" * 32


def marker(pid: int, epoch: int, frame: int, trigger: str, sector: int = 115, method: str = "exact") -> str:
    return (
        f"* iOS sector startup v1 pid={pid} epoch={epoch} frame={frame} level=zaton "
        f"trigger={trigger} status=resolved method={method} sector={sector} "
        "camera=(1.000,2.000,3.000) probe=(1.000,2.000,3.000) radius=0.0"
    )


def capture_value(pid: int, sequence: int, frame: int, epoch: int, sector: int, input_value: dict) -> dict:
    return {
        "schema": "openxray.capture.v2",
        "capture": {"token": f"{SESSION}:{sequence}", "session": SESSION, "sequence": sequence,
                    "pid": pid, "frame": frame, "continual_ms": frame * 10, "width": 1864, "height": 860,
                    "period_ms": 5000, "scene": "gameplay", "paused": False},
        "view": {"position": [0, 0, 0], "direction": [0, 0, 1], "fov": 67.5},
        "world": {"level": "zaton", "epoch": epoch, "sector": sector},
        "environment": {"game_time_ms": frame * 10, "day_time_s": 43200, "time_factor": 1,
                        "cycle": "default", "weather": "default", "weather_fx": False,
                        "descriptor0": "12:00:00", "descriptor1": "13:00:00", "weight": 0.5,
                        "ambient": [0.1, 0.1, 0.1], "hemi": [0.2, 0.2, 0.2, 1],
                        "sun": [0.3, 0.3, 0.3], "sun_direction": [0, -1, 0]},
        "input": input_value,
    }


def none_input() -> dict:
    return {"generation": 0, "state": "none", "request_id": None, "key": None, "scancode": None,
            "duration_ms": 0, "accepted": None, "released": None}


def released_input(request_id: str, key: str, scancode: int, accept: int, release: int) -> dict:
    return {"generation": 1 if key == "f5" else 2, "state": "released", "request_id": request_id,
            "key": key, "scancode": scancode, "duration_ms": 100,
            "accepted": {"frame": accept, "continual_ms": accept * 10, "sdl_ms": accept * 10},
            "released": {"frame": release, "continual_ms": release * 10, "sdl_ms": release * 10}}


def ppm(token: str) -> bytes:
    return f"P6\n# openxray-capture-v2 token={token}\n1864 860\n255\n".encode("ascii") + b"\0" * (1864 * 860 * 3)


def engine_path(path: Path) -> str:
    return str(path.absolute()).replace("/", "\\")


class QuickLoadEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name).resolve()
        self.docs = self.path / "Documents"
        self.appdata = self.docs / "_appdata_"
        self.saves = self.appdata / "savedgames"
        self.saves.mkdir(parents=True)
        self.original = self.saves / "mobile user - beginning of the game.scop"
        self.original.write_bytes(b"original-save")
        self.original.chmod(0o600)
        self.staged = self.path / "staged-files.tsv"
        self.staged.write_text(
            "sha256\tbytes\tpath\n"
            f"{hashlib.sha256(self.original.read_bytes()).hexdigest()}\t{self.original.stat().st_size}\t_appdata_/savedgames/{self.original.name}\n",
            encoding="utf-8",
        )
        self.log = self.docs / "xr_boot.log"
        self.pid = os.getpid()
        self.log.write_text(marker(self.pid, 1, 10, "level_load") + "\n", encoding="utf-8")
        self.meta, self.shot = self.docs / "xr_shot_meta.txt", self.docs / "xr_shot.ppm"
        self.root = self.path / "evidence"
        self.root.mkdir()
        self.root.chmod(0o700)
        self.qsave = "11111111-1111-4111-8111-111111111111"
        self.qload = "22222222-2222-4222-8222-222222222222"
        self.simulator_uuid = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
        self._write_capture(capture_value(self.pid, 1, 20, 1, 115, none_input()))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_capture(self, value: dict) -> None:
        self.meta.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        self.shot.write_bytes(ppm(value["capture"]["token"]))

    def _run_args(self) -> argparse.Namespace:
        return argparse.Namespace(documents=str(self.docs), log=str(self.log), expected_pid=self.pid,
                                  simulator_uuid=self.simulator_uuid,
                                  staged_manifest=str(self.staged), root=str(self.root),
                                  original_save=str(self.original), timeout=1.0, poll=0.01)

    @staticmethod
    def _save_metadata_record(**changes: object) -> dict[str, object]:
        record: dict[str, object] = {
            "name": "opaque-quicksave.scop",
            "device": 271828,
            "inode": 161803,
            "uid": os.geteuid(),
            "mode": 0o600,
            "nlink": 1,
            "flags": 0,
            "bytes": 314159,
            "mtime_ns": 987654321,
            "sha256": "deadbeef" * 8,
        }
        record.update(changes)
        return record

    def _save_metadata_failure(
            self, initial: object, final: object) -> MODULE.SaveMetadataDiagnosticError:
        with self.assertRaises(MODULE.SaveMetadataDiagnosticError) as raised:
            MODULE._save_transition(
                initial, final, "live QuickSave after F9",
                diagnostic_root=self.root,
            )
        return raised.exception

    def _publication_fixture(self, name: str = "publication") -> tuple[Path, Path, Path, Path, bytes]:
        parent = self.path / name
        parent.mkdir(mode=0o700)
        def save_record(filename: str, inode: int, *, flags: int = 0) -> dict:
            return {"name": filename, "device": 1, "inode": inode,
                    "uid": os.geteuid(), "mode": 0o600, "nlink": 1,
                    "flags": flags, "bytes": 1, "mtime_ns": 1,
                    "sha256": "a" * 64}
        def transition(filename: str, inode: int, *, initial_flags: int = 0,
                       final_flags: int = 0) -> dict:
            initial = save_record(filename, inode, flags=initial_flags)
            final = save_record(filename, inode, flags=final_flags)
            return {"initial": initial, "final": final,
                    "transition": MODULE._save_flag_transition(
                        initial_flags, final_flags, "fixture")}
        def events(request_id: str, key: str, watermark: int,
                   press: int, release: int, success: int,
                   terminal: int | None = None) -> dict:
            scancode = 62 if key == "f5" else 66
            value = {
                "request_id": request_id, "watermark_line": watermark,
                "ack": f"{request_id} accepted",
                "press": (f"* iOS diag: autoinput request {request_id} press/hold "
                          f"'{key}' (scancode {scancode}) for 100 ms"),
                "press_line": press,
                "release": (f"* iOS diag: autoinput request {request_id} released "
                            f"scancode {scancode}"),
                "release_line": release, "success_line": success,
            }
            if terminal is not None:
                value["terminal_line"] = terminal
            return value
        def capture_record(stem: str, sequence: int, frame: int) -> dict:
            return {"token": f"{SESSION}:{sequence}", "frame": frame,
                    "metadata": str(parent / f"{stem}.json"),
                    "ppm": str(parent / f"{stem}.ppm"),
                    "metadata_sha256": str(sequence) * 64,
                    "ppm_sha256": str(sequence + 3) * 64}
        value = {
            "schema": MODULE.SCHEMA,
            "artifact": "post-stop-revalidated-evidence",
            "post_stop_revalidated": True,
            "runtime": MODULE.RUNTIME,
            "simulator_uuid": self.simulator_uuid,
            "pid": self.pid,
            "baseline_epoch": {"epoch": 1, "frames": [10], "level": "zaton",
                               "markers": 1, "trigger": "level_load",
                               "classification": "exact", "method": "exact"},
            "quickload_epoch": {"epoch": 2, "frames": [60], "level": "zaton",
                                "markers": 1, "trigger": "quick_load",
                                "classification": "exact", "method": "exact"},
            "qsave": self.qsave,
            "qload": self.qload,
            "f5_events": events(self.qsave, "f5", 1, 3, 4, 2),
            "f9_events": events(self.qload, "f9", 5, 7, 9, 6, 8),
            "original_save": transition("original.scop", 1),
            "quicksave": transition(MODULE.PHYSICAL_QUICKSAVE, 2,
                                    final_flags=MODULE.UF_TRACKED),
            "quicksave_copy": save_record(MODULE.PHYSICAL_QUICKSAVE, 3),
            "captures": {
                "b0": capture_record("baseline-b0", 1, 20),
                "b1": capture_record("settled-b1", 2, 40),
                "c": capture_record("post-quickload-c", 3, 80),
            },
            "dds_status": MODULE.DDS_STATUS,
            "scope": MODULE.SCOPE,
        }
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        pending, manifest = parent / "manifest.pending.json", parent / "manifest.json"
        report_pending, report = parent / "report.pending", parent / "report.txt"
        pending.write_bytes(payload)
        production_fields = MODULE.quickload_report_fields(manifest, payload)
        report_pending.write_text(
            "result=PASS\n" + "".join(
                f"{key}={field_value}\n"
                for key, field_value in production_fields.items()),
            encoding="ascii",
        )
        pending.chmod(0o600)
        report_pending.chmod(0o600)
        return pending, manifest, report_pending, report, payload

    @staticmethod
    def _publish_args(pending: Path, manifest: Path,
                      report_pending: Path, report: Path) -> argparse.Namespace:
        return argparse.Namespace(
            pending_manifest=str(pending), manifest=str(manifest),
            report_pending=str(report_pending), report=str(report),
        )

    def _run_happy(self, *, replace_quicksave_on_load: bool = False) -> None:
        sent: list[str] = []
        save_completed = False

        def complete_save() -> None:
            nonlocal save_completed
            if save_completed:
                self.fail("QuickSave was completed twice")
            save_completed = True
            quicksave = self.saves / "player - quicksave.scop"
            quicksave.write_bytes(b"quicksave")
            quicksave.chmod(0o600)
            with self.log.open("a", encoding="utf-8") as out:
                out.write(
                    "* Game Player - quicksave.scop is successfully saved to file "
                    f"'{engine_path(quicksave)}'\n"
                )

        def advance(_: float) -> None:
            trigger = self.appdata / "autoinput.txt"
            if not trigger.exists():
                if sent == ["f5"] and not save_completed:
                    complete_save()
                return
            payload = trigger.read_text(encoding="ascii")
            if payload.startswith(f"id {self.qsave} f5") and "f5" not in sent:
                sent.append("f5")
                trigger.unlink()
                (self.appdata / "autoinput_ack.txt").write_text(f"{self.qsave} accepted\n", encoding="ascii")
                with self.log.open("a", encoding="utf-8") as out:
                    # A synchronous save is valid: press/hold is logged after
                    # dispatch returns, so semantic success may precede it.
                    complete_save()
                    out.write(f"* iOS diag: autoinput request {self.qsave} press/hold 'f5' (scancode 62) for 100 ms\n")
                    out.write(f"* iOS diag: autoinput request {self.qsave} released scancode 62\n")
                self._write_capture(capture_value(self.pid, 2, 37, 1, 115, released_input(self.qsave, "f5", 62, 30, 35)))
            elif payload.startswith(f"id {self.qload} f9") and "f9" not in sent:
                sent.append("f9")
                trigger.unlink()
                (self.appdata / "autoinput_ack.txt").write_text(f"{self.qload} accepted\n", encoding="ascii")
                quicksave = self.saves / "player - quicksave.scop"
                if replace_quicksave_on_load:
                    payload_bytes = quicksave.read_bytes()
                    quicksave.unlink()
                    quicksave.write_bytes(payload_bytes)
                with self.log.open("a", encoding="utf-8") as out:
                    # F9 can likewise synchronously load before post-dispatch
                    # press/hold logging; it must still precede quick_load.
                    out.write(
                        "* Game Player - quicksave is successfully loaded from file "
                        f"'{engine_path(quicksave)}' (0.001s)\n"
                    )
                    out.write(f"* iOS diag: autoinput request {self.qload} press/hold 'f9' (scancode 66) for 100 ms\n")
                    out.write(marker(self.pid, 2, 60, "quick_load") + "\n")
                    out.write(f"* iOS diag: autoinput request {self.qload} released scancode 66\n")
                self._write_capture(capture_value(self.pid, 3, 80, 2, 115, released_input(self.qload, "f9", 66, 50, 55)))
            else:
                self.fail(f"unexpected trigger {payload!r}")

        with mock.patch.object(MODULE.uuid, "uuid4", side_effect=[self.qsave, self.qload]), \
             mock.patch.object(MODULE.time, "sleep", side_effect=advance):
            MODULE.run(self._run_args())
        self.assertEqual(sent, ["f5", "f9"])

    def test_happy_run_finalize_and_proof_last_publication(self) -> None:
        self._run_happy()
        pre = json.loads((self.root / "pre-stop.json").read_text())
        self.assertEqual(pre["qsave"], self.qsave)
        self.assertEqual(pre["qload"], self.qload)
        self.assertEqual(pre["captures"]["c"]["frame"], 80)
        self.assertGreater(
            pre["f9_events"]["terminal_line"],
            pre["f9_events"]["success_line"],
        )
        self.assertLess(
            pre["f9_events"]["terminal_line"],
            pre["f9_events"]["release_line"],
        )
        MODULE.finalize(self._run_args())
        pending = self.root / "manifest.pending.json"
        self.assertTrue(pending.is_file())
        report_pending, report, manifest = self.path / "report.pending", self.path / "report.txt", self.root / "manifest.json"
        production_fields = MODULE.quickload_report_fields(
            manifest, pending.read_bytes())
        report_pending.write_text(
            "result=PASS\n" + "".join(
                f"{key}={value}\n" for key, value in production_fields.items()),
            encoding="ascii")
        report_pending.chmod(0o600)
        MODULE.publish(argparse.Namespace(pending_manifest=str(pending), manifest=str(manifest),
                                          report_pending=str(report_pending), report=str(report)))
        self.assertTrue(manifest.is_file())
        self.assertTrue(report.is_file())
        self.assertFalse(pending.exists())
        self.assertFalse(report_pending.exists())
        manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertNotIn("result", manifest_value)
        self.assertEqual(manifest_value["artifact"], "post-stop-revalidated-evidence")
        self.assertEqual(
            manifest_value["quicksave_copy"]["sha256"],
            manifest_value["quicksave"]["final"]["sha256"],
        )

    def test_finalize_rejects_changed_private_quicksave_copy(self) -> None:
        self._run_happy()
        private_copy = self.root / "player - quicksave.scop"
        private_copy.write_bytes(b"tampered private copy")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.finalize(self._run_args())

    def test_finalize_rejects_replaced_private_quicksave_copy_inode(self) -> None:
        self._run_happy()
        private_copy = self.root / "player - quicksave.scop"
        payload = private_copy.read_bytes()
        private_copy.unlink()
        private_copy.write_bytes(payload)
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.finalize(self._run_args())

    def test_private_quicksave_surface_rejects_flags_unknown_xattr_and_acl(self) -> None:
        evidence = self.root / MODULE.PHYSICAL_QUICKSAVE
        evidence.write_bytes(b"private evidence")
        evidence.chmod(0o600)
        MODULE._save_state(evidence, "private evidence baseline", private=True)
        guard = MODULE._PRIVATE_GUARD

        with mock.patch.object(
                 guard, "_raw_xattrs", return_value={"unapproved.test": "AA=="}), \
             self.assertRaisesRegex(MODULE.EvidenceError, "unapproved xattr"):
            MODULE._save_state(evidence, "private evidence xattr", private=True)
        with mock.patch.object(
                 guard, "_require_no_acl",
                 side_effect=guard.GuardError("unapproved ACL on private evidence")), \
             self.assertRaisesRegex(MODULE.EvidenceError, "unapproved ACL"):
            MODULE._save_state(evidence, "private evidence ACL", private=True)

        if sys.platform != "darwin" or not hasattr(os, "chflags"):
            return
        os.chflags(evidence, MODULE.UF_TRACKED, follow_symlinks=False)
        try:
            with self.assertRaisesRegex(MODULE.EvidenceError, "flags=0"):
                MODULE._save_state(evidence, "private evidence flags", private=True)
        finally:
            os.chflags(evidence, 0, follow_symlinks=False)

    def test_finalize_rejects_tampered_request_watermark(self) -> None:
        self._run_happy()
        pre_path = self.root / "pre-stop.json"
        pre = json.loads(pre_path.read_text(encoding="utf-8"))
        pre["f5_events"]["watermark_line"] += 1
        pre_path.write_text(json.dumps(pre, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.finalize(self._run_args())

    def test_finalize_rejects_tampered_success_index(self) -> None:
        self._run_happy()
        pre_path = self.root / "pre-stop.json"
        pre = json.loads(pre_path.read_text(encoding="utf-8"))
        pre["f9_events"]["success_line"] += 1
        pre_path.write_text(json.dumps(pre, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.finalize(self._run_args())

    def test_finalize_requires_exact_capture_set_and_revalidates_semantics(self) -> None:
        self._run_happy()
        pre_path = self.root / "pre-stop.json"
        pre = json.loads(pre_path.read_text(encoding="utf-8"))
        del pre["captures"]["b1"]
        pre_path.write_text(json.dumps(pre, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.finalize(self._run_args())

    def test_finalize_rejects_self_consistent_capture_semantic_mutation(self) -> None:
        self._run_happy()
        pre_path = self.root / "pre-stop.json"
        pre = json.loads(pre_path.read_text(encoding="utf-8"))
        b1_path = self.root / "settled-b1.json"
        b1 = json.loads(b1_path.read_text(encoding="utf-8"))
        b1["world"]["sector"] = 999
        payload = json.dumps(b1, separators=(",", ":")).encode("utf-8")
        b1_path.write_bytes(payload)
        pre["captures"]["b1"]["metadata_sha256"] = hashlib.sha256(payload).hexdigest()
        pre_path.write_text(json.dumps(pre, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.finalize(self._run_args())

    def test_release_log_contract_rejects_owner_case_extension_path_timing_and_order_mutations(self) -> None:
        quicksave = self.saves / "player - quicksave.scop"
        save = (
            "* Game Player - quicksave.scop is successfully saved to file "
            f"'{engine_path(quicksave)}'"
        )
        load = (
            "* Game Player - quicksave is successfully loaded from file "
            f"'{engine_path(quicksave)}' (0.001s)"
        )
        initial_save = (
            "* Game mobile user - beginning of the game.scop is successfully saved to file "
            f"'{engine_path(self.original)}'"
        )
        initial_load = (
            "* Game mobile user - beginning of the game is successfully loaded from file "
            f"'{engine_path(self.original)}' (0.001s)"
        )
        self.assertEqual(MODULE._expected_save_line(quicksave), save)
        self.assertIsNotNone(MODULE._expected_load_line(quicksave).fullmatch(load))
        # Semantic effects can precede the post-dispatch press/hold marker.
        self.assertEqual(
            MODULE._quicksave_success_index([save, "press"], quicksave, watermark_line=0, before_line=None), 0,
        )
        self.assertEqual(
            MODULE._quickload_success_index([load, "press", "terminal"], quicksave,
                                            watermark_line=0, terminal_line=2), 0,
        )
        # An autoload result before the request watermark is outside the
        # F5/F9 half-open window and must not make the request ambiguous.
        self.assertEqual(
            MODULE._quicksave_success_index([initial_save, save, "f9"], quicksave,
                                            watermark_line=1, before_line=3), 1,
        )
        self.assertEqual(
            MODULE._quickload_success_index([initial_load, load, "terminal"], quicksave,
                                            watermark_line=1, terminal_line=3), 1,
        )
        wrong_physical = self.saves / "Player - quicksave.scop"
        save_mutations = (
            save.replace("Player", "mobile user", 1),
            save.replace("* Game", "* game", 1),
            save.replace("quicksave.scop is", "quicksave is", 1),
            save.replace("savedgames", "other", 1),
            save.replace(engine_path(quicksave), engine_path(wrong_physical)),
            save + " (1 bytes compressed to 1)",
        )
        for line in save_mutations:
            with self.subTest(save=line):
                with self.assertRaises(MODULE.EvidenceError):
                    MODULE._quicksave_success_index(["press", line], quicksave,
                                                    watermark_line=0, before_line=None)
        load_mutations = (
            load.replace("Player", "mobile user", 1),
            load.replace("* Game", "* game", 1),
            load.replace("quicksave is", "quicksave.scop is", 1),
            load.replace("savedgames", "other", 1),
            load.replace(engine_path(quicksave), engine_path(wrong_physical)),
            load.replace("(0.001s)", "(0.01s)"),
        )
        for line in load_mutations:
            with self.subTest(load=line):
                with self.assertRaises(MODULE.EvidenceError):
                    MODULE._quickload_success_index(["press", line, "terminal"], quicksave,
                                                    watermark_line=0, terminal_line=2)
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._quicksave_success_index([initial_save, save, save], quicksave,
                                            watermark_line=1, before_line=3)
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._quickload_success_index([initial_load, load, load, "terminal"], quicksave,
                                            watermark_line=1, terminal_line=3)
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._quicksave_success_index([initial_save, save.replace("Player", "mobile user", 1)], quicksave,
                                            watermark_line=1, before_line=2)
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._quickload_success_index([initial_load, load.replace("Player", "mobile user", 1), "terminal"], quicksave,
                                            watermark_line=1, terminal_line=3)
        self.assertIsNone(
            MODULE._quicksave_success_index([save, "press"], quicksave, watermark_line=1, before_line=None)
        )
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._quickload_success_index([load, "press", "terminal"], quicksave,
                                            watermark_line=1, terminal_line=2)
        self.assertIsNone(
            MODULE._quicksave_success_index(["press", save, "f9"], quicksave,
                                            watermark_line=0, before_line=1)
        )
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._quickload_success_index(["press", "terminal", load], quicksave,
                                            watermark_line=0, terminal_line=1)

    def test_savedgames_rejects_extras_casefold_dds_and_original_mutation(self) -> None:
        quick = self.saves / "player - quicksave.scop"
        quick.write_bytes(b"quick")
        MODULE._savedgames_contract(self.docs, self.staged, self.original)
        (self.saves / "extra.scop").write_bytes(b"extra")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._savedgames_contract(self.docs, self.staged, self.original)
        (self.saves / "extra.scop").unlink()
        quick.unlink()
        wrong_case = self.saves / "Player - quicksave.scop"
        wrong_case.write_bytes(b"wrong case")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._savedgames_contract(self.docs, self.staged, self.original)
        wrong_case.unlink()
        quick.write_bytes(b"quick")
        (self.saves / "player - quicksave.dds").write_bytes(b"dds")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._savedgames_contract(self.docs, self.staged, self.original)
        (self.saves / "player - quicksave.dds").unlink()
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._verify_savedgames_entries(
                [self.original.name, "player - quicksave.scop", "Player - quicksave.scop"],
                {self.original.name},
            )
        self.original.write_bytes(b"changed")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._savedgames_contract(self.docs, self.staged, self.original)

    def test_staged_manifest_rejects_duplicate_casefold_reserved_and_noncanonical_rows(self) -> None:
        quick = self.saves / "player - quicksave.scop"
        quick.write_bytes(b"quick")
        original_row = self.staged.read_text(encoding="utf-8").splitlines()[1]
        digest, size, relative = original_row.split("\t")
        casefold_row = f"{digest}\t{size}\t{relative.upper()}"
        reserved_row = original_row.replace(self.original.name, "player - quicksave.scop")
        mutations = (
            f"sha256\tbytes\tpath\n{original_row}\n{original_row}\n",
            f"sha256\tbytes\tpath\n{original_row}\n{casefold_row}\n",
            f"sha256\tbytes\tpath\n{original_row}\n{reserved_row}\n",
            "sha256\tbytes\tpath\n" + original_row.replace(
                "_appdata_/savedgames/", "_appdata_/savedgames/../") + "\n",
            "sha256\tbytes\tpath\n" + original_row.replace("\t13\t", "\t013\t") + "\n",
        )
        for payload in mutations:
            with self.subTest(payload=payload):
                self.staged.write_text(payload, encoding="utf-8")
                with self.assertRaises(MODULE.EvidenceError):
                    MODULE._savedgames_contract(self.docs, self.staged, self.original)

    def test_quicksave_inode_replacement_fails_during_load_and_after_stop(self) -> None:
        with self.assertRaises(MODULE.EvidenceError):
            self._run_happy(replace_quicksave_on_load=True)

        for entry in list(self.root.iterdir()):
            if entry.is_file() or entry.is_symlink():
                entry.unlink()
        (self.saves / "player - quicksave.scop").unlink(missing_ok=True)
        (self.appdata / "autoinput_ack.txt").unlink(missing_ok=True)
        self.log.write_text(marker(self.pid, 1, 10, "level_load") + "\n", encoding="utf-8")
        self._write_capture(capture_value(self.pid, 1, 20, 1, 115, none_input()))
        self._run_happy()
        quick = self.saves / "player - quicksave.scop"
        payload = quick.read_bytes()
        quick.unlink()
        quick.write_bytes(payload)
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.finalize(self._run_args())

    def test_live_save_flags_accept_only_descriptor_settled_monotonic_transitions(self) -> None:
        self._run_happy()
        real_save_state = MODULE._save_state
        live_quicksave = self.saves / MODULE.PHYSICAL_QUICKSAVE

        def tracked_live(path: Path, label: str, *, private: bool = False) -> dict:
            state = real_save_state(path, label, private=private)
            if path in {self.original, live_quicksave}:
                state = {**state, "flags": MODULE.UF_TRACKED}
            return state

        with mock.patch.object(MODULE, "_save_state", side_effect=tracked_live):
            MODULE.finalize(self._run_args())
        manifest = json.loads((self.root / "manifest.pending.json").read_text())
        self.assertEqual(manifest["original_save"]["transition"], "0->UF_TRACKED")
        self.assertEqual(manifest["quicksave"]["transition"], "0->UF_TRACKED")
        self.assertEqual(manifest["quicksave_copy"]["flags"], 0)

        baseline = real_save_state(self.original, "transition baseline")
        tracked = {**baseline, "flags": MODULE.UF_TRACKED}
        self.assertEqual(
            MODULE._save_transition(baseline, tracked, "0 to tracked")["transition"],
            "0->UF_TRACKED")
        self.assertEqual(
            MODULE._save_transition(tracked, tracked, "tracked stable")["transition"],
            "UF_TRACKED->UF_TRACKED")
        with self.assertRaisesRegex(MODULE.EvidenceError, "removed UF_TRACKED"):
            MODULE._save_transition(tracked, baseline, "tracked removal")
        with self.assertRaisesRegex(MODULE.EvidenceError, "invalid closed v2 save metadata"):
            MODULE._save_transition(baseline, {**baseline, "flags": 0x41}, "combined flags")
        with mock.patch.object(MODULE, "_save_state",
                               side_effect=[baseline, tracked]), \
             self.assertRaisesRegex(MODULE.EvidenceError, "did not stabilize"):
            MODULE._settled_save_state(self.original, "racing flags")

    def test_after_f9_metadata_diagnostic_retains_invalid_initial_and_emits_exact_stderr(self) -> None:
        initial = self._save_metadata_record(
            name="sentinel-path-name-content", uid=os.geteuid() + 1,
            mode=0o644, nlink=2, flags=0x00000001,
        )
        final = self._save_metadata_record()
        error = self._save_metadata_failure(initial, final)
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        expected = {
            "schema": MODULE.SAVE_METADATA_DIAGNOSTIC_SCHEMA,
            "result": "FAIL",
            "boundary": "after-f9",
            "policy": {
                "uid": "current", "mode": "0600", "nlink": 1,
                "flags": ["0x00000000", "0x00000040"],
            },
            "initial": {
                "actual": {
                    "uid": "other", "mode": "0644", "nlink": 2,
                    "flags": "0x00000001",
                },
                "failed": ["uid", "mode", "nlink", "flags"],
            },
            "final": {
                "actual": {
                    "uid": "current", "mode": "0600", "nlink": 1,
                    "flags": "0x00000000",
                },
                "failed": [],
            },
        }
        expected_payload = (
            json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n").encode("ascii")
        self.assertEqual(artifact.read_bytes(), expected_payload)
        self.assertEqual(error.payload, expected_payload)
        self.assertEqual(
            expected_payload,
            (json.dumps(json.loads(expected_payload), sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True) + "\n").encode("ascii"),
        )
        for forbidden in (
                b"sentinel-path", b"sentinel-name", b"sentinel-content", b"deadbeef",
                b"271828", b"161803", b"314159",
                b"987654321", b'"name"', b'"device"', b'"inode"', b'"bytes"',
                b'"mtime_ns"', b'"sha256"', b'"hash"', b'"pid"', b'"uuid"', b"repr"):
            self.assertNotIn(forbidden, expected_payload)

        command = [
            "quickload-evidence", "finalize", "--documents", str(self.docs),
            "--log", str(self.log), "--expected-pid", str(self.pid),
            "--simulator-uuid", self.simulator_uuid, "--staged-manifest", str(self.staged),
            "--root", str(self.root), "--original-save", str(self.original),
        ]
        stderr = io.StringIO()
        with mock.patch.object(MODULE, "finalize", side_effect=error), \
             mock.patch.object(sys, "argv", command), \
             mock.patch.object(sys, "stderr", stderr):
            self.assertEqual(MODULE.main(), 1)
        self.assertEqual(stderr.getvalue().encode("ascii"), expected_payload)

    def test_after_f9_metadata_diagnostic_retains_invalid_final(self) -> None:
        initial = self._save_metadata_record()
        final = self._save_metadata_record(
            uid=os.geteuid() + 1, mode=0o640, nlink=3, flags=0x00000100)
        error = self._save_metadata_failure(initial, final)
        payload = json.loads(error.payload)
        self.assertEqual(payload["initial"]["failed"], [])
        self.assertEqual(payload["final"]["failed"], ["uid", "mode", "nlink", "flags"])
        self.assertEqual(payload["final"]["actual"], {
            "uid": "other", "mode": "0640", "nlink": 3,
            "flags": "0x00000100",
        })
        self.assertEqual(
            (self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME).read_bytes(), error.payload)

    def test_after_f9_metadata_diagnostic_leaf_and_parent_are_private(self) -> None:
        initial = self._save_metadata_record(flags=0x00000001)
        final = self._save_metadata_record()
        observed: dict[str, int] = {}
        real_surface = MODULE._private_file_surface
        real_fsync = MODULE.os.fsync
        synced: list[int] = []

        def inspect_private_leaf(fd: int, label: str) -> dict[str, object]:
            details = os.fstat(fd)
            observed.update({
                "uid": details.st_uid,
                "mode": stat.S_IMODE(details.st_mode),
                "nlink": details.st_nlink,
                "flags": getattr(details, "st_flags", 0),
            })
            return real_surface(fd, label)

        def record_fsync(fd: int) -> None:
            synced.append(os.fstat(fd).st_ino)
            real_fsync(fd)

        with mock.patch.object(MODULE, "_private_file_surface", side_effect=inspect_private_leaf), \
             mock.patch.object(MODULE.os, "fsync", side_effect=record_fsync):
            self._save_metadata_failure(initial, final)
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        details, parent = artifact.stat(), self.root.stat()
        self.assertEqual(observed, {
            "uid": os.geteuid(), "mode": 0o600, "nlink": 1,
            "flags": 0,
        })
        self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
        self.assertEqual(details.st_uid, os.geteuid())
        self.assertEqual(details.st_nlink, 1)
        self.assertEqual(getattr(details, "st_flags", 0), 0)
        self.assertEqual(stat.S_IMODE(parent.st_mode), 0o700)
        self.assertEqual(parent.st_uid, os.geteuid())
        self.assertEqual(getattr(parent, "st_flags", 0), 0)
        self.assertEqual(synced, [details.st_ino, parent.st_ino])

        artifact.unlink()
        self.root.chmod(0o755)
        try:
            with self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
                MODULE._save_transition(
                    initial, final, "live QuickSave after F9", diagnostic_root=self.root)
            self.assertFalse(artifact.exists())
        finally:
            self.root.chmod(0o700)

    def test_after_f9_metadata_diagnostic_retention_failures_preserve_foreign_artifact(self) -> None:
        initial = self._save_metadata_record(flags=0x00000001)
        final = self._save_metadata_record()
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        foreign = b"foreign diagnostic sentinel"
        descriptors_before = len(os.listdir("/dev/fd"))
        artifact.write_bytes(foreign)
        artifact.chmod(0o600)
        with self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
            MODULE._save_transition(
                initial, final, "live QuickSave after F9", diagnostic_root=self.root)
        self.assertEqual(artifact.read_bytes(), foreign)
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)
        artifact.unlink()

        for operation in ("write", "fsync"):
            with self.subTest(operation=operation):
                operation_root = self.path / f"evidence-{operation}-failure"
                operation_root.mkdir(mode=0o700)
                operation_artifact = operation_root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
                descriptors_before = len(os.listdir("/dev/fd"))
                foreign_sibling = operation_root / f"foreign-{operation}.txt"
                foreign_sibling.write_bytes(foreign)
                foreign_sibling.chmod(0o600)
                real_operation = getattr(MODULE.os, operation)
                operation_calls = 0

                def fail_once(*args, **kwargs):
                    nonlocal operation_calls
                    operation_calls += 1
                    if operation_calls == 1:
                        raise OSError(f"mocked {operation} failure")
                    return real_operation(*args, **kwargs)

                with mock.patch.object(
                        MODULE.os, operation, side_effect=fail_once), \
                     self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
                    MODULE._save_transition(
                        initial, final, "live QuickSave after F9",
                        diagnostic_root=operation_root)
                self.assertEqual(foreign_sibling.read_bytes(), foreign)
                self.assertFalse(operation_artifact.exists())
                self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

        guard = MODULE._private_guard()
        real_rebind = guard._require_leaf_rebound
        rebind_calls = 0

        def fail_first_rebind(parent_fd, name, descriptor, label):
            nonlocal rebind_calls
            rebind_calls += 1
            if rebind_calls == 1:
                raise guard.GuardError("mocked final leaf rebind failure")
            return real_rebind(parent_fd, name, descriptor, label)

        rebind_root = self.path / "evidence-rebind-failure"
        rebind_root.mkdir(mode=0o700)
        rebind_artifact = rebind_root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(guard, "_require_leaf_rebound", side_effect=fail_first_rebind), \
             self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
            MODULE._save_transition(
                initial, final, "live QuickSave after F9", diagnostic_root=rebind_root)
        self.assertEqual(rebind_calls, 5)
        self.assertFalse(rebind_artifact.exists())
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

        private_root = self.path / "evidence-private-validation-failure"
        private_root.mkdir(mode=0o700)
        private_artifact = private_root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        foreign_sibling = private_root / "foreign-private-validation.txt"
        foreign_sibling.write_bytes(foreign)
        foreign_sibling.chmod(0o600)
        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(
                MODULE, "_private_file_surface",
                side_effect=MODULE.EvidenceError("mocked private validation failure")), \
             self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
            MODULE._save_transition(
                initial, final, "live QuickSave after F9", diagnostic_root=private_root)
        self.assertEqual(foreign_sibling.read_bytes(), foreign)
        self.assertFalse(private_artifact.exists())
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

    def test_after_f9_metadata_diagnostic_cleans_partial_own_inode_and_preserves_replacement(self) -> None:
        initial = self._save_metadata_record(flags=0x00000001)
        final = self._save_metadata_record()
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        real_write = MODULE.os.write

        writes = 0

        def partial_then_fail(fd: int, data: memoryview) -> int:
            nonlocal writes
            writes += 1
            if writes == 1:
                return real_write(fd, data[:7])
            if writes == 2:
                raise OSError("mocked failure after partial diagnostic write")
            return real_write(fd, data)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(MODULE.os, "write", side_effect=partial_then_fail), \
             self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
            MODULE._save_transition(
                initial, final, "live QuickSave after F9", diagnostic_root=self.root)
        self.assertEqual(writes, 4)
        self.assertFalse(artifact.exists())
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)
        quarantine = sorted(
            entry for entry in self.root.iterdir()
            if entry.name.startswith(CLONE_GUARD.QUARANTINE_PREFIX))
        self.assertEqual(len(quarantine), 3)
        self.assertEqual(
            {ending: sum(entry.name.endswith(ending) for entry in quarantine)
             for ending in (".txn.json", ".complete.json", ".tombstone")},
            {".txn.json": 1, ".complete.json": 1, ".tombstone": 1},
        )
        tombstone = next(entry for entry in quarantine if entry.name.endswith(".tombstone"))
        self.assertEqual(tombstone.read_bytes(), b"")
        self.assertFalse(any(
            b'"result":"PASS"' in entry.read_bytes() for entry in quarantine))
        self.assertFalse((self.root / "manifest.pending.json").exists())
        self.assertFalse((self.root / "report-fields.txt").exists())

        writes = 0
        displaced = self.root / "displaced-owned-partial"
        foreign = b"foreign replacement must remain canonical"

        def replace_then_fail(fd: int, data: memoryview) -> int:
            nonlocal writes
            writes += 1
            if writes == 1:
                written = real_write(fd, data[:7])
                artifact.rename(displaced)
                artifact.write_bytes(foreign)
                artifact.chmod(0o600)
                return written
            if writes == 2:
                raise OSError("mocked failure after canonical replacement")
            return real_write(fd, data)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(MODULE.os, "write", side_effect=replace_then_fail), \
             self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
            MODULE._save_transition(
                initial, final, "live QuickSave after F9", diagnostic_root=self.root)
        self.assertEqual(artifact.read_bytes(), foreign)
        self.assertEqual(displaced.read_bytes(),
                         MODULE._save_metadata_diagnostic_payload(initial, final)[:7])
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

    def test_after_f9_metadata_cleanup_swap_at_quarantine_boundary_preserves_foreign(self) -> None:
        initial = self._save_metadata_record(flags=0x00000001)
        final = self._save_metadata_record()
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        displaced = self.root / "owned-partial-before-quarantine"
        foreign = b"foreign canonical at quarantine boundary"
        real_write = MODULE.os.write
        guard = MODULE._private_guard()
        real_quarantine = guard._quarantine_owned_at
        writes = 0
        quarantine_calls = 0

        def partial_then_fail(fd: int, data: memoryview) -> int:
            nonlocal writes
            writes += 1
            if writes == 1:
                return real_write(fd, data[:7])
            if writes == 2:
                raise OSError("mocked partial write before quarantine swap")
            return real_write(fd, data)

        def swap_then_quarantine(parent_fd, parent, name, identity, label, **kwargs):
            nonlocal quarantine_calls
            quarantine_calls += 1
            artifact.rename(displaced)
            artifact.write_bytes(foreign)
            artifact.chmod(0o600)
            return real_quarantine(
                parent_fd, parent, name, identity, label, **kwargs)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(MODULE.os, "write", side_effect=partial_then_fail), \
             mock.patch.object(guard, "_quarantine_owned_at", side_effect=swap_then_quarantine), \
             self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
            MODULE._save_transition(
                initial, final, "live QuickSave after F9", diagnostic_root=self.root)
        self.assertEqual(quarantine_calls, 1)
        self.assertEqual(artifact.read_bytes(), foreign)
        self.assertEqual(displaced.read_bytes(),
                         MODULE._save_metadata_diagnostic_payload(initial, final)[:7])
        self.assertFalse(any(
            entry.name.startswith(CLONE_GUARD.QUARANTINE_PREFIX)
            for entry in self.root.iterdir()))
        self.assertFalse((self.root / "manifest.pending.json").exists())
        self.assertFalse((self.root / "report-fields.txt").exists())
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

    def test_after_f9_metadata_diagnostic_rejects_parent_fsync_swap_and_preserves_foreign(self) -> None:
        initial = self._save_metadata_record(flags=0x00000001)
        final = self._save_metadata_record()
        payload = MODULE._save_metadata_diagnostic_payload(initial, final)
        assert payload is not None
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        displaced = self.root / "displaced-complete-diagnostic"
        foreign = b"foreign replacement after final leaf rebind"
        root_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        real_fsync = MODULE.os.fsync
        swapped = False

        def swap_on_parent_fsync(fd: int) -> None:
            nonlocal swapped
            details = os.fstat(fd)
            if not swapped and (details.st_dev, details.st_ino) == root_identity:
                artifact.rename(displaced)
                artifact.write_bytes(foreign)
                artifact.chmod(0o600)
                swapped = True
            real_fsync(fd)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(MODULE.os, "fsync", side_effect=swap_on_parent_fsync), \
             self.assertRaisesRegex(MODULE.EvidenceError, "diagnostic-retention-failed"):
            MODULE._save_transition(
                initial, final, "live QuickSave after F9", diagnostic_root=self.root)
        self.assertTrue(swapped)
        self.assertEqual(displaced.read_bytes(), payload)
        self.assertEqual(artifact.read_bytes(), foreign)
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

    def test_save_metadata_diagnostic_non_target_label_keeps_original_error(self) -> None:
        initial = self._save_metadata_record(flags=0x00000001)
        final = self._save_metadata_record()
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        with self.assertRaisesRegex(
                MODULE.EvidenceError, "invalid closed v2 save metadata") as raised:
            MODULE._save_transition(
                initial, final, "live QuickSave before stop", diagnostic_root=self.root)
        self.assertNotIsInstance(raised.exception, MODULE.SaveMetadataDiagnosticError)
        self.assertFalse(artifact.exists())

    def test_after_f9_metadata_diagnostic_skips_malformed_or_valid_records(self) -> None:
        valid = self._save_metadata_record()
        artifact = self.root / MODULE.SAVE_METADATA_DIAGNOSTIC_NAME
        self.assertEqual(
            MODULE._save_transition(
                valid, valid, "live QuickSave after F9", diagnostic_root=self.root),
            {
                "initial": valid, "final": valid,
                "transition": "0->0",
            },
        )
        self.assertFalse(artifact.exists())

        malformed = self._save_metadata_record(mode="0644", name="sentinel-malformed")
        with self.assertRaisesRegex(MODULE.EvidenceError, "invalid closed v2 save metadata") as raised:
            MODULE._save_transition(
                malformed, valid, "live QuickSave after F9", diagnostic_root=self.root)
        self.assertNotIsInstance(raised.exception, MODULE.SaveMetadataDiagnosticError)
        self.assertNotIn("sentinel-malformed", str(raised.exception))
        self.assertFalse(artifact.exists())

    def test_capture_contract_rejects_bad_input_frame_sector_and_session(self) -> None:
        value = capture_value(self.pid, 2, 80, 2, 115, released_input(self.qload, "f9", 66, 50, 55))
        MODULE._capture_contract(value, pid=self.pid, epoch=2, sector_id=115, input_id=self.qload,
                                 key="f9", scancode=66, min_frame=60, prior=(SESSION, 1))
        for mutate in (
            lambda v: v["input"].update({"key": "f5"}),
            lambda v: v["capture"].update({"frame": 55}),
            lambda v: v["world"].update({"sector": 9}),
            lambda v: v["capture"].update({"width": 932}),
            lambda v: v["capture"].update({"session": "b" * 32, "token": "b" * 32 + ":2"}),
        ):
            bad = json.loads(json.dumps(value))
            mutate(bad)
            with self.assertRaises(MODULE.EvidenceError):
                MODULE._capture_contract(bad, pid=self.pid, epoch=2, sector_id=115, input_id=self.qload,
                                         key="f9", scancode=66, min_frame=60, prior=(SESSION, 1))

    def test_capture_contract_accepts_release_plus_one_and_rejects_release_frame(self) -> None:
        # "Settled" means stable save plus post-release request identity, not
        # an additional frame-based claim about pixel stabilization.
        release_plus_one = capture_value(
            self.pid, 2, 56, 2, 115,
            released_input(self.qload, "f9", 66, 50, 55),
        )
        MODULE._capture_contract(
            release_plus_one, pid=self.pid, epoch=2, sector_id=115,
            input_id=self.qload, key="f9", scancode=66,
            min_frame=54, prior=(SESSION, 1),
        )
        at_release = json.loads(json.dumps(release_plus_one))
        at_release["capture"]["frame"] = 55
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._capture_contract(
                at_release, pid=self.pid, epoch=2, sector_id=115,
                input_id=self.qload, key="f9", scancode=66,
                min_frame=54, prior=(SESSION, 1),
            )

    def test_publish_rejects_unbound_report_and_does_not_delete_replacement(self) -> None:
        pending = self.path / "pending.json"
        pending.write_bytes(b"{}\n")
        pending.chmod(0o600)
        report_pending = self.path / "report.pending"
        report_pending.write_text("result=PASS\n", encoding="ascii")
        report_pending.chmod(0o600)
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.publish(argparse.Namespace(pending_manifest=str(pending), manifest=str(self.path / "manifest.json"),
                                              report_pending=str(report_pending), report=str(self.path / "report.txt")))
        self.assertFalse((self.path / "manifest.json").exists())

    def test_production_report_fields_are_exact_closed_unique_and_reserved(self) -> None:
        pending, manifest, report_pending, _, payload = self._publication_fixture(
            "production-report-schema")
        expected = MODULE.quickload_report_fields(manifest, payload)
        self.assertEqual(tuple(expected), MODULE.QUICKLOAD_REPORT_KEYS)
        baseline = report_pending.read_bytes()
        MODULE._validate_publish_report(baseline, payload, manifest)

        lines = baseline.decode("ascii").splitlines()
        for key, value in expected.items():
            exact = f"{key}={value}"
            with self.subTest(missing=key):
                candidate = ("\n".join(line for line in lines if line != exact)
                             + "\n").encode("ascii")
                with self.assertRaisesRegex(MODULE.EvidenceError, "exact closed"):
                    MODULE._validate_publish_report(candidate, payload, manifest)
            with self.subTest(duplicate=key):
                with self.assertRaisesRegex(MODULE.EvidenceError, "exact closed"):
                    MODULE._validate_publish_report(
                        baseline + (exact + "\n").encode("ascii"), payload, manifest)

        for unknown in ("quickload_unbound=1", "quickload_extra=1",
                        "clone_unbound=1"):
            with self.subTest(unknown=unknown), self.assertRaises(MODULE.EvidenceError):
                MODULE._validate_publish_report(
                    baseline + (unknown + "\n").encode("ascii"), payload, manifest)

        clone_fields = {
            "stage_mode": "clone-required",
            "clone_ledger": "/private/clone-ledger.json",
            "staged_manifest_sha256": "b" * 64,
        }
        clone_payload = baseline + b"".join(
            f"{key}={value}\n".encode("ascii")
            for key, value in clone_fields.items())
        MODULE._validate_publish_report(
            clone_payload, payload, manifest, clone_fields=clone_fields)
        for key, value in clone_fields.items():
            exact = f"{key}={value}\n".encode("ascii")
            with self.subTest(clone_missing=key), self.assertRaises(MODULE.EvidenceError):
                MODULE._validate_publish_report(
                    clone_payload.replace(exact, b"", 1), payload, manifest,
                    clone_fields=clone_fields)
            with self.subTest(clone_duplicate=key), self.assertRaises(MODULE.EvidenceError):
                MODULE._validate_publish_report(
                    clone_payload + exact, payload, manifest,
                    clone_fields=clone_fields)

    def test_publish_uses_single_link_exclusive_rename_and_rejects_hardlinks(self) -> None:
        pending, manifest, report_pending, report, payload = self._publication_fixture("rename-success")
        manifest_identity = (pending.stat().st_dev, pending.stat().st_ino)
        report_identity = (report_pending.stat().st_dev, report_pending.stat().st_ino)
        with mock.patch.object(MODULE.os, "link", side_effect=AssertionError("hardlink forbidden")):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertFalse(pending.exists())
        self.assertFalse(report_pending.exists())
        self.assertEqual(manifest.read_bytes(), payload)
        self.assertEqual((manifest.stat().st_dev, manifest.stat().st_ino), manifest_identity)
        self.assertEqual((report.stat().st_dev, report.stat().st_ino), report_identity)
        self.assertEqual(manifest.stat().st_nlink, 1)
        self.assertEqual(report.stat().st_nlink, 1)

        pending, manifest, report_pending, report, _ = self._publication_fixture("hardlink-rejected")
        os.link(pending, pending.parent / "manifest.second-link")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertFalse(manifest.exists())
        self.assertFalse(report.exists())
        self.assertTrue(report_pending.exists())

    def test_publish_succeeds_with_distinct_pinned_manifest_and_report_parents(self) -> None:
        pending, manifest, report_pending, _, payload = self._publication_fixture(
            "distinct-manifest-parent")
        report_parent = self.path / "distinct-report-parent"
        report_parent.mkdir(mode=0o700)
        moved_report_pending = report_parent / report_pending.name
        report_pending.rename(moved_report_pending)
        report = report_parent / "report.txt"
        expected_report = moved_report_pending.read_bytes()
        manifest_identity = (pending.stat().st_dev, pending.stat().st_ino)
        report_identity = (moved_report_pending.stat().st_dev,
                           moved_report_pending.stat().st_ino)

        MODULE.publish(self._publish_args(
            pending, manifest, moved_report_pending, report))

        self.assertEqual(manifest.read_bytes(), payload)
        self.assertEqual(report.read_bytes(), expected_report)
        self.assertEqual(
            (manifest.stat().st_dev, manifest.stat().st_ino), manifest_identity)
        self.assertEqual(
            (report.stat().st_dev, report.stat().st_ino), report_identity)
        self.assertFalse(pending.exists())
        self.assertFalse(moved_report_pending.exists())

    def test_publish_distinct_parent_provenance_drift_between_manifest_and_report_fails_closed(self) -> None:
        first = base64.b64encode(b"quickload-parent-first-opaque").decode("ascii")
        second = base64.b64encode(b"quickload-parent-second-opaque-value").decode("ascii")
        mutations = {
            "add": ({}, {"com.apple.provenance": first}),
            "remove": ({"com.apple.provenance": first}, {}),
            "value-change": ({"com.apple.provenance": first},
                             {"com.apple.provenance": second}),
        }
        fake_spec = types.SimpleNamespace(
            loader=types.SimpleNamespace(exec_module=lambda module: None))
        real_raw_xattrs = CLONE_GUARD._raw_xattrs
        real_publish = CLONE_GUARD._publish_owned_rename_at

        for mutation, (before, after) in mutations.items():
            with self.subTest(mutation=mutation):
                pending, manifest, report_pending, _, _ = self._publication_fixture(
                    f"distinct-provenance-manifest-{mutation}")
                report_parent = self.path / f"distinct-provenance-report-{mutation}"
                report_parent.mkdir(mode=0o700)
                moved_report_pending = report_parent / report_pending.name
                report_pending.rename(moved_report_pending)
                report = report_parent / "report.txt"
                foreign = report_parent / "foreign-private-data"
                foreign_payload = b"foreign bytes must remain untouched\n"
                foreign.write_bytes(foreign_payload)
                foreign.chmod(0o600)
                pending_payload = moved_report_pending.read_bytes()
                pending_identity = (moved_report_pending.stat().st_dev,
                                    moved_report_pending.stat().st_ino)
                foreign_identity = (foreign.stat().st_dev, foreign.stat().st_ino)
                report_parent_identity = (
                    report_parent.stat().st_dev, report_parent.stat().st_ino)
                synthetic = {report_parent_identity: dict(before)}

                def synthetic_raw_xattrs(fd, label):
                    identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                    if identity in synthetic:
                        return dict(synthetic[identity])
                    return real_raw_xattrs(fd, label)

                def publish_then_mutate(*args, **kwargs):
                    result = real_publish(*args, **kwargs)
                    if args[4] == "QuickLoad manifest":
                        synthetic[report_parent_identity] = dict(after)
                    return result

                with mock.patch.object(
                         MODULE.importlib.util, "spec_from_file_location",
                         return_value=fake_spec,
                     ), mock.patch.object(
                         MODULE.importlib.util, "module_from_spec",
                         return_value=CLONE_GUARD,
                     ), mock.patch.object(
                         CLONE_GUARD, "_raw_xattrs",
                         side_effect=synthetic_raw_xattrs,
                     ), mock.patch.object(
                         CLONE_GUARD, "_publish_owned_rename_at",
                         side_effect=publish_then_mutate,
                     ), self.assertRaises((MODULE.EvidenceError,
                                           CLONE_GUARD.GuardError)):
                    MODULE.publish(self._publish_args(
                        pending, manifest, moved_report_pending, report))

                self.assertTrue(manifest.exists())
                self.assertFalse(report.exists())
                self.assertEqual(moved_report_pending.read_bytes(), pending_payload)
                self.assertEqual(
                    (moved_report_pending.stat().st_dev,
                     moved_report_pending.stat().st_ino), pending_identity)
                self.assertEqual(foreign.read_bytes(), foreign_payload)
                self.assertEqual(
                    (foreign.stat().st_dev, foreign.stat().st_ino),
                    foreign_identity)
                self.assertFalse(any(report_parent.glob(
                    f"{CLONE_GUARD.QUARANTINE_PREFIX}*.tombstone")))

    def test_publish_interrupted_manifest_rename_leaves_poisoned_nonpass_state(self) -> None:
        pending, manifest, report_pending, report, _ = self._publication_fixture("manifest-interrupt")
        real_rename = CLONE_GUARD._rename_exclusive_at

        def rename_then_interrupt(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == pending.name and destination == manifest.name:
                raise KeyboardInterrupt
            return result

        fake_spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location", return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec", return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_rename_exclusive_at", side_effect=rename_then_interrupt), \
             self.assertRaises(KeyboardInterrupt):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertFalse(manifest.exists())
        self.assertFalse(report.exists())
        self.assertTrue(report_pending.exists())
        parent_fd = CLONE_GUARD._open_absolute_directory_nofollow(
            manifest.parent, "interrupted manifest parent")
        try:
            with self.assertRaisesRegex(CLONE_GUARD.GuardError, "poisoned|incomplete"):
                CLONE_GUARD._validate_transaction_parent_fd(
                    parent_fd, manifest.parent, "interrupted manifest parent")
        finally:
            os.close(parent_fd)

    def test_publish_interrupted_report_rename_keeps_pass_non_authoritative(self) -> None:
        pending, manifest, report_pending, report, _ = self._publication_fixture("report-interrupt")
        real_rename = CLONE_GUARD._rename_exclusive_at

        def rename_then_interrupt(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == report_pending.name and destination == report.name:
                raise SystemExit(12)
            return result

        fake_spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location", return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec", return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_rename_exclusive_at", side_effect=rename_then_interrupt), \
             self.assertRaises(SystemExit):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertTrue(manifest.exists())
        self.assertFalse(report.exists())
        self.assertFalse(report_pending.exists())
        parent_fd = CLONE_GUARD._open_absolute_directory_nofollow(
            report.parent, "interrupted report parent")
        try:
            with self.assertRaisesRegex(CLONE_GUARD.GuardError, "poisoned|incomplete"):
                CLONE_GUARD._validate_transaction_parent_fd(
                    parent_fd, report.parent, "interrupted report parent")
        finally:
            os.close(parent_fd)

    def test_publish_clone_inputs_are_all_or_none(self) -> None:
        pending, manifest, report_pending, report, _ = self._publication_fixture("all-or-none")
        with self.assertRaisesRegex(MODULE.EvidenceError, "all-or-none"):
            MODULE.publish(argparse.Namespace(
                pending_manifest=str(pending), manifest=str(manifest),
                report_pending=str(report_pending), report=str(report),
                clone_prepared=str(self.path), clone_ledger=None, staged_manifest=None,
            ))

    def test_integrated_clone_validation_runs_after_report_rename(self) -> None:
        pending, manifest, report_pending, report, _ = self._publication_fixture("clone-boundaries")
        phases = []
        def validate(_binding, _report_data, _parent_fd, _parent, active,
                     *, quickload_manifest_name):
            record = active["record"] if active is not None else None
            phases.append((active["phase"] if active else "initial",
                           record["destination"] if record else None))
            if (active is not None and active["phase"] == "postcommit"
                    and record["destination"] == report.name):
                raise MODULE.EvidenceError("clone mutation after report rename")
        fake_spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        arguments = argparse.Namespace(
            pending_manifest=str(pending), manifest=str(manifest),
            report_pending=str(report_pending), report=str(report),
            clone_prepared=str(self.path / "prepared"),
            clone_ledger=str(self.path / "ledger.json"),
            staged_manifest=str(self.path / "staged.tsv"),
        )
        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location",
                               return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec",
                               return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_pin_clone_publication_dependencies",
                               return_value={"pinned": True,
                                             "expected_fields": {}}), \
             mock.patch.object(CLONE_GUARD, "_close_clone_publication_dependencies"), \
             mock.patch.object(CLONE_GUARD, "_revalidate_clone_publication_dependencies",
                               side_effect=validate):
            with self.assertRaisesRegex(MODULE.EvidenceError, "after report rename"):
                MODULE.publish(arguments)
        self.assertIn(("postcommit", report.name), phases)
        self.assertTrue(manifest.exists())
        self.assertFalse(report.exists())
        self.assertFalse(pending.exists())
        self.assertFalse(report_pending.exists())

        pending, manifest, report_pending, report, payload = self._publication_fixture("reserved-fields")
        quickload_expected = MODULE.quickload_report_fields(manifest, payload)
        expected = {
            "stage_mode": "clone-required",
            "clone_ledger": "/tmp/ledger.json",
            "clone_ledger_dev": "1",
            "clone_ledger_ino": "2",
            "clone_ledger_bytes": "3",
            "clone_ledger_sha256": "a" * 64,
            "staged_manifest_sha256": "b" * 64,
            "clone_destination_post_delete": "unavailable-container-deleted",
        }
        report_pending.write_text(
            "result=PASS\n"
            + "".join(f"{key}={value}\n" for key, value in
                      MODULE.quickload_report_fields(manifest, payload).items())
            + "".join(f"{key}={value}\n" for key, value in expected.items())
            + "stage_mode=legacy-copy\n",
            encoding="ascii",
        )
        report_pending.chmod(0o600)

        def validate_reserved(_binding, report_data, _parent_fd, _parent, _active,
                              *, quickload_manifest_name):
            CLONE_GUARD._validate_reserved_clone_report_fields(
                report_data, expected, quickload_expected,
            )

        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location",
                               return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec",
                               return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_pin_clone_publication_dependencies",
                               return_value={"pinned": True,
                                             "expected_fields": expected}), \
             mock.patch.object(CLONE_GUARD, "_close_clone_publication_dependencies"), \
             mock.patch.object(CLONE_GUARD, "_revalidate_clone_publication_dependencies",
                               side_effect=validate_reserved), \
             self.assertRaisesRegex(MODULE.EvidenceError, "must occur exactly once"):
            MODULE.publish(argparse.Namespace(
                pending_manifest=str(pending), manifest=str(manifest),
                report_pending=str(report_pending), report=str(report),
                clone_prepared=str(self.path / "prepared"),
                clone_ledger=str(self.path / "ledger.json"),
                staged_manifest=str(self.path / "staged.tsv"),
            ))
        self.assertFalse(manifest.exists())
        self.assertFalse(report.exists())

    def test_quickload_v2_rejects_v1_malformed_and_private_save_flag_or_inode_debt(self) -> None:
        pending, _, _, _, _ = self._publication_fixture("v2-closed-manifest")
        original = json.loads(pending.read_text())
        valid_tracked_original = json.loads(json.dumps(original))
        valid_tracked_original["original_save"]["final"]["flags"] = MODULE.UF_TRACKED
        valid_tracked_original["original_save"]["transition"] = "0->UF_TRACKED"
        MODULE._validate_publish_manifest(
            (json.dumps(valid_tracked_original, sort_keys=True,
                        separators=(",", ":")) + "\n").encode(),
            expected_root=pending.parent)
        cases = {}
        v1 = json.loads(json.dumps(original))
        v1["schema"] = "openxray-ios-simulator-quickload-evidence-v1"
        cases["v1"] = v1
        extra = json.loads(json.dumps(original))
        extra["unexpected"] = True
        cases["extra"] = extra
        missing = json.loads(json.dumps(original))
        del missing["scope"]
        cases["missing"] = missing
        nested_extra = json.loads(json.dumps(original))
        nested_extra["original_save"]["unexpected"] = 1
        cases["nested-extra"] = nested_extra
        nested_missing = json.loads(json.dumps(original))
        del nested_missing["quicksave"]["final"]["mtime_ns"]
        cases["nested-missing"] = nested_missing
        private_flags = json.loads(json.dumps(original))
        private_flags["quicksave_copy"]["flags"] = 0x40
        cases["private-flags"] = private_flags
        shared_inode = json.loads(json.dumps(original))
        shared_inode["quicksave_copy"]["device"] = shared_inode["quicksave"]["final"]["device"]
        shared_inode["quicksave_copy"]["inode"] = shared_inode["quicksave"]["final"]["inode"]
        cases["private-shared-inode"] = shared_inode
        for name, value in cases.items():
            with self.subTest(name=name), self.assertRaises(MODULE.EvidenceError):
                MODULE._validate_publish_manifest(
                    (json.dumps(value, sort_keys=True,
                                separators=(",", ":")) + "\n").encode(),
                    expected_root=pending.parent)

    def test_publish_manifest_rejects_nested_semantic_and_private_binding_tamper(self) -> None:
        pending, _, _, _, original_payload = self._publication_fixture(
            "nested-semantic-tamper")
        original = json.loads(original_payload)
        cases = {}
        for name, mutate in (
            ("epoch-order", lambda value: value["quickload_epoch"].update({"epoch": 3})),
            ("event-uuid", lambda value: value["f9_events"].update(
                {"request_id": value["qsave"]})),
            ("terminal-equals-success", lambda value: value["f9_events"].update(
                {"terminal_line": value["f9_events"]["success_line"]})),
            ("terminal-before-success", lambda value: value["f9_events"].update(
                {"terminal_line": value["f9_events"]["success_line"] - 1})),
            ("release-equals-press", lambda value: value["f9_events"].update(
                {"release_line": value["f9_events"]["press_line"]})),
            ("release-before-press", lambda value: value["f9_events"].update(
                {"release_line": value["f9_events"]["press_line"] - 1})),
            ("capture-order", lambda value: value["captures"]["c"].update({"frame": 30})),
            ("capture-token", lambda value: value["captures"]["b1"].update(
                {"token": f"{'b' * 32}:2"})),
            ("dds", lambda value: value.update({"dds_status": "present"})),
            ("scope", lambda value: value.update({"scope": "wider proof"})),
            ("private-sha", lambda value: value["quicksave_copy"].update(
                {"sha256": "b" * 64})),
            ("original-role", lambda value: value["original_save"]["initial"].update(
                {"name": MODULE.PHYSICAL_QUICKSAVE})),
        ):
            candidate = json.loads(json.dumps(original))
            mutate(candidate)
            cases[name] = candidate
        for name, value in cases.items():
            payload = (json.dumps(value, sort_keys=True, separators=(",", ":"))
                       + "\n").encode()
            with self.subTest(name=name), self.assertRaises(MODULE.EvidenceError):
                MODULE._validate_publish_manifest(
                    payload, expected_root=pending.parent)

        MODULE.validate_report_fields_bytes(
            (f"quickload_manifest_sha256={hashlib.sha256(original_payload).hexdigest()}\n"
             f"quickload_manifest_bytes={len(original_payload)}\n").encode(),
            original_payload)
        with self.assertRaisesRegex(MODULE.EvidenceError, "exact closed"):
            MODULE.validate_report_fields_bytes(
                (f"quickload_manifest_sha256={hashlib.sha256(original_payload).hexdigest()}\n"
                 f"quickload_manifest_bytes={len(original_payload)}\n"
                 "quickload_extra=1\n").encode(), original_payload)

    def test_publish_revalidates_dependencies_after_rename_and_completion(self) -> None:
        fake_spec = types.SimpleNamespace(
            loader=types.SimpleNamespace(exec_module=lambda module: None))

        pending, manifest, report_pending, report, _ = self._publication_fixture(
            "manifest-post-rename-dependency")
        real_rename = CLONE_GUARD._rename_exclusive_at
        def mutate_report_after_manifest_rename(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == pending.name and destination == manifest.name:
                report_pending.write_bytes(report_pending.read_bytes() + b"mutation\n")
            return result
        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location",
                               return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec",
                               return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_rename_exclusive_at",
                               side_effect=mutate_report_after_manifest_rename), \
             self.assertRaises((MODULE.EvidenceError, CLONE_GUARD.GuardError)):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertFalse(manifest.exists())
        self.assertFalse(report.exists())

        pending, manifest, report_pending, report, _ = self._publication_fixture(
            "report-post-rename-dependency")
        def mutate_manifest_after_report_rename(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == report_pending.name and destination == report.name:
                manifest.write_bytes(manifest.read_bytes() + b" ")
            return result
        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location",
                               return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec",
                               return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_rename_exclusive_at",
                               side_effect=mutate_manifest_after_report_rename), \
             self.assertRaises((MODULE.EvidenceError, CLONE_GUARD.GuardError)):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertFalse(report.exists())

        pending, manifest, report_pending, report, _ = self._publication_fixture(
            "report-post-completion-dependency")
        real_completion = CLONE_GUARD._publish_completion_record_at
        def mutate_after_completion(parent_fd, stem, value, label):
            result = real_completion(parent_fd, stem, value, label)
            if label == "QuickLoad retail report publication completion":
                manifest.write_bytes(manifest.read_bytes() + b" ")
            return result
        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location",
                               return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec",
                               return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_publish_completion_record_at",
                               side_effect=mutate_after_completion), \
             self.assertRaises((MODULE.EvidenceError, CLONE_GUARD.GuardError)):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertFalse(report.exists())

    def test_publish_rejects_private_uf_tracked_initial_post_pin_rename_and_completion(self) -> None:
        if sys.platform != "darwin" or not hasattr(os, "chflags"):
            self.skipTest("Darwin UF_TRACKED publication contract requires macOS")
        fake_spec = types.SimpleNamespace(
            loader=types.SimpleNamespace(exec_module=lambda module: None))

        for phase in ("initial", "post-pin", "post-rename", "completion"):
            with self.subTest(phase=phase):
                pending, manifest, report_pending, report, _ = self._publication_fixture(
                    f"private-uf-tracked-{phase}")
                real_read = CLONE_GUARD._read_pinned_regular_at
                real_rename = CLONE_GUARD._rename_exclusive_at
                real_completion = CLONE_GUARD._publish_completion_record_at

                if phase == "initial":
                    os.chflags(pending, MODULE.UF_TRACKED, follow_symlinks=False)

                def pin_then_track(parent_fd, path, label, *, private):
                    state = real_read(parent_fd, path, label, private=private)
                    if phase == "post-pin" and label == "pending QuickLoad manifest":
                        os.chflags(path, MODULE.UF_TRACKED, follow_symlinks=False)
                    return state

                def rename_then_track(parent_fd, source, destination):
                    result = real_rename(parent_fd, source, destination)
                    if (phase == "post-rename" and source == report_pending.name
                            and destination == report.name):
                        os.chflags(report, MODULE.UF_TRACKED, follow_symlinks=False)
                    return result

                def complete_then_track(parent_fd, stem, value, label):
                    result = real_completion(parent_fd, stem, value, label)
                    if (phase == "completion"
                            and label == "QuickLoad retail report publication completion"):
                        os.chflags(report, MODULE.UF_TRACKED, follow_symlinks=False)
                    return result

                with mock.patch.object(
                         MODULE.importlib.util, "spec_from_file_location",
                         return_value=fake_spec), mock.patch.object(
                         MODULE.importlib.util, "module_from_spec",
                         return_value=CLONE_GUARD), mock.patch.object(
                         CLONE_GUARD, "_read_pinned_regular_at",
                         side_effect=pin_then_track), mock.patch.object(
                         CLONE_GUARD, "_rename_exclusive_at",
                         side_effect=rename_then_track), mock.patch.object(
                         CLONE_GUARD, "_publish_completion_record_at",
                         side_effect=complete_then_track), self.assertRaises(
                             (MODULE.EvidenceError, CLONE_GUARD.GuardError)):
                    MODULE.publish(self._publish_args(
                        pending, manifest, report_pending, report))

                self.assertFalse(report.exists() and phase in {"initial", "post-pin"})
                publication_parent = report.parent
                parent_fd = CLONE_GUARD._open_absolute_directory_nofollow(
                    publication_parent, f"{phase} tracked publication parent")
                try:
                    if report.exists():
                        with self.assertRaisesRegex(
                                CLONE_GUARD.GuardError, "poisoned|incomplete"):
                            CLONE_GUARD._validate_transaction_parent_fd(
                                parent_fd, publication_parent,
                                f"{phase} tracked publication parent")
                finally:
                    os.close(parent_fd)
                for path in (pending, manifest, report_pending, report):
                    if os.path.lexists(path):
                        os.chflags(path, 0, follow_symlinks=False)

    def test_publish_parent_swap_keeps_pass_only_in_poisoned_renamed_parent(self) -> None:
        publication_parent = self.path / "publication-parent"
        pending, manifest, report_pending, report, _ = self._publication_fixture(
            publication_parent.name)
        renamed_parent = self.path / "publication-parent-renamed"
        real_rename = CLONE_GUARD._rename_exclusive_at

        def rename_then_swap(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == report_pending.name and destination == report.name:
                publication_parent.rename(renamed_parent)
                publication_parent.mkdir(mode=0o700)
            return result

        fake_spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(MODULE.importlib.util, "spec_from_file_location", return_value=fake_spec), \
             mock.patch.object(MODULE.importlib.util, "module_from_spec", return_value=CLONE_GUARD), \
             mock.patch.object(CLONE_GUARD, "_rename_exclusive_at", side_effect=rename_then_swap), \
             self.assertRaisesRegex((MODULE.EvidenceError, CLONE_GUARD.GuardError), "pathname changed"):
            MODULE.publish(self._publish_args(pending, manifest, report_pending, report))
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)
        self.assertTrue((renamed_parent / "report.txt").exists())
        self.assertTrue((renamed_parent / "manifest.json").exists())
        self.assertFalse(report.exists())
        parent_fd = CLONE_GUARD._open_absolute_directory_nofollow(
            renamed_parent, "renamed QuickLoad parent")
        try:
            with self.assertRaisesRegex(CLONE_GUARD.GuardError, "poisoned|incomplete"):
                CLONE_GUARD._validate_transaction_parent_fd(
                    parent_fd, renamed_parent, "renamed QuickLoad parent")
        finally:
            os.close(parent_fd)

    def test_publish_partial_parent_open_baseexception_closes_descriptors(self) -> None:
        pending, manifest, report_pending, _, _ = self._publication_fixture("partial-open-manifest")
        report_parent = self.path / "partial-open-report"
        report_parent.mkdir(mode=0o700)
        moved_report_pending = report_parent / report_pending.name
        report_pending.rename(moved_report_pending)
        report = report_parent / "report.txt"
        real_open = MODULE._open_absolute_directory_nofollow

        def interrupt_second_parent(path, label):
            if Path(path) == report_parent:
                raise KeyboardInterrupt
            return real_open(path, label)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(
                MODULE, "_open_absolute_directory_nofollow",
                side_effect=interrupt_second_parent), self.assertRaises(KeyboardInterrupt):
            MODULE.publish(self._publish_args(
                pending, manifest, moved_report_pending, report))
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)
        self.assertFalse(manifest.exists())
        self.assertFalse(report.exists())

    def test_settled_save_and_request_reject_replacement_malformed_ack_and_duplicate_uuid(self) -> None:
        state = {"name": "x", "device": 1, "inode": 2, "bytes": 3, "sha256": "a" * 64}
        with mock.patch.object(MODULE, "_save_state", side_effect=[state, {**state, "inode": 9}]):
            with self.assertRaises(MODULE.EvidenceError):
                MODULE._settled_save_state(self.original, "unstable quicksave")

        trigger, ack = self.appdata / "autoinput.txt", self.appdata / "autoinput_ack.txt"
        prior = self.log.read_bytes()
        request = "33333333-3333-4333-8333-333333333333"

        def malformed(_: float) -> None:
            if trigger.exists():
                trigger.unlink()
                ack.write_text(f"{request} rejected\n", encoding="ascii")

        with mock.patch.object(MODULE.uuid, "uuid4", return_value=request), \
             mock.patch.object(MODULE.time, "sleep", side_effect=malformed):
            with self.assertRaises(MODULE.EvidenceError):
                MODULE._request(self.appdata, self.log, prior, key="f5", expected_pid=self.pid, timeout=0.1, poll=0.01)

        trigger.unlink(missing_ok=True)
        ack.unlink(missing_ok=True)
        def duplicate(_: float) -> None:
            if trigger.exists():
                trigger.unlink()
                ack.write_text(f"{request} accepted\n", encoding="ascii")
                with self.log.open("a", encoding="utf-8") as out:
                    out.write(f"* iOS diag: autoinput request {request} press/hold 'f5' (scancode 62) for 100 ms\n" * 2)
                    out.write(f"* iOS diag: autoinput request {request} released scancode 62\n")

        with mock.patch.object(MODULE.uuid, "uuid4", return_value=request), \
             mock.patch.object(MODULE.time, "sleep", side_effect=duplicate):
            with self.assertRaises(MODULE.EvidenceError):
                MODULE._request(self.appdata, self.log, prior, key="f5", expected_pid=self.pid, timeout=0.1, poll=0.01)

    def test_live_log_ignores_only_an_incomplete_appended_line(self) -> None:
        complete = self.log.read_bytes()
        with self.log.open("ab") as output:
            output.write(b"partial runtime line")
        _, snapshot, lines = MODULE._read_log(self.log, complete)
        self.assertEqual(snapshot, complete)
        self.assertEqual(lines, complete.decode("utf-8").splitlines())
        with self.assertRaises(MODULE.EvidenceError):
            MODULE._read_log(self.log, None, final=True)

    def test_failed_run_preserves_one_nofollow_log_snapshot(self) -> None:
        args = self._run_args()
        MODULE._preserve_run_failure(args)
        snapshot = self.root / "failure-log.txt"
        self.assertEqual(snapshot.read_bytes(), self.log.read_bytes())
        self.log.write_text("replacement log\n", encoding="utf-8")
        MODULE._preserve_run_failure(args)
        self.assertNotEqual(snapshot.read_bytes(), self.log.read_bytes())

    def test_exact_quickload_config_and_forbidden_sector_classes(self) -> None:
        import retail_simulator_guard as guard
        expected = (
            "keypress_on_start 0\n"
            "ios_diagnostics 1\n"
            "ios_autoinput 1\n"
            "bind quick_save kF5\n"
            "bind quick_load kF9\n"
            "start server(save/single/alife/load) client(localhost)\n"
        ).encode("ascii")
        self.assertEqual(guard.autoload_config("save", ios_diagnostics=True, ios_autoinput=True,
                                               quickload_evidence=True), expected)
        for status, method in (("unresolved", "none"), ("unresolved", "fallback")):
            bad = marker(self.pid, 1, 10, "level_load", sector=0xFFFFFFFF, method=method).replace(
                "status=resolved", f"status={status}")
            with self.assertRaises(MODULE.EvidenceError):
                MODULE._marker_epoch([bad], self.pid)

    def test_runner_cli_conflicts_fail_before_simulator_work(self) -> None:
        runner = ROOT / "misc/ios/retail_simulator.sh"
        import subprocess
        result = subprocess.run([str(runner), "--backup", str(self.path), "--quickload-evidence"],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --runtime 27.0", result.stderr)
        result = subprocess.run([str(runner), "--backup", str(self.path), "--runtime", "27.0", "--with-saves",
                                 "--autoload-save", "save", "--quickload-evidence", "--capture-v2"],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts", result.stderr)

    def test_original_save_post_stop_rejects_identical_bytes_inode_and_metadata_changes(self) -> None:
        self._run_happy()
        payload = self.original.read_bytes()
        self.original.unlink()
        self.original.write_bytes(payload)
        self.original.chmod(0o600)
        with self.assertRaisesRegex(MODULE.EvidenceError, "original selected save latest boundary changed inode"):
            MODULE.finalize(self._run_args())

        baseline = MODULE._save_state(self.original, "original baseline")
        for field, value in (
            ("mode", baseline["mode"] ^ 0o100),
            ("nlink", baseline["nlink"] + 1),
        ):
            with self.subTest(field=field), \
                 mock.patch.object(MODULE, "_save_state",
                                   return_value={**baseline, field: value}), \
                 self.assertRaisesRegex(MODULE.EvidenceError, f"changed {field}"):
                MODULE._verify_save(
                    self.original, baseline, "original save after stop",
                    inode_required=True, metadata_required=True,
                )

        # The private QuickSave copy remains an evidence copy: its bytes and
        # digest are authoritative, while the original save alone receives the
        # stronger post-stop metadata contract above.
        with mock.patch.object(MODULE, "_save_state",
                               return_value={**baseline, "mode": baseline["mode"] ^ 0o100}):
            MODULE._verify_save(self.original, baseline, "private quicksave evidence copy",
                                inode_required=True)


if __name__ == "__main__":
    unittest.main()
