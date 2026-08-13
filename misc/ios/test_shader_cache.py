#!/usr/bin/env python3
"""Persistent contracts for the fail-closed immutable iOS shader cache."""
from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment(
            "python::misc/ios/test_shader_cache.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import select
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import gate_hash
import shader_cache


REPO = Path(__file__).resolve().parents[2]
COMPILE_OK = (
    b"GLSL ES 3.00 shader check: 279/279 compile, 0 fail (0 include-only skipped)\n"
    b"GLSL ES 3.00 low-settings profile: 2/2 compile, 0 fail\n"
    b"GLSL ES 3.00 SSAO branch profile: 6/6 compile, 0 fail\n"
    b"GLSL ES 3.00 SSR branch profile: 9/9 compile, 0 fail\n"
    b"SSAO value-macro contract: PASS\n"
    b"Numeric feature-macro contract: PASS (five zero fallbacks; presence debt=0; undef debt=0)\n"
)
LINK_OK = b"vs->fs link check: 137/137 pairs clean, 0 with issues\n"


def _counter_command(counter: Path, payload: bytes = COMPILE_OK, *, mutate: Path | None = None,
                     status: int = 0) -> list[str]:
    code = (
        "import os,pathlib,sys; "
        "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600); "
        "os.write(fd,b'x\\n'); os.close(fd); "
        "pathlib.Path(sys.argv[2]).write_text('changed') if sys.argv[2] else None; "
        "sys.stdout.buffer.write(bytes.fromhex(sys.argv[3])); raise SystemExit(int(sys.argv[4]))"
    )
    return [sys.executable, "-c", code, str(counter), str(mutate or ""), payload.hex(), str(status)]


def _collision_worker(root: str, source: str, salt: str, key: str, counter: str,
                      barrier, result) -> None:
    try:
        barrier.wait(timeout=20)
        value = shader_cache.ShaderCache(root, allow_test_root=True).run(
            stage="compile", key=key, salt=salt, inputs=[source],
            command=_counter_command(Path(counter)), cwd=REPO)
        result.put(("ok", value.cache_hit))
    except BaseException as error:
        result.put(("error", type(error).__name__, str(error)))


def _different_key_worker(root: str, source: str, salt: str, key: str, counter: str,
                          barrier, result) -> None:
    try:
        def wait_at_checker(point: str) -> None:
            if point == "before-checker":
                barrier.wait(timeout=20)
        value = shader_cache.ShaderCache(root, allow_test_root=True).run(
            stage="compile", key=key, salt=salt, inputs=[source],
            command=_counter_command(Path(counter)), cwd=REPO, hook=wait_at_checker)
        result.put(("ok", value.cache_hit))
    except BaseException as error:
        result.put(("error", type(error).__name__, str(error)))


def _signal_worker(root: str, source: str, salt: str, key: str, ready: str, wait: str, result) -> None:
    command = [sys.executable, "-c", ("import sys; open(sys.argv[1],'wb',buffering=0).write(b'R'); "
                                         "open(sys.argv[2],'rb',buffering=0).read(1)"), ready, wait]
    try:
        shader_cache.ShaderCache(root, allow_test_root=True).run(
            stage="compile", key=key, salt=salt, inputs=[source], command=command, cwd=REPO)
        result.put(("unexpected", None))
    except BaseException as error:
        result.put((type(error).__name__, getattr(error, "signum", None)))


def _raw_emitting_checker(ids_path: Path, payload: bytes) -> list[str]:
    """Emit IDs supplied by the real --list-json producers, then a valid transcript."""
    code = (
        "import json,sys,time; sys.path.insert(0,sys.argv[1]); "
        "from test_feedback_unittest import install_from_environment,emit_raw; "
        "install_from_environment('shader-cache-contract',sys.argv); "
        "data=json.load(open(sys.argv[2],encoding='utf-8')); "
        "[(_ for _ in ()).throw(RuntimeError('raw emit failed')) if not emit_raw(i,'PASS',time.monotonic_ns()) else None for i in data['ids']]; "
        "sys.stdout.buffer.write(bytes.fromhex(sys.argv[3]))"
    )
    return [sys.executable, "-c", code, str(REPO / "misc/ios"), str(ids_path), payload.hex()]


def _real_list_ids(stage: str) -> list[str]:
    """Read the current public list producer, never a parsed source fixture."""
    checker = (REPO / "misc/ios/shadercheck/glsl_es_check.py" if stage == "compile"
               else REPO / "misc/ios/shadercheck/link_check.py")
    completed = subprocess.run([sys.executable, str(checker), "--list-json"], cwd=REPO,
                               text=True, capture_output=True, check=False, timeout=30,
                               env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"})
    if completed.returncode != 0:
        raise AssertionError(f"{stage} list producer failed: {completed.stderr}")
    value = json.loads(completed.stdout)
    if stage == "compile":
        if value.get("schema") != "openxray.shader-case-list.v1":
            raise AssertionError("compile list schema changed")
        identifiers = value.get("baseline", []) + value.get("variants", [])
    else:
        if value.get("schema") != "openxray.shader-link-list.v1":
            raise AssertionError("link list schema changed")
        identifiers = value.get("links", [])
    if not isinstance(identifiers, list) or not all(isinstance(item, str) for item in identifiers):
        raise AssertionError(f"{stage} list identifiers are invalid")
    return identifiers


def _helper_subprocess(config_path: Path) -> list[str]:
    """Run the actual helper implementation in a process which inherits raw FD."""
    code = (
        "import json,sys; sys.path.insert(0,sys.argv[1]); import shader_cache; "
        "c=json.load(open(sys.argv[2],encoding='utf-8')); "
        "r=shader_cache.ShaderCache(c['root'],allow_test_root=True).run("
        "stage=c['stage'],key=c['key'],salt=c['salt'],inputs=[c['input']],"
        "command=c['command'],cwd=c['cwd']); "
        "print(json.dumps({'hit':r.cache_hit,'direct':r.direct,'payload':r.payload.hex(),"
        "'output_path':str(r.output_path or '')},sort_keys=True))"
    )
    return [sys.executable, "-c", code, str(REPO / "misc/ios"), str(config_path)]


class ShaderCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openxray-shader-cache-")
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "repo/build/ios-engine-iphoneos/.ios_gate_cache"
        self.root.parent.mkdir(parents=True, mode=0o700)
        self.root.parent.chmod(0o700)
        self.source = self.base / "input.txt"
        self.source.write_text("input\n", encoding="utf-8")
        self.counter = self.base / "counter"
        self.salt = "shader-cache-contract-v2"
        self.key = gate_hash.digest_paths(self.salt, [str(self.source)])
        self.cache = shader_cache.ShaderCache(self.root, allow_test_root=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def cache_run(self, *, stage: str = "compile", key: str | None = None,
                  salt: str | None = None, inputs: list[str] | None = None,
                  command: list[str] | None = None, force_direct: bool = False,
                  hook=None, timeout: float = 300) -> shader_cache.CacheResult:
        selected_salt = salt or self.salt
        selected_inputs = inputs or [str(self.source)]
        selected_key = key or gate_hash.digest_paths(selected_salt, selected_inputs)
        return self.cache.run(stage=stage, key=selected_key, salt=selected_salt, inputs=selected_inputs,
                              command=command or _counter_command(
                                  self.counter, LINK_OK if stage == "link" else COMPILE_OK),
                              cwd=REPO, force_direct=force_direct, hook=hook, timeout=timeout)

    def final(self, stage: str = "compile", key: str | None = None) -> Path:
        return self.root / stage / f"{key or self.key}.out"

    def receipt(self, stage: str = "compile", key: str | None = None) -> Path:
        return self.root / "receipts" / stage / f"{key or self.key}.json"

    def _seed_entry(self, payload: bytes) -> None:
        """Construct a receipt-consistent but semantically invalid test entry."""
        self.cache_run()
        final, receipt = self.final(), self.receipt()
        final.chmod(0o600); final.unlink(); receipt.chmod(0o600); receipt.unlink()
        metadata = {"schema": shader_cache.SCHEMA, "stage": "compile", "key": self.key,
                    "payload_size": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest()}
        envelope = shader_cache.ENVELOPE_MAGIC + shader_cache._canonical(metadata) + b"\n" + payload
        final.write_bytes(envelope); final.chmod(0o400)
        detail = final.stat()
        value = {"schema": shader_cache.SCHEMA, "stage": "compile", "key": self.key,
                 "final": final.name, "temp": "0" * 32 + ".tmp",
                 "payload_sha256": metadata["payload_sha256"], "payload_size": len(payload),
                 "envelope_sha256": hashlib.sha256(envelope).hexdigest(), "envelope_size": len(envelope),
                 "file_dev": detail.st_dev, "file_ino": detail.st_ino, "file_mode": 0o400, "nlink": 1}
        receipt.write_bytes(shader_cache._canonical(value)); receipt.chmod(0o400)

    def _status_file(self, name: str, payload: bytes = b"") -> Path:
        path = self.base / name
        path.write_bytes(payload)
        path.chmod(0o600)
        return path

    @staticmethod
    def _status_result(*, cache_hit: bool = False, direct: bool = False,
                       output_path: Path | None = None) -> shader_cache.CacheResult:
        return shader_cache.CacheResult(payload=b"", cache_hit=cache_hit, direct=direct,
                                        output_path=output_path)

    def _assert_status_channel_contracts(self) -> None:
        """Exercise caller-owned status files without adding a new catalog case ID."""
        expected = b"cache_hit=1\ndirect=0\noutput_path=\n"
        empty = self._status_file("status-empty")
        empty_before = empty.stat()
        shader_cache._status(empty, self._status_result(cache_hit=True))
        empty_after = empty.stat()
        self.assertEqual(empty.read_bytes(), expected)
        self.assertEqual((empty_after.st_dev, empty_after.st_ino),
                         (empty_before.st_dev, empty_before.st_ino))

        nonempty = self._status_file("status-nonempty", b"obsolete trailing bytes")
        nonempty_before = nonempty.stat()
        shader_cache._status(nonempty, self._status_result(direct=True))
        nonempty_after = nonempty.stat()
        self.assertEqual(nonempty.read_bytes(), b"cache_hit=0\ndirect=1\noutput_path=\n")
        self.assertEqual((nonempty_after.st_dev, nonempty_after.st_ino),
                         (nonempty_before.st_dev, nonempty_before.st_ino))

        target = self._status_file("status-symlink-target", b"target")
        symlink = self.base / "status-symlink"
        symlink.symlink_to(target)
        with self.assertRaises(shader_cache.CacheError):
            shader_cache._status(symlink, self._status_result())
        self.assertEqual(target.read_bytes(), b"target")

        hardlinked = self._status_file("status-hardlink", b"hardlinked")
        os.link(hardlinked, hardlinked.with_name("status-hardlink-extra"))
        with self.assertRaises(shader_cache.CacheError):
            shader_cache._status(hardlinked, self._status_result())
        self.assertEqual(hardlinked.read_bytes(), b"hardlinked")

        wrong_mode = self._status_file("status-wrong-mode", b"mode")
        wrong_mode.chmod(0o640)
        with self.assertRaises(shader_cache.CacheError):
            shader_cache._status(wrong_mode, self._status_result())
        self.assertEqual(wrong_mode.read_bytes(), b"mode")

        foreign_owner = self._status_file("status-foreign-owner", b"owner")
        with mock.patch.object(shader_cache.os, "geteuid", return_value=foreign_owner.stat().st_uid + 1), \
             self.assertRaises(shader_cache.CacheError):
            shader_cache._status(foreign_owner, self._status_result())
        self.assertEqual(foreign_owner.read_bytes(), b"owner")

        replacement = self._status_file("status-replaced", b"original")
        replacement_payload = b"replacement must stay untouched"
        staged = self._status_file("status-replacement-staged", replacement_payload)
        real_open = shader_cache.os.open

        def replace_before_open(path, flags, *args, **kwargs):
            if os.fspath(path) == os.fspath(replacement) and flags & os.O_WRONLY:
                os.replace(staged, replacement)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(shader_cache.os, "open", side_effect=replace_before_open), \
             self.assertRaisesRegex(shader_cache.CacheError, "identity changed while opening"):
            shader_cache._status(replacement, self._status_result())
        self.assertEqual(replacement.read_bytes(), replacement_payload)

        linked_during_write = self._status_file("status-linked-during-write", b"link")
        extra_link = linked_during_write.with_name("status-linked-during-write-extra")
        real_write = shader_cache._write_all

        def add_link_during_write(descriptor: int, data: bytes) -> None:
            os.link(linked_during_write, extra_link)
            real_write(descriptor, data)

        with mock.patch.object(shader_cache, "_write_all", side_effect=add_link_during_write), \
             self.assertRaises(shader_cache.CacheError):
            shader_cache._status(linked_during_write, self._status_result())

        renamed_after_write = self._status_file("status-renamed-after-write", b"rename")
        renamed_payload = b"replacement after write"
        rename_staged = self._status_file("status-rename-staged", renamed_payload)
        real_fsync = shader_cache._fsync

        def replace_during_fsync(descriptor: int) -> None:
            os.replace(rename_staged, renamed_after_write)
            real_fsync(descriptor)

        with mock.patch.object(shader_cache, "_fsync", side_effect=replace_during_fsync), \
             self.assertRaises(shader_cache.CacheError):
            shader_cache._status(renamed_after_write, self._status_result())
        self.assertEqual(renamed_after_write.read_bytes(), renamed_payload)

        for label, patch_target, failure in (
                ("truncate", "ftruncate", OSError("truncate failed")),
                ("write", "write", OSError("write failed")),
                ("fsync", "fsync", OSError("fsync failed"))):
            with self.subTest(status_failure=label):
                failing = self._status_file(f"status-failure-{label}", b"before")
                with mock.patch.object(shader_cache.os, patch_target, side_effect=failure), \
                     self.assertRaises(shader_cache.CacheError):
                    shader_cache._status(failing, self._status_result())

        fake_repo = self.base / "status-cli-repository"
        fake_repo.mkdir(mode=0o700)
        cli_root = fake_repo / "build/ios-engine-iphoneos/.ios_gate_cache"
        cli_status = self._status_file("status-cli", b"old status")
        cli_status_before = cli_status.stat()
        launcher = (
            "import sys; sys.path.insert(0, sys.argv[1]); import shader_cache; "
            "shader_cache.REPO_ROOT=shader_cache.Path(sys.argv[2]); "
            "shader_cache.DEFAULT_ROOT=shader_cache.Path(sys.argv[3]); "
            "raise SystemExit(shader_cache.main(sys.argv[4:]))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", launcher, str(REPO / "misc/ios"), str(fake_repo), str(cli_root),
             "run", "--root", str(cli_root), "--stage", "compile", "--key", self.key,
             "--salt", self.salt, "--input", str(self.source), "--force-direct", "--status-file",
             str(cli_status), "--", sys.executable, "-c",
             "import sys; sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))", COMPILE_OK.hex()],
            cwd=REPO, text=True, capture_output=True, timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.encode("utf-8"), COMPILE_OK)
        self.assertEqual(cli_status.read_bytes(), b"cache_hit=0\ndirect=1\noutput_path=\n")
        cli_status_after = cli_status.stat()
        self.assertEqual((cli_status_after.st_dev, cli_status_after.st_ino),
                         (cli_status_before.st_dev, cli_status_before.st_ino))
        self.assertFalse(cli_root.exists())

    def test_01_fresh_canonical_root_is_descriptor_created_and_alternate_is_rejected(self) -> None:
        fake_repo = self.base / "fresh-repository"; fake_repo.mkdir(mode=0o700)
        canonical = fake_repo / "build/ios-engine-iphoneos/.ios_gate_cache"
        with mock.patch.object(shader_cache, "REPO_ROOT", fake_repo), \
             mock.patch.object(shader_cache, "DEFAULT_ROOT", canonical):
            production = shader_cache.ShaderCache(canonical)
            result = production.run(stage="compile", key=self.key, salt=self.salt, inputs=[str(self.source)],
                                    command=_counter_command(self.counter), cwd=REPO)
            self.assertFalse(result.cache_hit)
            for directory in (fake_repo / "build", fake_repo / "build/ios-engine-iphoneos", canonical):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            with self.assertRaises(shader_cache.CacheError):
                shader_cache.ShaderCache(self.base / "alternate")
            with self.assertRaises(shader_cache.CacheError):
                shader_cache.ShaderCache(
                    f"{fake_repo}/build/./ios-engine-iphoneos/.ios_gate_cache")
            shutil.rmtree(fake_repo / "build")
            (fake_repo / "build").symlink_to(self.base / "outside", target_is_directory=True)
            with self.assertRaises(shader_cache.CacheError):
                production._root_fd()
            (fake_repo / "build").unlink()
            (fake_repo / "build").mkdir(mode=0o700)
            (fake_repo / "build").chmod(0o775)
            with self.assertRaises(shader_cache.CacheError):
                production._root_fd()

    def test_02_twenty_processes_same_key_execute_checker_once(self) -> None:
        barrier, queue = multiprocessing.Barrier(20), multiprocessing.Queue()
        workers = [multiprocessing.Process(target=_collision_worker,
                   args=(str(self.root), str(self.source), self.salt, self.key, str(self.counter), barrier, queue))
                   for _ in range(20)]
        for worker in workers: worker.start()
        values = [queue.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(30); self.assertEqual(worker.exitcode, 0)
        self.assertTrue(all(value[0] == "ok" for value in values), values)
        self.assertEqual(sum(not value[1] for value in values), 1)
        self.assertEqual(self.counter.read_bytes().count(b"x\n"), 1)
        self.assertEqual(list((self.root / "tmp/compile" / self.key).iterdir()), [])

    def test_03_different_keys_and_stages_are_independently_locked(self) -> None:
        other = self.base / "other"; other.write_text("other\n", encoding="utf-8")
        other_key = gate_hash.digest_paths(self.salt, [str(other)])
        barrier, queue = multiprocessing.Barrier(2), multiprocessing.Queue()
        workers = [
            multiprocessing.Process(target=_different_key_worker,
                args=(str(self.root), str(self.source), self.salt, self.key, str(self.counter), barrier, queue)),
            multiprocessing.Process(target=_different_key_worker,
                args=(str(self.root), str(other), self.salt, other_key, str(self.counter), barrier, queue)),
        ]
        for worker in workers: worker.start()
        values = [queue.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(30); self.assertEqual(worker.exitcode, 0)
        self.assertEqual(values, [("ok", False), ("ok", False)])
        self.assertEqual(self.counter.read_bytes().count(b"x\n"), 2)
        self.assertNotEqual(self.root / "locks/compile" / f"{self.key}.lock",
                            self.root / "locks/compile" / f"{other_key}.lock")

    def test_04_helper_subprocess_raw_fd_and_real_list_replay_cover_exact_ids(self) -> None:
        import test_feedback as feedback
        cases = (("compile", "stage::shader::glsl-es", "shader::glsl-es", _real_list_ids("compile"), COMPILE_OK),
                 ("link", "stage::shader::link", "shader::link", _real_list_ids("link"), LINK_OK))
        self.assertEqual([len(item[3]) for item in cases], [296, 137])
        real_selection = feedback.runtime_selection

        def shader_only_selection(profile: str, run_id: str, nonce: str, directory: Path | str):
            """Use the production selector, narrowed only to its two shader stages."""
            selected = dict(real_selection(profile, run_id, nonce, directory))
            selected.pop("auth_tag", None)
            stage_ids = {item[1] for item in cases}
            origins = {item[2] for item in cases}
            selected["entrypoints"] = [item for item in selected["entrypoints"]
                                       if item["entrypoint_id"] in origins]
            selected["cases"] = [item for item in selected["cases"]
                                 if item["test_id"].startswith(("shader:", "shader-link:"))]
            selected["stages"] = [item for item in selected["stages"] if item["stage_id"] in stage_ids]
            selected["cache_contracts"] = [item for item in selected["cache_contracts"]
                                           if item["stage_id"] in stage_ids]
            selected["build_stage_classifications"] = []
            for ordinal, item in enumerate(selected["cases"], 1):
                item["ordinal"] = ordinal
            for ordinal, item in enumerate(selected["stages"], 1):
                item["ordinal"] = ordinal
            selected["case_ids_sha256"] = feedback.digest(item["test_id"] for item in selected["cases"])
            selected["stage_ids_sha256"] = feedback.digest(item["stage_id"] for item in selected["stages"])
            selected.pop("selection_sha256", None)
            selected["selection_sha256"] = feedback._selection_payload_hash(selected)
            return feedback._signed_record({"directory": str(directory), "profile": profile,
                                            "run_id": run_id, "nonce": nonce}, "selection", selected)

        def initialize_runtime(name: str) -> tuple[Path, dict[str, str]]:
            root = self.base.resolve() / name
            context = {"directory": str(root), "profile": "shaders",
                       "run_id": name, "nonce": ("a" if name == "direct" else "b") * 64}
            with mock.patch.object(feedback, "_CACHE_ROOT", self.root), \
                 mock.patch.object(feedback, "runtime_selection", side_effect=shader_only_selection):
                self.assertTrue(feedback.runtime_initialize(root, "shaders", context["run_id"], context["nonce"]))
            return root, context

        direct_root, direct_context = initialize_runtime("direct")
        raw = self.base / "raw-events"
        raw_fd = os.open(raw, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        raw.chmod(0o600)
        try:
            for stage, stage_id, origin, identifiers, payload in cases:
                ids_path = self.base / f"{stage}-ids.json"
                ids_path.write_text(json.dumps({"ids": identifiers}), encoding="utf-8")
                salt = f"telemetry-{stage}"; key = gate_hash.digest_paths(salt, [str(self.source)])
                config = self.base / f"{stage}.json"
                config.write_text(json.dumps({"root": str(self.root), "stage": stage, "key": key,
                                              "salt": salt, "input": str(self.source), "cwd": str(REPO),
                                              "command": _raw_emitting_checker(ids_path, payload)}), encoding="utf-8")
                environment = dict(os.environ, XRAY_FEEDBACK_RAW_EVENT_FD=str(raw_fd))
                first = subprocess.run(_helper_subprocess(config), cwd=REPO, text=True, capture_output=True,
                                       env=environment, pass_fds=(raw_fd,), check=False, timeout=30)
                self.assertEqual(first.returncode, 0, first.stderr)
                first_result = json.loads(first.stdout)
                self.assertFalse(first_result["hit"])
                self.assertFalse(first_result["direct"])
                records = feedback._read_raw_events_fd(raw_fd)
                self.assertEqual([row["test_id"] for row in records], identifiers)
                self.assertTrue(all(row["result"] == "PASS" for row in records))
                self.assertTrue(feedback.runtime_ingest(origin, raw_fd=raw_fd, context=direct_context))
                self.assertTrue(feedback.runtime_event(stage_id, "PASS", time.monotonic_ns(),
                                                       entrypoint_id=origin, kind="stage", cache_hit=False,
                                                       context=direct_context))
                os.ftruncate(raw_fd, 0)
                os.lseek(raw_fd, 0, os.SEEK_SET)
            direct_final = feedback.runtime_finalize(direct_root, "shaders", direct_context["run_id"],
                                                     direct_context["nonce"], 0)
            self.assertEqual(direct_final["status"], "COMPLETE")
            direct_run = json.loads((direct_root / "run.json").read_text(encoding="utf-8"))
            self.assertEqual([item["test_id"] for item in direct_run["cases"]],
                             cases[0][3] + cases[1][3])
            self.assertEqual([item["stage_id"] for item in direct_run["stages"]],
                             [item[1] for item in cases])

            replay_root, replay_context = initialize_runtime("replay")
            for stage, stage_id, origin, identifiers, payload in cases:
                salt = f"telemetry-{stage}"; key = gate_hash.digest_paths(salt, [str(self.source)])
                config = self.base / f"{stage}.json"
                second = subprocess.run(_helper_subprocess(config), cwd=REPO, text=True, capture_output=True,
                                        env=environment, pass_fds=(raw_fd,), check=False, timeout=30)
                self.assertEqual(second.returncode, 0, second.stderr)
                second_result = json.loads(second.stdout)
                self.assertTrue(second_result["hit"])
                self.assertFalse(second_result["direct"])
                self.assertEqual(feedback._read_raw_events_fd(raw_fd), [])
                self.assertTrue(feedback.runtime_cache_certificate(
                    stage, key, Path(second_result["output_path"]), key, key,
                    entrypoint_id=origin, context=replay_context))
            replay_final = feedback.runtime_finalize(replay_root, "shaders", replay_context["run_id"],
                                                     replay_context["nonce"], 0)
            self.assertEqual(replay_final["status"], "COMPLETE")
            replay_run = json.loads((replay_root / "run.json").read_text(encoding="utf-8"))
            replayed = {item["stage_id"]: item for item in replay_run["stages"]}
            self.assertEqual(replayed[cases[0][1]]["covered_ids"], cases[0][3])
            self.assertEqual(replayed[cases[1][1]]["covered_ids"], cases[1][3])
        finally:
            os.close(raw_fd)

    def test_05_shared_bootstrap_deadline_returns_75_without_checker(self) -> None:
        self.cache_run()
        bootstrap = self.root / "locks/bootstrap.lock"
        busy_salt = self.salt + "-busy"
        busy_key = gate_hash.digest_paths(busy_salt, [str(self.source)])
        descriptor = os.open(bootstrap, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            with self.assertRaises(shader_cache.CacheBusy):
                self.cache_run(key=busy_key, salt=busy_salt, timeout=0)
        finally:
            os.close(descriptor)
        self.assertFalse(self.final(key=busy_key).exists())
        self.assertEqual(self.counter.read_bytes().count(b"x\n"), 1)

    def test_06_malformed_or_duplicate_zero_exit_transcripts_and_raw_fds_fail_closed(self) -> None:
        for stage, payload in (("compile", b"not a checker transcript\n"),
                               ("compile", COMPILE_OK + COMPILE_OK),
                               ("compile", COMPILE_OK + b"GLSL ES 3.00 shader check: 278/279 compile, 1 fail (0 include-only skipped)\n"),
                               ("link", LINK_OK + LINK_OK),
                               ("link", LINK_OK + b"vs->fs link check: 136/137 pairs clean, 1 with issues\n")):
            with self.subTest(stage=stage, payload=payload[:16]):
                salt = f"invalid-{stage}-{hashlib.sha256(payload).hexdigest()}"
                key = gate_hash.digest_paths(salt, [str(self.source)])
                with self.assertRaises(shader_cache.SemanticValidationError):
                    self.cache_run(stage=stage, salt=salt, key=key,
                                   command=_counter_command(self.counter, payload))
                self.assertFalse(self.final(stage, key).exists())
                self.assertFalse(self.receipt(stage, key).exists())

        def invalid_descriptor(label: str, descriptor: int, *, foreign_owner: bool = False) -> None:
            self.counter.unlink(missing_ok=True)
            real_fstat = shader_cache.os.fstat

            def fstat_with_foreign_owner(value: int):
                if foreign_owner and value == descriptor:
                    details = real_fstat(value)
                    return SimpleNamespace(st_mode=details.st_mode, st_uid=os.geteuid() + 1,
                                           st_nlink=details.st_nlink)
                return real_fstat(value)

            with self.subTest(raw_descriptor=label), \
                 mock.patch.dict(os.environ, {"XRAY_FEEDBACK_RAW_EVENT_FD": str(descriptor)}), \
                 mock.patch.object(shader_cache.os, "fstat", side_effect=fstat_with_foreign_owner):
                with self.assertRaises(shader_cache.CacheError):
                    self.cache_run(force_direct=True)
                self.assertFalse(self.counter.exists())

        reader, writer = os.pipe()
        try:
            invalid_descriptor("pipe", writer)
        finally:
            os.close(reader)
            os.close(writer)
        fifo = self.base / "raw.fifo"
        os.mkfifo(fifo)
        fifo_descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        try:
            invalid_descriptor("fifo", fifo_descriptor)
        finally:
            os.close(fifo_descriptor)
        for label, mutate, foreign_owner in (("wrong-mode", lambda path: path.chmod(0o644), False),
                                             ("hardlink", lambda path: os.link(path, path.with_name("raw.extra")), False),
                                             ("mocked-owner", lambda _path: None, True)):
            raw = self.base / f"raw-{label}"
            raw.write_bytes(b"")
            raw.chmod(0o600)
            descriptor = os.open(raw, os.O_RDWR)
            try:
                mutate(raw)
                invalid_descriptor(label, descriptor, foreign_owner=foreign_owner)
            finally:
                os.close(descriptor)

    def test_07_cache_hit_revalidates_semantics_even_with_matching_receipt(self) -> None:
        self._seed_entry(b"apparently successful but invalid\n")
        with self.assertRaises(shader_cache.SemanticValidationError):
            self.cache_run()

    def test_08_partial_receipt_is_removed_then_clean_miss_runs_once(self) -> None:
        def stop(point: str) -> None:
            if point == "after-receipt-write": raise RuntimeError("interrupt")
        with self.assertRaisesRegex(RuntimeError, "interrupt"):
            self.cache_run(hook=stop)
        self.assertEqual(stat.S_IMODE(self.receipt().stat().st_mode), 0o600)
        self.assertFalse(self.final().exists())
        self.assertFalse(self.cache_run().cache_hit)
        self.assertEqual(self.counter.read_bytes().count(b"x\n"), 2)

    def test_09_durable_prelink_receipt_recovers_without_second_checker(self) -> None:
        def stop(point: str) -> None:
            if point == "after-receipt": raise RuntimeError("interrupt")
        with self.assertRaisesRegex(RuntimeError, "interrupt"):
            self.cache_run(hook=stop)
        self.assertEqual(stat.S_IMODE(self.receipt().stat().st_mode), 0o400)
        self.assertFalse(self.final().exists())
        self.assertTrue(self.cache_run().cache_hit)
        self.assertEqual(self.counter.read_bytes().count(b"x\n"), 1)

    def test_10_post_link_and_terminal_recovery_keep_the_exact_receipt(self) -> None:
        for crash_point, initial_links in (("after-link", 2), ("after-unlink", 1)):
            with self.subTest(crash_point=crash_point):
                shutil.rmtree(self.root, ignore_errors=True)
                self.counter.unlink(missing_ok=True)

                def stop(point: str) -> None:
                    if point == crash_point:
                        raise RuntimeError("interrupt")

                with self.assertRaisesRegex(RuntimeError, "interrupt"):
                    self.cache_run(hook=stop)
                before, before_stat = self.receipt().read_bytes(), self.receipt().stat()
                self.assertEqual(self.final().stat().st_nlink, initial_links)
                self.assertTrue(self.cache_run().cache_hit)
                after_stat = self.receipt().stat()
                self.assertEqual(self.receipt().read_bytes(), before)
                self.assertEqual((after_stat.st_dev, after_stat.st_ino),
                                 (before_stat.st_dev, before_stat.st_ino))
                self.assertEqual(self.final().stat().st_nlink, 1)

    def test_11_terminal_hit_keeps_immutable_receipt_inode_and_bytes(self) -> None:
        self.cache_run()
        before, before_stat = self.receipt().read_bytes(), self.receipt().stat()
        self.assertTrue(self.cache_run().cache_hit)
        after = self.receipt().stat()
        self.assertEqual(self.receipt().read_bytes(), before)
        self.assertEqual((after.st_dev, after.st_ino), (before_stat.st_dev, before_stat.st_ino))

    def test_12_mutation_corpus_is_fail_closed(self) -> None:
        variants = ("delete", "append", "truncate", "bit-flip", "same-bytes-new-inode", "symlink",
                    "directory", "fifo", "hardlink", "mode")
        for variant in variants:
            with self.subTest(variant=variant):
                shutil.rmtree(self.root, ignore_errors=True); self.counter.unlink(missing_ok=True); self.cache_run()
                final, raw = self.final(), self.final().read_bytes()
                if variant == "delete": final.unlink()
                elif variant == "append": final.chmod(0o600); final.write_bytes(raw + b"x"); final.chmod(0o400)
                elif variant == "truncate": final.chmod(0o600); final.write_bytes(raw[:10]); final.chmod(0o400)
                elif variant == "bit-flip": changed = bytearray(raw); changed[-1] ^= 1; final.chmod(0o600); final.write_bytes(changed); final.chmod(0o400)
                elif variant == "same-bytes-new-inode": replacement = final.with_name("replacement"); replacement.write_bytes(raw); replacement.chmod(0o400); os.replace(replacement, final)
                elif variant == "symlink": final.unlink(); final.symlink_to("outside")
                elif variant == "directory": final.unlink(); final.mkdir()
                elif variant == "fifo": final.unlink(); os.mkfifo(final)
                elif variant == "hardlink": os.link(final, final.with_name("extra-link"))
                elif variant == "mode": final.chmod(0o600)
                with self.assertRaises(shader_cache.CacheError): self.cache_run()
        foreign = SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=os.geteuid() + 1, st_nlink=1)
        with self.assertRaises(shader_cache.CacheError): shader_cache._require_regular(foreign, mode=0o400, links=frozenset((1,)))

    def test_13_force_direct_bypasses_hot_cache_and_leaves_namespace_unchanged(self) -> None:
        self.cache_run()
        final_before, receipt_before = self.final().read_bytes(), self.receipt().read_bytes()
        final_stat_before, receipt_stat_before = self.final().stat(), self.receipt().stat()
        before = [(str(path.relative_to(self.root)), path.lstat().st_dev, path.lstat().st_ino,
                   path.lstat().st_mode, path.lstat().st_size, path.lstat().st_mtime_ns)
                  for path in sorted(self.root.rglob("*"))]
        direct = self.cache_run(command=_counter_command(self.counter, COMPILE_OK), force_direct=True)
        after = [(str(path.relative_to(self.root)), path.lstat().st_dev, path.lstat().st_ino,
                  path.lstat().st_mode, path.lstat().st_size, path.lstat().st_mtime_ns)
                 for path in sorted(self.root.rglob("*"))]
        self.assertTrue(direct.direct); self.assertFalse(direct.cache_hit); self.assertEqual(before, after)
        for path, payload, detail in ((self.final(), final_before, final_stat_before),
                                      (self.receipt(), receipt_before, receipt_stat_before)):
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), hashlib.sha256(payload).digest())
            current = path.stat()
            self.assertEqual((current.st_dev, current.st_ino), (detail.st_dev, detail.st_ino))

    def test_14_sigkill_releases_kernel_lock(self) -> None:
        self.cache_run()
        lock = self.root / "locks/compile" / f"{self.key}.lock"
        ready_r, ready_w = os.pipe(); wait_r, wait_w = os.pipe()
        holder = subprocess.Popen([sys.executable, "-c", ("import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW); "
                         "fcntl.flock(fd,fcntl.LOCK_EX); os.write(int(sys.argv[2]),b'R'); os.read(int(sys.argv[3]),1)"),
                         str(lock), str(ready_w), str(wait_r)], pass_fds=(ready_w, wait_r))
        os.close(ready_w); os.close(wait_r)
        try:
            self.assertEqual(os.read(ready_r, 1), b"R"); holder.kill(); self.assertNotEqual(holder.wait(20), 0)
            self.assertTrue(self.cache_run().cache_hit)
        finally:
            os.close(ready_r); os.close(wait_w)

    def test_15_sigterm_before_and_during_spawn_forward_and_never_publish(self) -> None:
        def before_spawn(point: str) -> None:
            if point == "before-spawn":
                os.kill(os.getpid(), signal.SIGTERM)

        with self.assertRaises(shader_cache.ForwardedSignal):
            self.cache_run(hook=before_spawn)
        self.assertFalse(self.final().exists())
        self.assertFalse(self.counter.exists())

        spawned: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def signal_during_popen(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            spawned.append(child)
            os.kill(os.getpid(), signal.SIGTERM)
            return child

        sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
        with mock.patch.object(shader_cache.subprocess, "Popen", side_effect=signal_during_popen), \
             self.assertRaises(shader_cache.ForwardedSignal):
            self.cache_run(command=sleeper)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())
        self.assertFalse(self.final().exists())

        ready, wait = self.base / "ready", self.base / "wait"; os.mkfifo(ready); os.mkfifo(wait)
        ready_fd = os.open(ready, os.O_RDONLY | os.O_NONBLOCK); queue = multiprocessing.Queue()
        worker = multiprocessing.Process(target=_signal_worker,
            args=(str(self.root), str(self.source), self.salt, self.key, str(ready), str(wait), queue))
        worker.start()
        try:
            self.assertEqual(select.select([ready_fd], [], [], 20)[0], [ready_fd]); self.assertEqual(os.read(ready_fd, 1), b"R")
            os.kill(worker.pid, signal.SIGTERM); worker.join(20)
            self.assertEqual(queue.get(timeout=5), ("ForwardedSignal", signal.SIGTERM)); self.assertFalse(self.final().exists())
        finally:
            os.close(ready_fd)

    def test_16_checker_failure_input_drift_and_bounded_cleanup_never_publish(self) -> None:
        diagnostic = b"checker diagnostic: bad shader\n\xffbinary marker\n"
        with self.assertRaises(shader_cache.CheckerFailed) as caught:
            self.cache_run(command=_counter_command(self.counter, diagnostic, status=7))
        self.assertEqual(caught.exception.status, 7)
        self.assertEqual(caught.exception.payload, diagnostic)
        self.assertFalse(self.final().exists())
        self.assertFalse(self.receipt().exists())

        fake_repo = self.base / "failure-cli-repository"
        fake_repo.mkdir(mode=0o700)
        cli_root = fake_repo / "build/ios-engine-iphoneos/.ios_gate_cache"
        status_file = self._status_file("failure-cli-status", b"not-success\n")
        status_before = status_file.stat()
        launcher = (
            "import sys; sys.path.insert(0, sys.argv[1]); import shader_cache; "
            "shader_cache.REPO_ROOT=shader_cache.Path(sys.argv[2]); "
            "shader_cache.DEFAULT_ROOT=shader_cache.Path(sys.argv[3]); "
            "raise SystemExit(shader_cache.main(sys.argv[4:]))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", launcher, str(REPO / "misc/ios"), str(fake_repo), str(cli_root),
             "run", "--root", str(cli_root), "--stage", "compile", "--key", self.key,
             "--salt", self.salt, "--input", str(self.source), "--status-file", str(status_file),
             "--", *_counter_command(self.base / "failure-cli-counter", diagnostic, status=7)],
            cwd=REPO, capture_output=True, timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(completed.returncode, 7)
        self.assertEqual(completed.stdout, diagnostic)
        self.assertEqual(completed.stderr, b"shader cache checker failed: exit 7\n")
        self.assertEqual(status_file.read_bytes(), b"not-success\n")
        status_after = status_file.stat()
        self.assertEqual((status_after.st_dev, status_after.st_ino, status_after.st_size),
                         (status_before.st_dev, status_before.st_ino, status_before.st_size))
        self.assertFalse((cli_root / "compile" / f"{self.key}.out").exists())
        self.assertFalse((cli_root / "receipts/compile" / f"{self.key}.json").exists())

        with self.assertRaises(shader_cache.InputDrift): self.cache_run(command=_counter_command(self.counter, mutate=self.source))
        self.assertFalse(self.final().exists())
        self.source.write_text("input\n", encoding="utf-8"); self.key = gate_hash.digest_paths(self.salt, [str(self.source)])
        self.cache_run(); temp = self.root / "tmp/compile" / self.key
        for index in range(shader_cache.MAX_TEMPS + 1): (temp / f"{index:032x}.tmp").write_bytes(b"x")
        with self.assertRaises(shader_cache.CacheError): self.cache_run()

    def test_17_no_clobber_collision_never_overwrites_foreign_final(self) -> None:
        foreign = b"foreign"
        def collide(point: str) -> None:
            if point == "after-receipt": self.final().write_bytes(foreign); self.final().chmod(0o400)
        with self.assertRaises(shader_cache.CacheError): self.cache_run(hook=collide)
        self.assertEqual(self.final().read_bytes(), foreign)

    def test_18_cache_ownership_wiring_and_profile_ids_remain_stable(self) -> None:
        import test_feedback as feedback
        source = (REPO / "misc/ios/build_check.sh").read_text(encoding="utf-8")
        start = source.index("run_shader_cache_stage() {")
        end = source.index("\n}\n\nartifact_salt=", start) + 2
        function = source[start:end]
        dispatch_log = self.base / "dispatch-log"
        status = self.base / "dispatch-status"
        harness = self.base / "dispatch.sh"
        harness.write_text(
            "set -eu -o pipefail\n"
            f"REPO_ROOT={shlex.quote(str(REPO))}\n"
            'GATE_CACHE_DIR="$REPO_ROOT/build/ios-engine-iphoneos/.ios_gate_cache"\n'
            "shader_cache_output=''\nshader_cache_hit=0\nshader_cache_output_file=''\n"
            "feedback_begin_manual_stage() { :; }\nfeedback_complete_manual_stage() { :; }\nfeedback_observer() { :; }\n"
            "feedback_enabled=0\n"
            "feedback_selected_raw() {\n"
            f"  printf '%s\\0' \"$@\" > {shlex.quote(str(dispatch_log))}\n"
            "  local status_file='' previous='' argument\n"
            "  for argument in \"$@\"; do\n"
            "    if [ \"$previous\" = --status-file ]; then status_file=\"$argument\"; break; fi\n"
            "    previous=\"$argument\"\n"
            "  done\n"
            "  printf 'cache_hit=0\\ndirect=1\\noutput_path=\\n' > \"$status_file\"\n"
            "  printf 'GLSL ES 3.00 shader check: 279/279 compile, 0 fail (0 include-only skipped)\\n'\n"
            "}\n"
            + function + "\n"
            "run_shader_cache_stage compile stage::shader::glsl-es shader::glsl-es "
            f"{'a' * 64} salt 1 \"$REPO_ROOT/misc/ios/build_check.sh\" -- controlled-checker\n",
            encoding="utf-8")
        completed = subprocess.run(["bash", str(harness)], cwd=REPO, text=True,
                                   capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        dispatched = dispatch_log.read_bytes().split(b"\0")[:-1]
        decoded = [item.decode("utf-8") for item in dispatched]
        self.assertEqual(decoded[0], "shader::glsl-es")
        self.assertEqual(decoded[1:3], ["python3", str(REPO / "misc/ios/shader_cache.py")])
        self.assertEqual(decoded[decoded.index("--root") + 1],
                         str(REPO / "build/ios-engine-iphoneos/.ios_gate_cache"))
        self.assertIn("--force-direct", decoded)
        self.assertEqual(shader_cache.DEFAULT_ROOT, REPO / "build/ios-engine-iphoneos/.ios_gate_cache")
        groups = feedback.profile_ids(); self.assertEqual(len(groups["shader-baseline"]) + len(groups["shader-variants"]), 296)
        self.assertEqual(len(groups["shader-links"]), 137)

    def test_19_inputs_catalog_and_legacy_files_are_bound_or_ignored(self) -> None:
        import test_feedback as feedback
        self._assert_status_channel_contracts()
        source = (REPO / "misc/ios/build_check.sh").read_text(encoding="utf-8")
        gate = (REPO / "misc/ios/gate_hash.py").read_text(encoding="utf-8")
        install = (REPO / "misc/ios/test_install_device_contract.py").read_text(encoding="utf-8")
        catalog = next(item for item in feedback.read_catalog()["entrypoints"]
                       if item["entrypoint_id"] == "python::misc/ios/test_shader_cache.py")
        bound = {entry["value"] for entry in catalog["explicit_inputs"]}
        def shell_array(name: str) -> set[str]:
            match = re.search(rf"(?ms)^\s*{re.escape(name)}=\(\n(.*?)^\s*\)", source)
            self.assertIsNotNone(match, name)
            assert match is not None
            return set(re.findall(r'"\$REPO_ROOT/([^"\n]+)"', match.group(1)))

        compile_bound, link_bound = shell_array("compile_inputs"), shell_array("link_inputs")
        for relative in ("misc/ios/shader_cache.py", "misc/ios/test_shader_cache.py"):
            self.assertIn(relative, source); self.assertIn(f'"{relative}"', gate)
            self.assertIn(f'"{relative}"', install); self.assertIn(relative, bound)
            self.assertIn(relative, compile_bound)
            self.assertIn(relative, link_bound)
        for relative in ("misc/ios/test_feedback.py", "misc/ios/test_test_feedback.py",
                         "misc/ios/test_feedback_catalog.json"):
            self.assertIn(relative, source); self.assertIn(f'"{relative}"', gate)
            self.assertIn(relative, compile_bound)
            self.assertIn(relative, link_bound)
        self.assertIn("misc/ios/test_feedback_unittest.py", compile_bound)
        self.assertIn("misc/ios/test_feedback_unittest.py", link_bound)

        fake_repo = self.base / "cli-repository"; fake_repo.mkdir(mode=0o700)
        cli_root = fake_repo / "build/ios-engine-iphoneos/.ios_gate_cache"
        cli_root.parent.mkdir(parents=True, mode=0o700)
        cli_root.parent.chmod(0o700)
        cli_cache = shader_cache.ShaderCache(cli_root, allow_test_root=True)
        cli_cache.run(stage="compile", key=self.key, salt=self.salt, inputs=[str(self.source)],
                      command=_counter_command(self.counter), cwd=REPO)
        bootstrap = cli_root / "locks/bootstrap.lock"
        lock_fd = os.open(bootstrap, os.O_RDWR | os.O_NOFOLLOW)
        launcher = (
            "import sys; sys.path.insert(0, sys.argv[1]); import shader_cache; "
            "shader_cache.REPO_ROOT=shader_cache.Path(sys.argv[2]); "
            "shader_cache.DEFAULT_ROOT=shader_cache.Path(sys.argv[3]); "
            "raise SystemExit(shader_cache.main(sys.argv[4:]))"
        )

        def cli(root: str, timeout: str = "1") -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, "-c", launcher, str(REPO / "misc/ios"), str(fake_repo),
                                   str(cli_root), "run", "--root", root, "--stage", "compile",
                                   "--key", self.key, "--salt", self.salt, "--input", str(self.source),
                                   "--timeout", timeout, "--", sys.executable, "-c", "raise SystemExit(9)"],
                                  cwd=REPO, text=True, capture_output=True, timeout=30)

        for malformed in ("", ".", "..", f"{cli_root}/", f"{cli_root}/.",
                          str(cli_root).replace("/build/", "//build/")):
            self.assertEqual(cli(malformed).returncode, 1, malformed)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self.assertEqual(cli(str(cli_root), "0").returncode, 75)
        finally:
            os.close(lock_fd)
        old = self.root / "shader_compile.out"; self.root.mkdir(parents=True, exist_ok=True); old.write_text("obsolete")
        self.cache_run(); self.assertTrue(old.exists())

        # Run the production entrypoint beneath its real raw-FD observer.  The
        # child guard avoids recursively launching a second complete suite,
        # while still running and reporting this nineteenth case itself.
        if os.environ.get("XRAY_SHADER_CACHE_ENTRYPOINT_PROBE") != "1":
            run_id, nonce = "shader-cache-entrypoint", "c" * 64
            feedback_root = self.base / "entrypoint-feedback"
            entrypoint_id = "python::misc/ios/test_shader_cache.py"
            stage_id = f"stage::{entrypoint_id}"
            real_selection = feedback.runtime_selection

            def entrypoint_only_selection(profile: str, selected_run_id: str,
                                          selected_nonce: str, directory: Path | str):
                selected = dict(real_selection(profile, selected_run_id, selected_nonce, directory))
                selected.pop("auth_tag", None)
                selected["entrypoints"] = [item for item in selected["entrypoints"]
                                           if item["entrypoint_id"] == entrypoint_id]
                selected["cases"] = [item for item in selected["cases"]
                                     if item["parent_stage_id"] == stage_id]
                selected["stages"] = [item for item in selected["stages"]
                                      if item["stage_id"] == stage_id]
                selected["cache_contracts"] = []
                selected["build_stage_classifications"] = []
                for ordinal, item in enumerate(selected["cases"], 1):
                    item["ordinal"] = ordinal
                for ordinal, item in enumerate(selected["stages"], 1):
                    item["ordinal"] = ordinal
                selected["case_ids_sha256"] = feedback.digest(item["test_id"] for item in selected["cases"])
                selected["stage_ids_sha256"] = feedback.digest(item["stage_id"] for item in selected["stages"])
                selected.pop("selection_sha256", None)
                selected["selection_sha256"] = feedback._selection_payload_hash(selected)
                return feedback._signed_record({"directory": str(directory), "profile": profile,
                                                "run_id": selected_run_id, "nonce": selected_nonce},
                                               "selection", selected)

            selected = entrypoint_only_selection("shaders", run_id, nonce, feedback_root)
            expected = [item["test_id"] for item in selected["cases"]
                        if item["parent_stage_id"] == stage_id]
            self.assertEqual(len(expected), 19)
            with mock.patch.object(feedback, "runtime_selection", side_effect=entrypoint_only_selection):
                self.assertTrue(feedback.runtime_initialize(feedback_root, "shaders", run_id, nonce))
            raw_path = self.base / "entrypoint-raw.events"
            raw_fd = os.open(raw_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                environment = dict(os.environ)
                environment["XRAY_FEEDBACK_RAW_EVENT_FD"] = str(raw_fd)
                environment["XRAY_SHADER_CACHE_ENTRYPOINT_PROBE"] = "1"
                completed = subprocess.run([sys.executable, str(REPO / "misc/ios/test_shader_cache.py"), "-q"],
                                           cwd=REPO, env=environment, pass_fds=(raw_fd,), text=True,
                                           capture_output=True, timeout=180, check=False)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                records = feedback._read_raw_events_fd(raw_fd)
                self.assertEqual([item["test_id"] for item in records], expected)
                self.assertTrue(all(item["result"] == "PASS" for item in records))
                self.assertTrue(feedback.runtime_ingest(entrypoint_id,
                                                        raw_fd=raw_fd,
                                                        context={"directory": str(feedback_root),
                                                                 "profile": "shaders", "run_id": run_id,
                                                                 "nonce": nonce}))
                self.assertTrue(feedback.runtime_event(stage_id, "PASS", time.monotonic_ns(),
                                                       entrypoint_id=entrypoint_id, kind="stage",
                                                       context={"directory": str(feedback_root),
                                                                "profile": "shaders", "run_id": run_id,
                                                                "nonce": nonce}))
                completed_run = feedback.runtime_finalize(feedback_root, "shaders", run_id, nonce, 0)
                self.assertEqual(completed_run["status"], "COMPLETE")
            finally:
                os.close(raw_fd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
