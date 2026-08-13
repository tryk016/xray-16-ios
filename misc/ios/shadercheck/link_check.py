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
import argparse, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glsl_es_check as g  # reuse assemble()

FEEDBACK_EMIT = None


def install_shader_feedback() -> object | None:
    """Install the non-secret raw sink for this strict checker, if supplied."""
    try:
        ios_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ios_dir not in sys.path:
            sys.path.insert(0, ios_dir)
        from test_feedback_unittest import install_from_environment
        return install_from_environment("shader::link", sys.argv)
    except BaseException:
        return None


def feedback_case(identifier: str, result: str, started_ns: int) -> None:
    if FEEDBACK_EMIT is None:
        return
    try:
        FEEDBACK_EMIT(identifier, result, started_ns)
    except BaseException:
        pass

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


def discover_pairs(root: str) -> list[tuple[str, str]]:
    """Return the exact deterministic pair enumeration used by the checker."""
    pairs: set[tuple[str, str]] = set()
    for filename in os.listdir(root):
        if filename.endswith(".s"):
            with open(os.path.join(root, filename), encoding="utf-8", errors="replace") as source:
                pairs.update(BEGIN.findall(source.read()))
    cpp_rpass = re.compile(r'r_Pass\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"')
    for base in ("src/Layers/xrRender/blenders", "src/Layers/xrRender",
                 "src/Layers/xrRender_R2", "src/Layers/xrRenderPC_GL"):
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for filename in files:
                if filename.endswith(".cpp"):
                    with open(os.path.join(dirpath, filename), encoding="utf-8",
                              errors="replace") as source:
                        for vs, fs in cpp_rpass.findall(source.read()):
                            if vs != "null" and fs != "null":
                                pairs.add((vs, fs))
    return sorted(pairs)


def list_pairs_json(root: str) -> str:
    pairs = [
        (vs, fs) for vs, fs in discover_pairs(root)
        if os.path.isfile(os.path.join(root, vs + ".vs"))
        and os.path.isfile(os.path.join(root, fs + ".ps"))
    ]
    return json.dumps(
        {"links": [f"shader-link:{vs}|{fs}" for vs, fs in pairs],
         "schema": "openxray.shader-link-list.v1"},
        sort_keys=True, separators=(",", ":"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shaders", default="res/gamedata/shaders/gl")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--list-json", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    root = args.shaders
    roots = [root, os.path.join(root, "shared"), os.path.join(root, "iostructs")]
    if args.list_json:
        if args.verbose or args.strict:
            ap.error("--list-json accepts only --shaders")
        print(list_pairs_json(root))
        return 0
    global FEEDBACK_EMIT
    if args.strict and os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
        if install_shader_feedback() is not None:
            from test_feedback_unittest import emit_raw
            FEEDBACK_EMIT = emit_raw
    pairs = discover_pairs(root)

    ok = 0
    bad: list[tuple[str, str, list[str]]] = []
    for vs, fs in pairs:
        started_ns = time.monotonic_ns()
        vp, fp = os.path.join(root, vs + ".vs"), os.path.join(root, fs + ".ps")
        if not (os.path.isfile(vp) and os.path.isfile(fp)):
            continue
        try:
            vo = varyings(vp, "vert", roots)
            fi = varyings(fp, "frag", roots)
        except Exception as e:  # noqa: BLE001
            bad.append((vs, fs, [f"[error] {e}"]))
            feedback_case(f"shader-link:{vs}|{fs}", "ERROR", started_ns)
            continue
        problems = []
        for name, types in sorted(fi.items()):
            if name not in vo:
                problems.append(f"FS reads {name} ({'/'.join(types)}) — VS never writes it")
            elif not (types & vo[name]):
                problems.append(f"{name}: FS in {'/'.join(types)} vs VS out {'/'.join(vo[name])} — type mismatch")
        if problems:
            bad.append((vs, fs, problems))
            feedback_case(f"shader-link:{vs}|{fs}", "FAIL", started_ns)
        else:
            ok += 1
            feedback_case(f"shader-link:{vs}|{fs}", "PASS", started_ns)
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
