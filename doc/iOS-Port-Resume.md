# OpenXRay iOS — operational handoff

**Updated:** 2026-08-24. **Docs:** [canonical](iOS-Port.md), [active](iOS-Port-Plan.md), [deferred](iOS-Port-Backlog.md).

Read this file completely, then the relevant active task/canonical section; search the Journal by task ID or exact symptom, never in full.

## Current checkpoint

The M3 Pro host runs macOS/Xcode/SDK 27.0 beta and CMake 4.4.0; device and
Simulator engines build arm64 for iOS 16.4+. Team `RMJWWPF379` signs stable
`io.github.tryk016.openxray.RMJWWPF379`, preserving the device data container.
The physical-device baseline is GLES 3.0 at a real 1864×860 drawable with 1:1 presentation and Bluetooth controller. Sector fallback and SSAO value-macro fixes are proven; a later dark-frame smoke remains unresolved.

IOS-P0-003's hardened iOS 27 F5/F9 Simulator packet passed: workroot
`simulator-work-20260810-164020-71268`, report
`27f89f6793ec2d0f0ee6831add123ae3e54b9d83eaaa37d4326a62d052f35cb9`, manifest
`7d45f49bbe15727a1813971b958fe8c19261b32cc3426a7bfd69dffbdd418896`, PID 77340.
It records `level_load/exact` frame 35 then `quick_load/retained` frame 122; B0/B1/C are 1864×860 frames 89/94/182, tokens 54/58/118 and F5/F9 scancodes 62/66. This is Apple Software Renderer control-flow evidence only, not iPhone, pixels, readability, lighting or performance proof.

M6-local importer is complete but M6 remains active as Plan task 5. Standalone
stdlib `misc/ios/retail_import.py` passes 21/21 mutation/recovery tests:
descriptor-relative confinement, immutable all-file SHA plan, byte-range prefix
resume (real 600 MiB sparse fixture at an interior offset), nonblocking lock,
private permissions, exclusive rename, fsync/recovery and exact final/legacy
guard verification. The reviewed full source and independent manifest are now
on exact-UUID APFS `DevArchive` as transactions `5beca2ef…bdba` and
`8039f433…81ae`; both verify PASS and their local sources are receipt-bound
zero tombstones. Prepared root with saves
`/Users/patryk/openxray-handoff/retail-prepared-20260810-211114` is verify and
idempotent-prepare PASS: only `Documents`+`manifest`, 13 files and
4,611,922,289 prepared bytes; required `resources.db0`–`resources.db4` and
`levels.db0`–`levels.db1` exist. Its manifest digests are
`3dcd34c5934e1bc04b8dfb91873f48e82efe514f1aaf774ad685c09d2f1ae6c8` and
`3008f88f4b658bf0aa89b64b9701448a253ba91fddb9a4bebeb66d86afe66f18`.
An interrupted prepare revalidates the source and existing prefix, preserves
the valid copied prefix and transfers only the remainder. An idempotent prepare
of an already published root avoids source validation and copying. This is not
yet APFS clone/no-copy staging: each deliberately fresh Simulator still receives
its own 4.3 GiB copy so evidence runs remain isolated and the source stays
immutable. Eight completed archive retirements reclaimed 5,750,628,350 logical
bytes; the prepared cache remains local and verifier PASS. No phone, Simulator
or installed app container was touched.

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
- Phone lighting A/B, more content/QuickLoad/transition, lock/held-touch and dedicated-helper audio acceptance.
- Frame pacing, 30-minute memory/thermal budget, dense UI/texture/SSR frames and remote CI miss/hit.

## Next slice

1. Do not treat the Phase 2B mapper as selection authority. IOS-P2-007 is deferred until an authenticated complete manifest, real/mutation zero-false-negative corpus and full-release completeness proof exist.
2. Resume active IOS-P0-003/M6 or another explicitly promoted Plan task; keep lock/held-touch/audio deferred until promoted, without repeating the accepted five-cycle app-switch proof.
3. The Phase 2B checkpoint is published. Keep the prepared cache distinct from legal distribution and device transfer/save-preservation evidence.

## Safety

- Never restore full level prefetch; it previously caused about a 1.6 GiB spike.
- Do not conflate sector and SSAO defects or replace them with brightness, streaming, ANGLE or Metal workarounds.
- Do not revoke certificates, change identity, uninstall or erase the device container.
- Rendering claims require an on-device frame or numeric probe; a clean GL log is not visual proof.
- Never install after a stale matching gate; append new evidence to the Journal.
