#!/usr/bin/env python3
"""Cheap contracts for the Phase 1A mocked-retail profile manifest."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment(
            "python::misc/ios/test_retail_test_profiles.py", sys.argv)
    except BaseException:
        pass

import retail_test_profiles as profiles
from test_retail_fixture_contract import RetailFixtureContract

# The support class is deliberately executed by this established entrypoint.
# Keep its stable telemetry origin here too; it has no direct gate command.
RetailFixtureContract.__module__ = __name__


class RetailTestProfilesContract(unittest.TestCase):
    def write_manifest(self, value: dict) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="openxray-retail-profile-contract-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "profiles.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_frozen_manifest_sets_and_default_selection_are_exact(self) -> None:
        manifest = profiles.load_manifest()
        self.assertEqual(manifest["selection_authority"], "NONE")
        self.assertFalse(manifest["input_mapping_complete"])
        self.assertEqual(set(profiles.inventory_ids()), {case["id"] for case in manifest["cases"]})
        for name, (count, digest) in profiles.FROZEN.items():
            with self.subTest(name=name):
                if name in ("guard", "integration"):
                    selected = [case["id"] for case in manifest["cases"]
                                if case["lane"] == name]
                elif name == "smoke":
                    selected = sorted(profiles.SMOKE_IDS)
                else:
                    selected = manifest["profiles"][name]["case_ids"]
                self.assertEqual(len(selected), count)
                self.assertEqual(profiles.stable_digest(selected), digest)
        self.assertEqual(profiles.select_case_ids(), manifest["profiles"]["complete"]["case_ids"])
        self.assertEqual(len(profiles.select_case_ids("host-fast")), 55)
        self.assertEqual(len(profiles.select_case_ids("retail-integration")), 49)
        self.assertEqual(manifest["profiles"]["complete"]["stage_timeout_seconds"], 1200)
        self.assertEqual(manifest["profiles"]["retail-integration"]["stage_timeout_seconds"], 1200)
        self.assertEqual(manifest["profiles"]["host-fast"]["stage_timeout_seconds"], 120)

    def test_listings_select_exact_existing_ids_without_executing_them(self) -> None:
        for profile, (count, digest) in (
                ("complete", profiles.FROZEN["complete"]),
                ("host-fast", profiles.FROZEN["host-fast"]),
                ("retail-integration", profiles.FROZEN["integration"]),
        ):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    [sys.executable, "misc/ios/retail_test_profiles.py", "--profile", profile, "--list"],
                    cwd=profiles.ROOT, text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                listing = json.loads(result.stdout)
                self.assertEqual(listing["profile"], profile)
                self.assertEqual(listing["count"], count)
                self.assertEqual(listing["sha256"], digest)
                if profile == "complete":
                    self.assertEqual(set(listing["case_ids"]), profiles.inventory_ids())

    def test_stdlib_exact_name_loader_preserves_profile_order_without_running(self) -> None:
        for profile, (count, _) in (
                ("complete", profiles.FROZEN["complete"]),
                ("host-fast", profiles.FROZEN["host-fast"]),
                ("retail-integration", profiles.FROZEN["integration"]),
        ):
            with self.subTest(profile=profile):
                suite = profiles.load_suite(profile)
                selected = profiles.select_case_ids(profile)
                discovered = [case.id().rsplit(".", 1)[-1] for case in profiles._flatten(suite)]
                expected = [identifier.rsplit(".", 1)[1] for identifier in selected]
                self.assertEqual(len(discovered), count)
                self.assertEqual(discovered, expected)

    def test_malformed_unknown_duplicate_inventory_and_classification_fail_before_suite_load(self) -> None:
        manifest = profiles.load_manifest()
        with self.assertRaisesRegex(profiles.ProfileError, "unknown retail profile"):
            profiles.select_case_ids("not-a-profile")
        for mutation, expression in (
            ("duplicate", lambda value: value["cases"].append(copy.deepcopy(value["cases"][0]))),
            ("missing", lambda value: value["cases"].pop()),
            ("extra", lambda value: value["cases"].append({
                "id": "py:misc/ios/test_retail_simulator.py::RetailSimulatorTests.test_extra",
                "lane": "guard", "fixture": "guard", "labels": ["retail", "guard", "unit"],
                "resources": ["host", "filesystem"], "timeout_seconds": 30,
            })),
            ("classification", lambda value: value["cases"][4].__setitem__("lane", "integration")),
        ):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(manifest)
                expression(changed)
                with self.assertRaises(profiles.ProfileError):
                    profiles.load_manifest(self.write_manifest(changed))
        result = subprocess.run(
            [sys.executable, "misc/ios/retail_test_profiles.py", "--profile", "not-a-profile", "--list"],
            cwd=profiles.ROOT, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown retail profile", result.stderr)

    def test_real_selector_propagates_selected_host_fast_failure(self) -> None:
        """A copied selector validates, loads and reports a real selected failure."""
        manifest = profiles.load_manifest()
        failing_id = next(identifier for identifier in manifest["profiles"]["host-fast"]["case_ids"]
                          if identifier in profiles.SMOKE_IDS)
        failing_method = failing_id.rsplit(".", 1)[1]
        with tempfile.TemporaryDirectory(prefix="openxray-retail-profile-selector-") as temporary:
            root = Path(temporary)
            ios = root / "misc/ios"
            ios.mkdir(parents=True)
            for source in (profiles.MANIFEST_PATH, profiles.ROOT / "misc/ios/retail_test_profiles.py"):
                shutil.copy2(source, ios / source.name)
            lines = ["import unittest", "", "class RetailSimulatorTests(unittest.TestCase):"]
            for case in manifest["cases"]:
                method = case["id"].rsplit(".", 1)[1]
                body = "self.fail('forced host-fast failure')" if method == failing_method else "pass"
                lines.extend((f"    def {method}(self):", f"        {body}", ""))
            (ios / "test_retail_simulator.py").write_text("\n".join(lines), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ios / "retail_test_profiles.py"), "--profile", "host-fast"],
                cwd=root, text=True, capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(failing_method, result.stderr)
        self.assertIn("forced host-fast failure", result.stderr)
        self.assertIn("Ran 55 tests", result.stderr)


if __name__ == "__main__":
    unittest.main()
