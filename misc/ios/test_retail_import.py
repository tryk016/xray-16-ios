#!/usr/bin/env python3
"""Host-only regression tests for the resumable retail importer."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / "misc/ios/retail_import.py"
GUARD = ROOT / "misc/ios/retail_simulator_guard.py"
spec = importlib.util.spec_from_file_location("retail_import_test_module", IMPORTER)
assert spec and spec.loader
ri = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ri)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RetailImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.backup = self.root / "backup"
        self.manifest = self.root / "source-manifest"
        self.output = self.root / "output"
        self.backup.mkdir()
        self.manifest.mkdir()
        self.output.mkdir()
        self.files: dict[str, bytes] = {}
        for prefix, count in (("resources/resources.db", 5), ("levels/levels.db", 2)):
            for index in range(count):
                self.files[f"{prefix}{index}"] = f"{prefix}{index}\n".encode()
        self.files.update({
            "localization/xefis_movies.db": b"movie payload\n" * 50,
            "localization/readme.txt": b"retail text\n",
            "patches/fix.db": b"patch\n",
            "_appdata_/savedgames/slot.scop": b"save payload\n",
            "bin/engine.cdb": b"outside allowlist\n",
        })
        for relative, data in self.files.items():
            target = self.backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        rows = ["bytes\tpath"] + [f"{len(data)}\t{relative}"
                                    for relative, data in sorted(self.files.items())]
        (self.manifest / "files.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        required = ["bytes\tsha256\tpath\tstatus"]
        for prefix, count in (("resources/resources.db", 5), ("levels/levels.db", 2)):
            for index in range(count):
                relative = f"{prefix}{index}"
                data = self.files[relative]
                required.append(f"{len(data)}\t{digest(data)}\t{relative}\tpresent")
        (self.manifest / "required-archives.tsv").write_text(
            "\n".join(required) + "\n", encoding="utf-8")
        large = self.files["localization/xefis_movies.db"]
        (self.manifest / "large-files-sha256.tsv").write_text(
            "sha256\tbytes\tpath\n"
            f"{digest(large)}\t{len(large)}\tlocalization/xefis_movies.db\n"
            f"{digest(self.files['_appdata_/savedgames/slot.scop'])}\t"
            f"{len(self.files['_appdata_/savedgames/slot.scop'])}\t_appdata_/savedgames/slot.scop\n"
            f"{digest(self.files['bin/engine.cdb'])}\t{len(self.files['bin/engine.cdb'])}\tbin/engine.cdb\n",
            encoding="utf-8")
        (self.manifest / "summary.txt").write_text("fixture summary\n", encoding="utf-8")
        (self.manifest / "zero-byte-files.txt").write_bytes(b"")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def destination(self, name: str = "prepared") -> Path:
        return self.output / name

    def command(self, destination: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run((sys.executable, str(IMPORTER), "prepare", "--backup", str(self.backup),
                               "--manifest", str(self.manifest), "--repo", str(self.repo),
                               "--destination", str(destination), *extra), text=True,
                              capture_output=True, check=False)

    def arguments(self, destination: Path, *, saves: bool = False) -> argparse.Namespace:
        return argparse.Namespace(backup=str(self.backup), manifest=str(self.manifest),
                                  repo=str(self.repo), destination=str(destination), with_saves=saves)

    def prepare_state(self, destination: Path, *, saves: bool = False) -> dict[str, object]:
        paths = ri.sidecars(destination)
        plan = ri.make_plan(self.backup, self.manifest, saves)
        data = ri.canonical_json(plan)
        ri.write_new(paths["plan.json"], data)
        ri.write_new(paths["recovery.json"], ri.canonical_json(
            {"schema": ri.RECOVERY_SCHEMA, "plan_sha256": digest(data)}))
        ri.mkdir_private(paths["partial"])
        ri.mkdir_private(paths["partial"] / "Documents")
        ri.mkdir_private(paths["partial"] / "manifest")
        return plan

    def assert_source_unchanged(self, before: dict[str, bytes]) -> None:
        after = {path.relative_to(self.backup).as_posix(): path.read_bytes()
                 for path in self.backup.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_happy_verify_guard_and_idempotence_with_and_without_saves(self) -> None:
        before = dict(self.files)
        for saves in (False, True):
            with self.subTest(saves=saves):
                destination = self.destination(f"prepared-{saves}")
                result = self.command(destination, *("--with-saves",) if saves else ())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(sorted(item.name for item in destination.iterdir()), ["Documents", "manifest"])
                self.assertEqual(sorted(item.name for item in (destination / "manifest").iterdir()),
                                 ["files.tsv", "large-files-sha256.tsv", "prepared-files.tsv",
                                  "prepared-manifest-files.tsv", "required-archives.tsv", "summary.txt",
                                  "zero-byte-files.txt"])
                self.assertEqual((destination / "manifest/summary.txt").read_bytes(),
                                 (self.manifest / "summary.txt").read_bytes())
                self.assertEqual((destination / "manifest/zero-byte-files.txt").read_bytes(), b"")
                check = subprocess.run((sys.executable, str(IMPORTER), "verify", "--prepared", str(destination)),
                                       text=True, capture_output=True, check=False)
                self.assertEqual(check.returncode, 0, check.stderr)
                guard = subprocess.run((sys.executable, str(GUARD), "retail-verify", "--backup",
                                        str(destination / "Documents"), "--manifest", str(destination / "manifest")),
                                       text=True, capture_output=True, check=False)
                self.assertEqual(guard.returncode, 0, guard.stderr)
                selected = (destination / "Documents" / "_appdata_/savedgames/slot.scop")
                self.assertEqual(selected.exists(), saves)
                large_rows = (destination / "manifest/large-files-sha256.tsv").read_text().splitlines()[1:]
                self.assertEqual(len(large_rows), 2 if saves else 1)
                self.assertNotIn("bin/engine.cdb", "\n".join(large_rows))
                rerun = self.command(destination, *("--with-saves",) if saves else ())
                self.assertEqual(rerun.returncode, 0, rerun.stderr)
                self.assertIn("idempotent", rerun.stdout)
        self.assert_source_unchanged(before)

    def test_idempotence_binds_summary_required_and_large_projection_to_plan(self) -> None:
        for name in ("summary.txt", "required-archives.tsv", "large-files-sha256.tsv"):
            with self.subTest(name=name):
                destination = self.destination(f"manifest-drift-{name}")
                result = self.command(destination, "--with-saves")
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = destination / "manifest"
                target = manifest / name
                if name == "summary.txt":
                    target.write_bytes(b"semantically unconstrained drift\n")
                elif name == "required-archives.tsv":
                    target.write_text(target.read_text().replace("\tpresent\n", "\tverified\n"))
                else:
                    lines = target.read_text().splitlines()
                    target.write_text(lines[0] + "\n" + "\n".join(reversed(lines[1:])) + "\n")
                projected = {item.name: item.read_bytes() for item in manifest.iterdir()
                             if item.name != ri.GENERATED_MANIFEST_FILES}
                (manifest / ri.GENERATED_MANIFEST_FILES).write_bytes(
                    ri.rendered_manifest_inventory(projected))
                # The self-contained verifier and runner still accept these semantic variants.
                ri.verify_prepared(destination)
                rerun = self.command(destination, "--with-saves")
                self.assertNotEqual(rerun.returncode, 0)
                self.assertIn("immutable plan", rerun.stderr)

    def test_resume_preserves_exact_prefix_at_varied_offsets(self) -> None:
        for offset in (0, 1, 17, len(self.files["localization/xefis_movies.db"]) - 1):
            with self.subTest(offset=offset):
                destination = self.destination(f"resume-{offset}")
                plan = self.prepare_state(destination)
                source = self.files["localization/xefis_movies.db"]
                partial = ri.sidecars(destination)["partial"] / "Documents/localization/xefis_movies.db"
                ri.mkdir_private(partial.parent)
                partial.write_bytes(source[:offset])
                partial.chmod(0o600)
                result = self.command(destination)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((destination / "Documents/localization/xefis_movies.db").read_bytes(), source)
                self.assertEqual(plan["file_count"], 10)

    def test_corrupt_or_too_long_partial_fails_untouched(self) -> None:
        for corrupt, extra in ((True, False), (False, True)):
            with self.subTest(corrupt=corrupt, extra=extra):
                destination = self.destination(f"bad-{corrupt}-{extra}")
                self.prepare_state(destination)
                source = self.files["localization/xefis_movies.db"]
                partial = ri.sidecars(destination)["partial"] / "Documents/localization/xefis_movies.db"
                ri.mkdir_private(partial.parent)
                bytes_ = (b"X" + source[1:8]) if corrupt else source + b"long"
                partial.write_bytes(bytes_)
                partial.chmod(0o600)
                result = self.command(destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(partial.read_bytes(), bytes_)
                self.assertFalse(destination.exists())

    def test_source_and_manifest_mutation_after_plan_are_rejected(self) -> None:
        for manifest_change in (False, True):
            with self.subTest(manifest_change=manifest_change):
                destination = self.destination(f"mutation-{manifest_change}")
                self.prepare_state(destination)
                if manifest_change:
                    with (self.manifest / "files.tsv").open("a", encoding="utf-8") as handle:
                        handle.write("# mutation\n")
                else:
                    target = self.backup / "patches/fix.db"
                    original = target.read_bytes()
                    target.write_bytes(b"changed\n")
                result = self.command(destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(destination.exists())
                if not manifest_change:
                    target.write_bytes(original)

    def test_manifest_mutation_between_initial_guard_and_capture_is_rejected(self) -> None:
        files_manifest = self.manifest / "files.tsv"
        original_manifest = files_manifest.read_bytes()
        original_guard = ri.GUARD.validate_retail
        source_guard_calls = 0
        try:
            def mutate_after_initial_guard(backup: Path, manifest: Path) -> object:
                nonlocal source_guard_calls
                source_call = backup == self.backup and manifest == self.manifest
                if source_call:
                    source_guard_calls += 1
                result = original_guard(backup, manifest)
                if source_call and source_guard_calls == 1:
                    relative = "bin/engine.cdb"
                    old_row = f"{len(self.files[relative])}\t{relative}".encode()
                    new_row = f"{len(self.files[relative]) + 1}\t{relative}".encode()
                    files_manifest.write_bytes(original_manifest.replace(old_row, new_row))
                return result
            ri.GUARD.validate_retail = mutate_after_initial_guard
            with self.assertRaisesRegex(ri.ImportError, "post-capture retail guard"):
                ri.make_plan(self.backup, self.manifest, False)
        finally:
            ri.GUARD.validate_retail = original_guard
            files_manifest.write_bytes(original_manifest)
        self.assertEqual(source_guard_calls, 2)

    def test_verify_source_reinventories_full_backup_for_extra_and_forbidden_removal(self) -> None:
        plan = ri.make_plan(self.backup, self.manifest, False)

        extra = self.backup / "patches/late-added.db"
        extra.write_bytes(b"late allowlisted payload\n")
        try:
            with self.assertRaisesRegex(ri.ImportError, "paths differ"):
                ri.verify_source(plan, self.backup, self.manifest)
        finally:
            extra.unlink()

        forbidden = self.backup / "bin/engine.cdb"
        forbidden_data = forbidden.read_bytes()
        forbidden.unlink()
        try:
            with self.assertRaisesRegex(ri.ImportError, "paths differ"):
                ri.verify_source(plan, self.backup, self.manifest)
        finally:
            forbidden.write_bytes(forbidden_data)

    def test_source_replacement_and_post_copy_mutation_are_rejected(self) -> None:
        replacement = self.backup / "patches/fix.db"
        original = replacement.read_bytes()
        destination = self.destination("replacement")
        self.prepare_state(destination)
        temporary = replacement.with_name("replacement.tmp")
        temporary.write_bytes(replacement.read_bytes())
        temporary.replace(replacement)
        result = self.command(destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed since planning", result.stderr)
        replacement.write_bytes(original)

        # A full existing partial must not skip the post-prefix source identity check.
        destination = self.destination("complete-partial-recheck")
        plan = self.prepare_state(destination)
        entry = next(item for item in plan["files"] if item["path"] == "localization/xefis_movies.db")
        full_partial = ri.sidecars(destination)["partial"] / "Documents/localization/xefis_movies.db"
        ri.mkdir_private(full_partial.parent)
        original_large = (self.backup / entry["path"]).read_bytes()
        full_partial.write_bytes(original_large)
        full_partial.chmod(0o600)
        original_prefix = ri.verify_prefix
        try:
            def mutate_after_prefix(source_fd: int, target_fd: int, count: int, label: str) -> None:
                original_prefix(source_fd, target_fd, count, label)
                (self.backup / entry["path"]).write_bytes(original_large + b"raced\n")
            ri.verify_prefix = mutate_after_prefix
            with self.assertRaises(ri.ImportError):
                ri.copy_one(self.backup, ri.sidecars(destination)["partial"], entry)
        finally:
            ri.verify_prefix = original_prefix
            (self.backup / entry["path"]).write_bytes(original_large)

        # Mutate after the initial verification/copy loop; the immediate
        # pre-publication source pass must detect this and retain the partial root.
        destination = self.destination("post-copy-mutation")
        original_copy = ri.copy_one
        mutated = False
        try:
            def mutate_after_copy(backup: Path, partial: Path, entry: dict[str, object]) -> None:
                nonlocal mutated
                original_copy(backup, partial, entry)
                if not mutated:
                    mutated = True
                    target = backup / "patches/fix.db"
                    target.write_bytes(target.read_bytes() + b"post-copy mutation\n")
            ri.copy_one = mutate_after_copy
            with self.assertRaises(ri.ImportError):
                ri.prepare(argparse.Namespace(backup=str(self.backup), manifest=str(self.manifest),
                                              repo=str(self.repo), destination=str(destination),
                                              with_saves=False))
        finally:
            ri.copy_one = original_copy
        self.assertFalse(destination.exists())
        self.assertTrue(ri.sidecars(destination)["partial"].exists())

    def test_mutation_after_output_verifier_before_final_source_check_is_rejected(self) -> None:
        destination = self.destination("last-source-window")
        source = self.backup / "patches/fix.db"
        original_data = source.read_bytes()
        original_verify = ri.verify_prepared
        injected = False
        try:
            def verify_then_mutate(final: Path | int, plan: dict[str, object] | None = None,
                                   *, guard_root: Path | None = None) -> None:
                nonlocal injected
                original_verify(final, plan, guard_root=guard_root)
                if not injected:
                    injected = True
                    source.write_bytes(original_data + b"after output hashing\n")
            ri.verify_prepared = verify_then_mutate
            with self.assertRaises(ri.ImportError):
                ri.prepare(self.arguments(destination))
        finally:
            ri.verify_prepared = original_verify
            source.write_bytes(original_data)
        self.assertTrue(injected)
        self.assertFalse(destination.exists())
        self.assertTrue(ri.sidecars(destination)["partial"].exists())

    def test_manifest_mutation_during_final_guard_is_rechecked_before_pass(self) -> None:
        destination = self.destination("final-guard-manifest-mutation")
        original_guard = ri.GUARD.validate_retail
        guard_calls = 0
        output = io.StringIO()
        try:
            def mutate_after_guard(documents: Path, manifest: Path) -> object:
                nonlocal guard_calls
                result = original_guard(documents, manifest)
                runner_call = documents == Path("Documents") and manifest == Path("manifest")
                if runner_call:
                    guard_calls += 1
                if runner_call and guard_calls == 2:
                    (manifest / "summary.txt").write_bytes(b"mutation during final guard\n")
                    projected = {
                        item.name: item.read_bytes()
                        for item in manifest.iterdir()
                        if item.name != ri.GENERATED_MANIFEST_FILES
                    }
                    (manifest / ri.GENERATED_MANIFEST_FILES).write_bytes(
                        ri.rendered_manifest_inventory(projected)
                    )
                return result
            ri.GUARD.validate_retail = mutate_after_guard
            with contextlib.redirect_stdout(output), self.assertRaisesRegex(
                    ri.ImportError, "immutable plan|runner compatibility validation"):
                ri.prepare(self.arguments(destination))
        finally:
            ri.GUARD.validate_retail = original_guard
        self.assertEqual(guard_calls, 2)
        self.assertTrue(destination.is_dir())
        self.assertNotIn("PASS", output.getvalue())
        retry = self.command(destination)
        self.assertNotEqual(retry.returncode, 0)
        self.assertNotIn("PASS", retry.stdout)
        self.assertIn("immutable plan", retry.stderr)

    def test_moved_directory_symlink_is_rejected_for_backup_and_source_manifest(self) -> None:
        try:
            destination = self.destination("backup-component-symlink")
            plan = self.prepare_state(destination)
            entry = next(item for item in plan["files"] if item["path"].startswith("localization/"))
            original = self.backup / "localization"
            moved = self.backup / "localization-moved"
            original.rename(moved)
            original.symlink_to(moved, target_is_directory=True)
            with self.assertRaises(ri.ImportError):
                ri.copy_one(self.backup, ri.sidecars(destination)["partial"], entry)
            result = self.command(destination)
            self.assertNotEqual(result.returncode, 0)
            original.unlink()
            moved.rename(original)

            destination = self.destination("manifest-component-symlink")
            self.prepare_state(destination)
            moved_manifest = self.manifest.with_name("source-manifest-moved")
            self.manifest.rename(moved_manifest)
            self.manifest.symlink_to(moved_manifest, target_is_directory=True)
            result = self.command(destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue("nofollow-open" in result.stderr or "canonical" in result.stderr,
                            result.stderr)
        except OSError as error:
            self.skipTest(f"host does not permit directory symlink fixture: {error}")
        finally:
            if self.manifest.is_symlink():
                self.manifest.unlink()
                moved_manifest.rename(self.manifest)
            original = self.backup / "localization"
            moved = self.backup / "localization-moved"
            if original.is_symlink():
                original.unlink()
                moved.rename(original)

    def test_destination_parent_swap_after_path_check_cannot_redirect_sidecars(self) -> None:
        parent = self.root / "anchored-destination"
        moved_parent = self.root / "anchored-destination-pinned"
        parent.mkdir()
        destination = parent / "prepared"

        def snapshot(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
            result: dict[str, tuple[int, int, bytes | None]] = {}
            for path in (root, *sorted(root.rglob("*"))):
                info = path.lstat()
                relative = "." if path == root else path.relative_to(root).as_posix()
                data = path.read_bytes() if path.is_file() else None
                result[relative] = (info.st_mode, info.st_nlink, data)
            return result

        protected_before = {
            "backup": snapshot(self.backup),
            "manifest": snapshot(self.manifest),
            "repo": snapshot(self.repo),
        }
        original_check = ri.check_paths
        swapped = False
        output = io.StringIO()
        try:
            def swap_after_check(*args: object, **kwargs: object) -> dict[str, Path]:
                nonlocal swapped
                result = original_check(*args, **kwargs)
                parent.rename(moved_parent)
                parent.symlink_to(self.backup, target_is_directory=True)
                swapped = True
                return result
            ri.check_paths = swap_after_check
            with contextlib.redirect_stdout(output), self.assertRaisesRegex(
                    ri.ImportError, "pinned descriptor"):
                ri.prepare(self.arguments(destination))
        finally:
            ri.check_paths = original_check
            if parent.is_symlink():
                parent.unlink()
            if moved_parent.exists():
                moved_parent.rename(parent)
        self.assertTrue(swapped)
        self.assertNotIn("PASS", output.getvalue())
        self.assertEqual(snapshot(self.backup), protected_before["backup"])
        self.assertEqual(snapshot(self.manifest), protected_before["manifest"])
        self.assertEqual(snapshot(self.repo), protected_before["repo"])
        names = ri.sidecar_names(destination.name)
        for name in (destination.name, *names.values()):
            self.assertFalse((self.backup / name).exists(), name)
            self.assertFalse((parent / name).exists(), name)

    def test_partial_replacement_before_or_inside_exclusive_rename_never_passes(self) -> None:
        before_rename = self.destination("partial-replaced-after-source-hash")
        partial = ri.sidecars(before_rename)["partial"]
        moved_partial = partial.with_name(partial.name + ".verified-original")
        original_verify_source = ri.verify_source
        verify_calls = 0
        output = io.StringIO()
        try:
            def replace_after_last_source_hash(plan: dict[str, object], backup: Path,
                                               manifest: Path) -> None:
                nonlocal verify_calls
                original_verify_source(plan, backup, manifest)
                verify_calls += 1
                if verify_calls == 3:
                    partial.rename(moved_partial)
                    partial.mkdir(mode=0o700)
                    partial.chmod(0o700)
            ri.verify_source = replace_after_last_source_hash
            with contextlib.redirect_stdout(output), self.assertRaisesRegex(
                    ri.ImportError, "verified partial root was replaced"):
                ri.prepare(self.arguments(before_rename))
        finally:
            ri.verify_source = original_verify_source
        self.assertEqual(verify_calls, 3)
        self.assertNotIn("PASS", output.getvalue())
        self.assertFalse(before_rename.exists())

        inside_rename = self.destination("partial-replaced-inside-rename")
        original_rename = ri.rename_exclusive_at
        output = io.StringIO()
        injected = False
        try:
            def replace_at_rename(parent_fd: int, source_name: str,
                                  destination_name: str) -> None:
                nonlocal injected
                moved_name = source_name + ".verified-original"
                os.rename(source_name, moved_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.mkdir(source_name, 0o700, dir_fd=parent_fd)
                replacement_fd = os.open(source_name, ri.directory_flags(), dir_fd=parent_fd)
                try:
                    os.fchmod(replacement_fd, 0o700)
                finally:
                    os.close(replacement_fd)
                injected = True
                original_rename(parent_fd, source_name, destination_name)
            ri.rename_exclusive_at = replace_at_rename
            with contextlib.redirect_stdout(output), self.assertRaisesRegex(
                    ri.ImportError, "identity does not match verified partial"):
                ri.prepare(self.arguments(inside_rename))
        finally:
            ri.rename_exclusive_at = original_rename
        self.assertTrue(injected)
        self.assertNotIn("PASS", output.getvalue())
        self.assertTrue(inside_rename.is_dir())
        verification = subprocess.run(
            (sys.executable, str(IMPORTER), "verify", "--prepared", str(inside_rename)),
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(verification.returncode, 0)
        self.assertNotIn("PASS", verification.stdout)

    def test_inventory_rejects_symlink_special_hardlink_and_casefold_entries(self) -> None:
        source = self.backup / "resources/resources.db0"
        link = self.backup / "resources/link.db"
        try:
            link.symlink_to(source)
        except OSError as error:
            self.skipTest(f"host does not permit symlink fixture: {error}")
        with self.assertRaises(ri.ImportError):
            ri.inventory(self.backup)
        link.unlink()
        hard = self.backup / "resources/hard.db"
        os.link(source, hard)
        with self.assertRaises(ri.ImportError):
            ri.inventory(self.backup)
        hard.unlink()
        fifo = self.backup / "resources/pipe"
        os.mkfifo(fifo)
        try:
            with self.assertRaises(ri.ImportError):
                ri.inventory(self.backup)
        finally:
            fifo.unlink()
        (self.backup / "localization/A.txt").write_bytes(b"a")
        (self.backup / "localization/a.txt").write_bytes(b"b")
        self.assertEqual(ri.collision_key("localization/A.txt"),
                         ri.collision_key("localization/a.txt"))
        names = {item.name for item in (self.backup / "localization").iterdir()}
        if {"A.txt", "a.txt"} <= names:
            with self.assertRaises(ri.ImportError):
                ri.inventory(self.backup)

    def test_lock_unknown_state_and_path_safety(self) -> None:
        destination = self.destination("locked")
        paths = ri.sidecars(destination)
        fd = ri.lock(paths["lock"])
        try:
            result = self.command(destination)
        finally:
            os.close(fd)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already running", result.stderr)
        info = paths["lock"].stat()
        self.assertEqual(info.st_uid, os.geteuid())
        self.assertEqual(info.st_nlink, 1)
        self.assertEqual(info.st_mode & 0o777, 0o600)
        hard = paths["lock"].with_name("lock-hardlink")
        os.link(paths["lock"], hard)
        try:
            with self.assertRaises(ri.ImportError):
                ri.lock(paths["lock"])
        finally:
            hard.unlink()
        paths["recovery.json"].write_text(json.dumps({"schema": "unknown"}), encoding="utf-8")
        result = self.command(destination)
        self.assertNotEqual(result.returncode, 0)
        malformed = self.destination("malformed-plan")
        malformed_paths = ri.sidecars(malformed)
        malformed_plan = ri.canonical_json({"schema": ri.PLAN_SCHEMA})
        ri.write_new(malformed_paths["plan.json"], malformed_plan)
        ri.write_new(malformed_paths["recovery.json"], ri.canonical_json(
            {"schema": ri.RECOVERY_SCHEMA, "plan_sha256": digest(malformed_plan)}))
        result = self.command(malformed)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue("invalid" in result.stderr or "unknown" in result.stderr, result.stderr)
        overlap = subprocess.run((sys.executable, str(IMPORTER), "prepare", "--backup", str(self.backup),
                                  "--manifest", str(self.manifest), "--repo", str(self.repo),
                                  "--destination", str(self.backup / "output")), text=True,
                                 capture_output=True, check=False)
        self.assertNotEqual(overlap.returncode, 0)
        with self.assertRaises(ri.ImportError):
            ri.check_paths(self.backup, self.manifest, self.root, self.destination("protected-overlap"))

    def test_manifest_plan_names_reject_traversal_and_control_characters(self) -> None:
        for index, unsafe in enumerate(("../escape", "bad\tname", "bad\nname", "bad\0name")):
            with self.subTest(unsafe=repr(unsafe)):
                destination = self.destination(f"unsafe-name-{index}")
                paths = ri.sidecars(destination)
                plan = ri.make_plan(self.backup, self.manifest, False)
                summary = next(item for item in plan["manifest_files"] if item["name"] == "summary.txt")
                summary["name"] = unsafe
                plan["manifest_files"] = sorted(plan["manifest_files"], key=lambda item: item["name"])
                plan_data = ri.canonical_json(plan)
                ri.write_new(paths["plan.json"], plan_data)
                ri.write_new(paths["recovery.json"], ri.canonical_json(
                    {"schema": ri.RECOVERY_SCHEMA, "plan_sha256": digest(plan_data)}))
                result = self.command(destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(destination.exists())
                self.assertFalse((self.output / "escape").exists())

    def test_wrong_mode_existing_lock_state_and_partial_fail_untouched(self) -> None:
        for kind in ("lock", "plan", "recovery", "partial-dir", "partial-file"):
            with self.subTest(kind=kind):
                destination = self.destination(f"wrong-mode-{kind}")
                paths = ri.sidecars(destination)
                if kind == "lock":
                    target = paths["lock"]
                    target.write_bytes(b"foreign mode lock")
                else:
                    self.prepare_state(destination)
                    if kind == "plan":
                        target = paths["plan.json"]
                    elif kind == "recovery":
                        target = paths["recovery.json"]
                    elif kind == "partial-dir":
                        target = paths["partial"] / "Documents"
                    else:
                        target = paths["partial"] / "Documents/bad.db"
                        target.write_bytes(b"partial")
                target.chmod(0o644 if target.is_file() else 0o755)
                before = target.read_bytes() if target.is_file() else None
                mode = target.stat().st_mode & 0o777
                result = self.command(destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.stat().st_mode & 0o777, mode)
                if before is not None:
                    self.assertEqual(target.read_bytes(), before)
        owner_lock = ri.sidecars(self.destination("wrong-owner"))["lock"]
        descriptor = ri.lock(owner_lock)
        os.close(descriptor)
        actual_uid = os.geteuid()
        original_geteuid = ri.os.geteuid
        try:
            ri.os.geteuid = lambda: actual_uid + 1
            with self.assertRaises(ri.ImportError):
                ri.lock(owner_lock)
        finally:
            ri.os.geteuid = original_geteuid

    def test_exclusive_publish_failure_race_and_post_rename_durability_reconcile(self) -> None:
        def args(destination: Path) -> argparse.Namespace:
            return argparse.Namespace(backup=str(self.backup), manifest=str(self.manifest),
                                      repo=str(self.repo), destination=str(destination), with_saves=False)

        destination = self.destination("rename-failure")
        old_rename = ri.rename_exclusive_at
        try:
            ri.rename_exclusive_at = lambda parent_fd, source, final: ri.fail(
                "synthetic exclusive rename failure")
            with self.assertRaises(ri.ImportError):
                ri.prepare(args(destination))
        finally:
            ri.rename_exclusive_at = old_rename
        self.assertFalse(destination.exists())
        self.assertTrue(ri.sidecars(destination)["partial"].exists())

        race = self.destination("rename-race")
        try:
            def publish_race(parent_fd: int, source: str, final: str) -> None:
                os.mkdir(final, 0o700, dir_fd=parent_fd)
                ri.fail("synthetic exclusive rename race")
            ri.rename_exclusive_at = publish_race
            with self.assertRaises(ri.ImportError):
                ri.prepare(args(race))
        finally:
            ri.rename_exclusive_at = old_rename
        self.assertTrue(race.exists())
        self.assertTrue(ri.sidecars(race)["partial"].exists())

        uncertain = self.destination("durability-uncertain")
        old_reconcile = ri.reconcile_published_parent_fd
        try:
            def rename_then_publish(parent_fd: int, source: str, final: str) -> None:
                os.rename(source, final, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            ri.rename_exclusive_at = rename_then_publish
            ri.reconcile_published_parent_fd = lambda parent_fd: ri.fail(
                "published/durability uncertain: synthetic post-rename fsync failure")
            with self.assertRaisesRegex(ri.ImportError, "published/durability uncertain"):
                ri.prepare(args(uncertain))
        finally:
            ri.rename_exclusive_at = old_rename
            ri.reconcile_published_parent_fd = old_reconcile
        self.assertTrue(uncertain.exists())
        # A retry must perform the parent fsync; another failure remains uncertain.
        retry_calls: list[int] = []
        try:
            def fail_retry(parent_fd: int) -> None:
                retry_calls.append(parent_fd)
                ri.fail("published/durability uncertain: synthetic retry fsync failure")
            ri.reconcile_published_parent_fd = fail_retry
            with self.assertRaisesRegex(ri.ImportError, "published/durability uncertain"):
                ri.prepare(args(uncertain))
        finally:
            ri.reconcile_published_parent_fd = old_reconcile
        self.assertTrue(retry_calls)
        success_calls: list[int] = []
        try:
            def record_retry(parent_fd: int) -> None:
                success_calls.append(parent_fd)
                old_reconcile(parent_fd)
            ri.reconcile_published_parent_fd = record_retry
            ri.prepare(args(uncertain))
        finally:
            ri.reconcile_published_parent_fd = old_reconcile
        self.assertTrue(success_calls)

    def test_plan_before_recovery_crash_reconciles_only_exact_current_orphan(self) -> None:
        def inject_boundary(destination: Path) -> bytes:
            paths = ri.sidecars(destination)
            names = ri.sidecar_names(destination.name)
            original_write = ri.write_new_name_at
            try:
                def crash_before_recovery(parent_fd: int, name: str, data: bytes,
                                          label: str) -> None:
                    if name == names["recovery.json"]:
                        ri.fail("synthetic plan-to-recovery crash")
                    original_write(parent_fd, name, data, label)
                ri.write_new_name_at = crash_before_recovery
                with self.assertRaisesRegex(ri.ImportError, "plan-to-recovery"):
                    ri.prepare(self.arguments(destination))
            finally:
                ri.write_new_name_at = original_write
            self.assertTrue(paths["plan.json"].is_file())
            self.assertFalse(paths["recovery.json"].exists())
            self.assertFalse(paths["partial"].exists())
            self.assertFalse(destination.exists())
            return paths["plan.json"].read_bytes()

        destination = self.destination("orphan-plan-reconcile")
        plan_bytes = inject_boundary(destination)
        result = self.command(destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ri.sidecars(destination)["plan.json"].read_bytes(), plan_bytes)

        incompatible = self.destination("orphan-plan-incompatible")
        incompatible_bytes = inject_boundary(incompatible)
        source = self.backup / "patches/fix.db"
        source_bytes = source.read_bytes()
        try:
            source.write_bytes(source_bytes + b"mutation\n")
            result = self.command(incompatible)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(ri.sidecars(incompatible)["plan.json"].read_bytes(), incompatible_bytes)
            self.assertFalse(ri.sidecars(incompatible)["recovery.json"].exists())
        finally:
            source.write_bytes(source_bytes)

    def test_output_extras_symlinks_and_casefold_collisions_are_rejected(self) -> None:
        destination = self.destination("extras")
        self.assertEqual(self.command(destination).returncode, 0)
        (destination / "Documents/extra.db").write_bytes(b"unexpected")
        check = subprocess.run((sys.executable, str(IMPORTER), "verify", "--prepared", str(destination)),
                               text=True, capture_output=True, check=False)
        self.assertNotEqual(check.returncode, 0)
        # Fresh sources exercise inventory before publication.  Symlink creation is available
        # on all supported host test systems; skip only where a host forbids it.
        source = self.backup / "resources/link.db"
        try:
            source.symlink_to(self.backup / "resources/resources.db0")
        except OSError as error:
            self.skipTest(f"host does not permit symlink fixture: {error}")
        rejected = self.command(self.destination("symlink"))
        self.assertNotEqual(rejected.returncode, 0)
        source.unlink()
        (self.backup / "localization/A.txt").write_bytes(b"a")
        (self.backup / "localization/a.txt").write_bytes(b"b")
        rejected = self.command(self.destination("casefold"))
        self.assertNotEqual(rejected.returncode, 0)

    def test_sparse_600mb_file_resumes_from_interior_offset_without_dense_fixture(self) -> None:
        large = self.backup / "localization/xefis_movies.db"
        with large.open("r+b") as handle:
            handle.truncate(600 * 1024 * 1024)
        size = large.stat().st_size
        # Rewrite the source manifests to a coherent sparse fixture.
        file_rows = []
        for relative, data in sorted(self.files.items()):
            file_rows.append(f"{size if relative == 'localization/xefis_movies.db' else len(data)}\t{relative}")
        (self.manifest / "files.tsv").write_text("bytes\tpath\n" + "\n".join(file_rows) + "\n")
        sparse_hash = ri.stable_file(large, "sparse fixture", capture=False)[1]
        (self.manifest / "large-files-sha256.tsv").write_text(
            f"sha256\tbytes\tpath\n{sparse_hash}\t{size}\tlocalization/xefis_movies.db\n")
        plan = ri.make_plan(self.backup, self.manifest, False)
        selected = next(item for item in plan["files"] if item["path"] == "localization/xefis_movies.db")
        partial = self.output / "sparse-partial"
        ri.mkdir_private(partial)
        ri.mkdir_private(partial / "Documents")
        target = partial / "Documents/localization/xefis_movies.db"
        ri.mkdir_private(target.parent)
        target.write_bytes(self.files["localization/xefis_movies.db"])
        offset = 257 * 1024 * 1024 + 123
        with target.open("r+b") as handle:
            handle.truncate(offset)
        target.chmod(0o600)
        inode_before = target.stat().st_ino
        with target.open("rb") as handle:
            prefix_before = handle.read(4096)
        original_write = ri.os.write
        try:
            def sparse_write(fd: int, data: bytes | memoryview) -> int:
                block = bytes(data)
                if block and not block.strip(b"\0"):
                    end = os.lseek(fd, len(block), os.SEEK_CUR)
                    os.ftruncate(fd, end)
                    return len(block)
                return original_write(fd, block)
            ri.os.write = sparse_write
            ri.copy_one(self.backup, partial, selected)
        finally:
            ri.os.write = original_write
        self.assertEqual(target.stat().st_ino, inode_before)
        self.assertEqual(target.stat().st_size, size)
        with target.open("rb") as handle:
            self.assertEqual(handle.read(4096), prefix_before)
        self.assertEqual(ri.stable_file(target, "resumed sparse output", capture=False)[1], sparse_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
