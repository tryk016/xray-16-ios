#!/usr/bin/env python3
"""Host-only mutation tests for isolated Simulator QuickSave/QuickLoad evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "misc/ios/simulator_quickload_evidence.py"
sys.path.insert(0, str(SCRIPT.parent))


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
        self.path = Path(self.tmp.name)
        self.docs = self.path / "Documents"
        self.appdata = self.docs / "_appdata_"
        self.saves = self.appdata / "savedgames"
        self.saves.mkdir(parents=True)
        self.original = self.saves / "mobile user - beginning of the game.scop"
        self.original.write_bytes(b"original-save")
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
        self.qsave = "11111111-1111-4111-8111-111111111111"
        self.qload = "22222222-2222-4222-8222-222222222222"
        self._write_capture(capture_value(self.pid, 1, 20, 1, 115, none_input()))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_capture(self, value: dict) -> None:
        self.meta.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        self.shot.write_bytes(ppm(value["capture"]["token"]))

    def _run_args(self) -> argparse.Namespace:
        return argparse.Namespace(documents=str(self.docs), log=str(self.log), expected_pid=self.pid,
                                  staged_manifest=str(self.staged), root=str(self.root),
                                  original_save=str(self.original), timeout=1.0, poll=0.01)

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
                    out.write(f"* iOS diag: autoinput request {self.qload} released scancode 66\n")
                    out.write(marker(self.pid, 2, 60, "quick_load") + "\n")
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
        MODULE.finalize(self._run_args())
        pending = self.root / "manifest.pending.json"
        self.assertTrue(pending.is_file())
        fields = (self.root / "report-fields.txt").read_bytes()
        report_pending, report, manifest = self.path / "report.pending", self.path / "report.txt", self.root / "manifest.json"
        report_pending.write_bytes(b"result=PASS\n" + fields)
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
            manifest_value["quicksave"]["sha256"],
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
        report_pending = self.path / "report.pending"
        report_pending.write_text("result=PASS\n", encoding="ascii")
        with self.assertRaises(MODULE.EvidenceError):
            MODULE.publish(argparse.Namespace(pending_manifest=str(pending), manifest=str(self.path / "manifest.json"),
                                              report_pending=str(report_pending), report=str(self.path / "report.txt")))
        self.assertFalse((self.path / "manifest.json").exists())

    def test_publish_rolls_back_only_its_owned_manifest_inode(self) -> None:
        pending = self.path / "pending.json"
        payload = b'{"schema":"test"}\n'
        pending.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        report_pending = self.path / "report.pending"
        report_pending.write_text(f"quickload_manifest_sha256={digest}\n", encoding="ascii")
        manifest, report = self.path / "manifest.json", self.path / "report.txt"
        real_link = MODULE.os.link
        calls = 0

        def raced_link(source, destination, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                manifest.unlink()
                manifest.write_bytes(b"attacker replacement\n")
                raise OSError("injected report-link failure")
            return real_link(source, destination, **kwargs)

        with mock.patch.object(MODULE.os, "link", side_effect=raced_link):
            with self.assertRaises(OSError):
                MODULE.publish(argparse.Namespace(pending_manifest=str(pending), manifest=str(manifest),
                                                  report_pending=str(report_pending), report=str(report)))
        self.assertEqual(manifest.read_bytes(), b"attacker replacement\n")
        self.assertFalse(report.exists())

    def test_publish_rolls_back_its_own_report_after_content_race(self) -> None:
        pending = self.path / "pending.json"
        payload = b'{"schema":"test"}\n'
        pending.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        report_pending = self.path / "report.pending"
        report_payload = f"quickload_manifest_sha256={digest}\n".encode("ascii")
        report_pending.write_bytes(report_payload)
        manifest, report = self.path / "manifest.json", self.path / "report.txt"
        real_link, calls = MODULE.os.link, 0

        def raced_link(source, destination, **kwargs):
            nonlocal calls
            calls += 1
            result = real_link(source, destination, **kwargs)
            if calls == 2:
                report.write_bytes(b"changed after report hardlink\n")
            return result

        with mock.patch.object(MODULE.os, "link", side_effect=raced_link):
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.publish(argparse.Namespace(pending_manifest=str(pending), manifest=str(manifest),
                                                  report_pending=str(report_pending), report=str(report)))
        self.assertFalse(manifest.exists())
        self.assertFalse(report.exists())

    def test_publish_interruption_before_report_leaves_no_pass_artifact(self) -> None:
        pending = self.path / "pending.json"
        payload = b'{"artifact":"post-stop-revalidated-evidence"}\n'
        pending.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        report_pending = self.path / "report.pending"
        report_pending.write_text(
            f"result=PASS\nquickload_manifest_sha256={digest}\n", encoding="ascii",
        )
        manifest, report = self.path / "manifest.json", self.path / "report.txt"
        real_link, calls = MODULE.os.link, 0

        def interrupted_link(source, destination, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return real_link(source, destination, **kwargs)

        with mock.patch.object(MODULE.os, "link", side_effect=interrupted_link):
            with self.assertRaises(KeyboardInterrupt):
                MODULE.publish(argparse.Namespace(
                    pending_manifest=str(pending), manifest=str(manifest),
                    report_pending=str(report_pending), report=str(report),
                ))
        self.assertTrue(manifest.is_file())
        self.assertNotIn(b'"result":"PASS"', manifest.read_bytes())
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


if __name__ == "__main__":
    unittest.main()
