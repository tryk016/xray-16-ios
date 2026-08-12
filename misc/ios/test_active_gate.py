#!/usr/bin/env python3
"""Private-temp contracts for active_gate.py; no real repository state is used."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "misc/ios/active_gate.py"
SPEC = importlib.util.spec_from_file_location("active_gate", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ActiveGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        (self.repo / "doc").mkdir(parents=True)
        (self.repo / ".Codex").mkdir()
        (self.repo / ".gitignore").write_text(".Codex/runtime/\n", encoding="utf-8")
        self.write_sources(padded=True)
        subprocess.run(("git", "init", "-q", str(self.repo)), check=True)
        subprocess.run(("git", "-C", str(self.repo), "add", "."), check=True)
        subprocess.run(("git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "fixture"), check=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(("python3", str(TOOL), "--repo-root", str(self.repo), *args), text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write_sources(self, padded: bool) -> None:
        padding = ("deterministic capsule context " * 80 + "\n") * 72 if padded else ""
        task_padding = ("documented active-task evidence " * 80 + "\n") * 40 if padded else ""
        journal_padding = ("historical evidence for the active task " * 80 + "\n") * 24 if padded else ""
        (self.repo / "doc/iOS-Port-Resume.md").write_text(
            "# Resume\n## Current checkpoint\nKnown local fact.\n" + padding +
            "## Validation contracts\n| Gate | PASS |\n## Still pending\n- iPhone proof.\n## Next slice\n- Run task.\n## Safety\n- No stale gate.\n", encoding="utf-8")
        (self.repo / "doc/iOS-Port-Plan.md").write_text(
            "# Plan\n**Current focus:** prove the active task.\n## 1. IOS-P0-123: validate capsule evidence\n"
            "**Priority:** P0.\n**Acceptance:** deterministic output.\n**Next actions:**\n1. Run host test.\n" + task_padding +
            "## 2. IOS-P1-999: later\n", encoding="utf-8")
        (self.repo / "doc/iOS-Port.md").write_text(
            "# Canonical\n## Current product state\nCurrent local evidence only.\n"
            "## Known open work\n- Validate the active task on its required platform.\n"
            "- Preserve the source and stale-cache boundary for every checkpoint.\n"
            "## Definition of done\n1. Required evidence is recorded and independently reviewed.\n"
            "2. The active acceptance criterion is proven without broadening scope.\n"
            "## Other\nignored\n", encoding="utf-8")
        (self.repo / "doc/iOS-Port-Journal.md").write_text("# Journal\n## Today\nIOS-P0-123 capsule evidence passed locally.\n" + journal_padding, encoding="utf-8")
        (self.repo / ".Codex/session-log.md").write_text("recent operational fact\n", encoding="utf-8")

    def test_generate_is_deterministic_and_check_is_clean(self) -> None:
        first = self.run_tool("generate")
        self.assertEqual(first.returncode, 0, first.stderr)
        output = self.repo / ".Codex/runtime/active-gate.md"
        before = output.read_bytes()
        second = self.run_tool("generate")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(before, output.read_bytes())
        checked = self.run_tool("check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        manifest = json.loads(output.with_suffix(".manifest.json").read_text())
        self.assertEqual(manifest["active_task"]["id"], "IOS-P0-123")
        self.assertLessEqual(manifest["output_bytes"], manifest["byte_limit"])
        self.assertGreaterEqual(manifest["estimated_tokens"], MODULE.MIN_ESTIMATED_TOKENS)
        self.assertEqual(manifest["min_estimated_tokens"], MODULE.MIN_ESTIMATED_TOKENS)
        self.assertEqual(manifest["output_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(manifest["source_manifest_sha256"], hashlib.sha256(
            json.dumps(manifest["source_manifest"], sort_keys=True, separators=(",", ":")).encode() + b"\n").hexdigest())

    def test_clean_and_dirty_capsules_keep_bounds_and_exact_canonical_selection(self) -> None:
        clean = self.run_tool("generate")
        self.assertEqual(clean.returncode, 0, clean.stderr)
        capsule = self.repo / ".Codex/runtime/active-gate.md"
        manifest_path = capsule.with_suffix(".manifest.json")
        clean_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(clean_manifest["freshness"]["dirty_scope"], [])
        self.assertGreaterEqual(clean_manifest["estimated_tokens"], MODULE.MIN_ESTIMATED_TOKENS)
        self.assertLessEqual(clean_manifest["estimated_tokens"], MODULE.MAX_ESTIMATED_TOKENS)

        canonical_text = (self.repo / "doc/iOS-Port.md").read_text(encoding="utf-8")
        plan_text = (self.repo / "doc/iOS-Port-Plan.md").read_text(encoding="utf-8")
        task, title, _ = MODULE.active(plan_text)
        current = MODULE.match(canonical_text, task, title, 5_000, r"^## Current product state\s*$")
        completion = MODULE.completion_scope(canonical_text)
        canonical_record = next(item for item in clean_manifest["source_manifest"] if item["path"] == "doc/iOS-Port.md")
        self.assertEqual([item["path"] for item in clean_manifest["source_manifest"]], list(MODULE.SOURCES))
        self.assertEqual(canonical_record["source_sha256"], hashlib.sha256(canonical_text.encode()).hexdigest())
        self.assertEqual(canonical_record["selected_sha256"], hashlib.sha256((current + completion).encode()).hexdigest())
        self.assertEqual(canonical_record["selected_bytes"], len((current + completion).encode()))
        rendered = capsule.read_text(encoding="utf-8")
        self.assertIn("## Matching current facts", rendered)
        self.assertIn("## Canonical completion scope", rendered)
        self.assertIn("## Known open work", rendered)
        self.assertIn("## Definition of done", rendered)

        (self.repo / "doc/iOS-Port-Plan.md").write_text(plan_text + "\nLocal dirty scope proof.\n", encoding="utf-8")
        stale = self.run_tool("check")
        self.assertNotEqual(stale.returncode, 0)
        dirty = self.run_tool("generate")
        self.assertEqual(dirty.returncode, 0, dirty.stderr)
        dirty_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(dirty_manifest["freshness"]["dirty_scope"])
        self.assertGreaterEqual(dirty_manifest["estimated_tokens"], MODULE.MIN_ESTIMATED_TOKENS)
        self.assertLessEqual(dirty_manifest["estimated_tokens"], MODULE.MAX_ESTIMATED_TOKENS)
        self.assertEqual(self.run_tool("check").returncode, 0)

    def test_generate_fails_closed_when_required_canonical_section_is_missing(self) -> None:
        canonical = self.repo / "doc/iOS-Port.md"
        original = canonical.read_text(encoding="utf-8")
        for heading in ("Known open work", "Definition of done"):
            with self.subTest(heading=heading):
                renamed = original.replace(f"## {heading}\n", f"## Missing {heading}\n", 1)
                self.assertEqual(len(renamed), len(original) + len("Missing "))
                canonical.write_text(renamed, encoding="utf-8")
                result = self.run_tool("generate")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"canonical section missing: ## {heading}", result.stderr)
                permissive_sections = [
                    MODULE.section(renamed, rf"^## {name}\s*$", maximum)
                    for name, maximum in (("Known open work", 2_500), ("Definition of done", 1_500))
                ]
                permissive = "\n\n".join(
                    value.rstrip() for value in permissive_sections
                    if value != "[no matching bounded section]\n"
                ) + "\n"
                with mock.patch.object(MODULE, "completion_scope", return_value=permissive):
                    _, metadata = MODULE.render(
                        self.repo, (".Codex", "runtime", "active-gate.md"),
                        (".Codex", "runtime", "active-gate.manifest.json"), MODULE.DEFAULT_BYTE_LIMIT)
                self.assertGreaterEqual(metadata["estimated_tokens"], MODULE.MIN_ESTIMATED_TOKENS)
                self.assertLessEqual(metadata["estimated_tokens"], MODULE.MAX_ESTIMATED_TOKENS)
                canonical.write_text(original, encoding="utf-8")

    def test_check_fails_closed_on_source_or_output_change(self) -> None:
        self.assertEqual(self.run_tool("generate").returncode, 0)
        output = self.repo / ".Codex/runtime/active-gate.md"
        output.write_text("tampered\n", encoding="utf-8")
        self.assertNotEqual(self.run_tool("check").returncode, 0)
        self.assertEqual(self.run_tool("generate").returncode, 0)
        (self.repo / "doc/iOS-Port-Plan.md").write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(self.run_tool("check").returncode, 0)

    def test_generate_rejects_hard_byte_cap(self) -> None:
        result = self.run_tool("--byte-limit", "20", "generate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("byte limit", result.stderr)

    def test_generate_and_check_reject_undersized_capsule(self) -> None:
        self.write_sources(padded=False)
        result = self.run_tool("generate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("estimated-token limit", result.stderr)
        output = (".Codex", "runtime", "active-gate.md")
        manifest = (".Codex", "runtime", "active-gate.manifest.json")
        with mock.patch.object(MODULE, "MIN_ESTIMATED_TOKENS", 0):
            rendered, metadata = MODULE.render(self.repo, output, manifest, MODULE.DEFAULT_BYTE_LIMIT)
            MODULE.publish_pair(self.repo, output, manifest, rendered, MODULE.stable(metadata))
        checked = self.run_tool("check")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("estimated-token limit", checked.stderr)

    def test_failed_generate_keeps_good_cache_and_dirty_state_fails_closed(self) -> None:
        self.assertEqual(self.run_tool("generate").returncode, 0)
        output = self.repo / ".Codex/runtime/active-gate.md"
        good = output.read_bytes()
        self.assertNotEqual(self.run_tool("--byte-limit", "20", "generate").returncode, 0)
        self.assertEqual(output.read_bytes(), good)
        (self.repo / ".gitignore").write_text(".Codex/runtime/\n# tracked dirty\n", encoding="utf-8")
        self.assertNotEqual(self.run_tool("check").returncode, 0)
        self.assertEqual(self.run_tool("generate").returncode, 0)
        extra = self.repo / "untracked-proof.txt"
        extra.write_text("one", encoding="utf-8")
        self.assertNotEqual(self.run_tool("check").returncode, 0)
        self.assertEqual(self.run_tool("generate").returncode, 0)
        extra.write_text("two", encoding="utf-8")
        self.assertNotEqual(self.run_tool("check").returncode, 0)

    def test_rejects_source_and_output_symlinks(self) -> None:
        source = self.repo / "doc/iOS-Port.md"
        original = self.repo / "doc/canonical-real.md"
        source.rename(original)
        source.symlink_to(original.name)
        self.assertNotEqual(self.run_tool("generate").returncode, 0)
        source.unlink()
        original.rename(source)
        real_dir = self.repo / "real-output"
        real_dir.mkdir()
        (self.repo / "unsafe-output").symlink_to(real_dir.name, target_is_directory=True)
        self.assertNotEqual(self.run_tool("--output", str(self.repo / "unsafe-output/capsule.md"), "generate").returncode, 0)
        (self.repo / "unsafe-output").unlink()
        docs = self.repo / "doc"
        real_docs = self.repo / "doc-real"
        docs.rename(real_docs)
        docs.symlink_to(real_docs.name, target_is_directory=True)
        self.assertNotEqual(self.run_tool("generate").returncode, 0)

    def test_journal_prefers_newest_exact_task_entry(self) -> None:
        journal_padding = ("historical evidence for the active task " * 80 + "\n") * 24
        (self.repo / "doc/iOS-Port-Journal.md").write_text(
            "## Old historical entry\nIOS-P0-123 old exact evidence\n"
            "## Newest entry\nIOS-P0-123 newest exact evidence\n" + journal_padding +
            "## Generic\nvalidate capsule evidence but no exact id\n", encoding="utf-8")
        self.assertEqual(self.run_tool("generate").returncode, 0)
        capsule = (self.repo / ".Codex/runtime/active-gate.md").read_text(encoding="utf-8")
        self.assertIn("newest exact evidence", capsule)
        self.assertNotIn("old exact evidence", capsule)

    def test_session_tail_keeps_newest_complete_lines(self) -> None:
        old_marker = "OLD_SESSION_DATA_MUST_BE_CLIPPED"
        newest_marker = "NEWEST_SESSION_CLOSEOUT_MUST_SURVIVE"
        older = old_marker + "\n" + ("older context " * 12 + "\n") * 100
        (self.repo / ".Codex/session-log.md").write_text(
            older + newest_marker + "\n", encoding="utf-8")
        self.assertEqual(self.run_tool("generate").returncode, 0)
        capsule = (self.repo / ".Codex/runtime/active-gate.md").read_text(encoding="utf-8")
        tail = capsule.split("## Session-log tail\n", 1)[1].split("\n## Gate contracts", 1)[0]
        self.assertIn("[bounded selection truncated; earlier session-log lines omitted]", tail)
        self.assertIn(newest_marker, tail)
        self.assertNotIn(old_marker, tail)

    def test_second_publication_failure_restores_last_good_pair(self) -> None:
        self.assertEqual(self.run_tool("generate").returncode, 0)
        output = (".Codex", "runtime", "active-gate.md")
        manifest = (".Codex", "runtime", "active-gate.manifest.json")
        old_output = (self.repo / ".Codex/runtime/active-gate.md").read_bytes()
        old_manifest = (self.repo / ".Codex/runtime/active-gate.manifest.json").read_bytes()
        original = MODULE.atomic_write
        calls = {"count": 0}
        def flaky(*args):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected second publication failure")
            return original(*args)
        with mock.patch.object(MODULE, "atomic_write", side_effect=flaky):
            with self.assertRaises(OSError):
                MODULE.publish_pair(self.repo, output, manifest, b"new capsule", b"new manifest")
        self.assertEqual((self.repo / ".Codex/runtime/active-gate.md").read_bytes(), old_output)
        self.assertEqual((self.repo / ".Codex/runtime/active-gate.manifest.json").read_bytes(), old_manifest)
        self.assertEqual(self.run_tool("check").returncode, 0)

    def test_requires_private_runtime_and_artifact_permissions(self) -> None:
        self.assertEqual(self.run_tool("generate").returncode, 0)
        runtime = self.repo / ".Codex/runtime"
        output = runtime / "active-gate.md"
        manifest = runtime / "active-gate.manifest.json"
        self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        for path, mode in ((output, 0o644), (manifest, 0o644), (runtime, 0o755)):
            original = path.stat().st_mode & 0o777
            os.chmod(path, mode)
            self.assertNotEqual(self.run_tool("check").returncode, 0)
            self.assertNotEqual(self.run_tool("generate").returncode, 0)
            os.chmod(path, original)
            self.assertEqual(self.run_tool("check").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
