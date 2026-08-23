#!/usr/bin/env bash

set -euo pipefail

# The selected shell sees only a raw, non-authoritative append sink.  It never
# receives a telemetry directory, nonce, run ID, profile or context descriptor.
feedback_raw_fd="${XRAY_FEEDBACK_RAW_EVENT_FD:-}"
unset XRAY_FEEDBACK_RAW_EVENT_FD XRAY_FEEDBACK_CONTEXT_FD
feedback_enabled=0
[[ "$feedback_raw_fd" =~ ^[3-9][0-9]*$ ]] && feedback_enabled=1
for feedback_environment_name in "${!OPENXRAY_TEST_FEEDBACK_@}"; do
    unset "$feedback_environment_name"
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=misc/ios/ui_automation/log_oracles.sh
source "$SCRIPT_DIR/log_oracles.sh"

TEMP_DIR="$(mktemp -d -t openxray-log-oracles.XXXXXX)"

cleanup()
{
    local path
    for path in "$TEMP_DIR"/*; do
        [ ! -e "$path" ] || unlink "$path"
    done
    rmdir "$TEMP_DIR"
}

trap cleanup EXIT

feedback_python()
{
    [ "$feedback_enabled" = 1 ] || return 0
    BASH_ENV='' XRAY_FEEDBACK_RAW_EVENT_FD="$feedback_raw_fd" \
        python3 -S "$SCRIPT_DIR/../test_feedback_unittest.py" "$@"
}

record_case()
{
    local name="$1"
    local status="$2"
    local started="$3"
    [ "$feedback_enabled" = 1 ] || return 0
    feedback_python --raw-emit \
        "sh:misc/ios/ui_automation/test_log_oracles.sh::$name" \
        "$([ "$status" -eq 0 ] && printf PASS || printf FAIL)" \
        "$started" >/dev/null 2>&1 || true
}

write_fixture()
{
    local name="$1"
    shift
    printf '%s\n' "$@" > "$TEMP_DIR/$name.log"
}

expect_pass()
{
    local name="$1"
    local oracle="$2"
    shift 2
    local stderr_path="$TEMP_DIR/$name.stderr"

    local started status
    if [ "$feedback_enabled" = 1 ]; then
        started=$(python3 -c 'import time; print(time.monotonic_ns())' 2>/dev/null || true)
    else
        started=0
    fi
    if "$oracle" "$@" > /dev/null 2> "$stderr_path"; then
        if [ -s "$stderr_path" ]; then
            echo "FAIL: $name: PASS oracle wrote stderr" >&2
            sed 's/^/  /' "$stderr_path" >&2
            record_case "$name" 1 "$started"
            return 1
        fi
        printf 'PASS: %s\n' "$name"
        record_case "$name" 0 "$started"
        return 0
    fi

    echo "FAIL: $name: expected status 0" >&2
    sed 's/^/  /' "$stderr_path" >&2
    record_case "$name" 1 "$started"
    return 1
}

expect_fail()
{
    local name="$1"
    local expected_message="$2"
    local oracle="$3"
    shift 3
    local stderr_path="$TEMP_DIR/$name.stderr"
    local status=0 started
    if [ "$feedback_enabled" = 1 ]; then
        started=$(python3 -c 'import time; print(time.monotonic_ns())' 2>/dev/null || true)
    else
        started=0
    fi

    if "$oracle" "$@" > /dev/null 2> "$stderr_path"; then
        echo "FAIL: $name: expected non-zero status" >&2
        record_case "$name" 1 "$started"
        return 1
    else
        status=$?
    fi
    if [ "$status" -eq 0 ]; then
        echo "FAIL: $name: expected non-zero status" >&2
        record_case "$name" 1 "$started"
        return 1
    fi
    if ! diff -u <(printf '%s\n' "$expected_message") "$stderr_path"; then
        echo "FAIL: $name: unexpected diagnostic" >&2
        record_case "$name" 1 "$started"
        return 1
    fi
    printf 'PASS: %s\n' "$name"
    record_case "$name" 0 "$started"
}

# Additional mutation/teardown assertions share this shell stage but are not
# stable feedback cases.  Keep the frozen expect_pass/expect_fail inventory at
# its original 12 IDs.
assert_untracked_pass()
{
    local name="$1"
    local oracle="$2"
    shift 2
    local stderr_path="$TEMP_DIR/$name.stderr"

    if "$oracle" "$@" > /dev/null 2> "$stderr_path" && [ ! -s "$stderr_path" ]; then
        printf 'PASS: %s (untracked)\n' "$name"
        return 0
    fi
    echo "FAIL: $name: untracked assertion expected status 0 and empty stderr" >&2
    sed 's/^/  /' "$stderr_path" >&2
    return 1
}

assert_untracked_fail()
{
    local name="$1"
    local expected_message="$2"
    local oracle="$3"
    shift 3
    local stderr_path="$TEMP_DIR/$name.stderr"

    if "$oracle" "$@" > /dev/null 2> "$stderr_path"; then
        echo "FAIL: $name: untracked assertion expected non-zero status" >&2
        return 1
    fi
    if ! diff -u <(printf '%s\n' "$expected_message") "$stderr_path"; then
        echo "FAIL: $name: untracked assertion produced an unexpected diagnostic" >&2
        return 1
    fi
    printf 'PASS: %s (untracked)\n' "$name"
}

lifecycle_group()
{
    LIFECYCLE_SEQ=$((LIFECYCLE_SEQ + 1))
    local deactivate_seq="$LIFECYCLE_SEQ"
    LIFECYCLE_SEQ=$((LIFECYCLE_SEQ + 1))
    local activate_seq="$LIFECYCLE_SEQ"
    printf '%s\n' \
        '* iOS: app deactivate' \
        "* iOS lifecycle v1 pid=$LIFECYCLE_PID seq=$deactivate_seq event=deactivate" \
        '* iOS: app activate' \
        '* iOS: foreground drawable 1864x860 (engine 1864x860)' \
        "* iOS lifecycle v1 pid=$LIFECYCLE_PID seq=$activate_seq event=activate"
}

LIFECYCLE_PID=4242
LIFECYCLE_SEQ=0
{
    for _ in 1 2 3 4 5; do
        lifecycle_group
    done
} > "$TEMP_DIR/lifecycle-five.log"
expect_pass lifecycle-five verify_lifecycle_oracle "$TEMP_DIR/lifecycle-five.log" "$LIFECYCLE_PID"

LIFECYCLE_SEQ=0
{
    for _ in 1 2 3 4 5; do
        lifecycle_group
    done
    printf '%s\n' '* iOS: app deactivate'
} > "$TEMP_DIR/lifecycle-five-teardown.log"
assert_untracked_fail lifecycle-five-teardown \
    'FAIL: lifecycle log oracle: truncated lifecycle group' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-five-teardown.log" "$LIFECYCLE_PID"

write_fixture lifecycle-missing-final-activate \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=4242 seq=1 event=deactivate' \
    '* iOS: app activate' \
    '* iOS: foreground drawable 1864x860 (engine 1864x860)'
assert_untracked_fail lifecycle-missing-final-activate \
    'FAIL: lifecycle log oracle: truncated lifecycle group' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-missing-final-activate.log" "$LIFECYCLE_PID"

write_fixture lifecycle-drawable-before-app-activate \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=4242 seq=1 event=deactivate' \
    '* iOS: foreground drawable 1864x860 (engine 1864x860)'
assert_untracked_fail lifecycle-drawable-before-app-activate \
    'FAIL: lifecycle log oracle: foreground drawable before app activate' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-drawable-before-app-activate.log" "$LIFECYCLE_PID"

LIFECYCLE_SEQ=0
{
    for _ in 1 2 3 4 5 6; do
        lifecycle_group
    done
} > "$TEMP_DIR/lifecycle-six.log"
assert_untracked_fail lifecycle-six \
    'FAIL: lifecycle log oracle: sixth complete lifecycle group' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-six.log" "$LIFECYCLE_PID"

LIFECYCLE_SEQ=0
{
    for _ in 1 2 3 4; do
        lifecycle_group
    done
} > "$TEMP_DIR/lifecycle-four.log"
expect_fail lifecycle-four \
    'FAIL: lifecycle log oracle: expected exactly 5 complete lifecycle groups, observed 4' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-four.log" "$LIFECYCLE_PID"

write_fixture lifecycle-bad-order \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=4242 seq=1 event=deactivate' \
    '* iOS: app activate' \
    '* iOS lifecycle v1 pid=4242 seq=2 event=activate'
expect_fail lifecycle-bad-order \
    'FAIL: lifecycle log oracle: activate before foreground drawable' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-bad-order.log" "$LIFECYCLE_PID"

write_fixture lifecycle-duplicate-deactivate \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=4242 seq=1 event=deactivate' \
    '* iOS: app deactivate'
expect_fail lifecycle-duplicate-deactivate \
    'FAIL: lifecycle log oracle: deactivate before the previous group completed' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-duplicate-deactivate.log" "$LIFECYCLE_PID"

write_fixture lifecycle-wrong-pid \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=99 seq=1 event=deactivate'
assert_untracked_fail lifecycle-wrong-pid \
    'FAIL: lifecycle log oracle: lifecycle marker PID 99 does not match expected PID 4242' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-wrong-pid.log" "$LIFECYCLE_PID"

write_fixture lifecycle-nonmonotonic \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=4242 seq=2 event=deactivate' \
    '* iOS: app activate' \
    '* iOS: foreground drawable 1864x860 (engine 1864x860)' \
    '* iOS lifecycle v1 pid=4242 seq=2 event=activate'
assert_untracked_fail lifecycle-nonmonotonic \
    'FAIL: lifecycle log oracle: lifecycle marker sequence 2 is not strictly increasing after 2' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-nonmonotonic.log" "$LIFECYCLE_PID"

LIFECYCLE_SEQ=0
{
    for _ in 1 2 3 4 5; do
        lifecycle_group
    done
} > "$TEMP_DIR/lifecycle-pre-baseline-nonmonotonic.log"
assert_untracked_fail lifecycle-pre-baseline-nonmonotonic \
    'FAIL: lifecycle log oracle: lifecycle marker sequence 1 is not strictly increasing after 1' \
    verify_lifecycle_oracle "$TEMP_DIR/lifecycle-pre-baseline-nonmonotonic.log" "$LIFECYCLE_PID" 1

write_fixture audio-target \
    'iOS audio: interruption began, suspending sound, wasSuspended=0' \
    'iOS audio: interruption ended (shouldResume), sound resumed'
expect_pass audio-target verify_audio_oracle "$TEMP_DIR/audio-target.log" "$LIFECYCLE_PID" 0 0

write_fixture audio-missing-end \
    'iOS audio: interruption began, suspending sound, wasSuspended=0'
expect_fail audio-missing-end \
    'FAIL: audio log oracle: target foreground audio interruption did not end' \
    verify_audio_oracle "$TEMP_DIR/audio-missing-end.log" "$LIFECYCLE_PID" 0 0

write_fixture audio-target-deactivate \
    'iOS audio: interruption began, suspending sound, wasSuspended=0' \
    '* iOS: app deactivate' \
    'iOS audio: interruption ended (shouldResume), sound resumed'
expect_fail audio-target-deactivate \
    'FAIL: audio log oracle: OpenXRay deactivated between audio interruption begin and end' \
    verify_audio_oracle "$TEMP_DIR/audio-target-deactivate.log" "$LIFECYCLE_PID" 0 0

write_fixture audio-nested-target \
    'iOS audio: interruption began, suspending sound, wasSuspended=0' \
    'iOS audio: interruption began, suspending sound, wasSuspended=0'
expect_fail audio-nested-target \
    'FAIL: audio log oracle: nested audio interruption began before interruption ended' \
    verify_audio_oracle "$TEMP_DIR/audio-nested-target.log" "$LIFECYCLE_PID" 0 0

write_fixture audio-suspended-before-target \
    'iOS audio: interruption began, suspending sound, wasSuspended=1' \
    'iOS audio: interruption ended (background), sound resumed' \
    'iOS audio: interruption began, suspending sound, wasSuspended=0' \
    'iOS audio: interruption ended (shouldResume), sound resumed'
expect_pass audio-suspended-before-target verify_audio_oracle "$TEMP_DIR/audio-suspended-before-target.log" "$LIFECYCLE_PID" 0 0

write_fixture audio-suspended-only \
    'iOS audio: interruption began, suspending sound, wasSuspended=1' \
    'iOS audio: interruption ended (background), sound resumed'
expect_fail audio-suspended-only \
    'FAIL: audio log oracle: no ordered wasSuspended=0 interruption begin-to-ended pair' \
    verify_audio_oracle "$TEMP_DIR/audio-suspended-only.log" "$LIFECYCLE_PID" 0 0

{
    for _ in 1 2 3 4; do
        lifecycle_group
    done
    printf '%s\n' 'iOS audio: interruption began, suspending sound, wasSuspended=0'
} > "$TEMP_DIR/reliability-target-early.log"
expect_fail reliability-target-early \
    'FAIL: audio log oracle: audio interruption began before exactly ordered lifecycle groups=5' \
    verify_audio_oracle "$TEMP_DIR/reliability-target-early.log" "$LIFECYCLE_PID" 0 5

LIFECYCLE_SEQ=0
{
    printf '%s\n' \
        'iOS audio: interruption began, suspending sound, wasSuspended=1' \
        'iOS audio: interruption ended (background), sound resumed'
    for _ in 1 2 3 4 5; do
        lifecycle_group
    done
    printf '%s\n' \
        'iOS audio: interruption began, suspending sound, wasSuspended=1' \
        'iOS audio: interruption ended (background), sound resumed' \
        'iOS audio: interruption began, suspending sound, wasSuspended=0' \
        'iOS audio: interruption ended (shouldResume), sound resumed'
} > "$TEMP_DIR/reliability-target-after-five.log"
expect_pass reliability-target-after-five verify_audio_oracle "$TEMP_DIR/reliability-target-after-five.log" "$LIFECYCLE_PID" 0 5

LIFECYCLE_SEQ=0
{
    for _ in 1 2 3 4 5 6; do
        lifecycle_group
    done
    printf '%s\n' \
        'iOS audio: interruption began, suspending sound, wasSuspended=0' \
        'iOS audio: interruption ended (shouldResume), sound resumed'
} > "$TEMP_DIR/reliability-target-after-six.log"
assert_untracked_fail reliability-target-after-six \
    'FAIL: audio log oracle: more than expected complete lifecycle groups=5' \
    verify_audio_oracle "$TEMP_DIR/reliability-target-after-six.log" "$LIFECYCLE_PID" 0 5

LIFECYCLE_SEQ=0
{
    for _ in 1 2 3 4 5; do
        lifecycle_group
    done
    printf '%s\n' \
        'iOS audio: interruption began, suspending sound, wasSuspended=0' \
        'iOS audio: interruption ended (shouldResume), sound resumed'
    printf '%s\n' '* iOS: app deactivate'
} > "$TEMP_DIR/reliability-target-five-teardown.log"
assert_untracked_fail reliability-target-five-teardown \
    'FAIL: audio log oracle: lifecycle event after target audio interruption' \
    verify_audio_oracle "$TEMP_DIR/reliability-target-five-teardown.log" "$LIFECYCLE_PID" 0 5

write_fixture audio-wrong-lifecycle-pid \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=99 seq=1 event=activate' \
    'iOS audio: interruption began, suspending sound, wasSuspended=0' \
    'iOS audio: interruption ended (shouldResume), sound resumed'
assert_untracked_fail audio-wrong-lifecycle-pid \
    'FAIL: audio log oracle: lifecycle marker PID 99 does not match expected PID 4242' \
    verify_audio_oracle "$TEMP_DIR/audio-wrong-lifecycle-pid.log" "$LIFECYCLE_PID" 0 0

write_fixture audio-nonmonotonic-lifecycle \
    '* iOS: app deactivate' \
    '* iOS lifecycle v1 pid=4242 seq=2 event=deactivate' \
    '* iOS: app activate' \
    '* iOS: foreground drawable 1864x860 (engine 1864x860)' \
    '* iOS lifecycle v1 pid=4242 seq=2 event=activate' \
    'iOS audio: interruption began, suspending sound, wasSuspended=0' \
    'iOS audio: interruption ended (shouldResume), sound resumed'
assert_untracked_fail audio-nonmonotonic-lifecycle \
    'FAIL: audio log oracle: lifecycle marker sequence 2 is not strictly increasing after 2' \
    verify_audio_oracle "$TEMP_DIR/audio-nonmonotonic-lifecycle.log" "$LIFECYCLE_PID" 0 0

printf 'PASS: log oracle fixtures complete\n'
