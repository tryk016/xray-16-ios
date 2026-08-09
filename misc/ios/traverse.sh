#!/usr/bin/env bash
# Submit a repeatable forward/turn command sequence through the cable-input gate.
# UUID acknowledgements prove command acceptance, not actor movement.
# A larger batch can pass its exact shared-lease token; otherwise this script
# acquires the phone only for the requested route and releases it on exit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=misc/ios/device_lease.sh
source "$SCRIPT_DIR/device_lease.sh"
DURATION_SECONDS="${1:-1800}"
FORWARD_MS="${2:-25000}"

case "$DURATION_SECONDS" in
    ''|*[!0-9]*) echo "duration must be whole seconds" >&2; exit 2 ;;
esac
case "$FORWARD_MS" in
    ''|*[!0-9]*) echo "forward hold must be whole milliseconds" >&2; exit 2 ;;
esac
[ "$DURATION_SECONDS" -ge 1 ] && [ "$DURATION_SECONDS" -le 7200 ] \
    || { echo "duration must be 1..7200 seconds" >&2; exit 2; }
[ "$FORWARD_MS" -ge 1000 ] && [ "$FORWARD_MS" -le 30000 ] \
    || { echo "forward hold must be 1000..30000 ms" >&2; exit 2; }

cleanup()
{
    ios_device_lease_release
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The entire route is active phone work. Acquire only after local validation,
# with enough time for the requested duration plus a small cleanup margin.
lease_minutes=$(((DURATION_SECONDS + 299) / 60))
ios_device_lease_acquire "$lease_minutes" || exit $?

deadline=$(( $(date +%s) + DURATION_SECONDS ))
turn=a
accepted_forward_commands=0

while [ "$(date +%s)" -lt "$deadline" ]; do
    remaining=$(( deadline - $(date +%s) ))
    hold_ms="$FORWARD_MS"
    if [ "$((remaining * 1000))" -lt "$hold_ms" ]; then
        hold_ms=$((remaining * 1000))
    fi
    [ "$hold_ms" -gt 0 ] || break

    "$SCRIPT_DIR/input.sh" w "$hold_ms"
    sleep "$(awk -v milliseconds="$hold_ms" 'BEGIN { printf "%.3f", milliseconds / 1000.0 }')"
    accepted_forward_commands=$((accepted_forward_commands + 1))

    [ "$(date +%s)" -lt "$deadline" ] || break
    "$SCRIPT_DIR/input.sh" "$turn" 900
    sleep 1
    if [ "$turn" = a ]; then
        turn=d
    else
        turn=a
    fi
done

echo "RESULT — commands accepted: ${accepted_forward_commands} forward holds plus alternating turns in ${DURATION_SECONDS}s; no actor movement oracle"
