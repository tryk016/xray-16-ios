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
XCODEBUILD="${OPENXRAY_UI_AUTOMATION_XCODEBUILD:-xcodebuild}"
CMAKE="${OPENXRAY_UI_AUTOMATION_CMAKE:-cmake}"
PREPARE_SCHEME="${OPENXRAY_UI_AUTOMATION_PREPARE_SCHEME:-$SCRIPT_DIR/prepare_scheme.py}"
SECURITY="${OPENXRAY_UI_AUTOMATION_SECURITY:-/usr/bin/security}"
CODESIGN="${OPENXRAY_UI_AUTOMATION_CODESIGN:-codesign}"
OTOOL="${OPENXRAY_UI_AUTOMATION_OTOOL:-otool}"
XCRUN="${OPENXRAY_UI_AUTOMATION_XCRUN:-xcrun}"
PYTHON3="${OPENXRAY_UI_AUTOMATION_PYTHON3:-python3}"
SLEEP="${OPENXRAY_UI_AUTOMATION_SLEEP:-sleep}"
BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"
OPENXRAY_EXECUTABLE="${OPENXRAY_UI_AUTOMATION_EXECUTABLE:-xr_3da}"
READY_TIMEOUT_SECONDS="${OPENXRAY_UI_AUTOMATION_READY_TIMEOUT_SECONDS:-60}"
READY_POLL_SECONDS="${OPENXRAY_UI_AUTOMATION_READY_POLL_SECONDS:-1}"
TRACE_FILE="${OPENXRAY_UI_AUTOMATION_TRACE:-}"
DEVICE_LEASE_TOKEN="${OPENXRAY_DEVICE_LEASE_TOKEN:-}"
DEVICE_LEASE_OWNED=0
LAUNCH_SUCCEEDED=0
LAUNCHED_PID=""
TEMP_FILES=()
SUCCESS_MESSAGE=""

trace_event()
{
    [ -z "$TRACE_FILE" ] || printf '%s\n' "$1" >> "$TRACE_FILE" 2>/dev/null || true
}

cleanup()
{
    local original_status="$1"
    local cleanup_status=0
    local remembered_pid="${LAUNCHED_PID:-}"
    local process_state=""
    local attempt

    cleanup_fail()
    {
        cleanup_status=1
        trace_event "cleanup-failed=$1"
        echo "FAIL: UI automation cleanup: $1" >&2
    }

    cleanup_process_state()
    {
        local query_label="$1"
        trace_event "$query_label"
        if ! ios_run_with_timeout 3 "$XCRUN" devicectl device info processes \
                --device "$DEVICE_UDID" \
                --json-output "$CLEANUP_PROCESS_JSON" >/dev/null 2>&1; then
            return 1
        fi
        "$PYTHON3" "$SCRIPT_DIR/devicectl_contract.py" cleanup-process-state \
            --json "$CLEANUP_PROCESS_JSON" \
            --pid "$remembered_pid" \
            --executable "$OPENXRAY_EXECUTABLE"
    }

    if [ "${LAUNCH_SUCCEEDED:-0}" = 1 ]; then
        if [[ ! "$remembered_pid" =~ ^[1-9][0-9]*$ ]]; then
            cleanup_fail "launched process has no exact PID; refusing name-based termination"
        elif ! process_state=$(cleanup_process_state cleanup-query-initial); then
            cleanup_fail "fresh process query/classification failed for PID $remembered_pid"
        elif [ "$process_state" = gone ] || [ "$process_state" = reused ]; then
            trace_event "cleanup-confirmed-$process_state"
        elif [ "$process_state" = bound ]; then
            trace_event "terminate-pid=$remembered_pid"
            if ! ios_run_with_timeout 3 "$XCRUN" devicectl device process terminate \
                    --device "$DEVICE_UDID" --pid "$remembered_pid" >/dev/null 2>&1; then
                trace_event "terminate-command-failed-pid=$remembered_pid"
            fi
            for attempt in 1 2 3 4 5; do
                if ! "$SLEEP" 1; then
                    cleanup_fail "cleanup poll sleep failed at attempt $attempt for PID $remembered_pid"
                    break
                fi
                if ! process_state=$(cleanup_process_state "cleanup-query-poll-$attempt"); then
                    cleanup_fail "fresh process query/classification failed at cleanup poll $attempt for PID $remembered_pid"
                    break
                fi
                if [ "$process_state" = gone ] || [ "$process_state" = reused ]; then
                    trace_event "cleanup-confirmed-$process_state"
                    break
                fi
                if [ "$attempt" = 1 ]; then
                    trace_event "kill-pid=$remembered_pid"
                    if ! ios_run_with_timeout 3 "$XCRUN" devicectl device process terminate \
                            --device "$DEVICE_UDID" --pid "$remembered_pid" --kill \
                            >/dev/null 2>&1; then
                        trace_event "kill-command-failed-pid=$remembered_pid"
                    fi
                fi
                if [ "$attempt" = 5 ]; then
                    cleanup_fail "PID $remembered_pid remained exact-bound after five cleanup polls"
                fi
            done
        else
            cleanup_fail "unexpected cleanup process state $process_state for PID $remembered_pid"
        fi
    fi
    if [ "${#TEMP_FILES[@]}" -gt 0 ]; then
        for file in "${TEMP_FILES[@]}"; do
            [ ! -e "$file" ] || unlink "$file" 2>/dev/null || true
        done
    fi
    [ ! -e "$LOCK_DIR/pid" ] || unlink "$LOCK_DIR/pid" 2>/dev/null || true
    [ ! -d "$LOCK_DIR" ] || rmdir "$LOCK_DIR" 2>/dev/null || true
    if [ "$DEVICE_LEASE_OWNED" = 1 ]; then
        DEVICE_LEASE_OWNED=0
        trace_event lease-release
        if ! "$DEVICE_LOCK" release-token openxray "$DEVICE_LEASE_TOKEN" >/dev/null 2>&1; then
            cleanup_fail "could not release the exact OpenXRay lease token"
        fi
    fi

    if [ "$cleanup_status" -ne 0 ] && [ "$original_status" -eq 0 ]; then
        return 1
    fi
    return "$original_status"
}

cleanup_and_exit()
{
    local original_status="$1"
    local final_status

    trap - EXIT
    set +e
    cleanup "$original_status"
    final_status=$?
    if [ "$final_status" -eq 0 ] && [ "$original_status" -eq 0 ] \
            && [ -n "${SUCCESS_MESSAGE:-}" ]; then
        echo "$SUCCESS_MESSAGE"
    fi
    exit "$final_status"
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
trap 'cleanup_and_exit "$?"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$MODE" != --prepare ] && \
        ! "$SECURITY" find-identity -v -p codesigning 2>/dev/null | \
            grep -q 'Apple Development:'; then
    echo "FAIL: no valid Apple Development signing identity is available" >&2
    exit 1
fi

"$CMAKE" -S "$SCRIPT_DIR" -B "$BUILD_DIR" -G Xcode \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchains/ios.toolchain.cmake" \
    -DPLATFORM=OS64 \
    -DDEPLOYMENT_TARGET=16.4 \
    -Wno-deprecated

"$PREPARE_SCHEME" "$PROJECT"

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

build_for_testing()
{
    "$XCODEBUILD" clean build-for-testing "${COMMON[@]}" -destination 'generic/platform=iOS'
    HOST_APP="$BUILD_DIR/Debug-iphoneos/AutomationHost.app"
    TEST_RUNNER_APP="$BUILD_DIR/Debug-iphoneos/OpenXRayUITests-Runner.app"
    TEST_BUNDLE="$TEST_RUNNER_APP/PlugIns/OpenXRayUITests.xctest"
    TEST_BINARY="$TEST_BUNDLE/OpenXRayUITests"
    "$CODESIGN" -vv --strict "$HOST_APP"
    "$CODESIGN" -vv --strict "$TEST_BUNDLE"
    "$CODESIGN" -vv --deep --strict "$TEST_RUNNER_APP"
    "$OTOOL" -L "$TEST_BINARY" | grep -Fq 'AVFAudio.framework/AVFAudio' \
        || { echo "FAIL: UI-test bundle does not link AVFAudio" >&2; exit 1; }
    trace_event build-for-testing-complete
}

if [ "$MODE" = --build ]; then
    build_for_testing
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

# A physical run must own a fully signed generic-device test product before it
# can reserve the shared phone.  No build action is permitted after PRE_LOG.
build_for_testing

echo "UI Automation may require one interactive device authorization on its first run."
AUTONOMOUS_SESSION=0
REQUIRE_RESOLVED_SECTOR=0
# `cfg_save` deliberately does not persist `start`, so an autonomous harness
# session owns this fixed command-line contract instead of mutating user.ltx.
AUTONOMOUS_START_ARGS='server(mobile user - beginning of the game/single/alife/load) client(localhost)'
case "$MODE" in
    --test|--test-options)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testMainMenuToOptionsThreeTimes"
        ;;
    --test-lifecycle)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testLifecycleFiveAppSwitchCycles"
        AUTONOMOUS_SESSION=1
        ;;
    --test-audio)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testAudioInterruptionWhileOpenXRayForeground"
        AUTONOMOUS_SESSION=1
        ;;
    --test-reliability)
        TEST_SELECTOR="OpenXRayUITests/OpenXRayUITests/testReliabilityFiveAppSwitchCyclesAndForegroundAudioInterruption"
        AUTONOMOUS_SESSION=1
        REQUIRE_RESOLVED_SECTOR=1
        ;;
esac

[[ "$READY_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] \
    || { echo "FAIL: OPENXRAY_UI_AUTOMATION_READY_TIMEOUT_SECONDS must be a positive integer" >&2; exit 2; }
[[ "$READY_POLL_SECONDS" =~ ^[0-9]+$ ]] \
    || { echo "FAIL: OPENXRAY_UI_AUTOMATION_READY_POLL_SECONDS must be a non-negative integer" >&2; exit 2; }

copy_from_container()
{
    local source_path="$1"
    local destination_path="$2"
    ios_run_with_timeout 30 "$XCRUN" devicectl device copy from \
        --device "$DEVICE_UDID" \
        --domain-type appDataContainer \
        --domain-identifier "$BUNDLE_ID" \
        --user mobile \
        --source "$source_path" \
        --destination "$destination_path" >/dev/null 2>&1
}

wait_for_same_pid_readiness()
{
    local expected_pid="$1"
    local ready_log="$2"
    local require_resolved_sector="$3"
    local deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
    local readiness_args=(log-ready --log "$ready_log" --pid "$expected_pid")
    [ "$require_resolved_sector" = 0 ] \
        || readiness_args+=(--require-resolved-sector)

    while :; do
        if copy_from_container Documents/xr_boot.log "$ready_log" \
                && "$PYTHON3" "$SCRIPT_DIR/devicectl_contract.py" \
                    "${readiness_args[@]}" >/dev/null 2>&1; then
            return 0
        fi
        [ "$SECONDS" -ge "$deadline" ] && return 1
        "$SLEEP" "$READY_POLL_SECONDS"
    done
}

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
    trace_event lease-acquired
elif [ -n "$DEVICE_LEASE_TOKEN" ] \
        && grep -q '"owner":"openxray"' <<< "$device_status" \
        && grep -qF "\"token\":\"$DEVICE_LEASE_TOKEN\"" <<< "$device_status"; then
    echo "Using the existing OpenXRay iPhone lease."
    trace_event lease-reused
else
    echo "FAIL: shared iPhone is busy: $device_status" >&2
    exit 3
fi

PRE_LOG=$(mktemp -t openxray-ui-pre)
POST_LOG=$(mktemp -t openxray-ui-post)
DELTA_LOG=$(mktemp -t openxray-ui-delta)
DEVICE_CONFIG=$(mktemp -t openxray-ui-config)
LAUNCH_JSON=$(mktemp -t openxray-ui-launch)
PROCESS_JSON=$(mktemp -t openxray-ui-processes)
CLEANUP_PROCESS_JSON=$(mktemp -t openxray-ui-cleanup-processes)
READY_LOG=$(mktemp -t openxray-ui-ready)
TEMP_FILES+=("$PRE_LOG" "$POST_LOG" "$DELTA_LOG" "$DEVICE_CONFIG" "$LAUNCH_JSON" \
    "$PROCESS_JSON" "$CLEANUP_PROCESS_JSON" "$READY_LOG")

copy_from_container Documents/_appdata_/user.ltx "$DEVICE_CONFIG" \
    || { echo "FAIL: could not read user.ltx before the physical UI test" >&2; exit 1; }
grep -Eq '^ios_diagnostics[[:space:]]+0[[:space:]]*$' "$DEVICE_CONFIG" \
    || { echo "FAIL: physical UI tests require ios_diagnostics 0" >&2; exit 1; }
grep -Eq '^ios_autoinput[[:space:]]+0[[:space:]]*$' "$DEVICE_CONFIG" \
    || { echo "FAIL: physical UI tests require ios_autoinput 0" >&2; exit 1; }
if [ "$AUTONOMOUS_SESSION" = 1 ]; then
    # XR_IOConsole strips leading whitespace before command dispatch; match the
    # same semantic command rather than accepting an indented active autoload.
    if grep -Eq '^[[:space:]]*start[[:space:]]+server\(' "$DEVICE_CONFIG"; then
        echo "FAIL: autonomous UI tests require user.ltx to contain zero active start server(...) autoload commands" >&2
        exit 1
    fi
fi
trace_event config-validated

PRE_LIFECYCLE_SEQUENCE=0
if [ "$AUTONOMOUS_SESSION" = 1 ]; then
    ios_run_with_timeout 30 "$XCRUN" devicectl device process launch \
        --device "$DEVICE_UDID" \
        --terminate-existing \
        --json-output "$LAUNCH_JSON" \
        -- "$BUNDLE_ID" -start "$AUTONOMOUS_START_ARGS" >/dev/null \
        || { echo "FAIL: could not launch OpenXRay with devicectl" >&2; exit 1; }
    LAUNCH_SUCCEEDED=1
    LAUNCHED_PID=$("$PYTHON3" "$SCRIPT_DIR/devicectl_contract.py" launch-pid --json "$LAUNCH_JSON") \
        || { echo "FAIL: devicectl launch did not return a valid OpenXRay PID" >&2; exit 1; }
    trace_event "launch-pid=$LAUNCHED_PID"

    wait_for_same_pid_readiness "$LAUNCHED_PID" "$READY_LOG" "$REQUIRE_RESOLVED_SECTOR" \
        || { echo "FAIL: xr_boot.log did not publish required newline-complete readiness for launched PID $LAUNCHED_PID" >&2; exit 1; }
    trace_event "ready-pid=$LAUNCHED_PID"

    ios_run_with_timeout 30 "$XCRUN" devicectl device info processes \
        --device "$DEVICE_UDID" \
        --json-output "$PROCESS_JSON" >/dev/null \
        || { echo "FAIL: could not query OpenXRay runningProcesses" >&2; exit 1; }
    "$PYTHON3" "$SCRIPT_DIR/devicectl_contract.py" running-process \
        --json "$PROCESS_JSON" \
        --pid "$LAUNCHED_PID" \
        --executable "$OPENXRAY_EXECUTABLE" >/dev/null \
        || { echo "FAIL: runningProcesses did not prove the launched OpenXRay executable/PID" >&2; exit 1; }
    trace_event "running-process-pid=$LAUNCHED_PID"

    copy_from_container Documents/xr_boot.log "$PRE_LOG" \
        || { echo "FAIL: could not take PRE_LOG baseline after OpenXRay readiness" >&2; exit 1; }
    [ -s "$PRE_LOG" ] \
        || { echo "FAIL: PRE_LOG baseline is empty after OpenXRay readiness" >&2; exit 1; }
    readiness_args=(log-ready --log "$PRE_LOG" --pid "$LAUNCHED_PID")
    [ "$REQUIRE_RESOLVED_SECTOR" = 0 ] \
        || readiness_args+=(--require-resolved-sector)
    "$PYTHON3" "$SCRIPT_DIR/devicectl_contract.py" "${readiness_args[@]}" >/dev/null \
        || { echo "FAIL: PRE_LOG baseline no longer proves the launched OpenXRay readiness PID" >&2; exit 1; }
    PRE_LIFECYCLE_SEQUENCE=$("$PYTHON3" "$SCRIPT_DIR/devicectl_contract.py" log-sequence \
        --log "$PRE_LOG" --pid "$LAUNCHED_PID") \
        || { echo "FAIL: PRE_LOG baseline has invalid lifecycle sequencing for launched PID $LAUNCHED_PID" >&2; exit 1; }
    trace_event pre-log-baseline
fi

"$DEVICE_LOCK" renew-token openxray "$DEVICE_LEASE_TOKEN" 20 >/dev/null \
    || { echo "FAIL: could not renew the exact OpenXRay iPhone lease before XCUITest" >&2; exit 1; }
trace_event lease-renewed

set +e
trace_event test-without-building
ios_run_with_timeout 480 "$XCODEBUILD" test-without-building "${COMMON[@]}" \
    -destination "id=$DEVICE_UDID" \
    -only-testing:"$TEST_SELECTOR" \
    -resultBundlePath "$RESULT_BUNDLE"
test_status=$?
set -e

post_log_collected=1
if ! copy_from_container Documents/xr_boot.log "$POST_LOG"; then
    post_log_collected=0
    echo "FAIL: could not collect xr_boot.log after the UI test" >&2
else
    trace_event post-log-collected
fi

oracle_status=0
log_continued=0
if [ "$post_log_collected" = 1 ]; then
    cp "$POST_LOG" "$LOG_OUTPUT"
    if [ "$AUTONOMOUS_SESSION" = 1 ]; then
        pre_size=$(wc -c < "$PRE_LOG" | tr -d ' ')
        post_size=$(wc -c < "$POST_LOG" | tr -d ' ')
        if [ "$pre_size" -gt 0 ] && [ "$post_size" -ge "$pre_size" ] \
                && dd if="$POST_LOG" bs=1 count="$pre_size" 2>/dev/null | cmp -s - "$PRE_LOG"; then
            tail -c "+$((pre_size + 1))" "$POST_LOG" > "$DELTA_LOG"
            log_continued=1
        else
            cp "$POST_LOG" "$DELTA_LOG"
            echo "FAIL: post-test xr_boot.log does not preserve the exact PRE_LOG prefix" >&2
            oracle_status=1
        fi
    fi
else
    oracle_status=1
fi

case "$MODE" in
    --test-lifecycle|--test-reliability)
        if [ "$log_continued" != 1 ]; then
            echo "FAIL: lifecycle log oracle: post-test xr_boot.log did not preserve the exact PRE_LOG prefix" >&2
            oracle_status=1
        elif ! verify_lifecycle_oracle "$DELTA_LOG" "$LAUNCHED_PID" "$PRE_LIFECYCLE_SEQUENCE"; then
            oracle_status=1
        fi
        ;;
esac

case "$MODE" in
    --test-audio)
        if [ "$log_continued" != 1 ]; then
            echo "FAIL: audio log oracle: post-test xr_boot.log did not preserve the exact PRE_LOG prefix" >&2
            oracle_status=1
        elif ! verify_audio_oracle "$DELTA_LOG" "$LAUNCHED_PID" "$PRE_LIFECYCLE_SEQUENCE" 0; then
            oracle_status=1
        fi
        ;;
    --test-reliability)
        if [ "$log_continued" != 1 ]; then
            echo "FAIL: audio log oracle: post-test xr_boot.log did not preserve the exact PRE_LOG prefix" >&2
            oracle_status=1
        elif ! verify_audio_oracle "$DELTA_LOG" "$LAUNCHED_PID" "$PRE_LIFECYCLE_SEQUENCE" 5; then
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

if [ "$AUTONOMOUS_SESSION" = 1 ]; then
    SUCCESS_MESSAGE="PASS — UI automation and scoped device-log oracle passed; log: $LOG_OUTPUT"
else
    SUCCESS_MESSAGE="PASS — UI automation and post-test device log collection passed; log: $LOG_OUTPUT"
fi
