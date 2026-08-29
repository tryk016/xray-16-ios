#!/usr/bin/env python3
"""Exactly 32 stable unit tests for the pure shadow-only manifest mapper."""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

if os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment(
            "python::misc/ios/test_affected_test_planner.py", sys.argv)
    except BaseException:
        pass

import affected_test_planner as planner


ROOT = Path(__file__).resolve().parent


def raw_manifest(records: list[dict[str, str]], complete: bool = True) -> bytes:
    return json.dumps({"schema": planner.MANIFEST_SCHEMA, "producer": "external-untrusted", "complete": complete,
                       "base": None, "head": None, "records": records}, sort_keys=True, separators=(",", ":")).encode()


def assets() -> tuple[dict, dict]:
    return planner.load_assets(ROOT)


class PureManifestMapperTests(unittest.TestCase):
    def test_01_exactly_32_stable_tests(self) -> None:
        names = sorted(name for name in dir(type(self)) if name.startswith("test_"))
        self.assertEqual(32, len(names))

    def test_02_missing_manifest_is_full(self) -> None:
        mapping, inventory = assets()
        report = planner.build_report(mapping, inventory, None, "manifest missing")
        self.assertEqual("full-release", report["profile"])
        self.assertEqual([planner.ERROR_MANIFEST_MISSING], report["fallback_reasons"])

    def test_03_invalid_manifest_is_full(self) -> None:
        mapping, inventory = assets()
        report = planner.build_report(mapping, inventory, b"{", None)
        self.assertTrue(report["fallback"])
        self.assertEqual("full-release", report["profile"])
        self.assertEqual([planner.ERROR_MANIFEST_INVALID], report["fallback_reasons"])

    def test_04_incomplete_manifest_is_full(self) -> None:
        mapping, inventory = assets()
        report = planner.build_report(mapping, inventory, raw_manifest([], complete=False), None)
        self.assertEqual(["external manifest is incomplete"], report["fallback_reasons"])

    def test_05_complete_external_is_still_full(self) -> None:
        mapping, inventory = assets()
        report = planner.build_report(mapping, inventory, raw_manifest([], complete=True), None)
        self.assertEqual("full-release", report["profile"])
        self.assertEqual("untrusted-not-evaluated", report["advisory"]["status"])

    def test_06_internal_empty_fixture_maps_host_fast(self) -> None:
        mapping, inventory = assets()
        self.assertEqual("host-fast", planner.evaluate_complete_fixture(mapping, inventory, planner.validate_manifest(json.loads(raw_manifest([]))))["profile"])

    def test_07_internal_shader_fixture_maps_shader(self) -> None:
        mapping, inventory = assets()
        manifest = planner.validate_manifest(json.loads(raw_manifest([{"status":"M","path":"res/gamedata/shaders/r2/test.ps"}])))
        self.assertEqual("shader-affected", planner.evaluate_complete_fixture(mapping, inventory, manifest)["profile"])

    def test_08_internal_engine_fixture_maps_engine(self) -> None:
        mapping, inventory = assets()
        manifest = planner.validate_manifest(json.loads(raw_manifest([{"status":"M","path":"src/xrGame/example.cpp"}])))
        self.assertEqual("engine-affected", planner.evaluate_complete_fixture(mapping, inventory, manifest)["profile"])

    def test_09_internal_retail_fixture_maps_retail(self) -> None:
        mapping, inventory = assets()
        manifest = planner.validate_manifest(json.loads(raw_manifest([{"status":"M","path":"misc/ios/retail_import.py"}])))
        self.assertEqual("retail-integration", planner.evaluate_complete_fixture(mapping, inventory, manifest)["profile"])

    def test_10_mixed_specialized_profiles_fallback(self) -> None:
        mapping, inventory = assets()
        records = [{"status":"M","path":"misc/ios/retail_import.py"},{"status":"M","path":"res/gamedata/shaders/r2/test.ps"}]
        self.assertTrue(planner.evaluate_complete_fixture(mapping, inventory, planner.validate_manifest(json.loads(raw_manifest(records))))["fallback"])

    def test_11_unknown_path_fallback(self) -> None:
        mapping, inventory = assets()
        manifest = planner.validate_manifest(json.loads(raw_manifest([{"status":"M","path":"doc/new-note.md"}])))
        self.assertTrue(planner.evaluate_complete_fixture(mapping, inventory, manifest)["fallback"])

    def test_12_global_path_fallback(self) -> None:
        mapping, inventory = assets()
        manifest = planner.validate_manifest(json.loads(raw_manifest([{"status":"M","path":"CMakeLists.txt"}])))
        self.assertTrue(planner.evaluate_complete_fixture(mapping, inventory, manifest)["fallback"])

    def test_13_unsafe_statuses_fallback(self) -> None:
        mapping, inventory = assets()
        for status in ("D", "T", "U"):
            manifest = planner.validate_manifest(json.loads(raw_manifest([{"status":status,"path":"src/xrGame/example.cpp"}])))
            self.assertTrue(planner.evaluate_complete_fixture(mapping, inventory, manifest)["fallback"])
        rename = planner.validate_manifest(json.loads(raw_manifest([{"status":"R","path":"src/xrGame/new.cpp","old_path":"src/xrGame/old.cpp"}])))
        self.assertTrue(planner.evaluate_complete_fixture(mapping, inventory, rename)["fallback"])

    def test_14_unsorted_records_rejected(self) -> None:
        records = [{"status":"M","path":"src/z.cpp"},{"status":"M","path":"src/a.cpp"}]
        with self.assertRaises(planner.MapperError): planner.validate_manifest(json.loads(raw_manifest(records)))

    def test_15_duplicate_paths_rejected(self) -> None:
        records = [{"status":"A","path":"src/a.cpp"},{"status":"M","path":"src/a.cpp"}]
        with self.assertRaises(planner.MapperError): planner.validate_manifest(json.loads(raw_manifest(records)))

    def test_16_unsafe_paths_rejected(self) -> None:
        unsafe = ("/abs", "a/../b", "a//b", "a\\b", "a/./b", "a/\x00b",
                  "a/\u0085b", "a/\u200eb", "a/\ue000b", "a/\ud800b", "a/\U0002ffffb")
        for path in unsafe:
            with self.subTest(path=repr(path)):
                with self.assertRaises(planner.MapperError): planner.safe_path(path)

    def test_17_non_nfc_path_rejected(self) -> None:
        with self.assertRaises(planner.MapperError): planner.safe_path("doc/e\u0301.txt")

    def test_18_duplicate_json_key_rejected(self) -> None:
        with self.assertRaises(planner.MapperError): planner.strict_json(b'{"a":1,"a":2}', "fixture")

    def test_19_nonfinite_json_rejected(self) -> None:
        for raw in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e400}'):
            with self.subTest(raw=raw):
                with self.assertRaises(planner.MapperError): planner.strict_json(raw, "fixture")
        for raw in (b'{"x":0}', b'{"x":1.0}', b'{"x":1e400}'):
            with self.subTest(manifest_number=raw):
                with self.assertRaises(planner.MapperError): planner.strict_json(raw, "manifest", forbid_numbers=True)

    def test_20_exact_manifest_schema_rejected(self) -> None:
        bad = {"schema":planner.MANIFEST_SCHEMA,"producer":"external-untrusted","complete":True,"base":None,"head":None,"records":[],"extra":1}
        with self.assertRaises(planner.MapperError): planner.validate_manifest(bad)

    def test_21_record_limit_rejected(self) -> None:
        records = [{"status":"M","path":f"src/{index:05d}.cpp"} for index in range(planner.MAX_RECORDS + 1)]
        with self.assertRaises(planner.MapperError): planner.validate_manifest(json.loads(raw_manifest(records)))

    def test_22_byte_limit_rejected(self) -> None:
        with self.assertRaises(planner.MapperError): planner.strict_json(b" " * (planner.MAX_BYTES + 1), "fixture")

    def test_23_descriptor_symlink_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "target.json"; link = root / "link.json"
            target.write_bytes(b"{}"); link.symlink_to(target.name)
            with self.assertRaises(planner.MapperError): planner.read_bound_file(link, "fixture")
            previous = Path.cwd(); os.chdir(root)
            try:
                with self.assertRaises(planner.MapperError): planner.read_bound_file(Path("link.json"), "fixture")
            finally:
                os.chdir(previous)
            stderr = io.StringIO(); stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(0, planner.main(["--manifest", str(link)]))
            self.assertEqual("", stderr.getvalue())
            self.assertNotIn(str(root), stdout.getvalue())
            self.assertIn(planner.ERROR_MANIFEST_UNAVAILABLE, stdout.getvalue())

    def test_24_descriptor_mode_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.json"; path.write_bytes(b"{}"); os.chmod(path, 0o666)
            with self.assertRaises(planner.MapperError): planner.read_bound_file(path, "fixture")

    def test_25_descriptor_race_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race.json"; path.write_bytes(b'{"a":1}'); os.chmod(path, 0o600)
            original = planner.os.read; changed = False
            def replace(fd: int, count: int) -> bytes:
                nonlocal changed
                if not changed:
                    changed = True; path.write_bytes(b'{"a":2}')
                return original(fd, count)
            planner.os.read = replace
            try:
                with self.assertRaises(planner.MapperError): planner.read_bound_file(path, "fixture")
            finally:
                planner.os.read = original
            stderr = io.StringIO(); stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(0, planner.main(["--manifest", str(path)]))
            self.assertEqual("", stderr.getvalue())
            self.assertNotIn(str(path), stdout.getvalue())
            self.assertIn(planner.ERROR_MANIFEST_INVALID, stdout.getvalue())

    def test_26_stdin_and_file_manifest_parity(self) -> None:
        raw = raw_manifest([], complete=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.json"; path.write_bytes(raw); os.chmod(path, 0o600)
            def invoke(argv: list[str], stdin: bytes | None = None) -> str:
                output = io.StringIO(); old = sys.stdin
                try:
                    if stdin is not None: sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
                    with contextlib.redirect_stdout(output): self.assertEqual(0, planner.main(argv))
                finally: sys.stdin = old
                return output.getvalue()
            self.assertEqual(invoke(["--manifest", str(path)]), invoke(["--stdin"], raw))
            for unavailable in (root / "missing.json", root / ("x" * 300)):
                with self.subTest(unavailable=unavailable.name[:7]):
                    stdout = io.StringIO(); stderr = io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        self.assertEqual(0, planner.main(["--manifest", str(unavailable)]))
                    self.assertEqual("", stderr.getvalue())
                    self.assertNotIn(str(unavailable), stdout.getvalue())
                    self.assertIn(planner.ERROR_MANIFEST_UNAVAILABLE, stdout.getvalue())

    def test_27_deterministic_decision_hash(self) -> None:
        mapping, inventory = assets(); raw = raw_manifest([], complete=True)
        self.assertEqual(planner.build_report(mapping, inventory, raw, None)["decision_sha256"], planner.build_report(mapping, inventory, raw, None)["decision_sha256"])

    def test_28_mapping_drift_rejected(self) -> None:
        mapping, _ = assets(); mapping = copy.deepcopy(mapping); mapping["path_rules"][0]["profile"] = "full-release"
        with self.assertRaises(planner.MapperError): planner._validate_mapping(mapping)

    def test_29_inventory_drift_rejected(self) -> None:
        _, inventory = assets()
        for mutation in ("reverse", "subset", "self-declared-count"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(inventory)
                if mutation == "reverse":
                    changed["profiles"]["full-release"]["stable_ids"].reverse()
                elif mutation == "subset":
                    changed["profiles"]["full-release"]["stable_ids"] = changed["profiles"]["full-release"]["stable_ids"][:1]
                else:
                    changed["profiles"]["full-release"]["stable_ids"] = changed["profiles"]["full-release"]["stable_ids"][:1]
                    changed["parity"]["catalog_complete"] = {"count": 1, "ordering": "lexicographic", "sha256": planner.stable_id_digest(changed["profiles"]["full-release"]["stable_ids"])}
                with self.assertRaises(planner.MapperError): planner._validate_inventory(changed)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in planner.FROZEN_ASSET_SHA256:
                source = ROOT / name
                (base / name).write_bytes(source.read_bytes())
                os.chmod(base / name, 0o600)
            catalog_path = base / "test_feedback_catalog.json"
            catalog_path.write_bytes((ROOT / "test_feedback_catalog.json").read_bytes())
            os.chmod(catalog_path, 0o600)
            inventory_path = base / "test_feedback_inventory_v1.json"
            changed = json.loads(inventory_path.read_text(encoding="utf-8"))
            changed["profiles"]["full-release"]["stable_ids"] = changed["profiles"]["full-release"]["stable_ids"][:1]
            changed["parity"]["catalog_complete"] = {"count": 1, "ordering": "lexicographic", "sha256": planner.stable_id_digest(changed["profiles"]["full-release"]["stable_ids"])}
            inventory_path.write_text(json.dumps(changed, sort_keys=True), encoding="utf-8")
            with self.assertRaises(planner.MapperError): planner.load_assets(base)
            original_file = planner.__file__; stdout = io.StringIO(); stderr = io.StringIO()
            try:
                planner.__file__ = str(base / "affected_test_planner.py")
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(2, planner.main(["--stdin"]))
            finally:
                planner.__file__ = original_file
            self.assertEqual("", stdout.getvalue())
            self.assertEqual(f"pure mapper asset error: {planner.ERROR_ASSET_BINDING}\n", stderr.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in planner.FROZEN_ASSET_SHA256:
                source = ROOT / name
                (base / name).write_bytes(source.read_bytes())
                os.chmod(base / name, 0o600)
            catalog_path = base / "test_feedback_catalog.json"
            catalog = json.loads((ROOT / "test_feedback_catalog.json").read_text(encoding="utf-8"))
            catalog["entrypoints"].pop()
            catalog_path.write_text(json.dumps(catalog, sort_keys=True), encoding="utf-8")
            os.chmod(catalog_path, 0o600)
            with self.assertRaises(planner.MapperError):
                planner.load_assets(base)

    def test_30_postintegration_inventory_parity(self) -> None:
        _, inventory = assets()
        self.assertEqual(
            {"host-fast":877,"retail-integration":924,"shader-affected":527,
             "engine-affected":658,"full-release":1361},
            {name:len(item["stable_ids"]) for name,item in inventory["profiles"].items()},
        )
        self.assertEqual(
            {"host-fast":56,"retail-integration":56,"shader-affected":54,
             "engine-affected":53,"full-release":63},
            {name:len(item["stage_ids"]) for name,item in inventory["profiles"].items()},
        )
        self.assertEqual(57, inventory["parity"]["entrypoints"]["count"])
        self.assertEqual(18, inventory["parity"]["build_stage_exclusions"]["count"])
        self.assertEqual(1361, inventory["parity"]["catalog_complete"]["count"])
        self.assertEqual(planner.FROZEN_CATALOG_SHA256, inventory["catalog_binding"]["sha256"])
        self.assertEqual(planner.FROZEN_CATALOG_BINDING, inventory["catalog_binding"])
        catalog_raw = planner.read_bound_file(ROOT / "test_feedback_catalog.json", "catalog")
        self.assertEqual(planner.CURRENT_CATALOG_SHA256, hashlib.sha256(catalog_raw).hexdigest())
        catalog = planner.strict_json(catalog_raw, "catalog")
        entrypoints = planner._catalog_names(catalog, "entrypoints", "entrypoint_id")
        exclusions = planner._catalog_names(catalog, "build_stage_exclusions", "stage_id")
        self.assertEqual(planner.CURRENT_CATALOG_PARITY["entrypoints"],
                         (len(entrypoints), planner.stable_id_digest(entrypoints)))
        self.assertEqual(planner.CURRENT_CATALOG_PARITY["build_stage_exclusions"],
                         (len(exclusions), planner.stable_id_digest(exclusions)))
        self.assertIn("python::misc/ios/test_quickload_phase.py", entrypoints)
        self.assertFalse(any(identifier.startswith("py:misc/ios/test_quickload_phase.py::")
                             for identifier in inventory["profiles"]["full-release"]["stable_ids"]))
        snapshot = catalog["frozen"]["post_phase2b"]
        self.assertEqual(
            ("openxray.test-feedback-phase2b-snapshot.v1", "shadow-only-non-authoritative"),
            (snapshot["schema"], snapshot["status"]),
        )
        self.assertEqual(
            snapshot["catalog"]["complete"],
            {key: inventory["parity"]["catalog_complete"][key]
             for key in ("count", "sha256")},
        )
        self.assertEqual(
            {name: (entry["stable_ids"]["count"], entry["stage_ids"]["count"])
             for name, entry in snapshot["advisory_profiles"].items()},
            {name: (len(entry["stable_ids"]), len(entry["stage_ids"]))
             for name, entry in inventory["profiles"].items()},
        )

    def test_31_production_authority_is_none_and_false(self) -> None:
        mapping, inventory = assets(); report = planner.build_report(mapping, inventory, raw_manifest([], True), None)
        self.assertEqual((False, False, "NONE", "NONE"), (report["execution_allowed"], report["runtime_claims_allowed"], report["selection_authority"], report["cache_authority"]))
        self.assertEqual(("full-release", True, 1361, 63),
                         (report["profile"], report["fallback"], report["selected_count"], report["stage_count"]))
        mutations = (
            (None, planner.ERROR_MANIFEST_MISSING),
            (b"{", planner.ERROR_MANIFEST_INVALID),
            (raw_manifest([], complete=False), "external manifest is incomplete"),
            (raw_manifest([], complete=True), "external completeness is unauthenticated"),
            (raw_manifest([{"status":"M","path":"src/xrGame/example.cpp"}], complete=True),
             "external completeness is unauthenticated"),
            (raw_manifest([{"status":"D","path":"src/xrGame/example.cpp"}], complete=True),
             "external completeness is unauthenticated"),
        )
        for raw, reason in mutations:
            with self.subTest(reason=reason):
                report = planner.build_report(mapping, inventory, raw, None)
                self.assertEqual(("full-release", True, 1361, False, False, "NONE", "NONE"),
                                 (report["profile"], report["fallback"], report["selected_count"],
                                  report["execution_allowed"], report["runtime_claims_allowed"],
                                  report["selection_authority"], report["cache_authority"]))
                self.assertEqual([reason], report["fallback_reasons"])

    def test_32_static_no_capability_and_no_cli_fixture_api(self) -> None:
        source = (ROOT / "affected_test_planner.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        self.assertNotIn("subprocess", imports)
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse({"walk", "rglob"} & calls)
        self.assertNotIn(".git", source)
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        self.assertNotIn("evaluate_complete_fixture", {node.func.id for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
