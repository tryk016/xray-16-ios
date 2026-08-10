#!/usr/bin/env bash
#
# Drive the game on the tethered iPhone: hold a key for a while, with no hands on
# the device.
#
#   ./misc/ios/input.sh w 1500      # walk forward for 1.5 s
#   ./misc/ios/input.sh --request-id 12345678-1234-4abc-8def-1234567890ab w 12000
#   ./misc/ios/input.sh i 100       # tap inventory
#   ./misc/ios/input.sh escape 100  # open/close pause menu
#   ./misc/ios/input.sh tap 650 142 # tap logical UI coordinate (932x430 space)
#   ./misc/ios/input.sh w           # default 1000 ms
#
# Exit code 0 = the engine acknowledged the exact generated request after
# dispatching the key press or completed tap. The log/frame remains the oracle
# for the resulting gameplay or UI state.
#
# WHY THIS EXISTS: rendering and gameplay regressions often need a repeatable
# camera path or a key press after unattended loading. File-driven input keeps
# those device tests reproducible without requiring a person to play each run.
#
# The engine side is an iOS-guarded block in xrEngine/xr_input.cpp (CInput::KeyUpdate).
# It only polls while `ios_diagnostics 1` or `ios_autoinput 1`; use
# `install_device.sh --autoinput` for movement without framebuffer readback.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=misc/ios/device_lease.sh
source "$SCRIPT_DIR/device_lease.sh"

DEVICE_UDID="00008130-000564403E12001C"
BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"

REQUEST_ID=""
if [ "${1:-}" = --request-id ]; then
    [ "$#" -ge 3 ] || { echo "usage: $0 [--request-id <lowercase-uuid>] <key> [duration_ms] | tap <x> <y>" >&2; exit 2; }
    REQUEST_ID="$2"
    shift 2
    [[ "$REQUEST_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
        || { echo "request ID must be a canonical lowercase UUID" >&2; exit 2; }
fi

[ "$#" -le 3 ] || { echo "usage: $0 [--request-id <lowercase-uuid>] <letter|digit|escape|tab|enter|space|arrow|menu> [duration_ms] | tap <x> <y>" >&2; exit 2; }
KEY="${1:-w}"
MS="${2:-1000}"

if [ "$KEY" = tap ]; then
    [ "$#" -eq 3 ] || { echo "usage: $0 tap <x> <y>" >&2; exit 2; }
    X="${2:-}"
    Y="${3:-}"
    case "$X" in
        ''|*[!0-9]*) echo "tap coordinates must be whole numbers" >&2; exit 2 ;;
    esac
    case "$Y" in
        ''|*[!0-9]*) echo "tap coordinates must be whole numbers" >&2; exit 2 ;;
    esac
    [ "$X" -lt 932 ] && [ "$Y" -lt 430 ] \
        || { echo "tap coordinates must fit the 932x430 logical UI space" >&2; exit 2; }
else
[ "$#" -le 2 ] || { echo "usage: $0 <letter|digit|escape|tab|enter|space|arrow|menu> [duration_ms]" >&2; exit 2; }
case "$KEY" in
    [a-zA-Z0-9]|escape|esc|tab|enter|return|space|up|down|left|right|menu) ;;
    *) echo "usage: $0 <letter|digit|escape|tab|enter|space|arrow|menu> [duration_ms] | tap <x> <y>" >&2; exit 2 ;;
esac
case "$MS" in
    ''|*[!0-9]*) echo "duration must be a whole number of milliseconds" >&2; exit 2 ;;
esac
[ "$MS" -gt 0 ] && [ "$MS" -le 30000 ] || { echo "duration must be 1..30000 ms" >&2; exit 2; }
fi

WORK="$(mktemp -d -t xrinput)"
cleanup()
{
    ios_device_lease_release
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
CFG="$WORK/user.ltx"
TRIGGER="$WORK/autoinput.txt"
ACK="$WORK/autoinput_ack.txt"
if [ -z "$REQUEST_ID" ]; then
    REQUEST_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
fi

ios_device_lease_acquire 2 || exit $?

ios_run_with_timeout 15 xcrun devicectl device copy from \
    --device "$DEVICE_UDID" \
    --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" \
    --user mobile \
    --source Documents/_appdata_/user.ltx \
    --destination "$CFG" >/dev/null 2>&1 \
    || { echo "FAIL: could not read user.ltx from the device" >&2; exit 1; }

if ! grep -Eq '^ios_diagnostics[[:space:]]+1[[:space:]]*$' "$CFG" \
        && ! grep -Eq '^ios_autoinput[[:space:]]+1[[:space:]]*$' "$CFG"; then
    echo "FAIL: cable input is disabled; launch with ./misc/ios/install_device.sh --autoinput or --diagnostics" >&2
    exit 1
fi

if [ "$KEY" = tap ]; then
    printf 'id %s tap %s %s\n' "$REQUEST_ID" "$X" "$Y" > "$TRIGGER"
else
    printf 'id %s %s %s\n' "$REQUEST_ID" "$KEY" "$MS" > "$TRIGGER"
fi

ios_run_with_timeout 15 xcrun devicectl device copy to \
    --device "$DEVICE_UDID" \
    --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" \
    --user mobile \
    --source "$TRIGGER" \
    --destination Documents/_appdata_/autoinput.txt >/dev/null 2>&1 \
    || { echo "FAIL: could not deliver the trigger file" >&2; exit 1; }

acknowledged=0
for _attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    sleep 0.25
    [ ! -e "$ACK" ] || unlink "$ACK"
    if ios_run_with_timeout 3 xcrun devicectl device copy from \
            --device "$DEVICE_UDID" \
            --domain-type appDataContainer \
            --domain-identifier "$BUNDLE_ID" \
            --user mobile \
            --source Documents/_appdata_/autoinput_ack.txt \
            --destination "$ACK" >/dev/null 2>&1; then
        read -r ack_id ack_result < "$ACK" || true
        if [ "${ack_id:-}" = "$REQUEST_ID" ]; then
            [ "${ack_result:-}" = accepted ] \
                || { echo "FAIL: engine rejected request $REQUEST_ID (${ack_result:-unknown})" >&2; exit 1; }
            acknowledged=1
            break
        fi
    fi
done
[ "$acknowledged" = 1 ] \
    || { echo "FAIL: engine did not acknowledge request $REQUEST_ID within 5 seconds" >&2; exit 1; }

if [ "$KEY" = tap ]; then
    echo "accepted: tap ($X, $Y) [$REQUEST_ID]"
else
    echo "accepted: hold '$KEY' for ${MS} ms [$REQUEST_ID]"
fi
