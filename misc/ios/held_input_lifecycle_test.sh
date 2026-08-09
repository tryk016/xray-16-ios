#!/usr/bin/env bash

# Device oracle for synthetic held-input cancellation and stale-trigger replay.
# The matching app must already be running with ios_autoinput=1 and diagnostics=0.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=misc/ios/device_lease.sh
source "$SCRIPT_DIR/device_lease.sh"

DEVICE_UDID="${OPENXRAY_DEVICE_UDID:-00008130-000564403E12001C}"
BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"
OUTPUT="${OPENXRAY_HELD_INPUT_LOG:-/tmp/OpenXRay-held-input-$(date +%Y%m%d-%H%M%S)-xr_boot.log}"

[ "$#" -eq 0 ] || { echo "usage: $0" >&2; exit 2; }
[ ! -e "$OUTPUT" ] || { echo "FAIL: output already exists: $OUTPUT" >&2; exit 1; }

WORK="$(mktemp -d -t xrheldinput)"
PRE_LOG="$WORK/pre.log"
POST_LOG="$WORK/post.log"
DELTA_LOG="$WORK/delta.log"
CONFIG="$WORK/user.ltx"
STALE_TRIGGER="$WORK/autoinput-stale.txt"

cleanup()
{
    ios_device_lease_release
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

copy_from_container()
{
    local source="$1" destination="$2"
    ios_run_with_timeout 20 xcrun devicectl device copy from \
        --device "$DEVICE_UDID" \
        --domain-type appDataContainer \
        --domain-identifier "$BUNDLE_ID" \
        --user mobile \
        --source "$source" --destination "$destination" >/dev/null 2>&1
}

# Everything above is phone-free validation/setup. Lease only the actual test.
ios_device_lease_acquire 5 || exit $?

copy_from_container Documents/_appdata_/user.ltx "$CONFIG" \
    || { echo "FAIL: could not read user.ltx" >&2; exit 1; }
grep -Eq '^ios_diagnostics[[:space:]]+0[[:space:]]*$' "$CONFIG" \
    || { echo "FAIL: held-input test requires ios_diagnostics 0" >&2; exit 1; }
grep -Eq '^ios_autoinput[[:space:]]+1[[:space:]]*$' "$CONFIG" \
    || { echo "FAIL: launch first with install_device.sh --fast --autoinput" >&2; exit 1; }

if ! copy_from_container Documents/xr_boot.log "$PRE_LOG"; then
    : > "$PRE_LOG"
fi

accepted_output=$(OPENXRAY_DEVICE_LEASE_TOKEN="$IOS_DEVICE_LEASE_TOKEN" \
    "$SCRIPT_DIR/input.sh" w 30000)
accepted_id=$(sed -n 's/.*\[\([0-9a-f-]*\)\]$/\1/p' <<< "$accepted_output")
[[ "$accepted_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
    || { echo "FAIL: could not extract accepted request UUID: $accepted_output" >&2; exit 1; }

# Background while W is still held. Then place a command that must be discarded
# at foreground activation instead of executing in the resumed level.
ios_run_with_timeout 20 xcrun devicectl device process launch \
    --device "$DEVICE_UDID" com.apple.mobilesafari >/dev/null
sleep 2
stale_id=$(uuidgen | tr '[:upper:]' '[:lower:]')
printf 'id %s w 30000\n' "$stale_id" > "$STALE_TRIGGER"
ios_run_with_timeout 20 xcrun devicectl device copy to \
    --device "$DEVICE_UDID" \
    --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" \
    --user mobile \
    --source "$STALE_TRIGGER" \
    --destination Documents/_appdata_/autoinput.txt >/dev/null 2>&1

ios_run_with_timeout 20 xcrun devicectl device process launch \
    --device "$DEVICE_UDID" "$BUNDLE_ID" >/dev/null
sleep 6
copy_from_container Documents/xr_boot.log "$POST_LOG" \
    || { echo "FAIL: could not collect xr_boot.log" >&2; exit 1; }
cp "$POST_LOG" "$OUTPUT"

pre_size=$(wc -c < "$PRE_LOG" | tr -d ' ')
post_size=$(wc -c < "$POST_LOG" | tr -d ' ')
if ! { [ "$pre_size" -gt 0 ] && [ "$post_size" -ge "$pre_size" ] \
        && dd if="$POST_LOG" bs=1 count="$pre_size" 2>/dev/null | cmp -s - "$PRE_LOG"; }; then
    echo "FAIL: xr_boot.log restarted; process continuity was not preserved" >&2
    exit 1
fi
tail -c "+$((pre_size + 1))" "$POST_LOG" > "$DELTA_LOG"

awk -v accepted="$accepted_id" '
    index($0, "autoinput request " accepted " press/hold") { phase = 1; next }
    phase == 1 && /level dispatched keyboard release to entity scancode 26/ { phase = 2; next }
    phase == 2 && index($0, "lifecycle cancelled autoinput request " accepted " scancode 26") {
        phase = 3
        next
    }
    phase == 3 && /discarded pending autoinput trigger at lifecycle boundary/ { discarded = 1 }
    phase == 3 && /\* iOS: app activate/ { activated = 1 }
    phase == 3 && activated \
        && /foreground drawable 1864x860 \(engine 1864x860\)/ { recovered = 1 }
    index($0, "autoinput request " accepted " released scancode") { timer_release = 1 }
    phase == 3 && /autoinput request .* press\/hold/ { replayed = 1 }
    END { exit !(phase == 3 && discarded && activated && recovered && !timer_release && !replayed) }
' "$DELTA_LOG" || {
    echo "FAIL: held-input/replay lifecycle oracle did not complete; log: $OUTPUT" >&2
    exit 1
}

echo "PASS — held request $accepted_id reached entity release; stale request $stale_id did not replay"
echo "log: $OUTPUT"
