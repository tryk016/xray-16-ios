# OpenXRay iOS — operational handoff

**Updated:** 2026-07-24

**Read first:** [iOS-Port.md](iOS-Port.md)

**Active tasks:** [iOS-Port-Plan.md](iOS-Port-Plan.md)

**Platform scope:** iOS-only. Do not spend project time preserving desktop
behavior unless it directly helps the iOS build.

## Current state in one paragraph

The Apple M3 Pro host is fully validated with macOS 26.5.2, Xcode 26.6, SDK
26.5 and CMake 4.4.0. Clean arm64 dependency, device, simulator and isolated
LuaJIT builds target iOS 16.4. The complete application signs with Apple
Developer Team `RMJWWPF379`, installs over USB, boots a Call of Pripyat save,
renders through native OpenGL ES 3.0, and is playable with a Bluetooth
controller. Shader gates are 279/279 compile, 2/2 low-settings, 6/6 SSAO
branches plus the numeric macro contract, and 137/137 link. Two separate
startup defects are fixed: a vertical collision miss left the camera without a
sector and hid static geometry; independently, `ssao.ps` treated explicit
zero-valued ES feature macros as enabled and suppressed global ambient light.
The current build was installed and left running in normal mode. A diagnostic
round-trip produced a fresh 1864×860 level/HUD frame, then normal launch restored
`ios_diagnostics 0`. A clean full-symbol build passed the minimum 100 MiB
`__debug_info` and exact app/dSYM UUID contract. Multi-save/level validation
remains the current P0; exact artifact values are recorded in the journal.

## Saved checkpoint before macOS 27

The pre-upgrade source checkpoint is named
`ios-pre-macos27-2026-07-24`. Its portable bundle is stored outside the
repository at:

```text
/Users/patryk/openxray-backups/openxray-ios-pre-macos27-2026-07-24.bundle
```

The matching unsigned build product and full dSYM are archived separately at:

```text
/Users/patryk/openxray-backups/openxray-ios-artifacts-pre-macos27-2026-07-24.zip
```

The app in that archive is the reproducible build output with the bare product
bundle identifier. `install_device.sh` copies it, applies the stable installed
identifier and paid-team profile, signs the copy, then installs it. The archive
does not replace the signing step.

Only active ARM/iOS 16.4 caches remain:

```text
build/ios-prefix-iphoneos
build/ios-prefix-iphonesimulator
build/ios-deps-iphoneos
build/ios-deps-iphonesimulator
build/ios-engine-iphoneos
build/ios-engine-iphonesimulator
bin/aarch64/Release/xr_3da.app
bin/aarch64/Release/xr_3da.app.dSYM
```

Obsolete iOS 15, Xcode 26.4, partial migration and failed LuaJIT host-tool
caches were removed. Intel Homebrew and other projects are outside this
repository and are not part of the cleanup.

Immediately after the OS update, do not diagnose renderer behavior from an old
cache. First record and validate the host:

```bash
sw_vers
xcode-select -p
xcodebuild -version
xcodebuild -showsdks
cmake --version
glslangValidator --version
xcrun devicectl list devices
./misc/ios/build_check.sh
./misc/ios/install_device.sh --launch
```

Then qualify GPU capture and XCUITest under IOS-P1-009. Availability of macOS 27
tooling is not yet evidence that Apple's capture can inspect this OpenGL
ES-on-Metal workload.

## First commands

From `/Users/patryk/openxray`:

```bash
git status --short
./misc/ios/build_check.sh
./misc/ios/install_device.sh --diagnostics
./misc/ios/shot.sh /tmp/openxray-start.png
./misc/ios/input.sh w 2500
./misc/ios/shot.sh /tmp/openxray-path.png
./misc/ios/install_device.sh --launch
```

Only one worker may build/install/use the device at a time.

## Device and signing

| Item | Value |
|---|---|
| Program | Apple Developer Program, Individual |
| Team ID | `RMJWWPF379` |
| Installed bundle ID | `io.github.tryk016.openxray.RMJWWPF379` |
| Profile duration | One year under the paid membership |
| Install tool | `misc/ios/install_device.sh` |
| Validated phone | iPhone 15 Pro Max, iOS 26.6, Developer Mode enabled |
| Deployment target | iOS 16.4 |

Do not revoke certificates. Do not change the installed bundle ID casually: the
retail assets and saves live in that app's data container.

The installer renews/fetches the profile through the provisioning stub when
required. It selects a keychain identity only when its certificate fingerprint
is authorized by that exact profile; another team's first available Apple
Development certificate cannot be selected accidentally. Both launch modes
restore autoload and disable the “press any key” gate. `--diagnostics`
additionally enables cable input and fresh frame capture; ordinary `--launch`
explicitly disables their polling/readback overhead.

## Current validated state

### Proven

- Apple M3 Pro/macOS 26.5.2 with Xcode 26.6 builds clean arm64 device and
  simulator dependency prefixes plus the complete engine at iOS 16.4.
- LuaJIT host generators are macOS arm64 and target archives are iOS arm64.
- A clean device build compiled 1,854 translation units and produced a full
  dSYM above the 100 MiB gate with matching UUID.
- Signing identity, paid-team provisioning profile (valid through 2027-07-19),
  cable install, autoload, launch and fresh 1864×860 diagnostic capture work on
  the migrated host. The app was returned to `ios_diagnostics 0`.
- The affected camera position has no vertical static-collision hit.
- With no sector, the main draw graph returns before static world geometry.
- A probe 8 m in +Z finds sector 115 and restores normal rendering.
- Walking only appeared to repair streaming because it eventually found a
  sector; walking back retained the cached sector and the world stayed correct.
- Three cold launches rendered correctly without synthetic input; the third used
  the build with temporary visibility counters removed.
- After geometry recovery, the remaining dark world was isolated to
  `hdiffuse *= occ`; local light bypasses that multiplication and formed the
  apparent bright circle.
- `SSAO_OPT_DATA=0` incorrectly selected the ungenerated half-depth path because
  `ssao.ps` used `#ifndef` instead of a value test. `SSAO_QUALITY=0` likewise
  invalidated the earlier “SSAO off” test.
- The value-test fix produced correct lighting with G-buffer packing off and on,
  after a nine-second scripted walk, and on two additional cold launches.
- Final baseline: 932×430 logical UIKit/input space with a real 1864×860
  OpenGL drawable and 1:1 presentation; `r__supersample 1`, VSync off, SSAO
  high, G-buffer optimization on, MSAA 2×, full texture quality.

### Next slice

After the macOS 27 host requalification, close IOS-P0-003 first: test one
additional outdoor save and one indoor/portal location, followed by save/reload,
a level transition and a 30-minute memory/lifecycle run. Then use
`--diagnostics` to verify Advanced Options (especially AO/SSAO), Controls font
and duplicate gamepad binding resolution, inspect the exact retail XML logged
for inventory/PDA/map, and validate focus auto-scroll/clipping in a full
inventory. Return to normal `--launch` before performance work.

## Working rules

- Run `./misc/ios/build_check.sh` before every device install that follows an
  engine or shader change.
- The gate includes a 2/2 low-settings shader permutation check in addition to
  the 279/279 baseline, a 6/6 SSAO resource-branch check, the numeric SSAO macro
  contract and the 137/137 link check.
- Shader-only checking does not rebuild/copy gamedata into the app; use the
  engine gate before an on-device shader experiment.
- Do not pipe the gate through `tail` or anything that masks its status.
- Treat frame readback and file-driven input as diagnostic overhead; scripts
  intentionally fail unless the app was launched with `--diagnostics`.
- Label every conclusion `proven`, `inferred`, or `untested`.
- Append each completed slice to [iOS-Port-Journal.md](iOS-Port-Journal.md).

## Known open debt

- Background/foreground rendering lifecycle and `SDL_APP_LOWMEMORY`.
- Current physical-footprint telemetry and decoded-texture accounting.
- Both diagnostic and normal launch modes are device-verified; a measured
  normal-mode overhead sample is still required.
- Touch cancellation and virtual gameplay controls.
- Deprecated Apple OpenAL is still selected instead of OpenAL Soft.
- Occlusion queries are disabled on iOS.
- Presentation is fixed at a real 2x/1864×860 drawable with a 1:1 blit. HUD,
  inventory and PDA remain independent logical layouts. Options now uses its
  own accepted 25%-larger iOS layout with larger fonts and stronger contrast.
- CI now enforces both local shader gates with pinned glslang and packages a
  locally verified, UUID-matched dSYM. The first remote artifact check and a
  deliberate negative canary remain.
- Release publication waits for all independent build jobs and is serialized
  without cancelling an in-flight IPA/apps.json replacement.
- Other zero-default ES feature macros still need a presence-test audit before
  HBAO or alternate SSR paths are enabled.
- Environment cubemap aliases need explicit cache invalidation across ID changes;
  the DXT fallback also needs a documented sRGB/swizzle policy. Neither caused
  the fixed SSAO darkness.

Do not begin an ANGLE or Metal migration as a workaround for either resolved
startup defect. Renderer migration remains a measured post-stability decision.
