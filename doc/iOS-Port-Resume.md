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

Commit `8b265b07b489f1633e6be17f57fd1578adb11092` received a clean full PASS:
`gate-1786570175187364000-15196-0`, log `fd07567c…95e71`, source
`d8b2fc5d…f394`, UUID `574CACAC-E835-3E81-9919-60F8B93343DE`, bundle
`c6dbaa33…9a0c`, stamp `54c54c5c…b59a5`; archive 131/131, Simulator 102/102
and established graphics/link contracts passed. Real read-only historical
verify then failed closed only on the documented removal of Finder's 6,148-byte
`.DS_Store`; the selected 13-file importer verification remains PASS. An exact
non-authorizing metadata overlay now binds old/new manifests, sole deletion,
root inode/metadata and unchanged entries without relaxing production paths.
The overlay is committed in `ba2e07e05` and its inventory supplement in `ab1ba7f45`;
a fresh clean full gate and real `HISTORICAL_PASS` still remain required before push or install.

The earlier M6-local importer review returned `APPROVE — brak P0/P1/P2`; it did
not pre-approve the later DevArchive retirement or this documentation closeout.
IOS-P1-002 is temporarily Backlog-pending until the phone returns.

## Host feedback checkpoint

Phase 0A commit `f7b105b61` froze deterministic IDs/hashes; catalog validation
is unchanged and tooling remains 24. Reviewed, implemented Phase 0B is fail-open,
signed-private case/stage telemetry with profile/input/cache/shard fields,
isolated unlinked raw sink and authoritative OFF/ON parity. Gate
`gate-1786607468382948000-67713-0.json` passed in 755.539 s: 1,151/1,151 cases,
48/48 stages, zero errors/residue and 12 receipt fields; no nonce persisted;
`selection_authority=NONE`, `cache_authority=NONE`. Sol xhigh: `APPROVE — brak P0/P1/P2`. Scope is
host/shader coverage, not Simulator/device runtime or rendering. Next: 10 warm
unchanged-selection host measurements for p50/p95, then the one-to-one Phase 1 split.

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

1. Record 10 unchanged-selection warm host telemetry runs and p50/p95; do not claim speedup or alter selection first.
2. When the phone returns, restore IOS-P1-002 and run its lifecycle batch with controlled lighting.
3. Keep M6 active; the cache is not legal distribution or device transfer/save-preservation evidence.

## Safety

- Never restore full level prefetch; it previously caused about a 1.6 GiB spike.
- Do not conflate sector and SSAO defects or replace them with brightness, streaming, ANGLE or Metal workarounds.
- Do not revoke certificates, change identity, uninstall or erase the device container.
- Rendering claims require an on-device frame or numeric probe; a clean GL log
  is not visual proof.
- Never install after a stale matching gate; append new evidence to the Journal.
