#!/usr/bin/env bash
#
# Controlled future-phone batch for the unresolved dark-frame observation.
# It produces correlation evidence only: the offline verifier never classifies
# pixels and never attributes a lighting change to movement or streaming.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=misc/ios/device_lease.sh
source "$SCRIPT_DIR/device_lease.sh"
SHOT_TOOL="${OPENXRAY_SHOT_TOOL:-$SCRIPT_DIR/shot.sh}"
INPUT_TOOL="${OPENXRAY_INPUT_TOOL:-$SCRIPT_DIR/input.sh}"
EVIDENCE_TOOL="${OPENXRAY_EVIDENCE_TOOL:-$SCRIPT_DIR/lighting_ab_evidence.py}"

[ "$#" -eq 1 ] || { echo "usage: $0 <new-evidence-directory>" >&2; exit 2; }
OUTPUT_DIR="$1"
if [ -e "$OUTPUT_DIR" ] || [ -L "$OUTPUT_DIR" ]; then
    echo "FAIL: evidence directory already exists: $OUTPUT_DIR" >&2
    exit 1
fi
mkdir -m 700 "$OUTPUT_DIR" || { echo "FAIL: cannot create evidence directory: $OUTPUT_DIR" >&2; exit 1; }

cleanup()
{
    ios_device_lease_release
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

capture_token()
{
    python3 "$EVIDENCE_TOOL" metadata-token --metadata "$1"
}

third_generation()
{
    python3 "$EVIDENCE_TOOL" add-sequence --token "$1" --increment 3
}

renew_exact_lease()
{
    # Worst case: a shot has a 15 s config read, 15 s baseline, then twelve
    # 1 s + 3 s + 15 s + 3 s polling attempts (282 s). Input is bounded below
    # 95 s. Renew before each device phase so an 8-minute exact-token lease
    # always covers the phase even when previous phases used their full budget.
    "$IOS_DEVICE_LOCK" renew-token openxray "$IOS_DEVICE_LEASE_TOKEN" 8 >/dev/null \
        || { echo "FAIL: could not renew the exact shared iPhone lease" >&2; exit 1; }
}

# All device subtools inherit this exact token. Their nested cleanup sees a
# borrowed lease and cannot release this batch's newer owner token.
ios_device_lease_acquire 8 || exit $?
export OPENXRAY_DEVICE_LEASE_TOKEN="$IOS_DEVICE_LEASE_TOKEN"

"$SHOT_TOOL" --metadata-out "$OUTPUT_DIR/A.json" "$OUTPUT_DIR/A.png" >&2
renew_exact_lease
expected_b=$(third_generation "$(capture_token "$OUTPUT_DIR/A.json")")
"$SHOT_TOOL" --expect-token "$expected_b" --metadata-out "$OUTPUT_DIR/B.json" "$OUTPUT_DIR/B.png" >&2

renew_exact_lease
request_id=$(uuidgen | tr '[:upper:]' '[:lower:]')
"$INPUT_TOOL" --request-id "$request_id" w 12000 >&2
renew_exact_lease
expected_c=$(third_generation "$(capture_token "$OUTPUT_DIR/B.json")")
"$SHOT_TOOL" --expect-token "$expected_c" --metadata-out "$OUTPUT_DIR/C.json" "$OUTPUT_DIR/C.png" >&2

python3 "$EVIDENCE_TOOL" verify-ab \
    --a "$OUTPUT_DIR/A.json" --a-image "$OUTPUT_DIR/A.png" \
    --b "$OUTPUT_DIR/B.json" --b-image "$OUTPUT_DIR/B.png" \
    --c "$OUTPUT_DIR/C.json" --c-image "$OUTPUT_DIR/C.png" \
    --request-id "$request_id" --report "$OUTPUT_DIR/report.json"
