#!/usr/bin/env python3
"""Fail-closed, stdlib-only contract tests for the iOS GitHub Actions workflow.

This binds cache provenance to the reviewed workflow inputs and runner/toolchain
identity. It does not claim cache-poisoning resistance: the default ios-port
branch is currently unprotected and remains an external trust-boundary limit.
"""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/ios.yml"

# sha256-v1-exact-utf8-bytes hashes the complete reviewed workflow as exact
# UTF-8 bytes, including comments and line endings. Intentional workflow changes
# require updating this digest and the semantic contract together under review.
WORKFLOW_DIGEST_ALGORITHM = "sha256-v1-exact-utf8-bytes"
EXPECTED_WORKFLOW_SHA256 = "47885fe333f741eb5e1438c3bc5a5de7c1ee552d8c6635bce8317727e01c389b"
WORKFLOW_DIGEST_ERROR = (
    f"complete workflow must match reviewed {WORKFLOW_DIGEST_ALGORITHM} digest"
)

ACTION_MAP = {
    "actions/checkout": ("11d5960a326750d5838078e36cf38b85af677262", "# v4.4.0", 5),
    "actions/upload-artifact": ("ea165f8d65b6e75b540449e92b4886f43607fa02", "# v4.6.2", 4),
    "actions/download-artifact": ("d3f86a106a0bac45b974a628896c90dbdf5c8093", "# v4.3.0", 2),
    "actions/cache": ("0057852bfaa89a56745cba8c7296529d2fc39830", "# v4.3.0", 1),
}

DEPS_INPUTS = (
    ".github/workflows/ios.yml",
    "cmake/ios/deps/CMakeLists.txt",
    "cmake/toolchains/ios.toolchain.cmake",
    "cmake/ios/theora/CMakeLists.txt",
    "cmake/ios/apply_sdl2_scene_patch.py",
    "cmake/ios/sdl2_scene_patch_manifest.json",
    "cmake/ios/patches/sdl2-2.32.10-uikit-scene.patch",
)

AMBIENT_OVERRIDES = (
    "DEVELOPER_DIR", "SDKROOT", "CC", "CXX", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS",
    "CMAKE_GENERATOR", "_BUILD_LIBTOOL", "_CMAKE_CXX_COMPILER", "_CMAKE_C_COMPILER",
    "_CMAKE_INSTALL_NAME_TOOL", "_CMAKE_OSX_SYSROOT_INT", "_IOS_TOOLCHAIN_HAS_RUN", "_PLATFORM",
    "_SDK_VERSION", "_XCODE_VERSION_INT",
)

FINGERPRINT_FIELDS = (
    "ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH", "sw_vers", "xcode-select-path",
    "xcodebuild-version", "matrix-sdk", "sdk-path", "sdk-version", "sdk-build", "clang-path",
    "libtool-path", "install-name-tool-path", "clang-version", "cmake-path", "cmake-version",
    "python3-path", "python3-version", "generator", "make-path", "make-version",
)

CACHE_KEY = (
    "openxray-ios-deps-v1-${{ github.ref_name }}-${{ runner.os }}-${{ runner.arch }}-"
    "${{ matrix.sdk }}-${{ matrix.platform }}-ios16.4-bitcodeOFF-Release-UnixMakefiles-"
    "${{ steps.deps-toolchain.outputs.sha256 }}-${{ steps.deps-inputs.outputs.sha256 }}"
)

DEPS_STEP_ORDER = (
    "Validate dependency cache inputs",
    "Fingerprint dependency toolchain",
    "Cache dependency prefix",
    "Cross-build dependency superbuild (${{ matrix.platform }})",
    "Verify static libs are arm64",
    "Upload deps prefix",
)

CANONICAL_BLOCK_SCALARS = {"|", "|-", "|+", ">", ">-", ">+"}
TRIGGER_KEYS = {
    "push", "workflow_dispatch", "pull_request", "pull_request_target", "workflow_run",
    "schedule", "repository_dispatch",
}
SECURITY_KEYS = {
    "on", "permissions", "jobs", "steps", "uses", "run", "if", "with", "key", "path",
    "continue-on-error", *TRIGGER_KEYS,
}
CACHE_PATH = "build/ios-prefix-${{ matrix.sdk }}"
BUILD_CACHE_MISS_IF = "${{ steps.deps-cache.outputs.cache-hit != 'true' }}"
SHADER_CONTRACT_COMMAND = "python3 misc/ios/test_ios_ci_contract.py"

TOP_LEVEL_FIELDS = ("name", "on", "permissions", "jobs")
DEPS_JOB_FIELDS = ("name", "runs-on", "timeout-minutes", "strategy", "steps")
DEPS_STEP_FIELDS = (
    ("uses",),
    ("name", "id", "shell", "run"),
    ("name", "id", "shell", "run"),
    ("name", "id", "uses", "with"),
    ("name", "if", "run"),
    ("name", "run"),
    ("name", "uses", "with"),
)
SHADER_JOB_FIELDS = ("name", "runs-on", "timeout-minutes", "steps")
SHADER_STEP_HEADERS = (
    "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0",
    "      - name: Validate iOS CI contract",
    "      - name: Build pinned glslang 16.4.0",
    "      - name: Validate GL shaders as GLSL ES 3.00",
)
SHADER_STEP_FIELDS = (("uses",), ("name", "run"), ("name", "run"), ("name", "run"))

EXPECTED_DEPS_INPUTS_SCRIPT = textwrap.dedent(
    r"""
    set -euo pipefail
    inputs=(
      ".github/workflows/ios.yml"
      "cmake/ios/deps/CMakeLists.txt"
      "cmake/toolchains/ios.toolchain.cmake"
      "cmake/ios/theora/CMakeLists.txt"
      "cmake/ios/apply_sdl2_scene_patch.py"
      "cmake/ios/sdl2_scene_patch_manifest.json"
      "cmake/ios/patches/sdl2-2.32.10-uikit-scene.patch"
    )
    for path in "${inputs[@]}"; do
      if [ ! -f "$path" ] || [ -L "$path" ]; then
        echo "::error::dependency cache input must be a regular non-symlink file: $path"
        exit 1
      fi
    done
    sha256="${{ hashFiles('.github/workflows/ios.yml', 'cmake/ios/deps/CMakeLists.txt', 'cmake/toolchains/ios.toolchain.cmake', 'cmake/ios/theora/CMakeLists.txt', 'cmake/ios/apply_sdl2_scene_patch.py', 'cmake/ios/sdl2_scene_patch_manifest.json', 'cmake/ios/patches/sdl2-2.32.10-uikit-scene.patch') }}"
    if ! [[ "$sha256" =~ ^[0-9a-f]{64}$ ]]; then
      echo "::error::hashFiles did not produce a lowercase SHA-256 for all dependency cache inputs"
      exit 1
    fi
    echo "dependency-inputs-sha256=$sha256"
    echo "sha256=$sha256" >> "$GITHUB_OUTPUT"
    """
).strip("\n")

EXPECTED_DEPS_TOOLCHAIN_SCRIPT = textwrap.dedent(
    r"""
    set -euo pipefail
    ambient_overrides=(
      DEVELOPER_DIR SDKROOT CC CXX CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CMAKE_GENERATOR
      _BUILD_LIBTOOL _CMAKE_CXX_COMPILER _CMAKE_C_COMPILER _CMAKE_INSTALL_NAME_TOOL
      _CMAKE_OSX_SYSROOT_INT _IOS_TOOLCHAIN_HAS_RUN _PLATFORM _SDK_VERSION _XCODE_VERSION_INT
    )
    for variable in "${ambient_overrides[@]}"; do
      if [ "${!variable+x}" = x ]; then
        echo "::error::ambient toolchain override is forbidden: $variable"
        exit 1
      fi
    done
    required_runner_fields=(ImageOS ImageVersion RUNNER_OS RUNNER_ARCH)
    for variable in "${required_runner_fields[@]}"; do
      if [ -z "${!variable-}" ]; then
        echo "::error::required runner field is empty: $variable"
        exit 1
      fi
    done
    fingerprint="$(mktemp)"
    trap 'rm -f "$fingerprint"' EXIT
    fail() {
      echo "::error::$1"
      exit 1
    }
    require_value() {
      [ -n "$2" ] || fail "fingerprint field is empty: $1"
      printf '%s=%s\n' "$1" "$2" >> "$fingerprint"
    }
    require_command_output() {
      local name="$1"
      shift
      local value
      value="$("$@")" || fail "fingerprint command failed: $name"
      require_value "$name" "$value"
    }
    require_value "ImageOS" "$ImageOS"
    require_value "ImageVersion" "$ImageVersion"
    require_value "RUNNER_OS" "$RUNNER_OS"
    require_value "RUNNER_ARCH" "$RUNNER_ARCH"
    require_command_output "sw_vers" /usr/bin/sw_vers
    require_command_output "xcode-select-path" /usr/bin/xcode-select -p
    require_command_output "xcodebuild-version" /usr/bin/xcodebuild -version
    sdk="${{ matrix.sdk }}"
    require_value "matrix-sdk" "$sdk"
    require_command_output "sdk-path" /usr/bin/xcrun --sdk "$sdk" --show-sdk-path
    require_command_output "sdk-version" /usr/bin/xcrun --sdk "$sdk" --show-sdk-version
    require_command_output "sdk-build" /usr/bin/xcrun --sdk "$sdk" --show-sdk-build-version
    clang_path="$(/usr/bin/xcrun --sdk "$sdk" --find clang)" || fail "clang path lookup failed"
    libtool_path="$(/usr/bin/xcrun --sdk "$sdk" --find libtool)" || fail "libtool path lookup failed"
    install_name_tool_path="$(/usr/bin/xcrun --sdk "$sdk" --find install_name_tool)" || fail "install_name_tool path lookup failed"
    require_value "clang-path" "$clang_path"
    require_value "libtool-path" "$libtool_path"
    require_value "install-name-tool-path" "$install_name_tool_path"
    require_command_output "clang-version" "$clang_path" --version
    cmake_path="$(command -v cmake)" || fail "cmake path lookup failed"
    python3_path="$(command -v python3)" || fail "python3 path lookup failed"
    require_value "cmake-path" "$cmake_path"
    require_command_output "cmake-version" "$cmake_path" --version
    require_value "python3-path" "$python3_path"
    require_command_output "python3-version" "$python3_path" --version
    generator="Unix Makefiles"
    "$cmake_path" --help | /usr/bin/grep -F "Unix Makefiles" >/dev/null \
      || fail "required CMake generator is unavailable: Unix Makefiles"
    require_value "generator" "$generator"
    [ -x /usr/bin/make ] || fail "/usr/bin/make is unavailable"
    require_value "make-path" "/usr/bin/make"
    require_command_output "make-version" /usr/bin/make --version
    cat "$fingerprint"
    sha256="$(/usr/bin/shasum -a 256 "$fingerprint" | /usr/bin/awk '{print $1}')"
    if ! [[ "$sha256" =~ ^[0-9a-f]{64}$ ]]; then
      fail "toolchain fingerprint did not produce a lowercase SHA-256"
    fi
    echo "dependency-toolchain-sha256=$sha256"
    echo "sha256=$sha256" >> "$GITHUB_OUTPUT"
    """
).strip("\n")


def job_block(workflow: str, job: str) -> str:
    start = re.search(rf"(?m)^  {re.escape(job)}:\n", workflow)
    if not start:
        return ""
    end = re.search(r"(?m)^  [A-Za-z0-9_-]+:\n", workflow[start.end():])
    return workflow[start.start():start.end() + end.start()] if end else workflow[start.start():]


def strip_yaml_comment(line: str) -> str:
    """Remove a YAML comment without treating quoted # characters as comments."""
    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if quote == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
        elif quote == "'":
            if character == quote:
                quote = ""
        elif character in ("'", '"'):
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def structural_yaml_lines(text: str) -> list[tuple[int, str, str]]:
    """Return non-comment YAML lines, excluding block-scalar payload text."""
    result: list[tuple[int, str, str]] = []
    scalar_indent: int | None = None
    for index, raw in enumerate(text.splitlines()):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if scalar_indent is not None:
            if indent > scalar_indent:
                continue
            scalar_indent = None
        code = strip_yaml_comment(raw)
        if not code.strip():
            continue
        result.append((index, raw, code))
        if re.search(r":\s*[>|][^\s#]*\s*$", code):
            scalar_indent = indent
    return result


def step_items(job: str) -> list[str]:
    steps = re.search(r"(?m)^    steps:\n", job)
    if not steps:
        return []
    body = job[steps.end():]
    starts = list(re.finditer(r"(?m)^      -(?:\s|$).*$", body))
    return [
        body[start.start():(starts[index + 1].start() if index + 1 < len(starts) else len(body))]
        for index, start in enumerate(starts)
    ]


def named_step(job: str, name: str) -> str:
    prefix = f"      - name: {name}\n"
    matches = [item for item in step_items(job) if item.startswith(prefix)]
    return matches[0] if len(matches) == 1 else ""


def workflow_job_blocks(workflow: str) -> list[tuple[str, str]]:
    jobs = re.search(r"(?m)^jobs:\n", workflow)
    if not jobs:
        return []
    names = re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\n", workflow[jobs.end():])
    return [(name, job_block(workflow, name)) for name in names]


def step_field_values(step: str, field: str) -> list[str]:
    pattern = re.compile(rf"^        {re.escape(field)}:\s*(.*)$")
    values: list[str] = []
    for _, _, code in structural_yaml_lines(step):
        match = pattern.match(code)
        if match:
            values.append(match.group(1).strip())
    return values


def direct_mapping_keys(text: str, indent: int, sequence_item: bool = False) -> tuple[str, ...]:
    if sequence_item:
        pattern = re.compile(rf"^ {{{indent}}}- ([A-Za-z0-9_-]+)\s*:")
    else:
        pattern = re.compile(rf"^ {{{indent}}}([A-Za-z0-9_-]+)\s*:")
    keys: list[str] = []
    for _, _, code in structural_yaml_lines(text):
        match = pattern.match(code)
        if match:
            keys.append(match.group(1))
    return tuple(keys)


def step_direct_keys(step: str) -> tuple[str, ...]:
    keys = list(direct_mapping_keys(step, 6, sequence_item=True))
    keys.extend(direct_mapping_keys(step, 8))
    return tuple(keys)


def normalized_block_payload(step: str, field: str) -> str | None:
    lines = step.splitlines()
    headers = [
        (index, code) for index, _, code in structural_yaml_lines(step)
        if re.match(rf"^        {re.escape(field)}\s*:", code)
    ]
    if len(headers) != 1 or headers[0][1] != f"        {field}: |":
        return None
    payload: list[str] = []
    for raw in lines[headers[0][0] + 1:]:
        if raw.strip() and len(raw) - len(raw.lstrip(" ")) <= 8:
            break
        payload.append(raw)
    while payload and not payload[-1].strip():
        payload.pop()
    if not payload:
        return None
    nonempty_indents = [len(line) - len(line.lstrip(" ")) for line in payload if line.strip()]
    if not nonempty_indents or min(nonempty_indents) != 10:
        return None
    return "\n".join(line[10:] if line.strip() else "" for line in payload)


def decode_yaml_unicode_escapes(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        digits = next(group for group in match.groups() if group is not None)
        try:
            return chr(int(digits, 16))
        except (ValueError, OverflowError):
            return match.group(0)

    return re.sub(
        r"\\x([0-9A-Fa-f]{2})|\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})",
        replace,
        text,
    )


def supported_sensitive_field(key: str, code: str) -> bool:
    if key == "on":
        return code == "on:"
    if key == "jobs":
        return code == "jobs:"
    if key == "permissions":
        return code in ("permissions:", "    permissions:")
    if key == "steps":
        return code == "    steps:"
    if key in TRIGGER_KEYS:
        return code == f"  {key}:"
    if key == "uses":
        return bool(re.fullmatch(r"(?:      - |        )uses:\s*[^\s{}\[\]]+", code))
    if key == "run":
        match = re.fullmatch(r"        run:\s*(.+)", code)
        if not match:
            return False
        value = match.group(1).strip()
        return value in CANONICAL_BLOCK_SCALARS or not re.match(r"[>|{\[]", value)
    if key == "if":
        match = re.fullmatch(r"(?:    |        )if:\s*(.+)", code)
        return bool(match and not re.match(r"[>|{\[]", match.group(1).strip()))
    if key == "with":
        return code == "        with:"
    if key == "key":
        return code == "          key: >-"
    if key == "path":
        match = re.fullmatch(r"          path:\s*(.+)", code)
        return bool(match and not re.match(r"[>|{\[]", match.group(1).strip()))
    return False


def canonical_yaml_subset_errors(workflow: str) -> list[str]:
    """Reject YAML spellings outside the small structural subset audited here."""
    errors: list[str] = []
    key_alternation = "|".join(sorted((re.escape(key) for key in SECURITY_KEYS), key=len, reverse=True))
    quoted_key = re.compile(
        r'''(?<![A-Za-z0-9_-])(?:"(?:\\.|[^"\\])*"|'(?:''|[^'])*')\s*:'''
    )
    explicit_key = re.compile(
        r'''^\s*(?:-\s+)?\?\s*(?:"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[^\s:]+)(?:\s*:|\s*$)'''
    )
    lexical_key = re.compile(rf"(?<![A-Za-z0-9_-])({key_alternation})\s*:")

    structural = structural_yaml_lines(workflow)
    if direct_mapping_keys(workflow, 0) != TOP_LEVEL_FIELDS:
        errors.append("workflow top-level fields must match the exact reviewed allowlist")
    for line_number, raw, code in structural:
        location = line_number + 1
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            errors.append(f"canonical YAML subset forbids tab indentation at line {location}")
        if quoted_key.search(code):
            errors.append(f"canonical YAML subset forbids quoted structural keys at line {location}")
        if explicit_key.search(code):
            errors.append(f"canonical YAML subset forbids explicit mapping keys at line {location}")
        scalar = re.search(r":\s*([>|][^\s#]*)\s*$", code)
        if scalar and scalar.group(1) not in CANONICAL_BLOCK_SCALARS:
            errors.append(
                f"canonical YAML subset forbids block scalar indicator {scalar.group(1)} at line {location}"
            )
        masked = re.sub(r"\$\{\{.*?}}", "EXPRESSION", code)
        if re.search(r"(?:^|[\s:\[{},])(?:[&*][A-Za-z0-9_-]+|![^\s]+)", masked):
            errors.append(f"canonical YAML subset forbids anchors, aliases and tags at line {location}")
        if re.search(r"(?<![A-Za-z0-9_-])<<\s*:", masked):
            errors.append(f"canonical YAML subset forbids merge keys at line {location}")
        if re.match(r"^\s*-\s*[\[{]", masked):
            errors.append(f"canonical YAML subset forbids flow-style sequence items at line {location}")
        if re.search(r"(?<![A-Za-z0-9_-])steps\s*:\s*[\[{]", masked):
            errors.append(f"canonical YAML subset forbids flow-style steps collections at line {location}")
        for key in lexical_key.findall(code):
            if not supported_sensitive_field(key, code):
                errors.append(
                    f"canonical YAML subset requires the supported {key} field form at line {location}"
                )

    jobs = workflow_job_blocks(workflow)
    parsed_step_items = 0
    parsed_run_fields = 0
    for job_name, block in jobs:
        steps_headers = [
            code for _, _, code in structural_yaml_lines(block)
            if re.search(r"(?<![A-Za-z0-9_-])steps\s*:", code)
        ]
        if steps_headers != ["    steps:"]:
            errors.append(f"canonical YAML subset requires one block steps collection in job {job_name}")
            continue
        items = step_items(block)
        parsed_step_items += len(items)
        for item in items:
            first_line = item.splitlines()[0]
            if not re.match(r"^      - (?:name|uses):\s*\S", first_line):
                errors.append(f"canonical YAML subset requires canonical block step items in job {job_name}")
                continue
            uses_count = len(re.findall(r"(?m)^(?:      - |        )uses:\s*", item))
            run_count = len(step_field_values(item, "run"))
            parsed_run_fields += run_count
            if uses_count + run_count != 1:
                errors.append(f"canonical YAML subset requires exactly one uses or run field per step in job {job_name}")
            for field in ("uses", "run", "if", "with"):
                if field == "uses":
                    count = uses_count
                else:
                    count = len(step_field_values(item, field))
                if count > 1:
                    errors.append(f"canonical YAML subset forbids duplicate {field} fields in job {job_name}")
    jobs_header = next((line_number for line_number, _, code in structural if code == "jobs:"), None)
    if jobs_header is not None:
        jobs_structure = [entry for entry in structural if entry[0] > jobs_header]
        global_steps = sum(code == "    steps:" for _, _, code in jobs_structure)
        global_items = sum(bool(re.match(r"^      -(?:\s|$)", code)) for _, _, code in jobs_structure)
        global_runs = sum(bool(re.match(r"^        run\s*:", code)) for _, _, code in jobs_structure)
        if global_steps != len(jobs):
            errors.append("canonical YAML subset must account for every steps collection")
        if global_items != parsed_step_items:
            errors.append("canonical YAML subset must account for every block step item")
        if global_runs != parsed_run_fields:
            errors.append("canonical YAML subset must account for every executable run field")
    return errors


def folded_yaml_value(lines: list[str], field_index: int, field_indent: int) -> str | None:
    """Decode the fail-closed subset of YAML >- used by the cache key."""
    payload: list[str] = []
    for raw in lines[field_index + 1:]:
        if not raw.strip():
            return None
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= field_indent:
            break
        payload.append(raw)
    if not payload:
        return None
    content_indent = min(len(line) - len(line.lstrip(" ")) for line in payload)
    if content_indent <= field_indent:
        return None
    if any(len(line) - len(line.lstrip(" ")) != content_indent for line in payload):
        return None
    # For equally indented, non-empty lines, YAML's folded >- style replaces
    # each line break with one space and strips the final line break.
    return " ".join(line[content_indent:] for line in payload)


def executable_run_commands(step: str) -> list[str]:
    lines = step.splitlines()
    commands: list[str] = []
    for index, _, code in structural_yaml_lines(step):
        direct = re.match(r"^        run\s*:\s*(.*)$", code)
        if direct:
            value = direct.group(1).strip()
            if re.fullmatch(r"[>|][+-]?", value):
                payload: list[str] = []
                for raw in lines[index + 1:]:
                    if not raw.strip():
                        continue
                    indent = len(raw) - len(raw.lstrip(" "))
                    if indent <= 8:
                        break
                    shell = strip_yaml_comment(raw.lstrip(" "))
                    if shell:
                        payload.append(shell)
                commands.append("\n".join(payload))
            else:
                commands.append(value)
            continue
        if re.search(r"(?<![A-Za-z0-9_-])run\s*:", code):
            commands.append(code)
    return commands


def workflow_sha256(workflow: str) -> str:
    """Hash exact UTF-8 bytes without normalizing line endings or other text."""
    return hashlib.sha256(workflow.encode("utf-8", errors="strict")).hexdigest()


def workflow_errors(workflow: str) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{64}", EXPECTED_WORKFLOW_SHA256):
        errors.append("reviewed workflow SHA-256 must be lowercase 64-hex")
    if workflow_sha256(workflow) != EXPECTED_WORKFLOW_SHA256:
        errors.append(WORKFLOW_DIGEST_ERROR)

    canonical_errors = canonical_yaml_subset_errors(workflow)
    errors.extend(canonical_errors)
    if canonical_errors:
        return errors
    scalar_content = "\n".join(
        code for raw in workflow.splitlines()
        if (code := strip_yaml_comment(raw)).strip()
    )
    if "simctl" in decode_yaml_unicode_escapes(scalar_content).lower():
        errors.append("non-comment workflow scalar content must not contain simctl")

    uses: list[tuple[str, str, str]] = []
    canonical_uses = re.compile(
        r"^\s*(?:-\s+)?uses:\s*([^@\s]+)@([^\s#]+)(\s+#.*)?\s*$"
    )
    for _, raw, code in structural_yaml_lines(workflow):
        lexical_uses = re.findall(r"(?<![A-Za-z0-9_-])uses\s*:", code)
        if not lexical_uses:
            continue
        canonical = canonical_uses.fullmatch(raw)
        if len(lexical_uses) != 1 or canonical is None:
            errors.append(f"every uses key must use canonical block syntax: {code.strip()}")
            continue
        uses.append(canonical.groups())
    actual_counts = Counter(action for action, _, _ in uses)
    for action, (sha, comment, count) in ACTION_MAP.items():
        if actual_counts[action] != count:
            errors.append(f"{action} must appear exactly {count} times")
    for action, ref, comment in uses:
        expected = ACTION_MAP.get(action)
        if expected is None:
            errors.append(f"unexpected action reference: {action}@{ref}")
            continue
        sha, expected_comment, _ = expected
        if ref != sha or (comment or "").strip() != expected_comment:
            errors.append(f"{action} ref/comment must be exactly {sha} {expected_comment}")

    trigger = re.search(r"(?ms)^on:\n(.*?)(?=^permissions:|^jobs:)", workflow)
    if not trigger:
        errors.append("workflow must declare an on block before permissions/jobs")
    else:
        trigger_body = trigger.group(1)
        trigger_names = re.findall(r"(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):", trigger_body)
        if trigger_names != ["push", "workflow_dispatch"]:
            errors.append("workflow triggers must be push and workflow_dispatch only")
        branches = re.search(r"(?ms)^    branches:\n((?:      - [^\n]+\n)+)", trigger_body)
        branch_names = re.findall(r"(?m)^      - ([^\n]+)", branches.group(1)) if branches else []
        if branch_names != ["ios-port"]:
            errors.append("push trigger must target only ios-port")
        if re.search(r"(?m)^  (?:pull_request_target|workflow_run):", trigger_body):
            errors.append("pull_request_target and workflow_run triggers are forbidden")

    top_permissions = re.findall(r"(?ms)^permissions:\n((?:  [^\n]+\n)+)", workflow)
    if len(top_permissions) != 1 or top_permissions[0].strip() != "contents: read":
        errors.append("top-level permissions must be only contents: read")
    job_permissions = []
    for job_name, block in workflow_job_blocks(workflow):
        for _, _, code in structural_yaml_lines(block):
            if re.match(r"^    permissions\s*:", code):
                job_permissions.append((job_name, code))
    release = job_block(workflow, "release")
    if job_permissions != [("release", "    permissions:")] or not re.search(
        r"(?m)^    permissions:\n      contents: write\n    steps:$", release
    ):
        errors.append("release contents: write must be the sole job-level permission override")

    deps = job_block(workflow, "deps")
    if not deps:
        errors.append("deps job is missing")
        return errors
    expected_checkout = (
        "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
    )
    deps_items = step_items(deps)
    expected_step_headers = [expected_checkout, *[f"      - name: {name}" for name in DEPS_STEP_ORDER]]
    actual_step_headers = [item.splitlines()[0] for item in deps_items]
    if len(deps_items) != 7 or actual_step_headers != expected_step_headers:
        errors.append("deps steps must be the exact seven-item checkout/input/toolchain/cache/build/verifier/upload sequence")
    if direct_mapping_keys(deps, 4) != DEPS_JOB_FIELDS:
        errors.append("deps job fields must match the exact reviewed allowlist")
    if len(deps_items) == len(DEPS_STEP_FIELDS):
        for index, (item, expected_fields) in enumerate(zip(deps_items, DEPS_STEP_FIELDS, strict=True)):
            if step_direct_keys(item) != expected_fields:
                errors.append(f"deps step {index} fields must match the exact reviewed allowlist")

    inputs = named_step(deps, "Validate dependency cache inputs")
    if not re.search(r"(?m)^        id: deps-inputs$", inputs):
        errors.append("dependency inputs step must use id deps-inputs")
    input_array = re.search(r"(?ms)inputs=\(\n(.*?)\n          \)", inputs)
    listed_inputs = re.findall(r'"([^"]+)"', input_array.group(1)) if input_array else []
    if listed_inputs != list(DEPS_INPUTS):
        errors.append("dependency cache input list must contain the exact seven ordered paths")
    if 'if [ ! -f "$path" ] || [ -L "$path" ]; then' not in inputs:
        errors.append("dependency cache inputs must reject missing or symlink paths")
    hashfiles = re.findall(r"(?s)hashFiles\((.*?)\)\s*}}", inputs)
    hashed_inputs = re.findall(r"'([^']+)'", hashfiles[0]) if len(hashfiles) == 1 else []
    if hashed_inputs != list(DEPS_INPUTS):
        errors.append("hashFiles must contain the exact seven ordered paths")
    if 'echo "sha256=$sha256" >> "$GITHUB_OUTPUT"' not in inputs:
        errors.append("dependency inputs hash must be exported as sha256")
    if '[[ "$sha256" =~ ^[0-9a-f]{64}$ ]]' not in inputs:
        errors.append("dependency inputs hash must be a lowercase SHA-256")
    if step_field_values(inputs, "shell") != ["bash"]:
        errors.append("dependency inputs step shell must be exactly bash")
    if normalized_block_payload(inputs, "run") != EXPECTED_DEPS_INPUTS_SCRIPT:
        errors.append("dependency inputs script must match the complete reviewed payload")

    toolchain = named_step(deps, "Fingerprint dependency toolchain")
    if not re.search(r"(?m)^        id: deps-toolchain$", toolchain):
        errors.append("dependency toolchain step must use id deps-toolchain")
    override_array = re.search(r"(?ms)ambient_overrides=\(\n(.*?)\n          \)", toolchain)
    overrides = re.findall(r"\b[A-Z_][A-Z0-9_]*\b", override_array.group(1)) if override_array else []
    if overrides != list(AMBIENT_OVERRIDES):
        errors.append("dependency toolchain must reject the exact ambient override list")
    required_runner_array = re.search(r"required_runner_fields=\(([^)]*)\)", toolchain)
    runner_fields = required_runner_array.group(1).split() if required_runner_array else []
    if runner_fields != ["ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH"]:
        errors.append("dependency toolchain must require runner identity fields in fixed order")
    fields = re.findall(r'require_(?:value|command_output) "([A-Za-z][^"]*)"', toolchain)
    if fields != list(FINGERPRINT_FIELDS):
        errors.append("dependency toolchain fingerprint fields must be complete and ordered")
    required_toolchain_fragments = (
        'if [ "${!variable+x}" = x ]; then',
        'require_command_output "xcodebuild-version" /usr/bin/xcodebuild -version',
        'require_command_output "sdk-path" /usr/bin/xcrun --sdk "$sdk" --show-sdk-path',
        'require_command_output "sdk-version" /usr/bin/xcrun --sdk "$sdk" --show-sdk-version',
        'require_command_output "sdk-build" /usr/bin/xcrun --sdk "$sdk" --show-sdk-build-version',
        'require_command_output "clang-version" "$clang_path" --version',
        'require_value "generator" "$generator"',
        'require_command_output "make-version" /usr/bin/make --version',
        'cat "$fingerprint"',
        'echo "sha256=$sha256" >> "$GITHUB_OUTPUT"',
        '[[ "$sha256" =~ ^[0-9a-f]{64}$ ]]',
    )
    for fragment in required_toolchain_fragments:
        if fragment not in toolchain:
            errors.append(f"dependency toolchain is missing required evidence: {fragment}")
    if step_field_values(toolchain, "shell") != ["bash"]:
        errors.append("dependency toolchain step shell must be exactly bash")
    if normalized_block_payload(toolchain, "run") != EXPECTED_DEPS_TOOLCHAIN_SCRIPT:
        errors.append("dependency toolchain script must match the complete reviewed payload")

    cache = named_step(deps, "Cache dependency prefix")
    if not re.search(r"(?m)^        id: deps-cache$", cache):
        errors.append("dependency prefix cache must use id deps-cache")
    if not re.search(
        r"(?m)^        uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830 # v4.3.0$", cache
    ):
        errors.append("dependency prefix cache must use the pinned actions/cache release")
    cache_lines = cache.splitlines()
    with_headers = [
        index for index, _, code in structural_yaml_lines(cache) if code == "        with:"
    ]
    cache_key: str | None = None
    cache_paths: list[str] = []
    key_syntax_count = 0
    if len(with_headers) == 1:
        with_index = with_headers[0]
        with_end = len(cache_lines)
        for index in range(with_index + 1, len(cache_lines)):
            raw = cache_lines[index]
            if raw.strip() and len(raw) - len(raw.lstrip(" ")) <= 8:
                with_end = index
                break
        with_block = "\n".join(cache_lines[with_index + 1:with_end])
        if direct_mapping_keys(with_block, 10) != ("path", "key"):
            errors.append("dependency cache with mapping must contain exactly direct path and key fields")
        key_lines: list[tuple[int, str]] = []
        for index, _, code in structural_yaml_lines(with_block):
            key_syntax_count += len(re.findall(r"(?<![A-Za-z0-9_-])key\s*:", code))
            direct = re.match(r"^          key\s*:\s*(.*)$", code)
            if direct:
                key_lines.append((index, direct.group(1).strip()))
            path = re.match(r"^          path\s*:\s*(.*)$", code)
            if path:
                cache_paths.append(path.group(1).strip())
        if key_syntax_count == 1 and len(key_lines) == 1:
            key_index, key_indicator = key_lines[0]
            if key_indicator == ">-":
                cache_key = folded_yaml_value(with_block.splitlines(), key_index, 10)
    if len(with_headers) != 1 or key_syntax_count != 1 or cache_key != CACHE_KEY:
        errors.append("dependency cache key must be exactly one with.key folded scalar equal to the provenance key")
    if cache_paths != [CACHE_PATH]:
        errors.append("dependency cache path must be exactly one direct scalar equal to the matrix SDK prefix")
    for forbidden in ("restore-keys", "actions/cache/restore", "actions/cache/save", "save-always", "always()", "enableCrossOsArchive", "cross-os"):
        if forbidden in deps:
            errors.append(f"forbidden cache feature in deps job: {forbidden}")
    if step_field_values(cache, "if"):
        errors.append("dependency prefix cache must be a monolithic unconditional cache action")

    build = named_step(deps, "Cross-build dependency superbuild (${{ matrix.platform }})")
    if step_field_values(build, "if") != [BUILD_CACHE_MISS_IF]:
        errors.append("dependency build must use the exact cache-hit condition")
    if '-G "Unix Makefiles"' not in build:
        errors.append("dependency build must configure with Unix Makefiles")

    verifier = named_step(deps, "Verify static libs are arm64")
    upload = named_step(deps, "Upload deps prefix")
    if step_field_values(verifier, "if"):
        errors.append("dependency library verifier must remain unconditional")
    if step_field_values(upload, "if"):
        errors.append("dependency prefix upload must remain unconditional")
    libraries = re.search(r"for lib in ([^;\n]+); do", verifier)
    expected_libraries = (
        "libSDL2.a", "libopenal.a", "libogg.a", "libvorbis.a", "libvorbisfile.a", "libtheora.a", "liblzo2.a",
    )
    if not libraries or tuple(libraries.group(1).split()) != expected_libraries:
        errors.append("dependency verifier must retain the exact seven-library contract")
    if 'uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2' not in upload:
        errors.append("dependency prefix upload must use the pinned artifact action")

    deps_matrix = re.search(r"(?ms)^      matrix:\n(.*?)(?=^    steps:)", deps)
    pairs = re.findall(
        r"(?ms)^            platform: ([^\n]+)\n            sdk: ([^\n]+)$",
        deps_matrix.group(1) if deps_matrix else "",
    )
    if pairs != [("OS64", "iphoneos"), ("SIMULATORARM64", "iphonesimulator")]:
        errors.append("deps matrix must be exactly OS64/iphoneos and SIMULATORARM64/iphonesimulator")

    ios = job_block(workflow, "ios")
    simulator = re.search(
        r"(?ms)^          - name: simulator \(compile check\)\n.*?(?=^          - name:|^    steps:)", ios
    )
    if not simulator or not re.search(
        r"(?m)^            platform: SIMULATORARM64\n            sdk: iphonesimulator\n            package_ipa: false$",
        simulator.group(0),
    ):
        errors.append("simulator must remain SIMULATORARM64 compile-only with package_ipa false")
    for job_name, block in workflow_job_blocks(workflow):
        for step in step_items(block):
            run_fields = step_field_values(step, "run")
            commands = executable_run_commands(step)
            if len(commands) != len(run_fields):
                errors.append(f"every executable run field must be parsed in job {job_name}")
            for command in commands:
                if "simctl" in command.lower():
                    errors.append(f"executable simulator runtime commands are forbidden in job {job_name}")

    shader = job_block(workflow, "shader-check")
    shader_items = step_items(shader)
    if direct_mapping_keys(shader, 4) != SHADER_JOB_FIELDS:
        errors.append("shader-check job fields must match the exact reviewed allowlist")
    if [item.splitlines()[0] for item in shader_items] != list(SHADER_STEP_HEADERS):
        errors.append("shader-check steps must match the exact reviewed sequence")
    if len(shader_items) == len(SHADER_STEP_FIELDS):
        for index, (item, expected_fields) in enumerate(zip(shader_items, SHADER_STEP_FIELDS, strict=True)):
            if step_direct_keys(item) != expected_fields:
                errors.append(f"shader-check step {index} fields must match the exact reviewed allowlist")
    contract_step = named_step(shader, "Validate iOS CI contract")
    if step_field_values(contract_step, "run") != [SHADER_CONTRACT_COMMAND]:
        errors.append("shader-check must invoke exactly the iOS CI contract command")

    return errors


class IosCiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_bytes = WORKFLOW.read_bytes()
        cls.workflow = cls.workflow_bytes.decode("utf-8", errors="strict")

    def assert_rejected(self, mutated: str, expected: str) -> None:
        errors = workflow_errors(mutated)
        self.assertTrue(any(expected in error for error in errors), errors)

    def replace_once(self, old: str, new: str) -> str:
        self.assertIn(old, self.workflow)
        return self.workflow.replace(old, new, 1)

    def replace_once_in_job(self, job_name: str, old: str, new: str) -> str:
        block = job_block(self.workflow, job_name)
        self.assertTrue(block, f"missing job {job_name}")
        self.assertIn(old, block)
        mutated_block = block.replace(old, new, 1)
        return self.workflow.replace(block, mutated_block, 1)

    def add_ios_step(self, step: str) -> str:
        anchor = "      - name: Package unsigned .ipa\n"
        return self.replace_once(anchor, step + "\n\n" + anchor)

    def test_baseline(self) -> None:
        self.assertEqual(workflow_errors(self.workflow), [])

    def test_expected_workflow_digest_format_and_baseline(self) -> None:
        self.assertRegex(EXPECTED_WORKFLOW_SHA256, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(
            WORKFLOW_DIGEST_ALGORITHM,
            "sha256-v1-exact-utf8-bytes",
        )
        self.assertEqual(
            self.workflow.encode("utf-8", errors="strict"),
            self.workflow_bytes,
            "workflow bytes must be strict UTF-8 with no newline normalization",
        )
        self.assertEqual(
            hashlib.sha256(self.workflow_bytes).hexdigest(),
            EXPECTED_WORKFLOW_SHA256,
        )

    def test_line_ending_change_fails_complete_workflow_digest(self) -> None:
        self.assertNotIn("\r\n", self.workflow)
        self.assert_rejected(self.workflow.replace("\n", "\r\n"), WORKFLOW_DIGEST_ERROR)

    def test_unknown_harmless_unreviewed_change_fails_complete_workflow_digest(self) -> None:
        mutated = "# Harmless-looking but unreviewed workflow change.\n" + self.workflow
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_deps_build_platform_payload_change_fails_complete_workflow_digest(self) -> None:
        mutated = self.replace_once_in_job(
            "deps",
            "-DPLATFORM=${{ matrix.platform }}",
            "-DPLATFORM=OS64",
        )
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_deps_build_deployment_target_change_fails_complete_workflow_digest(self) -> None:
        mutated = self.replace_once_in_job(
            "deps",
            "-DDEPLOYMENT_TARGET=16.4",
            "-DDEPLOYMENT_TARGET=15.0",
        )
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_deps_verifier_arm64_or_true_fails_complete_workflow_digest(self) -> None:
        original = (
            "echo \"$info\" | grep -qi 'arm64' || "
            "{ echo \"::error::$lib is not arm64\"; exit 1; }"
        )
        mutated = self.replace_once_in_job("deps", original, original + " || true")
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_push_paths_ignore_change_fails_complete_workflow_digest(self) -> None:
        mutated = self.replace_once(
            "      - '**/*.md'\n",
            "      - '**/*.md'\n      - 'src/**'\n",
        )
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_push_tags_filter_fails_complete_workflow_digest(self) -> None:
        mutated = self.replace_once(
            "  workflow_dispatch:\n",
            "    tags:\n      - '*'\n  workflow_dispatch:\n",
        )
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_all_build_job_matrix_changes_fail_complete_workflow_digest(self) -> None:
        for job_name in ("ios", "deps", "engine-build", "luajit"):
            with self.subTest(job=job_name):
                mutated = self.replace_once_in_job(
                    job_name,
                    "            platform: OS64",
                    "            platform: UNREVIEWED",
                )
                self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_all_build_job_matrix_excludes_fail_complete_workflow_digest(self) -> None:
        exclude = "        exclude:\n          - platform: OS64\n\n    steps:\n"
        for job_name in ("ios", "deps", "engine-build", "luajit"):
            with self.subTest(job=job_name):
                mutated = self.replace_once_in_job(job_name, "    steps:\n", exclude)
                self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_all_build_job_fail_fast_changes_fail_complete_workflow_digest(self) -> None:
        for job_name in ("ios", "deps", "engine-build", "luajit"):
            with self.subTest(job=job_name):
                mutated = self.replace_once_in_job(
                    job_name,
                    "      fail-fast: false",
                    "      fail-fast: true",
                )
                self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_glslang_build_or_true_fails_complete_workflow_digest(self) -> None:
        mutated = self.replace_once_in_job(
            "shader-check",
            "cmake --build /tmp/glslang-build --target glslang-standalone --parallel 2",
            "cmake --build /tmp/glslang-build --target glslang-standalone --parallel 2 || true",
        )
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_shader_gate_or_true_fails_complete_workflow_digest(self) -> None:
        mutated = self.replace_once_in_job(
            "shader-check",
            "run: ./misc/ios/build_check.sh --shaders",
            "run: ./misc/ios/build_check.sh --shaders || true",
        )
        self.assert_rejected(mutated, WORKFLOW_DIGEST_ERROR)

    def test_obfuscated_simctl_fails_complete_workflow_digest(self) -> None:
        step = (
            "      - name: Obfuscated simulator runtime\n"
            '        run: xcrun s""imctl boot booted'
        )
        self.assert_rejected(self.add_ios_step(step), WORKFLOW_DIGEST_ERROR)

    def test_xcodebuild_test_destination_fails_complete_workflow_digest(self) -> None:
        step = (
            "      - name: Simulator test destination\n"
            "        run: xcodebuild test -scheme Unsafe "
            "-destination 'platform=iOS Simulator,name=iPhone'"
        )
        self.assert_rejected(self.add_ios_step(step), WORKFLOW_DIGEST_ERROR)

    def test_mutable_action_tag_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(ACTION_MAP["actions/checkout"][0], "v4"),
            "actions/checkout ref/comment",
        )

    def test_wrong_action_sha_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(ACTION_MAP["actions/checkout"][0], "0" * 40),
            "actions/checkout ref/comment",
        )

    def test_wrong_action_comment_fails(self) -> None:
        self.assert_rejected(self.replace_once("# v4.4.0", "# v4.4.1"), "actions/checkout ref/comment")

    def test_quoted_uses_key_fails(self) -> None:
        self.assert_rejected(
            self.replace_once("      - uses: actions/checkout@", '      - "uses": actions/checkout@'),
            "quoted structural keys",
        )

    def test_explicit_uses_key_fails(self) -> None:
        checkout = (
            "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
        )
        explicit = (
            "      - ? uses\n"
            "        : actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
        )
        self.assert_rejected(self.replace_once(checkout, explicit), "explicit mapping keys")

    def test_explicit_quoted_uses_key_fails(self) -> None:
        checkout = (
            "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
        )
        explicit = (
            "      - ? \"uses\"\n"
            "        : actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
        )
        self.assert_rejected(self.replace_once(checkout, explicit), "explicit mapping keys")

    def test_quoted_permissions_key_fails(self) -> None:
        self.assert_rejected(
            self.replace_once("permissions:\n  contents: read", '"permissions":\n  contents: read'),
            "quoted structural keys",
        )

    def test_unicode_escaped_permissions_key_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "permissions:\n  contents: read",
                '"\\u0070ermissions":\n  contents: read',
            ),
            "quoted structural keys",
        )

    def test_explicit_permissions_key_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "  deps:\n    name: iOS deps ${{ matrix.name }}",
                "  deps:\n    ? permissions\n    : write-all\n    name: iOS deps ${{ matrix.name }}",
            ),
            "explicit mapping keys",
        )

    def test_explicit_quoted_permissions_key_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "  deps:\n    name: iOS deps ${{ matrix.name }}",
                "  deps:\n    ? 'permissions'\n    : write-all\n    name: iOS deps ${{ matrix.name }}",
            ),
            "explicit mapping keys",
        )

    def test_flow_style_permissions_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "    permissions:\n      contents: write",
                "    permissions: { contents: write }",
            ),
            "supported permissions field form",
        )

    def test_quoted_pull_request_target_fails(self) -> None:
        self.assert_rejected(
            self.replace_once("  workflow_dispatch:\n", '  "pull_request_target":\n  workflow_dispatch:\n'),
            "quoted structural keys",
        )

    def test_unicode_escaped_pull_request_target_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "  workflow_dispatch:\n",
                '  "pull_\\u0072equest_target":\n  workflow_dispatch:\n',
            ),
            "quoted structural keys",
        )

    def test_unicode_escaped_uses_in_reusable_job_fails(self) -> None:
        hidden_job = (
            "  escaped-reusable:\n"
            '    "\\u0075ses": attacker/example/.github/workflows/unsafe.yml@main\n\n'
        )
        self.assert_rejected(
            self.replace_once("  release:\n", hidden_job + "  release:\n"),
            "quoted structural keys",
        )

    def test_unicode_escaped_restore_keys_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                f"          path: {CACHE_PATH}\n",
                f'          path: {CACHE_PATH}\n          "\\u0072estore-keys": unsafe\n',
            ),
            "quoted structural keys",
        )

    def test_flow_style_steps_collection_fails(self) -> None:
        flow_job = (
            "  flow-runtime:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: [{ run: \"xcrun simctl boot booted\" }]\n\n"
        )
        self.assert_rejected(
            self.replace_once("  release:\n", flow_job + "  release:\n"),
            "flow-style steps collections",
        )

    def test_run_under_unparsed_quoted_job_fails(self) -> None:
        hidden_job = (
            "  \"hidden-runtime\":\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Hidden simulator runtime\n"
            "        run: xcrun simctl boot booted\n\n"
        )
        self.assert_rejected(
            self.replace_once("  release:\n", hidden_job + "  release:\n"),
            "must account for every",
        )

    def test_omitted_or_reordered_input_fails(self) -> None:
        self.assert_rejected(
            self.replace_once('            "cmake/ios/theora/CMakeLists.txt"\n', ""),
            "dependency cache input list",
        )
        self.assert_rejected(
            self.replace_once(
                '            "cmake/ios/deps/CMakeLists.txt"\n            "cmake/toolchains/ios.toolchain.cmake"',
                '            "cmake/toolchains/ios.toolchain.cmake"\n            "cmake/ios/deps/CMakeLists.txt"',
            ),
            "dependency cache input list",
        )

    def test_missing_cache_key_components_fail(self) -> None:
        for component in (
            "${{ github.ref_name }}", "${{ matrix.sdk }}", "${{ matrix.platform }}",
            "${{ steps.deps-toolchain.outputs.sha256 }}", "${{ steps.deps-inputs.outputs.sha256 }}",
        ):
            with self.subTest(component=component):
                self.assert_rejected(
                    self.replace_once(CACHE_KEY, CACHE_KEY.replace(component, "omitted", 1)),
                    "dependency cache key",
                )

    def test_static_cache_key_with_expected_comment_decoy_fails(self) -> None:
        folded_key = f"          key: >-\n            {CACHE_KEY}"
        decoy = (
            "          key: openxray-ios-deps-v1-static\n"
            f"          # expected key decoy: {CACHE_KEY}"
        )
        self.assert_rejected(
            self.replace_once(folded_key, decoy),
            "supported key field form",
        )

    def test_duplicate_cache_key_fails(self) -> None:
        folded_key = f"          key: >-\n            {CACHE_KEY}"
        duplicate = (
            "          key: openxray-ios-deps-v1-static\n"
            f"          key: >-\n            {CACHE_KEY}"
        )
        self.assert_rejected(
            self.replace_once(folded_key, duplicate),
            "supported key field form",
        )

    def test_cache_path_comment_decoy_fails(self) -> None:
        decoy = (
            "          path: build/ios-prefix-static\n"
            f"          # expected path decoy: {CACHE_PATH}"
        )
        self.assert_rejected(
            self.replace_once(f"          path: {CACHE_PATH}", decoy),
            "dependency cache path",
        )

    def test_duplicate_cache_path_fails(self) -> None:
        duplicate = (
            "          path: build/ios-prefix-static\n"
            f"          path: {CACHE_PATH}"
        )
        self.assert_rejected(
            self.replace_once(f"          path: {CACHE_PATH}", duplicate),
            "dependency cache path",
        )

    def test_multiline_cache_path_fails(self) -> None:
        multiline = f"          path:\n            {CACHE_PATH}"
        self.assert_rejected(
            self.replace_once(f"          path: {CACHE_PATH}", multiline),
            "supported path field form",
        )

    def test_folded_cache_path_fails(self) -> None:
        folded = f"          path: >-\n            {CACHE_PATH}"
        self.assert_rejected(
            self.replace_once(f"          path: {CACHE_PATH}", folded),
            "supported path field form",
        )

    def test_missing_toolchain_fingerprint_field_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                '          require_command_output "clang-version" "$clang_path" --version\n', ""
            ),
            "dependency toolchain fingerprint fields",
        )

    def test_late_input_sha256_override_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                '          echo "dependency-inputs-sha256=$sha256"\n',
                '          sha256=0000000000000000000000000000000000000000000000000000000000000000\n'
                '          echo "dependency-inputs-sha256=$sha256"\n',
            ),
            "dependency inputs script must match the complete reviewed payload",
        )

    def test_late_toolchain_sha256_override_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                '          echo "dependency-toolchain-sha256=$sha256"\n',
                '          sha256=0000000000000000000000000000000000000000000000000000000000000000\n'
                '          echo "dependency-toolchain-sha256=$sha256"\n',
            ),
            "dependency toolchain script must match the complete reviewed payload",
        )

    def test_toolchain_fail_function_returning_success_fails(self) -> None:
        original = (
            "          fail() {\n"
            '            echo "::error::$1"\n'
            "            exit 1\n"
            "          }"
        )
        weakened = (
            "          fail() {\n"
            '            echo "::error::$1"\n'
            "            return 0\n"
            "          }"
        )
        self.assert_rejected(
            self.replace_once(original, weakened),
            "dependency toolchain script must match the complete reviewed payload",
        )

    def test_input_missing_file_guard_returning_success_fails(self) -> None:
        original = (
            '              echo "::error::dependency cache input must be a regular non-symlink file: $path"\n'
            "              exit 1"
        )
        weakened = (
            '              echo "::error::dependency cache input must be a regular non-symlink file: $path"\n'
            "              true"
        )
        self.assert_rejected(
            self.replace_once(original, weakened),
            "dependency inputs script must match the complete reviewed payload",
        )

    def test_dependency_input_shell_wrapper_fails(self) -> None:
        self.assert_rejected(
            self.replace_once("        shell: bash\n", "        shell: bash {0} || true\n"),
            "dependency inputs step shell must be exactly bash",
        )

    def test_dependency_toolchain_shell_wrapper_fails(self) -> None:
        original = (
            "      - name: Fingerprint dependency toolchain\n"
            "        id: deps-toolchain\n"
            "        shell: bash"
        )
        weakened = (
            "      - name: Fingerprint dependency toolchain\n"
            "        id: deps-toolchain\n"
            "        shell: bash {0} || true"
        )
        self.assert_rejected(
            self.replace_once(original, weakened),
            "dependency toolchain step shell must be exactly bash",
        )

    def test_restore_key_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                '          path: build/ios-prefix-${{ matrix.sdk }}\n',
                '          path: build/ios-prefix-${{ matrix.sdk }}\n          restore-keys: unsafe\n',
            ),
            "forbidden cache feature",
        )

    def test_cache_lookup_only_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                f"          path: {CACHE_PATH}\n",
                f"          path: {CACHE_PATH}\n          lookup-only: true\n",
            ),
            "cache with mapping must contain exactly direct path and key",
        )

    def test_cache_fail_on_miss_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                f"          path: {CACHE_PATH}\n",
                f"          path: {CACHE_PATH}\n          fail-on-cache-miss: true\n",
            ),
            "cache with mapping must contain exactly direct path and key",
        )

    def test_weakened_cache_hit_condition_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "if: ${{ steps.deps-cache.outputs.cache-hit != 'true' }}",
                "if: ${{ steps.deps-cache.outputs.cache-hit != 'false' }}",
            ),
            "dependency build must use the exact cache-hit condition",
        )

    def test_reversed_build_condition_with_comment_decoy_fails(self) -> None:
        reversed_condition = (
            "if: ${{ 'true' != steps.deps-cache.outputs.cache-hit }}\n"
            f"        # expected condition decoy: {BUILD_CACHE_MISS_IF}"
        )
        self.assert_rejected(
            self.replace_once(f"if: {BUILD_CACHE_MISS_IF}", reversed_condition),
            "dependency build must use the exact cache-hit condition",
        )

    def test_quoted_verifier_condition_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "      - name: Verify static libs are arm64\n",
                "      - name: Verify static libs are arm64\n        \"if\": ${{ always() }}\n",
            ),
            "quoted structural keys",
        )

    def test_shader_contract_continue_on_error_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "      - name: Validate iOS CI contract\n",
                "      - name: Validate iOS CI contract\n        continue-on-error: true\n",
            ),
            "supported continue-on-error field form",
        )

    def test_dependency_verifier_continue_on_error_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "      - name: Verify static libs are arm64\n",
                "      - name: Verify static libs are arm64\n        continue-on-error: true\n",
            ),
            "supported continue-on-error field form",
        )

    def test_dependency_job_continue_on_error_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "  deps:\n    name: iOS deps ${{ matrix.name }}",
                "  deps:\n    name: iOS deps ${{ matrix.name }}\n    continue-on-error: true",
            ),
            "supported continue-on-error field form",
        )

    def test_shader_contract_command_suffix_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                f"        run: {SHADER_CONTRACT_COMMAND}\n",
                f"        run: {SHADER_CONTRACT_COMMAND} || true\n",
            ),
            "shader-check must invoke exactly the iOS CI contract command",
        )

    def test_dependency_job_unknown_field_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "  deps:\n    name: iOS deps ${{ matrix.name }}",
                "  deps:\n    name: iOS deps ${{ matrix.name }}\n    environment: unsafe",
            ),
            "deps job fields must match the exact reviewed allowlist",
        )

    def test_top_level_default_shell_wrapper_fails(self) -> None:
        defaults = (
            "defaults:\n"
            "  run:\n"
            "    shell: bash {0} || true\n\n"
        )
        self.assert_rejected(
            self.replace_once("jobs:\n", defaults + "jobs:\n"),
            "workflow top-level fields must match the exact reviewed allowlist",
        )

    def test_dependency_verifier_unknown_field_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "      - name: Verify static libs are arm64\n",
                "      - name: Verify static libs are arm64\n        shell: bash\n",
            ),
            "deps step 5 fields must match the exact reviewed allowlist",
        )

    def test_conditional_or_moved_verifier_upload_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "      - name: Verify static libs are arm64\n",
                "      - name: Verify static libs are arm64\n        if: ${{ always() }}\n",
            ),
            "dependency library verifier must remain unconditional",
        )
        deps = job_block(self.workflow, "deps")
        verifier = named_step(deps, "Verify static libs are arm64")
        upload = named_step(deps, "Upload deps prefix")
        self.assertTrue(verifier and upload)
        self.assert_rejected(
            self.replace_once(verifier + upload, upload + verifier),
            "exact seven-item",
        )

    def test_cross_os_cache_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                '          path: build/ios-prefix-${{ matrix.sdk }}\n',
                '          path: build/ios-prefix-${{ matrix.sdk }}\n          enableCrossOsArchive: true\n',
            ),
            "forbidden cache feature",
        )

    def test_scalar_job_permissions_fail(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "  deps:\n    name: iOS deps ${{ matrix.name }}",
                "  deps:\n    permissions: write-all\n    name: iOS deps ${{ matrix.name }}",
            ),
            "supported permissions field form",
        )

    def test_flow_style_action_fails(self) -> None:
        self.assert_rejected(
            self.replace_once(
                "      - name: Build pinned glslang 16.4.0",
                "      - { uses: attacker/example@v1 }\n\n      - name: Build pinned glslang 16.4.0",
            ),
            "flow-style sequence items",
        )

    def test_unnamed_deps_run_step_fails(self) -> None:
        checkout = (
            "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
        )
        self.assert_rejected(
            self.replace_once(
                f"{checkout}\n\n      - name: Validate dependency cache inputs",
                f"{checkout}\n\n      - run: echo bypass\n\n      - name: Validate dependency cache inputs",
            ),
            "canonical block step items",
        )

    def test_simctl_command_wrapper_case_insensitive_fails(self) -> None:
        step = (
            "      - name: Wrapped simulator command\n"
            "        if: ${{ matrix.sdk == 'iphonesimulator' }}\n"
            "        run: command xcrun SiMcTl list"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "executable simulator runtime commands are forbidden",
        )

    def test_simctl_install_fails(self) -> None:
        step = (
            "      - name: Install simulator app\n"
            "        if: ${{ matrix.sdk == 'iphonesimulator' }}\n"
            "        run: xcrun simctl install booted OpenXRay.app"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "executable simulator runtime commands are forbidden",
        )

    def test_simctl_string_literal_fails(self) -> None:
        step = (
            "      - name: Simulator token string\n"
            "        run: printf '%s\\n' 'simctl'"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "executable simulator runtime commands are forbidden",
        )

    def test_simctl_env_indirection_fails(self) -> None:
        step = (
            "      - name: Simulator environment indirection\n"
            "        env:\n"
            "          TOOL: SiMcTl\n"
            "        run: echo \"$TOOL\""
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "non-comment workflow scalar content must not contain simctl",
        )

    def test_unicode_escaped_simctl_env_value_fails(self) -> None:
        step = (
            "      - name: Escaped simulator environment indirection\n"
            "        env:\n"
            '          TOOL: "\\u0073imctl"\n'
            "        run: echo \"$TOOL\""
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "non-comment workflow scalar content must not contain simctl",
        )

    def test_simctl_comment_is_semantically_ignored_but_digest_rejected(self) -> None:
        mutated = "# simctl is mentioned only in this YAML comment\n" + self.workflow
        errors = workflow_errors(mutated)
        self.assertIn(WORKFLOW_DIGEST_ERROR, errors)
        self.assertFalse(
            any("simctl" in error and "digest" not in error for error in errors),
            errors,
        )

    def test_run_alias_fails_before_execution_scan(self) -> None:
        step = (
            "      - name: Aliased simulator run\n"
            "        run: *simulator-command"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "forbids anchors, aliases and tags",
        )

    def test_unsupported_run_block_scalar_indicator_fails(self) -> None:
        step = (
            "      - name: Unsupported simulator scalar\n"
            "        run: |2\n"
            "          xcrun simctl boot booted"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "forbids block scalar indicator |2",
        )

    def test_flow_style_run_fails(self) -> None:
        step = (
            "      - name: Flow simulator run\n"
            "        run: { command: \"xcrun simctl boot booted\" }"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "supported run field form",
        )

    def test_quoted_run_key_fails(self) -> None:
        step = (
            "      - name: Quoted simulator run\n"
            "        \"run\": xcrun simctl boot booted"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "quoted structural keys",
        )

    def test_explicit_run_key_fails(self) -> None:
        step = (
            "      - name: Explicit simulator run\n"
            "        ? run\n"
            "        : xcrun simctl boot booted"
        )
        self.assert_rejected(
            self.add_ios_step(step),
            "explicit mapping keys",
        )

    def test_simulator_package_or_runtime_fails(self) -> None:
        self.assert_rejected(
            self.replace_once("            package_ipa: false", "            package_ipa: true"),
            "simulator must remain",
        )
        self.assert_rejected(
            self.replace_once(
                "      - name: Package unsigned .ipa\n",
                "      - name: Boot and launch simulator\n"
                "        if: ${{ matrix.sdk == 'iphonesimulator' }}\n"
                "        run: |\n"
                "          xcrun simctl boot 'iPhone test'\n"
                "          xcrun simctl launch booted io.github.tryk016.openxray\n\n"
                "      - name: Package unsigned .ipa\n",
            ),
            "executable simulator runtime commands are forbidden",
        )

    def test_forbidden_triggers_fail(self) -> None:
        for trigger in ("pull_request_target", "workflow_run"):
            with self.subTest(trigger=trigger):
                self.assert_rejected(
                    self.replace_once("permissions:\n", f"  {trigger}:\n\npermissions:\n"),
                    "workflow triggers must be push and workflow_dispatch only",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
