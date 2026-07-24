# OpenXRay — iOS port

Canonical specification for the OpenXRay iOS project.

**Last synchronized:** 2026-07-24

**Status:** playable development build; startup geometry and global-lighting
blockers fixed, reliability validation in progress

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

It is not release-ready. The startup geometry and global ambient-lighting
blockers are fixed, but the port still needs multi-level validation, memory
budgets, lifecycle hardening and a repeatable performance baseline.

## Supported baseline

| Area | Contract |
|---|---|
| OS | iOS 16.4 or newer |
| Architecture | arm64 |
| Game | Call of Pripyat 1.6.02 |
| Renderer | Native OpenGL ES 3.0 through Apple's GL-on-Metal implementation |
| Shaders | Runtime GLSL ES 3.00; offline gate 279/279 compile, 2/2 low-settings, 6/6 SSAO resource branches plus value-macro contract, and 137/137 link |
| Lua | LuaJIT interpreter mode; JIT disabled by default |
| Build host | Apple M3 Pro, macOS 26.5.2, Xcode 26.6 (17F113), iPhoneOS/iPhoneSimulator SDK 26.5, CMake 4.4.0 |
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
- Key-binding values now use `letterica18`; controller-binding conflict updates
  stay in the controller group instead of incorrectly notifying keyboard rows.
- The controller focus frame is a stable gold affordance. The earlier flashing
  cyan/yellow frame, bracket markers and per-input diagnostic logging are gone.
- Controller focus now scrolls an off-screen inventory cell into view and the
  gold frame reconstructs clipping for inventory/list/scroll parents. Both
  changes are build-verified and await a dense-inventory device pass.

## Renderer decision

Native OpenGL ES remains the reference backend until the game is stable and
measured. It already renders the menu and world, so it provides the cheapest path
to diagnosing engine-level defects.

ANGLE-on-Metal would replace Apple's GL implementation but would not change the
engine's sector detection, resource policy or memory model. A native Metal
backend would be a larger renderer project. The former missing-geometry blocker
was fixed in shared renderer logic and did not require either migration.

The deferred Metal proposal is documented in
[iOS-Metal-Plan-Draft.md](iOS-Metal-Plan-Draft.md). Its start gate is defined in
the active plan.

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
  quality/aniso, MSAA, sun shafts, volumetrics, wet surfaces and reflections for
  the missing-geometry defect. The old “SSAO off” result was later proven invalid
  by the separate macro bug described below.

### Remaining validation

The affected save is fixed across three launches. Before closing release
validation, repeat fresh loads across at least one additional save and level,
verify indoor/portal spawn points, and run a memory/lifecycle soak. Full prefetch
remains disabled because it previously added an approximately 1.6 GB transient
spike.

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

- Full gate: 279/279 compile, 2/2 low-settings compile, 137/137 links, arm64
  engine build.
- Normal, non-diagnostic build: correct global lighting before and after a
  nine-second scripted walk.
- Two additional cold launches reproduced the correct frame.
- Final profile retains SSAO high, G-buffer optimization on, MSAA 2× and full
  texture quality; no brightness multiplier workaround is required.

## Memory model

The source assets use desktop DXT/BC compression. On the active ES path,
unsupported compressed textures fall back to CPU decoding and RGBA8 upload. That
can consume four to eight times the compressed source size.

Until a compressed mobile format or better streaming policy exists:

- non-UI texture top mip is capped;
- full level prefetch is disabled;
- memory measurements must use current physical footprint, not only peak RSS;
- `SDL_APP_LOWMEMORY` handling and bounded cache eviction remain required.

ASTC is a future optimization, not an implemented feature.

## Build and device loop

The local Apple Silicon toolchain was rebuilt and revalidated from source on
2026-07-24. Both device and simulator dependency prefixes contain arm64 SDL2,
OpenAL Soft, Ogg/Vorbis/Theora and LZO archives. The complete device and
simulator engines build for an actual iOS 16.4 deployment target. LuaJIT's host
generators are macOS arm64 tools, while its target archives are iOS arm64.
Non-interactive gates prefer `/opt/homebrew/bin` so a migrated Intel Homebrew
installation cannot silently select x86_64 tools.

From the repository root:

```bash
./misc/ios/build_check.sh
./misc/ios/install_device.sh --diagnostics
./misc/ios/input.sh w 9000
./misc/ios/shot.sh /tmp/openxray-frame.png
./misc/ios/install_device.sh --launch
```

`build_check.sh` is mandatory before installing any engine or shader change.
`--diagnostics` is the one-command unattended-test mode. It writes
`ios_diagnostics 1`, enabling both cable input polling and the five-second frame
capture. The cadence uses continual time, so it advances while Options pauses
game time. `shot.sh` waits for a new generation token, or two tokens after an
uncertain first cable read, and refuses stale output.
Ordinary `--launch` writes `ios_diagnostics 0`; normal gameplay therefore does
not poll the trigger file and performs no periodic framebuffer readback. A
failure to read or write the configuration aborts launch instead of inheriting
an unknown mode.

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
conservative 100 MiB `__debug_info` floor. The clean Xcode 26.6 checkpoint
passes that contract. Exact artifact sizes and UUIDs belong in the append-only
journal because they change on every link. GitHub Actions is no longer the
primary developer loop.

Local installation preserves the existing asset/save container by using:

```text
Team ID: RMJWWPF379
Bundle ID: io.github.tryk016.openxray.RMJWWPF379
```

Do not revoke certificates or change that installed bundle identifier as a
routine signing fix.

## Known open work

| Priority | Area | Current requirement |
|---|---|---|
| P0 | Validation | Verify startup-sector fallback across saves/levels and complete a memory-safe soak |
| P1 | Memory | Add a measurable budget, current-footprint telemetry, LOWMEMORY eviction |
| P1 | Lifecycle | Stop GL work in background and recover safely on foreground |
| P1 | CI | Run a deliberate negative shader/varying canary in Actions and monitor the verified dSYM artifact on the first remote run |
| P1 | Shaders | Audit zero-default ES feature macros for remaining presence tests before enabling HBAO/alternate SSR paths |
| P1 | Diagnostics | Measure normal-mode performance and prove no autonomous polling/readback overhead |
| P1 | Presentation | Measure the fixed 1864×860 1:1 path and define FPS/thermal targets; validate Advanced/Controls and dense retail UI independently |
| P1 | Environment textures | Prove and fix cubemap alias rebinding when `surface_set()` changes a GL texture ID behind a cached `CTexture*` |
| P1 | macOS 27 qualification | Revalidate build/sign/install, determine a usable GPU capture path and bring up repeatable XCUITest menu automation after the host update |
| P2 | Touch | Finish touch cancellation and optional virtual gameplay controls |
| P2 | Audio | Prefer the built OpenAL Soft library over deprecated Apple OpenAL |
| P2 | Visibility | Replace the disabled iOS occlusion-query path |
| P2 | Texture color | Preserve or deliberately replace BC/DXT sRGB and swizzle semantics in the iOS decode fallback |
| P2 | Tooling | Make the local installer configurable and team-aware |

The prioritized task list and acceptance criteria live in
[iOS-Port-Plan.md](iOS-Port-Plan.md).

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
- [iOS-Port-Resume.md](iOS-Port-Resume.md) — current handoff and first commands.
- [iOS-Port-Journal.md](iOS-Port-Journal.md) — append-only development evidence.
- [iOS-Port-Audit-2026-07-17.md](iOS-Port-Audit-2026-07-17.md) — historical audit,
  explicitly superseded.
- [iOS-Controller-Prior-Art.md](iOS-Controller-Prior-Art.md) — touch/controller
  design reference.
- [iOS-Metal-Plan-Draft.md](iOS-Metal-Plan-Draft.md) — deferred renderer RFC.
