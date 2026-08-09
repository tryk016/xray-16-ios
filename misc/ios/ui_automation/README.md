# OpenXRay iOS UI automation

This is a test-only Xcode runner. It does not change the OpenXRay application,
bundle identifier, deployment target or data container.

## Local build without a phone

```bash
./misc/ios/ui_automation/run.sh --build
```

The script generates a temporary Xcode project, adds the missing UI-test
relationship to CMake's scheme and performs a signed `build-for-testing` for a
generic iOS device. Override the temporary directory with
`OPENXRAY_UI_AUTOMATION_BUILD_DIR`.

The signed build requires a valid local Apple Development identity and access
to Team `RMJWWPF379`; it does not install, launch or otherwise access a phone.
One process owns a build directory at a time. If the process is forcibly killed,
verify that no runner remains before removing its `.openxray-ui-automation.lock`.

## Physical-device run

```bash
OPENXRAY_UI_AUTOMATION_RESULT=/tmp/OpenXRayUIAutomation-1.xcresult \
./misc/ios/ui_automation/run.sh --test
```

The first run may require interactive authorization on the Mac or phone. Never
guess a password or passcode. `OPENXRAY_DEVICE_UDID` overrides the current test
device. Result bundles and collected engine logs are never overwritten, and the
test terminates OpenXRay during teardown even when its assertion fails.

Physical modes acquire the shared iPhone lease immediately before the first
device read and release it during cleanup. If a larger OpenXRay batch already
owns the lease, the runner reuses it only when that batch passes the exact token
through `OPENXRAY_DEVICE_LEASE_TOKEN`; it does not release ownership that it did
not acquire. Any lease without a matching token fails the run before Xcode
touches the phone. `--prepare` and `--build` never acquire the lease.

The test performs three independent launches of the stable
`io.github.tryk016.openxray.RMJWWPF379` installation, taps Main Menu → Options
and preserves before/after screenshots in the result bundle. Its calibrated
coordinate accounts for XCUITest exposing the landscape-right OpenGL surface
through rotated screenshot axes. The 2026-08-04 device run visually proved all
three final captures on Video Options; lifecycle actions remain separate work.

The physical scenarios are selected explicitly so a short shared-device lease
does not run unrelated tests:

```bash
./misc/ios/ui_automation/run.sh --test-options
./misc/ios/ui_automation/run.sh --test-lifecycle
./misc/ios/ui_automation/run.sh --test-audio
./misc/ios/ui_automation/run.sh --test-reliability
```

`--test` remains an alias for `--test-options`. The lifecycle scenario preserves
the already-running game process and completes five app switches through Safari
(`com.apple.mobilesafari`): Safari must be foreground, OpenXRay must be running
in the background or suspended (never terminated), and OpenXRay must then return
to the foreground. It aborts on the first failed stage and keeps one screenshot
for every successful recovery. The scoped log delta must contain five ordered
`deactivate → activate → foreground drawable 1864x860` groups in one
continuously appended engine log, so foreground activation cannot hide a
killed-and-relaunched process.

The audio scenario activates a non-mixable `Playback` AVAudioSession in the
XCTest runner itself while OpenXRay stays foreground. It runs a looping,
nonzero PCM buffer through `AVAudioEngine` and `AVAudioPlayerNode`, then stops
and deactivates with `NotifyOthersOnDeactivation`. Its log oracle requires an
ordered `interruption began … wasSuspended=0` followed by `interruption ended`,
with no app deactivation in between. In reliability mode that audio pair must
occur only after the fifth foreground drawable. A screenshot, a successful test
driver start, or a local build alone never proves an engine interruption.

The collected log defaults next to the `.xcresult` as `*-xr_boot.log`;
`OPENXRAY_UI_AUTOMATION_LOG` overrides it. `--test-reliability` performs both
operations without terminating OpenXRay in between, matching the roadmap's
same-session recovery requirement. A physical-device rerun is still required to
prove the five real switches, the AVAudioSession begin/end pair, recovery frames
and absence of a crash; local `--build` proves only compilation, nested signing
and the AVFAudio linkage.

Xcode 27's XCTest/XCUIAutomation frameworks require iOS 17.0. That minimum
applies only to this runner; the product baseline remains iOS 16.4+.
