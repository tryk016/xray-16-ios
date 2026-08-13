"""Persisted Phase 1B contracts for the lazily composed retail fixture.

This module is intentionally imported by ``test_retail_test_profiles.py``.  It
has no standalone ``__main__`` entrypoint: telemetry, catalog ownership and
gate execution remain with that existing entrypoint.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import stat
import unittest

from test_retail_simulator import RetailSimulatorTests


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "misc/ios/retail_fixture_contract.json"


def _node_digest(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest()


def _fixture_records(case: RetailSimulatorTests) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(case.root.rglob("*")):
        details = path.lstat()
        record: dict[str, object] = {
            "mode": f"{stat.S_IMODE(details.st_mode):04o}",
            "path": path.relative_to(case.root).as_posix(),
        }
        if stat.S_ISDIR(details.st_mode):
            record["type"] = "dir"
        elif stat.S_ISLNK(details.st_mode):
            record.update(type="symlink", target=path.readlink().as_posix())
        elif stat.S_ISREG(details.st_mode):
            payload = path.read_bytes()
            if record["path"] == "repo/build/ios-prefix-iphonesimulator/lib/pkgconfig/fixture.pc":
                payload = payload.replace(str(case.repo).encode("utf-8"), b"<repo>")
            record.update(
                type="file",
                bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        else:
            raise AssertionError(f"unsupported fixture node: {path}")
        records.append(record)
    return records


class RetailFixtureContract(unittest.TestCase):
    """Keep Phase 1B mechanics outside the frozen 102 retail test bodies."""

    def setUp(self) -> None:
        self.golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    def fixture(self) -> RetailSimulatorTests:
        case = RetailSimulatorTests("runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        return case

    def test_frozen_retail_bodies_and_unapproved_helpers_are_ast_identical(self) -> None:
        self.assertEqual(self.golden["schema"], "openxray-phase1b-fixture-contract-v1")
        self.assertEqual(self.golden["baseline"], {
            "commit": "77801820f72098a137bf34ea8726ae6bdef6dcca",
            "source_sha256": "70a4e469335dccba94033ca66879d7b00d20eded748d156a496269be9a5088ee",
            "snapshot_sha256": "5c63928c98f57cd9b263855cf34cc565f0ec16da097823dd9875590d0c11126f",
        })
        self.assertEqual(
            self.golden["snapshot_provenance"],
            "snapshot_sha256 is SHA-256 of the external historical JSON snapshot bytes used "
            "to derive this checked-in golden; runtime tests deliberately verify the copied "
            "provenance value rather than depend on the external file.",
        )
        source = ROOT / "misc/ios/test_retail_simulator.py"
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        classes = [node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "RetailSimulatorTests"]
        self.assertEqual(len(classes), 1)
        functions = {
            node.name: node for node in classes[0].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        actual_tests = {
            f"RetailSimulatorTests.{name}": _node_digest(node)
            for name, node in functions.items() if name.startswith("test_")
        }
        self.assertEqual(actual_tests, self.golden["test_method_ast_sha256"])
        approved = set(self.golden["approved_modified_helpers"])
        self.assertEqual(approved, {
            "_mock", "_retail_file", "_write_diagnostic_guard_wrapper", "_write_mocks",
            "_write_navigation_controller", "_write_quickload_evidence_mock",
            "_write_retail_manifests", "prepare_retail_fixture", "run_guard",
            "run_launch_proof", "run_runner", "setUp", "stage_runtime_fixture",
        })
        for identifier, expected in self.golden["helper_ast_sha256"].items():
            name = identifier.rsplit(".", 1)[1]
            with self.subTest(helper=name):
                self.assertNotIn(name, approved)
                self.assertIn(name, functions)
                self.assertEqual(_node_digest(functions[name]), expected)

    def test_component_state_transitions_to_ready_and_failure_is_terminal(self) -> None:
        case = self.fixture()
        self.assertEqual(case._fixture_state["repo"], "UNSEEN")
        self.assertTrue(case.repo.is_dir())
        self.assertEqual(case._fixture_state["repo"], "READY")
        attempts: list[str] = []

        def fail_build() -> None:
            attempts.append("build")
            raise ValueError("deliberate fixture failure")

        with self.assertRaisesRegex(ValueError, "deliberate fixture failure"):
            case._ensure_component("retail", fail_build, lambda: None)
        self.assertEqual(case._fixture_state["retail"], "FAILED")
        with self.assertRaisesRegex(RuntimeError, "previously failed"):
            case._ensure_component("retail", fail_build, lambda: None)
        self.assertEqual(attempts, ["build"])

    def test_component_cycle_is_fail_closed_and_terminal(self) -> None:
        case = self.fixture()
        name = "recursive-contract"
        case._fixture_state[name] = "UNSEEN"
        calls: list[str] = []

        def recursive_build() -> None:
            calls.append("build")
            self.assertEqual(case._fixture_state[name], "BUILDING")
            case._ensure_component(name, recursive_build, lambda: None)

        with self.assertRaisesRegex(RuntimeError, f"dependency cycle at {name}"):
            case._ensure_component(name, recursive_build, lambda: None)
        self.assertEqual(calls, ["build"])
        self.assertEqual(case._fixture_state[name], "FAILED")
        with self.assertRaisesRegex(RuntimeError, "previously failed"):
            case._ensure_component(name, recursive_build, lambda: None)

    def test_work_base_reassignment_uses_only_the_caller_path(self) -> None:
        case = self.fixture()
        original = case._work_base_path
        assigned = case.root / "caller-selected-work-base"
        case.work_base = assigned
        self.assertEqual(case._fixture_state["work-base"], "UNSEEN")
        self.assertEqual(case.work_base, assigned)
        self.assertFalse(original.exists())
        case._ensure_runner_fixture()
        self.assertTrue(assigned.is_dir())
        self.assertFalse(original.exists())
        self.assertEqual(case._fixture_state["work-base"], "READY")

    def test_retail_and_stamp_mutations_are_not_silently_repaired(self) -> None:
        case = self.fixture()
        case._ensure_runner_fixture()
        retail = case.backup / "resources/resources.db0"
        stamp = case.repo / "build/ios-engine-iphoneos/.ios_full_gate_ok"
        retail.write_bytes(b"retail mutation")
        stamp.write_bytes(b"stamp mutation")
        case._ensure_retail_fixture()
        case._ensure_repo_fixture()
        case._ensure_runner_fixture()
        self.assertEqual(retail.read_bytes(), b"retail mutation")
        self.assertEqual(stamp.read_bytes(), b"stamp mutation")

    def test_runner_mock_mutations_fail_closed_without_repair(self) -> None:
        def node_signature(path: Path) -> tuple[object, ...]:
            if not path.exists() and not path.is_symlink():
                return ("missing",)
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode):
                return ("symlink", stat.S_IMODE(details.st_mode), path.readlink().as_posix())
            if stat.S_ISDIR(details.st_mode):
                return ("dir", stat.S_IMODE(details.st_mode))
            self.assertTrue(stat.S_ISREG(details.st_mode), path)
            return ("file", stat.S_IMODE(details.st_mode),
                    hashlib.sha256(path.read_bytes()).hexdigest(), details.st_dev, details.st_ino)

        def delete(path: Path) -> None:
            path.unlink()

        def rewrite(path: Path) -> None:
            path.write_bytes(b"#!/usr/bin/env python3\nraise SystemExit(98)\n")

        def chmod(path: Path) -> None:
            path.chmod(0o744)

        def replace_with_directory(path: Path) -> None:
            path.unlink()
            path.mkdir()

        def replace_with_symlink(path: Path) -> None:
            target = path.with_name(f"{path.name}.symlink-target")
            target.write_bytes(b"not a published mock\n")
            target.chmod(0o755)
            path.unlink()
            path.symlink_to(target.name)

        def replace_with_identical_bytes_new_inode(path: Path) -> None:
            expected = path.read_bytes()
            old_inode = path.lstat().st_ino
            replacement = path.with_name(f"{path.name}.same-bytes-replacement")
            replacement.write_bytes(expected)
            replacement.chmod(stat.S_IMODE(path.lstat().st_mode))
            replacement.replace(path)
            self.assertNotEqual(path.lstat().st_ino, old_inode)

        mutations = (
            ("git", None, "delete", delete),
            ("rsync", None, "byte-rewrite", rewrite),
            ("cmake", None, "mode", chmod),
            ("xcodebuild", None, "directory", replace_with_directory),
            ("ar", None, "symlink", replace_with_symlink),
            ("nm", None, "identical-bytes-new-inode", replace_with_identical_bytes_new_inode),
            ("otool", None, "byte-rewrite", rewrite),
            ("ps", "lifecycle", "mode", chmod),
            ("xcrun", "xcrun", "identical-bytes-new-inode", replace_with_identical_bytes_new_inode),
            ("lipo", "lipo", "symlink", replace_with_symlink),
        )
        for name, component, mutation_class, mutate in mutations:
            with self.subTest(mock=name, mutation_class=mutation_class):
                case = self.fixture()
                case._ensure_runner_fixture()
                target = ((case.lifecycle_mocks if component == "lifecycle" else case.mocks) / name)
                mutate(target)
                mutated_signature = node_signature(target)
                with self.assertRaises((RuntimeError, OSError)):
                    case._ensure_runner_fixture()
                self.assertEqual(case._fixture_state["runner"], "FAILED")
                if component in {"xcrun", "lipo"}:
                    self.assertEqual(case._fixture_state[component], "FAILED")
                else:
                    self.assertEqual(case._fixture_state["xcrun"], "READY")
                    self.assertEqual(case._fixture_state["lipo"], "READY")
                self.assertEqual(node_signature(target), mutated_signature)
                with self.assertRaisesRegex(RuntimeError, "previously failed"):
                    case._ensure_runner_fixture()
                self.assertEqual(node_signature(target), mutated_signature)

    def test_shared_xcrun_and_lipo_ownership_is_inode_stable_across_composition(self) -> None:
        case = self.fixture()
        case._ensure_launch_fixture()
        xcrun = case.mocks / "xcrun"
        xcrun_identity = (xcrun.stat().st_dev, xcrun.stat().st_ino)
        case._ensure_binary_fixture()
        lipo = case.mocks / "lipo"
        lipo_identity = (lipo.stat().st_dev, lipo.stat().st_ino)
        case._ensure_runner_fixture()
        self.assertEqual((xcrun.stat().st_dev, xcrun.stat().st_ino), xcrun_identity)
        self.assertEqual((lipo.stat().st_dev, lipo.stat().st_ino), lipo_identity)
        self.assertEqual(case._fixture_state["xcrun"], "READY")
        self.assertEqual(case._fixture_state["lipo"], "READY")

    def test_runner_fixture_matches_the_frozen_96_record_snapshot(self) -> None:
        case = self.fixture()
        case._ensure_runner_fixture()
        records = _fixture_records(case)
        expected = self.golden["runner_fixture"]
        self.assertEqual(len(records), expected["record_count"])
        self.assertEqual(records, expected["records"])
        canonical = json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n"
        self.assertEqual(hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                         expected["canonical_json_sha256"])
        fixture_pc = next(record for record in records if str(record["path"]).endswith("/fixture.pc"))
        self.assertEqual(fixture_pc["bytes"], expected["fixture_pc"]["normalized_bytes"])
        self.assertEqual(fixture_pc["sha256"], expected["fixture_pc"]["normalized_sha256"])
        symlink_target = case.root / "synthetic-symlink-target"
        symlink_target.write_bytes(b"synthetic target\n")
        symlink = case.root / "synthetic-symlink"
        symlink.symlink_to(symlink_target.name)
        symlink_record = next(
            record for record in _fixture_records(case)
            if record["path"] == "synthetic-symlink"
        )
        symlink_details = symlink.lstat()
        self.assertEqual(symlink_record, {
            "mode": f"{stat.S_IMODE(symlink_details.st_mode):04o}",
            "path": "synthetic-symlink",
            "type": "symlink",
            "target": symlink_target.name,
        })
        self.assertNotIn("bytes", symlink_record)
        self.assertNotIn("sha256", symlink_record)
