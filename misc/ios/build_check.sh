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
cd "$REPO_ROOT" || exit 1

# A migrated Apple Silicon Mac can retain Intel Homebrew links in /usr/local.
# Prefer the native prefix explicitly so non-interactive runners do not attempt
# to execute an x86_64 cmake or glslang without Rosetta.
if [ "$(uname -m)" = "arm64" ] && [ -d /opt/homebrew/bin ]; then
    PATH="/opt/homebrew/bin:$PATH"
fi

BUILD_DIR="build/ios-engine-iphoneos"
FULL_GATE_STAMP="$BUILD_DIR/.ios_full_gate_ok"
EXPECT_COMPILE="279/279"
EXPECT_LINK="137/137"
EXPECT_GLSLANG="16.4.0"
EXPECT_DEPLOYMENT_TARGET="16.4"
MIN_DEBUG_INFO_BYTES=104857600

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
    "$GLSLANG" --version | grep -q "Glslang Version:.*$EXPECT_GLSLANG" \
        || fail "glslangValidator $EXPECT_GLSLANG is required; found: $($GLSLANG --version | head -1)"

    echo "== shader compile gate =="
    out=$(python3 misc/ios/shadercheck/glsl_es_check.py --glslang "$GLSLANG" --strict 2>&1) \
        || fail "glsl_es_check.py errored:\n$out"
    echo "$out" | tail -4
    echo "$out" | grep -q "$EXPECT_COMPILE compile" \
        || fail "expected $EXPECT_COMPILE compiling shaders. A regression here means a shader no longer builds as GLSL ES 3.00."
    echo "$out" | grep -q "low-settings profile: 2/2 compile, 0 fail" \
        || fail "low-settings shader profile failed. Check the no-MSAA / SSAO_QUALITY=1 runtime permutation."
    echo "$out" | grep -q "SSAO branch profile: 6/6 compile, 0 fail" \
        || fail "SSAO resource-branch profile failed. Check disabled, G-buffer, and optimized full/half permutations."
    echo "$out" | grep -q "SSAO value-macro contract: PASS" \
        || fail "SSAO feature macros must be tested numerically; presence tests select resources the CPU did not populate."

    echo "== shader link gate =="
    out=$(python3 misc/ios/shadercheck/link_check.py --strict 2>&1) \
        || fail "link_check.py errored:\n$out"
    echo "$out" | tail -1
    echo "$out" | grep -q "$EXPECT_LINK pairs clean" \
        || fail "expected $EXPECT_LINK clean vs->fs pairs. ES links varyings by NAME, so a mismatch here disables a render pass on device."
fi

if [ "$run_engine" = 1 ]; then
    [ -d "$BUILD_DIR" ] || fail "$BUILD_DIR missing — configure it once first (doc/iOS-Port-Journal.md, slice 6.12)"
    grep -q "^CMAKE_OSX_DEPLOYMENT_TARGET:INTERNAL=$EXPECT_DEPLOYMENT_TARGET$" "$BUILD_DIR/CMakeCache.txt" \
        || fail "$BUILD_DIR must target iOS $EXPECT_DEPLOYMENT_TARGET; reconfigure the build tree"

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
        grep -E "CMake Error|BUILD FAILED|The following build commands failed" "$log" | head -20
        echo "--- full log: $log ---"
        fail "engine build failed"
    fi
    compiled=$(grep -c "CompileC" "$log" || true)
    echo "built OK ($compiled translation unit(s) recompiled)"
    rm -f "$log"

    # A target-only dSYM can have a valid UUID and a tiny non-empty debug_info
    # section while all static engine libraries were compiled without DWARF.
    # The clean reference is ~590 MB, so 100 MiB is a conservative completeness
    # floor that still leaves ample room for linker/toolchain variation.
    grep -q '^CMAKE_XCODE_ATTRIBUTE_GCC_GENERATE_DEBUGGING_SYMBOLS:.*=YES$' \
        "$BUILD_DIR/CMakeCache.txt" \
        || fail "device gate requires GCC_GENERATE_DEBUGGING_SYMBOLS=YES"
    app="$REPO_ROOT/bin/aarch64/Release/xr_3da.app"
    dsym="$REPO_ROOT/bin/aarch64/Release/xr_3da.app.dSYM"
    [ -x "$app/xr_3da" ] || fail "symbol-enabled build did not produce $app/xr_3da"
    [ -d "$dsym" ] || fail "symbol-enabled build did not produce $dsym"
    debug_info_bytes=$(xcrun dwarfdump --show-section-sizes "$dsym" \
        | awk '$1 == "__debug_info" {print $2; exit}')
    [ -n "$debug_info_bytes" ] \
        || fail "dSYM has no __debug_info section"
    [ "$debug_info_bytes" -ge "$MIN_DEBUG_INFO_BYTES" ] \
        || fail "dSYM __debug_info is only $debug_info_bytes bytes; expected at least $MIN_DEBUG_INFO_BYTES from the complete target graph"
    bin_uuid=$(xcrun dwarfdump --uuid "$app/xr_3da" | awk '{print $2}' | sort)
    dsym_uuid=$(xcrun dwarfdump --uuid "$dsym" | awk '{print $2}' | sort)
    [ -n "$bin_uuid" ] && [ "$bin_uuid" = "$dsym_uuid" ] \
        || fail "dSYM UUID does not match the app binary"
    echo "dSYM OK ($debug_info_bytes __debug_info bytes, UUID $bin_uuid)"
fi

if [ "$run_shaders" = 1 ] && [ "$run_engine" = 1 ]; then
    touch "$FULL_GATE_STAMP"
fi

echo ""
echo "PASS — safe to push."
