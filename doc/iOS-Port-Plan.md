# OpenXRay iOS — active roadmap

**Last synchronized:** 2026-07-24

**Canonical contract:** [iOS-Port.md](iOS-Port.md)

**Platform scope:** iOS-only; desktop compatibility is not an acceptance gate.

**Current focus:** validate the startup-sector fix across content, then establish
memory and lifecycle reliability

This document contains active and future work. Historical slice details and
resolved failures belong in [iOS-Port-Journal.md](iOS-Port-Journal.md).

## Milestone status

| Milestone | Outcome | Status |
|---|---|---|
| M0 — Toolchain | iOS arm64 toolchain, dependencies, static engine link | Complete |
| M1 — Application | UIKit/SDL launch, sandbox paths, app bundle and IPA | Complete |
| M2 — ES renderer | ES 3.0 context, shaders, textures, deferred world rendering | Complete with known defects |
| M3 — Playability | Level load, controller gameplay, touch UI, audio, saves | Complete for controller testing |
| M4 — Reliability | Streaming, memory, lifecycle, deterministic automation | In progress |
| M5 — Performance | Resolution, frame pacing, thermal and GPU/CPU optimization | Baseline started |
| M6 — Distribution | Reproducible tester package and documented data setup | Partial |
| M7 — Renderer decision | Stay ES, adopt ANGLE, or start native Metal from measurements | Deferred |

M0 was clean-room revalidated on 2026-07-24 after moving to an Apple M3 Pro:
macOS 26.5.2, Xcode 26.6, SDK 26.5 and CMake 4.4.0 build device and simulator
dependencies plus the complete engine for deployment target iOS 16.4.

## P0 — release validation

### IOS-P0-003: validate startup-sector recovery across content

**Evidence:** The affected Zaton save is fixed on three cold launches without
input. Exact vertical sector detection fails at the spawn, the nearest-floor
probe finds sector 115 at 8 m, and the full static world renders from the first
playable frames.

**Next slice:**

1. Load at least one additional outdoor save and one indoor/portal location.
2. Confirm direct detection remains the normal path and fallback runs only when
   the exact query is invalid.
3. Exercise save/reload and a level transition.
4. Record current and peak physical footprint during a 30-minute controller run.

**Acceptance:**

- Three cold launches and two save/level combinations start with complete world
  geometry and no movement workaround.
- Indoor and portal-adjacent starts select the correct sector.
- No approximately 1.6 GB prefetch spike or new unbounded growth.
- The diagnostic visibility counters are absent from the final test build.

## P1 — correctness and reliability

### IOS-P1-001: make CI gates authoritative

**Evidence level:** local proven; first remote artifact and negative canary
remain untested.

CI now uses the strict local shader contract, pinned glslang, serialized release
publication and a device dSYM contract of at least 100 MiB plus exact UUID
match. Exact checkpoint values are retained in the journal, not this roadmap.

**Remaining:**

- Run a temporary deliberately broken shader and varying pair through Actions
  to prove the remote job, not only the local wrapper, turns red.
- Confirm the first remote device job publishes both IPA and dSYM artifacts.

**Acceptance:** a deliberately broken shader or varying pair makes CI red.

### IOS-P1-002: finish iOS lifecycle

**Evidence level:** source audit; full recovery cycle untested.

- Stop rendering before entering background.
- Avoid all GL calls while backgrounded.
- Revalidate/rebind the drawable on foreground.
- Handle `SDL_APP_LOWMEMORY`.
- Reset touch state and release synthetic mouse input on deactivate.
- Verify save/config flush behavior.

**Acceptance:** five lock/background cycles and one audio interruption recover in
the same session without stuck input, black output, or lost settings.

### IOS-P1-003: trustworthy memory telemetry

**Evidence level:** current `phys_footprint` logging proven; eviction accounting
and budgets unimplemented.

- Report current `phys_footprint` separately from peak RSS.
- Account for decoded GPU texture allocation, not only compressed source bytes.
- Log prefetch and eviction deltas.
- Define budgets for at least the current test device and one lower-memory target.

**Acceptance:** logs can prove that an eviction lowers current memory.

### IOS-P1-004: isolate the autonomous test harness

**Evidence level:** both launch modes and fresh-frame generation proven on
device; normal-mode performance impact unmeasured.

`ios_diagnostics` is the single fail-closed gate around file input and periodic
frame readback. Diagnostic mode produced a fresh 1864×860 frame; ordinary launch
restored zero and remained running.

**Remaining validation:** record a normal-mode performance sample proving no
`autoinput.txt` polling and no periodic `glReadPixels`.

**Acceptance:** performance/release builds do not poll input files or execute
periodic full-frame `glReadPixels`.

### IOS-P1-005: define presentation and performance baseline

**Evidence level:** drawable geometry proven on device; FPS/thermal baseline
unmeasured.

- Current device facts: logical window/input 932×430 and actual EAGL drawable
  1864×860 (`contentScaleFactor=2.0`).
- Engine render targets and the screen drawable are both 1864×860; presentation
  is a 1:1 blit, with no final upscale or downsample.
- `r__supersample` has returned to 1 and no longer controls iOS resolution.
- Revalidate the 2x drawable after foreground recovery and any UIKit layout
  change.
- The Options dialog now has a dedicated iOS layout with a 25% larger panel,
  larger fonts and stronger contrast. Keep later HUD/inventory/PDA sizing work
  independent from that screen and from the fixed pixel resolution.
- Measure CPU frame, GPU frame, memory and thermals with diagnostics disabled.
- Choose a 30 FPS floor first; evaluate 60 FPS only after stability.

**Acceptance:** the same scripted camera path produces a repeatable baseline
report, and five foreground cycles retain a 1864×860 drawable.

### IOS-P1-006: close visible UI renderer gaps

**Evidence level:** menu/Options/HUD and core texture-path fixes proven on
device; dense inventory/PDA behavior is only build-verified.

Still confirm Advanced Options and Controls on device. For inventory/PDA/map,
collect the new exact resolved-XML log under `ui_style_default`, then validate
focus auto-scroll and clipping in an overfilled inventory. Also verify textured
cursor, minimap, magnifier and video-wrapper surfaces using on-device captures.
Do not infer a common cause without proving the draw path.

**Acceptance:** menu, HUD, inventory, PDA/map and video surfaces are complete,
readable and internally consistent on the supported iPhone baseline.

### IOS-P1-007: audit ES feature-macro semantics

**Evidence level:** current SSAO permutations proven; dormant HBAO/HDAO/SSR
paths are static findings only.

- Keep explicit zero defaults required by GLSL ES numeric `#if` expressions.
- Replace incompatible presence tests only where the corresponding disabled or
  non-optimized path is valid.
- Keep the landed 6/6 disabled, full-G-buffer, optimized full/half and
  downsample full/half profile plus the numeric macro contract in the offline
  gate.
- Review the older HBAO and SSR helper paths separately; do not enable them
  merely because the primary SSAO path is fixed.
- Static audit found dormant value-contract violations in HBAO/HDAO and a
  locally unsafe `SSR_QUALITY` helper test. They are not active in the current
  iOS profile. Add real harness entries before changing or enabling those paths;
  a green profile that never includes HDAO is not evidence.

**Acceptance:** every zero-default quality/feature macro has one documented
meaning, disabled permutations compile, and no shader selects a resource path
that the CPU did not allocate or populate.

### IOS-P1-008: make environment alias rebinding explicit

**Evidence level:** source-level risk identified; runtime stale-bind event
unproven.

`dxEnvironmentRender::lerp()` can replace the GL surface ID inside an existing
`CTexture`, while the backend texture cache primarily compares the `CTexture*`.
This did not cause the fixed stable-darkness defect, but it can leave stale
environment cubemaps across weather/menu transitions.

- Add a surface revision or explicitly invalidate affected backend slots.
- Log expected and actual `GL_TEXTURE_BINDING_CUBE_MAP` in one diagnostic run.
- Exercise a weather transition and menu→world transition.

**Acceptance:** every alias ID change produces the intended bind, with no stale
cubemap and no redundant full cache flush.

### IOS-P1-009: qualify macOS 27 debugging and UI automation

**Evidence level:** pre-upgrade build/sign/install baseline proven; post-update
GPU capture and XCUITest support untested.

1. Record macOS, Xcode, SDK, CMake and native tool versions before changing
   caches.
2. Regenerate device and simulator smoke tests at deployment target 16.4, then
   run the complete gate and device install.
3. Test Xcode GPU capture against the GLES process. If translated work is not
   visible, prove that limitation and select Metal System Trace or a minimal
   ANGLE/Metal probe explicitly.
4. Add an XCUITest target that launches the app and deterministically reaches
   Main Menu → Options.

**Acceptance:** build/sign/install is green after the update; one repeatable GPU
workload capture opens in an available Apple tool (or the unsupported GLES path
and selected alternative are documented); and UI automation reaches Options
without manual input on three consecutive runs.

## P2 — product and maintenance debt

### IOS-P2-001: touch robustness and gameplay controls

**Evidence level:** menu pointer/tap proven; lifecycle cancellation and virtual
gameplay controls incomplete.

- Clear active-finger state on lifecycle transitions.
- Synthesize release for any held touch-generated button.
- Decide whether controller-only gameplay is acceptable for the first release.
- If not, implement virtual move/look controls and action buttons using the
  existing input receiver contract.

Reference: [iOS-Controller-Prior-Art.md](iOS-Controller-Prior-Art.md).

**Acceptance:** no stuck touch/mouse state survives five lifecycle transitions;
the first-release controller-only or virtual-gamepad scope is explicit and its
chosen gameplay path completes a ten-minute device run.

### IOS-P2-002: use OpenAL Soft intentionally

**Evidence level:** OpenAL Soft archive exists; final link to Apple OpenAL proven.

The dependency superbuild creates arm64 `libopenal.a`, but the Xcode 26.6 device
cache and final link still resolve Apple's deprecated SDK
`OpenAL.framework`.

**Acceptance:** the final link uses the intended library, interruptions still
recover, and the device log reports expected extension support.

### IOS-P2-003: restore visibility-query optimization

**Evidence level:** disabled iOS path source-proven; ES query benefit untested.

The iOS path currently disables occlusion queries for correctness. Implement the
ES-compatible boolean query path only after startup-world validation is closed.

**Acceptance:** no invalid GL calls, no visual regression, and measured CPU/GPU
benefit on the scripted path.

### IOS-P2-004: harden signing/install tooling

**Evidence level:** current single-device flow and profile-authorized
certificate fingerprint matching are proven locally; device/artifact
configuration remains hard-coded.

- Move device UDID and local IPA directory to arguments or environment.
- Keep the installed bundle identifier stable.
- Preserve the profile-renewal stub and never revoke unrelated certificates.

**Acceptance:** the installer selects only a certificate authorized by the
`RMJWWPF379` provisioning profile, accepts device and artifact configuration
without source edits, preserves the stable bundle ID and refuses an
identity/profile mismatch before installation.

### IOS-P2-005: supply-chain and CI maintenance

**Evidence level:** current workflow reviewed locally; action SHA pinning and
cache provenance incomplete.

- Pin third-party GitHub Actions to reviewed commit SHAs.
- Cache immutable dependency outputs with source/toolchain hashes.
- Retain the simulator as compile coverage, not as the rendering authority.

**Acceptance:** third-party actions use reviewed SHAs, dependency cache keys
include source/toolchain inputs, and a clean uncached run reproduces the device
and simulator artifacts.

### IOS-P2-006: define the iOS texture color-space fallback

**Evidence level:** RGBA8 expansion path source-proven; visual/color contract
unmeasured.

The CPU DXT/BC fallback currently expands sRGB variants to ordinary `GL_RGBA8`
and does not preserve every GLI swizzle. This was not the cause of the SSAO
lighting failure, but it can produce unintended sampling on iOS.

**Acceptance:** representative sky/environment/albedo textures document their
source format, upload format, swizzle and shader-space expectation; numeric
probes and iOS reference frames show an intentional, bounded result.

## M6 — distribution completion

**Priority:** P2.

**Evidence level:** local paid-team signing, cable installation and stable data
container are proven; resumable retail-data import and the first complete remote
release artifact set are untested.

The primary developer route is local signing and cable installation. GitHub
Actions may continue publishing an unsigned IPA and SideStore source for remote
testers.

Before calling distribution complete:

- document legal retail-data import;
- make asset transfer resumable and verifiable;
- keep saves across signed updates;
- remove device-specific identifiers from general instructions;
- ensure release metadata does not claim the build is pre-renderer;
- keep release metadata generated by CI; do not restore the removed stale
  repository-root `apps.json` snapshot;
- document profile/certificate recovery without destructive steps.

App Store distribution is not an active target.

**Acceptance:** a new authorized tester can import legally owned retail data,
verify and resume the transfer, install the generated package without a
device-specific source edit, update it without losing saves, and recover an
expired profile without revoking unrelated certificates.

## M7 — renderer decision gate

**Priority:** deferred; it must not displace open P0/P1 reliability work.

**Evidence level:** native ES correctness fixes and the current renderer
baseline are proven; ANGLE compatibility and native Metal cost/performance are
research estimates without an iOS prototype.

Do not start an ANGLE or native Metal migration to solve either resolved startup
bug. Missing geometry was an engine sector-detection defect; global darkness was
a GLSL feature-macro defect. Both are fixed and verified on the ES backend.

Revisit the renderer only after:

1. IOS-P0-003 is complete.
2. Lifecycle and memory telemetry are trustworthy.
3. A repeatable ES performance baseline exists.
4. Remaining problems are classified as engine-level or API/backend-level.

Decision inputs:

| Option | Choose when |
|---|---|
| Keep ES | Correctness is complete and performance meets the target |
| ANGLE-on-Metal | ES API/driver behavior is the dominant remaining risk and ANGLE proves compatible in a prototype |
| Native Metal | Long-term performance/tooling justifies the estimated 42–58 implementation slices |

The native proposal is maintained separately in
[iOS-Metal-Plan-Draft.md](iOS-Metal-Plan-Draft.md).

**Acceptance:** after all four prerequisites are met, a recorded decision
compares ES, an iOS ANGLE prototype and the native-Metal estimate using the same
scene, correctness checks, CPU/GPU frame times, memory and tooling evidence. Any
selected migration starts under a separately approved implementation plan.

## Definition of done

The completion contract is canonical in [iOS-Port.md](iOS-Port.md). Every task
closed in this plan must include:

- the tested revision and device;
- commands used;
- measured evidence;
- a journal entry stating what was proven and what remains inferred;
- a clean `./misc/ios/build_check.sh` result for engine/shader changes.
