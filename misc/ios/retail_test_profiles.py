#!/usr/bin/env python3
"""Fail-closed manifest selection for the mocked retail Simulator suite.

This helper deliberately does not participate in build_check yet.  Existing
``test_retail_simulator.py`` execution remains the complete 102-case suite;
the explicit profiles here are a Phase 1A contract for later orchestration.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import unittest
from typing import Any, TextIO


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "misc/ios/retail_test_profiles.json"
SCHEMA = "openxray.ios.retail-test-profiles.v1"
TARGET_FILE = "misc/ios/test_retail_simulator.py"
TARGET_MODULE = "test_retail_simulator"
TARGET_CLASS = "RetailSimulatorTests"
PROFILE_NAMES = ("complete", "host-fast", "retail-integration")
FROZEN = {
    "complete": (102, "c46c5ac58a477b21a9217f5df477a2e42e9c4649060d184f38fddd6cd6786b36"),
    "guard": (53, "c6179d1bdb509dad1ea994e64a26365b2c89074c640a18de1ebb618246905567"),
    "integration": (49, "b0df68952a9f912cac236fa1725a46b170de1c0017b665dda1cf9f65cca5d9ee"),
    "smoke": (2, "0da7d9b2b29edc77225a7b8e618cab98ae6142d9305d6b30db02796993927beb"),
    "host-fast": (55, "c4b8fd6b853e964488c9edba9b7ca69aebb39e00e3ccf0f07f2aa0ec314a47ae"),
}
SMOKE_IDS = frozenset((
    "py:misc/ios/test_retail_simulator.py::RetailSimulatorTests.test_mocked_happy_path_isolated_and_deletes_only_own_uuid",
    "py:misc/ios/test_retail_simulator.py::RetailSimulatorTests.test_successful_path_rejects_delete_failure_and_never_prints_pass",
))


class ProfileError(RuntimeError):
    """The manifest/profile cannot safely select a test."""


def stable_digest(identifiers: list[str] | set[str]) -> str:
    if len(identifiers) != len(set(identifiers)):
        raise ProfileError("duplicate stable test ID")
    if any(not isinstance(identifier, str) or not identifier or "\n" in identifier
           or "\r" in identifier for identifier in identifiers):
        raise ProfileError("invalid stable test ID")
    return hashlib.sha256(("\n".join(sorted(identifiers)) + "\n").encode()).hexdigest()


def inventory_ids(source: Path = ROOT / TARGET_FILE) -> set[str]:
    """Read only method names; classification always comes from the manifest."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError) as error:
        raise ProfileError(f"retail inventory unreadable: {error}") from error
    classes = [node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == TARGET_CLASS]
    if len(classes) != 1:
        raise ProfileError("retail test class inventory drift")
    identifiers = [
        f"py:{TARGET_FILE}::{TARGET_CLASS}.{method.name}"
        for method in classes[0].body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and method.name.startswith("test_")
    ]
    if len(identifiers) != len(set(identifiers)):
        raise ProfileError("retail test inventory contains duplicate method IDs")
    return set(identifiers)


def _require_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise ProfileError(f"{context} keys mismatch")


def _ids(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProfileError(f"{context} IDs malformed")
    if len(value) != len(set(value)):
        raise ProfileError(f"{context} IDs duplicate")
    return value


def _validate_set(name: str, identifiers: list[str], frozen: dict[str, Any]) -> None:
    expected_count, expected_digest = FROZEN[name]
    declared = frozen.get(name)
    if not isinstance(declared, dict) or declared.get("count") != expected_count \
            or declared.get("sha256") != expected_digest:
        raise ProfileError(f"{name} frozen metadata mismatch")
    if len(identifiers) != expected_count or stable_digest(identifiers) != expected_digest:
        raise ProfileError(f"{name} classification/hash mismatch")


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(f"manifest unreadable: {error}") from error
    if not isinstance(value, dict):
        raise ProfileError("manifest is not an object")
    _require_keys(value, {"schema", "version", "target", "selection_authority",
                          "input_mapping_complete", "cases", "profiles", "frozen"},
                  "manifest")
    if value["schema"] != SCHEMA or value["version"] != 1:
        raise ProfileError("manifest schema/version mismatch")
    if value["selection_authority"] != "NONE" or value["input_mapping_complete"] is not False:
        raise ProfileError("manifest authority/mapping contract mismatch")
    target = value["target"]
    if not isinstance(target, dict):
        raise ProfileError("manifest target malformed")
    _require_keys(target, {"file", "module", "class"}, "manifest target")
    if target != {"file": TARGET_FILE, "module": TARGET_MODULE, "class": TARGET_CLASS}:
        raise ProfileError("manifest target mismatch")
    cases = value["cases"]
    if not isinstance(cases, list):
        raise ProfileError("manifest cases malformed")
    by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ProfileError("manifest case is not an object")
        _require_keys(case, {"id", "lane", "fixture", "labels", "resources", "timeout_seconds"},
                      "manifest case")
        identifier = case["id"]
        if not isinstance(identifier, str) or identifier in by_id:
            raise ProfileError("manifest case ID missing or duplicate")
        lane = case["lane"]
        expected = {
            "guard": ("guard", ["retail", "guard", "unit"], ["host", "filesystem"], 30),
            "integration": ("runner", ["retail", "integration", "mocked-simulator"],
                            ["host", "filesystem", "subprocess", "mocked-simulator"], 120),
        }.get(lane)
        if expected is None or (case["fixture"], case["labels"], case["resources"],
                                case["timeout_seconds"]) != expected:
            raise ProfileError("manifest case classification metadata mismatch")
        by_id[identifier] = case
    actual_inventory = inventory_ids()
    if set(by_id) != actual_inventory:
        missing = sorted(actual_inventory - set(by_id))
        extra = sorted(set(by_id) - actual_inventory)
        raise ProfileError(f"manifest inventory mismatch: missing={missing} extra={extra}")
    frozen = value["frozen"]
    if not isinstance(frozen, dict) or set(frozen) != set(FROZEN):
        raise ProfileError("manifest frozen sets malformed")
    complete = list(by_id)
    guard = [identifier for identifier, case in by_id.items() if case["lane"] == "guard"]
    integration = [identifier for identifier, case in by_id.items() if case["lane"] == "integration"]
    if set(guard) & set(integration) or set(guard) | set(integration) != set(complete):
        raise ProfileError("manifest lane partition mismatch")
    _validate_set("complete", complete, frozen)
    _validate_set("guard", guard, frozen)
    _validate_set("integration", integration, frozen)
    profiles = value["profiles"]
    if not isinstance(profiles, dict) or set(profiles) != set(PROFILE_NAMES):
        raise ProfileError("manifest profiles malformed")
    expected_profiles = {
        "complete": set(complete),
        "retail-integration": set(integration),
        "host-fast": set(guard) | SMOKE_IDS,
    }
    if not SMOKE_IDS <= set(integration):
        raise ProfileError("manifest smoke classification mismatch")
    _validate_set("smoke", sorted(SMOKE_IDS), frozen)
    for profile, expected_ids in expected_profiles.items():
        record = profiles[profile]
        if not isinstance(record, dict):
            raise ProfileError(f"{profile} profile malformed")
        _require_keys(record, {"case_ids", "stage_timeout_seconds"}, f"{profile} profile")
        selected = _ids(record["case_ids"], profile)
        if set(selected) != expected_ids:
            raise ProfileError(f"{profile} profile classification mismatch")
        # Only the future host-fast stage has the 120-second target.  The
        # complete and complete-integration paths retain today's 1200-second
        # stage budget until a later gate-wiring change proves otherwise.
        expected_timeout = 120 if profile == "host-fast" else 1200
        if record["stage_timeout_seconds"] != expected_timeout:
            raise ProfileError(f"{profile} stage timeout mismatch")
        frozen_name = {
            "complete": "complete",
            "host-fast": "host-fast",
            "retail-integration": "integration",
        }[profile]
        _validate_set(frozen_name, selected, frozen)
    return value


def select_case_ids(profile: str = "complete") -> list[str]:
    if profile not in PROFILE_NAMES:
        raise ProfileError(f"unknown retail profile: {profile}")
    return list(load_manifest()["profiles"][profile]["case_ids"])


def load_suite(profile: str = "complete") -> unittest.TestSuite:
    identifiers = select_case_ids(profile)
    loader = unittest.defaultTestLoader
    names = [f"{TARGET_MODULE}.{TARGET_CLASS}.{identifier.rsplit('.', 1)[1]}"
             for identifier in identifiers]
    # Names include their importable module segment, so passing ``module`` as
    # an explicit prefix would duplicate it and produce _FailedTest objects.
    suite = loader.loadTestsFromNames(names)
    discovered = [case.id().rsplit(".", 1)[-1] for case in _flatten(suite)]
    expected = [identifier.rsplit(".", 1)[1] for identifier in identifiers]
    if discovered != expected:
        raise ProfileError("unittest exact-name selection mismatch")
    return suite


def _flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def run_profile(profile: str = "complete", *, stream: TextIO | None = None,
                runner_factory: type[unittest.TextTestRunner] = unittest.TextTestRunner) -> int:
    suite = load_suite(profile)
    result = runner_factory(verbosity=2, stream=stream or sys.stderr).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="complete")
    parser.add_argument("--list", action="store_true", dest="list_only")
    args = parser.parse_args()
    try:
        manifest = load_manifest()
        if args.profile not in PROFILE_NAMES:
            raise ProfileError(f"unknown retail profile: {args.profile}")
        selected = manifest["profiles"][args.profile]["case_ids"]
        if args.list_only:
            print(json.dumps({"case_ids": selected, "count": len(selected),
                              "profile": args.profile,
                              "sha256": stable_digest(selected)}, sort_keys=True))
            return 0
        return run_profile(args.profile)
    except ProfileError as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
