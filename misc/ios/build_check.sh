#!/usr/bin/env bash
#
# iOS local gate. Its shader compile/link results are cached by content hash for
# fast device iterations. Run --full before every push that touches engine code
# or shaders; --full deliberately ignores the shader cache.
#
#   ./misc/ios/build_check.sh              # cached shaders + incremental engine
#   ./misc/ios/build_check.sh --full       # uncached pre-push gate
#   ./misc/ios/build_check.sh --shaders    # shaders only (no C++ touched)
#   ./misc/ios/build_check.sh --engine     # engine only (no shaders touched)
#
# Exit code 0 means the selected gate passed. Only --full is the uncached
# pre-push contract; the default invocation writes an installable-device stamp.
#
# Requires the engine build tree to exist already (see "First-time setup" in
# doc/iOS-Port-Journal.md, slice 6.12). It refreshes that existing CMake
# configuration so source-list changes cannot be skipped, but a missing tree is
# still a hard error rather than a silent dependency rebuild.

set -u -o pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

# A migrated Apple Silicon Mac can retain Intel Homebrew links in /usr/local.
# Prefer the native prefix explicitly so non-interactive runners do not attempt
# to execute an x86_64 cmake or glslang without Rosetta.
if [ "$(uname -m)" = "arm64" ] && [ -d /opt/homebrew/bin ]; then
    PATH="/opt/homebrew/bin:$PATH"
fi

BUILD_DIR="build/ios-engine-iphoneos"
PREFIX_DIR="$REPO_ROOT/build/ios-prefix-iphoneos"
DEVICE_GATE_STAMP="$BUILD_DIR/.ios_device_gate_ok"
FULL_GATE_STAMP="$BUILD_DIR/.ios_full_gate_ok"
CMAKE_CONFIG_STAMP="$BUILD_DIR/.ios_cmake_inputs.sha256"
GATE_CACHE_DIR="$BUILD_DIR/.ios_gate_cache"
EXPECT_COMPILE="279/279"
EXPECT_LINK="137/137"
EXPECT_GLSLANG="16.4.0"
EXPECT_DEPLOYMENT_TARGET="16.4"
MIN_DEBUG_INFO_BYTES=104857600

run_shaders=1
run_engine=1
force_shader_gate=0
case "${1:-}" in
    --full)    force_shader_gate=1 ;;
    --shaders) run_engine=0 ;;
    --engine)  run_shaders=0 ;;
    "")        ;;
    *) echo "usage: $0 [--full|--shaders|--engine]" >&2; exit 2 ;;
esac

# glslang: the vendored copy wins, otherwise whatever is on PATH (brew installs
# 16.4.0, which is the version both gates were tuned against).
GLSLANG="$REPO_ROOT/tools/glslang/bin/glslangValidator"
[ -x "$GLSLANG" ] || GLSLANG="$(command -v glslangValidator || true)"

fail() { echo ""; echo "FAIL: $*"; echo "DO NOT PUSH."; exit 1; }

openal_fields=""
OPENAL_PROVIDER=""
OPENAL_SHA256=""
run_openal_configured() {
    openal_fields=$(python3 "$REPO_ROOT/misc/ios/openal_provider_contract.py" configured \
        --cache "$BUILD_DIR/CMakeCache.txt" --prefix "$PREFIX_DIR" \
        --project "$BUILD_DIR/OpenXRay.xcodeproj" --platform iphoneos) \
        || fail "OpenAL provider configured contract failed"
}
run_openal_artifact() {
    local binary="$1"
    openal_fields=$(python3 "$REPO_ROOT/misc/ios/openal_provider_contract.py" artifact \
        --prefix "$PREFIX_DIR" --binary "$binary" --platform iphoneos) \
        || fail "OpenAL provider artifact contract failed"
    OPENAL_PROVIDER=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_provider" {print $2}')
    OPENAL_SHA256=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_sha256" {print $2}')
    [ "$OPENAL_PROVIDER" = "OpenALSoft-1.25.2-static" ] \
        && [[ "$OPENAL_SHA256" =~ ^[0-9a-f]{64}$ ]] \
        || fail "OpenAL provider artifact contract returned incomplete stamp fields"
}

policy_test=""
lifecycle_test=""
audio_interruption_test=""
texture_memory_test=""
texture_bc_fallback_test=""
texture_bc_fallback_sanitized_test=""
sector_fallback_test=""
sector_fallback_sanitized_test=""
ui_focus_geometry_test=""
ui_focus_geometry_sanitized_test=""
capture_state_test=""
capture_state_sanitized_test=""
diagnostic_input_state_test=""
diagnostic_input_state_sanitized_test=""
gate_start=$(mktemp "${TMPDIR:-/tmp}/ios-gate-start.XXXXXX") \
    || fail "could not create gate start marker"
cleanup() {
    rm -f "$gate_start"
    [ -z "$policy_test" ] || rm -f "$policy_test"
    [ -z "$lifecycle_test" ] || rm -f "$lifecycle_test"
    [ -z "$audio_interruption_test" ] || rm -f "$audio_interruption_test"
    [ -z "$texture_memory_test" ] || rm -f "$texture_memory_test"
    [ -z "$texture_bc_fallback_test" ] || rm -f "$texture_bc_fallback_test"
    [ -z "$texture_bc_fallback_sanitized_test" ] || rm -f "$texture_bc_fallback_sanitized_test"
    [ -z "$sector_fallback_test" ] || rm -f "$sector_fallback_test"
    [ -z "$sector_fallback_sanitized_test" ] || rm -f "$sector_fallback_sanitized_test"
    [ -z "$ui_focus_geometry_test" ] || rm -f "$ui_focus_geometry_test"
    [ -z "$ui_focus_geometry_sanitized_test" ] || rm -f "$ui_focus_geometry_sanitized_test"
    [ -z "$capture_state_test" ] || rm -f "$capture_state_test"
    [ -z "$capture_state_sanitized_test" ] || rm -f "$capture_state_sanitized_test"
    [ -z "$diagnostic_input_state_test" ] || rm -f "$diagnostic_input_state_test"
    [ -z "$diagnostic_input_state_sanitized_test" ] || rm -f "$diagnostic_input_state_sanitized_test"
}
trap cleanup EXIT

echo "== iOS OpenAL provider contract unit gate =="
python3 misc/ios/test_openal_provider_contract.py \
    || fail "OpenAL provider contract regression tests failed"

echo "== iOS SDL2 UIKit UIScene contract gate =="
python3 misc/ios/test_sdl2_scene_contract.py \
    || fail "SDL2 UIKit UIScene contract regression tests failed"

echo "== iOS install preflight contract gate =="
python3 misc/ios/test_install_device_contract.py \
    || fail "iOS install preflight contract regression tests failed"

echo "== iOS capture-v2 host tooling gate =="
bash -n misc/ios/device_lease.sh misc/ios/input.sh misc/ios/shot.sh misc/ios/lighting_ab_capture.sh \
    || fail "capture-v2 shell syntax check failed"
shellcheck -x misc/ios/device_lease.sh misc/ios/input.sh misc/ios/shot.sh misc/ios/lighting_ab_capture.sh \
    || fail "capture-v2 ShellCheck failed"
python3 misc/ios/test_lighting_ab_evidence.py \
    || fail "capture-v2 parser/evidence regression tests failed"
python3 misc/ios/test_capture_v2_host_tools.py \
    || fail "capture-v2 mock host-tool regression tests failed"
python3 misc/ios/test_capture_state_source_contract.py \
    || fail "capture-v2 source contract regression tests failed"

# Hash paths and their contents in one Python process. The salt carries tool
# versions and gate expectations that are not repository files. Hashing names
# as well as bytes catches file additions, removals and renames.
hash_inputs() {
    local salt="$1"
    shift
    python3 "$REPO_ROOT/misc/ios/gate_hash.py" --salt "$salt" "$@"
}

cmake_config_digest() {
    local cmake_version xcode_version cache_identity
    cmake_version=$(cmake --version | head -1) \
        || return 1
    xcode_version=$(xcodebuild -version | tr '\n' '|') \
        || return 1
    cache_identity=$(
        {
            grep -E '^[^#][^:]*:(BOOL|STRING|PATH|FILEPATH|UNINITIALIZED)=' \
                "$BUILD_DIR/CMakeCache.txt"
            grep -E '^(CMAKE_GENERATOR|CMAKE_OSX_DEPLOYMENT_TARGET):INTERNAL=' \
                "$BUILD_DIR/CMakeCache.txt"
        } | sort -u
    ) \
        || return 1
    python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
        --salt "ios-cmake-config-v1|$cmake_version|$xcode_version|$cache_identity" \
        --build-context-root "$REPO_ROOT" \
        --cmake-project-root "$REPO_ROOT" \
        "$REPO_ROOT/misc/ios/build_check.sh"
}

configure_symbol_complete() {
    cmake -S . -B "$BUILD_DIR" -DXRAY_IOS_FAST_DEVICE=OFF
}

stabilize_cmake_config() {
    local first_log second_log first_digest second_digest

    first_log=$(mktemp "${TMPDIR:-/tmp}/xr-release-configure.XXXXXX") \
        || fail "could not create Release configure log"
    if ! configure_symbol_complete > "$first_log" 2>&1; then
        echo "--- configure errors ---"
        grep -E "CMake Error|error:" "$first_log" | head -30
        echo "--- full log: $first_log ---"
        fail "symbol-complete Release configure failed"
    fi
    rm -f "$first_log"

    first_digest=$(cmake_config_digest) \
        || fail "could not hash refreshed symbol-complete CMake inputs"
    validate_digest "$first_digest" "CMake configuration hash"

    second_log=$(mktemp "${TMPDIR:-/tmp}/xr-release-configure-stability.XXXXXX") \
        || fail "could not create Release configure stability log"
    if ! configure_symbol_complete > "$second_log" 2>&1; then
        echo "--- configure stability errors ---"
        grep -E "CMake Error|error:" "$second_log" | head -30
        echo "--- full log: $second_log ---"
        fail "symbol-complete Release configure stability check failed"
    fi
    rm -f "$second_log"

    second_digest=$(cmake_config_digest) \
        || fail "could not rehash stabilized symbol-complete CMake inputs"
    validate_digest "$second_digest" "CMake configuration hash"
    [ "$first_digest" = "$second_digest" ] \
        || fail "CMake inputs or selected cache values changed during identical configuration"

    CMAKE_STABLE_DIGEST="$second_digest"
}

validate_digest() {
    local digest="$1"
    local label="$2"
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] \
        || fail "$label produced an invalid digest"
}

cache_output_path() {
    local gate="$1"
    local digest="$2"
    printf '%s/%s/%s.out\n' "$GATE_CACHE_DIR" "$gate" "$digest"
}

publish_cache() {
    local output="$1"
    local output_file="$2"
    local output_dir
    local output_tmp
    output_dir=$(dirname "$output_file")
    mkdir -p "$output_dir" || return 1
    output_tmp="$output_file.tmp.$$"
    printf '%s\n' "$output" > "$output_tmp" \
        && mv "$output_tmp" "$output_file"
}

artifact_salt="ios-device-artifact-v2"
artifact_hash_before=""
if [ "$run_shaders" = 1 ] && [ "$run_engine" = 1 ]; then
    artifact_hash_before=$(python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
        --salt "$artifact_salt" --build-context-root "$REPO_ROOT" \
        --ios-artifact-root "$REPO_ROOT") \
        || fail "could not hash device artifact inputs"
    validate_digest "$artifact_hash_before" "device artifact hash"
fi

if [ "$(uname -s)" = "Darwin" ]; then
    xcrun --sdk macosx --find clang++ >/dev/null 2>&1 \
        || fail "Xcode clang++ not found"
    policy_compile=(xcrun --sdk macosx clang++)
else
    policy_cxx="${CXX:-c++}"
    command -v "$policy_cxx" >/dev/null 2>&1 \
        || fail "C++ compiler not found for graphics policy test"
    policy_compile=("$policy_cxx")
fi

if [ "$(uname -s)" = "Darwin" ]; then
    capture_v2_asan_options=detect_leaks=0
else
    capture_v2_asan_options=detect_leaks=1
fi

echo "== iOS capture-v2 serializer/input-state strict gate =="
capture_state_test=$(mktemp "${TMPDIR:-/tmp}/ios-capture-state.XXXXXX") \
    || fail "could not create capture-v2 serializer test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    misc/ios/ios_capture_state_v2_test.cpp -o "$capture_state_test" \
    || fail "capture-v2 serializer test did not compile"
"$capture_state_test" || fail "capture-v2 serializer test failed"
rm -f "$capture_state_test"
capture_state_test=""

capture_state_sanitized_test=$(mktemp "${TMPDIR:-/tmp}/ios-capture-state-sanitized.XXXXXX") \
    || fail "could not create sanitized capture-v2 serializer test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" -O1 -g \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    misc/ios/ios_capture_state_v2_test.cpp -o "$capture_state_sanitized_test" \
    || fail "sanitized capture-v2 serializer test did not compile"
ASAN_OPTIONS="$capture_v2_asan_options" UBSAN_OPTIONS=halt_on_error=1 "$capture_state_sanitized_test" \
    || fail "sanitized capture-v2 serializer test failed"
rm -f "$capture_state_sanitized_test"
capture_state_sanitized_test=""

diagnostic_input_state_test=$(mktemp "${TMPDIR:-/tmp}/ios-diagnostic-input-state.XXXXXX") \
    || fail "could not create diagnostic input-state test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    misc/ios/ios_diagnostic_input_state_test.cpp -o "$diagnostic_input_state_test" \
    || fail "diagnostic input-state test did not compile"
"$diagnostic_input_state_test" || fail "diagnostic input-state test failed"
rm -f "$diagnostic_input_state_test"
diagnostic_input_state_test=""

diagnostic_input_state_sanitized_test=$(mktemp "${TMPDIR:-/tmp}/ios-diagnostic-input-state-sanitized.XXXXXX") \
    || fail "could not create sanitized diagnostic input-state test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" -O1 -g \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    misc/ios/ios_diagnostic_input_state_test.cpp -o "$diagnostic_input_state_sanitized_test" \
    || fail "sanitized diagnostic input-state test did not compile"
ASAN_OPTIONS="$capture_v2_asan_options" UBSAN_OPTIONS=halt_on_error=1 "$diagnostic_input_state_sanitized_test" \
    || fail "sanitized diagnostic input-state test failed"
rm -f "$diagnostic_input_state_sanitized_test"
diagnostic_input_state_sanitized_test=""

echo "== iOS startup-sector evidence oracle gate =="
python3 misc/ios/test_sector_startup_oracle.py \
    || fail "iOS startup-sector evidence oracle regression tests failed"
python3 misc/ios/test_sector_marker_contract.py \
    || fail "iOS startup-sector marker source contract failed"

echo "== iOS sector fallback policy gate =="
sector_fallback_test=$(mktemp "${TMPDIR:-/tmp}/ios-sector-fallback-policy.XXXXXX") \
    || fail "could not create iOS sector fallback policy test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    misc/ios/sector_fallback_policy_test.cpp -o "$sector_fallback_test" \
    || fail "iOS sector fallback policy test did not compile"
"$sector_fallback_test" || fail "iOS sector fallback policy test failed"
rm -f "$sector_fallback_test"
sector_fallback_test=""

sector_fallback_sanitized_test=$(mktemp "${TMPDIR:-/tmp}/ios-sector-fallback-policy-sanitized.XXXXXX") \
    || fail "could not create sanitized iOS sector fallback policy test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" -O1 -g \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    misc/ios/sector_fallback_policy_test.cpp -o "$sector_fallback_sanitized_test" \
    || fail "sanitized iOS sector fallback policy test did not compile"
if [ "$(uname -s)" = "Darwin" ]; then
    # Apple's ASan runtime does not implement LeakSanitizer and exits when
    # detect_leaks=1 is requested. Address/undefined checks remain enabled.
    sector_fallback_asan_options=detect_leaks=0
else
    sector_fallback_asan_options=detect_leaks=1
fi
ASAN_OPTIONS="$sector_fallback_asan_options" UBSAN_OPTIONS=halt_on_error=1 "$sector_fallback_sanitized_test" \
    || fail "sanitized iOS sector fallback policy test failed"
rm -f "$sector_fallback_sanitized_test"
sector_fallback_sanitized_test=""

echo "== iOS graphics profile policy gate =="
policy_test=$(mktemp "${TMPDIR:-/tmp}/ios-graphics-policy.XXXXXX") \
    || fail "could not create iOS graphics policy test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    misc/ios/graphics_profile_policy_test.cpp -o "$policy_test" \
    || fail "iOS graphics profile policy test did not compile"
"$policy_test" || fail "iOS graphics profile policy test failed"
rm -f "$policy_test"
policy_test=""

echo "== iOS lifecycle policy gate =="
lifecycle_test=$(mktemp "${TMPDIR:-/tmp}/ios-lifecycle-policy.XXXXXX") \
    || fail "could not create iOS lifecycle policy test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    misc/ios/lifecycle_policy_test.cpp -o "$lifecycle_test" \
    || fail "iOS lifecycle policy test did not compile"
"$lifecycle_test" || fail "iOS lifecycle policy test failed"
rm -f "$lifecycle_test"
lifecycle_test=""

echo "== iOS lifecycle marker contract gate =="
python3 misc/ios/test_lifecycle_marker_contract.py \
    || fail "iOS lifecycle marker contract regression tests failed"

echo "== iOS audio interruption policy gate =="
audio_interruption_test=$(mktemp "${TMPDIR:-/tmp}/ios-audio-interruption-policy.XXXXXX") \
    || fail "could not create iOS audio interruption policy test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    misc/ios/audio_interruption_policy_test.cpp -o "$audio_interruption_test" \
    || fail "iOS audio interruption policy test did not compile"
"$audio_interruption_test" || fail "iOS audio interruption policy test failed"
rm -f "$audio_interruption_test"
audio_interruption_test=""

echo "== iOS texture memory policy gate =="
texture_memory_test=$(mktemp "${TMPDIR:-/tmp}/ios-texture-memory.XXXXXX") \
    || fail "could not create iOS texture memory test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    misc/ios/texture_memory_policy_test.cpp -o "$texture_memory_test" \
    || fail "iOS texture memory policy test did not compile"
"$texture_memory_test" || fail "iOS texture memory policy test failed"
rm -f "$texture_memory_test"
texture_memory_test=""

echo "== iOS BC texture fallback contract gate =="
texture_bc_fallback_test=$(mktemp "${TMPDIR:-/tmp}/ios-texture-bc-fallback.XXXXXX") \
    || fail "could not create iOS BC texture fallback test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    -isystem "$REPO_ROOT/Externals/gli" -isystem "$REPO_ROOT/Externals/gli/external" \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    misc/ios/texture_bc_fallback_test.cpp -o "$texture_bc_fallback_test" \
    || fail "iOS BC texture fallback test did not compile"
"$texture_bc_fallback_test" || fail "iOS BC texture fallback test failed"
rm -f "$texture_bc_fallback_test"
texture_bc_fallback_test=""

texture_bc_fallback_sanitized_test=$(mktemp "${TMPDIR:-/tmp}/ios-texture-bc-fallback-sanitized.XXXXXX") \
    || fail "could not create sanitized iOS BC texture fallback test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    -isystem "$REPO_ROOT/Externals/gli" -isystem "$REPO_ROOT/Externals/gli/external" -O1 -g \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    misc/ios/texture_bc_fallback_test.cpp -o "$texture_bc_fallback_sanitized_test" \
    || fail "sanitized iOS BC texture fallback test did not compile"
if [ "$(uname -s)" = "Darwin" ]; then
    texture_bc_fallback_asan_options=detect_leaks=0
else
    texture_bc_fallback_asan_options=detect_leaks=1
fi
ASAN_OPTIONS="$texture_bc_fallback_asan_options" UBSAN_OPTIONS=halt_on_error=1 "$texture_bc_fallback_sanitized_test" \
    || fail "sanitized iOS BC texture fallback test failed"
rm -f "$texture_bc_fallback_sanitized_test"
texture_bc_fallback_sanitized_test=""

echo "== iOS UI focus geometry policy gate =="
ui_focus_geometry_test=$(mktemp "${TMPDIR:-/tmp}/ios-ui-focus-geometry.XXXXXX") \
    || fail "could not create iOS UI focus geometry policy test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    misc/ios/ui_focus_geometry_policy_test.cpp -o "$ui_focus_geometry_test" \
    || fail "iOS UI focus geometry policy test did not compile"
"$ui_focus_geometry_test" || fail "iOS UI focus geometry policy test failed"
rm -f "$ui_focus_geometry_test"
ui_focus_geometry_test=""

ui_focus_geometry_sanitized_test=$(mktemp "${TMPDIR:-/tmp}/ios-ui-focus-geometry-sanitized.XXXXXX") \
    || fail "could not create sanitized iOS UI focus geometry policy test binary"
"${policy_compile[@]}" -std=c++17 -I "$REPO_ROOT" -O1 -g \
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    misc/ios/ui_focus_geometry_policy_test.cpp -o "$ui_focus_geometry_sanitized_test" \
    || fail "sanitized iOS UI focus geometry policy test did not compile"
if [ "$(uname -s)" = "Darwin" ]; then
    ui_focus_geometry_asan_options=detect_leaks=0
else
    ui_focus_geometry_asan_options=detect_leaks=1
fi
ASAN_OPTIONS="$ui_focus_geometry_asan_options" UBSAN_OPTIONS=halt_on_error=1 "$ui_focus_geometry_sanitized_test" \
    || fail "sanitized iOS UI focus geometry policy test failed"
rm -f "$ui_focus_geometry_sanitized_test"
ui_focus_geometry_sanitized_test=""

echo "== iOS UI contract gate =="
python3 misc/ios/test_ui_contract_check.py \
    || fail "iOS UI contract regression tests failed"
misc/ios/ui_automation/test_log_oracles.sh \
    || fail "iOS UI automation log-oracle regression tests failed"
python3 misc/ios/ui_contract_check.py \
    || fail "iOS menu/Options contract failed"

echo "== iOS Activity Monitor trace parser gate =="
python3 misc/ios/test_activity_trace_summary.py \
    || fail "iOS Activity Monitor trace parser regression tests failed"

echo "== iOS rendered UI-state marker gate =="
python3 misc/ios/test_ui_state_marker_contract.py \
    || fail "iOS rendered UI-state marker regression tests failed"

echo "== iOS CoP PDA map-hotkey contract gate =="
python3 misc/ios/test_pda_map_hotkey_contract.py \
    || fail "iOS CoP PDA map-hotkey contract regression tests failed"

echo "== iOS Simulator semantic UI-navigation gate =="
python3 misc/ios/test_simulator_ui_navigation.py \
    || fail "iOS Simulator semantic UI-navigation regression tests failed"

if [ "$run_shaders" = 1 ]; then
    echo "== iOS Locator registration contract gate =="
    python3 misc/ios/test_locator_registration_contract.py \
        || fail "iOS LocatorAPI registration contract regression tests failed"

    echo "== iOS retail Simulator isolation gate =="
    python3 misc/ios/test_retail_simulator.py \
        || fail "retail Simulator isolation regression tests failed"

    echo "== iOS numeric feature-macro contract gate =="
    python3 misc/ios/test_shader_macro_contract.py \
        || fail "numeric feature-macro regression tests failed"
    python3 misc/ios/shadercheck/glsl_es_check.py --macro-contract \
        || fail "numeric feature-macro contract failed"

    [ -n "$GLSLANG" ] || fail "glslangValidator not found (brew install glslang)"
    glslang_version=$("$GLSLANG" --version)
    echo "$glslang_version" | grep -q "Glslang Version:.*$EXPECT_GLSLANG" \
        || fail "glslangValidator $EXPECT_GLSLANG is required; found: $(echo "$glslang_version" | head -1)"
    python_version=$(python3 --version 2>&1)
    host_identity=$(uname -srm)
    mkdir -p "$GATE_CACHE_DIR/compile" "$GATE_CACHE_DIR/link" \
        || fail "could not create shader gate cache"

    glslang_real=$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$GLSLANG") \
        || fail "could not resolve glslangValidator"
    compile_inputs=(
        "$REPO_ROOT/misc/ios/build_check.sh"
        "$REPO_ROOT/misc/ios/gate_hash.py"
        "$REPO_ROOT/misc/ios/shadercheck/glsl_es_check.py"
        "$REPO_ROOT/misc/ios/test_shader_macro_contract.py"
        "$REPO_ROOT/res/gamedata/shaders/gl"
        "$glslang_real"
    )
    if [ "$(uname -s)" = "Darwin" ]; then
        glslang_lib_dir="$(dirname "$glslang_real")/../lib"
        otool_output=$(otool -L "$glslang_real") \
            || fail "could not inspect glslang dynamic dependencies"
        while IFS= read -r dependency; do
            case "$dependency" in
                @rpath/*) dependency="$glslang_lib_dir/${dependency#@rpath/}" ;;
                @loader_path/*) dependency="$(dirname "$glslang_real")/${dependency#@loader_path/}" ;;
                @executable_path/*) dependency="$(dirname "$glslang_real")/${dependency#@executable_path/}" ;;
            esac
            [ -f "$dependency" ] && compile_inputs+=("$dependency")
        done <<< "$(printf '%s\n' "$otool_output" | tail -n +2 | awk '{print $1}')"
        # Homebrew keeps the glslang/SPIR-V dylib closure in this dedicated
        # directory. Hashing it closes transitive dependency gaps as well.
        [ ! -d "$glslang_lib_dir" ] || compile_inputs+=("$glslang_lib_dir")
    elif command -v ldd >/dev/null 2>&1; then
        ldd_output=$(ldd "$glslang_real" 2>/dev/null) \
            || fail "could not inspect glslang dynamic dependencies"
        while IFS= read -r dependency; do
            [ -f "$dependency" ] && compile_inputs+=("$dependency")
        done <<< "$(printf '%s\n' "$ldd_output" \
            | awk '/=> \// {print $3} /^\// {print $1}')"
    fi
    compile_salt="compile|$EXPECT_COMPILE|$EXPECT_GLSLANG|$python_version|$host_identity|$glslang_version"

    echo "== shader compile gate =="
    compile_hash=$(hash_inputs "$compile_salt" "${compile_inputs[@]}") \
        || fail "could not hash shader compile inputs"
    validate_digest "$compile_hash" "shader compile hash"
    compile_output_file=$(cache_output_path compile "$compile_hash")
    compile_cache_hit=0
    if [ "$force_shader_gate" = 0 ] && [ -f "$compile_output_file" ]; then
        echo "cache HIT ($compile_hash)"
        out=$(cat "$compile_output_file") \
            || fail "could not read shader compile cache"
        compile_cache_hit=1
    else
        echo "cache MISS ($compile_hash)"
        out=$(python3 misc/ios/shadercheck/glsl_es_check.py \
            --glslang "$GLSLANG" --strict 2>&1) \
            || fail "glsl_es_check.py errored:\n$out"
    fi
    echo "$out" | tail -4
    echo "$out" | grep -q "$EXPECT_COMPILE compile" \
        || fail "expected $EXPECT_COMPILE compiling shaders. A regression here means a shader no longer builds as GLSL ES 3.00."
    echo "$out" | grep -q "low-settings profile: 2/2 compile, 0 fail" \
        || fail "low-settings shader profile failed. Check the no-MSAA / SSAO_QUALITY=1 runtime permutation."
    echo "$out" | grep -q "SSAO branch profile: 6/6 compile, 0 fail" \
        || fail "SSAO resource-branch profile failed. Check disabled, G-buffer, and optimized full/half permutations."
    echo "$out" | grep -q "SSAO value-macro contract: PASS" \
        || fail "SSAO feature macros must be tested numerically; presence tests select resources the CPU did not populate."
    echo "$out" | grep -q "Numeric feature-macro contract: PASS" \
        || fail "numeric feature macros must retain explicit zero fallbacks and never gain unreviewed presence tests."
    compile_hash_after=$(hash_inputs "$compile_salt" "${compile_inputs[@]}") \
        || fail "could not rehash shader compile inputs"
    [ "$compile_hash_after" = "$compile_hash" ] \
        || fail "shader compile inputs changed while the gate was running"
    if [ "$compile_cache_hit" = 0 ]; then
        publish_cache "$out" "$compile_output_file" \
            || fail "could not update shader compile cache"
    fi

    echo "== shader link gate =="
    link_salt="link|$EXPECT_LINK|$python_version|$host_identity"
    link_inputs=(
        "$REPO_ROOT/misc/ios/build_check.sh" \
        "$REPO_ROOT/misc/ios/gate_hash.py" \
        "$REPO_ROOT/misc/ios/shadercheck/glsl_es_check.py" \
        "$REPO_ROOT/misc/ios/test_shader_macro_contract.py" \
        "$REPO_ROOT/misc/ios/shadercheck/link_check.py" \
        "$REPO_ROOT/res/gamedata/shaders/gl" \
        "$REPO_ROOT/src/Layers/xrRender/blenders" \
        "$REPO_ROOT/src/Layers/xrRender" \
        "$REPO_ROOT/src/Layers/xrRender_R2" \
        "$REPO_ROOT/src/Layers/xrRenderPC_GL"
    )
    link_hash=$(hash_inputs "$link_salt" "${link_inputs[@]}") \
        || fail "could not hash shader link inputs"
    validate_digest "$link_hash" "shader link hash"
    link_output_file=$(cache_output_path link "$link_hash")
    link_cache_hit=0
    if [ "$force_shader_gate" = 0 ] && [ -f "$link_output_file" ]; then
        echo "cache HIT ($link_hash)"
        out=$(cat "$link_output_file") \
            || fail "could not read shader link cache"
        link_cache_hit=1
    else
        echo "cache MISS ($link_hash)"
        out=$(python3 misc/ios/shadercheck/link_check.py --strict 2>&1) \
            || fail "link_check.py errored:\n$out"
    fi
    echo "$out" | tail -1
    echo "$out" | grep -q "$EXPECT_LINK pairs clean" \
        || fail "expected $EXPECT_LINK clean vs->fs pairs. ES links varyings by NAME, so a mismatch here disables a render pass on device."
    link_hash_after=$(hash_inputs "$link_salt" "${link_inputs[@]}") \
        || fail "could not rehash shader link inputs"
    [ "$link_hash_after" = "$link_hash" ] \
        || fail "shader link inputs changed while the gate was running"
    if [ "$link_cache_hit" = 0 ]; then
        publish_cache "$out" "$link_output_file" \
            || fail "could not update shader link cache"
    fi
fi

if [ "$run_engine" = 1 ]; then
    [ -d "$BUILD_DIR" ] || fail "$BUILD_DIR missing — configure it once first (doc/iOS-Port-Journal.md, slice 6.12)"
    grep -q "^CMAKE_OSX_DEPLOYMENT_TARGET:INTERNAL=$EXPECT_DEPLOYMENT_TARGET$" "$BUILD_DIR/CMakeCache.txt" \
        || fail "$BUILD_DIR must target iOS $EXPECT_DEPLOYMENT_TARGET; reconfigure the build tree"

    cmake_hash_before=""
    if grep -q '^XRAY_IOS_FAST_DEVICE:BOOL=OFF$' "$BUILD_DIR/CMakeCache.txt"; then
        cmake_hash_before=$(cmake_config_digest) \
            || fail "could not hash symbol-complete CMake inputs"
        validate_digest "$cmake_hash_before" "CMake configuration hash"
    fi
    if [ -n "$cmake_hash_before" ] && [ -f "$CMAKE_CONFIG_STAMP" ] \
            && [ "$(cat "$CMAKE_CONFIG_STAMP")" = "$cmake_hash_before" ]; then
        echo "== symbol-complete iPhoneOS configuration cache HIT =="
    else
        echo "== refreshing symbol-complete iPhoneOS configuration =="
        stabilize_cmake_config
        cmake_stamp_tmp="$CMAKE_CONFIG_STAMP.tmp.$$"
        if ! printf '%s\n' "$CMAKE_STABLE_DIGEST" > "$cmake_stamp_tmp" \
                || ! mv "$cmake_stamp_tmp" "$CMAKE_CONFIG_STAMP"; then
            fail "could not publish CMake configuration stamp"
        fi
    fi
    grep -q '^XRAY_IOS_FAST_DEVICE:BOOL=OFF$' "$BUILD_DIR/CMakeCache.txt" \
        || fail "symbol-complete Release tree retained XRAY_IOS_FAST_DEVICE=ON"
    run_openal_configured

    echo "== incremental engine build (arm64 device) =="
    log=$(mktemp -t xrbuild)
    if [ -n "${IOS_BUILD_JOBS:-}" ]; then
        build_jobs="$IOS_BUILD_JOBS"
    else
        build_jobs=$(sysctl -n hw.ncpu 2>/dev/null \
            || getconf _NPROCESSORS_ONLN 2>/dev/null \
            || echo 8)
        memory_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
        if [ "$memory_bytes" -gt 0 ]; then
            memory_jobs=$((memory_bytes / 2147483648))
            [ "$memory_jobs" -ge 1 ] || memory_jobs=1
            [ "$build_jobs" -le "$memory_jobs" ] || build_jobs="$memory_jobs"
        fi
    fi
    case "$build_jobs" in
        ''|*[!0-9]*|0) fail "IOS_BUILD_JOBS must be a positive integer" ;;
    esac
    if ! cmake --build "$BUILD_DIR" --config Release --target xr_3da \
            --parallel "$build_jobs" > "$log" 2>&1; then
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
    "$REPO_ROOT/misc/ios/sync_app_resources.sh" "$app" \
        || fail "Release app resource synchronization failed"
    build_info=$(xcrun vtool -show-build "$app/xr_3da" 2>/dev/null)
    echo "$build_info" | grep -Eq '^[[:space:]]*platform IOS$' \
        || fail "shared output contains a non-device binary (expected platform IOS)"
    echo "$build_info" | grep -Eq "^[[:space:]]*minos $EXPECT_DEPLOYMENT_TARGET$" \
        || fail "device binary does not target iOS $EXPECT_DEPLOYMENT_TARGET"
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
    bundle_hash=$(python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
        --salt ios-app-bundle-v1 --relative-root "$app" "$app") \
        || fail "could not hash the Release app bundle"
    validate_digest "$bundle_hash" "Release bundle hash"
    echo "dSYM OK ($debug_info_bytes __debug_info bytes, UUID $bin_uuid)"
    run_openal_artifact "$app/xr_3da"
fi

echo ""
if [ "$run_shaders" = 1 ] && [ "$run_engine" = 1 ]; then
    artifact_hash_after=$(python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
        --salt "$artifact_salt" --build-context-root "$REPO_ROOT" \
        --ios-artifact-root "$REPO_ROOT") \
        || fail "could not rehash device artifact inputs"
    [ "$artifact_hash_after" = "$artifact_hash_before" ] \
        || fail "device artifact inputs changed while the gate was running"

    stamp_tmp="$DEVICE_GATE_STAMP.tmp.$$"
    {
        printf 'source_sha256=%s\n' "$artifact_hash_after"
        printf 'app_uuid=%s\n' "$bin_uuid"
        printf 'bundle_sha256=%s\n' "$bundle_hash"
        printf 'openal_provider=%s\n' "$OPENAL_PROVIDER"
        printf 'openal_sha256=%s\n' "$OPENAL_SHA256"
        printf 'platform=IOS\n'
        printf 'minos=%s\n' "$EXPECT_DEPLOYMENT_TARGET"
    } > "$stamp_tmp" \
        || fail "could not write device gate stamp"
    mv "$stamp_tmp" "$DEVICE_GATE_STAMP" \
        || fail "could not publish device gate stamp"

    if [ "$force_shader_gate" = 1 ]; then
        full_tmp="$FULL_GATE_STAMP.tmp.$$"
        {
            cat "$DEVICE_GATE_STAMP"
            printf 'shader_cache=forced-off\n'
        } > "$full_tmp" \
            || fail "could not write full gate stamp"
        mv "$full_tmp" "$FULL_GATE_STAMP" \
            || fail "could not publish full gate stamp"
        echo "PASS — full uncached gate; safe to push."
    else
        if [ -f "$FULL_GATE_STAMP" ]; then
            full_source_hash=$(awk -F= '$1 == "source_sha256" {print $2}' "$FULL_GATE_STAMP")
            full_app_uuid=$(awk -F= '$1 == "app_uuid" {print $2}' "$FULL_GATE_STAMP")
            full_bundle_hash=$(awk -F= '$1 == "bundle_sha256" {print $2}' "$FULL_GATE_STAMP")
            full_openal_provider=$(awk -F= '$1 == "openal_provider" {print $2}' "$FULL_GATE_STAMP")
            full_openal_sha256=$(awk -F= '$1 == "openal_sha256" {print $2}' "$FULL_GATE_STAMP")
            if [ "$full_source_hash" != "$artifact_hash_after" ] \
                    || [ "$full_app_uuid" != "$bin_uuid" ] \
                    || [ "$full_bundle_hash" != "$bundle_hash" ] \
                    || [ "$full_openal_provider" != "$OPENAL_PROVIDER" ] \
                    || [ "$full_openal_sha256" != "$OPENAL_SHA256" ]; then
                rm -f "$FULL_GATE_STAMP" \
                    || fail "could not invalidate stale full-gate stamp"
            fi
        fi
        echo "PASS — device gate; safe to install. Run --full before push."
    fi
else
    echo "PASS — selected partial gate only."
fi
