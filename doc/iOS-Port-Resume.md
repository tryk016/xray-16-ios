# OpenXRay iOS — operational handoff

**Updated:** 2026-08-29. **Docs:** [canonical](iOS-Port.md), [active](iOS-Port-Plan.md), [deferred](iOS-Port-Backlog.md).

Read this file completely, then the relevant active task/canonical section; search the Journal by task ID or exact symptom, never in full.

## Current checkpoint

The M3 Pro host runs macOS/Xcode/SDK 27.0 beta and CMake 4.4.0; device and
Simulator engines build arm64 for iOS 16.4+. Team `RMJWWPF379` signs stable
`io.github.tryk016.openxray.RMJWWPF379`, preserving the device data container.
The physical-device baseline is GLES 3.0 at a real 1864×860 drawable with 1:1 presentation and Bluetooth controller. Sector fallback and SSAO value-macro fixes are proven. Current dirty checkpoint is at HEAD `de866336…`; its code state passed Fast/full and a fresh device gate, then one signed in-place update was installed and tested. The app remains installed but is closed. There was no commit or push. Post-document preflight still passes without a device, so the artifact/install stamp remains content-valid; the device-gate receipt's full dirty diff/status predates this documentation closeout. A future commit changes HEAD and requires a new matching gate before another install, and a final post-commit `full` remains mandatory before push.

IOS-P0-003 has one [controlled physical Zaton/default_clear lighting packet](/Users/patryk/openxray-handoff/ios-p0-003-phone-20260826-210300/lighting-outdoor/report.json): stationary A/B, C after 45.558 m, fixed PID 3317/session/epoch 1/sector 115 and native 1864×860. Global lighting is visually correct without a radial lit circle in that exact run only—not other saves, interiors, weather, restarts or a movement/streaming cause.

The phone-free QuickLoad instrumentation/private-save checkpoint is formally complete. Exact request identity has 11 ordered phases (`deferred` through `event_complete`) and seven memory markers; host phase/private-writer is 16/16, Simulator evidence 45/45 and affected mapper 32/32, with 45 frozen existing inventory IDs. The iOS-only writer forces final mode 0600 independently of umask, rejects symlink/hardlink targets, retains `UF_TRACKED`, propagates final flush/close failure and Locator normalizes separators before post-close publication.

Fresh isolated iOS 27 Simulator PASS is `/Users/patryk/openxray-handoff/simulator-work-20260827-022513-64141/report.txt`: PID 70183 has epoch 1 `level_load/exact` sector 115 frame 35 and epoch 2 `quick_load/retained` sector 115 frame 122; its Simulator was deleted. Original save is unchanged (631235 B, `7ff0b12e…214cc`, 0600, `UF_TRACKED`); live QuickSave is 650219 B (`f022e6e6…c8dd7`, 0600, `UF_TRACKED`, UID 501, nlink 1), while the private evidence copy has identical bytes/hash/mode and a distinct inode. F9 press/load-success/terminal/release 956/1038/1117/1202 is accepted; F5 943/944/947 is intentionally accepted. This is Apple Software Renderer control-flow/save-permission/publication evidence only—not iPhone rendering, physical memory, performance, lighting or wider content. Sol xhigh: `APPROVE — brak P0/P1/P2`.

The old physical F9 process-exit reproduction is closed for the exact affected Zaton save/build. Packet `/Users/patryk/openxray-handoff/ios-p0-003-phone-20260829-172509/report.txt` kept PID 29757 through one F5/F9, all 11 phases/seven memory markers and epoch 2 `quick_load/retained`, sector 115. Its native 1864×860 released-request frame is complete and has no radial lit circle. Original save was unchanged; prior QuickSave and `user.ltx` were restored byte-for-byte. The phase peak was 3,355,476 KiB and after-load peak 3,413,924 KiB; a pre-F9 JetsamEvent did not kill `xr_3da`, so P1 memory remains open. Do not restore full prefetch.

M6-local importer is complete (mutation/recovery 21/21); authorized tester
distribution, signed-update save preservation and remote release remain open.
The verified prepared cache and DevArchive retirement remain local/host evidence;
each isolated Simulator still stages its own 4.3 GiB copy. That M6 importer work
did not touch the device container.

After historical `8b265b07b`, only Finder's 6,148-byte `.DS_Store` was removed; all 13 retail files verify. Overlay `ba2e07e05` plus supplement `ab1ba7f45` are
committed; real `HISTORICAL_PASS` remains read-only closeout work and `9aad5df0` is stale for install.

Physical UIKit app-switch acceptance is complete: lifecycle XCTest/log oracle 1/1, PID 13920, seq2–11, five recoveries and exact 1864×860; `OpenXRay-lifecycle-20260814-0521.xcresult`, log `5ee26c83…30a6`, cleanup SIGTERM→SIGKILL→gone→release.
The five frames show complete fixed Zaton/HUD without darkness/lit-circle only in that exact view. Options `OpenXRay-options-20260814-0519.xcresult` passed 1/1: three attempts, six readable complete menu/Video frames, no Multiplayer, `Optimal`, 1864×860; log `accbd3c04…16cb`.
Audio is unproven: reliability was 0/1 because the XCTest helper failed AVAudioSession activation 561015905 before interruption; no audio claim or regression.
Memory remains open P1: warning 2,000,569 KiB texture storage, eviction 0/0 KiB, after-load physical 3,310,899/3,314,995 KiB; no soak/budget acceptance.

## Host feedback checkpoint
Optimization Phase 1 remains complete: 102 frozen retail bodies/IDs, 53 guards,
49 integration cases, `host-fast` 55, 96-record fixture and 13/13 support contract.
The deterministic capture-v2 mock uses causal ACKs only for its exact four
loading modes; normal/jupiter/wrong-pid retain historical timing and normal T0
null. Exact stderr binding, ordered transcript, mutations and nonce-bound bounded
cleanup are covered. Stress passed 50/50 and loaded 20/20 with cleanup verified.
The first corrected Fast receipt `gate-1787489460135495000-78059-0` exposed only
the over-broad non-loading scope; the disjoint branch restored normal semantics.
Final exact-state Fast receipt `gate-1787491750981592000-67197-0.json` passed in
1050.721 s: retail 102/102 in 735.357 s, archive 131/131, macros 30/30,
resources 13/13, links 137/137, UUID `4A5C8C5B-BEF6-3A30-A173-FD5E22D6E29B`.
Sol xhigh returned exact `APPROVE — brak P0/P1/P2` and authorized this docs-only
append without another gate. Production is byte-identical.

Phase 2A remains host-complete: its force-direct, shader-uncached `full` contract and 1,183-ID MISS/HIT evidence are unchanged.
Phase 2B now has a pure shadow-only explicit-manifest mapper: an unconditional host stage whose production result is `full-release` fallback with execution/runtime authority false and selection/cache authority `NONE`; it has no discovery, process/network, runner, pruning, cache or build-semantic authority.

The post-integration inventory is 57 entrypoints/18 exclusions. Mapper 32/32, telemetry 24/24 and exact Fast receipt `gate-1787531806677934000-26322-0.json` passed in 899.248371 s at unchanged HEAD `4edd85a04fee02df5a0330a8b737c00847a837a5`.
It recorded 811 direct PASS/63 stages; retail 102/102, archive 131/131, compile 279/279, low 2/2, SSAO 6/6, SSR 9/9, numeric 30/30, resources 13/13 and links 137/137.
Sol xhigh pre-review and final review returned `APPROVE — brak P0/P1/P2`.
Checkpoint `559817b375be1f34fff47fdfd2f1bf5f1a1631f8` passed matching clean
post-commit `full` in 969.618545 s: 1,244 direct PASS, 63/63 stages, zero
errors, uncached shaders and links 137/137; dSYM UUID
`FA8745D7-D795-38B0-8CF7-D8128E904457`. It was pushed and matches
`origin/ios-port`. This is host/build evidence only; IOS-P2-007 owns future
pruning and no install, phone, real Simulator or runtime claim follows.

## First commands
```bash
git status --short && (python3 misc/ios/active_gate.py check || { python3 misc/ios/active_gate.py generate && python3 misc/ios/active_gate.py check; })
./misc/ios/run_gate_logged.sh fast
./misc/ios/run_gate_logged.sh full   # required before push
./misc/ios/install_device.sh --preflight
```

Prepare an external cache, verify it, then use its published children with the
unchanged isolated Simulator runner:

```bash
SOURCE=/Volumes/DevArchive/OpenXRay/backups/5beca2ef4f034286be6299d6d9a2bdba-device-retail-backup-20260808-185559
SOURCE_MANIFEST=/Volumes/DevArchive/OpenXRay/backups/8039f43384784b879442f993dab281ae-device-retail-backup-20260808-185559.manifest
PREPARED=/Users/patryk/openxray-handoff/retail-prepared-YYYYMMDD-HHMMSS
python3 misc/ios/retail_import.py prepare --backup "$SOURCE" \
  --manifest "$SOURCE_MANIFEST" --repo "$PWD" --destination "$PREPARED" --with-saves
python3 misc/ios/retail_import.py verify --prepared "$PREPARED"
./misc/ios/retail_simulator.sh --backup "$PREPARED/Documents" \
  --manifest "$PREPARED/manifest" --with-saves \
  --autoload-save 'mobile user - beginning of the game'
```

The prepared root is a one-time resumable verified cache/source, not APFS
clone/no-copy staging. Physical iPhone builds and in-place installs do not
recopy retail `Documents`: stable Team/bundle keep the same container, and the
Mach-O UUID is unrelated. The isolated Simulator deliberately creates/deletes a
fresh Simulator and stages data on every run for evidence isolation.
An interrupted prepare preserves verified prefix bytes but revalidates the
source and prefix; only an idempotent prepare of the published root avoids that
validation and copy. A separately reviewed local APFS clone/cache slice may
later reduce the remaining 4.3 GiB per-run Simulator staging cost without
weakening fresh-Simulator isolation or source immutability.

Only one worker may own the device and shared iOS build tree. Acquire the shared
lease immediately before a concrete physical-device command; never use the
phone for this host-only M6 evidence.

## Validation contracts

| Contract | Required result |
|---|---|
| Retail importer | mutation/recovery 21/21 |
| Retail Simulator | 102/102 |
| DevArchive policy | 131/131 |
| Shader stages | 279/279 |
| Variants | low 2/2; SSAO 6/6; SSR 9/9; numeric macros 30/30, debt 0+0 |
| Shader resources | CPU→allocation/downsample/water/SSR 13/13 |
| Shader links | 137/137 |
| Binary | arm64, IOS, minOS 16.4; LuaJIT interpreter mode |

## Still pending

- M6: authorized tester, legal boundary, signed-update data preservation and remote release; App Store excluded.
- Wider save/level and transition coverage, then lock/held-touch and dedicated-helper audio acceptance.
- Frame pacing, 30-minute memory/thermal budget, dense UI/texture/SSR frames and remote CI miss/hit.

## Next slice

1. Extend IOS-P0-003 to another outdoor save, an indoor/portal start and one level transition with exact epoch/sector plus objective frames.
2. Keep the cooled 30-minute memory/thermal gate separate; do not restore full prefetch.
3. Phase 2B remains published and non-authoritative; keep prepared retail cache distinct from distribution and device-save evidence.

## Safety

- Never restore full level prefetch; it previously caused about a 1.6 GiB spike.
- Do not conflate sector and SSAO defects or replace them with brightness, streaming, ANGLE or Metal workarounds.
- Do not revoke certificates, change identity, uninstall or erase the device container.
- Rendering claims require an on-device frame or numeric probe; a clean GL log is not visual proof.
- Never install after a stale matching gate; append new evidence to the Journal.
