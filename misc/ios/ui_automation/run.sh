#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=misc/ios/device_lease.sh
source "$ROOT/misc/ios/device_lease.sh"
# shellcheck source=misc/ios/ui_automation/log_oracles.sh
source "$SCRIPT_DIR/log_oracles.sh"
BUILD_DIR="${OPENXRAY_UI_AUTOMATION_BUILD_DIR:-/tmp/openxray-ui-automation}"
PROJECT="$BUILD_DIR/OpenXRayUIAutomation.xcodeproj"
MODE="${1:---build}"
DEVICE_UDID="${OPENXRAY_DEVICE_UDID:-00008130-000564403E12001C}"
DEVICE_LOCK="${OPENXRAY_DEVICE_LOCK:-/Users/patryk/.codex/device-coordination/ios-device-lock.sh}"
DEVICE_LEASE_TOKEN="${OPENXRAY_DEVICE_LEASE_TOKEN:-}"
DEVICE_LEASE_OWNED=0
TEMP_FILES=()

cleanup()
{
    if [ "$DEVICE_LEASE_OWNED" = 1 ]; then
        DEVICE_LEASE_OWNED=0
        "$DEVICE_LOCK" release-token openxray "$DEVICE_LEASE_TOKEN" >/dev/null 2>&1 || true
    fi
    if [ "${#TEMP_FILES[@]}" -gt 0 ]; then
        for file in "${TEMP_FILES[@]}"; do
            [ ! -e "$file" ] || unlink "$file" 2>/dev/null || true
        done
    fi
    [ ! -e "$LOCK_DIR/pid" ] || unlink "$LOCK_DIR/pid" 2>/dev/null || true
    [ ! -d "$LOCK_DIR" ] || rmdir "$LOCK_DIR" 2>/dev/null || true
}

[ "$#" -le 1 ] || {
    echo "usage: $0 [--prepare|--build|--test|--test-options|--test-lifecycle|--test-audio|--test-reliability]" >&2
    exit 2
}

case "$MODE" in
    --prepare|--build|--test|--test-options|--test-lifecycle|--test-audio|--test-reliability) ;;
    *)
        echo "usage: $0 [--prepare|--build|--test|--test-options|--test-lifecycle|--test-audio|--test-reliability]" >&2
        exit 2
        ;;
esac

mkdir -p "$BUILD_DIR"
LOCK_DIR="$BUILD_DIR/.openxray-ui-automation.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    owner="unknown"
    [ -r "$LOCK_DIR/pid" ] && owner="$(<"$LOCK_DIR/pid")"
    echo "FAIL: UI automation build directory is busy (owner pid: $owner): $BUILD_DIR" >&2
    exit 1
fi
printf '%s\n' "$$" > "$LOCK_DIR/pid"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$MODE" != --prepare ] && \
        ! /usr/bin/security find-identity -v -p codesigning 2>/dev/null | \
            grep -q 'Apple Development:'; then
    echo "FAIL: no valid Apple Development signing identity is available" >&2
    exit 1
fi

cmake -S "$SCRIPT_DIR" -B "$BUILD_DIR" -G Xcode \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchains/ios.toolchain.cmake" \
    -DPLATFORM=OS64 \
    -DDEPLOYMENT_TARGET=16.4 \
    -Wno-deprecated

python3 "$SCRIPT_DIR/prepare_scheme.py" "$PROJECT"

if [ "$MODE" = --prepare ]; then
    exit 0
fi

COMMON=(
    -project "$PROJECT"
    -scheme OpenXRayUITests
    -configuration Debug
    -parallel-testing-enabled NO
    -derivedDataPath "$BUILD_DIR/DerivedData"
    -quiet
)

if [ "$MODE" = --build ]; then
    xcodebuild clean build-for-testing "${COMMON[@]}" -destination 'generic/platform=iOS'
    HOST_APP="$BUILD_DIR/Debug-iphoneos/AutomationHost.app"
    TEST_RUNNER_APP="$BUILD_DIR/Debug-iphoneos/OpenXRayUITests-Runner.app"
    TEST_BUNDLE="$TEST_RUNNER_APP/PlugIns/OpenXRayUITests.xctest"
    TEST_BINARY="$TEST_BUNDLE/OpenXRayUITests"
    codesign -vv --strict "$HOST_APP"
    codesign -vv --strict "$TEST_BUNDLE"
    codesign -vv --deep --strict "$TEST_RUNNER_APP"
    otool -L "$TEST_BINARY" | grep -Fq 'AVFAudio.framework/AVFAudio' \
        || { echo "FAIL: UI-test bundle does not link AVFAudio" >&2; exit 1; }
    exit 0
fi

RESULT_BUNDLE="${OPENXRAY_UI_AUTOMATION_RESULT:-/tmp/OpenXRayUIAutomation.xcresult}"
LOG_OUTPUT="${OPENXRAY_UI_AUTOMATION_LOG:-${RESULT_BUNDLE%.xcresult}-xr_boot.log}"
if [ -e "$RESULT_BUNDLE" ]; then
    echo "FAIL: result bundle already exists: $RESULT_BUNDLE" >&2
    exit 1
fi
if [ -e "$LOG_OUTPUT" ]; then
    echo "FAIL: log output already exists: $LOG_OUTPUT" >&2
    exit 1
fi

echo "UI Automation may require one interactive device authorization on its first run."
case "$MODE" in
    --test|--test-options)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testMainMenuToOptionsThreeTimes"
        ;;
    --test-lifecycle)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testLifecycleFiveAppSwitchCycles"
        ;;
    --test-audio)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testAudioInterruptionWhileOpenXRayForeground"
        ;;
    --test-reliability)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testReliabilityFiveAppSwitchCyclesAndForegroundAudioInterruption"
        ;;
esac

if [ ! -x "$DEVICE_LOCK" ]; then
    echo "FAIL: shared iPhone coordinator is unavailable: $DEVICE_LOCK" >&2
    exit 1
fi
device_status=$("$DEVICE_LOCK" status)
if grep -q '"state":"free"' <<< "$device_status"; then
    acquired_status=$("$DEVICE_LOCK" acquire openxray 10)
    DEVICE_LEASE_TOKEN=$(sed -n 's/.*"token":"\([^"]*\)".*/\1/p' <<< "$acquired_status")
    [ -n "$DEVICE_LEASE_TOKEN" ] \
        || { echo "FAIL: coordinator did not return a lease token" >&2; exit 1; }
    DEVICE_LEASE_OWNED=1
elif [ -n "$DEVICE_LEASE_TOKEN" ] \
        && grep -q '"owner":"openxray"' <<< "$device_status" \
        && grep -qF "\"token\":\"$DEVICE_LEASE_TOKEN\"" <<< "$device_status"; then
    echo "Using the existing OpenXRay iPhone lease."
else
    echo "FAIL: shared iPhone is busy: $device_status" >&2
    exit 3
fi

PRE_LOG=$(mktemp -t openxray-ui-pre)
POST_LOG=$(mktemp -t openxray-ui-post)
DELTA_LOG=$(mktemp -t openxray-ui-delta)
DEVICE_CONFIG=$(mktemp -t openxray-ui-config)
TEMP_FILES+=("$PRE_LOG" "$POST_LOG" "$DELTA_LOG" "$DEVICE_CONFIG")
if ! ios_run_with_timeout 30 xcrun devicectl device copy from \
        --device "$DEVICE_UDID" \
        --domain-type appDataContainer \
        --domain-identifier io.github.tryk016.openxray.RMJWWPF379 \
        --user mobile \
        --source Documents/xr_boot.log \
        --destination "$PRE_LOG" >/dev/null 2>&1; then
    : > "$PRE_LOG"
fi

case "$MODE" in
    --test-lifecycle|--test-audio|--test-reliability)
        ios_run_with_timeout 30 xcrun devicectl device copy from \
            --device "$DEVICE_UDID" \
            --domain-type appDataContainer \
            --domain-identifier io.github.tryk016.openxray.RMJWWPF379 \
            --user mobile \
            --source Documents/_appdata_/user.ltx \
            --destination "$DEVICE_CONFIG" >/dev/null 2>&1 \
            || { echo "FAIL: could not read user.ltx before the reliability test" >&2; exit 1; }
        grep -Eq '^ios_diagnostics[[:space:]]+0[[:space:]]*$' "$DEVICE_CONFIG" \
            || { echo "FAIL: reliability UI tests require ios_diagnostics 0" >&2; exit 1; }
        grep -Eq '^ios_autoinput[[:space:]]+0[[:space:]]*$' "$DEVICE_CONFIG" \
            || { echo "FAIL: reliability UI tests require ios_autoinput 0" >&2; exit 1; }
        ;;
esac

set +e
ios_run_with_timeout 480 xcodebuild clean test "${COMMON[@]}" \
    -destination "id=$DEVICE_UDID" \
    -only-testing:"$TEST_SELECTOR" \
    -resultBundlePath "$RESULT_BUNDLE"
test_status=$?
set -e

post_log_collected=1
if ! ios_run_with_timeout 30 xcrun devicectl device copy from \
        --device "$DEVICE_UDID" \
        --domain-type appDataContainer \
        --domain-identifier io.github.tryk016.openxray.RMJWWPF379 \
        --user mobile \
        --source Documents/xr_boot.log \
        --destination "$POST_LOG" >/dev/null 2>&1; then
    post_log_collected=0
    echo "FAIL: could not collect xr_boot.log after the UI test" >&2
fi

oracle_status=0
log_continued=0
if [ "$post_log_collected" = 1 ]; then
    cp "$POST_LOG" "$LOG_OUTPUT"
    pre_size=$(wc -c < "$PRE_LOG" | tr -d ' ')
    post_size=$(wc -c < "$POST_LOG" | tr -d ' ')
    if [ "$pre_size" -gt 0 ] && [ "$post_size" -ge "$pre_size" ] \
            && dd if="$POST_LOG" bs=1 count="$pre_size" 2>/dev/null | cmp -s - "$PRE_LOG"; then
        tail -c "+$((pre_size + 1))" "$POST_LOG" > "$DELTA_LOG"
        log_continued=1
    else
        cp "$POST_LOG" "$DELTA_LOG"
    fi
else
    oracle_status=1
fi

case "$MODE" in
    --test-lifecycle|--test-reliability)
        if [ "$log_continued" != 1 ]; then
            echo "FAIL: lifecycle log oracle: xr_boot.log restarted; process continuity was not preserved" >&2
            oracle_status=1
        elif ! verify_lifecycle_oracle "$DELTA_LOG"; then
            oracle_status=1
        fi
        ;;
esac

case "$MODE" in
    --test-audio)
        if [ "$log_continued" != 1 ]; then
            echo "FAIL: audio log oracle: xr_boot.log restarted; audio did not stay in the original session" >&2
            oracle_status=1
        elif ! verify_audio_oracle "$DELTA_LOG" 0; then
            oracle_status=1
        fi
        ;;
    --test-reliability)
        if [ "$log_continued" != 1 ]; then
            echo "FAIL: audio log oracle: xr_boot.log restarted; audio did not stay in the original session" >&2
            oracle_status=1
        elif ! verify_audio_oracle "$DELTA_LOG" 5; then
            oracle_status=1
        fi
        ;;
esac

if [ "$test_status" -ne 0 ]; then
    if [ "$post_log_collected" = 1 ]; then
        echo "FAIL: XCUITest failed with status $test_status; log/oracle diagnostics above; log: $LOG_OUTPUT" >&2
    else
        echo "FAIL: XCUITest failed with status $test_status; log/oracle diagnostics above" >&2
    fi
    exit "$test_status"
fi

[ "$post_log_collected" = 1 ] \
    || { echo "FAIL: XCUITest passed but post-test engine log was unavailable" >&2; exit 1; }
[ "$oracle_status" = 0 ] \
    || { echo "FAIL: XCUITest passed but the scoped device-log oracle failed; log: $LOG_OUTPUT" >&2; exit 1; }

echo "PASS — UI automation and scoped device-log oracle passed; log: $LOG_OUTPUT"
