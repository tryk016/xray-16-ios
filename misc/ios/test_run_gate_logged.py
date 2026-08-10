#!/usr/bin/env python3
"""Private-temp contracts for the allowlisted logged gate runner."""
from __future__ import annotations
import importlib.util
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "misc/ios/run_gate_logged.py"
SPEC = importlib.util.spec_from_file_location("run_gate_logged", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def held_run(log_dir: str) -> None:
    spec = MODULE.GateSpec("test", (sys.executable, "-c", "import time; time.sleep(2)"), Path("/tmp/shared-build"), None)
    MODULE.run(spec, Path(log_dir))


class RunGateLoggedTests(unittest.TestCase):
    def setUp(self) -> None:
        # macOS /tmp traverses the /var compatibility symlink; the production
        # runner deliberately rejects that.  Keep these private fixtures below
        # the real home-directory path instead.
        self.temp = tempfile.TemporaryDirectory(dir=str(Path.home()))
        self.path = Path(self.temp.name)
        self.logs = self.path / "logs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def spec(self, body: str, stamp: Path | None = None, tree: str = "/tmp/build"):
        return MODULE.GateSpec("test", (sys.executable, "-c", body), Path(tree), stamp)

    def metadata(self) -> dict:
        files = sorted(self.logs.glob("*.json"))
        self.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text())

    def test_pass_private_full_log_detail_env_and_no_stamp_write(self) -> None:
        stamp = self.path / "underlying-stamp"
        stamp.write_text("gate-owned", encoding="utf-8")
        code = MODULE.run(self.spec("import os,pathlib; pathlib.Path(os.environ['OPENXRAY_GATE_DETAIL_DIR']).joinpath('seen').write_text('yes'); print('full output')", stamp), self.logs)
        self.assertEqual(code, 0)
        metadata = self.metadata()
        self.assertEqual(Path(metadata["log_path"]).read_text(), "full output\n")
        self.assertTrue((Path(metadata["detail_dir"]) / "seen").exists())
        self.assertEqual(stamp.read_text(), "gate-owned")
        self.assertEqual(metadata["underlying_stamp"]["sha256"], MODULE.sha(stamp))
        self.assertNotIn("command", metadata)
        self.assertEqual(stat_mode(Path(metadata["log_path"])), 0o600)
        self.assertEqual(stat_mode(self.logs), 0o700)

    def test_failure_preserves_exit_and_never_snapshots_stamp(self) -> None:
        stamp = self.path / "underlying-stamp"
        stamp.write_text("old", encoding="utf-8")
        code = MODULE.run(self.spec("import sys; print('fatal: causal failure'); sys.exit(23)", stamp), self.logs)
        self.assertEqual(code, 23)
        metadata = self.metadata()
        self.assertIsNone(metadata["underlying_stamp"])
        self.assertEqual(stamp.read_text(), "old")

    def test_lock_is_nonblocking_and_log_names_do_not_clobber(self) -> None:
        worker = multiprocessing.Process(target=held_run, args=(str(self.logs),))
        worker.start()
        for _ in range(50):
            if list(self.logs.glob("*.lock")): break
            time.sleep(.02)
        busy = MODULE.run(self.spec("print('other')", tree="/tmp/shared-build"), self.logs)
        self.assertEqual(busy, 75)
        worker.join(5)
        self.assertEqual(worker.exitcode, 0)
        self.assertEqual(MODULE.run(self.spec("print('one')", tree="/tmp/other-build"), self.logs), 0)
        self.assertEqual(MODULE.run(self.spec("print('two')", tree="/tmp/third-build"), self.logs), 0)
        logs = list(self.logs.glob("*.log"))
        self.assertEqual(len(logs), len({path.name for path in logs}))

    def test_signal_forwards_and_returns_143(self) -> None:
        script = f"""import importlib.util, pathlib, sys
spec0=importlib.util.spec_from_file_location('runner',{str(TOOL)!r}); module=importlib.util.module_from_spec(spec0); sys.modules['runner']=module; spec0.loader.exec_module(module)
raise SystemExit(module.run(module.GateSpec('signal',(sys.executable,'-c',"import os,pathlib,time; pathlib.Path(os.environ['OPENXRAY_GATE_DETAIL_DIR']).joinpath('ready').write_text('ready'); time.sleep(20)"),pathlib.Path('/tmp/signal-build'),None),pathlib.Path({str(self.logs)!r})))
"""
        process = subprocess.Popen((sys.executable, "-c", script))
        for _ in range(100):
            if list(self.logs.glob("detail-*/ready")):
                break
            time.sleep(.02)
        self.assertTrue(list(self.logs.glob("detail-*/ready")))
        time.sleep(.05)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=5), 143)
        self.assertEqual(self.metadata()["exit_code"], 143)

    def test_state_binds_tracked_and_untracked_content(self) -> None:
        repo = self.path / "state-repo"
        repo.mkdir()
        (repo / "tracked.txt").write_text("one", encoding="utf-8")
        subprocess.run(("git", "init", "-q", str(repo)), check=True)
        subprocess.run(("git", "-C", str(repo), "add", "tracked.txt"), check=True)
        subprocess.run(("git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "fixture"), check=True)
        clean = MODULE.state(repo)
        (repo / "tracked.txt").write_text("two", encoding="utf-8")
        tracked_changed = MODULE.state(repo)
        self.assertNotEqual(clean["diff_binary_head_sha256"], tracked_changed["diff_binary_head_sha256"])
        (repo / "untracked.txt").write_text("one", encoding="utf-8")
        untracked_one = MODULE.state(repo)
        (repo / "untracked.txt").write_text("two", encoding="utf-8")
        untracked_two = MODULE.state(repo)
        self.assertEqual(untracked_one["status_sha256"], untracked_two["status_sha256"])
        self.assertNotEqual(untracked_one["untracked"], untracked_two["untracked"])

    def test_excerpt_skips_green_summaries_for_causal_error(self) -> None:
        log = self.path / "excerpt.log"
        log.write_text("0 failures\nPASS summary\nfatal: actual cause\n", encoding="utf-8")
        descriptor = os.open(log, os.O_RDONLY)
        try:
            self.assertIn("fatal: actual cause", MODULE.excerpt_fd(descriptor))
        finally:
            os.close(descriptor)

    def test_atomic_metadata_short_writes_no_clobber_and_private_directory_policy(self) -> None:
        private = self.path / "private"
        MODULE.private_dir(private)
        target = private / "metadata.json"
        original_write = MODULE.os.write
        with mock.patch.object(MODULE.os, "write", side_effect=lambda fd, value: original_write(fd, value[:1])):
            MODULE.write_atomic(target, b"complete payload")
        self.assertEqual(target.read_bytes(), b"complete payload")
        with self.assertRaises(FileExistsError):
            MODULE.write_atomic(target, b"replacement")
        self.assertEqual(target.read_bytes(), b"complete payload")
        existing = self.path / "existing"
        existing.mkdir(mode=0o755)
        os.chmod(existing, 0o755)
        with self.assertRaises(RuntimeError):
            MODULE.private_dir(existing)
        real = self.path / "real"
        real.mkdir(mode=0o700)
        link = self.path / "link"
        link.symlink_to(real.name, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            MODULE.private_dir(link / "child")

    def test_reserved_log_descriptor_cannot_be_redirected_by_name_swap(self) -> None:
        private = self.path / "private"
        MODULE.private_dir(private)
        reserved, descriptor = MODULE.reserve_unique(private, ".log")
        victim = private / "victim"
        victim.write_text("unchanged", encoding="utf-8")
        reserved.unlink()
        reserved.symlink_to(victim.name)
        os.write(descriptor, b"private log bytes")
        os.close(descriptor)
        self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")

    def test_name_swap_fails_closed_without_hashing_or_exposing_victim(self) -> None:
        captured: list[Path] = []
        original = MODULE.reserve_unique
        def reserve(path: Path, suffix: str):
            result = original(path, suffix)
            if suffix == ".log":
                captured.append(result[0])
            return result
        victim = self.path / "victim"
        victim.write_text("fatal: victim secret", encoding="utf-8")
        class SwapChild:
            pid = 12345
            def wait(inner) -> int:
                self.assertTrue(captured)
                captured[0].unlink()
                captured[0].symlink_to(victim.name)
                return 0
        original_popen = MODULE.subprocess.Popen
        def fake_popen(*args, **kwargs):
            return SwapChild() if kwargs.get("start_new_session") else original_popen(*args, **kwargs)
        with mock.patch.object(MODULE, "reserve_unique", side_effect=reserve), \
             mock.patch.object(MODULE.subprocess, "Popen", side_effect=fake_popen):
            self.assertEqual(MODULE.run(self.spec("print('unused')"), self.logs), 1)
        metadata = self.metadata()
        self.assertTrue(metadata["log_identity_mismatch"])
        self.assertIsNone(metadata["log_path"])
        self.assertIsNone(metadata["log_sha256"])
        self.assertNotIn("victim secret", json.dumps(metadata))
        self.assertEqual(victim.read_text(encoding="utf-8"), "fatal: victim secret")


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main(verbosity=2)
