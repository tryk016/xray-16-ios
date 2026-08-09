#!/usr/bin/env bash

# Shared iPhone lease helper for device-facing OpenXRay shell tools.
# Source this file, call ios_device_lease_acquire immediately before the first
# device command, and call ios_device_lease_release from the caller's cleanup.

IOS_DEVICE_LOCK="${OPENXRAY_DEVICE_LOCK:-/Users/patryk/.codex/device-coordination/ios-device-lock.sh}"
IOS_DEVICE_LEASE_TOKEN="${OPENXRAY_DEVICE_LEASE_TOKEN:-}"
IOS_DEVICE_LEASE_OWNED=0
IOS_DEVICE_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ios_run_with_timeout()
{
    python3 "$IOS_DEVICE_HELPER_DIR/command_timeout.py" "$@"
}

ios_device_lease_acquire()
{
    local minutes="${1:-5}"
    local status acquired

    [ -x "$IOS_DEVICE_LOCK" ] || {
        echo "FAIL: shared iPhone coordinator is unavailable: $IOS_DEVICE_LOCK" >&2
        return 1
    }

    status=$("$IOS_DEVICE_LOCK" status) || return $?
    if grep -q '"state":"free"' <<< "$status"; then
        acquired=$("$IOS_DEVICE_LOCK" acquire openxray "$minutes") || return $?
        IOS_DEVICE_LEASE_TOKEN=$(sed -n 's/.*"token":"\([^"]*\)".*/\1/p' <<< "$acquired")
        [ -n "$IOS_DEVICE_LEASE_TOKEN" ] || {
            echo "FAIL: coordinator did not return an iPhone lease token" >&2
            return 1
        }
        IOS_DEVICE_LEASE_OWNED=1
        export OPENXRAY_DEVICE_LEASE_TOKEN="$IOS_DEVICE_LEASE_TOKEN"
        return 0
    fi

    if [ -n "$IOS_DEVICE_LEASE_TOKEN" ] \
            && grep -q '"owner":"openxray"' <<< "$status" \
            && grep -qF "\"token\":\"$IOS_DEVICE_LEASE_TOKEN\"" <<< "$status"; then
        export OPENXRAY_DEVICE_LEASE_TOKEN="$IOS_DEVICE_LEASE_TOKEN"
        return 0
    fi

    echo "FAIL: shared iPhone is busy: $status" >&2
    return 3
}

ios_device_lease_release()
{
    if [ "$IOS_DEVICE_LEASE_OWNED" = 1 ]; then
        IOS_DEVICE_LEASE_OWNED=0
        "$IOS_DEVICE_LOCK" release-token openxray "$IOS_DEVICE_LEASE_TOKEN" >/dev/null 2>&1 || true
    fi
}
