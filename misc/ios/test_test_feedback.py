#!/usr/bin/env python3
"""Focused contracts for the static, non-executing Phase 0A catalog."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_feedback as feedback


AUDITED = {
    "central-python": "a1d44f025c13d688885d530dc8c91266744ff5a4965b5d0ec07f0d2a9e147560",
    "ci-contract": "0dff3ed04462d247c96ae814e597e1f52500530fc98752c82149f108ee4f23fb",
    "host-infra": "d9a3b20deb64f2f00d47175b6cd2b5db51e2bb8d95bb064f56637270e7f214a6",
    "legacy-python": "2a0b400ff28b119b2b33bb68cc9d58bd0d28a730b6c631020181794437aae1d0",
    "cpp": "2e3b8feb7d6b7c143e322e391a09725be032b6d155afc4614f7f8e673ad2eae2",
    "shell": "fdfa6b29c893090567b3c9e498ce0ebd641d7a45f6336f20792c0f1da2ef1977",
    "shader-baseline": "e817fbc5e008b775761533a828bec1e7d8801e6c1736bffb852f42715684b0ab",
    "shader-variants": "2e51d4d72b14ba85b0a2302ace76dd1dc25ab3782ebc72a3b1e7d6028af7fad8",
    "shader-links": "5bf4594445e395f4e32a191e8d2aa26716878bf809c99013e2e1b2ee3cd1c212",
    "xctest": "2b62812c1d340f8eef901bb48cdd4d5078352f7e3ea6086c35eb116dbfd26b03",
}


class TestFeedbackPhase0ATests(unittest.TestCase):
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
        self.assertEqual(len(records), 50)
        self.assertEqual(sum(item.startswith("python::") for item in records), 30)
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
            "xctest::OpenXRayUITests",
        ):
            self.assertEqual(by_id[identifier]["selected_profiles"], [])
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
        self.assertEqual(len(actual), 11)
        self.assertEqual(
            by_id["python::misc/ios/test_test_feedback.py"]["expected_case_count"],
            len(actual),
        )
        self.assertEqual(catalog["frozen"]["baseline"]["legacy_python_cases"], 781)
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

    def test_list_modes_do_not_invoke_glslang_and_gate_files_equal_head(self) -> None:
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
        for path in (
            "misc/ios/build_check.sh",
            "misc/ios/build_fast_device.sh",
            "misc/ios/run_gate_logged.py",
            "misc/ios/ui_automation/test_log_oracles.sh",
        ):
            expected = subprocess.run(
                ["git", "show", f"HEAD:{path}"], cwd=feedback.ROOT,
                check=True, capture_output=True,
            ).stdout
            self.assertEqual((feedback.ROOT / path).read_bytes(), expected, path)
        build_source = (feedback.ROOT / "misc/ios/build_check.sh").read_text(
            encoding="utf-8"
        )
        cpp_mutations = (
            build_source.replace(
                '"$capture_state_test" || fail',
                '"$wrong_capture_state_test" || fail',
                1,
            ),
            build_source.replace(
                '"$capture_state_test" || fail "capture-v2 serializer test failed"',
                '"$capture_state_test" || fail "capture-v2 serializer test failed"\n'
                '"$capture_state_test" || fail "capture-v2 serializer test failed"',
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
            "    python3 misc/ios/shadercheck/glsl_es_check.py --macro-contract \\\n",
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
            "misc/ios/ui_automation/test_log_oracles.sh \\\n",
            "        out=$(python3 misc/ios/shadercheck/glsl_es_check.py \\\n",
            "        out=$(python3 misc/ios/shadercheck/link_check.py --strict 2>&1) \\\n",
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
