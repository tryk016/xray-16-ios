#!/usr/bin/env bash
#
# Drive the game on the tethered iPhone: hold a key for a while, with no hands on
# the device.
#
#   ./misc/ios/input.sh w 1500      # walk forward for 1.5 s
#   ./misc/ios/input.sh i 100       # tap inventory
#   ./misc/ios/input.sh escape 100  # open/close pause menu
#   ./misc/ios/input.sh w           # default 1000 ms
#
# Exit code 0 = the request was delivered. It does NOT confirm the game acted on
# it; check Documents/xr_boot.log for the "autoinput press/hold" line, or look at
# the next frame with ./misc/ios/shot.sh.
#
# WHY THIS EXISTS: rendering and gameplay regressions often need a repeatable
# camera path or a key press after unattended loading. File-driven input keeps
# those device tests reproducible without requiring a person to play each run.
#
# The engine side is an iOS-guarded block in xrEngine/xr_input.cpp (CInput::KeyUpdate).
# It only polls while `ios_diagnostics 1`; enable that mode by launching with
# ./misc/ios/install_device.sh --diagnostics.

set -u -o pipefail

DEVICE_UDID="00008130-000564403E12001C"
BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"

[ "$#" -le 2 ] || { echo "usage: $0 <letter|digit|escape|tab|enter|space|arrow|menu> [duration_ms]" >&2; exit 2; }
KEY="${1:-w}"
MS="${2:-1000}"

case "$KEY" in
    [a-zA-Z0-9]|escape|esc|tab|enter|return|space|up|down|left|right|menu) ;;
    *) echo "usage: $0 <letter|digit|escape|tab|enter|space|arrow|menu> [duration_ms]" >&2; exit 2 ;;
esac
case "$MS" in
    ''|*[!0-9]*) echo "duration must be a whole number of milliseconds" >&2; exit 2 ;;
esac
[ "$MS" -gt 0 ] && [ "$MS" -le 30000 ] || { echo "duration must be 1..30000 ms" >&2; exit 2; }

WORK="$(mktemp -d -t xrinput)"
trap 'rm -rf "$WORK"' EXIT
CFG="$WORK/user.ltx"
TRIGGER="$WORK/autoinput.txt"

xcrun devicectl device copy from \
    --device "$DEVICE_UDID" \
    --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" \
    --user mobile \
    --source Documents/_appdata_/user.ltx \
    --destination "$CFG" >/dev/null 2>&1 \
    || { echo "FAIL: could not read user.ltx from the device" >&2; exit 1; }

grep -Eq '^ios_diagnostics[[:space:]]+1[[:space:]]*$' "$CFG" \
    || { echo "FAIL: diagnostics are disabled; launch with ./misc/ios/install_device.sh --diagnostics" >&2; exit 1; }

printf '%s %s\n' "$KEY" "$MS" > "$TRIGGER"

xcrun devicectl device copy to \
    --device "$DEVICE_UDID" \
    --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" \
    --user mobile \
    --source "$TRIGGER" \
    --destination Documents/_appdata_/autoinput.txt >/dev/null 2>&1 \
    || { echo "FAIL: could not deliver the trigger file" >&2; exit 1; }

echo "sent: hold '$KEY' for ${MS} ms"
