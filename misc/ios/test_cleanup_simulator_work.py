#!/usr/bin/env python3
"""Host-only safety tests for cleanup_simulator_work.sh."""

from __future__ import annotations

from datetime import datetime, timedelta
import fcntl
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).with_name("cleanup_simulator_work.sh")
DEAD_PID = 99_999_999


class CleanupSimulatorWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openxray-cleanup-test-")
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "handoff"
        self.root.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cleaner(self, *arguments: str,
                    environment: dict[str, str] | None = None
                    ) -> subprocess.CompletedProcess[str]:
        child_environment = os.environ.copy()
        if environment is not None:
            child_environment.update(environment)
        return subprocess.run(
            ("bash", str(SCRIPT), "--root", str(self.root), *arguments),
            text=True, capture_output=True, check=False, env=child_environment,
        )

    def candidate(self, hours_old: float, sequence: int,
                  *, pid: int = DEAD_PID, payload_bytes: int = 32) -> Path:
        stamp = datetime.now() - timedelta(hours=hours_old, seconds=sequence)
        path = self.root / f"simulator-work-{stamp:%Y%m%d-%H%M%S}-{pid}"
        path.mkdir(mode=0o700)
        (path / "payload.bin").write_bytes(bytes([sequence % 251]) * payload_bytes)
        modified = time.time() - hours_old * 3600
        os.utime(path / "payload.bin", (modified, modified))
        os.utime(path, (modified, modified))
        return path

    def test_default_is_dry_run_and_keeps_three_newest(self) -> None:
        paths = [self.candidate(72 + index, index) for index in range(5)]
        result = self.run_cleaner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(all(path.is_dir() for path in paths))
        self.assertEqual(result.stdout.count("status=WOULD_DELETE"), 2)
        self.assertIn("SUMMARY mode=DRY_RUN candidates=5 deleted=0 would_delete=2", result.stdout)
        self.assertRegex(result.stdout, r"approx_candidate_bytes=[1-9][0-9]*")

    def test_apply_honors_explicit_keep(self) -> None:
        paths = [self.candidate(72 + index, index) for index in range(5)]
        newest = set(sorted(paths, key=lambda path: path.name, reverse=True)[:2])
        result = self.run_cleaner("--apply", "--keep", "2", "--min-age-hours", "0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        existing = {path for path in paths if path.exists()}
        # Embedded timestamps differ by age, so lexicographic order is chronological.
        self.assertEqual(existing, newest)
        self.assertEqual(result.stdout.count("status=DELETED"), 3)
        self.assertIn("deleted=3", result.stdout)

    def test_minimum_age_uses_the_safer_of_name_and_mtime(self) -> None:
        old = self.candidate(72, 1)
        recently_touched = self.candidate(72, 2)
        os.utime(recently_touched, None)
        result = self.run_cleaner(
            "--apply", "--keep", "0", "--min-age-hours", "24")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(recently_touched.exists())
        self.assertIn(f"path={str(recently_touched)!r}".replace("'", '"'), result.stdout)
        self.assertIn("status=TOO_YOUNG", result.stdout)

    def test_live_suffix_pid_is_always_protected(self) -> None:
        live = self.candidate(96, 1, pid=os.getpid())
        result = self.run_cleaner(
            "--apply", "--keep", "0", "--min-age-hours", "0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(live.is_dir())
        self.assertIn("status=PROTECTED_LIVE_PID", result.stdout)
        self.assertIn("protected=1", result.stdout)

    def test_symlinks_are_never_followed_at_candidate_or_nested_level(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("must survive\n", encoding="utf-8")
        stamp = datetime.now() - timedelta(hours=96)
        candidate_link = self.root / f"simulator-work-{stamp:%Y%m%d-%H%M%S}-{DEAD_PID}"
        candidate_link.symlink_to(outside, target_is_directory=True)
        deletable = self.candidate(97, 2)
        (deletable / "outside-link").symlink_to(outside, target_is_directory=True)

        result = self.run_cleaner(
            "--apply", "--keep", "0", "--min-age-hours", "0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(candidate_link.is_symlink())
        self.assertFalse(deletable.exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "must survive\n")
        self.assertIn("reason=SYMLINK", result.stdout)

    def test_identity_swap_in_exact_predelete_window_preserves_both_trees(self) -> None:
        candidate = self.candidate(96, 7, payload_bytes=77)
        replacement = self.root / ".openxray-cleanup-test-foreign"
        replacement.mkdir(mode=0o700)
        foreign = replacement / "foreign.txt"
        foreign.write_text("foreign bytes must survive\n", encoding="utf-8")
        displaced_name = ".openxray-cleanup-test-displaced"
        result = self.run_cleaner(
            "--apply", "--keep", "0", "--min-age-hours", "0",
            environment={
                "OPENXRAY_CLEANUP_TEST_MODE": "openxray-cleanup-test-v1",
                "OPENXRAY_CLEANUP_TEST_SWAP_CANDIDATE": candidate.name,
                "OPENXRAY_CLEANUP_TEST_SWAP_REPLACEMENT": replacement.name,
                "OPENXRAY_CLEANUP_TEST_SWAP_DISPLACED": displaced_name,
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(candidate.exists())
        displaced = self.root / displaced_name
        self.assertEqual((displaced / "payload.bin").stat().st_size, 77)
        quarantines = list(self.root.glob(".openxray-cleanup-quarantine-*"))
        self.assertEqual(len(quarantines), 1)
        preserved_foreign = list(quarantines[0].rglob("foreign.txt"))
        self.assertEqual(len(preserved_foreign), 1)
        self.assertEqual(
            preserved_foreign[0].read_text(encoding="utf-8"),
            "foreign bytes must survive\n")
        self.assertIn("status=QUARANTINED_IDENTITY_MISMATCH", result.stdout)
        self.assertIn("deleted=0", result.stdout)
        self.assertIn("quarantined=1", result.stdout)

    def test_global_root_lock_rejects_a_concurrent_cleaner(self) -> None:
        candidate = self.candidate(96, 3)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_cleaner(
                "--apply", "--keep", "0", "--min-age-hours", "0")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("holds the root lock", result.stderr)
        self.assertTrue(candidate.is_dir())

    def test_weird_candidates_and_unsafe_roots_fail_closed(self) -> None:
        invalid = self.root / "simulator-work-20261340-996061-123"
        invalid.mkdir()
        near_match = self.root / "simulator-work-20240101-010101-0"
        near_match.mkdir()
        stamp = datetime.now() - timedelta(hours=96)
        regular = self.root / f"simulator-work-{stamp:%Y%m%d-%H%M%S}-{DEAD_PID}"
        regular.write_text("not a directory\n", encoding="utf-8")

        result = self.run_cleaner(
            "--apply", "--keep", "0", "--min-age-hours", "0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(invalid.is_dir())
        self.assertTrue(near_match.is_dir())
        self.assertTrue(regular.is_file())
        self.assertEqual(result.stdout.count("SKIPPED"), 3)
        self.assertIn("reason=INVALID_TIMESTAMP", result.stdout)
        self.assertIn("reason=INVALID_FORMAT", result.stdout)
        self.assertIn("reason=NOT_DIRECTORY", result.stdout)

        root_link = self.base / "handoff-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        linked = subprocess.run(
            ("bash", str(SCRIPT), "--root", str(root_link)),
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(linked.returncode, 0)
        self.assertIn("nofollow-open root", linked.stderr)

        weird_root = str(self.root / ".." / self.root.name)
        lexical = subprocess.run(
            ("bash", str(SCRIPT), "--root", weird_root),
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(lexical.returncode, 0)
        self.assertIn("lexically normalized", lexical.stderr)

        missing = subprocess.run(
            ("bash", str(SCRIPT), "--root", str(self.base / "missing")),
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("not an existing directory", missing.stderr)


if __name__ == "__main__":
    unittest.main()
