# OpenXRay — iOS port

Canonical specification for the OpenXRay iOS project.

**Last synchronized:** 2026-08-13

**Status:** playable development build; affected-iPhone startup-sector, known
SSAO and stationary Simulator startup-sector defects fixed; the later
movement-correlated dark-frame observation remains under investigation

**Target game:** S.T.A.L.K.E.R.: Call of Pripyat 1.6.02

**Product scope:** iOS-only. Desktop compatibility is not a release requirement;
implementation choices may deliberately favor iPhone behavior.

This is a fan project. The application does not bundle retail game data; the
tester must own and provide a legal copy of the game assets.

## Current product state

The port is past initial bring-up. On a physical iPhone it can:

- build and link the complete arm64 engine;
- launch through SDL/UIKit;
- render the main menu and a 3D level through native OpenGL ES 3.0;
- compile all 279 shader stages and link all 137 known stage pairs;
- load retail Call of Pripyat data from the app's Documents container;
- play with an MFi/Bluetooth controller;
- navigate pointer-driven UI with one-finger touch;
- play audio with iOS interruption handling;
- save and load games;
- install and launch over USB in either a normal zero-readback mode or an
  explicit unattended diagnostic mode with synthetic input and fresh captures.

It is not release-ready. The startup geometry defect, stationary startup-sector
branch and proven SSAO macro defect are fixed, but a later dark-frame smoke
remains causally unresolved. The isolated iOS 27 Simulator capture-v2 path has
published a fresh hardened normal F5/F9 QuickLoad packet after a stationary
saved-game load. IOS-P0-003 remains open for wider physical-device and content
coverage.
IOS-P1-006 now additionally has two independent native 1864x860 Simulator UI
capture packets with semantic navigation and post-stop manifests; these are
file-level Simulator visual evidence only and do not replace the remaining
iPhone surface/readability/color/performance tests.
Diagnostic lifecycle recovery, LOWMEMORY handling and
decoded-texture lazy reload are device-proven; normal lock/audio cycles,
multi-level validation, memory budgets and a repeatable performance baseline
remain.

## Supported baseline

| Area | Contract |
|---|---|
| OS | iOS 16.4 or newer |
| Architecture | arm64 |
| Game | Call of Pripyat 1.6.02 |
| Renderer | Native OpenGL ES 3.0 through Apple's GL-on-Metal implementation |
| Shaders | Runtime GLSL ES 3.00; offline gate 279/279 compile, 2/2 low-settings, 6/6 SSAO branches, 30/30 numeric-macro mutations and 137/137 link |
| Lua | LuaJIT interpreter mode; JIT disabled by default |
| Build host | Apple M3 Pro, macOS 27.0 beta (26A5388g), Xcode 27.0 beta (27A5228h), iPhoneOS/iPhoneSimulator SDK 27.0, CMake 4.4.0 |
| Device install | Apple Development signing and cable install |
| Developer team | Apple Developer Program Individual, Team ID `RMJWWPF379` |
| Primary controls | MFi/Bluetooth controller |
| Touch | Menu pointer/tap; virtual gameplay controls are incomplete |

## Presentation baseline

- The SDL/UIKit window and input space remain 932×430 logical points.
- The SDL OpenGL view uses a 2.0 content scale, giving a real 1864×860 EAGL
  drawable on the current iPhone.
- The engine-owned render targets are also 1864×860, so `Present()` copies
  1864×860 to 1864×860. There is no final spatial upscale or downsample.
- `r__supersample` is no longer repurposed as the iOS resolution selector and
  remains at its normal value of 1.
- UI still uses a 1024×768 logical coordinate system. The Options dialog has a
  dedicated iOS layout so its scale, typography and contrast can evolve without
  changing gameplay HUD, inventory or PDA sizing.
- The current iOS Options layout is 25% larger than the legacy widescreen
  panel, uses `letterica18` body text, `letterica25` tabs/actions and brighter
  enabled/disabled states. It was accepted from an on-device 1864×860 capture.
- The Video tab is intentionally product-level rather than a desktop tuning
  panel. It exposes only Graphics Profile, a read-only native render
  resolution, gamma, contrast and brightness. Renderer, window mode, the
  legacy five-level preset and Advanced graphics controls are hidden.
- The shared Advanced Lua initializer still constructs hidden controls. Its
  `rs_always_active` compatibility node exists in the iOS XML but is deliberately
  unbound, so Options initializes without exposing or changing desktop lifecycle
  policy. All four tabs are device-proven at 1864×860.
- The resolution row reads `Device.dwWidth`/`Device.dwHeight`, so it reports
  the actual 1864×860 renderer dimensions instead of the 932×430 UIKit point
  size. It cannot write `vid_mode` and therefore cannot accidentally request a
  second 2x scale.
- The iOS main-menu builder filters both known retail Multiplayer button names
  before creating menu items. This applies even when the device container
  supplies a different retail `ui_mm_main.xml` from the repository fallback.
- Key-binding values now use `letterica18`; controller-binding conflict updates
  stay in the controller group instead of incorrectly notifying keyboard rows.
- The controller focus frame is a stable gold affordance. The earlier flashing
  cyan/yellow frame, bracket markers and per-input diagnostic logging are gone.
- Controller focus now scrolls an off-screen inventory cell into view using a
  stable content-space integer interval; fractional or over-height cells align
  to a deterministic top-edge fixed point instead of oscillating. The gold
  frame intersects inventory/list parents with the exact indented clip used by
  `CUIScrollView::Draw`. Strict and ASan/UBSan policy tests, 32 static fixtures
  total (1 positive baseline + 31 negative mutations),
  FastDevice and the full Release gate pass. A dense overfilled-inventory visual
  remains device-pending. The ordinary inventory plus core PDA/map surfaces are
  readable on device. The former `ui_pda2_fr_*` and
  `ui_ingame2_pda_buttons_background_e` messages were false topology probes:
  the UI created both nine-slice and authored three-slice/static alternatives.
  Conditional fallback ordering removes those warnings locally; the
  faction-war page itself still needs a device visual pass.
- The iOS/autoinput-only semantic-state marker is emitted after
  `DoRenderDialogs()` only when the observed semantic state actually changes;
  each record carries the exact PID, sequence, frame and state. CoP retail has
  no standalone `eptMap` tab: `eptTasks` is its combined Tasks/Map surface.
  `Show_MapWnd(true)` prefers a standalone `eptMap` where another topology
  provides one, otherwise selects and synchronizes `eptTasks`; `false` or
  neither available surface is a no-op. The isolated Simulator navigation
  evidence is deliberately `semantic-ui-navigation-only`, not pixel,
  readability, performance or physical-device evidence.
- The opt-in `--ui-captures` extension requires `--ui-navigation`, iOS 27,
  diagnostics and autoinput. It captures semantic steps 1 `inventory`, 3
  `pda_tasks`, 4 `other`/Stats and 6 `pda_tasks` of the expected
  `inventory, world, pda_tasks, other, world, pda_tasks, world` sequence.
  It accepts only stable metadata/PPM pairs bound to the exact session, PID,
  token, frame, dimensions and input order; the PPM is authoritative and its
  PNG is an exact derivative. The manifest/report publish only after process
  stop and revalidation. Its live runtime-log reader allows monotonic append
  but fails closed on symlink/nonregular input, inode change, shrink, rewrite,
  truncation or a read race.
- Two isolated iOS 27 Apple Software Renderer runs passed all 7 transitions and
  all 4 native 1864x860 captures with protected inputs unchanged and deleted
  dedicated Simulators. The main chat visually inspected their eight PNGs:
  complete inventory, area map/tasks, Stats and area map/tasks. This proves
  native file-level Simulator presentation at those surfaces, not iPhone,
  readability, color or performance. Focused host tests are capture 15/15,
  navigation 34/34 and retail isolation 92/92. Final Sol xhigh verdict is
  `APPROVE — brak P0/P1/P2`.

## Graphics profiles

The native 1864×860 renderer resolution and shader/resource topology remain
fixed in all three profiles:

| Profile | Frame target | Initial runtime tier |
|---|---:|---|
| Performance | 60 FPS | visibility 0.75, geometry LOD 0.50, local-shadow quality 0.50, shadowed-light fade 0.35 |
| Optimal | 30 FPS | balanced 0.90 / 0.75 / 0.75 / 0.425, then adaptive |
| Quality | 30 FPS | full 1.00 / 1.00 / 1.00 / 0.50 |

`Optimal` measures CPU plus render work before the frame limiter sleeps with
the unscaled monotonic iOS clock in nanoseconds, preserving sub-millisecond
samples independently from gameplay time dilation. It uses two-second p90
windows, lowers one tier after two windows above 31 ms (or one above 40 ms),
and raises one tier only after eight windows below 24 ms. A gap above 250 ms
restarts the five-second warmup; asymmetric 8/20-second cooldowns prevent menu,
loading and short hitch transients from causing oscillation. Low Power Mode or
`serious`/`critical` thermal state forces the performance tier at 30 FPS;
`fair` thermal state caps adaptation at balanced.

Upgrade evidence is valid only when every admitted sample in its complete
two-second window was eligible for an upgrade. Lifting a thermal or power cap
therefore requires eight fresh fast windows; mixed blocked/unblocked windows
cannot leak prior headroom into an immediate upgrade. Blocked windows still
contribute to slow-window downgrades.

Runtime adaptation is deliberately limited to `rs_vis_distance`,
`r__geometry_lod`, `r2_ls_squality` and `r2_slight_fade`. It does not change
resolution, textures, SSAO, shader macros, render-target layout, MSAA, VSync or
post-processing effects. The deterministic policy gate covers exact thresholds,
complete tier traversal, cooldowns, warmup/gaps, upgrade constraints,
30/60/120 Hz and uneven cadence; strict warnings plus ASan/UBSan and arm64
device builds pass. All three profile selections, their initial
tier/target logs, the simplified Options presentation and correct world frames
are device-proven. Five diagnostic foreground cycles retained the 1864×860
drawable. Low Power/thermal constraints are evaluated even when diagnostics
suspend p90 adaptation; a device run proved the balanced-to-performance change
under `serious thermal`. Measured downgrade/upgrade thresholds, normal-mode
frame pacing and the final tier values still require a repeatable gameplay
baseline.

After a successful `cfg_load`, the selected iOS profile is synchronously
reapplied before the command returns, so a following `vid_restart` cannot use
legacy desktop values. A pre-frame reassert remains as a defensive fallback.
This ordering is contract- and build-proven; device behavior after Options
discard/reload remains pending.

## Renderer decision

Native OpenGL ES remains the reference backend until the game is stable and
measured. It already renders the menu and world, so it provides the cheapest path
to diagnosing engine-level defects.

ANGLE-on-Metal would replace Apple's GL implementation but would not change the
engine's sector detection, resource policy or memory model. A native Metal
backend would be a larger renderer project. The former missing-geometry blocker
was fixed in shared renderer logic and did not require either migration.

The deferred Metal proposal is documented in
[iOS-Metal-Plan-Draft.md](iOS-Metal-Plan-Draft.md). Its start gate is retained
in the deferred backlog and must be promoted explicitly before implementation.

## Resolved blocker: startup world visibility

### Proven on device

- At the affected save position `(256.24, 21.47, 550.82)`, the legacy camera
  sector detector's straight down/up collision rays return no triangle.
- `last_sector_id` therefore remains invalid and the main draw-graph exits before
  static geometry, producing the misleading sky/HUD plus white/black terrain
  frame.
- A vertical probe 8 m away at `(256.24, 21.47, 558.82)` returns outdoor sector
  115. Once any later movement found that sector, the cached sector remained
  valid, which created the false appearance of geometry streaming in.
- The transition did not require a portal or sector crossing. Static level
  buffers, visuals, sectors and portals were already loaded synchronously.
- The fix corrects the camera-change test to compare saved and current position,
  then uses an iOS-only nearest-floor probe only when the exact vertical query
  fails.
- Three cold launches of the affected save rendered the full world without
  input. The fallback ran once per launch, sector 115 was active by frame 34,
  and the stable main pass accepted 543 static visuals in the diagnostic build.
- Controlled setting changes correctly exonerated 1×/2× internal resolution,
  VSync, geometry LOD, visibility distance, G-buffer optimization, texture
  quality/aniso, the requested MSAA setting, sun shafts, volumetrics, wet
  surfaces and reflections for the missing-geometry defect. The GL renderer
  currently forces MSAA off, so that particular console setting was inert. The
  old “SSAO off” result was later proven invalid by the separate macro bug
  described below.

### Local regression contract

The iOS fallback is now a pure host-tested policy. It preserves the exact query
fast path, the seven radii and eight directions in their original order, the
diagonal factor, unchanged camera height and first-hit exit. A dedicated
`level_load` camera-generation barrier permits one stationary exact/fallback
attempt only after authoritative camera application, with invalid sector,
awaiting evidence and no pending report. Real movement remains unconditional;
QuickLoad retains its separate barrier and no-detection semantics. A separate
commit policy proves that an invalid result cannot notify or replace
`last_sector_id`. Strict C++ policy, ASan/UBSan and the source-marker contract
pass. This freezes the mechanism but does not replace the remaining outdoor,
indoor/portal and transition device tests.

### Startup-sector v1 evidence oracle

The iOS startup-sector v1 evidence state covers `level_load` and `QuickLoad`
epochs and classifies every terminal outcome as `exact`, `fallback`, `retained`,
`unresolved` or `recovered`. It is two-phase: `Prepare`, fully validate/build
the payload, `CommitPrepared`, `Msg`, then `FlushLog`; an invalid payload stays
pending for retry and does not consume state.

For `QuickLoad`, `retained`/`none` may be emitted only after a post-epoch
`CCameraManager::ApplyDevice` generation. `retained` means only that the prior
valid `last_sector_id` was used after an authoritative camera application; it
does not assert fresh sector detection or that the new save/location is correct.

The CLI requires `--expected-pid`, `--after-epoch` and repeated
`--expect-trigger`. It enforces an exact anchored epoch suffix, count and order,
and rejects a missing final outcome, gaps, returns, extras, wrong trigger,
malformed near-prefix, impossible fallback geometry and unsafe/symlink I/O. Its
new output uses `O_NOFOLLOW|O_EXCL`, mode `0600`, never overwrites or deletes a
different path, and a failed write may leave only its own fresh partial output
fail-closed. A JSON `PASS` means the expected marker stream is structurally
complete only: `classification=unresolved` is a failed device startup test, and
even `exact`/`fallback`/`retained` markers are sector evidence rather than
visual or pixel proof.

Host evidence for this oracle is parser 17/17, source-marker contract 10/10,
strict C++ policy PASS and ASan/UBSan PASS. The independent implementation
re-review by Sol xhigh returned exactly `APPROVE — brak P0/P1/P2`. The partial
engine gate rebuilt 1,383 translation units; its full dSYM `__debug_info` is
513539650 and its UUID is `8D623468-0A50-3055-9516-3EA703818076` (log:
`/Users/patryk/openxray-handoff/local-gates/ios-p0-003-engine-20260809.log`).

### Remaining validation

The affected save is fixed across three launches. Before each future short phone
batch, record its baseline completed epoch, then require the exact expected
oracle batch. Separately require a resolved classification
(`exact`/`fallback`/`retained`, never `unresolved`) and an objective world frame.
Repeat fresh loads across another outdoor save, an indoor/portal start, QuickLoad
and a level transition before closing release validation. Full prefetch remains
disabled because it previously added an approximately 1.6 GB transient spike.

## Resolved blocker: global ambient lighting

### Proven on device

- After static geometry was restored, terrain and vegetation still appeared
  nearly black except for a small correctly lit area around the player.
- The environment cubemaps, material LUT, G-buffer hemisphere, weather constants
  and `L_ambient` were present. `hmodel` produced a strong hemispheric result
  before SSAO.
- The SSAO factor `occ` reduced that result by about six times across most of the
  world. Dynamic/local light in the accumulator bypasses that multiplication,
  which exactly explains the bright local circle.
- `common.h` defines ES feature macros to `0` because GLSL ES rejects undefined
  macros in numeric `#if` expressions. `ssao.ps` still used presence tests:
  `#ifndef SSAO_QUALITY` and `#ifndef SSAO_OPT_DATA`.
- Consequently, `SSAO_OPT_DATA=0` selected the half-depth path and sampled an
  `s_half_depth` buffer that the CPU had not generated. `SSAO_QUALITY=0` also
  failed to select the disabled stub, so the earlier settings test did not
  actually turn SSAO off.
- Replacing the two presence tests with value tests restored correct `occ` and
  global lighting with both packed and unpacked G-buffers.

### Final validation

- Full gate: 279/279 compile, 2/2 low-settings, 6/6 SSAO branches plus
  value-macro contract, 137/137 links and arm64 engine build.
- Normal, non-diagnostic build: correct global lighting before and after a
  nine-second scripted walk.
- Two additional cold launches reproduced the correct frame.
- Final shader/resource baseline retains SSAO high, G-buffer optimization on
  and full texture quality; no brightness multiplier workaround is required.
  `r3_msaa 2x` may remain in an inherited config, but the current GL path forces
  `o.msaa=false`, so MSAA is not an active or advertised profile feature.

A later diagnostic smoke on the preceding stamped build-10045 artifact opened
a separate end-to-end visual question: its initial gameplay frame was dark
across terrain and vegetation, and a frame after an acknowledged 12-second
forward request showed lighter nearby terrain while distant vegetation remained
dark. Camera position, game time and weather were not held constant, and there
was no stationary control. This observation neither invalidates the proven SSAO
macro mechanism nor proves a movement/streaming cause; controlled device A/B
evidence remains required before classifying it.

Capture-state v2 now prepares that controlled run without claiming its result.
Each opt-in diagnostic frame freezes one canonical JSON snapshot of capture
identity, scene, camera, level/epoch/sector, environment and diagnostic input,
then binds it to the PPM and converted PNG with one process-session/sequence
token. The producer drains and reports pre-existing GL errors, verifies the read
framebuffer, zeroes the destination and publishes neither image nor sidecar if
`glReadPixels` fails. Host reads require byte-identical metadata-before/after
and an exact matching image token; evidence files and reports never overwrite.

`lighting_ab_capture.sh` uses one exact-token lease, renews it before each later
device phase and requests A, stationary B=`A+3`, then a UUID-correlated 12 s W
hold and C=`B+3`. The offline verifier requires one PID/session/level/epoch,
equal 15 s arms, stationary A-B, forward B-C, stable camera direction/FOV,
comparable game time/weather and no active or intervening A-B diagnostic input.
Position-modified `ambient.rgb` and `hemi.rgb` are gated against stationary A-B
evolution; nonlinear `hemi.a`, sun color and sun direction remain explicitly
reported as non-gating observations. `READY_FOR_DEVICE_VISUAL_COMPARISON` means
only that correlation controls passed; pixel correctness and a movement,
streaming or lighting cause remain device/manual-review pending.

### Isolated Simulator capture-v2 checkpoint

The opt-in iOS 27 Simulator single-capture path is implemented in exactly five
host files: `retail_simulator.sh`, `retail_simulator_guard.py`,
`lighting_ab_evidence.py`, `test_retail_simulator.py` and
`test_lighting_ab_evidence.py`. `--capture-v2` requires saved-game autoload,
conflicts with UI navigation and leaves ordinary modes unchanged. Normal launch
uses diagnostics/autoinput `0/0`, UI navigation `0/1`, and capture-v2 `1/0`.
The runtime guard owns `saved_game_sync_complete`, the exact live PID and one
original launch deadline; the evidence parser remains the sole owner of secure
capture grammar, snapshots and publication.

The temporal contract is T0, the stable metadata watermark at synchronization
or an explicit null watermark; T1, the first stable sidecar newer than T0; and
T2, a stable metadata/PPM/metadata pair newer than T1 in the same session and
PID. T2 requires advancing sequence, frame and continual time, unpaused Zaton
gameplay at 1864x860, period 5000, epoch at least one, populated world and
environment, and exactly no diagnostic input. Valid live transitional states
(`loading`, paused gameplay, or gameplay without environment) retry with CLI
75. Stable schema or invariant violations fail with CLI 1, and the stopped-
runtime verifier escalates every retry classification to fatal. Publication is
new-only and proof-last. The successful set under `work_root/capture-v2` is
exactly `boundary-watermark.json`, `baseline.json`, `capture.json`,
`capture.ppm` and `capture-proof.json`; final reporting occurs only after
Simulator deletion and complete post-stop evidence, log, save, staged and
protected-input guards.

The host implementation is locally reviewed: final focused static checks pass,
parser tests are 31/31, runner tests are 84/84, and the final Sol xhigh verdict
is exactly `APPROVE — brak P0/P1/P2`. This is tooling acceptance, not a
successful real capture publication.

The first isolated run,
`/Users/patryk/openxray-handoff/simulator-work-20260810-015703-14748`, reached
the saved-game boundary with T0=`loading` sequence 24 and T1=`loading` sequence
25. Its first stable post-T1 candidate, sequence 26, was also `loading` and
exposed an over-strict fatal readiness classification. The correction above
makes only valid live readiness states retryable while preserving fatal
invariants and fatal post-stop verification.

The second real run,
`/Users/patryk/openxray-handoff/simulator-work-20260810-024814-86404`, exposed
the stationary branch: equal saved/current camera positions produced
`level_load unresolved/none`, invalid sector and no T2 before the 600-second
deadline. It failed closed and deleted its dedicated Simulator.

After the dedicated one-shot `level_load` camera barrier correction, fresh
workroot `/Users/patryk/openxray-handoff/simulator-work-20260810-041339-82601`
passed. PID 90549, epoch 1, frame 35 recorded `level_load resolved/exact`, sector
115 and radius 0 without movement. T0/T1/T2 tokens 54/55/56 retained one
session/PID, advanced frame 89/91/92 and continual time, and published unpaused
Zaton gameplay at 1864x860 with no input. All post-stop guards passed,
protected inputs were unchanged, the dedicated Simulator was deleted, and the
five capture artifacts plus final report were published. The evidence scope is
exactly `iOS-27.0-Simulator-Apple-Software-Renderer-only`: it proves the
control-flow and publication contract, not pixel correctness, readability,
physical-iPhone behavior, performance or a lighting cause.

The static shader contract now covers all five numeric zero-default macros:
`SUN_QUALITY`, `SSR_QUALITY`, `SSAO_QUALITY`, `SSAO_OPT_DATA` and
`MSAA_SAMPLES`. Thirty mutation tests validate complete guarded zero fallbacks,
comments, multiline directives and the active SSAO quality-3 threshold; the
legacy debt is now zero presence tests and zero `#undef` directives. A separate
13-mutation CPU-to-shader contract is comment-aware and requires exactly one
allocation, downsample, water binding and SSR sample where applicable. It also
proves that `SSR_HALF_DEPTH` is emitted only for `requested &&
ssao_opt_data`; otherwise SSR samples `s_position` rather than a missing
half-depth target. The supported static profiles compile 279/279, low 2/2,
SSAO 6/6, SSR 9/9 and link 137/137. HBAO/HDAO remain forced false on OpenGL;
this is local contract evidence, not iPhone water/pixel or performance proof.

## Lifecycle model

SDL app-event watches only publish into an atomic inbox. The main thread drains
that inbox immediately after SDL pumps events, persists configuration before
deactivation, and uses an atomic frame permit so a pending background event and
a new rendered frame have a defined order. A skipped frame retains a 10 ms loop
backoff, so the background interval cannot busy-spin. Deactivation releases
active keyboard, mouse, controller, touch and diagnostic-hold state before
clearing queues, freezes both gameplay clocks and explicitly detaches EAGL.
Pending cable-input triggers are discarded at both lifecycle boundaries. A
synthetic hold retains its request UUID, and the level logs release only after
it reaches the current entity. A physical 30-second W request proved downstream
release, UUID-correlated cancellation and no stale suspended-trigger replay.
The desktop `rs_always_active` policy is hidden and ignored on iOS. Foreground
rendering resumes only after the primary context is restored successfully; a
failed final context check rolls activation callbacks back before retry.

The ordering and context restore passed four diagnostic device cycles on the
current build, retaining one PID, the 1864×860 drawable and correct frames. One
later cycle also passed after LOWMEMORY and walking. Normal lock/app-switch
cycles, a held-touch transition and an audio interruption remain required.

## Memory model

The source assets use desktop DXT/BC compression. On the active ES path,
unsupported compressed textures fall back to CPU decoding and RGBA8 upload. That
can consume four to eight times the compressed source size.

The iOS BC fallback now has one host-testable codec and one GLI format mapping.
Its current upload plan deliberately preserves deployed behavior: BC1/2/3
sources tagged sRGB still allocate `GL_RGBA8`, GLI source swizzles are ignored,
BC4 expands to `RRR1`, and BC5 expands to `RG01`. Strict and ASan/UBSan tests
cover exact synthetic blocks, malformed sizes and real GLI DDS/KTX fixtures.
This freezes the present contract; it does not decide that linear storage is
colorimetrically correct or replace the required reference-frame comparison.

Current local memory controls are:

- non-UI texture top mip is capped;
- full level prefetch is disabled;
- Mach `TASK_VM_INFO` logs current/peak physical footprint, resident memory and
  compressed memory;
- texture accounting follows uploaded storage, including complete decoded
  RGBA8 mip chains rather than compressed DXT source bytes;
- `SDL_APP_LOWMEMORY` requests an allocator trim and then, only at a foreground
  primary-context safe point, evicts at most 256 MiB of file-backed textures
  unused for at least 300 frames;
- `$user$`, video/sequence, render-target, ImGui and raw-GLuint alias sources
  remain pinned because they cannot be reconstructed safely by filename.

Pinning is an ownership property: a repeated `Load()` of an existing ordinary
file-backed surface preserves its unpinned state instead of promoting every
level texture to non-evictable storage.

The policy, accounting and lazy-reload contracts pass host and build checks. On
the iPhone 15 Pro Max, one real warning evicted 47 stale surfaces and 262,143 KiB
of uploaded texture storage. Current physical footprint fell from 3,262,723 to
2,998,051 KiB. Fresh PDA, world, inventory and post-walk frames remained
complete, followed by a successful foreground cycle. This proves bounded
recovery, not a 30-minute memory budget or every pinned-alias class.

A 20-second normal-mode Optimal Activity Monitor trace on the iPhone 15 Pro Max
measured a stable approximately 3.20 GB physical footprint, about 625 MB
resident memory and approximately 2.58 GB compressed memory at an idle camera.
This is a short diagnostic baseline, not a release budget or a 30-minute soak.
Repeated diagnostic launches later reached 3,251,907 K physical footprint while
iOS reported `serious thermal`; contemporaneous system reports identified
`xr_3da` as the largest process during VM-compressor thrashing. Diagnostic
readback must therefore remain excluded from performance conclusions.

The provisional post-load soak budgets are explicit and intentionally below
that pressure episode:

- iPhone 15 Pro Max baseline: at least 1,700 seconds of one-PID samples, no more
  than 3,328 MiB physical footprint and no more than 128 MiB first-to-last
  growth;
- future 6 GB lower-memory target: no more than 2,560 MiB physical footprint
  with the same continuity and growth limit. This is a product target, not
  device-proven support.

`activity_trace_summary.py` parses the exported Activity Monitor live table,
resolves xctrace's shared XML references and enforces sample coverage, maximum
adjacent gap, duration, PID, footprint and growth thresholds. It rejects
non-finite or negative timestamps/footprints, non-positive or fractional PIDs,
invalid references/schema and malformed rows. Twenty subprocess fixtures cover
sorting, warmup, continuity, thresholds, CPU sentinels and JSON/I/O exit classes;
the budgets remain provisional until the phone trace completes.

```bash
DEVICE_UDID=00008130-000564403E12001C
LOCK=/Users/patryk/.codex/device-coordination/ios-device-lock.sh
LEASE="$($LOCK acquire openxray 35)" || exit $?
export OPENXRAY_DEVICE_LEASE_TOKEN="$(sed -n 's/.*"token":"\([^"]*\)".*/\1/p' <<< "$LEASE")"
[ -n "$OPENXRAY_DEVICE_LEASE_TOKEN" ] || exit 1
trap '$LOCK release-token openxray "$OPENXRAY_DEVICE_LEASE_TOKEN" >/dev/null 2>&1 || true' EXIT
misc/ios/traverse.sh 1800 &
ROUTE_PID=$!
xcrun xctrace record --template 'Activity Monitor' --device "$DEVICE_UDID" \
  --all-processes --time-limit 30m --output /tmp/openxray-30m.trace --no-prompt
wait "$ROUTE_PID"
xcrun xctrace export --input /tmp/openxray-30m.trace \
  --xpath '/trace-toc/run[@number="1"]/data/table[@schema="activity-monitor-process-live"]' \
  --output /tmp/openxray-30m-live.xml
python3 misc/ios/activity_trace_summary.py /tmp/openxray-30m-live.xml \
  --process xr_3da --min-duration-seconds 1700 --min-samples 1500 \
  --max-sample-gap-seconds 5 --require-single-pid \
  --max-footprint-mib 3328 --max-growth-mib 128
```

For an unattended non-readback route, launch with `--autoinput` and run
`misc/ios/traverse.sh 1800` beside the trace. It alternates bounded forward
holds and short turns. Exact UUID acknowledgements prove that commands were
accepted, not that the actor moved; without a position/sector oracle this run is
memory/profile load evidence only and must not be called a proven traversal.

ASTC is a future optimization, not an implemented feature.

## Build and device loop

### Phase 0 test-feedback telemetry — complete

Phase 0A commit `f7b105b61` froze the deterministic test inventory: the
catalogue's exact IDs and hashes remain unchanged and the Phase 0 tooling count
is 24. The reviewed Phase 0B implementation is deliberately observational
and fail-open: it records signed private per-case/per-stage JSON with profile,
input hash, cache-hit and shard fields, while preserving the authoritative
OFF/ON command parity and exact selection/stage coverage. Its child raw sink is
isolated and unlinked; an adapter supplies the Python unittest records.

Phase 0 is complete on source/evidence commit
`41385b7edddc163fa6f53f11d3b0a638853eae3f`.
All 10 warm shaders runs passed with telemetry `COMPLETE`, unchanged before/after
source, zero errors/residue, 718 direct plus 433 cache-certified cases (1,151
covered) and 48 stages per run. Compile/link observations were cache-certificate
hits. Total time was 769.065 s p50 (sample median), 801.651 s p95 (nearest
rank), range 737.020–801.651 s. Mocked retail was 616.241 s p50, 643.948 s p95,
range 593.494–643.948 s; median per-run retail share was 80.320%. The private
mode-600 manifest is
`/Users/patryk/openxray-handoff/gate-logs/phase0-warm-baseline-41385b7ed.json`,
SHA-256 `6791a9b9970fe4689e3ec50f6167c2ced697d057e107c8ba629358d56813263f`.

This proves stable host telemetry and coverage, not a speedup, Simulator/device
runtime, rendering or streaming. Phase 1A's completed static partition is
recorded below; it does not yet change production gate selection.

### Phase 0 pushed full-gate artifact (historical)

Exact clean commit `727c51da6021230893bcf26cf6987c386764aca7` passed the full
gate in 826.999 s and then matched `origin/ios-port` after push. Receipt
`/Users/patryk/openxray-handoff/gate-logs/gate-1786622784219994000-30295-0.json`
binds identical before/after source, log SHA-256
`ac5cae5de462fb65d9104d6cd2e224f8d0016ec4015bf39363939eb95df894de`
and stamp SHA-256
`9ac7d8a1b80c63680d5105a8d3c1edff31a3f4ee3b90d9496ef052be04baabaf`.
Telemetry is `COMPLETE`: all 1,151 case and 57 stage records PASS with zero
errors. Retail passed 102/102 in 591.940 s; archive policy 131/131, uncached
shader compilation 279/279 plus profiles, links 137/137 and the 68-TU engine
build passed. The artifact source is
`d1872a0b1d96ede05986ce3c5d81917c86f8af258353c4d6cb044c6150d7dd92`,
UUID `8F350949-A69B-3A73-933E-EBC5414055E3`, bundle SHA-256
`232428958a1ca8d9082d5314d6e4e4af75b1d1f4d486f02a6e143d2583c23e0e`
and dSYM `__debug_info` 513,889,238 bytes. That receipt authorized and closed
the push of `727c51da` only. This documentation change creates a new HEAD-bound
artifact hash, so the receipt and install stamp are historical for any future
installation; a new matching device/full gate is required first. The artifact
was not installed or run and supplies no phone/runtime evidence.

### Phase 1A retail test profiles — complete

The explicit versioned manifest freezes all 102 existing retail test IDs against
byte/AST-identical methods and partitions them statically. The complete set is
102, SHA-256
`c46c5ac58a477b21a9217f5df477a2e42e9c4649060d184f38fddd6cd6786b36`;
guards are 53,
`c6179d1bdb509dad1ea994e64a26365b2c89074c640a18de1ebb618246905567`;
integration is 49,
`b0df68952a9f912cac236fa1725a46b170de1c0017b665dda1cf9f65cca5d9ee`;
the two smokes hash to
`0da7d9b2b29edc77225a7b8e618cab98ae6142d9305d6b30db02796993927beb`;
and `host-fast` is 55,
`c4b8fd6b853e964488c9edba9b7ca69aebb39e00e3ccf0f07f2aa0ec314a47ae`.
Profiles are `complete` (default), `host-fast` and `retail-integration`. The exact
smokes are `test_mocked_happy_path_isolated_and_deletes_only_own_uuid` and
`test_successful_path_rejects_delete_failure_and_never_prints_pass`.

The `shaders`, `device`, `full` and `fast` gate profiles still run the complete
102-case suite; `engine` retains its existing non-shader scope. The separate
cheap profile contract is mandatory and fails closed in all five gate profiles.
Selection and cache authority remain `NONE`, and input
mapping remains incomplete/false. Phase 1A adds no FastDevice profile switch,
fixture extraction, parallelism or cache planner.

Focused evidence passed: profile contract 5/5, feedback 24/24 and installer
14/14. One actual `host-fast` run passed 55/55 in 48.164 s (48.27 s wall); one
integration run passed 49/49 in 529.911 s (530.02 s wall). The unchanged full
suite passed 102/102 in the fast gate in 590.178 s. These are single local
measurements, not p50/p95 or speedup acceptance.

Fast receipt
`/Users/patryk/openxray-handoff/gate-logs/gate-1786626921163957000-41895-0.json`
passed in 767.549 s on unchanged dirty diff at HEAD
`14d8973e9f92a54d7d0a8191101c04a7beae1602`; log SHA-256 is
`59bdd8178ec05bedcf5017824cca62f66641eca2dce59ce0e61a29384da7bea4`.
Telemetry is `COMPLETE`: all 1,156 case and 58 stage records PASS, zero errors,
source before=after; the profile stage passed 5/5 in 0.862 s. This is not a
clean commit/full/push/install stamp. Sol medium P1 findings were corrected;
Sol xhigh code verdict: `APPROVE — brak P0/P1/P2`.

### Phase 1B fixture decomposition — complete

Phase 1B completes the behavior-preserving fixture decomposition and closes
optimization Phase 1. All 102 original `RetailSimulatorTests` IDs and method
bodies remain AST-identical. A versioned 96-record golden fixture freezes each
path, type, mode, bytes, SHA-256 and symlink target; the root-dependent
`fixture.pc` record is normalized before comparison. The component state machine
is fail-closed, terminal after failure and rejects dependency cycles or a later
`work_base` reassignment. All ten mock publications validate bytes, SHA-256,
type, mode and inode before runner use; `xcrun` and `lipo` have shared sole
writers. Guard dependencies are minimal, and no later component repairs a
mutated retail file, stamp or mock.

Persisted contracts run through the existing profile entrypoint and pass
13/13. Frozen profile counts and hashes remain unchanged: complete 102
(`c46c5ac58a477b21a9217f5df477a2e42e9c4649060d184f38fddd6cd6786b36`),
integration 49
(`b0df68952a9f912cac236fa1725a46b170de1c0017b665dda1cf9f65cca5d9ee`) and
`host-fast` 55
(`c4b8fd6b853e964488c9edba9b7ca69aebb39e00e3ccf0f07f2aa0ec314a47ae`).
Retail-integration passed 49/49 in 526.322 s (wrapper 526.437 s); complete
passed 102/102 in 564.422 s (wrapper 564.553 s). Twenty serial fresh-process
`host-fast` runs all passed 55/55: p50 47.472 s, nearest-rank p95 49.323 s,
min/max 46.472/49.519 s and mean 47.500 s.

Continuation evidence is
`/Users/patryk/openxray-handoff/phase1b-fixture-evidence-20260813-162948-continuation/final-continuation-evidence.json`,
SHA-256 `6c9030fd565814989739255385f0b03648eb32f03a6743566dac95cef5cbd05a`.
The integration source log is
`/Users/patryk/openxray-handoff/phase1b-fixture-evidence-20260813-161702/05-retail-integration.stderr`,
SHA-256 `563ca32fdfef9490a546d0dd1eb66e17d6e3e081a4125096452aa005efc45e91`:
its surrounding helper JSON incorrectly classified the run as FAIL because it
examined stdout although unittest writes its summary to stderr. The command
returned zero and that stderr records 49/49 OK; the duplicate was stopped and
is not acceptance evidence. Repository hashes before and after evidence are
identical. Sol xhigh verdict: `APPROVE — brak P0/P1/P2`.

This is host-only fixture and test-feedback evidence. It makes no claim about
production behavior, real Simulator, device, rendering, streaming, distribution
or FastDevice speed. No current commit, full gate or push exists. Phase 2 is
next: first an OS-level release shader-cache lock or immutable namespaces before
local concurrency, then explicit conservative profiles and an affected-test
planner. Full remains semantically complete and shaders remain uncached.

### Phase 1A pushed full-gate artifact

Exact clean commit `9aad5df0afab8b851c2e95173ec503133b2907a3` passed the full
gate in 818.681 s and then matched `origin/ios-port` after push. Receipt
`/Users/patryk/openxray-handoff/gate-logs/gate-1786629614658776000-66873-0.json`
binds identical clean before/after source and log SHA-256
`ff26190a4be551835c2d58a402e7fd88f84fee66ad604cef875a655f41c76029`.
Telemetry is `COMPLETE`: all 1,156 case and 58 stage records PASS with zero
errors; the profile contract passed 5/5 and the complete retail suite passed
102/102 in 593.365 s. Shader cache was forced off.

The symbol-enabled artifact passed the dSYM size/UUID contract and has source SHA-256
`76acc2b68d12177f0e33377935122c259e53a6ed0428eb7126bcc9c80e24196e`,
UUID `6DFBE2E2-2958-30E6-9F8A-ABCE2BC7FCB2`, bundle SHA-256
`cf6351536336badac895a6fe0eeeb5a7dd52f5298988a23d85127e13866af3bf`,
minimum iOS 16.4 and stamp SHA-256
`8207bf92e3d4bb1fd2946c97b453cc8649b438577cbbf4721501f189e3dd30a5`.
This receipt authorized and closed only the push of `9aad5df0`; the following
documentation commit changes the HEAD-bound artifact hash, so it is historical
for any future installation and a fresh matching device/full gate is required
first. No phone, Simulator/device runtime or rendering evidence was produced.

The local Apple Silicon toolchain was rebuilt and revalidated from source on
2026-07-24, then requalified after the macOS 27/Xcode 27 update. Both device and
simulator dependency prefixes contain arm64 SDL2, OpenAL Soft,
Ogg/Vorbis/Theora and LZO archives. The complete device and simulator engines
build against SDK 27.0 for an actual iOS 16.4 deployment target. LuaJIT's host
generators are macOS arm64 tools, while its target archives are iOS arm64.
Non-interactive gates prefer `/opt/homebrew/bin` so a migrated Intel Homebrew
installation cannot silently select x86_64 tools.

The isolated iOS 26.5 arm64 Simulator workflow snapshots the dirty source tree
and dependency prefix outside the repository, validates an external retail
backup, builds an `IOSSIMULATOR`/minOS 16.4 app, stages only the allowlisted
retail directories and optionally saved games, then deletes its dedicated
Simulator. Its earlier corrected menu run verified 789 backup files totalling
4,761,053,330 bytes, all seven required archive hashes, 12 mounted archives,
`system.ltx`, a later `Starting engine...`, a live process through the
post-screenshot stability interval and a fully textured main menu. The first
run exposed a harness-only `SIGTERM` from `simctl launch --console`; non-console
stdout/stderr redirection plus exact-PID liveness checks removed that artificial
`FATAL` from the corrected evidence.

The later saved-game autoload run found and corrected a real Simulator-only
path-length failure in `CLocatorAPI::Register`: `string256` could not hold a
256-byte path plus its NUL terminator, `xr_strcpy_s` returned `ERANGE` and
cleared the buffer, so long archive entries collapsed to the empty lookup key.
The physical-app root is shorter, which had hidden the defect. `string_path`,
an explicit nonempty-name check and checked copy/fail-fast handling now serve
both Apple targets without a Simulator branch. The mutation-backed Locator
contract passes 8/8 and the hardened retail-isolation workflow contract passes
84/84.

The retail guard now mandatorily parses `large-files-sha256.tsv`, checks every
entry's size and SHA-256 against `files.tsv`, and protects every allowlisted
large file during preflight, staging and post-runtime verification. The selected
mutable large save is the sole exception and must remain a nonempty regular
file. The final log oracle is deliberately two-stage: `launch-proof` writes a
fresh snapshot manifest but no runtime proof; only after successful
`simctl terminate` does `finalize-log`, without resolving the path, verify
`lstat`/`O_NOFOLLOW`/`fstat`-read-`fstat`-`lstat`, inode identity, captured-prefix
size and SHA-256, rotation, truncation, rewrite, symlink substitution, the full
success sequence and failure markers. Only that final step may write
`runtime-proof.txt` and `report.txt`.

The positive delayed-lifecycle fixture is scheduler-independent: an isolated
mock delegates the exact liveness query to `/bin/ps`, then advances separate
`deactivate` and `activate` payloads through `pending -> armed -> released`
between guard snapshots. It cannot publish a marker for a dead or mismatched
PID. Fifty sequential and 200 eight-way parallel workflows pass, while the
production stable-read, rotation, truncation, rewrite and symlink guards remain
byte-identical and their adversarial tests still fail closed.

The opt-in iOS 27 `--quickload-evidence` path exercises normal engine input and
save/load dispatch, not a test-only load shortcut. Its isolated config binds F5
to QuickSave and F9 to QuickLoad. iOS maps those keys to SDL scancodes 62/66;
the evidence oracle binds unique request UUIDs, acknowledgements, press/release,
exact logical `Player - quicksave` messages, LocatorAPI's lowercase physical
`player - quicksave.scop`, one unchanged PID, an anchored `quick_load` epoch and
three native 1864x860 gameplay captures. It revalidates the live save, an
inode-bound private copy, original staged saves, captures, logs and protected
inputs after process termination. `report.txt` is the sole PASS artifact and
is hard-linked last after dedicated-Simulator deletion; the evidence manifest
cannot claim PASS by itself. An empty post-sanitization iOS username now falls
back to `Player`, while every nonempty device username remains unchanged.

Fresh hardened workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-164020-71268` passed on
iOS 27 with PID 77340 after a clean single-slice arm64 `IOSSIMULATOR` build
(minOS 16.4, SDK 27.0). Epoch 1 was `level_load/exact` at frame 35; epoch 2 was
`quick_load/retained` by method `retained` at frame 122. Exactly B0/B1/C were
native 1864x860 gameplay captures at frames 89/94/182 and tokens 54/58/118;
normal F5/F9 dispatch used SDL scancodes 62/66. Live/private QuickSave copies
had distinct inodes 22482673/22482715, identical 649473-byte content and
SHA-256 `eb82993bc9eb70ef64ef7830669328470fb556da69a82883a41ffc9dda5c43fe`.
The original staged save remained 631235 bytes with SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`.
`report.txt` (SHA-256 `27f89f6793ec2d0f0ee6831add123ae3e54b9d83eaaa37d4326a62d052f35cb9`)
is PASS; its evidence manifest SHA-256 is
`7d45f49bbe15727a1813971b958fe8c19261b32cc3426a7bfd69dffbdd418896`.
Protected inputs were unchanged; pending aliases were absent; Simulator
`278AD2E8-7116-448F-8474-670945489415` was deleted before report publication.
Final Sol xhigh verdict: `APPROVE — brak P0/P1/P2`. This proves normal F5/F9
control flow on Apple Software Renderer only, not iPhone behavior, pixels,
readability, lighting, performance or other content.

The final Sol xhigh verdict for the Locator/autoload and semantic UI navigation
checkpoint is `APPROVE — brak P0/P1/P2`.

The final isolated iOS 26.5 Simulator run is
`/Users/patryk/openxray-handoff/simulator-work-20260809-013601-65931`: PASS,
with deleted Simulator `410EA3BC-23FC-4F4C-843D-CF4438704652`. PID `72114`
emitted the post-`DoRenderDialogs()` transition sequence `1/34/world`,
`2/92/inventory`, `3/94/world`, `4/96/pda_tasks`, `5/98/other`,
`6/100/world`, `7/102/pda_tasks`, `8/104/world`, proving controller input
`I, I, P, E, Escape, M, Escape` from the world baseline. The deliberate `E`
shows that `M` does not inherit the previous Tasks state. The run completed
synchronization in 337327 ms; `after_load` physical footprint was 3559173 K
and textures 2206009 K. The selected save remains 631235 bytes with SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`, protected
inputs are unchanged and cleanup completed. The post-binding JSON records
`final_log_sha256=638434c1b8b745d7d7002237eed356144b893e823ddbce28e571b8f34dba9d96`
and exact scope `semantic-ui-navigation-only`.

Its task-specific host contracts passed; historical artifact identifiers for
that semantic-navigation slice are retained in the append-only Journal. Its
pre-document stamp remains historical; the later Phase 0 pushed full-gate
artifact is recorded above and is likewise historical for future installation.

This establishes semantic navigation and saved-game synchronization only. It
does not prove pixels, readability, performance or physical-device behavior.
No phone, device lease, `devicectl`, signing, install, commit or push was used.

The current iOS audio contract intentionally selects the project-owned static
OpenAL Soft 1.25.2 archive from the exact device or Simulator dependency prefix,
not deprecated Apple OpenAL. CMake and the provider verifier require the exact
archive/header/framework contract; AudioToolbox, CoreFoundation and CoreAudio
must be strong dependencies. Apple OpenAL, dylibs, `-lopenal`, `-latomic`,
alternate or duplicate archives, forwarded linker spellings and response files
are rejected. The required runtime line reports vendor `OpenAL Community`,
renderer `OpenAL Soft`, version `1.1 ALSOFT 1.25.2` and extension, pause and
resume support all equal to `1`.

The interruption implementation records exactly the sound scenes that it
paused. Local strict and ASan/UBSan policy tests cover an empty begin, A
destroyed before B is created including ABA address reuse, a surviving scene,
an already-paused scene and duplicate begin/end. This proves the local mechanism
and provider/menu boundary only. It does not prove a physical iPhone audio
interruption; that remaining device action belongs to IOS-P1-002 and IOS-P2-002.

## Current UIKit scene lifecycle

iOS uses a repository-owned, hash-pinned and fail-closed SDL2 2.32.10 UIKit
scene-lifecycle backport. The app declares exactly one scene
(`UIApplicationSupportsMultipleScenes=false`); `SDLUIKitSceneDelegate` owns
the four foreground/background transitions; `SDL_main` starts exactly once;
and iOS 13+ window creation binds to a connected `UIWindowScene`.

The isolated retail oracle passes on both Simulator runtimes. iOS 26.5 work
`/Users/patryk/openxray-handoff/simulator-work-20260809-135908-18426` retained
PID `25991`, emitted lifecycle sequence `1 activate`, `2 deactivate`,
`3 activate`, reached one code-level menu frame, preserved protected inputs and
deleted its dedicated Simulator. iOS 27.0 work
`/Users/patryk/openxray-handoff/simulator-work-20260809-140608-26903` retained
PID `35981` with the same sequence and boundaries; cleanup and protected-input
checks also passed.

This proves UIKit/SDL bootstrap, the code-level rendered-menu boundary and one
same-PID foreground recovery on iOS 26.5 and 27.0 Simulators. It does not prove
pixels or readability, physical-device behavior, performance, audio
interruption or multi-cycle soak. Physical-device acceptance remains active
under IOS-P1-010. Related IOS-P1-002 acceptance is temporarily backlogged while
the phone is unavailable and requires explicit promotion before that device
batch.

The UIScene-local full gate rebuilt four translation units and passed its SDL
scene 7/7, lifecycle marker 10/10 and retail oracle 74/74 contracts alongside
the shader gates. It remains valid UIScene-local/Simulator evidence, but its
older stamp is stale and does not authorize an install or push.

### Latest pre-document code-artifact gates (historical)

The fresh final-code Fast gate passed with identical before/after state: private
log `/Users/patryk/openxray-handoff/gate-logs/gate-1786396379224210000-17575-0.log`
has SHA-256 `b232912151ffc77e82f2bebf9705e7ce01bf34e18b6e0494b53d4ac8f3c4d440`
and Fast UUID `0C929156-EBDD-336D-ACDB-B5084481271A`.

The latest pre-document full gate also passed with identical before/after state:
`/Users/patryk/openxray-handoff/gate-logs/gate-1786563440297027000-96172-0.log`
has SHA-256 `16f14682fce0287d842108347fbddc8f28b2708dba59eaebf2ff2242ba0b2894`.
It passes retail importer 21/21, retail Simulator 102/102, archive policy
125/125, numeric macros 30/30, resource contract 13/13, internally enforced
shader stages 279/279, low 2/2, SSAO 6/6, SSR 9/9 and links 137/137; 0 TUs. The arm64
target is `IOS`, minOS 16.4, cache forced off, OpenAL unchanged and dSYM is
513889238 bytes. The source, UUID, bundle and full-stamp SHA-256 values are
recorded below. No phone, lease, `devicectl`, Simulator, install, commit or
push occurred.

```text
source_sha256=f8727806510005248fb9b92e2c7075fd5c91f66a82af451adfe8baa2d37e587f
app_uuid=36095C34-3BB5-379F-8ECE-321F937A9043
bundle_sha256=e15e8300a4b1fe87dd4752417d77fcce16e783a6f04a34b32b2074cd5e889ff6
full_stamp_sha256=d9cfba1057e29a05fa10937c48da0c43bc7cb896a0b315af44634521d4cc0906
openal_provider=OpenALSoft-1.25.2-static
openal_sha256=86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914
platform=IOS
minos=16.4
shader_cache=forced-off
```

At that checkpoint the documentation changes invalidated this artifact's
install/push authorization and required a new post-commit full gate. It was not
installed on a phone; the later `727c51da` receipt recorded above is now also
historical for future installation.

Commit `a355fab834e0e4e1bd06128e65c9d0335306c463` then received a clean
post-commit full PASS in `gate-1786565992761388000-42873-0.log` (SHA-256
`37c3e0a6828eb1668facdfb4fc9c845c868b4953878387bfeac189f8a285dbc9`).
Before/after were the exact clean commit with no untracked files. The resulting
source was `b2bcf20b…b751e1`, UUID `122A97AB-39E3-31F3-86BD-3B795F208140`,
bundle `626c874e…08c4` and stamp SHA-256 `bf95fcfd…050658f`; established
retail, archive, shader and link contracts passed with zero rebuilt TUs.

That proof is deliberately historical after the subsequent verifier and
active-gate fixes. The production archive verifier continues to reject the
dirty-retirement/clean-commit source and stamp mismatch. A separate exact,
read-only historical verifier reports only `HISTORICAL_PASS` with
`mutation_authorization: NONE`; it cannot authorize sibling retirement,
recovery, publication, install or push. At that checkpoint a fresh post-commit
full gate remained required; commit `727c51da` later satisfied it for its own
completed push only.

Commit `8b265b07b489f1633e6be17f57fd1578adb11092` received its own clean full
PASS in `gate-1786570175187364000-15196-0.log` (SHA-256
`fd07567c73d2d4e2fbdd5a8c9178d40e0e6db008337e691bafa49332a5895e71`).
The 102-case retail Simulator stage took 656.256 s; archive 131/131 and all
established shader/link contracts passed, with 68 rebuilt TUs. Source was
`d8b2fc5d…f394`, UUID `574CACAC-E835-3E81-9919-60F8B93343DE`, bundle
`c6dbaa33…9a0c` and stamp SHA-256 `54c54c5c…b59a5`.

The first real read-only historical verification then failed closed because
the immutable prepared proof captured Finder's `.DS_Store`, while the reviewed
prepared cache later removed exactly that 6,148-byte metadata file to restore
the importer contract. The 13 selected retail files still pass
`retail_import.py verify`. The historical path now has an exact non-authorizing
metadata-overlay policy binding both complete manifests, the sole removed file,
root inode/metadata, unchanged records and the importer verifier. No generic
prepared/production path is relaxed. These overlay changes make the `8b265b07b`
gate historical. The overlay is committed in `ba2e07e05`; the historical-proof
inventory supplement is committed in `ab1ba7f45`. Commit `727c51da` later
satisfied the clean full-gate requirement for its completed push; its stamp is
historical for future installation after this documentation change. A real
`HISTORICAL_PASS` remains a separate read-only historical-verifier closeout.

### Historical code-artifact full-gate stamp (stale)

The prior clean post-commit `e967a4c36dfbecf13f3934fee1cf018d618dd776` stamp
(source `e0c841e7…3e8d0`, UUID `6F266276-A948-3D6F-838A-09E60F04BD11`, bundle
`53dd0d5c…175f39`) is historical and stale because its artifact inputs precede
the IOS-P1-007 local sub-slice. It must not authorize an install or push.

After commit `362bf625d59a5219857382afb8a5f23ccbf54fbe`, the final uncached
full gate rebuilt 68 translation units and passed the strict/sanitized BC
contract, retail 84/84, installer 13/13, local CI contract 79/79, numeric
macros 30/30, low 2/2, SSAO 6/6, shader contract 279/279 and links 137/137.
Platform is `IOS`, minOS 16.4 and shader cache is forced off. This historical
artifact was not installed on a phone:

```text
source_sha256=33c9efabda5cfdb99939440199b87331fac3ee8deb106383ca8fb6112a6e9c06
app_uuid=7297E9B6-1DBE-348D-8BAE-A153A19F781E
bundle_sha256=0e0ba58332a97b6d04fa458a50fd82006ebd6cecbe1f6f1182f8dabf453b6ff3
openal_provider=OpenALSoft-1.25.2-static
openal_sha256=86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914
__debug_info=513888155
```

Subsequent artifact-input changes moved the artifact hash after this recorded
build. Do not treat these historical values as a matching stamp; the current
matching artifact-input stamp is the one above.

The installer has local-only completion. `--device` resolves CLI over
environment over the fixed default. A custom absolute `.app`/stamp pair resolves
as an indivisible CLI pair over an indivisible environment pair; mixing,
incomplete pairs, IPA, positional payloads and `--fast` with a custom pair fail
closed. The Team ID `RMJWWPF379` and bundle identifier
`io.github.tryk016.openxray.RMJWWPF379` remain nonconfigurable.

`--preflight` verifies the existing profile and keychain identity, a private
descriptor-safe snapshot of the stamp and profile, source/UUID/bundle/OpenAL/
platform/minOS evidence, base/final identifier and entitlement invariants, then
signs and verifies a temporary app copy. It never renews provisioning, obtains a
lease or reaches `devicectl`. The hermetic mutation contract passed 13/13 and
proves that both the lease helper and timeout wrapper invalidate the stamp;
ShellCheck, `bash -n`, no-write Python compile and scoped `git diff --check`
passed. The corrected complete closeout received the independent Sol xhigh
verdict `APPROVE — brak P0/P1/P2`.

The main worker independently recomputed the then-current gate hash and it
exactly matched the historical stamp. A real no-device preflight passed with a
sentinel lock path still absent, a cleaned temporary work directory and
unchanged source-bundle hash. This is host/build/signing-preflight evidence
only: it adds no device install, container, runtime, visual or pixel proof.

Xcode 27's Metal toolchain is installed. A ten-second whole-device
`Metal System Trace` captured the running GLES-on-Metal application, exported
successfully and contained 47,766 GPU execution events. Direct `xctrace
--attach` selection did not resolve the process even though `devicectl` did;
whole-device capture is the proven path until that beta-tool issue is resolved.

From the repository root:

```bash
./misc/ios/build_fast_device.sh
./misc/ios/install_device.sh --fast --diagnostics
./misc/ios/input.sh w 9000
./misc/ios/shot.sh /tmp/openxray-frame.png
./misc/ios/install_device.sh --fast --autoinput
./misc/ios/held_input_lifecycle_test.sh
./misc/ios/install_device.sh --fast --launch
./misc/ios/build_check.sh --full
```

For a phone-free retail smoke test or saved-game synchronization run, pass an
external, manifest-verified backup to `misc/ios/retail_simulator.sh`. The
workflow creates a new work root outside the repository and never reuses it:

```bash
./misc/ios/retail_simulator.sh \
  --backup /absolute/path/to/device-retail-backup \
  --manifest /absolute/path/to/device-retail-backup.manifest \
  --with-saves \
  --autoload-save 'mobile user - beginning of the game'
```

### Resumable retail preparation (M6-local complete)

`misc/ios/retail_import.py` is a standalone, standard-library host importer.
It prepares only an already accessible, legally owned retail backup; it neither
copies from a phone nor establishes a distribution right. Its 21/21
mutation/recovery contract covers descriptor-relative source/output confinement,
an immutable all-file SHA-256 plan, byte-range prefix resume (including a real
600 MiB sparse fixture at an interior offset), nonblocking lock, private
permissions, `renameatx_np(RENAME_EXCL)`, fsync/recovery, exact final
verification and unchanged legacy-runner guard compatibility.

The reviewed full backup is archived read-only at
`/Volumes/DevArchive/OpenXRay/backups/5beca2ef4f034286be6299d6d9a2bdba-device-retail-backup-20260808-185559`;
its independent manifest is at
`/Volumes/DevArchive/OpenXRay/backups/8039f43384784b879442f993dab281ae-device-retail-backup-20260808-185559.manifest`.
Both archive transactions verify PASS. The former local sources were retired
through immutable receipts after descriptor-bound APFS/UUID verification; the
phone container was not touched. The one verified local prepared cache with saves is
`/Users/patryk/openxray-handoff/retail-prepared-20260810-211114`; it contains
only `Documents` and `manifest`, with 13 selected files and 4,611,922,289
prepared file bytes. Required `resources.db0`–`resources.db4` and
`levels.db0`–`levels.db1` are present. Its manifest digests are
`prepared-files.tsv`
`3dcd34c5934e1bc04b8dfb91873f48e82efe514f1aaf774ad685c09d2f1ae6c8` and
`prepared-manifest-files.tsv`
`3008f88f4b658bf0aa89b64b9701448a253ba91fddb9a4bebeb66d86afe66f18`. The
unchanged retail guard is PASS for its published `Documents` and `manifest`.

From the repository root, make a fresh prepared cache (the destination must be
a new external path), verify it, then feed its two published children to the
unchanged Simulator runner:

```bash
SOURCE=/Volumes/DevArchive/OpenXRay/backups/5beca2ef4f034286be6299d6d9a2bdba-device-retail-backup-20260808-185559
SOURCE_MANIFEST=/Volumes/DevArchive/OpenXRay/backups/8039f43384784b879442f993dab281ae-device-retail-backup-20260808-185559.manifest
PREPARED=/Users/patryk/openxray-handoff/retail-prepared-YYYYMMDD-HHMMSS
python3 misc/ios/retail_import.py prepare \
  --backup "$SOURCE" --manifest "$SOURCE_MANIFEST" --repo "$PWD" \
  --destination "$PREPARED" --with-saves
python3 misc/ios/retail_import.py verify --prepared "$PREPARED"
./misc/ios/retail_simulator.sh \
  --backup "$PREPARED/Documents" --manifest "$PREPARED/manifest" \
  --with-saves --autoload-save 'mobile user - beginning of the game'
```

This cache is resumable and verified, not APFS-clone/no-copy staging. Physical
iPhone builds and in-place installs do **not** recopy retail `Documents`: the
stable Team ID and bundle identifier retain the same app container, while the
Mach-O UUID is unrelated to that container. The isolated Simulator runner does
intentionally create and delete a fresh Simulator and stage data on every run,
so its evidence remains isolated rather than a physical-device transfer proof.

The archival checkpoint used the exact APFS `DevArchive` UUID and completed
eight reviewed transactions. Main retail transaction
`5beca2ef4f034286be6299d6d9a2bdba` is `RETIRED_TOMBSTONE` with 790/790 files,
tree `929ddd05bd7b6a33f2729074be2847c38d05b5f54e230d08e4f06b694af110ac`
and external deletion-receipt SHA-256
`bf032741b03cd30879037943fcffd673d6decb60da966d51df04ed88e4968304`.
Sibling transaction `8039f43384784b879442f993dab281ae` verifies tree
`1d9c432282d0bc0f3978bfbf1ba05b039c51b456ee1bfa355244600c84e118de`.
All eight retirements reclaimed 5,750,628,350 logical bytes locally; the active
prepared cache remains verifier PASS and is not archive cargo.

`build_fast_device.sh` is the normal iteration gate. It uses a separate
`build/ios-engine-fastdevice-iphoneos` tree and
`bin/aarch64/FastDevice/xr_3da.app`, retains Release `-O3/NDEBUG` semantics,
and deliberately omits LTO and the full dSYM. It never installs or launches.
The current local no-op iteration is approximately 5.1 seconds and produces a
75 MiB app instead of the symbol-complete Release app plus its 739 MiB dSYM.
Those timings are local measurements, not CI or phone-performance evidence.

`build_check.sh` remains the symbol-complete Release gate. Its default mode
uses content-addressed shader compile/link results and writes a device stamp;
`--full` forces all 279 compile stages and 137 link pairs to run and is required
before push. Shader compilation uses up to ten CPU/memory-bounded workers.
Both build paths synchronize `res/gamedata` and `res/fsgame.ltx` even when the
engine does not relink, then stamp the source hash, Mach-O UUID and complete app
bundle hash. The installer verifies all three, including the copied bundle,
before signing. A stale or modified app is rejected.

Every device-facing shell tool acquires the shared cross-project iPhone lease
immediately before its first device command and releases only a lease it owns.
A larger batch may reuse one lease by exporting the exact
`OPENXRAY_DEVICE_LEASE_TOKEN`; a missing or mismatched token fails before
`devicectl`. Device commands have bounded wall-clock timeouts and terminate
their process group on expiry, so a wedged Xcode service cannot consume an
unbounded lease. Generic-device builds remain phone-free.

The current Xcode 27 symbol-complete Release tree also has no active
`LLVM_LTO`/`-flto`: its existing IPO capability probe returns unsupported.
Do not attribute current iteration cost to thin-LTO without rechecking generated
build settings.

The five cable-diagnostic timing/hold/tap fields are file-local in
`xr_input.cpp`; ordinary touch state remains on `CInput`. The one-time layout
change rebuilt 1,383 units, while a control field added and removed inside the
file-local state rebuilt only `xr_input.cpp` each time. This relies on the
existing single `CInput` instance and is locally build-proven, not phone-proven.

Direct pre-launch injection into `Documents/gamedata` is not yet a valid fast
resource path: `CLocatorAPI` overwrites the engine-owned overlay from the app
bundle on every launch. A safe override requires a version marker, an explicit
restore path and device validation; until then, resource-only changes use
`sync_app_resources.sh` plus a normal FastDevice reinstall.

`--diagnostics` is the visual unattended-test mode. It writes
`ios_diagnostics 1` and `ios_autoinput 1`, enabling cable input polling plus the
five-second frame capture. `--autoinput` instead writes diagnostics off and only
the input gate on, allowing repeatable unattended camera paths without periodic
`glReadPixels`; this mode does not suppress graphics-profile measurement. The
capture cadence uses continual time, so it advances while Options pauses game
time. `shot.sh` accepts only a capture-v2 JSON/PPM pair with stable metadata and
the same process-session/sequence token, embeds that token in PNG output and
refuses stale, partial, overshot or existing evidence paths.
`input.sh tap x y` accepts logical 932×430 coordinates. The engine moves the
cursor first, then waits 500 ms before sending press/release so one command can
both establish UI focus and activate the target. Every request now carries a
UUID; the engine publishes an atomic ACK only after dispatching a key press or
completing the tap, and the host rejects stale/missing/mismatched responses.
The trigger is tombstoned before launch and removed again on activate/deactivate,
so a timed-out request cannot replay after foreground or relaunch.
`held_input_lifecycle_test.sh` backgrounds an accepted 30-second hold, injects a
second trigger while suspended and requires one continuous log to prove entity
release, UUID-correlated cancellation, foreground recovery and no stale replay.
Ordinary `--launch` writes both gates to zero; normal gameplay therefore does
not poll the trigger file and performs no periodic framebuffer readback. A
failure to read or write the configuration aborts launch instead of inheriting
an unknown mode.

`misc/ios/ui_automation/run.sh` generates the XCTest/XCUIAutomation project and
patches CMake's generated scheme idempotently from its buildable references.
Its default phone-free mode performs a clean, signed generic-device
`build-for-testing` and verifies the host and runner signatures. The runner is
serialized per build directory, refuses extra arguments and existing result
bundles, and accesses a physical device only under explicit `--test`. Xcode
27's test frameworks require iOS 17.0 for this test-only runner; OpenXRay and
its automation host retain the iOS 16.4 product baseline. First-device
authorization now passes, and three independent launches reached Video Options
without manual input; every attempt retained an xcresult before/after capture
and teardown left no OpenXRay process.

The first combined reliability attempt used Home and Siri state assumptions
that did not occur and is rejected as engine evidence. The corrected lifecycle
scenario performs five fail-fast Safari/OpenXRay switches. The audio scenario
runs a non-mixable `Playback` session and looping nonzero PCM from the XCTest
runner while OpenXRay remains foreground. Scoped oracles require five ordered
`deactivate -> activate -> 1864x860 drawable` groups and then a foreground
`wasSuspended=0` interruption pair with no intervening app deactivation;
complete `wasSuspended=1` suspension pairs are ignored. Twelve durable log
fixtures, twelve UI-contract fixtures, nested signing, AVFAudio linkage,
FastDevice and the full Release gate pass. These are local preparation only;
the corrected lifecycle/audio scenario still requires a physical-device rerun.

Expected gate values are:

```text
279/279 compile
2/2 low-settings profile
6/6 SSAO branch profile
SSAO value-macro contract: PASS
137/137 pairs clean
```

GitHub Actions runs this same strict shader-only wrapper with glslang 16.4.0
pinned to commit `168d452a4f460d24b588fed08477a81c44ee27a1` before it
may publish the unsigned development IPA and SideStore source. Only `ios-port`
may publish `ios-dev`; that publication job is serialized and never cancels an
in-flight two-asset update. It waits for smoke, engine, shader and LuaJIT jobs.
The device job also generates a full dSYM. Its debug settings are applied to
every compiled target, including static engine libraries, rather than only the
final app target. It rejects an empty `__debug_info` section or a UUID mismatch
and uploads the verified symbol archive beside the IPA. Because a target-only
dSYM can still be technically non-empty, both local and CI gates also enforce a
conservative 100 MiB `__debug_info` floor. The clean Xcode 27 requalification
passes that contract. Exact artifact sizes and UUIDs belong in the append-only
journal because they change on every link. GitHub Actions is no longer the
primary developer loop.

All 12 external action uses in the iOS workflow are pinned to reviewed commit
SHAs with release comments. Dependency prefixes use one monolithic exact cache
key over branch, runner, SDK/platform, iOS 16.4/bitcode/build/generator, seven
reviewed source inputs and a fail-closed toolchain fingerprint. There are no
restore keys; the dependency build skips only when `cache-hit` is exactly
`true`, while the seven-library arm64 verifier and artifact upload run on both
hit and miss paths. A stdlib-only contract retains semantic diagnostics and
binds the entire reviewed workflow as exact UTF-8 bytes. Its 79/79 mutation
suite, Python compile, `actionlint`, whitespace checks and extracted shell
smokes pass with independent Sol xhigh approval. This is local provenance/drift
evidence only: a clean remote miss and exact remote hit remain untested, and the
unprotected `ios-port` branch prevents a cache-poisoning-resistance claim.

Local installation preserves the existing asset/save container by using:

```text
Team ID: RMJWWPF379
Bundle ID: io.github.tryk016.openxray.RMJWWPF379
```

Do not revoke certificates or change that installed bundle identifier as a
routine signing fix.

The simulator app intentionally retains the unsigned base identifier
`io.github.tryk016.openxray`; use it for `simctl launch`, not the paid-team
device identifier above.

## Known open work

| Priority | Area | Current requirement |
|---|---|---|
| P0 | Validation | Verify startup-sector recovery across saves/levels on iPhone, run controlled lighting evidence and complete a memory-safe soak |
| P1 | Memory | Device-prove current/peak telemetry and bounded LOWMEMORY eviction, then set budgets |
| P1 | Lifecycle | Device-prove atomic frame gating, context restore, persistence and audio recovery |
| P1 | CI | Run a deliberate negative shader/varying canary in Actions and monitor the verified dSYM artifact on the first remote run |
| P1 | Shaders | IOS-P1-007 local macro/resource contract is complete; obtain a later iPhone water reference frame before claiming the SSR behavior change is visually accepted |
| P1 | Diagnostics | Measure normal-mode performance and prove no autonomous polling/readback overhead |
| P1 | Presentation | Measure the fixed 1864×860 1:1 path and profile FPS/thermal targets; validate simplified Video/Controls and dense retail UI independently |
| P1 | Environment textures | Device-prove that pinned environment/colormap raw aliases remain valid through LOWMEMORY handling and subsequent texture reloads |
| P2 | Touch | Device-prove local lifecycle cancellation and decide whether optional virtual gameplay controls are required |
| P2 | Audio | Device-prove interruption recovery with the selected OpenAL Soft provider; local provider and exact-scene mechanism are approved |
| P2 | Visibility | Replace the disabled iOS occlusion-query path |
| P2 | Texture color | Use the frozen local BC/DXT contract to choose sRGB/swizzle semantics from representative numeric probes and iPhone reference frames |
| P2 | Supply chain | Run one clean miss and one exact-hit workflow, compare artifacts and close the unprotected-branch trust boundary |
| P2 | Distribution | M6-local importer is complete; device transfer/container/save preservation, legal tester distribution and remote release remain open |

Active priorities and acceptance criteria live in
[iOS-Port-Plan.md](iOS-Port-Plan.md); deferred requirements remain in
[iOS-Port-Backlog.md](iOS-Port-Backlog.md).

## Definition of done

The iOS port is considered playable/stable only when all of the following are
true on the supported device matrix:

1. A new game and existing save load into a complete world without movement or
   timing workarounds.
2. Repeated level loads and at least 30 minutes of play do not trigger jetsam,
   unbounded growth, or corruption.
3. Current and peak memory are logged, with an agreed per-device budget.
4. The game meets a documented FPS target at a documented internal resolution.
5. Background/foreground, lock/unlock, audio interruption, and low-memory cycles
   recover without a restart.
6. Controller, touch UI, audio, saves, and retail asset persistence work.
7. CI enforces the same 279/279 and 137/137 shader contracts as the local gate.
8. Performance builds have no automatic full-frame readback or file polling.
9. The user-facing package description matches the actual build.

## Documentation map

- [iOS-Port-Plan.md](iOS-Port-Plan.md) — active roadmap.
- [iOS-Port-Backlog.md](iOS-Port-Backlog.md) — deferred work; not active scope.
- [iOS-Port-Resume.md](iOS-Port-Resume.md) — current handoff and first commands.
- [iOS-Port-Journal.md](iOS-Port-Journal.md) — append-only development evidence.
- [iOS-Port-Audit-2026-07-17.md](iOS-Port-Audit-2026-07-17.md) — historical audit,
  explicitly superseded.
- [iOS-Controller-Prior-Art.md](iOS-Controller-Prior-Art.md) — touch/controller
  design reference.
- [iOS-Metal-Plan-Draft.md](iOS-Metal-Plan-Draft.md) — deferred renderer RFC.
