# OpenXRay iOS — operational handoff

**Updated:** 2026-08-09
**Docs:** [canonical](iOS-Port.md), [active](iOS-Port-Plan.md), [deferred](iOS-Port-Backlog.md).

Read this file completely, then the relevant active task/canonical section; search the Journal by task ID or exact symptom, never in full.
## Current checkpoint

The Apple M3 Pro host runs macOS 27.0 beta, Xcode/SDK 27.0 beta and CMake 4.4.0.
Device and simulator engines build arm64 for iOS 16.4+. Team `RMJWWPF379` signs
the stable `io.github.tryk016.openxray.RMJWWPF379` identifier, preserving data.
The physical-device baseline renders Call of Pripyat through native OpenGL ES
3.0 at a real 1864×860 drawable with 1:1 presentation and is playable with a
Bluetooth controller. The sector fallback and SSAO value-macro fixes resolved
their proven defects; a later dark-frame smoke remains causally unresolved.
IOS-P0-003 startup-sector v1 has host-complete `level_load`/`QuickLoad` epoch
evidence and exact/fallback/retained/unresolved/recovered outcomes. `retained`
only reuses a prior valid sector after authoritative camera application; JSON
PASS is structural, never world-frame/pixel proof, and unresolved fails device startup.
The simplified main/Video menu and Performance/Optimal/Quality controller are
device-proven. Diagnostic mode no longer suppresses Low Power/thermal limits.
Repeated diagnostic launches reached `serious thermal` and about 3.25 GB
physical footprint, so let the phone cool before a long run.
The reliability slice retains one PID, the 1864×860 drawable and correct frames
across four diagnostic background/foreground cycles. LOWMEMORY evicted 47 stale
surfaces, released 262,143 KiB and reduced footprint from 3,262,723 to 2,998,051 KiB; held W does not replay. Held touch, lock/app-switch and audio remain unproven.
The Options crash is fixed and all four tabs plus Video Options 3x are device-proven. Dense-inventory focus has stable fixed-point auto-scroll and exact indented clipping; 32 UI fixtures and both device gates pass, while the overfilled visual is phone-pending.
The corrected Safari/AVFAudio harness builds locally but needs a phone rerun.
iOS selects project-owned static OpenAL Soft 1.25.2 and fail-closes Apple OpenAL, dynamic/alternate/duplicate or forwarded linker forms; its local exact-scene interruption registry is reviewed but not iPhone interruption proof.
The hash-pinned SDL2 2.32.10 UIScene backport is locally/Simulator-complete: one scene, `SDLUIKitSceneDelegate`, one `SDL_main`, connected iOS 13+ `UIWindowScene` and scene-owned four-transition lifecycle. Isolated retail runs on iOS 26.5 (`simulator-work-20260809-135908-18426`, PID 25991) and iOS 27.0 (`simulator-work-20260809-140608-26903`, PID 35981) each reached one menu marker and one same-PID `activate -> deactivate -> activate` cycle; protected inputs and cleanup passed. This is not pixel/readability, iPhone, performance, audio-interruption or multi-cycle-soak evidence.
IOS-P2-004 installer hardening (13/13) and IOS-P2-005 local CI provenance (79/79) are Sol-approved; remote CI and device-install/container proof remain open.
IOS-P2-006 now has one bounds-checked BC1-BC5 codec and GLI mapping, strict/sanitized synthetic tests and five real fixtures. Runtime behavior is unchanged: source-sRGB still uses `GL_RGBA8`, swizzles are ignored, BC4=`RRR1`, BC5=`RG01`; the visual color decision remains phone-pending.
## First commands

```bash
git status --short
./misc/ios/build_fast_device.sh
./misc/ios/build_check.sh --full   # required before push
./misc/ios/install_device.sh --preflight  # host-only signing/preflight check
```

Preserve unrelated work. The consolidated source checkpoint is commit `27d966ddc`; inspect `git status` before every new slice.

When the phone is available and the matching FastDevice gate is green:

```bash
./misc/ios/install_device.sh --fast --launch
./misc/ios/install_device.sh --fast --autoinput
./misc/ios/install_device.sh --fast --diagnostics
./misc/ios/shot.sh /tmp/openxray-start.png
./misc/ios/input.sh w 2500
```

Only one worker may own the device and shared iOS build tree. Diagnostics are
for deterministic captures only. Device tools take the shared lease only before
their first phone command and can reuse an outer lease solely via its exact
`OPENXRAY_DEVICE_LEASE_TOKEN`. Autoinput keeps readback off, but disclose its
file polling in performance evidence and use ordinary launch for final baselines.

## Validation contracts

| Contract | Required result |
|---|---|
| Shader stages | 279/279 |
| Shader variants | low 2/2; SSAO 6/6 plus value; numeric macros 30/30 |
| Shader links | 137/137 |
| BC fallback | strict + ASan/UBSan; five real GLI fixtures |
| Binary | arm64, platform IOS, minOS 16.4 |
| Lua | LuaJIT interpreter mode |
| Presentation | logical 932×430, drawable/render targets 1864×860 |

The post-commit full gate rebuilt 68 TUs. UUID
`545F8958-0B1F-3D85-BDF2-758CF9553A16`; source
`449dd3f9d6bcd540d1ed6739fa111219f7c9d95fddcc0eed8529e10326a28da6`; bundle
`eeacc4b2d878f62ca70a33cf51e9014b2f20de20a28a64e50096879467c68351`; OpenAL
Soft 1.25.2 static SHA-256 `86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914`.
Gates: BC strict/sanitized, retail 74/74, installer 13/13, CI 79/79, shaders 279/279, low 2/2, SSAO 6/6, numeric 30/30, links 137/137; IOS/minOS 16.4, shader cache forced off.
A real preflight validated the existing profile/keychain and temporary signed copy without lease, provisioning or device access. The older UIScene stamp is local/Simulator evidence, not current.

## Proven on device

- Paid-team signing, in-place installation, autoload and ordinary/diagnostic
  launch work without deleting the data container.
- Logical UIKit/input space is 932×430; EAGL drawable and engine targets are
  1864×860 with no final spatial upscale.
- The affected Zaton spawn has no exact vertical sector hit; an 8 m
  nearest-floor fallback finds sector 115 and restores the complete world.
- Three cold launches render the affected save without a movement workaround.
- `ssao.ps` previously treated explicit zero-valued macros as enabled. Numeric
  `SSAO_QUALITY`/`SSAO_OPT_DATA` tests restore global ambient lighting.
- Correct lighting survives G-buffer packing variants, a scripted walk and
  repeated cold launches.
- Multiplayer is absent. Video exposes only Performance, Optimal and Quality,
  read-only 1864×860, gamma, contrast and brightness.
- All three profiles select their documented tier/target and render a complete
  world. Resolution and shader/resource topology remain fixed.
- With `ios_diagnostics=1`, Optimal still obeys the device constraint and
  switched from balanced to performance when iOS reported `serious thermal`.
- Main menu, all Options tabs, HUD, inventory and core PDA/map are readable at
  1864×860; XCUITest reached Video Options three times. Dense inventory remains untested.
- Four cycles on the current reliability build retained the PID, frame and
  1864×860 drawable. A later cycle also passed after LOWMEMORY and walking.
- A UUID-correlated W hold released at the entity, cancelled and did not replay.
- LOWMEMORY released 262,143 KiB of decoded texture storage and reduced current
  physical footprint by 264,672 KiB; PDA, world and inventory lazy reloads pass.

## Still pending

- Validate Optimal's measured downgrade/upgrade thresholds and frame pacing on
  a repeatable normal-mode path after the phone cools.
- For each IOS-P0-003 short phone batch, record a completed baseline epoch
  before the exact expected oracle batch; separately require resolved sector
  classification and an objective world frame. Run another outdoor save, an
  indoor/portal start, QuickLoad/save-reload and a level transition.
- Parser validation is 20/20; complete the budget: one PID, at least 1,700 seconds,
  at least 1,500 samples with no gap over five seconds, at most 3,328 MiB
  footprint and at most 128 MiB net growth. The separate
  2,560 MiB target remains unprovable until a 6 GB device exists. A 20-second
  normal-mode trace was stable near 3.20 GB, while repeated diagnostic launches
  produced `serious thermal` and system compressor pressure.
- Test dense inventory focus/clipping and the PDA faction-war surface. Retail
  fallback ordering is locally fixed but not visually confirmed.
- Device-prove held touch, lock/app-switch and the selected OpenAL Soft audio
  interruption; corrected XCUITest is local-only.
- Device-confirm profile reassert after Options discard/config reload.
- Device-accept the UIScene lifecycle: five Safari foreground cycles plus a
  separate lock/unlock and audio-interruption cycle, preserving PID, drawable,
  input, saves and audio.

## Next slice

Follow the numbered order in the active Plan:

1. Let the phone cool, then validate IOS-P0-003 on another outdoor save and an
   indoor/portal start: baseline completed epoch, exact batch, resolved outcome,
   then separately an objective world frame.
2. Device-accept IOS-P1-010 and IOS-P1-002: five Safari app-switch cycles,
   then separate lock/held-touch and OpenAL Soft interruption cycles.
3. Measure the same normal-mode path under all profiles.

## Safety

- Never restore full level prefetch blindly; it caused an approximately 1.6 GB
  transient spike and jetsam.
- Do not conflate the sector and SSAO defects or replace their proven fixes with
  brightness, streaming, ANGLE or Metal workarounds.
- Do not revoke certificates, change identity, uninstall or erase the container.
- Do not claim rendering correctness without an on-device frame or numeric
  probe.
- Never install after a stale gate; append evidence to [iOS-Port-Journal.md](iOS-Port-Journal.md).
