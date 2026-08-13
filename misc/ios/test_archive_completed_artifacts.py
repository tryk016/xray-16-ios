#!/usr/bin/env python3
"""Focused mutation/recovery cases for the permanent DevArchive policy."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_archive_completed_artifacts.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import errno
import copy
from contextlib import contextmanager, ExitStack, nullcontext
from dataclasses import replace
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import archive_completed_artifacts as ARC


class TestPlatform(ARC.DarwinPlatform):
    def __init__(self, mount: Path, settings: ARC.Settings) -> None:
        self.mount = mount
        self.attrs: dict[str, str] | None = {
            "mountpoint": str(mount), "name": settings.volume_name,
            "uuid": settings.volume_uuid, "filesystem": "apfs",
        }
        self.synced: list[tuple[int, int]] = []
        self.sync_labels: list[str] = []
        self.fail_sync_label: str | None = None
        self.write_limit: int | None = None
        self.fail_write_after: int | None = None
        self.written = 0
        self.gate_hash = "a" * 64
        self.clock = 2_000_000_000.0
        self.current_gate_state: dict[str, object] = {
            "head": "1" * 40, "status_sha256": "d" * 64,
            "diff_binary_head_sha256": "e" * 64, "untracked": [],
        }

    def open_mount(self, path: Path) -> int:
        if path != self.mount:
            raise OSError(errno.ENOENT, "test mount is unavailable")
        return ARC.open_absolute_directory(path, "test mount")

    def volume_attrs(self, fd: int) -> dict[str, str]:
        if self.attrs is None:
            raise ARC.DeferredVolume("test volume disconnected")
        return dict(self.attrs)

    def rename_exclusive(self, source_parent_fd: int, source: str,
                         destination_parent_fd: int, destination: str) -> None:
        try:
            os.stat(destination, dir_fd=destination_parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ARC.ArchiveError("test exclusive destination exists")
        os.rename(source, destination, src_dir_fd=source_parent_fd,
                  dst_dir_fd=destination_parent_fd)

    def fsync(self, fd: int) -> None:
        label = getattr(self, "current_label", "")
        if self.fail_sync_label is not None and self.fail_sync_label in label:
            raise OSError(errno.EIO, "injected fsync failure")
        self.synced.append(ARC.identity(os.fstat(fd)))
        os.fsync(fd)

    def write(self, fd: int, value: bytes) -> int:
        if self.fail_write_after is not None and self.written >= self.fail_write_after:
            raise OSError(errno.EIO, "injected write failure")
        chunk = value if self.write_limit is None else value[:self.write_limit]
        written = os.write(fd, chunk)
        self.written += written
        return written

    def now_unix(self) -> float:
        return self.clock

    def gate_input_hash(self, settings: ARC.Settings) -> str:
        del settings
        return self.gate_hash

    def gate_state(self, settings: ARC.Settings) -> dict[str, object]:
        del settings
        return json.loads(json.dumps(self.current_gate_state))


class ArchivePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openxray-archive-v2-")
        self.base = Path(self.temporary.name).resolve()
        self.handoff = self.base / "handoff"
        self.handoff.mkdir(mode=0o700)
        self.mount = self.base / "DevArchive"
        self.mount.mkdir(mode=0o700)
        archive = self.mount / ARC.ARCHIVE_ROOT
        archive.mkdir(mode=0o755)
        for leaf in (*ARC.CATEGORY_ROOTS.values(), ARC.MANIFEST_ROOT):
            (archive / leaf).mkdir(mode=0o755)
        self.source = self.base / "closed-evidence"
        self.source.mkdir(mode=0o700)
        (self.source / "report.txt").write_text("closed evidence\n", encoding="utf-8")
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "trace.bin").write_bytes(b"trace\0payload")
        self.candidate = ARC.CandidateSpec(
            "test-closed", self.source, "completed-evidence",
            "reproducible-noncritical", "delete-after-full-pass")
        self.settings = ARC.Settings(
            queue_root=self.handoff / "archive-queue", mount=self.mount,
            volume_name="DevArchive", volume_uuid=ARC.VOLUME_UUID,
            production=False, review_authorized=True,
            repo_root=self.base / "repo", gate_log_root=self.base / "gate-logs",
            full_gate_stamp=self.base / "repo/build/full/.ios_full_gate_ok",
            candidates=(self.candidate,))
        self.platform = TestPlatform(self.mount, self.settings)
        self.service = ARC.ArchiveService(self.settings, self.platform)
        self.write_gate_receipt()

    def tearDown(self) -> None:
        subprocess.run(("chmod", "-RN", str(self.base)), check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(("chflags", "-R", "nouchg,noschg", str(self.base)), check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for root, directories, files in os.walk(self.base, topdown=False, followlinks=False):
            for name in files:
                path = Path(root) / name
                if not path.is_symlink():
                    os.chmod(path, 0o600)
            for name in directories:
                path = Path(root) / name
                if not path.is_symlink():
                    os.chmod(path, 0o700)
        self.temporary.cleanup()

    def write_gate_receipt(self, *, source_hash: str | None = None,
                           ended: float | None = None,
                           receipt_name: str = "gate-2000000000-123-0.json") -> Path:
        source_hash = source_hash or self.platform.gate_hash
        ended = self.platform.clock - 10 if ended is None else ended
        self.settings.gate_log_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.settings.full_gate_stamp.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = (
            f"source_sha256={source_hash}\n"
            f"app_uuid=00000000-0000-0000-0000-000000000001\n"
            f"bundle_sha256={'b' * 64}\n"
            "openal_provider=OpenALSoft-test\n"
            f"openal_sha256={'c' * 64}\n"
            "platform=IOS\nminos=16.4\nshader_cache=forced-off\n"
        ).encode("ascii")
        self.settings.full_gate_stamp.write_bytes(stamp)
        os.chmod(self.settings.full_gate_stamp, 0o644)
        receipt_path = self.settings.gate_log_root / receipt_name
        log_path = receipt_path.with_suffix(".log")
        log = b"full gate PASS\n"
        log_path.write_bytes(log)
        os.chmod(log_path, 0o600)
        state = json.loads(json.dumps(self.platform.current_gate_state))
        receipt = {
            "schema": "openxray.gate-log.v2", "gate": "full",
            "started_unix": ended - 20, "ended_unix": ended, "exit_code": 0,
            "before": state, "after": state,
            "log_path": str(log_path), "log_sha256": ARC.sha256_bytes(log),
            "log_identity_mismatch": False,
            "detail_dir": str(self.settings.gate_log_root / f"detail-{receipt_path.stem}"),
            "underlying_stamp": {"path": str(self.settings.full_gate_stamp),
                                  "sha256": ARC.sha256_bytes(stamp)},
        }
        receipt_path.write_bytes(ARC.canonical_json(receipt))
        os.chmod(receipt_path, 0o600)
        return receipt_path

    def enqueue(self) -> str:
        return self.service.enqueue(self.candidate.ident)["transaction_id"]

    def drain(self, transaction: str) -> dict[str, object]:
        return self.service.drain_one(transaction, apply=True)

    def crash_then_resume(self, checkpoint: str) -> tuple[str, dict[str, object]]:
        transaction = self.enqueue()
        self.service.hooks[checkpoint] = lambda **_: (_ for _ in ()).throw(
            ARC.InjectedCrash(checkpoint))
        with self.assertRaisesRegex(ARC.InjectedCrash, checkpoint):
            self.drain(transaction)
        self.service.hooks.clear()
        result = self.drain(transaction)
        self.assertEqual(result["status"], "PASS")
        return transaction, result

    def transaction_fd(self, transaction: str) -> int:
        return ARC.open_absolute_directory(self.settings.queue_root / transaction,
                                           "test transaction")

    def external_category(self) -> Path:
        return self.mount / ARC.ARCHIVE_ROOT / "completed-evidence"

    def final_path(self, transaction: str) -> Path:
        return self.external_category() / f"{transaction}-{self.source.name}"

    @staticmethod
    def write_tree_file(root: Path, relative: str, contents: bytes) -> Path:
        path = root / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def real_retail_loader_fixture(
            self, name: str) -> tuple[Path, Path, Path]:
        """Copy the real nested loader modules and make a valid tiny cache."""
        root = self.base / name
        modules = root / "modules"
        prepared = root / "prepared"
        documents = prepared / "Documents"
        manifests = prepared / "manifest"
        modules.mkdir(mode=0o700, parents=True)
        (modules / "__pycache__").mkdir(mode=0o700)
        source_modules = Path(ARC.__file__).resolve().parent
        for module_name in (
                "retail_import.py", "retail_simulator_guard.py",
                "openal_provider_contract.py"):
            shutil.copyfile(source_modules / module_name,
                            modules / module_name)

        required = tuple(
            [f"resources/resources.db{index}" for index in range(5)]
            + [f"levels/levels.db{index}" for index in range(2)])
        rows: list[tuple[str, int, str]] = []
        for index, relative in enumerate(required):
            data = f"retail-loader-{index}\n".encode("ascii")
            path = self.write_tree_file(documents, relative, data)
            rows.append((relative, len(data), ARC.sha256_bytes(data)))
            os.chmod(path, 0o600)
        manifests.mkdir(mode=0o700, parents=True)
        files_tsv = ("bytes\tpath\n" + "".join(
            f"{size}\t{relative}\n" for relative, size, _ in rows
        )).encode("ascii")
        required_tsv = ("bytes\tsha256\tpath\tstatus\n" + "".join(
            f"{size}\t{digest}\t{relative}\tPASS\n"
            for relative, size, digest in rows)).encode("ascii")
        large_tsv = ("sha256\tbytes\tpath\n" + "".join(
            f"{digest}\t{size}\t{relative}\n"
            for relative, size, digest in rows)).encode("ascii")
        prepared_tsv = ("sha256\tbytes\tpath\n" + "".join(
            f"{digest}\t{size}\t{relative}\n"
            for relative, size, digest in rows)).encode("ascii")
        manifest_payloads = {
            "files.tsv": files_tsv,
            "required-archives.tsv": required_tsv,
            "large-files-sha256.tsv": large_tsv,
            "prepared-files.tsv": prepared_tsv,
        }
        inventory = ("sha256\tbytes\tpath\n" + "".join(
            f"{ARC.sha256_bytes(data)}\t{len(data)}\t{item}\n"
            for item, data in sorted(manifest_payloads.items()))).encode("ascii")
        manifest_payloads["prepared-manifest-files.tsv"] = inventory
        for item, data in manifest_payloads.items():
            path = manifests / item
            path.write_bytes(data)
            os.chmod(path, 0o600)
        for current, directories, _ in os.walk(prepared):
            os.chmod(current, 0o700)
            for directory in directories:
                os.chmod(Path(current) / directory, 0o700)
        return modules / "retail_import.py", prepared, modules / "__pycache__"

    @staticmethod
    def tree_snapshot(root: Path) -> dict[str, object]:
        descriptor = ARC.open_absolute_directory(root, "metadata-exact test snapshot")
        try:
            return ARC.manifest_bound(descriptor, "directory")
        finally:
            os.close(descriptor)

    def assert_tree_preserved(self, root: Path, snapshot: dict[str, object]) -> None:
        self.assertTrue(root.is_dir())
        self.assertEqual(ARC.canonical_json(self.tree_snapshot(root)),
                         ARC.canonical_json(snapshot))

    @staticmethod
    def overlay_fixture() -> tuple[
            dict[str, object], dict[str, object], ARC.ProvenanceOverlayPolicy,
            str, str, int]:
        value = ARC.PROVENANCE_OVERLAY_VALUE
        common = {"uid": os.geteuid(), "gid": os.getegid(), "mtime_ns": 123456789,
                  "flags": 0, "acl": "", "allocated_bytes": 4096}
        entries = [
            {"path": ".", "kind": "directory", "mode": 0o700,
             "xattrs": {}, "local_identity": [1, 10], "logical_bytes": 0, **common},
            {"path": "nested", "kind": "directory", "mode": 0o755,
             "xattrs": {}, "local_identity": [1, 11], "logical_bytes": 0, **common},
            {"path": "inherited.bin", "kind": "regular", "mode": 0o600,
             "xattrs": {ARC.PROVENANCE_OVERLAY_XATTR: value},
             "local_identity": [1, 12], "logical_bytes": 3,
             "sha256": ARC.sha256_bytes(b"one"), **common},
            {"path": "nested/added.bin", "kind": "regular", "mode": 0o640,
             "xattrs": {"com.openxray.kept": "a2VwdA=="},
             "local_identity": [1, 13], "logical_bytes": 3,
             "sha256": ARC.sha256_bytes(b"two"), **common},
        ]
        source = ARC.finish_manifest(copy.deepcopy(entries))
        physical_entries = copy.deepcopy(entries)
        added = []
        for index, row in enumerate(physical_entries):
            row["local_identity"] = [2, 100 + index]
            row["allocated_bytes"] = 8192
            if ARC.PROVENANCE_OVERLAY_XATTR not in row["xattrs"]:
                row["xattrs"][ARC.PROVENANCE_OVERLAY_XATTR] = value
                added.append(row["path"])
        physical = ARC.finish_manifest(physical_entries)
        candidate_id = "pure-overlay-main"
        transaction_id = "a" * 32
        allowlist_version = ARC.ALLOWLIST_VERSION
        policy = ARC.ProvenanceOverlayPolicy(
            77, ARC.PROVENANCE_OVERLAY_XATTR, value,
            candidate_id, allowlist_version,
            len(entries), 2, 2, 1, len(added),
            ARC.sha256_bytes(ARC.canonical_json(sorted(added))),
            source["tree_sha256"], physical["tree_sha256"])
        return source, physical, policy, transaction_id, candidate_id, allowlist_version

    @staticmethod
    def rebuild_manifest(manifest: dict[str, object]) -> dict[str, object]:
        return ARC.finish_manifest(copy.deepcopy(manifest["entries"]))

    @staticmethod
    def set_xattr_tree_fd(root_fd: int, name: str, encoded: str) -> None:
        stack = [os.dup(root_fd)]
        try:
            while stack:
                descriptor = stack.pop()
                try:
                    values = ARC.xattrs_fd(descriptor)
                    values[name] = encoded
                    ARC.apply_xattrs_fd(descriptor, values)
                    if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                        for leaf in os.listdir(descriptor):
                            info = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
                            if stat.S_ISDIR(info.st_mode):
                                child = ARC.open_leaf(descriptor, leaf, "directory")
                            elif stat.S_ISREG(info.st_mode):
                                child = ARC.open_leaf(descriptor, leaf, "regular")
                            else:
                                raise AssertionError("overlay integration fixture is not regular")
                            stack.append(child)
                finally:
                    os.close(descriptor)
        except BaseException:
            for descriptor in stack:
                os.close(descriptor)
            raise

    def configure_overlay_integration(
            self, transaction: str) -> ARC.ProvenanceOverlayPolicy:
        source_manifest = self.tree_snapshot(self.source)
        physical_entries = copy.deepcopy(source_manifest["entries"])
        test_name = "com.openxray.test-provenance"
        test_value = "dGVzdC1vdmVybGF5"
        inherited = []
        added = []
        for row in physical_entries:
            if test_name in row["xattrs"]:
                inherited.append(row["path"])
            else:
                row["xattrs"][test_name] = test_value
                added.append(row["path"])
        physical = ARC.finish_manifest(physical_entries)
        policy = ARC.ProvenanceOverlayPolicy(
            901, test_name, test_value, self.candidate.ident,
            ARC.ALLOWLIST_VERSION, len(physical_entries),
            source_manifest["files"], source_manifest["directories"],
            len(inherited), len(added),
            ARC.sha256_bytes(ARC.canonical_json(sorted(added))),
            source_manifest["tree_sha256"], physical["tree_sha256"])
        self.service.provenance_overlay_policy_for_record = lambda record: policy
        self.service.hooks["before_copy_manifest"] = lambda payload_fd, **_: (
            self.set_xattr_tree_fd(payload_fd, test_name, test_value))
        return policy

    def configure_retail(self, tag: str = "") -> tuple[
            ARC.CandidateSpec, ARC.CandidateSpec, Path]:
        suffix = f"-{tag}" if tag else ""
        source = self.base / f"device-retail-backup-test{suffix}"
        sibling_source = self.base / f"device-retail-backup-test{suffix}.manifest"
        prepared = self.base / f"retail-prepared-test{suffix}"
        source.mkdir(mode=0o700)
        sibling_source.mkdir(mode=0o700)
        prepared.mkdir(mode=0o700)
        for index, (source_path, prepared_path) in enumerate(ARC.SECOND_COPY_MAPPINGS):
            contents = f"mapped-retail-{index:02d}\n".encode("ascii")
            self.write_tree_file(source, source_path, contents)
            self.write_tree_file(prepared, prepared_path, contents)
        source_ds_store = self.write_tree_file(
            source, ".DS_Store", b"historical finder metadata\n")
        if sys.platform == "darwin":
            subprocess.run(("chflags", "hidden", str(source_ds_store)), check=True)
            self.assertEqual(ARC.bsd_flags(source_ds_store.stat()) & 0x8000, 0x8000)
        self.write_tree_file(
            source, "gamedata/scripts/ui_main_menu.script", b"historical script\n")
        self.write_tree_file(prepared, ".DS_Store", b"prepared finder metadata\n")
        for path in sorted(ARC.PREPARED_METADATA_PATHS):
            self.write_tree_file(prepared, path, b"untrusted historical table\n")
        for path in sorted(ARC.SIBLING_MANIFEST_PATHS):
            self.write_tree_file(sibling_source, path, b"historical source manifest\n")
        main = ARC.CandidateSpec(
            f"retail-main{suffix}", source, "backups", "redundant",
            "requires-fixed-prepared-proof")
        sibling = ARC.CandidateSpec(
            f"retail-sibling{suffix}", sibling_source, "backups",
            "reproducible-noncritical", "after-main-backup-pass")
        self.settings.candidates = (main, sibling)
        self.settings.retail_prepared_root = prepared
        self.settings.test_retail_roles = ((main.ident, "main"), (sibling.ident, "sibling"))
        classifications = []
        source_snapshot = self.tree_snapshot(source)
        file_paths = sorted(
            row["path"] for row in source_snapshot["entries"]
            if row["kind"] == "regular")
        for path in file_paths:
            classification = "redundant-mapped" if path in ARC.MAPPED_SOURCE_PATHS \
                else "historical-reproducible"
            classifications.append({"path": path, "classification": classification})
        self.settings.test_retail_inventory = (
            len(classifications),
            ARC.sha256_bytes(ARC.canonical_json([item["path"] for item in classifications])),
            ARC.sha256_bytes(ARC.canonical_json(classifications)),
        )
        return main, sibling, prepared

    @contextmanager
    def source_root_overlay_transaction(self, tag: str):
        """Create the reviewed ordering in an isolated test transaction.

        The test-only method replacement cannot be reached through Settings,
        CandidateSpec or the CLI; it exists solely to exercise 008+ recovery
        with small fixtures after a canonical 000-007 prefix is durable.
        """
        main, sibling, _ = self.configure_retail(f"source-root-{tag}")
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        self.service.hooks["after_delete_intent"] = lambda **_: (
            _ for _ in ()).throw(ARC.InjectedCrash("armed source-root overlay"))
        with self.assertRaisesRegex(ARC.InjectedCrash, "armed source-root overlay"):
            self.drain(transaction)
        self.service.hooks.clear()
        transaction_root = self.settings.queue_root / transaction
        record = ARC.strict_json_loads((
            transaction_root / ARC.RECORD_NAMES["candidate"]).read_text(encoding="ascii"))
        semantic = record["source_manifest"]
        source_fd = ARC.open_absolute_directory(main.source, "test source-root overlay")
        test_name = "com.openxray.test-source-root-provenance"
        test_value = "c291cmNlLXJvb3Qtb3ZlcmxheQ=="
        try:
            values = ARC.xattrs_fd(source_fd)
            self.assertNotIn(test_name, values)
            values[test_name] = test_value
            ARC.restore_xattrs_fd(source_fd, values)
            os.fsync(source_fd)
            physical = ARC.manifest_bound(source_fd, "directory")
        finally:
            os.close(source_fd)
        policy = ARC.SourceRootOverlayPolicy(
            901, test_name, test_value, transaction, main.ident, str(main.source),
            tuple(record["source_identity"]), tuple(record["source_parent_identity"]),
            ARC.ALLOWLIST_VERSION, len(physical["entries"]), physical["files"],
            physical["directories"], 1,
            ARC.sha256_bytes(ARC.canonical_json(["."])),
            semantic["tree_sha256"], ARC.manifest_canonical_sha256(semantic),
            physical["tree_sha256"], ARC.manifest_canonical_sha256(physical))
        prefix = tuple(
            (ARC.RECORD_NAMES[stage], ARC.sha256_bytes(
                (transaction_root / ARC.RECORD_NAMES[stage]).read_bytes()))
            for stage in ARC.RECORD_STAGES[:8])
        prefix_rows = [{"name": name, "sha256": digest}
                       for name, digest in prefix]
        legacy_005 = ARC.strict_json_loads((
            transaction_root / ARC.RECORD_NAMES["manifest-published"]
        ).read_text(encoding="ascii"))
        legacy_006 = (
            transaction_root / ARC.RECORD_NAMES["second-copy-proof"]).read_bytes()
        original_policy_method = self.service.source_root_overlay_policy_for_record
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                ARC, "SOURCE_ROOT_OVERLAY_LEGACY_PREFIX", prefix))
            stack.enter_context(mock.patch.object(
                ARC, "SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256",
                ARC.sha256_bytes(ARC.canonical_json(prefix_rows))))
            stack.enter_context(mock.patch.object(
                ARC, "SOURCE_ROOT_OVERLAY_LEGACY_005", legacy_005))
            stack.enter_context(mock.patch.object(
                ARC, "SOURCE_ROOT_OVERLAY_LEGACY_006_CANONICAL", legacy_006))
            self.service.source_root_overlay_policy_for_record = lambda candidate_record: (
                policy if candidate_record.get("transaction_id") == transaction else None)
            try:
                yield {
                    "main": main, "sibling": sibling,
                    "transaction": transaction, "record": record,
                    "semantic": semantic, "physical": physical, "policy": policy,
                    "transaction_root": transaction_root,
                    "legacy_005_bytes": (
                        transaction_root / ARC.RECORD_NAMES["manifest-published"]
                    ).read_bytes(),
                    "legacy_006_bytes": legacy_006,
                }
            finally:
                self.service.hooks.clear()
                self.service.source_root_overlay_policy_for_record = original_policy_method

    @contextmanager
    def retirement_overlay_transaction(self, tag: str):
        """Create a hermetic analogue of the exact post-009 fail state."""
        with self.source_root_overlay_transaction(
                f"retirement-overlay-{tag}") as fixture:
            transaction = fixture["transaction"]
            self.service.hooks["after_retirement_started"] = lambda **_: (
                _ for _ in ()).throw(
                    ARC.InjectedCrash("durable-009-before-retirement-overlay"))
            with self.assertRaisesRegex(
                    ARC.InjectedCrash, "durable-009-before-retirement-overlay"):
                self.drain(transaction)
            self.service.hooks.clear()
            root = fixture["transaction_root"]
            intent = ARC.strict_json_loads((
                root / ARC.RECORD_NAMES["delete-intent"]
            ).read_text(encoding="ascii"))
            source_root = (self.settings.queue_root /
                           intent["quarantine_namespace"] /
                           intent["quarantine_name"])
            if not source_root.exists():
                source_root = (self.settings.queue_root /
                               intent["quarantine_namespace"] /
                               intent["retired_name"])
            test_name = "com.openxray.test-retirement-provenance"
            test_value = "cmV0aXJlbWVudC1vdmVybGF5"
            changed_path = ".DS_Store"
            changed_fd = os.open(
                source_root / changed_path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                old_xattrs = ARC.xattrs_fd(changed_fd)
                self.assertNotIn(test_name, old_xattrs)
                new_xattrs = dict(old_xattrs)
                new_xattrs[test_name] = test_value
                ARC.restore_xattrs_fd(changed_fd, new_xattrs)
                os.fsync(changed_fd)
                changed_identity = ARC.identity(os.fstat(changed_fd))
            finally:
                os.close(changed_fd)
            current = self.tree_snapshot(source_root)
            immutable = ARC.strict_json_loads((
                root / ARC.RECORD_NAMES["source-quarantined"]
            ).read_text(encoding="ascii"))["source_physical_manifest"]
            current_rows = {row["path"]: row for row in current["entries"]}
            eligible = sorted(
                path for path, row in current_rows.items()
                if row["kind"] == "regular" and test_name not in row["xattrs"])
            inherited = sorted(
                path for path, row in current_rows.items()
                if test_name in row["xattrs"])
            policy = ARC.RetirementOverlayBaselinePolicy(
                901, ARC.RETIREMENT_OVERLAY_BASELINE_SCHEMA,
                ARC.RETIREMENT_OVERLAY_BASELINE_RECORD,
                transaction, fixture["main"].ident, str(fixture["main"].source),
                tuple(fixture["record"]["source_identity"]),
                tuple(fixture["record"]["source_parent_identity"]),
                ARC.ALLOWLIST_VERSION,
                ARC.sha256_bytes((root / ARC.RECORD_NAMES[
                    "source-quarantined"]).read_bytes()),
                ARC.sha256_bytes((root / ARC.RECORD_NAMES[
                    "retirement-started"]).read_bytes()),
                ARC.manifest_canonical_sha256(immutable),
                immutable["tree_sha256"],
                ARC.manifest_canonical_sha256(current),
                current["tree_sha256"], changed_path, changed_identity,
                tuple(sorted(old_xattrs.items())),
                tuple(sorted(new_xattrs.items())), 1,
                ARC.sha256_bytes(ARC.canonical_json([changed_path])),
                len(current["entries"]), current["files"],
                current["directories"], current["symlinks"], 901,
                len(eligible),
                ARC.sha256_bytes(ARC.canonical_json(eligible)),
                ARC.sha256_bytes(ARC.canonical_json(inherited)),
                test_name, test_value)
            original = \
                self.service.retirement_overlay_baseline_policy_for_transaction
            self.service.retirement_overlay_baseline_policy_for_transaction = \
                lambda transaction_fd, candidate_record: (
                    policy if candidate_record.get("transaction_id") == transaction
                    else None)
            try:
                yield {
                    **fixture, "intent": intent, "source_root": source_root,
                    "immutable_retirement": immutable,
                    "current_retirement": current,
                    "retirement_policy": policy,
                    "eligible": eligible, "inherited": inherited,
                }
            finally:
                self.service.hooks.clear()
                self.service.retirement_overlay_baseline_policy_for_transaction = \
                    original

    @contextmanager
    def pretruncate_open_transaction(self, tag: str):
        """Create a hermetic 009a state and its exact test-only 009b policy."""
        with self.retirement_overlay_transaction(
                f"pretruncate-open-{tag}") as fixture:
            transaction = fixture["transaction"]
            root = fixture["transaction_root"]
            self.service.hooks[
                "after_retirement_overlay_baseline_record"] = lambda **_: (
                    _ for _ in ()).throw(ARC.InjectedCrash("durable-009a"))
            with self.assertRaisesRegex(ARC.InjectedCrash, "durable-009a"):
                self.drain(transaction)
            self.service.hooks.clear()
            baseline_path = root / ARC.RETIREMENT_OVERLAY_BASELINE_RECORD
            baseline_bytes = baseline_path.read_bytes()
            baseline = ARC.strict_json_loads(baseline_bytes.decode("ascii"))
            intent = ARC.strict_json_loads((
                root / ARC.RECORD_NAMES["delete-intent"]
            ).read_text(encoding="ascii"))
            quarantine = (self.settings.queue_root /
                          intent["quarantine_namespace"])
            source_root = quarantine / intent["quarantine_name"]
            if not source_root.exists():
                source_root = quarantine / intent["retired_name"]
            runtime_name = fixture["retirement_policy"].runtime_xattr_name
            runtime_value = fixture["retirement_policy"].runtime_xattr_value
            changed_path = "gamedata/scripts/ui_main_menu.script"
            changed_fd = os.open(
                source_root / changed_path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                changed_before = ARC.stable_retirement_snapshot(
                    changed_fd, "regular", changed_path)
                values = ARC.xattrs_fd(changed_fd)
                self.assertNotIn(runtime_name, values)
                values[runtime_name] = runtime_value
                ARC.restore_xattrs_fd(changed_fd, values)
                os.fsync(changed_fd)
                changed_identity = ARC.identity(os.fstat(changed_fd))
            finally:
                os.close(changed_fd)
            pre_open = self.tree_snapshot(source_root)
            rows = {row["path"]: row for row in pre_open["entries"]}
            eligible = sorted(
                path for path, row in rows.items()
                if row["kind"] == "regular"
                and runtime_name not in row["xattrs"])
            inherited = sorted(
                path for path, row in rows.items()
                if runtime_name in row["xattrs"])
            stage_b = baseline["current_009a_gate_proof"]
            policy = ARC.RetirementPretruncateOpenPolicy(
                902, ARC.RETIREMENT_PRETRUNCATE_OPEN_SCHEMA,
                ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD,
                transaction, fixture["main"].ident, str(fixture["main"].source),
                tuple(fixture["record"]["source_identity"]),
                tuple(fixture["record"]["source_parent_identity"]),
                ARC.ALLOWLIST_VERSION,
                ARC.sha256_bytes((root / ARC.RECORD_NAMES[
                    "source-quarantined"]).read_bytes()),
                ARC.sha256_bytes((root / ARC.RECORD_NAMES[
                    "retirement-started"]).read_bytes()),
                ARC.sha256_bytes(baseline_bytes),
                ARC.manifest_canonical_sha256(
                    baseline["current_source_manifest"]),
                baseline["current_source_manifest"]["tree_sha256"],
                baseline["production_authorization_sha256"],
                stage_b["gate_source_sha256"],
                stage_b["gate_stamp_sha256"],
                ARC.manifest_canonical_sha256(pre_open),
                pre_open["tree_sha256"], changed_path, changed_identity,
                changed_before["logical_bytes"], changed_before["sha256"],
                changed_before["mode"], changed_before["mtime_ns"],
                ARC.sha256_bytes(ARC.canonical_json([changed_path])),
                len(pre_open["entries"]), pre_open["files"],
                pre_open["directories"], pre_open["symlinks"],
                len(eligible), ARC.sha256_bytes(ARC.canonical_json(eligible)),
                len(inherited), ARC.sha256_bytes(ARC.canonical_json(inherited)),
                runtime_name, runtime_value)
            original = \
                self.service.retirement_pretruncate_open_policy_for_transaction
            self.service.retirement_pretruncate_open_policy_for_transaction = \
                lambda transaction_fd, candidate_record: (
                    policy if candidate_record.get("transaction_id") == transaction
                    else None)
            try:
                yield {
                    **fixture, "source_root": source_root,
                    "retirement_baseline": baseline,
                    "retirement_baseline_bytes": baseline_bytes,
                    "pretruncate_policy": policy,
                    "pretruncate_base": pre_open,
                    "pretruncate_eligible": eligible,
                    "pretruncate_inherited": inherited,
                    "pretruncate_runtime_name": runtime_name,
                    "pretruncate_runtime_value": runtime_value,
                    "pretruncate_changed_path": changed_path,
                }
            finally:
                self.service.hooks.clear()
                self.service.retirement_pretruncate_open_policy_for_transaction = \
                    original

    @contextmanager
    def historical_completed_transaction(self, tag: str):
        """Build a small exact closed analogue for the read-only command."""
        with self.pretruncate_open_transaction(
                f"historical-completed-{tag}") as fixture:
            transaction = fixture["transaction"]
            self.assertEqual(self.drain(transaction)["status"], "PASS")
            root = fixture["transaction_root"]
            stage_names = [name for name, _ in
                           ARC.HISTORICAL_COMPLETED_STAGE_SHA256]
            stages = {
                name: ARC.strict_json_loads(
                    (root / name).read_text(encoding="ascii"))
                for name in stage_names
            }
            published = stages[ARC.RECORD_NAMES["published"]]
            second_stage = stages[ARC.RECORD_NAMES["second-copy-proof"]]
            retired = stages[ARC.RECORD_NAMES["source-deleted"]]
            receipt = stages[ARC.RECORD_NAMES["deletion-receipt"]]
            manifests_root = self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT
            external_manifest = ARC.strict_json_loads(
                (manifests_root / stages[ARC.RECORD_NAMES[
                    "manifest-published"]]["external_manifest"]
                 ).read_text(encoding="ascii"))
            external_second = ARC.strict_json_loads(
                (manifests_root / second_stage["external_proof"]
                 ).read_text(encoding="ascii"))
            external_receipt = ARC.strict_json_loads(
                (manifests_root / receipt["external_receipt"]
                 ).read_text(encoding="ascii"))

            def historical_proof(slot: int) -> dict[str, object]:
                name = f"gate-1999998{slot:03d}000000000-9{slot:02d}-0.json"
                source_hash = f"{slot + 1:x}" * 64
                receipt_path = self.write_gate_receipt(
                    source_hash=source_hash,
                    ended=self.platform.clock - 1000 - slot,
                    receipt_name=name)
                raw = receipt_path.read_bytes()
                gate_receipt = ARC.strict_json_loads(raw.decode("ascii"))
                return {
                    "schema": "openxray.archive-gate-proof.v1",
                    "review_policy_version": ARC.REVIEW_POLICY_VERSION,
                    "receipt_name": name,
                    "receipt_sha256": ARC.sha256_bytes(raw),
                    "gate": "full",
                    "gate_ended_unix": gate_receipt["ended_unix"],
                    "gate_source_sha256": source_hash,
                    "gate_stamp_sha256":
                        gate_receipt["underlying_stamp"]["sha256"],
                    "gate_log_sha256": gate_receipt["log_sha256"],
                }

            transfer_gate = copy.deepcopy(external_manifest["gate_proof"])
            gate_a, gate_b, gate_c = tuple(
                historical_proof(slot) for slot in range(1, 4))

            def write_stage(name: str) -> str:
                path = root / name
                path.write_bytes(ARC.canonical_json(stages[name]))
                os.chmod(path, 0o600)
                return ARC.sha256_bytes(path.read_bytes())

            def source_root_gates(
                    payload: dict[str, object], immutable: dict[str, object],
                    current: dict[str, object]) -> None:
                payload["source_root_overlay_gate_proof"] = immutable
                payload["source_root_overlay_gate_proof_sha256"] = \
                    ARC.sha256_bytes(ARC.canonical_json(immutable))
                payload["source_root_overlay_current_gate_proof"] = current
                payload["source_root_overlay_current_gate_proof_sha256"] = \
                    ARC.sha256_bytes(ARC.canonical_json(current))

            external_manifest_path = (
                manifests_root / stages[ARC.RECORD_NAMES[
                    "manifest-published"]]["external_manifest"])

            quarantined_name = ARC.RECORD_NAMES["source-quarantined"]
            stages[quarantined_name]["gate_proof"] = gate_a
            stages[quarantined_name]["gate_proof_sha256"] = \
                ARC.sha256_bytes(ARC.canonical_json(gate_a))
            quarantined_sha = write_stage(quarantined_name)

            retirement_name = ARC.RECORD_NAMES["retirement-started"]
            source_root_gates(stages[retirement_name], gate_a, gate_b)
            stages[retirement_name]["source_quarantined_stage_sha256"] = \
                quarantined_sha
            retirement_sha = write_stage(retirement_name)

            baseline_name = ARC.RETIREMENT_OVERLAY_BASELINE_RECORD
            stages[baseline_name]["immutable_008_gate_proof"] = gate_a
            stages[baseline_name]["immutable_008_gate_proof_sha256"] = \
                ARC.sha256_bytes(ARC.canonical_json(gate_a))
            stages[baseline_name]["current_009a_gate_proof"] = gate_b
            stages[baseline_name]["current_009a_gate_proof_sha256"] = \
                ARC.sha256_bytes(ARC.canonical_json(gate_b))
            stages[baseline_name]["source_quarantined_stage_sha256"] = \
                quarantined_sha
            stages[baseline_name]["retirement_started_stage_sha256"] = \
                retirement_sha
            baseline_sha = write_stage(baseline_name)

            pretruncate_name = ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD
            stages[pretruncate_name]["historical_009a_gate_proof"] = gate_b
            stages[pretruncate_name]["historical_009a_gate_proof_sha256"] = \
                ARC.sha256_bytes(ARC.canonical_json(gate_b))
            stages[pretruncate_name]["current_009b_gate_proof"] = gate_c
            stages[pretruncate_name]["current_009b_gate_proof_sha256"] = \
                ARC.sha256_bytes(ARC.canonical_json(gate_c))
            stages[pretruncate_name]["source_quarantined_stage_sha256"] = \
                quarantined_sha
            stages[pretruncate_name]["retirement_started_stage_sha256"] = \
                retirement_sha
            stages[pretruncate_name][
                "retirement_overlay_baseline_stage_sha256"] = baseline_sha
            pretruncate_sha = write_stage(pretruncate_name)

            retired_name = ARC.RECORD_NAMES["source-deleted"]
            source_root_gates(stages[retired_name], gate_a, gate_c)
            stages[retired_name]["source_quarantined_stage_sha256"] = \
                quarantined_sha
            stages[retired_name]["retirement_started_stage_sha256"] = \
                retirement_sha
            stages[retired_name][
                "retirement_overlay_baseline_stage_sha256"] = baseline_sha
            stages[retired_name][
                "retirement_pretruncate_open_stage_sha256"] = pretruncate_sha
            retired_sha = write_stage(retired_name)

            source_root_gates(external_receipt, gate_a, gate_c)
            external_receipt["source_quarantined_stage_sha256"] = \
                quarantined_sha
            external_receipt["retirement_started_stage_sha256"] = \
                retirement_sha
            external_receipt[
                "retirement_overlay_baseline_stage_sha256"] = baseline_sha
            external_receipt[
                "retirement_pretruncate_open_stage_sha256"] = pretruncate_sha
            external_receipt["source_deleted_stage_sha256"] = retired_sha
            external_receipt_path = manifests_root / receipt["external_receipt"]
            external_receipt_path.write_bytes(
                ARC.canonical_json(external_receipt))
            os.chmod(external_receipt_path, 0o600)
            external_receipt_sha = ARC.sha256_bytes(
                external_receipt_path.read_bytes())

            receipt_name = ARC.RECORD_NAMES["deletion-receipt"]
            source_root_gates(stages[receipt_name], gate_a, gate_c)
            stages[receipt_name]["source_quarantined_stage_sha256"] = \
                quarantined_sha
            stages[receipt_name]["retirement_started_stage_sha256"] = \
                retirement_sha
            stages[receipt_name][
                "retirement_overlay_baseline_stage_sha256"] = baseline_sha
            stages[receipt_name][
                "retirement_pretruncate_open_stage_sha256"] = pretruncate_sha
            stages[receipt_name]["source_deleted_stage_sha256"] = retired_sha
            stages[receipt_name]["external_receipt_sha256"] = \
                external_receipt_sha
            write_stage(receipt_name)

            retired = stages[retired_name]
            receipt = stages[receipt_name]
            stage_sha = tuple(
                (name, ARC.sha256_bytes((root / name).read_bytes()))
                for name in stage_names)
            found: dict[bytes, dict[str, object]] = {}
            for payload in (*stages.values(), external_manifest,
                            external_second, external_receipt):
                found.update(self.service.historical_gate_proofs_in(payload))
            gate_rows = tuple(sorted((
                (proof["receipt_name"], proof["receipt_sha256"],
                 proof["gate_ended_unix"], proof["gate_source_sha256"],
                 proof["gate_stamp_sha256"], proof["gate_log_sha256"])
                for proof in found.values()), key=lambda row: row[0]))
            destination = published["destination_manifest"]
            tombstone = retired["tombstone_manifest"]
            prepared = external_second["prepared_manifest"]
            prepared_ds_store = self.settings.retail_prepared_root / ".DS_Store"
            self.assertTrue(prepared_ds_store.is_file())
            prepared_ds_store_bytes = prepared_ds_store.read_bytes()
            prepared_ds_store.unlink()
            prepared_fd = ARC.open_absolute_directory(
                self.settings.retail_prepared_root,
                "test historical prepared overlay")
            try:
                prepared_current = ARC.manifest_bound(prepared_fd, "directory")
            finally:
                os.close(prepared_fd)
            record = stages[ARC.RECORD_NAMES["candidate"]]
            policy = ARC.HistoricalCompletedRetirementPolicy(
                901, transaction, record["candidate_id"], record["source"],
                tuple(record["source_identity"]),
                tuple(record["source_parent_identity"]),
                record["allowlist_version"], record["category"],
                record["data_class"], record["deletion_rule"],
                ARC.manifest_canonical_sha256(record["source_manifest"]),
                record["source_tree_sha256"], stage_sha, gate_rows,
                published["final_name"], tuple(published["final_identity"]),
                ARC.manifest_canonical_sha256(destination),
                destination["tree_sha256"], destination["files"],
                destination["directories"], destination["symlinks"],
                destination["logical_bytes"],
                stages[ARC.RECORD_NAMES["manifest-published"]][
                    "external_manifest"],
                stages[ARC.RECORD_NAMES["manifest-published"]][
                    "external_manifest_sha256"],
                second_stage["external_proof"],
                second_stage["external_proof_sha256"],
                second_stage["proof_hash"],
                tuple(external_second["prepared_root_identity"]),
                ARC.manifest_canonical_sha256(prepared),
                prepared["tree_sha256"], receipt["external_receipt"],
                receipt["external_receipt_sha256"],
                retired["tombstone_name"],
                tuple(retired["tombstone_identity"]),
                ARC.manifest_canonical_sha256(tombstone),
                tombstone["tree_sha256"], tombstone["files"],
                tombstone["directories"], tombstone["symlinks"],
                tombstone["logical_bytes"], tombstone["allocated_bytes"],
                ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
            old_rows = {row["path"]: row for row in prepared["entries"]}
            current_rows = {
                row["path"]: row for row in prepared_current["entries"]}
            removed = old_rows[".DS_Store"]
            unchanged_paths = sorted(set(current_rows) - {"."})
            prepared_overlay = ARC.HistoricalPreparedMetadataOverlayPolicy(
                902, transaction, record["candidate_id"],
                second_stage["external_proof"],
                second_stage["external_proof_sha256"],
                tuple(external_second["prepared_root_identity"]),
                ARC.manifest_canonical_sha256(prepared),
                prepared["tree_sha256"],
                ARC.manifest_canonical_sha256(prepared_current),
                prepared_current["tree_sha256"], ".DS_Store",
                ARC.sha256_bytes(ARC.canonical_json(removed)),
                removed["sha256"], removed["logical_bytes"],
                removed["allocated_bytes"], removed["flags"],
                tuple(sorted(removed["xattrs"].items())),
                ARC.sha256_bytes(ARC.canonical_json(old_rows["."])),
                ARC.sha256_bytes(ARC.canonical_json(current_rows["."])),
                ARC.sha256_bytes(ARC.canonical_json(
                    [old_rows[path] for path in unchanged_paths])),
                prepared["files"], prepared_current["files"],
                prepared["directories"], prepared["symlinks"],
                prepared_current["logical_bytes"] - prepared["logical_bytes"],
                prepared_current["allocated_bytes"]
                - prepared["allocated_bytes"],
                ARC.historical_completed_policy_authorization_sha256(policy))
            original_policy = self.service.historical_completed_policy
            original_prepared_policy = \
                self.service.historical_prepared_overlay_policy
            original_retail_verifier = \
                self.service.retail_import_prepared_verifier
            self.service.historical_completed_policy = lambda: policy
            self.service.historical_prepared_overlay_policy = \
                lambda completed: prepared_overlay
            retail_verifier_calls: list[tuple[int, int]] = []

            def hermetic_retail_verifier(descriptor: int) -> None:
                retail_verifier_calls.append(ARC.identity(os.fstat(descriptor)))
                self.assertEqual(
                    ARC.identity(os.fstat(descriptor)),
                    prepared_overlay.prepared_root_identity)
                self.assertEqual(
                    sorted(os.listdir(descriptor)), ["Documents", "manifest"])
                # Exercise the archive wrapper's unconditional cwd restoration.
                os.fchdir(descriptor)

            self.service.retail_import_prepared_verifier = \
                lambda: hermetic_retail_verifier
            self.platform.gate_hash = "f" * 64
            empty = ARC.sha256_bytes(b"")
            self.platform.current_gate_state = {
                "head": "9" * 40, "status_sha256": empty,
                "diff_binary_head_sha256": empty, "untracked": [],
            }
            current_receipt = self.write_gate_receipt(
                source_hash=self.platform.gate_hash,
                ended=self.platform.clock - 1,
                receipt_name="gate-2000000001-909-0.json")
            try:
                yield {
                    **fixture, "historical_policy": policy,
                    "historical_prepared_policy": prepared_overlay,
                    "historical_prepared_old": prepared,
                    "historical_prepared_current": prepared_current,
                    "historical_prepared_removed": removed,
                    "historical_prepared_removed_bytes":
                        prepared_ds_store_bytes,
                    "retail_verifier_calls": retail_verifier_calls,
                    "historical_stages": stages,
                    "external_manifest": external_manifest,
                    "external_manifest_path": external_manifest_path,
                    "external_second": external_second,
                    "external_receipt": external_receipt,
                    "historical_gate_proofs": (
                        transfer_gate, gate_a, gate_b, gate_c),
                    "current_receipt": current_receipt,
                    "destination_path": (
                        self.mount / ARC.ARCHIVE_ROOT /
                        ARC.CATEGORY_ROOTS[record["category"]] /
                        published["final_name"]),
                    "tombstone_path": (
                        self.settings.queue_root /
                        stages[ARC.RECORD_NAMES["delete-intent"]][
                            "quarantine_namespace"] /
                        retired["tombstone_name"]),
                }
            finally:
                self.service.historical_completed_policy = original_policy
                self.service.historical_prepared_overlay_policy = \
                    original_prepared_policy
                self.service.retail_import_prepared_verifier = \
                    original_retail_verifier

    def historical_readonly_snapshot(self) -> dict[str, bytes]:
        roots = {
            "queue": self.settings.queue_root,
            "archive": self.mount / ARC.ARCHIVE_ROOT,
            "prepared": self.settings.retail_prepared_root,
            "gates": self.settings.gate_log_root,
            "stamp": self.settings.full_gate_stamp.parent,
        }
        result: dict[str, bytes] = {}
        for label, root in roots.items():
            if not root.exists():
                continue
            descriptor = ARC.open_absolute_directory(
                root, f"historical read-only snapshot {label}")
            try:
                result[label] = ARC.canonical_json(
                    ARC.manifest_bound(descriptor, "directory"))
            finally:
                os.close(descriptor)
        return result

    def fork_crash_drain(self, transaction: str, hook: str, *,
                         before_boundary_failure: bool = False) -> None:
        if not hasattr(os, "fork"):
            self.skipTest("source-root crash mutation requires fork")
        pid = os.fork()
        if pid == 0:
            try:
                if before_boundary_failure:
                    self.service.hooks["before_final_retirement_boundary"] = (
                        lambda **_: (_ for _ in ()).throw(
                            ARC.ArchiveError("test pretruncate boundary failure")))
                self.service.hooks[hook] = lambda **_: os._exit(91)
                self.drain(transaction)
            except BaseException:
                os._exit(92)
            os._exit(93)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 91)
        self.service.hooks.clear()

    def fork_crash_after_truncate(self, transaction: str, index: int) -> None:
        if not hasattr(os, "fork"):
            self.skipTest("retirement truncate crash mutation requires fork")
        pid = os.fork()
        if pid == 0:
            try:
                def crash(*, index: int, **_: object) -> None:
                    if index == target:
                        os._exit(91)
                target = index
                self.service.hooks["after_retirement_truncate"] = crash
                self.drain(transaction)
            except BaseException:
                os._exit(92)
            os._exit(93)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 91)
        self.service.hooks.clear()

    # VOL-01..08
    def vol_01(self) -> None:
        self.mount.rename(self.base / "offline-volume")
        with self.assertRaises(ARC.DeferredVolume):
            self.service.bind_volume()

    def vol_02(self) -> None:
        assert self.platform.attrs is not None
        self.platform.attrs["uuid"] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaisesRegex(ARC.DeferredVolume, "attributes"):
            self.service.bind_volume()

    def vol_03(self) -> None:
        assert self.platform.attrs is not None
        self.platform.attrs["filesystem"] = "exfat"
        with self.assertRaisesRegex(ARC.DeferredVolume, "attributes"):
            self.service.bind_volume()

    def vol_04(self) -> None:
        assert self.platform.attrs is not None
        self.platform.attrs["mountpoint"] = "/Volumes/wrong"
        with self.assertRaisesRegex(ARC.DeferredVolume, "attributes"):
            self.service.bind_volume()

    def vol_05(self) -> None:
        assert self.platform.attrs is not None
        self.platform.attrs["name"] = "Wrong"
        with self.assertRaisesRegex(ARC.DeferredVolume, "attributes"):
            self.service.bind_volume()

    def vol_06(self) -> None:
        real = self.base / "real-volume"
        self.mount.rename(real)
        self.mount.symlink_to(real, target_is_directory=True)
        with self.assertRaises((OSError, ARC.ArchiveError)):
            self.service.bind_volume()

    def vol_07(self) -> None:
        transaction = self.enqueue()
        old_mount = self.base / "old-pinned-volume"
        replacement = self.mount
        swapped = {"done": False}
        def swap(**_: object) -> None:
            if not swapped["done"]:
                replacement.rename(old_mount)
                replacement.mkdir(mode=0o700)
                swapped["done"] = True
        self.service.hooks["after_copy_write"] = swap
        result = self.drain(transaction)
        self.assertEqual(result["status"], "DEFERRED_VOLUME")
        self.assertEqual(list(replacement.iterdir()), [])
        self.assertTrue(any(path.is_file() for path in old_mount.rglob("*")))
        self.assertTrue(self.source.exists())

    def vol_08(self) -> None:
        transaction = self.enqueue()
        self.service.hooks["after_copy_complete"] = lambda **_: (_ for _ in ()).throw(
            ARC.InjectedCrash("after_copy_complete"))
        with self.assertRaises(ARC.InjectedCrash):
            self.drain(transaction)
        old_mount = self.base / "old-volume"
        self.mount.rename(old_mount)
        self.mount.mkdir(mode=0o700)
        self.platform.mount = self.mount
        assert self.platform.attrs is not None
        self.platform.attrs["mountpoint"] = str(self.mount)
        with self.assertRaises((ARC.ArchiveError, FileNotFoundError)):
            self.drain(transaction)
        self.assertTrue(self.source.exists())

        fixed = self.mount / ARC.ARCHIVE_ROOT
        fixed.mkdir(mode=0o755)
        for leaf in (*ARC.CATEGORY_ROOTS.values(), ARC.MANIFEST_ROOT):
            (fixed / leaf).mkdir(mode=0o755)
        paths = [fixed, *(fixed / leaf for leaf in
                           (*ARC.CATEGORY_ROOTS.values(), ARC.MANIFEST_ROOT))]
        def snapshot() -> list[tuple[str, tuple[int, int], int, int, int, tuple[str, ...]]]:
            result = []
            for path in paths:
                info = os.stat(path, follow_symlinks=False)
                result.append((str(path), ARC.identity(info), stat.S_IMODE(info.st_mode),
                               info.st_uid, info.st_mtime_ns, tuple(sorted(os.listdir(path)))))
            return result
        before = snapshot()
        modes = self.service.preflight_policy_roots_readonly()
        after = snapshot()
        self.assertEqual(before, after)
        self.assertEqual(set(modes), {ARC.ARCHIVE_ROOT, *ARC.CATEGORY_ROOTS.values(),
                                      ARC.MANIFEST_ROOT})
        self.assertTrue(set(modes.values()) <= {0o700, 0o755})

    # SCOPE-01..08
    def scope_01(self) -> None:
        item = ARC.CandidateSpec("bad", Path("/Users/patryk/openxray/evidence"),
                                 "archives", "reproducible-noncritical", "delete-after-full-pass")
        self.settings.candidates = (item,)
        with self.assertRaisesRegex(ARC.ArchiveError, "repository"):
            self.service.spec("bad")

    def scope_02(self) -> None:
        path = self.base / "retail-prepared-123"
        path.mkdir()
        item = ARC.CandidateSpec("bad", path, "archives", "reproducible-noncritical",
                                 "delete-after-full-pass")
        self.settings.candidates = (item,)
        with self.assertRaisesRegex(ARC.ArchiveError, "protected"):
            self.service.spec("bad")

    def scope_03(self) -> None:
        path = self.base / "device-backup-123"
        path.mkdir()
        item = ARC.CandidateSpec("bad", path, "backups", "critical", "copy-only")
        self.settings.candidates = (item,)
        with self.assertRaisesRegex(ARC.ArchiveError, "protected"):
            self.service.spec("bad")

    def scope_04(self) -> None:
        path = self.base / "simulator-work-123"
        path.mkdir()
        item = ARC.CandidateSpec("bad", path, "completed-evidence",
                                 "reproducible-noncritical", "delete-after-full-pass")
        self.settings.candidates = (item,)
        with self.assertRaisesRegex(ARC.ArchiveError, "protected"):
            self.service.spec("bad")

    def scope_05(self) -> None:
        path = self.base / "Documents"
        path.mkdir()
        item = ARC.CandidateSpec("bad", path, "backups", "critical", "copy-only")
        self.settings.candidates = (item,)
        with self.assertRaisesRegex(ARC.ArchiveError, "protected"):
            self.service.spec("bad")

    def scope_06(self) -> None:
        with self.assertRaisesRegex(ARC.ArchiveError, "allowlist"):
            self.service.spec("unreviewed")
        build = self.base / "build"
        build.mkdir()
        item = ARC.CandidateSpec("build", build, "archives", "reproducible-noncritical",
                                 "delete-after-full-pass")
        self.settings.candidates = (item,)
        with self.assertRaisesRegex(ARC.ArchiveError, "protected"):
            self.service.spec("build")
        self.settings.candidates = (self.candidate,)
        nested_build = self.source / "build"
        nested_build.mkdir()
        (nested_build / "object.o").write_bytes(b"reproducible")
        with self.assertRaisesRegex(ARC.ArchiveError, "protected data/build"):
            self.enqueue()

    def scope_07(self) -> None:
        real_parent = self.base / "real-parent"
        real_parent.mkdir()
        child = real_parent / "child"
        child.mkdir()
        link_parent = self.base / "linked-parent"
        link_parent.symlink_to(real_parent, target_is_directory=True)
        item = ARC.CandidateSpec("bad", link_parent / "child", "archives",
                                 "reproducible-noncritical", "delete-after-full-pass")
        self.settings.candidates = (item,)
        with self.assertRaises(OSError):
            self.service.bind_source(item)

    def scope_08(self) -> None:
        expected = {
            "/Users/patryk/openxray-backups",
            "/Users/patryk/openxray-handoff/simulator-autoload-20260808-extended.log",
            "/Users/patryk/openxray-handoff/simulator-autoload-20260809-postreview.log",
            "/Users/patryk/openxray-handoff/uiscene-full-gate-20260809-141312-36282.log",
            "/Users/patryk/openxray-handoff/uiscene-runtime-26.5-rerun-20260809-135905-18424.log",
            "/Users/patryk/openxray-handoff/uiscene-runtime-27.0-rerun-20260809-140604-26901.log",
            str(ARC.RETAIL_BACKUP_PATH),
            str(ARC.RETAIL_BACKUP_MANIFEST_PATH),
        }
        self.assertEqual({str(item.source) for item in ARC.INITIAL_CANDIDATES}, expected)
        self.assertTrue(ARC.Settings().review_authorized)
        self.assertTrue(ARC.PRODUCTION_REVIEW_AUTHORIZED)
        self.assertEqual(ARC.REVIEW_POLICY_VERSION, 1)
        self.assertEqual(ARC.ALLOWLIST_VERSION, 2)
        with self.assertRaisesRegex(ARC.ArchiveError, "cannot be overridden"):
            ARC.ArchiveService(ARC.Settings(mount=self.base / "replacement"))
        injected = ARC.CandidateSpec(
            ARC.RETAIL_BACKUP_CANDIDATE.ident, ARC.RETAIL_BACKUP_PATH, "backups",
            "redundant", "requires-fixed-prepared-proof")
        with self.assertRaisesRegex(ARC.ArchiveError, "protected"):
            ARC.ArchiveService().validate_candidate(injected)

    # COPY-01..10
    def copy_01(self) -> None:
        transaction = self.enqueue()
        result = self.drain(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual((self.final_path(transaction) / "report.txt").read_text(),
                         "closed evidence\n")

    def copy_02(self) -> None:
        transaction = self.enqueue()
        self.drain(transaction)
        self.assertEqual((self.final_path(transaction) / "nested/trace.bin").read_bytes(),
                         b"trace\0payload")

    def copy_03(self) -> None:
        source_link = self.source / "trace-link"
        source_link.symlink_to("nested/trace.bin")
        subprocess.run(("xattr", "-w", "-s", "com.openxray.link-test",
                        "link-metadata", str(source_link)), check=True)
        transaction = self.enqueue()
        self.drain(transaction)
        link = self.final_path(transaction) / "trace-link"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "nested/trace.bin")
        link_fd = os.open(link, os.O_RDONLY | ARC.O_SYMLINK)
        try:
            self.assertIn("com.openxray.link-test", ARC.xattrs_fd(link_fd))
        finally:
            os.close(link_fd)

    def copy_04(self) -> None:
        target = self.source / "report.txt"
        subprocess.run(("chmod", "+a", "everyone deny delete", str(target)), check=True)
        source_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            source_acl = ARC.acl_text_fd(source_fd)
        finally:
            os.close(source_fd)
        self.assertTrue(source_acl)
        transaction = self.enqueue()
        self.drain(transaction)
        destination_fd = os.open(self.final_path(transaction) / "report.txt",
                                 os.O_RDONLY | os.O_NOFOLLOW)
        try:
            self.assertEqual(ARC.acl_text_fd(destination_fd), source_acl)
        finally:
            os.close(destination_fd)
            subprocess.run(("chmod", "-N", str(self.final_path(transaction) / "report.txt")),
                           check=True)

    def copy_05(self) -> None:
        target = self.source / "report.txt"
        subprocess.run(("xattr", "-w", "com.openxray.archive-test", "metadata", str(target)),
                       check=True)
        transaction = self.enqueue()
        self.drain(transaction)
        source_fd = os.open(self.final_path(transaction) / "report.txt", os.O_RDONLY)
        try:
            self.assertIn("com.openxray.archive-test", ARC.xattrs_fd(source_fd))
        finally:
            os.close(source_fd)

    def copy_06(self) -> None:
        os.mkfifo(self.source / "blocked.fifo")
        with self.assertRaisesRegex(ARC.ArchiveError, "special"):
            self.enqueue()

    def copy_07(self) -> None:
        original = ARC.os.stat
        def altered(*args, **kwargs):  # type: ignore[no-untyped-def]
            value = original(*args, **kwargs)
            if args and args[0] == "report.txt" and kwargs.get("dir_fd") is not None:
                raw = list(value)
                raw[2] = value.st_dev + 1
                return os.stat_result(raw)
            return value
        with mock.patch.object(ARC.os, "stat", side_effect=altered):
            with self.assertRaisesRegex(ARC.ArchiveError, "cross-device"):
                self.enqueue()

    def copy_08(self) -> None:
        transaction = self.enqueue()
        category = self.external_category()
        collision = category / f"{transaction}-{self.source.name}"
        collision.mkdir()
        with self.assertRaisesRegex(ARC.ArchiveError, "collision|destination exists"):
            self.drain(transaction)
        self.assertTrue(self.source.exists())

    def copy_09(self) -> None:
        published_race = self.enqueue()
        def mutate_published(**_: object) -> None:
            (self.final_path(published_race) / "report.txt").write_text("foreign publication\n")
        self.service.hooks["after_manifest_record"] = mutate_published
        with self.assertRaisesRegex(ARC.ArchiveError, "published destination"):
            self.drain(published_race)
        self.assertTrue(self.source.exists())
        self.service.hooks.clear()

        source_race = self.enqueue()
        self.service.hooks["before_source_quarantine"] = lambda **_: (
            self.source / "report.txt").write_text("mutated after final source bind\n")
        with self.assertRaisesRegex(ARC.ArchiveError, "quarantined source"):
            self.drain(source_race)
        self.assertTrue(self.source.exists())
        self.assertEqual((self.source / "report.txt").read_text(),
                         "mutated after final source bind\n")

    def copy_10(self) -> None:
        sparse = self.source / "sparse.bin"
        sparse_fd = os.open(sparse, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.ftruncate(sparse_fd, 8 * 1024 * 1024 + 4)
            os.pwrite(sparse_fd, b"head", 0)
            os.pwrite(sparse_fd, b"tail", 8 * 1024 * 1024)
        finally:
            os.close(sparse_fd)
        source_fd = ARC.open_absolute_directory(self.source, "source")
        try:
            source_manifest = ARC.manifest_bound(source_fd, "directory")
        finally:
            os.close(source_fd)
        dense = self.base / "dense-copy"
        def dense_copy(source: str, destination: str) -> str:
            with open(source, "rb") as input_file, open(destination, "wb") as output_file:
                while block := input_file.read(1024 * 1024):
                    output_file.write(block)
            shutil.copystat(source, destination, follow_symlinks=False)
            return destination
        shutil.copytree(self.source, dense, copy_function=dense_copy)
        dense_fd = ARC.open_absolute_directory(dense, "dense copy")
        original_allocated = ARC.allocated_bytes
        try:
            # APFS may transparently materialize/clone sparse extents.  Inject a
            # different st_blocks view while independently regenerating the
            # dense tree manifest from real files and descriptors.
            with mock.patch.object(
                    ARC, "allocated_bytes",
                    side_effect=lambda info: original_allocated(info) + 4096):
                dense_manifest = ARC.manifest_bound(dense_fd, "directory")
        finally:
            os.close(dense_fd)
        self.assertNotEqual(source_manifest["allocated_bytes"], dense_manifest["allocated_bytes"])
        self.assertTrue(ARC.manifests_equal(source_manifest, dense_manifest))
        self.assertEqual(source_manifest["tree_sha256"], dense_manifest["tree_sha256"])

    # REC-01..08
    def rec_01(self) -> None:
        transaction, _ = self.crash_then_resume("after_copy_started")
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["deletion-receipt"]).is_file())

    def rec_02(self) -> None:
        transaction = self.enqueue()
        calls = {"count": 0}
        def crash_partial_copy(**_: object) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise ARC.InjectedCrash("partial-copy")
        self.service.hooks["after_copy_write"] = crash_partial_copy
        with self.assertRaisesRegex(ARC.InjectedCrash, "partial-copy"):
            self.drain(transaction)
        self.service.hooks.clear()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        started = json.loads((self.settings.queue_root / transaction /
                              ARC.RECORD_NAMES["copy-started"]).read_text())
        stage = self.external_category() / started["stage_name"]
        residues = [path for path in stage.iterdir()
                    if ARC.COPY_RESIDUE_RE.fullmatch(path.name)]
        self.assertEqual(len(residues), 1)
        residue_fd = ARC.open_absolute_directory(residues[0], "copy residue")
        try:
            self.assertEqual(ARC.manifest_bound(residue_fd, "directory")["logical_bytes"], 0)
        finally:
            os.close(residue_fd)
        self.assertTrue(self.final_path(transaction).exists())

    def rec_03(self) -> None:
        transaction, _ = self.crash_then_resume("after_copy_complete")
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["verified"]).is_file())

    def rec_04(self) -> None:
        transaction, _ = self.crash_then_resume("after_verified")
        self.assertTrue(self.final_path(transaction).exists())

    def rec_05(self) -> None:
        transaction = self.enqueue()
        for checkpoint in ("after_publish_rename", "after_published_record"):
            self.service.hooks.clear()
            self.service.hooks[checkpoint] = lambda checkpoint=checkpoint, **_: (
                _ for _ in ()).throw(ARC.InjectedCrash(checkpoint))
            with self.assertRaisesRegex(ARC.InjectedCrash, checkpoint):
                self.drain(transaction)
        self.service.hooks.clear()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertEqual(self.service.verify_published(transaction)["status"], "PASS")

    def rec_06(self) -> None:
        transaction = self.enqueue()
        for checkpoint in ("after_external_manifest", "after_manifest_record"):
            self.service.hooks.clear()
            self.service.hooks[checkpoint] = lambda checkpoint=checkpoint, **_: (
                _ for _ in ()).throw(ARC.InjectedCrash(checkpoint))
            with self.assertRaisesRegex(ARC.InjectedCrash, checkpoint):
                self.drain(transaction)
        self.service.hooks.clear()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertTrue((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                         f"{transaction}.json").is_file())

    def rec_07(self) -> None:
        transaction = self.enqueue()
        for checkpoint in ("after_source_quarantine_rename",
                           "after_source_quarantined_record",
                           "after_final_retire_rebind"):
            self.service.hooks.clear()
            self.service.hooks[checkpoint] = lambda checkpoint=checkpoint, **_: (
                _ for _ in ()).throw(ARC.InjectedCrash(checkpoint))
            with self.assertRaisesRegex(ARC.InjectedCrash, checkpoint):
                self.drain(transaction)
        self.service.hooks.clear()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertFalse(self.source.exists())
        self.assertTrue((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                         f"{transaction}-source-deletion.json").is_file())

    def rec_08(self) -> None:
        transaction = self.enqueue()
        source_deleted_name = ARC.RECORD_NAMES["source-deleted"]
        def exit_during_record(name: str, **_: object) -> None:
            if name == source_deleted_name:
                os._exit(91)
        self.service.hooks["after_partial_record_fsync"] = exit_during_record
        child = os.fork()
        if child == 0:
            self.drain(transaction)
            os._exit(90)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 91)
        self.service.hooks.clear()
        self.service.hooks["after_source_deleted_record"] = lambda **_: (
            _ for _ in ()).throw(ARC.InjectedCrash("deleted-before-receipt"))
        with self.assertRaisesRegex(ARC.InjectedCrash, "deleted-before-receipt"):
            self.drain(transaction)
        self.service.hooks.clear()
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["source-deleted"]).is_file())
        self.assertFalse((self.settings.queue_root / transaction /
                          ARC.RECORD_NAMES["deletion-receipt"]).exists())
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertEqual(self.service.verify_published(transaction)["status"], "PASS")
        residue = self.settings.queue_root / transaction / ARC.RECORD_RESIDUE
        partials = [name for name in os.listdir(residue)
                    if name.startswith(f".partial-{source_deleted_name[:-5]}-")]
        self.assertEqual(len(partials), 1)
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["deletion-receipt"]).is_file())

    # DEL-01..05
    def del_01(self) -> None:
        transaction = self.enqueue()
        result = self.drain(transaction)
        self.assertEqual(result["source"], "RETIRED")
        self.assertFalse(self.source.exists())
        manifest_root = self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT
        transfer = manifest_root / f"{transaction}.json"
        receipt = manifest_root / f"{transaction}-source-deletion.json"
        transfer_payload = json.loads(transfer.read_text())
        receipt_payload = json.loads(receipt.read_text())
        self.assertEqual(transfer_payload["category"], "completed-evidence")
        self.assertEqual(receipt_payload["category"], "completed-evidence")
        self.assertEqual(receipt_payload["owned_payload"], "ZEROED_TOMBSTONE")
        self.assertEqual(receipt_payload["foreign_entries_removed"], 0)
        self.assertGreater(result["reclaimed_logical_bytes"], 0)

    def del_02(self) -> None:
        critical = ARC.CandidateSpec("critical", self.source, "backups", "critical", "copy-only")
        self.settings.candidates = (critical,)
        transaction = self.service.enqueue("critical")["transaction_id"]
        result = self.drain(transaction)
        self.assertEqual(result["source"], "PRESERVED")
        self.assertTrue(self.source.exists())

    def del_03(self) -> None:
        redundant = ARC.CandidateSpec("redundant", self.source, "backups", "redundant",
                                      "requires-second-copy-proof")
        self.settings.candidates = (redundant,)
        transaction = self.service.enqueue("redundant")["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "second-copy"):
            self.drain(transaction)
        self.assertTrue(self.source.exists())

    def del_04(self) -> None:
        transaction = self.enqueue()
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "foreign.txt").write_text("foreign\n")
        displaced = self.base / "expected-displaced"
        def swap(source: ARC.SourceBinding, **_: object) -> None:
            os.rename(self.source, displaced)
            os.rename(foreign, self.source)
        self.service.hooks["before_source_quarantine"] = swap
        with self.assertRaisesRegex(ARC.ArchiveError, "foreign"):
            self.drain(transaction)
        self.assertEqual((self.source / "foreign.txt").read_text(), "foreign\n")
        self.assertEqual((displaced / "report.txt").read_text(), "closed evidence\n")

    def del_05(self) -> None:
        transaction = self.enqueue()
        held: dict[str, Path] = {}
        def swap(quarantine_fd: int, intent: dict[str, object],
                 owned_name: str, **_: object) -> None:
            namespace = self.settings.queue_root / str(intent["quarantine_namespace"])
            late = str(intent["late_owned_name"])
            os.rename(owned_name, late, src_dir_fd=quarantine_fd,
                      dst_dir_fd=quarantine_fd)
            seed = ".foreign-seed"
            os.mkdir(seed, 0o700, dir_fd=quarantine_fd)
            seed_fd = os.open(seed, ARC.directory_open_flags(), dir_fd=quarantine_fd)
            try:
                foreign_fd = os.open("foreign.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     0o600, dir_fd=seed_fd)
                try:
                    os.write(foreign_fd, b"foreign\n")
                finally:
                    os.close(foreign_fd)
            finally:
                os.close(seed_fd)
            os.rename(seed, owned_name, src_dir_fd=quarantine_fd,
                      dst_dir_fd=quarantine_fd)
            held["expected"] = namespace / late
            held["foreign"] = namespace / owned_name
        self.service.hooks["after_final_retire_rebind"] = swap
        result = self.drain(transaction)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual((held["expected"] / "report.txt").stat().st_size, 0)
        self.assertEqual((held["expected"] / "nested/trace.bin").stat().st_size, 0)
        self.assertEqual((held["foreign"] / "foreign.txt").read_text(), "foreign\n")
        receipt = json.loads((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                              f"{transaction}-source-deletion.json").read_text())
        self.assertEqual(receipt["foreign_entries_removed"], 0)
        self.assertEqual(receipt["tombstone_name"], held["expected"].name)
        self.assertNotEqual(receipt["tombstone_identity"],
                            list(ARC.identity(held["foreign"].stat())))

    # RED-01..09: exact retail-backup second-copy and sibling-order contract
    def red_01(self) -> None:
        main, _, prepared = self.configure_retail("missing")
        source_snapshot = self.tree_snapshot(main.source)
        (prepared / ARC.SECOND_COPY_MAPPINGS[0][1]).unlink()
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaises((ARC.ArchiveError, FileNotFoundError)):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, source_snapshot)

        main, _, prepared = self.configure_retail("changed")
        source_snapshot = self.tree_snapshot(main.source)
        (prepared / ARC.SECOND_COPY_MAPPINGS[1][1]).write_bytes(b"changed prepared data\n")
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "differs"):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, source_snapshot)

    def red_02(self) -> None:
        main, _, _ = self.configure_retail("extra-save")
        self.write_tree_file(
            main.source, "_appdata_/savedgames/foreign.scop", b"second save\n")
        source_snapshot = self.tree_snapshot(main.source)
        with self.assertRaisesRegex(ARC.ArchiveError, "second save"):
            self.service.enqueue(main.ident)
        self.assert_tree_preserved(main.source, source_snapshot)

        main, _, prepared = self.configure_retail("extra-db")
        self.write_tree_file(prepared, "Documents/resources/unreviewed.db9", b"unknown db\n")
        source_snapshot = self.tree_snapshot(main.source)
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "unknown database/archive"):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, source_snapshot)

    def red_03(self) -> None:
        main, _, prepared = self.configure_retail("lying-tsv")
        (prepared / "manifest/files.tsv").write_text(
            "THIS TABLE LIES\t0\tdeadbeef\n", encoding="utf-8")
        (prepared / "manifest/required-archives.tsv").write_text(
            "no-required-files\n", encoding="utf-8")
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        result = self.drain(transaction)
        self.assertEqual(result["status"], "PASS")
        proof = json.loads((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                            f"{transaction}-second-copy-proof.json").read_text())
        self.assertEqual(len(proof["mappings"]), 13)
        self.assertEqual({item["source_path"] for item in proof["mappings"]},
                         ARC.MAPPED_SOURCE_PATHS)

    def red_04(self) -> None:
        main, _, _ = self.configure_retail("replacement-race")
        source_snapshot = self.tree_snapshot(main.source)
        state = {"swapped": False}
        foreign = b"foreign replacement\n"
        def replace_bound_prepared(index: int, prepared_binding: ARC.RelativeFileBinding,
                                   **_: object) -> None:
            if index != 0 or state["swapped"]:
                return
            held = ".held-reviewed-mapping"
            os.rename(prepared_binding.name, held,
                      src_dir_fd=prepared_binding.parent_fd,
                      dst_dir_fd=prepared_binding.parent_fd)
            descriptor = os.open(
                prepared_binding.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=prepared_binding.parent_fd)
            try:
                os.write(descriptor, foreign)
            finally:
                os.close(descriptor)
            state["swapped"] = True
        self.service.hooks["after_second_copy_file_bind"] = replace_bound_prepared
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "changed while proving"):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, source_snapshot)
        prepared_path = self.settings.retail_prepared_root / ARC.SECOND_COPY_MAPPINGS[0][1]
        self.assertEqual(prepared_path.read_bytes(), foreign)

    def red_05(self) -> None:
        main, _, prepared = self.configure_retail("late-prepared-mutation")
        source_snapshot = self.tree_snapshot(main.source)
        target = prepared / ARC.SECOND_COPY_MAPPINGS[2][1]
        self.service.hooks["before_retirement_second_copy_proof"] = lambda **_: (
            target.write_bytes(b"mutated after immutable proof\n"))
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "differs"):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, source_snapshot)
        quarantined = json.loads((self.settings.queue_root / transaction /
                                  ARC.RECORD_NAMES["source-quarantined"]).read_text())
        namespace = self.settings.queue_root / quarantined["quarantine_namespace"]
        self.assertFalse((namespace / quarantined["quarantine_name"]).exists())

    def red_06(self) -> None:
        main, _, _ = self.configure_retail("proof-short-write")
        source_snapshot = self.tree_snapshot(main.source)
        armed = {"value": True}
        def short_write(**_: object) -> None:
            if not armed["value"]:
                return
            armed["value"] = False
            self.platform.write_limit = 5
            self.platform.fail_write_after = self.platform.written + 5
        self.service.hooks["before_second_copy_proof_publish"] = short_write
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaises(OSError):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, source_snapshot)
        self.service.hooks.clear()
        self.platform.write_limit = None
        self.platform.fail_write_after = None
        self.assertEqual(self.drain(transaction)["status"], "PASS")

    def red_07(self) -> None:
        main, _, _ = self.configure_retail("proof-crash")
        source_snapshot = self.tree_snapshot(main.source)
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        proof_name = f"{transaction}-second-copy-proof.json"
        def crash_proof(name: str, **_: object) -> None:
            if name == proof_name:
                os._exit(92)
        self.service.hooks["after_partial_record_fsync"] = crash_proof
        child = os.fork()
        if child == 0:
            self.drain(transaction)
            os._exit(90)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 92)
        self.assert_tree_preserved(main.source, source_snapshot)
        self.service.hooks.clear()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        residue = self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT / ARC.RECORD_RESIDUE
        self.assertTrue(any(name.startswith(f".partial-{proof_name[:-5]}-")
                            for name in os.listdir(residue)))

    def red_08(self) -> None:
        main, _, _ = self.configure_retail("unknown-path")
        self.write_tree_file(main.source, "notes/random.dat", b"unreviewed\n")
        source_snapshot = self.tree_snapshot(main.source)
        with self.assertRaisesRegex(ARC.ArchiveError, "not reviewed"):
            self.service.enqueue(main.ident)
        self.assert_tree_preserved(main.source, source_snapshot)

    def red_09(self) -> None:
        main, sibling, _ = self.configure_retail("sibling-order")
        sibling_snapshot = self.tree_snapshot(sibling.source)
        sibling_transaction = self.service.enqueue(sibling.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "completed main backup"):
            self.drain(sibling_transaction)
        self.assert_tree_preserved(sibling.source, sibling_snapshot)

        main_transaction = self.service.enqueue(main.ident)["transaction_id"]
        self.assertEqual(self.drain(main_transaction)["status"], "PASS")
        self.assertEqual(self.service.verify_published(main_transaction)["status"], "PASS")
        self.assertEqual(self.drain(sibling_transaction)["status"], "PASS")
        proof = json.loads((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                            f"{sibling_transaction}-second-copy-proof.json").read_text())
        self.assertEqual(proof["main_transaction_id"], main_transaction)
        self.assertIn("main_deletion_receipt_sha256", proof)
        self.assertFalse(sibling.source.exists())

    # SOL-01..10: final xhigh identity, durability and live-boundary findings
    def sol_01(self) -> None:
        transaction = self.enqueue()
        original_outside = self.base / "original-child-outside.bin"
        foreign = b"foreign child replacement\n"
        def replace_child(pinned_fd: int, **_: object) -> None:
            nested_fd = os.open("nested", ARC.directory_open_flags(), dir_fd=pinned_fd)
            try:
                os.rename("trace.bin", original_outside, src_dir_fd=nested_fd)
                descriptor = os.open(
                    "trace.bin", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=nested_fd)
                try:
                    os.write(descriptor, foreign)
                finally:
                    os.close(descriptor)
            finally:
                os.close(nested_fd)
        self.service.hooks["after_final_retire_rebind"] = replace_child
        with self.assertRaisesRegex(ARC.ArchiveError, "child identity changed"):
            self.drain(transaction)
        # The immutable child identity no longer exists in the owned tree, so
        # the latest reviewed policy retains the whole quarantine fail-closed
        # instead of republishing a non-canonical public source.
        self.assertFalse(self.source.exists())
        self.assertEqual(original_outside.read_bytes(), b"trace\0payload")
        candidate = json.loads((self.settings.queue_root / transaction /
                                ARC.RECORD_NAMES["candidate"]).read_text())
        intent = json.loads((self.settings.queue_root / transaction /
                             ARC.RECORD_NAMES["delete-intent"]).read_text())
        quarantine = (self.settings.queue_root / intent["quarantine_namespace"])
        owned = next(path for path in quarantine.iterdir() if path.is_dir())
        self.assertEqual((owned / "nested/trace.bin").read_bytes(), foreign)
        identities = {row["path"]: row["local_identity"]
                      for row in candidate["source_manifest"]["entries"]}
        self.assertNotEqual(
            identities["nested/trace.bin"],
            list(ARC.identity((owned / "nested/trace.bin").stat())))

    def sol_02(self) -> None:
        outside = self.base / "outside-hardlink.txt"
        os.link(self.source / "report.txt", outside)
        with self.assertRaisesRegex(ARC.ArchiveError, "external hard link"):
            self.enqueue()
        self.assertEqual(outside.read_text(), "closed evidence\n")
        self.assertEqual((self.source / "report.txt").read_text(), "closed evidence\n")

        regular = self.base / "regular-root.log"
        regular.write_bytes(b"regular root payload\n")
        regular_outside = self.base / "regular-root-outside.log"
        os.link(regular, regular_outside)
        candidate = ARC.CandidateSpec(
            "hardlinked-root", regular, "completed-evidence",
            "reproducible-noncritical", "delete-after-full-pass")
        self.settings.candidates = (candidate,)
        with self.assertRaisesRegex(ARC.ArchiveError, "manifest root.*hard link"):
            self.service.enqueue(candidate.ident)
        self.assertEqual(regular_outside.read_bytes(), b"regular root payload\n")

    def sol_03(self) -> None:
        outside = self.base / "hash-window-hardlink.txt"
        armed = {"value": True}
        def link_during_hash(path: str, parent_fd: int | None,
                             name: str | None, **_: object) -> None:
            if path != "report.txt" or not armed["value"]:
                return
            assert parent_fd is not None and name is not None
            os.link(name, outside, src_dir_fd=parent_fd)
            armed["value"] = False
        self.service.hooks["during_source_manifest_hash"] = link_during_hash
        with self.assertRaisesRegex(ARC.ArchiveError, "gained a hard link while hashing"):
            self.enqueue()
        self.assertEqual(outside.read_text(), "closed evidence\n")
        self.assertEqual((self.source / "report.txt").read_text(), "closed evidence\n")

    def sol_04(self) -> None:
        transaction = self.enqueue()
        outside = self.base / "final-fstat-child-hardlink.bin"
        armed = {"value": True}
        def link_before_boundary(path: str, parent_fd: int | None,
                                 name: str | None, **_: object) -> None:
            if path != "nested/trace.bin" or not armed["value"]:
                return
            assert parent_fd is not None and name is not None
            os.link(name, outside, src_dir_fd=parent_fd)
            armed["value"] = False
        self.service.hooks["before_final_retirement_boundary"] = link_before_boundary
        with self.assertRaisesRegex(ARC.ArchiveError, "hard link before truncate"):
            self.drain(transaction)
        self.assertEqual(outside.read_bytes(), b"trace\0payload")
        intent = json.loads((self.settings.queue_root / transaction /
                             ARC.RECORD_NAMES["delete-intent"]).read_text())
        retained = (self.settings.queue_root / intent["quarantine_namespace"] /
                    intent["retired_name"] / "nested/trace.bin")
        self.assertEqual(retained.read_bytes(), b"trace\0payload")

        regular = self.base / "final-fstat-root.log"
        regular.write_bytes(b"root survives\n")
        candidate = ARC.CandidateSpec(
            "pretruncate-root", regular, "completed-evidence",
            "reproducible-noncritical", "delete-after-full-pass")
        self.settings.candidates = (candidate,)
        self.settings.test_retail_roles = ()
        self.service.hooks.clear()
        root_transaction = self.service.enqueue(candidate.ident)["transaction_id"]
        root_outside = self.base / "final-fstat-root-hardlink.log"
        def link_root(path: str, parent_fd: int | None,
                      name: str | None, **_: object) -> None:
            if path == "." and not root_outside.exists():
                assert parent_fd is not None and name is not None
                os.link(name, root_outside, src_dir_fd=parent_fd)
        self.service.hooks["before_final_retirement_boundary"] = link_root
        with self.assertRaisesRegex(ARC.ArchiveError, "hard link before truncate"):
            self.drain(root_transaction)
        self.assertEqual(root_outside.read_bytes(), b"root survives\n")
        root_intent = json.loads((self.settings.queue_root / root_transaction /
                                  ARC.RECORD_NAMES["delete-intent"]).read_text())
        retained_root = (self.settings.queue_root / root_intent["quarantine_namespace"] /
                         root_intent["retired_name"])
        self.assertEqual(retained_root.read_bytes(), b"root survives\n")

        implementation = Path(ARC.__file__).read_text(encoding="utf-8")
        self.assertNotIn("after_final_retirement_boundary", implementation)
        boundary = implementation.index("        final_retirement_boundary()")
        final_fstat = implementation.index("            final = os.fstat(first.fd)", boundary)
        first_truncate = implementation.index("            os.ftruncate(first.fd, 0)", final_fstat)
        between = implementation[final_fstat:first_truncate]
        self.assertNotIn("require_volume", between)
        self.assertNotIn("self.hook", between)

    def sol_05(self) -> None:
        transaction = self.enqueue()
        archived = self.final_path(transaction) / "report.txt"
        pristine: dict[str, object] = {}
        def corrupt_archive(**_: object) -> None:
            if not pristine:
                pristine["bytes"] = archived.read_bytes()
                pristine["stat"] = archived.stat()
            archived.write_bytes(b"corrupt archive\n")
        self.service.hooks["before_final_retirement_boundary"] = corrupt_archive
        for attempt in range(2):
            with self.assertRaisesRegex(ARC.ArchiveError, "published destination"):
                self.drain(transaction)
            self.assertEqual((self.source / "report.txt").read_text(), "closed evidence\n")
            self.assertTrue((self.settings.queue_root / transaction /
                             ARC.RECORD_NAMES["retirement-started"]).is_file())
            self.assertFalse((self.settings.queue_root / transaction /
                              ARC.RECORD_NAMES["source-deleted"]).exists())
            if attempt == 0:
                archived.write_bytes(pristine["bytes"])
                pristine_stat = pristine["stat"]
                assert isinstance(pristine_stat, os.stat_result)
                os.utime(archived, ns=(pristine_stat.st_atime_ns,
                                       pristine_stat.st_mtime_ns))
        self.assertEqual(archived.read_bytes(), b"corrupt archive\n")

    def sol_06(self) -> None:
        main, _, prepared = self.configure_retail("after-retirement-started")
        snapshot = self.tree_snapshot(main.source)
        mapped = prepared / ARC.SECOND_COPY_MAPPINGS[0][1]
        self.service.hooks["before_final_retirement_boundary"] = lambda **_: (
            mapped.write_bytes(b"changed after durable retirement start\n"))
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "prepared retail manifest changed"):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, snapshot)
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["retirement-started"]).is_file())
        self.assertFalse((self.settings.queue_root / transaction /
                          ARC.RECORD_NAMES["source-deleted"]).exists())

    def sol_07(self) -> None:
        main, _, prepared = self.configure_retail("retirement-crash")
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        def exit_after_zeroing(**_: object) -> None:
            os._exit(94)
        self.service.hooks["after_source_retire_before_record"] = exit_after_zeroing
        child = os.fork()
        if child == 0:
            self.drain(transaction)
            os._exit(90)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 94)
        self.service.hooks.clear()
        transaction_root = self.settings.queue_root / transaction
        self.assertTrue((transaction_root / ARC.RECORD_NAMES["retirement-started"]).is_file())
        self.assertFalse((transaction_root / ARC.RECORD_NAMES["source-deleted"]).exists())
        transaction_fd = ARC.open_absolute_directory(transaction_root, "retirement crash tx")
        try:
            self.service.validate_transaction_residue(transaction_fd)
        finally:
            os.close(transaction_fd)
        prepared_target = prepared / ARC.SECOND_COPY_MAPPINGS[0][1]
        original_prepared = prepared_target.read_bytes()
        original_prepared_stat = prepared_target.stat()
        prepared_target.write_bytes(b"invalid recovery proof\n")
        with self.assertRaisesRegex(ARC.ArchiveError, "prepared retail manifest changed"):
            self.drain(transaction)
        self.assertFalse(main.source.exists())
        self.assertFalse((transaction_root / ARC.RECORD_NAMES["source-deleted"]).exists())
        prepared_target.write_bytes(original_prepared)
        os.utime(prepared_target, ns=(original_prepared_stat.st_atime_ns,
                                      original_prepared_stat.st_mtime_ns))
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertTrue((transaction_root / ARC.RECORD_NAMES["deletion-receipt"]).is_file())

    def sol_08(self) -> None:
        self.assertEqual(self.service.matching_full_gate_receipt()["gate"], "full")
        original = json.loads(json.dumps(self.platform.current_gate_state))
        for key, value in (
                ("head", "2" * 40),
                ("status_sha256", "3" * 64),
                ("diff_binary_head_sha256", "4" * 64),
                ("untracked", [{"path": "new.txt", "sha256": "5" * 64}])):
            self.platform.current_gate_state = json.loads(json.dumps(original))
            self.platform.current_gate_state[key] = value
            with self.assertRaisesRegex(ARC.ArchiveError, "no fresh matching"):
                self.service.matching_full_gate_receipt()
        self.platform.current_gate_state = original
        self.assertEqual(self.service.matching_full_gate_receipt()["gate"], "full")

        repo = self.base / "state-repo"
        repo.mkdir()
        subprocess.run(("git", "init", "-q", str(repo)), check=True)
        subprocess.run(("git", "-C", str(repo), "config", "user.email", "test@example.invalid"), check=True)
        subprocess.run(("git", "-C", str(repo), "config", "user.name", "Archive Test"), check=True)
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(("git", "-C", str(repo), "add", "tracked.txt"), check=True)
        subprocess.run(("git", "-C", str(repo), "commit", "-qm", "state"), check=True)
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            "run_gate_logged_contract", Path(__file__).with_name("run_gate_logged.py"))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)
        settings = ARC.Settings(production=False, repo_root=repo)
        self.assertEqual(ARC.DarwinPlatform().gate_state(settings), module.state(repo))

    def sol_09(self) -> None:
        self.assertEqual(ARC.RETAIL_INVENTORY_VERSION, 1)
        self.assertEqual(ARC.RETAIL_INVENTORY_COUNT, 790)
        self.assertEqual(
            ARC.RETAIL_INVENTORY_PATHS_SHA256,
            "d132d7a7073208e2fec5a2f566c1fdcf08e5cfd00d07cb11a6574c90d65fe139")
        main, _, _ = self.configure_retail("inventory-proof")
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        proof = json.loads((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                            f"{transaction}-second-copy-proof.json").read_text())
        self.assertEqual(proof["inventory"]["version"], 1)
        self.assertEqual(proof["inventory"]["file_count"], 15)

        main, _, _ = self.configure_retail("future-path")
        self.write_tree_file(
            main.source, "gamedata/configs/future_allowed.xml", b"future path\n")
        snapshot = self.tree_snapshot(main.source)
        with self.assertRaisesRegex(ARC.ArchiveError, "exact audited inventory"):
            self.service.enqueue(main.ident)
        self.assert_tree_preserved(main.source, snapshot)

    def sol_10(self) -> None:
        main, _, prepared = self.configure_retail("safe-modes")
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        self.assertEqual(self.drain(transaction)["status"], "PASS")

        main, _, prepared = self.configure_retail("bad-directory-mode")
        snapshot = self.tree_snapshot(main.source)
        (prepared / "manifest").chmod(0o777)
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "directory mode is unsafe"):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, snapshot)

        main, _, prepared = self.configure_retail("bad-file-mode")
        snapshot = self.tree_snapshot(main.source)
        (prepared / "manifest/files.tsv").chmod(0o666)
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        with self.assertRaisesRegex(ARC.ArchiveError, "group/other writable"):
            self.drain(transaction)
        self.assert_tree_preserved(main.source, snapshot)

    def sol_11(self) -> None:
        report = self.source / "report.txt"
        nested = self.source / "nested"
        if sys.platform == "darwin":
            subprocess.run(
                ("xattr", "-w", "com.openxray.restore-test", "metadata", str(report)),
                check=True)
            subprocess.run(
                ("xattr", "-w", "com.openxray.restore-root", "root-metadata",
                 str(self.source)), check=True)
            subprocess.run(
                ("chmod", "+a", "everyone allow read", str(report)), check=True)
            subprocess.run(
                ("chmod", "+a", "everyone allow read", str(self.source)), check=True)
            subprocess.run(("chflags", "hidden", str(report)), check=True)
            subprocess.run(("chflags", "hidden", str(self.source)), check=True)
            self.assertEqual(ARC.bsd_flags(report.stat()) & 0x8000, 0x8000)
            self.assertEqual(ARC.bsd_flags(self.source.stat()) & 0x8000, 0x8000)
        report.chmod(0o400)
        nested.chmod(0o510)
        snapshot = self.tree_snapshot(self.source)
        transaction = self.enqueue()
        archived = self.final_path(transaction) / "report.txt"
        def fail_final_boundary(**_: object) -> None:
            archived.chmod(0o600)
            archived.write_bytes(b"metadata boundary failure\n")
        self.service.hooks["before_final_retirement_boundary"] = fail_final_boundary
        with self.assertRaisesRegex(ARC.ArchiveError, "published destination"):
            self.drain(transaction)
        self.assert_tree_preserved(self.source, snapshot)
        self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(nested.stat().st_mode), 0o510)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o700)
        self.assertEqual(report.read_bytes(), b"closed evidence\n")
        if sys.platform == "darwin":
            report_fd = os.open(report, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                report_row = next(
                    row for row in snapshot["entries"] if row["path"] == "report.txt")
                self.assertEqual(ARC.xattrs_fd(report_fd), report_row["xattrs"])
                self.assertEqual(ARC.acl_text_fd(report_fd), report_row["acl"])
                self.assertEqual(ARC.bsd_flags(os.fstat(report_fd)), report_row["flags"])
            finally:
                os.close(report_fd)

        failed_source = self.base / "metadata-restore-failure"
        failed_source.mkdir(mode=0o700)
        (failed_source / "payload.bin").write_bytes(b"must remain quarantined\n")
        failed_candidate = ARC.CandidateSpec(
            "metadata-restore-failure", failed_source, "completed-evidence",
            "reproducible-noncritical", "delete-after-full-pass")
        self.settings.candidates = (failed_candidate,)
        self.service.hooks.clear()
        failed_transaction = self.service.enqueue(failed_candidate.ident)["transaction_id"]
        failed_archive = (self.external_category() /
                          f"{failed_transaction}-{failed_source.name}" / "payload.bin")
        self.service.hooks["before_final_retirement_boundary"] = lambda **_: (
            failed_archive.write_bytes(b"force boundary rejection\n"))
        self.service.hooks["before_fsync"] = lambda fd, label: setattr(
            self.platform, "current_label", label)
        self.platform.fail_sync_label = "restored source metadata"
        with self.assertRaises(ARC.ArchiveError):
            self.drain(failed_transaction)
        self.platform.fail_sync_label = None
        self.assertFalse(failed_source.exists())
        failed_intent = json.loads((self.settings.queue_root / failed_transaction /
                                    ARC.RECORD_NAMES["delete-intent"]).read_text())
        retained = (self.settings.queue_root / failed_intent["quarantine_namespace"] /
                    failed_intent["retired_name"] / "payload.bin")
        self.assertEqual(retained.read_bytes(), b"must remain quarantined\n")

    def sol_12(self) -> None:
        current = ARC.INITIAL_CANDIDATES
        self.assertEqual(len(current), 8)
        self.assertEqual(
            ARC.CANDIDATE_AUTHORIZATION_FIELDS,
            ("ident", "source", "category", "data_class", "deletion_rule"))
        self.assertEqual(
            ARC.allowlist_authorization_sha256(ARC.ALLOWLIST_VERSION, current),
            ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        self.assertTrue(ARC.reviewed_allowlist_authorized(
            ARC.ALLOWLIST_VERSION, current))
        self.assertTrue(ARC.Settings().review_authorized)
        self.assertIsInstance(ARC.ArchiveService(ARC.Settings()), ARC.ArchiveService)

        def rejected(candidates: tuple[ARC.CandidateSpec, ...]) -> None:
            self.assertFalse(ARC.reviewed_allowlist_authorized(
                ARC.ALLOWLIST_VERSION, candidates))
            with self.assertRaisesRegex(ARC.ArchiveError, "cannot be overridden"):
                ARC.ArchiveService(ARC.Settings(candidates=candidates))

        for index, candidate in enumerate(current):
            changes = (
                ARC.CandidateSpec(
                    candidate.ident + "-changed", candidate.source, candidate.category,
                    candidate.data_class, candidate.deletion_rule),
                ARC.CandidateSpec(
                    candidate.ident, Path(str(candidate.source) + "-changed"),
                    candidate.category, candidate.data_class, candidate.deletion_rule),
                ARC.CandidateSpec(
                    candidate.ident, candidate.source,
                    "backups" if candidate.category != "backups" else "archives",
                    candidate.data_class, candidate.deletion_rule),
                ARC.CandidateSpec(
                    candidate.ident, candidate.source, candidate.category,
                    "critical" if candidate.data_class != "critical" else "redundant",
                    candidate.deletion_rule),
                ARC.CandidateSpec(
                    candidate.ident, candidate.source, candidate.category,
                    candidate.data_class, candidate.deletion_rule + "-changed"),
            )
            for changed in changes:
                mutated = list(current)
                mutated[index] = changed
                rejected(tuple(mutated))

        extra = ARC.CandidateSpec(
            "future-unreviewed", Path("/Users/patryk/openxray-handoff/future.log"),
            "completed-evidence", "reproducible-noncritical",
            "delete-after-full-pass")
        rejected((*current, extra))
        rejected((extra, *current))
        live_service = ARC.ArchiveService(ARC.Settings())
        live_service.settings.candidates = (*current, extra)
        with self.assertRaisesRegex(ARC.ArchiveError, "digest is stale"):
            live_service.spec(current[0].ident)
        for index in range(len(current)):
            rejected(current[:index] + current[index + 1:])
        reordered = list(current)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        rejected(tuple(reordered))

        for version in (ARC.ALLOWLIST_VERSION - 1, ARC.ALLOWLIST_VERSION + 1):
            self.assertFalse(ARC.reviewed_allowlist_authorized(version, current))
        with mock.patch.object(ARC, "ALLOWLIST_VERSION", ARC.ALLOWLIST_VERSION + 1):
            with self.assertRaisesRegex(ARC.ArchiveError, "cannot be overridden"):
                ARC.ArchiveService(ARC.Settings())

    def sol_13(self) -> None:
        gate_root = self.settings.gate_log_root
        current_path = gate_root / "gate-2000000000-123-0.json"
        current = json.loads(current_path.read_text(encoding="ascii"))

        historical_shape = json.loads(json.dumps(current))
        historical_shape.pop("log_identity_mismatch")
        historical_path = gate_root / "gate-2000000002-123-0.json"
        historical_path.write_bytes(ARC.canonical_json(historical_shape))
        historical_path.chmod(0o600)

        historical_schema = json.loads(json.dumps(current))
        historical_schema["schema"] = "openxray.gate-log.v1"
        historical_schema_path = gate_root / "gate-2000000003-123-0.json"
        historical_schema_path.write_bytes(ARC.canonical_json(historical_schema))
        historical_schema_path.chmod(0o600)

        selected = self.service.matching_full_gate_receipt()
        self.assertEqual(selected["receipt_name"], current_path.name)

        transaction = self.enqueue()
        self.service.hooks["after_published_record"] = lambda **_: (
            _ for _ in ()).throw(ARC.InjectedCrash("published-before-gate-proof"))
        with self.assertRaisesRegex(ARC.InjectedCrash, "published-before-gate-proof"):
            self.drain(transaction)
        self.service.hooks.clear()
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["published"]).is_file())
        self.assertTrue(self.source.exists())

        current_path.unlink()
        with self.assertRaisesRegex(ARC.ArchiveError, "no fresh matching"):
            self.service.matching_full_gate_receipt()
        with self.assertRaisesRegex(ARC.ArchiveError, "no fresh matching"):
            self.drain(transaction)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.final_path(transaction).exists())

        malformed = gate_root / "gate-2000000004-123-0.json"
        malformed.write_bytes(b"{\n")
        malformed.chmod(0o600)
        with self.assertRaisesRegex(ARC.ArchiveError, "malformed"):
            self.service.matching_full_gate_receipt()
        malformed.unlink()

        noncanonical = gate_root / "gate-2000000005-123-0.json"
        noncanonical.write_text(json.dumps(current), encoding="ascii")
        noncanonical.chmod(0o600)
        with self.assertRaisesRegex(ARC.ArchiveError, "not canonical"):
            self.service.matching_full_gate_receipt()
        noncanonical.unlink()

        unsafe = gate_root / "gate-2000000006-123-0.json"
        unsafe.write_bytes(ARC.canonical_json(current))
        unsafe.chmod(0o644)
        with self.assertRaisesRegex(ARC.ArchiveError, "foreign or unsafe"):
            self.service.matching_full_gate_receipt()
        unsafe.unlink()

        fresh = self.write_gate_receipt(
            receipt_name="gate-2000000007-123-0.json",
            ended=self.platform.clock - 1)
        self.assertEqual(
            self.service.matching_full_gate_receipt()["receipt_name"], fresh.name)
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertFalse(self.source.exists())

    def sol_14(self) -> None:
        gate_path = self.settings.gate_log_root / "gate-2000000000-123-0.json"
        valid_raw = gate_path.read_bytes()
        valid_receipt = ARC.strict_json_loads(valid_raw.decode("ascii"))

        for constant in (float("nan"), float("inf"), float("-inf")):
            mutated = json.loads(json.dumps(valid_receipt))
            mutated["ended_unix"] = constant
            raw = (json.dumps(
                mutated, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=True) + "\n").encode("ascii")
            gate_path.write_bytes(raw)
            with self.assertRaisesRegex(ARC.ArchiveError, "malformed"):
                self.service.matching_full_gate_receipt()
            with self.assertRaises(ValueError):
                ARC.canonical_json(mutated)
        with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
            ARC.strict_json_loads('{"nested":[{"overflow":1e999}]}')

        for field in ("started_unix", "ended_unix"):
            for value in (False, True):
                mutated = json.loads(json.dumps(valid_receipt))
                mutated[field] = value
                gate_path.write_bytes(ARC.canonical_json(mutated))
                with self.assertRaisesRegex(ARC.ArchiveError, "no fresh matching"):
                    self.service.matching_full_gate_receipt()

        gate_path.write_bytes(valid_raw)
        self.assertEqual(
            self.service.matching_full_gate_receipt()["receipt_name"], gate_path.name)

        transaction = self.enqueue()
        candidate_path = (self.settings.queue_root / transaction /
                          ARC.RECORD_NAMES["candidate"])
        candidate_raw = candidate_path.read_bytes()
        candidate = ARC.strict_json_loads(candidate_raw.decode("ascii"))
        transaction_fd = self.transaction_fd(transaction)
        try:
            for spelling, constant in (
                    ("NaN", float("nan")),
                    ("Infinity", float("inf")),
                    ("-Infinity", float("-inf"))):
                mutated = json.loads(json.dumps(candidate))
                mutated["nonfinite_mutation"] = constant
                raw = (json.dumps(
                    mutated, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"), allow_nan=True) + "\n").encode("ascii")
                self.assertIn(spelling.encode("ascii"), raw)
                candidate_path.write_bytes(raw)
                with self.assertRaisesRegex(ARC.ArchiveError, "record is malformed"):
                    self.service.read_record(
                        transaction_fd, ARC.RECORD_NAMES["candidate"])
                with self.assertRaises(ValueError):
                    self.service.write_record(
                        transaction_fd, f"nonfinite-{spelling.lower()}.json",
                        {"value": constant})
                self.assertFalse((self.settings.queue_root / transaction /
                                  f"nonfinite-{spelling.lower()}.json").exists())
        finally:
            candidate_path.write_bytes(candidate_raw)
            os.close(transaction_fd)
        transaction_fd = self.transaction_fd(transaction)
        try:
            self.assertEqual(
                self.service.candidate_record(transaction_fd)["transaction_id"],
                transaction)
        finally:
            os.close(transaction_fd)

    # OVR-01..09 — exact, production-only APFS provenance overlay
    def ovr_01(self) -> None:
        source, physical, policy, transaction, candidate, version = self.overlay_fixture()
        proof = ARC.compare_provenance_overlay(
            source, physical, transaction_id=transaction, candidate_id=candidate,
            allowlist_version=version, policy=policy,
            authorization_sha256=ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        self.assertEqual(proof["schema"], "openxray.apfs-provenance-overlay-proof.v1")
        self.assertEqual(proof["total_entries"], 4)
        self.assertEqual(proof["inherited_count"], 1)
        self.assertEqual(proof["added_count"], 3)
        self.assertEqual(proof["source_semantic_tree_sha256"], source["tree_sha256"])
        self.assertEqual(proof["physical_tree_sha256"], physical["tree_sha256"])
        self.assertEqual(proof["source_manifest_sha256"],
                         ARC.manifest_canonical_sha256(source))
        self.assertEqual(proof["physical_manifest_sha256"],
                         ARC.manifest_canonical_sha256(physical))
        self.assertEqual(proof["proof_hash"], ARC.provenance_overlay_proof_hash(proof))
        self.assertFalse(ARC.manifests_equal(source, physical))

    def ovr_02(self) -> None:
        source, physical, policy, transaction, candidate, version = self.overlay_fixture()
        def rejected(changed_source: dict[str, object],
                     changed_physical: dict[str, object]) -> None:
            with self.assertRaises(ARC.ArchiveError):
                ARC.compare_provenance_overlay(
                    changed_source, changed_physical, transaction_id=transaction,
                    candidate_id=candidate, allowlist_version=version, policy=policy,
                    authorization_sha256=ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)

        wrong = copy.deepcopy(physical)
        next(row for row in wrong["entries"] if row["path"] == ".")["xattrs"][
            ARC.PROVENANCE_OVERLAY_XATTR] = "d3Jvbmc="
        rejected(source, self.rebuild_manifest(wrong))
        missing = copy.deepcopy(physical)
        next(row for row in missing["entries"] if row["path"] == ".")["xattrs"].pop(
            ARC.PROVENANCE_OVERLAY_XATTR)
        rejected(source, self.rebuild_manifest(missing))
        extra = copy.deepcopy(physical)
        next(row for row in extra["entries"] if row["path"] == ".")["xattrs"][
            "com.openxray.foreign"] = "Zm9yZWlnbg=="
        rejected(source, self.rebuild_manifest(extra))
        changed_source = copy.deepcopy(source)
        next(row for row in changed_source["entries"]
             if row["path"] == "inherited.bin")["xattrs"][
                 ARC.PROVENANCE_OVERLAY_XATTR] = "Y2hhbmdlZA=="
        changed_source = self.rebuild_manifest(changed_source)
        changed_physical = copy.deepcopy(physical)
        next(row for row in changed_physical["entries"]
             if row["path"] == "inherited.bin")["xattrs"][
                 ARC.PROVENANCE_OVERLAY_XATTR] = "Y2hhbmdlZA=="
        rejected(changed_source, self.rebuild_manifest(changed_physical))

    def ovr_03(self) -> None:
        source, physical, policy, transaction, candidate, version = self.overlay_fixture()
        mutations = (
            ("sha256", "0" * 64), ("mode", 0o666),
            ("mtime_ns", 987654321), ("acl", "Y2hhbmdlZA=="), ("flags", 0x8000),
        )
        for field, value in mutations:
            changed = copy.deepcopy(physical)
            row = next(item for item in changed["entries"]
                       if item["path"] == "nested/added.bin")
            row[field] = value
            with self.subTest(field=field), self.assertRaises(ARC.ArchiveError):
                ARC.compare_provenance_overlay(
                    source, self.rebuild_manifest(changed), transaction_id=transaction,
                    candidate_id=candidate, allowlist_version=version, policy=policy,
                    authorization_sha256=ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        changed = copy.deepcopy(physical)
        next(item for item in changed["entries"]
             if item["path"] == "nested/added.bin")["path"] = "nested/foreign.bin"
        with self.assertRaises(ARC.ArchiveError):
            ARC.compare_provenance_overlay(
                source, self.rebuild_manifest(changed), transaction_id=transaction,
                candidate_id=candidate, allowlist_version=version, policy=policy,
                authorization_sha256=ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)

    def ovr_04(self) -> None:
        fake = {
            "candidate_id": ARC.PROVENANCE_OVERLAY_CANDIDATE_ID,
            "source": str(ARC.RETAIL_BACKUP_PATH), "category": "backups",
            "data_class": "redundant",
            "deletion_rule": "requires-fixed-prepared-proof",
            "allowlist_version": ARC.ALLOWLIST_VERSION,
        }
        self.assertIsNone(self.service.provenance_overlay_policy_for_record(fake))
        self.settings.test_retail_roles = ((self.candidate.ident, "main"),)
        self.assertIsNone(self.service.provenance_overlay_policy_for_record(fake))

        policy = ARC.PRODUCTION_PROVENANCE_OVERLAY
        for field in policy.__dataclass_fields__:
            value = getattr(policy, field)
            changed = value + 1 if isinstance(value, int) else value + "-changed"
            replacement = ARC.ProvenanceOverlayPolicy(**{
                name: changed if name == field else getattr(policy, name)
                for name in policy.__dataclass_fields__})
            with mock.patch.object(ARC, "PRODUCTION_PROVENANCE_OVERLAY", replacement):
                self.assertFalse(ARC.reviewed_allowlist_authorized(
                    ARC.ALLOWLIST_VERSION, ARC.INITIAL_CANDIDATES))
                with self.assertRaisesRegex(ARC.ArchiveError, "cannot be overridden"):
                    ARC.ArchiveService(ARC.Settings())
        for name in (
                "PROVENANCE_OVERLAY_RECOVERY_TRANSACTION_ID",
                "PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256",
                "PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256",
                "PROVENANCE_OVERLAY_RECOVERY_PHYSICAL_MANIFEST_SHA256"):
            with mock.patch.object(ARC, name, getattr(ARC, name)[:-1] + "0"):
                self.assertFalse(ARC.reviewed_allowlist_authorized(
                    ARC.ALLOWLIST_VERSION, ARC.INITIAL_CANDIDATES))

    def ovr_05(self) -> None:
        candidate = {
            "transaction_id": ARC.PROVENANCE_OVERLAY_RECOVERY_TRANSACTION_ID,
            "candidate_id": ARC.PROVENANCE_OVERLAY_CANDIDATE_ID,
            "allowlist_version": ARC.ALLOWLIST_VERSION,
            "source_tree_sha256": ARC.PROVENANCE_OVERLAY_SOURCE_TREE_SHA256,
        }
        copied = {
            "schema": ARC.SCHEMA, "stage": "copy-complete",
            "transaction_id": ARC.PROVENANCE_OVERLAY_RECOVERY_TRANSACTION_ID,
            "payload_identity": [1, 2], "destination_manifest": {"reviewed": True},
        }
        proof = {
            "physical_tree_sha256": ARC.PROVENANCE_OVERLAY_PHYSICAL_TREE_SHA256,
            "physical_manifest_sha256":
                ARC.PROVENANCE_OVERLAY_RECOVERY_PHYSICAL_MANIFEST_SHA256,
            "source_semantic_tree_sha256": ARC.PROVENANCE_OVERLAY_SOURCE_TREE_SHA256,
            "inherited_count": ARC.PROVENANCE_OVERLAY_INHERITED_COUNT,
            "added_count": ARC.PROVENANCE_OVERLAY_ADDED_COUNT,
            "added_paths_sha256": ARC.PROVENANCE_OVERLAY_ADDED_PATHS_SHA256,
        }
        self.assertTrue(ARC.reviewed_legacy_overlay_recovery_tuple(
            candidate, copied, proof,
            candidate_record_sha256=ARC.PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256,
            copy_record_sha256=ARC.PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256))
        for key in ("transaction_id", "candidate_id", "allowlist_version",
                    "source_tree_sha256"):
            changed = copy.deepcopy(candidate)
            changed[key] = "changed"
            self.assertFalse(ARC.reviewed_legacy_overlay_recovery_tuple(
                changed, copied, proof,
                candidate_record_sha256=ARC.PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256,
                copy_record_sha256=ARC.PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256))
        for key in ("schema", "stage", "transaction_id"):
            changed = copy.deepcopy(copied)
            changed[key] = "changed"
            self.assertFalse(ARC.reviewed_legacy_overlay_recovery_tuple(
                candidate, changed, proof,
                candidate_record_sha256=ARC.PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256,
                copy_record_sha256=ARC.PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256))
        self.assertFalse(ARC.reviewed_legacy_overlay_recovery_tuple(
            candidate, copied, proof,
            candidate_record_sha256=ARC.PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256,
            copy_record_sha256="0" * 64))
        changed_002 = copy.deepcopy(copied)
        changed_002["destination_manifest"] = {"reviewed": False}
        self.assertNotEqual(ARC.sha256_bytes(ARC.canonical_json(changed_002)),
                            ARC.PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256)
        self.assertFalse(ARC.reviewed_legacy_overlay_recovery(candidate, changed_002, proof))

    def ovr_06(self) -> None:
        source, physical, policy, transaction, candidate, version = self.overlay_fixture()
        record = {"transaction_id": transaction, "candidate_id": candidate,
                  "allowlist_version": version, "source_manifest": source,
                  "source_tree_sha256": source["tree_sha256"], "source": "/source",
                  "category": "backups", "data_class": "redundant",
                  "deletion_rule": "reviewed", "created_utc": "2026-08-12T00:00:00Z"}
        self.service.provenance_overlay_policy_for_record = lambda _: policy
        proof = self.service.provenance_overlay_proof_for(record, physical, policy)
        binding = self.service.overlay_record_binding(source, physical, proof)
        payload = {"transaction_id": transaction, **binding}
        self.assertEqual(self.service.validate_overlay_record_binding(
            record, payload, physical), proof)
        published = {"final_name": "final", "published_utc": "2026-08-12T00:01:00Z",
                     **binding}
        volume = ARC.VolumeBinding(-1, (1, 2), {
            "mountpoint": str(self.mount), "name": "DevArchive",
            "uuid": ARC.VOLUME_UUID, "filesystem": "apfs"})
        external = self.service.transfer_manifest_payload(
            record, published, volume, {"receipt_sha256": "a" * 64})
        self.service.validate_external_overlay_binding(record, external, published)
        retired = {"tombstone_name": "retired", "tombstone_identity": [1, 2],
                   "tombstone_manifest": {"logical_bytes": 0},
                   "reclaimed_logical_bytes": 10, "reclaimed_allocated_bytes": 20}
        receipt = self.service.deletion_receipt_payload(record, retired, published)
        self.service.validate_external_overlay_binding(record, receipt, published)
        for target in (payload, external, receipt):
            self.assertEqual(target["provenance_overlay_proof"], proof)
            self.assertEqual(target["source_manifest_sha256"],
                             ARC.manifest_canonical_sha256(source))
            self.assertEqual(target["destination_manifest_sha256"],
                             ARC.manifest_canonical_sha256(physical))

    def ovr_07(self) -> None:
        transaction = self.enqueue()
        self.configure_overlay_integration(transaction)
        self.service.hooks["after_copy_complete"] = lambda **_: (
            _ for _ in ()).throw(ARC.InjectedCrash("overlay-after-002"))
        with self.assertRaisesRegex(ARC.InjectedCrash, "overlay-after-002"):
            self.drain(transaction)
        self.assertTrue(self.source.exists())
        copied = ARC.strict_json_loads((
            self.settings.queue_root / transaction /
            ARC.RECORD_NAMES["copy-complete"]).read_text(encoding="ascii"))
        self.assertIn("provenance_overlay_proof", copied)
        self.service.hooks.clear()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        for stage in ("verified", "published"):
            payload = ARC.strict_json_loads((
                self.settings.queue_root / transaction /
                ARC.RECORD_NAMES[stage]).read_text(encoding="ascii"))
            self.assertEqual(payload["provenance_overlay_proof"],
                             copied["provenance_overlay_proof"])
        external_root = self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT
        for name in (f"{transaction}.json", f"{transaction}-source-deletion.json"):
            payload = ARC.strict_json_loads((external_root / name).read_text(encoding="ascii"))
            self.assertEqual(payload["provenance_overlay_proof"],
                             copied["provenance_overlay_proof"])

    def ovr_08(self) -> None:
        transaction = self.enqueue()
        policy = self.configure_overlay_integration(transaction)
        source_snapshot = self.tree_snapshot(self.source)
        def mutate_final(**_: object) -> None:
            descriptor = os.open(
                self.final_path(transaction) / "report.txt", os.O_RDONLY | os.O_NOFOLLOW)
            try:
                values = ARC.xattrs_fd(descriptor)
                values[policy.xattr_name] = "d3Jvbmc="
                ARC.apply_xattrs_fd(descriptor, values)
            finally:
                os.close(descriptor)
        self.service.hooks["before_final_retirement_boundary"] = mutate_final
        with self.assertRaises(ARC.ArchiveError):
            self.drain(transaction)
        self.assert_tree_preserved(self.source, source_snapshot)
        self.assertTrue(self.final_path(transaction).exists())
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["source-quarantined"]).exists())

    def ovr_09(self) -> None:
        source, physical, _, transaction, _, version = self.overlay_fixture()
        record = {"transaction_id": transaction, "candidate_id": self.candidate.ident,
                  "allowlist_version": version, "source_manifest": source}
        with self.assertRaisesRegex(ARC.ArchiveError, "copied payload differs"):
            self.service.validate_copy_overlay(record, physical)
        self.assertEqual(self.drain(self.enqueue())["status"], "PASS")

    def ovr_10(self) -> None:
        main, _, _ = self.configure_retail("final-order-destination")
        source_snapshot = self.tree_snapshot(main.source)
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        archived = (self.mount / ARC.ARCHIVE_ROOT / ARC.CATEGORY_ROOTS["backups"] /
                    f"{transaction}-{main.source.name}" / ARC.SECOND_COPY_MAPPINGS[0][0])
        called = {"value": False}
        def mutate_after_prepared(**_: object) -> None:
            called["value"] = True
            archived.write_bytes(b"destination changed after prepared proof\n")
        self.service.hooks["after_final_prepared_proof"] = mutate_after_prepared
        with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
            with self.assertRaisesRegex(ARC.ArchiveError, "published destination"):
                self.drain(transaction)
            self.assertEqual(truncating.call_count, 0)
        self.assertTrue(called["value"])
        self.assert_tree_preserved(main.source, source_snapshot)
        self.assertFalse((self.settings.queue_root / transaction /
                          ARC.RECORD_NAMES["source-deleted"]).exists())

    def ovr_11(self) -> None:
        main, _, _ = self.configure_retail("final-order-remount-recovery")
        source_snapshot = self.tree_snapshot(main.source)
        transaction = self.service.enqueue(main.ident)["transaction_id"]
        self.service.hooks["before_final_retirement_boundary"] = lambda **_: (
            _ for _ in ()).throw(ARC.InjectedCrash("arm durable recovery"))
        with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
            with self.assertRaisesRegex(ARC.InjectedCrash, "arm durable recovery"):
                self.drain(transaction)
            self.assertEqual(truncating.call_count, 0)
        self.assert_tree_preserved(main.source, source_snapshot)
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["retirement-started"]).exists())

        self.service.hooks.clear()
        old_mount = self.base / "old-final-order-volume"
        called = {"value": False}
        def remount_after_prepared(**_: object) -> None:
            called["value"] = True
            self.mount.rename(old_mount)
            self.mount.mkdir(mode=0o700)
        self.service.hooks["after_final_prepared_proof"] = remount_after_prepared
        with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
            result = self.drain(transaction)
            self.assertEqual(truncating.call_count, 0)
        self.assertEqual(result["status"], "DEFERRED_VOLUME")
        self.assertTrue(called["value"])
        self.assert_tree_preserved(main.source, source_snapshot)
        self.assertFalse((self.settings.queue_root / transaction /
                          ARC.RECORD_NAMES["source-deleted"]).exists())

    def ovr_12(self) -> None:
        source, _, _, _, _, _ = self.overlay_fixture()
        physical = copy.deepcopy(source)
        root = next(row for row in physical["entries"] if row["path"] == ".")
        root["xattrs"][ARC.PROVENANCE_OVERLAY_XATTR] = ARC.PROVENANCE_OVERLAY_VALUE
        physical = self.rebuild_manifest(physical)
        policy = ARC.SourceRootOverlayPolicy(
            1, ARC.PROVENANCE_OVERLAY_XATTR, ARC.PROVENANCE_OVERLAY_VALUE,
            "b" * 32, "source-root-fixture", "/source-root",
            tuple(source["entries"][0]["local_identity"]), (1, 2),
            ARC.ALLOWLIST_VERSION, len(source["entries"]), source["files"],
            source["directories"], 1,
            ARC.sha256_bytes(ARC.canonical_json(["."])),
            source["tree_sha256"], ARC.manifest_canonical_sha256(source),
            physical["tree_sha256"], ARC.manifest_canonical_sha256(physical))
        proof = ARC.compare_source_root_overlay(
            source, physical, policy=policy,
            production_authorization_sha256=ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        self.assertEqual(proof["added_count"], 1)
        self.assertEqual(proof["added_paths_sha256"],
                         ARC.sha256_bytes(ARC.canonical_json(["."])))

        mutations = (
            ("mode", 0o777), ("uid", source["entries"][0]["uid"] + 1),
            ("gid", source["entries"][0]["gid"] + 1),
            ("mtime_ns", 987654321), ("flags", 0x8000),
            ("acl", "Y2hhbmdlZA=="), ("allocated_bytes", 8192),
            ("local_identity", [99, 100]), ("logical_bytes", 1),
        )
        for field, value in mutations:
            changed = copy.deepcopy(physical)
            next(row for row in changed["entries"] if row["path"] == ".")[field] = value
            with self.subTest(root_field=field), self.assertRaises(ARC.ArchiveError):
                ARC.compare_source_root_overlay(
                    source, self.rebuild_manifest(changed), policy=policy,
                    production_authorization_sha256=
                    ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        child_mutations = (
            ("sha256", "0" * 64), ("local_identity", [99, 101]),
            ("allocated_bytes", 12288), ("mode", 0o666),
        )
        for field, value in child_mutations:
            changed = copy.deepcopy(physical)
            child = next(row for row in changed["entries"]
                         if row["path"] == "nested/added.bin")
            child[field] = value
            with self.subTest(child_field=field), self.assertRaises(ARC.ArchiveError):
                ARC.compare_source_root_overlay(
                    source, self.rebuild_manifest(changed), policy=policy,
                    production_authorization_sha256=
                    ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        for mutation in ("missing-root", "wrong-root", "extra-root",
                         "child-added", "inherited-changed", "path"):
            changed = copy.deepcopy(physical)
            root = next(row for row in changed["entries"] if row["path"] == ".")
            if mutation == "missing-root":
                root["xattrs"].pop(ARC.PROVENANCE_OVERLAY_XATTR)
            elif mutation == "wrong-root":
                root["xattrs"][ARC.PROVENANCE_OVERLAY_XATTR] = "d3Jvbmc="
            elif mutation == "extra-root":
                root["xattrs"]["com.openxray.foreign"] = "Zm9yZWlnbg=="
            elif mutation == "child-added":
                next(row for row in changed["entries"]
                     if row["path"] == "nested")["xattrs"][
                         ARC.PROVENANCE_OVERLAY_XATTR] = ARC.PROVENANCE_OVERLAY_VALUE
            elif mutation == "inherited-changed":
                next(row for row in changed["entries"]
                     if row["path"] == "inherited.bin")["xattrs"][
                         ARC.PROVENANCE_OVERLAY_XATTR] = "Y2hhbmdlZA=="
            else:
                next(row for row in changed["entries"]
                     if row["path"] == "nested/added.bin")["path"] = "other.bin"
            with self.subTest(xattr_or_path=mutation), self.assertRaises(ARC.ArchiveError):
                ARC.compare_source_root_overlay(
                    source, self.rebuild_manifest(changed), policy=policy,
                    production_authorization_sha256=
                    ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)

    def ovr_13(self) -> None:
        self.assertTrue(ARC.PRODUCTION_REVIEW_AUTHORIZED)
        self.assertTrue(ARC.reviewed_allowlist_authorized(
            ARC.ALLOWLIST_VERSION, ARC.INITIAL_CANDIDATES))
        policy = ARC.PRODUCTION_SOURCE_ROOT_OVERLAY
        for field in policy.__dataclass_fields__:
            value = getattr(policy, field)
            if isinstance(value, tuple):
                changed = (value[0] + 1, value[1])
            elif isinstance(value, int):
                changed = value + 1
            else:
                changed = value + "-changed"
            replacement = ARC.SourceRootOverlayPolicy(**{
                name: changed if name == field else getattr(policy, name)
                for name in policy.__dataclass_fields__})
            with mock.patch.object(ARC, "PRODUCTION_SOURCE_ROOT_OVERLAY", replacement):
                self.assertFalse(ARC.reviewed_allowlist_authorized(
                    ARC.ALLOWLIST_VERSION, ARC.INITIAL_CANDIDATES), field)
        prefix = list(ARC.SOURCE_ROOT_OVERLAY_LEGACY_PREFIX)
        prefix[3] = (prefix[3][0], "0" * 64)
        mutations = (
            mock.patch.object(ARC, "SOURCE_ROOT_OVERLAY_LEGACY_PREFIX", tuple(prefix)),
            mock.patch.object(ARC, "SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256", "0" * 64),
            mock.patch.object(
                ARC, "SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256",
                "0" * 64),
            mock.patch.dict(ARC.SOURCE_ROOT_OVERLAY_LEGACY_005,
                            {"external_manifest_sha256": "0" * 64}),
            mock.patch.object(ARC, "SOURCE_ROOT_OVERLAY_LEGACY_006_CANONICAL",
                              ARC.SOURCE_ROOT_OVERLAY_LEGACY_006_CANONICAL + b" "),
        )
        for index, mutation in enumerate(mutations):
            with mutation, self.subTest(tuple_mutation=index):
                self.assertFalse(ARC.reviewed_allowlist_authorized(
                    ARC.ALLOWLIST_VERSION, ARC.INITIAL_CANDIDATES))
        self.assertFalse(ARC.reviewed_allowlist_authorized(
            ARC.ALLOWLIST_VERSION + 1, ARC.INITIAL_CANDIDATES))

    def ovr_14(self) -> None:
        self.assertEqual(
            ARC.parser().parse_args(["verify-production-state"]).command,
            "verify-production-state")
        with self.source_root_overlay_transaction("hermetic-production-proof") as fixture:
            transaction = fixture["transaction"]
            result = self.service.verify_production_state(transaction)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["source_physical_manifest_sha256"],
                             ARC.manifest_canonical_sha256(fixture["physical"]))
            original = dict(self.platform.attrs or {})
            self.platform.attrs = None
            with self.assertRaises(ARC.DeferredVolume):
                self.service.verify_production_state(transaction)
            self.platform.attrs = {**original, "uuid": "0" * 36}
            with self.assertRaises(ARC.DeferredVolume):
                self.service.verify_production_state(transaction)
            self.platform.attrs = original

    def ovr_15(self) -> None:
        with self.source_root_overlay_transaction("before-008") as fixture:
            transaction = fixture["transaction"]
            self.fork_crash_drain(transaction, "after_source_quarantine_rename")
            self.assertFalse(fixture["main"].source.exists())
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RECORD_NAMES["source-quarantined"]).exists())
            self.assertEqual(self.drain(transaction)["status"], "PASS")

        with self.source_root_overlay_transaction("non-equivalent-gate") as fixture:
            transaction = fixture["transaction"]
            self.fork_crash_drain(transaction, "after_source_quarantined_record")
            self.platform.current_gate_state["head"] = "2" * 40
            self.write_gate_receipt(
                receipt_name="gate-2000000101-123-0.json",
                ended=self.platform.clock - 1)
            with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
                with self.assertRaisesRegex(
                        ARC.ArchiveError, "current worktree|semantically equivalent"):
                    self.drain(transaction)
                self.assertEqual(truncating.call_count, 0)
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RECORD_NAMES["retirement-started"]).exists())

    def ovr_16(self) -> None:
        with self.source_root_overlay_transaction("after-008") as fixture:
            transaction = fixture["transaction"]
            self.fork_crash_drain(transaction, "after_source_quarantined_record")
            stage_008_path = (fixture["transaction_root"] /
                              ARC.RECORD_NAMES["source-quarantined"])
            immutable_008 = stage_008_path.read_bytes()
            stage_008 = ARC.strict_json_loads((
                stage_008_path).read_text(encoding="ascii"))
            self.assertEqual(stage_008["source_physical_manifest_sha256"],
                             ARC.manifest_canonical_sha256(fixture["physical"]))
            old_gate = stage_008["gate_proof"]
            self.platform.clock += self.settings.gate_max_age_seconds + 60
            fresh = self.write_gate_receipt(
                receipt_name="gate-2000000201-123-0.json",
                ended=self.platform.clock - 1)
            self.service.hooks["before_final_retirement_boundary"] = lambda **_: (
                _ for _ in ()).throw(ARC.ArchiveError("restore for repeated rename"))
            with self.assertRaisesRegex(ARC.ArchiveError, "repeated rename"):
                self.drain(transaction)
            self.service.hooks.clear()
            self.assertEqual(self.tree_snapshot(fixture["main"].source), fixture["physical"])
            self.assertEqual(ARC.identity(fixture["main"].source.stat()),
                             tuple(fixture["record"]["source_identity"]))
            self.assertEqual(self.drain(transaction)["status"], "PASS")
            self.assertEqual(stage_008_path.read_bytes(), immutable_008)
            stage_009 = ARC.strict_json_loads((
                fixture["transaction_root"] / ARC.RECORD_NAMES["retirement-started"]
            ).read_text(encoding="ascii"))
            self.assertEqual(stage_009["source_root_overlay_gate_proof"], old_gate)
            self.assertEqual(
                stage_009["source_root_overlay_current_gate_proof"]["receipt_name"],
                fresh.name)
            retired = ARC.strict_json_loads((
                fixture["transaction_root"] / ARC.RECORD_NAMES["source-deleted"]
            ).read_text(encoding="ascii"))
            self.assertEqual(retired["tombstone_identity"],
                             fixture["record"]["source_identity"])

    def ovr_17(self) -> None:
        with self.source_root_overlay_transaction("after-restore") as fixture:
            transaction = fixture["transaction"]
            self.fork_crash_drain(
                transaction, "after_source_restore", before_boundary_failure=True)
            self.assertEqual(self.tree_snapshot(fixture["main"].source), fixture["physical"])
            self.assertTrue((fixture["transaction_root"] /
                             ARC.RECORD_NAMES["retirement-started"]).exists())
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RECORD_NAMES["source-deleted"]).exists())
            self.assertEqual(self.drain(transaction)["status"], "PASS")

    def ovr_18(self) -> None:
        with self.source_root_overlay_transaction("after-009") as fixture:
            transaction = fixture["transaction"]
            self.fork_crash_drain(transaction, "after_retirement_started")
            root = fixture["transaction_root"]
            self.assertTrue((root / ARC.RECORD_NAMES["source-quarantined"]).exists())
            self.assertTrue((root / ARC.RECORD_NAMES["retirement-started"]).exists())
            self.assertFalse((root / ARC.RECORD_NAMES["source-deleted"]).exists())
            self.assertEqual(self.drain(transaction)["status"], "PASS")
            self.assertEqual((root / ARC.RECORD_NAMES["manifest-published"]).read_bytes(),
                             fixture["legacy_005_bytes"])
            self.assertEqual((root / ARC.RECORD_NAMES["second-copy-proof"]).read_bytes(),
                             fixture["legacy_006_bytes"])
            stages = {stage: ARC.strict_json_loads((
                root / ARC.RECORD_NAMES[stage]).read_text(encoding="ascii"))
                for stage in ("source-quarantined", "retirement-started",
                              "source-deleted", "deletion-receipt")}
            self.assertEqual(
                stages["retirement-started"]["source_quarantined_stage_sha256"],
                ARC.sha256_bytes(ARC.canonical_json(stages["source-quarantined"])))
            self.assertEqual(
                stages["source-deleted"]["retirement_started_stage_sha256"],
                ARC.sha256_bytes(ARC.canonical_json(stages["retirement-started"])))
            self.assertEqual(
                stages["deletion-receipt"]["source_deleted_stage_sha256"],
                ARC.sha256_bytes(ARC.canonical_json(stages["source-deleted"])))
            receipt = ARC.strict_json_loads((
                self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                f"{transaction}-source-deletion.json").read_text(encoding="ascii"))
            for payload in (stages["retirement-started"], stages["source-deleted"],
                            stages["deletion-receipt"], receipt):
                self.assertEqual(payload["source_root_overlay_proof"],
                                 stages["source-quarantined"]["source_root_overlay_proof"])
                self.assertEqual(payload["source_root_overlay_gate_proof"],
                                 stages["source-quarantined"]["gate_proof"])

    def ovr_19(self) -> None:
        with self.source_root_overlay_transaction("boundary-restore") as fixture:
            transaction = fixture["transaction"]
            source = fixture["main"].source
            for attempt in range(2):
                self.service.hooks["before_final_retirement_boundary"] = lambda **_: (
                    _ for _ in ()).throw(ARC.ArchiveError("pretruncate rejected"))
                with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
                    with self.assertRaisesRegex(ARC.ArchiveError, "pretruncate rejected"):
                        self.drain(transaction)
                    self.assertEqual(truncating.call_count, 0, attempt)
                self.service.hooks.clear()
                self.assertEqual(self.tree_snapshot(source), fixture["physical"])
                self.assertFalse((fixture["transaction_root"] /
                                  ARC.RECORD_NAMES["source-deleted"]).exists())
            self.assertTrue((fixture["transaction_root"] /
                             ARC.RECORD_NAMES["retirement-started"]).exists())

        mutations = ("xattr", "acl", "flags", "mtime", "mode", "uid-gid")
        for mutation in mutations:
            with self.subTest(metadata=mutation):
                with self.source_root_overlay_transaction(
                        f"post-009-{mutation}") as fixture:
                    transaction = fixture["transaction"]
                    intent = ARC.strict_json_loads((
                        fixture["transaction_root"] / ARC.RECORD_NAMES["delete-intent"]
                    ).read_text(encoding="ascii"))
                    target = (self.settings.queue_root /
                              intent["quarantine_namespace"] /
                              intent["quarantine_name"] /
                              ARC.SECOND_COPY_MAPPINGS[0][0])
                    fake_identity = {"armed": False}

                    def mutate_after_009(**_: object) -> None:
                        if mutation == "xattr":
                            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                            try:
                                values = ARC.xattrs_fd(descriptor)
                                values["com.openxray.foreign-after-009"] = "Zm9yZWlnbg=="
                                ARC.restore_xattrs_fd(descriptor, values)
                            finally:
                                os.close(descriptor)
                        elif mutation == "acl":
                            subprocess.run(
                                ("chmod", "+a", "everyone allow read", str(target)),
                                check=True)
                        elif mutation == "flags":
                            subprocess.run(("chflags", "hidden", str(target)), check=True)
                        elif mutation == "mtime":
                            current = target.stat()
                            os.utime(target, ns=(current.st_atime_ns,
                                                current.st_mtime_ns + 1_000_000_000))
                        elif mutation == "mode":
                            target.chmod(0o444)
                        else:
                            fake_identity["armed"] = True

                    original_manifest_bound = ARC.manifest_bound
                    def manifest_with_uid_gid(fd: int, kind: str, **kwargs: object):
                        manifest = original_manifest_bound(fd, kind, **kwargs)
                        if fake_identity["armed"] \
                                and ARC.identity(os.fstat(fd)) \
                                == tuple(fixture["record"]["source_identity"]):
                            fake_identity["armed"] = False
                            changed = copy.deepcopy(manifest)
                            row = next(item for item in changed["entries"]
                                       if item["path"] == ARC.SECOND_COPY_MAPPINGS[0][0])
                            row["uid"] += 1
                            row["gid"] += 1
                            return ARC.finish_manifest(changed["entries"])
                        return manifest

                    self.service.hooks["after_retirement_started"] = mutate_after_009
                    patcher = mock.patch.object(
                        ARC, "manifest_bound", side_effect=manifest_with_uid_gid) \
                        if mutation == "uid-gid" else mock.patch.object(
                            ARC, "manifest_bound", wraps=ARC.manifest_bound)
                    with patcher, mock.patch.object(
                            os, "ftruncate", wraps=os.ftruncate) as truncating:
                        with self.assertRaisesRegex(
                                ARC.ArchiveError, "metadata/content differs"):
                            self.drain(transaction)
                        self.assertEqual(truncating.call_count, 0)
                    self.service.hooks.clear()
                    self.assertFalse((fixture["transaction_root"] /
                                      ARC.RECORD_NAMES["source-deleted"]).exists())
                    self.assertEqual(self.tree_snapshot(fixture["main"].source),
                                     fixture["physical"])

    def ovr_20(self) -> None:
        with self.source_root_overlay_transaction("content-race") as fixture:
            transaction = fixture["transaction"]
            def mutate_content(pinned_fd: int, **_: object) -> None:
                descriptor = os.open(".DS_Store", os.O_RDWR | os.O_NOFOLLOW,
                                     dir_fd=pinned_fd)
                try:
                    original = os.pread(descriptor, os.fstat(descriptor).st_size, 0)
                    changed = bytes([original[0] ^ 1]) + original[1:]
                    os.pwrite(descriptor, changed, 0)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self.service.hooks["after_final_retire_rebind"] = mutate_content
            with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
                with self.assertRaisesRegex(ARC.ArchiveError, "content changed"):
                    self.drain(transaction)
                self.assertEqual(truncating.call_count, 0)
            self.assertFalse(fixture["main"].source.exists())
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RECORD_NAMES["source-deleted"]).exists())

        with self.source_root_overlay_transaction("hardlink-race") as fixture:
            transaction = fixture["transaction"]
            outside = self.base / "source-root-outside-hardlink"
            def add_hardlink(parent_fd: int, name: str, **_: object) -> None:
                os.link(name, outside, src_dir_fd=parent_fd)
            self.service.hooks["before_final_retirement_boundary"] = add_hardlink
            with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
                with self.assertRaisesRegex(ARC.ArchiveError, "hard link"):
                    self.drain(transaction)
                self.assertEqual(truncating.call_count, 0)
            self.assertGreater(outside.stat().st_size, 0)
            self.assertFalse(fixture["main"].source.exists())

    def ovr_21(self) -> None:
        self.assertEqual(len(ARC.SECOND_COPY_MAPPINGS), 13)
        self.assertEqual(len({source for source, _ in ARC.SECOND_COPY_MAPPINGS}), 13)
        self.assertEqual(len({prepared for _, prepared in ARC.SECOND_COPY_MAPPINGS}), 13)
        production = ARC.ArchiveService()
        strict = [candidate for candidate in ARC.INITIAL_CANDIDATES
                  if candidate not in {
                      ARC.RETAIL_BACKUP_CANDIDATE,
                      ARC.RETAIL_BACKUP_MANIFEST_CANDIDATE}]
        self.assertEqual(len(strict), 6)
        for candidate in strict:
            fake = {
                "transaction_id": ARC.SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
                "candidate_id": candidate.ident, "source": str(candidate.source),
                "source_identity": list(ARC.SOURCE_ROOT_OVERLAY_IDENTITY),
                "source_parent_identity": list(ARC.SOURCE_ROOT_OVERLAY_PARENT_IDENTITY),
                "allowlist_version": ARC.ALLOWLIST_VERSION,
                "source_tree_sha256": ARC.SOURCE_ROOT_OVERLAY_SEMANTIC_TREE_SHA256,
                "category": candidate.category, "data_class": candidate.data_class,
                "deletion_rule": candidate.deletion_rule,
            }
            self.assertIsNone(production.source_root_overlay_policy_for_record(fake))
            self.assertIsNone(production.provenance_overlay_policy_for_record(fake))

    def ovr_22(self) -> None:
        def add_foreign_xattr(fd: int, name: str) -> None:
            values = ARC.xattrs_fd(fd)
            values[name] = "Zm9yZWlnbg=="
            ARC.restore_xattrs_fd(fd, values)

        windows = (
            "after_retirement_manifest_preflight",
            "after_retirement_metadata_prepared",
            "before_final_retirement_boundary",
        )
        for window in windows:
            with self.subTest(window=window):
                with self.source_root_overlay_transaction(
                        f"toctou-{window}") as fixture:
                    transaction = fixture["transaction"]
                    root = fixture["transaction_root"]

                    if window == "after_retirement_manifest_preflight":
                        def mutate(pinned_fd: int, **_: object) -> None:
                            child = os.open(
                                ARC.SECOND_COPY_MAPPINGS[0][0],
                                os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pinned_fd)
                            try:
                                add_foreign_xattr(
                                    child, "com.openxray.manifest-prepare-race")
                            finally:
                                os.close(child)
                    else:
                        def mutate(file_fd: int, **_: object) -> None:
                            add_foreign_xattr(
                                file_fd, f"com.openxray.{window}")

                    self.service.hooks[window] = mutate
                    with mock.patch.object(
                            os, "ftruncate", wraps=os.ftruncate) as truncating:
                        with self.assertRaises(ARC.ArchiveError):
                            self.drain(transaction)
                        self.assertEqual(truncating.call_count, 0)
                    self.service.hooks.clear()
                    self.assertEqual(
                        self.tree_snapshot(fixture["main"].source),
                        fixture["physical"])
                    self.assertFalse((root / ARC.RECORD_NAMES["source-deleted"]).exists())
                    self.assertFalse((root / ARC.RECORD_NAMES["deletion-receipt"]).exists())

    def ovr_23(self) -> None:
        crash_points = (
            ("first-truncate", "truncate", 0, "PARTIAL_RETIREMENT"),
            ("middle-truncate", "truncate", 2, "PARTIAL_RETIREMENT"),
            ("after-010", "after_source_deleted_record", None,
             "RETIRED_TOMBSTONE"),
            ("before-011", "after_external_deletion_receipt", None,
             "RETIRED_TOMBSTONE"),
        )
        for tag, hook, index, expected_state in crash_points:
            with self.subTest(crash=tag):
                with self.source_root_overlay_transaction(
                        f"stage-aware-{tag}") as fixture:
                    transaction = fixture["transaction"]
                    if hook == "truncate":
                        assert index is not None
                        self.fork_crash_after_truncate(transaction, index)
                    else:
                        self.fork_crash_drain(transaction, hook)
                    proof = self.service.verify_production_state(transaction)
                    self.assertEqual(proof["status"], "PASS")
                    self.assertEqual(proof["retirement_state"], expected_state)
                    self.assertEqual(self.drain(transaction)["status"], "PASS")
                    self.assertTrue((fixture["transaction_root"] /
                                     ARC.RECORD_NAMES["deletion-receipt"]).exists())

        with self.source_root_overlay_transaction("stage-chain-mutation") as fixture:
            transaction = fixture["transaction"]
            self.fork_crash_after_truncate(transaction, 0)
            stage_009 = (fixture["transaction_root"] /
                         ARC.RECORD_NAMES["retirement-started"])
            changed = ARC.strict_json_loads(stage_009.read_text(encoding="ascii"))
            changed["proof_hash"] = "0" * 64
            stage_009.write_bytes(ARC.canonical_json(changed))
            os.chmod(stage_009, 0o600)
            with self.assertRaises(ARC.ArchiveError):
                self.service.verify_production_state(transaction)
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RECORD_NAMES["source-deleted"]).exists())

        with self.source_root_overlay_transaction("tombstone-mutation") as fixture:
            transaction = fixture["transaction"]
            self.fork_crash_drain(transaction, "after_source_deleted_record")
            intent = ARC.strict_json_loads((
                fixture["transaction_root"] / ARC.RECORD_NAMES["delete-intent"]
            ).read_text(encoding="ascii"))
            retired = ARC.strict_json_loads((
                fixture["transaction_root"] / ARC.RECORD_NAMES["source-deleted"]
            ).read_text(encoding="ascii"))
            target = (self.settings.queue_root / intent["quarantine_namespace"] /
                      retired["tombstone_name"] / ".DS_Store")
            target.chmod(0o600)
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                ARC.apply_xattrs_fd(
                    descriptor, {"com.openxray.tombstone-race": "dGFtcGVy"})
            finally:
                os.close(descriptor)
            with self.assertRaisesRegex(ARC.ArchiveError, "tombstone differs"):
                self.service.verify_production_state(transaction)
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RECORD_NAMES["deletion-receipt"]).exists())

    def ovr_24(self) -> None:
        with self.source_root_overlay_transaction("late-directory-xattr") as fixture:
            transaction = fixture["transaction"]
            def mutate_root_directory(root_fd: int, **_: object) -> None:
                ARC.apply_xattrs_fd(
                    root_fd,
                    {"com.openxray.late-directory-race": "Zm9yZWlnbg=="})
            self.service.hooks["before_final_retirement_boundary"] = \
                mutate_root_directory
            with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
                with self.assertRaisesRegex(
                        ARC.ArchiveError, "prepared retirement tree"):
                    self.drain(transaction)
                self.assertEqual(truncating.call_count, 0)
            self.service.hooks.clear()
            self.assertEqual(
                self.tree_snapshot(fixture["main"].source), fixture["physical"])
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RECORD_NAMES["source-deleted"]).exists())

        self.settings.candidates = (self.candidate,)
        self.settings.test_retail_roles = ()
        self.settings.test_retail_inventory = None
        link = self.source / "late-link"
        link.symlink_to("nested/trace.bin")
        source_snapshot = self.tree_snapshot(self.source)
        transaction = self.enqueue()
        held = self.base / "held-original-link"
        def replace_symlink(root_fd: int, **_: object) -> None:
            os.rename("late-link", held, src_dir_fd=root_fd)
            os.symlink("report.txt", "late-link", dir_fd=root_fd)
        self.service.hooks["before_final_retirement_boundary"] = replace_symlink
        with mock.patch.object(os, "ftruncate", wraps=os.ftruncate) as truncating:
            with self.assertRaisesRegex(
                    ARC.ArchiveError, "prepared retirement tree|symlink"):
                self.drain(transaction)
            self.assertEqual(truncating.call_count, 0)
        self.service.hooks.clear()
        self.assertTrue(held.is_symlink())
        self.assertEqual(os.readlink(held), "nested/trace.bin")
        self.assertFalse((self.settings.queue_root / transaction /
                          ARC.RECORD_NAMES["source-deleted"]).exists())
        self.assertFalse((self.settings.queue_root / transaction /
                          ARC.RECORD_NAMES["deletion-receipt"]).exists())
        # The foreign replacement cannot be safely removed to republish the
        # original tree, so both it and the original symlink remain retained.
        intent = ARC.strict_json_loads((
            self.settings.queue_root / transaction / ARC.RECORD_NAMES["delete-intent"]
        ).read_text(encoding="ascii"))
        quarantined = (self.settings.queue_root / intent["quarantine_namespace"] /
                       intent["retired_name"])
        self.assertTrue((quarantined / "late-link").is_symlink())
        self.assertEqual(os.readlink(quarantined / "late-link"), "report.txt")
        self.assertIn("late-link", {row["path"] for row in source_snapshot["entries"]})

    def ovr_25(self) -> None:
        for mutation in ("add", "remove"):
            with self.subTest(inventory=mutation):
                with self.source_root_overlay_transaction(
                        f"late-inventory-{mutation}") as fixture:
                    transaction = fixture["transaction"]
                    held = self.base / f"held-inventory-{mutation}"
                    def mutate_inventory(root_fd: int, **_: object) -> None:
                        if mutation == "add":
                            descriptor = os.open(
                                "foreign-added", os.O_WRONLY | os.O_CREAT |
                                os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
                            try:
                                os.write(descriptor, b"foreign inventory\n")
                            finally:
                                os.close(descriptor)
                        else:
                            os.rename(".DS_Store", held, src_dir_fd=root_fd)
                    self.service.hooks["before_final_retirement_boundary"] = \
                        mutate_inventory
                    with mock.patch.object(
                            os, "ftruncate", wraps=os.ftruncate) as truncating:
                        with self.assertRaisesRegex(
                                ARC.ArchiveError, "prepared retirement tree"):
                            self.drain(transaction)
                        self.assertEqual(truncating.call_count, 0)
                    self.service.hooks.clear()
                    self.assertFalse((fixture["transaction_root"] /
                                      ARC.RECORD_NAMES["source-deleted"]).exists())
                    self.assertFalse((fixture["transaction_root"] /
                                      ARC.RECORD_NAMES["deletion-receipt"]).exists())
                    if mutation == "add":
                        intent = ARC.strict_json_loads((
                            fixture["transaction_root"] /
                            ARC.RECORD_NAMES["delete-intent"]
                        ).read_text(encoding="ascii"))
                        retained = (self.settings.queue_root /
                                    intent["quarantine_namespace"] /
                                    intent["retired_name"] / "foreign-added")
                        self.assertEqual(retained.read_bytes(), b"foreign inventory\n")
                    else:
                        self.assertEqual(
                            held.read_bytes(), b"historical finder metadata\n")

    def ovr_26(self) -> None:
        for trigger in ("root", "first-child"):
            with self.subTest(mutation_after=trigger):
                with self.source_root_overlay_transaction(
                        f"closure-{trigger}") as fixture:
                    transaction = fixture["transaction"]
                    mutated = {"value": False}
                    def mutate_after_first_snapshot(
                            path: str, root_fd: int, **_: object) -> None:
                        selected = path == "." if trigger == "root" else path != "."
                        if mutated["value"] or not selected:
                            return
                        descriptor = os.open(
                            "foreign-empty", os.O_WRONLY | os.O_CREAT |
                            os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
                        os.close(descriptor)
                        mutated["value"] = True
                    self.service.hooks[
                        "after_detailed_retirement_directory_snapshot"] = \
                        mutate_after_first_snapshot
                    with mock.patch.object(
                            os, "ftruncate", wraps=os.ftruncate) as truncating:
                        with self.assertRaisesRegex(
                                ARC.ArchiveError,
                                "retirement closure inventory"):
                            self.drain(transaction)
                        self.assertEqual(truncating.call_count, 0)
                    self.service.hooks.clear()
                    self.assertTrue(mutated["value"])
                    root = fixture["transaction_root"]
                    self.assertFalse((root /
                                      ARC.RECORD_NAMES["source-deleted"]).exists())
                    self.assertFalse((root /
                                      ARC.RECORD_NAMES["deletion-receipt"]).exists())
                    intent = ARC.strict_json_loads((
                        root / ARC.RECORD_NAMES["delete-intent"]
                    ).read_text(encoding="ascii"))
                    retained = (self.settings.queue_root /
                                intent["quarantine_namespace"] /
                                intent["retired_name"])
                    self.assertEqual(
                        (retained / "foreign-empty").read_bytes(), b"")
                    self.assertEqual(
                        (retained / ".DS_Store").read_bytes(),
                        b"historical finder metadata\n")

        implementation = Path(ARC.__file__).read_text(encoding="utf-8")
        closure_start = implementation.index(
            "    def close_final_prepared_retirement_tree(")
        closure_end = implementation.index(
            "    def validate_final_prepared_retirement_tree(", closure_start)
        self.assertNotIn("self.hook", implementation[closure_start:closure_end])
        validation = implementation.index(
            "        first_guard = self.validate_final_prepared_retirement_tree")
        first_fstat = implementation.index(
            "            final = os.fstat(first.fd)", validation)
        between = implementation[validation:first_fstat]
        self.assertNotIn("self.hook", between)
        self.assertNotIn("final_retirement_boundary", between)

    def assert_same_name_replacement_closed(self, kind: str) -> None:
        if kind == "symlink":
            self.settings.candidates = (self.candidate,)
            self.settings.test_retail_roles = ()
            self.settings.test_retail_inventory = None
            (self.source / "late-link").symlink_to("nested/trace.bin")
            transaction = self.enqueue()
            transaction_root = self.settings.queue_root / transaction
            context = nullcontext({
                "transaction": transaction,
                "transaction_root": transaction_root,
                "source": self.source,
            })
            child_name = "late-link"
        else:
            context = self.source_root_overlay_transaction(
                f"same-name-{kind}")
            child_name = "resources" if kind == "directory" else ".DS_Store"

        with context as fixture:
            transaction = fixture["transaction"]
            transaction_root = fixture["transaction_root"]
            source = fixture.get("main", self.candidate).source
            if kind == "directory":
                original_snapshot = self.tree_snapshot(source / child_name)
            elif kind == "regular":
                original_bytes = (source / child_name).read_bytes()
            else:
                original_target = os.readlink(source / child_name)

            armed = {"value": False}
            replaced = {"value": False}
            retained_paths: dict[str, Path] = {}

            def arm_after_detailed_root(path: str, **_: object) -> None:
                if path == ".":
                    armed["value"] = True

            original_snapshotter = ARC.stable_retirement_snapshot

            def snapshot_then_replace(
                    fd: int, entry_kind: str, path: str,
                    **kwargs: object) -> dict[str, object]:
                snapshot = original_snapshotter(fd, entry_kind, path, **kwargs)
                if (not armed["value"] or replaced["value"]
                        or path != "." or entry_kind != "directory"):
                    return snapshot
                intent = ARC.strict_json_loads((
                    transaction_root / ARC.RECORD_NAMES["delete-intent"]
                ).read_text(encoding="ascii"))
                namespace = (self.settings.queue_root /
                             intent["quarantine_namespace"])
                held = self.base / f"held-original-{kind}"
                os.rename(child_name, held, src_dir_fd=fd)
                if kind == "directory":
                    os.mkdir(child_name, 0o700, dir_fd=fd)
                    replacement = ARC.open_leaf(fd, child_name, "directory")
                    try:
                        foreign = os.open(
                            "foreign.txt", os.O_WRONLY | os.O_CREAT |
                            os.O_EXCL | os.O_NOFOLLOW, 0o600,
                            dir_fd=replacement)
                        try:
                            os.write(foreign, b"foreign directory replacement\n")
                            os.fsync(foreign)
                        finally:
                            os.close(foreign)
                    finally:
                        os.close(replacement)
                elif kind == "regular":
                    foreign = os.open(
                        child_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                        os.O_NOFOLLOW, 0o600, dir_fd=fd)
                    try:
                        os.write(foreign, b"foreign regular replacement\n")
                        os.fsync(foreign)
                    finally:
                        os.close(foreign)
                else:
                    os.symlink("report.txt", child_name, dir_fd=fd)
                retained_paths["namespace"] = namespace
                retained_paths["held"] = held
                retained_paths["root"] = namespace / intent["retired_name"]
                replaced["value"] = True
                armed["value"] = False
                return snapshot

            self.service.hooks[
                "after_detailed_retirement_directory_snapshot"] = \
                arm_after_detailed_root
            with mock.patch.object(
                    ARC, "stable_retirement_snapshot",
                    side_effect=snapshot_then_replace), mock.patch.object(
                        os, "ftruncate", wraps=os.ftruncate) as truncating:
                with self.assertRaisesRegex(
                        ARC.ArchiveError,
                        "retirement closure child binding|prepared retirement"):
                    self.drain(transaction)
                self.assertEqual(truncating.call_count, 0)
            self.service.hooks.clear()

            self.assertTrue(replaced["value"])
            self.assertFalse((transaction_root /
                              ARC.RECORD_NAMES["source-deleted"]).exists())
            self.assertFalse((transaction_root /
                              ARC.RECORD_NAMES["deletion-receipt"]).exists())
            self.assertFalse(source.exists())
            held = retained_paths["held"]
            retained_root = retained_paths["root"]
            if kind == "directory":
                self.assertEqual(
                    ARC.canonical_json(self.tree_snapshot(held)),
                    ARC.canonical_json(original_snapshot))
                self.assertEqual(
                    (retained_root / child_name / "foreign.txt").read_bytes(),
                    b"foreign directory replacement\n")
            elif kind == "regular":
                self.assertEqual(held.read_bytes(), original_bytes)
                self.assertEqual(
                    (retained_root / child_name).read_bytes(),
                    b"foreign regular replacement\n")
            else:
                self.assertTrue(held.is_symlink())
                self.assertEqual(os.readlink(held), original_target)
                self.assertTrue((retained_root / child_name).is_symlink())
                self.assertEqual(
                    os.readlink(retained_root / child_name), "report.txt")

    def ovr_27(self) -> None:
        self.assert_same_name_replacement_closed("directory")

    def ovr_28(self) -> None:
        self.assert_same_name_replacement_closed("symlink")

    def ovr_29(self) -> None:
        self.assert_same_name_replacement_closed("regular")

    def ovr_30(self) -> None:
        """Historical d0 is data, not a stored-proof-controlled switch."""
        semantic, _, _, _, _, _ = self.overlay_fixture()
        physical = copy.deepcopy(semantic)
        root = next(row for row in physical["entries"] if row["path"] == ".")
        root["xattrs"][ARC.PROVENANCE_OVERLAY_XATTR] = \
            ARC.PROVENANCE_OVERLAY_VALUE
        physical = self.rebuild_manifest(physical)
        fixture_policy = ARC.SourceRootOverlayPolicy(
            1, ARC.PROVENANCE_OVERLAY_XATTR, ARC.PROVENANCE_OVERLAY_VALUE,
            "b" * 32, "historical-proof-fixture", "/historical-source-root",
            tuple(semantic["entries"][0]["local_identity"]), (1, 2),
            ARC.ALLOWLIST_VERSION, len(semantic["entries"]), semantic["files"],
            semantic["directories"], 1,
            ARC.sha256_bytes(ARC.canonical_json(["."])),
            semantic["tree_sha256"], ARC.manifest_canonical_sha256(semantic),
            physical["tree_sha256"], ARC.manifest_canonical_sha256(physical))

        historical = ARC.reconstruct_source_root_overlay_proof(
            semantic, physical, policy=fixture_policy,
            authorization_sha256=
            ARC.SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256)
        self.assertEqual(
            historical["production_authorization_sha256"],
            ARC.SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256)
        self.assertEqual(
            historical["proof_hash"],
            ARC.source_root_overlay_proof_hash(historical))
        self.assertEqual(
            ARC.reconstruct_source_root_overlay_proof(
                semantic, physical, policy=fixture_policy,
                authorization_sha256=
                ARC.SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256,
                stored_proof=historical),
            historical)

        forged = copy.deepcopy(historical)
        forged["production_authorization_sha256"] = "0" * 64
        with self.assertRaisesRegex(ARC.ArchiveError, "proof differs"):
            ARC.reconstruct_source_root_overlay_proof(
                semantic, physical, policy=fixture_policy,
                authorization_sha256=
                ARC.SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256,
                stored_proof=forged)
        self_consistent = ARC.reconstruct_source_root_overlay_proof(
            semantic, physical, policy=fixture_policy,
            authorization_sha256="f" * 64)
        with self.assertRaisesRegex(ARC.ArchiveError, "proof differs"):
            ARC.reconstruct_source_root_overlay_proof(
                semantic, physical, policy=fixture_policy,
                authorization_sha256=
                ARC.SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256,
                stored_proof=self_consistent)

        current = ARC.reconstruct_source_root_overlay_proof(
            semantic, physical, policy=fixture_policy,
            authorization_sha256=ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        self.assertEqual(
            current["production_authorization_sha256"],
            ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        self.assertNotEqual(current["proof_hash"], historical["proof_hash"])

        exact_record = {
            "transaction_id": ARC.SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
            "candidate_id": ARC.SOURCE_ROOT_OVERLAY_CANDIDATE_ID,
            "source": ARC.SOURCE_ROOT_OVERLAY_PATH,
            "source_identity": list(ARC.SOURCE_ROOT_OVERLAY_IDENTITY),
            "source_parent_identity": list(
                ARC.SOURCE_ROOT_OVERLAY_PARENT_IDENTITY),
            "allowlist_version": ARC.ALLOWLIST_VERSION,
            "source_tree_sha256": ARC.SOURCE_ROOT_OVERLAY_SEMANTIC_TREE_SHA256,
            "category": ARC.RETAIL_BACKUP_CANDIDATE.category,
            "data_class": ARC.RETAIL_BACKUP_CANDIDATE.data_class,
            "deletion_rule": ARC.RETAIL_BACKUP_CANDIDATE.deletion_rule,
        }
        select = lambda candidate_record=exact_record, **overrides: (  # noqa: E731
            ARC.source_root_overlay_historical_authorization_for_tuple(
                candidate_record, overrides.get(
                    "policy", ARC.PRODUCTION_SOURCE_ROOT_OVERLAY),
                source_quarantined_sha256=overrides.get(
                    "sha_008", ARC.RETIREMENT_OVERLAY_BASELINE_008_SHA256),
                retirement_started_sha256=overrides.get(
                    "sha_009", ARC.RETIREMENT_OVERLAY_BASELINE_009_SHA256),
                legacy_prefix_sha256=overrides.get(
                    "prefix", ARC.SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256)))
        self.assertEqual(
            select(), ARC.SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256)
        self.assertIsNone(select(sha_008="0" * 64))
        self.assertIsNone(select(sha_009="0" * 64))
        self.assertIsNone(select(prefix="0" * 64))
        changed_record = dict(exact_record)
        changed_record["candidate_id"] += "-forged"
        self.assertIsNone(select(changed_record))
        changed_policy = ARC.SourceRootOverlayPolicy(**{
            name: (ARC.PRODUCTION_SOURCE_ROOT_OVERLAY.version + 1
                   if name == "version"
                   else getattr(ARC.PRODUCTION_SOURCE_ROOT_OVERLAY, name))
            for name in ARC.PRODUCTION_SOURCE_ROOT_OVERLAY.__dataclass_fields__})
        self.assertIsNone(select(policy=changed_policy))

        # A non-production Settings/TestPlatform pair cannot activate d0 even
        # when fed the public exact tuple constants.
        transaction = self.enqueue()
        transaction_fd = self.transaction_fd(transaction)
        try:
            self.assertIsNone(
                self.service.historical_source_root_overlay_authorization(
                    transaction_fd, exact_record, {},
                    {"aggregate_sha256":
                     ARC.SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256}))
        finally:
            os.close(transaction_fd)

        # The stored 008 proof digest is an independent immutable binding.
        with self.source_root_overlay_transaction(
                "historical-proof-sha") as fixture:
            transaction = fixture["transaction"]
            self.service.hooks["after_source_quarantined_record"] = lambda **_: (
                _ for _ in ()).throw(ARC.InjectedCrash("durable-008"))
            with self.assertRaisesRegex(ARC.InjectedCrash, "durable-008"):
                self.drain(transaction)
            self.service.hooks.clear()
            stage_path = (fixture["transaction_root"] /
                          ARC.RECORD_NAMES["source-quarantined"])
            stage = ARC.strict_json_loads(stage_path.read_text(encoding="ascii"))
            stage["source_root_overlay_proof_sha256"] = "0" * 64
            stage_path.write_bytes(ARC.canonical_json(stage))
            with self.assertRaisesRegex(
                    ARC.ArchiveError, "stored proof binding"):
                self.drain(transaction)

    # ROB-01..09 — one exact post-009 retirement overlay recovery
    def rob_01(self) -> None:
        with self.retirement_overlay_transaction("pure") as fixture:
            policy = fixture["retirement_policy"]
            proof = ARC.compare_retirement_overlay_baseline(
                fixture["immutable_retirement"], fixture["current_retirement"],
                policy=policy,
                production_authorization_sha256=
                ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
            self.assertEqual(proof["changed_path"], ".DS_Store")
            self.assertEqual(proof["eligible_count"], len(fixture["eligible"]))
            comparator_fields = {
                "immutable_manifest_sha256", "immutable_tree_sha256",
                "current_manifest_sha256", "current_tree_sha256",
                "changed_path", "changed_identity", "changed_old_xattrs",
                "changed_new_xattrs", "changed_count",
                "changed_paths_sha256", "entry_count", "file_count",
                "directory_count", "symlink_count", "eligible_count",
                "eligible_paths_sha256", "inherited_paths_sha256",
                "runtime_xattr_name",
            }
            for field in comparator_fields:
                value = getattr(policy, field)
                if isinstance(value, tuple):
                    changed = (*value, ("foreign", "value")) \
                        if field.endswith("xattrs") else (value[0] + 1, value[1])
                elif isinstance(value, int):
                    changed = value + 1
                else:
                    changed = value + "-changed"
                replacement = ARC.RetirementOverlayBaselinePolicy(**{
                    name: changed if name == field else getattr(policy, name)
                    for name in policy.__dataclass_fields__})
                with self.subTest(field=field), self.assertRaises(
                        ARC.ArchiveError):
                    ARC.compare_retirement_overlay_baseline(
                        fixture["immutable_retirement"],
                        fixture["current_retirement"], policy=replacement,
                        production_authorization_sha256=
                        ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)

    def rob_02(self) -> None:
        with self.retirement_overlay_transaction("record") as fixture:
            transaction = fixture["transaction"]
            self.service.hooks[
                "after_retirement_overlay_baseline_record"] = lambda **_: (
                    _ for _ in ()).throw(ARC.InjectedCrash("after-009a"))
            with self.assertRaisesRegex(ARC.InjectedCrash, "after-009a"):
                self.drain(transaction)
            path = fixture["transaction_root"] / \
                ARC.RETIREMENT_OVERLAY_BASELINE_RECORD
            immutable = path.read_bytes()
            self.service.hooks.clear()
            self.assertEqual(self.drain(transaction)["status"], "PASS")
            self.assertEqual(path.read_bytes(), immutable)

        with self.retirement_overlay_transaction("short-write") as fixture:
            self.platform.write_limit = 7
            self.platform.fail_write_after = 7
            with self.assertRaises(OSError):
                self.drain(fixture["transaction"])
            self.platform.write_limit = None
            self.platform.fail_write_after = None
            self.platform.written = 0
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RETIREMENT_OVERLAY_BASELINE_RECORD).exists())
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")

    def rob_03(self) -> None:
        with self.retirement_overlay_transaction("no-metadata-setters") as fixture:
            self.service.hooks[
                "after_retirement_overlay_baseline_record"] = lambda **_: (
                    _ for _ in ()).throw(ARC.InjectedCrash("009a-ready"))
            with self.assertRaisesRegex(ARC.InjectedCrash, "009a-ready"):
                self.drain(fixture["transaction"])
            self.service.hooks.clear()
            source_identities = {
                tuple(row["local_identity"])
                for row in fixture["current_retirement"]["entries"]}
            original_fchmod = os.fchmod
            source_fchmod: list[tuple[int, int]] = []
            def checked_fchmod(fd: int, mode: int) -> None:
                if ARC.identity(os.fstat(fd)) in source_identities:
                    source_fchmod.append(ARC.identity(os.fstat(fd)))
                original_fchmod(fd, mode)
            with mock.patch.object(
                    ARC, "clear_delete_protection_fd",
                    side_effect=AssertionError("metadata preparation forbidden")), \
                    mock.patch.object(
                        os, "fchmod",
                        side_effect=checked_fchmod), \
                    mock.patch.object(
                        ARC, "restore_acl_text_fd",
                        side_effect=AssertionError("ACL setter forbidden")), \
                    mock.patch.object(
                        ARC, "restore_bsd_flags_fd",
                        side_effect=AssertionError("flags setter forbidden")):
                self.assertEqual(
                    self.drain(fixture["transaction"])["status"], "PASS")
            self.assertEqual(source_fchmod, [])

    def rob_04(self) -> None:
        for fraction in ("zero", "first", "middle", "last", "all"):
            with self.subTest(runtime=fraction):
                with self.retirement_overlay_transaction(
                        f"runtime-{fraction}") as fixture:
                    eligible = fixture["eligible"]
                    selected = {
                        "zero": [], "first": eligible[:1],
                        "middle": eligible[len(eligible)//2:len(eligible)//2+1],
                        "last": eligible[-1:], "all": eligible,
                    }[fraction]
                    selected_set = set(selected)
                    def add_runtime(path: str, file_fd: int, **_: object) -> None:
                        if path not in selected_set:
                            return
                        values = ARC.xattrs_fd(file_fd)
                        values[fixture["retirement_policy"].runtime_xattr_name] = \
                            fixture["retirement_policy"].runtime_xattr_value
                        ARC.restore_xattrs_fd(file_fd, values)
                    self.service.hooks["after_retirement_truncate"] = add_runtime
                    self.assertEqual(
                        self.drain(fixture["transaction"])["status"], "PASS")
                    retired = ARC.strict_json_loads((
                        fixture["transaction_root"] /
                        ARC.RECORD_NAMES["source-deleted"]
                    ).read_text(encoding="ascii"))
                    proof = retired["retirement_runtime_overlay_proof"]
                    self.assertEqual(proof["observed_paths"], sorted(selected))

    def rob_05(self) -> None:
        for mutation in ("wrong-value", "directory", "foreign-xattr"):
            with self.subTest(mutation=mutation):
                with self.retirement_overlay_transaction(
                        f"invalid-{mutation}") as fixture:
                    changed = {"value": False}
                    def mutate(path: str, file_fd: int, root_fd: int | None = None,
                               **_: object) -> None:
                        if changed["value"]:
                            return
                        if mutation == "directory":
                            target = root_fd if root_fd is not None else file_fd
                            values = ARC.xattrs_fd(target)
                            values[fixture["retirement_policy"].runtime_xattr_name] = \
                                fixture["retirement_policy"].runtime_xattr_value
                        else:
                            values = ARC.xattrs_fd(file_fd)
                            name = fixture["retirement_policy"].runtime_xattr_name \
                                if mutation == "wrong-value" \
                                else "com.openxray.foreign-runtime"
                            values[name] = "d3Jvbmc="
                        ARC.restore_xattrs_fd(
                            root_fd if mutation == "directory" and root_fd is not None
                            else file_fd, values)
                        changed["value"] = True
                    self.service.hooks["after_retirement_truncate"] = mutate
                    with self.assertRaises(ARC.ArchiveError):
                        self.drain(fixture["transaction"])
                    self.assertFalse((fixture["transaction_root"] /
                                      ARC.RECORD_NAMES["source-deleted"]).exists())

    def rob_06(self) -> None:
        for checkpoint in (
                "after_retirement_overlay_baseline_record",
                "after_retirement_ftruncate_before_metadata_restore",
                "after_source_deleted_record",
                "after_external_deletion_receipt"):
            with self.subTest(checkpoint=checkpoint):
                with self.retirement_overlay_transaction(
                        f"crash-{checkpoint}") as fixture:
                    self.fork_crash_drain(fixture["transaction"], checkpoint)
                    self.assertEqual(
                        self.drain(fixture["transaction"])["status"], "PASS")
                    self.assertEqual(
                        self.drain(fixture["transaction"])["status"], "PASS")

    def rob_07(self) -> None:
        for mutation in ("destination", "remount", "prepared"):
            with self.subTest(mutation=mutation):
                with self.retirement_overlay_transaction(
                        f"pretruncate-{mutation}") as fixture:
                    transaction = fixture["transaction"]
                    archived = (self.mount / ARC.ARCHIVE_ROOT / "backups" /
                                f"{transaction}-{fixture['main'].source.name}")
                    old: Path | None = None
                    def mutate_boundary(**_: object) -> None:
                        nonlocal old
                        if mutation == "destination":
                            (archived / ARC.SECOND_COPY_MAPPINGS[0][0]).write_bytes(
                                b"changed destination\n")
                        elif mutation == "prepared":
                            mapping = ARC.SECOND_COPY_MAPPINGS[0][1]
                            (self.settings.retail_prepared_root / mapping).write_bytes(
                                b"changed prepared\n")
                        else:
                            old = self.base / "retirement-overlay-old-volume"
                            self.mount.rename(old)
                            self.mount.mkdir(mode=0o700)
                    self.service.hooks["before_final_retirement_boundary"] = \
                        mutate_boundary
                    with mock.patch.object(
                            os, "ftruncate", wraps=os.ftruncate) as truncating:
                        if mutation == "remount":
                            result = self.drain(transaction)
                            self.assertEqual(result["status"], "DEFERRED_VOLUME")
                        else:
                            with self.assertRaises(ARC.ArchiveError):
                                self.drain(transaction)
                        self.assertEqual(truncating.call_count, 0)
                    if old is not None:
                        shutil.rmtree(self.mount)
                        old.rename(self.mount)

    def rob_08(self) -> None:
        self.assertTrue(ARC.PRODUCTION_REVIEW_AUTHORIZED)
        policy = ARC.PRODUCTION_RETIREMENT_OVERLAY_BASELINE
        self.assertEqual(
            policy.source_quarantined_sha256,
            "9b0693a18c51d95ca90b08a4ccc4df1b692d6a7c32b15332bae99426a44d6b25")
        self.assertEqual(
            policy.retirement_started_sha256,
            "30de606ee732db5a8b578e7a838c5da311138ae323b412d74ce2de88944d6f15")
        self.assertEqual(
            policy.current_manifest_sha256,
            "5d10c29acce200c78433006c9e39003e383c805f99a7e05036baee4d58f9cad4")
        self.assertEqual(
            policy.current_tree_sha256,
            "d129322749104af3ddcdfb23fc5f9d5306e6c87f68715805db632f43964a8895")
        self.assertEqual(policy.changed_identity, (16777234, 25412239))
        for field in policy.__dataclass_fields__:
            value = getattr(policy, field)
            if isinstance(value, tuple):
                changed = (*value, ("x", "y")) if field.endswith("xattrs") \
                    else (value[0] + 1, value[1])
            elif isinstance(value, int):
                changed = value + 1
            else:
                changed = value + "-changed"
            replacement = ARC.RetirementOverlayBaselinePolicy(**{
                name: changed if name == field else getattr(policy, name)
                for name in policy.__dataclass_fields__})
            with mock.patch.object(
                    ARC, "PRODUCTION_RETIREMENT_OVERLAY_BASELINE", replacement):
                self.assertFalse(ARC.reviewed_allowlist_authorized(
                    ARC.ALLOWLIST_VERSION, ARC.INITIAL_CANDIDATES), field)

    def rob_09(self) -> None:
        """The reviewed immutable gate A and current gate B are independent."""
        gate_b_hash = "b" * 64
        gate_c_hash = "c" * 64

        def select_gate_b(*, tag: str, age: float = 1) -> Path:
            self.platform.gate_hash = gate_b_hash
            self.platform.current_gate_state = {
                "head": "2" * 40,
                "status_sha256": "3" * 64,
                "diff_binary_head_sha256": "4" * 64,
                "untracked": [],
            }
            return self.write_gate_receipt(
                source_hash=gate_b_hash,
                ended=self.platform.clock - age,
                receipt_name=f"gate-{int(self.platform.clock)}-456-{tag}.json")

        with self.retirement_overlay_transaction("gate-a-to-b") as fixture:
            transaction = fixture["transaction"]
            root = fixture["transaction_root"]
            stage_008_path = root / ARC.RECORD_NAMES["source-quarantined"]
            stage_009_path = root / ARC.RECORD_NAMES["retirement-started"]
            immutable_008 = stage_008_path.read_bytes()
            immutable_009 = stage_009_path.read_bytes()
            gate_a = ARC.strict_json_loads(
                immutable_008.decode("ascii"))["gate_proof"]
            gate_a_path = self.settings.gate_log_root / gate_a["receipt_name"]
            immutable_gate_a_receipt = gate_a_path.read_bytes()

            # Gate A is now historical and older than 24 hours.  A distinct,
            # exact gate B authorizes 009a for the current code/worktree.
            self.platform.clock += self.settings.gate_max_age_seconds + 60
            gate_b_path = select_gate_b(tag="1")
            self.service.hooks[
                "after_retirement_overlay_baseline_record"] = lambda **_: (
                    _ for _ in ()).throw(ARC.InjectedCrash("durable-gate-b"))
            with self.assertRaisesRegex(ARC.InjectedCrash, "durable-gate-b"):
                self.drain(transaction)
            self.service.hooks.clear()

            baseline_path = root / ARC.RETIREMENT_OVERLAY_BASELINE_RECORD
            immutable_baseline = baseline_path.read_bytes()
            baseline = ARC.strict_json_loads(immutable_baseline.decode("ascii"))
            self.assertEqual(
                baseline["immutable_008_gate_proof"], gate_a)
            self.assertEqual(
                baseline["immutable_008_gate_proof_sha256"],
                ARC.sha256_bytes(ARC.canonical_json(gate_a)))
            self.assertEqual(
                baseline["current_009a_gate_proof"]["gate_source_sha256"],
                gate_b_hash)
            self.assertEqual(
                baseline["current_009a_gate_proof"]["receipt_name"],
                gate_b_path.name)
            self.assertEqual(
                baseline["production_authorization_sha256"],
                ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
            self.assertEqual(
                baseline["baseline_proof"]["production_authorization_sha256"],
                ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
            self.assertNotEqual(
                baseline["immutable_008_gate_proof"]["gate_source_sha256"],
                baseline["current_009a_gate_proof"]["gate_source_sha256"])

            # A later semantically equivalent B2 may authorize resume without
            # rewriting immutable 008, 009 or 009a.
            self.platform.clock += 30
            gate_b2_path = select_gate_b(tag="2")
            self.assertEqual(self.drain(transaction)["status"], "PASS")
            self.assertEqual(self.drain(transaction)["status"], "PASS")
            self.assertEqual(stage_008_path.read_bytes(), immutable_008)
            self.assertEqual(stage_009_path.read_bytes(), immutable_009)
            self.assertEqual(baseline_path.read_bytes(), immutable_baseline)
            self.assertEqual(gate_a_path.read_bytes(), immutable_gate_a_receipt)
            self.assertEqual(
                self.service.matching_full_gate_receipt()["receipt_name"],
                gate_b2_path.name)
            retired = ARC.strict_json_loads((
                root / ARC.RECORD_NAMES["source-deleted"]
            ).read_text(encoding="ascii"))
            receipt = ARC.strict_json_loads((
                root / ARC.RECORD_NAMES["deletion-receipt"]
            ).read_text(encoding="ascii"))
            self.assertEqual(
                retired["retirement_overlay_baseline_stage_sha256"],
                ARC.sha256_bytes(immutable_baseline))
            self.assertEqual(
                receipt["retirement_overlay_baseline_stage_sha256"],
                ARC.sha256_bytes(immutable_baseline))

        for failure_index, failure in enumerate(
                ("stale", "forged", "mismatched"), start=3):
            with self.subTest(gate_b=failure):
                # Each fixture must first seal its own current immutable A;
                # residue from a prior rejected B remains readable but
                # ineligible, as it would in the production gate-log root.
                self.platform.gate_hash = "a" * 64
                self.platform.current_gate_state = {
                    "head": "1" * 40,
                    "status_sha256": "d" * 64,
                    "diff_binary_head_sha256": "e" * 64,
                    "untracked": [],
                }
                self.write_gate_receipt(
                    ended=self.platform.clock - 1,
                    receipt_name=(
                        f"gate-{int(self.platform.clock)}-123-"
                        f"{failure_index}.json"))
                with self.retirement_overlay_transaction(
                        f"gate-b-{failure}") as fixture:
                    transaction = fixture["transaction"]
                    root = fixture["transaction_root"]
                    self.platform.clock += self.settings.gate_max_age_seconds + 60
                    if failure == "stale":
                        select_gate_b(
                            tag="3",
                            age=self.settings.gate_max_age_seconds + 1)
                    else:
                        receipt_path = select_gate_b(tag="4")
                        if failure == "forged":
                            forged = ARC.strict_json_loads(
                                receipt_path.read_text(encoding="ascii"))
                            forged["log_sha256"] = "0" * 64
                            receipt_path.write_bytes(ARC.canonical_json(forged))
                        else:
                            self.platform.gate_hash = gate_c_hash
                    with mock.patch.object(
                            os, "ftruncate", wraps=os.ftruncate) as truncating:
                        with self.assertRaises(ARC.ArchiveError):
                            self.drain(transaction)
                        self.assertEqual(truncating.call_count, 0)
                    self.assertFalse((
                        root / ARC.RETIREMENT_OVERLAY_BASELINE_RECORD).exists())
                    self.assertFalse((
                        root / ARC.RECORD_NAMES["source-deleted"]).exists())
                    self.assertEqual(
                        ARC.canonical_json(self.tree_snapshot(
                            fixture["source_root"])),
                        ARC.canonical_json(fixture["current_retirement"]))

    # RPO-01..08 — immutable 009b pretruncate-open overlay
    def rpo_01(self) -> None:
        """The pure comparator admits only exact monotonic provenance."""
        with self.pretruncate_open_transaction("pure") as fixture:
            policy = fixture["pretruncate_policy"]
            baseline = fixture["retirement_baseline"]["current_source_manifest"]
            base = ARC.build_retirement_pretruncate_open_base_manifest(
                baseline, policy=policy)
            self.assertEqual(
                ARC.canonical_json(base),
                ARC.canonical_json(fixture["pretruncate_base"]))
            selected = fixture["pretruncate_eligible"][::2]
            entries = copy.deepcopy(base["entries"])
            for row in entries:
                if row["path"] in selected:
                    row["xattrs"][policy.runtime_xattr_name] = \
                        policy.runtime_xattr_value
            current = ARC.finish_manifest(entries)
            proof = ARC.compare_retirement_pretruncate_open_overlay(
                base, current, policy=policy,
                production_authorization_sha256=
                ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
            self.assertEqual(proof["observed_paths"], sorted(selected))
            self.assertEqual(
                proof["proof_hash"],
                ARC.retirement_pretruncate_open_proof_hash(proof))

            mutations = {
                "wrong-provenance": lambda row: row["xattrs"].__setitem__(
                    policy.runtime_xattr_name, "d3Jvbmc="),
                "extra-xattr": lambda row: row["xattrs"].__setitem__(
                    "com.openxray.foreign", "Zm9yZWlnbg=="),
                "content": lambda row: row.__setitem__("sha256", "0" * 64),
                "mode": lambda row: row.__setitem__("mode", row["mode"] ^ 0o100),
                "mtime": lambda row: row.__setitem__(
                    "mtime_ns", row["mtime_ns"] + 1),
                "acl": lambda row: row.__setitem__("acl", "foreign-acl"),
                "flags": lambda row: row.__setitem__("flags", row["flags"] + 1),
            }
            target = fixture["pretruncate_eligible"][0]
            for label, mutate in mutations.items():
                broken_entries = copy.deepcopy(base["entries"])
                broken = next(row for row in broken_entries
                              if row["path"] == target)
                mutate(broken)
                with self.subTest(mutation=label), self.assertRaises(
                        ARC.ArchiveError):
                    ARC.compare_retirement_pretruncate_open_overlay(
                        base, ARC.finish_manifest(broken_entries), policy=policy,
                        production_authorization_sha256=
                        ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
            removed = copy.deepcopy(base["entries"][:-1])
            with self.assertRaises(ARC.ArchiveError):
                ARC.compare_retirement_pretruncate_open_overlay(
                    base, ARC.finish_manifest(removed), policy=policy,
                    production_authorization_sha256=
                    ARC.REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)

    def rpo_02(self) -> None:
        """Zero/first/middle/last/all open additions bind 009b and 010/011."""
        for position in ("zero", "first", "middle", "last", "all"):
            with self.subTest(position=position):
                with self.pretruncate_open_transaction(
                        f"positions-{position}") as fixture:
                    eligible = fixture["pretruncate_eligible"]
                    selected = {
                        "zero": [], "first": eligible[:1],
                        "middle": eligible[len(eligible)//2:len(eligible)//2+1],
                        "last": eligible[-1:], "all": eligible,
                    }[position]
                    selected_set = set(selected)
                    pinned: list[int] = []
                    policy = fixture["pretruncate_policy"]

                    def on_open(path: str, file_fd: int, **_: object) -> None:
                        pinned.append(file_fd)
                        if path in selected_set:
                            values = ARC.xattrs_fd(file_fd)
                            values[policy.runtime_xattr_name] = \
                                policy.runtime_xattr_value
                            ARC.restore_xattrs_fd(file_fd, values)

                    def assert_pinned(**_: object) -> None:
                        self.assertEqual(len(pinned),
                                         fixture["pretruncate_base"]["files"])
                        for descriptor in pinned:
                            self.assertTrue(stat.S_ISREG(
                                os.fstat(descriptor).st_mode))

                    self.service.hooks[
                        "after_retirement_overlay_regular_open"] = on_open
                    self.service.hooks[
                        "before_final_retirement_boundary"] = assert_pinned
                    immutable = {
                        name: (fixture["transaction_root"] / name).read_bytes()
                        for name in (
                            ARC.RECORD_NAMES["source-quarantined"],
                            ARC.RECORD_NAMES["retirement-started"],
                            ARC.RETIREMENT_OVERLAY_BASELINE_RECORD)}
                    self.assertEqual(
                        self.drain(fixture["transaction"])["status"], "PASS")
                    open_record = ARC.strict_json_loads((
                        fixture["transaction_root"] /
                        ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD
                    ).read_text(encoding="ascii"))
                    self.assertEqual(open_record["observed_paths"], sorted(selected))
                    retired = ARC.strict_json_loads((
                        fixture["transaction_root"] /
                        ARC.RECORD_NAMES["source-deleted"]
                    ).read_text(encoding="ascii"))
                    receipt = ARC.strict_json_loads((
                        fixture["transaction_root"] /
                        ARC.RECORD_NAMES["deletion-receipt"]
                    ).read_text(encoding="ascii"))
                    stage_sha = ARC.sha256_bytes(ARC.canonical_json(open_record))
                    self.assertEqual(
                        retired["retirement_pretruncate_open_stage_sha256"],
                        stage_sha)
                    self.assertEqual(
                        receipt["retirement_pretruncate_open_stage_sha256"],
                        stage_sha)
                    runtime_observed = set(
                        retired["retirement_runtime_overlay_proof"][
                            "observed_paths"])
                    self.assertTrue(set(selected) <= runtime_observed)
                    self.assertIn(
                        fixture["pretruncate_changed_path"], runtime_observed)
                    for name, contents in immutable.items():
                        self.assertEqual(
                            (fixture["transaction_root"] / name).read_bytes(),
                            contents)

    def rpo_03(self) -> None:
        """Opening and immutable-sidecar crash points resume deterministically."""
        for hook in (
                "after_retirement_overlay_regular_open",
                "before_retirement_pretruncate_open_record",
                "after_retirement_pretruncate_open_record"):
            with self.subTest(crash=hook):
                with self.pretruncate_open_transaction(
                        f"crash-{hook}") as fixture:
                    self.fork_crash_drain(fixture["transaction"], hook)
                    self.assertEqual(
                        self.drain(fixture["transaction"])["status"], "PASS")
                    self.assertEqual(
                        self.drain(fixture["transaction"])["status"], "PASS")

        with self.pretruncate_open_transaction("short-write") as fixture:
            self.platform.write_limit = 11
            self.platform.fail_write_after = 11
            with self.assertRaises(OSError):
                self.drain(fixture["transaction"])
            self.platform.write_limit = None
            self.platform.fail_write_after = None
            self.platform.written = 0
            self.assertFalse((fixture["transaction_root"] /
                              ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD).exists())
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")

        with self.pretruncate_open_transaction("fsync") as fixture:
            self.service.hooks["before_fsync"] = lambda label, **_: setattr(
                self.platform, "current_label", label)
            self.platform.fail_sync_label = \
                ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD
            with self.assertRaises(ARC.ArchiveError):
                self.drain(fixture["transaction"])
            self.platform.fail_sync_label = None
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")

        with self.pretruncate_open_transaction("rename") as fixture:
            original = self.platform.rename_exclusive
            failed = {"value": False}
            def reject_sidecar(source_parent_fd: int, source: str,
                               destination_parent_fd: int,
                               destination: str) -> None:
                if (not failed["value"]
                        and destination
                        == ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD):
                    failed["value"] = True
                    raise ARC.ArchiveError("injected 009b rename failure")
                original(source_parent_fd, source,
                         destination_parent_fd, destination)
            with mock.patch.object(
                    self.platform, "rename_exclusive",
                    side_effect=reject_sidecar):
                with self.assertRaisesRegex(ARC.ArchiveError, "009b rename"):
                    self.drain(fixture["transaction"])
            self.assertTrue(failed["value"])
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")

    def rpo_04(self) -> None:
        """A durable 009b resumes both monotonic reopen and partial truncation."""
        with self.pretruncate_open_transaction("subset-reopen") as fixture:
            eligible = fixture["pretruncate_eligible"]
            first = set(eligible[:2])
            count = {"value": 0}
            policy = fixture["pretruncate_policy"]
            if not hasattr(os, "fork"):
                self.skipTest("009b open crash requires fork")
            pid = os.fork()
            if pid == 0:
                def crash_subset(path: str, file_fd: int, **_: object) -> None:
                    if path in first:
                        values = ARC.xattrs_fd(file_fd)
                        values[policy.runtime_xattr_name] = \
                            policy.runtime_xattr_value
                        ARC.restore_xattrs_fd(file_fd, values)
                        count["value"] += 1
                        if count["value"] == len(first):
                            os._exit(91)
                self.service.hooks[
                    "after_retirement_overlay_regular_open"] = crash_subset
                try:
                    self.drain(fixture["transaction"])
                except BaseException:
                    os._exit(92)
                os._exit(93)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.WEXITSTATUS(status), 91)
            self.service.hooks.clear()
            remaining = set(eligible) - first
            def add_remaining(path: str, file_fd: int, **_: object) -> None:
                if path in remaining:
                    values = ARC.xattrs_fd(file_fd)
                    values[policy.runtime_xattr_name] = \
                        policy.runtime_xattr_value
                    ARC.restore_xattrs_fd(file_fd, values)
            self.service.hooks[
                "after_retirement_overlay_regular_open"] = add_remaining
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")

        with self.pretruncate_open_transaction("partial-truncate") as fixture:
            self.fork_crash_drain(
                fixture["transaction"],
                "after_retirement_ftruncate_before_metadata_restore")
            open_path = fixture["transaction_root"] / \
                ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD
            immutable = open_path.read_bytes()
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")
            self.assertEqual(open_path.read_bytes(), immutable)
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")

    def rpo_05(self) -> None:
        """Fresh gate C, equivalent C2, and stale/forged variants are distinct."""
        gate_c_hash = "c" * 64
        with self.pretruncate_open_transaction("gate-c-to-c2") as fixture:
            gate_b = fixture["retirement_baseline"]["current_009a_gate_proof"]
            self.platform.gate_hash = gate_c_hash
            self.platform.current_gate_state = {
                "head": "5" * 40, "status_sha256": "6" * 64,
                "diff_binary_head_sha256": "7" * 64, "untracked": [],
            }
            gate_c = self.write_gate_receipt(
                source_hash=gate_c_hash, ended=self.platform.clock - 1,
                receipt_name="gate-2000000100-701-1.json")
            self.service.hooks[
                "after_retirement_pretruncate_open_record"] = lambda **_: (
                    _ for _ in ()).throw(ARC.InjectedCrash("durable-gate-c"))
            with self.assertRaisesRegex(ARC.InjectedCrash, "durable-gate-c"):
                self.drain(fixture["transaction"])
            self.service.hooks.clear()
            open_path = fixture["transaction_root"] / \
                ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD
            immutable = open_path.read_bytes()
            open_record = ARC.strict_json_loads(immutable.decode("ascii"))
            self.assertNotEqual(
                gate_b["gate_source_sha256"],
                open_record["current_009b_gate_proof"]["gate_source_sha256"])
            self.assertEqual(
                open_record["current_009b_gate_proof"]["receipt_name"],
                gate_c.name)
            self.platform.clock += self.settings.gate_max_age_seconds + 60
            with mock.patch.object(
                    os, "ftruncate", wraps=os.ftruncate) as truncating:
                with self.assertRaisesRegex(
                        ARC.ArchiveError, "no fresh matching"):
                    self.drain(fixture["transaction"])
                self.assertEqual(truncating.call_count, 0)
            self.platform.gate_hash = "d" * 64
            with self.assertRaises(ARC.ArchiveError):
                self.drain(fixture["transaction"])
            self.platform.gate_hash = gate_c_hash
            forged_c2 = self.write_gate_receipt(
                source_hash=gate_c_hash, ended=self.platform.clock - 1,
                receipt_name="gate-2000099999-702-1.json")
            forged_value = ARC.strict_json_loads(
                forged_c2.read_text(encoding="ascii"))
            forged_value["log_sha256"] = "0" * 64
            forged_c2.write_bytes(ARC.canonical_json(forged_value))
            with self.assertRaises(ARC.ArchiveError):
                self.drain(fixture["transaction"])
            forged_c2.unlink()
            gate_c2 = self.write_gate_receipt(
                source_hash=gate_c_hash, ended=self.platform.clock - 1,
                receipt_name="gate-2000100000-702-2.json")
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")
            self.assertEqual(open_path.read_bytes(), immutable)
            self.assertEqual(
                self.service.matching_full_gate_receipt()["receipt_name"],
                gate_c2.name)

        for failure_index, failure in enumerate(
                ("stale", "forged", "mismatched"), start=3):
            with self.subTest(failure=failure):
                self.platform.gate_hash = "a" * 64
                self.platform.current_gate_state = {
                    "head": "1" * 40, "status_sha256": "d" * 64,
                    "diff_binary_head_sha256": "e" * 64, "untracked": [],
                }
                self.write_gate_receipt(
                    ended=self.platform.clock - 1,
                    receipt_name=(
                        f"gate-{int(self.platform.clock)}-703-"
                        f"{failure_index}.json"))
                with self.pretruncate_open_transaction(
                        f"gate-c-{failure}") as fixture:
                    self.platform.clock += self.settings.gate_max_age_seconds + 60
                    self.platform.gate_hash = gate_c_hash
                    path = self.write_gate_receipt(
                        source_hash=gate_c_hash,
                        ended=(self.platform.clock -
                               (self.settings.gate_max_age_seconds + 1)
                               if failure == "stale" else self.platform.clock - 1),
                        receipt_name=(
                            f"gate-{int(self.platform.clock)}-704-"
                            f"{failure_index}.json"))
                    if failure == "forged":
                        value = ARC.strict_json_loads(path.read_text(encoding="ascii"))
                        value["log_sha256"] = "0" * 64
                        path.write_bytes(ARC.canonical_json(value))
                    elif failure == "mismatched":
                        self.platform.gate_hash = "d" * 64
                    with mock.patch.object(
                            os, "ftruncate", wraps=os.ftruncate) as truncating:
                        with self.assertRaises(ARC.ArchiveError):
                            self.drain(fixture["transaction"])
                        self.assertEqual(truncating.call_count, 0)
                    self.assertFalse((fixture["transaction_root"] /
                                      ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD).exists())

    def rpo_06(self) -> None:
        """009b cryptographically binds predecessors and every downstream receipt."""
        with self.pretruncate_open_transaction("bindings") as fixture:
            self.service.hooks[
                "after_retirement_pretruncate_open_record"] = lambda **_: (
                    _ for _ in ()).throw(ARC.InjectedCrash("inspect-009b"))
            with self.assertRaisesRegex(ARC.InjectedCrash, "inspect-009b"):
                self.drain(fixture["transaction"])
            self.service.hooks.clear()
            transaction_fd = self.transaction_fd(fixture["transaction"])
            try:
                open_record = self.service.load_retirement_pretruncate_open(
                    transaction_fd)
                self.assertIsNotNone(open_record)
                assert open_record is not None
                for field in (
                        "source_quarantined_stage_sha256",
                        "retirement_started_stage_sha256",
                        "retirement_overlay_baseline_stage_sha256",
                        "post_open_manifest_sha256",
                        "open_overlay_proof_sha256"):
                    broken = copy.deepcopy(open_record)
                    broken[field] = "0" * 64
                    with self.subTest(field=field), self.assertRaises(
                            ARC.ArchiveError):
                        self.service.validate_retirement_pretruncate_open_record(
                            transaction_fd, fixture["record"], broken,
                            require_current_gate=False)
            finally:
                os.close(transaction_fd)
            self.assertEqual(self.drain(fixture["transaction"])["status"], "PASS")
            retired_path = (fixture["transaction_root"] /
                            ARC.RECORD_NAMES["source-deleted"])
            retired = ARC.strict_json_loads(retired_path.read_text(encoding="ascii"))
            receipt = ARC.strict_json_loads((
                fixture["transaction_root"] /
                ARC.RECORD_NAMES["deletion-receipt"]
            ).read_text(encoding="ascii"))
            external = ARC.strict_json_loads((
                self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                f"{fixture['transaction']}-source-deletion.json"
            ).read_text(encoding="ascii"))
            for payload in (retired, receipt, external):
                self.assertIn(
                    "retirement_pretruncate_open_stage_sha256", payload)
                self.assertIn(
                    "retirement_pretruncate_open_overlay_proof_sha256", payload)
            broken = copy.deepcopy(retired)
            broken["retirement_pretruncate_open_stage_sha256"] = "0" * 64
            transaction_fd = self.transaction_fd(fixture["transaction"])
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.validate_retirement_overlay_downstream_binding(
                        transaction_fd, fixture["record"], broken,
                        retired["tombstone_manifest"])
            finally:
                os.close(transaction_fd)

    def rpo_07(self) -> None:
        """Partial states accept exact live/zero rows and reject every drift class."""
        with self.pretruncate_open_transaction("partial-validator") as fixture:
            policy = fixture["retirement_policy"]
            base = fixture["pretruncate_base"]
            target = fixture["pretruncate_eligible"][0]
            entries = copy.deepcopy(base["entries"])
            row = next(item for item in entries if item["path"] == target)
            row["xattrs"][policy.runtime_xattr_name] = policy.runtime_xattr_value
            live = ARC.finish_manifest(entries)
            self.assertEqual(
                self.service.validate_retirement_overlay_partial_manifest(
                    base, live, policy), 0)
            row["logical_bytes"] = 0
            row["allocated_bytes"] = 0
            row["sha256"] = ARC.sha256_bytes(b"")
            zero = ARC.finish_manifest(entries)
            self.assertEqual(
                self.service.validate_retirement_overlay_partial_manifest(
                    base, zero, policy), 1)
            for field, value in (
                    ("mode", row["mode"] ^ 0o100),
                    ("uid", row["uid"] + 1),
                    ("gid", row["gid"] + 1),
                    ("flags", row["flags"] + 1),
                    ("acl", "foreign")):
                broken = copy.deepcopy(entries)
                next(item for item in broken if item["path"] == target)[field] = value
                with self.subTest(field=field), self.assertRaises(
                        ARC.ArchiveError):
                    self.service.validate_retirement_overlay_partial_manifest(
                        base, ARC.finish_manifest(broken), policy)

        # The six non-retail candidates never gain a 009b exception.
        self.settings.candidates = (self.candidate,)
        self.settings.test_retail_roles = ()
        self.settings.test_retail_inventory = None
        self.service = ARC.ArchiveService(self.settings, self.platform)
        transaction = self.enqueue()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertFalse((self.settings.queue_root / transaction /
                          ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD).exists())

    def rpo_08(self) -> None:
        """Production authorization binds every exact 009b policy constant."""
        self.assertTrue(ARC.PRODUCTION_REVIEW_AUTHORIZED)
        policy = ARC.PRODUCTION_RETIREMENT_PRETRUNCATE_OPEN
        expected = {
            "source_quarantined_sha256":
                "9b0693a18c51d95ca90b08a4ccc4df1b692d6a7c32b15332bae99426a44d6b25",
            "retirement_started_sha256":
                "30de606ee732db5a8b578e7a838c5da311138ae323b412d74ce2de88944d6f15",
            "retirement_overlay_baseline_sha256":
                "ec677fb39ba5fff06d9dc82964ad1874917d61522a3a60bc0dc41095c4804653",
            "pre_open_manifest_sha256":
                "d882cd09d874eeda63e31b5c8ef71568c5f73851e545290294949e77c6ff529b",
            "pre_open_tree_sha256":
                "bfd450867421647baec5453864b42ab2f762249bfc311769162e60fe2e4b6623",
            "changed_paths_sha256":
                "6fff2effcd446d05d0ae67a8facf0fb78e782f1f717977b111aed9077aa06d81",
            "eligible_paths_sha256":
                "dc8d7a5b2c5b995979699e7f55628f5de0a1e18e8167804852929a600fb73da4",
            "inherited_paths_sha256":
                "16e954228ab3ecc6df6afc4e61c9cb1f834a1cd0cafde88a9e84e176ed49afeb",
        }
        for field, value in expected.items():
            self.assertEqual(getattr(policy, field), value)
        self.assertEqual(policy.changed_identity, (16777234, 11969264))
        self.assertEqual(policy.changed_size, 46)
        self.assertEqual(policy.changed_mode, 0o644)
        self.assertEqual(
            policy.historical_009a_authorization_sha256,
            "114ae1731dfcf921b79ff096602d7f12219b3083e03d8c358dab792d20f6107e")
        for field in policy.__dataclass_fields__:
            value = getattr(policy, field)
            if isinstance(value, tuple):
                changed = (value[0] + 1, value[1])
            elif isinstance(value, int):
                changed = value + 1
            else:
                changed = value + "-changed"
            replacement = ARC.RetirementPretruncateOpenPolicy(**{
                name: changed if name == field else getattr(policy, name)
                for name in policy.__dataclass_fields__})
            with mock.patch.object(
                    ARC, "PRODUCTION_RETIREMENT_PRETRUNCATE_OPEN", replacement):
                self.assertFalse(ARC.reviewed_allowlist_authorized(
                    ARC.ALLOWLIST_VERSION, ARC.INITIAL_CANDIDATES), field)
        self.assertIsNone(
            self.service.retirement_pretruncate_open_policy_for_transaction(
                -1, {"transaction_id": policy.transaction_id}))

    # HST-01..06 — exact read-only completed-retirement verification
    def hst_01(self) -> None:
        """The dedicated command returns only its non-authorizing status."""
        self.assertEqual(len(ARC.HISTORICAL_COMPLETED_GATE_PROOFS), 4)
        self.assertEqual(
            ARC.HISTORICAL_COMPLETED_GATE_PROOFS[0],
            ("gate-1786541795853451000-5335-0.json",
             "723355598ddbb043d1c37e09f7b5dd8a332a20e7483780522a61538a5f3e29fb",
             1786542482.8174772,
             "d2d39c2693b8d9ed810a1d90ab70a3649c14d7c3bedac0eb78b52b5cb293b830",
             "c633b0cd5c7c199d6dafe1927bc1e71b0a8958c00316dfa2df9ec8c148b1a098",
             "54ad2d9662230750bb000c07451b2a576013e1ce123fd9f73dd4ffdc08a689d8"))
        self.assertEqual(
            ARC.historical_completed_policy_authorization_sha256(
                ARC.PRODUCTION_HISTORICAL_COMPLETED),
            ARC.HISTORICAL_COMPLETED_POLICY_AUTHORIZATION_SHA256)
        self.assertEqual(
            ARC.historical_prepared_overlay_authorization_sha256(
                ARC.PRODUCTION_HISTORICAL_PREPARED_OVERLAY),
            ARC.HISTORICAL_PREPARED_OVERLAY_AUTHORIZATION_SHA256)
        self.assertEqual(
            ARC.PRODUCTION_HISTORICAL_PREPARED_OVERLAY
                .historical_policy_authorization_sha256,
            ARC.HISTORICAL_COMPLETED_POLICY_AUTHORIZATION_SHA256)
        self.assertEqual(
            ARC.parser().parse_args(
                ["verify-historical-production-state"]).command,
            "verify-historical-production-state")
        with self.historical_completed_transaction("positive") as fixture:
            proofs = fixture["historical_gate_proofs"]
            self.assertEqual(len(proofs), 4)
            self.assertEqual(
                fixture["historical_policy"].historical_gate_proofs,
                tuple(sorted((
                    (proof["receipt_name"], proof["receipt_sha256"],
                     proof["gate_ended_unix"], proof["gate_source_sha256"],
                     proof["gate_stamp_sha256"], proof["gate_log_sha256"])
                    for proof in proofs), key=lambda row: row[0])))
            self.assertEqual(
                fixture["external_manifest"]["gate_proof"], proofs[0])
            self.assertEqual(
                fixture["historical_stages"][ARC.RECORD_NAMES[
                    "source-quarantined"]]["gate_proof"], proofs[1])
            self.assertEqual(
                fixture["historical_stages"][
                    ARC.RETIREMENT_OVERLAY_BASELINE_RECORD][
                        "current_009a_gate_proof"], proofs[2])
            self.assertEqual(
                fixture["historical_stages"][
                    ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD][
                        "current_009b_gate_proof"], proofs[3])
            before = self.historical_readonly_snapshot()
            cwd_before = ARC.identity(os.stat("."))
            result = self.service.verify_historical_production_state()
            after = self.historical_readonly_snapshot()
            self.assertEqual(result, {
                "status": "HISTORICAL_PASS",
                "mutation_authorization": "NONE",
            })
            self.assertNotIn("PASS", result.values())
            self.assertEqual(before, after)
            self.assertEqual(ARC.identity(os.stat(".")), cwd_before)
            self.assertEqual(
                fixture["retail_verifier_calls"],
                [fixture["historical_prepared_policy"].prepared_root_identity])

        module_path, prepared_root, cache = \
            self.real_retail_loader_fixture("real-loader-success")

        def module_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
            result: dict[str, tuple[object, ...]] = {}
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root).as_posix()
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    result[relative] = (
                        "regular", info.st_size, info.st_mtime_ns,
                        ARC.sha256_file(path))
                elif stat.S_ISDIR(info.st_mode):
                    result[relative] = ("directory", info.st_mtime_ns)
                else:
                    result[relative] = ("other", stat.S_IFMT(info.st_mode))
            return result

        module_root = module_path.parent
        before_modules = module_snapshot(module_root)
        self.assertEqual(list(cache.iterdir()), [])
        original_bytecode = sys.dont_write_bytecode
        try:
            verifier = None
            for prior in (False, True):
                sys.dont_write_bytecode = prior
                loaded = ARC.load_retail_import_prepared_verifier(module_path)
                self.assertTrue(callable(loaded))
                self.assertIs(sys.dont_write_bytecode, prior)
                verifier = loaded
            assert verifier is not None
            prepared_fd = ARC.open_absolute_directory(
                prepared_root, "real retail loader prepared fixture")
            cwd_before = ARC.identity(os.stat("."))
            try:
                verified = verifier(prepared_fd)
            finally:
                os.close(prepared_fd)
            self.assertEqual(ARC.identity(os.stat(".")), cwd_before)
            self.assertEqual(len(verified["documents"]), 7)
            self.assertEqual(len(verified["manifest"]), 5)

            broken_root = self.base / "real-loader-exception"
            broken_modules = broken_root / "modules"
            broken_modules.mkdir(mode=0o700, parents=True)
            broken_cache = broken_modules / "__pycache__"
            broken_cache.mkdir(mode=0o700)
            for name in ("retail_import.py", "retail_simulator_guard.py"):
                shutil.copyfile(module_root / name, broken_modules / name)
            before_broken = module_snapshot(broken_modules)
            for prior in (False, True):
                sys.dont_write_bytecode = prior
                with self.assertRaisesRegex(
                        ARC.ArchiveError, "dependency chain cannot be loaded"):
                    ARC.load_retail_import_prepared_verifier(
                        broken_modules / "retail_import.py")
                self.assertIs(sys.dont_write_bytecode, prior)
            self.assertEqual(module_snapshot(broken_modules), before_broken)
            self.assertEqual(list(broken_cache.iterdir()), [])
        finally:
            sys.dont_write_bytecode = original_bytecode
        self.assertEqual(module_snapshot(module_root), before_modules)
        self.assertEqual(list(cache.iterdir()), [])
        self.assertEqual(list(module_root.rglob("*.pyc")), [])

    def hst_02(self) -> None:
        """The actual sibling path rejects HST and keeps strict equivalence."""
        with self.historical_completed_transaction("strict-existing") as fixture:
            with mock.patch.object(
                    self.service, "verify_historical_prepared_overlay",
                    side_effect=AssertionError(
                        "generic path used historical prepared overlay")) \
                    as historical_overlay:
                with self.assertRaisesRegex(
                        ARC.ArchiveError,
                        "semantically equivalent|009a tuple differs"):
                    self.service.verify_production_state(fixture["transaction"])
                with self.assertRaisesRegex(
                        ARC.ArchiveError,
                        "semantically equivalent|009a tuple differs"):
                    self.service.verify_published(fixture["transaction"])
                historical_overlay.assert_not_called()
            result = self.service.verify_historical_production_state()
            self.assertEqual(set(result), {"status", "mutation_authorization"})
            self.assertFalse(any(key.startswith("main_") for key in result))

            sibling_transaction = self.service.enqueue(
                fixture["sibling"].ident)["transaction_id"]
            sibling_root = self.settings.queue_root / sibling_transaction
            external_proof = (
                self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                f"{sibling_transaction}-second-copy-proof.json")
            lock_fd, queue_fd, transaction_fd, sibling_record = \
                self.service.open_transaction(
                    sibling_transaction, exclusive=True,
                    recover_partials=True)
            try:
                volume = self.service.bind_volume()
                try:
                    roots = self.service.external_roots(
                        volume, sibling_record["category"])
                    try:
                        with mock.patch.object(
                                self.service,
                                "verify_historical_production_state",
                                return_value=result) as historical_api, \
                                mock.patch.object(
                                    self.service,
                                    "verify_retired_main_transaction",
                                    wraps=getattr(
                                        self.service,
                                        "verify_retired_main_transaction")) \
                                as strict_api:
                            with self.assertRaisesRegex(
                                    ARC.ArchiveError,
                                    "semantically equivalent|009a tuple differs"):
                                self.service.ensure_sibling_dependency_proof(
                                    queue_fd, transaction_fd, sibling_record,
                                    volume, roots)
                            historical_api.assert_not_called()
                            self.assertGreaterEqual(strict_api.call_count, 1)
                        with mock.patch.object(
                                self.service,
                                "verify_retired_main_transaction",
                                return_value=result):
                            with self.assertRaisesRegex(
                                    ARC.ArchiveError,
                                    "not an authorizing proof"):
                                self.service.ensure_sibling_dependency_proof(
                                    queue_fd, transaction_fd, sibling_record,
                                    volume, roots)
                    finally:
                        roots.close()
                finally:
                    volume.close()
            finally:
                os.close(transaction_fd)
                os.close(queue_fd)
                os.close(lock_fd)
            self.assertFalse(external_proof.exists())
            self.assertFalse((sibling_root /
                              ARC.RECORD_NAMES["second-copy-proof"]).exists())

    def hst_03(self) -> None:
        """Every immutable late stage and authorization tuple is exact."""
        with self.historical_completed_transaction("stages") as fixture:
            root = fixture["transaction_root"]
            for name in (
                    ARC.RECORD_NAMES["source-quarantined"],
                    ARC.RECORD_NAMES["retirement-started"],
                    ARC.RETIREMENT_OVERLAY_BASELINE_RECORD,
                    ARC.RETIREMENT_PRETRUNCATE_OPEN_RECORD,
                    ARC.RECORD_NAMES["source-deleted"],
                    ARC.RECORD_NAMES["deletion-receipt"]):
                path = root / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b" ")
                    os.chmod(path, 0o600)
                    with self.subTest(stage=name), self.assertRaises(
                            ARC.ArchiveError):
                        self.service.verify_historical_production_state()
                finally:
                    path.write_bytes(original)
                    os.chmod(path, 0o600)

            policy = fixture["historical_policy"]
            mutations = (
                replace(policy, candidate_id=policy.candidate_id + "-foreign"),
                replace(policy, source_path=policy.source_path + "-foreign"),
                replace(policy, allowlist_version=policy.allowlist_version + 1),
                replace(policy, category="archives"),
                replace(policy, source_tree_sha256="0" * 64),
                replace(policy, production_authorization_sha256="0" * 64),
            )
            for changed in mutations:
                with self.subTest(policy=changed), mock.patch.object(
                        self.service, "historical_completed_policy",
                        return_value=changed), self.assertRaises(
                            (ARC.ArchiveError, FileNotFoundError)):
                    self.service.verify_historical_production_state()

        production = ARC.ArchiveService()
        changed = replace(
            ARC.PRODUCTION_HISTORICAL_COMPLETED,
            external_receipt_sha256="0" * 64)
        with mock.patch.object(
                ARC, "PRODUCTION_HISTORICAL_COMPLETED", changed), \
                self.assertRaisesRegex(ARC.ArchiveError, "not authorized"):
            production.historical_completed_policy()

        overlay = ARC.PRODUCTION_HISTORICAL_PREPARED_OVERLAY
        baseline_digest = ARC.historical_prepared_overlay_authorization_sha256(
            overlay)
        self.assertEqual(
            baseline_digest,
            ARC.HISTORICAL_PREPARED_OVERLAY_AUTHORIZATION_SHA256)
        for field_name in overlay.__dataclass_fields__:
            value = getattr(overlay, field_name)
            if isinstance(value, int):
                replacement_value = value + 1
            elif isinstance(value, str):
                replacement_value = value + "-changed"
            elif isinstance(value, tuple):
                replacement_value = value + (("foreign", "0" * 64),)
            else:  # pragma: no cover - the immutable policy has no other type
                self.fail(f"unhandled overlay policy field: {field_name}")
            replacement = replace(
                overlay, **{field_name: replacement_value})
            self.assertNotEqual(
                ARC.historical_prepared_overlay_authorization_sha256(
                    replacement), baseline_digest, field_name)
            with self.subTest(prepared_overlay_field=field_name), \
                    mock.patch.object(
                        ARC, "PRODUCTION_HISTORICAL_PREPARED_OVERLAY",
                        replacement), self.assertRaisesRegex(
                            ARC.ArchiveError, "not authorized"):
                production.historical_prepared_overlay_policy(
                    ARC.PRODUCTION_HISTORICAL_COMPLETED)

    def hst_04(self) -> None:
        """Historical evidence, external state, tombstone and prepared proof bind."""
        with self.historical_completed_transaction("physical") as fixture:
            policy = fixture["historical_policy"]
            values = [*fixture["historical_stages"].values(),
                      fixture["external_manifest"],
                      fixture["external_second"],
                      fixture["external_receipt"]]
            self.service.validate_historical_gate_proof_inventory(
                values, policy)

            without_transfer_manifest = [
                *fixture["historical_stages"].values(),
                fixture["external_second"], fixture["external_receipt"]]
            with self.assertRaisesRegex(
                    ARC.ArchiveError, "proof inventory differs"):
                self.service.validate_historical_gate_proof_inventory(
                    without_transfer_manifest, policy)

            extra_proof = copy.deepcopy(
                fixture["historical_gate_proofs"][0])
            extra_proof["receipt_name"] = \
                "gate-1999998999000000000-999-0.json"
            with self.assertRaisesRegex(
                    ARC.ArchiveError, "proof inventory differs"):
                self.service.validate_historical_gate_proof_inventory(
                    [*values, {"gate_proof": extra_proof}], policy)

            mutated_transfer = copy.deepcopy(fixture["external_manifest"])
            mutated_transfer["gate_proof"] = copy.deepcopy(
                mutated_transfer["gate_proof"])
            mutated_transfer["gate_proof"]["gate_source_sha256"] = "0" * 64
            with self.assertRaisesRegex(
                    ARC.ArchiveError, "proof inventory differs"):
                self.service.validate_historical_gate_proof_inventory(
                    [*fixture["historical_stages"].values(),
                     mutated_transfer, fixture["external_second"],
                     fixture["external_receipt"]], policy)

            receipt_name = policy.historical_gate_proofs[0][0]
            historical_receipt = self.settings.gate_log_root / receipt_name
            original_receipt = historical_receipt.read_bytes()
            value = ARC.strict_json_loads(original_receipt.decode("ascii"))
            value["started_unix"] -= 1
            historical_receipt.write_bytes(ARC.canonical_json(value))
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                historical_receipt.write_bytes(original_receipt)
                os.chmod(historical_receipt, 0o600)

            historical_log = historical_receipt.with_suffix(".log")
            original_log = historical_log.read_bytes()
            historical_log.write_bytes(original_log + b"tampered\n")
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                historical_log.write_bytes(original_log)
                os.chmod(historical_log, 0o600)

            manifests = self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT
            for name in (
                    policy.external_manifest_name,
                    policy.external_second_copy_name,
                    policy.external_receipt_name):
                path = manifests / name
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                try:
                    with self.subTest(external=name), self.assertRaises(
                            ARC.ArchiveError):
                        self.service.verify_historical_production_state()
                finally:
                    path.write_bytes(original)
                    os.chmod(path, 0o600)

            destination_manifest = fixture["historical_stages"][
                ARC.RECORD_NAMES["published"]]["destination_manifest"]
            target_row = next(row for row in destination_manifest["entries"]
                              if row["kind"] == "regular")
            target = fixture["destination_path"] / target_row["path"]
            original = target.read_bytes()
            info = target.stat()
            target.write_bytes(original + b"x")
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                target.write_bytes(original)
                os.chmod(target, stat.S_IMODE(info.st_mode))
                os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))

            tombstone = fixture["tombstone_path"]
            tombstone_mode = stat.S_IMODE(tombstone.stat().st_mode)
            os.chmod(tombstone, tombstone_mode ^ 0o100)
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                os.chmod(tombstone, tombstone_mode)

            prepared_path = (
                self.settings.retail_prepared_root /
                ARC.SECOND_COPY_MAPPINGS[0][1])
            prepared_bytes = prepared_path.read_bytes()
            prepared_info = prepared_path.stat()
            prepared_path.write_bytes(prepared_bytes + b"x")
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                prepared_path.write_bytes(prepared_bytes)
                os.chmod(prepared_path, stat.S_IMODE(prepared_info.st_mode))
                os.utime(prepared_path, ns=(prepared_info.st_atime_ns,
                                            prepared_info.st_mtime_ns))

            prepared_root = self.settings.retail_prepared_root
            root_info = prepared_root.stat()

            def restore_root_mtime() -> None:
                os.utime(prepared_root, ns=(root_info.st_atime_ns,
                                            root_info.st_mtime_ns))

            # Restoring the reviewed Finder file is not an allowed current
            # state: the exception is exactly its removal, not optionality.
            restored_ds = prepared_root / ".DS_Store"
            restored_ds.write_bytes(
                fixture["historical_prepared_removed_bytes"])
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                restored_ds.unlink()
                restore_root_mtime()

            extra_file = prepared_root / "foreign.txt"
            extra_file.write_bytes(b"foreign\n")
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                extra_file.unlink()
                restore_root_mtime()

            extra_directory = prepared_root / "foreign-directory"
            extra_directory.mkdir(mode=0o700)
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                extra_directory.rmdir()
                restore_root_mtime()

            existing_metadata = prepared_root / sorted(
                ARC.PREPARED_METADATA_PATHS)[0]
            hidden_metadata = existing_metadata.with_name(
                existing_metadata.name + ".temporarily-missing")
            metadata_parent_info = existing_metadata.parent.stat()
            existing_metadata.rename(hidden_metadata)
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                hidden_metadata.rename(existing_metadata)
                os.utime(existing_metadata.parent,
                         ns=(metadata_parent_info.st_atime_ns,
                             metadata_parent_info.st_mtime_ns))

            mapped = prepared_root / ARC.SECOND_COPY_MAPPINGS[1][1]
            mapped_info = mapped.stat()
            os.chmod(mapped, stat.S_IMODE(mapped_info.st_mode) ^ 0o100)
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                os.chmod(mapped, stat.S_IMODE(mapped_info.st_mode))

            mapped_fd = os.open(mapped, os.O_RDONLY | ARC.O_NOFOLLOW)
            mapped_xattrs = ARC.xattrs_fd(mapped_fd)
            try:
                changed_xattrs = dict(mapped_xattrs)
                changed_xattrs["com.openxray.historical-test"] = \
                    ARC.base64.b64encode(b"foreign").decode("ascii")
                ARC.restore_xattrs_fd(mapped_fd, changed_xattrs)
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                ARC.restore_xattrs_fd(mapped_fd, mapped_xattrs)
                os.close(mapped_fd)

            if sys.platform == "darwin":
                mapped_fd = os.open(mapped, os.O_RDONLY | ARC.O_NOFOLLOW)
                original_flags = ARC.bsd_flags(os.fstat(mapped_fd))
                try:
                    ARC.restore_bsd_flags_fd(
                        mapped_fd, original_flags ^ 0x8000)
                    with self.assertRaises(ARC.ArchiveError):
                        self.service.verify_historical_production_state()
                finally:
                    ARC.restore_bsd_flags_fd(mapped_fd, original_flags)
                    os.close(mapped_fd)

            os.utime(mapped, ns=(mapped_info.st_atime_ns,
                                 mapped_info.st_mtime_ns + 1_000_000))
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                os.utime(mapped, ns=(mapped_info.st_atime_ns,
                                     mapped_info.st_mtime_ns))

            documents = prepared_root / "Documents"
            documents_mode = stat.S_IMODE(documents.stat().st_mode)
            os.chmod(documents, 0o755 if documents_mode == 0o700 else 0o700)
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                os.chmod(documents, documents_mode)

            documents_fd = os.open(
                documents, ARC.directory_open_flags())
            documents_xattrs = ARC.xattrs_fd(documents_fd)
            try:
                changed_xattrs = dict(documents_xattrs)
                changed_xattrs["com.openxray.historical-directory-test"] = \
                    ARC.base64.b64encode(b"foreign").decode("ascii")
                ARC.restore_xattrs_fd(documents_fd, changed_xattrs)
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                ARC.restore_xattrs_fd(documents_fd, documents_xattrs)
                os.close(documents_fd)

            os.utime(prepared_root, ns=(root_info.st_atime_ns,
                                        root_info.st_mtime_ns + 1_000_000))
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                restore_root_mtime()

            changed_count = copy.deepcopy(
                fixture["historical_prepared_current"])
            changed_count["files"] += 1
            with self.assertRaises(ARC.ArchiveError):
                self.service.validate_historical_prepared_metadata_overlay(
                    fixture["historical_prepared_old"], changed_count,
                    fixture["historical_prepared_policy"])

            changed_mapping = copy.deepcopy(fixture["external_second"])
            changed_mapping["mappings"][0]["prepared_path"] = \
                "Documents/foreign-mapping.db"
            changed_mapping.pop("proof_hash", None)
            changed_mapping["proof_hash"] = \
                self.service.second_copy_payload_hash(changed_mapping)
            with self.assertRaises(ARC.ArchiveError):
                self.service.verify_historical_prepared_overlay(
                    changed_mapping, policy)

            cwd_identity = ARC.identity(os.stat("."))

            def failing_retail_verifier(descriptor: int) -> None:
                os.fchdir(descriptor)
                raise RuntimeError("injected retail verifier failure")

            with mock.patch.object(
                    self.service, "retail_import_prepared_verifier",
                    return_value=failing_retail_verifier), \
                    self.assertRaisesRegex(
                        ARC.ArchiveError, "retail importer rejected"):
                self.service.verify_historical_production_state()
            self.assertEqual(ARC.identity(os.stat(".")), cwd_identity)

            self.assertEqual(
                self.service.verify_historical_production_state()["status"],
                "HISTORICAL_PASS")

    def hst_05(self) -> None:
        """Only a fresh exact clean current full gate is independently accepted."""
        with self.historical_completed_transaction("current-gate") as fixture:
            receipt_path = fixture["current_receipt"]
            log_path = receipt_path.with_suffix(".log")
            receipt_raw = receipt_path.read_bytes()
            log_raw = log_path.read_bytes()
            original_state = copy.deepcopy(self.platform.current_gate_state)
            original_clock = self.platform.clock
            original_hash = self.platform.gate_hash

            def reset() -> dict[str, object]:
                receipt_path.write_bytes(receipt_raw)
                os.chmod(receipt_path, 0o600)
                log_path.write_bytes(log_raw)
                os.chmod(log_path, 0o600)
                self.platform.current_gate_state = copy.deepcopy(original_state)
                self.platform.clock = original_clock
                self.platform.gate_hash = original_hash
                return ARC.strict_json_loads(receipt_raw.decode("ascii"))

            for failure in (
                    "dirty", "stale", "failed", "non-full", "stamp",
                    "log", "before-after", "source"):
                value = reset()
                if failure == "dirty":
                    self.platform.current_gate_state["status_sha256"] = "1" * 64
                elif failure == "stale":
                    self.platform.clock += self.settings.gate_max_age_seconds + 10
                elif failure == "failed":
                    value["exit_code"] = 1
                    receipt_path.write_bytes(ARC.canonical_json(value))
                elif failure == "non-full":
                    value["gate"] = "fast"
                    receipt_path.write_bytes(ARC.canonical_json(value))
                elif failure == "stamp":
                    value["underlying_stamp"]["sha256"] = "0" * 64
                    receipt_path.write_bytes(ARC.canonical_json(value))
                elif failure == "log":
                    log_path.write_bytes(log_raw + b"foreign\n")
                elif failure == "before-after":
                    value["after"] = copy.deepcopy(value["after"])
                    value["after"]["head"] = "8" * 40
                    receipt_path.write_bytes(ARC.canonical_json(value))
                elif failure == "source":
                    self.platform.gate_hash = "7" * 64
                with self.subTest(current_gate=failure), self.assertRaises(
                        ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            reset()
            self.assertEqual(
                self.service.verify_historical_production_state()["status"],
                "HISTORICAL_PASS")

    def hst_06(self) -> None:
        """The capability performs no writes and cannot target incomplete/other work."""
        with self.historical_completed_transaction("no-writes") as fixture:
            before = self.historical_readonly_snapshot()
            real_open = os.open
            write_open_flags = (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC |
                os.O_APPEND | os.O_EXCL)

            def read_only_open(path: object, flags: int, mode: int = 0o777,
                               *, dir_fd: int | None = None) -> int:
                if flags & write_open_flags:
                    raise AssertionError(
                        f"read-only verifier opened mutation flags: {flags:#x}")
                if dir_fd is None:
                    return real_open(path, flags, mode)  # type: ignore[arg-type]
                return real_open(  # type: ignore[arg-type]
                    path, flags, mode, dir_fd=dir_fd)

            with ExitStack() as stack:
                for owner, name in (
                        (self.service, "write_record"),
                        (self.service, "stage"),
                        (self.service, "fsync"),
                        (self.service, "ensure_private_directory"),
                        (self.service, "create_private_directory_exclusive"),
                        (self.service, "recover_record_partials"),
                        (self.service, "quarantine_record_partial"),
                        (self.platform, "write"),
                        (self.platform, "rename_exclusive"),
                        (self.platform, "fsync"),
                        (ARC, "apply_xattrs_fd"),
                        (ARC, "restore_xattrs_fd"),
                        (ARC, "copy_acl_fd"),
                        (ARC, "restore_acl_text_fd"),
                        (ARC, "clear_delete_protection_fd"),
                        (ARC, "apply_bsd_flags_fd"),
                        (ARC, "restore_bsd_flags_fd")):
                    stack.enter_context(mock.patch.object(
                        owner, name, side_effect=AssertionError(
                            f"read-only verifier called {name}")))
                stack.enter_context(mock.patch.object(
                    os, "open", side_effect=read_only_open))
                for name in (
                        "write", "replace", "rmdir", "fchmod", "fchown",
                        "chmod", "chown", "lchown", "utime", "truncate",
                        "ftruncate", "unlink", "remove", "rename", "renames",
                        "mkdir", "makedirs", "setxattr", "removexattr"):
                    if hasattr(os, name):
                        stack.enter_context(mock.patch.object(
                            os, name, side_effect=AssertionError(
                                f"read-only verifier called os.{name}")))
                result = self.service.verify_historical_production_state()
            self.assertEqual(result["mutation_authorization"], "NONE")
            self.assertEqual(before, self.historical_readonly_snapshot())

            root = fixture["transaction_root"]
            saved: list[tuple[Path, Path]] = []
            for name in (ARC.RECORD_NAMES["source-deleted"],
                         ARC.RECORD_NAMES["deletion-receipt"]):
                path = root / name
                backup = root.parent / f"{fixture['transaction']}-{name}.saved"
                path.rename(backup)
                saved.append((path, backup))
            try:
                with self.assertRaises(ARC.ArchiveError):
                    self.service.verify_historical_production_state()
            finally:
                for path, backup in saved:
                    backup.rename(path)

            sibling_transaction = self.service.enqueue(
                fixture["sibling"].ident)["transaction_id"]
            sibling_policy = replace(
                fixture["historical_policy"],
                transaction_id=sibling_transaction,
                candidate_id=fixture["sibling"].ident,
                source_path=str(fixture["sibling"].source))
            with mock.patch.object(
                    self.service, "historical_completed_policy",
                    return_value=sibling_policy), self.assertRaises(
                        ARC.ArchiveError):
                self.service.verify_historical_production_state()

        self.settings.candidates = (self.candidate,)
        self.settings.test_retail_roles = ()
        self.settings.test_retail_inventory = None
        self.service = ARC.ArchiveService(self.settings, self.platform)
        with self.assertRaisesRegex(ARC.ArchiveError, "not authorized"):
            self.service.verify_historical_production_state()

    # LOCK-01..12
    def lock_01(self) -> None:
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(ready_r)
            os.close(release_w)
            descriptor = -1
            try:
                descriptor = self.service.acquire_archiver_lock(exclusive=False)
                os.write(ready_w, b"READY\n")
                if os.read(release_r, 1) != b"R":
                    os._exit(92)
                os._exit(0)
            except BaseException:
                os._exit(91)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        os.close(ready_w)
        os.close(release_r)
        try:
            self.assertEqual(os.read(ready_r, 6), b"READY\n")
            shared = self.service.acquire_archiver_lock(exclusive=False)
            os.close(shared)
            self.assertEqual(self.service.audit()[0]["status"], "READY_TO_ENQUEUE")
            with self.assertRaisesRegex(ARC.ArchiveBusy, "BUSY"):
                self.service.acquire_archiver_lock(exclusive=True)
            with self.assertRaisesRegex(ARC.ArchiveBusy, "BUSY"):
                self.enqueue()
        finally:
            os.write(release_w, b"R")
            os.close(release_w)
            os.close(ready_r)
        _, child_status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(child_status), 0)

    def lock_02(self) -> None:
        class Fixed:
            hex = "a" * 32
        with mock.patch.object(ARC.uuid, "uuid4", return_value=Fixed()):
            self.enqueue()
            with self.assertRaises(FileExistsError):
                self.enqueue()

    def lock_03(self) -> None:
        queue_fd = self.service.queue_fd(create=True)
        try:
            transaction_fd = self.service.create_private_directory_exclusive(queue_fd, "partial-test")
            try:
                self.platform.write_limit = 5
                self.platform.fail_write_after = 5
                with self.assertRaises(OSError):
                    self.service.write_record(transaction_fd, "001-copy-started.json",
                                              {"schema": ARC.SCHEMA, "stage": "copy-started"})
                self.assertNotIn("001-copy-started.json", os.listdir(transaction_fd))
                self.assertIn(ARC.RECORD_RESIDUE, os.listdir(transaction_fd))
                residue_fd = os.open(ARC.RECORD_RESIDUE, ARC.directory_open_flags(),
                                     dir_fd=transaction_fd)
                try:
                    partials = os.listdir(residue_fd)
                    self.assertEqual(len(partials), 1)
                    self.assertRegex(partials[0], ARC.PARTIAL_RECORD_RE)
                    partial_info = os.stat(partials[0], dir_fd=residue_fd,
                                           follow_symlinks=False)
                    self.assertEqual(stat.S_IMODE(partial_info.st_mode), 0o600)
                    self.assertEqual(partial_info.st_nlink, 1)
                finally:
                    os.close(residue_fd)
                self.service.validate_transaction_residue(transaction_fd)
                self.platform.write_limit = None
                self.platform.fail_write_after = None
                self.platform.written = 0
                foreign_fd = os.open("foreign-residue", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     0o600, dir_fd=transaction_fd)
                os.close(foreign_fd)
                with self.assertRaisesRegex(ARC.ArchiveError, "unknown/tampered"):
                    self.service.validate_transaction_residue(transaction_fd)
                unknown_parent = self.service.create_private_directory_exclusive(
                    queue_fd, "unknown-partial-test")
                try:
                    unknown = os.open(".partial-foreign-not-a-random-id",
                                      os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                      0o600, dir_fd=unknown_parent)
                    os.close(unknown)
                    with self.assertRaisesRegex(ARC.ArchiveError, "unknown/foreign"):
                        self.service.recover_record_partials(unknown_parent)
                finally:
                    os.close(unknown_parent)
                unsafe_parent = self.service.create_private_directory_exclusive(
                    queue_fd, "unsafe-partial-test")
                try:
                    unsafe_name = f".partial-001-copy-started-{'f' * 32}"
                    unsafe = os.open(unsafe_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     0o644, dir_fd=unsafe_parent)
                    os.close(unsafe)
                    with self.assertRaisesRegex(ARC.ArchiveError, "foreign or unsafe"):
                        self.service.recover_record_partials(
                            unsafe_parent,
                            allowed=lambda name: name in ARC.RECORD_NAMES.values())
                finally:
                    os.close(unsafe_parent)
                gap_fd_parent = self.service.create_private_directory_exclusive(
                    queue_fd, "gap-test")
                try:
                    gap_name = ARC.RECORD_NAMES["verified"]
                    gap_fd = os.open(gap_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     0o600, dir_fd=gap_fd_parent)
                    os.close(gap_fd)
                    with self.assertRaisesRegex(ARC.ArchiveError, "durable prefix"):
                        self.service.validate_transaction_residue(gap_fd_parent)
                finally:
                    os.close(gap_fd_parent)
            finally:
                os.close(transaction_fd)
        finally:
            os.close(queue_fd)

    def lock_04(self) -> None:
        transaction = self.enqueue()
        event_r, event_w = os.pipe()
        release_r, release_w = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(event_r)
            os.close(release_w)
            def pause(label: str) -> None:
                os.write(event_w, (label + "\n").encode("ascii"))
                if os.read(release_r, 1) != b"R":
                    os._exit(92)
            try:
                self.service.hooks["after_source_quarantined_record"] = \
                    lambda **_: pause("quarantined")
                self.service.hooks["after_retirement_truncate"] = \
                    lambda index, **_: pause("first-truncate") if index == 0 else None
                self.service.hooks["after_source_deleted_record"] = \
                    lambda **_: pause("source-deleted-010")
                self.service.hooks["after_external_deletion_receipt"] = \
                    lambda **_: pause("external-receipt")
                self.service.hooks["after_deletion_receipt_record"] = \
                    lambda **_: pause("local-receipt-011")
                result = self.drain(transaction)
                os.write(event_w, ("result-" + str(result["status"]) + "\n").encode("ascii"))
                os._exit(0)
            except BaseException as error:
                os.write(event_w, ("error-" + type(error).__name__ + "\n").encode("ascii"))
                os._exit(91)

        os.close(event_w)
        os.close(release_r)
        stream = os.fdopen(event_r, "r", encoding="ascii", buffering=1)
        contender = ARC.ArchiveService(self.settings, self.platform)
        failures: list[str] = []
        phases = (
            "quarantined", "first-truncate", "source-deleted-010",
            "external-receipt", "local-receipt-011",
        )
        try:
            for expected in phases:
                observed = stream.readline().strip()
                if observed != expected:
                    failures.append(f"expected {expected}, observed {observed}")
                operations = {
                    "audit/shared": contender.audit,
                    "enqueue/exclusive": lambda: contender.enqueue(self.candidate.ident),
                    "drain/shared": lambda: contender.drain_one(transaction, apply=False),
                    "verify/shared": lambda: contender.verify_published(transaction),
                    "production-state/shared":
                        lambda: contender.verify_production_state(transaction),
                }
                for label, operation in operations.items():
                    try:
                        operation()
                    except ARC.ArchiveBusy:
                        pass
                    except BaseException as error:
                        failures.append(
                            f"{expected} {label} raised {type(error).__name__}, not BUSY")
                    else:
                        failures.append(f"{expected} {label} bypassed exclusive lock")
                os.write(release_w, b"R")
            self.assertEqual(stream.readline().strip(), "result-PASS")
        finally:
            stream.close()
            os.close(release_w)
        _, child_status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(child_status), 0)
        self.assertEqual(failures, [])
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["source-deleted"]).is_file())
        self.assertTrue((self.settings.queue_root / transaction /
                         ARC.RECORD_NAMES["deletion-receipt"]).is_file())

    def lock_05(self) -> None:
        transaction = self.enqueue()
        child = os.fork()
        if child == 0:
            self.service.hooks["after_retirement_truncate"] = \
                lambda index, **_: os._exit(91) if index == 0 else None
            self.drain(transaction)
            os._exit(90)
        _, child_status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(child_status), 91)
        self.service.hooks.clear()

        # Kernel process teardown must release the exact OS lock immediately.
        descriptor = self.service.acquire_archiver_lock(exclusive=True)
        os.close(descriptor)
        root = self.settings.queue_root / transaction
        self.assertTrue((root / ARC.RECORD_NAMES["retirement-started"]).is_file())
        self.assertFalse((root / ARC.RECORD_NAMES["source-deleted"]).exists())
        self.assertFalse((root / ARC.RECORD_NAMES["deletion-receipt"]).exists())
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertTrue((root / ARC.RECORD_NAMES["deletion-receipt"]).is_file())

    def lock_06(self) -> None:
        transaction = self.enqueue()
        root = self.settings.queue_root / transaction
        held = self.base / "post-boundary-owned-report.txt"
        foreign = b"foreign post-boundary replacement\n"

        def replace_after_first(index: int, **_: object) -> None:
            if index != 0 or held.exists():
                return
            intent = ARC.strict_json_loads((
                root / ARC.RECORD_NAMES["delete-intent"]
            ).read_text(encoding="ascii"))
            retired_root = (self.settings.queue_root /
                            intent["quarantine_namespace"] /
                            intent["retired_name"])
            os.rename(retired_root / "report.txt", held)
            (retired_root / "report.txt").write_bytes(foreign)

        self.service.hooks["after_retirement_truncate"] = replace_after_first
        with self.assertRaisesRegex(
                ARC.ArchiveError,
                "regular name was replaced|metadata/content changed"):
            self.drain(transaction)
        self.service.hooks.clear()
        intent = ARC.strict_json_loads((
            root / ARC.RECORD_NAMES["delete-intent"]
        ).read_text(encoding="ascii"))
        retired_root = (self.settings.queue_root /
                        intent["quarantine_namespace"] /
                        intent["retired_name"])
        self.assertEqual((retired_root / "nested/trace.bin").stat().st_size, 0)
        self.assertEqual((retired_root / "report.txt").read_bytes(), foreign)
        self.assertEqual(held.read_bytes(), b"closed evidence\n")
        self.assertTrue((root / ARC.RECORD_NAMES["retirement-started"]).is_file())
        self.assertFalse((root / ARC.RECORD_NAMES["source-deleted"]).exists())
        self.assertFalse((root / ARC.RECORD_NAMES["deletion-receipt"]).exists())
        external_receipt = (self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                            f"{transaction}-source-deletion.json")
        self.assertFalse(external_receipt.exists())

        # Exact-only recovery must neither unlink nor overwrite the foreign
        # replacement or the retained original after a post-boundary mismatch.
        with self.assertRaises(ARC.ArchiveError):
            self.drain(transaction)
        self.assertEqual((retired_root / "report.txt").read_bytes(), foreign)
        self.assertEqual(held.read_bytes(), b"closed evidence\n")
        self.assertFalse((root / ARC.RECORD_NAMES["source-deleted"]).exists())
        help_output = ARC.parser().format_help()
        self.assertIn("cooperating OpenXRay archive tools", help_output)
        self.assertIn("same-UID", help_output)
        self.assertIn("root mutation", help_output)

    def assert_post_truncate_exact_tombstone_rejects(self, mutation: str) -> None:
        transaction = self.enqueue()
        transaction_root = self.settings.queue_root / transaction
        changed: dict[str, object] = {}
        xattr_name = f"com.openxray.post-closure-{mutation}"

        def mutate_after_first(path: str, index: int, **_: object) -> None:
            if index != 0 or changed:
                return
            intent = ARC.strict_json_loads((
                transaction_root / ARC.RECORD_NAMES["delete-intent"]
            ).read_text(encoding="ascii"))
            retired_root = (self.settings.queue_root /
                            intent["quarantine_namespace"] /
                            intent["retired_name"])
            changed["root"] = retired_root
            changed["first"] = path
            if mutation == "extra-regular":
                target = retired_root / "foreign-empty"
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                    os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
                changed["target"] = target
                changed["identity"] = ARC.identity(target.stat())
            elif mutation == "extra-symlink":
                target = retired_root / "foreign-link"
                target.symlink_to("report.txt")
                changed["target"] = target
                changed["identity"] = ARC.identity(target.lstat())
            elif mutation == "directory-metadata":
                target = retired_root / "nested"
                descriptor = ARC.open_absolute_directory(
                    target, "post-closure directory mutation")
                try:
                    ARC.apply_xattrs_fd(descriptor, {xattr_name: "Zm9yZWlnbg=="})
                finally:
                    os.close(descriptor)
                changed["target"] = target
            elif mutation == "zeroed-regular-metadata":
                target = retired_root / path
                descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    self.assertEqual(os.fstat(descriptor).st_size, 0)
                    ARC.apply_xattrs_fd(descriptor, {xattr_name: "Zm9yZWlnbg=="})
                finally:
                    os.close(descriptor)
                changed["target"] = target
            else:
                raise AssertionError(f"unknown post-closure mutation: {mutation}")

        self.service.hooks["after_retirement_truncate"] = mutate_after_first
        with self.assertRaisesRegex(
                ARC.ArchiveError,
                "exact post-closure|post-closure|retirement .* differs"):
            self.drain(transaction)
        self.service.hooks.clear()
        self.assertTrue(changed)
        retired_root = changed["root"]
        first = changed["first"]
        target = changed["target"]
        assert isinstance(retired_root, Path)
        assert isinstance(first, str)
        assert isinstance(target, Path)
        self.assertEqual((retired_root / first).stat().st_size, 0)
        self.assertTrue((transaction_root /
                         ARC.RECORD_NAMES["retirement-started"]).is_file())
        self.assertFalse((transaction_root /
                          ARC.RECORD_NAMES["source-deleted"]).exists())
        self.assertFalse((transaction_root /
                          ARC.RECORD_NAMES["deletion-receipt"]).exists())
        self.assertFalse((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                          f"{transaction}-source-deletion.json").exists())

        if mutation == "extra-regular":
            self.assertEqual(ARC.identity(target.stat()), changed["identity"])
            self.assertEqual(target.read_bytes(), b"")
        elif mutation == "extra-symlink":
            self.assertTrue(target.is_symlink())
            self.assertEqual(ARC.identity(target.lstat()), changed["identity"])
            self.assertEqual(os.readlink(target), "report.txt")
        else:
            descriptor = ARC.open_absolute_directory(
                target, "preserved directory metadata") \
                if mutation == "directory-metadata" else os.open(
                    target, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                self.assertEqual(
                    ARC.xattrs_fd(descriptor).get(xattr_name), "Zm9yZWlnbg==")
            finally:
                os.close(descriptor)

        # A second exact-only recovery attempt must reject the same foreign
        # state without creating receipts or changing the injected entry.
        with self.assertRaises(ARC.ArchiveError):
            self.drain(transaction)
        self.assertFalse((transaction_root /
                          ARC.RECORD_NAMES["source-deleted"]).exists())
        self.assertFalse((transaction_root /
                          ARC.RECORD_NAMES["deletion-receipt"]).exists())
        if mutation == "extra-regular":
            self.assertEqual(ARC.identity(target.stat()), changed["identity"])
            self.assertEqual(target.read_bytes(), b"")
        elif mutation == "extra-symlink":
            self.assertEqual(ARC.identity(target.lstat()), changed["identity"])
            self.assertEqual(os.readlink(target), "report.txt")
        else:
            descriptor = ARC.open_absolute_directory(
                target, "preserved directory metadata retry") \
                if mutation == "directory-metadata" else os.open(
                    target, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                self.assertEqual(
                    ARC.xattrs_fd(descriptor).get(xattr_name), "Zm9yZWlnbg==")
            finally:
                os.close(descriptor)

    def lock_07(self) -> None:
        self.assert_post_truncate_exact_tombstone_rejects("extra-regular")

    def lock_08(self) -> None:
        self.assert_post_truncate_exact_tombstone_rejects("extra-symlink")

    def lock_09(self) -> None:
        self.assert_post_truncate_exact_tombstone_rejects("directory-metadata")

    def lock_10(self) -> None:
        self.assert_post_truncate_exact_tombstone_rejects(
            "zeroed-regular-metadata")

    def crash_after_ftruncate_before_mtime(
            self, transaction: str) -> tuple[Path, str, int]:
        root = self.settings.queue_root / transaction
        candidate = ARC.strict_json_loads((
            root / ARC.RECORD_NAMES["candidate"]
        ).read_text(encoding="ascii"))
        regulars = sorted(
            [row for row in candidate["source_manifest"]["entries"]
             if row["kind"] == "regular" and row["logical_bytes"] > 0],
            key=lambda row: row["path"])
        first = regulars[0]
        child = os.fork()
        if child == 0:
            self.service.hooks[
                "after_retirement_ftruncate_before_metadata_restore"] = \
                lambda index, **_: os._exit(91) if index == 0 else None
            self.drain(transaction)
            os._exit(90)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 91)
        self.service.hooks.clear()
        intent = ARC.strict_json_loads((
            root / ARC.RECORD_NAMES["delete-intent"]
        ).read_text(encoding="ascii"))
        retired_root = (self.settings.queue_root /
                        intent["quarantine_namespace"] /
                        intent["retired_name"])
        return retired_root, first["path"], first["mtime_ns"]

    def lock_11(self) -> None:
        fixed_mtime = 1_234_567_890_123_456_789
        target = self.source / "nested/trace.bin"
        current = target.stat()
        os.utime(target, ns=(current.st_atime_ns, fixed_mtime))
        transaction = self.enqueue()
        root = self.settings.queue_root / transaction
        retired_root, first_path, expected_mtime = \
            self.crash_after_ftruncate_before_mtime(transaction)
        self.assertEqual(first_path, "nested/trace.bin")
        self.assertEqual(expected_mtime, fixed_mtime)
        partial = retired_root / first_path
        self.assertEqual(partial.stat().st_size, 0)
        self.assertNotEqual(partial.stat().st_mtime_ns, expected_mtime)
        arbitrary_mtime = expected_mtime + 987_654_321
        partial_info = partial.stat()
        os.utime(partial, ns=(partial_info.st_atime_ns, arbitrary_mtime))
        self.assertEqual(partial.stat().st_mtime_ns, arbitrary_mtime)
        self.assertFalse((root / ARC.RECORD_NAMES["source-deleted"]).exists())
        self.assertFalse((root / ARC.RECORD_NAMES["deletion-receipt"]).exists())

        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertEqual(partial.stat().st_mtime_ns, expected_mtime)
        retired = ARC.strict_json_loads((
            root / ARC.RECORD_NAMES["source-deleted"]
        ).read_text(encoding="ascii"))
        tombstone_row = next(
            row for row in retired["tombstone_manifest"]["entries"]
            if row["path"] == first_path)
        self.assertEqual(tombstone_row["mtime_ns"], expected_mtime)
        self.assertEqual(tombstone_row["logical_bytes"], 0)
        self.assertEqual(tombstone_row["sha256"], ARC.sha256_bytes(b""))
        self.assertEqual(tombstone_row["mode"], 0o400)
        self.assertTrue((root / ARC.RECORD_NAMES["deletion-receipt"]).is_file())
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        self.assertEqual(partial.stat().st_mtime_ns, expected_mtime)

    def lock_12(self) -> None:
        source = self.base / "mtime-foreign-source"
        source.mkdir(mode=0o700)
        (source / "first.bin").write_bytes(b"immutable first payload\n")
        (source / "second.bin").write_bytes(b"immutable second payload\n")
        fixed_mtime = 1_111_111_111_222_222_222
        first_info = (source / "first.bin").stat()
        os.utime(source / "first.bin",
                 ns=(first_info.st_atime_ns, fixed_mtime))
        candidate = ARC.CandidateSpec(
            "mtime-foreign", source, "completed-evidence",
            "reproducible-noncritical", "delete-after-full-pass")
        self.settings.candidates = (candidate,)
        self.settings.test_retail_roles = ()
        transaction = self.service.enqueue(candidate.ident)["transaction_id"]
        root = self.settings.queue_root / transaction
        retired_root, first_path, expected_mtime = \
            self.crash_after_ftruncate_before_mtime(transaction)
        self.assertEqual(first_path, "first.bin")
        self.assertEqual(expected_mtime, fixed_mtime)
        partial = retired_root / first_path
        held = self.base / "held-known-zeroed-inode"
        os.rename(partial, held)
        partial.write_bytes(b"")
        foreign_mtime = fixed_mtime + 444_444_444
        foreign_info = partial.stat()
        os.utime(partial, ns=(foreign_info.st_atime_ns, foreign_mtime))
        foreign_identity = ARC.identity(partial.stat())
        held_identity = ARC.identity(held.stat())

        with self.assertRaisesRegex(
                ARC.ArchiveError, "identity|differs|foreign"):
            self.drain(transaction)
        self.assertEqual(ARC.identity(partial.stat()), foreign_identity)
        self.assertEqual(partial.stat().st_mtime_ns, foreign_mtime)
        self.assertEqual(ARC.identity(held.stat()), held_identity)
        self.assertNotEqual(held.stat().st_mtime_ns, expected_mtime)
        self.assertFalse((root / ARC.RECORD_NAMES["source-deleted"]).exists())
        self.assertFalse((root / ARC.RECORD_NAMES["deletion-receipt"]).exists())

    # GATE-01..04
    def gate_01(self) -> None:
        transaction = self.enqueue()
        before = set(self.mount.rglob("*"))
        self.assertEqual(self.service.drain_one(transaction)["status"], "DRY_RUN")
        self.assertEqual(before, set(self.mount.rglob("*")))
        self.settings.review_authorized = False
        self.assertEqual(self.drain(transaction)["status"], "DEFERRED_REVIEW")
        self.assertTrue(self.source.exists())

    def gate_02(self) -> None:
        foreign = self.mount / ARC.ARCHIVE_ROOT
        foreign.chmod(0o750)
        volume = self.service.bind_volume()
        try:
            with self.assertRaisesRegex(ARC.ArchiveError, "foreign or unsafe"):
                self.service.external_roots(volume, "completed-evidence")
        finally:
            volume.close()
        self.assertEqual(stat.S_IMODE(foreign.stat().st_mode), 0o750)

    def gate_03(self) -> None:
        transaction = self.enqueue()
        labels: list[str] = []
        def track(fd: int, label: str) -> None:
            labels.append(label)
            self.platform.current_label = label
        self.service.hooks["before_fsync"] = track
        self.platform.fail_sync_label = "copied file nested/trace.bin"
        with self.assertRaisesRegex(ARC.ArchiveError, "fsync"):
            self.drain(transaction)
        self.assertTrue(self.source.exists())
        self.platform.fail_sync_label = None
        completed = self.enqueue()
        self.assertEqual(self.drain(completed)["status"], "PASS")
        self.assertTrue(any("copied file report.txt" in label for label in labels))
        self.assertTrue(any("copied directory nested" in label for label in labels))
        self.assertTrue(any("copied root directory" in label for label in labels))
        copied_identities: set[tuple[int, int]] = set()
        for path in self.final_path(completed).rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                continue
            flags = ARC.directory_open_flags() if path.is_dir() else os.O_RDONLY | os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            try:
                copied_identities.add(ARC.identity(os.fstat(descriptor)))
            finally:
                os.close(descriptor)
        root_fd = ARC.open_absolute_directory(self.final_path(completed), "published root")
        try:
            copied_identities.add(ARC.identity(os.fstat(root_fd)))
        finally:
            os.close(root_fd)
        self.assertTrue(copied_identities <= set(self.platform.synced))

    def gate_04(self) -> None:
        build = Path(__file__).with_name("build_check.sh").read_text()
        gate = Path(__file__).with_name("gate_hash.py").read_text()
        self.assertIn("test_archive_completed_artifacts.py", build)
        self.assertIn("archive_completed_artifacts.py", gate)
        self.assertIn("test_archive_completed_artifacts.py", gate)
        self.assertIn('"misc/ios/archive_completed_artifacts.py"', gate)
        self.assertIn('"misc/ios/test_archive_completed_artifacts.py"', gate)

        proof = self.service.matching_full_gate_receipt()
        self.assertEqual(proof["gate"], "full")
        self.assertEqual(proof["gate_source_sha256"], self.platform.gate_hash)
        self.platform.gate_hash = "f" * 64
        with self.assertRaisesRegex(ARC.ArchiveError, "input hash"):
            self.service.matching_full_gate_receipt()
        fresh_receipt = self.write_gate_receipt(
            source_hash=self.platform.gate_hash, ended=self.platform.clock - 5,
            receipt_name="gate-2000000001-123-0.json")
        proof = self.service.matching_full_gate_receipt()
        self.assertEqual(proof["receipt_name"], fresh_receipt.name)
        self.assertEqual(proof["gate_source_sha256"], self.platform.gate_hash)
        original_age = self.settings.gate_max_age_seconds
        self.settings.gate_max_age_seconds = 1
        with self.assertRaisesRegex(ARC.ArchiveError, "no fresh matching"):
            self.service.matching_full_gate_receipt()
        self.settings.gate_max_age_seconds = original_age

        transaction = self.enqueue()
        self.assertEqual(self.drain(transaction)["status"], "PASS")
        manifest = json.loads((self.mount / ARC.ARCHIVE_ROOT / ARC.MANIFEST_ROOT /
                               f"{transaction}.json").read_text())
        published = json.loads((self.settings.queue_root / transaction /
                                ARC.RECORD_NAMES["published"]).read_text())
        self.assertEqual(manifest["closure_proof_hash"], proof["receipt_sha256"])
        self.assertEqual(manifest["finished_utc"], published["published_utc"])
        self.assertNotEqual(manifest["finished_utc"], manifest["started_utc"])
        self.assertEqual(Path(manifest["destination"]), self.final_path(transaction))
        self.assertEqual(manifest["category"], "completed-evidence")
        self.assertEqual(manifest["allowlist_version"], ARC.ALLOWLIST_VERSION)
        self.assertEqual(manifest["source_tree_sha256"],
                         manifest["source_manifest"]["tree_sha256"])
        self.assertNotEqual(manifest["closure_proof_hash"],
                            manifest["source_tree_sha256"])


CASE_BODIES = {
    **{f"VOL-{index:02d}": getattr(ArchivePolicyTests, f"vol_{index:02d}") for index in range(1, 9)},
    **{f"SCOPE-{index:02d}": getattr(ArchivePolicyTests, f"scope_{index:02d}") for index in range(1, 9)},
    **{f"COPY-{index:02d}": getattr(ArchivePolicyTests, f"copy_{index:02d}") for index in range(1, 11)},
    **{f"REC-{index:02d}": getattr(ArchivePolicyTests, f"rec_{index:02d}") for index in range(1, 9)},
    **{f"DEL-{index:02d}": getattr(ArchivePolicyTests, f"del_{index:02d}") for index in range(1, 6)},
    **{f"RED-{index:02d}": getattr(ArchivePolicyTests, f"red_{index:02d}") for index in range(1, 10)},
    **{f"SOL-{index:02d}": getattr(ArchivePolicyTests, f"sol_{index:02d}") for index in range(1, 15)},
    **{f"OVR-{index:02d}": getattr(ArchivePolicyTests, f"ovr_{index:02d}") for index in range(1, 31)},
    **{f"ROB-{index:02d}": getattr(ArchivePolicyTests, f"rob_{index:02d}") for index in range(1, 10)},
    **{f"RPO-{index:02d}": getattr(ArchivePolicyTests, f"rpo_{index:02d}") for index in range(1, 9)},
    **{f"HST-{index:02d}": getattr(ArchivePolicyTests, f"hst_{index:02d}") for index in range(1, 7)},
    **{f"LOCK-{index:02d}": getattr(ArchivePolicyTests, f"lock_{index:02d}") for index in range(1, 13)},
    **{f"GATE-{index:02d}": getattr(ArchivePolicyTests, f"gate_{index:02d}") for index in range(1, 5)},
}


def make_case(case_id: str, body):  # type: ignore[no-untyped-def]
    def test(self: ArchivePolicyTests) -> None:
        body(self)
    test.__name__ = "test_" + case_id.replace("-", "_")
    test.__doc__ = case_id
    return test


for _case_id, _body in CASE_BODIES.items():
    setattr(ArchivePolicyTests, "test_" + _case_id.replace("-", "_"),
            make_case(_case_id, _body))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ArchivePolicyTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"archive_completed_artifacts: {passed}/{result.testsRun} PASS")
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun == 131 else 1)
