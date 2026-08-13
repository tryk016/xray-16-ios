#!/usr/bin/env python3
"""Static Phase 0A inventory for the iOS test feedback work.

This module deliberately does not import or run test entrypoints.  It is an
offline inventory: AST plus the existing shader/link list modes are its only
sources of executable-test information.  It has no authority over selection,
cache, gate execution, stamps, or runtime telemetry.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = Path(__file__).with_name("test_feedback_catalog.json")
SCHEMA = "openxray.test-feedback-catalog.v1"
PYTHON_COMMAND = re.compile(r"^\s*python3\s+(misc/ios/test_[^\s\\]+\.py)")
CASE_RANGE = re.compile(r"^([A-Z]+)-\{index:02d\}$")
EXPECTED_BUILD_STAGE_EXCLUSIONS = frozenset({
    "build-stage::artifact-input-hash-after",
    "build-stage::artifact-input-hash-before",
    "build-stage::artifact-platform-debug-verification",
    "build-stage::bundle-hash",
    "build-stage::cmake-config-hash",
    "build-stage::cmake-configure",
    "build-stage::cpp-policy-compilation",
    "build-stage::dependency-discovery",
    "build-stage::dependency-discovery-linux",
    "build-stage::gate-hash-helper",
    "build-stage::glslang-realpath-probe",
    "build-stage::glslang-toolchain-probe",
    "build-stage::host-compiler-probe",
    "build-stage::input-hashing",
    "build-stage::python-version-probe",
    "build-stage::release-engine-build",
    "build-stage::resource-sync",
    "build-stage::stamp-publication",
})


class CatalogError(RuntimeError):
    pass


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(values: Iterable[str]) -> str:
    """Historical audited-ID algorithm: sorted LF records with a final LF."""
    identifiers = list(values)
    if len(identifiers) != len(set(identifiers)):
        raise CatalogError("duplicate stable test ID")
    if any(not item or "\n" in item or "\r" in item for item in identifiers):
        raise CatalogError("stable test ID is empty or contains CR/LF")
    payload = ("\n".join(sorted(identifiers)) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"catalog unreadable: {error}") from error
    if catalog.get("schema") != SCHEMA:
        raise CatalogError("unexpected catalog schema")
    if catalog.get("selection_authority") != "NONE":
        raise CatalogError("Phase 0A catalog must not select tests")
    if catalog.get("cache_authority") != "NONE":
        raise CatalogError("Phase 0A catalog must not govern cache")
    if not isinstance(catalog.get("entrypoints"), list):
        raise CatalogError("catalog entrypoints missing")
    return catalog


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError as error:
        raise CatalogError(f"path outside repository: {path}") from error


def parse_python_methods(path: Path) -> list[str]:
    """Return unittest methods, including safely resolved local inheritance."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise CatalogError(f"cannot parse {relative(path)}: {error}") from error
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    cache: dict[str, tuple[bool, list[str]]] = {}

    def base_name(node: ast.AST) -> str:
        return ast.unparse(node)

    def resolve(name: str, stack: tuple[str, ...] = ()) -> tuple[bool, list[str]]:
        if name in cache:
            return cache[name]
        if name in stack:
            raise CatalogError(f"cyclic local test inheritance in {relative(path)}: {name}")
        node = classes[name]
        own = [
            child.name for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name.startswith("test_")
        ]
        # Preserve static discovery for a directly declared test-shaped class
        # with no base while still resolving every declared inheritance edge.
        # This keeps the inventory import-free and lets synthetic fixtures prove
        # that parsing cannot trigger module side effects.
        is_test = bool(own) and not node.bases
        inherited: list[str] = []
        for base in node.bases:
            rendered = base_name(base)
            if rendered in ("unittest.TestCase", "TestCase"):
                is_test = True
            elif rendered in ("object",):
                continue
            elif rendered in classes:
                base_is_test, base_methods = resolve(rendered, stack + (name,))
                is_test = is_test or base_is_test
                inherited.extend(base_methods)
            elif rendered not in {"BaseException", "Exception", "RuntimeError"}:
                raise CatalogError(
                    f"unresolved test-contributing base {rendered} for {name} in {relative(path)}"
                )
        methods_by_name = {method: method for method in inherited}
        methods_by_name.update({method: method for method in own})
        result = is_test, list(methods_by_name)
        cache[name] = result
        return result

    methods: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_test, names = resolve(node.name)
        if is_test:
            methods.extend(
                f"py:{relative(path)}::{node.name}.{name}" for name in names
            )
    return methods


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        values: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                values.append(item.value)
            else:
                return None
        return "".join(values)
    return None


def _generated_case_prefix(node: ast.AST) -> str | None:
    value = _constant_string(node)
    if value is not None:
        return value
    if not isinstance(node, ast.JoinedStr) or len(node.values) != 2:
        return None
    prefix, formatted = node.values
    if (not isinstance(prefix, ast.Constant) or not isinstance(prefix.value, str)
            or not isinstance(formatted, ast.FormattedValue)
            or not isinstance(formatted.value, ast.Name)
            or formatted.value.id != "index"):
        return None
    return prefix.value + "{index:02d}"


def _exact_index_fstring(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.JoinedStr) or len(node.values) != 2:
        return None
    prefix, formatted = node.values
    if (not isinstance(prefix, ast.Constant) or not isinstance(prefix.value, str)
            or not isinstance(formatted, ast.FormattedValue)
            or not isinstance(formatted.value, ast.Name)
            or formatted.value.id != "index" or formatted.conversion != -1
            or not isinstance(formatted.format_spec, ast.JoinedStr)
            or len(formatted.format_spec.values) != 1
            or not isinstance(formatted.format_spec.values[0], ast.Constant)
            or formatted.format_spec.values[0].value != "02d"):
        return None
    return prefix.value, formatted.value.id


def parse_archive_generated_methods(path: Path) -> list[str]:
    """Statically reconstruct CASE_BODIES' generated 131-test naming contract."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise CatalogError(f"cannot parse archive cases: {error}") from error
    assignment = next(
        (node for node in tree.body if isinstance(node, ast.Assign)
         and any(isinstance(target, ast.Name) and target.id == "CASE_BODIES"
                 for target in node.targets)),
        None,
    )
    if assignment is None or not isinstance(assignment.value, ast.Dict):
        raise CatalogError("archive CASE_BODIES assignment missing")
    cases: list[str] = []
    for expansion in assignment.value.values:
        if not isinstance(expansion, ast.DictComp) or len(expansion.generators) != 1:
            raise CatalogError("archive CASE_BODIES must contain one-generator dictionary comprehensions")
        key = _exact_index_fstring(expansion.key)
        if key is None or not re.fullmatch(r"[A-Z]+-", key[0]):
            raise CatalogError("archive generated case key must use exact PREFIX-{index:02d}")
        prefix = key[0][:-1]
        generator = expansion.generators[0]
        if (not isinstance(generator.target, ast.Name) or generator.target.id != "index"
                or generator.ifs or generator.is_async):
            raise CatalogError(f"archive generated case iterator invalid for {prefix}")
        range_call = generator.iter
        if (not isinstance(range_call, ast.Call)
                or not isinstance(range_call.func, ast.Name)
                or range_call.func.id != "range" or len(range_call.args) != 2
                or range_call.keywords):
            raise CatalogError(f"archive generated case range missing for {prefix}")
        if (not isinstance(expansion.value, ast.Call)
                or not isinstance(expansion.value.func, ast.Name)
                or expansion.value.func.id != "getattr"
                or len(expansion.value.args) != 2 or expansion.value.keywords
                or not isinstance(expansion.value.args[0], ast.Name)
                or expansion.value.args[0].id != "ArchivePolicyTests"):
            raise CatalogError(f"archive CASE_BODIES getattr contract invalid for {prefix}")
        lookup = _exact_index_fstring(expansion.value.args[1])
        if lookup != (prefix.lower() + "_", "index"):
            raise CatalogError(f"archive CASE_BODIES lookup contract invalid for {prefix}")
        start = ast.literal_eval(range_call.args[0])
        stop = ast.literal_eval(range_call.args[1])
        if not isinstance(start, int) or not isinstance(stop, int) or start != 1 or stop <= start:
            raise CatalogError(f"archive generated case range invalid for {prefix}")
        cases.extend(
            f"py:{relative(path)}::ArchivePolicyTests::{prefix}-{index:02d}"
            for index in range(start, stop)
        )
    expected_make_case = ast.parse(
        "def make_case(case_id: str, body):\n"
        "    def test(self: ArchivePolicyTests) -> None:\n"
        "        body(self)\n"
        "    test.__name__ = 'test_' + case_id.replace('-', '_')\n"
        "    test.__doc__ = case_id\n"
        "    return test\n"
    ).body[0]
    make_case = next(
        (node for node in tree.body
         if isinstance(node, ast.FunctionDef) and node.name == "make_case"), None
    )
    if make_case is None or ast.dump(make_case) != ast.dump(expected_make_case):
        raise CatalogError("archive make_case naming/doc wiring invalid")
    expected_loop = ast.parse(
        "for _case_id, _body in CASE_BODIES.items():\n"
        "    setattr(ArchivePolicyTests, 'test_' + _case_id.replace('-', '_'), "
        "make_case(_case_id, _body))\n"
    ).body[0]
    loop = next(
        (node for node in tree.body
         if isinstance(node, ast.For)
         and ast.unparse(node.iter) == "CASE_BODIES.items()"), None
    )
    if loop is None or ast.dump(loop) != ast.dump(expected_loop):
        raise CatalogError("archive CASE_BODIES loop/setattr wiring invalid")
    if len(cases) != len(set(cases)):
        raise CatalogError("archive generated cases are not unique")
    return cases


def parse_build_check_python(root: Path = ROOT) -> tuple[list[str], list[str]]:
    """Read the authoritative execution order without evaluating shell."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    before_shader, marker, after_shader = text.partition('if [ "$run_shaders" = 1 ]; then')
    if not marker:
        raise CatalogError("build_check shader branch missing")
    shader_block, engine_marker, _after_engine = after_shader.partition('if [ "$run_engine" = 1 ]; then')
    if not engine_marker:
        raise CatalogError("build_check engine branch missing after shader branch")
    unconditional: list[str] = []
    shader_only: list[str] = []
    for line in before_shader.splitlines():
        match = PYTHON_COMMAND.match(line)
        if match:
            unconditional.append(match.group(1))
    for line in shader_block.splitlines():
        match = PYTHON_COMMAND.match(line)
        if match:
            shader_only.append(match.group(1))
    if len(unconditional) != 22 or len(shader_only) != 4:
        raise CatalogError(
            f"build_check Python inventory drift: unconditional={len(unconditional)} shader={len(shader_only)}"
        )
    return unconditional, shader_only


def parse_cpp_executions(root: Path = ROOT) -> list[str]:
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    compiled = re.findall(r"misc/ios/([A-Za-z0-9_]+_test\.cpp)\s+-o\s+\"\$([A-Za-z0-9_]+)\"", text)
    result: list[str] = []
    seen_variables: set[str] = set()
    seen_identifiers: set[str] = set()
    for source, variable in compiled:
        if variable in seen_variables:
            raise CatalogError(f"duplicate C++ compile output variable: {variable}")
        seen_variables.add(variable)
        variant = "asan-ubsan" if "sanitized" in variable else "strict"
        identifier = f"cpp:misc/ios/{source.removesuffix('.cpp')}@{variant}"
        if identifier in seen_identifiers:
            raise CatalogError(f"duplicate C++ source/variant compile: {identifier}")
        seen_identifiers.add(identifier)
        escaped = re.escape(variable)
        if variant == "asan-ubsan":
            executions = re.findall(
                rf'^ASAN_OPTIONS="\$[A-Za-z0-9_]+"\s+UBSAN_OPTIONS=halt_on_error=1\s+'
                rf'"\${escaped}"\s+\\?$',
                text, re.MULTILINE,
            )
        else:
            executions = re.findall(rf'^"\${escaped}"\s+\|\|\s+fail\b.*$', text, re.MULTILINE)
        if len(executions) != 1:
            raise CatalogError(
                f"C++ binary execution mapping drift for {variable}: executions={len(executions)}"
            )
        result.append(identifier)
    if len({item.split("@", 1)[0] for item in result}) != 9 or len(result) != 14:
        raise CatalogError(f"C++ inventory drift: {len(result)} executions")
    return result


def parse_gate_validation_entrypoints(root: Path = ROOT) -> dict[str, list[str]]:
    """Discover non-unittest validation commands from authoritative gate syntax."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    exact_patterns = {
        "validation::python::openal-configured": (
            r'^\s+openal_fields=\$\(python3 "\$REPO_ROOT/misc/ios/openal_provider_contract\.py" configured\s+\\$',
            ["engine", "device", "full", "fast"],
        ),
        "validation::python::openal-artifact": (
            r'^\s+openal_fields=\$\(python3 "\$REPO_ROOT/misc/ios/openal_provider_contract\.py" artifact\s+\\$',
            ["engine", "device", "full", "fast"],
        ),
        "validation::bash-n::run-gate-launcher": (
            r"^bash -n misc/ios/run_gate_logged\.sh\s+\\$", ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::bash-n::capture-tools": (
            r"^bash -n misc/ios/device_lease\.sh misc/ios/input\.sh misc/ios/shot\.sh "
            r"misc/ios/lighting_ab_capture\.sh\s+\\$", ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::shellcheck::capture-tools": (
            r"^shellcheck -x misc/ios/device_lease\.sh misc/ios/input\.sh misc/ios/shot\.sh "
            r"misc/ios/lighting_ab_capture\.sh\s+\\$", ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::python::ui-contract": (
            r"^python3 misc/ios/ui_contract_check\.py\s+\\$", ["engine", "shaders", "device", "full", "fast"]
        ),
        "validation::shader::macro-contract": (
            r"^\s+python3 misc/ios/shadercheck/glsl_es_check\.py --macro-contract\s+\\$",
            ["shaders", "device", "full", "fast"],
        ),
    }
    result: dict[str, list[str]] = {}
    for identifier, (pattern, profiles) in exact_patterns.items():
        matches = re.findall(pattern, text, re.MULTILINE)
        if len(matches) != 1:
            raise CatalogError(f"gate validation entrypoint drift: {identifier} matches={len(matches)}")
        result[identifier] = profiles
    return result


def parse_gate_family_entrypoints(root: Path = ROOT) -> dict[str, list[str]]:
    """Verify test/checker families are actually invoked by build_check."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    patterns = {
        "shell::ui-log-oracle": (
            r"^misc/ios/ui_automation/test_log_oracles\.sh\s+\\$",
            ["engine", "shaders", "device", "full", "fast"],
        ),
        "shader::glsl-es": (
            r"^\s+out=\$\(python3 misc/ios/shadercheck/glsl_es_check\.py\s+\\$",
            ["shaders", "device", "full", "fast"],
        ),
        "shader::link": (
            r"^\s+out=\$\(python3 misc/ios/shadercheck/link_check\.py --strict 2>&1\)\s+\\$",
            ["shaders", "device", "full", "fast"],
        ),
    }
    result: dict[str, list[str]] = {}
    for identifier, (pattern, profiles) in patterns.items():
        matches = re.findall(pattern, text, re.MULTILINE)
        if len(matches) != 1:
            raise CatalogError(f"gate family entrypoint drift: {identifier} matches={len(matches)}")
        result[identifier] = profiles
    return result


def validate_build_stage_exclusions(catalog: dict[str, Any], root: Path = ROOT) -> None:
    """Machine-check non-test build stages without turning them into test IDs."""
    text = (root / "misc/ios/build_check.sh").read_text(encoding="utf-8")
    stages = catalog.get("build_stage_exclusions")
    if not isinstance(stages, list) or not stages:
        raise CatalogError("build-stage exclusions missing")
    identifiers: set[str] = set()
    exclusion_matches: list[re.Match[str]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise CatalogError("build-stage exclusion malformed")
        identifier = stage.get("stage_id")
        pattern = stage.get("anchor_regex")
        reason = stage.get("reason")
        if (not isinstance(identifier, str) or identifier in identifiers
                or not isinstance(pattern, str) or not isinstance(reason, str) or not reason):
            raise CatalogError("build-stage exclusion metadata invalid")
        identifiers.add(identifier)
        try:
            matches = re.findall(pattern, text, re.MULTILINE)
        except re.error as error:
            raise CatalogError(f"build-stage exclusion regex invalid: {identifier}") from error
        expected_matches = stage.get("expected_matches", 1)
        if not isinstance(expected_matches, int) or expected_matches < 1:
            raise CatalogError(f"build-stage exclusion match count invalid: {identifier}")
        if len(matches) != expected_matches:
            raise CatalogError(
                f"build-stage exclusion anchor drift: {identifier} matches={len(matches)}"
            )
        exclusion_matches.extend(re.finditer(pattern, text, re.MULTILINE))
    if identifiers != EXPECTED_BUILD_STAGE_EXCLUSIONS:
        raise CatalogError(
            "build-stage exclusion inventory drift: "
            f"missing={sorted(EXPECTED_BUILD_STAGE_EXCLUSIONS - identifiers)} "
            f"extra={sorted(identifiers - EXPECTED_BUILD_STAGE_EXCLUSIONS)}"
        )

    # Every Python process in the authoritative shell must be either a test,
    # a catalogued validation entrypoint, or one of the explicit build stages.
    covered_lines: set[int] = set()
    for match in exclusion_matches:
        if "python3" in match.group(0):
            position = match.start() + match.group(0).index("python3")
            covered_lines.add(text.count("\n", 0, position) + 1)
    for pattern in (
        r"^\s*python3\s+misc/ios/test_[^\s\\]+\.py",
        r'^\s+openal_fields=\$\(python3 "\$REPO_ROOT/misc/ios/openal_provider_contract\.py" (?:configured|artifact)',
        r"^python3 misc/ios/ui_contract_check\.py",
        r"^\s+python3 misc/ios/shadercheck/glsl_es_check\.py --macro-contract",
        r"^\s+out=\$\(python3 misc/ios/shadercheck/(?:glsl_es_check|link_check)\.py",
    ):
        for match in re.finditer(pattern, text, re.MULTILINE):
            position = match.start() + match.group(0).index("python3")
            covered_lines.add(text.count("\n", 0, position) + 1)
    python_lines = {
        number for number, line in enumerate(text.splitlines(), 1)
        if "python3" in line and not line.lstrip().startswith("#")
    }
    if python_lines != covered_lines:
        raise CatalogError(
            f"unclassified Python gate command lines: {sorted(python_lines - covered_lines)}; "
            f"stale anchors: {sorted(covered_lines - python_lines)}"
        )


def parse_shell_cases(root: Path = ROOT) -> list[str]:
    text = (root / "misc/ios/ui_automation/test_log_oracles.sh").read_text(encoding="utf-8")
    names = re.findall(r"^\s*expect_(?:pass|fail)\s+([A-Za-z0-9_-]+)", text, re.MULTILINE)
    identifiers = [
        f"sh:misc/ios/ui_automation/test_log_oracles.sh::{name}"
        for name in names
    ]
    if len(identifiers) != 12 or len(set(identifiers)) != len(identifiers):
        raise CatalogError(f"shell oracle inventory drift: {len(identifiers)} cases")
    return identifiers


def parse_xctest_cases(root: Path = ROOT) -> list[str]:
    path = root / "misc/ios/ui_automation/OpenXRayUITests.m"
    text = path.read_text(encoding="utf-8")
    methods = re.findall(r"^-\s*\(void\)(test[A-Za-z0-9_]+)", text, re.MULTILINE)
    identifiers = [
        f"xctest:OpenXRayUITests/OpenXRayUITests/{method}"
        for method in methods
    ]
    if len(identifiers) != 4 or len(set(identifiers)) != len(identifiers):
        raise CatalogError(f"XCTest inventory drift: {len(identifiers)} cases")
    return identifiers


def command_json(path: Path, *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(path), *arguments],
        cwd=ROOT, check=False, text=True, capture_output=True,
    )
    if completed.returncode != 0:
        raise CatalogError(f"{relative(path)} {' '.join(arguments)} failed: {completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CatalogError(f"{relative(path)} did not emit JSON") from error


def parse_shader_cases(root: Path = ROOT) -> tuple[list[str], list[str], list[str]]:
    shader = command_json(root / "misc/ios/shadercheck/glsl_es_check.py", "--list-json")
    links = command_json(root / "misc/ios/shadercheck/link_check.py", "--list-json")
    baseline = shader.get("baseline")
    variants = shader.get("variants")
    link_ids = links.get("links")
    if not all(isinstance(item, list) and all(isinstance(value, str) for value in item)
               for item in (baseline, variants, link_ids)):
        raise CatalogError("shader list schema drift")
    if len(baseline) != 279 or len(variants) != 17 or len(link_ids) != 137:
        raise CatalogError(
            f"shader inventory drift: baseline={len(baseline)} variants={len(variants)} links={len(link_ids)}"
        )
    return baseline, variants, link_ids


def validate_shader_shared_specs(path: Path) -> None:
    """Prove list and execution paths reference the same variant spec objects."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise CatalogError(f"cannot parse shared shader specs: {error}") from error
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    for name in ("build_ssr_profiles", "list_cases_json", "main"):
        if name not in functions:
            raise CatalogError(f"shared shader spec function missing: {name}")

    def call_count(function: str, called: str) -> int:
        return sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == called
            for node in ast.walk(functions[function])
        )

    def name_count(function: str, referenced: str) -> int:
        return sum(
            isinstance(node, ast.Name) and node.id == referenced
            for node in ast.walk(functions[function])
        )

    if call_count("list_cases_json", "build_ssr_profiles") != 1:
        raise CatalogError("SSR list path does not consume build_ssr_profiles")
    if call_count("main", "build_ssr_profiles") != 1:
        raise CatalogError("SSR execution path does not consume build_ssr_profiles")
    for shared in ("LOW_SETTINGS_TARGETS", "SSAO_PROFILES"):
        if name_count("list_cases_json", shared) < 1 or name_count("main", shared) < 1:
            raise CatalogError(f"shader list/execution do not share {shared}")


def profile_ids(root: Path = ROOT) -> dict[str, list[str]]:
    unconditional, shader_only = parse_build_check_python(root)
    python_ids: list[str] = []
    for item in unconditional + shader_only:
        path = root / item
        if item.endswith("test_archive_completed_artifacts.py"):
            python_ids.extend(parse_archive_generated_methods(path))
        else:
            python_ids.extend(parse_python_methods(path))
    ci = parse_python_methods(root / "misc/ios/test_ios_ci_contract.py")
    host = (
        parse_python_methods(root / "misc/ios/test_cleanup_simulator_work.py")
        + parse_python_methods(root / "misc/ios/ui_automation/test_prepare_scheme.py")
    )
    phase0 = parse_python_methods(root / "misc/ios/test_test_feedback.py")
    baseline, variants, links = parse_shader_cases(root)
    return {
        "central-python": python_ids,
        "ci-contract": ci,
        "host-infra": host,
        "phase0-tooling": phase0,
        "cpp": parse_cpp_executions(root),
        "shell": parse_shell_cases(root),
        "shader-baseline": baseline,
        "shader-variants": variants,
        "shader-links": links,
        "xctest": parse_xctest_cases(root),
        "retail-simulator": [
            item for item in python_ids
            if "test_retail_simulator.py" in item
        ],
    }


def catalog_entrypoint_ids(catalog: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for entry in catalog["entrypoints"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("entrypoint_id"), str):
            raise CatalogError("catalog entrypoint lacks entrypoint_id")
        entrypoint = entry["entrypoint_id"]
        if entrypoint in values:
            raise CatalogError(f"duplicate catalog entrypoint: {entrypoint}")
        values.add(entrypoint)
        required = ("kind", "selected_profiles", "labels", "resources",
                    "timeout_seconds", "timeout_policy", "explicit_inputs",
                    "input_mapping_complete")
        if any(key not in entry for key in required):
            raise CatalogError(f"catalog entrypoint incomplete: {entrypoint}")
        if entry["timeout_policy"] != "report-only":
            raise CatalogError(f"catalog entrypoint timeout must be report-only: {entrypoint}")
        if entry["input_mapping_complete"] is not False:
            raise CatalogError(f"Phase 0A dependency mapping is not proven complete: {entrypoint}")
        if not entry["labels"] or not entry["resources"] or not entry["explicit_inputs"]:
            raise CatalogError(f"catalog entrypoint metadata is empty: {entrypoint}")
        for field in ("selected_profiles", "labels", "resources"):
            values_for_field = entry[field]
            if (not isinstance(values_for_field, list)
                    or any(not isinstance(value, str) or not value for value in values_for_field)
                    or len(values_for_field) != len(set(values_for_field))):
                raise CatalogError(f"catalog {field} must be unique non-empty strings: {entrypoint}")
        unknown_profiles = set(entry["selected_profiles"]) - {
            "engine", "shaders", "device", "full", "fast",
        }
        if unknown_profiles:
            raise CatalogError(f"catalog profile is not an existing gate profile: {entrypoint}")
    return values


def cpp_entrypoint_ids(root: Path = ROOT) -> set[str]:
    return {identifier.rsplit("@", 1)[0] for identifier in parse_cpp_executions(root)}


def expected_entrypoints(root: Path = ROOT) -> set[str]:
    unconditional, shader_only = parse_build_check_python(root)
    return {
        *(f"python::{path}" for path in unconditional + shader_only),
        "python::misc/ios/test_ios_ci_contract.py",
        "python::misc/ios/test_cleanup_simulator_work.py",
        "python::misc/ios/ui_automation/test_prepare_scheme.py",
        "python::misc/ios/test_test_feedback.py",
        *cpp_entrypoint_ids(root),
        *parse_gate_family_entrypoints(root),
        "xctest::OpenXRayUITests",
        *parse_gate_validation_entrypoints(root),
    }


def audited_digest(name: str, ids: list[str], catalog: dict[str, Any]) -> str:
    """Freshly reproduce and compare the exact historical audited-ID digest."""
    frozen = catalog["frozen"]["groups"].get(name)
    if not isinstance(frozen, dict):
        raise CatalogError(f"missing frozen group: {name}")
    actual_count = len(ids)
    actual_digest = digest(ids)
    audit = frozen.get("audited_sha256")
    if not isinstance(audit, str) or not re.fullmatch(r"[0-9a-f]{64}", audit):
        raise CatalogError(f"invalid audited digest for {name}")
    if frozen.get("count") != actual_count or audit != actual_digest:
        raise CatalogError(
            f"audited inventory drift for {name}: count={actual_count} sha256={actual_digest}"
        )
    return actual_digest


def group_report(name: str, ids: list[str], catalog: dict[str, Any]) -> dict[str, Any]:
    frozen = catalog["frozen"]["groups"].get(name)
    if frozen is None:
        return {"count": len(ids)}
    return {"count": len(ids), "audited_sha256": audited_digest(name, ids, catalog)}


def validate(catalog: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    if catalog.get("profiles") != ["engine", "shaders", "device", "full", "fast"]:
        raise CatalogError("catalog profile vocabulary drift")
    validate_build_stage_exclusions(catalog, root)
    validate_shader_shared_specs(root / "misc/ios/shadercheck/glsl_es_check.py")
    actual_entrypoints = catalog_entrypoint_ids(catalog)
    expected_records = expected_entrypoints(root)
    if actual_entrypoints != expected_records:
        missing = sorted(expected_records - actual_entrypoints)
        extra = sorted(actual_entrypoints - expected_records)
        raise CatalogError(f"catalog entrypoint drift: missing={missing} extra={extra}")
    groups = profile_ids(root)
    expected = {
        "central-python": 692,
        "ci-contract": 79,
        "host-infra": 10,
        "cpp": 14,
        "shell": 12,
        "shader-baseline": 279,
        "shader-variants": 17,
        "shader-links": 137,
        "xctest": 4,
        "retail-simulator": 102,
    }
    if {name: len(groups[name]) for name in expected} != expected:
        raise CatalogError("inventory count drift")
    if not groups["phase0-tooling"]:
        raise CatalogError("Phase 0 tooling tests missing")
    all_python = groups["central-python"] + groups["ci-contract"] + groups["host-infra"]
    if len(all_python) != 781:
        raise CatalogError(f"legacy Python total drift: {len(all_python)}")
    baseline = catalog["frozen"].get("baseline")
    if baseline != {
        "central_python_files": 26, "central_python_cases": 692,
        "ci_files": 1, "ci_cases": 79,
        "preexisting_host_infra_files": 2, "preexisting_host_infra_cases": 10,
        "legacy_python_files": 29, "legacy_python_cases": 781,
        "retail_simulator_cases": 102,
    }:
        raise CatalogError("frozen legacy baseline metadata drift")
    catalog_by_id = {entry["entrypoint_id"]: entry for entry in catalog["entrypoints"]}
    unconditional, shader_only = parse_build_check_python(root)
    for path in unconditional + shader_only:
        identifiers = (
            parse_archive_generated_methods(root / path)
            if path.endswith("test_archive_completed_artifacts.py")
            else parse_python_methods(root / path)
        )
        if catalog_by_id[f"python::{path}"].get("expected_case_count") != len(identifiers):
            raise CatalogError(f"Python entrypoint case count drift: {path}")
    for path in (
        "misc/ios/test_ios_ci_contract.py",
        "misc/ios/test_cleanup_simulator_work.py",
        "misc/ios/ui_automation/test_prepare_scheme.py",
    ):
        if catalog_by_id[f"python::{path}"].get("expected_case_count") != len(
                parse_python_methods(root / path)):
            raise CatalogError(f"non-gate Python entrypoint case count drift: {path}")
    if catalog_by_id["python::misc/ios/test_test_feedback.py"].get(
            "expected_case_count") != len(groups["phase0-tooling"]):
        raise CatalogError("Phase 0 tooling case count drift")
    for entrypoint in (
        "python::misc/ios/test_ios_ci_contract.py",
        "python::misc/ios/test_cleanup_simulator_work.py",
        "python::misc/ios/ui_automation/test_prepare_scheme.py",
        "python::misc/ios/test_test_feedback.py",
        "xctest::OpenXRayUITests",
    ):
        if catalog_by_id[entrypoint]["selected_profiles"] != []:
            raise CatalogError(f"Phase 0A non-selected entrypoint was selected: {entrypoint}")
    for entrypoint in cpp_entrypoint_ids(root):
        variants = sorted(
            identifier.rsplit("@", 1)[1]
            for identifier in groups["cpp"] if identifier.startswith(entrypoint + "@")
        )
        if sorted(catalog_by_id[entrypoint].get("variants", [])) != variants:
            raise CatalogError(f"C++ variants drift: {entrypoint}")
        if catalog_by_id[entrypoint].get("expected_case_count") != len(variants):
            raise CatalogError(f"C++ execution count drift: {entrypoint}")
    expected_family_counts = {
        "shell::ui-log-oracle": len(groups["shell"]),
        "shader::glsl-es": len(groups["shader-baseline"]) + len(groups["shader-variants"]),
        "shader::link": len(groups["shader-links"]),
        "xctest::OpenXRayUITests": len(groups["xctest"]),
    }
    for entrypoint, count in expected_family_counts.items():
        if catalog_by_id[entrypoint].get("expected_case_count") != count:
            raise CatalogError(f"family entrypoint case count drift: {entrypoint}")
    all_five = ["engine", "shaders", "device", "full", "fast"]
    shader_four = ["shaders", "device", "full", "fast"]
    for path in unconditional:
        if catalog_by_id[f"python::{path}"]["selected_profiles"] != all_five:
            raise CatalogError(f"unconditional Python profile drift: {path}")
    for path in shader_only:
        if catalog_by_id[f"python::{path}"]["selected_profiles"] != shader_four:
            raise CatalogError(f"shader Python profile drift: {path}")
    for entrypoint in cpp_entrypoint_ids(root):
        if catalog_by_id[entrypoint]["selected_profiles"] != all_five:
            raise CatalogError(f"unconditional process profile drift: {entrypoint}")
    for entrypoint, profiles in parse_gate_family_entrypoints(root).items():
        if catalog_by_id[entrypoint]["selected_profiles"] != profiles:
            raise CatalogError(f"gate family profile drift: {entrypoint}")
    for entrypoint, profiles in parse_gate_validation_entrypoints(root).items():
        if catalog_by_id[entrypoint]["selected_profiles"] != profiles:
            raise CatalogError(f"validation entrypoint profile drift: {entrypoint}")
    report = {
        "schema": SCHEMA,
        "selection_authority": "NONE",
        "cache_authority": "NONE",
        "groups": {
            name: group_report(name, ids, catalog)
            for name, ids in groups.items()
        },
        "legacy_python": {
            "count": len(all_python),
            "audited_sha256": audited_digest("legacy-python", all_python, catalog),
        },
        "selected": {
            "ci-contract": False,
            "host-infra": False,
            "xctest": False,
            "reason": "Phase 0A inventory only; existing gate selection remains authoritative",
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "json"))
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    args = parser.parse_args()
    try:
        report = validate(read_catalog(args.catalog))
    except CatalogError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if args.command == "json":
        print(canonical(report))
    else:
        for name, values in report["groups"].items():
            suffix = f" {values['audited_sha256']}" if "audited_sha256" in values else ""
            print(f"PASS: {name} {values['count']}{suffix}")
        print(f"PASS: legacy-python {report['legacy_python']['count']} "
              f"{report['legacy_python']['audited_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
