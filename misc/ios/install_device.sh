#!/usr/bin/env bash
#
# Sign an OpenXRay .ipa/.app with our own Apple Development certificate and
# install it on the tethered iPhone. This replaces SideStore/Sideloadly for the
# development loop — see doc/iOS-Port-Journal.md, slice 6.13.
#
#   ./misc/ios/install_device.sh                       # newest local .ipa, sign + install
#   ./misc/ios/install_device.sh --launch              # ...and launch it afterwards
#   ./misc/ios/install_device.sh path/to/Some.ipa      # explicit .ipa (or .app)
#   ./misc/ios/install_device.sh --renew               # refresh the 7-day profile only
#
# Exit code 0 = installed. Anything else = nothing was installed.
#
# WHY THIS EXISTS, AND THE TWO RULES THAT MAKE IT WORK
#
# 1. The bundle id MUST keep the team-id suffix (io.github.tryk016.openxray
#    .RMJWWPF379). Two independent reasons:
#      - iOS keys the app's data container by bundle id, and THAT CONTAINER HOLDS
#        ~4.6 GB of retail CoP assets plus the save games. Change the id and you
#        get a fresh, empty container and have to push all of it again.
#      - The App ID already exists (SideStore created it). Creating App IDs is
#        rate-limited; issuing a profile for an existing one is not. Signing under
#        the bare id mints a new App ID — that is what blocked slice 6.12.
#
# 2. NEVER revoke certificates to "fix" signing. The portal also holds SideStore's
#    certificate, whose private key is not on this Mac (that mismatch is exactly
#    why Sideloadly dies with "there is no iOS certificate with serial number").
#    Revoking it would kill the working SideStore installs of both OpenXRay and
#    OpenGothic. We sign with our own cert instead and leave SideStore's alone.
#
# Profiles last a YEAR. The Apple Developer Program membership approved
# 2026-07-19 upgraded this same team (RMJWWPF379) from free to paid in place — the
# Team ID did not change, so the bundle id, the App ID and the container all
# carried over untouched. Before that, profiles were 7-day. If the app ever
# refuses to launch because the profile lapsed, re-run with --renew (or just
# re-run the script, which renews automatically).

set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"
TEAM_ID="RMJWWPF379"
DEVICE_UDID="00008130-000564403E12001C"
IPA_DIR="$HOME/openxray-handoff/local-builds"
AUTOLOAD='start server(mobile user - beginning of the game/single/alife/load) client(localhost)'
PROFILE_DIR="$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"
STUB="$REPO_ROOT/misc/ios/provisioning-stub"

fail() { echo ""; echo "FAIL: $*"; exit 1; }

do_launch=0
renew_only=0
input=""
for arg in "$@"; do
    case "$arg" in
        --launch) do_launch=1 ;;
        --renew)  renew_only=1 ;;
        -*) echo "usage: $0 [--launch] [--renew] [path/to/app.ipa]" >&2; exit 2 ;;
        *)  input="$arg" ;;
    esac
done

# --- signing identity -------------------------------------------------------
# Only identities with a matching private key can sign, and -v lists exactly
# those. If this comes up empty the cert expired or the keychain is locked;
# minting a new one needs `xcodebuild -allowProvisioningUpdates` (see --renew).
IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
    | awk -v team="$TEAM_ID" '/Apple Development/ {print $2; exit}')
[ -n "$IDENTITY" ] || fail "no Apple Development identity with a private key in the keychain"

# --- provisioning profile ---------------------------------------------------
# Find a profile for our App ID that is still valid. Profiles are CMS-wrapped
# plists, hence the `security cms -D` decode.
find_profile() {
    local f exp now
    now=$(date +%s)
    shopt -s nullglob
    for f in "$PROFILE_DIR"/*.mobileprovision; do
        security cms -D -i "$f" 2>/dev/null > /tmp/.xrprof.$$ || continue
        /usr/libexec/PlistBuddy -c "Print :Entitlements:application-identifier" /tmp/.xrprof.$$ 2>/dev/null \
            | grep -q "^$TEAM_ID\.$BUNDLE_ID$" || continue
        exp=$(/usr/libexec/PlistBuddy -c "Print :ExpirationDate" /tmp/.xrprof.$$ 2>/dev/null)
        [ -n "$exp" ] || continue
        # PlistBuddy prints e.g. "Sun Jul 26 15:41:52 GMT 2026"
        if [ "$(date -j -f "%a %b %d %T %Z %Y" "$exp" +%s 2>/dev/null || echo 0)" -gt "$now" ]; then
            rm -f /tmp/.xrprof.$$
            echo "$f"
            return 0
        fi
    done
    rm -f /tmp/.xrprof.$$
    return 1
}

renew_profile() {
    echo "== renewing provisioning profile =="
    [ -d "$STUB" ] || fail "$STUB missing — cannot renew"
    # Building the throwaway stub under OUR bundle id is what makes Xcode fetch
    # the profile. The engine tree is deliberately not involved: a failure here
    # must never leave the real build tree half-configured.
    local log; log=$(mktemp -t xrprof)
    if ! xcodebuild -project "$STUB/Probe.xcodeproj" -scheme Probe \
            -destination 'generic/platform=iOS' \
            -allowProvisioningUpdates \
            PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE_ID" \
            DEVELOPMENT_TEAM="$TEAM_ID" \
            build > "$log" 2>&1; then
        echo "--- errors ---"
        grep -E "error:|maximum App ID|limit reached" "$log" | head -20
        echo "--- full log: $log ---"
        fail "could not renew the profile (if this mentions the App ID limit, the bundle id lost its .$TEAM_ID suffix)"
    fi
    rm -f "$log"
}

PROFILE=$(find_profile) || { renew_profile; PROFILE=$(find_profile); }
[ -n "${PROFILE:-}" ] || fail "still no valid profile for $BUNDLE_ID after renewal"
echo "profile: $(basename "$PROFILE")"

if [ "$renew_only" = 1 ]; then
    echo ""
    echo "PASS — profile valid."
    exit 0
fi

# --- locate the payload -----------------------------------------------------
if [ -z "$input" ]; then
    input=$(ls -t "$IPA_DIR"/*.ipa 2>/dev/null | head -1)
    [ -n "$input" ] || fail "no .ipa in $IPA_DIR — build one first, or pass a path"
fi
[ -e "$input" ] || fail "$input not found"

WORK=$(mktemp -d -t xrinstall)
trap 'rm -rf "$WORK"' EXIT

if [ "${input##*.}" = "ipa" ]; then
    unzip -q "$input" -d "$WORK" || fail "could not unpack $input"
    APP=$(ls -d "$WORK"/Payload/*.app 2>/dev/null | head -1)
    [ -n "$APP" ] || fail "no Payload/*.app inside $input"
else
    APP="$WORK/$(basename "$input")"
    cp -R "$input" "$APP"
fi
echo "payload: $(basename "$input") ($(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$APP/Info.plist" 2>/dev/null))"

# --- sign -------------------------------------------------------------------
# The .ipa ships with the BARE bundle id (SideStore used to append the suffix at
# install time). We must apply the suffix ourselves — see rule 1 at the top.
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" "$APP/Info.plist" \
    || fail "could not set the bundle id"

cp "$PROFILE" "$APP/embedded.mobileprovision"

# Entitlements must come from the profile itself; inventing them causes a
# launch-time "invalid entitlements" kill that looks like a crash.
security cms -D -i "$PROFILE" > "$WORK/prof.plist" || fail "could not decode the profile"
/usr/libexec/PlistBuddy -x -c "Print :Entitlements" "$WORK/prof.plist" > "$WORK/ent.plist" \
    || fail "profile has no Entitlements"

echo "== signing =="
codesign -f -s "$IDENTITY" --entitlements "$WORK/ent.plist" --generate-entitlement-der "$APP" \
    || fail "codesign failed"
codesign -vv --deep --strict "$APP" || fail "signature did not verify"

# --- install ----------------------------------------------------------------
echo "== installing on device =="
xcrun devicectl device install app --device "$DEVICE_UDID" "$APP" 2>&1 \
    | grep -vE "provisioning paramter list|devicectl manage create" \
    || fail "install failed"

if [ "$do_launch" = 1 ]; then
    # The engine rewrites user.ltx whenever it saves settings, and DROPS the autoload line
    # when it does - twice in one session so far. Without it the app boots to the main menu
    # and every automated capture silently photographs a menu instead of the game, which
    # looks like "the probe found nothing" rather than like a broken harness. So re-assert it
    # (and the no-keypress gate) on every launch. Cheap, idempotent, and it removes the one
    # failure mode that produces confidently wrong results.
    echo "== ensuring unattended boot (autoload + no keypress gate) =="
    cfg=$(mktemp -t xrcfg)
    if xcrun devicectl device copy from --device "$DEVICE_UDID" \
            --domain-type appDataContainer --domain-identifier "$BUNDLE_ID" --user mobile \
            --source Documents/_appdata_/user.ltx --destination "$cfg" >/dev/null 2>&1; then
        if grep -q "^keypress_on_start" "$cfg"; then
            sed -i '' 's/^keypress_on_start.*/keypress_on_start 0/' "$cfg"
        else
            printf 'keypress_on_start 0\n' >> "$cfg"
        fi
        grep -q "^start server(" "$cfg" || printf '%s\n' "$AUTOLOAD" >> "$cfg"
        xcrun devicectl device copy to --device "$DEVICE_UDID" \
            --domain-type appDataContainer --domain-identifier "$BUNDLE_ID" --user mobile \
            --source "$cfg" --destination Documents/_appdata_/user.ltx >/dev/null 2>&1 \
            || echo "warning: could not write user.ltx back - the app may boot to the menu"
    else
        echo "warning: could not read user.ltx - the app may boot to the menu"
    fi
    rm -f "$cfg"

    echo "== launching =="
    xcrun devicectl device process launch --device "$DEVICE_UDID" "$BUNDLE_ID" 2>&1 \
        | grep -vE "provisioning paramter list|devicectl manage create" \
        || fail "launch failed"
fi

echo ""
echo "PASS — installed as $BUNDLE_ID."
