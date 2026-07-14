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
]

# ES fragment shaders have no default float precision, and sampler types need an
# explicit default too. The engine relies on desktop GL's implicit precision;
# under ES we must state it. (Once shared/common.h is ported this moves there.)
FRAG_PRECISION = [
    "precision highp float;",
    "precision highp int;",
    "precision highp sampler2D;",
    "precision highp sampler3D;",
    "precision highp samplerCube;",
    "precision highp sampler2DArray;",
    "precision highp sampler2DShadow;",
]
VERT_PRECISION = [
    "precision highp float;",
    "precision highp int;",
]


def find_include(name: str, roots: list[str]) -> str | None:
    """Resolve an #include target. Tries the name relative to each root (so
    'iostructs/p_foo.h' works), then falls back to a basename search."""
    for root in roots:
        cand = os.path.normpath(os.path.join(root, name))
        if os.path.isfile(cand):
            return cand
    base = os.path.basename(name)
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            if base in files:
                return os.path.join(dirpath, base)
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


def assemble(shader: str, stage: str, roots: list[str]) -> str:
    lines = ["#version 300 es"]
    lines += VERT_PRECISION if stage == "vert" else FRAG_PRECISION
    for name, value in BASE_DEFINES:
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
    for line in report.splitlines():
        s = line.strip()
        if s and not s.lower().startswith(("warning", "info")) and "warning:" not in s.lower():
            return s
    return report.splitlines()[0] if report.splitlines() else "(no diagnostic)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shaders", default="res/gamedata/shaders/gl")
    ap.add_argument("--glslang", default="glslangValidator")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    root = args.shaders
    if not os.path.isdir(root):
        print(f"::error::shader dir not found: {root}")
        return 2
    roots = [root, os.path.join(root, "shared"), os.path.join(root, "iostructs")]

    shaders: list[tuple[str, str]] = []
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f.endswith(".vs"):
                shaders.append((os.path.join(dirpath, f), "vert"))
            elif f.endswith(".ps"):
                shaders.append((os.path.join(dirpath, f), "frag"))
    shaders.sort()

    passed, failed = 0, []
    for path, stage in shaders:
        try:
            src = assemble(path, stage, roots)
            ok, report = validate(args.glslang, stage, src)
        except Exception as e:  # noqa: BLE001 — report, don't abort the sweep
            ok, report = False, f"[shadercheck exception] {e}"
        rel = os.path.relpath(path, root)
        if ok:
            passed += 1
            if args.verbose:
                print(f"  OK   {rel}")
        else:
            failed.append((rel, first_error(report)))
            if args.verbose:
                print(f"  FAIL {rel}\n       {first_error(report)}")

    total = len(shaders)
    print("")
    print(f"GLSL ES 3.00 shader check: {passed}/{total} compile, {len(failed)} fail")
    if failed:
        print("\nFirst error per failing shader:")
        for rel, err in failed:
            print(f"  {rel}: {err}")

    if args.strict and failed:
        print(f"\n::error::{len(failed)} shaders fail GLSL ES 3.00 validation")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
