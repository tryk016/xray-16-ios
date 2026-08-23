#!/usr/bin/env python3
"""Focused contracts for the Phase 0A catalog and Phase 0B observer runtime."""
from __future__ import annotations

import copy
import errno
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_feedback as feedback


AUDITED = {
    "central-python": "445de4b6c81dc03622bd664ebc56eeef2bf8751321b70c58240e52406220a1c0",
    "ci-contract": "0dff3ed04462d247c96ae814e597e1f52500530fc98752c82149f108ee4f23fb",
    "host-infra": "d9a3b20deb64f2f00d47175b6cd2b5db51e2bb8d95bb064f56637270e7f214a6",
    "legacy-python": "ab2e02c8f9d1779c2969adcd668d6eaecaddad4d262dc51c7d39e6e1eeb11adf",
    "cpp": "2e3b8feb7d6b7c143e322e391a09725be032b6d155afc4614f7f8e673ad2eae2",
    "shell": "fdfa6b29c893090567b3c9e498ce0ebd641d7a45f6336f20792c0f1da2ef1977",
    "shader-baseline": "e817fbc5e008b775761533a828bec1e7d8801e6c1736bffb852f42715684b0ab",
    "shader-variants": "2e51d4d72b14ba85b0a2302ace76dd1dc25ab3782ebc72a3b1e7d6028af7fad8",
    "shader-links": "5bf4594445e395f4e32a191e8d2aa26716878bf809c99013e2e1b2ee3cd1c212",
    "xctest": "2b62812c1d340f8eef901bb48cdd4d5078352f7e3ea6086c35eb116dbfd26b03",
}


class TestFeedbackPhase0ATests(unittest.TestCase):
    def _runtime_root(self, temporary: str, profile: str = "engine",
                      run_id: str = "fixture", nonce: str = "a" * 64) -> tuple[Path, dict[str, str]]:
        root = Path(temporary).resolve() / "feedback"
        self.assertTrue(feedback.runtime_initialize(root, profile, run_id, nonce))
        return root, {"directory": str(root), "profile": profile,
                      "run_id": run_id, "nonce": nonce}

    def _telemetry_env(self, context: dict[str, str]) -> dict[str, str]:
        """Production child environment: marker only; never four credentials."""
        environment = dict(os.environ)
        for name in tuple(environment):
            if name.startswith("OPENXRAY_TEST_FEEDBACK_"):
                environment.pop(name)
        return environment

    def _run_with_raw_sink(self, command: list[str], context: dict[str, str], *,
                           environment: dict[str, str] | None = None, **kwargs):
        """Run an untrusted selected command with only a non-secret raw sink."""
        raw_path = Path(context["directory"]) / f"raw-{time.time_ns()}.events"
        descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            child_env = self._telemetry_env(context) if environment is None else dict(environment)
            child_env.pop("XRAY_FEEDBACK_CONTEXT_FD", None)
            child_env["XRAY_FEEDBACK_RAW_EVENT_FD"] = str(descriptor)
            result = subprocess.run(command, cwd=feedback.ROOT, env=child_env,
                                    pass_fds=(descriptor,), **kwargs)
            return result, raw_path
        finally:
            os.close(descriptor)

    def _custom_runtime(self, target: Path, case_ids: list[str], *,
                        profile: str = "engine", run_id: str = "custom",
                        nonce: str = "b" * 64,
                        stage_id: str | None = None,
                        extra_entrypoints: list[str] | None = None) -> tuple[tempfile.TemporaryDirectory, Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name).resolve() / "feedback"
        root.mkdir(mode=0o700)
        for child in ("case-events", "stage-events", "error-events"):
            (root / child).mkdir(mode=0o700)
        relative = target.resolve().relative_to(feedback.ROOT).as_posix()
        entrypoint_id = f"python::{relative}"
        stage_id = stage_id or f"stage::{entrypoint_id}"
        inputs = [{"type": "path", "value": relative,
                   "sha256": feedback.sha256_file(target)}]
        input_hash = feedback._hash_bytes(
            (feedback.canonical(inputs) + "\n").encode("utf-8")
        )
        metadata = {
            "kind": "shader-checker" if stage_id.startswith("stage::shader::") else "python-unittest",
            "labels": ["python", "fixture"],
            "resources": ["host"], "timeout_seconds": 30,
            "timeout_policy": "report-only", "explicit_inputs": inputs,
            "input_sha256": input_hash, "input_mapping_complete": False,
        }
        cache_contracts = []
        if stage_id == "stage::shader::glsl-es":
            checker = feedback.ROOT / "misc/ios/shadercheck/glsl_es_check.py"
            # Cache outputs are build artifacts, never telemetry artifacts;
            # keeping this fixture sibling outside the runtime root exercises
            # the production closed-root invariant.
            cache_root = Path(temporary.name).resolve() / "cache"
            (cache_root / "compile").mkdir(parents=True)
            cache_contracts.append({
                "stage_id": stage_id, "checker_path": "misc/ios/shadercheck/glsl_es_check.py",
                "checker_sha256": feedback._sha256_regular_file(checker),
                "catalog_sha256": feedback.sha256_file(feedback.CATALOG_PATH),
                "catalog_schema": feedback.SCHEMA, "runtime_schema": feedback.RUNTIME_SCHEMA,
                "list_schema": "openxray.shader-case-list.v1", "covered_ids": case_ids,
                "covered_count": len(case_ids), "covered_sha256": feedback.digest(case_ids),
                "list_ids_sha256": feedback.digest(case_ids),
                "cache_root": str(cache_root),
                "output_template": feedback._CACHE_OUTPUT_TEMPLATE,
            })
        selection = {
            "schema": feedback.RUNTIME_SCHEMA, "run_id": run_id,
            "profile": profile, "selection_authority": "NONE", "cache_authority": "NONE",
            "catalog_sha256": feedback.sha256_file(feedback.CATALOG_PATH),
            "entrypoints": ([{"entrypoint_id": entrypoint_id, "kind": "python-unittest",
                              "selected": True, "reason": "authenticated fixture"}]
                            + [{"entrypoint_id": value, "kind": "python-unittest",
                                "selected": True, "reason": "foreign selected fixture"}
                               for value in (extra_entrypoints or [])]),
            "build_stage_classifications": [],
            "cache_contracts": cache_contracts,
            "cases": [{"test_id": identifier, "ordinal": ordinal,
                       "parent_stage_id": stage_id, **metadata}
                      for ordinal, identifier in enumerate(case_ids, 1)],
            "stages": [{"stage_id": stage_id, "ordinal": 1,
                        "parent_stage_id": None, **metadata}],
            "case_ids_sha256": feedback.digest(case_ids),
            "stage_ids_sha256": feedback.digest([stage_id]),
        }
        selection["selection_sha256"] = feedback._selection_payload_hash(selection)
        context = {"directory": str(root), "profile": profile,
                   "run_id": run_id, "nonce": nonce}
        selection = feedback._signed_record(context, "selection", selection)
        descriptor = feedback._open_private_directory(root)
        try:
            feedback._publish_json_at(descriptor, "selection.json", selection)
        finally:
            os.close(descriptor)
        return temporary, root, context

    def _run_observed_target(self, target: Path, context: dict[str, str],
                             *arguments: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        environment = self._telemetry_env(context)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return self._run_with_raw_sink([sys.executable, str(target), *arguments], context,
                                       environment=environment, text=True, capture_output=True, timeout=60)

    def _head_tool(self, directory: Path, name: str) -> Path:
        path = directory / name
        path.write_bytes(subprocess.run(
            ["git", "show", f"HEAD:misc/ios/shadercheck/{name}"],
            cwd=feedback.ROOT, check=True, capture_output=True,
        ).stdout)
        return path

    def _checker_result(self, command: list[str]) -> tuple[int, str, str]:
        result = subprocess.run(
            command, cwd=feedback.ROOT, text=True, capture_output=True, timeout=180,
        )
        return result.returncode, result.stdout, result.stderr

    def test_all_ten_audited_hashes_are_freshly_reproduced(self) -> None:
        groups = feedback.profile_ids()
        legacy = groups["central-python"] + groups["ci-contract"] + groups["host-infra"]
        actual = {
            name: feedback.digest(legacy if name == "legacy-python" else groups[name])
            for name in AUDITED
        }
        self.assertEqual(actual, AUDITED)
        report = feedback.validate(feedback.read_catalog())
        self.assertEqual(report["groups"]["retail-simulator"]["count"], 102)
        self.assertNotIn("source_id_sha256", json.dumps(feedback.read_catalog()))

    def test_feedback_runtime_inputs_are_bound_to_artifact_hash(self) -> None:
        gate_hash = (feedback.ROOT / "misc/ios/gate_hash.py").read_text(encoding="utf-8")
        for path in ("misc/ios/test_feedback.py", "misc/ios/test_feedback_catalog.json",
                     "misc/ios/test_feedback_unittest.py", "misc/ios/test_test_feedback.py",
                     "misc/ios/shader_cache.py", "misc/ios/test_shader_cache.py",
                     "misc/ios/retail_test_profiles.py", "misc/ios/retail_test_profiles.json",
                     "misc/ios/test_retail_test_profiles.py",
                     "misc/ios/test_retail_fixture_contract.py",
                     "misc/ios/retail_fixture_contract.json"):
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', gate_hash)

    def test_every_audited_digest_mutation_fails_closed(self) -> None:
        original = feedback.read_catalog()
        for name in AUDITED:
            with self.subTest(name=name):
                mutated = copy.deepcopy(original)
                mutated["frozen"]["groups"][name]["audited_sha256"] = "0" * 64
                with self.assertRaisesRegex(feedback.CatalogError, "audited inventory drift"):
                    feedback.validate(mutated)
        with self.assertRaisesRegex(feedback.CatalogError, "duplicate"):
            feedback.digest(["case", "case"])
        for invalid in ("line\nfeed", "carriage\rreturn", ""):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaisesRegex(feedback.CatalogError, "CR/LF|empty"):
                    feedback.digest([invalid])

    def test_catalog_entrypoints_profiles_and_metadata_are_exact(self) -> None:
        catalog = feedback.read_catalog()
        records = feedback.catalog_entrypoint_ids(catalog)
        self.assertEqual(records, feedback.expected_entrypoints())
        self.assertEqual(len(records), 56)
        self.assertEqual(sum(item.startswith("python::") for item in records), 35)
        self.assertEqual(sum(item.startswith("cpp:") for item in records), 9)
        by_id = {entry["entrypoint_id"]: entry for entry in catalog["entrypoints"]}
        all_five = ["engine", "shaders", "device", "full", "fast"]
        shader_four = ["shaders", "device", "full", "fast"]
        unconditional, shader_only = feedback.parse_build_check_python()
        for path in unconditional:
            self.assertEqual(by_id[f"python::{path}"]["selected_profiles"], all_five)
        for path in shader_only:
            self.assertEqual(by_id[f"python::{path}"]["selected_profiles"], shader_four)
        for identifier in feedback.cpp_entrypoint_ids() | {"shell::ui-log-oracle"}:
            self.assertEqual(by_id[identifier]["selected_profiles"], all_five)
        for identifier in ("shader::glsl-es", "shader::link"):
            self.assertEqual(by_id[identifier]["selected_profiles"], shader_four)
        for identifier, profiles in feedback.parse_gate_validation_entrypoints().items():
            self.assertEqual(by_id[identifier]["selected_profiles"], profiles)
        for identifier in (
            "python::misc/ios/test_ios_ci_contract.py",
            "python::misc/ios/test_cleanup_simulator_work.py",
            "python::misc/ios/ui_automation/test_prepare_scheme.py",
            "python::misc/ios/test_test_feedback.py",
            "utility::retail-test-profiles",
            "xctest::OpenXRayUITests",
        ):
            self.assertEqual(by_id[identifier]["selected_profiles"], [])
        profile_contract_ids = feedback.profile_ids()["host-feedback-tooling"]
        for profile in all_five:
            with self.subTest(runtime_profile=profile):
                self.assertTrue(set(profile_contract_ids) <= set(feedback.runtime_profile_ids(profile)))
        for entry in by_id.values():
            self.assertFalse(entry["input_mapping_complete"])
            self.assertTrue(entry["labels"])
            self.assertTrue(entry["resources"])
            self.assertTrue(entry["explicit_inputs"])
            self.assertEqual(entry["timeout_policy"], "report-only")
        for entrypoint in (
            "python::misc/ios/test_active_gate.py",
            "cpp:misc/ios/sector_fallback_policy_test",
            "shell::ui-log-oracle",
            "shader::glsl-es",
            "shader::link",
            "xctest::OpenXRayUITests",
        ):
            with self.subTest(entrypoint=entrypoint):
                mutated = copy.deepcopy(catalog)
                record = next(
                    item for item in mutated["entrypoints"]
                    if item["entrypoint_id"] == entrypoint
                )
                record["expected_case_count"] += 1
                with self.assertRaisesRegex(feedback.CatalogError, "count drift"):
                    feedback.validate(mutated)
        mutated = copy.deepcopy(catalog)
        mutated["profiles"].append("invented")
        with self.assertRaisesRegex(feedback.CatalogError, "profile vocabulary drift"):
            feedback.validate(mutated)
        mutated = copy.deepcopy(catalog)
        mutated["entrypoints"][0]["labels"].append(
            mutated["entrypoints"][0]["labels"][0]
        )
        with self.assertRaisesRegex(feedback.CatalogError, "unique non-empty"):
            feedback.validate(mutated)

    def test_phase0_tooling_count_is_source_derived_outside_legacy_baseline(self) -> None:
        catalog = feedback.read_catalog()
        by_id = {entry["entrypoint_id"]: entry for entry in catalog["entrypoints"]}
        actual = feedback.parse_python_methods(feedback.ROOT / "misc/ios/test_test_feedback.py")
        self.assertEqual(len(actual), 24)
        self.assertEqual(
            by_id["python::misc/ios/test_test_feedback.py"]["expected_case_count"],
            len(actual),
        )
        self.assertEqual(catalog["frozen"]["baseline"]["legacy_python_cases"], 800)
        self.assertNotIn("phase0-tooling", catalog["frozen"]["groups"])

    def test_static_ids_archive_semantics_and_no_import_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "test_side_effect.py"
            marker = root / "marker"
            source.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
                "class SideEffect:\n    def test_static(self): pass\n",
                encoding="utf-8",
            )
            original_root = feedback.ROOT
            try:
                feedback.ROOT = root
                identifiers = feedback.parse_python_methods(source)
            finally:
                feedback.ROOT = original_root
            self.assertEqual(identifiers, ["py:test_side_effect.py::SideEffect.test_static"])
            self.assertFalse(marker.exists())
            inherited = root / "test_inherited.py"
            inherited.write_text(
                "import unittest\n"
                "class Base(unittest.TestCase):\n    def test_base(self): pass\n"
                "class Child(Base):\n    def test_child(self): pass\n",
                encoding="utf-8",
            )
            original_root = feedback.ROOT
            try:
                feedback.ROOT = root
                inherited_ids = feedback.parse_python_methods(inherited)
                inherited.write_text(
                    inherited.read_text(encoding="utf-8").replace(
                        "class Child(Base):", "class Child(UnresolvedBase):"
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(feedback.CatalogError, "unresolved"):
                    feedback.parse_python_methods(inherited)
                inherited.write_text(
                    "class InheritedOnly(UnresolvedBase):\n    pass\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(feedback.CatalogError, "unresolved"):
                    feedback.parse_python_methods(inherited)
            finally:
                feedback.ROOT = original_root
            self.assertEqual(
                inherited_ids,
                [
                    "py:test_inherited.py::Base.test_base",
                    "py:test_inherited.py::Child.test_base",
                    "py:test_inherited.py::Child.test_child",
                ],
            )
        archive = feedback.parse_archive_generated_methods(
            feedback.ROOT / "misc/ios/test_archive_completed_artifacts.py"
        )
        self.assertEqual(len(archive), 131)
        self.assertIn(
            "py:misc/ios/test_archive_completed_artifacts.py::ArchivePolicyTests::VOL-01",
            archive,
        )
        self.assertIn(
            "py:misc/ios/test_archive_completed_artifacts.py::ArchivePolicyTests::GATE-04",
            archive,
        )
        archive_source = (
            feedback.ROOT / "misc/ios/test_archive_completed_artifacts.py"
        ).read_text(encoding="utf-8")
        mutations = (
            archive_source.replace(
                'f"VOL-{index:02d}"', 'f"VOL-{index:03d}"', 1
            ),
            archive_source.replace("test.__doc__ = case_id", "test.__doc__ = None", 1),
            archive_source.replace("def make_case(", "def broken_make_case(", 1),
            archive_source.replace("CASE_BODIES.items()", "CASE_BODIES.values()", 1),
            archive_source.replace("    setattr(ArchivePolicyTests,", "    broken_setattr(ArchivePolicyTests,", 1),
            archive_source.replace(
                "make_case(_case_id, _body)", "broken_make_case(_case_id, _body)", 1
            ),
        )
        for index, mutated_source in enumerate(mutations):
            with self.subTest(archive_mutation=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                target = root / "test_archive_completed_artifacts.py"
                target.write_text(mutated_source, encoding="utf-8")
                original_root = feedback.ROOT
                try:
                    feedback.ROOT = root
                    with self.assertRaises(feedback.CatalogError):
                        feedback.parse_archive_generated_methods(target)
                finally:
                    feedback.ROOT = original_root

    def test_list_output_is_canonical_deterministic_and_uses_exact_ids(self) -> None:
        first = subprocess.run(
            [sys.executable, "misc/ios/test_feedback.py", "json"],
            cwd=feedback.ROOT, check=True, text=True, capture_output=True,
        ).stdout
        second = subprocess.run(
            [sys.executable, "misc/ios/test_feedback.py", "json"],
            cwd=feedback.ROOT, check=True, text=True, capture_output=True,
        ).stdout
        self.assertEqual(first, second)
        self.assertEqual(first, feedback.canonical(json.loads(first)) + "\n")
        groups = feedback.profile_ids()
        self.assertIn("shader:baseline:accum_sun.ps", groups["shader-baseline"])
        self.assertIn("shader:low:combine_1_nomsaa.ps", groups["shader-variants"])
        self.assertIn("shader:ssao:optimized-half", groups["shader-variants"])
        self.assertIn("shader:ssr:off", groups["shader-variants"])
        self.assertIn("shader:ssr:full-q4", groups["shader-variants"])
        self.assertIn("shader:ssr:half-q4", groups["shader-variants"])
        self.assertIn("shader-link:stub_default|stub_default", groups["shader-links"])
        self.assertIn(
            "xctest:OpenXRayUITests/OpenXRayUITests/testMainMenuToOptionsThreeTimes",
            groups["xctest"],
        )
        shader_source = feedback.ROOT / "misc/ios/shadercheck/glsl_es_check.py"
        feedback.validate_shader_shared_specs(shader_source)
        with tempfile.TemporaryDirectory() as temporary:
            mutated = Path(temporary) / "glsl_es_check.py"
            mutated.write_text(
                shader_source.read_text(encoding="utf-8").replace(
                    "ssr_profiles = build_ssr_profiles()",
                    "ssr_profiles = []",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(feedback.CatalogError, "SSR list path"):
                feedback.validate_shader_shared_specs(mutated)

    def test_list_modes_do_not_invoke_glslang_and_cpp_observer_keeps_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            blocker = Path(temporary) / "glslangValidator"
            blocker.write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8")
            blocker.chmod(0o700)
            environment = {**os.environ, "PATH": f"{temporary}:{os.environ.get('PATH', '')}"}
            result = subprocess.run(
                [sys.executable, "misc/ios/shadercheck/glsl_es_check.py", "--list-json"],
                cwd=feedback.ROOT, text=True, capture_output=True, env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((len(json.loads(result.stdout)["baseline"]),
                              len(json.loads(result.stdout)["variants"])), (279, 17))
        build_source = (feedback.ROOT / "misc/ios/build_check.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("feedback_case", build_source)
        cpp_mutations = (
            build_source.replace(
                'feedback_case "cpp:misc/ios/ios_capture_state_v2_test@strict" "$capture_state_test" || fail',
                '"$wrong_capture_state_test" || fail',
                1,
            ),
            build_source.replace(
                'feedback_case "cpp:misc/ios/ios_capture_state_v2_test@strict" "$capture_state_test" || fail "capture-v2 serializer test failed"',
                'feedback_case "cpp:misc/ios/ios_capture_state_v2_test@strict" "$capture_state_test" || fail "capture-v2 serializer test failed"\n'
                'feedback_case "cpp:misc/ios/ios_capture_state_v2_test@strict" "$capture_state_test" || fail "capture-v2 serializer test failed"',
                1,
            ),
            build_source.replace(
                'UBSAN_OPTIONS=halt_on_error=1 "$capture_state_sanitized_test"',
                '"$capture_state_sanitized_test"',
                1,
            ),
        )
        for index, source in enumerate(cpp_mutations):
            with self.subTest(cpp_mutation=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                target = root / "misc/ios/build_check.sh"
                target.parent.mkdir(parents=True)
                target.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(
                        feedback.CatalogError, "execution mapping drift"):
                    feedback.parse_cpp_executions(root)

    def test_runtime_records_are_private_and_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = feedback.ROOT / "misc/ios/test_test_feedback.py"
            identifier = "py:misc/ios/test_test_feedback.py::Fixture.test_partial"
            runtime, root, context = self._custom_runtime(
                target, [identifier], run_id="fixture", nonce="a" * 64,
            )
            self.addCleanup(runtime.cleanup)
            saved = {key: os.environ.get(key) for key in (
                "OPENXRAY_TEST_FEEDBACK_DIR", "OPENXRAY_TEST_FEEDBACK_PROFILE",
                "OPENXRAY_TEST_FEEDBACK_RUN_ID", "OPENXRAY_TEST_FEEDBACK_NONCE")}
            os.environ.update({
                "OPENXRAY_TEST_FEEDBACK_DIR": str(root),
                "OPENXRAY_TEST_FEEDBACK_PROFILE": context["profile"],
                "OPENXRAY_TEST_FEEDBACK_RUN_ID": context["run_id"],
                "OPENXRAY_TEST_FEEDBACK_NONCE": context["nonce"],
            })
            try:
                self.assertTrue(feedback.runtime_event(identifier, "PASS", 1,
                                                       entrypoint_id="python::misc/ios/test_test_feedback.py", kind="case",
                                                       context=context))
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            event = next((root / "case-events").glob("*.json"))
            self.assertEqual(stat.S_IMODE(event.stat().st_mode), 0o600)
            payload = json.loads(event.read_text(encoding="utf-8"))
            self.assertEqual(payload["test_id"], identifier)
            self.assertEqual(payload["selection_authority"], "NONE")
            self.assertNotIn("nonce", payload)
            self.assertTrue(payload.get("auth_tag"))
            self.assertTrue(feedback.hmac.compare_digest(
                payload["auth_tag"], feedback._auth_tag(context, "case-event", payload)))
            for artifact in root.rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(context["nonce"].encode(), artifact.read_bytes(), artifact)

            # Ingest must retain the test's completion timestamp rather than
            # charging the parent-side delay to this case's elapsed duration.
            delayed_id = "py:misc/ios/test_test_feedback.py::Fixture.test_delayed_ingest"
            delayed_runtime, delayed_root, delayed_context = self._custom_runtime(
                target, [delayed_id], run_id="delayed-ingest", nonce="b" * 64,
            )
            self.addCleanup(delayed_runtime.cleanup)
            started = time.monotonic_ns()
            ended = started + 10_000_000
            raw = delayed_root / "raw-events.delayed"
            raw.write_text(feedback.canonical({
                "detail": None, "ended_monotonic_ns": ended, "result": "PASS",
                "schema": "openxray.test-feedback.raw.v1", "started_monotonic_ns": started,
                "test_id": delayed_id,
            }) + "\n", encoding="utf-8")
            raw.chmod(0o600)
            time.sleep(0.05)
            self.assertTrue(feedback.runtime_ingest(
                "python::misc/ios/test_test_feedback.py", raw, delayed_context))
            delayed_event = json.loads(next((delayed_root / "case-events").glob("*.json")).read_text())
            self.assertEqual(delayed_event["ended_monotonic_ns"], ended)
            self.assertEqual(delayed_event["elapsed_ms"], 10)
            self.assertGreaterEqual(delayed_event["recorded_monotonic_ns"], ended)
            raw.unlink()

            forged = dict(context)
            selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
            forged["nonce"] = selection["auth_tag"]
            self.assertFalse(feedback.runtime_event(
                identifier, "PASS", 2,
                entrypoint_id="python::misc/ios/test_test_feedback.py", context=forged))
            # One deliberately partial event never claims a clean full gate.
            finalized = feedback.runtime_finalize(root, "engine", context["run_id"], context["nonce"], 0)
            self.assertEqual(finalized["status"], "ERROR")

    def test_runtime_complete_rejects_failed_or_foreign_exact_coverage(self) -> None:
        target = feedback.ROOT / "misc/ios/test_test_feedback.py"
        relative = target.relative_to(feedback.ROOT).as_posix()
        identifier = f"py:{relative}::Fixture.test_one"
        for result, foreign, expected in (("PASS", False, "COMPLETE"),
                                          ("FAIL", False, "INCOMPLETE"),
                                          ("ERROR", False, "INCOMPLETE"),
                                          ("TIMEOUT", False, "INCOMPLETE"),
                                          ("SKIP", False, "INCOMPLETE"),
                                          ("PLATFORM_SKIP", False, "INCOMPLETE"),
                                          ("PASS", True, "INCOMPLETE")):
            with self.subTest(result=result, foreign=foreign):
                temporary, root, context = self._custom_runtime(
                    target, [identifier], run_id=f"complete-{result}-{foreign}",
                    nonce=("e" if foreign else "f") * 64,
                )
                self.addCleanup(temporary.cleanup)
                self.assertTrue(feedback.runtime_event(
                    identifier, result, 1, entrypoint_id=f"python::{relative}", context=context,
                    detail="fixture skip" if result in {"SKIP", "PLATFORM_SKIP"} else None))
                stage_id = f"stage::python::{relative}"
                self.assertTrue(feedback.runtime_event(stage_id, "PASS", 1,
                                                       entrypoint_id=f"python::{relative}", kind="stage", context=context))
                if foreign:
                    (root / "case-events" / "foreign.marker").write_text("foreign", encoding="utf-8")
                finalized = feedback.runtime_finalize(root, "engine", context["run_id"],
                                                       context["nonce"], 0)
                self.assertEqual(finalized["status"], expected)
                if expected == "COMPLETE":
                    run = json.loads((root / "run.json").read_text(encoding="utf-8"))
                    self.assertNotIn("nonce", run)
                    self.assertTrue(feedback.hmac.compare_digest(
                        run["auth_tag"], feedback._auth_tag(context, "run", run)))
                    for persistent in root.rglob("*"):
                        if persistent.is_file():
                            self.assertNotIn(context["nonce"].encode(), persistent.read_bytes(), persistent)

        temporary, root, context = self._custom_runtime(
            target, [identifier], run_id="foreign-origin", nonce="8" * 64)
        self.addCleanup(temporary.cleanup)
        self.assertFalse(feedback.runtime_event(
            identifier, "PASS", 1, entrypoint_id="python::misc/ios/test_active_gate.py", context=context))
        self.assertEqual(feedback.runtime_finalize(root, "engine", context["run_id"], context["nonce"], 0)["status"],
                         "INCOMPLETE")

        for artifact, expected in (("pending", "INCOMPLETE"),
                                   ("corrupt", "ERROR"),
                                   ("symlink", "ERROR")):
            with self.subTest(artifact=artifact):
                temporary, root, context = self._custom_runtime(
                    target, [identifier], run_id=f"artifact-{artifact}", nonce="9" * 64,
                )
                self.addCleanup(temporary.cleanup)
                self.assertTrue(feedback.runtime_event(identifier, "PASS", 1,
                                                       entrypoint_id=f"python::{relative}", context=context))
                stage_id = f"stage::python::{relative}"
                self.assertTrue(feedback.runtime_event(stage_id, "PASS", 1,
                                                       entrypoint_id=f"python::{relative}", kind="stage", context=context))
                if artifact == "pending":
                    (root / "case-events/.interrupted.pending").write_text("pending", encoding="utf-8")
                elif artifact == "corrupt":
                    bad = root / "case-events/corrupt.json"
                    bad.write_text("not-json\n", encoding="utf-8")
                    bad.chmod(0o600)
                else:
                    victim = root / "symlink-victim.json"
                    victim.write_text("victim", encoding="utf-8")
                    (root / "case-events/symlink.json").symlink_to(victim)
                finalized = feedback.runtime_finalize(root, "engine", context["run_id"],
                                                       context["nonce"], 0)
                self.assertEqual(finalized["status"], expected)

        # The root itself is a closed namespace: a failed raw/nested cleanup,
        # a pending transaction, arbitrary file, or pre-existing receipt never
        # becomes a superficially COMPLETE run.
        for artifact in ("raw-events.leftover", "nested-context.leftover", ".orphan.pending",
                         "foreign.bin", "run.json"):
            with self.subTest(root_artifact=artifact):
                temporary, root, context = self._custom_runtime(
                    target, [identifier], run_id=f"root-{artifact.replace('.', '-')}", nonce="1" * 64,
                )
                self.addCleanup(temporary.cleanup)
                self.assertTrue(feedback.runtime_event(
                    identifier, "PASS", 1, entrypoint_id=f"python::{relative}", context=context))
                stage_id = f"stage::python::{relative}"
                self.assertTrue(feedback.runtime_event(
                    stage_id, "PASS", 1, entrypoint_id=f"python::{relative}", kind="stage", context=context))
                leftover = root / artifact
                leftover.write_text("leftover\n", encoding="utf-8")
                leftover.chmod(0o600)
                self.assertEqual(feedback.runtime_finalize(
                    root, "engine", context["run_id"], context["nonce"], 0)["status"], "ERROR")

        # Altering canonical content without its nonce-derived HMAC is a hard
        # telemetry error, even when the superficial selection is complete.
        temporary, root, context = self._custom_runtime(
            target, [identifier], run_id="hmac-mutation", nonce="2" * 64)
        self.addCleanup(temporary.cleanup)
        self.assertTrue(feedback.runtime_event(
            identifier, "PASS", 1, entrypoint_id=f"python::{relative}", context=context))
        stage_id = f"stage::python::{relative}"
        self.assertTrue(feedback.runtime_event(
            stage_id, "PASS", 1, entrypoint_id=f"python::{relative}", kind="stage", context=context))
        event_path = next((root / "case-events").glob("*.json"))
        altered = json.loads(event_path.read_text(encoding="utf-8"))
        altered["result_detail"] = "tampered"
        event_path.write_text(feedback.canonical(altered) + "\n", encoding="utf-8")
        event_path.chmod(0o600)
        self.assertEqual(feedback.runtime_finalize(
            root, "engine", context["run_id"], context["nonce"], 0)["status"], "ERROR")

    def test_descriptor_bound_directories_and_records_reject_name_swaps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "runtime"
            root.mkdir(mode=0o700)
            events = root / "case-events"
            events.mkdir(mode=0o700)

            root_real_open = feedback.os.open
            swapped_root = base / "runtime-original"
            root_swapped = False

            def swap_root_open(path, flags, *args, **kwargs):
                nonlocal root_swapped
                descriptor = root_real_open(path, flags, *args, **kwargs)
                if path == root.name and kwargs.get("dir_fd") is not None and not root_swapped:
                    root_swapped = True
                    root.rename(swapped_root)
                    root.mkdir(mode=0o700)
                return descriptor

            with mock.patch.object(feedback.os, "open", side_effect=swap_root_open):
                with self.assertRaisesRegex(feedback.CatalogError, "identity changed"):
                    feedback._open_private_directory(root)

            # Restore one stable root and force the same race on a child lookup.
            root.rmdir()
            swapped_root.rename(root)
            child_original = root / "case-events-original"
            child_swapped = False

            def swap_child_open(path, flags, *args, **kwargs):
                nonlocal child_swapped
                descriptor = root_real_open(path, flags, *args, **kwargs)
                if path == "case-events" and kwargs.get("dir_fd") is not None and not child_swapped:
                    child_swapped = True
                    events.rename(child_original)
                    events.mkdir(mode=0o700)
                return descriptor

            root_fd = feedback._open_private_directory(root)
            try:
                with mock.patch.object(feedback.os, "open", side_effect=swap_child_open):
                    with self.assertRaisesRegex(feedback.CatalogError, "identity changed"):
                        feedback._open_child_directory(root_fd, "case-events")
            finally:
                os.close(root_fd)

            events.rmdir()
            child_original.rename(events)
            root_fd = feedback._open_private_directory(root)
            events_fd = feedback._open_child_directory(root_fd, "case-events")
            try:
                real_link = feedback.os.link
                displaced = events / "published-original.json"

                def swap_final_link(source, destination, *args, **kwargs):
                    result = real_link(source, destination, *args, **kwargs)
                    (events / destination).rename(displaced)
                    victim_fd = os.open(events / destination,
                                        os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.write(victim_fd, b"victim")
                    os.close(victim_fd)
                    return result

                with mock.patch.object(feedback.os, "link", side_effect=swap_final_link):
                    with self.assertRaisesRegex(feedback.CatalogError, "final record identity"):
                        feedback._publish_json_at(events_fd, "published.json", {"value": 1})
                self.assertEqual((events / "published.json").read_bytes(), b"victim")

                feedback._publish_json_at(events_fd, "read.json", {"value": "original"})
                real_read = feedback.os.read
                read_swapped = False

                def swap_final_read(descriptor, size):
                    nonlocal read_swapped
                    block = real_read(descriptor, size)
                    if block and not read_swapped:
                        read_swapped = True
                        (events / "read.json").rename(events / "read-original.json")
                        replacement = events / "read.json"
                        replacement.write_text(feedback.canonical({"value": "victim"}) + "\n",
                                               encoding="utf-8")
                        replacement.chmod(0o600)
                    return block

                with mock.patch.object(feedback.os, "read", side_effect=swap_final_read):
                    with self.assertRaisesRegex(feedback.CatalogError, "changed while reading"):
                        feedback._read_json_at(events_fd, "read.json")
                self.assertEqual(json.loads((events / "read.json").read_text()), {"value": "victim"})
            finally:
                os.close(events_fd)
                os.close(root_fd)

    def test_publish_failures_are_fail_open_private_and_never_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initialized = Path(temporary).resolve() / "descriptor-created"
            with mock.patch.object(Path, "mkdir", side_effect=AssertionError("path mkdir forbidden")), \
                    mock.patch.object(feedback, "runtime_selection", return_value={"fixture": True}):
                self.assertTrue(feedback.runtime_initialize(
                    initialized, "engine", "descriptor-init", "8" * 64))
            self.assertEqual(stat.S_IMODE(initialized.stat().st_mode), 0o700)
            for child in ("case-events", "stage-events", "error-events"):
                self.assertEqual(stat.S_IMODE((initialized / child).stat().st_mode), 0o700)
            selection_before = (initialized / "selection.json").read_bytes()
            self.assertFalse(feedback.runtime_initialize(
                initialized, "engine", "collision", "7" * 64))
            self.assertEqual((initialized / "selection.json").read_bytes(), selection_before)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "runtime"
            root.mkdir(mode=0o700)
            events = root / "case-events"
            events.mkdir(mode=0o700)
            root_fd = feedback._open_private_directory(root)
            events_fd = feedback._open_child_directory(root_fd, "case-events")
            try:
                real_write = feedback.os.write

                def short_write(descriptor, data):
                    return real_write(descriptor, data[:max(1, min(7, len(data)))])

                with mock.patch.object(feedback.os, "write", side_effect=short_write):
                    feedback._publish_json_at(events_fd, "short.json", {"value": "short-write"})
                self.assertEqual(feedback._read_json_at(events_fd, "short.json"),
                                 {"value": "short-write"})

                victim = events / "collision.json"
                victim.write_bytes(b"victim")
                victim.chmod(0o600)
                with self.assertRaises(FileExistsError):
                    feedback._publish_json_at(events_fd, "collision.json", {"value": "new"})
                self.assertEqual(victim.read_bytes(), b"victim")

                failures = (
                    ("zero.json", mock.patch.object(feedback.os, "write", return_value=0)),
                    ("enospc.json", mock.patch.object(
                        feedback.os, "write", side_effect=OSError(errno.ENOSPC, "fixture"))),
                )
                for name, failure_patch in failures:
                    with self.subTest(name=name), failure_patch:
                        with self.assertRaises(OSError):
                            feedback._publish_json_at(events_fd, name, {"value": name})
                        self.assertFalse((events / name).exists())

                real_open = feedback.os.open
                denied_once = False

                def deny_pending(path, flags, *args, **kwargs):
                    nonlocal denied_once
                    if flags & os.O_CREAT and not denied_once:
                        denied_once = True
                        raise PermissionError(errno.EACCES, "fixture")
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(feedback.os, "open", side_effect=deny_pending):
                    with self.assertRaises(PermissionError):
                        feedback._publish_json_at(events_fd, "eacces.json", {"value": 1})
                self.assertFalse((events / "eacces.json").exists())
                self.assertFalse(any(path.name.endswith(".pending") for path in events.iterdir()))

                corrupt = events / "corrupt.json"
                corrupt.write_text("{ \"not\": \"canonical\" }\n", encoding="utf-8")
                corrupt.chmod(0o600)
                with self.assertRaisesRegex(feedback.CatalogError, "canonical"):
                    feedback._read_json_at(events_fd, "corrupt.json")
                target = events / "target.json"
                target.write_text("target", encoding="utf-8")
                (events / "symlink.json").symlink_to(target)
                with self.assertRaises((OSError, feedback.CatalogError)):
                    feedback._read_json_at(events_fd, "symlink.json")
            finally:
                os.close(events_fd)
                os.close(root_fd)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            actual = base / "actual"
            actual.mkdir(mode=0o700)
            root_link = base / "runtime"
            root_link.symlink_to(actual, target_is_directory=True)
            with self.assertRaises((OSError, feedback.CatalogError)):
                feedback._open_private_directory(root_link)
            root = base / "private"
            root.mkdir(mode=0o700)
            outside = base / "outside"
            outside.mkdir(mode=0o700)
            (root / "case-events").symlink_to(outside, target_is_directory=True)
            root_fd = feedback._open_private_directory(root)
            try:
                with self.assertRaises((OSError, feedback.CatalogError)):
                    feedback._open_child_directory(root_fd, "case-events")
            finally:
                os.close(root_fd)

        target = feedback.ROOT / "misc/ios/test_test_feedback.py"
        identifier = "py:misc/ios/test_test_feedback.py::Fixture.test_write_failure"
        for failure in ("zero", "enospc", "eacces"):
            with self.subTest(runtime_failure=failure):
                temporary, root, context = self._custom_runtime(
                    target, [identifier], run_id=f"write-{failure}", nonce="6" * 64,
                )
                self.addCleanup(temporary.cleanup)
                real_write = feedback.os.write
                real_open = feedback.os.open
                triggered = False

                def fail_write_once(descriptor, data):
                    nonlocal triggered
                    if not triggered:
                        triggered = True
                        if failure == "zero":
                            return 0
                        raise OSError(errno.ENOSPC, "fixture")
                    return real_write(descriptor, data)

                def fail_open_once(path, flags, *args, **kwargs):
                    nonlocal triggered
                    if flags & os.O_CREAT and not triggered:
                        triggered = True
                        raise PermissionError(errno.EACCES, "fixture")
                    return real_open(path, flags, *args, **kwargs)

                patcher = (mock.patch.object(feedback.os, "open", side_effect=fail_open_once)
                           if failure == "eacces" else
                           mock.patch.object(feedback.os, "write", side_effect=fail_write_once))
                with patcher:
                    self.assertFalse(feedback.runtime_event(
                        identifier, "PASS", 1,
                        entrypoint_id="python::misc/ios/test_test_feedback.py", context=context))
                self.assertEqual(list((root / "case-events").glob("*.json")), [])
                finalized = feedback.runtime_finalize(root, "engine", context["run_id"],
                                                       context["nonce"], 0)
                self.assertEqual(finalized["status"], "INCOMPLETE")

    def test_cache_certificate_binds_output_and_rejects_direct_overlap(self) -> None:
        target = feedback.ROOT / "misc/ios/test_test_feedback.py"
        identifier = "shader:fixture"
        key = "1" * 64
        self.assertEqual(
            feedback._CACHE_ROOT,
            feedback.ROOT / "build/ios-engine-iphoneos/.ios_gate_cache",
        )
        self.assertEqual(feedback._CACHE_OUTPUT_TEMPLATE,
                         "{cache_root}/{stage}/{key}.out")

        invalid_calls = (
            ("key", "2" * 64, key, key, None),
            ("input", key, key, "3" * 64, None),
            ("path", key, key, key, "wrong.result"),
        )
        for name, supplied_key, before, after, wrong_name in invalid_calls:
            with self.subTest(certificate_rejected=name):
                temporary, root, context = self._custom_runtime(
                    target, [identifier], run_id=f"cache-invalid-{name}", nonce="4" * 64,
                    stage_id="stage::shader::glsl-es",
                )
                self.addCleanup(temporary.cleanup)
                output = root.parent / "cache/compile" / (wrong_name or f"{key}.out")
                output.write_text("cached shader evidence\n", encoding="utf-8")
                self.assertFalse(feedback.runtime_cache_certificate(
                    "compile", supplied_key, output, before, after,
                    entrypoint_id="shader::glsl-es", context=context))
                finalized = feedback.runtime_finalize(root, "engine", context["run_id"],
                                                       context["nonce"], 0)
                self.assertEqual(finalized["status"], "INCOMPLETE")

        event_mutations = {
            "catalog": lambda value: value.__setitem__("catalog_sha256", "0" * 64),
            "checker": lambda value: value.__setitem__("checker_sha256", "0" * 64),
            "list-tool": lambda value: value.__setitem__("list_tool_sha256", "0" * 64),
            "list-schema": lambda value: value.__setitem__("list_schema", "mutated.schema"),
            "catalog-schema": lambda value: value.__setitem__("catalog_schema", "mutated.schema"),
            "runtime-schema": lambda value: value.__setitem__("runtime_schema", "mutated.schema"),
            "coverage": lambda value: value.__setitem__("covered_ids", []),
            "coverage-count": lambda value: value.__setitem__("covered_count", 0),
            "coverage-digest": lambda value: value.__setitem__("covered_sha256", "0" * 64),
            "list-digest": lambda value: value.__setitem__("list_ids_sha256", "0" * 64),
        }
        variants = ["clean", "output-mutation", "output-replacement", "direct-overlap",
                    "catalog-file", "checker-file", *event_mutations]
        for variant in variants:
            with self.subTest(cache_mutation=variant):
                temporary, root, context = self._custom_runtime(
                    target, [identifier], run_id=f"cache-{variant}", nonce="5" * 64,
                    stage_id="stage::shader::glsl-es",
                )
                self.addCleanup(temporary.cleanup)
                output = root.parent / "cache/compile" / f"{key}.out"
                output.write_text("cached shader evidence\n", encoding="utf-8")
                self.assertTrue(feedback.runtime_cache_certificate(
                    "compile", key, output, key, key,
                    entrypoint_id="shader::glsl-es", context=context))
                if variant == "direct-overlap":
                    self.assertTrue(feedback.runtime_event(identifier, "PASS", 1,
                                                           entrypoint_id="shader::glsl-es", context=context))
                elif variant == "output-mutation":
                    output.write_text("mutated cached shader evidence\n", encoding="utf-8")
                elif variant == "output-replacement":
                    output.rename(output.with_suffix(".original"))
                    output.write_text("cached shader evidence\n", encoding="utf-8")
                elif variant in event_mutations:
                    event_path = next((root / "stage-events").glob("*.json"))
                    payload = json.loads(event_path.read_text(encoding="utf-8"))
                    event_mutations[variant](payload)
                    event_path.write_text(feedback.canonical(payload) + "\n", encoding="utf-8")
                    event_path.chmod(0o600)

                if variant == "catalog-file":
                    real_hash = feedback.sha256_file

                    def catalog_hash(path):
                        return "0" * 64 if Path(path) == feedback.CATALOG_PATH else real_hash(path)

                    finalizer = mock.patch.object(feedback, "sha256_file", side_effect=catalog_hash)
                elif variant == "checker-file":
                    real_checker_hash = feedback._sha256_regular_file

                    def checker_hash(path):
                        return ("0" * 64 if Path(path).name == "glsl_es_check.py"
                                else real_checker_hash(path))

                    finalizer = mock.patch.object(
                        feedback, "_sha256_regular_file", side_effect=checker_hash)
                else:
                    finalizer = mock.patch.object(feedback, "_RESULTS", feedback._RESULTS)
                with finalizer:
                    finalized = feedback.runtime_finalize(
                        root, "engine", context["run_id"], context["nonce"], 0)
                # This fixture intentionally has synthetic shader IDs.  The
                # production list producer now rejects even its apparent
                # "clean" certificate because it cannot prove those IDs.
                self.assertEqual(finalized["status"], "ERROR")

    def test_production_shell_scrubs_secrets_and_keeps_dispatcher_narrow(self) -> None:
        script_paths = (
            feedback.ROOT / "misc/ios/build_check.sh",
            feedback.ROOT / "misc/ios/build_fast_device.sh",
            feedback.ROOT / "misc/ios/ui_automation/test_log_oracles.sh",
        )
        sources = {path: path.read_text(encoding="utf-8") for path in script_paths}
        for path, source in sources.items():
            with self.subTest(script=path.name):
                self.assertIn("XRAY_FEEDBACK_CONTEXT_FD", source)
                self.assertNotIn("env OPENXRAY_TEST_FEEDBACK_DIR=", source)
                self.assertNotIn("OPENXRAY_TEST_FEEDBACK_NONCE=\"$feedback_nonce\"", source)
        for path in script_paths[:2]:
            source = sources[path]
            self.assertIn('feedback_context_marker="${XRAY_FEEDBACK_CONTEXT_FD:-}"', source)
            self.assertIn('[ -f "/dev/fd/$feedback_context_fd" ]', source)
            self.assertIn("python3 -S", source)
            self.assertIn("set +a +x", source)
            self.assertIn("unset BASH_ENV ENV PS4 BASH_XTRACEFD", source)
            self.assertIn("run_gate_logged.py", source)
            self.assertIn("not a supported telemetry trust boundary",
                          source.replace("\n# ", " "))
            self.assertNotIn("builtin compgen -A function", source)
            self.assertIn("unset feedback_context_fd feedback_dir feedback_nonce feedback_run_id feedback_profile", source)
        oracle_source = sources[feedback.ROOT / "misc/ios/ui_automation/test_log_oracles.sh"]
        self.assertIn('feedback_raw_fd="${XRAY_FEEDBACK_RAW_EVENT_FD:-}"', oracle_source)
        self.assertIn("--raw-emit", oracle_source)
        self.assertNotIn("feedback_nonce=", oracle_source)

        build_source = sources[feedback.ROOT / "misc/ios/build_check.sh"]
        self.assertIn("feedback_origin_for_event()", build_source)
        self.assertIn("ingest --entrypoint-id", build_source)
        self.assertIn("XRAY_FEEDBACK_RAW_EVENT_FD", build_source)
        self.assertIn("--raw-fd", build_source)
        self.assertIn("feedback_raw_cleanup_path", build_source)
        self.assertIn("trap cleanup EXIT", build_source)
        selected_raw = build_source[
            build_source.index("feedback_selected_raw() {"):
            build_source.index("feedback_selected_python() {")
        ]
        self.assertEqual(selected_raw.count('feedback_selected_command "$@"'), 5)
        raw_open = selected_raw.index('exec 8<>"$raw_path"')
        raw_unlink = selected_raw.index('if ! rm -f "$raw_path"', raw_open)
        raw_child = selected_raw.index('XRAY_FEEDBACK_RAW_EVENT_FD="$raw_fd"', raw_unlink)
        self.assertLess(raw_open, raw_unlink)
        self.assertLess(raw_unlink, raw_child)
        self.assertIn('if ! { exec 8<>"$raw_path"; } 2>/dev/null; then', selected_raw)
        self.assertIn('{ exec 8>&-; } 2>/dev/null || true', selected_raw)
        self.assertNotIn('exec 8>&- 2>/dev/null', selected_raw)
        self.assertNotIn('|| { "$@";', selected_raw)
        self.assertNotIn('\n        "$@"\n', selected_raw)

        case_dispatch = build_source[
            build_source.index("feedback_case() {"):
            build_source.index("feedback_stage() {")
        ]
        self.assertIn('feedback_selected_command "$@"', case_dispatch)
        self.assertNotIn('\n        "$@"\n', case_dispatch)
        stage_dispatch = build_source[
            build_source.index("feedback_stage() {"):
            build_source.index("feedback_mark_stage() {")
        ]
        self.assertIn('feedback_stage_command "$@"', stage_dispatch)
        self.assertNotIn('\n        "$@"\n', stage_dispatch)

        fast_source = sources[feedback.ROOT / "misc/ios/build_fast_device.sh"]
        self.assertIn('XRAY_FEEDBACK_CONTEXT_FD="$nested_fd" /bin/bash', fast_source)
        self.assertIn("feedback_nested_bash()", fast_source)
        self.assertIn("BASH_FUNC_*%%) feedback_nested_env+=(-u", fast_source)
        self.assertNotIn('build_check.sh --shaders 9<&9', fast_source)
        nested = fast_source[
            fast_source.index("feedback_run_nested_shaders() {"):
            fast_source.index("archive_gate_detail_log() {")
        ]
        nested_open = nested.index('exec 9<"$context_path"')
        nested_unlink = nested.index('rm -f "$context_path"', nested_open)
        nested_launch = nested.index('feedback_nested_bash XRAY_FEEDBACK_CONTEXT_FD="$nested_fd" /bin/bash')
        self.assertLess(nested_open, nested_unlink)
        self.assertLess(nested_unlink, nested_launch)
        self.assertIn('{ exec 9<&-; } 2>/dev/null || true', nested)
        self.assertNotIn('exec 9<&- 2>/dev/null', nested)

        dispatcher = build_source[
            build_source.index("feedback_stage_command() {"):
            build_source.index("feedback_now() {")
        ]
        unconditional, shader_only = feedback.parse_build_check_python()
        expected_targets = set(unconditional + shader_only + [
            "misc/ios/shadercheck/glsl_es_check.py",
            "misc/ios/shadercheck/link_check.py",
            "misc/ios/ui_automation/test_log_oracles.sh",
        ])
        self.assertIn("misc/ios/test_run_gate_logged.py", expected_targets)

        def dispatcher_targets(value: str) -> set[str]:
            return set(re.findall(
                r'misc/ios/(?:(?:ui_automation/)?test_[A-Za-z0-9_]+\.py|shadercheck/(?:glsl_es_check|link_check)\.py|'
                r'ui_automation/test_log_oracles\.sh)', value))

        self.assertEqual(dispatcher_targets(dispatcher), expected_targets)
        for target in sorted(expected_targets):
            with self.subTest(dispatcher_target=target):
                mutated = dispatcher.replace(target, "misc/ios/not-allowlisted", 1)
                self.assertNotEqual(dispatcher_targets(mutated), expected_targets)

    def test_bootstrap_authenticates_exact_target_and_scrubs_every_process(self) -> None:
        target = (feedback.ROOT / "misc/ios" /
                  f"_auth_fixture_{os.getpid()}_{time.time_ns()}.py")
        relative = target.relative_to(feedback.ROOT).as_posix()
        identifier = f"py:{relative}::Authenticated.test_observed"
        target.write_text(
            "import sys; sys.path.insert(0, 'misc/ios')\n"
            "if __import__('os').environ.get('XRAY_FEEDBACK_RAW_EVENT_FD'):\n"
            " try:\n"
            f"  from test_feedback_unittest import install_from_environment\n  install_from_environment({f'python::{relative}'!r})\n"
            " except BaseException:\n  pass\n"
            "import json, os, subprocess, sys, unittest\n"
            "PREFIX='OPENXRAY_TEST_FEEDBACK_'\n"
            "child_code=\"import json,os,subprocess,sys; "
            "g=subprocess.run([sys.executable,'-c','import json,os; print(json.dumps(sorted(k for k in os.environ if k.startswith(\\\"OPENXRAY_TEST_FEEDBACK_\\\"))))'],text=True,capture_output=True,check=True); "
            "print(json.dumps({'env':sorted(k for k in os.environ if k.startswith('OPENXRAY_TEST_FEEDBACK_')),'grandchild':json.loads(g.stdout)}))\"\n"
            "child=subprocess.run([sys.executable,'-c',child_code],text=True,capture_output=True,check=True)\n"
            "print('SCRUB='+json.dumps({'env':sorted(k for k in os.environ if k.startswith(PREFIX)),"
            "'path':any('test_feedback_bootstrap' in p for p in sys.path),"
            "'child':json.loads(child.stdout)}),flush=True)\n"
            "class Authenticated(unittest.TestCase):\n"
            " def test_observed(self): self.assertTrue(True)\n"
            "if __name__=='__main__': unittest.main(verbosity=0)\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        foreign_entrypoint = "python::misc/ios/test_active_gate.py"
        temporary, root, context = self._custom_runtime(
            target, [identifier], extra_entrypoints=[foreign_entrypoint])
        self.addCleanup(temporary.cleanup)

        # An arbitrary selected process gets only the raw sink.  It cannot see
        # a telemetry context and it cannot write authenticated evidence.
        non_target, non_target_raw = self._run_with_raw_sink(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'misc/ios'); from test_feedback_unittest import install_from_environment; install_from_environment('python::misc/ios/not_selected.py'); import json,os; print(json.dumps({'env':sorted(k for k in os.environ if k.startswith('OPENXRAY_TEST_FEEDBACK_')),'path':False}))"],
            context, text=True, capture_output=True, check=True,
        )
        self.assertEqual(json.loads(non_target.stdout), {"env": [], "path": False})
        self.assertEqual(non_target_raw.read_bytes(), b"")
        self.assertEqual(list((root / "case-events").glob("*.json")), [])

        # A hostile inherited pipe with a live writer is ignored without a
        # blocking read.  Direct tests therefore cannot be stopped by a stale
        # marker accidentally inherited from another process.
        reader, writer = os.pipe()
        try:
            hostile_env = self._telemetry_env(context)
            hostile_env["XRAY_FEEDBACK_RAW_EVENT_FD"] = str(reader)
            hostile = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0,'misc/ios'); from test_feedback_unittest import install_from_environment; install_from_environment('python::misc/ios/not_selected.py'); print('ordinary')"],
                cwd=feedback.ROOT, env=hostile_env, pass_fds=(reader,), text=True,
                capture_output=True, timeout=2, check=True)
            self.assertEqual(hostile.stdout, "ordinary\n")
        finally:
            os.close(reader)
            os.close(writer)

        # The explicit helper never owns interpreter startup, so a user's
        # existing sitecustomize remains chained and observable.
        with tempfile.TemporaryDirectory() as site_temporary:
            site_directory = Path(site_temporary)
            seen_by_site = site_directory / "seen.json"
            (site_directory / "sitecustomize.py").write_text(
                "import json, os, stat\nfrom pathlib import Path\n"
                "marker=os.environ.get('XRAY_FEEDBACK_RAW_EVENT_FD',''); info={'secrets':sorted(k for k in os.environ if k.startswith('OPENXRAY_TEST_FEEDBACK_')),'marker':bool(marker)}\n"
                "if marker:\n details=os.fstat(int(marker)); info.update({'regular':stat.S_ISREG(details.st_mode),'mode':stat.S_IMODE(details.st_mode),'size':details.st_size})\n"
                "Path(os.environ['OPENXRAY_SITECHAIN_RECORD']).write_text(json.dumps(info,sort_keys=True))\n"
                "os.environ['OPENXRAY_SITECHAIN_FIXTURE'] = 'preserved'\n",
                encoding="utf-8",
            )
            chained_environment = self._telemetry_env(context)
            chained_environment["PYTHONPATH"] = str(site_directory)
            chained_environment["OPENXRAY_SITECHAIN_RECORD"] = str(seen_by_site)
            chained, chained_raw = self._run_with_raw_sink(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, 'misc/ios'); from test_feedback_unittest import install_from_environment; install_from_environment('python::misc/ios/not_selected.py'); import os; print(os.environ.get('OPENXRAY_SITECHAIN_FIXTURE'))"],
                context, environment=chained_environment, text=True, capture_output=True, check=True,
            )
            self.assertEqual(chained.stdout.strip(), "preserved")
            self.assertEqual(json.loads(seen_by_site.read_text(encoding="utf-8")),
                             {"marker": True, "mode": 0o600, "regular": True,
                              "secrets": [], "size": 0})
            self.assertEqual(chained_raw.read_bytes(), b"")

        forged = dict(context)
        forged["nonce"] = json.loads((root / "selection.json").read_text(encoding="utf-8"))["auth_tag"]
        forged_result, forged_raw = self._run_observed_target(target, forged)
        self.assertEqual(forged_result.returncode, 0, forged_result.stderr)
        self.assertFalse(feedback.runtime_ingest(f"python::{relative}", forged_raw, forged))
        self.assertEqual(list((root / "case-events").glob("*.json")), [])

        self.assertIsNone(feedback.authenticate_target(
            context, f"python::{relative}", [str(target), "--unexpected"], feedback.ROOT))
        self.assertIsNone(feedback.authenticate_target(
            context, f"python::{relative}", [str(target)], target.parent))
        wrong_profile = dict(context)
        wrong_profile["profile"] = "shaders"
        self.assertIsNone(feedback.authenticate_target(
            wrong_profile, f"python::{relative}", [str(target)], feedback.ROOT))

        authenticated, raw_path = self._run_observed_target(target, context)
        self.assertEqual(authenticated.returncode, 0, authenticated.stderr)
        scrub_line = next(line for line in authenticated.stdout.splitlines()
                          if line.startswith("SCRUB="))
        scrub = json.loads(scrub_line.removeprefix("SCRUB="))
        self.assertEqual(scrub, {"env": [], "path": False,
                                 "child": {"env": [], "grandchild": []}})
        self.assertTrue(feedback.runtime_ingest(f"python::{relative}", raw_path, context))
        events = [json.loads(path.read_text(encoding="utf-8"))
                  for path in (root / "case-events").glob("*.json")]
        self.assertEqual([event["test_id"] for event in events], [identifier])

        # A startup hook or child can append raw bytes but cannot claim another
        # selected entrypoint: the trusted parent supplies the sole origin.
        forged_raw = root / "forged-other-origin.events"
        forged_payload = {"detail": None, "ended_monotonic_ns": 2, "result": "PASS",
                          "schema": "openxray.test-feedback.raw.v1", "started_monotonic_ns": 1,
                          "test_id": "py:misc/ios/test_active_gate.py::ActiveGateTests.test_placeholder"}
        forged_raw.write_text(feedback.canonical(forged_payload) + "\n", encoding="utf-8")
        forged_raw.chmod(0o600)
        self.assertFalse(feedback.runtime_ingest(f"python::{relative}", forged_raw, context))

        corrupt_raw = root.parent / "corrupt-raw.events"
        corrupt_raw.write_text("not-json\n", encoding="utf-8")
        corrupt_raw.chmod(0o600)
        self.assertFalse(feedback.runtime_ingest(f"python::{relative}", corrupt_raw, context))

    def test_unittest_outcomes_subtests_semantic_ids_and_custom_runner(self) -> None:
        target = (feedback.ROOT / "misc/ios" /
                  f"_outcome_fixture_{os.getpid()}_{time.time_ns()}.py")
        relative = target.relative_to(feedback.ROOT).as_posix()
        methods = ["error", "expected", "failure", "semantic", "skip",
                   "subtests", "success", "unexpected"]
        identifiers = [
            (f"py:{relative}::ArchivePolicyTests::VOL-01" if method == "semantic"
             else f"py:{relative}::Outcomes.test_{method}")
            for method in methods
        ]
        target.write_text(
            "import sys; sys.path.insert(0, 'misc/ios')\n"
            f"from test_feedback_unittest import install_from_environment\ninstall_from_environment({f'python::{relative}'!r})\n"
            "import sys, unittest\n"
            "class Outcomes(unittest.TestCase):\n"
            " def test_success(self): pass\n"
            " def test_failure(self): self.fail('failure')\n"
            " def test_error(self): raise RuntimeError('error')\n"
            " @unittest.skip('fixture skip')\n"
            " def test_skip(self): pass\n"
            " @unittest.expectedFailure\n"
            " def test_expected(self): self.fail('expected')\n"
            " @unittest.expectedFailure\n"
            " def test_unexpected(self): pass\n"
            " def test_subtests(self):\n"
            "  for value in (0,1):\n"
            "   with self.subTest(value=value): self.assertEqual(value,0)\n"
            "class ArchivePolicyTests(unittest.TestCase):\n"
            " def test_semantic(self): pass\n"
            "ArchivePolicyTests.test_semantic.__doc__='VOL-01'\n"
            "if __name__=='__main__':\n"
            " suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Outcomes),unittest.defaultTestLoader.loadTestsFromTestCase(ArchivePolicyTests)])\n"
            " result=unittest.TextTestRunner(verbosity=0).run(suite)\n"
            " sys.exit(0 if result.wasSuccessful() else 7)\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        # A fixture uses ordinary class.method; production semantic mapping is
        # separately authenticated below against the real archive module path.
        identifiers[methods.index("semantic")] = f"py:{relative}::ArchivePolicyTests.test_semantic"
        temporary, root, context = self._custom_runtime(target, identifiers)
        self.addCleanup(temporary.cleanup)
        result, raw_path = self._run_observed_target(target, context)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertTrue(feedback.runtime_ingest(f"python::{relative}", raw_path, context))
        events = {value["test_id"]: value for value in (
            json.loads(path.read_text(encoding="utf-8"))
            for path in (root / "case-events").glob("*.json")
        )}
        self.assertEqual(set(events), set(identifiers))
        expected_results = {
            "error": "ERROR", "expected": "PASS", "failure": "FAIL",
            "semantic": "PASS", "skip": "SKIP", "subtests": "FAIL",
            "success": "PASS", "unexpected": "ERROR",
        }
        for method, expected in expected_results.items():
            self.assertEqual(events[identifiers[methods.index(method)]]["result"], expected)
        self.assertEqual(events[identifiers[methods.index("expected")]]["result_detail"],
                         "expected-failure")
        self.assertEqual(events[identifiers[methods.index("unexpected")]]["result_detail"],
                         "unexpected-success")
        self.assertFalse(any("subTest" in identifier or "value=" in identifier
                             for identifier in events))

        # A wrapper is a distinct entrypoint.  It may not emit the archive
        # case's global ID merely because that ID is selected elsewhere.
        archive_target = feedback.ROOT / "misc/ios/test_archive_completed_artifacts.py"
        archive_id = "py:misc/ios/test_archive_completed_artifacts.py::ArchivePolicyTests::VOL-01"
        wrapper = (feedback.ROOT / "misc/ios" /
                   f"_archive_fixture_{os.getpid()}_{time.time_ns()}.py")
        wrapper.write_text(
            "import sys; sys.path.insert(0, 'misc/ios')\n"
            "import importlib.util, pathlib, sys, unittest\n"
            "path=pathlib.Path('misc/ios/test_archive_completed_artifacts.py').resolve()\n"
            "sys.path.insert(0,str(path.parent))\n"
            "spec=importlib.util.spec_from_file_location('_archive_fixture_module',path)\n"
            "module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)\n"
            "case=module.ArchivePolicyTests('test_VOL_01')\n"
            "result=unittest.TextTestRunner(verbosity=0).run(unittest.TestSuite([case]))\n"
            "sys.exit(0 if result.wasSuccessful() else 1)\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda: wrapper.unlink(missing_ok=True))
        archive_temp, archive_root, archive_context = self._custom_runtime(
            wrapper, [archive_id], run_id="archive-custom", nonce="d" * 64
        )
        self.addCleanup(archive_temp.cleanup)
        archive_result, archive_raw = self._run_observed_target(wrapper, archive_context)
        self.assertEqual(archive_result.returncode, 0, archive_result.stderr)
        self.assertFalse(feedback.runtime_ingest(
            "python::misc/ios/test_archive_completed_artifacts.py", archive_raw, archive_context))
        archive_events = [json.loads(path.read_text(encoding="utf-8"))
                          for path in (archive_root / "case-events").glob("*.json")]
        self.assertEqual(archive_events, [])

    def test_optional_helper_failure_has_off_on_authoritative_parity(self) -> None:
        """A broken observer import must not alter an ordinary unittest process."""
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "helper_failure.py"
            target.write_text(
                "import os\n"
                "if os.environ.get('XRAY_FEEDBACK_RAW_EVENT_FD'):\n"
                " try:\n  import definitely_missing_phase0b_helper\n"
                " except BaseException:\n  pass\n"
                "print('authoritative-output')\n",
                encoding="utf-8",
            )
            runtime, _root, context = self._custom_runtime(
                feedback.ROOT / "misc/ios/test_test_feedback.py", [], run_id="helper-failure", nonce="7" * 64)
            self.addCleanup(runtime.cleanup)
            off = subprocess.run([sys.executable, str(target)], cwd=feedback.ROOT,
                                 text=True, capture_output=True, check=False)
            on, _raw = self._run_with_raw_sink([sys.executable, str(target)], context,
                                                text=True, capture_output=True, check=False)
            self.assertEqual((on.returncode, on.stdout, on.stderr),
                             (off.returncode, off.stdout, off.stderr))

    def test_cache_replay_executes_current_list_producer_and_rejects_mutation(self) -> None:
        """Replay proof executes a real temporary producer, not certificate fields."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "misc/ios/shadercheck/list_producer.py"
            checker.parent.mkdir(parents=True)
            ids = ["shader:baseline:fixture.ps", "shader:ssao:fixture"]
            contract = {"stage_id": "stage::shader::glsl-es", "checker_path": str(checker.relative_to(root)),
                        "list_schema": "openxray.shader-case-list.v1", "covered_ids": ids,
                        "covered_count": len(ids), "covered_sha256": feedback.digest(ids),
                        "list_ids_sha256": feedback.digest(ids)}
            checker.write_text(
                "import json\nprint(json.dumps({'schema':'openxray.shader-case-list.v1',"
                "'baseline':['shader:baseline:fixture.ps'],'variants':['shader:ssao:fixture']}))\n",
                encoding="utf-8")
            original_root = feedback.ROOT
            try:
                feedback.ROOT = root
                feedback._replay_current_cache_list(contract)
                checker.write_text("print('not-json')\n", encoding="utf-8")
                with self.assertRaisesRegex(feedback.CatalogError, "malformed"):
                    feedback._replay_current_cache_list(contract)
                checker.write_text(
                    "import json\nprint(json.dumps({'schema':'openxray.shader-case-list.v1',"
                    "'baseline':['shader:baseline:changed.ps'],'variants':['shader:ssao:fixture']}))\n",
                    encoding="utf-8")
                with self.assertRaisesRegex(feedback.CatalogError, "coverage changed"):
                    feedback._replay_current_cache_list(contract)
            finally:
                feedback.ROOT = original_root

    def test_stage_order_is_derived_from_executing_wrapper_markers(self) -> None:
        """All five profiles reject wrapper removal/reorder/duplicate drift."""
        catalog = feedback.read_catalog()
        baseline = {profile: feedback._stage_order(profile, catalog)
                    for profile in ("engine", "shaders", "device", "full", "fast")}
        self.assertEqual(
            baseline["fast"],
            ["build-stage::artifact-input-hash-before", *baseline["shaders"],
             "build-stage::cmake-configure",
             "stage::validation::python::openal-configured",
             "build-stage::release-engine-build", "build-stage::resource-sync",
             "build-stage::artifact-platform-debug-verification",
             "stage::validation::python::openal-artifact",
             "build-stage::artifact-input-hash-after", "build-stage::stamp-publication"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "misc/ios"
            scripts.mkdir(parents=True)
            source = (feedback.ROOT / "misc/ios/build_check.sh").read_text(encoding="utf-8")
            fast = (feedback.ROOT / "misc/ios/build_fast_device.sh").read_text(encoding="utf-8")
            (scripts / "build_fast_device.sh").write_text(fast, encoding="utf-8")
            removed = source.replace('feedback_stage "stage::python::misc/ios/test_active_gate.py"',
                                     'feedback_stage "stage::python::misc/ios/test_active_gate_REMOVED.py"', 1)
            (scripts / "build_check.sh").write_text(removed, encoding="utf-8")
            for profile, expected in baseline.items():
                with self.subTest(profile=profile, mutation="remove-build-check"):
                    self.assertNotEqual(feedback._stage_order(profile, catalog, root), expected)
            active = "stage::python::misc/ios/test_active_gate.py"
            runner = "stage::python::misc/ios/test_run_gate_logged.py"
            reordered = source.replace(active, "STAGE_SWAP", 1).replace(runner, active, 1).replace("STAGE_SWAP", runner, 1)
            (scripts / "build_check.sh").write_text(reordered, encoding="utf-8")
            for profile, expected in baseline.items():
                with self.subTest(profile=profile, mutation="reorder-build-check"):
                    self.assertNotEqual(feedback._stage_order(profile, catalog, root), expected)
            retail_profiles = "stage::python::misc/ios/test_retail_test_profiles.py"
            removed_profiles = source.replace(
                f'feedback_stage "{retail_profiles}"',
                f'feedback_stage "{retail_profiles}-REMOVED"', 1,
            )
            (scripts / "build_check.sh").write_text(removed_profiles, encoding="utf-8")
            for profile, expected in baseline.items():
                with self.subTest(profile=profile, mutation="remove-retail-profile-stage"):
                    self.assertNotEqual(feedback._stage_order(profile, catalog, root), expected)
            reordered_profiles = source.replace(retail_profiles, "RETAIL_PROFILE_SWAP", 1)
            reordered_profiles = reordered_profiles.replace(active, retail_profiles, 1)
            reordered_profiles = reordered_profiles.replace("RETAIL_PROFILE_SWAP", active, 1)
            (scripts / "build_check.sh").write_text(reordered_profiles, encoding="utf-8")
            for profile, expected in baseline.items():
                with self.subTest(profile=profile, mutation="reorder-retail-profile-stage"):
                    self.assertNotEqual(feedback._stage_order(profile, catalog, root), expected)
            duplicated_profiles = source.replace(
                f'feedback_stage "{runner}"',
                f'feedback_stage "{retail_profiles}"', 1,
            )
            (scripts / "build_check.sh").write_text(duplicated_profiles, encoding="utf-8")
            for profile in baseline:
                with self.subTest(profile=profile, mutation="duplicate-retail-profile-stage"):
                    with self.assertRaisesRegex(feedback.CatalogError, "duplicate marker"):
                        feedback._stage_order(profile, catalog, root)
            duplicate = source.replace(
                'feedback_stage "stage::python::misc/ios/test_run_gate_logged.py"',
                'feedback_stage "stage::python::misc/ios/test_active_gate.py"', 1)
            (scripts / "build_check.sh").write_text(duplicate, encoding="utf-8")
            for profile in baseline:
                with self.subTest(profile=profile, mutation="duplicate-build-check"):
                    with self.assertRaisesRegex(feedback.CatalogError, "duplicate marker"):
                        feedback._stage_order(profile, catalog, root)
            profile_drift = source.replace('if [ "$run_shaders" = 1 ]; then',
                                           'if [ "$run_shader_profile" = 1 ]; then', 1)
            (scripts / "build_check.sh").write_text(profile_drift, encoding="utf-8")
            for profile in baseline:
                with self.subTest(profile=profile, mutation="profile-build-check"):
                    with self.assertRaisesRegex(feedback.CatalogError, "profile guard drift"):
                        feedback._stage_order(profile, catalog, root)

            (scripts / "build_check.sh").write_text(source, encoding="utf-8")
            fast_removed = fast.replace(
                'feedback_stage "build-stage::resource-sync"',
                'feedback_stage "build-stage::resource-sync-REMOVED"', 1)
            (scripts / "build_fast_device.sh").write_text(fast_removed, encoding="utf-8")
            self.assertNotEqual(feedback._stage_order("fast", catalog, root), baseline["fast"])

            fast_reordered = fast.replace('"build-stage::artifact-input-hash-before"', '"FAST_SWAP"', 1)
            fast_reordered = fast_reordered.replace(
                'feedback_stage "build-stage::cmake-configure" stabilize_cmake_config',
                'feedback_stage "build-stage::artifact-input-hash-before" stabilize_cmake_config', 1)
            fast_reordered = fast_reordered.replace('"FAST_SWAP"', '"build-stage::cmake-configure"', 1)
            (scripts / "build_fast_device.sh").write_text(fast_reordered, encoding="utf-8")
            self.assertNotEqual(feedback._stage_order("fast", catalog, root), baseline["fast"])

            fast_duplicate = fast.replace(
                'feedback_stage "build-stage::cmake-configure" stabilize_cmake_config',
                'feedback_stage "build-stage::artifact-input-hash-before" stabilize_cmake_config', 1)
            (scripts / "build_fast_device.sh").write_text(fast_duplicate, encoding="utf-8")
            with self.assertRaisesRegex(feedback.CatalogError, "overlap"):
                feedback._stage_order("fast", catalog, root)

            fast_profile_drift = fast.replace("feedback_run_nested_shaders || fail",
                                              "feedback_run_nested_shader_profile || fail", 1)
            (scripts / "build_fast_device.sh").write_text(fast_profile_drift, encoding="utf-8")
            with self.assertRaisesRegex(feedback.CatalogError, "FastDevice executing profile anchor drift"):
                feedback._stage_order("fast", catalog, root)

    def test_extracted_production_dispatch_has_on_off_parity(self) -> None:
        """Run the real dispatch functions in a bounded no-build harness."""
        source = (feedback.ROOT / "misc/ios/build_check.sh").read_text(encoding="utf-8")
        cutoff = source.index('echo "== iOS active-gate capsule and private logging contracts =="')
        prefix = source[:cutoff].replace(
            'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"',
            f"REPO_ROOT={shlex.quote(str(feedback.ROOT))}",
        )
        probe = (prefix + "\nfeedback_stage 'stage::python::misc/ios/test_active_gate.py' "
                 "python3 misc/ios/test_active_gate.py\n")
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "dispatch-probe.sh"
            script.write_text(probe, encoding="utf-8")
            _root, context = self._runtime_root(temporary, profile="engine", run_id="dispatch", nonce="a" * 64)
            hostile_root = Path(temporary).resolve() / "hostile-feedback"
            self.assertTrue(feedback.runtime_initialize(
                hostile_root, "engine", "dispatch-hostile", "c" * 64))
            hostile_context = {"directory": str(hostile_root), "profile": "engine",
                               "run_id": "dispatch-hostile", "nonce": "c" * 64}

            def authoritative_shape(result: subprocess.CompletedProcess[str]) -> tuple[int, str, str]:
                return (result.returncode, result.stdout,
                        re.sub(r"Ran (\d+) tests in [0-9.]+s", r"Ran \1 tests in <time>s", result.stderr))

            def run_trusted_shell(path: Path, runtime_context: dict[str, str] = context,
                                  environment: dict[str, str] | None = None):
                context_file = Path(temporary) / f"context-{time.time_ns()}"
                context_file.write_text("\n".join((runtime_context["directory"], runtime_context["nonce"],
                                                       runtime_context["run_id"], runtime_context["profile"])) + "\n",
                                        encoding="utf-8")
                context_file.chmod(0o600)
                descriptor = os.open(context_file, os.O_RDONLY)
                context_file.unlink()
                try:
                    child_environment = (self._telemetry_env(runtime_context)
                                         if environment is None else dict(environment))
                    child_environment["XRAY_FEEDBACK_CONTEXT_FD"] = str(descriptor)
                    child_environment["BASH_ENV"] = "/definitely/ignored/bash-env"
                    return subprocess.run(["bash", str(path)], cwd=feedback.ROOT,
                                          env=child_environment, pass_fds=(descriptor,),
                                          text=True, capture_output=True)
                finally:
                    os.close(descriptor)

            detail_sentinel = str(Path(temporary).resolve() / "gate-detail-sentinel")

            def hostile_environment(runtime_context: dict[str, str], record: Path,
                                   trace_fd: int) -> dict[str, str]:
                environment = self._telemetry_env(runtime_context)
                environment.update({
                    "BASH_ENV": "/definitely/ignored/bash-env",
                    "ENV": "/definitely/ignored/env-hook",
                    "SHELLOPTS": "allexport:xtrace",
                    "BASHOPTS": "extglob",
                    "PS4": "HOSTILE-XTRACE ",
                    "BASH_XTRACEFD": str(trace_fd),
                    "OPENXRAY_SITECHAIN_RECORD": str(record),
                    "OPENXRAY_GATE_DETAIL_DIR": detail_sentinel,
                })
                return environment

            def write_sitecustomize(directory: Path, record: Path,
                                    forbidden: tuple[str, ...], *, require_raw: bool) -> None:
                (directory / "sitecustomize.py").write_text(
                    "import json, os, stat, sys\nfrom pathlib import Path\n"
                    f"forbidden={forbidden!r}\n"
                    "descriptors=[]\n"
                    "for value in range(0,256):\n"
                    " try:\n  detail=os.fstat(value); descriptors.append((detail.st_dev,detail.st_ino))\n"
                    " except OSError:\n  pass\n"
                    "keys=sorted(os.environ)\n"
                    "data={'keys':keys,'forbidden_values':sorted(k for k,v in os.environ.items() if v in forbidden),"
                    "'context_names':sorted(k for k in keys if k.startswith('OPENXRAY_TEST_FEEDBACK_') or k in {'XRAY_FEEDBACK_CONTEXT_FD','OPENXRAY_GATE_DETAIL_DIR'}),"
                    "'function_names':sorted(k for k in keys if k.startswith('BASH_FUNC_') and k.endswith('%%')),"
                    "'raw_marker':bool(os.environ.get('XRAY_FEEDBACK_RAW_EVENT_FD')),'argv':sys.argv,'descriptors':descriptors}\n"
                    # A selected unittest can launch further Python helpers.
                    # Record every observed child, rather than allowing a
                    # later helper to hide the selected interpreter's raw
                    # descriptor state.
                    f"with Path({str(record)!r}).open('a',encoding='utf-8') as stream:\n"
                    " stream.write(json.dumps(data,sort_keys=True)+'\\n')\n",
                    encoding="utf-8")

            def recorded_children(path: Path) -> list[dict]:
                return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

            with tempfile.TemporaryDirectory() as site_temporary:
                site_directory = Path(site_temporary)
                selected_record = site_directory / "selected.json"
                trace_path = site_directory / "xtrace.log"
                write_sitecustomize(site_directory, selected_record,
                                    (hostile_context["directory"], hostile_context["nonce"],
                                     hostile_context["run_id"], hostile_context["profile"], detail_sentinel),
                                    require_raw=True)
                trace_fd = os.open(trace_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    hostile_off = hostile_environment(hostile_context, selected_record, trace_fd)
                    hostile_off["PYTHONPATH"] = str(site_directory)
                    off = subprocess.run(["bash", str(script)], cwd=feedback.ROOT,
                                         env=hostile_off, pass_fds=(trace_fd,),
                                         text=True, capture_output=True)
                    trace_fd_detail = os.fstat(trace_fd)
                finally:
                    os.close(trace_fd)
                off_selected = recorded_children(selected_record)

                selected_record.unlink(missing_ok=True)
                trace_path.unlink(missing_ok=True)
                trace_fd = os.open(trace_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    hostile_on = hostile_environment(hostile_context, selected_record, trace_fd)
                    hostile_on["PYTHONPATH"] = str(site_directory)
                    on = run_trusted_shell(script, hostile_context, hostile_on)
                    trace_fd_detail = os.fstat(trace_fd)
                finally:
                    os.close(trace_fd)

                self.assertEqual(authoritative_shape(on), authoritative_shape(off))
                selected = recorded_children(selected_record)
                selected_off = [observed for observed in off_selected
                                if observed["argv"][:1] == ["misc/ios/test_active_gate.py"]]
                selected_on = [observed for observed in selected
                               if observed["argv"][:1] == ["misc/ios/test_active_gate.py"]]
                self.assertTrue(selected_off, off_selected)
                self.assertTrue(selected_on, selected)
                for observed in (*selected_off, *selected_on):
                    self.assertEqual(observed["forbidden_values"], [])
                    self.assertEqual(observed["context_names"], [])
                    self.assertEqual(observed["function_names"], [])
                    self.assertNotIn((trace_fd_detail.st_dev, trace_fd_detail.st_ino),
                                     [tuple(value) for value in observed["descriptors"]])
                self.assertFalse(any(observed["raw_marker"] for observed in selected_off), selected_off)
                self.assertTrue(any(observed["raw_marker"] for observed in selected_on), selected_on)
                for value in (hostile_context["nonce"], hostile_context["directory"],
                              hostile_context["run_id"], hostile_context["profile"]):
                    self.assertNotIn(value, on.stdout + on.stderr + trace_path.read_text(encoding="utf-8"))
                for path in Path(hostile_context["directory"]).rglob("*"):
                    if path.is_file():
                        self.assertNotIn(hostile_context["nonce"].encode(), path.read_bytes())

                # The selected child is deliberately hostile: it asks macOS
                # fcntl(F_GETPATH) for the raw descriptor and probes procfs
                # when available, then tries to enumerate/open every runtime
                # sibling reachable from either result.  The raw sink was
                # unlinked before exec, so neither mechanism may lead it into
                # the protected test-feedback root.
                raw_probe = Path(temporary) / "raw-fd-hostile-child.py"
                raw_probe.write_text(
                    "import ctypes, json, os, sys\nfrom pathlib import Path\n"
                    "fd=int(os.environ['XRAY_FEEDBACK_RAW_EVENT_FD'])\n"
                    "data={'fgetpath_attempted':False,'fgetpath':None,'fgetpath_errno':None,"
                    "'proc_attempted':True,'proc_path':None,'discoveries':[],'accessed':[]}\n"
                    "paths=[]\n"
                    "if sys.platform == 'darwin':\n"
                    " data['fgetpath_attempted']=True\n"
                    " buffer=ctypes.create_string_buffer(4096)\n"
                    " libc=ctypes.CDLL(None,use_errno=True)\n"
                    " if libc.fcntl(fd,50,ctypes.byref(buffer)) == 0:\n"
                    "  data['fgetpath']=buffer.value.decode(errors='surrogateescape'); paths.append(data['fgetpath'])\n"
                    " else: data['fgetpath_errno']=ctypes.get_errno()\n"
                    "proc=f'/proc/self/fd/{fd}'\n"
                    "if os.path.lexists(proc):\n"
                    " try:\n  data['proc_path']=os.readlink(proc); paths.append(data['proc_path'])\n"
                    " except OSError: pass\n"
                    "for value in paths:\n"
                    " parent=Path(value).parent\n"
                    " for sibling in ('selection.json','case-events','stage-events','error-events'):\n"
                    "  target=parent/sibling\n"
                    "  try:\n   os.lstat(target); data['discoveries'].append(str(target))\n"
                    "  except OSError: continue\n"
                    "  try:\n   flags=os.O_RDONLY | (getattr(os,'O_DIRECTORY',0) if target.is_dir() else 0)\n"
                    "   opened=os.open(target,flags); os.close(opened); data['accessed'].append(str(target))\n"
                    "  except OSError: pass\n"
                    "Path(os.environ['RAW_PROBE_RECORD']).write_text(json.dumps(data,sort_keys=True))\n",
                    encoding="utf-8",
                )
                raw_record = site_directory / "raw-fd-hostile.json"
                raw_script = Path(temporary) / "raw-fd-hostile-dispatch.sh"
                raw_script.write_text(
                    prefix + "\nfeedback_selected_raw 'python::misc/ios/test_active_gate.py' "
                    f"python3 {shlex.quote(str(raw_probe))}\n",
                    encoding="utf-8",
                )
                trace_path.unlink(missing_ok=True)
                trace_fd = os.open(trace_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    raw_environment = hostile_environment(hostile_context, raw_record, trace_fd)
                    raw_environment["RAW_PROBE_RECORD"] = str(raw_record)
                    raw_result = run_trusted_shell(raw_script, context, raw_environment)
                finally:
                    os.close(trace_fd)
                self.assertEqual(raw_result.returncode, 0, raw_result.stderr)
                hostile_fd = json.loads(raw_record.read_text(encoding="utf-8"))
                if sys.platform == "darwin":
                    self.assertTrue(hostile_fd["fgetpath_attempted"], hostile_fd)
                protected_root = str(Path(context["directory"]).resolve())
                for value in (hostile_fd["fgetpath"], hostile_fd["proc_path"],
                              *hostile_fd["discoveries"], *hostile_fd["accessed"]):
                    if value is not None:
                        self.assertNotIn(protected_root, value, hostile_fd)
                self.assertEqual(hostile_fd["discoveries"], [], hostile_fd)
                self.assertEqual(hostile_fd["accessed"], [], hostile_fd)
                self.assertEqual(list(Path(context["directory"]).glob("raw-events.*")), [])

                # A synthetic C++ feedback_case has no raw sink; its parent
                # owns the event.  It must receive the same scrubbed child
                # environment in OFF and ON, including the detail sentinel.
                cxx_record = site_directory / "cpp-selected.json"
                cxx_probe = prefix + "\nfeedback_now() { printf '1\\n'; }\n" \
                    "feedback_case 'cpp:synthetic-feedback-case@strict' python3 -c 'pass'\n"
                cxx_script = Path(temporary) / "cpp-dispatch-probe.sh"
                cxx_script.write_text(cxx_probe, encoding="utf-8")
                write_sitecustomize(site_directory, cxx_record,
                                    (hostile_context["directory"], hostile_context["nonce"],
                                     hostile_context["run_id"], hostile_context["profile"], detail_sentinel),
                                    require_raw=False)
                trace_path.unlink(missing_ok=True)
                trace_fd = os.open(trace_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    cxx_off_environment = hostile_environment(hostile_context, cxx_record, trace_fd)
                    cxx_off_environment["PYTHONPATH"] = str(site_directory)
                    cxx_off = subprocess.run(["bash", str(cxx_script)], cwd=feedback.ROOT,
                                             env=cxx_off_environment, pass_fds=(trace_fd,),
                                             text=True, capture_output=True)
                finally:
                    os.close(trace_fd)
                cxx_off_selected = recorded_children(cxx_record)
                cxx_record.unlink(missing_ok=True)
                trace_path.unlink(missing_ok=True)
                trace_fd = os.open(trace_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    cxx_on_environment = hostile_environment(hostile_context, cxx_record, trace_fd)
                    cxx_on_environment["PYTHONPATH"] = str(site_directory)
                    cxx_on = run_trusted_shell(cxx_script, hostile_context, cxx_on_environment)
                finally:
                    os.close(trace_fd)
                self.assertEqual(authoritative_shape(cxx_on), authoritative_shape(cxx_off))
                cxx_selected = recorded_children(cxx_record)
                for observed in (*cxx_off_selected, *cxx_selected):
                    self.assertEqual(observed["forbidden_values"], [])
                    self.assertEqual(observed["context_names"], [])
                    self.assertEqual(observed["function_names"], [])
                    self.assertFalse(observed["raw_marker"], observed)

                # FastDevice creates a fresh context FD for its nested Bash.
                # Use the real nested wrapper, with a tiny extracted
                # build_check child that consumes the context then launches a
                # selected command.  This proves the nested boundary neither
                # imports hostile functions nor leaks context/detail to it.
                fast_source = (feedback.ROOT / "misc/ios/build_fast_device.sh").read_text(encoding="utf-8")
                fast_prefix = fast_source[:fast_source.index("archive_gate_detail_log() {")].replace(
                    'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"',
                    f"REPO_ROOT={shlex.quote(str(feedback.ROOT))}",
                )
                nested_script = Path(temporary) / "nested-selected-probe.sh"
                nested_script.write_text(
                    prefix + "\nfeedback_selected_command python3 -c 'pass'\n",
                    encoding="utf-8",
                )
                fast_prefix = fast_prefix.replace("./misc/ios/build_check.sh", str(nested_script))
                fast_script = Path(temporary) / "fast-direct-probe.sh"
                fast_script.write_text(
                    fast_prefix + "\n[ \"$feedback_enabled\" = 1 ] || exit 97\n"
                    "feedback_run_nested_shaders\n",
                    encoding="utf-8")
                selected_record.unlink(missing_ok=True)
                write_sitecustomize(site_directory, selected_record,
                                    (hostile_context["directory"], hostile_context["nonce"],
                                     hostile_context["run_id"], hostile_context["profile"], detail_sentinel),
                                    require_raw=False)
                trace_path.unlink(missing_ok=True)
                trace_fd = os.open(trace_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    fast_environment = hostile_environment(hostile_context, selected_record, trace_fd)
                    fast_environment["PYTHONPATH"] = str(site_directory)
                    fast_result = run_trusted_shell(fast_script, hostile_context, fast_environment)
                finally:
                    os.close(trace_fd)
                self.assertEqual(fast_result.returncode, 0, fast_result.stderr)
                fast_selected = recorded_children(selected_record)
                self.assertTrue(fast_selected)
                for observed in fast_selected:
                    self.assertEqual(observed["forbidden_values"], [])
                    self.assertEqual(observed["context_names"], [])
                    self.assertEqual(observed["function_names"], [])
                    self.assertFalse(observed["raw_marker"])
                # Use a separate no-build FastDevice harness for authoritative
                # ON/OFF parity.  The hostile bootstrap probe above deliberately
                # starts with xtrace and therefore is not its output baseline.
                fast_parity_script = Path(temporary) / "fast-parity-probe.sh"
                fast_parity_script.write_text(
                    fast_prefix + "\nfeedback_run_nested_shaders\n", encoding="utf-8")
                fast_off = subprocess.run(["bash", str(fast_parity_script)], cwd=feedback.ROOT,
                                          env=self._telemetry_env(context), text=True,
                                          capture_output=True)
                fast_parity_root = Path(temporary).resolve() / "fast-feedback-parity"
                self.assertTrue(feedback.runtime_initialize(
                    fast_parity_root, "fast", "fast-parity", "d" * 64))
                fast_parity_context = {"directory": str(fast_parity_root), "profile": "fast",
                                       "run_id": "fast-parity", "nonce": "d" * 64}
                fast_on = run_trusted_shell(fast_parity_script, fast_parity_context)
                self.assertEqual(authoritative_shape(fast_on), authoritative_shape(fast_off))
                fast_faults = {
                    "mktemp": ("fast-mktemp-sentinel",
                               "mktemp() { printf 'fast-mktemp-sentinel\\n' >&2; return 1; }"),
                    "chmod": ("fast-chmod-sentinel",
                              "chmod() { printf 'fast-chmod-sentinel\\n' >&2; return 1; }"),
                    "cleanup": ("fast-rm-sentinel",
                                "rm() { case \"$*\" in *nested-context.*) "
                                "printf 'fast-rm-sentinel\\n' >&2; return 1 ;; "
                                "*) command rm \"$@\" ;; esac; }"),
                }
                for fault, (sentinel, override) in fast_faults.items():
                    with self.subTest(fast_observer_fault=fault):
                        fast_fault_root = Path(temporary).resolve() / f"fast-feedback-{fault}"
                        self.assertTrue(feedback.runtime_initialize(
                            fast_fault_root, "fast", f"fast-{fault}", "d" * 64))
                        fast_fault_context = {"directory": str(fast_fault_root), "profile": "fast",
                                              "run_id": f"fast-{fault}", "nonce": "d" * 64}
                        fast_fault_script = Path(temporary) / f"fast-{fault}-probe.sh"
                        fast_fault_script.write_text(
                            fast_prefix + f"\n{override}\n"
                            "[ \"$feedback_enabled\" = 1 ] || exit 97\n"
                            "feedback_run_nested_shaders\n",
                            encoding="utf-8")
                        fast_fault_result = run_trusted_shell(fast_fault_script, fast_fault_context)
                        self.assertEqual(authoritative_shape(fast_fault_result),
                                         authoritative_shape(fast_off))
                        self.assertNotIn(sentinel, fast_fault_result.stderr)
                        contexts = list(fast_fault_root.glob("nested-context.*"))
                        if fault == "cleanup":
                            self.assertTrue(contexts)
                            self.assertEqual(feedback.runtime_finalize(
                                fast_fault_root, "fast", fast_fault_context["run_id"],
                                fast_fault_context["nonce"], 0)["status"], "ERROR")
                        else:
                            self.assertEqual(contexts, [])
                self.assertNotIn(hostile_context["nonce"],
                                 fast_result.stdout + fast_result.stderr + trace_path.read_text(encoding="utf-8"))

            off = subprocess.run(["bash", str(script)], cwd=feedback.ROOT,
                                 env=self._telemetry_env(context), text=True, capture_output=True)
            on = run_trusted_shell(script)

            self.assertEqual(authoritative_shape(on), authoritative_shape(off))
            expected_cases = feedback.parse_python_methods(
                feedback.ROOT / "misc/ios/test_active_gate.py")
            observed_cases = sorted(json.loads(path.read_text(encoding="utf-8"))["test_id"]
                                    for path in (Path(context["directory"]) / "case-events").glob("*.json"))
            self.assertEqual(observed_cases, sorted(expected_cases))
            self.assertTrue(list((Path(context["directory"]) / "stage-events").glob("*.json")))
            self.assertEqual(list(Path(context["directory"]).glob("raw-events.*")), [])
            self.assertEqual(list(Path(context["directory"]).glob("nested-context.*")), [])

            # All observer-path failures are fail-open for the real selected
            # entrypoint.  They may remove telemetry, but never alter its
            # authoritative output or process status.
            fault_sources = {
                "mktemp": ("raw-mktemp-sentinel",
                           prefix + "\nmktemp() { printf 'raw-mktemp-sentinel\\n' >&2; return 1; }\n"
                           + probe[len(prefix):]),
                "chmod": ("raw-chmod-sentinel",
                          prefix + "\nchmod() { printf 'raw-chmod-sentinel\\n' >&2; return 1; }\n"
                          + probe[len(prefix):]),
                "ingest": (None, prefix + "\nfeedback_ingest_raw() { return 1; }\n" + probe[len(prefix):]),
                "corrupt": (None, prefix + "\nfeedback_ingest_raw() { printf 'corrupt\\n' >&\"$2\"; "
                            "feedback_with_context ingest --entrypoint-id \"$1\" --raw-fd \"$2\" "
                            ">/dev/null 2>&1 || true; }\n" + probe[len(prefix):]),
                "cleanup": ("raw-rm-sentinel", prefix + "\nrm() { case \"$*\" in "
                            "*raw-events.*) printf 'raw-rm-sentinel\\n' >&2; return 1 ;; "
                            "*) command rm \"$@\" ;; esac; }\n" + probe[len(prefix):]),
            }
            for fault, (sentinel, fault_source) in fault_sources.items():
                with self.subTest(observer_fault=fault):
                    fault_script = Path(temporary) / f"dispatch-{fault}.sh"
                    fault_script.write_text(fault_source, encoding="utf-8")
                    fault_root = Path(temporary).resolve() / f"feedback-{fault}"
                    self.assertTrue(feedback.runtime_initialize(
                        fault_root, "engine", f"dispatch-{fault}", "b" * 64))
                    fault_context = {"directory": str(fault_root), "profile": "engine",
                                     "run_id": f"dispatch-{fault}", "nonce": "b" * 64}
                    fault_result = run_trusted_shell(fault_script, fault_context)
                    self.assertEqual(authoritative_shape(fault_result), authoritative_shape(off))
                    if sentinel is not None:
                        self.assertNotIn(sentinel, fault_result.stderr)
                    if fault == "cleanup":
                        self.assertTrue(list(fault_root.glob("raw-events.*")))
                        self.assertEqual(feedback.runtime_finalize(
                            fault_root, "engine", fault_context["run_id"], fault_context["nonce"], 0)["status"],
                            "ERROR")
                    elif fault == "corrupt":
                        self.assertFalse(list((fault_root / "case-events").glob("*.json")))
                        self.assertEqual(feedback.runtime_finalize(
                            fault_root, "engine", fault_context["run_id"], fault_context["nonce"], 0)["status"],
                            "INCOMPLETE")
                    else:
                        self.assertFalse(list((fault_root / "case-events").glob("*.json")))
                        self.assertEqual(list(fault_root.glob("raw-events.*")), [])

            # An interruption after the raw cleanup path is published invokes
            # the real EXIT trap.  Its telemetry-only rm failure must remain
            # invisible to the selected command's authoritative output.
            trap_sentinel = "raw-exit-trap-sentinel"
            trap_script = Path(temporary) / "dispatch-exit-trap.sh"
            trap_script.write_text(
                prefix + "\nrm() { case \"$*\" in *raw-events.*) "
                f"printf '{trap_sentinel}\\n' >&2; return 1 ;; "
                "*) command rm \"$@\" ;; esac; }\n"
                "feedback_raw_cleanup_path=\"$feedback_dir/raw-events.interrupted\"\n"
                "python3 misc/ios/test_active_gate.py\n",
                encoding="utf-8")
            trap_root = Path(temporary).resolve() / "feedback-exit-trap"
            self.assertTrue(feedback.runtime_initialize(
                trap_root, "engine", "dispatch-exit-trap", "e" * 64))
            trap_context = {"directory": str(trap_root), "profile": "engine",
                            "run_id": "dispatch-exit-trap", "nonce": "e" * 64}
            trap_result = run_trusted_shell(trap_script, trap_context)
            self.assertEqual(authoritative_shape(trap_result), authoritative_shape(off))
            self.assertNotIn(trap_sentinel, trap_result.stderr)

            # The top-level Bash rejects a FIFO before reading it.  Keep the
            # writer live to prove this is nonblocking, not just EOF-tolerant.
            reader, writer = os.pipe()
            try:
                hostile_env = self._telemetry_env(context)
                hostile_env["XRAY_FEEDBACK_CONTEXT_FD"] = str(reader)
                hostile_env["BASH_ENV"] = "/definitely/ignored/bash-env"
                hostile = subprocess.run(["bash", str(script)], cwd=feedback.ROOT,
                                         env=hostile_env, pass_fds=(reader,), text=True,
                                         capture_output=True, timeout=20)
                self.assertEqual(authoritative_shape(hostile), authoritative_shape(off))
            finally:
                os.close(reader)
                os.close(writer)
            broken = Path(temporary) / "dispatch-probe-broken-observer.sh"
            broken.write_text(probe.replace('"$REPO_ROOT/misc/ios/test_feedback.py"',
                                            '"/definitely/missing/test_feedback.py"'), encoding="utf-8")
            broken_on = run_trusted_shell(broken)
            self.assertEqual(authoritative_shape(broken_on), authoritative_shape(off))

    def test_full_real_glsl_success_and_invalid_argument_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = self._head_tool(directory, "glsl_es_check.py")
            missing = str(directory / "missing")
            old_invalid = self._checker_result(
                [sys.executable, str(old), "--jobs", "-1", "--shaders", missing]
            )
            new_invalid = self._checker_result(
                [sys.executable, "misc/ios/shadercheck/glsl_es_check.py",
                 "--jobs", "-1", "--shaders", missing]
            )
            self.assertEqual(new_invalid, old_invalid)
            glslang = shutil.which("glslangValidator")
            if glslang is None:
                self.skipTest("glslangValidator unavailable: invalid-argument parity passed")
            arguments = ["--strict", "--glslang", glslang]
            before = self._checker_result([sys.executable, str(old), *arguments])
            after = self._checker_result(
                [sys.executable, "misc/ios/shadercheck/glsl_es_check.py", *arguments]
            )
            self.assertEqual(after, before)
            self.assertEqual(after[0], 0)
            self.assertIn("279/279 compile", after[1])

    def test_missing_variant_targets_preserve_head_diagnostics_and_exit(self) -> None:
        glslang = shutil.which("glslangValidator")
        if glslang is None:
            self.skipTest("glslangValidator unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = self._head_tool(directory, "glsl_es_check.py")
            for target in ("combine_1_nomsaa.ps", "water.ps"):
                with self.subTest(target=target):
                    fixture = directory / target.replace(".ps", "")
                    shutil.copytree(feedback.ROOT / "res/gamedata/shaders/gl", fixture)
                    (fixture / target).unlink()
                    arguments = [
                        "--shaders", str(fixture), "--glslang", glslang,
                        "--strict", "--jobs", "1",
                    ]
                    before = self._checker_result([sys.executable, str(old), *arguments])
                    after = self._checker_result(
                        [sys.executable, "misc/ios/shadercheck/glsl_es_check.py", *arguments]
                    )
                    self.assertEqual(after, before)
                    self.assertEqual(after[0], 1)
                    self.assertIn("missing", after[1])

    def test_real_137_link_path_and_output_match_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old_glsl = self._head_tool(directory, "glsl_es_check.py")
            self.assertTrue(old_glsl.is_file())
            old_link = self._head_tool(directory, "link_check.py")
            before = self._checker_result([sys.executable, str(old_link), "--strict"])
            after = self._checker_result(
                [sys.executable, "misc/ios/shadercheck/link_check.py", "--strict"]
            )
            self.assertEqual(after, before)
            self.assertEqual(after[0], 0)
            self.assertIn("137/137 pairs clean", after[1])
            listed = json.loads(subprocess.run(
                [sys.executable, "misc/ios/shadercheck/link_check.py", "--list-json"],
                cwd=feedback.ROOT, check=True, text=True, capture_output=True,
            ).stdout)
            self.assertEqual(len(listed["links"]), 137)

    def test_missing_catalog_entry_and_shader_list_target_fail_closed(self) -> None:
        original = feedback.read_catalog()
        for identifier in feedback.parse_gate_validation_entrypoints():
            with self.subTest(identifier=identifier):
                catalog = copy.deepcopy(original)
                catalog["entrypoints"] = [
                    item for item in catalog["entrypoints"]
                    if item["entrypoint_id"] != identifier
                ]
                with self.assertRaisesRegex(feedback.CatalogError, "entrypoint drift"):
                    feedback.validate(catalog)
        build_source = (feedback.ROOT / "misc/ios/build_check.sh").read_text(
            encoding="utf-8"
        )
        command_anchors = (
            "python3 misc/ios/ui_contract_check.py \\\n",
            "bash -n misc/ios/run_gate_logged.sh \\\n",
            "bash -n misc/ios/device_lease.sh misc/ios/input.sh misc/ios/shot.sh misc/ios/lighting_ab_capture.sh \\\n",
            "shellcheck -x misc/ios/device_lease.sh misc/ios/input.sh misc/ios/shot.sh misc/ios/lighting_ab_capture.sh \\\n",
            '    feedback_stage "stage::validation::shader::macro-contract" python3 misc/ios/shadercheck/glsl_es_check.py --macro-contract \\\n',
        )
        for index, anchor in enumerate(command_anchors):
            with self.subTest(gate_anchor=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                target = root / "misc/ios/build_check.sh"
                target.parent.mkdir(parents=True)
                self.assertIn(anchor, build_source)
                target.write_text(build_source.replace(anchor, "", 1), encoding="utf-8")
                with self.assertRaisesRegex(
                        feedback.CatalogError, "validation entrypoint drift"):
                    feedback.parse_gate_validation_entrypoints(root)
        family_anchors = (
            'feedback_stage "stage::shell::ui-log-oracle" misc/ios/ui_automation/test_log_oracles.sh \\\n',
            '    run_shader_cache_stage compile "stage::shader::glsl-es" shader::glsl-es "$compile_hash" \\\n',
            '    run_shader_cache_stage link "stage::shader::link" shader::link "$link_hash" \\\n',
        )
        for index, anchor in enumerate(family_anchors):
            with self.subTest(gate_family_anchor=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                target = root / "misc/ios/build_check.sh"
                target.parent.mkdir(parents=True)
                self.assertIn(anchor, build_source)
                target.write_text(build_source.replace(anchor, "", 1), encoding="utf-8")
                with self.assertRaisesRegex(feedback.CatalogError, "family entrypoint drift"):
                    feedback.parse_gate_family_entrypoints(root)
        dispatcher_anchors = (
            '    elif [ "${1:-}" = misc/ios/ui_automation/test_log_oracles.sh ]; then\n'
            '        feedback_selected_shell "$@"\n        return $?\n',
            '    shader_cache_output=$(feedback_selected_raw "$origin" python3 "$REPO_ROOT/misc/ios/shader_cache.py" "${helper_args[@]}" 2>&1)\n',
        )
        for index, anchor in enumerate(dispatcher_anchors):
            with self.subTest(dispatcher_family=index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                target = root / "misc/ios/build_check.sh"
                target.parent.mkdir(parents=True)
                self.assertIn(anchor, build_source)
                target.write_text(build_source.replace(anchor, "", 1), encoding="utf-8")
                with self.assertRaisesRegex(feedback.CatalogError, "family entrypoint drift"):
                    feedback.parse_gate_family_entrypoints(root)
        catalog = copy.deepcopy(original)
        catalog["build_stage_exclusions"].pop()
        with self.assertRaisesRegex(feedback.CatalogError, "unclassified Python|exclusion"):
            feedback.validate(catalog)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "gl"
            shutil.copytree(feedback.ROOT / "res/gamedata/shaders/gl", fixture)
            (fixture / "water.ps").unlink()
            result = subprocess.run(
                [sys.executable, "misc/ios/shadercheck/glsl_es_check.py",
                 "--list-json", "--shaders", str(fixture)],
                cwd=feedback.ROOT, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("variant shader target missing", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
