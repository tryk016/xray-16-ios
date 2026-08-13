#!/usr/bin/env python3
"""
Offline GLSL ES 3.00 validation gate for the OpenXRay GL shader tree.

Why this exists
---------------
Phase 4 of the iOS port converts the desktop-GL renderer to OpenGL ES 3.0. The
~286 GL shaders under res/gamedata/shaders/gl are written for desktop GLSL 4.10
(#version 410, gl_PerVertex redeclaration, glBindFragDataLocation-style outputs,
no ES precision qualifiers). Each of these only surfaces at *runtime* on the
device today, which means one SideStore round-trip per error. This script
reproduces — approximately — how the engine assembles a shader
(rgl_shaders.cpp: version line + #defines + recursive #include inlining) but
emits `#version 300 es` and default precision, then runs glslangValidator over
every .vs/.ps so ES incompatibilities show up in CI in seconds.

It is deliberately an *approximation*: the engine's real #define set is driven by
runtime hardware caps and user settings, so we compile each shader's default
(#ifdef-off) path with a small representative define set. That catches the bulk
of the structural ES problems (version, gl_PerVertex, precision, HLSL shims,
sampler types, layout bindings) which is what the port needs to grind down. It is
NOT a guarantee of runtime correctness.

Usage
-----
    python3 glsl_es_check.py [--shaders DIR] [--glslang PATH] [--verbose] [--strict]

Exit code is 0 by default (informational report). Pass --strict to exit non-zero
when any shader fails (flip the CI job to blocking once the tree is clean).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import dataclass
import json
import os
import re
import subprocess
import sys
import tempfile

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)

# Minimal, representative define set. The engine derives these from caps/settings
# at runtime; here we compile each shader's default path. SMAP_size is the one
# value that is always substituted, so give it a plausible number.
BASE_DEFINES = [
    ("SMAP_size", "2048"),
    # The device's r3/deferred path (what CoP runs) assembles shaders with
    # GBUFFER_OPTIMIZATION defined. It changes the *fragment interface* — the MRT
    # f_deffer layout and, critically, the `in vec4 gl_FragCoord;` redeclaration in
    # the iostructs p_*.h headers. Without defining it here the gate skips that whole
    # path and silently passes shaders that fail on-device (that redeclaration is
    # illegal in GLSL ES 3.00). Model it so the gate catches this class offline.
    ("GBUFFER_OPTIMIZATION", "1"),
    # Modern iPhones use hardware shadow maps + PCF (the device log shows USE_HWSMAP /
    # USE_HWSMAP_PCF defined). The `#ifndef USE_HWSMAP` fallback path (e.g. O.depth in
    # shadow_direct_*.vs, a field the v2p struct doesn't have) is dead on the device,
    # so define these to compile the path CoP actually runs instead of the dead one.
    ("USE_HWSMAP", "1"),
    ("USE_HWSMAP_PCF", "1"),
    # Model vertex shaders declare their input `I` only inside a skinning-variant guard
    # (#ifdef SKIN_NONE / SKIN_0..4 in v_model_*.h); the engine always compiles each with
    # exactly one defined. Pick the non-skinned variant so `I` is declared — otherwise every
    # deffer_model_*/model_* VS fails with a cascade of "I undeclared".
    ("SKIN_NONE", "1"),
    # The rest of the define set the device actually compiles with (copied from the shader
    # preamble the engine dumps in xr_boot.log on an A17 at default settings). Without these
    # the gate skips whole feature paths (sun shafts, SSR, DOF, soft water/particles) that
    # then fail only on-device — accum_volumetric_sun's SUN_SHAFTS block was the first.
    ("FP16_FILTER", "1"),
    ("FP16_BLEND", "1"),
    ("USE_BRANCHING", "1"),
    ("USE_SOFT_WATER", "1"),
    ("SSR_QUALITY", "3"),
    # The representative half-depth SSR path requires the same generated depth
    # resource as optimized SSAO. Keep those two defines coherent here, as the
    # GL renderer now does for real water permutations.
    ("SSAO_OPT_DATA", "1"),
    ("SSR_HALF_DEPTH", "1"),
    ("SSR_JITTER", "1"),
    ("USE_SOFT_PARTICLES", "1"),
    ("USE_DOF", "1"),
    ("SUN_SHAFTS_QUALITY", "2"),
    ("SSAO_QUALITY", "3"),
    ("SUN_QUALITY", "1"),
    ("ALLOW_STEEPPARALLAX", "1"),
]

# Only the float/int floor goes in the preamble — matching what the engine's ES
# emission will prepend after `#version 300 es`. SAMPLER precision is deliberately
# NOT added here: it belongs in the shader source (gl/common.h, #ifdef GL_ES),
# so the gate faithfully fails if that source is missing/wrong rather than masking it.
FRAG_PRECISION = [
    "precision highp float;",
    "precision highp int;",
]
VERT_PRECISION = list(FRAG_PRECISION)


# These quality options are emitted as numeric values by the GL renderer.  GLSL
# ES does not accept an undefined identifier in a numeric #if expression, so
# common.h provides a zero fallback for each one. Presence tests are wrong for
# this set: #ifdef sees an explicit zero as enabled and can select resources the
# CPU did not create.
#
# SUN_QUALITY: 0=low, 1=medium, 2=high (3/4 are unavailable to GL).
# SSR_QUALITY: 0=off, 1=low, 2=medium, 3=high, 4=ultra.
# SSAO_QUALITY: 0=off, 1=low, 2=medium, 3=high (4 is reduced to 3 on iOS).
# SSAO_OPT_DATA: 0=G-buffer, 1=generated full-res depth, 2=half-res depth.
# MSAA_SAMPLES: 0=off, otherwise the real sample count (2, 4 or 8).
NUMERIC_FEATURE_MACROS = (
    "SUN_QUALITY",
    "SSR_QUALITY",
    "SSAO_QUALITY",
    "SSAO_OPT_DATA",
    "MSAA_SAMPLES",
)
NUMERIC_FEATURE_MACRO_SET = frozenset(NUMERIC_FEATURE_MACROS)
SHADER_SOURCE_SUFFIXES = (".h", ".ps", ".vs")

# These exact numeric predicates replace the former presence-test debt. They
# deliberately pin the zero/one and quality-range boundaries; a broad source
# scan could prove no #ifdef exists yet miss a changed condition.
REQUIRED_NUMERIC_VALUE_TESTS = {
    "combine_1.ps": {
        "#if SSAO_QUALITY<=3": 2,
    },
    "ssr.h": {
        "#if (SSR_QUALITY<=1)||(SSR_QUALITY>4)": 1,
        "#if SSR_QUALITY==0": 1,
    },
    "ssao_hbao.ps": {
        "#if SSAO_QUALITY==0": 1,
        "#if SSAO_OPT_DATA==0": 1,
    },
    "ssao_hdao.ps": {
        "#if SSAO_QUALITY>0": 1,
        "#if SSAO_QUALITY==0": 1,
    },
    "ssao_hdao_new.ps": {
        "#if SSAO_QUALITY==0": 2,
    },
}


@dataclass(frozen=True)
class NumericMacroOccurrence:
    relative_path: str
    directive: str
    start_line: int


@dataclass
class PreprocessorBlock:
    block_id: int
    kind: str
    body: str
    start_line: int
    parent_block_ids: tuple[int, ...]
    end_line: int | None = None
    has_alternative: bool = False


@dataclass(frozen=True)
class PreprocessorDefinition:
    macro: str
    value: str
    function_like: bool
    start_line: int
    parent_block_ids: tuple[int, ...]


def splice_backslash_newlines(source: str) -> list[tuple[int, str]]:
    """Apply translation-phase splicing and retain logical-line origins.

    Only a backslash immediately followed by a physical newline is a splice.
    In particular, ``\\ <newline>`` is not a splice. Keeping the first
    physical line lets diagnostics identify the original source location after
    logical directives have been joined.
    """
    logical_lines: list[tuple[int, str]] = []
    current = ""
    first_line = 1
    continuing = False
    for line_number, physical_line in enumerate(source.splitlines(keepends=True), 1):
        if physical_line.endswith("\r\n"):
            text = physical_line[:-2]
            has_newline = True
        elif physical_line.endswith(("\n", "\r")):
            text = physical_line[:-1]
            has_newline = True
        else:
            text = physical_line
            has_newline = False

        if not continuing:
            first_line = line_number
        current += text
        if has_newline and text.endswith("\\"):
            current = current[:-1]
            continuing = True
            continue
        logical_lines.append((first_line, current))
        current = ""
        continuing = False

    if continuing:
        logical_lines.append((first_line, current))
    return logical_lines


def strip_c_comments(source: str) -> str:
    """Replace C/C++ comments with spaces while preserving newlines.

    The caller first splices backslash-newline pairs, matching the preprocessor
    phase order.  Regex stripping would falsely detect a commented-out
    directive and can be confused by comment-looking text in a string or
    character literal, so keep a small lexical state machine instead.
    """
    out: list[str] = []
    index = 0
    state = "code"
    quote = ""
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if char == "/" and next_char == "/":
                out.extend((" ", " "))
                index += 2
                state = "line-comment"
                continue
            if char == "/" and next_char == "*":
                out.extend((" ", " "))
                index += 2
                state = "block-comment"
                continue
            out.append(char)
            if char in ('"', "'"):
                quote = char
                state = "quote"
            index += 1
            continue

        if state == "line-comment":
            if char in "\r\n":
                out.append(char)
                state = "code"
            else:
                out.append(" ")
            index += 1
            continue

        if state == "block-comment":
            if char == "*" and next_char == "/":
                out.extend((" ", " "))
                index += 2
                state = "code"
                continue
            out.append(char if char in "\r\n" else " ")
            index += 1
            continue

        # Quoted text cannot form a preprocessor directive, but preserving it
        # makes comment stripping a safe operation for arbitrary shader text.
        out.append(char)
        if char == "\\" and next_char:
            out.append(next_char)
            index += 2
            continue
        if char == quote:
            state = "code"
        index += 1
    return "".join(out)


def logical_preprocessor_lines(source: str) -> list[tuple[int, str]]:
    """Return comment-free logical lines with first physical source lines.

    Comments are stripped only after exact backslash-newline splicing. This is
    material for ``// ... \\<newline>#directive``: after phase 2 it is one
    line comment, so the apparent directive must remain hidden.
    """
    spliced_lines = splice_backslash_newlines(source)
    if not spliced_lines:
        return []
    stripped = strip_c_comments("\n".join(text for _line, text in spliced_lines))
    return [
        (line_number, text)
        for (line_number, _original), text in zip(
            spliced_lines, stripped.split("\n"), strict=True
        )
    ]


DIRECTIVE_RE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif)\b(.*)$")
CONDITIONAL_DIRECTIVE_RE = re.compile(
    r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)$"
)
DIRECT_PRESENCE_RE = re.compile(r"^\s*([A-Za-z_]\w*)\b")
DIRECT_MACRO_ONLY_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*$")
DEFINED_PRESENCE_RE = re.compile(
    r"\bdefined\s*(?:\(\s*([A-Za-z_]\w*)\s*\)|([A-Za-z_]\w*)\b)"
)
DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)(.*)$")
UNDEF_RE = re.compile(r"^\s*#\s*undef\s+([A-Za-z_]\w*)\b")


def normalize_directive(kind: str, body: str) -> str:
    """Normalize non-semantic whitespace for the frozen legacy ledger."""
    compact_body = re.sub(r"\s+", "", body)
    if kind in {"ifdef", "ifndef"}:
        match = DIRECT_PRESENCE_RE.match(body)
        macro = match.group(1) if match else compact_body
        return f"#{kind} {macro}"
    return f"#{kind} {compact_body}"


def find_numeric_feature_presence_tests(
    root: str,
    common_fallback_guard_lines: frozenset[int] = frozenset(),
) -> list[NumericMacroOccurrence]:
    """Find forbidden presence tests for the numeric feature-macro manifest."""
    occurrences: list[NumericMacroOccurrence] = []
    common_path = os.path.normcase(os.path.abspath(os.path.join(root, "common.h")))
    for directory, _dirs, files in os.walk(root):
        for filename in sorted(files):
            if not filename.endswith(SHADER_SOURCE_SUFFIXES):
                continue
            path = os.path.join(directory, filename)
            with open(path, encoding="utf-8", errors="replace") as source_file:
                source = source_file.read()
            relative_path = os.path.relpath(path, root).replace(os.sep, "/")
            is_common = os.path.normcase(os.path.abspath(path)) == common_path
            for start_line, line in logical_preprocessor_lines(source):
                match = DIRECTIVE_RE.match(line)
                if not match:
                    continue
                kind, body = match.groups()
                macros: set[str] = set()
                if kind in {"ifdef", "ifndef"}:
                    direct_match = DIRECT_PRESENCE_RE.match(body)
                    if direct_match:
                        macros.add(direct_match.group(1))
                else:
                    for parenthesized, bare in DEFINED_PRESENCE_RE.findall(body):
                        macros.add(parenthesized or bare)
                if not macros & NUMERIC_FEATURE_MACRO_SET:
                    continue
                # Only guards proven below to be one of the five exact
                # #ifndef / #define 0 / #endif fallback blocks are
                # declarations.  Any other presence test in common.h is real
                # feature use and must remain visible to the debt contract.
                if (
                    is_common
                    and kind == "ifndef"
                    and start_line in common_fallback_guard_lines
                ):
                    continue
                directive = normalize_directive(kind, body)
                occurrences.append(
                    NumericMacroOccurrence(relative_path, directive, start_line)
                )
    return sorted(
        occurrences,
        key=lambda occurrence: (
            occurrence.relative_path,
            occurrence.directive,
            occurrence.start_line,
        ),
    )


def find_numeric_feature_undefs(root: str) -> list[NumericMacroOccurrence]:
    """Find every non-comment logical numeric #undef in the shader tree."""
    occurrences: list[NumericMacroOccurrence] = []
    for directory, _dirs, files in os.walk(root):
        for filename in sorted(files):
            if not filename.endswith(SHADER_SOURCE_SUFFIXES):
                continue
            path = os.path.join(directory, filename)
            with open(path, encoding="utf-8", errors="replace") as source_file:
                lines = logical_preprocessor_lines(source_file.read())
            relative_path = os.path.relpath(path, root).replace(os.sep, "/")
            for start_line, line in lines:
                undef_match = UNDEF_RE.match(line)
                if not undef_match:
                    continue
                macro = undef_match.group(1)
                if macro in NUMERIC_FEATURE_MACRO_SET:
                    occurrences.append(
                        NumericMacroOccurrence(
                            relative_path, f"#undef {macro}", start_line
                        )
                    )
    return sorted(
        occurrences,
        key=lambda occurrence: (
            occurrence.relative_path,
            occurrence.directive,
            occurrence.start_line,
        ),
    )


def validate_required_numeric_value_tests(root: str) -> list[str]:
    """Require the reviewed numeric replacements for every former debt site."""
    errors: list[str] = []
    expected = Counter(
        (relative_path, directive)
        for relative_path, directives in REQUIRED_NUMERIC_VALUE_TESTS.items()
        for directive, count in directives.items()
        for _ in range(count)
    )
    actual: Counter[tuple[str, str]] = Counter()
    try:
        for relative_path, directives in REQUIRED_NUMERIC_VALUE_TESTS.items():
            path = os.path.join(root, relative_path)
            with open(path, encoding="utf-8", errors="replace") as source_file:
                lines = logical_preprocessor_lines(source_file.read())
            required_directives = frozenset(directives)
            for _start_line, line in lines:
                match = DIRECTIVE_RE.match(line)
                if not match:
                    continue
                kind, body = match.groups()
                if kind not in {"if", "elif"}:
                    continue
                directive = normalize_directive(kind, body)
                if directive in required_directives:
                    actual[(relative_path, directive)] += 1
    except OSError as error:
        return [f"could not read required numeric value test: {error}"]

    for key in sorted(set(actual) | set(expected)):
        actual_count = actual[key]
        expected_count = expected[key]
        if actual_count == expected_count:
            continue
        relative_path, directive = key
        errors.append(
            f"required numeric value test mismatch: {relative_path}: "
            f"{directive} (expected {expected_count}, found {actual_count})"
        )
    return errors


def validate_numeric_feature_fallbacks(root: str) -> tuple[list[str], frozenset[int]]:
    """Require one exact guarded zero fallback block per manifest entry.

    The returned line set contains only guards that are safe to exclude from
    the presence-use scan.  A broad common.h exclusion would let an unrelated
    ``#ifndef`` silently become feature logic, which is the regression this
    contract exists to prevent.
    """
    common_path = os.path.join(root, "common.h")
    try:
        with open(common_path, encoding="utf-8", errors="replace") as common_file:
            lines = logical_preprocessor_lines(common_file.read())
    except OSError as error:
        return [f"common.h: could not read numeric feature fallbacks: {error}"], frozenset()

    blocks: list[PreprocessorBlock] = []
    block_stack: list[PreprocessorBlock] = []
    definitions: dict[str, list[PreprocessorDefinition]] = {
        macro: [] for macro in (*NUMERIC_FEATURE_MACROS, "COMMON_H")
    }
    errors: list[str] = []

    for line_number, line in lines:
        define_match = DEFINE_RE.match(line)
        if define_match:
            macro, tail = define_match.groups()
            if macro in definitions:
                definitions[macro].append(
                    PreprocessorDefinition(
                        macro=macro,
                        value=tail.strip(),
                        function_like=tail.startswith("("),
                        start_line=line_number,
                        parent_block_ids=tuple(
                            block.block_id for block in block_stack
                        ),
                    )
                )

        directive_match = CONDITIONAL_DIRECTIVE_RE.match(line)
        if not directive_match:
            continue
        kind, body = directive_match.groups()
        if kind in {"if", "ifdef", "ifndef"}:
            block = PreprocessorBlock(
                block_id=len(blocks),
                kind=kind,
                body=body,
                start_line=line_number,
                parent_block_ids=tuple(
                    parent.block_id for parent in block_stack
                ),
            )
            blocks.append(block)
            block_stack.append(block)
            continue
        if kind in {"elif", "else"}:
            if not block_stack:
                errors.append(
                    f"common.h:{line_number}: unmatched #{kind} in fallback scan"
                )
            else:
                block_stack[-1].has_alternative = True
            continue
        if not block_stack:
            errors.append(
                f"common.h:{line_number}: unmatched #endif in fallback scan"
            )
            continue
        block_stack.pop().end_line = line_number

    for block in block_stack:
        errors.append(
            f"common.h:{block.start_line}: #{block.kind} has no matching #endif"
        )

    guards: dict[str, list[PreprocessorBlock]] = {
        macro: [] for macro in NUMERIC_FEATURE_MACROS
    }
    for block in blocks:
        if block.kind != "ifndef":
            continue
        macro_match = DIRECT_MACRO_ONLY_RE.fullmatch(block.body)
        if macro_match and macro_match.group(1) in guards:
            guards[macro_match.group(1)].append(block)

    header_guard_uses: list[PreprocessorBlock] = []
    for block in blocks:
        macro_match = DIRECT_PRESENCE_RE.match(block.body)
        if (
            block.kind == "ifndef"
            and macro_match
            and macro_match.group(1) == "COMMON_H"
        ):
            header_guard_uses.append(block)
    header_guard: PreprocessorBlock | None = None
    if len(header_guard_uses) != 1:
        errors.append(
            "common.h: expected exactly one top-level '#ifndef COMMON_H' "
            f"header guard; found {len(header_guard_uses)}"
        )
    else:
        candidate = header_guard_uses[0]
        header_definitions = definitions["COMMON_H"]
        header_is_valid = (
            bool(DIRECT_MACRO_ONLY_RE.fullmatch(candidate.body))
            and not candidate.parent_block_ids
            and candidate.end_line is not None
            and not candidate.has_alternative
            and len(header_definitions) == 1
            and header_definitions[0].value == ""
            and not header_definitions[0].function_like
            and header_definitions[0].parent_block_ids == (candidate.block_id,)
        )
        if header_is_valid:
            header_guard = candidate
        else:
            errors.append(
                "common.h: '#ifndef COMMON_H' must be a complete top-level "
                "header guard with one direct '#define COMMON_H' and no "
                "alternate branch"
            )

    excluded_guard_lines: set[int] = set()
    for macro in NUMERIC_FEATURE_MACROS:
        macro_definitions = definitions[macro]
        if (
            len(macro_definitions) != 1
            or macro_definitions[0].value != "0"
            or macro_definitions[0].function_like
        ):
            errors.append(
                f"common.h: expected exactly one '#define {macro} 0' fallback; "
                f"found {len(macro_definitions)} value(s): "
                f"{[definition.value for definition in macro_definitions] or 'none'}"
            )
        macro_guards = guards[macro]
        if len(macro_guards) != 1:
            errors.append(
                f"common.h: expected exactly one '#ifndef {macro}' fallback guard; "
                f"found {len(macro_guards)}"
            )
            continue
        guard = macro_guards[0]
        block_definitions = [
            definition
            for definition in macro_definitions
            if definition.parent_block_ids
            and definition.parent_block_ids[-1] == guard.block_id
        ]
        block_is_complete = (
            header_guard is not None
            and guard.parent_block_ids == (header_guard.block_id,)
            and guard.end_line is not None
            and not guard.has_alternative
            and len(macro_definitions) == 1
            and len(block_definitions) == 1
            and block_definitions[0].value == "0"
            and not block_definitions[0].function_like
            and block_definitions[0].parent_block_ids
            == (header_guard.block_id, guard.block_id)
        )
        if not block_is_complete:
            errors.append(
                "common.h: expected complete fallback block "
                f"'#ifndef {macro}' / exactly one direct '#define {macro} 0' / "
                "'#endif' as a direct child of the top-level COMMON_H guard"
            )
            continue
        excluded_guard_lines.add(guard.start_line)

    if len(excluded_guard_lines) != len(NUMERIC_FEATURE_MACROS):
        errors.append(
            "common.h: expected exactly five validated numeric fallback blocks; "
            f"found {len(excluded_guard_lines)}"
        )
    return errors, frozenset(excluded_guard_lines)


def check_numeric_feature_macro_contract(root: str) -> list[str]:
    """Return deterministic contract failures; an empty list means PASS.

    This function is intentionally usable without glslang so the mutation suite
    and a cached shader gate can test the static preprocessor contract directly.
    """
    errors, common_fallback_guard_lines = validate_numeric_feature_fallbacks(root)
    errors.extend(validate_required_numeric_value_tests(root))
    try:
        occurrences = find_numeric_feature_presence_tests(
            root, common_fallback_guard_lines
        )
        undef_occurrences = find_numeric_feature_undefs(root)
    except OSError as error:
        return errors + [f"could not scan shader numeric feature macros: {error}"]
    if occurrences:
        errors.append(
            "numeric feature-macro presence debt must be zero; found "
            f"{len(occurrences)} occurrence(s)"
        )
        errors.extend(
            "numeric feature-macro presence test forbidden: "
            f"{occurrence.relative_path}: {occurrence.directive}"
            for occurrence in occurrences
        )
    if undef_occurrences:
        errors.append(
            "numeric feature-macro undef debt must be zero; found "
            f"{len(undef_occurrences)} occurrence(s)"
        )
        errors.extend(
            "numeric feature-macro undef forbidden: "
            f"{occurrence.relative_path}: {occurrence.directive}"
            for occurrence in undef_occurrences
        )
    return errors


def report_numeric_feature_macro_contract(root: str) -> bool:
    errors = check_numeric_feature_macro_contract(root)
    print(
        "Numeric feature-macro contract: "
        + ("PASS (five zero fallbacks; presence debt=0; undef debt=0)" if not errors else "FAIL")
    )
    for error in errors:
        print(f"  {error}")
    return not errors


def automatic_jobs() -> int:
    jobs = min(os.cpu_count() or 1, 10)
    try:
        memory_bytes = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        memory_bytes = 0
    for limit_path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(limit_path, encoding="ascii") as limit_file:
                raw_limit = limit_file.read().strip()
            limit = int(raw_limit)
        except (OSError, ValueError):
            continue
        if limit > 0:
            memory_bytes = limit if memory_bytes <= 0 else min(memory_bytes, limit)
    # Keep approximately 2 GiB per worker available. glslang normally uses far
    # less, but this avoids turning the gate into memory pressure on older Macs.
    if memory_bytes > 0:
        jobs = min(jobs, max(1, memory_bytes // (2 * 1024**3)))
    return jobs


def find_include(name: str, roots: list[str]) -> str | None:
    """Resolve an #include target. Tries the name relative to each root (so
    'iostructs/p_foo.h' works), then falls back to a basename search. Shader
    includes use Windows separators (e.g. `#include "shared\\common.h"`), so
    normalise backslashes to forward slashes — otherwise on Linux CI the whole
    `shared\\common.h` string is one literal filename and never resolves, which
    silently drops the type shims and makes every downstream decl look broken.

    Matching is CASE-INSENSITIVE: several shaders #include a mixed-case name
    (`iostructs\\p_TL.h`, `combine_2_AA.ps`) whose file on disk is lower-case.
    Windows dev and the iOS runtime FS (case-insensitive APFS) resolve these; a
    case-sensitive Linux CI would not, mis-dropping the iostructs header that
    supplies main() and skipping the shader as if it were an include-only helper.
    Resolve case-insensitively so CI matches Windows and the device."""
    name = name.replace("\\", "/")
    for root in roots:
        cand = os.path.normpath(os.path.join(root, name))
        if os.path.isfile(cand):
            return cand
    base = os.path.basename(name).lower()
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.lower() == base:
                    return os.path.join(dirpath, fn)
    return None


def inline(path: str, roots: list[str], seen: set[str], out: list[str]) -> None:
    """Recursively inline #include directives, emitting each file at most once
    (mirrors the shaders' own #ifndef include guards; avoids include cycles)."""
    real = os.path.realpath(path)
    if real in seen:
        return
    seen.add(real)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    pos = 0
    for m in INCLUDE_RE.finditer(text):
        out.append(text[pos:m.start()])
        target = find_include(m.group(1), roots)
        if target:
            inline(target, roots, seen, out)
        else:
            out.append(f"// [shadercheck] MISSING INCLUDE: {m.group(1)}\n")
        pos = m.end()
    out.append(text[pos:])


def assemble(
    shader: str,
    stage: str,
    roots: list[str],
    define_overrides: dict[str, str | None] | None = None,
) -> str:
    lines = ["#version 300 es"]
    lines += VERT_PRECISION if stage == "vert" else FRAG_PRECISION
    define_overrides = define_overrides or {}
    for name, value in BASE_DEFINES:
        effective_value = define_overrides.get(name, value)
        if effective_value is not None:
            lines.append(f"#define {name} {effective_value}")
    base_names = {name for name, _value in BASE_DEFINES}
    for name, value in define_overrides.items():
        if name not in base_names and value is not None:
            lines.append(f"#define {name} {value}")
    body: list[str] = []
    inline(shader, roots, set(), body)
    return "\n".join(lines) + "\n" + "".join(body)


def validate(glslang: str, stage: str, source: str) -> tuple[bool, str]:
    suffix = ".vert" if stage == "vert" else ".frag"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as tf:
        tf.write(source)
        tmp = tf.name
    try:
        # glslangValidator reads `#version 300 es` and validates ES 3.00 rules.
        proc = subprocess.run([glslang, tmp], capture_output=True, text=True)
        ok = proc.returncode == 0
        return ok, (proc.stdout + proc.stderr).strip()
    finally:
        os.unlink(tmp)


def first_error(report: str) -> str:
    """Extract the first real glslang diagnostic. glslangValidator prints the input
    filename first, then `ERROR: <file>:<line>: <message>` lines and an
    `ERROR: N compilation errors` summary — we want the first <message>."""
    for line in report.splitlines():
        s = line.strip()
        if not s.startswith("ERROR:"):
            continue
        msg = s[len("ERROR:"):].strip()
        if "compilation error" in msg.lower():  # the summary line, not a diagnostic
            continue
        msg = re.sub(r"^\S+:\d+:\s*", "", msg)  # strip "file:line: "
        return msg
    for line in report.splitlines():  # fallback: first non-filename, non-warning line
        s = line.strip()
        if s and not s.startswith("/tmp/") and not s.lower().startswith(("warning", "info")):
            return s
    return "(no diagnostic)"


def normalize_error(msg: str) -> str:
    """Collapse a diagnostic to a family key so errors aggregate across shaders."""
    key = re.sub(r"'[^']*'", "'X'", msg)
    key = re.sub(r"\b\d+\b", "N", key)
    return key.strip()


DEAD_FILES = {"ssao_hdao_new.ps"}
ENTRY_RE = re.compile(r'\bvoid\s+main\s*\(')
LOW_SETTINGS_TARGETS = {"combine_1_nomsaa.ps", "ssao_calc.ps"}
SSAO_PROFILES = (
    ("disabled", "combine_1_nomsaa.ps", {"SSAO_QUALITY": "0", "SSAO_OPT_DATA": "0"}),
    ("full-gbuffer", "combine_1_nomsaa.ps", {"SSAO_QUALITY": "3", "SSAO_OPT_DATA": "0"}),
    ("optimized-full", "combine_1_nomsaa.ps", {"SSAO_QUALITY": "3", "SSAO_OPT_DATA": "1"}),
    ("optimized-half", "combine_1_nomsaa.ps", {"SSAO_QUALITY": "3", "SSAO_OPT_DATA": "2"}),
    ("downsample-full", "depth_downs.ps", {"SSAO_QUALITY": "3", "SSAO_OPT_DATA": "1"}),
    ("downsample-half", "depth_downs.ps", {"SSAO_QUALITY": "3", "SSAO_OPT_DATA": "2"}),
)


def build_ssr_profiles() -> list[tuple[str, str, dict[str, str | None]]]:
    profiles: list[tuple[str, str, dict[str, str | None]]] = [
        ("off", "water.ps", {"SSR_QUALITY": "0", "SSR_HALF_DEPTH": None, "SSAO_OPT_DATA": "0"})
    ]
    for quality in range(1, 5):
        profiles.append(
            (
                f"q{quality}-full-depth",
                "water.ps",
                {"SSR_QUALITY": str(quality), "SSR_HALF_DEPTH": None, "SSAO_OPT_DATA": "0"},
            )
        )
        profiles.append(
            (
                f"q{quality}-half-depth",
                "water.ps",
                {"SSR_QUALITY": str(quality), "SSR_HALF_DEPTH": "1", "SSAO_OPT_DATA": "1"},
            )
        )
    return profiles


def ssr_case_id(profile_name: str) -> str:
    if profile_name == "off":
        return "shader:ssr:off"
    match = re.fullmatch(r"q([1-4])-(full|half)-depth", profile_name)
    if match is None:
        raise RuntimeError(f"unsupported SSR profile name: {profile_name}")
    return f"shader:ssr:{match.group(2)}-q{match.group(1)}"


def discover_shader_sources(root: str) -> list[tuple[str, str]]:
    """Return the deterministic source/stage enumeration used by every mode."""
    shaders: list[tuple[str, str]] = []
    for dirpath, _dirs, files in os.walk(root):
        for filename in sorted(files):
            if filename in DEAD_FILES:
                continue
            path = os.path.join(dirpath, filename)
            if filename.endswith(".vs"):
                shaders.append((path, "vert"))
            elif filename.endswith(".ps"):
                shaders.append((path, "frag"))
    return sorted(shaders)


def list_baseline_paths(root: str, roots: list[str]) -> list[str]:
    """List baseline cases without invoking glslang; ordinary mode does not call this."""
    cases: list[str] = []
    for path, stage in discover_shader_sources(root):
        try:
            source = assemble(path, stage, roots)
        except Exception:
            cases.append(path)
            continue
        if ENTRY_RE.search(source):
            cases.append(path)
    return cases


def list_cases_json(root: str, roots: list[str]) -> str:
    sources = discover_shader_sources(root)
    source_names = {os.path.basename(path) for path, _stage in sources}
    ssr_profiles = build_ssr_profiles()
    required = (
        LOW_SETTINGS_TARGETS
        | {target for _label, target, _overrides in SSAO_PROFILES}
        | {target for _label, target, _overrides in ssr_profiles}
    )
    missing = sorted(required - source_names)
    if missing:
        raise RuntimeError(f"variant shader target missing: {', '.join(missing)}")
    baseline = [
        f"shader:baseline:{os.path.relpath(path, root)}"
        for path in list_baseline_paths(root, roots)
    ]
    variants = (
        [f"shader:low:{target}" for target in sorted(LOW_SETTINGS_TARGETS)]
        + [f"shader:ssao:{label}" for label, _target, _overrides in SSAO_PROFILES]
        + [
            ssr_case_id(label)
            for label, _target, _overrides in ssr_profiles
        ]
    )
    return json.dumps(
        {"baseline": baseline, "schema": "openxray.shader-case-list.v1", "variants": variants},
        sort_keys=True, separators=(",", ":"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shaders", default="res/gamedata/shaders/gl")
    ap.add_argument("--glslang", default="glslangValidator")
    ap.add_argument(
        "--macro-contract",
        action="store_true",
        help="validate only the static numeric feature-macro contract",
    )
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--list-json", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="parallel glslang workers; 0 selects a CPU/memory-aware maximum of 10",
    )
    ap.add_argument("--dump", type=int, default=0,
                    help="print full glslang output for the first N failing shaders")
    args = ap.parse_args()
    root = args.shaders
    if args.list_json:
        if args.macro_contract or args.verbose or args.strict or args.dump or args.jobs:
            ap.error("--list-json accepts only --shaders")
        if not os.path.isdir(root):
            print(f"::error::shader dir not found: {root}")
            return 2
        roots = [root, os.path.join(root, "shared"), os.path.join(root, "iostructs")]
        print(list_cases_json(root, roots))
        return 0
    jobs = args.jobs or automatic_jobs()
    if jobs < 1:
        ap.error("--jobs must be 0 or a positive integer")
    if not os.path.isdir(root):
        print(f"::error::shader dir not found: {root}")
        return 2
    if args.macro_contract:
        return 0 if report_numeric_feature_macro_contract(root) else 1
    roots = [root, os.path.join(root, "shared"), os.path.join(root, "iostructs")]

    # Files with a main() the engine nevertheless never compiles in the GL tree — dead/
    # disabled. ssao_hdao_new.ps is a DX-only HDAO *compute* path (RWTexture2D, groupshared,
    # register(u0)); combine_1.ps's include of it is commented out (`//#_include`) and no
    # blender references it. Not GL-portable and not shipped, so don't score it.
    # A .ps/.vs is only a real shader *stage* if its assembled source produces an entry
    # point. The engine generates `void main(){…_main…}` from the iostructs p_*.h / v_*.h
    # header that an entry shader includes; pure helper files that are only ever #included
    # (gather.ps, fxaa.ps, ssao*.ps — confirmed: included by other .ps, referenced by no
    # blender) have no main() and are meaningless to compile standalone. Skip them so the
    # gate scores only real entry shaders; they still get validated via their includers.
    shaders = discover_shader_sources(root)

    def ordered_map(function, items):
        if jobs == 1:
            return list(map(function, items))
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            return list(executor.map(function, items))

    def validate_baseline(item: tuple[str, str]) -> tuple[str, str, str]:
        path, stage = item
        rel = os.path.relpath(path, root)
        try:
            src = assemble(path, stage, roots)
        except Exception as e:  # noqa: BLE001 — report, don't abort the sweep
            return "assemble-fail", rel, f"[assemble error] {e}"
        if not ENTRY_RE.search(src):
            return "skip", rel, ""
        try:
            ok, report = validate(args.glslang, stage, src)
        except Exception as e:  # noqa: BLE001
            return "fail", rel, f"[shadercheck exception] {e}"
        return ("pass" if ok else "fail"), rel, report

    print(f"GLSL ES 3.00 shader workers: {jobs}")
    passed, failed, skipped = 0, [], []
    full_reports: list[tuple[str, str]] = []
    for status, rel, report in ordered_map(validate_baseline, shaders):
        if status == "skip":
            skipped.append(rel)
            if args.verbose:
                print(f"  SKIP {rel} (include-only, no main())")
            continue
        if status == "pass":
            passed += 1
            if args.verbose:
                print(f"  OK   {rel}")
        else:
            error = report if status == "assemble-fail" else first_error(report)
            failed.append((rel, error))
            full_reports.append((rel, report))
            if args.verbose:
                print(f"  FAIL {rel}\n       {error}")

    # Show the full glslang output for the first few failures so the exact token +
    # line of the dominant error is visible (drives the common.h / iostructs fixes).
    for rel, report in full_reports[: args.dump]:
        print(f"\n===== full glslang output: {rel} =====")
        print(report)

    total = len(shaders) - len(skipped)
    print("")
    print(f"GLSL ES 3.00 shader check: {passed}/{total} compile, {len(failed)} fail"
          f" ({len(skipped)} include-only skipped)")

    if failed:
        from collections import Counter
        families = Counter(normalize_error(err) for _rel, err in failed)
        print("\nDominant first-error families (count x message):")
        for msg, n in families.most_common(20):
            print(f"  {n:4d}  {msg}")
        print("\nFirst error per failing shader:")
        for rel, err in failed:
            print(f"  {rel}: {err}")

    # The baseline models the device's high preset. Also compile the entry
    # shaders that include ssao.ps with SSAO_QUALITY=1: the low/no-MSAA runtime
    # permutation takes different preprocessor branches and previously reached
    # the device with an ES-illegal `int + float` expression even though the
    # baseline sweep was green.
    low_settings_targets = LOW_SETTINGS_TARGETS
    low_passed = 0
    low_failed: list[tuple[str, str]] = []

    def validate_variant(item):
        label, path, stage, overrides = item
        try:
            src = assemble(path, stage, roots, overrides)
            ok, report = validate(args.glslang, stage, src)
        except Exception as e:  # noqa: BLE001
            ok, report = False, f"[shadercheck exception] {e}"
        return label, ok, report

    low_variants = [
        (
            os.path.relpath(path, root),
            path,
            stage,
            {"SSAO_QUALITY": "1"},
        )
        for path, stage in shaders
        if os.path.basename(path) in low_settings_targets
    ]
    for rel, ok, report in ordered_map(validate_variant, low_variants):
        if ok:
            low_passed += 1
        else:
            low_failed.append((rel, first_error(report)))

    low_total = len(low_settings_targets)
    print(
        f"GLSL ES 3.00 low-settings profile: "
        f"{low_passed}/{low_total} compile, {len(low_failed)} fail"
    )
    for rel, err in low_failed:
        print(f"  {rel}: {err}")

    # SSAO has a CPU/GPU resource-selection contract that ordinary compilation
    # cannot infer: explicit zero defaults in common.h must be tested by VALUE,
    # not by macro presence. A presence test made SSAO_OPT_DATA=0 sample the
    # ungenerated half-depth buffer on device. Compile both resource branches and
    # keep a small structural assertion so that exact semantic regression cannot
    # return while all shaders still compile.
    shader_targets_by_name = {
        os.path.basename(path): (path, stage)
        for path, stage in shaders
    }
    ssao_profiles = list(SSAO_PROFILES)
    ssao_passed = 0
    ssao_failed: list[tuple[str, str]] = []
    ssao_variants = []
    for profile_name, target_name, overrides in ssao_profiles:
        target = shader_targets_by_name.get(target_name)
        if target is None:
            ssao_failed.append((profile_name, f"{target_name} missing"))
            continue
        path, stage = target
        ssao_variants.append((profile_name, path, stage, overrides))
    for profile_name, ok, report in ordered_map(validate_variant, ssao_variants):
        if ok:
            ssao_passed += 1
        else:
            ssao_failed.append((profile_name, first_error(report)))

    # Water SSR has a separate resource permutation.  Full depth must compile
    # without SSR_HALF_DEPTH at every supported quality; half depth is valid
    # only when SSAO_OPT_DATA selects the generated depth target that the GL
    # renderer allocates and fills. ``None`` deliberately removes the baseline
    # define, which is different from an explicit numeric zero for this
    # presence-style resource switch.
    ssr_profiles = build_ssr_profiles()
    ssr_passed = 0
    ssr_failed: list[tuple[str, str]] = []
    ssr_variants = []
    for profile_name, target_name, overrides in ssr_profiles:
        target = shader_targets_by_name.get(target_name)
        if target is None:
            ssr_failed.append((profile_name, f"{target_name} missing"))
            continue
        path, stage = target
        ssr_variants.append((profile_name, path, stage, overrides))
    for profile_name, ok, report in ordered_map(validate_variant, ssr_variants):
        if ok:
            ssr_passed += 1
        else:
            ssr_failed.append((profile_name, first_error(report)))

    ssao_source_path = os.path.join(root, "ssao.ps")
    with open(ssao_source_path, "r", encoding="utf-8", errors="replace") as fh:
        ssao_source = fh.read()
    required_value_tests = [
        re.compile(r"^\s*#\s*if\s+SSAO_QUALITY\s*==\s*0\s*$", re.MULTILINE),
        re.compile(r"^\s*#\s*if\s+SSAO_OPT_DATA\s*==\s*0\s*$", re.MULTILINE),
    ]
    forbidden_presence_test = re.compile(
        r"^\s*#\s*(?:ifdef|ifndef)\s+(?:SSAO_QUALITY|SSAO_OPT_DATA)\b",
        re.MULTILINE,
    )
    ssao_contract_ok = (
        all(pattern.search(ssao_source) for pattern in required_value_tests)
        and forbidden_presence_test.search(ssao_source) is None
    )

    print(
        f"GLSL ES 3.00 SSAO branch profile: "
        f"{ssao_passed}/{len(ssao_profiles)} compile, {len(ssao_failed)} fail"
    )
    for profile_name, err in ssao_failed:
        print(f"  {profile_name}: {err}")
    print(
        f"GLSL ES 3.00 SSR branch profile: "
        f"{ssr_passed}/{len(ssr_profiles)} compile, {len(ssr_failed)} fail"
    )
    for profile_name, err in ssr_failed:
        print(f"  {profile_name}: {err}")
    print(
        "SSAO value-macro contract: "
        + ("PASS" if ssao_contract_ok else "FAIL (use numeric #if, never #ifdef/#ifndef)")
    )
    numeric_macro_contract_ok = report_numeric_feature_macro_contract(root)

    if args.strict and (
        failed
        or low_failed
        or ssao_failed
        or ssr_failed
        or not ssao_contract_ok
        or not numeric_macro_contract_ok
    ):
        failure_count = (
            len(failed)
            + len(low_failed)
            + len(ssao_failed)
            + len(ssr_failed)
            + (0 if ssao_contract_ok else 1)
            + (0 if numeric_macro_contract_ok else 1)
        )
        print(f"\n::error::{failure_count} shader checks fail GLSL ES 3.00 validation")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
