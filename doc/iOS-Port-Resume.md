# OpenXRay iOS — operational handoff

**Updated:** 2026-08-10
**Docs:** [canonical](iOS-Port.md), [active](iOS-Port-Plan.md), [deferred](iOS-Port-Backlog.md).

Read this file completely, then the relevant active task/canonical section; search the Journal by task ID or exact symptom, never in full.
## Current checkpoint

The M3 Pro host runs macOS/Xcode/SDK 27.0 beta and CMake 4.4.0; device and
Simulator engines build arm64 for iOS 16.4+. Team `RMJWWPF379` signs the stable `io.github.tryk016.openxray.RMJWWPF379` identifier, preserving data.
The physical-device baseline renders Call of Pripyat through native OpenGL ES
3.0 at a real 1864×860 drawable with 1:1 presentation and is playable with a
Bluetooth controller. The sector fallback and SSAO value-macro fixes resolved
their proven defects; a later dark-frame smoke remains causally unresolved.
IOS-P0-003 startup-sector v1 has host-complete `level_load`/`QuickLoad` epoch
evidence and exact/fallback/retained/unresolved/recovered outcomes. `retained`
only reuses a prior valid sector after authoritative camera application; JSON
PASS is structural, never world-frame/pixel proof, and unresolved fails device startup.
Capture-state v2 binds canonical JSON to each PPM/PNG token; its device A/A+3/A+6 harness is local/Sol-complete, while iPhone A/B and pixel/cause proof remain open.
The separate isolated iOS 27 single-capture host path is locally reviewed: static
PASS, parser 31/31, runner 84/84 and final Sol `APPROVE — brak P0/P1/P2`.
Real workroot `simulator-work-20260810-024814-86404` reached Zaton sync/after_load, T0 loading seq23 and T1 loading seq24, but candidates through seq88 stayed loading until the one 600 s deadline expired.
Its PID 92531 epoch1/frame35 marker was `level_load unresolved/none`, invalid sector, same camera/probe and radius0: stationary equal saved/current camera enters `CRender::Calculate` no-detection, so exact/fallback is not attempted. IOS-P0-003 stays active; both Simulators were deleted and no T2/report was published.
The simplified main/Video menu and Performance/Optimal/Quality controller are
device-proven. Diagnostic mode no longer suppresses Low Power/thermal limits.
Repeated diagnostic launches reached `serious thermal` and about 3.25 GB
physical footprint, so let the phone cool before a long run.
The reliability slice retains one PID, the 1864×860 drawable and correct frames across four diagnostic background/foreground cycles. LOWMEMORY evicted 47 stale
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

Preserve unrelated work. Device capture-state v2 is commit `90e9d3c3e`; inspect `git status` before every slice.

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

Commit `362bf625d` passed the post-commit full gate after rebuilding 68 TUs. UUID
`7297E9B6-1DBE-348D-8BAE-A153A19F781E`; source
`33c9efabda5cfdb99939440199b87331fac3ee8deb106383ca8fb6112a6e9c06`; bundle
`0e0ba58332a97b6d04fa458a50fd82006ebd6cecbe1f6f1182f8dabf453b6ff3`; OpenAL
Soft 1.25.2 static SHA-256 `86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914`.
Gates: BC strict/sanitized, retail 84/84, installer 13/13, CI 79/79, shaders 279/279, low 2/2, SSAO 6/6, numeric 30/30, links 137/137; IOS/minOS 16.4, shader cache forced off.
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
- Correct and regression-test the stationary IOS-P0-003 sector branch, then rerun isolated iOS 27 capture-v2 to a gameplay T2 and post-delete report; Apple Software Renderer evidence is not pixel/iPhone/performance proof.
- For each later phone batch, record a completed baseline epoch and require the exact resolved batch plus world frame. Run the new-directory lighting A/B packet, another outdoor save, indoor/portal, QuickLoad and a transition.
- Parser validation is 20/20; complete the budget with one PID, at least 1,700 seconds and 1,500 samples, no gap over five seconds, at most 3,328 MiB footprint and 128 MiB net growth. The 2,560 MiB target awaits a 6 GB device; a 20-second normal trace was stable near 3.20 GB, while repeated diagnostics caused `serious thermal` and compressor pressure.
- Test dense inventory focus/clipping and the PDA faction-war surface. Retail
  fallback ordering is locally fixed but not visually confirmed.
- Device-prove held touch, lock/app-switch and the selected OpenAL Soft audio
  interruption; corrected XCUITest is local-only.
- Device-confirm profile reassert after Options discard/config reload.
- Device-accept the UIScene lifecycle: five Safari foreground cycles plus separate lock/unlock and audio interruption, preserving PID, drawable, input, saves and audio.

## Next slice

Follow the numbered order in the active Plan:

1. Correct the stationary startup-sector branch, extend its deterministic policy/oracle tests, then rerun isolated iOS 27 capture-v2 to complete T2/report publication.
2. Run the controlled phone lighting A/B packet, then validate another outdoor and indoor/portal start with baseline, resolved outcome and a frame.
3. Device-accept IOS-P1-010 and IOS-P1-002: five Safari app-switch cycles,
   then separate lock/held-touch and OpenAL Soft interruption cycles.
4. Measure the same normal-mode path under all profiles.

## Safety

- Never restore full level prefetch blindly; it caused an approximately 1.6 GB
  transient spike and jetsam.
- Do not conflate the sector and SSAO defects or replace their proven fixes with
  brightness, streaming, ANGLE or Metal workarounds.
- Do not revoke certificates, change identity, uninstall or erase the container.
- Do not claim rendering correctness without an on-device frame or numeric
  probe.
- Never install after a stale gate; append evidence to [iOS-Port-Journal.md](iOS-Port-Journal.md).
