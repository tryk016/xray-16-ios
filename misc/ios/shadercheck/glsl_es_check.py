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


def find_include(name: str, roots: list[str]) -> str | None:
    """Resolve an #include target. Tries the name relative to each root (so
    'iostructs/p_foo.h' works), then falls back to a basename search. Shader
    includes use Windows separators (e.g. `#include "shared\\common.h"`), so
    normalise backslashes to forward slashes — otherwise on Linux CI the whole
    `shared\\common.h` string is one literal filename and never resolves, which
    silently drops the type shims and makes every downstream decl look broken."""
    name = name.replace("\\", "/")
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shaders", default="res/gamedata/shaders/gl")
    ap.add_argument("--glslang", default="glslangValidator")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--dump", type=int, default=0,
                    help="print full glslang output for the first N failing shaders")
    args = ap.parse_args()

    root = args.shaders
    if not os.path.isdir(root):
        print(f"::error::shader dir not found: {root}")
        return 2
    roots = [root, os.path.join(root, "shared"), os.path.join(root, "iostructs")]

    # Files with a main() the engine nevertheless never compiles in the GL tree — dead/
    # disabled. ssao_hdao_new.ps is a DX-only HDAO *compute* path (RWTexture2D, groupshared,
    # register(u0)); combine_1.ps's include of it is commented out (`//#_include`) and no
    # blender references it. Not GL-portable and not shipped, so don't score it.
    DEAD_FILES = {"ssao_hdao_new.ps"}

    shaders: list[tuple[str, str]] = []
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f in DEAD_FILES:
                continue
            if f.endswith(".vs"):
                shaders.append((os.path.join(dirpath, f), "vert"))
            elif f.endswith(".ps"):
                shaders.append((os.path.join(dirpath, f), "frag"))
    shaders.sort()

    # A .ps/.vs is only a real shader *stage* if its assembled source produces an entry
    # point. The engine generates `void main(){…_main…}` from the iostructs p_*.h / v_*.h
    # header that an entry shader includes; pure helper files that are only ever #included
    # (gather.ps, fxaa.ps, ssao*.ps — confirmed: included by other .ps, referenced by no
    # blender) have no main() and are meaningless to compile standalone. Skip them so the
    # gate scores only real entry shaders; they still get validated via their includers.
    ENTRY_RE = re.compile(r'\bvoid\s+main\s*\(')

    passed, failed, skipped = 0, [], []
    full_reports: list[tuple[str, str]] = []
    for path, stage in shaders:
        rel = os.path.relpath(path, root)
        try:
            src = assemble(path, stage, roots)
        except Exception as e:  # noqa: BLE001 — report, don't abort the sweep
            failed.append((rel, f"[assemble error] {e}"))
            full_reports.append((rel, str(e)))
            continue
        if not ENTRY_RE.search(src):
            skipped.append(rel)  # include-only helper — no shader entry point
            if args.verbose:
                print(f"  SKIP {rel} (include-only, no main())")
            continue
        try:
            ok, report = validate(args.glslang, stage, src)
        except Exception as e:  # noqa: BLE001
            ok, report = False, f"[shadercheck exception] {e}"
        if ok:
            passed += 1
            if args.verbose:
                print(f"  OK   {rel}")
        else:
            failed.append((rel, first_error(report)))
            full_reports.append((rel, report))
            if args.verbose:
                print(f"  FAIL {rel}\n       {first_error(report)}")

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

    if args.strict and failed:
        print(f"\n::error::{len(failed)} shaders fail GLSL ES 3.00 validation")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
