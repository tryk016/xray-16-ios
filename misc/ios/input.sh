#!/usr/bin/env bash
#
# Drive the game on the tethered iPhone: hold a key for a while, with no hands on
# the device.
#
#   ./misc/ios/input.sh w 1500      # walk forward for 1.5 s
#   ./misc/ios/input.sh a 600       # strafe left for 0.6 s
#   ./misc/ios/input.sh w           # default 1000 ms
#
# Exit code 0 = the request was delivered. It does NOT confirm the game acted on
# it; check Documents/xr_boot.log for the "autoinput hold" line, or just look at
# the next frame with ./misc/ios/shot.sh.
#
# WHY THIS EXISTS: several graphics defects on this port only resolve once the
# CAMERA MOVES - standing still they persist indefinitely, no matter how many
# frames elapse. So a screenshot of a freshly loaded save shows only the broken
# state, and any before/after comparison needs the player to actually walk.
#
# The engine side is an iOS-guarded block in xrEngine/xr_input.cpp (CInput::KeyUpdate)
# which polls for this trigger file 4x/sec, consumes it, and forces the key into
# keyboardState so it travels the normal IR_OnKeyboardHold path.

set -u -o pipefail

DEVICE_UDID="00008130-000564403E12001C"
BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"

KEY="${1:-w}"
MS="${2:-1000}"

case "$KEY" in
    [a-zA-Z]) ;;
    *) echo "usage: $0 <single letter> [duration_ms]   e.g. $0 w 1500" >&2; exit 2 ;;
esac
case "$MS" in
    ''|*[!0-9]*) echo "duration must be a whole number of milliseconds" >&2; exit 2 ;;
esac
[ "$MS" -gt 0 ] && [ "$MS" -le 30000 ] || { echo "duration must be 1..30000 ms" >&2; exit 2; }

TMP="$(mktemp -t xrinput)"
printf '%s %s\n' "$KEY" "$MS" > "$TMP"

xcrun devicectl device copy to \
    --device "$DEVICE_UDID" \
    --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" \
    --user mobile \
    --source "$TMP" \
    --destination Documents/_appdata_/autoinput.txt >/dev/null 2>&1 \
    || { rm -f "$TMP"; echo "FAIL: could not deliver the trigger file" >&2; exit 1; }

rm -f "$TMP"
echo "sent: hold '$KEY' for ${MS} ms"
