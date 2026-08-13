# OpenXRay iOS — active roadmap

**Last synchronized:** 2026-08-13

**Canonical contract:** [iOS-Port.md](iOS-Port.md)

**Deferred work:** [iOS-Port-Backlog.md](iOS-Port-Backlog.md)

**Platform scope:** iOS-only; desktop compatibility is not an acceptance gate.

**Current focus:** Phase 2B host feedback optimization: explicit profile
separation and a conservative affected-test planner. The M6 local importer and
independently verified DevArchive retirement are complete; remaining M6 work is
authorized-tester distribution, signed-update save preservation and
remote-release acceptance. IOS-P2-005's local
pinning/cache contract is complete and has returned to the Backlog pending two
remote runs. IOS-P2-006's behavior-preserving BC codec contract is also locally
complete and backlogged pending a color-space decision plus iPhone reference
frames. The UIKit scene-lifecycle migration is locally and Simulator-complete;
its physical-device acceptance is backlogged as IOS-P1-010, alongside
IOS-P1-002. Both are deliberately priority-deferred behind Phase 2A/2B
checkpoint closeout and require explicit promotion. IOS-P0-003's
stationary branch is corrected and its real iOS 27 capture-v2 publication
passes; wider save/level and physical-device evidence is now the active edge.

**Host test-feedback:** Optimization Phase 1 remains complete: 102 frozen
byte/AST-identical retail IDs, 53 guards, 49 integration cases and `host-fast`
55, with the Phase 1B fail-closed fixture and 13/13 persisted contract.
Phase 2A code and post-gate evidence are host-complete: the canonical shader cache has per-key kernel locks,
immutable output/receipt publication, fail-closed recovery and semantic
MISS/HIT validation. `full` remains force-direct, opens no cache namespace and
is shader-uncached. Natural `shaders` MISS/HIT passed in 820.149/802.981 s with
identical 1,183/1,183 logical IDs (SHA-256
`4ae15fba39fb626c41f9a00ccbf1b1982338690b0b0da7b6212a23d504626f74`) and
50/50 stages (SHA-256
`801483ceb8b4f1bafd53720ef26f2730944920bdb571879ba89fa39cf3a5ecbf`); the
17.168 s saving is not a
FastDevice or runtime claim because complete retail preflight remains dominant.
The code review and post-gate evidence review each approved their respective
scope; the complete Phase 2A code-plus-documentation checkpoint then received
the exact Sol xhigh verdict `APPROVE — brak P0/P1/P2`. No planner, affected
selection or sharding exists yet.

**Optimization next:** Phase 2B adds explicit profile separation and a
conservative versioned affected-test planner: unknown or global input must
select full, and renderer/lifecycle/memory/streaming or runtime claims cannot
close from affected-only evidence. Existing external build-tree, Simulator and
device locks remain mandatory before any concurrency beyond per-key cache locks;
retail sharding is later work.

**Latest pushed code checkpoint:** exact clean code commit
`5b0bc5ba0d61959f8fd79ff6b99c9467f34a5249` received a clean post-commit full
PASS. It was the latest pushed code commit and matched `origin/ios-port`
immediately after push (`9aad5df0a..5b0bc5ba0`). The later documentation
closeout `12bd62067a228db1a9ea5699a5641287eecc9432` is now both `HEAD` and
`origin/ios-port`.
Receipt `gate-1786642077179447000-182-0.json` records 1,164 cases/58 stages,
zero errors/skips, profile 13/13, retail 102/102, uncached shader compile/link
137/137 and Release 68 TUs in 914.847 s. It authorizes only that push; this
following documentation closeout has no matching install stamp. No phone,
installation or runtime proof was produced.

This file intentionally contains no more than five active tasks. Historical
evidence belongs in [iOS-Port-Journal.md](iOS-Port-Journal.md). Work not listed
here is deferred and must be promoted explicitly from the Backlog.

## Milestone status

| Milestone | Outcome | Status |
|---|---|---|
| M0 — Toolchain | arm64 dependencies and complete iOS 16.4 engine | Complete |
| M1 — Application | UIKit/SDL launch, sandbox, bundle and install | Complete |
| M2 — ES renderer | ES 3.0 deferred world renderer | Complete with known debt |
| M3 — Playability | Level, controller, touch menu, audio and saves | Controller-playable |
| M4 — Reliability | Multi-level, memory, lifecycle and automation | In progress |
| M5 — Performance | Fixed 1864×860 profiles and measured baseline | Device validation in progress |
| M6 — Distribution | Reproducible tester package and data setup | Local importer complete; distribution acceptance remains |
| M7 — Renderer decision | ES, ANGLE or native Metal from measurements | Deferred |

## 1. IOS-P1-011: Phase 2B — separate host profiles and add a conservative affected-test planner

**Priority:** P1.

**Evidence level:** Phase 1's frozen retail profile contract is complete.
Phase 2A's code review and its natural host MISS/HIT evidence review each
approved their scopes; the complete Phase 2A code-plus-documentation checkpoint
then received the exact Sol xhigh verdict `APPROVE — brak P0/P1/P2`. The current evidence is host-only: logical selection
SHA-256 `4ae15fba39fb626c41f9a00ccbf1b1982338690b0b0da7b6212a23d504626f74` and
50-stage SHA-256
`801483ceb8b4f1bafd53720ef26f2730944920bdb571879ba89fa39cf3a5ecbf` are equal
for the natural `shaders` MISS/HIT receipts. No affected selection, local
sharding or runtime evidence exists.

**Next actions:**

1. Freeze the complete post-Phase-2A entrypoint inventory, IDs, labels,
   timeouts, resources and explicit inputs, with per-case/per-stage JSON timing.
2. Add versioned profile selection for `host-fast`, shader-affected,
   engine-affected, retail-integration, full-release, sim-runtime and
   device-runtime; unknown or global paths select full.
3. Preserve every existing guard and fail-closed mutation case. Runtime claims
   for renderer, lifecycle, memory or streaming stay outside affected-only
   selection.

**Acceptance:** the old and new runners select identical stable IDs before any
profile optimization; an injected failure propagates identically; unknown/global
inputs select full; full-release remains semantically complete and shader
uncached. No profile or host result is presented as Simulator/device/runtime
proof.

## 2. IOS-P0-003: validate startup-sector recovery across content

**Priority:** P0.

**Evidence level:** affected Zaton save proven on three cold launches; v1
startup-sector evidence oracle is host-complete (parser 17/17, marker 10/10,
strict/ASan/UBSan PASS and final Sol xhigh approval); normal F5/F9 QuickLoad is
proven on iOS 27 Simulator, while other saves, indoor/portal starts, transitions
and physical-device visual proof remain untested.
Capture-state v2 and the stationary-vs-forward A/B harness are locally complete
(evidence parser 16/16, host-tool mocks 8/8, producer mutation contract 2/2,
strict/sanitized C++ PASS, post-commit full Release and Sol xhigh approval), but
have not yet produced a physical-device A/B packet. The separate isolated iOS
27 single-capture host extension is also locally reviewed (final static PASS,
parser 31/31, runner 84/84 and final Sol xhigh
`APPROVE — brak P0/P1/P2`). Commit `399b7fdbb` records the stationary-branch
correction, its real successful T2 publication and its post-commit full Release
PASS. The earlier clean `e967a4c...` install stamp is historical/stale. The
later source `aea7d8fb0fe4ffac1f1c002205cc9762b61ded35b241861c54ddebe5d85161b3`
and stamp `c198b069433c092e0148ba7ecf02f1a8479e4bab25b17f909253750ef3f8e92d`
are also historical/stale. Commit
`8b265b07b489f1633e6be17f57fd1578adb11092` received a clean full gate with
source `d8b2fc5d4b421c01cdf0d656d1cc02794dea49d6f8410389511f22199239f394`,
UUID `574CACAC-E835-3E81-9919-60F8B93343DE`, bundle
`c6dbaa33fe9101403b68969264521461160f2f43c3007ef8151f1b43a83e9a0c` and stamp
`54c54c5c972ef34a4852189a57b2d012eeef6be6ea3634af3bb94c8b5dbb59a5`.
It is historical after adding the exact prepared `.DS_Store` removal overlay;
commit `727c51da` later received its own clean full receipt and used it for its
completed push. That stamp is historical for future installation after this
documentation change. Production equivalence remains strict, and the historical
verifier remains read-only/non-authorizing.

Real workroot `simulator-work-20260810-015703-14748` exposed and corrected an
over-strict live-readiness classification: a valid post-T1 `loading` candidate
must retry with CLI 75 while invariant breaches remain fatal with CLI 1 and any
post-stop retry is fatal. Corrected workroot
`simulator-work-20260810-024814-86404` built and reached Zaton sync/`after_load`,
then recorded T0 `loading` sequence 23, T1 `loading` sequence 24 and candidates
through sequence 88 still `loading` under one 600-second deadline. The startup
marker was PID 92531, epoch 1, frame 35, `level_load`, `unresolved`/`none`,
invalid sector `4294967295`, identical camera/probe and radius 0. Both dedicated
Simulators were deleted and publication failed closed without T2/report output.

The corrected iOS policy preserves camera movement as an unconditional trigger
and adds one synthetic stationary `level_load` attempt only while the epoch is
awaiting, the sector is invalid, no report is pending and a dedicated
post-epoch camera-generation barrier has passed. LevelLoad can no longer publish
retained/none; QuickLoad keeps its independent barrier and no-detection path.

The exact vertical query misses the affected spawn floor. The iOS-only
nearest-floor fallback finds sector 115 at 8 m and renders the complete static
world without movement. Full prefetch remains prohibited because it previously
added an approximately 1.6 GB transient spike.

The phone-free regression policy now proves exact-query bypass, the independent
56-probe order, first-hit selection, unchanged camera height, no-hit metadata,
invalid-sector commit protection, stationary exact/fallback/no-hit one-shot
behavior and later movement recovery. v1 also validates
`level_load`/`QuickLoad` epochs, exact/fallback/retained/unresolved/recovered
outcomes and an anchored oracle stream. Parser 17/17, source-marker 10/10,
strict C++ and ASan/UBSan pass. The FastDevice gate passed after rebuilding 249
translation units. This protects the mechanism only; real CDB results across
additional content remain device-untested.

Fresh isolated workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-041339-82601` passed on
iOS 27. PID 90549 recorded stationary `level_load resolved/exact`, sector 115,
at frame 35. T0/T1/T2 tokens 54/55/56 were same-session/same-PID gameplay with
advancing frames 89/91/92 and continual time, no input and 1864x860. The five
capture artifacts and report were published only after all guards passed and
the dedicated Simulator was deleted. Scope remains Apple Software Renderer
control-flow/publication evidence, not iPhone or pixel proof.

Fresh hardened QuickLoad workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-164020-71268` passed:
single arm64 `IOSSIMULATOR`, minOS 16.4, SDK 27.0; PID 77340; epoch 1
`level_load/exact` frame 35 then epoch 2 `quick_load/retained` method `retained`
frame 122. Exactly B0/B1/C were 1864x860 gameplay frames 89/94/182 with tokens
54/58/118; normal F5/F9 used scancodes 62/66. Live/private QuickSave inodes
22482673/22482715 differ but both are 649473 bytes with SHA-256
`eb82993bc9eb70ef64ef7830669328470fb556da69a82883a41ffc9dda5c43fe`; the original
save is unchanged at 631235 bytes and SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`.
PASS report SHA-256 is
`27f89f6793ec2d0f0ee6831add123ae3e54b9d83eaaa37d4326a62d052f35cb9`; manifest
SHA-256 is `7d45f49bbe15727a1813971b958fe8c19261b32cc3426a7bfd69dffbdd418896`.
Pending aliases were absent, protected inputs unchanged and Simulator
`278AD2E8-7116-448F-8474-670945489415` deleted before report publication. Final
Sol xhigh: `APPROVE — brak P0/P1/P2`. Scope is normal F5/F9 Apple Software
Renderer control flow only, not iPhone/pixel/readability/lighting/performance or
other-content proof; IOS-P0-003 stays active for physical and wider content
acceptance.

**Next actions:**

1. Run `lighting_ab_capture.sh` on the next outdoor phone batch into a new
   evidence directory; accept only its A/A+3/A+6 controls and keep manual pixel
   interpretation separate from sector and cause claims.
2. Continue one additional outdoor save, one indoor/portal start, physical-device
   QuickLoad and one level transition with baseline-plus-exact-batch evidence;
   record the 30-minute controller-run memory footprint separately.
3. Correlate any remaining dark-frame transition with the controlled stationary
   and forward capture packet before changing rendering or streaming code.
4. Complete the 30-minute memory/thermal run only after short correctness
   batches pass and the phone has cooled.

**Acceptance:**

- Local acceptance met: a stationary isolated iOS 27 Zaton load records a
  resolved exact outcome without movement and publishes the revalidated
  T0/T1/T2 set plus report only after Simulator deletion and all guards pass.
  This is Apple Software Renderer control-flow evidence, not iPhone or pixel
  proof.
- Local hardened QuickLoad acceptance met: normal F5/F9 preserves one PID,
  creates and reloads the exact QuickSave, advances to `quick_load/retained`,
  revalidates exactly B0/B1/C, removes pending aliases and publishes only after
  Simulator deletion. It does not close physical-device or wider-content scope.
- Three cold launches and two save/level combinations record the baseline epoch,
  exact expected batch, resolved classification and objective complete-world
  frame without a movement workaround.
- Indoor and portal-adjacent starts select the correct sector with the same
  sector and visual evidence kept distinct.
- No prefetch spike, unbounded growth or diagnostic visibility counters.

## 3. IOS-P1-005: validate graphics profiles and presentation baseline

**Priority:** P1, immediately after IOS-P0-003.

**Evidence level:** profile UI, read-only 1864×860, all three initial
tier/target selections and correct Performance/Quality/Optimal world frames
proven on device. Five diagnostic foreground cycles retained the drawable.
`serious thermal` forced Optimal from balanced to performance in both normal
and diagnostic mode. The unscaled nanosecond work/elapsed source, sub-ms sample
retention and resume warmup pass host, Simulator and full Release gates.
The expanded deterministic policy contract covers exact 24/31/40 ms
thresholds, complete tier traversal, cooldown boundaries, warmup/gaps,
30/60/120 Hz and uneven cadence. It also proves that blocked or mixed
power/thermal windows cannot be reused for an upgrade while they still permit
downgrades. Strict warnings and ASan/UBSan pass. Measured downgrade/upgrade and
frame pacing remain device-untested.

The framebuffer remains fixed at 1864×860 with a 1:1 present in every mode:

- Performance: low runtime tier, 60 FPS target.
- Optimal: balanced 30 FPS start, p90/hysteresis adaptation.
- Quality: full runtime tier, 30 FPS target.

Optimal may change only visibility, geometry LOD, local-shadow quality and
shadowed-light fade. Resolution, textures, shader/resource topology, SSAO,
VSync, MSAA and postprocessing remain fixed.

**Next actions:**

1. Let the device cool, then record the same normal-mode camera path under all
   fixed modes without diagnostic readback.
2. Measure frame pacing against the 60/30 FPS targets.
3. Drive Optimal through a measured downgrade, recovery and upgrade.
4. Validate Low Power separately from the proven `serious thermal` path.
5. Device-confirm the locally enforced synchronous profile reassert after
   config reload and Options discard.

**Acceptance:** the three modes select their documented tier and target;
Optimal does not oscillate or change shader/resource topology; transitions do
not corrupt the frame; and five foreground cycles retain 1864×860.

## 4. IOS-P1-006: close visible UI renderer gaps

**Priority:** P1.

**Evidence level:** Multiplayer absence, all four simplified Options tabs, HUD,
ordinary inventory and core PDA/map surfaces are readable on device. The run
found and fixed a fatal missing `video_adv:cap_always_active` XML contract; the
shared Lua initializer is now satisfied by an unbound hidden compatibility
node and all tabs re-open successfully. The phone-free focus policy now proves
a one-correction fixed point for visible, clipped, fractional, over-height and
externally clamped cells, including non-finite and saturated bounds. The focus
overlay consumes the same indented clip as `CUIScrollView::Draw`; strict and
ASan/UBSan tests, 32 UI fixtures total (1 positive baseline + 31 negative
mutations), FastDevice, the full uncached
Release gate and Sol xhigh review pass. Overfilled inventory visuals and the
faction-war page remain device-untested. The isolated iOS 26.5 retail Simulator
reaches a fully textured main menu with a live-process and screenshot oracle.
Its later exact-save autoload path now reaches `saved_game_sync_complete` and
`after_load` under the Apple Software Renderer after the cross-Apple
`CLocatorAPI::Register` path-length correction. The Locator mutation contract
passes 8/8 and the hardened retail-isolation workflow passes 62/62. Mandatory
`large-files-sha256.tsv` size/SHA checks now agree with `files.tsv` and protect
all allowlisted large files across preflight, staging and post-runtime; only the
selected mutable large save may change, and it must remain a nonempty regular
file. A two-stage log oracle snapshots before runtime proof and securely
finalizes only after successful process termination, rejecting path/inode,
prefix, rotation, truncation, rewrite, symlink, sequence and failure anomalies.

The final isolated iOS 26.5 Simulator run is PASS in
`simulator-work-20260809-013601-65931`; its dedicated Simulator
`410EA3BC-23FC-4F4C-843D-CF4438704652` was deleted. The iOS/autoinput-only
marker runs after `DoRenderDialogs()` and records actual semantic transitions
with exact PID/sequence/frame/state. PID `72114` proves the controller path
`I, I, P, E, Escape, M, Escape` as `world -> inventory -> world -> pda_tasks
-> other -> world -> pda_tasks -> world`. In CoP retail there is no standalone
`eptMap`: `eptTasks` is the combined Tasks/Map surface; the deliberate `E`
shows `M` does not inherit prior Tasks state. Synchronization was 337327 ms,
`after_load` physical footprint 3559173 K and textures 2206009 K. The 631235
byte save remains SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`; protected
inputs are unchanged and cleanup passed. Binding JSON has
`final_log_sha256=638434c1b8b745d7d7002237eed356144b893e823ddbce28e571b8f34dba9d96`
and exact scope `semantic-ui-navigation-only`.

Its task-specific host contracts passed and final Sol xhigh verdict is
`APPROVE — brak P0/P1/P2`. The latest pre-document full source
`f8727806510005248fb9b92e2c7075fd5c91f66a82af451adfe8baa2d37e587f` and stamp
`d9cfba1057e29a05fa10937c48da0c43bc7cb896a0b315af44634521d4cc0906` are now
historical; `727c51da` later received a clean full receipt for its own push.
This is semantic navigation only, not pixel, readability, performance or
physical-device proof.

Unpromoted backlog note: IOS-P1-007 now has local macro/resource completion
(30/30, 13/13, SSR 9/9 and debt 0+0) under the same full stamp. It remains
deferred for an iPhone water reference frame; this does not add an active task.

The opt-in iOS 27 `--ui-captures` path now layers native evidence on top of
`--ui-navigation`: it requires diagnostics/autoinput and captures steps 1
`inventory`, 3 `pda_tasks`, 4 `other`/Stats and 6 `pda_tasks` after the
post-`DoRenderDialogs()` marker. Stable metadata/PPM pairs are bound to exact
session/PID/token/frame/dimensions/input ordering; PPM is authoritative, PNG is
an exact derivative, and manifest/report publication follows process-stop
revalidation. The live log reader permits monotonic append but rejects
symlink/nonregular input, inode replacement, shrink, rewrite, truncation and
races. Two independent iOS 27 Apple Software Renderer runs passed 7/7 expected
states (`inventory, world, pda_tasks, other, world, pda_tasks, world`) and 4/4
1864x860 captures with protected inputs unchanged and deleted Simulators.
Focused tests are capture 15/15, navigation 34/34 and retail isolation 92/92.
The eight PNGs were visually inspected as complete inventory, area map/tasks,
Stats and area map/tasks. This is native file-level Simulator evidence, not
iPhone/readability/color/performance proof. Final Sol xhigh verdict is
`APPROVE — brak P0/P1/P2`.

**Next actions:**

1. Validate focus auto-scroll and clipping in an overfilled inventory.
2. Open the faction-war PDA page and visually validate the authored
   three-slice/static fallback without missing-texture noise.
3. Verify cursor, minimap, magnifier and video-wrapper textured surfaces.
4. Device-validate the remaining listed surfaces; do not promote the Simulator
   captures to their readability, color or performance acceptance.

Advanced desktop graphics controls are not part of the iOS product.

**Acceptance:** menu, HUD, inventory, PDA/map and video surfaces are complete,
readable and internally consistent on the supported iPhone baseline.

## 5. M6: make legal retail-data import resumable and verifiable

**Priority:** P2 promoted for the current phone-free implementation window;
physical lifecycle remains higher priority when the iPhone is available.

**Evidence level:** the M6-local importer sub-slice is complete: standalone
stdlib `misc/ios/retail_import.py` and 21/21 mutation/recovery tests prove
descriptor-relative source/output confinement, immutable all-file SHA-256
planning, byte-range prefix resume (including a real 600 MiB sparse fixture at
an interior offset), nonblocking locking, private permissions,
`renameatx_np(RENAME_EXCL)`, fsync/recovery, exact final verification and
unchanged legacy-runner guard compatibility. The reviewed source and its
independent manifest are now archived under `/Volumes/DevArchive/OpenXRay/backups`
as transactions `5beca2ef4f034286be6299d6d9a2bdba` and
`8039f43384784b879442f993dab281ae`; both verify PASS and their local sources
are receipt-bound zero tombstones. One prepared root with saves at
`/Users/patryk/openxray-handoff/retail-prepared-20260810-211114` verifies and
idempotently prepares PASS: final root is only `Documents` plus `manifest`, 13
selected files, 4.3 GiB/4,611,922,289 prepared bytes, required
`resources.db0`–`resources.db4` and `levels.db0`–`levels.db1` present. No phone
is in scope. The unchanged retail guard is PASS for the prepared
`Documents`/`manifest`. The earlier Sol xhigh review of the local importer
implementation and then-current documentation returned
`APPROVE — brak P0/P1/P2`; that verdict did not pre-approve the later
DevArchive production retirement or this documentation closeout.

The historical pre-document full gate `gate-1786563440297027000-96172-0.log` is
PASS: archive policy 125/125, mocked retail Simulator 102/102, numeric macros
30/30, resources 13/13, SSAO 6/6, SSR 9/9, links 137/137 and 0 rebuilt TUs.
This archival evidence is host-only and does not close the remaining M6 device,
legal-distribution or remote-release acceptance.

**Next actions:**

1. Define and evidence the distribution path for authorized testers, including
   a no-copy/clone decision if it is proposed.
2. Device-prove transfer/container/save preservation across a signed update;
   retain the legal, remote-release and App Store boundaries.

**Acceptance:** local importer acceptance met: a lawful, already accessible
backup can be interrupted inside a large archive, resumed without recopying its
valid prefix, independently verified byte-for-byte and atomically published in
the unchanged Simulator format. M6 remains active: physical-device transfer,
signed-update save preservation, legal distribution and remote-release
artifacts remain separate acceptance work.

## Definition of done for an active task

The completion contract is canonical in [iOS-Port.md](iOS-Port.md). Closing an
item from this plan requires:

- a tested revision against the item's declared acceptance scope;
- physical-device evidence whenever the item claims installation, runtime,
  rendering or device behavior; a host-only tooling item may instead close on
  a real preflight with its untested device boundary stated explicitly;
- commands used;
- measured evidence with proven/inferred/untested separated;
- a new append-only Journal entry;
- updated concise Resume and session log when operational state changed;
- clean `./misc/ios/build_check.sh --full` for engine or shader changes.
