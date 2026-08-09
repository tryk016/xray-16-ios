#!/usr/bin/env bash
#
# Sign the current gate-validated OpenXRay app with our Apple Development
# certificate and install it on the tethered iPhone. This replaces
# SideStore/Sideloadly for the development loop — see doc/iOS-Port-Journal.md,
# slice 6.13.
#
#   ./misc/ios/install_device.sh                       # current validated Release .app
#   ./misc/ios/install_device.sh --fast                # validated FastDevice iteration build
#   ./misc/ios/install_device.sh --launch              # ...and launch it afterwards
#   ./misc/ios/install_device.sh --autoinput           # launch with cable input, no frame readback
#   ./misc/ios/install_device.sh --diagnostics         # launch with cable input + frame capture enabled
#   ./misc/ios/install_device.sh --renew               # refresh the provisioning profile only
#   ./misc/ios/install_device.sh --preflight            # validate + sign a temporary copy, no device access
#   ./misc/ios/install_device.sh --preflight \
#       --app /absolute/xr_3da.app --stamp /absolute/gate-stamp
#
# A custom app is accepted only as an absolute --app/--stamp pair and is held to
# the same source, UUID, bundle, OpenAL, platform and minOS evidence as the
# default Release/FastDevice paths. IPA files, positional payloads, discovery
# and a lone app or stamp are intentionally unsupported.
#
# Exit code 0 means exactly the requested operation completed: preflight,
# profile renewal/validation, or installation. Any other code means no install
# was reported successful.
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
export PYTHONDONTWRITEBYTECODE=1

BUNDLE_ID="io.github.tryk016.openxray.RMJWWPF379"
TEAM_ID="RMJWWPF379"
DEFAULT_DEVICE_UDID="00008130-000564403E12001C"
AUTOLOAD='start server(mobile user - beginning of the game/single/alife/load) client(localhost)'

fail() { echo ""; echo "FAIL: $*"; exit 1; }
usage() {
    echo "usage: $0 [--fast] [--launch|--autoinput|--diagnostics] [--renew] [--preflight] [--device UDID] [--app ABS_APP.app --stamp ABS_STAMP]" >&2
    exit 2
}
usage_error() { echo "usage: $*" >&2; usage; }

do_launch=0
diagnostics=0
autoinput=0
renew_only=0
use_fast=0
preflight_only=0
cli_device=""
cli_app=""
cli_stamp=""
seen_fast=0
seen_launch=0
seen_diagnostics=0
seen_autoinput=0
seen_renew=0
seen_preflight=0
seen_device=0
seen_app=0
seen_stamp=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --launch)
            [ "$seen_launch" = 0 ] || usage_error "--launch may appear once"
            seen_launch=1; do_launch=1 ;;
        --diagnostics)
            [ "$seen_diagnostics" = 0 ] || usage_error "--diagnostics may appear once"
            seen_diagnostics=1; diagnostics=1; autoinput=1; do_launch=1 ;;
        --autoinput)
            [ "$seen_autoinput" = 0 ] || usage_error "--autoinput may appear once"
            seen_autoinput=1; autoinput=1; do_launch=1 ;;
        --renew)
            [ "$seen_renew" = 0 ] || usage_error "--renew may appear once"
            seen_renew=1; renew_only=1 ;;
        --fast)
            [ "$seen_fast" = 0 ] || usage_error "--fast may appear once"
            seen_fast=1; use_fast=1 ;;
        --preflight)
            [ "$seen_preflight" = 0 ] || usage_error "--preflight may appear once"
            seen_preflight=1; preflight_only=1 ;;
        --device)
            [ "$seen_device" = 0 ] || usage_error "--device may appear once"
            [ "$#" -ge 2 ] && [ -n "$2" ] || usage_error "--device requires a UDID"
            case "$2" in --*) usage_error "--device requires a UDID" ;; esac
            seen_device=1; cli_device="$2"; shift ;;
        --app)
            [ "$seen_app" = 0 ] || usage_error "--app may appear once"
            [ "$#" -ge 2 ] && [ -n "$2" ] || usage_error "--app requires an absolute .app path"
            case "$2" in --*) usage_error "--app requires an absolute .app path" ;; esac
            seen_app=1; cli_app="$2"; shift ;;
        --stamp)
            [ "$seen_stamp" = 0 ] || usage_error "--stamp may appear once"
            [ "$#" -ge 2 ] && [ -n "$2" ] || usage_error "--stamp requires an absolute path"
            case "$2" in --*) usage_error "--stamp requires an absolute path" ;; esac
            seen_stamp=1; cli_stamp="$2"; shift ;;
        --*) usage_error "unknown option $1" ;;
        *) usage_error "positional payloads and IPA files are unsupported" ;;
    esac
    shift
done

[ "$seen_app" = "$seen_stamp" ] || usage_error "--app and --stamp are an atomic pair"
if [ "$seen_app" = 1 ]; then
    CUSTOM_APP="$cli_app"
    CUSTOM_STAMP="$cli_stamp"
else
    env_app="${OPENXRAY_INSTALL_APP:-}"
    env_stamp="${OPENXRAY_INSTALL_STAMP:-}"
    [ -z "$env_app" ] && [ -z "$env_stamp" ] || {
        [ -n "$env_app" ] && [ -n "$env_stamp" ] \
            || usage_error "OPENXRAY_INSTALL_APP and OPENXRAY_INSTALL_STAMP are an atomic pair"
    }
    CUSTOM_APP="$env_app"
    CUSTOM_STAMP="$env_stamp"
fi
reject_path_controls() {
    case "$1" in
        *$'\n'*|*$'\r'*) usage_error "$2 must not contain CR or LF" ;;
    esac
}
if [ -n "$CUSTOM_APP" ]; then
    reject_path_controls "$CUSTOM_APP" "app path"
    reject_path_controls "$CUSTOM_STAMP" "stamp path"
    case "$CUSTOM_APP" in /*) ;; *) usage_error "--app must be an absolute path" ;; esac
    case "$CUSTOM_STAMP" in /*) ;; *) usage_error "--stamp must be an absolute path" ;; esac
    case "$CUSTOM_APP" in *.app) ;; *) usage_error "--app must name a .app bundle; IPA files are unsupported" ;; esac
    case "$CUSTOM_STAMP" in *.ipa|*.IPA) usage_error "IPA files are unsupported" ;; esac
fi
[ "$use_fast" = 0 ] || [ -z "$CUSTOM_APP" ] \
    || usage_error "--fast cannot be combined with a custom app/stamp pair"
[ "$preflight_only" = 0 ] || {
    [ "$renew_only" = 0 ] && [ "$do_launch" = 0 ] && [ "$autoinput" = 0 ] && [ "$diagnostics" = 0 ] \
        || usage_error "--preflight cannot be combined with --renew, --launch, --autoinput or --diagnostics"
}
[ "$renew_only" = 0 ] || {
    [ "$do_launch" = 0 ] && [ "$use_fast" = 0 ] && [ -z "$CUSTOM_APP" ] \
        || usage_error "--renew is a standalone profile operation"
}
DEVICE_UDID="${cli_device:-${OPENXRAY_DEVICE_UDID:-$DEFAULT_DEVICE_UDID}}"
[ -n "$DEVICE_UDID" ] || usage_error "device UDID must not be empty"

# Parse and reject every unsafe combination before test mode can replace the
# repository root, Apple tools, or the shared device-lease helper.
INSTALL_TEST_MODE="${OPENXRAY_INSTALL_TEST_MODE:-0}"
case "$INSTALL_TEST_MODE" in
    0) ;;
    1)
        [ "$preflight_only" = 1 ] \
            || usage_error "OPENXRAY_INSTALL_TEST_MODE=1 requires --preflight"
        ;;
    *) usage_error "OPENXRAY_INSTALL_TEST_MODE must be exactly 0 or 1" ;;
esac

if [ "$INSTALL_TEST_MODE" = 1 ]; then
    REPO_ROOT="${OPENXRAY_INSTALL_TEST_REPO_ROOT:-}"
    INSTALL_TEST_BIN="${OPENXRAY_INSTALL_TEST_BIN:-}"
    [ -n "$REPO_ROOT" ] && [ "${REPO_ROOT#/}" != "$REPO_ROOT" ] && [ -d "$REPO_ROOT" ] \
        || usage_error "test mode requires an absolute existing test repository"
    [ -n "$INSTALL_TEST_BIN" ] && [ "${INSTALL_TEST_BIN#/}" != "$INSTALL_TEST_BIN" ] \
        && [ -d "$INSTALL_TEST_BIN" ] \
        || usage_error "test mode requires an absolute existing tool directory"
else
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

PLISTBUDDY=/usr/libexec/PlistBuddy
SECURITY=/usr/bin/security
OPENSSL=/usr/bin/openssl
CODESIGN=/usr/bin/codesign
XCRUN=/usr/bin/xcrun
XCODEBUILD=/usr/bin/xcodebuild
PLUTIL=/usr/bin/plutil
BASE64=/usr/bin/base64
COPY=/bin/cp
if [ "$INSTALL_TEST_MODE" = 1 ]; then
    PLISTBUDDY="$INSTALL_TEST_BIN/PlistBuddy"
    SECURITY="$INSTALL_TEST_BIN/security"
    OPENSSL="$INSTALL_TEST_BIN/openssl"
    CODESIGN="$INSTALL_TEST_BIN/codesign"
    XCRUN="$INSTALL_TEST_BIN/xcrun"
    XCODEBUILD="$INSTALL_TEST_BIN/xcodebuild"
    PLUTIL="$INSTALL_TEST_BIN/plutil"
    BASE64="$INSTALL_TEST_BIN/base64"
    COPY="$INSTALL_TEST_BIN/cp"
fi

cd "$REPO_ROOT" || exit 1
# shellcheck source=misc/ios/device_lease.sh
source "$REPO_ROOT/misc/ios/device_lease.sh"
PROFILE_DIR="$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"
STUB="$REPO_ROOT/misc/ios/provisioning-stub"

path_has_symlink_component() {
    local path="$1" part current=/
    case "$path" in /*) ;; *) return 0 ;; esac
    IFS=/ read -r -a parts <<< "${path#/}"
    for part in "${parts[@]}"; do
        [ -n "$part" ] || continue
        current="$current$part"
        [ ! -L "$current" ] || return 0
        current="$current/"
    done
    return 1
}
validate_custom_pair() {
    [ -n "$CUSTOM_APP" ] || return 0
    [ -d "$CUSTOM_APP" ] || fail "custom app bundle does not exist"
    [ -f "$CUSTOM_STAMP" ] || fail "custom gate stamp does not exist"
}
reject_artifact_symlinks() {
    local app="$1" stamp="$2" label="$3" linked
    [ -d "$app" ] || fail "$label app bundle does not exist"
    [ -f "$stamp" ] || fail "$label gate stamp does not exist"
    ! path_has_symlink_component "$app" || fail "$label app path contains a symlink"
    ! path_has_symlink_component "$stamp" || fail "$label stamp path contains a symlink"
    linked=$(find "$app" -type l -print -quit) \
        || fail "could not inspect $label app bundle for symlinks"
    [ -z "$linked" ] || fail "$label app bundle contains a symlink"
}
validate_custom_pair
WORK_CREATED=$(mktemp -d -t xrinstall) || fail "could not create private install workspace"
WORK=$(cd "$WORK_CREATED" && pwd -P) || fail "could not canonicalize private install workspace"
cleanup()
{
    [ "$preflight_only" = 1 ] || ios_device_lease_release
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Copy one regular file without ever resolving a symlink.  Both parent chains
# are walked descriptor-relative from /, the source identity is stable across
# the copy, and the destination must be a fresh 0600 file in private WORK.
safe_snapshot_regular_file() {
    /usr/bin/python3 - "$1" "$2" "$WORK" <<'PY'
import os
import stat
import sys


def absolute_parts(path):
    if not path.startswith("/"):
        raise OSError("path is not absolute")
    parts = path.split("/")[1:]
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise OSError("path contains an empty, dot, or dotdot component")
    if any("\r" in part or "\n" in part for part in parts):
        raise OSError("path contains CR or LF")
    return parts


def open_directory(parts):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in parts:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


source_path, destination_path, work_path = sys.argv[1:]
source_parts = absolute_parts(source_path)
destination_parts = absolute_parts(destination_path)
if os.path.dirname(destination_path) != work_path:
    raise SystemExit("safe snapshot destination is outside private WORK")
source_parent = destination_parent = source_fd = destination_fd = None
destination_identity = None
created = False
success = False
try:
    source_parent = open_directory(source_parts[:-1])
    destination_parent = open_directory(destination_parts[:-1])
    source_fd = os.open(
        source_parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent
    )
    before = os.fstat(source_fd)
    if not stat.S_ISREG(before.st_mode):
        raise OSError("source is not a regular file")
    destination_fd = os.open(
        destination_parts[-1],
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=destination_parent,
    )
    created = True
    destination_stat = os.fstat(destination_fd)
    destination_identity = (destination_stat.st_dev, destination_stat.st_ino)
    os.fchmod(destination_fd, 0o600)
    while True:
        block = os.read(source_fd, 1024 * 1024)
        if not block:
            break
        view = memoryview(block)
        while view:
            written = os.write(destination_fd, view)
            if written <= 0:
                raise OSError("short destination write")
            view = view[written:]
    os.fsync(destination_fd)
    after = os.fstat(source_fd)
    copied = os.fstat(destination_fd)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise OSError("source changed while being copied")
    if not stat.S_ISREG(copied.st_mode) or copied.st_size != before.st_size:
        raise OSError("destination snapshot is incomplete or not regular")
    success = True
except BaseException as error:
    print(f"safe snapshot failed: {error}", file=sys.stderr)
finally:
    for descriptor in (source_fd, destination_fd):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if created and not success and destination_parent is not None:
        try:
            current = os.stat(
                destination_parts[-1], dir_fd=destination_parent, follow_symlinks=False
            )
            if (current.st_dev, current.st_ino) == destination_identity:
                os.unlink(destination_parts[-1], dir_fd=destination_parent)
        except OSError:
            pass
    for descriptor in (source_parent, destination_parent):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

sys.exit(0 if success else 1)
PY
}

# --- provisioning profile ---------------------------------------------------
# Find a profile for our App ID that is still valid. Profiles are CMS-wrapped
# plists, hence the `security cms -D` decode.
find_profile() {
    local f exp now profile_app_id profile_team_id candidate decoded profile_index
    now=$(date +%s)
    profile_index=0
    shopt -s nullglob
    for f in "$PROFILE_DIR"/*.mobileprovision; do
        while :; do
            candidate="$WORK/profile-candidate-$profile_index.mobileprovision"
            decoded="$WORK/profile-candidate-$profile_index.plist"
            profile_index=$((profile_index + 1))
            [ ! -e "$candidate" ] && [ ! -L "$candidate" ] && break
        done
        safe_snapshot_regular_file "$f" "$candidate" >/dev/null 2>&1 || continue
        "$SECURITY" cms -D -i "$candidate" 2>/dev/null > "$decoded" || continue
        profile_app_id=$("$PLISTBUDDY" -c "Print :Entitlements:application-identifier" \
            "$decoded" 2>/dev/null) || continue
        profile_team_id=$("$PLISTBUDDY" -c "Print :Entitlements:com.apple.developer.team-identifier" \
            "$decoded" 2>/dev/null) || continue
        [ "$profile_app_id" = "$TEAM_ID.$BUNDLE_ID" ] || continue
        [ "$profile_team_id" = "$TEAM_ID" ] || continue
        exp=$("$PLISTBUDDY" -c "Print :ExpirationDate" "$decoded" 2>/dev/null)
        [ -n "$exp" ] || continue
        # PlistBuddy prints e.g. "Sun Jul 26 15:41:52 GMT 2026"
        if [ "$(date -j -f "%a %b %d %T %Z %Y" "$exp" +%s 2>/dev/null || echo 0)" -gt "$now" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

renew_profile() {
    echo "== renewing provisioning profile =="
    [ -d "$STUB" ] || fail "$STUB missing — cannot renew"
    # Building the throwaway stub under OUR bundle id is what makes Xcode fetch
    # the profile. The engine tree is deliberately not involved: a failure here
    # must never leave the real build tree half-configured.
    local log; log=$(mktemp -t xrprof)
    if ! "$XCODEBUILD" -project "$STUB/Probe.xcodeproj" -scheme Probe \
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

PROFILE=$(find_profile) || {
    [ "$preflight_only" = 0 ] || fail "no valid profile for $BUNDLE_ID; run --renew separately before --preflight"
    renew_profile
    PROFILE=$(find_profile)
}
[ -n "${PROFILE:-}" ] || fail "still no valid profile for $BUNDLE_ID after renewal"
echo "profile: $(basename "$PROFILE")"

# --- signing identity -------------------------------------------------------
# A keychain can hold multiple Apple Development identities. Select only a
# private key whose certificate is explicitly authorized by this provisioning
# profile; the profile's application identifier already pins the required team.
"$SECURITY" cms -D -i "$PROFILE" > "$WORK/prof.plist" \
    || fail "could not decode the selected provisioning profile"
IDENTITY=""
cert_index=0
while cert_data=$("$PLUTIL" -extract "DeveloperCertificates.$cert_index" raw -o - \
        "$WORK/prof.plist" 2>/dev/null); do
    cert_file="$WORK/profile-cert-$cert_index.cer"
    printf '%s' "$cert_data" | "$BASE64" -D > "$cert_file" \
        || fail "could not decode DeveloperCertificates.$cert_index"
    cert_fingerprint=$("$OPENSSL" x509 -inform DER -in "$cert_file" \
        -noout -fingerprint -sha1 2>/dev/null \
        | awk -F= '{gsub(":", "", $2); print toupper($2)}')
    if [ -n "$cert_fingerprint" ] \
            && "$SECURITY" find-identity -v -p codesigning 2>/dev/null \
                | awk -v fingerprint="$cert_fingerprint" \
                    '$2 == fingerprint {found=1} END {exit !found}'; then
        IDENTITY="$cert_fingerprint"
        break
    fi
    cert_index=$((cert_index + 1))
done
[ -n "$IDENTITY" ] \
    || fail "no private key matches a DeveloperCertificate authorized by the selected profile"

if [ "$renew_only" = 1 ]; then
    echo ""
    echo "PASS — profile and matching signing identity valid."
    exit 0
fi

# --- locate the payload -----------------------------------------------------
EXPECTED_BUNDLE_HASH=""
EXPECTED_OPENAL_PROVIDER=""
EXPECTED_OPENAL_SHA256=""
EXACT_FIELD_ERROR=""
read_exact_field() {
    local file="$1" key="$2" output_name="$3" parsed state value
    if ! parsed=$(awk -F= -v key="$key" '
        $1 == key {
            count++
            if (count == 1) value = substr($0, length(key) + 2)
        }
        END {
            if (count == 0) print "missing"
            else if (count > 1) print "duplicate"
            else if (length(value) == 0) print "empty"
            else printf "ok\t%s\n", value
        }
    ' "$file"); then
        EXACT_FIELD_ERROR="could not parse field $key"
        return 1
    fi
    state=${parsed%%$'\t'*}
    value=${parsed#*$'\t'}
    case "$state" in
        ok) printf -v "$output_name" '%s' "$value" ;;
        missing) EXACT_FIELD_ERROR="is missing required field $key"; return 1 ;;
        duplicate) EXACT_FIELD_ERROR="contains duplicate field $key"; return 1 ;;
        empty) EXACT_FIELD_ERROR="contains empty field $key"; return 1 ;;
        *) EXACT_FIELD_ERROR="could not parse field $key"; return 1 ;;
    esac
}
validate_gated_app() {
    local app="$1"
    local gate_stamp="$2"
    local gate_command="$3"
    local label="$4"
    local expected_source_hash expected_app_uuid expected_bundle_hash expected_openal_provider expected_openal_sha256 expected_platform expected_minos
    local current_source_hash current_app_uuid openal_fields current_openal_provider current_openal_sha256

    [ -x "$app/xr_3da" ] \
        || fail "$label app is missing — run $gate_command"
    [ -f "$gate_stamp" ] \
        || fail "$label app has no successful device-gate stamp — run $gate_command"
    read_exact_field "$gate_stamp" source_sha256 expected_source_hash \
        || fail "$label gate $EXACT_FIELD_ERROR"
    read_exact_field "$gate_stamp" app_uuid expected_app_uuid \
        || fail "$label gate $EXACT_FIELD_ERROR"
    read_exact_field "$gate_stamp" bundle_sha256 expected_bundle_hash \
        || fail "$label gate $EXACT_FIELD_ERROR"
    read_exact_field "$gate_stamp" openal_provider expected_openal_provider \
        || fail "$label gate $EXACT_FIELD_ERROR"
    read_exact_field "$gate_stamp" openal_sha256 expected_openal_sha256 \
        || fail "$label gate $EXACT_FIELD_ERROR"
    read_exact_field "$gate_stamp" platform expected_platform \
        || fail "$label gate $EXACT_FIELD_ERROR"
    read_exact_field "$gate_stamp" minos expected_minos \
        || fail "$label gate $EXACT_FIELD_ERROR"
    current_source_hash=$(python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
        --salt ios-device-artifact-v2 --build-context-root "$REPO_ROOT" \
        --ios-artifact-root "$REPO_ROOT") \
        || fail "could not hash current device artifact inputs"
    current_app_uuid=$("$XCRUN" dwarfdump --uuid "$app/xr_3da" \
        | awk '{print $2}' | sort) \
        || fail "could not read $label app UUID"
    [ -n "$expected_source_hash" ] \
        && [ "$current_source_hash" = "$expected_source_hash" ] \
        || fail "source content differs from the last $label gate — run $gate_command"
    [ -n "$expected_app_uuid" ] \
        && [ "$current_app_uuid" = "$expected_app_uuid" ] \
        || fail "$label app UUID differs from the last device gate — run $gate_command"
    [[ "$expected_bundle_hash" =~ ^[0-9a-f]{64}$ ]] \
        || fail "$label gate has no valid app-bundle hash — run $gate_command"
    [ "$expected_platform" = IOS ] \
        || fail "$label gate platform must be IOS"
    [ "$expected_minos" = 16.4 ] \
        || fail "$label gate minos must be 16.4"
    openal_fields=$(python3 "$REPO_ROOT/misc/ios/openal_provider_contract.py" artifact \
        --prefix "$REPO_ROOT/build/ios-prefix-iphoneos" --binary "$app/xr_3da" --platform iphoneos) \
        || fail "$label OpenAL provider artifact contract failed — run $gate_command"
    current_openal_provider=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_provider" {print $2}')
    current_openal_sha256=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_sha256" {print $2}')
    [ "$expected_openal_provider" = "OpenALSoft-1.25.2-static" ] \
        && [ "$current_openal_provider" = "$expected_openal_provider" ] \
        || fail "$label gate has no matching OpenAL provider proof — run $gate_command"
    [[ "$expected_openal_sha256" =~ ^[0-9a-f]{64}$ ]] \
        && [ "$current_openal_sha256" = "$expected_openal_sha256" ] \
        || fail "$label gate has no matching OpenAL archive hash — run $gate_command"
    EXPECTED_BUNDLE_HASH="$expected_bundle_hash"
    EXPECTED_OPENAL_PROVIDER="$expected_openal_provider"
    EXPECTED_OPENAL_SHA256="$expected_openal_sha256"
}

# The development loop installs only an app paired with the exact gate stamp
# which validated it.  An archived IPA is never accepted.
if [ -n "$CUSTOM_APP" ]; then
    PAYLOAD="$CUSTOM_APP"
    GATE_STAMP="$CUSTOM_STAMP"
    GATE_COMMAND="the matching gate which produced this stamp"
    LABEL="Custom"
elif [ "$use_fast" = 1 ]; then
    PAYLOAD="$REPO_ROOT/bin/aarch64/FastDevice/xr_3da.app"
    GATE_STAMP="$REPO_ROOT/build/ios-engine-fastdevice-iphoneos/.ios_fast_device_gate_ok"
    GATE_COMMAND="./misc/ios/build_fast_device.sh"
    LABEL="FastDevice"
elif [ -d "$REPO_ROOT/bin/aarch64/Release/xr_3da.app" ]; then
    PAYLOAD="$REPO_ROOT/bin/aarch64/Release/xr_3da.app"
    GATE_STAMP="$REPO_ROOT/build/ios-engine-iphoneos/.ios_device_gate_ok"
    GATE_COMMAND="./misc/ios/build_check.sh"
    LABEL="Release"
else
    fail "no current Release app — run ./misc/ios/build_check.sh before installing"
fi
reject_artifact_symlinks "$PAYLOAD" "$GATE_STAMP" "$LABEL source"
# Take immutable private inputs before evaluating any gate field.  The original
# app and stamp stay untouched even if signing or a later device step fails.
safe_snapshot_regular_file "$GATE_STAMP" "$WORK/gate-stamp" \
    || fail "could not snapshot gate stamp"
APP="$WORK/$(basename "$PAYLOAD")"
"$COPY" -RP "$PAYLOAD" "$APP" \
    || fail "could not copy app bundle"
reject_artifact_symlinks "$APP" "$WORK/gate-stamp" "$LABEL private snapshot"
validate_gated_app "$APP" "$WORK/gate-stamp" "$GATE_COMMAND" "$LABEL"

if [ -n "$EXPECTED_BUNDLE_HASH" ]; then
    copied_bundle_hash=$(python3 "$REPO_ROOT/misc/ios/gate_hash.py" \
        --salt ios-app-bundle-v1 --relative-root "$APP" "$APP") \
        || fail "could not hash the copied app bundle"
    [ "$copied_bundle_hash" = "$EXPECTED_BUNDLE_HASH" ] \
        || fail "app bundle differs from the last device gate — rebuild before installing"
fi

openal_fields=$(python3 "$REPO_ROOT/misc/ios/openal_provider_contract.py" artifact \
    --prefix "$REPO_ROOT/build/ios-prefix-iphoneos" --binary "$APP/xr_3da" --platform iphoneos) \
    || fail "payload fails the OpenAL provider artifact contract"
payload_openal_provider=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_provider" {print $2}')
payload_openal_sha256=$(printf '%s\n' "$openal_fields" | awk -F= '$1 == "openal_sha256" {print $2}')
[ "$payload_openal_provider" = "OpenALSoft-1.25.2-static" ] \
    && [[ "$payload_openal_sha256" =~ ^[0-9a-f]{64}$ ]] \
    || fail "payload has incomplete OpenAL provider proof"
if [ -n "$EXPECTED_OPENAL_PROVIDER" ]; then
    [ "$payload_openal_provider" = "$EXPECTED_OPENAL_PROVIDER" ] \
        && [ "$payload_openal_sha256" = "$EXPECTED_OPENAL_SHA256" ] \
        || fail "payload OpenAL provider/archive differs from its validated gate"
fi

build_info=$("$XCRUN" vtool -show-build "$APP/xr_3da" 2>/dev/null)
echo "$build_info" | grep -Eq '^[[:space:]]*platform IOS$' \
    || fail "payload is not an iPhoneOS device build (IOSSIMULATOR cannot be installed)"
echo "$build_info" | grep -Eq '^[[:space:]]*minos 16\.4$' \
    || fail "payload does not use the required iOS 16.4 deployment target"

base_bundle_id=$("$PLISTBUDDY" -c 'Print :CFBundleIdentifier' "$APP/Info.plist" 2>/dev/null) \
    || fail "payload has no CFBundleIdentifier"
[ "$base_bundle_id" = "io.github.tryk016.openxray" ] \
    || fail "payload base CFBundleIdentifier is not the project identifier"
payload_version=$("$PLISTBUDDY" -c 'Print :CFBundleVersion' "$APP/Info.plist" 2>/dev/null || true)
echo "payload: $(basename "$PAYLOAD") ($payload_version)"

# --- sign -------------------------------------------------------------------
# The build bundle ships with the bare bundle id (SideStore used to append the
# suffix at install time). We must apply the suffix ourselves — see rule 1 at
# the top.
"$PLISTBUDDY" -c "Set :CFBundleIdentifier $BUNDLE_ID" "$APP/Info.plist" \
    || fail "could not set the bundle id"

"$COPY" -P "$PROFILE" "$APP/embedded.mobileprovision" \
    || fail "could not embed provisioning profile"

# Entitlements must come from the profile itself; inventing them causes a
# launch-time "invalid entitlements" kill that looks like a crash.
"$PLISTBUDDY" -x -c "Print :Entitlements" "$WORK/prof.plist" > "$WORK/ent.plist" \
    || fail "profile has no Entitlements"

echo "== signing =="
"$CODESIGN" -f -s "$IDENTITY" --entitlements "$WORK/ent.plist" --generate-entitlement-der "$APP" \
    || fail "codesign failed"
"$CODESIGN" -vv --deep --strict "$APP" || fail "signature did not verify"
final_bundle_id=$("$PLISTBUDDY" -c 'Print :CFBundleIdentifier' "$APP/Info.plist" 2>/dev/null) \
    || fail "could not read final CFBundleIdentifier"
[ "$final_bundle_id" = "$BUNDLE_ID" ] \
    || fail "signed payload CFBundleIdentifier changed unexpectedly"
"$CODESIGN" -dvv "$APP" > "$WORK/codesign-details.txt" 2>&1 \
    || fail "could not inspect signed payload identity"
signed_team_identifier=""
read_exact_field "$WORK/codesign-details.txt" TeamIdentifier signed_team_identifier \
    || fail "signed payload $EXACT_FIELD_ERROR"
[ "$signed_team_identifier" = "$TEAM_ID" ] \
    || fail "signed payload TeamIdentifier differs from the project team"
"$CODESIGN" -d --entitlements :- "$APP" > "$WORK/signed-entitlements.plist" 2>/dev/null \
    || fail "could not inspect signed payload entitlements"
signed_application_identifier=$("$PLISTBUDDY" -c 'Print :application-identifier' \
    "$WORK/signed-entitlements.plist" 2>/dev/null) \
    || fail "signed payload has no application-identifier entitlement"
signed_entitlement_team=$("$PLISTBUDDY" -c 'Print :com.apple.developer.team-identifier' \
    "$WORK/signed-entitlements.plist" 2>/dev/null) \
    || fail "signed payload has no team-identifier entitlement"
[ "$signed_application_identifier" = "$TEAM_ID.$BUNDLE_ID" ] \
    || fail "signed payload application-identifier entitlement differs from the provisioning profile"
[ "$signed_entitlement_team" = "$TEAM_ID" ] \
    || fail "signed payload team-identifier entitlement differs from the provisioning profile"

if [ "$preflight_only" = 1 ]; then
    echo ""
    echo "preflight target: $DEVICE_UDID"
    echo "PASS — hermetic signing preflight completed; no device lease or device command was issued."
    exit 0
fi

# --- install ----------------------------------------------------------------
ios_device_lease_acquire 5 || exit $?

echo "== installing on device =="
ios_run_with_timeout 90 "$XCRUN" devicectl device install app --device "$DEVICE_UDID" "$APP" 2>&1 \
    | grep -vE "provisioning paramter list|devicectl manage create" \
    || fail "install failed"

if [ "$do_launch" = 1 ]; then
    # The engine rewrites user.ltx whenever it saves settings, and DROPS the autoload line
    # when it does - twice in one session so far. Without it the app boots to the main menu
    # and every automated capture silently photographs a menu instead of the game, which
    # looks like "the probe found nothing" rather than like a broken harness. So re-assert it
    # (and the no-keypress gate) on every launch. Also write the diagnostics mode explicitly:
    # ordinary --launch must not inherit expensive readback or file polling from an earlier run.
    # Cheap, idempotent, and it removes the one
    # failure mode that produces confidently wrong results.
    echo "== ensuring unattended boot (autoload + no keypress gate) =="
    cfg="$WORK/user.ltx"
    if ios_run_with_timeout 30 "$XCRUN" devicectl device copy from --device "$DEVICE_UDID" \
            --domain-type appDataContainer --domain-identifier "$BUNDLE_ID" --user mobile \
            --source Documents/_appdata_/user.ltx --destination "$cfg" >/dev/null 2>&1; then
        if grep -q "^keypress_on_start" "$cfg"; then
            sed -i '' 's/^keypress_on_start.*/keypress_on_start 0/' "$cfg"
        else
            printf 'keypress_on_start 0\n' >> "$cfg"
        fi
        if grep -q "^ios_diagnostics" "$cfg"; then
            sed -i '' "s/^ios_diagnostics.*/ios_diagnostics $diagnostics/" "$cfg"
        else
            printf 'ios_diagnostics %s\n' "$diagnostics" >> "$cfg"
        fi
        if grep -q "^ios_autoinput" "$cfg"; then
            sed -i '' "s/^ios_autoinput.*/ios_autoinput $autoinput/" "$cfg"
        else
            printf 'ios_autoinput %s\n' "$autoinput" >> "$cfg"
        fi
        grep -q "^start server(" "$cfg" || printf '%s\n' "$AUTOLOAD" >> "$cfg"
        ios_run_with_timeout 30 "$XCRUN" devicectl device copy to --device "$DEVICE_UDID" \
            --domain-type appDataContainer --domain-identifier "$BUNDLE_ID" --user mobile \
            --source "$cfg" --destination Documents/_appdata_/user.ltx >/dev/null 2>&1 \
            || fail "could not write user.ltx; refusing to launch with an unknown diagnostics mode"
    else
        fail "could not read user.ltx; refusing to launch with an unknown diagnostics mode"
    fi

    # There is no portable devicectl remove operation for one app-container file.
    # Replace any request left by an interrupted host command with an invalid tombstone;
    # the engine discards it at the next lifecycle activation before polling is enabled.
    stale_trigger="$WORK/autoinput.cancelled"
    printf 'cancelled\n' > "$stale_trigger"
    ios_run_with_timeout 30 "$XCRUN" devicectl device copy to --device "$DEVICE_UDID" \
        --domain-type appDataContainer --domain-identifier "$BUNDLE_ID" --user mobile \
        --source "$stale_trigger" --destination Documents/_appdata_/autoinput.txt >/dev/null 2>&1 \
        || fail "could not tombstone stale autoinput request before launch"

    echo "== launching (ios_diagnostics=$diagnostics, ios_autoinput=$autoinput) =="
    ios_run_with_timeout 30 "$XCRUN" devicectl device process launch --device "$DEVICE_UDID" "$BUNDLE_ID" 2>&1 \
        | grep -vE "provisioning paramter list|devicectl manage create" \
        || fail "launch failed"
fi

echo ""
echo "PASS — installed as $BUNDLE_ID."
