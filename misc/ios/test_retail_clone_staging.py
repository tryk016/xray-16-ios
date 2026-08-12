#!/usr/bin/env python3
"""Host-only contracts for prepared APFS clone staging and publication."""
from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
RUNNER = HERE / "retail_simulator.sh"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RI = load("retail_import_clone_tests", "retail_import.py")
GUARD = load("retail_guard_clone_tests", "retail_simulator_guard.py")


class CloneStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.prepared = self.root / "prepared"
        self.documents = self.prepared / "Documents"
        self.manifest = self.prepared / "manifest"
        self.documents.mkdir(parents=True)
        self.manifest.mkdir()
        self.artifacts = self.root / "artifacts"
        self.container = self.root / "container"
        self.artifacts.mkdir()
        self.container.mkdir()
        for directory in (self.prepared, self.documents, self.manifest,
                          self.artifacts, self.container):
            directory.chmod(0o700)
        self.files = {f"resources/resources.db{i}": f"resource-{i}".encode()
                      for i in range(5)}
        self.files.update({f"levels/levels.db{i}": f"level-{i}".encode()
                           for i in range(2)})
        self.files["localization/xefis_movies.db"] = b"movie-payload"
        self.files["_appdata_/savedgames/slot.scop"] = b"original-save"
        for relative, data in self.files.items():
            target = self.documents / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o600)
        rows = ["bytes\tpath"] + [f"{len(data)}\t{path}"
                                    for path, data in sorted(self.files.items())]
        (self.manifest / "files.tsv").write_text("\n".join(rows) + "\n")
        required = ["bytes\tsha256\tpath\tstatus"]
        for relative in sorted(path for path in self.files
                               if path.startswith(("resources/", "levels/"))):
            data = self.files[relative]
            required.append(
                f"{len(data)}\t{hashlib.sha256(data).hexdigest()}\t{relative}\tpresent")
        (self.manifest / "required-archives.tsv").write_text("\n".join(required) + "\n")
        large_rows = ["sha256\tbytes\tpath"]
        for relative in ("localization/xefis_movies.db", "_appdata_/savedgames/slot.scop"):
            data = self.files[relative]
            large_rows.append(f"{hashlib.sha256(data).hexdigest()}\t{len(data)}\t{relative}")
        (self.manifest / "large-files-sha256.tsv").write_text("\n".join(large_rows) + "\n")
        self.destination = self.container / "Documents"
        self.staged = self.artifacts / "staged-files.tsv"
        self.pending = self.artifacts / ".clone-ledger.pending.json"
        self.ledger = self.artifacts / "clone-ledger.json"
        self.lock_path = RI.sidecars(self.prepared)["lock"]
        self.lock_path.write_bytes(b"")
        self.lock_path.chmod(0o600)
        self._real_raw_xattrs = GUARD._raw_xattrs
        self.synthetic_xattrs: dict[tuple[int, int], dict[str, str]] = {}
        # The temporary directory can acquire Finder provenance on this host.
        # A real Simulator container root has only its ContainerManager xattrs;
        # keep the baseline fixture faithful without weakening production policy.
        self.set_synthetic_xattrs({self.container: {}})
        self.raw_xattr_patcher = mock.patch.object(
            GUARD, "_raw_xattrs", side_effect=self.synthetic_raw_xattrs,
        )
        self.raw_xattr_patcher.start()

    def tearDown(self) -> None:
        self.raw_xattr_patcher.stop()
        self.temporary.cleanup()

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def snapshot(self) -> dict[str, object]:
        def identity(path: Path) -> dict[str, int]:
            return RI.identity(path.stat())
        records = {
            relative: {"identity": identity(self.documents / relative),
                       "sha256": self.digest(data)}
            for relative, data in sorted(self.files.items())
        }
        return {"directories": [identity(self.prepared), identity(self.documents),
                                identity(self.manifest)],
                "manifest": {}, "documents": records}

    def importer(self, verify=None) -> object:
        return types.SimpleNamespace(
            verify_prepared=verify or (lambda path: self.snapshot()),
            open_absolute_directory_nofollow=RI.open_absolute_directory_nofollow,
            open_relative_regular_nofollow=RI.open_relative_regular_nofollow,
        )

    @staticmethod
    def encoded_xattrs(values: dict[str, bytes]) -> dict[str, str]:
        return {name: base64.b64encode(value).decode("ascii")
                for name, value in values.items()}

    @staticmethod
    def container_manager_xattrs() -> dict[str, str]:
        return CloneStageTests.encoded_xattrs({
            "com.apple.containermanager.identifier": b"io.github.tryk016.openxray",
            "com.apple.containermanager.schema-version": b"1",
            "com.apple.containermanager.uuid": b"container-uuid",
        })

    def set_synthetic_xattrs(self, values: dict[Path, dict[str, str]]) -> None:
        self.synthetic_xattrs = {
            (path.stat().st_dev, path.stat().st_ino): dict(xattrs)
            for path, xattrs in values.items()
        }

    def synthetic_raw_xattrs(self, fd, label):
        replacement = self.synthetic_xattrs.get((os.fstat(fd).st_dev, os.fstat(fd).st_ino))
        if replacement is not None:
            return dict(replacement)
        # Replacement parents in TOCTOU tests are not the original container
        # identity.  Keep their synthetic ContainerManager surface empty so the
        # test reaches its intended identity/rebind assertion.
        if "destination parent" in label:
            return {}
        return self._real_raw_xattrs(fd, label)

    def stage(self, *, saves: bool = False, clone=None) -> None:
        patches = [mock.patch.object(GUARD, "_import_retail_import",
                                     return_value=self.importer())]
        if clone is not None:
            patches.append(mock.patch.object(GUARD, "_fclonefileat", clone))
        with patches[0]:
            if len(patches) == 2:
                with patches[1]:
                    GUARD.clone_stage(self.prepared, self.destination, self.staged,
                                      self.pending, saves)
            else:
                GUARD.clone_stage(self.prepared, self.destination, self.staged,
                                  self.pending, saves)

    def read_ledger(self, path: Path, *, finalized: bool) -> dict[str, object]:
        pinned = GUARD._read_clone_ledger(path, finalized=finalized)
        try:
            return json.loads(json.dumps(pinned["ledger"]))
        finally:
            GUARD._close_pinned(pinned)

    def finalize(self, *, saves: bool = False, mutable: str | None = None,
                 diagnostics: Path | None = None) -> None:
        with mock.patch.object(GUARD, "_import_retail_import", return_value=self.importer()):
            GUARD.finalize_clone_ledger(
                self.prepared, self.destination, self.staged, self.pending,
                self.ledger, saves, mutable, diagnostics, "27.0",
            )

    def report(self) -> Path:
        pinned = GUARD._read_clone_ledger(self.ledger, finalized=True)
        try:
            report = self.artifacts / ".report.pending"
            fields = GUARD._clone_report_fields(
                pinned, hashlib.sha256(self.staged.read_bytes()).hexdigest())
            text = "result=PASS\n" + "".join(
                f"{key}={value}\n" for key, value in fields.items())
            quickload = pinned["ledger"]["post_runtime"]["quickload"]
            if quickload is not None:
                manifest = Path(quickload["manifest"]["path"])
                validator = GUARD._import_quickload_evidence()
                production = validator.quickload_report_fields(
                    manifest.with_name("manifest.json"), manifest.read_bytes())
                text += "".join(f"{key}={value}\n"
                                for key, value in production.items())
            report.write_text(text)
            report.chmod(0o600)
            return report
        finally:
            GUARD._close_pinned(pinned)

    def validate_publication(self, report: Path) -> None:
        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()):
            GUARD.validate_clone_publication(
                self.prepared, self.ledger, self.staged, report,
            )

    def test_real_darwin_apfs_clone_and_bidirectional_cow(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin/APFS-only contract")
        source = self.root / "cow-source"
        destination_parent = self.root / "cow-destination"
        destination_parent.mkdir()
        source.write_bytes(b"A" * 8192)
        source.chmod(0o600)
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        parent_fd = os.open(destination_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            GUARD._fclonefileat(source_fd, parent_fd, "clone", "clone")
        finally:
            os.close(parent_fd)
            os.close(source_fd)
        clone = destination_parent / "clone"
        self.assertEqual(source.stat().st_dev, clone.stat().st_dev)
        self.assertNotEqual(source.stat().st_ino, clone.stat().st_ino)
        self.assertEqual((source.stat().st_nlink, clone.stat().st_nlink), (1, 1))
        self.assertEqual(self.digest(source.read_bytes()), self.digest(clone.read_bytes()))
        clone.write_bytes(b"B" * 8192)
        self.assertEqual(source.read_bytes(), b"A" * 8192)
        source.write_bytes(b"C" * 8192)
        self.assertEqual(clone.read_bytes(), b"B" * 8192)

    def test_full_stage_has_closed_clone_required_ledger_and_byte_compatible_manifest(self) -> None:
        self.stage()
        ledger = self.read_ledger(self.pending, finalized=False)
        self.assertEqual(ledger["stage_mode"], "clone-required")
        self.assertEqual(ledger["method"], "fclonefileat")
        self.assertEqual(ledger["fallback_count"], 0)
        self.assertEqual(ledger["flags"]["value"], 0x001B)
        staged = GUARD._parse_staged_bytes(self.staged.read_bytes(), "staged")
        self.assertEqual(sorted(staged), sorted(path for path in self.files
                                                if not path.startswith("_appdata_")))
        for record in ledger["files"]:
            self.assertNotEqual(record["source"]["ino"], record["destination"]["ino"])
            self.assertEqual(record["destination"]["nlink"], 1)

    def test_core_simulator_container_xattrs_are_accepted_bound_and_finalized(self) -> None:
        expected = self.container_manager_xattrs()
        self.set_synthetic_xattrs({self.container: expected})
        self.stage()
        pending = self.read_ledger(self.pending, finalized=False)
        self.assertEqual(pending["destination_parent"]["xattrs"], expected)
        self.finalize()
        finalized = self.read_ledger(self.ledger, finalized=True)
        self.assertEqual(finalized["destination_parent"]["xattrs"], expected)

    def test_core_simulator_container_xattr_subset_is_accepted(self) -> None:
        subset = {"com.apple.containermanager.uuid":
                  self.container_manager_xattrs()["com.apple.containermanager.uuid"]}
        self.set_synthetic_xattrs({self.container: subset})
        self.stage()
        self.assertEqual(self.read_ledger(self.pending, finalized=False)
                         ["destination_parent"]["xattrs"], subset)
        self.finalize()

    def test_core_simulator_container_xattr_change_removal_and_addition_fail_finalize(self) -> None:
        original = self.container_manager_xattrs()
        for mutation in ("change", "remove", "add"):
            with self.subTest(mutation=mutation):
                self.destination = self.container / f"Documents-{mutation}"
                self.staged = self.artifacts / f"staged-{mutation}.tsv"
                self.pending = self.artifacts / f"pending-{mutation}.json"
                self.ledger = self.artifacts / f"ledger-{mutation}.json"
                current = dict(original)
                self.set_synthetic_xattrs({self.container: current})
                self.stage()
                if mutation == "change":
                    current["com.apple.containermanager.uuid"] = (
                        self.encoded_xattrs({"value": b"changed"})["value"])
                elif mutation == "remove":
                    del current["com.apple.containermanager.uuid"]
                else:
                    current["com.apple.containermanager.extra"] = (
                        self.encoded_xattrs({"value": b"extra"})["value"])
                self.set_synthetic_xattrs({self.container: current})
                with self.assertRaisesRegex(
                        GUARD.GuardError,
                        "destination parent|unapproved xattr"):
                    self.finalize()
                self.assertFalse(self.ledger.exists())

    def test_core_simulator_parent_rejects_unknown_and_provenance_xattrs(self) -> None:
        for name in ("com.apple.containermanager.extra", "com.apple.provenance"):
            with self.subTest(name=name):
                xattrs = self.container_manager_xattrs()
                xattrs[name] = self.encoded_xattrs({"value": b"forged"})["value"]
                self.set_synthetic_xattrs({self.container: xattrs})
                with self.assertRaisesRegex(GUARD.GuardError, "unapproved xattr"):
                    self.stage()
                self.assertFalse(self.staged.exists())
                self.assertFalse(self.pending.exists())

    def test_core_simulator_xattrs_remain_rejected_outside_destination_parent(self) -> None:
        container_xattrs = self.container_manager_xattrs()
        for target, label in ((self.prepared, "prepared root"),
                              (self.documents, "prepared Documents")):
            self.set_synthetic_xattrs({self.container: {}, target: container_xattrs})
            descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                with self.subTest(label=label), \
                     self.assertRaisesRegex(GUARD.GuardError, "unapproved xattr"):
                    GUARD._directory_metadata(descriptor, label, child=False)
            finally:
                os.close(descriptor)

        cases = (
            (self.documents / "resources/resources.db0", "prepared retail file"),
            (self.artifacts, "artifact parent"),
        )
        for index, (target, label) in enumerate(cases):
            self.destination = self.container / f"Documents-reject-{index}"
            self.staged = self.artifacts / f"staged-reject-{index}.tsv"
            self.pending = self.artifacts / f"pending-reject-{index}.json"
            self.ledger = self.artifacts / f"ledger-reject-{index}.json"
            self.set_synthetic_xattrs({self.container: {}, target: container_xattrs})
            with self.subTest(label=label), \
                 self.assertRaisesRegex(GUARD.GuardError, "unapproved xattr"):
                self.stage()
            self.assertFalse(self.staged.exists())
            self.assertFalse(self.pending.exists())

        self.destination.mkdir(mode=0o700)
        self.set_synthetic_xattrs({self.container: {}, self.destination: container_xattrs})
        with self.assertRaisesRegex(GUARD.GuardError, "unapproved xattr"):
            self.stage()
        self.assertFalse(self.staged.exists())
        self.assertFalse(self.pending.exists())

    def test_forged_ledger_destination_parent_xattrs_are_rejected(self) -> None:
        expected = self.container_manager_xattrs()
        self.set_synthetic_xattrs({self.container: expected})
        self.stage()
        ledger = json.loads(self.pending.read_text())
        ledger["destination_parent"]["xattrs"] = {
            "com.apple.provenance": self.encoded_xattrs({"value": b"forged"})["value"]
        }
        self.pending.write_bytes(GUARD._canonical_json(ledger))
        self.pending.chmod(0o600)
        with self.assertRaisesRegex(GUARD.GuardError, "invalid xattrs"):
            GUARD._read_clone_ledger(self.pending, finalized=False)

    def test_ctypes_dispatch_uses_exact_flags(self) -> None:
        class Function:
            def __init__(self):
                self.calls = []
            def __call__(self, *args):
                self.calls.append(args)
                return 0
        function = Function()
        library = types.SimpleNamespace(fclonefileat=function)
        with mock.patch.object(GUARD.sys, "platform", "darwin"), \
             mock.patch.object(GUARD.ctypes, "CDLL", return_value=library):
            GUARD._fclonefileat(7, 8, "target", "relative")
        self.assertEqual(function.calls, [(7, 8, b"target", 0x001B)])

    def test_missing_symbol_and_clone_errnos_have_no_fallback(self) -> None:
        with mock.patch.object(GUARD.sys, "platform", "darwin"), \
             mock.patch.object(GUARD.ctypes, "CDLL", return_value=types.SimpleNamespace()):
            with self.assertRaisesRegex(GUARD.GuardError, "no copy fallback"):
                GUARD._fclonefileat(1, 2, "x", "x")
        for number in (errno.ENOTSUP, errno.EXDEV, errno.EEXIST, errno.EINVAL,
                       errno.ENOSPC, errno.EIO):
            with self.subTest(errno=number):
                function = mock.Mock(return_value=-1)
                library = types.SimpleNamespace(fclonefileat=function)
                with mock.patch.object(GUARD.sys, "platform", "darwin"), \
                     mock.patch.object(GUARD.ctypes, "CDLL", return_value=library), \
                     mock.patch.object(GUARD.ctypes, "get_errno", return_value=number):
                    with self.assertRaises(GUARD.GuardError) as raised:
                        GUARD._fclonefileat(1, 2, "x", "x")
                self.assertIn(errno.errorcode[number], str(raised.exception))
                function.assert_called_once_with(1, 2, b"x", 0x001B)

    def test_cli_matrix_rejects_conflicts_duplicates_and_forged_fd(self) -> None:
        cases = (
            (["--backup", str(self.documents), "--prepared", str(self.prepared),
              "--runtime", "27.0"], "mutually exclusive"),
            (["--prepared", str(self.prepared), "--manifest", str(self.manifest),
              "--runtime", "27.0"], "only valid with --backup"),
            (["--prepared", str(self.prepared), "--prepared", str(self.prepared)], "duplicate --prepared"),
        )
        for arguments, marker in cases:
            with self.subTest(marker=marker):
                result = subprocess.run(("bash", str(RUNNER), *arguments), text=True,
                                        capture_output=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(marker, result.stderr)
        environment = dict(os.environ)
        environment["OPENXRAY_PREPARED_LOCK_FD"] = "999999"
        result = subprocess.run(("bash", str(RUNNER), "--prepared", str(self.prepared),
                                 "--runtime", "27.0"),
                                text=True, capture_output=True, check=False, env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lock verification failed", result.stderr)

    def test_wrapper_requires_exact_single_child_prepared_and_passes_only_fd(self) -> None:
        with mock.patch.object(RI.sys, "platform", "darwin"), \
             mock.patch.object(RI, "verify_prepared"), \
             mock.patch.object(RI.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run, \
             mock.patch.dict(RI.os.environ, {"OPENXRAY_PREPARED_RUNNER_LOCKED": "forged"}):
            result = RI.run_prepared_runner(
                self.prepared, RUNNER, ["--prepared", str(self.prepared), "--runtime", "27.0"])
        self.assertEqual(result, 0)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["pass_fds"], (kwargs["pass_fds"][0],))
        self.assertEqual(kwargs["env"]["OPENXRAY_PREPARED_LOCK_FD"], str(kwargs["pass_fds"][0]))
        self.assertNotIn("OPENXRAY_PREPARED_RUNNER_LOCKED", kwargs["env"])
        for arguments in (
            ["--prepared", str(self.prepared), "--prepared", str(self.prepared)],
            ["--prepared", str(self.root / "different")],
            ["--runtime", "27.0"],
        ):
            with self.subTest(arguments=arguments), \
                 mock.patch.object(RI.sys, "platform", "darwin"):
                with self.assertRaises(RI.ImportError):
                    RI.run_prepared_runner(self.prepared, RUNNER, arguments)
        with mock.patch.object(RI.sys, "platform", "linux"):
            with self.assertRaisesRegex(RI.ImportError, "Darwin APFS"):
                RI.run_prepared_runner(
                    self.prepared, RUNNER, ["--prepared", str(self.prepared)])
        legacy_destination = self.container / "LegacyDocuments"
        legacy_manifest = self.artifacts / "legacy-staged.tsv"
        with mock.patch.object(GUARD.sys, "platform", "linux"):
            GUARD.stage_retail(
                self.documents, self.manifest, legacy_destination, False, legacy_manifest,
            )
        self.assertTrue(legacy_manifest.is_file())
        self.assertTrue((legacy_destination / "resources/resources.db0").is_file())

    def test_inherited_lock_rejects_forged_fd_and_competing_exclusive_lock(self) -> None:
        unrelated = self.root / "unrelated"
        unrelated.write_bytes(b"")
        unrelated.chmod(0o600)
        forged = os.open(unrelated, os.O_RDONLY)
        try:
            with self.assertRaisesRegex(RI.ImportError, "does not identify"):
                RI.verify_inherited_prepared_lock(self.prepared, forged)
        finally:
            os.close(forged)
        exclusive = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RI.ImportError, "currently mutating"):
                RI.prepared_runner_lock(self.prepared)
        finally:
            os.close(exclusive)

    def test_child_duplicate_keeps_shared_lock_after_parent_fd_closes(self) -> None:
        parent = RI.prepared_runner_lock(self.prepared)
        child = os.dup(parent)
        os.close(parent)
        RI.verify_inherited_prepared_lock(self.prepared, child)
        competitor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(child)
            fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(competitor)

    def test_overlap_ancestor_and_leaf_symlink_are_rejected(self) -> None:
        with self.assertRaisesRegex(GUARD.GuardError, "pairwise disjoint"):
            GUARD._clone_paths(self.prepared, self.prepared / "destination",
                               self.staged, self.pending)
        alias = self.root / "alias"
        alias.symlink_to(self.container, target_is_directory=True)
        with mock.patch.object(GUARD, "_import_retail_import", return_value=self.importer()):
            with self.assertRaises(GUARD.GuardError):
                GUARD.clone_stage(self.prepared, alias / "Documents", self.staged,
                                  self.pending, False)
        self.assertFalse(self.staged.exists())
        self.assertFalse(self.pending.exists())

    def test_hardlink_source_and_preexisting_destination_are_rejected(self) -> None:
        source = self.documents / "resources/resources.db0"
        hardlink = self.root / "source-hardlink"
        os.link(source, hardlink)
        try:
            with mock.patch.object(GUARD, "_import_retail_import", return_value=self.importer()):
                with self.assertRaisesRegex(GUARD.GuardError, "nlink=1"):
                    GUARD.clone_stage(self.prepared, self.destination, self.staged,
                                      self.pending, False)
        finally:
            hardlink.unlink()
        self.destination.mkdir(exist_ok=True)
        (self.destination / "occupied").write_bytes(b"x")
        with mock.patch.object(GUARD, "_import_retail_import", return_value=self.importer()):
            with self.assertRaisesRegex(GUARD.GuardError, "must be empty"):
                GUARD.clone_stage(self.prepared, self.destination, self.staged,
                                  self.pending, False)

    def test_source_acl_is_rejected(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin extended ACL contract requires macOS")
        source = self.documents / "resources/resources.db0"
        result = subprocess.run(
            ("chmod", "+a", f"{pwd.getpwuid(os.geteuid()).pw_name} allow read", str(source)),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with mock.patch.object(GUARD, "_import_retail_import", return_value=self.importer()), \
             self.assertRaisesRegex(GUARD.GuardError, "unapproved ACL"):
            GUARD.clone_stage(self.prepared, self.destination, self.staged,
                              self.pending, False)

    def test_parent_replacement_and_leaf_symlink_fail_post_runtime(self) -> None:
        for mutation in ("parent", "leaf"):
            with self.subTest(mutation=mutation):
                self.destination = self.container / f"Documents-{mutation}"
                self.staged = self.artifacts / f"staged-{mutation}.tsv"
                self.pending = self.artifacts / f"pending-{mutation}.json"
                self.ledger = self.artifacts / f"ledger-{mutation}.json"
                self.stage()
                if mutation == "parent":
                    parent = self.destination / "resources"
                    parent.rename(parent.with_name("resources-old"))
                    parent.mkdir(mode=0o700)
                else:
                    victim = self.destination / "resources/resources.db0"
                    victim.unlink()
                    victim.symlink_to(self.documents / "resources/resources.db0")
                with self.assertRaises(GUARD.GuardError):
                    self.finalize()
                self.assertFalse(self.ledger.exists())

    def test_source_root_swap_after_open_fails_final_rebind(self) -> None:
        self.stage()
        ledger = self.read_ledger(self.pending, finalized=False)
        original_metadata = GUARD._file_metadata
        swapped = False

        def swap_root_after_metadata(fd, label, *, digest):
            nonlocal swapped
            metadata = original_metadata(fd, label, digest=digest)
            if not swapped and "prepared revalidation source file" in label:
                swapped = True
                self.prepared.rename(self.root / "prepared-opened-root")
                self.prepared.mkdir(mode=0o700)
                (self.prepared / "Documents").mkdir(mode=0o700)
            return metadata

        with mock.patch.object(GUARD, "_file_metadata",
                               side_effect=swap_root_after_metadata), \
             self.assertRaisesRegex(GUARD.GuardError, "prepared root identity"):
            GUARD._validate_prepared_source_records(ledger, self.importer())
        self.assertTrue(swapped)

    def test_source_leaf_swap_after_metadata_fails_descriptor_rebind(self) -> None:
        self.stage()
        ledger = self.read_ledger(self.pending, finalized=False)
        victim = self.documents / "resources/resources.db0"
        original_metadata = GUARD._file_metadata
        swapped = False

        def swap_leaf_after_metadata(fd, label, *, digest):
            nonlocal swapped
            metadata = original_metadata(fd, label, digest=digest)
            if not swapped and label.endswith("source file resources/resources.db0"):
                swapped = True
                replacement = victim.with_name("resources.db0.replacement")
                replacement.write_bytes(victim.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(victim)
            return metadata

        with mock.patch.object(GUARD, "_file_metadata",
                               side_effect=swap_leaf_after_metadata), \
             self.assertRaisesRegex(GUARD.GuardError, "pathname changed"):
            GUARD._validate_prepared_source_records(ledger, self.importer())
        self.assertTrue(swapped)

    def test_destination_root_swap_after_open_fails_final_rebind(self) -> None:
        self.stage()
        ledger = self.read_ledger(self.pending, finalized=False)
        original_metadata = GUARD._destination_file_metadata
        swapped = False

        def swap_root_after_metadata(fd, label, *, digest):
            nonlocal swapped
            metadata = original_metadata(fd, label, digest=digest)
            if not swapped and "post-runtime destination file" in label:
                swapped = True
                self.destination.rename(self.container / "Documents-opened-root")
                self.destination.mkdir(mode=0o700)
            return metadata

        with mock.patch.object(GUARD, "_destination_file_metadata",
                               side_effect=swap_root_after_metadata), \
             self.assertRaisesRegex(GUARD.GuardError, "destination root identity"):
            GUARD._validate_destination_post_runtime(ledger, None)
        self.assertTrue(swapped)

    def test_destination_leaf_swap_after_metadata_fails_descriptor_rebind(self) -> None:
        self.stage()
        ledger = self.read_ledger(self.pending, finalized=False)
        victim = self.destination / "resources/resources.db0"
        original_metadata = GUARD._destination_file_metadata
        swapped = False

        def swap_leaf_after_metadata(fd, label, *, digest):
            nonlocal swapped
            metadata = original_metadata(fd, label, digest=digest)
            if not swapped and label.endswith("destination file resources/resources.db0"):
                swapped = True
                replacement = victim.with_name("resources.db0.replacement")
                replacement.write_bytes(victim.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(victim)
            return metadata

        with mock.patch.object(GUARD, "_destination_file_metadata",
                               side_effect=swap_leaf_after_metadata), \
             self.assertRaisesRegex(GUARD.GuardError, "pathname changed"):
            GUARD._validate_destination_post_runtime(ledger, None)
        self.assertTrue(swapped)

    def test_destination_leaf_swap_between_pre_open_stat_and_fstat_fails_exactly(self) -> None:
        self.stage()
        ledger = self.read_ledger(self.pending, finalized=False)
        victim = self.destination / "resources/resources.db0"
        replacement = victim.with_name("resources.db0.pre-open-replacement")
        replacement.write_bytes(victim.read_bytes())
        replacement.chmod(0o600)
        real_open = GUARD.os.open
        swapped = False

        def swap_at_open(path, flags, *arguments, **kwargs):
            nonlocal swapped
            if (not swapped and path == victim.name and "dir_fd" in kwargs
                    and flags & os.O_RDONLY == os.O_RDONLY):
                swapped = True
                replacement.replace(victim)
            return real_open(path, flags, *arguments, **kwargs)

        with mock.patch.object(GUARD.os, "open", side_effect=swap_at_open), \
             self.assertRaisesRegex(
                 GUARD.GuardError,
                 "descriptor-relative pre-open stat and immediate fstat",
             ):
            GUARD._validate_destination_post_runtime(ledger, None)
        self.assertTrue(swapped)

    def test_destination_parent_swap_during_stage_never_publishes_artifacts(self) -> None:
        original_clone = GUARD._fclonefileat
        swapped = False
        def clone_then_swap(*arguments):
            nonlocal swapped
            original_clone(*arguments)
            if not swapped:
                swapped = True
                self.container.rename(self.root / "container-old")
                self.container.mkdir(mode=0o700)
        with mock.patch.object(GUARD, "_import_retail_import", return_value=self.importer()), \
             mock.patch.object(GUARD, "_fclonefileat", side_effect=clone_then_swap):
            with self.assertRaises(GUARD.GuardError):
                GUARD.clone_stage(self.prepared, self.destination, self.staged,
                                  self.pending, False)
        self.assertTrue(swapped)
        self.assertFalse(self.staged.exists())
        self.assertFalse(self.pending.exists())

    def test_clone_stage_child_metadata_baseexception_closes_partial_descriptors(self) -> None:
        real_metadata = GUARD._directory_metadata

        def fail_first_child(descriptor, label, *, child, **kwargs):
            if child and label.startswith("clone destination directory"):
                raise KeyboardInterrupt
            return real_metadata(descriptor, label, child=child, **kwargs)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             mock.patch.object(GUARD, "_directory_metadata",
                               side_effect=fail_first_child), \
             self.assertRaises(KeyboardInterrupt):
            GUARD.clone_stage(
                self.prepared, self.destination, self.staged, self.pending, False,
            )
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)
        self.assertFalse(self.staged.exists())
        self.assertFalse(self.pending.exists())

    def test_immutable_inode_mode_and_xattr_mutations_fail(self) -> None:
        mutations = ("inode", "mode", "xattr")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.destination = self.container / f"Documents-{mutation}"
                self.staged = self.artifacts / f"staged-{mutation}.tsv"
                self.pending = self.artifacts / f"pending-{mutation}.json"
                self.ledger = self.artifacts / f"ledger-{mutation}.json"
                self.stage()
                victim = self.destination / "resources/resources.db0"
                if mutation == "inode":
                    replacement = victim.with_name("replacement")
                    replacement.write_bytes(victim.read_bytes())
                    replacement.chmod(0o600)
                    replacement.replace(victim)
                    context = mock.patch.object(GUARD, "_xattrs", wraps=GUARD._xattrs)
                elif mutation == "mode":
                    victim.chmod(0o640)
                    context = mock.patch.object(GUARD, "_xattrs", wraps=GUARD._xattrs)
                else:
                    victim_inode = victim.stat().st_ino
                    original_xattrs = GUARD._xattrs
                    def changed_xattrs(fd, label, **kwargs):
                        value = original_xattrs(fd, label, **kwargs)
                        if os.fstat(fd).st_ino == victim_inode:
                            return {"com.apple.provenance": base64.b64encode(b"changed").decode()}
                        return value
                    context = mock.patch.object(GUARD, "_xattrs", side_effect=changed_xattrs)
                with context, self.assertRaises(GUARD.GuardError):
                    self.finalize()

    def test_prepared_source_metadata_mutation_fails_exact_revalidation(self) -> None:
        self.stage()
        source = self.documents / "resources/resources.db0"
        source.chmod(0o400)
        with self.assertRaisesRegex(GUARD.GuardError, "prepared revalidation"):
            self.finalize()

    def test_only_explicit_mutable_save_may_replace_inode(self) -> None:
        self.stage(saves=True)
        save = self.destination / "_appdata_/savedgames/slot.scop"
        original_inode = save.stat().st_ino
        replacement = save.with_name("slot.new")
        replacement.write_bytes(b"runtime-save")
        replacement.chmod(0o600)
        replacement.replace(save)
        self.finalize(saves=True, mutable="slot")
        ledger = self.read_ledger(self.ledger, finalized=True)
        post = {record["path"]: record for record in ledger["post_runtime"]["files"]}
        self.assertEqual(ledger["post_runtime"]["destination_boundary"],
                         "validated-before-container-delete")
        self.assertTrue(post["_appdata_/savedgames/slot.scop"]["mutable"])
        self.assertNotEqual(post["_appdata_/savedgames/slot.scop"]["destination"]["ino"],
                            original_inode)

    def test_mutable_save_cannot_gain_unbound_provenance_xattr(self) -> None:
        self.stage(saves=True)
        save = self.destination / "_appdata_/savedgames/slot.scop"
        replacement = save.with_name("slot.new")
        replacement.write_bytes(b"runtime-save")
        replacement.chmod(0o600)
        replacement.replace(save)
        replacement_inode = save.stat().st_ino
        original_xattrs = GUARD._xattrs

        def injected_xattrs(fd, label, **kwargs):
            value = original_xattrs(fd, label, **kwargs)
            if os.fstat(fd).st_ino == replacement_inode:
                return {"com.apple.provenance": base64.b64encode(b"unbound").decode()}
            return value

        with mock.patch.object(GUARD, "_xattrs", side_effect=injected_xattrs), \
             self.assertRaisesRegex(GUARD.GuardError, "mutable save metadata"):
            self.finalize(saves=True, mutable="slot")
        self.assertFalse(self.ledger.exists())

    def test_mutable_save_acl_is_rejected_on_darwin(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin extended ACL contract requires macOS")
        self.stage(saves=True)
        save = self.destination / "_appdata_/savedgames/slot.scop"
        replacement = save.with_name("slot.new")
        replacement.write_bytes(b"runtime-save")
        replacement.chmod(0o600)
        replacement.replace(save)
        result = subprocess.run(
            ("chmod", "+a",
             f"{pwd.getpwuid(os.geteuid()).pw_name} allow read", str(save)),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaisesRegex(GUARD.GuardError, "unapproved ACL"):
            self.finalize(saves=True, mutable="slot")
        self.assertFalse(self.ledger.exists())

    def test_malformed_extra_key_and_replaced_ledger_fail(self) -> None:
        self.stage()
        value = json.loads(self.pending.read_text())
        value["unexpected"] = True
        self.pending.write_bytes(GUARD._canonical_json(value))
        self.pending.chmod(0o600)
        with self.assertRaisesRegex(GUARD.GuardError, "top-level"):
            GUARD._read_clone_ledger(self.pending, finalized=False)

    def test_stage_transaction_publishes_ledger_first_and_cleans_second_failure(self) -> None:
        calls = []
        original = GUARD._atomic_publish_new
        def ordered(path, data, label, expected_parent=None):
            calls.append(label)
            if label == "staged-files.tsv":
                raise GUARD.GuardError("injected second publication failure")
            return original(path, data, label, expected_parent)
        with mock.patch.object(GUARD, "_import_retail_import", return_value=self.importer()), \
             mock.patch.object(GUARD, "_atomic_publish_new", side_effect=ordered):
            with self.assertRaisesRegex(GUARD.GuardError, "second publication"):
                GUARD.clone_stage(self.prepared, self.destination, self.staged,
                                  self.pending, False)
        self.assertEqual(calls, ["pending clone ledger", "staged-files.tsv"])
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.staged.exists())

    def test_atomic_publish_failures_never_leave_an_authoritative_target(self) -> None:
        for boundary in ("write", "rename", "parent-fsync"):
            with self.subTest(boundary=boundary):
                parent = self.root / f"atomic-{boundary}-parent"
                parent.mkdir(mode=0o700)
                target = parent / "artifact"
                if boundary == "write":
                    patcher = mock.patch.object(GUARD.os, "write", side_effect=OSError(errno.EIO, "write"))
                elif boundary == "rename":
                    patcher = mock.patch.object(GUARD, "_rename_exclusive_at",
                                                side_effect=GUARD.GuardError("rename"))
                else:
                    original_fsync = GUARD.os.fsync
                    calls = 0
                    def fail_parent_fsync(fd):
                        nonlocal calls
                        calls += 1
                        if calls == 2:
                            raise OSError(errno.EIO, "parent fsync")
                        return original_fsync(fd)
                    patcher = mock.patch.object(GUARD.os, "fsync", side_effect=fail_parent_fsync)
                with patcher, self.assertRaises((OSError, GUARD.GuardError)):
                    GUARD._atomic_publish_new(target, b"payload", boundary)
                self.assertFalse(target.exists())
                parent_fd = GUARD._open_absolute_directory_nofollow(parent, "atomic test parent")
                try:
                    if boundary in {"write", "rename"}:
                        with self.assertRaises(GUARD.GuardError):
                            GUARD._validate_transaction_parent_fd(
                                parent_fd, parent, "atomic test parent")
                    else:
                        GUARD._validate_transaction_parent_fd(
                            parent_fd, parent, "atomic test parent")
                        tombstones = list(parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone"))
                        self.assertEqual(len(tombstones), 1)
                        self.assertEqual(tombstones[0].read_bytes(), b"")
                finally:
                    os.close(parent_fd)

    def test_completed_quarantine_is_zero_non_authoritative_and_allows_publication(self) -> None:
        parent = self.root / "completed-quarantine"
        parent.mkdir(mode=0o700)
        victim = parent / "owned.pending"
        victim.write_bytes(b"result=PASS\nforeign-looking-field=value\n")
        victim.chmod(0o600)
        identity = (victim.stat().st_dev, victim.stat().st_ino)
        parent_fd = GUARD._open_absolute_directory_nofollow(parent, "quarantine parent")
        try:
            self.assertTrue(GUARD._quarantine_owned_at(
                parent_fd, parent, victim.name, identity, "owned candidate"))
            GUARD._validate_transaction_parent_fd(
                parent_fd, parent, "completed quarantine parent")
        finally:
            os.close(parent_fd)
        tombstones = list(parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone"))
        self.assertEqual(len(tombstones), 1)
        self.assertEqual(tombstones[0].read_bytes(), b"")
        self.assertFalse(victim.exists())

        pending, report = parent / "report.pending", parent / "report.txt"
        GUARD.prepare_report(pending, b"result=PASS\nfield=value\n")
        pending_identity = (pending.stat().st_dev, pending.stat().st_ino)
        with mock.patch.object(GUARD.os, "link", side_effect=AssertionError("hardlink forbidden")):
            GUARD.publish_report(pending, report)
        self.assertFalse(pending.exists())
        self.assertEqual((report.stat().st_dev, report.stat().st_ino), pending_identity)
        self.assertEqual(report.stat().st_nlink, 1)
        parent_fd = GUARD._open_absolute_directory_nofollow(parent, "published parent")
        try:
            GUARD._validate_transaction_parent_fd(parent_fd, parent, "published parent")
        finally:
            os.close(parent_fd)

    def test_transaction_parent_rejects_mode_xattr_acl_and_flags_mutations(self) -> None:
        mode_parent = self.root / "private-parent-mode"
        mode_parent.mkdir(mode=0o700)
        mode_parent.chmod(0o755)
        mode_fd = GUARD._open_absolute_directory_nofollow(mode_parent, "mode parent")
        try:
            with self.assertRaisesRegex(GUARD.GuardError, "0700"):
                GUARD._validate_transaction_parent_fd(
                    mode_fd, mode_parent, "mode parent")
        finally:
            os.close(mode_fd)
            mode_parent.chmod(0o700)

        for index, opaque in enumerate((b"opaque-parent-alpha", b"\x00opaque-parent-beta\xff")):
            with self.subTest(provenance=index):
                provenance_parent = self.root / f"private-parent-provenance-{index}"
                provenance_parent.mkdir(mode=0o700)
                identity = (provenance_parent.stat().st_dev,
                            provenance_parent.stat().st_ino)
                encoded = base64.b64encode(opaque).decode("ascii")
                self.synthetic_xattrs[identity] = {"com.apple.provenance": encoded}
                provenance_fd = GUARD._open_absolute_directory_nofollow(
                    provenance_parent, "provenance parent")
                try:
                    metadata = GUARD._validate_transaction_parent_fd(
                        provenance_fd, provenance_parent, "provenance parent")
                    self.assertEqual(
                        metadata["xattrs"], {"com.apple.provenance": encoded})
                finally:
                    os.close(provenance_fd)

        xattr_parent = self.root / "private-parent-unknown-xattr"
        xattr_parent.mkdir(mode=0o700)
        identity = (xattr_parent.stat().st_dev, xattr_parent.stat().st_ino)
        self.synthetic_xattrs[identity] = {
            "unapproved.test": base64.b64encode(b"mutation").decode("ascii")}
        xattr_fd = GUARD._open_absolute_directory_nofollow(xattr_parent, "xattr parent")
        try:
            with self.assertRaisesRegex(GUARD.GuardError, r"unapproved xattr"):
                GUARD._validate_transaction_parent_fd(
                    xattr_fd, xattr_parent, "xattr parent")
        finally:
            os.close(xattr_fd)

        if sys.platform == "darwin" and hasattr(os, "chflags") and hasattr(stat, "UF_NODUMP"):
            flags_parent = self.root / "private-parent-flags"
            flags_parent.mkdir(mode=0o700)
            os.chflags(flags_parent, stat.UF_NODUMP, follow_symlinks=False)
            flags_fd = GUARD._open_absolute_directory_nofollow(flags_parent, "flags parent")
            try:
                with self.assertRaisesRegex(GUARD.GuardError, "file flags"):
                    GUARD._validate_transaction_parent_fd(
                        flags_fd, flags_parent, "flags parent")
            finally:
                os.close(flags_fd)
                os.chflags(flags_parent, 0, follow_symlinks=False)

        if sys.platform != "darwin":
            self.skipTest("Darwin extended ACL contract requires macOS")
        acl_parent = self.root / "private-parent-acl"
        acl_parent.mkdir(mode=0o700)
        acl_result = subprocess.run(
            ("chmod", "+a",
             f"{pwd.getpwuid(os.geteuid()).pw_name} allow read", str(acl_parent)),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(acl_result.returncode, 0, acl_result.stderr)
        acl_fd = GUARD._open_absolute_directory_nofollow(acl_parent, "ACL parent")
        try:
            with self.assertRaisesRegex(GUARD.GuardError, "unapproved ACL"):
                GUARD._validate_transaction_parent_fd(
                    acl_fd, acl_parent, "ACL parent")
        finally:
            os.close(acl_fd)

    def test_private_parent_provenance_add_remove_and_value_change_fail_after_rename(self) -> None:
        first = base64.b64encode(b"first-opaque-provenance").decode("ascii")
        second = base64.b64encode(b"second-opaque-provenance-value").decode("ascii")
        mutations = {
            "add": ({}, {"com.apple.provenance": first}),
            "remove": ({"com.apple.provenance": first}, {}),
            "value-change": ({"com.apple.provenance": first},
                             {"com.apple.provenance": second}),
        }
        real_rename = GUARD._rename_exclusive_at
        for mutation, (before, after) in mutations.items():
            with self.subTest(mutation=mutation):
                parent = self.root / f"parent-provenance-{mutation}"
                parent.mkdir(mode=0o700)
                identity = (parent.stat().st_dev, parent.stat().st_ino)
                self.synthetic_xattrs[identity] = dict(before)
                pending, final = parent / "report.pending", parent / "report.txt"
                payload = b"result=PASS\nfield=value\n"
                GUARD.prepare_report(pending, payload)
                pending_identity = (pending.stat().st_dev, pending.stat().st_ino)

                def mutate_after_report_rename(parent_fd, source, destination):
                    result = real_rename(parent_fd, source, destination)
                    if source == pending.name and destination == final.name:
                        self.synthetic_xattrs[identity] = dict(after)
                    return result

                with mock.patch.object(
                         GUARD, "_rename_exclusive_at",
                         side_effect=mutate_after_report_rename,
                     ), self.assertRaisesRegex(
                         GUARD.GuardError, "neutralization was incomplete"):
                    GUARD.publish_report(pending, final)
                self.assertTrue(final.exists())
                self.assertFalse(pending.exists())
                self.assertEqual(final.read_bytes(), payload)
                self.assertEqual(
                    (final.stat().st_dev, final.stat().st_ino), pending_identity)
                self.assertFalse(any(
                    parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone")))
                publication_records = list(
                    parent.glob(f"{GUARD.PUBLICATION_PREFIX}*.txn.json"))
                self.assertEqual(len(publication_records), 1)
                transaction = json.loads(publication_records[0].read_text())
                self.assertEqual(transaction["schema"], GUARD.PUBLICATION_TXN_SCHEMA)
                self.assertEqual(transaction["parent"]["xattrs"], before)
                parent_fd = GUARD._open_absolute_directory_nofollow(
                    parent, "drifted publication parent")
                try:
                    with self.assertRaisesRegex(
                            GUARD.GuardError, "poisoned|parent metadata changed"):
                        GUARD._validate_transaction_parent_fd(
                            parent_fd, parent, "drifted publication parent")
                finally:
                    os.close(parent_fd)

    def test_emergency_neutralization_requires_the_original_active_transaction(self) -> None:
        parent = self.root / "publication-active-transaction-mutation"
        parent.mkdir(mode=0o700)
        self.synthetic_xattrs[(parent.stat().st_dev, parent.stat().st_ino)] = {}
        pending, final = parent / "report.pending", parent / "report.txt"
        payload = b"result=PASS\nactive-transaction=original\n"
        GUARD.prepare_report(pending, payload)
        pending_identity = (pending.stat().st_dev, pending.stat().st_ino)
        real_rename = GUARD._rename_exclusive_at

        def mutate_transaction_after_report_rename(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == pending.name and destination == final.name:
                transaction_path = next(parent.glob(
                    f"{GUARD.PUBLICATION_PREFIX}*.txn.json"))
                transaction = json.loads(transaction_path.read_text())
                transaction["token"] = "f" * 32
                transaction_path.write_bytes(GUARD._canonical_json(transaction))
                transaction_path.chmod(0o600)
            return result

        with mock.patch.object(
                 GUARD, "_rename_exclusive_at",
                 side_effect=mutate_transaction_after_report_rename,
             ), self.assertRaisesRegex(
                 GUARD.GuardError, "neutralization was incomplete"):
            GUARD.publish_report(pending, final)
        self.assertFalse(pending.exists())
        self.assertEqual(final.read_bytes(), payload)
        self.assertEqual(
            (final.stat().st_dev, final.stat().st_ino), pending_identity)
        self.assertFalse(any(parent.glob(
            f"{GUARD.QUARANTINE_PREFIX}*.tombstone")))
        parent_fd = GUARD._open_absolute_directory_nofollow(
            parent, "active transaction mutation parent")
        try:
            with self.assertRaisesRegex(
                    GUARD.GuardError, "invalid closed schema|poisoned"):
                GUARD._validate_transaction_parent_fd(
                    parent_fd, parent, "active transaction mutation parent")
        finally:
            os.close(parent_fd)

    def test_quarantine_parent_provenance_change_before_rename_never_truncates(self) -> None:
        parent = self.root / "quarantine-parent-provenance-change"
        parent.mkdir(mode=0o700)
        identity = (parent.stat().st_dev, parent.stat().st_ino)
        first = base64.b64encode(b"quarantine-opaque-before").decode("ascii")
        second = base64.b64encode(b"quarantine-opaque-after").decode("ascii")
        self.synthetic_xattrs[identity] = {"com.apple.provenance": first}
        victim = parent / "owned"
        payload = b"result=PASS\nowned=true\n"
        victim.write_bytes(payload)
        victim.chmod(0o600)
        victim_identity = (victim.stat().st_dev, victim.stat().st_ino)
        real_write = GUARD._write_private_record_at

        def mutate_after_transaction(parent_fd, name, value, label):
            result = real_write(parent_fd, name, value, label)
            if label == "provenance candidate quarantine transaction":
                self.synthetic_xattrs[identity] = {"com.apple.provenance": second}
            return result

        parent_fd = GUARD._open_absolute_directory_nofollow(
            parent, "quarantine provenance parent")
        try:
            with mock.patch.object(
                     GUARD, "_write_private_record_at",
                     side_effect=mutate_after_transaction,
                 ), self.assertRaisesRegex(GUARD.GuardError, "before rename"):
                GUARD._quarantine_owned_at(
                    parent_fd, parent, victim.name, victim_identity,
                    "provenance candidate")
        finally:
            os.close(parent_fd)
        self.assertEqual(victim.read_bytes(), payload)
        self.assertFalse(any(
            parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone")))

    def test_v1_transaction_and_completion_records_are_always_poisoned(self) -> None:
        cases = (
            ("publication-txn", GUARD.PUBLICATION_PREFIX, ".txn.json",
             "openxray.private-rename-publication.v1"),
            ("publication-complete", GUARD.PUBLICATION_PREFIX, ".complete.json",
             "openxray.private-rename-publication-completion.v1"),
            ("quarantine-txn", GUARD.QUARANTINE_PREFIX, ".txn.json",
             "openxray.private-quarantine-transaction.v1"),
            ("quarantine-complete", GUARD.QUARANTINE_PREFIX, ".complete.json",
             "openxray.private-quarantine-completion.v1"),
        )
        for label, prefix, suffix, legacy_schema in cases:
            with self.subTest(label=label):
                parent = self.root / f"legacy-{label}"
                parent.mkdir(mode=0o700)
                parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
                self.synthetic_xattrs[parent_identity] = {}
                if label.startswith("publication"):
                    pending, final = parent / "pending", parent / "report.txt"
                    GUARD.prepare_report(pending, b"result=PASS\n")
                    GUARD.publish_report(pending, final)
                else:
                    victim = parent / "owned"
                    victim.write_bytes(b"result=PASS\n")
                    victim.chmod(0o600)
                    parent_fd = GUARD._open_absolute_directory_nofollow(
                        parent, "legacy quarantine parent")
                    try:
                        GUARD._quarantine_owned_at(
                            parent_fd, parent, victim.name,
                            (victim.stat().st_dev, victim.stat().st_ino),
                            "legacy quarantine candidate")
                    finally:
                        os.close(parent_fd)
                record_path = next(parent.glob(f"{prefix}*{suffix}"))
                record = json.loads(record_path.read_text())
                record["schema"] = legacy_schema
                record_path.write_bytes(GUARD._canonical_json(record))
                record_path.chmod(0o600)
                parent_fd = GUARD._open_absolute_directory_nofollow(
                    parent, "legacy transaction parent")
                try:
                    with self.assertRaisesRegex(
                            GUARD.GuardError, "invalid closed schema|not bound"):
                        GUARD._validate_transaction_parent_fd(
                            parent_fd, parent, "legacy transaction parent")
                finally:
                    os.close(parent_fd)

    def test_completion_parent_provenance_add_remove_and_change_are_poisoned(self) -> None:
        first = base64.b64encode(b"completion-parent-first").decode("ascii")
        second = base64.b64encode(b"completion-parent-second").decode("ascii")
        mutations = {
            "add": ({}, {"com.apple.provenance": first}),
            "remove": ({"com.apple.provenance": first}, {}),
            "change": ({"com.apple.provenance": first},
                       {"com.apple.provenance": second}),
        }
        for mutation, (current, forged) in mutations.items():
            with self.subTest(mutation=mutation):
                parent = self.root / f"completion-parent-{mutation}"
                parent.mkdir(mode=0o700)
                identity = (parent.stat().st_dev, parent.stat().st_ino)
                self.synthetic_xattrs[identity] = dict(current)
                pending, final = parent / "pending", parent / "report.txt"
                GUARD.prepare_report(pending, b"result=PASS\n")
                GUARD.publish_report(pending, final)
                completion_path = next(parent.glob(
                    f"{GUARD.PUBLICATION_PREFIX}*.complete.json"))
                completion = json.loads(completion_path.read_text())
                completion["parent"]["xattrs"] = dict(forged)
                completion_path.write_bytes(GUARD._canonical_json(completion))
                completion_path.chmod(0o600)
                parent_fd = GUARD._open_absolute_directory_nofollow(
                    parent, "mutated completion parent")
                try:
                    with self.assertRaisesRegex(
                            GUARD.GuardError, "not bound|parent"):
                        GUARD._validate_transaction_parent_fd(
                            parent_fd, parent, "mutated completion parent")
                finally:
                    os.close(parent_fd)

    def test_darwin_private_directory_provenance_is_opaque_and_immutable(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS 27 provenance immutability contract requires Darwin")
        parent = self.root / "darwin-private-provenance"
        parent.mkdir(mode=0o700)
        descriptor = GUARD._open_absolute_directory_nofollow(
            parent, "Darwin private provenance parent")
        try:
            before = GUARD._raw_xattrs(descriptor, "Darwin private provenance before")
            self.assertEqual(set(before), {"com.apple.provenance"})
            opaque = base64.b64decode(
                before["com.apple.provenance"], validate=True)
            replacement = os.urandom(max(1, len(opaque)))
            while replacement == opaque:
                replacement = os.urandom(max(1, len(opaque)))
            libc = ctypes.CDLL(None, use_errno=True)
            fsetxattr = libc.fsetxattr
            fremovexattr = libc.fremovexattr
            fsetxattr.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
            fsetxattr.restype = ctypes.c_int
            fremovexattr.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
            fremovexattr.restype = ctypes.c_int
            name = b"com.apple.provenance"
            replacement_buffer = ctypes.create_string_buffer(replacement)
            ctypes.set_errno(0)
            write_rc = fsetxattr(
                descriptor, name, replacement_buffer, len(replacement), 0, 0)
            self.assertEqual(write_rc, 0, os.strerror(ctypes.get_errno()))
            after_write = GUARD._raw_xattrs(
                descriptor, "Darwin private provenance after write")
            ctypes.set_errno(0)
            delete_rc = fremovexattr(descriptor, name, 0)
            self.assertEqual(delete_rc, 0, os.strerror(ctypes.get_errno()))
            after_delete = GUARD._raw_xattrs(
                descriptor, "Darwin private provenance after delete")
            self.assertEqual(after_write, before)
            self.assertEqual(after_delete, before)
        finally:
            os.close(descriptor)

    def test_quarantine_rejects_hardlink_mode_flags_xattr_and_acl_before_rename(self) -> None:
        def attempt(parent: Path, victim: Path, label: str) -> None:
            identity = (victim.stat().st_dev, victim.stat().st_ino)
            parent_fd = GUARD._open_absolute_directory_nofollow(parent, f"{label} parent")
            try:
                with self.assertRaises((GUARD.GuardError, OSError)):
                    GUARD._quarantine_owned_at(parent_fd, parent, victim.name, identity, label)
            finally:
                os.close(parent_fd)
            self.assertEqual(victim.read_bytes(), b"result=PASS\n")
            self.assertFalse(any(parent.glob(f"{GUARD.QUARANTINE_PREFIX}*")))

        parent = self.root / "quarantine-hardlink"
        parent.mkdir(mode=0o700)
        victim = parent / "victim"
        victim.write_bytes(b"result=PASS\n")
        victim.chmod(0o600)
        os.link(victim, parent / "second-link")
        attempt(parent, victim, "hardlinked candidate")

        parent = self.root / "quarantine-mode"
        parent.mkdir(mode=0o700)
        victim = parent / "victim"
        victim.write_bytes(b"result=PASS\n")
        victim.chmod(0o640)
        attempt(parent, victim, "mode-mutated candidate")

        if sys.platform == "darwin" and hasattr(os, "chflags") and hasattr(stat, "UF_NODUMP"):
            parent = self.root / "quarantine-flags"
            parent.mkdir(mode=0o700)
            victim = parent / "victim"
            victim.write_bytes(b"result=PASS\n")
            victim.chmod(0o600)
            os.chflags(victim, stat.UF_NODUMP, follow_symlinks=False)
            try:
                attempt(parent, victim, "flag-mutated candidate")
            finally:
                os.chflags(victim, 0, follow_symlinks=False)

        parent = self.root / "quarantine-xattr"
        parent.mkdir(mode=0o700)
        victim = parent / "victim"
        victim.write_bytes(b"result=PASS\n")
        victim.chmod(0o600)
        self.synthetic_xattrs[(victim.stat().st_dev, victim.stat().st_ino)] = {
            "unapproved.test": base64.b64encode(b"foreign").decode("ascii")}
        attempt(parent, victim, "xattr-mutated candidate")

        parent = self.root / "quarantine-acl"
        parent.mkdir(mode=0o700)
        victim = parent / "victim"
        victim.write_bytes(b"result=PASS\n")
        victim.chmod(0o600)
        real_acl = GUARD._require_no_acl

        def reject_victim_acl(fd, label):
            if label == "ACL-mutated candidate":
                raise GUARD.GuardError("unapproved ACL on candidate")
            return real_acl(fd, label)

        with mock.patch.object(GUARD, "_require_no_acl", side_effect=reject_victim_acl):
            attempt(parent, victim, "ACL-mutated candidate")

    def test_quarantine_rejects_symlink_and_special_without_touching_foreign_data(self) -> None:
        foreign = self.root / "foreign-pass.txt"
        foreign.write_bytes(b"result=PASS\nforeign=true\n")
        foreign.chmod(0o600)
        for kind in ("symlink", "directory", "socket"):
            with self.subTest(kind=kind):
                parent = self.root / f"quarantine-{kind}"
                parent.mkdir(mode=0o700)
                victim = parent / "victim"
                if kind == "symlink":
                    victim.symlink_to(foreign)
                elif kind == "socket":
                    endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        endpoint.bind(str(victim))
                    finally:
                        endpoint.close()
                else:
                    victim.mkdir(mode=0o700)
                info = victim.lstat()
                parent_fd = GUARD._open_absolute_directory_nofollow(parent, f"{kind} parent")
                try:
                    with self.assertRaises((GUARD.GuardError, OSError)):
                        GUARD._quarantine_owned_at(
                            parent_fd, parent, victim.name,
                            (info.st_dev, info.st_ino), f"{kind} candidate")
                finally:
                    os.close(parent_fd)
                self.assertEqual(foreign.read_bytes(), b"result=PASS\nforeign=true\n")
                self.assertFalse(any(parent.glob(f"{GUARD.QUARANTINE_PREFIX}*")))

    def test_quarantine_never_truncates_foreign_tombstone_substitution(self) -> None:
        parent = self.root / "quarantine-foreign-substitution"
        parent.mkdir(mode=0o700)
        victim, foreign = parent / "owned", parent / "foreign"
        owned_payload = b"owned bytes\n"
        foreign_payload = b"result=PASS\nforeign=true\n"
        victim.write_bytes(owned_payload)
        foreign.write_bytes(foreign_payload)
        victim.chmod(0o600)
        foreign.chmod(0o600)
        identity = (victim.stat().st_dev, victim.stat().st_ino)
        displaced = parent / "displaced-owned"
        real_rename = GUARD._rename_exclusive_at

        def replace_tombstone(parent_fd, source, destination):
            real_rename(parent_fd, source, destination)
            if source == victim.name and destination.endswith(".tombstone"):
                os.rename(destination, displaced.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.rename(foreign.name, destination, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)

        parent_fd = GUARD._open_absolute_directory_nofollow(parent, "foreign substitution parent")
        try:
            with mock.patch.object(GUARD, "_rename_exclusive_at", side_effect=replace_tombstone), \
                 self.assertRaisesRegex(GUARD.GuardError, "exactly pinned source"):
                GUARD._quarantine_owned_at(
                    parent_fd, parent, victim.name, identity, "owned substitution candidate")
            with self.assertRaisesRegex(GUARD.GuardError, "poisoned|incomplete"):
                GUARD._validate_transaction_parent_fd(
                    parent_fd, parent, "foreign substitution parent")
        finally:
            os.close(parent_fd)
        tombstones = list(parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone"))
        self.assertEqual(len(tombstones), 1)
        self.assertEqual(tombstones[0].read_bytes(), foreign_payload)
        self.assertEqual(displaced.read_bytes(), owned_payload)

    def test_quarantine_detects_metadata_substitution_after_pin_before_truncate(self) -> None:
        mutations = ["hardlink", "uid", "mode", "xattr", "acl"]
        if sys.platform == "darwin" and hasattr(os, "chflags") and hasattr(stat, "UF_NODUMP"):
            mutations.append("flags")
        real_rename = GUARD._rename_exclusive_at
        real_acl = GUARD._require_no_acl
        real_file_metadata = GUARD._file_metadata
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                parent = self.root / f"post-pin-{mutation}"
                parent.mkdir(mode=0o700)
                victim = parent / "owned"
                payload = b"result=PASS\nowned=true\n"
                victim.write_bytes(payload)
                victim.chmod(0o600)
                identity = (victim.stat().st_dev, victim.stat().st_ino)

                def mutate_then_rename(parent_fd, source, destination):
                    if source == victim.name and destination.endswith(".tombstone"):
                        if mutation == "hardlink":
                            os.link(victim, parent / "extra-link")
                        elif mutation == "mode":
                            victim.chmod(0o640)
                        elif mutation == "flags":
                            os.chflags(victim, stat.UF_NODUMP, follow_symlinks=False)
                        elif mutation == "xattr":
                            self.synthetic_xattrs[identity] = {
                                "unapproved.test": base64.b64encode(b"race").decode("ascii")}
                    return real_rename(parent_fd, source, destination)

                def reject_late_acl(fd, label):
                    if mutation == "acl" and "tombstone" in label:
                        raise GUARD.GuardError("unapproved ACL on raced tombstone")
                    return real_acl(fd, label)

                def substitute_uid(fd, label, *, digest):
                    metadata = real_file_metadata(fd, label, digest=digest)
                    if mutation == "uid" and "tombstone" in label:
                        return {**metadata, "uid": metadata["uid"] + 1}
                    return metadata

                parent_fd = GUARD._open_absolute_directory_nofollow(
                    parent, f"post-pin {mutation} parent")
                try:
                    with mock.patch.object(
                            GUARD, "_rename_exclusive_at", side_effect=mutate_then_rename), \
                         mock.patch.object(
                             GUARD, "_require_no_acl", side_effect=reject_late_acl), \
                         mock.patch.object(
                             GUARD, "_file_metadata", side_effect=substitute_uid), \
                         self.assertRaises(GUARD.GuardError):
                        GUARD._quarantine_owned_at(
                            parent_fd, parent, victim.name, identity,
                            f"post-pin {mutation} candidate")
                    with self.assertRaisesRegex(GUARD.GuardError, "poisoned|incomplete"):
                        GUARD._validate_transaction_parent_fd(
                            parent_fd, parent, f"post-pin {mutation} parent")
                finally:
                    os.close(parent_fd)
                tombstones = list(parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone"))
                self.assertEqual(len(tombstones), 1)
                self.assertEqual(tombstones[0].read_bytes(), payload)
                if mutation == "flags":
                    os.chflags(tombstones[0], 0, follow_symlinks=False)

    def test_quarantine_rename_open_truncate_and_fsync_boundaries_fail_closed(self) -> None:
        real_rename = GUARD._rename_exclusive_at
        real_open = GUARD.os.open
        real_fsync = GUARD.os.fsync
        cases = [("rename-KeyboardInterrupt", KeyboardInterrupt()),
                 ("rename-SystemExit", SystemExit(9)),
                 ("tombstone-open", OSError(errno.EIO, "open")),
                 ("truncate", OSError(errno.EIO, "truncate"))]
        cases.extend((f"fsync-{index}", OSError(errno.EIO, f"fsync {index}"))
                     for index in range(1, 8))
        for boundary, exception in cases:
            with self.subTest(boundary=boundary):
                parent = self.root / boundary
                parent.mkdir(mode=0o700)
                victim = parent / "owned"
                victim.write_bytes(b"result=PASS\nowned=true\n")
                victim.chmod(0o600)
                identity = (victim.stat().st_dev, victim.stat().st_ino)
                patches = []
                if boundary.startswith("rename-"):
                    patches.append(mock.patch.object(
                        GUARD, "_rename_exclusive_at", side_effect=exception))
                elif boundary == "tombstone-open":
                    def fail_tombstone_open(name, flags, *args, **kwargs):
                        if str(name).endswith(".tombstone") and flags & os.O_RDWR:
                            raise exception
                        return real_open(name, flags, *args, **kwargs)
                    patches.append(mock.patch.object(GUARD.os, "open", side_effect=fail_tombstone_open))
                elif boundary == "truncate":
                    patches.append(mock.patch.object(GUARD.os, "ftruncate", side_effect=exception))
                else:
                    fail_at = int(boundary.split("-", 1)[1])
                    calls = 0
                    def fail_fsync(fd):
                        nonlocal calls
                        calls += 1
                        if calls == fail_at:
                            raise exception
                        return real_fsync(fd)
                    patches.append(mock.patch.object(GUARD.os, "fsync", side_effect=fail_fsync))
                parent_fd = GUARD._open_absolute_directory_nofollow(parent, f"{boundary} parent")
                try:
                    with patches[0], self.assertRaises(type(exception)):
                        GUARD._quarantine_owned_at(
                            parent_fd, parent, victim.name, identity, f"{boundary} candidate")
                    with self.assertRaisesRegex(GUARD.GuardError, "poisoned|incomplete|unknown"):
                        GUARD._validate_transaction_parent_fd(
                            parent_fd, parent, f"{boundary} parent")
                finally:
                    os.close(parent_fd)
                for tombstone in parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone"):
                    self.assertNotEqual(tombstone.read_bytes(), b"foreign data")

    def test_explicit_crash_record_poisons_parent_and_preserves_foreign_pass(self) -> None:
        parent = self.root / "crash-record"
        parent.mkdir(mode=0o700)
        foreign = parent / "report.txt"
        foreign.write_bytes(b"result=PASS\nforeign=true\n")
        foreign.chmod(0o600)
        token = "f" * 32
        record = parent / f"{GUARD.QUARANTINE_PREFIX}{token}.txn.json"
        record.write_bytes(b"{}\n")
        record.chmod(0o600)
        parent_fd = GUARD._open_absolute_directory_nofollow(parent, "crash record parent")
        try:
            with self.assertRaisesRegex(GUARD.GuardError, "poisoned|incomplete"):
                GUARD._validate_transaction_parent_fd(
                    parent_fd, parent, "crash record parent")
        finally:
            os.close(parent_fd)
        self.assertEqual(foreign.read_bytes(), b"result=PASS\nforeign=true\n")
        pending, destination = parent / "new.pending", parent / "new-report.txt"
        GUARD.prepare_report(pending, b"result=PASS\nowned=true\n")
        with self.assertRaisesRegex(GUARD.GuardError, "poisoned|incomplete"):
            GUARD.publish_report(pending, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(foreign.read_bytes(), b"result=PASS\nforeign=true\n")

    def test_pinned_ledger_replacement_around_publication_is_rejected(self) -> None:
        self.stage()
        self.finalize()
        report = self.report()
        original_verify = self.importer().verify_prepared
        def replace_then_verify(path):
            replacement = self.ledger.with_suffix(".replacement")
            replacement.write_bytes(self.ledger.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(self.ledger)
            return original_verify(path)
        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer(replace_then_verify)):
            with self.assertRaisesRegex(
                    GUARD.GuardError, "changed around|private metadata"):
                GUARD.validate_clone_publication(
                    self.prepared, self.ledger, self.staged, report,
                )

    def test_generic_publish_rolls_back_report_on_clone_mutation(self) -> None:
        self.stage()
        self.finalize()
        pending_report = self.report()
        final_report = self.artifacts / "report.txt"
        real_rename = GUARD._rename_exclusive_at
        def mutate_after_rename(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == pending_report.name and destination == final_report.name:
                self.staged.write_bytes(self.staged.read_bytes() + b"mutation\n")
            return result
        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             mock.patch.object(GUARD, "_rename_exclusive_at",
                               side_effect=mutate_after_rename):
            with self.assertRaises(GUARD.GuardError):
                GUARD.publish_report(
                    pending_report, final_report, None, self.prepared,
                    self.ledger, self.staged,
                )
        self.assertFalse(final_report.exists())
        self.assertFalse(pending_report.exists())
        self.assertTrue(any(
            path.read_bytes() == b"" for path in
            self.artifacts.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone")))

    def test_generic_publish_rolls_back_report_on_ledger_replacement(self) -> None:
        self.stage()
        self.finalize()
        pending_report = self.report()
        final_report = self.artifacts / "report-ledger.txt"
        real_rename = GUARD._rename_exclusive_at
        def replace_after_rename(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == pending_report.name and destination == final_report.name:
                replacement = self.ledger.with_suffix(".replacement")
                replacement.write_bytes(self.ledger.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(self.ledger)
            return result
        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             mock.patch.object(GUARD, "_rename_exclusive_at",
                               side_effect=replace_after_rename):
            with self.assertRaises(GUARD.GuardError):
                GUARD.publish_report(
                    pending_report, final_report, None, self.prepared,
                    self.ledger, self.staged,
                )
        self.assertFalse(final_report.exists())
        self.assertFalse(pending_report.exists())

    def test_generic_publish_revalidates_uf_tracked_fields_after_completion(self) -> None:
        self.stage()
        self.finalize()
        pending_report = self.report()
        final_report = self.artifacts / "report-after-completion.txt"
        real_completion = GUARD._publish_completion_record_at

        def mutate_policy_digest_after_completion(parent_fd, stem, value, label):
            result = real_completion(parent_fd, stem, value, label)
            if label == "Simulator report publication completion":
                ledger = json.loads(self.ledger.read_text())
                ledger["post_runtime"]["flags_digest"] = "f" * 64
                self.ledger.write_bytes(GUARD._canonical_json(ledger))
                self.ledger.chmod(0o600)
            return result

        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             mock.patch.object(GUARD, "_publish_completion_record_at",
                               side_effect=mutate_policy_digest_after_completion), \
             self.assertRaises(GUARD.GuardError):
            GUARD.publish_report(
                pending_report, final_report, None, self.prepared,
                self.ledger, self.staged)
        self.assertFalse(final_report.exists())
        self.assertFalse(pending_report.exists())
        self.assertTrue(any(
            path.read_bytes() == b"" for path in
            self.artifacts.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone")))

    def test_generic_publish_baseexceptions_leave_only_poisoned_transactions(self) -> None:
        real_rename = GUARD._rename_exclusive_at
        for exception, after_rename in ((KeyboardInterrupt(), False), (SystemExit(7), True)):
            with self.subTest(exception=type(exception).__name__, after_rename=after_rename):
                parent = self.root / f"publish-{type(exception).__name__}"
                parent.mkdir(mode=0o700)
                pending, final = parent / "report.pending", parent / "report.txt"
                GUARD.prepare_report(pending, b"result=PASS\n")

                def interrupt(parent_fd, source, destination):
                    if source == pending.name and destination == final.name:
                        if after_rename:
                            real_rename(parent_fd, source, destination)
                        raise exception
                    return real_rename(parent_fd, source, destination)

                descriptors_before = len(os.listdir("/dev/fd"))
                with mock.patch.object(GUARD, "_rename_exclusive_at", side_effect=interrupt), \
                     self.assertRaises(type(exception)):
                    GUARD.publish_report(pending, final)
                self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)
                self.assertFalse(final.exists())
                self.assertEqual(pending.exists(), not after_rename)
                if after_rename:
                    self.assertTrue(any(
                        path.read_bytes() == b"" for path in
                        parent.glob(f"{GUARD.QUARANTINE_PREFIX}*.tombstone")))
                parent_fd = GUARD._open_absolute_directory_nofollow(parent, "poisoned publication")
                try:
                    with self.assertRaisesRegex(GUARD.GuardError, "poisoned|incomplete"):
                        GUARD._validate_transaction_parent_fd(
                            parent_fd, parent, "poisoned publication")
                finally:
                    os.close(parent_fd)

    def test_generic_publish_parent_swap_keeps_pass_non_authoritative(self) -> None:
        publication_parent = self.root / "generic-publication-parent"
        publication_parent.mkdir(mode=0o700)
        pending = publication_parent / "report.pending"
        final = publication_parent / "report.txt"
        renamed_parent = self.root / "generic-publication-renamed"
        GUARD.prepare_report(pending, b"result=PASS\n")
        real_rename = GUARD._rename_exclusive_at

        def rename_then_swap(parent_fd, source, destination):
            result = real_rename(parent_fd, source, destination)
            if source == pending.name and destination == final.name:
                publication_parent.rename(renamed_parent)
                publication_parent.mkdir(mode=0o700)
            return result

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(GUARD, "_rename_exclusive_at", side_effect=rename_then_swap), \
             self.assertRaisesRegex(GUARD.GuardError, "pathname changed"):
            GUARD.publish_report(pending, final)
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)
        self.assertEqual((renamed_parent / final.name).read_bytes(), b"result=PASS\n")
        self.assertFalse(final.exists())
        parent_fd = GUARD._open_absolute_directory_nofollow(renamed_parent, "renamed publication")
        try:
            with self.assertRaisesRegex(GUARD.GuardError, "poisoned|incomplete"):
                GUARD._validate_transaction_parent_fd(
                    parent_fd, renamed_parent, "renamed publication")
        finally:
            os.close(parent_fd)

    def test_reserved_clone_report_fields_are_unique_exact_and_generic_rejects_them(self) -> None:
        self.stage()
        self.finalize()
        baseline_report = self.report()
        baseline = baseline_report.read_bytes()
        reserved_lines = [line for line in baseline.decode("utf-8").splitlines()
                          if line.split("=", 1)[0] in {
                              "stage_mode", "clone_ledger", "clone_ledger_dev",
                              "clone_ledger_ino", "clone_ledger_bytes",
                              "clone_ledger_sha256", "staged_manifest_sha256",
                              "clone_destination_post_delete",
                          }]
        self.assertEqual(len(reserved_lines), 8)
        for index, duplicate in enumerate(reserved_lines):
            with self.subTest(field=duplicate.split("=", 1)[0]):
                candidate = self.artifacts / f"reserved-{index}.pending"
                candidate.write_bytes(baseline + (duplicate + "\n").encode("utf-8"))
                candidate.chmod(0o600)
                with mock.patch.object(GUARD, "_import_retail_import",
                                       return_value=self.importer()), \
                     self.assertRaisesRegex(GUARD.GuardError, "must occur exactly once"):
                    GUARD.validate_clone_publication(
                        self.prepared, self.ledger, self.staged, candidate,
                    )
        for index, exact in enumerate(reserved_lines):
            key = exact.split("=", 1)[0]
            with self.subTest(contradictory=key):
                candidate = self.artifacts / f"reserved-contradictory-{index}.pending"
                candidate.write_bytes(
                    baseline.replace((exact + "\n").encode("utf-8"),
                                     f"{key}=contradictory\n".encode("utf-8"), 1))
                candidate.chmod(0o600)
                with mock.patch.object(GUARD, "_import_retail_import",
                                       return_value=self.importer()), \
                     self.assertRaisesRegex(GUARD.GuardError, "must occur exactly once"):
                    GUARD.validate_clone_publication(
                        self.prepared, self.ledger, self.staged, candidate,
                    )
        missing = self.artifacts / "reserved-missing.pending"
        missing.write_bytes(
            baseline.replace((reserved_lines[0] + "\n").encode(), b"", 1))
        missing.chmod(0o600)
        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             self.assertRaisesRegex(GUARD.GuardError, "must occur exactly once"):
            GUARD.validate_clone_publication(
                self.prepared, self.ledger, self.staged, missing)
        for unknown in ("clone_unbound=1", "quickload_unbound=1"):
            with self.subTest(unknown=unknown):
                candidate = self.artifacts / f"unknown-{unknown.split('=', 1)[0]}.pending"
                candidate.write_bytes(baseline + f"{unknown}\n".encode())
                candidate.chmod(0o600)
                with mock.patch.object(GUARD, "_import_retail_import",
                                       return_value=self.importer()), \
                     self.assertRaisesRegex(GUARD.GuardError,
                                            "unknown reserved field"):
                    GUARD.validate_clone_publication(
                        self.prepared, self.ledger, self.staged, candidate)
        contradictory = self.artifacts / "reserved-generic.pending"
        contradictory.write_bytes(baseline + b"stage_mode=legacy-copy\n")
        contradictory.chmod(0o600)
        final = self.artifacts / "reserved-generic.txt"
        with mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             self.assertRaisesRegex(GUARD.GuardError, "must occur exactly once"):
            GUARD.publish_report(
                contradictory, final, None, self.prepared, self.ledger, self.staged,
            )
        self.assertFalse(final.exists())

    def test_sequential_clone_artifact_open_failures_close_partial_descriptors(self) -> None:
        self.stage()
        ledger = self.read_ledger(self.pending, finalized=False)
        real_metadata = GUARD._directory_metadata

        def fail_destination_parent(descriptor, label, **kwargs):
            if label == "post-runtime destination parent":
                raise KeyboardInterrupt
            return real_metadata(descriptor, label, **kwargs)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(GUARD, "_directory_metadata",
                               side_effect=fail_destination_parent), \
             self.assertRaises(KeyboardInterrupt):
            GUARD._validate_destination_post_runtime(ledger, None)
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(GUARD, "_read_pinned_regular", side_effect=KeyboardInterrupt), \
             self.assertRaises(KeyboardInterrupt):
            self.finalize()
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

        self.finalize()
        report = self.report()
        real_read = GUARD._read_pinned_regular
        calls = 0

        def fail_third_sequential_open(*arguments, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return real_read(*arguments, **kwargs)

        descriptors_before = len(os.listdir("/dev/fd"))
        with mock.patch.object(
                 GUARD, "_read_pinned_regular", side_effect=fail_third_sequential_open,
             ), self.assertRaises(KeyboardInterrupt):
            GUARD.validate_clone_publication(
                self.prepared, self.ledger, self.staged, report,
            )
        self.assertEqual(len(os.listdir("/dev/fd")), descriptors_before)

    def _diagnostics_root(self) -> Path:
        return self.artifacts / "clone-save-metadata-diagnostics"

    def _reset_diagnostics_root(self) -> Path:
        root = self._diagnostics_root()
        if root.exists():
            for child in root.iterdir():
                child.unlink()
            root.rmdir()
        return root

    def _snapshot(self, label: str) -> dict[str, object]:
        root = self._diagnostics_root()
        GUARD.clone_stat_snapshot(
            self.prepared, self.destination, self.pending,
            self.artifacts, root, label,
        )
        return json.loads((root / f"{label}.json").read_text())

    def _write_quickload_transients(self, quickload: Path) -> None:
        quickload.mkdir(mode=0o700, exist_ok=True)
        quickload.chmod(0o700)
        def state(name: str, inode: int, flags: int = 0) -> dict[str, object]:
            return {"name": name, "device": 1, "inode": inode,
                    "uid": os.geteuid(), "mode": 0o600, "nlink": 1,
                    "flags": flags, "bytes": 8, "mtime_ns": 1,
                    "sha256": "a" * 64}
        def transition(name: str, inode: int) -> dict[str, object]:
            value = state(name, inode)
            return {"initial": value, "final": dict(value), "transition": "0->0"}
        def event(request: str, key: str, watermark: int, press: int,
                  release: int, success: int, terminal: int | None = None) -> dict[str, object]:
            scancode = 62 if key == "f5" else 66
            value = {"request_id": request, "watermark_line": watermark,
                     "ack": f"{request} accepted",
                     "press": (f"* iOS diag: autoinput request {request} press/hold "
                               f"'{key}' (scancode {scancode}) for 100 ms"),
                     "press_line": press,
                     "release": (f"* iOS diag: autoinput request {request} released "
                                 f"scancode {scancode}"),
                     "release_line": release, "success_line": success}
            if terminal is not None:
                value["terminal_line"] = terminal
            return value
        qsave = "11111111-1111-4111-8111-111111111111"
        qload = "22222222-2222-4222-8222-222222222222"
        def captured(stem: str, sequence: int, frame: int) -> dict[str, object]:
            return {"token": f"{'a' * 32}:{sequence}", "frame": frame,
                    "metadata": str(quickload / f"{stem}.json"),
                    "ppm": str(quickload / f"{stem}.ppm"),
                    "metadata_sha256": str(sequence) * 64,
                    "ppm_sha256": str(sequence + 3) * 64}
        manifest = {
            "schema": "openxray-ios-simulator-quickload-evidence-v2",
            "artifact": "post-stop-revalidated-evidence",
            "post_stop_revalidated": True, "runtime": "27.0",
            "simulator_uuid": "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE",
            "pid": 123,
            "baseline_epoch": {"epoch": 1, "frames": [10], "level": "zaton",
                               "markers": 1, "trigger": "level_load",
                               "classification": "exact", "method": "exact"},
            "quickload_epoch": {"epoch": 2, "frames": [60], "level": "zaton",
                                "markers": 1, "trigger": "quick_load",
                                "classification": "exact", "method": "exact"},
            "qsave": qsave, "qload": qload,
            "f5_events": event(qsave, "f5", 1, 3, 4, 2),
            "f9_events": event(qload, "f9", 5, 7, 8, 6, 9),
            "original_save": transition("slot.scop", 1),
            "quicksave": transition("player - quicksave.scop", 2),
            "quicksave_copy": state("player - quicksave.scop", 3),
            "captures": {"b0": captured("baseline-b0", 1, 20),
                         "b1": captured("settled-b1", 2, 40),
                         "c": captured("post-quickload-c", 3, 80)},
            "dds_status": "expected_absent_gl_sm_for_gamesave_noop",
            "scope": ("iOS-27.0-Simulator-Apple-Software-Renderer-only; normal F5/F9 "
                      "path; not iPhone/pixel-quality/performance/other-content proof"),
        }
        payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                   + "\n").encode()
        pending = quickload / "manifest.pending.json"
        fields = quickload / "report-fields.txt"
        if not pending.exists():
            pending.write_bytes(payload)
            pending.chmod(0o600)
        if not fields.exists():
            fields.write_text(
                f"quickload_manifest_sha256={hashlib.sha256(payload).hexdigest()}\n"
                f"quickload_manifest_bytes={len(payload)}\n", encoding="ascii")
            fields.chmod(0o600)

    def _snapshot_flags(self, label: str, flags: dict[str, int]) -> dict[str, object]:
        if label in {"D2", "D3", "D4"}:
            quickload = self.artifacts / "quickload-evidence"
            quickload.mkdir(mode=0o700, exist_ok=True)
            quickload.chmod(0o700)
            if label == "D4":
                self._write_quickload_transients(quickload)
        real_stat = GUARD._descriptor_relative_stat

        def with_flags(root_fd, relative, stat_label):
            value = real_stat(root_fd, relative, stat_label)
            if set(value) != {"error"}:
                value = dict(value)
                value["flags"] = f"0x{flags.get(relative.as_posix(), 0):08x}"
            return value

        with mock.patch.object(
                GUARD, "_descriptor_relative_stat", side_effect=with_flags):
            return self._snapshot(label)

    def _new_clone_case(self, name: str, *, saves: bool = True) -> None:
        self.artifacts = self.root / f"artifacts-{name}"
        self.artifacts.mkdir(mode=0o700)
        self.destination = self.container / f"Documents-{name}"
        self.staged = self.artifacts / "staged-files.tsv"
        self.pending = self.artifacts / ".clone-ledger.pending.json"
        self.ledger = self.artifacts / "clone-ledger.json"
        self.stage(saves=saves)

    def _finalize_with_flags(self, flags: dict[str, int], *, saves: bool = False,
                             mutable: str | None = None,
                             diagnostics: Path | None = None) -> None:
        real_metadata = GUARD._destination_file_metadata

        def with_flags(fd, label, *, digest):
            metadata = real_metadata(fd, label, digest=digest)
            for path, value in flags.items():
                if label.endswith(f"destination file {path}"):
                    metadata = dict(metadata)
                    metadata["flags"] = value
                    break
            return metadata

        with mock.patch.object(
                GUARD, "_destination_file_metadata", side_effect=with_flags):
            self.finalize(saves=saves, mutable=mutable, diagnostics=diagnostics)

    def test_clone_stat_snapshot_is_private_stat_only_and_bound_to_pending_ledger(self) -> None:
        self.stage(saves=True)
        root = self._diagnostics_root()
        real_open, leaf_names = os.open, {Path(path).name for path in self.files}
        opened_leaf_names: list[str] = []

        def observe_open(path, *arguments, **kwargs):
            if isinstance(path, str) and path in leaf_names and "dir_fd" in kwargs:
                opened_leaf_names.append(path)
            return real_open(path, *arguments, **kwargs)

        with mock.patch.object(GUARD.os, "open", side_effect=observe_open):
            snapshot = self._snapshot("D0")
        output = root / "D0.json"
        self.assertEqual(snapshot["schema"], GUARD.CLONE_DIAGNOSTIC_SCHEMA)
        self.assertTrue(snapshot["diagnostic_only"])
        self.assertEqual(snapshot["result"], "diagnostic")
        self.assertNotIn("PASS", output.read_text())
        self.assertEqual(snapshot["runtime"], "27.0")
        self.assertEqual(snapshot["policy"], GUARD.UF_TRACKED_POLICY)
        self.assertEqual(snapshot["ledger"]["sha256"], self.digest(self.pending.read_bytes()))
        self.assertIsNone(snapshot["previous"])
        self.assertEqual(snapshot["file_count"], len(self.read_ledger(self.pending, finalized=False)["files"]))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(output.stat().st_nlink, 1)
        self.assertEqual(opened_leaf_names, [])
        for record in snapshot["files"]:
            self.assertFalse(record["first_diff"])
            self.assertFalse(record["second_diff"])
            self.assertFalse(record["race"])
            self.assertEqual(record["first"]["flags"], "0x00000000")
            self.assertEqual(record["first"], record["second"])

    def test_clone_stat_snapshot_publishes_every_metadata_difference_before_failing(self) -> None:
        self.stage(saves=True)
        victim = "resources/resources.db0"
        expected = self.read_ledger(self.pending, finalized=False)
        base = next(record for record in expected["files"] if record["path"] == victim)["destination"]
        mutations = {
            "type": "directory",
            "ino": base["ino"] + 1,
            "uid": base["uid"] + 1,
            "mode": 0o640,
            "nlink": 2,
            "size": base["size"] + 1,
            "mtime_ns": base["mtime_ns"] + 1,
        }
        real_stat = GUARD._descriptor_relative_stat
        for field, value in mutations.items():
            with self.subTest(field=field):
                root = self._reset_diagnostics_root()
                self._snapshot("D0")

                def mutate(root_fd, relative, label, *, field=field, value=value):
                    result = real_stat(root_fd, relative, label)
                    if relative.as_posix() == victim:
                        result = dict(result)
                        result[field] = value
                    return result

                with mock.patch.object(GUARD, "_descriptor_relative_stat", side_effect=mutate), \
                     self.assertRaisesRegex(GUARD.GuardError, "ledger difference"):
                    self._snapshot("D1")
                snapshot = json.loads((root / "D1.json").read_text())
                record = next(item for item in snapshot["files"] if item["path"] == victim)
                self.assertEqual(record["first_diff"][field]["actual"], value)
                self.assertEqual(record["second_diff"][field]["actual"], value)
                self.assertFalse(record["race"])
                self.assertTrue(snapshot["diagnostic_only"])
                self.assertNotIn("PASS", (root / "D1.json").read_text())

    def test_clone_stat_snapshot_detects_two_stat_race_and_does_not_publish_pass(self) -> None:
        self.stage(saves=True)
        victim, calls = "resources/resources.db0", 0
        root = self._diagnostics_root()
        real_stat = GUARD._descriptor_relative_stat
        self._snapshot("D0")

        def mutate_second(root_fd, relative, label):
            nonlocal calls
            result = real_stat(root_fd, relative, label)
            if relative.as_posix() == victim:
                calls += 1
                if calls == 2:
                    result = dict(result)
                    result["mtime_ns"] += 1
            return result

        with mock.patch.object(GUARD, "_descriptor_relative_stat", side_effect=mutate_second), \
             self.assertRaisesRegex(GUARD.GuardError, "stat race"):
            self._snapshot("D1")
        snapshot = json.loads((root / "D1.json").read_text())
        record = next(item for item in snapshot["files"] if item["path"] == victim)
        self.assertTrue(record["race"])
        self.assertEqual(record["second_diff"]["mtime_ns"]["actual"],
                         record["first"]["mtime_ns"] + 1)
        self.assertFalse((self.artifacts / "report.txt").exists())
        self.assertFalse(self.ledger.exists())

    def test_clone_stat_snapshot_rejects_untrusted_diagnostics_root(self) -> None:
        self.stage(saves=True)
        external = self.root / "external-diagnostics"
        with self.assertRaisesRegex(GUARD.GuardError, "exact trusted work-root child"):
            GUARD.clone_stat_snapshot(
                self.prepared, self.destination, self.pending,
                self.artifacts, external, "D0",
            )
        self.assertFalse(external.exists())

    def test_clone_stat_snapshot_rolls_back_own_file_if_pending_ledger_is_substituted(self) -> None:
        self.stage(saves=True)
        root = self._diagnostics_root()
        real_write = GUARD._write_clone_stat_snapshot

        def publish_then_substitute(*arguments, **kwargs):
            result = real_write(*arguments, **kwargs)
            data = self.pending.read_bytes()
            old = self.pending.with_name("old-pending-ledger")
            self.pending.replace(old)
            self.pending.write_bytes(data)
            self.pending.chmod(0o600)
            return result

        with mock.patch.object(
                 GUARD, "_write_clone_stat_snapshot",
                 side_effect=publish_then_substitute,
             ), self.assertRaisesRegex(GUARD.GuardError, "pending clone ledger"):
            self._snapshot("D0")
        self.assertTrue(root.is_dir())
        self.assertFalse((root / "D0.json").exists())

    def test_clone_stat_snapshot_final_adjacent_snapshot_revalidation_is_load_bearing(self) -> None:
        self.stage(saves=True)
        root = self._diagnostics_root()
        real_revalidate = GUARD._revalidate_pinned
        injected = False

        def mutate_between_final_pair(state, label):
            nonlocal injected
            result = real_revalidate(state, label)
            if label == "pending clone ledger final diagnostic boundary":
                diagnostic = root / "D0.json"
                diagnostic.write_bytes(diagnostic.read_bytes() + b" ")
                diagnostic.chmod(0o600)
                injected = True
            return result

        with mock.patch.object(
                 GUARD, "_revalidate_pinned", side_effect=mutate_between_final_pair,
             ), self.assertRaisesRegex(GUARD.GuardError, "final boundary"):
            self._snapshot("D0")
        self.assertTrue(injected)
        self.assertFalse((root / "D0.json").exists())
        self.assertFalse((self.artifacts / "report.txt").exists())
        self.assertFalse(self.ledger.exists())
        self.assertFalse(any(
            b"result=PASS" in path.read_bytes()
            for path in root.iterdir() if path.is_file()))

    def test_clone_stat_snapshot_never_unlinks_foreign_diagnostic_replacement(self) -> None:
        self.stage(saves=True)
        root = self._diagnostics_root()
        foreign = self.artifacts / "foreign-diagnostic"
        foreign.write_bytes(b'{"foreign":true}\n')
        foreign.chmod(0o600)
        real_read = GUARD._read_pinned_regular_at
        replaced = False

        def replace_before_binding(parent_fd, path, label, *, private):
            nonlocal replaced
            if not replaced and "published clone metadata diagnostic" in label:
                replaced = True
                foreign.replace(path)
            return real_read(parent_fd, path, label, private=private)

        with mock.patch.object(
                 GUARD, "_read_pinned_regular_at", side_effect=replace_before_binding,
             ), self.assertRaisesRegex(GUARD.GuardError, "rollback was incomplete"):
            self._snapshot("D0")
        self.assertTrue(replaced)
        self.assertEqual((root / "D0.json").read_bytes(), b'{"foreign":true}\n')

    def test_uf_tracked_mixed_d0_d4_chain_and_final_report_binding(self) -> None:
        self.stage(saves=True)
        paths = [record["path"] for record in
                 self.read_ledger(self.pending, finalized=False)["files"]]
        d0 = {path: 0 for path in paths}
        d1 = {path: (GUARD.UF_TRACKED if index % 3 == 0 else 0)
              for index, path in enumerate(paths)}
        d2 = {path: (GUARD.UF_TRACKED if index % 3 in {0, 1} else 0)
              for index, path in enumerate(paths)}
        d3 = dict(d2)
        d4 = {path: GUARD.UF_TRACKED for path in paths[:-1]}
        d4[paths[-1]] = 0
        final = dict(d4)
        final[paths[-1]] = GUARD.UF_TRACKED
        snapshots = [self._snapshot_flags(label, flags) for label, flags in
                     (("D0", d0), ("D1", d1), ("D2", d2),
                      ("D3", d3), ("D4", d4))]
        self.assertEqual(
            [snapshot["sequence"] for snapshot in snapshots], list(range(5)))
        self.assertEqual(snapshots[0]["previous"], None)
        self.assertTrue(all(snapshot["quickload"] is None
                            for snapshot in snapshots[:4]))
        d4_quickload = snapshots[4]["quickload"]
        self.assertEqual(
            d4_quickload["contract"],
            "openxray-ios27-quicksave-quickload-semantic-v2")
        self.assertRegex(d4_quickload["semantic_sha256"], r"^[0-9a-f]{64}$")
        for artifact in ("manifest", "report_fields"):
            path = Path(d4_quickload[artifact]["path"])
            details = path.stat()
            self.assertEqual(
                (d4_quickload[artifact]["dev"], d4_quickload[artifact]["ino"],
                 d4_quickload[artifact]["bytes"],
                 d4_quickload[artifact]["sha256"]),
                (details.st_dev, details.st_ino, details.st_size,
                 hashlib.sha256(path.read_bytes()).hexdigest()))
        for index in range(1, 5):
            self.assertEqual(
                snapshots[index]["previous"]["path"],
                str(self._diagnostics_root() / f"D{index - 1}.json"))
        self._finalize_with_flags(
            final, saves=True, diagnostics=self._diagnostics_root())
        ledger = self.read_ledger(self.ledger, finalized=True)
        post = ledger["post_runtime"]
        self.assertEqual(post["continuity_mode"], "D0-D4")
        self.assertEqual(post["runtime"], "27.0")
        self.assertEqual(post["policy"], GUARD.UF_TRACKED_POLICY)
        self.assertEqual(post["flag_counts"], {"zero": 0, "tracked": len(paths)})
        self.assertEqual(post["diagnostics"]["final_record"]["label"], "D4")
        self.assertEqual(
            {record["transition"] for record in post["files"]},
            {"UF_TRACKED->UF_TRACKED", "0->UF_TRACKED"})
        report = self.report()
        report_text = report.read_text()
        self.assertIn("clone_continuity_mode=D0-D4\n", report_text)
        self.assertIn(f"clone_uf_tracked_tracked_count={len(paths)}\n", report_text)
        self.assertIn("clone_validated_before_container_delete=true\n", report_text)
        self.validate_publication(report)

    def test_d4_quickload_binding_drift_during_final_ledger_publication_fails_closed(self) -> None:
        self.stage(saves=True)
        for label in ("D0", "D1", "D2", "D3", "D4"):
            self._snapshot_flags(label, {})
        pending_manifest = self.artifacts / "quickload-evidence/manifest.pending.json"
        original = pending_manifest.read_bytes()
        real_publish = GUARD._atomic_publish_new

        def publish_then_mutate(*arguments, **kwargs):
            identity = real_publish(*arguments, **kwargs)
            if arguments[2] == "final clone ledger":
                pending_manifest.write_bytes(original + b" ")
            return identity

        with mock.patch.object(GUARD, "_atomic_publish_new",
                               side_effect=publish_then_mutate), \
             self.assertRaises(GUARD.GuardError):
            self.finalize(saves=True, diagnostics=self._diagnostics_root())
        self.assertFalse(self.ledger.exists())
        self.assertFalse((self.artifacts / "report.txt").exists())

    def test_d4_is_revalidated_at_each_final_ledger_publication_boundary(self) -> None:
        for boundary in ("pre-rename", "post-rename", "completion"):
            with self.subTest(boundary=boundary):
                self._new_clone_case(f"d4-final-ledger-{boundary}", saves=True)
                for label in ("D0", "D1", "D2", "D3", "D4"):
                    self._snapshot_flags(label, {})
                d4 = self._diagnostics_root() / "D4.json"
                original = d4.read_bytes()
                real_publish = GUARD._atomic_publish_new

                def publish_with_boundary_mutation(*arguments, **kwargs):
                    callback = arguments[4]

                    def mutate_after_validation(phase):
                        callback(phase)
                        if phase == boundary:
                            d4.write_bytes(original + b" ")

                    return real_publish(
                        *arguments[:4], mutate_after_validation, **kwargs)

                with mock.patch.object(
                         GUARD, "_atomic_publish_new",
                         side_effect=publish_with_boundary_mutation), \
                     self.assertRaises(GUARD.GuardError):
                    self.finalize(saves=True, diagnostics=self._diagnostics_root())
                self.assertFalse(self.ledger.exists())
                self.assertFalse((self.artifacts / "report.txt").exists())

    def test_d4_quickload_baseline_rejects_inode_content_and_coherent_semantic_swap(self) -> None:
        for mutation in ("inode", "content", "semantic"):
            with self.subTest(mutation=mutation):
                self._new_clone_case(f"d4-quickload-{mutation}", saves=True)
                for label in ("D0", "D1", "D2", "D3", "D4"):
                    self._snapshot_flags(label, {})
                root = self.artifacts / "quickload-evidence"
                manifest = root / "manifest.pending.json"
                fields = root / "report-fields.txt"
                original = manifest.read_bytes()
                if mutation == "inode":
                    manifest.unlink()
                    manifest.write_bytes(original)
                    manifest.chmod(0o600)
                elif mutation == "content":
                    manifest.write_bytes(original + b" ")
                else:
                    value = json.loads(original)
                    value["simulator_uuid"] = "CCCCCCCC-DDDD-4EEE-8FFF-AAAAAAAAAAAA"
                    replacement = (json.dumps(
                        value, sort_keys=True, separators=(",", ":")) + "\n").encode()
                    manifest.unlink()
                    manifest.write_bytes(replacement)
                    manifest.chmod(0o600)
                    fields.unlink()
                    fields.write_text(
                        f"quickload_manifest_sha256={hashlib.sha256(replacement).hexdigest()}\n"
                        f"quickload_manifest_bytes={len(replacement)}\n",
                        encoding="ascii")
                    fields.chmod(0o600)
                with self.assertRaises(GUARD.GuardError):
                    self.finalize(saves=True, diagnostics=self._diagnostics_root())
                self.assertFalse(self.ledger.exists())
                self.assertFalse((self.artifacts / "report.txt").exists())

    def test_quickload_publish_rejects_coherent_manifest_and_report_substitution_against_ledger(self) -> None:
        self.stage(saves=True)
        quickload_root = self.artifacts / "quickload-evidence"
        self._write_quickload_transients(quickload_root)
        self.finalize(saves=True)
        report_pending = self.report()
        pending = quickload_root / "manifest.pending.json"
        manifest = quickload_root / "manifest.json"
        report = self.artifacts / "report.txt"

        value = json.loads(pending.read_text())
        value["simulator_uuid"] = "FFFFFFFF-EEEE-4DDD-8CCC-BBBBBBBBBBBB"
        substituted_manifest = (json.dumps(
            value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        substituted_fields = (
            f"quickload_manifest_sha256={hashlib.sha256(substituted_manifest).hexdigest()}\n"
            f"quickload_manifest_bytes={len(substituted_manifest)}\n"
        ).encode("ascii")
        pending.unlink()
        pending.write_bytes(substituted_manifest)
        pending.chmod(0o600)
        fields = quickload_root / "report-fields.txt"
        fields.unlink()
        fields.write_bytes(substituted_fields)
        fields.chmod(0o600)
        quickload = GUARD._import_quickload_evidence()
        retained = [line for line in report_pending.read_text().splitlines()
                    if not line.startswith("quickload_")]
        substituted_production = quickload.quickload_report_fields(
            manifest, substituted_manifest)
        substituted_report = (("\n".join(retained) + "\n") + "".join(
            f"{key}={value}\n" for key, value in substituted_production.items())
        ).encode("ascii")
        report_pending.unlink()
        report_pending.write_bytes(substituted_report)
        report_pending.chmod(0o600)

        fake_spec = types.SimpleNamespace(
            loader=types.SimpleNamespace(exec_module=lambda module: None))
        real_spec_from_file = importlib.util.spec_from_file_location
        real_module_from_spec = importlib.util.module_from_spec
        def selective_spec(name, path, *args, **kwargs):
            if name == "retail_guard_for_quickload_publish":
                return fake_spec
            return real_spec_from_file(name, path, *args, **kwargs)
        def selective_module(spec):
            return GUARD if spec is fake_spec else real_module_from_spec(spec)
        arguments = argparse.Namespace(
            pending_manifest=str(pending), manifest=str(manifest),
            report_pending=str(report_pending), report=str(report),
            clone_prepared=str(self.prepared), clone_ledger=str(self.ledger),
            staged_manifest=str(self.staged),
        )
        with mock.patch.object(
                 quickload.importlib.util, "spec_from_file_location",
                 side_effect=selective_spec), \
             mock.patch.object(quickload.importlib.util, "module_from_spec",
                               side_effect=selective_module), \
             mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             self.assertRaises(quickload.EvidenceError):
            quickload.publish(arguments)
        self.assertEqual(pending.read_bytes(), substituted_manifest)
        self.assertEqual(report_pending.read_bytes(), substituted_report)
        self.assertFalse(manifest.exists())
        self.assertFalse(report.exists())

    def test_quickload_publish_happy_and_revalidates_ledger_semantics_after_completion(self) -> None:
        def prepare(name: str):
            self._new_clone_case(name, saves=True)
            quickload_root = self.artifacts / "quickload-evidence"
            self._write_quickload_transients(quickload_root)
            self.finalize(saves=True)
            report_pending = self.report()
            return (
                GUARD._import_quickload_evidence(),
                argparse.Namespace(
                    pending_manifest=str(quickload_root / "manifest.pending.json"),
                    manifest=str(quickload_root / "manifest.json"),
                    report_pending=str(report_pending),
                    report=str(self.artifacts / "report.txt"),
                    clone_prepared=str(self.prepared), clone_ledger=str(self.ledger),
                    staged_manifest=str(self.staged)),
            )

        quickload, arguments = prepare("quickload-publish-happy")
        ledger = self.read_ledger(self.ledger, finalized=True)
        binding = ledger["post_runtime"]["quickload"]
        self.assertEqual(
            binding["contract"],
            "openxray-ios27-quicksave-quickload-semantic-v2")
        self.assertEqual(binding["runtime"], "27.0")
        self.assertRegex(binding["semantic_sha256"], r"^[0-9a-f]{64}$")
        for key in ("manifest", "report_fields"):
            path = Path(binding[key]["path"])
            details = path.stat()
            self.assertEqual(
                (binding[key]["dev"], binding[key]["ino"], binding[key]["bytes"],
                 binding[key]["sha256"]),
                (details.st_dev, details.st_ino, details.st_size,
                 hashlib.sha256(path.read_bytes()).hexdigest()))
        fake_spec = types.SimpleNamespace(
            loader=types.SimpleNamespace(exec_module=lambda module: None))
        real_spec_from_file = importlib.util.spec_from_file_location
        real_module_from_spec = importlib.util.module_from_spec
        def selective_spec(name, path, *args, **kwargs):
            if name == "retail_guard_for_quickload_publish":
                return fake_spec
            return real_spec_from_file(name, path, *args, **kwargs)
        def selective_module(spec):
            return GUARD if spec is fake_spec else real_module_from_spec(spec)
        with mock.patch.object(
                 quickload.importlib.util, "spec_from_file_location",
                 side_effect=selective_spec), \
             mock.patch.object(quickload.importlib.util, "module_from_spec",
                               side_effect=selective_module), \
             mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()):
            quickload.publish(arguments)
        self.assertTrue(Path(arguments.manifest).is_file())
        self.assertTrue(Path(arguments.report).is_file())

        quickload, arguments = prepare("quickload-publish-completion-mutation")
        real_completion = GUARD._publish_completion_record_at

        def mutate_binding_after_completion(parent_fd, stem, value, label):
            result = real_completion(parent_fd, stem, value, label)
            if label == "QuickLoad retail report publication completion":
                ledger = json.loads(self.ledger.read_text())
                ledger["post_runtime"]["quickload"]["semantic_sha256"] = "f" * 64
                self.ledger.write_bytes(GUARD._canonical_json(ledger))
                self.ledger.chmod(0o600)
            return result

        with mock.patch.object(
                 quickload.importlib.util, "spec_from_file_location",
                 side_effect=selective_spec), \
             mock.patch.object(quickload.importlib.util, "module_from_spec",
                               side_effect=selective_module), \
             mock.patch.object(GUARD, "_import_retail_import",
                               return_value=self.importer()), \
             mock.patch.object(GUARD, "_publish_completion_record_at",
                               side_effect=mutate_binding_after_completion), \
             self.assertRaises((quickload.EvidenceError, GUARD.GuardError)):
            quickload.publish(arguments)
        self.assertFalse(Path(arguments.report).exists())

    def test_uf_tracked_removal_other_combination_and_flags_race_fail_closed(self) -> None:
        for mutation in ("removal", "other", "combination", "race"):
            with self.subTest(mutation=mutation):
                self._new_clone_case(f"tracked-invalid-{mutation}")
                paths = [record["path"] for record in
                         self.read_ledger(self.pending, finalized=False)["files"]]
                victim = paths[0]
                self._snapshot_flags("D0", {})
                if mutation == "removal":
                    self._snapshot_flags("D1", {victim: GUARD.UF_TRACKED})
                    label, flags = "D2", {}
                else:
                    label = "D1"
                    flags = {victim: (0x20 if mutation == "other" else 0x41)}
                if mutation == "race":
                    real_stat = GUARD._descriptor_relative_stat
                    calls = 0
                    def race_flags(root_fd, relative, stat_label):
                        nonlocal calls
                        value = real_stat(root_fd, relative, stat_label)
                        if relative.as_posix() == victim:
                            calls += 1
                            value = dict(value)
                            value["flags"] = ("0x00000000" if calls == 1
                                              else "0x00000040")
                        return value
                    with mock.patch.object(
                             GUARD, "_descriptor_relative_stat",
                             side_effect=race_flags), self.assertRaises(GUARD.GuardError):
                        self._snapshot(label)
                else:
                    with self.assertRaises(GUARD.GuardError):
                        self._snapshot_flags(label, flags)
                self.assertFalse(self.ledger.exists())
                self.assertFalse((self.artifacts / "report.txt").exists())

    def test_diagnostic_chain_missing_replay_reorder_substitution_ledger_and_file_set_fail(self) -> None:
        for mutation in ("missing", "replay", "reorder", "substitution",
                         "ledger", "file-set"):
            with self.subTest(mutation=mutation):
                self._new_clone_case(f"diagnostic-chain-{mutation}")
                self._snapshot_flags("D0", {})
                self._snapshot_flags("D1", {})
                root = self._diagnostics_root()
                d0, d1 = root / "D0.json", root / "D1.json"
                if mutation == "missing":
                    d0.unlink()
                elif mutation == "replay":
                    d1.write_bytes(d0.read_bytes())
                elif mutation == "reorder":
                    temporary = root / "swap"
                    d0.rename(temporary)
                    d1.rename(d0)
                    temporary.rename(d1)
                elif mutation == "substitution":
                    data = d0.read_bytes()
                    d0.unlink()
                    d0.write_bytes(data)
                    d0.chmod(0o600)
                else:
                    value = json.loads(d1.read_text())
                    if mutation == "ledger":
                        value["ledger"]["sha256"] = "f" * 64
                    else:
                        value["files"] = value["files"][:-1]
                        value["file_count"] -= 1
                    d1.write_bytes(GUARD._canonical_json(value))
                with self.assertRaises(GUARD.GuardError):
                    self._snapshot_flags("D2", {})
                self.assertFalse(self.ledger.exists())
                self.assertFalse((self.artifacts / "report.txt").exists())

    def test_d4_tracked_to_final_zero_is_rejected(self) -> None:
        self.stage(saves=True)
        victim = self.read_ledger(self.pending, finalized=False)["files"][0]["path"]
        self._snapshot_flags("D0", {})
        for label in ("D1", "D2", "D3", "D4"):
            self._snapshot_flags(label, {victim: GUARD.UF_TRACKED})
        with self.assertRaisesRegex(GUARD.GuardError, "removed UF_TRACKED"):
            self.finalize(saves=True, diagnostics=self._diagnostics_root())
        self.assertFalse(self.ledger.exists())
        self.assertFalse(
            (self.artifacts / "quickload-evidence/manifest.pending.json").exists())
        self.assertFalse(
            (self.artifacts / "quickload-evidence/report-fields.txt").exists())

    def test_final_only_accepts_zero_and_tracked_without_continuity_claim(self) -> None:
        for tracked in (False, True):
            with self.subTest(tracked=tracked):
                self._new_clone_case(f"final-only-{tracked}", saves=False)
                path = self.read_ledger(self.pending, finalized=False)["files"][0]["path"]
                flags = {path: GUARD.UF_TRACKED} if tracked else {}
                self._finalize_with_flags(flags)
                post = self.read_ledger(self.ledger, finalized=True)["post_runtime"]
                self.assertEqual(post["continuity_mode"], "final-only")
                self.assertIsNone(post["diagnostics"])
                self.assertTrue(all(record["previous_flags"] is None
                                    and record["transition"] == "final-only"
                                    for record in post["files"]))
                expected_tracked = 1 if tracked else 0
                self.assertEqual(post["flag_counts"]["tracked"], expected_tracked)

    def test_global_private_metadata_and_directories_never_accept_uf_tracked(self) -> None:
        leaf = self.documents / "resources/resources.db0"
        leaf_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW)
        directory_fd = os.open(self.documents, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        real_fstat = GUARD.os.fstat

        def tracked(fd):
            info = real_fstat(fd)
            values = {name: getattr(info, name) for name in (
                "st_mode", "st_uid", "st_nlink", "st_dev", "st_ino",
                "st_size", "st_mtime_ns")}
            values["st_flags"] = GUARD.UF_TRACKED
            return types.SimpleNamespace(**values)

        try:
            with mock.patch.object(GUARD.os, "fstat", side_effect=lambda fd: (
                    tracked(fd) if fd == leaf_fd else real_fstat(fd))):
                with self.assertRaisesRegex(GUARD.GuardError, "flags=0"):
                    GUARD._file_metadata(leaf_fd, "prepared/source/private file", digest=False)
            with mock.patch.object(GUARD.os, "fstat", side_effect=lambda fd: (
                    tracked(fd) if fd == directory_fd else real_fstat(fd))):
                with self.assertRaisesRegex(GUARD.GuardError, "without file flags"):
                    GUARD._directory_metadata(
                        directory_fd, "stage/private directory", child=False)
        finally:
            os.close(directory_fd)
            os.close(leaf_fd)

    def test_private_pin_rejects_uf_tracked_initially_and_after_pin(self) -> None:
        parent = self.root / "private-pin-flags"
        parent.mkdir(mode=0o700)
        leaf = parent / "manifest.pending.json"
        leaf.write_bytes(b"private transient\n")
        leaf.chmod(0o600)
        parent_fd = GUARD._open_absolute_directory_nofollow(parent, "private pin parent")
        real_fstat = GUARD.os.fstat
        identity = (leaf.stat().st_dev, leaf.stat().st_ino)

        def with_tracked(info):
            values = {name: getattr(info, name) for name in (
                "st_mode", "st_uid", "st_nlink", "st_dev", "st_ino",
                "st_size", "st_mtime_ns")}
            values["st_flags"] = GUARD.UF_TRACKED
            return types.SimpleNamespace(**values)

        def tracked_leaf(fd):
            info = real_fstat(fd)
            return with_tracked(info) if (info.st_dev, info.st_ino) == identity else info

        try:
            with mock.patch.object(GUARD.os, "fstat", side_effect=tracked_leaf), \
                 self.assertRaisesRegex(GUARD.GuardError, "flags=0"):
                GUARD._read_pinned_regular_at(
                    parent_fd, leaf, "initial tracked private transient", private=True)

            state = GUARD._read_pinned_regular_at(
                parent_fd, leaf, "private transient", private=True)
            try:
                with mock.patch.object(GUARD.os, "fstat", side_effect=tracked_leaf), \
                     self.assertRaisesRegex(GUARD.GuardError, "flags=0"):
                    GUARD._revalidate_pinned(
                        state, "post-pin tracked private transient")
            finally:
                GUARD._close_pinned(state)
        finally:
            os.close(parent_fd)

    def test_mutable_replacement_constraints_and_tracked_positive(self) -> None:
        for mutation in ("tracked-positive", "filesystem", "uid", "mode",
                         "nlink", "empty", "combined-flags"):
            with self.subTest(mutation=mutation):
                self._new_clone_case(f"mutable-{mutation}")
                save_path = "_appdata_/savedgames/slot.scop"
                save = self.destination / save_path
                replacement = save.with_name("slot.new")
                replacement.write_bytes(b"runtime-save")
                replacement.chmod(0o600)
                replacement.replace(save)
                if mutation == "mode":
                    save.chmod(0o640)
                elif mutation == "nlink":
                    os.link(save, save.with_name("slot.hardlink"))
                elif mutation == "empty":
                    save.write_bytes(b"")
                if mutation in {"tracked-positive", "combined-flags"}:
                    flags = {save_path: (GUARD.UF_TRACKED
                                         if mutation == "tracked-positive" else 0x41)}
                    action = lambda: self._finalize_with_flags(
                        flags, saves=True, mutable="slot")
                elif mutation in {"filesystem", "uid"}:
                    real_metadata = GUARD._destination_file_metadata
                    def changed(fd, label, *, digest, mutation=mutation):
                        value = real_metadata(fd, label, digest=digest)
                        if label.endswith(f"destination file {save_path}"):
                            value = dict(value)
                            key = "dev" if mutation == "filesystem" else "uid"
                            value[key] += 1
                        return value
                    action = lambda: mock.patch.object(
                        GUARD, "_destination_file_metadata", side_effect=changed)
                else:
                    action = lambda: self.finalize(saves=True, mutable="slot")
                if mutation == "tracked-positive":
                    action()
                    post = self.read_ledger(self.ledger, finalized=True)["post_runtime"]
                    mutable = next(record for record in post["files"] if record["mutable"])
                    self.assertEqual(mutable["destination"]["flags"], GUARD.UF_TRACKED)
                elif mutation in {"filesystem", "uid"}:
                    with action(), self.assertRaises(GUARD.GuardError):
                        self.finalize(saves=True, mutable="slot")
                    self.assertFalse(self.ledger.exists())
                else:
                    with self.assertRaises(GUARD.GuardError):
                        action()
                    self.assertFalse(self.ledger.exists())

    def test_v1_and_malformed_v2_ledgers_and_diagnostics_are_rejected(self) -> None:
        self.stage(saves=True)
        original = json.loads(self.pending.read_text())
        mutations = {
            "v1": {**original, "schema": "openxray.retail-clone-ledger.v1"},
            "extra": {**original, "unexpected": True},
            "missing": {key: value for key, value in original.items()
                        if key != "uf_tracked_policy"},
        }
        for name, value in mutations.items():
            candidate = self.artifacts / f"ledger-{name}.json"
            candidate.write_bytes(GUARD._canonical_json(value))
            candidate.chmod(0o600)
            with self.assertRaises(GUARD.GuardError):
                GUARD._read_clone_ledger(candidate, finalized=False)
        self._snapshot_flags("D0", {})
        diagnostic = self._diagnostics_root() / "D0.json"
        value = json.loads(diagnostic.read_text())
        value["schema"] = "openxray.retail-clone-stat-diagnostics.v1"
        diagnostic.write_bytes(GUARD._canonical_json(value))
        with self.assertRaises(GUARD.GuardError):
            self._snapshot_flags("D1", {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
