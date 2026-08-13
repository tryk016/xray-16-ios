#!/usr/bin/env bash
#
# Build a symbol-light iPhoneOS app for fast device iteration. This uses the
# existing Release configuration (-O3/NDEBUG) in a separate build/output tree,
# with no dSYM and no IPO/LTO. It never installs or launches the app.

# The supported observer boundary is run_gate_logged.py: it scrubs Bash startup
# state and exported functions before this trusted shell receives the unlinked
# context.  Direct invocation remains a normal gate entrypoint, not a supported
# telemetry trust boundary.  Nested build_check receives a fresh unlinked file;
# ordinary tools never inherit either context descriptor.
set +a +x
feedback_inherited_xtrace_fd="${BASH_XTRACEFD:-}"
feedback_context_marker="${XRAY_FEEDBACK_CONTEXT_FD:-}"
unset BASH_ENV ENV PS4 BASH_XTRACEFD
unset feedback_context_fd feedback_dir feedback_nonce feedback_run_id feedback_profile
unset XRAY_FEEDBACK_CONTEXT_FD XRAY_FEEDBACK_RAW_EVENT_FD
if [[ "$feedback_inherited_xtrace_fd" =~ ^[3-9][0-9]*$ ]] \
        && [ "$feedback_inherited_xtrace_fd" != "$feedback_context_marker" ]; then
    eval "exec ${feedback_inherited_xtrace_fd}>&-" 2>/dev/null || true
fi
feedback_context_fd="$feedback_context_marker"
unset feedback_context_marker feedback_inherited_xtrace_fd
set -u -o pipefail
export PYTHONDONTWRITEBYTECODE=1

feedback_dir=""
feedback_nonce=""
feedback_run_id=""
feedback_profile=""
if [[ "$feedback_context_fd" =~ ^[3-9][0-9]*$ ]]; then
    # Bash itself validates /dev/fd and bounds every read; no external command
    # can observe the secret descriptor before it is closed.
    if [ -f "/dev/fd/$feedback_context_fd" ] \
            && IFS= read -r -n 4096 -u "$feedback_context_fd" feedback_dir \
            && IFS= read -r -n 4096 -u "$feedback_context_fd" feedback_nonce \
            && IFS= read -r -n 4096 -u "$feedback_context_fd" feedback_run_id \
            && IFS= read -r -n 4096 -u "$feedback_context_fd" feedback_profile; then
        if IFS= read -r -n 1 -u "$feedback_context_fd"; then
            feedback_dir=""; feedback_nonce=""; feedback_run_id=""; feedback_profile=""
        fi
    fi
    eval "exec ${feedback_context_fd}<&-" 2>/dev/null || true
fi
feedback_enabled=0
if [ -n "$feedback_dir" ] && [ -n "$feedback_nonce" ] && [ -n "$feedback_run_id" ] \
        && [ -n "$feedback_profile" ]; then
    feedback_enabled=1
fi
for feedback_environment_name in "${!OPENXRAY_TEST_FEEDBACK_@}"; do
    unset "$feedback_environment_name"
done

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

feedback_manual_stage_id=""
feedback_manual_stage_started=""
feedback_manual_stage_fail() {
    [ -n "$feedback_manual_stage_id" ] || return 0
    [ -n "$feedback_manual_stage_started" ] || return 0
    feedback_observer emit --kind stage --id "$feedback_manual_stage_id" \
        --result FAIL --started-ns "$feedback_manual_stage_started" --exit-code 1 >/dev/null 2>&1 || true
    feedback_manual_stage_id=""
    feedback_manual_stage_started=""
}
fail() { feedback_manual_stage_fail; echo ""; echo "FAIL: $*"; exit 1; }

feedback_with_context() {
    [ "$feedback_enabled" = 1 ] || { "$@"; return $?; }
    BASH_ENV='' XRAY_FEEDBACK_CONTEXT_FD=9 python3 -S \
        "$REPO_ROOT/misc/ios/test_feedback.py" "$@" 9<<EOF
$feedback_dir
$feedback_nonce
$feedback_run_id
$feedback_profile
EOF
}
feedback_origin_for_stage() {
    local source="${1#stage::}"
    case "$source" in
        python::*|shader::glsl-es|shader::link|shell::ui-log-oracle) printf '%s\n' "$source" ;;
        cpp:*) printf '%s\n' "${source%%@*}" ;;
        *) printf '%s\n' "observer::fast" ;;
    esac
}
feedback_observer() {
    [ "$feedback_enabled" = 1 ] || return 0
    local -a original=("$@")
    local identifier="" origin
    while [ "$#" -gt 1 ]; do
        case "$1" in --id) identifier="$2"; shift 2 ;; *) shift ;; esac
    done
    origin=$(feedback_origin_for_stage "$identifier") || return 0
    feedback_with_context "${original[@]}" --entrypoint-id "$origin"
}
feedback_now() { python3 -c 'import time; print(time.monotonic_ns())' 2>/dev/null; }
feedback_stage() {
    local identifier="$1" started status
    shift
    if [ "$feedback_enabled" = 0 ]; then "$@"; return $?; fi
    started=$(feedback_now || true)
    "$@"; status=$?
    [ -z "$started" ] || feedback_observer emit --kind stage \
        --id "$identifier" --result "$([ "$status" -eq 0 ] && printf PASS || printf FAIL)" \
        --started-ns "$started" --exit-code "$status" >/dev/null 2>&1 || true
    return "$status"
}
feedback_mark_stage() {
    [ "$feedback_enabled" = 1 ] && [ -n "$2" ] || return 0
    feedback_observer emit --kind stage --id "$1" --result PASS \
        --started-ns "$2" --exit-code 0 --cache-hit "${3:-none}" >/dev/null 2>&1 || true
}
feedback_cached_stage() { local started; started=$(feedback_now || true); feedback_mark_stage "$1" "$started" true; }
feedback_begin_manual_stage() {
    feedback_manual_stage_id="$1"
    feedback_manual_stage_started=$(feedback_now || true)
}
feedback_complete_manual_stage() {
    feedback_mark_stage "$feedback_manual_stage_id" "$feedback_manual_stage_started" "${1:-none}"
    feedback_manual_stage_id=""
    feedback_manual_stage_started=""
}

feedback_nested_bash() {
    # ``env`` cannot wildcard -u names.  Discover dynamic exported Bash
    # function names and remove them as well as the fixed startup controls
    # before every nested top-level Bash.  This remains relevant if a future
    # parent path exports a function after our initial scrub.
    local feedback_nested_entry feedback_nested_name
    local -a feedback_nested_env=(/usr/bin/env -u SHELLOPTS -u BASHOPTS -u PS4 \
        -u BASH_XTRACEFD -u BASH_ENV -u ENV)
    while IFS= read -r feedback_nested_entry; do
        feedback_nested_name="${feedback_nested_entry%%=*}"
        case "$feedback_nested_name" in
            BASH_FUNC_*%%) feedback_nested_env+=(-u "$feedback_nested_name") ;;
        esac
    done < <(/usr/bin/env)
    "${feedback_nested_env[@]}" "$@"
}

feedback_run_nested_shaders() {
    if [ "$feedback_enabled" = 0 ]; then
        feedback_nested_bash /bin/bash ./misc/ios/build_check.sh --shaders
        return $?
    fi
    local context_path nested_fd=9 status
    context_path=$(mktemp "$feedback_dir/nested-context.XXXXXX" 2>/dev/null) \
        || {
            feedback_nested_bash /bin/bash ./misc/ios/build_check.sh --shaders
            return $?
        }
    if ! chmod 600 "$context_path" 2>/dev/null \
            || ! { printf '%s\n%s\n%s\n%s\n' "$feedback_dir" "$feedback_nonce" "$feedback_run_id" "$feedback_profile" > "$context_path"; } 2>/dev/null \
            || ! { exec 9<"$context_path"; } 2>/dev/null \
            || ! rm -f "$context_path" 2>/dev/null; then
        { exec 9<&-; } 2>/dev/null || true
        rm -f "$context_path" 2>/dev/null || true
        feedback_nested_bash /bin/bash ./misc/ios/build_check.sh --shaders
        return $?
    fi
    # FD 9 is already open and inherited by this exact nested top-level Bash.
    # ``9<&9`` only duplicated it onto itself; it added no pass_fds semantics.
    feedback_nested_bash XRAY_FEEDBACK_CONTEXT_FD="$nested_fd" /bin/bash \
        ./misc/ios/build_check.sh --shaders
    status=$?
    { exec 9<&-; } 2>/dev/null || true
    return "$status"
}

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
    openal_fields=$(feedback_stage "stage::validation::python::openal-configured" python3 "$REPO_ROOT/misc/ios/openal_provider_contract.py" configured \
        --cache "$BUILD_DIR/CMakeCache.txt" --prefix "$PREFIX_DIR" \
        --project "$BUILD_DIR/OpenXRay.xcodeproj" --platform iphoneos) \
        || fail "OpenAL provider configured contract failed"
}
run_openal_artifact() {
    openal_fields=$(feedback_stage "stage::validation::python::openal-artifact" python3 "$REPO_ROOT/misc/ios/openal_provider_contract.py" artifact \
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

source_hash_before=$(feedback_stage "build-stage::artifact-input-hash-before" python3 misc/ios/gate_hash.py \
    --salt "$ARTIFACT_SALT" --build-context-root "$REPO_ROOT" \
    --ios-artifact-root "$REPO_ROOT") \
    || fail "could not hash FastDevice inputs"

# This partial gate also runs the host audio-interruption pairing policy before
# it configures or touches the shared FastDevice build tree.
feedback_run_nested_shaders || fail "shader/UI/policy gate failed"

cmake_hash_before=""
if [ -f "$BUILD_DIR/CMakeCache.txt" ]; then
    cmake_hash_before=$(cmake_config_digest) \
        || fail "could not hash FastDevice CMake inputs"
    validate_digest "$cmake_hash_before" "FastDevice CMake configuration hash"
fi
if [ -n "$cmake_hash_before" ] && [ -f "$CMAKE_CONFIG_STAMP" ] \
        && [ "$(cat "$CMAKE_CONFIG_STAMP")" = "$cmake_hash_before" ]; then
    echo "== FastDevice iPhoneOS configuration cache HIT =="
    feedback_cached_stage "build-stage::cmake-configure"
else
    echo "== refreshing FastDevice iPhoneOS configuration =="
    feedback_stage "build-stage::cmake-configure" stabilize_cmake_config \
        || fail "FastDevice CMake configuration stage failed"
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
if ! feedback_stage "build-stage::release-engine-build" cmake --build "$BUILD_DIR" --config Release --target xr_3da \
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
feedback_stage "build-stage::resource-sync" ./misc/ios/sync_app_resources.sh "$APP" \
    || fail "FastDevice resource synchronization failed"

feedback_begin_manual_stage "build-stage::artifact-platform-debug-verification"
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
feedback_complete_manual_stage
run_openal_artifact

app_uuid=$(xcrun dwarfdump --uuid "$APP/xr_3da" | awk '{print $2}' | sort)
[ -n "$app_uuid" ] || fail "FastDevice app has no UUID"
bundle_hash=$(python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
    --salt ios-app-bundle-v1 --relative-root "$APP" "$APP") \
    || fail "could not hash the FastDevice app bundle"
validate_digest "$bundle_hash" "FastDevice bundle hash"
source_hash_after=$(feedback_stage "build-stage::artifact-input-hash-after" python3 misc/ios/gate_hash.py \
    --salt "$ARTIFACT_SALT" --build-context-root "$REPO_ROOT" \
    --ios-artifact-root "$REPO_ROOT") \
    || fail "could not rehash FastDevice inputs"
[ "$source_hash_after" = "$source_hash_before" ] \
    || fail "FastDevice inputs changed while the build was running"
feedback_begin_manual_stage "build-stage::stamp-publication"
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
feedback_complete_manual_stage

echo ""
echo "PASS — FastDevice ready at $APP (UUID $app_uuid)."
