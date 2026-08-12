# OpenXRay iOS — deferred backlog

**Last synchronized:** 2026-08-12

**Canonical contract:** [iOS-Port.md](iOS-Port.md)

**Active work:** [iOS-Port-Plan.md](iOS-Port-Plan.md)

This file contains deferred work only. Its presence does not authorize starting
an item. Promote an item into the active Plan explicitly, keep its ID, and
remove it from this file in the same slice. Historical implementation evidence
belongs in [iOS-Port-Journal.md](iOS-Port-Journal.md).

## Deferred P1

### IOS-P1-001: make CI gates authoritative

**Priority:** P1 deferred behind device correctness and reliability.

**Evidence level:** local gate proven; first remote artifact and deliberate
negative canary remain untested.

CI uses the strict local shader contract, pinned glslang, serialized release
publication and a device dSYM contract of at least 100 MiB plus exact UUID
match.

**Acceptance:** a deliberately broken shader or varying pair makes Actions red,
and a clean remote device job publishes matching IPA and dSYM artifacts.

### IOS-P1-004: isolate the autonomous test harness

**Priority:** P1 deferred until the normal-mode performance pass.

**Evidence level:** diagnostic and ordinary launch modes proven on device;
normal-mode overhead unmeasured.

`ios_diagnostics` is the fail-closed gate around file input and periodic frame
readback. Record a normal-mode sample proving there is no `autoinput.txt`
polling and no periodic `glReadPixels`.

**Acceptance:** performance/release builds do not poll input files or execute
periodic full-frame readback.

### IOS-P1-007: audit ES feature-macro semantics

**Priority:** P1 deferred; do not promote solely on local static evidence.

**Evidence level:** the local sub-slice is complete and Sol xhigh-approved:
five explicit numeric zero fallbacks; macro mutations 30/30; debt 0 presence +
0 `#undef`; comment-aware CPU-to-allocation/downsample/water-binding/SSR-sample
mutations 13/13; compile 279/279, low 2/2, SSAO 6/6, SSR 9/9 and links 137/137.
HBAO/HDAO remain forced false. `SSR_HALF_DEPTH` is now emitted only when
requested and `ssao_opt_data` creates/populates the target; otherwise SSR uses
`s_position`. This changes a real water/SSR resource selection but remains
static/local evidence only.

**Remaining acceptance:** retain explicit numeric meanings and disabled
permutations; later capture a reference iPhone frame containing water under the
new SSR policy, then review pixels separately from performance. Do not claim
image quality or close the whole item before that device evidence.

### IOS-P1-008: make environment alias rebinding explicit

**Priority:** P1 deferred until a stale-bind event is reproduced.

**Evidence level:** source-level cache risk identified; runtime failure
unproven.

`dxEnvironmentRender::lerp()` can replace the GL surface ID inside an existing
`CTexture`, while the backend cache primarily compares the `CTexture*`. Add a
surface revision or explicit invalidation, then probe weather and menu→world
transitions.

**Acceptance:** every alias ID change produces the intended cubemap bind with
no stale texture and no redundant full cache flush.

### IOS-P1-003: establish trustworthy memory telemetry

**Priority:** P1 deferred behind the current startup, presentation and
lifecycle/device-interruption validation.

**Evidence level:** a real device warning selected 47 stale surfaces and
released 262,143 KiB of decoded texture storage. Current physical footprint
fell from 3,262,723 to 2,998,051 KiB. PDA, world, HUD and inventory rendered
afterward; a scripted five-second walk and a later foreground cycle also passed.
The Activity Monitor parser passes twenty positive/negative fixtures, the
FastDevice/full gates and final Sol xhigh review. The 30-minute budget and
lower-memory target remain device-untested.

**Remaining device actions:**

- Repeat LOWMEMORY after a longer traversal and verify environment/colormap,
  video and render-target aliases in addition to the proven UI/world reloads.
- Run the parser-enforced 30-minute iPhone 15 Pro Max budget: one PID, at least
  1,700 seconds, at least 1,500 post-warmup samples, no adjacent gap over five
  seconds, at most 3,328 MiB footprint and at most 128 MiB net growth.
- Validate the separate 2,560 MiB lower-memory target when a 6 GB device exists;
  it is defined but cannot be claimed on current hardware.

**Acceptance:** device logs prove that an eviction lowers current memory and
the 30-minute run remains inside an explicit device budget.

### IOS-P1-002: finish iOS lifecycle

**Priority:** P1 temporarily deferred while the phone is unavailable; restore
it to the active Plan before the next physical lifecycle batch.

**Evidence level:** the local frame gate, lifecycle inbox, persistence ordering,
input cancellation, GL detach/rebind, LOWMEMORY path and corrected Safari/audio
harness pass their deterministic contracts and reviews. Four diagnostic device
cycles and one held-W cycle pass. The UIScene backport also preserves one PID
through one Simulator recovery cycle on both iOS 26.5 and 27.0. Normal Safari
cycles, lock/held-touch and a real audio interruption remain physical-device
untested.

**Remaining acceptance:** five normal Safari/background cycles, one separate
lock/unlock cycle with held touch and one real OpenAL Soft interruption recover
in the same process without stuck input, black output, lost audio or lost
settings/saves.

## P2 — product and maintenance debt

### IOS-P2-005: harden iOS CI supply-chain provenance

**Priority:** P2; local contract complete, remote acceptance pending.

**Evidence level:** all 12 external action uses are pinned to reviewed exact
commit SHAs with release comments. The dependency-prefix cache has one exact
key covering branch, runner, SDK/platform, iOS 16.4/bitcode/build/generator,
seven reviewed source inputs and the measured toolchain fingerprint. There are
no restore keys; the build skips only on an exact hit, while library verification
and artifact upload remain unconditional.

The stdlib-only local validator retains semantic diagnostics and also binds the
complete workflow as exact UTF-8 bytes. Its 79/79 mutation contract, Python
compile, `actionlint`, whitespace checks and extracted input/toolchain shell
smokes pass. Independent Sol xhigh review returned
`APPROVE — brak P0/P1/P2`. The default `ios-port` branch is unprotected, so this
is provenance/drift protection, not cache-poisoning resistance.

**Remaining:** run one clean cache miss and a second exact cache hit in GitHub
Actions, then compare the device and Simulator dependency/artifact evidence.

**Acceptance:** both remote runs reproduce the expected device and Simulator
artifacts; branch policy or another explicit trust control closes the remaining
unprotected-writer boundary. Simulator coverage is compile-only and never
rendering authority.

### IOS-P2-002: prove the intentional OpenAL Soft interruption path

**Priority:** P2 deferred; local technical approval is complete, while the
physical interruption proof depends on temporarily deferred IOS-P1-002 being
promoted back to the active Plan.

**Evidence level:** the project-owned static OpenAL Soft 1.25.2 selection,
per-Core exact-scene interruption registry, strict/ASan/UBSan policy tests and
final Sol xhigh review are proven locally. Physical iPhone interruption
recovery is untested.

The provider contract selects only the exact prefix archive and rejects Apple
OpenAL, dynamic/alternate/duplicate archives and forwarded linker forms. The
required runtime record is `OpenAL Community` / `OpenAL Soft` /
`1.1 ALSOFT 1.25.2` with extension, pause and resume support `1/1/1`.

**Acceptance:** after IOS-P1-002 is promoted for its physical lifecycle batch,
an iPhone audio interruption preserves the same process/drawable/input state,
resumes audio without paused-emitter underflow and records the expected provider
capability line.

### IOS-P2-001: touch robustness and gameplay controls

**Priority:** P2.

**Evidence level:** menu pointer/tap proven; lifecycle cancellation is locally
implemented, reviewed and build-proven; virtual gameplay controls are incomplete.

- Device-prove held-touch and synthetic-key cancellation across five lifecycle
  transitions without a stuck button or stale queued event.
- Decide whether controller-only gameplay is acceptable for the first release.
- Otherwise implement virtual move/look controls and action buttons.

Reference: [iOS-Controller-Prior-Art.md](iOS-Controller-Prior-Art.md).

**Acceptance:** no stuck touch/mouse state survives five lifecycle transitions;
the chosen controller-only or virtual-gamepad path completes a ten-minute
device run.

### IOS-P2-003: restore visibility-query optimization

**Priority:** P2.

**Evidence level:** disabled iOS path source-proven; ES query benefit untested.

Implement the ES-compatible boolean query path only after startup-world
validation is closed.

**Acceptance:** no invalid GL calls or visual regression and a measured CPU/GPU
benefit on the scripted path.

### IOS-P2-006: define the iOS texture color-space fallback

**Priority:** P2; local decoder contract complete, visual decision pending.

**Evidence level:** codec, GLI mapping and present upload behavior are proven
locally; visual/color correctness remains unmeasured.

The fallback now has one bounds-checked BC1-BC5 decoder, one GLI format mapping
and one block-size policy. Exact synthetic cases, overflow/truncation failures
and five real DDS/KTX fixtures pass strict and ASan/UBSan tests. The runtime
still intentionally expands sRGB variants to ordinary `GL_RGBA8`, ignores GLI
source swizzles, maps BC4 to `RRR1` and BC5 to `RG01`; this slice changed no
rendered behavior.

**Acceptance:** representative sky, environment and albedo textures document
source format, upload format, swizzle and shader-space expectation; numeric
probes and iPhone reference frames justify either preserving that policy or
switching to `GL_SRGB8_ALPHA8`/explicit swizzles with an intentional bounded
result.

## M7 — renderer decision gate

**Priority:** deferred; it must not displace P0/P1 reliability work.

**Evidence level:** native ES correctness and current baseline proven; ANGLE
compatibility and native Metal performance remain unproven estimates.

Do not start ANGLE or native Metal as a workaround for the resolved sector or
SSAO defects. Revisit the renderer only after:

1. IOS-P0-003 is complete.
2. Lifecycle and memory telemetry are trustworthy.
3. A repeatable ES performance baseline exists.
4. Remaining failures are classified as engine-level or backend-level.

| Option | Choose when |
|---|---|
| Keep ES | Correctness is complete and performance meets the target |
| ANGLE-on-Metal | ES API/driver behavior dominates and an iOS prototype proves compatibility |
| Native Metal | Long-term performance/tooling justifies the estimated implementation cost |

The native proposal is maintained in
[iOS-Metal-Plan-Draft.md](iOS-Metal-Plan-Draft.md).

**Acceptance:** a recorded decision compares ES, an iOS ANGLE prototype and the
native-Metal estimate using the same scene, correctness checks, CPU/GPU frame
times, memory and tooling evidence.
