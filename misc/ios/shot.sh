#!/usr/bin/env bash
#
# Pull one atomically correlated capture-v2 frame from the tethered iPhone.
#
#   ./misc/ios/shot.sh out.png
#   ./misc/ios/shot.sh --metadata-out out.json out.png
#
# Capture-v2 is a PPM plus a canonical JSON sidecar. The producer publishes
# PPM first and JSON second, so this script copies metadata-before, PPM and
# metadata-after and accepts only byte-identical metadata whose token matches
# the PPM header. It never turns an unpaired image into evidence.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=misc/ios/device_lease.sh
source "$SCRIPT_DIR/device_lease.sh"

DEVICE_UDID="00008130-000564403E12001C"
BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"
METADATA_OUT=""
EXPECT_TOKEN=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --metadata-out)
            [ -z "$METADATA_OUT" ] && [ "$#" -ge 2 ] \
                || { echo "usage: $0 [--expect-token session:sequence] [--metadata-out capture.json] [output.png]" >&2; exit 2; }
            METADATA_OUT="$2"
            shift 2
            ;;
        --expect-token)
            [ -z "$EXPECT_TOKEN" ] && [ "$#" -ge 2 ] \
                || { echo "usage: $0 [--expect-token session:sequence] [--metadata-out capture.json] [output.png]" >&2; exit 2; }
            EXPECT_TOKEN="$2"
            [[ "$EXPECT_TOKEN" =~ ^[0-9a-f]{32}:[1-9][0-9]*$ ]] \
                || { echo "expected token must be canonical session:sequence" >&2; exit 2; }
            shift 2
            ;;
        *) break ;;
    esac
done
[ "$#" -le 1 ] || { echo "usage: $0 [--metadata-out capture.json] [output.png]" >&2; exit 2; }
OUT="${1:-/tmp/xr_shot.png}"

# --metadata-out is evidence mode. Do not silently overwrite any artifact that
# a later A/B verifier might mistake for this batch.
if [ -n "$METADATA_OUT" ]; then
    { [ ! -e "$OUT" ] && [ ! -L "$OUT" ]; } \
        || { echo "FAIL: evidence PNG already exists: $OUT" >&2; exit 1; }
    { [ ! -e "$METADATA_OUT" ] && [ ! -L "$METADATA_OUT" ]; } \
        || { echo "FAIL: evidence metadata already exists: $METADATA_OUT" >&2; exit 1; }
fi

WORK="$(mktemp -d -t xrshot)"
cleanup()
{
    ios_device_lease_release
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

PPM="$WORK/xr_shot.ppm"
CFG="$WORK/user.ltx"
META_BASE="$WORK/meta-base.json"
META_BEFORE="$WORK/meta-before.json"
META_AFTER="$WORK/meta-after.json"

fail() { echo "FAIL: $*" >&2; exit 1; }

copy_from_container()
{
    local source="$1" destination="$2" timeout="$3"
    rm -f "$destination"
    ios_run_with_timeout "$timeout" xcrun devicectl device copy from \
        --device "$DEVICE_UDID" \
        --domain-type appDataContainer \
        --domain-identifier "$BUNDLE_ID" \
        --user mobile \
        --source "$source" \
        --destination "$destination" >/dev/null 2>&1
}

ios_device_lease_acquire 2 || exit $?

copy_from_container Documents/_appdata_/user.ltx "$CFG" 15 \
    || fail "could not read user.ltx from the device"
grep -Eq '^ios_diagnostics[[:space:]]+1[[:space:]]*$' "$CFG" \
    || fail "diagnostics are disabled; launch with ./misc/ios/install_device.sh --diagnostics"

# A missing first read is not a usable baseline. Require two metadata
# generations in that case so a capture left by another process cannot pass.
baseline_ready=0
if copy_from_container Documents/xr_shot_meta.txt "$META_BASE" 15 && [ -s "$META_BASE" ]; then
    baseline_ready=1
fi

fresh=0
for _attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 1
    if ! copy_from_container Documents/xr_shot_meta.txt "$META_BEFORE" 3 || [ ! -s "$META_BEFORE" ]; then
        continue
    fi
    if [ -n "$EXPECT_TOKEN" ]; then
        if ! actual_token=$(python3 "$SCRIPT_DIR/lighting_ab_evidence.py" metadata-token --metadata "$META_BEFORE"); then
            cp "$META_BEFORE" "$META_BASE"
            continue
        fi
        token_order=$(python3 "$SCRIPT_DIR/lighting_ab_evidence.py" token-order \
            --expected "$EXPECT_TOKEN" --actual "$actual_token") || fail "could not compare capture tokens"
        case "$token_order" in
            before) continue ;;
            equal) ;;
            after|session-mismatch) fail "expected capture token $EXPECT_TOKEN was missed (saw $actual_token)" ;;
            *) fail "invalid capture token ordering result: $token_order" ;;
        esac
    else
        if [ "$baseline_ready" = 0 ]; then
            cp "$META_BEFORE" "$META_BASE"
            baseline_ready=1
            continue
        fi
        cmp -s "$META_BASE" "$META_BEFORE" && continue
    fi

    if ! copy_from_container Documents/xr_shot.ppm "$PPM" 15 || [ ! -s "$PPM" ]; then
        cp "$META_BEFORE" "$META_BASE"
        continue
    fi
    if ! copy_from_container Documents/xr_shot_meta.txt "$META_AFTER" 3 || [ ! -s "$META_AFTER" ]; then
        cp "$META_BEFORE" "$META_BASE"
        continue
    fi
    if ! cmp -s "$META_BEFORE" "$META_AFTER"; then
        cp "$META_AFTER" "$META_BASE"
        continue
    fi
    if ! python3 "$SCRIPT_DIR/lighting_ab_evidence.py" validate-capture \
            --metadata "$META_BEFORE" --ppm "$PPM"; then
        cp "$META_AFTER" "$META_BASE"
        continue
    fi
    fresh=1
    break
done
[ "$fresh" = 1 ] || fail "no fresh correlated capture arrived within 12 seconds"

convert_args=(convert-ppm --ppm "$PPM" --output "$OUT")
[ -z "$METADATA_OUT" ] || convert_args+=(--no-clobber)
python3 "$SCRIPT_DIR/lighting_ab_evidence.py" "${convert_args[@]}" \
    || fail "PPM -> token-bound PNG conversion failed"
python3 "$SCRIPT_DIR/lighting_ab_evidence.py" validate-capture \
    --metadata "$META_BEFORE" --ppm "$PPM" --png "$OUT" \
    || fail "converted PNG did not retain the capture token"

if [ -n "$METADATA_OUT" ]; then
    python3 "$SCRIPT_DIR/lighting_ab_evidence.py" copy-file-exclusive \
        --source "$META_BEFORE" --destination "$METADATA_OUT" \
        || fail "could not exclusively publish evidence metadata"
fi

echo "OK: $OUT"
