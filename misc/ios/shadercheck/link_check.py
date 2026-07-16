#!/usr/bin/env python3
"""Offline GLSL ES 3.00 *linkage* check for vs->fs varyings.

glslangValidator's own linker is too lenient to catch cross-stage varying interface
mismatches (an `in` the vertex stage never wrote, or a name/type disagreement) — but
the on-device ES driver rejects them hard ("Input of fragment shader X not written by
vertex shader"). After the slot-based varying rename (every varying named xrvary<loc>),
a paired VS out and FS in at the same slot share a name; this checks that they also
share a TYPE and that the FS never reads a varying the VS doesn't write.

Pairings come from the blender scripts (`.s`): `shader:begin("<vs>", "<fs>")`. For each
pair we assemble both stages (like glsl_es_check) and compare their VARYING() decls.
Because the assembler doesn't resolve #ifdefs, a varying may appear with several types
across mutually-exclusive branches; we treat a FS `in` as satisfied if SOME VS `out` at
that name carries a compatible type, so this is an approximation that surfaces the gross
mismatches (which is what the port needs) rather than a proof.
"""
from __future__ import annotations
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glsl_es_check as g  # reuse assemble()

BEGIN = re.compile(r'shader\s*:\s*begin\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"')
VARY = re.compile(r'VARYING\(\w+\)\s*(out|in)\s+(\w+)\s+(xrvary\d+)')


def varyings(path: str, stage: str, roots: list[str]) -> dict[str, set[str]]:
    """name -> set of declared types for the given io direction."""
    want = "out" if stage == "vert" else "in"
    src = g.assemble(path, stage, roots)
    d: dict[str, set[str]] = {}
    for m in VARY.finditer(src):
        io, typ, name = m.groups()
        if io == want:
            d.setdefault(name, set()).add(typ)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shaders", default="res/gamedata/shaders/gl")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    root = args.shaders
    roots = [root, os.path.join(root, "shared"), os.path.join(root, "iostructs")]

    pairs: set[tuple[str, str]] = set()
    for f in os.listdir(root):
        if f.endswith(".s"):
            for vs, fs in BEGIN.findall(open(os.path.join(root, f), encoding="utf-8", errors="replace").read()):
                pairs.add((vs, fs))

    # The core render-target passes are hard-coded C++ blenders (CBlender_*::Compile) that
    # call r_Pass("<vs>", "<ps>", ...) — NOT .s scripts — and they are exactly the ones
    # created at boot (CRenderTarget). Scan them too so the boot-critical vs/fs pairs are
    # covered. Only literal-string pairs are found (a few blenders build names dynamically).
    CPP_RPASS = re.compile(r'r_Pass\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"')
    for base in ("src/Layers/xrRender/blenders", "src/Layers/xrRender",
                 "src/Layers/xrRender_R2", "src/Layers/xrRenderPC_GL"):
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if not f.endswith(".cpp"):
                    continue
                txt = open(os.path.join(dirpath, f), encoding="utf-8", errors="replace").read()
                for vs, fs in CPP_RPASS.findall(txt):
                    if vs != "null" and fs != "null":
                        pairs.add((vs, fs))

    ok = 0
    bad: list[tuple[str, str, list[str]]] = []
    for vs, fs in sorted(pairs):
        vp, fp = os.path.join(root, vs + ".vs"), os.path.join(root, fs + ".ps")
        if not (os.path.isfile(vp) and os.path.isfile(fp)):
            continue
        try:
            vo = varyings(vp, "vert", roots)
            fi = varyings(fp, "frag", roots)
        except Exception as e:  # noqa: BLE001
            bad.append((vs, fs, [f"[error] {e}"])); continue
        problems = []
        for name, types in sorted(fi.items()):
            if name not in vo:
                problems.append(f"FS reads {name} ({'/'.join(types)}) — VS never writes it")
            elif not (types & vo[name]):
                problems.append(f"{name}: FS in {'/'.join(types)} vs VS out {'/'.join(vo[name])} — type mismatch")
        if problems:
            bad.append((vs, fs, problems))
        else:
            ok += 1
            if args.verbose:
                print(f"  OK   {vs} | {fs}")

    print(f"\nvs->fs link check: {ok}/{ok + len(bad)} pairs clean, {len(bad)} with issues")
    for vs, fs, problems in bad:
        print(f"\n  [{vs} | {fs}]")
        for p in problems:
            print(f"     {p}")
    if args.strict and bad:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
