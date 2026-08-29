# OpenXRay iOS — active roadmap

**Last synchronized:** 2026-08-29

**Canonical contract:** [iOS-Port.md](iOS-Port.md)

**Deferred work:** [iOS-Port-Backlog.md](iOS-Port-Backlog.md)

**Platform scope:** iOS-only; desktop compatibility is not an acceptance gate.

**Current focus:** IOS-P0-003 remains the first active task. Its exact affected
Zaton physical QuickLoad reproduction now passes: one F5/F9 kept PID 29757,
completed all 11 phases/seven memory markers, advanced to epoch 2
`quick_load/retained` and preserved/restored every prior save. The active edge
is now wider save/level and transition coverage; P1 memory remains open after a
3,413,924 KiB after-load peak and a pre-F9 pressure event. Phase 2B's
safe shadow foundation is complete: its pure explicit-manifest mapper is only an
unconditional host test stage and cannot select, prune, cache or speed up work.
IOS-P2-007 remains deferred for an authenticated complete manifest producer.
The M6 local importer and independently verified DevArchive retirement are
complete; remaining M6 work is
authorized-tester distribution, signed-update save preservation and
remote-release acceptance. IOS-P2-005's local
pinning/cache contract is complete and has returned to the Backlog pending two
remote runs. IOS-P2-006's behavior-preserving BC codec contract is also locally
complete and backlogged pending a color-space decision plus iPhone reference
frames. The UIKit scene-lifecycle migration and its five-cycle physical
app-switch acceptance are complete under IOS-P1-010. Lock/held-touch and audio
remain deferred under IOS-P1-002, IOS-P2-001 and IOS-P2-002; audio requires a
dedicated background-audio helper because the hidden XCTest runner failed with
`!pla` before interrupting OpenXRay. IOS-P0-003's
stationary branch is corrected and the new real iOS 27 QuickLoad publication
passes; wider save/level and physical-device evidence is now the active edge.

The autonomous device runner no longer restores the non-persistent
`start server(...)` config line. It requires zero semantically active direct or
indented autoload commands and passes the fixed save through the engine's
existing `-start` argument. The stable runner contract passes 16/16, the
711-ID catalog remains unchanged, the signed generic build passes and Sol
xhigh approved the implementation. A direct iPhone mechanism probe reached
exact resolved Zaton on PID `13788`; its outer command was nonzero only because
of a local post-device unlink-path error, so this is not claimed as a new
runner end-to-end PASS.

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
the exact Sol xhigh verdict `APPROVE — brak P0/P1/P2`. Phase 2B adds only its
shadow-only mapper; no affected selection, pruning or sharding exists.

The capture-v2 test fixture is now scheduler-deterministic only for its exact
four loading modes; normal/jupiter/wrong-pid retain historical timing and the
normal T0 null boundary. Exact-state stress passed 50/50 and loaded 20/20 with
cleanup verified. Final pre-document Fast receipt
`gate-1787491750981592000-67197-0.json` passed in 1050.721 s with retail 102/102,
archive 131/131, macros 30/30, resources 13/13 and links 137/137. Sol xhigh
returned exact `APPROVE — brak P0/P1/P2`. This remains partial host evidence;
the active P0/P1/M6 priorities remain unchanged.

**Optimization next:** IOS-P2-007 is deferred until an authenticated complete
change-manifest producer exists. It must prove fail-closed completeness and zero
false negatives on real and mutation diffs before it can prune anything.
Full-release must remain complete and shader-uncached; runtime, renderer,
lifecycle, memory and streaming claims remain outside affected-only evidence.
Existing external build-tree, Simulator and device locks remain mandatory;
retail sharding is later work.

**Published checkpoint:** Phase 2B commit
`559817b375be1f34fff47fdfd2f1bf5f1a1631f8` (`ios: add shadow-only affected
test planner`) passed the exact clean post-commit `full` gate in 969.618545 s
and was pushed to `origin/ios-port`; local and remote matched immediately.
Receipt `gate-1787534168767500000-52860-0.json` records 1,244/1,244 direct
cases, 63/63 stages, zero errors and uncached shaders. This is host/build
evidence only and does not promote IOS-P2-007 or create a runtime/device claim.

**Historical clean full-gate checkpoint:** exact clean code commit
`5b0bc5ba0d61959f8fd79ff6b99c9467f34a5249` received a clean post-commit full
PASS and matched `origin/ios-port` immediately after push
(`9aad5df0a..5b0bc5ba0`).
Receipt `gate-1786642077179447000-182-0.json` records 1,164 cases/58 stages,
zero errors/skips, profile 13/13, retail 102/102, uncached shader compile/link
137/137 and Release 68 TUs in 914.847 s. It authorizes only that historical
push and creates no install stamp for later checkpoints. No phone, installation
or runtime proof was produced.

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

## 1. IOS-P0-003: validate startup-sector recovery across content

**Priority:** P0.

**Evidence level:** affected Zaton save proven on three cold launches; v1
startup-sector evidence oracle is host-complete (parser 17/17, marker 10/10,
strict/ASan/UBSan PASS and final Sol xhigh approval). QuickLoad instrumentation
and private-save publication are phone-free complete: exact request identity,
11 ordered phases, seven memory markers, host phase/private-writer 16/16,
Simulator evidence 45/45 and affected mapper 32/32; the frozen inventory still
has 45 IDs. A fresh matching device gate for current `de866336…` passed; one
signed in-place update was installed, tested and later stopped without
uninstalling. Post-document preflight still passes, while the receipt's complete
dirty diff/status predates the documentation closeout. A future commit requires
a new matching install gate, and push still requires a final post-commit `full`.
Physical A/B has one controlled
`zaton`/`default_clear` packet: native 1864×860 stationary A/B and C after
45.558 m, same PID 3317/session/epoch 1/sector 115, visually correct global
lighting with no radial lit circle in that exact run. It excludes other saves,
interiors, weather, restarts and movement/streaming causation.

Fresh isolated iOS 27 Simulator PASS is
`/Users/patryk/openxray-handoff/simulator-work-20260827-022513-64141/report.txt`
(report SHA-256 `58b51b671af555628ee276c50d16e61affc176acba98121564fd3b0c52af9d99`,
manifest `bd7144713dc77918f507224ff105629d37621d422491ade27e162ac6a599e9ff`).
Simulator `C5C45EA6-9937-4722-B8B7-8E7C9A550776` was deleted. PID 70183 records
epoch 1 `level_load`/`exact`, sector 115, frame 35 and epoch 2
`quick_load`/`retained`, sector 115, frame 122. The original save remains
631235 bytes at SHA-256 `7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`,
mode 0600 and `UF_TRACKED`; live QuickSave is 650219 bytes, SHA-256
`f022e6e6c44a1641346315db071f8dce646b9ac328effa60d71b5390973c8dd7`, mode
0600, `UF_TRACKED`, UID 501 and nlink 1. Its evidence copy matches bytes/hash/
mode, has flags 0 and a distinct inode. This proves Apple Software Renderer
control flow/save permissions/publication only, not iPhone rendering, physical
memory, performance, lighting or wider content. Sol xhigh verdict:
`APPROVE — brak P0/P1/P2`.

The old physical F9 process-exit reproduction is closed for this exact affected
Zaton save/build. Packet
`/Users/patryk/openxray-handoff/ios-p0-003-phone-20260829-172509/report.txt`
kept PID 29757 through one F5/F9, all 11 phases/seven memory markers and epoch 2
`quick_load/retained`, sector 115. Its released-request native 1864×860 frame is
complete and has no radial lit-circle symptom. The original save stayed
unchanged; the prior QuickSave and `user.ltx` were restored byte-for-byte.
Further saves, indoor/portal starts, transitions and soak remain open. Full
prefetch remains prohibited because it caused an approximately 1.6 GB transient
spike, and P1 memory remains open.

The current packet supersedes the older hardened QuickLoad workroot above. F9
press/load-success/terminal/release ordering 956/1038/1117/1202 is correctly
accepted; capture C uses the same request at frame 182, state `released`, release
frame 181. F5 success/press/release 943/944/947 remains intentionally accepted
for dispatch/log ordering. Its seven Simulator memory markers include
`event_begin` current/peak 3,603,381/3,621,333 KiB, `objects_removed`
3,609,893/3,629,877, `old_alife_destroyed` 3,549,621,
`new_alife_constructed` 3,581,573, observed resident peak 4,910,544 and
compressed 0. These markers are neither phone memory nor performance proof.

**Next actions:**

1. Extend the same baseline to another outdoor save, then an indoor/portal start
   and one level transition with exact epoch/sector and objective frame evidence.
2. Run the cooled 30-minute controller memory/thermal gate separately; do not
   treat the successful one-shot QuickLoad as a memory-budget acceptance.
3. Do not restore full prefetch; its approximately 1.6 GiB transient spike is
   unrelated to a proven QuickLoad fix.

**Acceptance:**

- Local acceptance met: a stationary isolated iOS 27 Zaton load records a
  resolved exact outcome without movement and publishes the revalidated
  T0/T1/T2 set plus report only after Simulator deletion and all guards pass.
  This is Apple Software Renderer control-flow evidence, not iPhone or pixel
  proof.
- Local hardened QuickLoad acceptance met: normal F5/F9 preserves one PID,
  records the exact ordered request phases and seven memory markers, creates the
  private 0600 QuickSave, advances to `quick_load/retained`, revalidates capture
  C and publishes only after Simulator deletion. It does not close physical-
  device or wider-content scope.
- Physical QuickLoad acceptance is met for the exact affected Zaton save/build:
  one F9 preserved process continuity, completed epoch 2 `quick_load/retained`
  and all 11 phases/seven memory markers, and retained/restored protected saves.
  This does not accept other content or the P1 memory budget.
- Three cold launches and two save/level combinations record the baseline epoch,
  exact expected batch, resolved classification and objective complete-world
  frame without a movement workaround.
- Indoor and portal-adjacent starts select the correct sector with the same
  sector and visual evidence kept distinct.
- No prefetch spike, unbounded growth or diagnostic visibility counters.

## 2. IOS-P1-005: validate graphics profiles and presentation baseline

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

## 3. IOS-P1-006: close visible UI renderer gaps

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

The 2026-08-14 physical Options rerun passed XCTest 1/1 in 47.945 seconds and
produced six retained screenshots over three launch/tap/terminate attempts.
They show the complete main menu without Multiplayer and the complete Video
Options surface with four tabs, `Optimal` and exact `1864x860`. Engine PID
`13765` loaded all expected Options resources; the exact-token trace released
before the final success marker, and a fresh process query proved `xr_3da`
absent. This is device navigation/presentation/cleanup evidence, not gameplay,
performance or audio proof.

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

## 4. M6: make legal retail-data import resumable and verifiable

**Priority:** P2 promoted for the current phone-free implementation window;
remaining lock/held-touch/audio device work stays separately deferred.

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
