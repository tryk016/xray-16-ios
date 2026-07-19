#!/usr/bin/env bash
#
# iOS pre-push gate. Run this from the repo root BEFORE every push that touches
# engine code or shaders. It is the local stand-in for the ~18-minute CI round
# trip: the shader gates take seconds, the incremental engine build ~20 s.
#
#   ./misc/ios/build_check.sh              # shaders + incremental engine build
#   ./misc/ios/build_check.sh --shaders    # shaders only (no C++ touched)
#   ./misc/ios/build_check.sh --engine     # engine only (no shaders touched)
#
# Exit code 0 = safe to push. Anything else = do not push.
#
# Requires the engine build tree to exist already (see "First-time setup" in
# doc/iOS-Port-Journal.md, slice 6.12). This script never configures; it only
# builds, so a missing tree is a hard error rather than a silent 40-minute wait.

set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

BUILD_DIR="build/ios-engine-iphoneos"
EXPECT_COMPILE="279/279"
EXPECT_LINK="137/137"

run_shaders=1
run_engine=1
case "${1:-}" in
    --shaders) run_engine=0 ;;
    --engine)  run_shaders=0 ;;
    "")        ;;
    *) echo "usage: $0 [--shaders|--engine]" >&2; exit 2 ;;
esac

# glslang: the vendored copy wins, otherwise whatever is on PATH (brew installs
# 16.4.0, which is the version both gates were tuned against).
GLSLANG="$REPO_ROOT/tools/glslang/bin/glslangValidator"
[ -x "$GLSLANG" ] || GLSLANG="$(command -v glslangValidator || true)"

fail() { echo ""; echo "FAIL: $*"; echo "DO NOT PUSH."; exit 1; }

if [ "$run_shaders" = 1 ]; then
    [ -n "$GLSLANG" ] || fail "glslangValidator not found (brew install glslang)"

    echo "== shader compile gate =="
    out=$(python3 misc/ios/shadercheck/glsl_es_check.py --glslang "$GLSLANG" 2>&1) \
        || fail "glsl_es_check.py errored:\n$out"
    echo "$out" | tail -1
    echo "$out" | grep -q "$EXPECT_COMPILE compile" \
        || fail "expected $EXPECT_COMPILE compiling shaders. A regression here means a shader no longer builds as GLSL ES 3.00."

    echo "== shader link gate =="
    out=$(python3 misc/ios/shadercheck/link_check.py 2>&1) \
        || fail "link_check.py errored:\n$out"
    echo "$out" | tail -1
    echo "$out" | grep -q "$EXPECT_LINK pairs clean" \
        || fail "expected $EXPECT_LINK clean vs->fs pairs. ES links varyings by NAME, so a mismatch here disables a render pass on device."
fi

if [ "$run_engine" = 1 ]; then
    [ -d "$BUILD_DIR" ] || fail "$BUILD_DIR missing — configure it once first (doc/iOS-Port-Journal.md, slice 6.12)"

    echo "== incremental engine build (arm64 device) =="
    log=$(mktemp -t xrbuild)
    if ! cmake --build "$BUILD_DIR" --config Release --target xr_3da --parallel 8 > "$log" 2>&1; then
        echo ""
        echo "--- first errors ---"
        # Match only real diagnostics (file:line:col: error:). A bare "error"
        # grep also hits every compiler command line, which carries
        # -Wl,-undefined,error and buries the actual failure.
        grep -E "^[^[:space:]].*:[0-9]+:[0-9]+: (error|fatal error):" "$log" | head -20
        grep -E "^(ld|clang|Undefined symbols|  \"_)" "$log" | head -10
        echo "--- full log: $log ---"
        fail "engine build failed"
    fi
    compiled=$(grep -c "CompileC" "$log" || true)
    echo "built OK ($compiled translation unit(s) recompiled)"
    rm -f "$log"
fi

echo ""
echo "PASS — safe to push."
