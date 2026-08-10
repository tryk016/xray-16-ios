#!/usr/bin/env bash
# Build and run retail Call of Pripyat only in an external iOS 26.5 Simulator.
# It intentionally never uses a physical device, its lease, or device install tools.

set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GUARD="$REPO_ROOT/misc/ios/retail_simulator_guard.py"
readonly BUNDLE_ID="io.github.tryk016.openxray"
readonly DEVICE_TYPE="com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro-Max"
readonly DEFAULT_WORK_BASE="/Users/patryk/openxray-handoff"
readonly SOURCE_EXCLUDES=(.git .Codex 'build*' bin)

backup=""
manifest=""
work_base="$DEFAULT_WORK_BASE"
with_saves=0
autoload_save=""
ui_navigation=0
ui_captures=0
capture_v2=0
quickload_evidence=0
runtime_label="26.5"
launch_timeout=120
poll_interval="${RETAIL_SIMULATOR_POLL_INTERVAL:-1}"

usage() {
    cat >&2 <<'EOF'
usage: retail_simulator.sh --backup PATH [--manifest PATH] [--work-base PATH]
                           [--with-saves] [--autoload-save NAME] [--ui-navigation [--ui-captures]|--capture-v2|--quickload-evidence]
                           [--runtime 26.5|27.0] [--launch-timeout SECONDS]

Creates a new, external Simulator work root. It never reuses a work root and
never writes to the repository, retail backup, or a physical device.
EOF
}

fail() { echo "FAIL: $*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --backup) [ "$#" -ge 2 ] || fail "--backup requires a path"; backup="$2"; shift 2 ;;
        --manifest) [ "$#" -ge 2 ] || fail "--manifest requires a path"; manifest="$2"; shift 2 ;;
        --work-base) [ "$#" -ge 2 ] || fail "--work-base requires a path"; work_base="$2"; shift 2 ;;
        --with-saves) with_saves=1; shift ;;
        --autoload-save) [ "$#" -ge 2 ] || fail "--autoload-save requires a save name"; autoload_save="$2"; shift 2 ;;
        --ui-navigation) ui_navigation=1; shift ;;
        --ui-captures) ui_captures=1; shift ;;
        --capture-v2) capture_v2=1; shift ;;
        --quickload-evidence) quickload_evidence=1; shift ;;
        --runtime) [ "$#" -ge 2 ] || fail "--runtime requires 26.5 or 27.0"; runtime_label="$2"; shift 2 ;;
        --launch-timeout) [ "$#" -ge 2 ] || fail "--launch-timeout requires seconds"; launch_timeout="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; fail "unknown argument: $1" ;;
    esac
done

# This closed mapping deliberately precedes all path resolution, work-root
# creation and simctl use.  Version aliases are not accepted: the report and
# dedicated Simulator name must identify exactly the requested CoreSimulator
# runtime.
case "$runtime_label" in
    26.5) runtime_id="com.apple.CoreSimulator.SimRuntime.iOS-26-5" ;;
    27.0) runtime_id="com.apple.CoreSimulator.SimRuntime.iOS-27-0" ;;
    *) fail "--runtime must be exactly 26.5 or 27.0" ;;
esac

[ -n "$backup" ] || { usage; fail "--backup is required"; }
[ -z "$autoload_save" ] || [ "$with_saves" = 1 ] \
    || fail "--autoload-save requires --with-saves"
[ "$ui_navigation" = 0 ] || { [ "$with_saves" = 1 ] && [ -n "$autoload_save" ]; } \
    || fail "--ui-navigation requires both --with-saves and --autoload-save"
[ "$capture_v2" = 0 ] || { [ "$with_saves" = 1 ] && [ -n "$autoload_save" ]; } \
    || fail "--capture-v2 requires both --with-saves and --autoload-save"
[ "$ui_navigation" = 0 ] || [ "$capture_v2" = 0 ] \
    || fail "--capture-v2 conflicts with --ui-navigation"
[ "$ui_captures" = 0 ] || [ "$ui_navigation" = 1 ] \
    || fail "--ui-captures requires --ui-navigation"
[ "$ui_captures" = 0 ] || [ "$capture_v2" = 0 ] \
    || fail "--ui-captures rejects --capture-v2"
[ "$ui_captures" = 0 ] || [ "$runtime_label" = "27.0" ] \
    || fail "--ui-captures requires --runtime 27.0"
[ "$capture_v2" = 0 ] || [ "$runtime_label" = "27.0" ] \
    || fail "--capture-v2 requires --runtime 27.0"
[ "$quickload_evidence" = 0 ] || { [ "$runtime_label" = "27.0" ] && [ "$with_saves" = 1 ] && [ -n "$autoload_save" ]; } \
    || fail "--quickload-evidence requires --runtime 27.0, --with-saves and --autoload-save"
[ "$quickload_evidence" = 0 ] || [ "$ui_navigation" = 0 ] \
    || fail "--quickload-evidence conflicts with --ui-navigation"
[ "$quickload_evidence" = 0 ] || [ "$ui_captures" = 0 ] \
    || fail "--quickload-evidence conflicts with --ui-captures"
[ "$quickload_evidence" = 0 ] || [ "$capture_v2" = 0 ] \
    || fail "--quickload-evidence conflicts with explicit --capture-v2"
if [ -z "$manifest" ]; then
    manifest="${backup%/}.manifest"
fi
python3 - "$launch_timeout" "$poll_interval" <<'PY' \
    || fail "launch timeout must be positive and poll interval must be between 0.01 and 5 seconds"
import sys
timeout, poll = map(float, sys.argv[1:])
raise SystemExit(0 if timeout > 0 and 0.01 <= poll <= 5.0 else 1)
PY
build_jobs="${IOS_BUILD_JOBS:-8}"
case "$build_jobs" in ''|*[!0-9]*|0) fail "IOS_BUILD_JOBS must be a positive integer" ;; esac

backup="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$backup")" \
    || fail "cannot resolve backup"
manifest="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$manifest")" \
    || fail "cannot resolve manifest"
work_base="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$work_base")" \
    || fail "cannot resolve work base"
[ -d "$backup" ] || fail "backup is not a directory: $backup"
[ -d "$manifest" ] || fail "manifest is not a directory: $manifest"
[ -d "$work_base" ] || fail "work base is not a directory: $work_base"
python3 "$GUARD" paths --repo "$REPO_ROOT" --backup "$backup" \
    --manifest "$manifest" --work-base "$work_base" || exit 1
python3 "$GUARD" retail-verify --backup "$backup" --manifest "$manifest" || exit 1

timestamp="$(date -u +%Y%m%d-%H%M%S)"
work_root="$work_base/simulator-work-$timestamp-$$"
[ ! -e "$work_root" ] || fail "refusing to reuse work root: $work_root"
mkdir "$work_root" || fail "could not create work root"
work_root="$(cd "$work_root" && pwd -P)"
python3 "$GUARD" work-root --repo "$REPO_ROOT" --backup "$backup" \
    --manifest "$manifest" --work-root "$work_root" || exit 1

report="$work_root/report.txt"
report_pending="$work_root/.report.pending"
source_snapshot="$work_root/source"
prefix_snapshot="$work_root/ios-prefix-iphonesimulator"
build_root="$work_root/build"
guard_root="$work_root/guards"
mkdir -p "$guard_root" || fail "could not create guard directory"

device_uuid=""
simulator_created=0
cleanup() {
    local status="$?"
    trap - EXIT HUP INT TERM
    if [ "$simulator_created" = 1 ] && [ -n "$device_uuid" ]; then
        xcrun simctl terminate "$device_uuid" "$BUNDLE_ID" >/dev/null 2>&1 || true
        xcrun simctl shutdown "$device_uuid" >/dev/null 2>&1 || true
        if ! xcrun simctl delete "$device_uuid" >/dev/null 2>&1; then
            cleanup_message="cleanup-error: could not delete dedicated Simulator $device_uuid"
            echo "$cleanup_message" >&2
            printf '%s\n' "$cleanup_message" > "$work_root/cleanup-error.txt" \
                || echo "cleanup-error: could not write $work_root/cleanup-error.txt" >&2
            [ "$status" -ne 0 ] || status=1
        fi
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

manifest_tree() {
    local root="$1" output="$2"
    shift 2
    local command=(python3 "$GUARD" tree-manifest --root "$root" --output "$output")
    local exclusion
    for exclusion in "$@"; do command+=(--exclude-top "$exclusion"); done
    "${command[@]}" || fail "could not manifest $root"
}
compare_tree() {
    local root="$1" expected="$2"
    shift 2
    local command=(python3 "$GUARD" tree-compare --root "$root" --manifest "$expected")
    local exclusion
    for exclusion in "$@"; do command+=(--exclude-top "$exclusion"); done
    "${command[@]}" || fail "protected input changed: $root"
}
manifest_file() {
    python3 "$GUARD" file-manifest --file "$1" --output "$2" \
        || fail "could not manifest protected file: $1"
}
compare_file() {
    python3 "$GUARD" file-compare --file "$1" --manifest "$2" \
        || fail "protected file changed: $1"
}
guard_protected() {
    compare_tree "$REPO_ROOT" "$guard_root/repo.tsv" "${SOURCE_EXCLUDES[@]}"
    compare_tree "$backup" "$guard_root/backup.tsv"
    compare_tree "$REPO_ROOT/bin/aarch64/Release" "$guard_root/device-release.tsv"
    compare_tree "$REPO_ROOT/bin/aarch64/FastDevice" "$guard_root/device-fast.tsv"
    compare_tree "$REPO_ROOT/build/ios-engine-iphoneos" "$guard_root/device-build.tsv"
    compare_tree "$REPO_ROOT/build/ios-engine-fastdevice-iphoneos" "$guard_root/fastdevice-build.tsv"
    compare_tree "$REPO_ROOT/build/ios-engine-iphonesimulator" "$guard_root/legacy-simulator-build.tsv"
    compare_tree "$REPO_ROOT/build/ios-prefix-iphonesimulator" "$guard_root/prefix-source.tsv"
    compare_file "$REPO_ROOT/build/ios-engine-iphoneos/.ios_device_gate_ok" "$guard_root/release-device-gate.stamp.tsv"
    compare_file "$REPO_ROOT/build/ios-engine-iphoneos/.ios_full_gate_ok" "$guard_root/release-full-gate.stamp.tsv"
    compare_file "$REPO_ROOT/build/ios-engine-iphoneos/.ios_cmake_inputs.sha256" "$guard_root/release-cmake-inputs.stamp.tsv"
    compare_file "$REPO_ROOT/build/ios-engine-fastdevice-iphoneos/.ios_fast_device_gate_ok" "$guard_root/fastdevice-gate.stamp.tsv"
    compare_file "$REPO_ROOT/build/ios-engine-fastdevice-iphoneos/.ios_cmake_inputs.sha256" "$guard_root/fastdevice-cmake-inputs.stamp.tsv"
}
guard_snapshots() {
    compare_tree "$source_snapshot" "$guard_root/repo.tsv" "${SOURCE_EXCLUDES[@]}"
    compare_tree "$prefix_snapshot" "$guard_root/prefix-snapshot.tsv"
}

manifest_tree "$REPO_ROOT" "$guard_root/repo.tsv" "${SOURCE_EXCLUDES[@]}"
manifest_tree "$backup" "$guard_root/backup.tsv"
manifest_tree "$REPO_ROOT/bin/aarch64/Release" "$guard_root/device-release.tsv"
manifest_tree "$REPO_ROOT/bin/aarch64/FastDevice" "$guard_root/device-fast.tsv"
manifest_tree "$REPO_ROOT/build/ios-engine-iphoneos" "$guard_root/device-build.tsv"
manifest_tree "$REPO_ROOT/build/ios-engine-fastdevice-iphoneos" "$guard_root/fastdevice-build.tsv"
manifest_tree "$REPO_ROOT/build/ios-engine-iphonesimulator" "$guard_root/legacy-simulator-build.tsv"
manifest_tree "$REPO_ROOT/build/ios-prefix-iphonesimulator" "$guard_root/prefix-source.tsv"
manifest_file "$REPO_ROOT/build/ios-engine-iphoneos/.ios_device_gate_ok" "$guard_root/release-device-gate.stamp.tsv"
manifest_file "$REPO_ROOT/build/ios-engine-iphoneos/.ios_full_gate_ok" "$guard_root/release-full-gate.stamp.tsv"
manifest_file "$REPO_ROOT/build/ios-engine-iphoneos/.ios_cmake_inputs.sha256" "$guard_root/release-cmake-inputs.stamp.tsv"
manifest_file "$REPO_ROOT/build/ios-engine-fastdevice-iphoneos/.ios_fast_device_gate_ok" "$guard_root/fastdevice-gate.stamp.tsv"
manifest_file "$REPO_ROOT/build/ios-engine-fastdevice-iphoneos/.ios_cmake_inputs.sha256" "$guard_root/fastdevice-cmake-inputs.stamp.tsv"
git_revision=""
source_tree_sha256=""
if (( ui_captures )); then
    git_revision="$(git -C "$REPO_ROOT" rev-parse --verify HEAD)" \
        || fail "could not identify source-snapshot git revision"
    [[ "$git_revision" =~ ^[0-9a-f]{40}$ ]] \
        || fail "source-snapshot git revision is not canonical"
    source_tree_sha256="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$guard_root/repo.tsv")" \
        || fail "could not hash source-snapshot tree manifest"
    [[ "$source_tree_sha256" =~ ^[0-9a-f]{64}$ ]] \
        || fail "source-snapshot tree digest is not canonical"
fi

rsync_args=(-a)
for exclusion in "${SOURCE_EXCLUDES[@]}"; do rsync_args+=("--exclude=/$exclusion"); done
rsync "${rsync_args[@]}" "$REPO_ROOT/" "$source_snapshot/" || fail "source snapshot failed"
rsync -a "$REPO_ROOT/build/ios-prefix-iphonesimulator/" "$prefix_snapshot/" \
    || fail "simulator dependency prefix snapshot failed"
[ ! -e "$source_snapshot/.git" ] && [ ! -e "$source_snapshot/.Codex" ] \
    && [ ! -e "$source_snapshot/bin" ] || fail "snapshot exclusion violation"
if find "$source_snapshot" -mindepth 1 -maxdepth 1 -name 'build*' -print -quit | grep -q .; then
    fail "snapshot exclusion violation: build*"
fi
compare_tree "$source_snapshot" "$guard_root/repo.tsv" "${SOURCE_EXCLUDES[@]}"
compare_tree "$prefix_snapshot" "$guard_root/prefix-source.tsv"
python3 "$GUARD" relocate-prefix --root "$prefix_snapshot" \
    --source "$REPO_ROOT/build/ios-prefix-iphonesimulator" \
    --replacement "$prefix_snapshot" || fail "could not relocate pkg-config prefix metadata"
manifest_tree "$prefix_snapshot" "$guard_root/prefix-snapshot.tsv"
guard_snapshots

cmake -S "$source_snapshot" -B "$build_root" -G Xcode \
    -DCMAKE_TOOLCHAIN_FILE="$source_snapshot/cmake/toolchains/ios.toolchain.cmake" \
    -DPLATFORM=SIMULATORARM64 -DDEPLOYMENT_TARGET=16.4 \
    -DCMAKE_FIND_ROOT_PATH="$prefix_snapshot" -DCMAKE_PREFIX_PATH="$prefix_snapshot" \
    -DXRAY_IOS_FAST_DEVICE=OFF \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED=NO \
    -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY='' || fail "Simulator configure failed"
python3 "$GUARD" cmake-cache --cache "$build_root/CMakeCache.txt" \
    --source "$source_snapshot" --prefix "$prefix_snapshot" --build "$build_root" \
    --repo "$REPO_ROOT" || fail "isolated Simulator CMakeCache validation failed"
python3 "$GUARD" openal-configured --cache "$build_root/CMakeCache.txt" \
    --prefix "$prefix_snapshot" --project "$build_root/OpenXRay.xcodeproj" \
    || fail "isolated Simulator OpenAL configured contract failed"
cmake --build "$build_root" --config Release --target xr_3da \
    --parallel "$build_jobs" || fail "Simulator build failed"

app="$source_snapshot/bin/aarch64/Release/xr_3da.app"
binary="$app/xr_3da"
[ -x "$binary" ] || fail "Simulator app was not produced at isolated output path"
python3 "$GUARD" binary-contract --binary "$binary" \
    || fail "Simulator Mach-O contract failed"
openal_artifact_fields=$(python3 "$GUARD" openal-artifact --prefix "$prefix_snapshot" --binary "$binary") \
    || fail "isolated Simulator OpenAL artifact contract failed"
openal_provider=$(printf '%s\n' "$openal_artifact_fields" | awk -F= '$1 == "openal_provider" {print $2}')
openal_sha256=$(printf '%s\n' "$openal_artifact_fields" | awk -F= '$1 == "openal_sha256" {print $2}')
[ "$openal_provider" = "OpenALSoft-1.25.2-static" ] && [[ "$openal_sha256" =~ ^[0-9a-f]{64}$ ]] \
    || fail "isolated Simulator OpenAL artifact contract returned incomplete fields"
bundle_id="$(python3 -c 'import plistlib,sys; print(plistlib.load(open(sys.argv[1], "rb"))["CFBundleIdentifier"])' "$app/Info.plist")" \
    || fail "could not read Simulator bundle identifier"
[ "$bundle_id" = "$BUNDLE_ID" ] || fail "unexpected Simulator bundle identifier: $bundle_id"
guard_protected
guard_snapshots

device_uuid="$(xcrun simctl create "OpenXRay Retail iOS-$runtime_label $runtime_id $timestamp" "$DEVICE_TYPE" "$runtime_id")" \
    || fail "could not create dedicated iOS $runtime_label Simulator"
case "$device_uuid" in *[!0-9A-Fa-f-]*|'') fail "invalid Simulator UUID" ;; esac
simulator_created=1
printf '%s\n' "$device_uuid" > "$work_root/simulator-uuid.txt" \
    || fail "could not write dedicated Simulator UUID"
xcrun simctl shutdown "$device_uuid" >/dev/null 2>&1 || true
xcrun simctl erase "$device_uuid" || fail "could not erase dedicated Simulator"
xcrun simctl boot "$device_uuid" || fail "could not boot dedicated Simulator"
xcrun simctl bootstatus "$device_uuid" -b || fail "Simulator did not boot"
xcrun simctl install "$device_uuid" "$app" || fail "could not install isolated Simulator app"
data_container="$(xcrun simctl get_app_container "$device_uuid" "$BUNDLE_ID" data)" \
    || fail "could not locate Simulator data container"
[ -d "$data_container" ] || fail "Simulator data container is not a directory"
data_container="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$data_container")" \
    || fail "could not resolve Simulator data container"
python3 "$GUARD" data-container --path "$data_container" --repo "$REPO_ROOT" \
    --backup "$backup" --manifest "$manifest" --home "$HOME" --udid "$device_uuid" \
    || fail "Simulator data container failed isolation contract"
documents="$data_container/Documents"
[ ! -e "$documents" ] || { [ -d "$documents" ] && [ -z "$(find "$documents" -mindepth 1 -print -quit)" ]; } \
    || fail "fresh Simulator Documents is not empty"
stage_args=(stage --backup "$backup" --manifest "$manifest" --repo "$REPO_ROOT" --destination "$documents" \
    --output "$work_root/staged-files.tsv")
[ "$with_saves" = 0 ] || stage_args+=(--with-saves)
python3 "$GUARD" "${stage_args[@]}" || fail "retail staging failed"
if [ -n "$autoload_save" ]; then
    autoload_config_args=(autoload-config --documents "$documents" --name "$autoload_save" \
        --evidence "$work_root/generated-user.ltx" \
        --manifest "$work_root/generated-user.ltx.manifest.tsv")
    [ "$ui_navigation" = 0 ] || autoload_config_args+=(--ios-autoinput)
    [ "$capture_v2" = 0 ] || autoload_config_args+=(--ios-diagnostics)
    if [ "$ui_captures" = 1 ]; then
        autoload_config_args+=(--ios-diagnostics --ui-captures)
    fi
    if [ "$quickload_evidence" = 1 ]; then
        autoload_config_args+=(--ios-diagnostics --ios-autoinput --quickload-evidence)
    fi
    python3 "$GUARD" "${autoload_config_args[@]}" \
        || fail "could not generate isolated Simulator user.ltx"
    selected_save="$documents/_appdata_/savedgames/$autoload_save.scop"
    python3 "$GUARD" selected-save-state --file "$selected_save" \
        --output "$work_root/autoload-save-before.tsv" \
        || fail "could not manifest selected staged save before launch"
fi
guard_protected
guard_snapshots

# A timeout is never success. Menu mode additionally requires a future engine
# marker emitted only after a real main-menu frame, then a foreground cycle
# that preserves the original PID and appends one ordered lifecycle pair.
launch_args=(launch-proof --timeout "$launch_timeout" --poll "$poll_interval" \
    --stdout "$work_root/host-stdout.log" --stderr "$work_root/host-stderr.log" \
    --copied-log "$work_root/xr_boot.log" --source-log "$documents/xr_boot.log" \
    --screenshot "$work_root/screenshot.png" --udid "$device_uuid" --bundle "$BUNDLE_ID" \
    --snapshot-manifest "$work_root/runtime-log-snapshot.txt")
if [ -z "$autoload_save" ]; then
    launch_args+=(--initial-pid "$work_root/initial-pid.txt" \
        --recovery-screenshot "$work_root/screenshot-after-foreground.png")
else
    launch_args+=(--autoload-save "$autoload_save")
fi
if [ "$quickload_evidence" = 1 ]; then
    launch_args+=(--pid-output "$work_root/quickload-pid.txt")
fi
if [ "$ui_navigation" = 1 ]; then
    launch_args+=(--navigation-script "$source_snapshot/misc/ios/simulator_ui_navigation.py" \
        --navigation-documents "$documents" \
        --navigation-snapshot "$work_root/ui-navigation-log-snapshot.txt" \
        --navigation-pre-report "$work_root/ui-navigation-pre-termination.json")
fi
if [ "$ui_captures" = 1 ]; then
    ui_capture_root="$work_root/ui-captures"
    mkdir "$ui_capture_root" || fail "could not create native UI capture parent"
    ui_capture_run_uuid="$(python3 -c 'import uuid; print(uuid.uuid4())')" || fail "could not create native UI capture run UUID"
    ui_capture_run="$ui_capture_root/$ui_capture_run_uuid"
    launch_args+=(--ui-capture-root "$ui_capture_root" \
        --ui-capture-run-uuid "$ui_capture_run_uuid" \
        --ui-capture-runtime "$runtime_label" \
        --ui-capture-renderer "Apple-Software-Renderer" \
        --ui-capture-git-revision "$git_revision" \
        --ui-capture-source-tree-sha256 "$source_tree_sha256")
fi
if [ "$capture_v2" = 1 ]; then
    capture_root="$work_root/capture-v2"
    mkdir "$capture_root" || fail "could not create capture-v2 evidence directory"
    launch_args+=(--capture-v2-parser "$source_snapshot/misc/ios/lighting_ab_evidence.py" \
        --capture-v2-root "$capture_root")
fi
python3 "$GUARD" "${launch_args[@]}" \
    || fail "Simulator launch did not prove its required runtime boundary before timeout"
if [ "$quickload_evidence" = 1 ]; then
    quickload_root="$work_root/quickload-evidence"
    mkdir "$quickload_root" || fail "could not create QuickLoad evidence directory"
    runtime_pid="$(< "$work_root/quickload-pid.txt")" \
        || fail "could not read QuickLoad launched PID"
    [[ "$runtime_pid" =~ ^[1-9][0-9]*$ ]] || fail "QuickLoad launched PID is invalid"
    python3 "$source_snapshot/misc/ios/simulator_quickload_evidence.py" run \
        --documents "$documents" --log "$documents/xr_boot.log" --expected-pid "$runtime_pid" \
        --staged-manifest "$work_root/staged-files.tsv" --root "$quickload_root" \
        --original-save "$selected_save" --timeout "$launch_timeout" --poll "$poll_interval" \
        || fail "QuickSave/QuickLoad Simulator evidence failed before termination"
fi
xcrun simctl terminate "$device_uuid" "$BUNDLE_ID" \
    || fail "could not stop Simulator app before post-runtime integrity checks"
finalize_args=(finalize-log --source-log "$documents/xr_boot.log" \
    --copied-log "$work_root/xr_boot.log" \
    --snapshot-manifest "$work_root/runtime-log-snapshot.txt" \
    --proof-metadata "$work_root/runtime-proof.txt")
[ -z "$autoload_save" ] || finalize_args+=(--autoload-save "$autoload_save")
# This generic finalizer only permits the one extra canonical load marker.  The
# dedicated oracle above and below proves the F9 request, PID and quick_load epoch.
[ "$quickload_evidence" = 0 ] || finalize_args+=(--allow-canonical-quickload-marker)
python3 "$GUARD" "${finalize_args[@]}" \
    || fail "post-stop Simulator log did not preserve its required runtime boundary"
openal_runtime_fields=$(python3 "$GUARD" openal-runtime-log --log "$work_root/xr_boot.log") \
    || fail "isolated Simulator OpenAL runtime provider contract failed"
runtime_openal_provider=$(printf '%s\n' "$openal_runtime_fields" | awk -F= '$1 == "openal_provider" {print $2}')
[ "$runtime_openal_provider" = "$openal_provider" ] \
    || fail "isolated Simulator OpenAL runtime provider does not match its archive"
if [ "$ui_navigation" = 1 ]; then
    python3 "$source_snapshot/misc/ios/simulator_ui_navigation.py" finalize \
        --log "$documents/xr_boot.log" \
        --snapshot "$work_root/ui-navigation-log-snapshot.txt" \
        --pre-report "$work_root/ui-navigation-pre-termination.json" \
        --final-report "$work_root/ui-navigation-post-termination.json" \
        || fail "post-stop semantic Simulator UI navigation proof failed"
fi
if [ "$ui_captures" = 1 ]; then
    [ -f "$ui_capture_run/manifest.json" ] && [ ! -L "$ui_capture_run/manifest.json" ] \
        || fail "post-stop native UI capture manifest is missing"
    ui_capture_manifest_state="$work_root/ui-capture-manifest.state"
    ui_capture_manifest_report_fields="$work_root/ui-capture-manifest-report-fields.txt"
    python3 "$GUARD" capture-manifest-state --manifest "$ui_capture_run/manifest.json" \
        --output "$ui_capture_manifest_state" \
        || fail "could not bind finalized native UI capture manifest state"
    python3 "$GUARD" capture-manifest-report-fields --state "$ui_capture_manifest_state" \
        --output "$ui_capture_manifest_report_fields" \
        || fail "could not prepare native UI capture manifest report fields"
fi
compare_args=(staged-compare --root "$documents" \
    --manifest "$work_root/staged-files.tsv" \
    --required "$manifest/required-archives.tsv" \
    --large "$manifest/large-files-sha256.tsv")
[ "$with_saves" = 0 ] || compare_args+=(--with-saves)
if [ -n "$autoload_save" ]; then
    python3 "$GUARD" selected-save-mutation --file "$selected_save" \
        --before "$work_root/autoload-save-before.tsv" \
        --after "$work_root/autoload-save-after.tsv" \
        --report "$work_root/autoload-save-mutation.txt" \
        || fail "selected staged save became invalid after Simulator runtime"
    if [ "$quickload_evidence" = 0 ]; then
        compare_args+=(--mutable-save "$autoload_save")
    fi
fi
python3 "$GUARD" "${compare_args[@]}" \
    || fail "staged retail data changed during Simulator runtime"
guard_protected
guard_snapshots

# This is intentionally after every generic post-stop guard.  It freezes the
# QuickLoad-specific save/log/capture packet at the last possible point before
# report preparation; no PASS artifact can survive a later staging mutation.
if [ "$quickload_evidence" = 1 ]; then
    python3 "$source_snapshot/misc/ios/simulator_quickload_evidence.py" finalize \
        --documents "$documents" --log "$documents/xr_boot.log" --expected-pid "$runtime_pid" \
        --staged-manifest "$work_root/staged-files.tsv" --root "$quickload_root" \
        --original-save "$selected_save" \
        || fail "post-stop QuickSave/QuickLoad evidence revalidation failed"
    quickload_manifest_pending="$quickload_root/manifest.pending.json"
    quickload_manifest="$quickload_root/manifest.json"
    quickload_report_fields="$quickload_root/report-fields.txt"
    [ -f "$quickload_manifest_pending" ] && [ -f "$quickload_report_fields" ] \
        || fail "QuickLoad evidence finalization did not prepare its pending artifacts"
fi

[ -s "$work_root/runtime-proof.txt" ] || fail "runtime proof metadata is missing or empty"
runtime_pid="$(awk -F= '$1 == "pid" {print $2}' "$work_root/runtime-proof.txt")" \
    || fail "could not read runtime PID"
case "$runtime_pid" in ''|*[!0-9]*|0) fail "runtime proof PID is invalid" ;; esac
capture_token=""
runtime_level=""
if [ "$capture_v2" = 1 ]; then
    runtime_level="$(awk -F= '$1 == "level" {print $2}' "$work_root/runtime-proof.txt")" \
        || fail "could not read capture-v2 runtime level"
    [ "$runtime_level" = "zaton" ] \
        || fail "capture-v2 checkpoint requires sync_level exactly zaton"
    capture_token="$(python3 "$source_snapshot/misc/ios/lighting_ab_evidence.py" \
        verify-simulator-capture-set \
        --boundary "$capture_root/boundary-watermark.json" \
        --baseline "$capture_root/baseline.json" \
        --metadata "$capture_root/capture.json" \
        --ppm "$capture_root/capture.ppm" \
        --proof "$capture_root/capture-proof.json" \
        --expected-pid "$runtime_pid" --expected-level zaton)" \
        || fail "capture-v2 evidence failed full post-runtime revalidation"
    [[ "$capture_token" =~ ^[0-9a-f]{32}:[1-9][0-9]*$ ]] \
        || fail "capture-v2 verifier returned an invalid token"
fi

# Prepare every fallible report field before cleanup.  The final report name
# remains absent until the dedicated Simulator has been deleted successfully.
[ ! -e "$report" ] && [ ! -L "$report" ] && [ ! -e "$report_pending" ] && [ ! -L "$report_pending" ] \
    || fail "Simulator report destinations must be new"
{
    printf 'result=PASS\n'
    printf 'work_root=%s\n' "$work_root"
    printf 'runtime_label=%s\n' "$runtime_label"
    printf 'runtime_id=%s\n' "$runtime_id"
    printf 'simulator_uuid=%s\n' "$device_uuid"
    printf 'backup=%s\n' "$backup"
    printf 'with_saves=%s\n' "$with_saves"
    printf 'openal_provider=%s\n' "$openal_provider"
    printf 'openal_sha256=%s\n' "$openal_sha256"
    cat "$work_root/runtime-proof.txt"
    if [ -n "$autoload_save" ]; then
        printf 'generated_user_ltx=%s\n' "$work_root/generated-user.ltx"
        printf 'generated_user_ltx_manifest=%s\n' "$work_root/generated-user.ltx.manifest.tsv"
        printf 'autoload_save_before=%s\n' "$work_root/autoload-save-before.tsv"
        printf 'autoload_save_after=%s\n' "$work_root/autoload-save-after.tsv"
        printf 'autoload_save_mutation=%s\n' "$work_root/autoload-save-mutation.txt"
    fi
    if [ "$ui_navigation" = 1 ]; then
        printf 'ui_navigation=1\n'
        printf 'ui_navigation_snapshot=%s\n' "$work_root/ui-navigation-log-snapshot.txt"
        printf 'ui_navigation_pre_report=%s\n' "$work_root/ui-navigation-pre-termination.json"
        printf 'ui_navigation_final_report=%s\n' "$work_root/ui-navigation-post-termination.json"
        printf 'ui_navigation_scope=semantic-ui-navigation-only; CoP eptTasks is the combined tasks/map surface; not pixel, readability, performance, or physical-device proof\n'
    fi
    if [ "$ui_captures" = 1 ]; then
        printf 'ui_captures=PASS\n'
        printf 'ui_capture_manifest=%s\n' "$ui_capture_run/manifest.json"
        cat "$ui_capture_manifest_report_fields"
        printf 'ui_capture_scope=iOS-27.0-Simulator-Apple-Software-Renderer-only; native diagnostic readback only; not iPhone/readability/color/performance proof\n'
    fi
    if [ "$capture_v2" = 1 ]; then
        printf 'capture_v2=PASS\n'
        printf 'capture_runtime_boundary=saved_game_sync_complete\n'
        printf 'capture_token=%s\n' "$capture_token"
        printf 'capture_pid=%s\n' "$runtime_pid"
        printf 'capture_level=%s\n' "$runtime_level"
        printf 'capture_metadata=%s\n' "$capture_root/capture.json"
        printf 'capture_ppm=%s\n' "$capture_root/capture.ppm"
        printf 'capture_proof=%s\n' "$capture_root/capture-proof.json"
        printf 'capture_scope=iOS-27.0-Simulator-Apple-Software-Renderer-only\n'
    fi
    if [ "$quickload_evidence" = 1 ]; then
        printf 'quickload_evidence=PASS\n'
        printf 'quickload_manifest=%s\n' "$quickload_manifest"
        cat "$quickload_report_fields"
        printf 'quickload_scope=iOS-27.0-Simulator-Apple-Software-Renderer-only; normal F5/F9 path; not iPhone/pixel-quality/performance/other-content proof\n'
    fi
    printf 'host_stdout=%s\n' "$work_root/host-stdout.log"
    printf 'host_stderr=%s\n' "$work_root/host-stderr.log"
    printf 'xr_boot_log=%s\n' "$work_root/xr_boot.log"
    printf 'screenshot=%s\n' "$work_root/screenshot.png"
    if [ -z "$autoload_save" ]; then
        printf 'initial_pid=%s\n' "$work_root/initial-pid.txt"
        printf 'foreground_recovery_screenshot=%s\n' "$work_root/screenshot-after-foreground.png"
        printf 'foreground_cycle=Simulator-only; PID/lifecycle/frame-marker proof, not pixel, readability, performance, or physical-device proof\n'
    fi
    printf 'cleanup=deleted\n'
    printf 'protected_inputs=unchanged\n'
} | python3 "$GUARD" prepare-report --destination "$report_pending" >/dev/null \
    || fail "could not prepare complete retail Simulator report"

xcrun simctl shutdown "$device_uuid" >/dev/null 2>&1 || true
xcrun simctl delete "$device_uuid" || fail "could not delete dedicated Simulator after successful run"
simulator_created=0
# publish-report revalidates the finalized manifest immediately before linking
# report.txt and immediately afterwards, deleting the link on any mismatch.
publish_args=(publish-report --source "$report_pending" --destination "$report")
if [ "$ui_captures" = 1 ]; then
    publish_args+=(--capture-manifest-state "$ui_capture_manifest_state")
fi
if [ "$quickload_evidence" = 1 ]; then
    python3 "$source_snapshot/misc/ios/simulator_quickload_evidence.py" publish \
        --pending-manifest "$quickload_manifest_pending" --manifest "$quickload_manifest" \
        --report-pending "$report_pending" --report "$report" \
        || fail "could not atomically publish QuickLoad evidence/report"
else
    python3 "$GUARD" "${publish_args[@]}" >/dev/null \
        || fail "could not atomically publish retail Simulator report"
fi
echo "PASS — isolated retail Simulator workflow: $work_root"
