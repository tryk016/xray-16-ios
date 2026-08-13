# OpenXRay iOS — operational handoff

**Updated:** 2026-08-13
**Docs:** [canonical](iOS-Port.md), [active](iOS-Port-Plan.md), [deferred](iOS-Port-Backlog.md).

Read this file completely, then the relevant active task/canonical section;
search the Journal by task ID or exact symptom, never in full.

## Current checkpoint

The M3 Pro host runs macOS/Xcode/SDK 27.0 beta and CMake 4.4.0; device and
Simulator engines build arm64 for iOS 16.4+. Team `RMJWWPF379` signs stable
`io.github.tryk016.openxray.RMJWWPF379`, preserving the device data container.
The physical-device baseline is GLES 3.0 at a real 1864×860 drawable with 1:1
presentation and Bluetooth controller. Sector fallback and SSAO value-macro
fixes are proven; a later dark-frame smoke remains unresolved.

IOS-P0-003's hardened iOS 27 F5/F9 Simulator packet passed: workroot
`simulator-work-20260810-164020-71268`, report
`27f89f6793ec2d0f0ee6831add123ae3e54b9d83eaaa37d4326a62d052f35cb9`, manifest
`7d45f49bbe15727a1813971b958fe8c19261b32cc3426a7bfd69dffbdd418896`, PID 77340.
It records `level_load/exact` frame 35 then `quick_load/retained` frame 122;
B0/B1/C are 1864×860 frames 89/94/182, tokens 54/58/118 and F5/F9 scancodes
62/66. This is Apple Software Renderer control-flow evidence only, not iPhone,
pixels, readability, lighting or performance proof.

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

After historical `8b265b07b`, only Finder's 6,148-byte `.DS_Store` was removed;
all 13 retail files verify. Overlay `ba2e07e05` plus supplement `ab1ba7f45` are
committed; real `HISTORICAL_PASS` remains read-only closeout work. `727c51da`
closed its push gate but is stale for install. IOS-P1-002 is Backlog-pending.

## Host feedback checkpoint

Phase 0 is complete. Phase 1A freezes all 102 byte/AST-identical retail IDs in
a versioned fail-closed manifest: 53 guards, 49 integration, and `host-fast` 55
(guards plus exact happy/fail-closed smokes). Profiles are `complete` (default),
`host-fast`, `retail-integration`; authority is `NONE`, input mapping false.
`shaders/device/full/fast` still run all 102; `engine` retains non-shader scope,
and the new contract runs in all five. No FastDevice switch, fixture extraction,
parallelism or cache planner exists.

Focused 5/5, 24/24, 14/14 PASS; single runs: `host-fast` 55/55 in 48.164 s,
integration 49/49 in 529.911 s, full 102/102 in 590.178 s. Fast receipt
`gate-1786626921163957000-41895-0.json` passed in 767.549 s on unchanged dirty
HEAD `14d8973e`: COMPLETE, 1,156 cases/58 stages, zero errors; not a clean
commit/full/push/install stamp. Sol xhigh: `APPROVE — brak P0/P1/P2`. These are
not p50/p95, speedup, phone, Simulator/runtime or rendering proof.

Pushed `727c51da` receipt `gate-1786622784219994000-30295-0.json`: 826.999 s, unchanged source, COMPLETE,
1,151 cases/57 stages, retail 102/102 in 591.940 s, archive 131/131, uncached
shaders 279/279 plus profiles, links 137/137 and 68 TUs PASS. UUID
`8F350949-A69B-3A73-933E-EBC5414055E3`, stamp `9ac7d8a1…aabaf`. It authorized
that completed push only and is historical for future install after this docs
change; no phone/runtime evidence.

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
- Phone lighting A/B, more content/QuickLoad/transition, and IOS-P1-002 lifecycle/audio acceptance.
- Frame pacing, 30-minute memory/thermal budget, dense UI/texture/SSR frames and remote CI miss/hit.

## Next slice

1. Phase 1B: extract fixtures preserving bodies/IDs, then 20 warm `host-fast` PASS targeting p50 60–90 s/p95 ≤120 s.
2. When the phone returns, restore IOS-P1-002 and run its lifecycle batch with controlled lighting.
3. Keep M6 active; the cache is not legal distribution or device transfer/save-preservation evidence.

## Safety

- Never restore full level prefetch; it previously caused about a 1.6 GiB spike.
- Do not conflate sector and SSAO defects or replace them with brightness, streaming, ANGLE or Metal workarounds.
- Do not revoke certificates, change identity, uninstall or erase the device container.
- Rendering claims require an on-device frame or numeric probe; a clean GL log is not visual proof.
- Never install after a stale matching gate; append new evidence to the Journal.
