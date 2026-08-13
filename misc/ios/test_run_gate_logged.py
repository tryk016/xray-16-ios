#!/usr/bin/env python3
"""Private-temp contracts for the allowlisted logged gate runner."""
from __future__ import annotations
import importlib.util
import json
import multiprocessing
import os
import signal
import stat
import subprocess
import sys
if os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import test_feedback_unittest
        test_feedback_unittest.install_from_environment(
            "python::misc/ios/test_run_gate_logged.py", sys.argv)
    except BaseException:
        pass
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "misc/ios/run_gate_logged.py"
RECEIPT_FIELDS = frozenset({
    "schema", "gate", "started_unix", "ended_unix", "exit_code", "before",
    "after", "log_path", "log_sha256", "log_identity_mismatch", "detail_dir",
    "underlying_stamp",
})
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
        value = json.loads(files[0].read_text())
        self.assertEqual(set(value), RECEIPT_FIELDS)
        self.assertNotIn("test_feedback", value)
        return value

    class Feedback:
        def __init__(self, *, initialize_error: bool = False,
                     initialize_result: bool = True,
                     finalize_error: bool = False,
                     finalize_base_exception: type[BaseException] | None = None) -> None:
            self.initialize_error = initialize_error
            self.initialize_result = initialize_result
            self.finalize_error = finalize_error
            self.finalize_base_exception = finalize_base_exception
            self.initialized: list[tuple[Path, str, str, str]] = []
            self.finalized: list[tuple[Path, str, str, str, int]] = []

        def runtime_initialize(self, directory: Path, profile: str,
                               run_id: str, nonce: str) -> bool:
            if self.initialize_error:
                raise RuntimeError("forced initialize failure")
            directory.mkdir(mode=0o700)
            self.initialized.append((directory, profile, run_id, nonce))
            return self.initialize_result

        def runtime_finalize(self, directory: Path, profile: str, run_id: str,
                             nonce: str, exit_code: int) -> dict:
            self.finalized.append((directory, profile, run_id, nonce, exit_code))
            if self.finalize_error:
                raise RuntimeError("forced finalize failure")
            if self.finalize_base_exception is not None:
                raise self.finalize_base_exception("forced finalize base exception")
            (directory / "run.json").write_text(
                json.dumps({"run_id": run_id, "exit_code": exit_code}), encoding="utf-8")
            return {"status": "COMPLETE"}

    def run_observed(self, body: str, *, exit_code: int = 0,
                     telemetry: bool = True, feedback=None, env=None):
        captured: list[dict] = []
        original_popen = MODULE.subprocess.Popen

        def observe(*args, **kwargs):
            if kwargs.get("start_new_session"):
                captured.append({"args": args[0], "cwd": kwargs["cwd"], "env": dict(kwargs["env"])})
            return original_popen(*args, **kwargs)

        with mock.patch.object(MODULE.subprocess, "Popen", side_effect=observe), \
             mock.patch.object(MODULE, "feedback_module", return_value=feedback) if feedback else mock.patch.object(MODULE, "feedback_module", side_effect=AssertionError("unexpected feedback import")), \
             mock.patch.dict(os.environ, env or {}, clear=False):
            code = MODULE.run(self.spec(body), self.logs, telemetry=telemetry)
        self.assertEqual(code, exit_code)
        self.assertEqual(len(captured), 1)
        return captured[0], self.metadata()

    def assert_receipt_v2_shape_is_historical_and_telemetry_is_private(self) -> None:
        self.logs = self.path / "logs-receipt-contract"
        observer = self.Feedback()
        _, receipt = self.run_observed("print('observed')", feedback=observer)
        self.assertEqual(len(observer.initialized), 1)
        telemetry_dir = Path(receipt["detail_dir"]) / "test-feedback"
        self.assertEqual(observer.initialized[0][0], telemetry_dir)
        self.assertTrue((telemetry_dir / "run.json").is_file())
        self.assertFalse(any(self.logs.glob("*test-feedback*")))

        context_dir = self.path / "context-fd"
        context_dir.mkdir(mode=0o700)
        descriptor = MODULE.feedback_context_fd(
            context_dir, "engine", "readable-context", "n" * 64)
        try:
            with self.assertRaises(OSError):
                os.write(descriptor, b"must remain read-only")
            self.assertEqual(
                os.read(descriptor, 4096),
                f"{context_dir}\n{'n' * 64}\nreadable-context\nengine\n".encode(),
            )
            details = os.fstat(descriptor)
            self.assertTrue(stat.S_ISREG(details.st_mode))
            self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
            self.assertEqual(list(context_dir.iterdir()), [])
        finally:
            os.close(descriptor)

    def assert_on_off_parity_preserves_command_cwd_output_and_pythonpath(self) -> None:
        baseline = self.path / "preexisting-pythonpath"
        baseline.mkdir()
        self.logs = self.path / "logs-environment-off"
        body = "import json,os,sys; print(json.dumps({'path':os.environ.get('PYTHONPATH'), 'sitecustomize': 'sitecustomize' in sys.modules, 'sys_path': sys.path, 'context': sorted(k for k in os.environ if k.startswith('OPENXRAY_TEST_FEEDBACK_'))}, sort_keys=True))"
        off, off_receipt = self.run_observed(body, telemetry=False,
                                             env={"PYTHONPATH": str(baseline)})
        off_log = json.loads(Path(off_receipt["log_path"]).read_text())
        self.logs = self.path / "logs-environment-on"
        on_observer = self.Feedback()
        on, on_receipt = self.run_observed(body, telemetry=True, feedback=on_observer,
                                           env={"PYTHONPATH": str(baseline)})
        self.assertEqual(off["args"], on["args"])
        self.assertEqual(off["cwd"], on["cwd"])
        self.assertEqual(off["env"]["PYTHONPATH"], str(baseline))
        self.assertEqual(on["env"]["PYTHONPATH"], str(baseline))
        # Arbitrary commands are never trusted as a top-level gate shell, so
        # ON cannot hand them either a secret context or a descriptor marker.
        self.assertEqual(set(on["env"]) - set(off["env"]), set())
        # The actual runner launches top-level scripts via /bin/bash.  It must
        # remove every startup/tracing control in both modes, before it adds a
        # trusted context FD for an allowlisted shell.
        for name in ("SHELLOPTS", "BASHOPTS", "PS4", "BASH_XTRACEFD", "BASH_ENV", "ENV"):
            self.assertNotIn(name, off["env"])
            self.assertNotIn(name, on["env"])
        self.assertEqual(set(off_receipt), set(on_receipt))
        on_log = json.loads(Path(on_receipt["log_path"]).read_text())
        self.assertEqual(off_log["path"], str(baseline))
        self.assertEqual(on_log["path"], str(baseline))
        self.assertEqual(off_log["sitecustomize"], on_log["sitecustomize"])
        self.assertEqual(off_log["sys_path"], on_log["sys_path"])
        self.assertNotIn(str(ROOT / "misc/ios/test_feedback_bootstrap"), on_log["sys_path"])
        self.assertEqual(off_log["context"], [])

        # BASH_ENV is stripped before *every* top-level child in both modes.
        # The startup hook would record its process environment if it ran.
        hook = self.path / "hostile-bash-env.sh"
        captured = self.path / "hostile-bash-env.json"
        hook.write_text(
            "python3 -c 'import json,os; from pathlib import Path; "
            f"Path({str(captured)!r}).write_text(json.dumps(dict(os.environ)))'\n",
            encoding="utf-8")
        script = self.path / "ordinary-gate.sh"
        script.write_text("printf 'ordinary-shell\\n'\n", encoding="utf-8")
        for telemetry in (False, True):
            with self.subTest(telemetry=telemetry):
                captured.unlink(missing_ok=True)
                gate = MODULE.GateSpec("ordinary", (str(script),), Path("/tmp/ordinary-shell"), None)
                hostile = {
                    "BASH_ENV": str(hook), "ENV": str(hook),
                    "SHELLOPTS": "allexport:xtrace", "BASHOPTS": "extglob",
                    "PS4": "HOSTILE-XTRACE ", "BASH_XTRACEFD": "199",
                    "BASH_FUNC_dirname%%": "() { :; }",
                    "BASH_FUNC_python3%%": "() { :; }",
                }
                with mock.patch.dict(os.environ, hostile, clear=False):
                    self.assertEqual(MODULE.run(gate, self.path / f"bash-env-{telemetry}",
                                                telemetry=telemetry), 0)
                self.assertFalse(captured.exists())
        self.assertEqual(on_log["context"], [])

        # Exercise the real runner's allowlisted-Bash path without running a
        # build.  ON may pass one unlinked context descriptor to that sole
        # trusted shell; OFF must have the same scrubbed startup environment
        # and no inherited descriptor.  The hostile xtrace descriptor is not
        # passed through ``pass_fds`` and cannot reach the child.
        class NoBuildChild:
            pid = os.getpid()
            def wait(inner) -> int:
                return 0

        def capture_trusted(telemetry: bool) -> dict:
            launches: list[dict] = []
            original_popen = MODULE.subprocess.Popen
            def fake_popen(*args, **kwargs):
                if kwargs.get("start_new_session"):
                    launches.append({"args": args[0], **kwargs})
                    return NoBuildChild()
                return original_popen(*args, **kwargs)
            self.logs = self.path / f"trusted-shell-{telemetry}"
            gate = MODULE.GateSpec("engine", (str(ROOT / "misc/ios/build_check.sh"), "--shaders"),
                                   Path("/tmp/trusted-shell"), None)
            with mock.patch.object(MODULE.subprocess, "Popen", side_effect=fake_popen), \
                 mock.patch.dict(os.environ, hostile, clear=False):
                self.assertEqual(MODULE.run(gate, self.logs, telemetry=telemetry), 0)
            self.assertEqual(len(launches), 1)
            return launches[0]

        trusted_off = capture_trusted(False)
        trusted_on = capture_trusted(True)
        self.assertEqual(trusted_off["args"][:2],
                         ("/bin/bash", str(ROOT / "misc/ios/build_check.sh")))
        self.assertEqual(trusted_off["args"], trusted_on["args"])
        for launch in (trusted_off, trusted_on):
            self.assertTrue(launch["close_fds"])
            for name in ("SHELLOPTS", "BASHOPTS", "PS4", "BASH_XTRACEFD", "BASH_ENV", "ENV"):
                self.assertNotIn(name, launch["env"])
            self.assertFalse([name for name in launch["env"]
                              if name.startswith("BASH_FUNC_") and name.endswith("%%")])
        self.assertEqual(trusted_off["pass_fds"], ())
        self.assertNotIn("XRAY_FEEDBACK_CONTEXT_FD", trusted_off["env"])
        self.assertEqual(len(trusted_on["pass_fds"]), 1)
        self.assertEqual(trusted_on["env"]["XRAY_FEEDBACK_CONTEXT_FD"],
                         str(trusted_on["pass_fds"][0]))

    def assert_on_off_exit_parity_and_telemetry_failures_preserve_first_failure(self) -> None:
        for expected in (0, 1, 23):
            with self.subTest(exit_code=expected):
                self.logs = self.path / f"logs-off-{expected}"
                body = f"import sys; print('causal-{expected}'); sys.exit({expected})"
                off, off_receipt = self.run_observed(body, exit_code=expected, telemetry=False)
                off_log = Path(off_receipt["log_path"]).read_bytes()
                self.logs = self.path / f"logs-on-{expected}"
                observer = self.Feedback()
                on, on_receipt = self.run_observed(body, exit_code=expected, feedback=observer)
                self.assertEqual(off["args"], on["args"])
                self.assertEqual(off["cwd"], on["cwd"])
                self.assertEqual(off_log, Path(on_receipt["log_path"]).read_bytes())
                self.assertEqual(off_receipt["log_sha256"], on_receipt["log_sha256"])
                self.assertEqual(off_receipt["underlying_stamp"], on_receipt["underlying_stamp"])
                self.assertEqual(observer.finalized[0][-1], expected)
        for number, observer in enumerate((None, self.Feedback(initialize_error=True),
                                           self.Feedback(initialize_result=False),
                                           self.Feedback(finalize_error=True),
                                           self.Feedback(finalize_base_exception=KeyboardInterrupt),
                                           self.Feedback(finalize_base_exception=SystemExit)), 1):
            with self.subTest(failure="import" if observer is None else type(observer).__name__,
                              init=False if observer is None else observer.initialize_error,
                              ready=False if observer is None else observer.initialize_result,
                              final=False if observer is None else observer.finalize_error):
                body = "import sys; print('first-cause'); sys.exit(23)"
                self.logs = self.path / f"logs-failure-off-{number}"
                off, off_receipt = self.run_observed(body, exit_code=23, telemetry=False)
                off_log = Path(off_receipt["log_path"]).read_bytes()
                self.logs = self.path / f"logs-failure-on-{number}"
                on, receipt = self.run_observed(body, exit_code=23, feedback=observer)
                self.assertEqual(off["args"], on["args"])
                self.assertEqual(off["cwd"], on["cwd"])
                self.assertEqual(receipt["exit_code"], 23)
                self.assertEqual(off_log, Path(receipt["log_path"]).read_bytes())
                self.assertEqual(off_receipt["log_sha256"], receipt["log_sha256"])
                self.assertIn(b"first-cause", off_log)
                self.assertIsNone(receipt["underlying_stamp"])

    def assert_default_cli_enables_and_explicit_cli_disables_telemetry(self) -> None:
        for args, expected in ((["engine"], True), (["--telemetry", "off", "engine"], False)):
            with self.subTest(args=args):
                with mock.patch.object(MODULE, "run", return_value=0) as run, \
                     mock.patch.object(sys, "argv", [str(TOOL), *args]):
                    self.assertEqual(MODULE.main(), 0)
                self.assertIs(run.call_args.kwargs["telemetry"], expected)

    def assert_actual_feedback_module_bounded_import_smoke(self) -> None:
        feedback = MODULE.feedback_module()
        self.assertTrue(callable(feedback.runtime_initialize))
        self.assertTrue(callable(feedback.runtime_finalize))
        directory = self.path / "actual-runtime-smoke"
        initialized = feedback.runtime_initialize(directory, "engine", "smoke", "n" * 64)
        self.assertIsInstance(initialized, bool)
        if initialized:
            result = feedback.runtime_finalize(directory, "engine", "smoke", "n" * 64, 0)
            self.assertIn(result["status"], {"INCOMPLETE", "ERROR"})

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
        self.assert_receipt_v2_shape_is_historical_and_telemetry_is_private()
        self.assert_on_off_parity_preserves_command_cwd_output_and_pythonpath()
        self.assert_default_cli_enables_and_explicit_cli_disables_telemetry()
        self.assert_actual_feedback_module_bounded_import_smoke()

    def test_failure_preserves_exit_and_never_snapshots_stamp(self) -> None:
        stamp = self.path / "underlying-stamp"
        stamp.write_text("old", encoding="utf-8")
        code = MODULE.run(self.spec("import sys; print('fatal: causal failure'); sys.exit(23)", stamp), self.logs)
        self.assertEqual(code, 23)
        metadata = self.metadata()
        self.assertIsNone(metadata["underlying_stamp"])
        self.assertEqual(stamp.read_text(), "old")
        self.assert_on_off_exit_parity_and_telemetry_failures_preserve_first_failure()

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
        def invoke(signum: int, telemetry: bool) -> tuple[int, dict, bytes]:
            log_dir = self.path / f"signal-{signum}-{telemetry}"
            script = f"""import importlib.util, json, pathlib, sys
spec0=importlib.util.spec_from_file_location('runner',{str(TOOL)!r}); module=importlib.util.module_from_spec(spec0); sys.modules['runner']=module; spec0.loader.exec_module(module)
class Feedback:
 def runtime_initialize(self,directory,profile,run_id,nonce): directory.mkdir(mode=0o700); return True
 def runtime_finalize(self,directory,profile,run_id,nonce,exit_code): (directory/'run.json').write_text(json.dumps({{'exit_code':exit_code}})); return {{'status':'COMPLETE'}}
module.feedback_module=lambda: Feedback()
command=(sys.executable,'-c',"import os,pathlib,time; pathlib.Path(os.environ['OPENXRAY_GATE_DETAIL_DIR']).joinpath('ready').write_text('ready'); time.sleep(20)")
raise SystemExit(module.run(module.GateSpec('signal',command,pathlib.Path('/tmp/signal-build'),None),pathlib.Path({str(log_dir)!r}),telemetry={telemetry!r}))
"""
            process = subprocess.Popen((sys.executable, "-B", "-c", script))
            for _ in range(100):
                if list(log_dir.glob("detail-*/ready")):
                    break
                time.sleep(.02)
            self.assertTrue(list(log_dir.glob("detail-*/ready")))
            process.send_signal(signum)
            expected = 130 if signum == signal.SIGINT else 143
            self.assertEqual(process.wait(timeout=5), expected)
            receipt_files = sorted(log_dir.glob("*.json"))
            self.assertEqual(len(receipt_files), 1)
            receipt = json.loads(receipt_files[0].read_text())
            self.assertEqual(set(receipt), RECEIPT_FIELDS)
            self.assertIsNone(receipt["underlying_stamp"])
            return expected, receipt, Path(receipt["log_path"]).read_bytes()

        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                off_code, off_receipt, off_log = invoke(signum, False)
                on_code, on_receipt, on_log = invoke(signum, True)
                self.assertEqual(on_code, off_code)
                self.assertEqual(on_receipt["exit_code"], off_receipt["exit_code"])
                self.assertEqual(on_log, off_log)

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
