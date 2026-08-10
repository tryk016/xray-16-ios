# OpenXRay — iOS port

Canonical specification for the OpenXRay iOS project.

**Last synchronized:** 2026-08-10

**Status:** playable development build; affected-iPhone startup-sector and known
SSAO defects fixed; stationary Simulator sector coverage and the later
movement-correlated dark-frame observation remain under investigation

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

It is not release-ready. The startup geometry defect and proven SSAO macro
defect are fixed, but a later dark-frame smoke remains causally unresolved.
The isolated iOS 27 Simulator capture-v2 tooling is host-complete, but its first
real publication is blocked by a stationary startup-sector coverage gap tracked
under IOS-P0-003.
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
diagonal factor, unchanged camera height and first-hit exit. A separate commit
policy proves that an invalid result cannot notify or replace `last_sector_id`.
Strict C++ policy and ASan/UBSan pass. This freezes the mechanism but does not
replace the remaining outdoor, indoor/portal and transition device tests.

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

Host evidence for this oracle is parser 17/17, source-mutation contract 9/9,
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

The corrected real run,
`/Users/patryk/openxray-handoff/simulator-work-20260810-024814-86404`, built
successfully and reached Zaton synchronization plus `after_load`. T0 was
`loading` sequence 23, T1 was `loading` sequence 24, and every candidate through
sequence 88 remained `loading` under the single 600-second deadline. Its startup
marker was PID 92531, epoch 1, frame 35, level `zaton`, trigger `level_load`,
status `unresolved`, method `none`, sector `4294967295`, with identical camera
and probe positions and radius 0.

`CRender::Calculate` currently invokes exact/fallback detection only when the
saved and current camera positions differ. A stationary Simulator start with
equal positions instead takes the no-detection branch, so the proven fallback
is never attempted. This is a new stationary Simulator coverage gap, not a
refutation of the device-proven fallback. IOS-P0-003 remains active until that
branch is corrected and a fresh isolated run publishes the complete T2 set.

The second run timed out fail-closed. Both dedicated Simulators were deleted;
no T2 `capture.json`, `capture.ppm`, `capture-proof.json`, `report.txt` or
`.report.pending` was published. The last successful protected-input and
source-snapshot guards were pre-launch; the timeout prevented the post-runtime
log, save, staged-data and protected-input guards from running. The evidence scope is exactly
`iOS-27.0-Simulator-Apple-Software-Renderer-only`: it proves no pixels,
readability, iPhone behavior, performance or lighting cause.

The static shader contract now covers all five numeric zero-default macros:
`SUN_QUALITY`, `SSR_QUALITY`, `SSAO_QUALITY`, `SSAO_OPT_DATA` and
`MSAA_SAMPLES`. Thirty mutation tests validate complete guarded zero fallbacks,
comments and multiline directives, and freeze exactly eight legacy presence
tests plus one `#undef`. It changes no shader behavior and does not enable HBAO,
HDAO, alternate SSR or MSAA.

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
62/62.

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
that semantic-navigation slice are retained in the append-only Journal. The
current authoritative full-gate stamp is recorded below under the UIKit scene
lifecycle, not inferred from an older FastDevice or UI-only artifact.

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
under IOS-P1-010 and IOS-P1-002.

The UIScene-local full gate rebuilt four translation units and passed its SDL
scene 7/7, lifecycle marker 10/10 and retail oracle 74/74 contracts alongside
the shader gates. It remains valid UIScene-local/Simulator evidence, but its
older stamp is not the current authoritative full-gate stamp.

### Current authoritative full-gate stamp

After commit `90e9d3c3e6de12c86be682875784d2032bcbc238`, the final uncached
full gate rebuilt 69 translation units and passed the strict/sanitized BC
contract, retail 74/74, installer 13/13, local CI contract 79/79, numeric
macros 30/30, low 2/2, SSAO 6/6, shader contract 279/279 and links 137/137.
Platform is `IOS`, minOS 16.4 and shader cache is forced off. The current source
hash was independently recomputed after the gate and exactly matches this
stamp. This post-commit artifact has not yet been installed on a phone:

```text
source_sha256=ff2c33c77f1fb0aca0a6b2ec9d49661ea41ded30ce44ffe28469167055b57f02
app_uuid=226890F0-CF42-302B-AA5F-3092CB5E4AF3
bundle_sha256=620f3b98983fe10184b31a232dbf564bf256b6d68e7073f1f547ff6e0181ae1a
openal_provider=OpenALSoft-1.25.2-static
openal_sha256=86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914
__debug_info=513888155
```

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

The main worker independently recomputed the current gate hash and it exactly
matched this stamp. A real no-device preflight passed with a sentinel lock path
still absent, a cleaned temporary work directory and unchanged source-bundle
hash. This is host/build/signing-preflight evidence only: it adds no device
install, container, runtime, visual or pixel proof.

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
| P0 | Validation | Correct stationary initial sector detection, pass isolated capture-v2, then verify the fallback across saves/levels and complete a memory-safe soak |
| P1 | Memory | Device-prove current/peak telemetry and bounded LOWMEMORY eviction, then set budgets |
| P1 | Lifecycle | Device-prove atomic frame gating, context restore, persistence and audio recovery |
| P1 | CI | Run a deliberate negative shader/varying canary in Actions and monitor the verified dSYM artifact on the first remote run |
| P1 | Shaders | Keep the frozen eight-presence/one-undef legacy debt explicit and remove or re-review it before enabling HBAO/alternate SSR paths |
| P1 | Diagnostics | Measure normal-mode performance and prove no autonomous polling/readback overhead |
| P1 | Presentation | Measure the fixed 1864×860 1:1 path and profile FPS/thermal targets; validate simplified Video/Controls and dense retail UI independently |
| P1 | Environment textures | Device-prove that pinned environment/colormap raw aliases remain valid through LOWMEMORY handling and subsequent texture reloads |
| P2 | Touch | Device-prove local lifecycle cancellation and decide whether optional virtual gameplay controls are required |
| P2 | Audio | Device-prove interruption recovery with the selected OpenAL Soft provider; local provider and exact-scene mechanism are approved |
| P2 | Visibility | Replace the disabled iOS occlusion-query path |
| P2 | Texture color | Use the frozen local BC/DXT contract to choose sRGB/swizzle semantics from representative numeric probes and iPhone reference frames |
| P2 | Supply chain | Run one clean miss and one exact-hit workflow, compare artifacts and close the unprotected-branch trust boundary |

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
