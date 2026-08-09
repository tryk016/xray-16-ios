# Codex session log archive — through 2026-07-24

## 2026-07-19 — project takeover and documentation reset

### Starting state

- Branch: `ios-port`
- Inherited revision: `43dadb509`
- Worktree at takeover: clean
- Apple Developer Program: Individual, Team ID `RMJWWPF379`
- Local device signing and cable installation: working
- Shader baseline: 279/279 compile, 137/137 link
- Current P0: distant terrain is absent from the G-buffer after level load and
  appears only after sufficient player movement

### Governance setup

The workspace policy requested a base copy from
`~/Codex/templates/AGENTS.md`. That template was not present on this machine.
`AGENTS.md` was therefore created from the repository's proven workflow,
safety constraints, and current iOS architecture.

### Decisions established

- `doc/iOS-Port.md` is the canonical current specification.
- Native OpenGL ES 3.0 remains the reference renderer.
- ANGLE/native Metal work is deferred until the ES baseline is stable and
  measured.
- The immediate technical target is bounded geometry prefetch/streaming, not a
  renderer rewrite.
- The build journal remains append-only; stale operational documents are
  replaced or explicitly marked historical.

### Completed documentation baseline

- Created the canonical iOS specification and measurable definition of done.
- Replaced the stale exhaustive plan with a prioritized P0/P1/P2 roadmap.
- Replaced the stale resume guide with the current local build/device handoff.
- Marked the dated audit historical and the Metal proposal deferred.
- Updated the journal header, controller reference, main README and public IPA
  metadata.
- Preserved all historical journal evidence.

### Next action

Start IOS-P0-001 as a read-only instrumentation slice: correlate the first
complete far-field G-buffer frame with player distance, sector/portal changes,
resource completion, and current physical memory footprint.

## 2026-07-19 — resolution and graphics-settings investigation

### Proven

- Static level buffers, visuals and sectors load synchronously; the disabled
  gameplay prefetch is not a direct explanation for missing static terrain.
- The P0 artefact survives 1×/2× resolution, VSync on/off, geometry LOD
  0.75/2.0, visibility distance 1.0/1.5, G-buffer optimization on/off, reduced
  texture memory, and disabled postprocess/reflection options.
- `r__supersample 2` now produces a real 1864×860 internal target while keeping
  the SDL/UIKit window at 932×430. It remained near 60 FPS in the diagnostic
  scene and added about 52 MB physical footprint, but HUD elements are too small.
- Reducing texture quality lowered reported texture allocation from about
  556 MB to 186 MB without changing the defect.

### Fixed

- `gl/ssao.ps`: corrected an ES-illegal `int + float` expression reached by the
  no-MSAA, `SSAO_QUALITY=1` permutation.
- `glsl_es_check.py` and `build_check.sh`: added an enforced 2/2 low-settings
  shader profile.
- `install_device.sh`: default installation now prefers the freshly built `.app`
  instead of silently selecting an older archived IPA.

### Device end state

- Restored Extreme/full-texture baseline.
- Internal scale 1 (932×430), VSync off, G-buffer optimization on, MSAA 2×.
- Autoload and `keypress_on_start 0` verified.

### Revised next action

Instrument sector/portal traversal, visible-sector lists, static draw-graph size,
and frustum/HOM/SSA rejection reasons around the movement-triggered transition.

## 2026-07-19 — startup-world root cause and fix

### Proven

- The old white/black terrain frame was not delayed streaming. When the camera
  was at `(256.24, 21.47, 550.82)`, straight down/up collision rays found no
  static triangle, leaving `last_sector_id` invalid.
- The main draw graph returns early for an invalid camera sector, so sky/HUD
  remained while static geometry was absent.
- An offset vertical query 8 m in +Z found outdoor sector 115. Earlier walking
  merely reached a point where sector detection succeeded; walking back kept the
  cached valid sector.
- An iOS-only nearest-floor fallback restored the world without movement on
  three cold launches. It ran once per launch; sector 115 was active by frame
  34. The third run used the build with temporary visibility counters removed.

### Fixed

- `r2_R_calculate.cpp`: compare saved/current camera position instead of saved
  direction/current position, and probe nearby floor positions only when exact
  camera-sector detection fails.
- `CSector` and `CPortal`: initialize traversal markers and dual-render state.
- Removed the temporary visibility/culling counters after diagnosis.

### Next action

Validate an additional outdoor save plus an indoor/portal location before
closing P0.

## 2026-07-19 — global ambient-lighting root cause and fix

### Correction to the previous state

The startup-sector fix restored all static geometry, but it did not fix the
separate global-lighting defect. With geometry present, the world remained
nearly black except for a small correctly lit area near the player.

### Proven

- `hmodel` produced a strong, nonzero hemispheric result before SSAO.
- `occ` reduced that result by about six times across most of the world; local
  light in the accumulator was not multiplied by `occ`, which created the
  apparent light circle around the player.
- `common.h` gives ES feature-quality macros explicit zero defaults. `ssao.ps`
  incorrectly tested macro presence with `#ifndef`, so `SSAO_OPT_DATA=0` selected
  the optimized half-depth branch and sampled `s_half_depth` even though the CPU
  did not generate that buffer. `SSAO_QUALITY=0` similarly failed to disable
  SSAO, invalidating the earlier “SSAO off” negative test.
- Changing both checks to value tests restored spatially valid `occ` values with
  both G-buffer packing off and on.

### Fixed and verified

- `res/gamedata/shaders/gl/ssao.ps` now uses
  `#if SSAO_QUALITY == 0` and `#if SSAO_OPT_DATA == 0`.
- Temporary lighting-band diagnostics were removed.
- The full gate passed: 279/279 shader compile, 2/2 low-settings compile, 6/6
  SSAO resource branches plus the value-macro contract, 137/137 shader links,
  arm64 engine build.
- The normal build rendered globally lit terrain and vegetation after a
  nine-second scripted walk and on two additional cold launches.
- Final device settings: 932×430 internal resolution, SSAO high, G-buffer
  optimization on, MSAA 2×, VSync off, full texture quality.
- Final boot log contained no fatal, shader compile/link, or GL error.

### Remaining work

Cross-save/indoor sector validation and memory/lifecycle reliability remain P0.
Audit the other ES zero-default feature macros before enabling HBAO or relying on
SSR variants; their older presence tests are a separate P1 hardening task.

## 2026-07-19 — 2× output and textured UI restoration

### Proven and fixed

- The device now runs `r__supersample 2`, producing a 1864×860 render target.
- `dxUIShader::GetBaseTexture()` incorrectly used a GLSL sampler unit as an
  index into the sorted `(unit, texture)` list. Looking up the matching unit
  restored valid atlas dimensions for HUD/UI UV generation.
- The ES base-vertex fallback left classic vertex attribute pointers offset by
  `baseV`. A later non-indexed UI draw supplied `startV` as well, applying the
  offset twice and reading unrelated vertices. Resetting `vb_base` before
  `glDrawArrays` restored inventory panels, item icons, equipment, PDA/menu
  atlases, and other textured quads.
- Screen-space `pttTL` draws now explicitly disable inherited scene
  depth/stencil/cull state immediately before drawing.
- Invalid or optimized-out 0×0 UI textures no longer generate infinite UVs.
- `sh_pair` now has a strict lexicographic comparator, preventing corruption of
  the shared UI shader cache.

### Device evidence

- Inventory is complete at 1864×860: background, slots, equipment, weapons,
  portrait, scrollbar and item icons are visible.
- Full gate before the diagnostic cleanup was green: 279/279 compile, 2/2 low
  settings, 6/6 SSAO branches/value contract, 137/137 links and arm64 build.
- Temporary UI texture, quad and GPU-state probes were removed.

## 2026-07-19 — real 1864×860 iOS drawable

### Proven and fixed

- The prior `r__supersample 2` path rendered the engine at 1864×860 but did not
  make that setting the contract of the UIKit/EAGL backing store.
- Added an iOS display bridge that sets the actual SDL OpenGL view
  `contentScaleFactor` to 2.0 after context creation and before engine render
  targets are created.
- `CHW::GetSurfaceSize()` now uses `SDL_GL_GetDrawableSize()` and
  `CRenderDevice::SelectResolution()` preserves the pixel size across window
  updates without consulting `r__supersample`.
- Device configuration was returned to `r__supersample 1`.
- Device log proved `OpenGL drawable 1864x860 (UIKit window 932x430, scale
  2.0)`; the captured render target is also 1864×860, making presentation 1:1.
- The arm64 engine gate passed and the signed build was installed without
  changing the bundle identifier or data container.

### Next action

Increase the logical size and contrast of the Options UI without changing the
now-fixed 1864×860 drawable or globally shrinking gameplay HUD elements.

## 2026-07-19 — native presentation and Options readability complete

### Scope

- User explicitly changed the project contract to iOS-only. Desktop
  compatibility is no longer an acceptance criterion.

### Completed

- Revalidated the post-review drawable refresh fix with the arm64 gate.
- Installed a build proving an actual 1864×860 OpenGL drawable and 1864×860
  frame capture with `r__supersample 1`.
- Added a dedicated iOS widescreen Options XML selected only on iOS.
- Enlarged the Options layout by 25%, raised body/heading fonts, increased text
  contrast and added an opt-in closed-combobox font path.
- Preserved all source XML nodes, IDs and 76 option bindings.
- Full shader/link/engine gate passed.
- Installed and navigated to Options autonomously; the on-device capture was
  accepted by the user.

### Device end state

- App running in Options at native app drawable 1864×860.
- `r__supersample 1`, `ui_style_default`, VSync off.
- Autoload temporarily removed for UI testing; retail assets and saves remain in
  the existing container.

### Next action

Continue iOS-only validation of the remaining dense UI surfaces (Advanced
Options, Controls, PDA/map) or return to P0 cross-save and lifecycle validation.

## 2026-07-21 — offline diagnostics, CI and dense-UI hardening

### Completed

- Added `ios_diagnostics` as the single opt-in gate for periodic frame readback
  and file-driven input; normal launch explicitly disables both.
- Made installer mode selection fail-closed, added fresh capture generation
  tokens, lifecycle-safe synthetic keys and digit-key support.
- Removed obsolete cursor/texture/controller/focus probes while retaining a
  stable gold gamepad focus frame.
- Corrected controller binding conflict groups and Controls font size, AO row
  height, fresh-press-only binding capture, plus 0×0 texture guards for
  frame/progress UI paths.
- Made CI use the same strict shader wrapper, pinned glslang 16.4.0, blocked
  release on shaders, restricted publication to `ios-port`, and serialized the
  rolling release globally.
- Added Xcode dSYM generation plus non-empty `__debug_info` and exact UUID
  checks. A full local symbol rebuild produced 590,419,597 debug-info bytes,
  matching app/dSYM UUID `757911FA-42BC-31A2-9B2A-CDEAE241BDB5`, and a valid
  292,020,812-byte archive.
- Full gate passed twice after integration: 279/279, 2/2, 6/6 + numeric
  contract, 137/137 and arm64; the last run rebuilt one translation unit.

### Not device-validated

- No phone was used. The new build is not installed; existing assets, saves and
  app container are untouched.
- Next device slice: `install_device.sh --diagnostics`, Advanced/Controls check,
  duplicate gamepad binding test, resolved retail XML capture for inventory/PDA,
  then `install_device.sh --launch` before performance and lifecycle work.

### Remaining evidence gaps

- Inventory focus auto-scroll and focus-frame clipping inside scrolled views.
- Dormant HBAO/HDAO/SSR zero-default macro paths need real test harnesses before
  enablement.
- First remote dSYM artifact observation and a deliberately failing remote
  shader/varying canary.

### Independent review corrections and UI extension

- Moved dSYM settings from the wrong `deps` matrix to `engine-build`; simulator
  keeps debug symbols off.
- Changed diagnostic capture scheduling from paused game time to continual time.
  `shot.sh` now needs two observed tokens after an uncertain baseline and waits
  up to 12 seconds, so a transient cable read cannot return a stale frame.
- Release now waits for smoke, engine, shader and LuaJIT jobs. Publication alone
  is serialized and is not cancelled midway through IPA/apps.json replacement.
- CI verifies glslang 16.4.0 against commit
  `168d452a4f460d24b588fed08477a81c44ee27a1`.
- A full-gate timestamp prevents the default installer from silently choosing a
  local app after relevant source/gamedata changes.
- Added diagnostic-only requested/resolved CUI XML logging, controller-focus
  auto-scroll for drag/drop inventory cells, and focus-frame clipping for known
  inventory/list/scroll viewports.
- Final full gate rebuilt 1,854 translation units and passed 279/279, 2/2, 6/6
  plus numeric contract, 137/137 and arm64. No phone was used.

## 2026-07-24 — migration to M3 Pro and Xcode 26.6

### Proven environment

- Apple M3 Pro, 36 GiB, macOS 26.5.2; Xcode 26.6 (17F113), device/simulator SDK
  26.5, CMake 4.4.0, glslang 16.4.0 and Ninja 1.13.2.
- Removed the stale Intel Homebrew shell initialization and made the local gate
  prefer `/opt/homebrew/bin` on arm64.
- Corrected the entire active project contract from iOS 15.0 to iOS 16.4.
- Clean arm64 device/simulator smoke tests and dependency superbuilds passed.
- Isolated LuaJIT device/simulator builds passed with macOS arm64 host tools and
  iOS arm64 target archives.
- Fixed LuaJIT host-tool configuration so the iOS 16.4 target is not forwarded
  as an invalid macOS deployment version.

### Engine, symbols and device

- Complete simulator engine build passed for arm64, platform IOSSIMULATOR,
  minOS 16.4 and SDK 26.5.
- Xcode 26.6 target defaults overrode the CI dSYM request. Propagated explicit
  debug settings to every compiled CMake target, not only `xr_3da`.
- Final clean device gate rebuilt 1,854 units and passed 279/279, 2/2, 6/6 plus
  numeric SSAO contract, 137/137 and arm64 final link.
- Final dSYM: 880 MB, 590,443,673 `__debug_info` bytes, UUID
  `92E88454-67D1-3689-8AC9-F5AED83B4828` matching the app. Mach-O is IOS arm64,
  minOS 16.4, SDK 26.5.
- Paid-team identity, provisioning profile and paired iPhone 15 Pro Max/iOS 26.6
  were valid. The app installed over the existing stable bundle ID and launched
  without deleting its data container.
- Fresh diagnostic frame was 1864×860 and showed the active world, textures,
  weapon and HUD. Final state is the clean build running with
  `ios_diagnostics 0`, autoload present and `keypress_on_start 0`.

### Open findings

- Final link still selects deprecated Apple `OpenAL.framework` despite the
  built arm64 OpenAL Soft archive.
- Toolchain policy and LuaJIT configure-probe warnings remain non-blocking debt.
- This migration validation does not close dense UI, cross-save/level, memory,
  lifecycle or performance work.
- Hardened local and CI dSYM checks to require at least 100 MiB of
  `__debug_info` plus an exact UUID match; this rejects the observed 51 KB
  final-target-only failure while the clean reference is 590,443,673 bytes.

## 2026-07-24 — cleanup and checkpoint before macOS 27

- Cleanup scope is the OpenXRay repository only. Global Intel Homebrew and other
  projects were left untouched; a briefly started ARM package migration was
  fully rolled back to the prior leaves list.
- Removed ignored iOS 15/Xcode 26.4/partial migration caches, the failed LuaJIT
  host-tool cache, one-shot environment validation trees, empty `tools/` and
  obsolete `.claude` local permissions.
- Retained active ARM device/simulator prefixes, dependency builds, engine
  trees, final app and matching dSYM.
- Removed the unused tracked `apps.json` snapshot; release metadata remains
  generated from the packaged IPA by GitHub Actions.
- Trimmed completed detail from the active roadmap, added missing
  evidence/acceptance contracts, and corrected stale deferred-Metal wording.
- Added the macOS 27 requalification task for build/sign/install, GPU capture
  and repeatable Main Menu → Options XCUITest automation.
- Checkpoint contract: tag `ios-pre-macos27-2026-07-24` and bundle
  `/Users/patryk/openxray-backups/openxray-ios-pre-macos27-2026-07-24.bundle`.
- Artifact checkpoint:
  `/Users/patryk/openxray-backups/openxray-ios-artifacts-pre-macos27-2026-07-24.zip`
  contains the unsigned app and matching full dSYM; installation still signs a
  temporary copy with the stable identifier/profile.
- Removed the superseded Intel/iOS 15 startup prompt and generated workflows
  from `openxray-handoff`, but retained the device save/config/log backup.
- Removed the historical iOS 15 IPA, transient migration logs, Python cache and
  Finder metadata.
- Final post-cleanup gate passed 279/279, 2/2, 6/6, the SSAO macro contract and
  137/137; 14 units rebuilt. The dSYM remains 590,443,673 `__debug_info` bytes
  with UUID `92E88454-67D1-3689-8AC9-F5AED83B4828` matching the arm64 iOS 16.4
  app.
- Independent review hardened the checkpoint path: device gates now require
  debug symbols and always verify dSYM size/UUID before stamping; install
  freshness covers source/resources/Externals/CMake and iOS app/gate inputs;
  signing selects only a keychain fingerprint authorized by the chosen
  provisioning profile.
- `install_device.sh --renew` validated the profile/identity pair. The hardened
  full gate passed again with all shader/link profiles, zero engine
  recompilations and the same full dSYM/UUID.

## 2026-07-24 — recovery after macOS 27/Xcode 27 update

- Requalified Apple M3 Pro/36 GiB on macOS 27.0 beta (`26A5388g`) with Xcode
  27.0 beta (`27A5228h`), SDK 27.0, AppleClang 21 and CMake 4.4.0.
- Removed only stale generated cache references to the old Xcode 26.5 path.
  Fresh smoke tests, both dependency superbuilds, isolated LuaJIT checks and the
  complete simulator engine passed as arm64 with minimum iOS 16.4.
- The full device gate rebuilt 1,854 units and passed 279/279, 2/2, 6/6, the
  SSAO macro contract and 137/137. The dSYM contains 513,353,999
  `__debug_info` bytes and matches app UUID
  `66122874-7481-3540-9C4F-8C171313E766`.
- Paid-team signing, in-place install and unattended launch passed with the
  stable bundle ID. Fresh 1864×860 loading/gameplay captures proved assets,
  world, textures, HUD and weapon; the final launch restored
  `ios_diagnostics 0`.
- Xcode 27's installed Metal toolchain recorded a ten-second whole-device Metal
  System Trace containing `xr_3da` and 47,766 GPU execution events. Direct
  PID/name attachment is broken in this beta; whole-device capture works.
- XCUITest Main Menu → Options automation remains the unfinished part of
  IOS-P1-009. No engine or shader source change was required.

## 2026-07-24 — simplified iOS graphics controls (build-verified)

- Filtered Multiplayer in `CUIMMShniaga::CreateList` so retail/fallback XML
  cannot reintroduce it.
- Video now shows Performance/Optimal/Quality, real read-only render dimensions
  and calibration sliders; desktop renderer/window/preset/Advanced choices are
  hidden.
- Added fixed 1864×860 profiles and a p90/hysteresis Optimal controller that
  changes only visibility, geometry LOD and two shadow-cost scalars. Thermal and
  Low Power constraints are integrated through `NSProcessInfo`.
- Added deterministic policy and static UI contract gates.
- Device arm64 build gate and complete simulator arm64 build passed for iOS
  16.4/SDK 27.0. No phone install/runtime validation was performed.
- Hardened both gate and installer against the shared output directory by
  rejecting simulator Mach-O payloads and any minimum OS other than 16.4.
- Final full gate after the simulator overwrite/relink passed 279/279, 2/2,
  6/6, the SSAO macro contract, 137/137 and produced an iPhoneOS 16.4 app with
  matching full dSYM UUID `A8883D4D-13B4-330B-BFBA-F7C9EEB8BBE2`.
