#!/usr/bin/env bash
#
# Build a symbol-light iPhoneOS app for fast device iteration. This uses the
# existing Release configuration (-O3/NDEBUG) in a separate build/output tree,
# with no dSYM and no IPO/LTO. It never installs or launches the app.

set -u -o pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

if [ "$(uname -m)" = "arm64" ] && [ -d /opt/homebrew/bin ]; then
    PATH="/opt/homebrew/bin:$PATH"
fi

BUILD_DIR="$REPO_ROOT/build/ios-engine-fastdevice-iphoneos"
PREFIX_DIR="$REPO_ROOT/build/ios-prefix-iphoneos"
APP="$REPO_ROOT/bin/aarch64/FastDevice/xr_3da.app"
DSYM="$REPO_ROOT/bin/aarch64/FastDevice/xr_3da.app.dSYM"
STAMP="$BUILD_DIR/.ios_fast_device_gate_ok"
CMAKE_CONFIG_STAMP="$BUILD_DIR/.ios_cmake_inputs.sha256"
EXPECT_DEPLOYMENT_TARGET="16.4"
ARTIFACT_SALT="ios-device-artifact-v2"

fail() { echo ""; echo "FAIL: $*"; exit 1; }

archive_gate_detail_log() {
    local source="$1"
    local name="$2"
    local target temporary

    [ -n "${OPENXRAY_GATE_DETAIL_DIR:-}" ] || return 0
    [ -f "$source" ] || return 1
    [ -d "$OPENXRAY_GATE_DETAIL_DIR" ] || return 1
    [ ! -L "$OPENXRAY_GATE_DETAIL_DIR" ] || return 1
    target="$OPENXRAY_GATE_DETAIL_DIR/$name"
    temporary="$target.tmp.$$"
    [ ! -e "$target" ] && [ ! -L "$target" ] && [ ! -e "$temporary" ] \
        && [ ! -L "$temporary" ] || return 1
    cp "$source" "$temporary" || return 1
    chmod 600 "$temporary" || return 1
    mv "$temporary" "$target" || return 1
    printf '%s\n' "$target"
}

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
    openal_fields=$(python3 "$REPO_ROOT/misc/ios/openal_provider_contract.py" artifact \
        --prefix "$PREFIX_DIR" --binary "$APP/xr_3da" --platform iphoneos) \
        || fail "OpenAL provider artifact contract failed"
    OPENAL_PROVIDER=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_provider" {print $2}')
    OPENAL_SHA256=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_sha256" {print $2}')
    [ "$OPENAL_PROVIDER" = "OpenALSoft-1.25.2-static" ] \
        && [[ "$OPENAL_SHA256" =~ ^[0-9a-f]{64}$ ]] \
        || fail "OpenAL provider artifact contract returned incomplete stamp fields"
}

validate_digest() {
    [[ "$1" =~ ^[0-9a-f]{64}$ ]] \
        || fail "$2 produced an invalid digest"
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
        "$REPO_ROOT/misc/ios/build_fast_device.sh"
}

configure_fast_device() {
    cmake -S . -B "$BUILD_DIR" -G Xcode \
        -DCMAKE_TOOLCHAIN_FILE="$REPO_ROOT/cmake/toolchains/ios.toolchain.cmake" \
        -DPLATFORM=OS64 \
        -DDEPLOYMENT_TARGET="$EXPECT_DEPLOYMENT_TARGET" \
        -DENABLE_BITCODE=OFF \
        -DBUILD_SHARED_LIBS=OFF \
        -DLUAJIT_DISABLE_JIT=ON \
        -DXRAY_IOS_FAST_DEVICE=ON \
        -DCMAKE_PREFIX_PATH="$PREFIX_DIR" \
        -DCMAKE_FIND_ROOT_PATH="$PREFIX_DIR" \
        -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO \
        -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED=NO \
        -DCMAKE_XCODE_ATTRIBUTE_GCC_GENERATE_DEBUGGING_SYMBOLS=NO \
        -DCMAKE_XCODE_ATTRIBUTE_DEBUG_INFORMATION_FORMAT=dwarf
}

stabilize_cmake_config() {
    local first_log second_log first_digest second_digest archived_log

    first_log=$(mktemp "${TMPDIR:-/tmp}/xr-fast-configure.XXXXXX") \
        || fail "could not create configure log"
    if ! configure_fast_device > "$first_log" 2>&1; then
        archived_log=$(archive_gate_detail_log "$first_log" "fast-configure-first.log") \
            || fail "could not archive failed FastDevice configure log"
        echo "--- configure errors ---"
        grep -E "CMake Error|error:" "$first_log" | head -30
        echo "--- full log: $first_log ---"
        [ -z "$archived_log" ] || echo "--- archived log: $archived_log ---"
        fail "FastDevice configure failed"
    fi
    archived_log=$(archive_gate_detail_log "$first_log" "fast-configure-first.log") \
        || fail "could not archive FastDevice configure log"
    rm -f "$first_log"

    first_digest=$(cmake_config_digest) \
        || fail "could not hash refreshed FastDevice CMake inputs"
    validate_digest "$first_digest" "FastDevice CMake configuration hash"

    second_log=$(mktemp "${TMPDIR:-/tmp}/xr-fast-configure-stability.XXXXXX") \
        || fail "could not create configure stability log"
    if ! configure_fast_device > "$second_log" 2>&1; then
        archived_log=$(archive_gate_detail_log "$second_log" "fast-configure-stability.log") \
            || fail "could not archive failed FastDevice configure stability log"
        echo "--- configure stability errors ---"
        grep -E "CMake Error|error:" "$second_log" | head -30
        echo "--- full log: $second_log ---"
        [ -z "$archived_log" ] || echo "--- archived log: $archived_log ---"
        fail "FastDevice configure stability check failed"
    fi
    archived_log=$(archive_gate_detail_log "$second_log" "fast-configure-stability.log") \
        || fail "could not archive FastDevice configure stability log"
    rm -f "$second_log"

    second_digest=$(cmake_config_digest) \
        || fail "could not rehash stabilized FastDevice CMake inputs"
    validate_digest "$second_digest" "FastDevice CMake configuration hash"
    [ "$first_digest" = "$second_digest" ] \
        || fail "FastDevice CMake inputs or selected cache values changed during identical configuration"

    CMAKE_STABLE_DIGEST="$second_digest"
}

calculate_jobs() {
    local jobs
    local memory_bytes
    local memory_jobs
    if [ -n "${IOS_BUILD_JOBS:-}" ]; then
        jobs="$IOS_BUILD_JOBS"
    else
        jobs=$(sysctl -n hw.ncpu 2>/dev/null || echo 8)
        memory_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
        if [ "$memory_bytes" -gt 0 ]; then
            memory_jobs=$((memory_bytes / 2147483648))
            [ "$memory_jobs" -ge 1 ] || memory_jobs=1
            [ "$jobs" -le "$memory_jobs" ] || jobs="$memory_jobs"
        fi
    fi
    case "$jobs" in
        ''|*[!0-9]*|0) fail "IOS_BUILD_JOBS must be a positive integer" ;;
    esac
    printf '%s\n' "$jobs"
}

[ -d "$PREFIX_DIR" ] \
    || fail "$PREFIX_DIR missing — build the iPhoneOS dependencies first"

source_hash_before=$(python3 misc/ios/gate_hash.py \
    --salt "$ARTIFACT_SALT" --build-context-root "$REPO_ROOT" \
    --ios-artifact-root "$REPO_ROOT") \
    || fail "could not hash FastDevice inputs"

# This partial gate also runs the host audio-interruption pairing policy before
# it configures or touches the shared FastDevice build tree.
./misc/ios/build_check.sh --shaders \
    || fail "shader/UI/policy gate failed"

cmake_hash_before=""
if [ -f "$BUILD_DIR/CMakeCache.txt" ]; then
    cmake_hash_before=$(cmake_config_digest) \
        || fail "could not hash FastDevice CMake inputs"
    validate_digest "$cmake_hash_before" "FastDevice CMake configuration hash"
fi
if [ -n "$cmake_hash_before" ] && [ -f "$CMAKE_CONFIG_STAMP" ] \
        && [ "$(cat "$CMAKE_CONFIG_STAMP")" = "$cmake_hash_before" ]; then
    echo "== FastDevice iPhoneOS configuration cache HIT =="
else
    echo "== refreshing FastDevice iPhoneOS configuration =="
    stabilize_cmake_config
    cmake_stamp_tmp="$CMAKE_CONFIG_STAMP.tmp.$$"
    if ! printf '%s\n' "$CMAKE_STABLE_DIGEST" > "$cmake_stamp_tmp" \
            || ! mv "$cmake_stamp_tmp" "$CMAKE_CONFIG_STAMP"; then
        fail "could not publish FastDevice CMake configuration stamp"
    fi
fi

grep -q '^XRAY_IOS_FAST_DEVICE:BOOL=ON$' "$BUILD_DIR/CMakeCache.txt" \
    || fail "FastDevice tree is not configured with XRAY_IOS_FAST_DEVICE=ON"
grep -q "^CMAKE_OSX_DEPLOYMENT_TARGET:INTERNAL=$EXPECT_DEPLOYMENT_TARGET$" \
    "$BUILD_DIR/CMakeCache.txt" \
    || fail "FastDevice tree must target iOS $EXPECT_DEPLOYMENT_TARGET"
grep -q '^CMAKE_XCODE_ATTRIBUTE_GCC_GENERATE_DEBUGGING_SYMBOLS:.*=NO$' \
    "$BUILD_DIR/CMakeCache.txt" \
    || fail "FastDevice tree must disable debug-symbol generation"
grep -q '^CMAKE_XCODE_ATTRIBUTE_DEBUG_INFORMATION_FORMAT:.*=dwarf$' \
    "$BUILD_DIR/CMakeCache.txt" \
    || fail "FastDevice tree must avoid dwarf-with-dsym"
run_openal_configured

jobs=$(calculate_jobs)
echo "== FastDevice engine build (Release semantics, $jobs jobs) =="
build_log=$(mktemp "${TMPDIR:-/tmp}/xr-fast-build.XXXXXX") \
    || fail "could not create FastDevice build log"
if ! cmake --build "$BUILD_DIR" --config Release --target xr_3da \
        --parallel "$jobs" > "$build_log" 2>&1; then
    archived_log=$(archive_gate_detail_log "$build_log" "fast-build.log") \
        || fail "could not archive failed FastDevice build log"
    echo "--- first errors ---"
    grep -E "^[^[:space:]].*:[0-9]+:[0-9]+: (error|fatal error):" "$build_log" | head -30
    grep -E "CMake Error|BUILD FAILED|The following build commands failed" "$build_log" | head -30
    echo "--- full log: $build_log ---"
    [ -z "$archived_log" ] || echo "--- archived log: $archived_log ---"
    fail "FastDevice engine build failed"
fi
compiled=$(grep -c "CompileC" "$build_log" || true)
archived_log=$(archive_gate_detail_log "$build_log" "fast-build.log") \
    || fail "could not archive FastDevice build log"
rm -f "$build_log"
echo "built OK ($compiled translation unit(s) recompiled)"

[ -x "$APP/xr_3da" ] || fail "FastDevice app was not produced at $APP"
./misc/ios/sync_app_resources.sh "$APP" \
    || fail "FastDevice resource synchronization failed"

build_info=$(xcrun vtool -show-build "$APP/xr_3da" 2>/dev/null)
echo "$build_info" | grep -Eq '^[[:space:]]*platform IOS$' \
    || fail "FastDevice payload is not platform IOS"
echo "$build_info" | grep -Eq "^[[:space:]]*minos $EXPECT_DEPLOYMENT_TARGET$" \
    || fail "FastDevice payload does not target iOS $EXPECT_DEPLOYMENT_TARGET"
[ ! -e "$DSYM" ] \
    || fail "FastDevice unexpectedly produced a dSYM at $DSYM"

settings=$(xcodebuild -project "$BUILD_DIR/OpenXRay.xcodeproj" \
    -target xr_3da -configuration Release -showBuildSettings 2>/dev/null) \
    || fail "could not inspect FastDevice Xcode settings"
echo "$settings" | grep -Eq '^[[:space:]]*GCC_GENERATE_DEBUGGING_SYMBOLS = NO$' \
    || fail "FastDevice target still generates debug symbols"
echo "$settings" | grep -Eq '^[[:space:]]*DEBUG_INFORMATION_FORMAT = dwarf$' \
    || fail "FastDevice target does not avoid dwarf-with-dsym"
echo "$settings" | grep -Eq '^[[:space:]]*GCC_OPTIMIZATION_LEVEL = 3$' \
    || fail "FastDevice target lost Release -O3"
echo "$settings" | grep -Eq 'OTHER_CPLUSPLUSFLAGS = .*[-]DNDEBUG' \
    || fail "FastDevice target lost Release NDEBUG semantics"
echo "$settings" | grep -Eq '^[[:space:]]*LLVM_LTO = (YES|YES_THIN)$' \
    && fail "FastDevice target unexpectedly enables Xcode LTO"
echo "$settings" | grep -Eq 'OTHER_(C|CPLUSPLUS|LD)FLAGS = .*[-]flto' \
    && fail "FastDevice target unexpectedly contains -flto"
run_openal_artifact

app_uuid=$(xcrun dwarfdump --uuid "$APP/xr_3da" | awk '{print $2}' | sort)
[ -n "$app_uuid" ] || fail "FastDevice app has no UUID"
bundle_hash=$(python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
    --salt ios-app-bundle-v1 --relative-root "$APP" "$APP") \
    || fail "could not hash the FastDevice app bundle"
validate_digest "$bundle_hash" "FastDevice bundle hash"
source_hash_after=$(python3 misc/ios/gate_hash.py \
    --salt "$ARTIFACT_SALT" --build-context-root "$REPO_ROOT" \
    --ios-artifact-root "$REPO_ROOT") \
    || fail "could not rehash FastDevice inputs"
[ "$source_hash_after" = "$source_hash_before" ] \
    || fail "FastDevice inputs changed while the build was running"
stamp_tmp="$STAMP.tmp.$$"
{
    printf 'source_sha256=%s\n' "$source_hash_after"
    printf 'app_uuid=%s\n' "$app_uuid"
    printf 'bundle_sha256=%s\n' "$bundle_hash"
    printf 'openal_provider=%s\n' "$OPENAL_PROVIDER"
    printf 'openal_sha256=%s\n' "$OPENAL_SHA256"
    printf 'platform=IOS\n'
    printf 'minos=%s\n' "$EXPECT_DEPLOYMENT_TARGET"
    printf 'configuration=Release\n'
    printf 'symbols=disabled-no-dsym\n'
    printf 'lto=off\n'
} > "$stamp_tmp" || fail "could not write FastDevice stamp"
mv "$stamp_tmp" "$STAMP" || fail "could not publish FastDevice stamp"

echo ""
echo "PASS — FastDevice ready at $APP (UUID $app_uuid)."
