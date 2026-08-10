# iOS Port — Build Journal & References

Append-only devlog for the iOS port. One entry **per slice**: what was done, how,
and — when CI went red — the error, its root cause, and the fix. This is the
narrative complement to git history (searchable in one place, survives context
resets) and to [iOS-Port-Resume.md](iOS-Port-Resume.md) (which holds only the
*current* state). Newest entry first.

Companion docs: [iOS-Port.md](iOS-Port.md) (overview), [iOS-Port-Plan.md](iOS-Port-Plan.md)
(58-task plan), [iOS-Port-Resume.md](iOS-Port-Resume.md) (resume guide),
[iOS-Controller-Prior-Art.md](iOS-Controller-Prior-Art.md) (Phase 5 controls).

## How we work (the loop)

1. Make the smallest self-contained change (one slice).
2. **Run `./misc/ios/build_check.sh` and get PASS. This is mandatory before any
   push that touches engine code or shaders — it is not optional and it is not
   "probably fine".** The whole gate is ~38 s (shader compile gate, shader link
   gate, incremental arm64 build). A patch that has not been compiled is not a
   finished patch. See slice 6.12.
3. To see it on the phone: `./misc/ios/install_device.sh --launch` (~15 s, signs
   with our own certificate and installs over the cable). No SideStore, no
   Sideloadly. If the app stops launching after a week, the 7-day profile
   lapsed — re-run the script. See slice 6.13.
4. Commit with a message that states **what + why + how any error was fixed**.
5. Push to `ios-port` → the `iOS` workflow re-validates on **GitHub macOS
   runners**. CI is now the *clean-room* check and the producer of the SideStore
   `.ipa` — it is no longer the only validator and no longer the fast path.
6. Read the CI result; fix red as its own micro-slice. **End every slice green.**
7. Add a journal entry here. Docs-only commits skip CI (`paths-ignore`), so
   journalling is free.

**Note for agents.** Every slice up to and including 6.11 was written on a
Windows box that physically could not build iOS, so patches were verified by
*reading code only* and syntax errors surfaced ~18 minutes later in CI. That
constraint is gone (2026-07-19, slice 6.12). If you are about to write "verified
by reading — cannot compile in this environment", you are working from a stale
brief: compile it.

## Authoritative references (consult first — don't guess)

We diagnose from **primary sources**, not memory. When a question comes up, the
answer is almost always in one of these:

- **leetal ios-cmake** (the toolchain we vendored at `cmake/toolchains/ios.toolchain.cmake`):
  <https://github.com/leetal/ios-cmake> — `PLATFORM` values (OS64/SIMULATORARM64/…),
  options, and *how it sets `-target`/`-isysroot`/`CMAKE_OSX_SYSROOT`* (crucial: it
  behaves differently for the Xcode vs Makefiles generator — see gotchas).
- **LuaJIT install / cross-compile**: <https://luajit.org/install.html> — the
  `HOST_CC` / `CROSS` / `TARGET` model that `Externals/LuaJIT-proj/CMakeLists.txt`
  replicates (native host codegen tools, cross target lib).
- **SDL2 for iOS**: `Externals/SDL/docs/README-ios.md` /
  <https://github.com/libsdl-org/SDL/blob/SDL2/docs/README-ios.md> — `SDL_main`,
  the UIKit/`UIApplicationMain` bootstrap and `CADisplayLink` run loop (Phase 3 input).
- **OpenXRay build wiki**: <https://github.com/OpenXRay/xray-16/wiki> — canonical
  desktop build steps, submodule requirements, game-data layout (CoP 1.6.02).
- **Apple**: `<TargetConditionals.h>` macros (`TARGET_OS_IOS`/`TARGET_OS_SIMULATOR`),
  iOS SDK; sideload distribution via **SideStore/AltStore** (unsigned `.ipa`,
  on-device re-sign, `get-task-allow`).
- **Library docs on demand**: the **context7 MCP** server
  (`resolve-library-id` → `query-docs`) for any third-party lib API/config.

## Recurring gotcha classes (each has bitten us at least once)

- **CMake 4.x** rejects `cmake_minimum_required(VERSION < 3.5)` →
  pass `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`.
- **Xcode 26.5 clang** promotes new warnings to hard errors (e.g.
  `-Werror=function-effects`) → pre-seed the submodule's `HAVE_*` probe var OFF;
  watch for other `-Werror=…` in third-party code.
- **Host vs target tools**: anything that must *run at build time* (LuaJIT's
  `minilua`/`buildvm`) must be built **native macOS**
  (`--target=<host>-apple-macos`, `-DCMAKE_OSX_SYSROOT=macosx`), never for iOS.
- **Xcode generator vs raw `execute_process`**: with `-G Xcode` the leetal toolchain
  applies `-target`/`-isysroot` through **Xcode build settings, not `CMAKE_C_FLAGS`**.
  So any `execute_process(${CMAKE_C_COMPILER} ${CMAKE_C_FLAGS} …)` (arch probes,
  config tests) gets **no sysroot/target** under Xcode and must pass
  `-isysroot ${CMAKE_OSX_SYSROOT}` / `--target=${CMAKE_C_COMPILER_TARGET}` itself.
- **Desktop glad FLAGS are 0 under `gladLoadGLES2`** — not just the function pointers.
  Any `HW.Caps` capability derived from `GLAD_GL_VERSION_*` / `GLAD_GL_ARB_*` (e.g.
  `bVTF` from `GLAD_GL_VERSION_3_0 || GLAD_GL_ARB_texture_float`) is **silently false
  on iOS**, and every feature it gates silently disappears with no GL error (the
  black-sky bug: the bVTF gate skipped binding the sky cubemaps entirely). When a
  feature "does nothing" on iOS with a clean log, grep what its code path is gated on.

---

## Journal

### 2026-07-19 (night) — Slice 6.14: the device becomes testable without a human — and the "lighting bug" turns out not to be one

**Two outcomes. A closed-loop test harness, and a root cause that is nothing like
what the symptom suggested.**

#### The harness: launch → play → look, with nobody holding the phone

| what | where |
|---|---|
| frame dump every 5 s to the app container | `xrRenderGL/glHW.cpp`, in `CHW::Present` |
| pull + convert that frame to PNG | `misc/ios/shot.sh` |
| synthetic held key ("walk forward 9 s") | `xrEngine/xr_input.cpp`, `CInput::KeyUpdate` |
| send an input command over the cable | `misc/ios/input.sh` |

```
./misc/ios/install_device.sh --launch bin/aarch64/Release/xr_3da.app
./misc/ios/input.sh w 9000
./misc/ios/shot.sh /tmp/frame.png
```

The frame dump reads the *same* render target the existing present-probe samples,
at the same point in `Present`, so what lands on disk is exactly what reached the
screen. Input is forced into `keyboardState` just before the `IR_OnKeyboardHold`
loop, so it travels the ordinary input path and is indistinguishable downstream
from a real held key. It is file-driven rather than compiled-in, so changing the
sequence costs a `devicectl` push instead of a rebuild.

**Two human gates removed, neither needing code.** `keypress_on_start` is already a
console variable (`console_commands.cpp:2623`); at 0, `game_loaded()` never builds
the "press any key" sequencer and the load screen stops on its own
(`GamePersistent.cpp:530-533`). And `user.ltx`'s `start server(...)` line boots
straight into the save. **The engine already supported unattended boot — it only
had to be found.**

**That autoload line is fragile, and it burns runs silently.** The engine drops it
whenever it saves settings — twice in one session, once costing an agent twelve
polls against a main menu it believed was the game. `install_device.sh` now
re-asserts both settings on every `--launch`. Negative-tested: config deliberately
sabotaged (autoload removed, `keypress_on_start 1`), repaired by the script, and
the app booted into gameplay.

#### The payoff, immediately

The first captured frame killed the working hypothesis. The world was **not**
uniformly dark: sky, clouds, weapon and vegetation all rendered correctly, and only
the **terrain** was flat. Half an hour of tooling replaced an hour of "is the sky
bright? what shape is the lit part?".

Then the user's clue — *time doesn't clear it, walking does* — became measurable for
the first time: **2.5 s of injected walking changed nothing; 9 s cleared the defect
completely.** Not a per-frame recompute. A distance threshold.

#### Root cause: distant terrain is missing from the gbuffer

Not a lighting bug, not a shadow bug. Measured on device, before vs after a walk:

- Terrain view-space `P.z` varies with distance **in both states** — the geometry
  that is present was always fine.
- Far-field sample points go from `mk=0.0` (**the sun pass never rasterized there**)
  to `mk=1.0`, and the far row's depth from a pinned `15.00` to a real, varying
  `33.69 / 17.27 / 6.51`.

Those pixels were never mis-lit; **they were not drawn at all**, and so received
ambient only. Everything measured earlier fits without contradiction: `ref`
clustered in [0.94, 0.965] because only near geometry existed; the depth comparison
was correct throughout; albedo and normals were healthy because the geometry that
existed was healthy.

**Leading hypothesis, explicitly NOT proven:** the skipped level prefetch
(`IGame_Persistent.cpp:385`, slice 4.25 anti-jetsam) means content arrives lazily.
Untested: whether the transition tracks distance travelled, a sector/portal
crossing, or a named streaming event. That is the next slice.

#### Four wrong turns, and what each cost

1. **Two patches to `shadow.h`** (emulating `CLAMP_TO_BORDER`, then forcing
   `shadow()=1`) — both reverted. The shadow path was never at fault.
2. **"The lit circle must be a point light."** It is the region where the broken
   state happens to approximate reality. A *shape* was promoted to decisive
   evidence; it was an artefact of the cause, not a clue to the mechanism.
3. **"Terrain `P.z` is constant"** — withdrawn by the agent that found it, once a
   corrected sampling grid showed two of its three rows had been landing on sky.
4. **A weather-confounded test.** Forcing `shadow()=1` under `default_cloudy`, where
   `sun(0.05 0.04 0.01)`, cannot brighten anything. The user caught it, not me.

The pattern behind 1 and 2: **verify a file is on the runtime path before patching
it, and don't promote a visual impression to a mechanism.** `accum_sun.ps` never
calls `shadow()` — one grep would have saved two build-install-inspect cycles.

#### Tooling gotchas that produced confidently wrong results

- **`build_check.sh --shaders` does not copy gamedata into the app bundle** — only
  the engine build step does. A shader edit tested that way silently measures the
  *old* shader. Run `--engine` (or the full gate) after touching a shader, verify the
  text is really in `bin/aarch64/Release/xr_3da.app/gamedata/...`, and after
  installing pull it back off the device.
- **`./misc/ios/build_check.sh | tail -3 && ./misc/ios/install_device.sh`** takes
  `tail`'s exit status, not the gate's — this installed a build that had failed to
  compile. The gate's whole value is one meaningful exit code; do not pipe it away.
- **Two agents building the same tree collide.** One build failed spuriously and one
  install killed another session's running game. Assign the cable and the build tree
  to one worker at a time.

### 2026-07-19 (night) — Slice 6.13: install over the cable, solved — we sign it ourselves

**6.12 deferred cable install as blocked by the App ID quota. That conclusion was
wrong, and one of its premises was already false when written.** Installing now
works, takes ~15 s, and never touches the quota. `misc/ios/install_device.sh` is
the whole loop.

**Correction to 6.12: the private key *is* on this Mac.** 6.12 recorded that the
only `Apple Development` certificate in the keychain was SideStore's, with no
matching private key, so nothing could be signed locally. But the
`-allowProvisioningUpdates` attempt that 6.12 describes as failing had in fact
*already succeeded at minting a fresh key + certificate* before it died at the
App ID step:

```
CN = Apple Development: tryk016@gmail.com (C4MLW25CWH)
OU = RMJWWPF379   (same personal team as SideStore)
serial 7946D0E0F154680563D36AFC9E013791
notBefore 2026-07-19 13:39 GMT, valid one year
```

`security find-identity -v -p codesigning` lists it — and `-v` lists *only*
identities that have a usable private key. 6.12 read the situation from the
certificate it expected to find rather than from the keychain as it stood after
the attempt. **Lesson: re-read the machine state after a failed attempt; a
command that exits non-zero may still have completed several of its steps.**

**The actual discovery: the App ID quota is charged on *creation*, not on use.**
"Maximum App ID limit reached — 10 every 7 days" fires while *registering a new*
App ID. Requesting a provisioning profile for an App ID that already exists costs
nothing. SideStore already registered
`io.github.tryk016.openxray.RMJWWPF379`, so signing under **that exact
suffixed id** sidesteps the quota entirely. 6.12 hit the wall only because it
used a fresh bundle id.

Verified on a throwaway stub (now vendored at `misc/ios/provisioning-stub/`, so
renewal is reproducible rather than remembered): `xcodebuild
-allowProvisioningUpdates` with that bundle id returned **BUILD SUCCEEDED**, zero
mentions of the limit, and downloaded a profile carrying our device UDID, our
cert, and `get-task-allow`.

**Sideloadly is a dead end — and an instructive one.** It fails with *"there is
no iOS certificate with serial number …"*. Cause: it lists the account's
certificates from Apple's portal and matches them against the local keychain.
SideStore's certificate is on the portal but its private key never was on this
Mac, so the match fails. Every guide answers this with *revoke your
certificates* — **do not**. Revoking SideStore's cert would kill the working
SideStore installs of **both OpenXRay and OpenGothic**. The tool was never the
problem; it was solving a problem we no longer had. (Its only real defect was a
Chrome quarantine flag, cleared with `xattr -d com.apple.quarantine`.)

**`misc/ios/install_device.sh` — sign + install + launch.** Finds the newest
`.ipa` in `~/openxray-handoff/local-builds`, applies the suffixed bundle id
(the `.ipa` ships with the bare one, because SideStore used to append the suffix
at install time), embeds the profile, signs with entitlements *extracted from
that profile*, installs via `devicectl`, optionally launches.

```
./misc/ios/install_device.sh --launch     # ~15 s, sign + install + launch
./misc/ios/install_device.sh --renew      # refresh the profile only
```

It renews the profile automatically when none is valid, so the weekly expiry is
self-healing. Both paths were tested for real: a full install (10024082 →
**10033000**, launched, PID confirmed), and renewal with the profile deleted to
simulate expiry (fetched a fresh one, no quota error).

**The binding constraint is now the 7-day profile, not the quota.** Free personal
teams issue 7-day profiles. The app will simply stop launching when it lapses —
that symptom means *run the script again*, not *something regressed*. Renewal is
unlimited and quota-free.

**Nothing was lost.** Save games survived the overwrite
(`savedgames/mobile user - beginning of the game.scop`, 617 KB, still on device),
`gamedata` intact, OpenGothic still installed, SideStore's certificate untouched.
The pre-attempt backup at `~/openxray-handoff/device-backup-2026-07-19/` was
never needed.

**Two rules to carry forward** (both encoded in the script's header):
1. Sign under `io.github.tryk016.openxray.RMJWWPF379` — keep the `.RMJWWPF379`
   suffix. Drop it and you mint a new App ID and hit the quota.
2. Never revoke certificates to fix signing. That trades a convenience for two
   working installs.

**RESOLVED (same session, from the logs): "build 10024084 verified live" was
false.** The pre-overwrite log shows the device was running *GitHub Actions build
82, commit `01ae8f793`* — **slice 6.9**. So slices **6.10 and 6.11 had never run
on the device at all** before this install; the resume note claimed a device
verdict that never happened. Both are only now getting their first real device
run. Treat every "verified on device" claim in the 6.9-6.11 range as unproven
unless a log backs it.

That first run also gave 6.11 its genuine verdict — **it works**: the menu probe
pixel moved from `(0,118,0)` (the green-quads bug) to `(19,17,17)`, and the OGM
surfaces decode (`ui\video_voroni_crop`, `ui\video_water_crop`). And it surfaced a
**new** regression: in-game the world is white for a while and then swings to
black — the tonemap adaptation loop now runs but converges wrong. Own slice.

**Apple Developer Program membership (approved 2026-07-19): there is no migration
— the same team was upgraded in place.** Membership details report **Team ID
`RMJWWPF379`**, i.e. the *same* team we had been calling "the free personal team".
Enrolling did not create a second team, so the bundle id, the App ID, the
certificate and the data container all carried over untouched, and profiles are
now **year-long** (verified: a freshly issued profile runs 2026-07-19 →
**2027-07-19**, against 7 days before). The weekly re-sign is gone.

*This corrects a wrong conclusion reached earlier in this same session* (and
stated in the commit that introduced this addendum): that a paid team would force
a new bundle id and a new container, costing a ~4.6 GB re-push. That reasoning —
bundle ids are globally unique across teams, the free team holds this one, free
teams have no portal UI to release it — is individually true but was applied to a
two-team scenario that does not exist here. **Lesson: establish whether the new
team IS a new team before reasoning about moving between teams.** One lookup of
the Team ID would have skipped the entire analysis.

**The device holds the only local copy of the CoP assets.** The ~4.6 GB in the
app container is the retail 2009 data (`levels.db0/1`, `resources.db0-4`,
localization, patches). It is *not* on this Mac — `res/gamedata` is 4 MB of engine
shaders and configs, and the `.ipa` ships only that. The owner's other copy is on
the old Windows box. **Back the container up before any migration or container-
destroying step.**

### 2026-07-19 (night) — Slice 6.12: the move to macOS — local builds, a 38 s pre-push gate, and cable access to the device

**Environment change.** The project moved from Windows to a MacBook (Intel i9
8-core, 32 GB, macOS 26.5.2, Xcode 26.4 / iPhoneOS 26.4 SDK). The automated setup
completed a **full local engine build** (`** BUILD SUCCEEDED **`,
`bin/aarch64/Release/xr_3da.app/xr_3da`, 70 MB arm64). This slice turns that from
"it builds" into a working method, and answers the three questions the move was
made to answer.

**Build timings (measured, not estimated).** Configure tree already present:

| what | time |
|---|---|
| full build (setup script, cold) | ~40 min |
| no-op rebuild | 19 s |
| one renderer `.cpp` changed + relink | **20.8 s** |
| **whole pre-push gate** (both shader gates + incremental build) | **37.9 s** |

The incremental cost is dominated by the thin-LTO relink of the 70 MB binary, not
by compilation — so 20 s is roughly the floor for *any* C++ change, and changing
ten files costs about the same as changing one. Compare: an `iOS` CI run is
~18 min. **The loop is ~28x faster.**

*Measurement trap worth recording:* the first two attempts measured 4.8 s and
looked like a triumph. They were nonsense — the path was guessed as
`src/Layers/xrRenderPC_GL/glTexture.cpp`, which does not exist (the sources live
in `src/Layers/xrRenderGL/`; `xrRenderPC_GL` is only the *build* directory name),
so `printf >>` silently created a new stray file and nothing rebuilt. `touch` on
a real file is also useless here — Xcode compares content, not mtime. To measure
or force a rebuild, make a real content change.

**New: `misc/ios/build_check.sh` — the mandatory pre-push gate.** Wraps the two
existing shader gates plus the incremental device build behind one command with
one meaningful exit code. `--shaders` / `--engine` narrow it when only one side
was touched. It refuses to configure (a missing build tree is a hard error, not a
silent 40-minute wait) and it prints only real `file:line:col: error:`
diagnostics — an early version grepped for bare "error" and drowned the failure
in the compiler command line, which contains `-Wl,-undefined,error`.
Negative-tested: a deliberate syntax error is caught and reported as
`glTexture.cpp:651:1: error: expected unqualified-id`. **The "How we work" loop
at the top of this file is updated: local gate first, CI second.**

**`tools/glslang/` arrived EMPTY** — the setup script created the directory but
never fetched the binary, so both shader gates were silently unrunnable on the
new machine. Fixed with `brew install glslang`, which supplies **16.4.0 — the
exact version the gates were tuned against** in slice 4.8, so there is no
validator-version drift. Both gates then reproduced their documented values on
the first run: **279/279 compile, 137/137 link.** `build_check.sh` prefers a
vendored `tools/glslang/bin/glslangValidator` and falls back to `PATH`.

**Also fixed:** `misc/ios/shadercheck/__pycache__/*.pyc` was tracked in git and
dirtied the working tree on every gate run; untracked, and `__pycache__/` +
`*.pyc` added to `.gitignore`.

#### The three questions the move was for

**1. Live device log — SOLVED, and it corrects a long-standing journal claim.**
This file has said since 4.10 that app `os_log` cannot be captured on iOS 15+.
That is true of **`idevicesyslog`** specifically, and it was over-generalised into
"there is no live log". It is wrong. What actually works:

- `pymobiledevice3 syslog live --match xr_3da` streams **our engine output live**
  — confirmed: `* CPU features: ARMSIMD, NEON`, `FS: 39310 files cached 12
  archives`, `-----loading .../system.ltx`. Installed into a venv
  (`pip install pymobiledevice3`, 9.36.0); not a repo dependency.
- macOS's own `log stream` does **not** support remote iOS devices any more
  (`unrecognized option --device-name` on macOS 26). Console.app remains the GUI
  route. Also note `log` is a **zsh builtin** — call `/usr/bin/log` or the shell
  eats the arguments with a confusing "too many arguments".
- Each engine line appears **twice** in the stream (once raw, once `[xr]`-prefixed).
- Filtering by `--match xr_3da` while the app is *not* running yields zero lines,
  which reads exactly like a broken tool. Launch first, then judge.

**2. Instruments — WORKS, and the memory work is unblocked.** `Allocations`,
`Leaks`, `Game Memory` and `Game Performance` templates are all present. A 12 s
attach produced a valid 41 MB `.trace`. **Attach by PID, not by name:** the
process is called **`OpenXRay`** (the display name), so `--attach xr_3da` fails
with "Cannot find process matching name". Working recipe:

```bash
DEV=088D4462-3B95-582F-8998-167D65A0CBD6   # devicectl UUID (not the 000081... one)
xcrun devicectl device process launch --device $DEV io.github.tryk016.openxray.<SUFFIX>
PID=$(xcrun devicectl device info processes --device $DEV | grep xr_3da | head -1 | awk '{print $1}')
xcrun xctrace record --device 00008130-... --template Allocations --attach $PID --time-limit 15s --output mem.trace
```

Note the two different device identifiers: `devicectl` wants the CoreDevice UUID,
`xctrace` wants the hardware UDID. This is the tooling for the queued 3.1 GB
load-peak investigation. Apple removed the OpenGL ES frame debugger from Xcode,
so there is still **no GPU frame capture** for this GL app.

**3. Install from Xcode over cable — BLOCKED by an Apple account limit, not by
anything we can engineer.** Investigated to a firm conclusion the same evening
after the Apple ID was added to Xcode:

- The keychain *does* hold `Apple Development: tryk016@gmail.com (C4MLW25CWH)`,
  valid 2026-07-19 → 2027-07-19. **It is SideStore's certificate, not Xcode's** —
  its `OU=RMJWWPF379` is the Team ID, and that is exactly the suffix SideStore
  appends to the bundle id (`io.github.tryk016.openxray.RMJWWPF379`). Reading the
  Team ID out of the cert subject is, incidentally, how to get it without the
  Xcode GUI, which shows no code for a personal team.
- **The matching private key is not on this Mac** (`security find-identity` → 0
  valid identities; a certificate without its key cannot sign). SideStore keeps
  its key in its own store.
- Making Xcode mint its own key+cert therefore needs `-allowProvisioningUpdates`
  **plus** an explicit `DEVELOPMENT_TEAM` — `xcodebuild` will not infer a personal
  team and fails with "requires a development team".
- With the team supplied, it fails at the next step:
  **`Your maximum App ID limit has been reached. You may create up to 10 App IDs
  every 7 days.`** SideStore consumes App IDs on its installs and the quota is
  spent. **This failed while creating the App ID, before touching certificates —
  SideStore's cert and the installed app were verified intact afterwards.**
- The only remaining route is to sign under the *existing* bundle id, which would
  overwrite the working, playable SideStore install (save games included) and
  still risks revoking SideStore's certificate on a free account. **Judged not
  worth it** — the quota lapses on its own within 7 days, and the loop it would
  buy is a convenience, not a blocker. **Revisit after 2026-07-26** if the faster
  device loop still looks worth the risk.

Tested against a throwaway CMake/Xcode project in the scratchpad, never against
the engine build tree, so none of this disturbed the working build. Before the
attempt, the device's writable state was pulled over the cable to
`~/openxray-handoff/device-backup-2026-07-19/` (savedgames, user.ltx, tmp.ltx,
imgui.ini) — `cdb_cache` deliberately skipped as regenerable.

**SideStore remains the install path.** What works regardless of signing, and is
the real prize:

- **`devicectl device process launch` starts the SideStore-installed app over
  the cable** — no signing identity required on the Mac.
- **`devicectl device copy from --domain-type appDataContainer` pulls files
  directly out of the app container** — `Documents/xr_boot.log` retrieved in
  ~2 s. **The manual File Sharing export in the on-device loop is obsolete.**
- The bundle id is **not** `io.github.tryk016.openxray`: SideStore appends a
  per-install suffix (currently `io.github.tryk016.openxray.RMJWWPF379`). Read it
  from `devicectl device info apps` rather than hard-coding it — it will change
  on reinstall.
- The device must be **unlocked** or the developer disk image will not mount
  (`kAMDMobileImageMounterDeviceLocked`), which surfaces as an unhelpful
  "failed to get a list of files".
- The recurring `Failed to load provisioning parameter list ... No provider was
  found` banner on every `devicectl` call is **noise from having no signing
  account** — it does not indicate failure; read the line below it.

**Incidental finding, and it matters right now:** `devicectl device info apps`
reports the installed build as **`1.6.02.10024082`** — that is slice 6.9. The
build queued for testing is **`1.6.02.10024084`** (6.10 + 6.11). **SideStore has
not been updated on the device**, so any device test run before updating would
have been testing the wrong binary and would have "reproduced" both the white
world and the green quads. Update SideStore first.

### 2026-07-19 (late) — Slice 6.11: main-menu GREEN QUADS — the video texture was destroyed by a sticky-GL-error false positive (patch landed, device log pending)

**Symptom:** in the CoP main menu the animated `.ogm` background and the logo render as flat
GREEN quads on device. The same path drives PDA/TV/tutorial video and the sleep-dialog static.

**The green is a fingerprint, not a colour bug.** `res/gamedata/shaders/gl/yuv2rgb.ps:9-21`
samples `s_base` and adds the constant bias `_S = (-0.86961, +0.53076, -1.0786)`. A sample of
exactly (0,0,0) clamps to RGB(0, 0.531, 0) ≈ **RGB(0,135,0)** — the observed green, alpha 1.0.
A *uniform* quad in exactly that colour proves the whole pipeline is standing: shader swap,
geometry, blend and sampling all work, and only the texture CONTENT is absent. On ES, sampling
an UNBOUND sampler returns (0,0,0,1) with **no GL error**, which is how the absence stayed
invisible.

**The mechanism (confirmed by reading):**
1. `glSH_Texture.cpp:236` ended the OGM create sequence with ONE trailing `glGetError()` and no
   prior drain. GL error flags are STICKY, and `CHK_GL` is a no-op in release builds
   (`xrDebug_macros.h:203`), so this read whatever error ANY earlier path in the frame had left
   pending — then logged `"Invalid video stream: 0x%x"`, deleted the decoder and zeroed
   `pSurface`. A perfectly good texture, destroyed by someone else's error.
2. `PostLoad()` (:62-65) consequently selected `apply_normal`, which does
   `glBindTexture(GL_TEXTURE_2D, 0)`.
3. Movie shader sampled nothing → (0,0,0,1) → bias → green.

**Stale claim corrected — and it cost us a wasted investigation.** The recurring note in this
file that video uses an unported "D3D-wrapper `CreateTexture(A8R8G8B8)`" is **WRONG**. That is
the **DX11** implementation (`xrRenderDX11/dx11SH_Texture.cpp`), which iOS never compiles. iOS
builds **xrRenderGL**, whose Theora path was already complete and ES-adapted
(`glSH_Texture.cpp:76-127`: `glMapBufferRange`, GL_RGBA + per-channel swizzle), and the decoder
is portable and linked (`xrEngine/xrTheora_Stream.cpp`, `xrTheora_Surface.cpp`). That note sent
us looking for a port that never needed writing, while the actual bug was six lines of error
handling. Corrected at the four sites below and in `GamePersistent.cpp`'s `allow_intro()`.

**A recon claim I had to REFUTE rather than repeat.** The pre-patch analysis identified
`glState.cpp:272`'s `glSamplerParameteri(sampler, GL_TEXTURE_MAX_LEVEL, ...)` as the *source* of
the sticky error. It is not: it is **dead code**. `UpdateSamplerState` (:213) is reached only
from `tss_def.cpp:51`, replaying tuples recorded by `RS.SetSAMP`, and no `SetSAMP` call site in
`src/` ever emits `D3DSAMP_MAXMIPLEVEL` (12 hits across the Blender_Recorder files: only
ADDRESSU/V/W, BORDERCOLOR, MIN/MIP/MAGFILTER, MAXANISOTROPY, COMPARISONFILTER/FUNC). The case has
never executed in a GL build. It *is* still wrong — `GL_TEXTURE_MAX_LEVEL` is not a valid
sampler-object parameter in any GL or ES version — so it is removed as a cleanup, **but it
generated no errors and its removal will not shift the log's 0x500 population.** Recording this
explicitly: the actual upstream source of the stale error is **still unidentified**; the engine's
own DXT-decoder comment (`glTexture.cpp:239-240`) notes 0x500/0x506 observed around texture
loads. The per-stage messages added here will name any remaining real failure. Not trading one
confidently-wrong journal note for another — that is the exact failure mode this slice exists to
correct.

**Fixes:**
- **Drain + per-stage checks** in the OGM create sequence (PBO alloc / storage / initial clear),
  mirroring the DXT decoder's idiom at `glTexture.cpp:238-253`, so the log attributes the stage
  that really failed instead of blaming the video for someone else's error.
- **Non-destructive failure.** `dxUIRender::UpdateShaderName` (`dxUIRender.cpp:152-162`) swaps
  the element to `hud\movie` purely because the `.ogm` file EXISTS, before and independently of
  the texture load — it cannot be un-swapped from `Load()`. So the invariant is now "the movie
  shader always samples a COMPLETE texture that decodes to black": on genuine failure we
  substitute a 1×1 immutable RGBA8 surface holding the YUV triple (Y=16, U=V=128) that yuv2rgb
  maps to black, and drop only the decoder. **Never `pSurface = 0` again.** Green is now
  structurally impossible; the worst case is black.
- **Latent bug closed for free:** storage is allocated at the pow2-ceil size `Width/Height(false)`
  but frames upload at the real size `Width/Height(true)`, so a non-pow2 `.ogm` would keep an
  uninitialised immutable-storage margin and show a bias-green border. The whole surface is now
  cleared to YUV black once at creation — correct for pow2 assets too, since every texel is
  overwritten by the first frame.
- **Fallback dimensions:** `m_width`/`m_height` were previously left UNINITIALISED on the OGM path
  (the constructor never sets them, and `desc_update()`'s `glGetTexLevelParameteriv` is NULL on
  ES), so any UI sizing that read them read garbage. Both paths now record the real dimensions.
- **`glState.cpp` cleanup:** the impossible `D3DSAMP_MAXMIPLEVEL` sampler call is dropped,
  UNGUARDED. Justified on all platforms: it is unreachable AND invalid, so removal cannot change
  observable behaviour anywhere. Per-texture level clamping already happens on the texture object
  (`glTexture.cpp:247-248`).
- **Safety guard:** `allow_game_intro()` is now iOS-guarded to `false`, matching `allow_intro()`.
  iOS has no command line for `-nogameintro`, so it returned `true` unconditionally — harmless
  only because the video texture could not be created. With video working, leaving it open would
  silently re-arm the intro-movie path that **the app was previously KILLED on** (~40 s, hard iOS
  kill with no fatal and no engine log tail — entry of 2026-07-16, never diagnosed). Do not drop
  this guard.
- **One-shot diagnostic:** first decoded frame per video texture is logged (dims + `_pos`). This
  settles the one thing reading cannot: whether the decoder has EVER produced a frame on device.
  No pixel readback — the PBO is mapped `GL_MAP_WRITE_BIT` only, so reading it back is undefined.

**Colour path re-verified, NO rework needed:** the decoder packs `255<<24 | u<<8 | v` with
`y<<16` (`xrTheora_Surface.cpp:264`), i.e. memory `[V,U,Y,255]`. iOS uploads GL_RGBA and swizzles
R←BLUE, B←RED → sample `(Y,U,V)`; desktop GL_BGRA yields the identical `(Y,U,V)`. `yuv2rgb.ps:9`
takes `.bgr` = `(V,U,Y)` and assigns Y/U/V at :11-13. Byte-identical on both platforms, which is
why `0xFF108080` is genuinely black and not a guess.

**BLAST RADIUS — read before the device test.** This makes video textures actually work, so
several previously-dead paths go live at once: main-menu background and logo, **tutorial/PDA
video** (`UIGameTutorialVideoItem`) and the **sleep-dialog static** (`UISleepStatic.cpp:59`).
Note that the last two are **NOT gated by `allow_intro`/`allow_game_intro`** — only the two intro
paths are. Memory: the initial clear allocates one transient `xr_vector<u32>` of the pow2 surface
size (≈2 MB for a 1024×512 menu background), freed at scope exit, once per video texture load.

**Desktop:** success path is byte-identical apart from the added full-surface clear. A genuinely
failing `.ogm` now shows black and a named stage instead of bias-green.

**AWAITING DEVICE LOG — interpretation table (nothing below is claimed, only predicted):**

| Observation | Meaning |
|---|---|
| video plays | 6.11 complete; the stale error was the whole story |
| black + `first frame decoded` line | decode works; upload/swizzle is the next suspect |
| black + NO such line | decoder never advances; look at `CTheoraSurface::Update` / libtheora on device |
| any `! OpenGL: ... video <stage>` line | a REAL failure, now correctly attributed. If it is the storage stage, check `_w`/`_h` — a zero or absurd dimension from the Theora header means the sticky-error theory was wrong end to end and the failure was always genuine |
| green still | the build does not contain the `glSH_Texture.cpp` edits |

**Not touched (deliberately):** the `#alpha` companion loop at `xrTheora_Surface.cpp:290` uses
`data[++pos]`, skipping pixel 0 and ignoring row padding. Only reachable if a `*#alpha.ogm`
exists; left alone.

### 2026-07-19 (evening) — Slice 6.10: white-world root cause FIXED (frozen tonemap adaptation), Track A diagnostics stripped, Track B narrowed to the textured-UI path

**Device verdict on build `1.6.02.10024082` (iPhone 15 Pro Max, iOS 26.6, Zaton, r2):**
the Track A timeline paid for itself in one run and Track A is now **solved**. Track B did
not resolve — but its leading suspect was killed and replaced with a better one, and the
probe that was supposed to settle it turned out to have been sampling the wrong pixel.

**TRACK A — PROVEN MECHANISM: the tonemap exposure feedback loop is frozen, not wrong.**

`f_luminance_adapt` is *not* an exposure value. It is the **lerp blend weight** of the 1×1
exposure feedback texture. `r2_rendertarget_phase_luminance.cpp:238` updates it as
`f = .9f*f + .1f*Device.fTimeDelta*ps_r2_tonemap_adaptation`, hands it to the shader as
`MiddleGray.w` (`:247`), and `res/gamedata/shaders/gl/bloom_luminance_3.ps:52` consumes it as
`rvalue = lerp(scale_prev, scale, MiddleGray.w)`. **Weight 0 does not pause adaptation — it
strands the exposure texel forever.** That texel is then the tonemap multiplier for the whole
frame (`combine_1.ps:229` → `common_functions.h:24-33`, `rgb = rgb*scale`) *and* for sky, clouds,
portals and volumetric fog (`sky2.ps:31`, `clouds.ps:30`, `portal.ps:11`,
`combine_volumetric.ps:18`) — which is exactly why the *background* is what blows out.

The level-intro sequencer pauses the device, so `Device.fTimeDelta` is pinned at exactly 0 for
the whole intro and the update degenerates to a pure `0.9^n` decay. The timeline shows it
happening, and shows it healing the instant the clock is released:

```
t= 0.00 fr=1120 intro=2 dt=0.0000 dtr=0.0028  adapt=0.0179
t= 1.02 fr=1159 intro=2 dt=0.0000 dtr=0.0028  adapt=0.0003
t= 2.04 fr=1193 intro=1 dt=0.0000 dtr=0.2272  adapt=0.0000
 ... six consecutive rows, intro=1, dt=0, adapt=0.0000 ...
t= 7.13 fr=1383 intro=1 dt=0.0000 dtr=0.2272  adapt=0.0000
t= 8.14 fr=1539 intro=0 dt=0.0166 dtr=0.0166  adapt=0.0148   <-- clock released
t= 9.14 fr=1599 intro=0 dt=0.0166 dtr=0.0166  adapt=0.0167   <-- converged, world correct
```

`ps_r2_tonemap_adaptation = 1.f`, so the steady state is `adapt == fTimeDelta` — the 0.0167
at 60 fps in the healthy rows confirms the model exactly. And `0.5 * 0.9^31.6 = 0.0179`
pins the very first sample: `phase_luminance` had run only ~32 frames total, **all** with
`dt == 0`, starting from the one-shot `f_luminance_adapt = 0.5f` seed at
`r2_rendertarget.cpp:629` (never re-armed per level load). So the exposure is frozen at the
value of a ~32-frame transient during which almost nothing had been drawn — over-exposed,
everything mid-to-bright saturating white on the Reinhard curve while near-black pixels stay
black. That is the reported "white sky and terrain, black vegetation silhouettes", and the
recovery at `t=8.14` with a ~1 s time constant (1/0.0167 = 60 frames) is the "snaps correct
after I take a few steps".

**THE FIX (iOS-guarded, `#else` arm byte-identical).** Two halves, and the second is the
load-bearing one:

- drive the update from `Device.fTimeDeltaReal` instead of `fTimeDelta`;
- **floor the weight handed to the shader at `_max(f, 0.015f)`**.

The floor is what actually guarantees recovery, and the reason is a trap worth recording:
**`fTimeDeltaReal` is NOT a live wall clock during the intro either.** `device.cpp` does
`if (Paused()) fTimeDelta = 0.0f;` and `CTimer::Start()` (`xrCore/FTimer.h`) *early-returns
while paused*, so `Device.Timer` is never restarted and `GetElapsed_sec()` returns a constant.
The log proves it: `dtr` is frozen at exactly `0.2272` across six consecutive rows and at
`0.0028` across both `intro=2` rows. It is merely guaranteed nonzero and finite. Had we shipped
only the `fTimeDeltaReal` swap, the `intro=2` segment would have run at a weight of 0.0028 —
a ~350-frame (~6 s) time constant inside an ~8 s intro, i.e. barely better than broken.
`0.015` is ~65 frames (~1.1 s), and sits just below the 0.0167 steady-state weight at 60 fps,
so it is **inert whenever the game is really running** and only bites when the clock stalls.
Intended side effect: on iOS, exposure now keeps adapting while paused. That is correct for an
eye model.

Deliberately NOT touched, both flagged and both correct to leave alone:
`bloom_luminance_3.ps:56`'s `clamp(rvalue, 1.0/128.0, 20.0)` discards its return value (dead
code — `:54` is simply commented out), but the pathological value sits *inside* `[1/128, 20]`,
so fixing it fixes nothing while changing shader behaviour on every GL platform; and re-seeding
`r2_rendertarget.cpp:629/640` per level load is redundant now that the loop is self-correcting.

**TRACK B — the leading hypothesis was WRONG; the replacement is better supported.**

The 6.9 reasoning was going to conclude "the `hud\crosshair` ui_shader draws nothing on ES,
corroborated by the missing in-game crosshair". That corroboration is **false**.
`CHUDCrosshair::OnRender` is only reached through the `else` branch of
`HUDTarget.cpp:266 if (!m_bShowCrosshair)`; `m_bShowCrosshair` initialises **false**
(`HUDTarget.cpp:53`) and is only set true while holding a weapon whose ltx sets
`use_crosshair` (`Actor.cpp:1136`, forced false again at `:1157`). With no weapon out the
crosshair is *supposed* to be absent. The screenshot says nothing about that shader.

What the always-taken branch draws instead is a **textured** dot via `hud\cursor` + `ui\cursor`
(`HUDTarget.cpp:48,266-296`) — the same textured-UI path as the missing `CUICursor` sprite
(`hud\cursor` + `ui\ui_ani_cursor`, `UICursor.cpp:11`). So: **two independent textured-UI draws
are missing while untextured and font draws are visible.** A texture load/bind failure on the
`ui\cursor` family is now the single best explanation for the cursor, and the focus frame's
status is simply untested.

The 6.9 present-time probe also has to be **discarded, not believed**. The cursor sprite is
40×40 UI units with x scaled by `get_current_kx()` = 0.6152 at 932×430, and `SetWndPos(vPos)`
puts its **top-left** at `vPos` — so the sprite's px rect is (466,215)-(488,237) and the probe's
`pxA=(466,215)` was its outermost **corner texel**, transparent on an arrow cursor. "Background
at the cursor position" was never evidence of a missing draw.

Also newly proven from the same log: the `lum` probe's `st=3` means *unreadable format*, not
*FBO incomplete* — so the strongest rival theory, that `rt_LUM_pool`'s FBO is incomplete on ES
and the exposure texture is never written at all (which would have made the Track A fix a no-op),
is **refuted**. The pool is complete and bound every frame; the probe only failed because the
pool's real ES pair is `GL_RED`/`GL_HALF_FLOAT` (R16F, not the requested R32F).

**What ships for Track B in 6.10 — three cheap, decisive additions:**

1. **`GetBaseTextureResolution` on the cursor's own shader**, logged at ~1 Hz in
   `CUICursor::OnRender`. `dxUIShader::GetBaseTextureResolution` returns `false` and zeroes
   `res` when there is no base texture, so `texOk=0` or a `0x0` size answers the whole
   textured-UI question outright, with no renderer-internals digging.
2. **A font-based focus affordance** drawn through `CGameFont` right after the existing
   `hud\crosshair` bars — `>` and `<` brackets straddling the focused widget.
   `CGameFont::Out` takes backbuffer pixels (`OutSetI` is `OutSet(DI2PX(x), DI2PY(y))`), the
   same space the bars are already in. `CMainMenu::OnRender`/`::OnRenderPPUI_main` call
   `UI().RenderFont()` immediately after `DoRenderDialogs()`, so it flushes the same frame.
   If the brackets appear and the bars do not, the failure is scoped to the ui_shader draw
   path and **not** to draw ordering or frame-edge state.
3. **The present probe now samples three points in both Y orientations** (six texels), biased
   toward the sprite's top-left body at UI offsets `(5·kx,6)`, `(10·kx,12)`, `(20·kx,20)` from
   `vPos`, instead of one corner texel. `g_ios_cursor_probe_x/_y` became `int[3]`.

Dead ends for Track B, verified this round — do **not** re-investigate:

- **Render order.** `MessageRegistry::Resort` sorts **descending** (`a.Prio > b.Prio`,
  `pure.h:110-113`) and `Process()` iterates forward (`:93-99`), so higher priority runs
  *first*. `CMainMenu` is 4, `CUICursor` is −3 → the cursor draws **last, on top**. Correct
  as-is.
- **Vertex colour byte order.** `unpack_D3DCOLOR(c) { return c.bgra; }`
  (`common_functions.h:74`) is a pure swizzle; alpha maps to alpha under every permutation.
  It cannot hide a draw.
- **State leakage from our iOS `ClearRT` patches.** At both call sites the desync cannot
  materialise: `OnFrameBegin` does `Invalidate()` → `set_RT(get_base_rt())` → `ClearRT` on the
  *same* texture, and `RenderMenu` does `set_ColorWriteEnable()` → `u_setrt(rt_Generic_0)` →
  `ClearRT(rt_Generic_0)`, likewise the same texture.
- **Y flip in `hud_crosshair.vs`.** It computes `I.P.y * screen_res.w * 2.0 - 1.0`, which is
  **byte-for-byte identical** to `stub_notransform_t_menu.vs`, the shader behind every menu
  static that IS visible on device. Adding a flip would be the bug.
- **A silently disabled shader pass.** `_LinkPP`'s soft-fail path logs
  `! Pass '%s' failed to link` and zeroes `pp`; the device log contains **zero** such lines.

One thing to tell the user before they hunt for the focus frame again: in the **main menu**
the log shows `focused='none' valuable=0` on every sample, so there the frame legitimately
draws nothing and its absence is not a bug. It must be checked inside **Options** or the
**Save** dialog, where the log shows real focus (`'button_cancel'`, `'combo_renderer'`,
`'edit_filename'`, valuable=5/14).

**STANDALONE FINDING, not this slice's fix: the game is not rendering at native resolution.**
`CHW::Present` overrides `dstW/dstH` from `SDL_GL_GetDrawableSize` (2796×1290 pixels) while the
source rect stays `Device.dwWidth/dwHeight` (932×430 **points**), then blits with
`GL_NEAREST` (`glHW.cpp:309-323, 371-374`; the log's `presentProbe rt=932x430` confirms it).
Every frame is a **3× nearest-neighbour upscale**. That is the entire explanation for the soft,
blocky look of the device screenshots — and it is a large free quality/performance decision
that has never been made deliberately. Its own slice.

Second unrelated hazard found and left for its own slice: `CBackend::ClearRT`/`ClearZB`
(`glR_Backend_Runtime.h:51-86`) issue `glClear` without touching `GL_SCISSOR_TEST`, and
`set_Scissor` is uncached. A UI scissor left enabled at frame end would silently shrink our
per-frame iOS clears.

**Stripped in this slice:** the entire Track A timeline — `iosdbg_*`/`IOSDBG_*` helper namespace
and its call site in `gl_rendertarget_phase_combine.cpp` (~220 lines), the `<cmath>` include
added for the half decode, and the `g_ios_intro_active` marker in all six of its sites
(`IGame_Persistent.cpp/.h`, four setters in `GamePersistent.cpp`). Its question is answered, and
it was costing the user a 20–60 ms pipeline stall once a second for 25 s after every level load.
`grep -rn "g_ios_intro_active\|iosdbg\|IOSDBG_" src/` now returns nothing.

**Kept on purpose:** all Track B diagnostics (cursor 1 Hz log, present probe, focus-frame diag,
the focus/nav logs in `ui_focus.cpp` and `xr_input.cpp`) — Track B is **not** solved and these
are the instruments that will solve it. The DXT avg-RGBA diagnostic in `glTexture.cpp` is also
kept, even though the colour question is closed: it fires once per texture at load, not per
frame, and the new leading hypothesis is a failure in exactly that texture path, so it is now
load-bearing evidence rather than noise. All of it goes once Track B lands.

### 2026-07-19 — Slice 6.9: device verdict on 10023080 + two diagnostic tracks (focus frame, white-world timeline)

**Device verdict on build `1.6.02.10023080` (iPhone 15 Pro Max, iOS 26.6, Zaton, r2) —
three of slice 6.7's four fixes CONFIRMED:**

1. **SKY + CLOUDS RENDER.** The unconditional cubemap bind in `dxEnvironmentRender.cpp`
   was the whole story — the bVTF gate really had been skipping `set_Textures(&sky_r_textures)`
   on iOS. Sky and clouds are now visible in-game.
2. **MENU TEXT NO LONGER STACKS.** The per-frame `ClearRT/ClearZB` in
   `CBackend::OnFrameBegin` plus the `rt_Generic_0` clear in `CRender::RenderMenu` killed
   the ghosting on the single persistent presented texture. Loading tips no longer bleed
   through the menu; shniaga text animates cleanly.
3. **COLOURS ARE CORRECT — the earlier "wrong colours" was never an R/B swap.** The DXT
   avg-RGBA diagnostic settles it on-device, not in theory: `detail_grnd_grass` decodes to
   `avg RGBA 124 114 98 255` (warm brown-grey earth, R>G>B — a swapped decode would report
   B>G>R), `sky_19_cube` to `156 165 184 255` (cool blue-dominant sky, B highest),
   `sky_20_cube` to `149 117 86 255` (warm sunset, R highest). Channel order is right in
   every direction. What looked like wrong colours was the **missing sky and the lighting
   that depends on it** — i.e. bug 1, now fixed. The DXT decoder is exonerated for good;
   do not re-open it.

**PROVEN by the same log: the pad focus LOGIC works perfectly — it is only INVISIBLE.**
The 6.7 diagnostics bracket the whole path and every stage reports success:

```
* iOS diag: padPress dik=533 uiAct=113 TIR='CUIDialogWndEx' cursorVis=1 cursor=(512,384) focused='none' valuable=14
* iOS diag: focusNav dir=4 from=(512,384) cand='none' cand2='CUIButton'
* iOS diag: SetFocused 'CUIButton' cursor=(566,320)
* iOS diag: padPress dik=533 uiAct=113 TIR='CUIDialogWndEx' cursorVis=1 cursor=(566,320) focused='CUIButton' valuable=14
* iOS diag: focusNav dir=4 from=(548,316) cand='CUIButton' cand2='combo_renderer'
* iOS diag: SetFocused 'CUIButton' cursor=(653,320)
```

14 valuable (focusable) widgets found, D-Pad direction resolved to a candidate, `SetFocused`
warped the cursor onto the widget's centre, and the *next* press starts from the new
position — the state machine is textbook-correct. `cursorVis=1` throughout. So the widget
IS focused and the cursor IS at the right virtual coordinate; the user simply sees nothing.
Two things could produce that, and 6.9 discriminates them instead of guessing:
the cursor's own material (`hud\cursor` draws the animated `ui\ui_ani_cursor` `.seq`, whose
per-frame texture rebind is the prime suspect under the ES unbound-sampler rule), or the
draw landing somewhere that never reaches the screen.

**What ships in 6.9 — two tracks, both pure diagnostics plus one deliberate affordance,
all `#if defined(XR_PLATFORM_APPLE_IOS)`:**

- **Track B — guaranteed-visible focus frame + cursor probe.** A yellow/cyan blinking
  3-unit rectangle is drawn around `UI().Focus().GetFocused()`'s absolute rect at the end
  of `CDialogHolder::DoRenderDialogs` — the single code path shared by
  `CMainMenu::OnRender`/`OnRenderPPUI_main` and `CUIGameCustom`, so it covers both the main
  menu and the in-game pause dialogs. It uses the `hud\crosshair` ui_shader
  (`shader:begin("hud_crosshair","simple_color")`), which has **no sampler stage at all**,
  so the ES "unbound sampler returns opaque black, no GL error" failure mode is structurally
  impossible for it. No manual Y flip: `hud_crosshair.vs` and `stub_notransform_t_menu.vs`
  (behind every menu static that IS visible on device) compute the identical
  `I.P.y * screen_res.w * 2.0 - 1.0`, so they agree — a flip here would be the bug. Alongside
  it, a ~1 Hz log at `CUICursor::OnRender` entry (reached / visible / position / whether the
  cursor's own ui_shader is `inited()`), placed *before* the `IsVisible()` early-out so a
  `vis=0` frame is reported rather than silent, and a ~1 Hz two-pixel `glReadPixels` probe in
  `CHW::Present` taken out of `pFB` after the read binding and before the blit, at both Y
  orientations. Cursor-coloured pixel ⇒ it rasterises and is lost downstream;
  background-coloured ⇒ the draw produced nothing. Whichever orientation returns a plausible
  colour also settles the FBO Y convention for good.
- **Track A — "white world at spawn" timeline, diagnostic ONLY, no fix.** One line per
  second for the first 25 s after every level load, emitted at the end of the `if (!_menu_pp)`
  combine_1 block in `gl_rendertarget_phase_combine.cpp` — the one point in the frame where
  the g-buffer albedo (`rt_Color`), the light accumulator (`rt_Accumulator`), the freshly
  combined LDR scene (`rt_Generic_0`) and the 1×1 exposure texel (`rt_LUM_pool`) are all
  simultaneously valid and none has been recycled by forward rendering, bloom or PP. Five
  1×1 readbacks go through a **private FBO bound to `GL_READ_FRAMEBUFFER` only**, with the
  previous read binding saved and restored, so `CBackend`'s cached `pFB`/`pRT[]`/`pZB` never
  go stale (leaving our FBO bound there would black-screen the device, since iOS Present
  blits through the read binding). The read format is **queried** per target via
  `GL_IMPLEMENTATION_COLOR_READ_FORMAT/_TYPE` and decoded for `UNSIGNED_BYTE`, `HALF_FLOAT`
  and `FLOAT`; an unsupported pair is skipped and reported once rather than producing a
  GL_INVALID_OPERATION storm. The same row carries `dt` vs `dtr` (which proves
  `Device::Paused()` exactly), the new `g_ios_intro_active` marker (1 = `intro_game`,
  2 = `game_loaded`, 0 = neither), env weight/modifier power/WFX, fog colour+density+near+far
  and the sun/hemi/ambient constants actually uploaded to the combine shader. One run
  separates fog blowout, zero sun accumulation, zero hemisphere, dead albedo, tonemap
  runaway and pause-with-intro from each other. **Cost:** ~three integer compares on
  non-sampling frames and zero GL calls; on the ~25 sampling frames each `glReadPixels`
  forces the tile-based GPU to resolve and stalls the CPU, so expect a 20–60 ms hitch once
  per second for 25 s. Acceptable for a diagnostic; must be stripped before release.

**Ruled out for Track A — do NOT re-investigate these:**

- **Late texture uploads.** The theory that the world is white because textures are still
  streaming in at spawn is dead. The 6.7 texture work landed the pair-whitelist and unpack
  alignment fixes and the phantom 0x500s went 129 → 0; the DXT decode is CPU-side and
  synchronous (`ios_upload_dxt_as_rgba8` in `glTexture.cpp` decodes every mip/face before
  the upload returns), so there is no window in which a texture is bound but blank. And a
  missing-texture world is *black* on ES (unbound sampler → `(0,0,0,1)`), not white.
- **Visible precache.** Not the mechanism either: `Device.dwPrecacheFrame` is what the Track A
  window is *armed on* — sampling only begins once it has fallen back to 0, i.e. after
  precache is over, and the white frames persist past that point. Precache frames are not
  what the user is seeing.
- **Environment lerp initialisation.** Already instrumented and clean: the `* iOS env:`
  weather log added in 6.4 shows the environment descriptors mixing normally from the first
  frame, with sane weights. Rather than re-derive it, Track A now prints `w=`/`mp=`/`wfx=`
  on every row, so if the lerp *were* the cause it would show up as a discontinuity on the
  exact row where the picture heals — a positive test, not another round of reading code.

The remaining live hypotheses are exactly the ones the table discriminates: zero sun
accumulation, zero hemisphere, fog blowout, dead albedo/g-buffer, tonemap runaway, and
pause-gated adaptation. No speculative fix ships in 6.9 — if the table indicts one, the fix
is its own slice.

### 2026-07-18 (late night) — Slice 6.8: fix erase-iterator UB in CUIFocusSystem::Update

Found in passing by the slice-6.7 focus-highlight verifier while reading `ui_focus.cpp`.
`CUIFocusSystem::Update` has two list-migration loops (valuable→temp, non_valuable→valuable)
that misuse `std::list::erase`: they do `it = list.erase(it)` inside a `for(...; ++it)`, so the
trailing `++it` **skips** the element that shifted into the erased slot, and if `erase` returns
`end()` the `++it` increments `end()` — UB. The valuable loop additionally dereferenced the
erase result (`if (*it == m_current_focused)`), a read of a possibly-`end()` iterator (UB) that
also compared against the *next* window instead of the removed one. Fixed both with the standard
idiom — advance only when keeping (`++it; continue`), and let `it = list.erase(it)` carry the
iterator forward otherwise — and moved the `m_current_focused` check ahead of the erase so it
refers to the window actually being removed. Pre-existing upstream-style bug; platform-neutral,
no `#ifdef`s, behavior otherwise unchanged. Desktop+iOS shared code (`src/xrUICore/ui_focus.cpp`).

### 2026-07-18 (late night) — Slice 6.7: device test of 079 → five-track worker-pool investigation

Device verdict on build `1.6.02.10023079`: **audio shim CONFIRMED** (sound returns
after Siri) and **the 129 phantom 0x500s are GONE** (0 in the log) — but sky still
black, menu text still stacks on itself, still no options highlight, no Game Mode
banner, and "colors look the same". (The uploaded JetsamEvent was unrelated —
largestProcess `backboardd`, xr_3da absent.) Ran the worker-pool at full width:
5 detectives + 5 adversarial verifiers (workflow `wf_0a1b6a6b-f8f`), 12 patches
approved, **two hypotheses executed before they could waste a CI+device cycle**.

1. **SKY — root cause CONFIRMED: the sky cubemaps were never bound on iOS.**
   `dxEnvironmentRender::RenderSky`'s OGL path gates `set_Textures(&sky_r_textures)`
   on `HW.Caps.geometry.bVTF`, which comes from `GLAD_GL_VERSION_3_0 ||
   GLAD_GL_ARB_texture_float` — desktop-glad flags that are **0 under
   `gladLoadGLES2`**, so bVTF is permanently false on iOS (new gotcha class above).
   The skybox blender binds `s_sky0/s_sky1` to `$null`; an unbound samplerCube
   samples (0,0,0,1) on ES with **no GL error** — black sky, spotless log. Also
   explains why 6.4's shader-side `#undef USE_VTF` was a no-op: `rgl_shaders.cpp`
   gates `USE_VTF` on the same false bVTF, so the shaders were already non-VTF.
   **Fix:** iOS binds unconditionally, exactly like the DX11 path
   (`dxEnvironmentRender.cpp`).
2. **MENU TEXT GHOSTING — root cause CONFIRMED: nothing ever clears the presented
   chain on iOS.** `CBackend::OnFrameBegin` (GL) binds base FB/RT/ZB with no clear;
   `RenderMenu` draws PP-UI into `rt_Generic_0`, which desktop deliberately never
   clears (frozen-scene backdrop for the pause menu) — desktop hides all of it
   behind fully-covering menu art + an ephemeral swap chain. iOS presents ONE
   persistent texture, so every unrepainted pixel keeps last frame's content:
   loading tips over the menu, shniaga text stacking as it animates. **Fix:**
   iOS-only `ClearRT/ClearZB` at `OnFrameBegin` (`R_Backend_Runtime.cpp`) +
   `ClearRT(rt_Generic_0)` in `RenderMenu` (`r2_R_render.cpp`). Accepted iOS-only
   trade-off: the in-game pause-menu backdrop is black, not the frozen scene.
3. **OPTIONS FOCUS HIGHLIGHT — prime hypothesis DISPROVEN.** The warp-skip theory
   (no `SDL_MOUSEMOTION` → no hover) is wrong: `CUIWindow::Update` re-polls
   `GetCursorPosition()` **every frame** (hover is polled, not event-driven), and
   `SetUICursorPosition` writes `vPos` directly before `iSetMousePos`. Root cause
   not provable from code → shipped **6 diagnostic log points** bracketing the
   entire path: SDL pad-button delivery (`xr_input.cpp`) → DialogHolder entry state
   (binding, cursorVis, cursor pos, focused widget, focusable count) → TIR
   consumption → focusNav candidates → `SetFocused` warp landing (`ui_focus.cpp`)
   → ACCEPT click `handled` flag (`UIDialogHolder.cpp`). The next device log
   discriminates all remaining hypotheses. (Spotted in passing, NOT fixed here:
   pre-existing erase-iterator misuse in `CUIFocusSystem::Update` — separate slice.)
4. **GAME MODE — root cause CONFIRMED: we ship a key Apple deprecated.** The plist
   chain is alive end-to-end (CI's plutil dump proves our template reaches the
   .ipa), but **`GCSupportsGameMode` was deprecated at iOS 18.6** — Apple's docs
   literally say "Use `LSSupportsGameMode` instead" — and iOS 26.6 keys off the LS
   variant. **Fix:** add `LSSupportsGameMode=true` (GC kept for 18.0–18.5);
   CI plist dump bumped head -50 → -100 so the next run proves the key shipped.
   **Test protocol (from Apple DTS):** Game Mode only (re)triggers when ≥5 min
   passed since the app was last closed; ground truth is Settings → Game Mode
   (lists eligible apps), not just the transient banner. Sideloading is likely NOT
   a blocker (development/TestFlight builds get Game Mode per Apple forums).
5. **COLORS — prime suspect DISPROVEN with a proof.** The iOS DXT decoder's channel
   order is spec-correct: BC1 RGB565 extraction `r=(c>>11)&31 / g=(c>>5)&63 /
   b=c&31`, memory order R,G,B,A — verified by transcribing the C++ to Python and
   decoding a synthetic pure-red block → bytes `[255,0,0,255]`. The verifier also
   re-inspected the device screenshot: dirt warm tan, AK wood red-brown, bush olive
   — **no R/B swap is actually present on device**; the perceived wrongness is
   lighting/sky/video territory (tracks 1-2 + the known video-texture gap).
   Shipped a temporary diag logging avg RGBA of decoded sky/terrain/grass base
   mips (`glTexture.cpp`) — the next device log is an end-to-end channel-order
   proof. Remove once confirmed.

Process: this is the worker-pool loop working as designed — every root cause was
re-derived independently by an adversarial verifier before I applied anything, and
two plausible-but-wrong fixes died in review instead of on the device.

First red CI in the Phase-6 run, and a load-bearing platform discovery behind it. The
first Objective-C++ TU in the project (ios_audio_session.mm) failed with `'alext.h' file
not found` — and the surrounding SDK deprecation warnings proved WHY: **the iOS build
compiles and links against Apple's deprecated OpenAL.framework, NOT the openal-soft
1.25.2 that cmake/ios/deps dutifully builds** (matches the runtime log's `efx[no]`; the
audit had flagged this as P2 "dead ballast"). Apple's framework ships no alext.h and no
ALC_SOFT_pause_device entry points, which the audio shim hard-referenced.

Hotfix (commit 89707f40c): `#include <OpenAL/alc.h>` with an `<AL/alc.h>` fallback via
__has_include, and alcDevicePauseSOFT/alcDeviceResumeSOFT resolved AT RUNTIME through
alcGetProcAddress — on OpenAL Soft they halt/restart the mixer's CoreAudio unit, on
Apple's framework the lookups return null and the AVAudioSession reactivation alone does
the un-muting (the core of the fix either way).

Process lesson recorded: the verifier confirmed the symbol existed in the WRONG library
(the built-but-unlinked openal-soft in the deps prefix) — link-reality can only be proven
by CI. Standing follow-up (audit P2): switch the link to the static openal-soft to regain
EFX/EAX and future-proof against the framework's removal — a deliberate slice of its own.

### 2026-07-18 (night) — Slice 6.6: worker-pool patch pack (agents wrote it, verifiers approved it, I applied it)

New standing process in action (user directive): a Workflow worker pool (5 workers + 3
adversarial verifiers + foreman) reviewed slices 6.1-6.4 BEFORE device testing, wrote
patches, and everything below shipped only after an "apply" verdict:

- **Textures HIGH (reviewer caught MY 6.1 bug):** the conversion whitelist checked
  Internal and External independently — the inconsistent X8R8G8B8 pair (gli BGRX8 ->
  internal RGB8 + external RGBA) slipped through to an ES-illegal glTexSubImage2D triple
  (0x502, black texture). Now whitelists consistent internal/external PAIRS + gates on
  probe.Type (the *_REV packed types don't exist in ES 3.0).
- **Textures MEDIUM (gem):** gli keeps rows tightly packed but GL defaults to
  GL_UNPACK_ALIGNMENT=4 — every 24-bit mip with width%4!=0 (incl. the 2-px tail mips of
  every POT chain) uploaded skewed. Fix: glPixelStorei(GL_UNPACK_ALIGNMENT,1) on iOS.
- **Options menu on pad (detective, verdict apply):** desktop options ARE pad-navigable,
  but generic pad focus drives an EMULATED cursor: CUIFocusSystem::SetFocused ->
  WarpToWindow -> iSetMousePos -> SDL_WarpMouseInWindow — which does nothing useful on
  iOS, and the next frame snapped the cursor back to the stale last-touch position, so
  focus cleared every frame (no highlight, dead widgets). CUIMMShniaga (main menu list)
  bypasses the cursor system — hence the split the user observed. Fix: on iOS
  iSetMousePos updates m_ios_touch_pos (now mutable) as the authoritative pointer and
  skips the SDL warp; plus an ACCEPT->click bridge in UIDialogHolder (A = LBUTTON
  DOWN/UP pair at the focused widget). If L1/R1 tabs stay dead after this, suspect the
  bind table (bind_gpad ui_tab_prev/next), not the focus system.
- **AVAudioSession shim (coder, verdict apply):** new src/xrEngine/ios/ios_audio_session
  .h/.mm (ObjC++; header deliberately engine-include-free — PlatformApple.inl's
  `typedef int32_t BOOL` collides with ObjC BOOL) + CMake (enable_language(OBJCXX),
  SKIP_PRECOMPILE_HEADERS on the .mm, AVFoundation framework) + x_ray.cpp wire-up before
  Engine.Sound.Create(). Playback category, interruption + route-change observers,
  ALC_SOFT_pause_device + counted pause_emitters on interrupt, reactivate + resume on
  end. First real compile check = CI (ObjC++ can't build on Windows).
- **Memory analyst (no patch, data first):** staging buffers + cdb_cache exonerated; #1
  contributor is driver-side DXT->RGBA8 residency (~1.2-2.2 GB; levers: cap 1024->512,
  wire get_texture_load_lod into the DXT path, ASTC later); #2 the full
  ResourcesDeferredUpload burst at net_start (prefetch skip does NOT cover it). First
  action when needed: per-load-phase phys_footprint instrumentation, then decide.
- **Reviewer notes for the record:** the ES sky fix depends on bVTF==true (RenderSky
  only binds sky_r_textures under that flag — never force bVTF=false on iOS without
  unconditional binding); cfg_save-on-background is best-effort and also fires on
  Control Center peeks — by design, not a bug.

### 2026-07-18 (evening) — Phase 6: graphics detective work (slices 6.1-6.4, builds 10023073-075)

User priority order: graphics -> memory -> settings -> (virtual pad LAST). Four slices of
iterative on-device diagnosis, each build's log narrowing the next fix:

- **6.1 (build 073):** legacy-format fallback (whitelist ES combos + gli::convert->RGBA8 +
  format logging), 3D DXT decode for water_sbumpvolume (per-level per-slice ->
  glTexStorage3D/TexSubImage3D; texture_load desc maps TARGET_3D), once-a-minute env
  lighting log (game time, sun/hemi/ambient/sky, sun_dir.y), and **settings persistence**:
  SDL_AddEventWatch on WILLENTERBACKGROUND/TERMINATING -> cfg_save + FlushLog (iOS never
  runs the desktop quit path).
- **Device verdict 073:** rain + lightning visible (dynamic weather works); env log showed
  sun(0,0,0) during RAIN = legitimate, but sky_color 0.98 with a BLACK rendered sky ->
  the real lighting bug is the sky draw. Conversions logged: ZERO, yet the same 129
  texture "failures" persisted -> suspicion of sticky-error mis-attribution.
- **6.2 (build 074):** normal-path glGetError drain + format-rich failure message + weather
  name in the env log.
- **Device verdict 074 (three wins):** (1) **user.ltx successfully loaded — settings
  persistence CONFIRMED working**; (2) weather='default_clear', bright sun (0.91) — env
  fully healthy, user SEES the sun sprite, sky dome still black; (3) failures now carry
  formats: RGBA8/RGBA/UNSIGNED_BYTE and RGB565/RGB/565 — PERFECTLY LEGAL combos "failing"
  => the error源 is between drain and check: **the vector GL_TEXTURE_SWIZZLE_RGBA pname is
  desktop-only (ES 3.0 has only per-channel SWIZZLE_R/G/B/A)** — every non-DXT texture
  raised GL_INVALID_ENUM there (all 129 phantoms = simply every normal-path texture), the
  textures actually load, but the swizzle never applied (BGRA assets rendered R/B-swapped;
  the invisible options-menu focus highlight is plausibly the same breakage).
- **6.3:** both SWIZZLE_RGBA call sites (glTexture gli path, glSH_Texture theora path) ->
  four per-channel glTexParameteri calls (legal on desktop + ES).
- **6.4 (the black sky):** sky2.vs under USE_VTF (device reports 16 VTF units) pre-scales
  the vertex color by texelFetch(s_tonemap) in the VERTEX stage — on the ES monolithic
  path that vertex-stage sampler read returns 0 -> sky*0 = black while the world stays lit
  (combine samples tonemap in the FRAGMENT stage, demonstrably fine). Clouds share the
  pattern. Fix: `#undef USE_VTF` under GL_ES in sky2.vs/ps + clouds.vs/ps BEFORE the
  iostructs include (varyings + both stages consistently non-VTF; tonemap via tex2D in
  PS). Gate 279/279, link_check 137/137.
- Open items tracked: options-menu focus/interaction on pad (likely improved by 6.3 —
  verify), non-VTF sky slightly different tone curve (acceptable), proper vertex-stage
  sampler binding for ES monolithic programs (future — would restore VTF).

### 2026-07-18 (later) — 🎮 FULL GAME LOOP ON A BLUETOOTH PAD (build 10022072, no code changes)

Research into the console remaster's controls (S.T.A.L.K.E.R. Legends of the Zone Trilogy,
PS5/Xbox) revealed OpenXRay already ships a near-identical default gamepad scheme in code
(xr_level_controller.cpp:972 predefined_bindings: LS/RS move+look, RT/LT fire+ADS, A jump,
B crouch, X reload, Y use, RB inventory, LB jobs, D-Pad quick slots, R3 torch, Back=ESC,
full UI/PDA pad navigation) — and SDL_INIT_GAMECONTROLLER + hot-plug were already active.
The user paired a Bluetooth pad: **everything works with zero new code.** Screenshots:
inventory open (Degtyarev, 2500 RU, item descriptions, D-Pad slot assignment), weapon
switching (pistol), and the IN-GAME PAUSE MENU (Return/Save/Load/Options/Quit) — save and
clean exit through the menu confirmed by the log's full shutdown stats. In-game memory:
heap 1.43 GB, textures 324 MB (mip-skip effective); 3.1 GB remains only as the load-time
peak. **Phase 5 (controls) is functionally complete for pad players; the virtual touch pad
is now an optional enhancement, not a blocker.** LotZ extras not in stock OpenXRay (L1
weapon-wheel radial, shift-modifier D-Pad layers, gyro aim) noted as future polish.
Next priorities: world brightness/lighting (near-black night; sun/shadow partially
stubbed), the ~150 non-DXT 0x500 textures, virtual touch pad, load-peak memory, audio
session, cfg_save lifecycle.

### 2026-07-18 — 🏆 THE GAME IS PLAYABLE ON iPHONE (build 10022072)

**S.T.A.L.K.E.R. Call of Pripyat runs, renders, plays, and SAVES on an iPhone 15 Pro Max.**
The user is in Zaton at night: first-person view, AK-74 with visible actor arms (SKINNED
MODELS render — family D's runtime works on device), HUD (ammo 30/180, quick slots,
minimap + game time), opening missions firing ("Stingray 1-5: investigate the crash
site"), FIRED ROUNDS (tap = LMB = shoot, audio works), autosave OK ("11053 objects are
successfully saved" -> .scop in Documents/_appdata_/savedgames). phys_footprint after
load: **3.1 GB — survived within ~300 MB of the jetsam limit**; skipping the prefetch was
the difference between death and gameplay.

The final ladder (all on 2026-07-17→18):
- **4.22 float RTs:** ES caps line proved EXT_color_buffer_float ABSENT / half_float
  present on A17 → 32F targets (G-buffer/accum/luminance) were FRAMEBUFFER_INCOMPLETE.
  ConvertTextureFormat downgrades R32F/RG32F/RGBA32F → 16F kin on iOS.
- **4.23 DXT mip-skip:** decode-time drop of top mips to a 1024px cap (ui\ exempt) — 4x
  texture memory cut; uploaded base extent reported back to CTexture dims.
- **4.24 the "backgrounds itself" mystery:** CHW::OnAppDeactivate ran desktop ALT-TAB
  behavior (SDL_MinimizeWindow on fullscreen focus loss) — on iOS minimizing IS
  backgrounding. Guarded out + activate/deactivate now logged; idle timer disabled.
  (Falsified as the New-Game killer by the very next log — kept as a real latent bomb.)
- **4.25 the actual killer — jetsam OOM:** JetsamEvent 22:54 caught it red-handed:
  largestProcess xr_3da, active+frontmost at 1.92 GB resident, system killing daemons
  around it (vm-compressor-thrashing). No xr_3da .ips ever — jetsam writes JetsamEvent
  files. Fix: skip Prefetch() on iOS (~1.6 GB spike; textures/models lazy-load via
  apply_load) + log task_info phys_footprint each load phase.
- Result: first try after 4.25 → in-game, shooting, saving. **Phases 3+4 DONE. Phase 5.1
  (menu tap) DONE. The port plays.**

Known state in-game: world very dark (night start + sun/shadow passes partially stubbed
— tree_s alpha shadows, disabled blenders); ~150 non-DXT textures still 0x500 (pfx/water/
ui_common family — separate format wall); occlusion culling off (perf headroom later);
touch = LMB only. **NEXT: Phase 5.2 — virtual gamepad** (movement stick via
kMOVE_AROUND/kLOOK_AROUND ControllerAxisState synthesis — audit found the engine's analog
path ready-made; look-drag; fire/aim/use buttons; pause/ESC button so the user can exit
without killing the app). Then: memory headroom (ASTC — Plan 4.9), lighting/brightness,
non-DXT texture formats, AVAudioSession, cfg_save lifecycle.

### 2026-07-17 (later) — Phase 5 touch + "New Game" reaches the 3D world; 12-agent audit; ES render-path hardening

- **Phase 5.1 touch (build 10022064): the menu is OPERABLE.** User entered Options and went
  back, then tapped New Game. Root cause of dead taps: iOS launches captureInput=true ->
  exclusiveInput=true -> CUICursor stuck in delta-accumulation mode; also m_bound_to_system_cursor
  mis-picked (logical points vs retina-pixel dwHeight). Fix: iOS forces exclusiveInput=false,
  disables SDL touch<->mouse synthesis, new CInput::TouchUpdate maps SDL_FINGER* -> logical
  points and drives the exact desktop click path (IR_OnMouseMove + IR_OnMousePress(MOUSE_1));
  iGetAsyncMousePos returns the touch pos; OnDeviceReset forces the absolute cursor path.
- **"New Game" loads Zaton almost completely** (build 064 boot log, 4102 lines): New game
  created, 6464 spawns, ALL gameplay Lua (dialog_manager/smart_covers/surge_manager/...),
  player, HOM+portal+objspace caches, level geometry. Crashed on the FIRST 3D frame. Two big
  positives: Lua/luabind on arm64 works (the #12 hidden-wall fear is dead), and the render/
  present chain is solid up to the world. NB: Analytics did not record a fresh .ips for 064.
- **853 `shadow_direct_*|null|null` link failures**: depth-only shadow passes have a "null"
  pixel shader (sh==0). Desktop links vertex-only via SSO pipeline; ES uses the monolithic
  path and requires VS+FS. **4.18**: inject a shared do-nothing FS (`#version 300 es; void
  main(){}`) when ps==0 in GLLinkMonolithicProgram (ES-only path). Known minor: alpha-tested
  vegetation shadows (tree_s) lose discard until a proper alpha stub is added.
- **12-agent audit** (`doc/iOS-Port-Audit-2026-07-17.md`, ran via Workflow; hit the account
  spend limit mid-run, resumed on the raised limit). Verdict: plan still valid; Lua solid;
  most iOS diffs clean. It independently pinpointed the New-Game crash and the next walls.
- **4.19 (audit-driven ES render-path fixes):**
  - **P0 (the crash): occlusion queries.** QueryHelper.h uses GL_SAMPLES_PASSED (not an ES
    target) + glGetQueryObjectiv/i64v (desktop-only, NULL glad entry -> crash) in the frame-1
    light-visibility test. Force R_occlusion disabled on iOS (== -no_occq; begin->0, get->
    "visible"). Proper ES path (GL_ANY_SAMPLES_PASSED + glGetQueryObjectuiv, whose 0/1 maps
    onto the existing "0==fragments" cull test) deferred as an optimization.
  - **P1 (next crash): glSamplerParameterIuiv(GL_TEXTURE_BORDER_COLOR)** needs
    EXT_texture_border_clamp, absent on ES 3.0 (NULL entry, crashes on sun shadow sampler) —
    runtime-guard the pointer.
  - **P2 (enum spam):** GL_TEXTURE_LOD_BIAS (2 sites) and GL_CLAMP_TO_BORDER guarded to iOS
    (-> CLAMP_TO_EDGE / skip).
- **4.20 family D (gate hygiene, shader-source only): gate 265/279 -> 279/279.** SKIN_NONE
  model VS declared NORMAL float4 vs the float3 v_model.N; strict glslang rejected the assign,
  Apple truncates (so never a device blocker). Extended the float3 NORMAL guard to SKIN_NONE
  across 8 iostructs headers. link_check still 137/137.
- **Audit's remaining ranked walls (see the audit doc):** float render-target renderability
  (RGBA16F likely OK, R32F risky -> possible black screen in-game if EXT_color_buffer_float
  missing); AVAudioSession unconfigured (interruptions permanently mute); skinned-geometry
  runtime (short4/DWORD vertex format + sbones upload, untested on GL-on-Metal); DXT->RGBA8
  memory inflation (jetsam risk on big levels, cmem hit ~1.6 GB during Zaton load); no iOS
  lifecycle handler (settings only persist on explicit Accept). Full list + file:line in the
  audit doc.

### 2026-07-17 — 🎉 MILESTONE: THE MAIN MENU RENDERS ON iPhone (build 10022063)

**S.T.A.L.K.E.R. Call of Pripyat's main menu is visible and stable on an iPhone 15 Pro Max —
native X-Ray engine, OpenGL ES 3.0 on Metal, no ANGLE.** The day's crash-ladder, each .ips
leading exactly one step deeper:

1. **4.13** skip logo intros on iOS (`allow_intro()` returns false — no cmdline for -nointro;
   movies can't render and wasted 40s/boot) + DXT-decoder per-stage error scoping (sticky
   glGetError pollution from the video path).
2. **4.14** menu crash #1: the animated .ogm menu background hit **glMapBuffer — which does
   not exist in ES** (NULL glad entry → PC=0, the CODESIGNING "Invalid Page" kill). Fixed:
   glMapBufferRange (core in GL 3.0+ AND ES 3.0), null-guards, RGBA+BGRA-swizzle frame upload
   (ES has no GL_BGRA). Plus the **float4 varying ABI** completing A2 for good: every varying
   declared float4 both sides (pad on write / swizzle on read, `.w=1` so projective `tc.xy/tc.w`
   is exact) — dual-type (USE_R2_STATIC_SUN/USE_VTF) branches made branch-aware after the
   transform initially corrupted them. **link_check 137/137 pairs clean.**
3. **4.15** menu crash #2: font renderer died on **glGetTexLevelParameteriv (ES 3.1+)**.
   Swept the whole renderer for ES-3.0-absent calls — exactly 4 (that one, glPolygonMode,
   singular glDrawBuffer, glMapBuffer) — all fixed/guarded; texture dims now reported by
   texture_load from the image (RT-wrapped CTexture dims-on-ES noted as a gap).
4. **4.16** menu crash #3, the big one: **the draw call itself — glDrawElementsBaseVertex is
   ES 3.2**. No draw had EVER executed on device. Added the classic-path base-vertex fallback
   (fold baseV*stride into attrib pointers via new SetGLVertexPointerBase + vb_base cache,
   plain glDrawElements) and fixed a latent fallback bug (VAO switch with unchanged VB left
   the new VAO pointer-less).
5. **4.17** the black screen with a *running* menu (menu music, zero crashes): **iOS has no
   framebuffer 0** — CHW::Present blitted the engine FBO to 0 (the recurring 0x506) and the
   image went nowhere. Present now targets SDL's view FBO (SysWM `uikit.framebuffer`) scaled
   to the retina drawable. **Next build: the menu appeared. 🎉**
- Loose ends carried forward: `ui_magnifier2.dds` still 0x500 on the non-DXT path (one
  texture); menu background video doesn't create (**corrected in 6.11: NOT a D3D wrapper — the
  GL Theora path was fine; a stale sticky GL error was misread as a creation failure**);
  A2-tail visual verification in-game; family D skinning; RT-wrapped texture dims on ES.
- **NEXT: Phase 5 — touch input.** The menu is visible but taps do nothing yet. Prior art:
  the user's own OpenGothic iOS pad/touch system (see memory note) to adapt for X-Ray.

### 2026-07-16 (end of day) — Build 10021058 verdict: ALL SHADERS PASS on device; kill-at-menu is the next wall

- **The shader layer is DONE on-device (for now):** the 10021058 xr_boot.log has **zero**
  `shader compilation failed` and **zero** `failed to link` lines — MRT locations + the
  real-define-set fixes cleared everything the device compiles at boot. `ui_main_menu.script`
  loads. (Family D model-VS + soft-failed effect pairs remain, but nothing at boot trips them.)
- **DXT decode: one suspicious report, likely a false alarm.** `0x506: iOS DXT->RGBA8 upload
  failed: intro_back.dds` — but 0x506 (GL_INVALID_FRAMEBUFFER_OPERATION) is not a texture-upload
  error; GL errors are STICKY and the video path raises 0x500/0x506 right around this point, so
  the decoder's trailing glGetError() almost certainly read someone else's leftover error.
  TODO: drain glGetError() before the upload and check per-stage (storage vs sub-uploads) so
  reports are attributable. Only ONE dds goes through before the log ends, so broad verification
  of the decoder needs the next run.
- **The real wall: the app is killed at the exact moment the intro movies end** (~40s of audio —
  same timing every run) — i.e., right when the MENU would appear. No fatal, no engine log tail →
  a hard iOS kill (watchdog 0x8badf00d? jetsam OOM? GPU fault). The intro Theora videos
  spin GL errors the whole time and can't render; they're also the prime suspect zone for the
  kill. **[Corrected in 6.11: the `D3D-wrapper CreateTexture(A8R8G8B8)` diagnosis written here
  was wrong — that is the DX11 path, not compiled on iOS. The real cause was a sticky-GL-error
  false positive at OGM texture creation: the create sequence read an error left behind by some
  earlier path and destroyed a good texture.]**
- **NEXT SESSION, in order:**
  1. **Pull crash reports FIRST** (`idevicecrashreport` staging; look for today's `xr_3da-*.ips`)
     — the termination reason (watchdog/jetsam/GPU) decides everything downstream.
  2. **Skip the intro movies on iOS** — they can't render (video-texture path unported), they
     cost 40s per boot iteration, and they bracket the kill. Find the sequence trigger: grep
     configs/scripts for `bitcomposer`/`intro` (CUISequencer in xrGame plays logo movies), guard
     it out on iOS. This alone may reach the menu.
  3. **Harden the DXT decoder's error scoping** (drain before, per-stage checks) so the next log
     tells the truth about texture uploads.
  4. Then: menu render test → video-texture fix (in-game PDA/TV need it later; **the "D3D-wrapper
     port" framing here was wrong — see 6.11, no port was ever needed**) →
     A2-tail effect pairs → family D → Phase 5 (touch input — menu needs taps).

### 2026-07-16 — Phase 4 Slices 4.11+4.12: THE ENGINE RUNS — MRT locations, real-define shader fixes, DXT→RGBA8 decode

- **Soft-fail build verdict (xr_boot.log, 8307 lines, ZERO fatals):** the engine boots to the
  **main loop** — `Starting engine...`, intro movie AUDIO plays (bitcomposer/AMD oggs — what the
  user heard), **Lua scripts load** (`xr_s.script`), memory stats print. Soft-fail worked exactly
  as designed: broken passes logged + disabled, boot continues. Black screen root cause now
  explicit in the log: `! OpenGL: 0x501: Invalid 2D texture ... intro_back.dds` — **Apple ES 3.0
  has no S3TC/DXT**, so every CoP .dds upload failed. (App backgrounded after ~40s — likely
  jetsam/watchdog while the intro-video loop spins; to observe after textures land.)
- **Shader failures left on device were just TWO roots (4.11):**
  (a) `GLSL 300 requires that all fragment shader outputs have a location if there is more than
  one output` — sky2/combine_1/deffer_particle/etc.; desktop got locations via
  glBindFragDataLocation (guarded out on ES in 4.6a). Added `layout(location = N)` to all 18
  `SV_Target0/1/2` declarations across 7 iostructs headers (valid on desktop 4.10 too, same values
  as the API binding).
  (b) accum_volumetric_sun int/float in the SUN_SHAFTS path — **the gate had never compiled that
  path**: its define set lacked the device's real options. Gate now uses the exact device define
  set (from the engine's dumped preamble: FP16_*, USE_BRANCHING, USE_SOFT_WATER, SSR_QUALITY 3,
  USE_DOF, SUN_SHAFTS_QUALITY 2, SSAO_QUALITY 3, SUN_QUALITY 1, ALLOW_STEEPPARALLAX). That
  exposed + fixed: RAY_SAMPLES float uses (define stays int for the loop), sload.h steep-parallax
  (maxSamples/minSamples, `float(i)<nNumSteps`, `1.0-fParallaxAmount`). **Gate: 265/279 on the
  real device paths** (14 left = family D).
- **4.12 — textures (Plan 4.9 runtime fallback):** software **BC1/2/3/4/5 → RGBA8 decoder** in
  glTexture.cpp (`ios_upload_dxt_as_rgba8`): decodes every face/mip, uploads GL_RGBA8 (2D+cube),
  edge-safe for small mips. Memory 4-8x per texture — correctness first; offline ASTC transcode
  stays the long-term plan. Non-DXT path on iOS now translates via gli `PROFILE_ES30` (ES has no
  GL_BGRA upload; ES profile maps BGRA8→RGBA+swizzle). Desktop untouched (PROFILE_GL33).
- **Known follow-ups:** Theora/AVI video textures still fail on ES (`Invalid video stream`,
  non-fatal — intro movies stay audio-only; menu doesn't depend on them). **[Corrected in 6.11:
  the `D3D-wrapper CreateTexture(A8R8G8B8)` attribution recorded here was wrong — that is the
  DX11 implementation, which iOS does not compile. xrRenderGL's Theora path was already
  ES-correct; the `Invalid video stream` message was a sticky-error false positive. `.avi` is a
  separate matter: it is hard-guarded to Windows only (glSH_Texture.cpp:134, :247) and was never
  live on iOS.]** A2-tail effect pairs (accum_sun↔2uv, distort↔particle, lplanes) are
  soft-failed, to fix root-by-root. Family D skinning. The ~40s backgrounding to re-observe.
- **Expectation for the next device run: the MAIN MENU renders.** All menu UI is DDS/DXT → now
  decodable; menu shaders compile+link; engine main loop confirmed running.

### 2026-07-16 — Phase 4 Slice 4.10: boot-log + soft-fail passes (device confirms A2 works, tail mapped)

- **Debug visibility saga (why "no new log"):** the A2 build sat on a black screen with NO log.
  Diagnosis chain: (1) `ForceFlushLog` is off by default and iOS has no command line → log only
  flushes on fatal/exit → made per-message flush default on iOS (`log.cpp`). Still no log →
  (2) os_log mirror added — but the old `idevicesyslog` relay does NOT surface a third-party
  process's os_log (only Apple frameworks dual-log) → dead end. (3) **The keeper:**
  `Documents/xr_boot.log` — every `AddOne()` line mirrored to a plain file at a fixed path,
  `fflush` per line, depends only on libc + Documents. Works regardless of FS/CreateLog/hangs.
  **This is now THE on-device debug channel** (main log is redundant with it for boot issues).
- **xr_boot.log verdict: A2 WORKS.** The engine marched PAST the old accum_sun_mask fatal (that
  pair now links) to the NEXT C++ blender (`CBlender_accum_direct`), failing on
  `stub_notransform_aa_AA|accum_sun_nomsaa`: TEXCOORD5 (`xrvary13`) VS writes float4, FS reads
  float2 → link fail → same `unsupported uniform` cascade. So the black screen was a *fatal*
  before the log ever opened — not a hang. Fixed that pair (p_aa_aa_sun.h: read float4, take .xy —
  identical to what desktop SSO did by location).
- **link_check now scans C++ blenders too** (`r_Pass("<vs>","<ps>")` in
  src/Layers/xrRender/blenders/*.cpp) — those, not .s scripts, are the boot-critical
  render-target passes. Full picture: **121/137 pairs clean; 16 broken = 3 roots:**
  (a) `stub_notransform_2uv` (v_TL2uv float2 TEXCOORD0/1) ↔ the whole `accum_sun_*` FS family
  reading float4 `tc` and using `tc.xy/tc.w` — the FS is shared with a float4 VS (`accum_sun`),
  the VS struct is shared with float2 consumers (`p_TL2uv`) → inherently location-based
  multi-consumer interface; fix = widen v_TL2uv varyings to float4 (`.w=1` for fullscreen) +
  make float2 consumers read `.xy` — needs visual verification (sun shafts);
  (b) `model_distort*` ↔ `particle_*` (7 pairs): FS reads TEXCOORD1 the VS never writes;
  (c) `model_def_lplanes|base_lplanes`: float4 vs float3 COLOR0.
- **Strategy decision (user asked for a 3rd way beyond native-vs-ANGLE):** chose **soft-fail**
  (option 3a): a failed monolithic link no longer kills boot. Root cause of the fatal: link
  failure returns program 0, `_LinkPP` parsed the constant table of program 0 (garbage uniforms)
  → `fatal("unsupported uniform")`. Now: parse constants only if linked; log
  `! Pass '<name>' failed to link — pass disabled`; `CBackend::Render` skips draws when `pp==0`.
  **One build now surfaces ALL broken pairs in xr_boot.log at once, and the menu (2D UI) likely
  doesn't need the broken sun/effect passes at all.** Remaining roots get fixed root-by-root;
  the float4-ABI transform (make EVERY varying float4 — kills type mismatches by construction)
  is the systematic fallback if mismatches keep appearing; ANGLE stays plan C.
- Also: pymobiledevice3 doesn't build on this Windows box (native wheels); idevicescreenshot
  needs a Developer Disk Image on iOS 17 — neither is part of the loop. SideStore suffix quirk:
  both OpenGothic and OpenXRay share the `.RMJWWPF379` team suffix — an earlier "no log" was
  the user launching Gothic by mistake; check `fgApp` in syslog when in doubt.

### 2026-07-16 — Phase 4 Slice 4.9 (A2): slot-based varying names for ES monolithic linking

- **The family-C build (10021) got PAST shader compilation on Apple ES** — first time both stages
  of `accum_sun_mask_nomsaa` compile on-device. Then it hit **A2 exactly as predicted**, at link:
  `Output of vertex shader 'v2p_TL_Tex0' not read by fragment shader` / `Input of fragment shader
  'p_TL_Tex0' not written by vertex shader` → the `unsupported uniform` fatal (confirmed cascade of
  the broken link). GLSL ES 3.00 links varyings **by name**; desktop SSO linked **by location**, so
  the paired VS/FS deliberately use *different* names (`v2p_*` vs `p_*`).
- **Scope investigation (user asked first):** it's NOT a cosmetic prefix swap. Comparing all 25
  varying structs, only 15 have matching field names/slots; the rest are pure location bridges — e.g.
  blender `("accum_volumetric","accum_volumetric")` has the FS read slot 0 as a different name/width
  than the VS writes. So manual per-name renaming is out; the fix must map to locations.
- **Decision (user chose native over ANGLE):** rename EVERY vs→fs varying to a **slot-encoded name**
  `xrvary<location>` (TEXCOORD0→xrvary8, COLOR→xrvary0, …) in both the VS `out` and FS `in` decls and
  their `main()` uses. Then any VS-out and FS-in at the same slot share a name → ES links them by name
  exactly as desktop links them by location. Verified collision-free (no two varyings share a slot in
  a file), **304 names across 82 files**, done by a deterministic transform. Desktop unaffected (still
  matches by the `layout(location=)` the VARYING macro keeps). glslang single-stage compile still
  265/279 — no regression.
- **New offline tool `misc/ios/shadercheck/link_check.py`:** glslang's own linker is too lenient to
  catch cross-stage varying mismatches (it returned rc=0 on the very pair the device rejected), so this
  parses the blender `shader:begin("<vs>","<fs>")` pairs and checks the ES interface rule (every FS
  `in` has a same-name, same-type VS `out`). After the rename: **40/49 pairs link clean, up from ~0**,
  incl. the boot-critical `accum_sun_mask`.
- **The 9 remaining pairs are genuine interface mismatches the rename exposes** (FS reads a wider type
  or a varying the VS never writes — SSO silently tolerated it): `model_distort*|particle_*` (heat-haze/
  anomaly effects), `model_def_lplanes|base_lplanes`, `stub_notransform_2uv|accum_volumetric_sun_normal`.
  These are effects, not the deferred-base boot path — deferred for per-pair fixes (narrow the FS input
  or widen the VS output per what components are actually used) informed by which ones the device hits.
- **Also:** `glsl_es_check.py find_include` made case-INSENSITIVE — several shaders `#include` mixed-case
  names (`iostructs\p_TL.h`, file is `p_tl.h`) that Windows + iOS APFS resolve but a case-sensitive Linux
  CI did not, mis-skipping ~30 real entries as include-only (CI reported 235/249 vs local 265/279; now
  they'll agree).
- **NEXT:** device test build 10021+A2. Expect the engine to march past the accum blenders into more
  render-target / shader compiles; watch whether any of the 9 effect-pairs block, and whether NEW ES
  issues appear beyond varyings (uniform introspection, RT formats, draw calls — Plan 4.7–4.10).

### 2026-07-14 — Phase 4 Slice 4.8: local glslang + family C grind (int/float strictness), gate 117→230

- **Unlocked a fully-offline fix loop.** Downloaded the official Khronos `glslangValidator`
  (16.4.0) to `tools/glslang/` (gitignored via `/tools/`), so the shader gate now runs locally in
  seconds instead of one CI round-trip per fix. Built a diagnosis helper (`scratchpad/diag.py`):
  it assembles a shader exactly like the gate, runs local glslang, and prints each ERROR with the
  offending assembled-source line — the whole family-C grind runs on this.
- **Family C = GLSL ES 3.00 int/float strictness.** ES (like WebGL2) has **no implicit int→float
  in operators / function args / assignments**; desktop GLSL 1.20+ does, which is exactly why these
  shaders compile on desktop and not on ES. Confirmed glslang is a faithful proxy for this (Apple's
  ES compiler enforces the same). The errors come as a *chain* per shader (fix one, the next
  surfaces), so the compile count only moves when a shader's whole chain is clean — the trick was to
  drive one representative shader to green, fixing the **shared headers** it pulls, which then clears
  every shader sharing that chain at once.
- **Fixes (mostly in shared headers → high leverage):**
  - `gather.ps`: `sm_gather`/`sm_minmax_gather` did `float * int2 offset` and passed int `0` LOD to
    `textureLodOffset` → `float2(offset)` + `0.0`. (Unblocked ~90 accum/shadow shaders at once.)
  - `shadow.h`: `-2*J0.xy`→`-2.0*`, `minmax < 0`→`< 0.0`, and the `textureLod(...,0)` LODs → `0.0`.
  - `sload.h`: `S.height = 0`→`0.0`, `* detail.rgb * 2`→`* 2.0`, parallax `textureLod(...,0)`→`0.0`.
  - Batch int-literal transform (`scratchpad/fix_intlit.py`, 26 edits, each prints before→after):
    `textureLod(...,0)`→`0.0`, `pow(x,N)`→`pow(x,N.0)`, `step(x,0)`→`0.0`, aref remap
    `/(1-def_aref*0.5)`→`/(1.0-...)`, across hmodel/lmodel/rain_layer/ssao/water/deffer_*.
  - `accum_volumetric.ps`: `saturate(1 - rsqr*…)`→`1.0 -` (covers _msaa/_nomsaa which include it).
- **Structural / non-int-float fixes in the same slice:**
  - **VARYING relocation:** the `VARYING()` macro lived in `gl/common.h`, but `stub_notransform_*.vs`
    include only `common_iostructs.h` → `shared/common.h` (never `gl/common.h`), so they saw VARYING
    undefined → parse error. **Moved VARYING into `shared/common.h`** (transitively still present for
    common.h users). Fixes all 3 stubs.
  - **gl_ClipDistance:** `v_volumetric.h` wrote `gl_ClipDistance[]` (a desktop/EXT built-in absent in
    ES 3.00) in an unguarded loop → guarded the loop `#ifndef GL_ES` (the array decl was already
    guarded). ES just skips frustum-clipping the light volume — minor overdraw, not a failure.
  - **Misnamed file:** `accum_volumetric_sun_normal .ps` had a **trailing space before .ps** so the
    engine (blender references `accum_volumetric_sun_normal`) could never load it on-device — renamed
    to drop the space; also `#unfdef`→`#undef` (invalid directive; "normal" = non-minmax variant).
- **Gate accuracy:** added `GBUFFER_OPTIMIZATION=1` (from 4.5c), plus `USE_HWSMAP`/`USE_HWSMAP_PCF=1`
  (the device log shows all three) so the gate compiles the r3/deferred + HW-shadow path CoP actually
  runs, not the dead fallback (removed 6 false `O.depth`/`shadow_direct` failures). Baseline note: the
  gate dropped 148→117 when GBUFFER_OPTIMIZATION was added — expected, it exposed real gbuffer-path ES
  errors the minimal define-set had skipped; 117 is the honest starting point for family C.
- **Result: local gate 117 → 265/279 (95%).** (parts 1–8; the denominator dropped 286→279 because the
  gate now skips 6 include-only helper files + 1 dead file — see below.) glslang 16.4.0 local matched
  CI's apt glslang exactly at the 230 checkpoint, so the local loop is faithful.
- **Method that carried it:** dedup the *first-error source line* across all failing shaders — repeatedly
  it was 2–18 unique offending expressions covering 30–90 shaders, mostly in shared headers (skin.h
  `v.N.w*255`/`1-w0-w1`/`v.ind[i]*255` was ~14 shaders each; shared/watermove.h, hmodel.h, cloudconfig.h).
  Batches applied via small print-every-change transforms. Also fixed structurally: `p_flat_atoc.h`
  forward-declared `_main(p_bumped)` instead of `p_flat` (copy-paste, broke the 4 ATOC-flat shaders);
  `v.uv` int2 → `float2(v.uv)` for unpack_tc_base; CLOUD_SPEED macro `(2*0.05)`→`(2.0*…)`.
- **Gate accuracy improvements (part 5/8):** skip include-only helpers (no generated `void main()`:
  gather/fxaa/ssao*.ps — #included by real entries, referenced by no blender); skip the dead
  `ssao_hdao_new.ps` (DX-only HDAO compute, its include is commented out); define `USE_HWSMAP`,
  `SKIN_NONE`, `SSAO_QUALITY`/`SSAO_OPT_DATA` defaults so it models the path CoP actually compiles.
- **The 14 remaining (deferred, ONE issue not fourteen):** every failing shader is a **model vertex
  shader** doing `I.N = v_model_N` where the NORMAL attribute is `float4` (packs a skin index/weight in
  .w) but `v_model.N` is `float3` → strict ES rejects vec4→vec3 (lenient desktop drivers tolerate it).
  It's a **skinning-variant vector-width inconsistency** (struct N is float3 for v_model/skinned_0/1 but
  float4 for skinned_2/3/4; the attribute is float3 only under SKIN_0) — no single-line fix, and it
  touches the vertex-format contract, so it needs a variant-aware change + on-device verification rather
  than a rushed edit. This is the next family (call it "family D: skinning vertex-format").

### 2026-07-14 — Phase 4 Slice 4.5c: guard `gl_FragCoord`/`gl_SampleID` redeclaration for ES (first real on-device compile error)

- **The definitive build finally delivered the port to the device.** On-device log
  (`Documents/xray_*.log`, now at the root) confirms: renderer up on **`OpenGL ES 3.0 Metal` /
  `GLSL ES 3.00`** (Apple A17 Pro), FS + CoP `.db` mounted (39265 files / 12 archives), 2736
  textures, and — the point — the **dumped shader source now carries all our fixes**: `#version
  300 es`, the `#ifdef GL_ES` precision prelude, `VARYING()` macro, default option macros. The
  seeding-refresh fix worked; the gate→fix→device loop is real now.
- **First real ES compile error (which the glslang gate had NOT caught):**
  `ERROR: Regular non-array variable 'gl_FragCoord' may not be redeclared`, on the very first
  shader compiled (`accum_sun_mask_nomsaa.ps`, deferred path). Root cause: every fragment shader
  pulls an `iostructs/p_*.h` header that does, under `#ifdef GBUFFER_OPTIMIZATION`,
  `in vec4 gl_FragCoord;` — a redeclaration of a **built-in**. Desktop GL tolerates it (used there
  to attach `origin_upper_left`/`pixel_center_integer`-style layout qualifiers); **GLSL ES 3.00
  forbids redeclaring `gl_FragCoord`** — it's implicitly available. Same class: `in int
  gl_SampleID;` under `MSAA_OPTIMIZATION` (off on iOS, but same illegal construct).
- **Why the gate missed it:** `glsl_es_check.py` only defined `SMAP_size`, **not
  `GBUFFER_OPTIMIZATION`**, so the `#ifdef GBUFFER_OPTIMIZATION … in vec4 gl_FragCoord … #endif`
  block was preprocessed out → the gate never saw the redeclaration. The device's r3/deferred path
  (what CoP runs) *does* define it (`#define GBUFFER_OPTIMIZATION 1` in the dumped source).
- **Fix (shaders):** wrap every standalone built-in redeclaration in `#ifndef GL_ES … #endif` —
  desktop keeps it verbatim, ES drops it (built-in stays available, and all the *usages*
  `_main(I, gl_FragCoord)`, `I.pos2d = gl_FragCoord`, `texelFetch(…, gl_SampleID)` are untouched
  and legal on ES). Python transform (`misc/…/guard_builtin_redecl.py`, scratchpad), CRLF-preserving,
  idempotent: **32 redeclarations across 24 files** (iostructs `p_*.h` + `depth_downs.ps`,
  `copy.ps`, `copy_p.ps`). Verified 0 unguarded remain.
- **Fix (gate):** add `GBUFFER_OPTIMIZATION=1` to `BASE_DEFINES` so the gate assembles the deferred
  fragment interface the device actually uses, and this class fails offline from now on. (The
  guarded redeclaration still passes — glslang predefines `GL_ES` for a `300 es` unit, so it drops
  the line exactly like the device.) glslangValidator isn't on the Windows box, so the new number
  lands from the CI `shader-check` job, not locally.
- **The downstream `FATAL … unsupported uniform` (`glR_constants.cpp:20`) is a cascade, not a
  second bug:** the fragment failed to compile → the monolithic program failed to link
  (`ES requires exactly one vertex and one fragment shader`) → `R_constant_table::parse` ran on the
  broken program and hit its `default: fatal("unsupported uniform")`. On a *valid* link every
  uniform our shaders declare is `float*/mat4/mat4x3/sampler2D/3D/Cube/2DShadow` — all in the
  handled switch — so this should vanish once the fragment compiles. Watch it on the next log; if it
  survives a valid link, the real fix is adding the offending GL type (likely a `uint`/`uvec`
  uniform or `sampler2DArray`) to the `parse` switch.
- **Next:** rebuild → SideStore Update → run → new `Documents/xray_*.log`. Expect
  `accum_sun_mask_nomsaa.ps` to compile now and the engine to march to the *next* shader/link error
  (this is the grind: one class of ES error per round). A2 (varying name matching in monolithic
  programs) is still the deferred link-time question we haven't reached yet.

### 2026-07-14 — CRITICAL: shader fixes weren't reaching the device (gamedata not refreshed)

- **Symptom:** first on-device run of an ES-capable build showed a **black screen, no crash**.
  The engine log (`_appdata_/logs/*.log`, pulled via File Sharing) was gold: FS up, **CoP data
  mounted** (39264 files / 12 archives), renderer up on **`OpenGL ES 3.0 Metal` / `GLSL ES 3.00`**
  (Apple's own GL-on-Metal — so native ES 3.0 is Metal-backed, no ANGLE needed to run), 2736
  textures processed — healthy all the way to shader compilation, which failed with
  `version '410' is not supported` (this was a pre-4.3 build). All expected.
- **The trap (found in the log's dumped shader source):** the shader text showed **none of our
  fixes** — no `#ifdef GL_ES` precision prelude, `clip(x)` still `if (x < 0)` not `< 0.0`. The
  engine reads shaders from **`Documents\gamedata\shaders`**, but `CLocatorAPI::setup_fs_path`'s
  iOS seed copied the bundled `gamedata` into Documents **only on first launch** (`if
  access(fsgame.ltx)!=0`). So the device was stuck on the shaders from whatever build was FIRST
  installed — **every shader fix since then never shipped.** The gate was green while the device
  ran stale files.
- **Fix (`LocatorAPI.cpp`):** on iOS, **refresh `fsgame.ltx` + the bundled `gamedata` overlay
  from the `.app` on every launch** (drop the first-launch guard). O_TRUNC overwrite; the user's
  CoP `.db` archives (resources/localization/patches — OUTSIDE gamedata) and `_appdata_`
  (logs/saves) are untouched. TODO: gate on a stored build-id marker to skip the copy when
  unchanged (startup cost). **This is what makes the whole gate→fix→device loop actually work.**
- **Log path QoL (`res/fsgame.ltx`):** `$logs$` moved from `$app_data_root$\logs\`
  (`_appdata_/logs/`, buried) to `$fs_root$\logs\` → **`OnMyiPhone/OpenXRay/logs/`**, visible at
  the top level in File Sharing. (Takes effect now that fsgame.ltx refreshes every launch.)
- **Net:** the next build carries 4.3 (`#version 300 es`) + this refresh + the log move, so the
  ~148 gate-passing shaders should finally compile on-device and we hit the real A2 link reality.

### 2026-07-14 — Phase 4.3: engine emits ES on iOS + ANGLE decision + FSR planned

- **Slice 4.3 (engine ES emission):** `rgl_shaders.cpp` now emits `#version 300 es` +
  `precision highp float/int;` on iOS (guarded `XR_PLATFORM_APPLE_IOS`) instead of
  `#version 410` + the desktop `GL_ARB_separate_shader_objects` extension. This is what makes
  the device build actually try the ES shaders we've been greening in the gate — the ~148
  compiling shaders should now compile on-device, and we hit the real monolithic-link (A2)
  behaviour. Desktop path unchanged.
- **Decision — native ES 3.0 now, ANGLE-on-Metal as the A2 fallback (not now).** Weighed
  switching to ANGLE (ES 3.1) immediately. Verdict: it does NOT help the current wall.
  - ANGLE would save **A2** (ES 3.1 has separable_shader_objects → varyings match by
    *location*, so no vs↔fs name reconciliation), enable `sampler2DMS` MSAA variants, and keep
    the renderer's SSO architecture. Genuinely valuable — *for linking*.
  - ANGLE does **not** save the shader-source ES conversion: `#version 310 es` GLSL is just as
    strict on int→float (family C), precision (B), HLSL shims, etc. That work is common to both
    paths and is exactly what's blocking us now. There is no "desktop-GL via ANGLE, skip the
    rewrite" shortcut on iOS (ANGLE's iOS front-end is strict GLES).
  - ANGLE costs a large up-front build/vendor of libEGL/libGLESv2 for iOS + SDL EGL wiring +
    ~10-20 MB `.ipa` + another Metal-backend moving part.
  - **So:** finish the shader ES conversion (needed either way) on native ES 3.0; if the A2
    monolithic-link (varying-name) reconciliation proves too painful on-device, THEN adopt
    ANGLE/ES-3.1-SSO to make A2 disappear. Keep it as plan B for linking, don't pay its cost
    blind. (This also matches the project's long-stated "ANGLE-on-Metal default" intent — it
    just arrives later, when it actually earns its keep.)
- **Planned — FSR 1.0 upscaling (new Plan Phase 6).** Added 6.1 dynamic render-scale infra +
  6.2 AMD FidelityFX FSR 1.0 (EASU+RCAS) ported to GLSL ES 3.00 as the present pass. Spatial
  (no motion vectors) → fits the deferred GL renderer + mobile; render-scale + sharpness cvars;
  gate-validated. Target: reach a playable framerate on high-DPI iOS panels.

### 2026-07-14 — Phase 4 shader port: gate baseline + first fixes + the real roadmap

- **Gate is live and driving fixes.** After two gate bugs were fixed (see below) the
  honest baseline is **14/286** GL shaders compiling as GLSL ES 3.00. The first source fix
  (precision prelude, Slice 4.4a) took it to **25/286**. The brama→fix→measure loop runs in
  ~2 min on CI — no device round-trip.
- **Two gate bugs fixed first (they masked the real errors):**
  1. `first_error()` reported glslang's echoed temp filename, not the diagnostic → fixed +
     added a normalized error histogram (`--dump N` prints full glslang output).
  2. **Backslash includes:** `gl/common.h` does `#include "shared\common.h"`. On the Linux
     runner `os.path.join` left `shared\common.h` as one literal name → the type shims
     (`float4x4`→`mat4`…) never inlined → every `uniform float4x4 …` looked like two
     identifiers (280 bogus "unexpected IDENTIFIER"). Fixed by normalising `\`→`/` in the
     resolver. (Resolved locally on Windows, which hid it.) After the fix the histogram is real.
- **Slice 4.4a (done, 14→25):** ES has no implicit default precision, so ~84 shaders (mostly
  *vertex*, whose samplers had no precision) failed `type requires declaration of default
  precision qualifier`. Fixed in the source: a `#ifdef GL_ES` precision prelude at the top of
  `gl/common.h` (float/int + sampler2D/3D/Cube/2DShadow). **`GL_ES` is auto-predefined by
  glslang and the on-device ES driver for a `#version … es` unit** — so shader-side ES fixes
  need no engine define and are desktop-safe (compiled out). The gate's Python preamble now
  only adds the float/int floor (not samplers) so it faithfully requires the source to declare
  sampler precision.
- **The real remaining roadmap (261 fail), by family:**
  - **A — 103× `layout(location=…) in` "not supported in this stage: fragment"** (+ located
    `out` on 37 vertex iostructs). ES 3.00 disallows explicit locations on fs inputs / vs
    outputs. **Crux (Plan 4.5):** desktop matches vs↔fs varyings by *location* (they're a
    separable-program pair with DIFFERENT names — vs `v2p_*` out vs fs `p_*` in). ES 3.00
    monolithic programs match by **name**, so we can't just strip locations — the varying
    names must be reconciled across each vs/fs pair. This is the "very-high effort" core.
  - **C — 87× `wrong operand types` (int ⊗ float)**. ES 3.00 has no implicit int→float in
    `- * <` etc.; desktop does. Needs explicit `float()` casts in shims/shaders (e.g. an int
    `SMAP_size` minus a float). Spread across many shaders.
  - **D — 55× `vertex output block not supported`** = `out gl_PerVertex { vec4 gl_Position; };`
    in the `.vs`/`v_*.h`. Wrap in `#ifndef GL_ES` (ES declares `gl_Position` implicitly).
    Bulk, mechanical (~120 files carry the identical line).
  - Tail: 4× `gl_` reserved (redeclared `gl_FragCoord`), 5× undefined-macro-in-`#if`
    (FXAA_360/MSAA_SAMPLES/SSR_QUALITY — define to 0 or `#ifdef`), 1× `#unfdef` typo (real
    shader bug in `accum_volumetric_sun_normal .ps` — note the stray space in the name too).
- **Progress trajectory (gate, compile/286):** 14 → **25** (4.4a precision) → **27** (4.5a
  gl_PerVertex) → **114** (4.5b A1 varying-location strip). A1 was the dominant unblock (+87).
- **Slice 4.5a (D, done):** `out gl_PerVertex { vec4 gl_Position; };` in 41 `.vs`/`v_*.h`
  wrapped `#ifndef GL_ES` (ES declares `gl_Position` implicitly). Revealed the twin vertex-out
  location error, folded into A1.
- **Slice 4.5b (A1, done, 27→114):** ES 3.00 forbids `layout(location=)` on vs outputs / fs
  inputs. Added `VARYING(loc)` macro in `gl/common.h` (`layout(location=loc)` desktop / empty
  under GL_ES) and rewrote every vs→fs varying (`out` anywhere, `in` in fragment iostructs/
  `.ps`) as `VARYING(loc) out/in …`; vertex **attribute** inputs keep raw `layout(location=)`
  (ES allows those). 82 files, mechanical. **A2 (deferred, device-only):** the paired vs/fs
  varyings have DIFFERENT names (`v2p_*` out vs `p_*` in) — a monolithic ES program matches by
  name, so linking needs a name reconciliation the gate cannot check. Do it at renderer bring-up.
- **Now dominant — family C (140× wrong operand types, int ⊗ float).** ES 3.00 has no implicit
  int→float in `- * / <` etc.; desktop does. DIVERSE: integer literals (`0`,`1`,`2`) and
  int-typed values used in float/vector math across many shaders + shims. Highest leverage is
  the shared shims (`common.h` `clip(x): if(x<0)` → `<0.0`; `common_functions.h`; texture-size
  int math), then per-shader casts. Also a preprocessor knock-on: several `accum_*.ps` show
  `missing #endif` after the first operand error (glslang bails mid-`#if`) — verify it clears
  once the operand error is fixed.
- **Tail (unchanged, small):** 5× overloaded-fn no-match, 4× `gl_` reserved (`in vec4
  gl_FragCoord;` redecl → drop on ES), 5× undefined-macro-in-`#if` (FXAA_360/MSAA_SAMPLES/
  SSR_QUALITY → define 0 or `#ifdef`), 1× `#unfdef` typo (`accum_volumetric_sun_normal .ps`,
  note the stray space in the filename too).
- **Trajectory cont'd:** 114 → **147** (4.4b lmodel/clip float literals) → **148** (4.3a option-
  macro defaults SUN_QUALITY/SSR_QUALITY/MSAA_SAMPLES=0). Family E is essentially gone (only
  fxaa.ps's self-contained FXAA_360/FXAA_PS3 + one MSAA_SAMPLES left).
- **Current wall = family C (int/float), 103 first-errors.** The count is stuck ~148 because
  ~103 shaders first-fail on `wrong operand types` (int literal / int-typed value in float or
  vector math). It's DIVERSE (many shaders + shared includes). Cleared shared sources so far:
  lmodel.h `1 - …`, `shared/common.h` `clip(x) x<0`. Next shared source to pinpoint: a
  **`float * ivec2`** in the accum family (deep in shadow.h / the deferred path — needs
  aligning glslang's assembled line to source; do it with `--dump` after the next edit so the
  line numbers match the current tree). Then a long tail of per-shader literal casts.
- **State at handoff:** gate at **148/286**, all commits green, HEAD `5ab28aad5`. Remaining:
  finish family C (shared sources first, then per-shader casts), the small tail (fxaa.ps FXAA_*
  defaults, `in vec4 gl_FragCoord;` redecl → drop on ES, `#unfdef` typo in
  `accum_volumetric_sun_normal .ps`), and A2 (varying-name reconciliation — device/link only).
- **Strategic fork raised with the user:** keep grinding C to green, OR pivot now to the engine
  change **Plan 4.3** (emit `#version 300 es` on iOS in `rgl_shaders.cpp:227` + define nothing
  extra since GL_ES is auto-predefined) to get a DEVICE build where the ~148 compiling shaders
  actually load and we hit the real monolithic-link (A2) behaviour — likely higher-value than
  the last ~90 casts.

### 2026-07-14 — Phase 4 tooling: offline GLSL ES 3.00 shader gate in CI

- **Why:** the shader/program port (Plan 4.3–4.5) means grinding ~286 GL shaders from
  desktop GLSL 4.10 to ES 3.00. Each error only shows at runtime on-device today = one
  SideStore round-trip per shader. Unworkable. Decision (user): go native GLES 3.0 **and
  build a CI shader-compile gate first** so errors surface in seconds on Linux.
- **Feasible because the shaders are in the repo:** `res/gamedata/shaders/gl/` — 199 `.ps`,
  87 `.vs`, plus `shared/` (5 shims incl. `common.h`) and `iostructs/` (81 interface headers).
  No `.gs` (ES 3.0 has no geometry shaders); `.s` are blender scripts, not GLSL.
- **How the engine assembles a shader** (mirrored by the gate): version line +
  `#define`s + **recursive `#include` inlining** ([rgl_shaders.cpp:141](../src/Layers/xrRenderPC_GL/rgl_shaders.cpp)
  `load_includes`, resolved against `getShaderPath()`=`"gl\\"`). `common.h` is an HLSL→GLSL
  shim (`float4`→`vec4`, `mul()` overloads, `tex2D`→`texture`, `POSITION`=3, …).
- **The gate** (`misc/ios/shadercheck/glsl_es_check.py` + CI job `shader-check`): walks every
  `.vs`/`.ps`, inlines includes (searching `gl/`,`gl/shared/`,`gl/iostructs/`), prepends
  `#version 300 es` + ES default precision + a representative `#define` set, and runs
  `glslangValidator`. Reports `passed/total` + the first error per failing shader. Runs on
  `ubuntu-latest` (`apt install glslang-tools`), **non-strict = always green** for now
  (informational); flip `--strict` to make it a hard gate once the tree compiles.
- **Deliberately approximate:** the engine's real define set is cap/settings-driven, so the
  gate compiles each shader's *default* (`#ifdef`-off) path with a fixed define set. It
  catches structural ES breakage (version, `gl_PerVertex` redecl, missing precision, shim
  validity, sampler/layout issues) — not runtime correctness. First CI run gives the
  **baseline failure count** that quantifies Plan 4.3–4.5. Then: port `common.h` (4.4) and
  the shader front-end to `#version 300 es` (4.3) and watch the number fall.

### 2026-07-14 — Phase 4 Slice 4.6a: guard glBindFragDataLocation on GLES (+ result of 4.1)

- **On-device result of Slice 4.1 + the version fix (build `1.6.02.10018035`):** both
  landed. The version bumped (SideStore offered the update) and the GLES 3.0 context works
  — the launch SIGKILL in `xrRender_test_hw` is **gone**. The engine now boots *far* deeper:
  `CRenderDevice::Create` → `CRender::create` → `CRenderTarget()` → shader/pass build
  (`CBlender_Compile::r_Pass` → `CResourceManager::_LinkPP`).
- **New crash (`xr_3da-2026-07-14-1712*.ips`):** same shape (`EXC_BAD_ACCESS`, `pc=0`,
  NULL func-ptr) but a new, deeper frame — register `x28 = glad_glBindFragDataLocation`.
  `_LinkPP` correctly takes the **monolithic** program path on ES (it's gated:
  `if (GLAD_GL_ARB_separate_shader_objects) …pipeline… else …GLLinkMonolithicProgram…`,
  and the ARB SSO extension is absent on ES), but `GLLinkMonolithicProgram`
  ([ShaderResourceTraits.h:119](../src/Layers/xrRender/ShaderResourceTraits.h)) calls
  **`glBindFragDataLocation`** unconditionally — desktop-GL only, null under ES → crash.
- **Fix (Slice 4.6a):** wrap the `glBindFragDataLocation` clusters in
  `if (glBindFragDataLocation)` (idiomatic here — mirrors the existing `if (glObjectLabel)`),
  in both `GLLinkMonolithicProgram` (the ES path) and `GLUseBinary`. On ES the pointer is
  null → skipped; fragment outputs bind via `layout(location=N) out` in the shader source
  (that's the shader-side work, Plan 4.5). Desktop path unchanged. No-regret under either
  renderer backend (native ES *or* ANGLE — ES semantics have no glBindFragDataLocation).
- **What this unblocks / the REAL next seam:** this stops the null-call SIGKILL and lets the
  engine's own shader path run — but the shaders are still emitted as **`#version 410`**
  ([rgl_shaders.cpp:227](../src/Layers/xrRenderPC_GL/rgl_shaders.cpp)), which a GLES 3.0
  context rejects. So the substantive blocker is now the **GLSL source port to ES 3.00**
  (Plan 4.3 `#version 300 es` + precision, 4.4 the common.h/common_samplers.h shims, 4.5 the
  ~81 iostructs headers → `layout(location)` outputs, drop `gl_PerVertex`). That's the
  interdependent core of Phase 4; direction (native ES 3.0 vs ANGLE) + tooling (a CI
  offline GLSL-ES compile gate to avoid a device round-trip per shader) decided next.

### 2026-07-13 — Fix: SideStore never shows an update (per-day, not per-build, version)

- **Symptom (user):** after pushing a new build, SideStore offered no update; removing +
  re-adding the source is not an acceptable workaround.
- **Root cause:** SideStore decides "is there an update?" from `CFBundleShortVersionString`
  (the source's `version` is read straight from the .ipa). Ours was
  `…​.${XRAY_BUILD_ID}`, and `XRAY_BUILD_ID` ([cmake/utils.cmake:48](../cmake/utils.cmake)
  `calculate_xray_build_id`) is **date-derived** — `(year-1999)*365 + day-of-year` — so it
  only changes **once per calendar day**. Two builds the same day both produced
  `1.6.02.10018` (confirmed: both crash reports were `1.6.02.10018`), so SideStore saw the
  same version and showed nothing.
- **Fix:** fold CI's monotonic run number into the version. CI passes
  `-DXR_IOS_BUILD_NUMBER=${{ github.run_number }}` ([.github/workflows/ios.yml](../.github/workflows/ios.yml)
  configure step) and [src/xr_3da/CMakeLists.txt](../src/xr_3da/CMakeLists.txt) computes
  `XR_IOS_VERSION_CODE = XRAY_BUILD_ID * 1000 + run_number` for both
  `CFBundleShortVersionString` (`1.6.02.<code>`) and `CFBundleVersion`. This matches the
  proven scheme from the user's OpenGothic-iOS project (`1.0.<github.run_number>`).
- **Why the formula:** `run_number` strictly increases every run and `XRAY_BUILD_ID` is
  non-decreasing, so the sum strictly increases **per build** (not per day). The `*1000`
  base keeps every new code far above any date-only build already installed
  (`10018` → `10018034`), so the update always reads as *newer* — no downgrade/no-op.
  Local dev builds (no `XR_IOS_BUILD_NUMBER`) fall back to the raw build id.
- **No release-job change:** it already reads `CFBundleShortVersionString` from the .ipa
  into `apps.json` `versions[].version`, so source and bundle stay in lockstep (avoids the
  earlier "expected X, found Y" mismatch). Docs-only + CI-config; verified on next CI run.

### 2026-07-13 — Phase 4 Slice 4.1: GLES 3.0 context request on iOS (renderer seam)

- **Trigger (device crash `xr_3da-2026-07-13-2307/2311.ips`):** the app boots all the
  way through `CApplication` → `CEngine::Initialize` → `CEngineAPI::CreateRendererList`
  → `RGLRendererModule::ObtainSupportedModes` → **`xrRender_test_hw()`** and then dies
  with `EXC_BAD_ACCESS (SIGKILL)`, `KERN_PROTECTION_FAILURE at 0x0`, **pc = 0** —
  a call through a NULL function pointer (Instruction Abort / Translation fault). This
  is the silent, no-dialog crash we'd been chasing; it's the Phase-4 renderer seam,
  confirmed to the line.
- **Root cause:** `xrRender_test_hw()` ([r2_test_hw.cpp:42](../src/Layers/xrRenderPC_GL/r2_test_hw.cpp))
  spins up a hidden test window and calls `CHW::CreateDevice`. `SetPrimaryAttributes`
  ([glHW.cpp:179](../src/Layers/xrRenderGL/glHW.cpp)) asked SDL for a **desktop GL 4.1
  CORE** context, which iOS cannot provide — SDL's UIKit/EAGL backend hands back an ES
  context instead. `CreateDevice` then called **`gladLoadGL`** (the *desktop* glad
  table), which mismatches the ES context, so the first desktop-only `glXXX` entry it
  invokes is NULL → jump to 0 → SIGKILL. (Not jetsam: the adjacent `JetsamEvent*.ips`
  is unrelated OS memory-pressure noise.)
- **Fix (3 iOS-guarded hunks in `glHW.cpp`, all `#if defined(XR_PLATFORM_APPLE_IOS)`):**
  1. `SetPrimaryAttributes`: request `SDL_GL_CONTEXT_PROFILE_ES` + MAJOR=3/MINOR=0
     instead of `PROFILE_CORE`.
  2. Skip the later desktop `MAJOR=4/MINOR=1` override block on iOS (it would clobber
     the ES version set above).
  3. `CreateDevice`: load `gladLoadGLES2(...)` instead of `gladLoadGL(...)`.
- **Why this is enough (and why no stub is needed):** the vendored glad
  ([sdk/include/glad/gl.h](../sdk/include/glad/gl.h)) is a **merged** loader generated
  with `gl:compatibility=4.6, gles1, gles2=3.2` — the *same* header/`gl.c` already
  defines **`gladLoadGLES2`** (`gl.c:13277`) sharing one function-pointer table. So the
  ES loader was already available; we just weren't calling it. This is exactly what
  Plan tasks 3.11 / 4.2 specified, done the clean way (real ES pointers, not a stub).
- **Expected on-device result:** `test_hw` now creates a genuine ES 3.0 context and
  populates the GLES function table (`glGetIntegerv`/`glGetString`/`glGenFramebuffers`
  all exist in ES 3.0), so the null-call SIGKILL is gone. Boot proceeds into real
  renderer creation; the **next** seam is GLSL — the ~286 `#version 410` desktop shaders
  won't compile under ES 3.0 (Slice 4.3+). Worst case the context still can't be made,
  but then `CreateDevice` logs + returns gracefully → readable FATAL, not a silent kill.
- **CI risk:** none for the build — both `gladLoadGL` and `gladLoadGLES2` are defined in
  `gl.c`, changes are iOS-only, desktop path byte-for-byte unchanged. CI validates
  compile+link on macOS; on-device behaviour is verified via SideStore + crash reports.

### 2026-07-13 — Phase 3 (in progress): boot + on-device test loop
Commits: SDL_main (3.1), Info.plist/.ipa/release/SideStore source (3.9/3.10), os_log,
Stalker app icon, SideStore v2-format + version-match fixes, fsgame.ltx seeding (3.7a).

- **3.1 ✅ SDL_main entry:** on iOS `entry_point.cpp` includes `<SDL_main.h>` (main → SDL_main)
  and xr_3da links `SDL2::SDL2main` so UIApplicationMain drives the app. App launches.
- **3.9/3.10 ✅ installable .ipa + distribution:** custom iOS `Info.plist`
  (`misc/ios/Info.plist.in`: bundle id, MinimumOSVersion, UIDeviceFamily,
  CFBundleSupportedPlatforms, UIFileSharingEnabled); CI packages the device app as an
  unsigned `.ipa` and a `release` job publishes it to a rolling `ios-dev` pre-release +
  a **SideStore/AltStore source** (`apps.json`). App icon from the user's Stalker CoP `.ico`
  (Pillow → opaque PNGs → `Assets.xcassets`, actool).
- **On-device log capture:** engine `OutputDebugString` also emits `os_log` on iOS. NOTE:
  the old `idevicesyslog` (libimobiledevice r1122) only relays legacy syslog and does NOT
  carry app os_log / launch / crash on iOS 15+ — a captured log was 40 s of brightness-daemon
  noise. **Working loop instead: the engine shows FATAL errors as on-screen dialogs**, so the
  user reads the message and reports it — no USB log needed. (`idevicecrashreport` is only for
  hard crashes with no dialog; pymobiledevice3 is the future upgrade for unified-log capture.)
- **SideStore source gotchas (fixed):** (1) legacy flat format → rewrote to v2 (`versions[]` +
  `minOSVersion`); (2) install refused "expected X, found Y" → the release job now reads
  `CFBundleShortVersionString` straight from the built .ipa for the source version, and the
  .ipa version appends `XRAY_BUILD_ID` so it increments per build (update detection).
- **3.7a ✅ (first FS error) fsgame.ltx:** app died at FS init — on iOS the engine tried to
  symlink fsgame.ltx from a non-existent install dir. Fix: `LocatorAPI.cpp` iOS branch copies
  the bundled `fsgame.ltx` + base gamedata from the read-only app bundle (`SDL_GetBasePath`)
  into writable Documents (`SDL_GetPrefPath`) on first launch, then runs from there
  (minimal POSIX recursive copy). xr_3da POST_BUILD bundles `res/fsgame.ltx` + `res/gamedata`
  (754 files) into the `.app`. Verified present in the .ipa at `Payload/xr_3da.app/`.
- **3.7b ✅ run from `$HOME/Documents` (not the bundle):** the first fsgame fix still ran
  from the read-only bundle — on iOS the initial CWD IS the `.app`, and since we bundle
  fsgame.ltx there, `access(FSLTX)` succeeded and it never took the Documents branch (error
  path was `.../App.app/gamedata/configs/system.ltx`). Also `SDL_GetPrefPath` is
  `Library/Application Support` (hidden from Files). Fix: make the iOS FS path unconditional
  and use `$HOME/Documents` (writable AND Files-app-visible via UIFileSharingEnabled). Verified
  on device: Documents now holds engine-seeded `_appdata_` (writes work), `fsgame.ltx`,
  `gamedata`, plus the user-copied CoP `resources`/`localization`/`patches`.
- **CoP data provisioning (done by user):** retail CoP is packed `.db` archives; user copied
  `resources`/`localization`/`patches` into `On My iPhone → OpenXRay` via File Sharing; the
  bundled fsgame.ltx `$arch_dir_*$` mount them. Engine got PAST system.ltx.
- **Current blocker:** hard crash on launch with **no dialog** (app vanishes) after data is in
  place — most likely desktop-GL context creation (`xrRender_GL`/glad, no GLES) = Phase 4 seam.
  Next: `idevicecrashreport -e -k C:\openxray\ios-crashes` → read `xr_3da-*.ips` backtrace.
- **CI infra note:** one build "failed" only on the `Upload .ipa artifact` step (transient
  GitHub ArtifactService timeout, 5 retries) — compile/link/package were green; `gh run rerun
  --failed` fixed it. Not a code issue.
- **Remaining Phase 3:** confirm crash cause, then per-frame tick (3.2), lifecycle (3.4),
  fullscreen window (3.5), GLES context for a cleared frame (3.11) — most of this overlaps the
  Phase 4 renderer.

### 2026-07-13 — Phase 2e: link the whole engine into one iOS Mach-O ✅ GREEN 🎉
Commits: `1b5426b05` (link attempt + signing off), `5cd7aa853`→`1b5426b05` (JPEG),
`444da52e5` (GameSpy). Green run: `29275432825` (device + sim). Output:
`bin/aarch64/Release/xr_3da.app/xr_3da` — **Mach-O 64-bit executable arm64, zero
unresolved symbols**. **Phase 2 COMPLETE.**

- **Done:** the entire engine (deps + all Externals + all engine libs + xrGame +
  xrGameSpy) links into one static iOS Mach-O for both SDKs. CI `engine-build` now:
  configure → Externals → support libs → core libs → **Link xr_3da**. Code signing
  disabled at configure (`CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED/REQUIRED=NO`);
  Windows resources gated off the iOS xr_3da target. `entry_point.cpp`'s `main()` is
  the entry symbol (SDL_main/UIKit lifecycle is Phase 3; runtime GLES is Phase 4).
- **Empirical link — the linker was the map.** Only two blockers surfaced, both flagged
  as watch-items:
  - **JPEG:** `find_package(JPEG)` matched the runner's host homebrew `libjpeg.dylib`
    (macOS) → `ld: building for 'iOS', but linking in dylib built for 'macOS'`. Fix
    (`1b5426b05`): skip `find_package(JPEG)` on iOS → `JPEG_FOUND` false → xrCore drops
    `JPEG::JPEG` and `ImageJPEG.cpp`'s `__has_include(<jpeglib.h>)` takes the no-JPEG path.
  - **GameSpy:** undefined `CGameSpy_*`/`gamespy_gp::*`/`xrGameSpyServer` referenced from
    xrGame (they live in the `xrGameSpy` module we'd gated off). Decision: **build GameSpy
    + xrGameSpy for iOS** (un-gate the three spots) rather than excise interwoven MP code —
    GameSpy builds on every desktop platform and its OpenXRay-fork POSIX support reached
    iOS **with no compile fixes**. MP is non-functional at runtime (dead servers) but the
    binary links (`444da52e5`).
  - Everything else — SDL2/OpenAL/ogg/vorbis/theora/lzo, all engine libs, the glad GL
    renderer (function-pointer globals, null until Phase 4), pthread/dl (libSystem) —
    resolved with **no** changes. OpenAL used the iOS SDK `OpenAL.framework` and linked fine.
- **Next: Phase 3** — boot to a window with the iOS app lifecycle (SDL_main/UIKit,
  per-frame tick, sandbox paths, real MACOSX_BUNDLE app target with Info.plist + gamedata).

### 2026-07-13 — Phase 2d Slice 4: compile the whole engine ✅ GREEN
Commits: `c1009a75b` (4a support libs), `cf3aeac71` (4b big three),
`58ffcd924` (xr_string varargs), `c39fb9536` (system() guards).
Green run: `29270095192` (device + sim).

- **Done:** every engine module compiles arm64 for both iOS SDKs — **no `xr_3da` link
  yet** (Phase 2e). The CI `engine-build` job now builds Externals → 12 support libs
  (Slice 4a) → the big three `xrEngine`/`xrRender_GL`/`xrGame` (Slice 4b), each target
  built in a continue-past-failure loop so one round surfaces every broken module.
- **Slice 4a:** all 12 support libs (xrMiscMath, xrCore, xrAICore, xrCDB, xrAPI,
  xrUICore, xrMaterialSystem, xrParticles, xrNetServer, xrPhysics, xrSound,
  xrScriptEngine) compiled **clean, first try** — the engine core is portable as-is.
- **Slice 4b:** `xrEngine` and `xrRender_GL` compiled clean (the GL renderer's glad
  function-pointers compile fine; runtime GLES is Phase 4). Only `xrGame` needed fixes,
  both hidden on desktop:
  - `xml_str_id_loader.h:173` passed an `xr_string` through variadic `Msg()` for `%s`
    → Apple clang hard error `-Wnon-pod-varargs` (desktop skips it under
    `#ifndef MASTER_GOLD`). Fix: `.c_str()` (`58ffcd924`).
  - `login_manager.cpp` / `MainMenu.cpp` used `system("xdg-open …")` to open a URL
    (GameSpy recovery / MP map download) — `system()` is unavailable on iOS. Fix: an
    `XR_PLATFORM_APPLE_IOS` no-op branch; URL opening moves to UIApplication in Phase 3
    (`c39fb9536`).
- **Next: Phase 2e** — link the whole engine into one static iOS Mach-O (`xr_3da`), zero
  unresolved symbols. Watch-items: the OpenAL framework-vs-static-lib choice, and the
  renderer's desktop-GL symbols (`xrRender_GL` compiled but its GL entry points are
  unresolved until the Phase 4 GLES/ANGLE port or a link-time stub).

### 2026-07-13 — Phase 2d Slice 3: compile the Externals static libs ✅ GREEN
Commits: `24aa962b4` (compile job + ImGui ES3), `0d523bf12` (host-tool generator),
`55bb87f4c` (lj_vm.S ASM). Green run: `29267435651` (device + sim).

- **Done:** all six engine-side Externals compile + archive as arm64 for both iOS SDKs:
  `xrLuaJIT`, `xrLuabind`, `xrLuaFix`, `xrOPCODE`, `xrODE`, `xrImGui`. The CI job
  (`engine-configure` → renamed `engine-build`) now configures then compiles them and
  verifies each `.a` is arm64. **This is the first LuaJIT build under the Xcode generator**
  (prior validation used Makefiles in `luajit-check`) — two Xcode-specific issues fell out,
  both the same gotcha class:
- **Error 1:** `.../HostBuildTools/minilua/minilua: No such file or directory` (the
  `buildvm_arch` step couldn't find `minilua`).
  - **Root cause:** the nested host-tool builds used `-G${CMAKE_GENERATOR}`; under the
    outer Xcode (multi-config) generator the binary nests in a per-config subdir
    (`Release/minilua`), but the consuming custom command expects the fixed path
    `${MINILUA_BINARY_DIR}/minilua`.
  - **Fix (`0d523bf12`):** host codegen tools are native macOS binaries that need no
    Xcode features → build them with a single-config generator (`Unix Makefiles`,
    `HOST_TOOL_GENERATOR`) on iOS for a predictable output path. Desktop unchanged.
- **Error 2:** `lj_vm.S:1:2: error: cannot use dot operator on a type`.
  - **Root cause:** LuaJIT-proj marks the generated VM asm `LANGUAGE CXX`
    (`LuaJIT-proj/CMakeLists.txt:478`). Under Makefiles the `.S` extension still makes
    clang assemble it, but the Xcode generator honours `LANGUAGE` literally and parses
    it as C++ — the leading `.` of the first Mach-O directive trips the C++ frontend.
  - **Fix (`55bb87f4c`):** on iOS set the source `LANGUAGE ASM` (enabled via
    `project(xrLuaJIT C CXX ASM)`); keep `CXX` for desktop. `machasm` (Mach-O) mode is
    already selected for Apple, so the asm was correct — only the compile language was wrong.
- **ImGui:** added `IMGUI_IMPL_OPENGL_ES3` on iOS (`Externals/imgui-proj/CMakeLists.txt`)
  so the unconditionally-compiled `imgui_impl_opengl3.cpp` includes `<OpenGLES/ES3/gl.h>`
  from the iOS SDK rather than a desktop-GL loader.
- **Next:** Slice 4 — compile the engine static libs (xrCore … xrRender_GL, xrGame),
  no `xr_3da` link (Phase 2e). `xrGame` is the likeliest to surface source-level
  GameSpy/multiplayer references now that the lib is gated off.

### 2026-07-13 — Phase 2d Slice 1: whole-engine iOS configure ✅ GREEN
Commits: `0c55559bf` (iOS branch + configure job), `a27bf6d39` (TESTARCH fix),
`82b76ecfa` (CMAKE_DEFAULT_BUILD_TYPE fix). Green run: `29265825199` (device + sim).

- **Done:** the *entire* engine configures **and generates** under the leetal iOS
  toolchain — static, LuaJIT interpreter mode, finding the pre-built deps prefix.
  Added an `engine-configure` CI job (`needs: deps`, `-G Xcode`, configure-only,
  device + sim). Config log confirms `BUILD_SHARED_LIBS: OFF`, `Using standard
  memory allocator`, and Ogg/Vorbis/Theora/LZO resolving from our prefix.
- **How:** one guard var `XRAY_PLATFORM_IOS`, defined once in `cmake/XRay.Build.cmake`
  (post-`project()`, before `add_subdirectory(Externals)`/`src`, so it propagates
  everywhere). It force-sets `BUILD_SHARED_LIBS=OFF` and `LUAJIT_DISABLE_JIT=ON`.
  Gated off on iOS: `GameSpy` (3 coordinated spots — `Externals/CMakeLists.txt`,
  `src/CMakeLists.txt`, `xrGame`'s link list via a generator expression),
  `find_package(mimalloc)` (→ `MEMORY_ALLOCATOR=standard`), `include(XRay.Packaging)`
  (DEB/RPM+CPack), and the `xr_3da` RUNTIME `install()`.
- **Error 1 (configure):** LuaJIT `TESTARCH`: `lj_arch.h:86:10: fatal error:
  'TargetConditionals.h' file not found` on both device+sim.
  - **Root cause:** the toolchain bakes `-target`/`-isysroot` into `CMAKE_C_FLAGS`
    *only for non-Xcode generators* (toolchain:955-959). `Externals/LuaJIT-proj`
    runs `execute_process(clang ${CMAKE_C_FLAGS} -E lj_arch.h -dM)` to probe the arch,
    so under `-G Xcode` it had no sysroot. The standalone `luajit-check` job passes
    only because it uses the default (Makefiles) generator.
  - **Fix (`a27bf6d39`):** on iOS, pass `--target=${CMAKE_C_COMPILER_TARGET}` and
    `-isysroot ${CMAKE_OSX_SYSROOT}` to the TESTARCH preprocess explicitly
    (generator-independent; redundant-but-harmless under Makefiles). This is the
    **Xcode-generator-vs-`execute_process`** gotcha class.
- **Error 2 (generate):** `Generator Xcode does not support variable
  CMAKE_DEFAULT_BUILD_TYPE but it has been specified.`
  - **Root cause:** `cmake/XRay.Configurations.cmake:14` sets `CMAKE_DEFAULT_BUILD_TYPE`
    for *any* multi-config generator, but only Ninja Multi-Config supports it; the
    Xcode multi-config generator errors. (This file runs in `PreProjectInit`, before
    `project()`, so `XRAY_PLATFORM_IOS` isn't defined yet — must gate on the generator.)
  - **Fix (`82b76ecfa`):** guard the `set()` with `NOT CMAKE_GENERATOR STREQUAL "Xcode"`.
    Desktop VS / Ninja-MC unaffected.
- **Watch-item for Phase 2e (link):** `find_package(OpenAL)` resolved to the iOS SDK
  `OpenAL.framework`, *not* our static `libopenal.a` in the prefix (FindOpenAL +
  `CMAKE_FIND_FRAMEWORK=FIRST`). Harmless at configure, but the deprecated Apple
  framework lacks openal-soft extensions the engine may use — may need to force
  `OPENALDIR`/prefer the prefix's static lib when we actually link.
- **Also benign noise:** LuaJIT's `execute_process(make clean)` (LuaJIT-proj:86) prints
  `Makefile:324: *** missing: export MACOSX_DEPLOYMENT_TARGET`. Its result isn't
  checked, so it's non-fatal (same as in the green `luajit-check` job).
- **Next:** Slice 3 (compile Externals static libs; add `IMGUI_IMPL_OPENGL_ES3`),
  Slice 4 (compile engine static libs). `xr_3da` link stays for Phase 2e.

### 2026-07-13 — Phase 2 deps: 7 libraries cross-build static
Commits: `b1d4e9206`, `122c5ba2a`, `0ac1cdb48`, `839c73d7a`, `69fa964cd`.

- **Done:** SDL2 2.32.10, OpenAL-soft 1.25.2, libogg 1.3.6, libvorbis 1.3.7,
  libtheora 1.1.1, lzo 2.10, and LuaJIT all cross-build **static** for both iOS SDKs
  (device OS64 + simulator SIMULATORARM64), verified arm64.
- **How:** a CMake ExternalProject superbuild at `cmake/ios/deps` (SDL2/OpenAL/
  ogg/vorbis/lzo); theora via an injected CMake build at `cmake/ios/theora` (upstream
  is autotools-only); LuaJIT via iOS-guarded fixes in `Externals/LuaJIT-proj` +
  a standalone `cmake/ios/luajit-check`.
- **Errors & fixes:**
  - CMake 4.x rejected `cmake_minimum_required(<3.5)` (vorbis/lzo) →
    `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` in the superbuild's common args.
  - OpenAL: Xcode 26.5 `-Werror=function-effects` in `coreaudio.cpp` →
    pre-seed `-DHAVE_WFUNCTION_EFFECTS=OFF` (skips the probe that adds the flag).
  - LuaJIT host tools were built as iOS binaries (Abort trap 6 when run) → build
    `minilua`/`buildvm` native (`--target=<host>-apple-macos`, `CMAKE_OSX_SYSROOT=macosx`);
    the working `-target` form is the single-token `--target=…`.
  - LuaJIT `use of undeclared identifier 'lj_cf_jit_util_trace*'` → `LUAJIT_DISABLE_JIT=ON`
    (interpreter mode — the intended iOS baseline; aligns host tools + target sources).

### 2026-07-13 — Phase 1: toolchain, smoke test, `.ipa`
- **Done:** vendored `cmake/toolchains/ios.toolchain.cmake` (leetal); an engine-free
  smoke test (`misc/ios/smoketest`) builds for simulator + device on macOS CI and
  produces an unsigned, SideStore-ready `.ipa`; added the `XR_PLATFORM_APPLE_IOS`
  macro (`src/Common/Platform.hpp`); restricted CI so only the `iOS` workflow runs on
  `ios-port` (desktop matrix + StyleCheck disabled for the branch).

### 2026-07-19 — Slice 6.15: documentation becomes an operating system instead of an archive

The implementation had outrun its specification by several phases. The overview
still described Windows-only authorship, SideStore-only installs, an absent
renderer, incomplete shader gates, and ANGLE as the active default. The resume
guide contained mutually exclusive signing conclusions. The exhaustive plan was
useful archaeology but no longer usable as a prioritized backlog.

This slice established one source-of-truth hierarchy:

- `iOS-Port.md` now holds the current product/architecture contract and measurable
  definition of done;
- `iOS-Port-Plan.md` is a short active roadmap organized by P0/P1/P2;
- `iOS-Port-Resume.md` is a first-command handoff for the current Mac/device loop;
- this journal remains append-only; current-fact corrections are recorded as
  later entries;
- the dated 2026-07-17 audit is explicitly a superseded snapshot;
- the native Metal plan is explicitly deferred behind streaming, memory,
  lifecycle, and performance gates;
- the controller prior-art note reflects the already-working hardware controller
  and menu-touch baseline;
- root `AGENTS.md` and `.Codex/session-log.md` define ownership, evidence, safety,
  and validation rules;
- the main README and SideStore release description no longer claim that the
  renderer is absent or that launch is expected to crash.

No runtime behavior changed. The next code slice remains IOS-P0-001: correlate the
first complete far-field frame with distance, sector/portal state, and named
resource-streaming events.

### 2026-07-19 — Slice 6.16: resolution/settings matrix narrows P0 to visibility and closes two test-loop defects

This slice tested the user's hypothesis that launch graphics settings might
cause the white/black world. It also traced the level-loading path before
changing behavior.

#### Static level data is not waiting for gameplay prefetch

`xrRender_R2/r2_loader.cpp::level_Load` synchronously completes level buffers,
visuals and sectors; `xrEngine/IGame_Level.cpp` calls that load before gameplay.
The iOS-disabled `IGame_Persistent::Prefetch()` covers gameplay objects/models
and deferred texture upload, not the static level visuals already loaded by
`level_Load`.

Therefore the old “skipped full prefetch causes missing static terrain”
explanation is withdrawn as a leading cause. The strongest remaining paths are
sector/portal traversal, frustum/HOM/SSA rejection, and construction of the
static draw graph.

#### Controlled settings matrix

Every run used the same save, unattended autoload, the same current `.app` and a
frame read from the engine-owned presentation target:

| Variant | Measured result |
|---|---|
| `r__geometry_lod 0.75` vs `2.0` | Captures were byte-identical; no effect |
| 932×430 vs 1864×860 internal rendering | P0 artefact unchanged; both ran near 60 FPS |
| VSync off vs on | P0 artefact and observed frame cadence unchanged |
| Postprocess/reflection group off | No fix after its shader permutation was repaired |
| `r3_gbuffer_opt off` | No fix; optimized packing/decoding is not the cause |
| `rs_vis_distance 1.0` vs `1.5` | No fix |
| `texture_lod 0`, aniso 15 vs `texture_lod 2`, aniso 4 | No fix; texture allocation fell from about 556 MB to 186 MB |

The 2× renderer uses `r__supersample` as an iOS internal scale while the
SDL/UIKit window and input stay at logical 932×430. `CHW::GetSurfaceSize()` and
`CRenderDevice::SelectResolution()` both preserve that distinction. The device
rendered 1864×860 and presented it to the 2796×1290 drawable. Physical footprint
rose from 3,176,307 K at 1× to 3,228,099 K at 2× (about 52 MB). The HUD/fonts are
currently too small at 2×, so the device was restored to the 1× Extreme baseline.
Presentation filtering and independent UI scaling remain P1 work.

#### Low-preset runtime shader defect found and fixed

Turning MSAA off selected `combine_1_nomsaa.ps` with `SSAO_QUALITY=1`. That
permutation failed on the ES driver because `gl/ssao.ps` evaluated
`(occ + 0.3) / (1 + 0.3)`: GLSL ES does not permit `int + float`. The failed
final-combine shader left only sky and HUD, which initially looked like a strong
settings clue.

Changing the denominator to `1.0 + 0.3` restored the world. The offline gate had
only modeled `SSAO_QUALITY=3`, so `glsl_es_check.py` now also compiles the two
SSAO entry shaders under a 2/2 low-settings profile. The full gate is green:
279/279 baseline compile, 2/2 low-settings compile, and 137/137 stage pairs.

#### Test-loop integrity defect found and fixed

`install_device.sh` with no explicit path selected the newest archived IPA even
when `bin/aarch64/Release/xr_3da.app` had just been rebuilt. This silently
installed an older renderer and produced a convincing false 932×430 result.
The installer now prefers the current build `.app` and uses the archived IPA
only as a fallback.

**End state:** device restored to 932×430, VSync off, Extreme/full textures,
G-buffer optimization on, MSAA 2×, autoload present and keypress gate disabled.
No P0 fix is claimed. Next slice instruments visibility decisions rather than
patching streaming or shaders.

### 2026-07-19 — Slice 6.17: startup world fixed — vertical sector detection missed the spawn floor

This slice continued the settings investigation with sector/portal and static
draw-graph counters. It closes the misleading “movement triggers streaming”
hypothesis.

#### What the missing log proved

The broken frame continued updating weather, HUD and weapon state, but produced
no main-pass visibility record. `R_dsgraph_structure::build_subspace()` returns
before traversal when the camera sector is `INVALID_SECTOR_ID`; that early return
was before the temporary log. The world was not waiting for static buffers or
textures: the renderer had no sector from which to build the static scene.

At the affected save:

```text
camera=(256.24 21.47 550.82)
exact down/up sector query: INVALID
nearest successful probe=(256.24 21.47 558.82)
probe radius=8.0
sector=115
```

Once the earlier walking test reached geometry under a vertical ray,
`last_sector_id` became 115. Walking back did not reproduce the defect because
the renderer deliberately keeps the last valid sector when a later exact query
fails. That persistence created the false appearance of asynchronous geometry
arrival. No portal transition occurred, and static level data had already loaded
synchronously.

#### Fix

`r2_R_calculate.cpp` now:

1. compares `vCameraPositionSaved` with `vCameraPosition` (the old expression
   accidentally compared saved direction with current position);
2. uses the ordinary exact vertical query first;
3. on iOS only, probes nearby floor positions at increasing radii when that exact
   query is invalid;
4. retains the first valid nearby sector and logs the exceptional fallback at
   most once per second.

Normal direct detection, all non-iOS platforms, and the portal traversal contract
remain unchanged. `CSector::r_marker`, `CPortal::marker`,
`CPortal::bDualRender`, and portal pointers are also initialized explicitly; the
instrumentation exposed one transient uninitialized-marker frame, although that
was not the persistent startup root cause.

#### Device evidence

- First patched cold launch, no input: fallback found sector 115 at radius 8 m;
  the stable main pass traversed one outdoor sector and accepted 543 static
  visuals. Full terrain and vegetation were visible immediately.
- Second cold launch, no input: the fallback ran exactly once, sector 115 was
  active by frame 34, and the same stable 543-visual draw graph followed.
- Third cold launch, no input, after removing temporary visibility counters:
  full world remained correct and the fallback again logged exactly once.
- The full pre-install gate passed: 279/279 shader compile, 2/2 low-settings
  compile, 137/137 shader links and arm64 engine build.
- Temporary portal/frustum/HOM/SSA counters were removed after diagnosis.

The prior settings matrix remains useful negative evidence: VSync, 1×/2×
resolution, geometry LOD, visibility distance, G-buffer packing, texture
quality/aniso, MSAA, SSAO, sun shafts, volumetrics, wet surfaces and reflections
did not cause this defect. The 1× Extreme profile remains the device baseline;
2× is functional but requires independent HUD/font scaling.

**Remaining P0 validation:** one additional outdoor save, one indoor/portal
location, save/reload and level transition. This entry supersedes slice 6.16's
visibility/streaming next step and slice 6.14's conclusion that movement caused
far-field data to arrive.

### 2026-07-19 — Slice 6.18: global darkness fixed — SSAO sampled an ungenerated half-depth buffer

This slice corrects the remaining lighting conclusion after slice 6.17 restored
static geometry. The world was complete but nearly black except for a small
correctly lit area near the player. It was not a shadow-map, tonemap, weather,
material-LUT, cubemap, resolution, or renderer-API defect.

#### Decisive on-device measurement

A temporary five-band `combine_1.ps` build displayed:

1. albedo;
2. SSAO factor `occ`;
3. hemispheric diffuse before `occ`;
4. hemispheric diffuse after `occ`;
5. the normal production output.

The environment input and pre-SSAO hemispheric result were strong. `occ` sat
near its remapped minimum across most of the world and reduced the entire
ambient/hemispheric term by about six times. The local-light accumulator is
added separately and is not multiplied by `occ`, exactly explaining the
correctly lit circle around the player.

#### Root cause

`gl/common.h` assigns explicit zero defaults to ES feature macros because GLSL
ES rejects undefined names in numeric preprocessor expressions:

```glsl
#define SSAO_QUALITY 0
#define SSAO_OPT_DATA 0
```

`gl/ssao.ps` still used presence checks:

```glsl
#ifndef SSAO_QUALITY
#ifndef SSAO_OPT_DATA
```

An explicit `0` is nevertheless defined. The active high-quality profile with
`SSAO_OPT_DATA=0` therefore entered the optimized branch and sampled
`s_half_depth`, although the CPU allocates/populates that path only when the
option is enabled. The same mismatch meant `r2_ssao st_opt_off` did not enter
the `return 1.0` stub, so the earlier “SSAO off did not help” test was invalid.

#### Fix

`res/gamedata/shaders/gl/ssao.ps` now tests values:

```glsl
#if SSAO_QUALITY == 0
#if SSAO_OPT_DATA == 0
```

No brightness, weather, cubemap, tonemap, ANGLE, or Metal workaround was added.
The temporary diagnostic bands were removed.

#### Verification

- Diagnostic A/B: before the fix, `occ` was near its floor and the post-SSAO
  hemisphere collapsed; after the fix, `occ` became bright and spatially
  varying, and pre/post hemisphere bands agreed outside actual occlusion.
- Repeated with `r3_gbuffer_opt off` and the final baseline
  `r3_gbuffer_opt on`.
- Full gate passed: 279/279 compile, 2/2 low-settings compile, 6/6 SSAO resource
  branches plus the value-macro contract, 137/137 links, arm64 engine build.
- Final normal build rendered detailed terrain and vegetation globally, both
  before and after a nine-second synthetic walk.
- Two further cold launches reproduced the corrected frame.
- Final boot log contained no fatal, shader compile/link, or GL error.
- Final device profile: 932×430, VSync off, SSAO high, G-buffer optimization on,
  MSAA 2×, texture LOD 0, anisotropy 15.

#### Follow-up

Cross-save/indoor startup-sector validation and memory/lifecycle work remain the
active P0. The older HBAO/SSR helpers also contain presence-style feature tests;
audit those as a separate P1 before enabling them. The primary SSAO path is
fixed and verified.

## 2026-07-19 — 2× resolution and HUD/menu/inventory textures

- Enabled and verified `r__supersample 2`: the captured render target is
  1864×860 instead of 932×430.
- Fixed `dxUIShader::GetBaseTexture`: sampler units are keys in
  `STextureList`, not vector indices. The old code could return no texture and
  divide atlas coordinates by 0.
- Fixed the decisive ES backend defect in the non-indexed render path. The
  `glDrawElementsBaseVertex` fallback stores `baseV` in classic attribute
  pointers; `glDrawArrays` must reset that base before using `startV`, otherwise
  the offset is applied twice. This was why CPU-side inventory geometry and
  textures were valid while no panel reached the frame.
- Added a screen-space UI state boundary (no inherited depth, stencil or
  culling), a zero-size texture guard, and a strict lexicographic comparator for
  the shared UI shader cache.
- Device result at 1864×860: textured minimap and complete inventory including
  panels, portrait, equipment, weapons, slots, scrollbar and item icons.
- Diagnostics used to prove texture bindings, draw state and framebuffer writes
  were removed after verification.

### 2026-07-13 — Phase 2 deps: 7 libraries cross-build static
Commits: `b1d4e9206`, `122c5ba2a`, `0ac1cdb48`, `839c73d7a`, `69fa964cd`.

- **Done:** SDL2 2.32.10, OpenAL-soft 1.25.2, libogg 1.3.6, libvorbis 1.3.7,
  libtheora 1.1.1, lzo 2.10, and LuaJIT all cross-build **static** for both iOS SDKs
  (device OS64 + simulator SIMULATORARM64), verified arm64.
- **How:** a CMake ExternalProject superbuild at `cmake/ios/deps` (SDL2/OpenAL/
  ogg/vorbis/lzo); theora via an injected CMake build at `cmake/ios/theora` (upstream
  is autotools-only); LuaJIT via iOS-guarded fixes in `Externals/LuaJIT-proj` +
  a standalone `cmake/ios/luajit-check`.
- **Errors & fixes:**
  - CMake 4.x rejected `cmake_minimum_required(<3.5)` (vorbis/lzo) →
    `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` in the superbuild's common args.
  - OpenAL: Xcode 26.5 `-Werror=function-effects` in `coreaudio.cpp` →
    pre-seed `-DHAVE_WFUNCTION_EFFECTS=OFF` (skips the probe that adds the flag).
  - LuaJIT host tools were built as iOS binaries (Abort trap 6 when run) → build
    `minilua`/`buildvm` native (`--target=<host>-apple-macos`, `CMAKE_OSX_SYSROOT=macosx`);
    the working `-target` form is the single-token `--target=…`.
  - LuaJIT `use of undeclared identifier 'lj_cf_jit_util_trace*'` → `LUAJIT_DISABLE_JIT=ON`
    (interpreter mode — the intended iOS baseline; aligns host tools + target sources).

### 2026-07-13 — Phase 1: toolchain, smoke test, `.ipa`
- **Done:** vendored `cmake/toolchains/ios.toolchain.cmake` (leetal); an engine-free
  smoke test (`misc/ios/smoketest`) builds for simulator + device on macOS CI and
  produces an unsigned, SideStore-ready `.ipa`; added the `XR_PLATFORM_APPLE_IOS`
  macro (`src/Common/Platform.hpp`); restricted CI so only the `iOS` workflow runs on
  `ios-port` (desktop matrix + StyleCheck disabled for the branch).

## 2026-07-19 — 1864×860 becomes the actual iOS drawable

The earlier 2x experiment used `r__supersample` to create 1864×860 engine render
targets while UIKit and input remained 932×430. That established that the engine
could render at the requested size, but it left presentation resolution coupled
to a misleading renderer cvar.

### Change

- Added an iOS Objective-C++ bridge that sets SDL's actual EAGL view
  `contentScaleFactor` to 2.0 immediately after context creation.
- `CHW::GetSurfaceSize()` now reads `SDL_GL_GetDrawableSize()` instead of
  multiplying the logical mode by `r__supersample`.
- `CRenderDevice::SelectResolution()` preserves the actual drawable dimensions
  after context creation and uses the 2x backing-store contract only as its
  pre-context seed.
- Device `user.ltx` was returned to `r__supersample 1`.

### Device evidence

- Full arm64 engine gate passed after regenerating the Xcode project.
- The signed build installed under the existing
  `io.github.tryk016.openxray.RMJWWPF379` container.
- Boot log: `OpenGL drawable 1864x860 (UIKit window 932x430, scale 2.0)`.
- Periodic frame capture: 1864×860.
- Therefore the final blit is 1864×860 → 1864×860: no final spatial upscale or
  downsample.

The remaining Options readability defect is now correctly classified as a
logical UI layout/font/contrast problem, not a framebuffer-resolution problem.

## 2026-07-19 — iOS-only scope and readable Options layout

### Product decision

- The active product is now explicitly iOS-only. Preserving desktop behavior is
  not an acceptance criterion and must not delay a better iPhone implementation.
- Retail Call of Pripyat resource and gameplay compatibility remain required.

### Change

- `CScriptXmlInit` maps only the iOS request for `ui_mm_opt.xml` to the dedicated
  `ui_mm_opt_ios_16.xml` widescreen layout.
- The iOS Options panel and its control geometry are 25% larger while retaining
  the original 1024×768 logical UI coordinate system.
- Body text moved from `letterica16` to `letterica18`; tabs and primary actions
  moved from `letterica18` to `letterica25`.
- Grayscale text states were raised from 170/200/210/230 to
  225/240/245/255, and disabled text from 70 to 120.
- Comboboxes can opt in to applying their XML `list_font` to the closed value;
  only the iOS Options file enables that behavior.
- The original node names, tab IDs and all 76 `options_item` bindings were
  preserved.

### Validation

- `xmllint` accepted the new XML and an automated tree comparison found the same
  535 nodes, IDs and option bindings as the source widescreen layout.
- Full gate passed: 279/279 shader compile, 2/2 low-settings, 6/6 SSAO branches,
  SSAO numeric contract, 137/137 links and arm64 engine build.
- The signed build installed over the existing app without deleting its data
  container.
- Boot log still reports
  `OpenGL drawable 1864x860 (UIKit window 932x430, scale 2.0)`.
- Device configuration remains `r__supersample 1`, `ui_style_default`, VSync off.
- Capture `/tmp/xr-options-ios-readable-v1.png` is 1864×860, shows the enlarged
  panel without clipping, and the user accepted its readability.

### Device state

- The app is running in the Options screen for visual inspection.
- Autoload is temporarily absent so the app opens at the main menu during UI
  work; `keypress_on_start 0` remains set.

## 2026-07-21 — autonomous diagnostics isolated, CI gate made blocking, UI hardening

### Hypotheses and observables

- If the autonomous harness perturbs normal gameplay, a normal launch must
  execute neither periodic `glReadPixels` nor `autoinput.txt` polling.
- If CI is authoritative, the rolling IPA release must depend on the exact local
  compile/link contract and an older/different-ref run must not clobber it.
- Static UI findings were accepted only where the source established a direct
  mismatch; inventory/PDA behavior that depends on retail XML remains a device
  test, not a claimed fix.

### Implemented

- Added the zero-default `ios_diagnostics` console variable. It gates both the
  five-second full-frame dump and four-Hz file input polling.
- `install_device.sh --diagnostics` enables the gate; normal `--launch` writes 0.
  Failure to read/write `user.ltx` now aborts instead of launching in an unknown
  mode. `input.sh` and `shot.sh` verify the mode before acting.
- Frame capture publishes an atomic generation sidecar after the PPM. `shot.sh`
  waits up to seven seconds for a new generation, preventing stale captures from
  a previous process.
- Synthetic key state moved from function statics into `CInput`, uses wrap-safe
  SDL tick comparisons, is reset on lifecycle changes, rejects extra protocol
  tokens, and supports digit keys.
- Removed finished cursor, framebuffer sample, DXT-channel, controller and focus
  diagnostics. Kept the controller focus affordance as a stable gold frame,
  without flashing, bracket glyphs or one-Hz logs.
- Controls now use `letterica18` binding values. Controller assignments notify
  `key_binding_gamepad`, and mouse buttons cannot be assigned into controller
  fields. Binding capture accepts only a fresh press, so releasing the gamepad
  Accept button that opened edit mode cannot immediately bind itself. The
  enlarged AO row is 130 units high so its fourth button ends before SSAO Quality.
- Frame-line, frame-window and radial-progress rendering now refuses missing or
  0×0 base textures rather than generating non-finite UVs.
- GitHub Actions invokes `build_check.sh --shaders` in strict mode with pinned
  glslang 16.4.0. Release depends on that job, publishes only from `ios-port`,
  and the workflow uses one global concurrency group for the rolling tag.
- Device CI requests Xcode `dwarf-with-dsym`, verifies a non-empty
  `__debug_info` section and exact binary/dSYM UUID equality, archives the dSYM,
  and uploads it as a separate artifact. The size check intentionally uses
  `--show-section-sizes`: piping the full 879 MB symbol dump into `grep -q`
  would be slow and could fail under `pipefail` after the early reader exit.

### Verification

- Shell syntax, YAML parsing and `git diff --check`: pass.
- glslang tag `16.4.0` exists. A clean clone configured and built the real
  `glslang-standalone` target; its executable reports 16.4.0. This caught and
  corrected an initially wrong CMake target name before handoff.
- Full local gate: 279/279 stages, 2/2 low-settings, 6/6 SSAO branches, numeric
  SSAO contract, 137/137 stage links, and arm64 engine build. The last two runs
  rebuilt nine and then one translation unit; both passed.
- A full local Release rebuild with Xcode debug symbols completed successfully.
  Its dSYM contains 590,419,597 bytes of `__debug_info`, has UUID
  `757911FA-42BC-31A2-9B2A-CDEAE241BDB5` matching the app binary, and produced a
  292,020,812-byte zip that passed `unzip -t`.
- No install or device validation was performed in this slice because the user
  intentionally made the phone unavailable. The prior installed build and its
  retail assets/save container were untouched.

### Audits and remaining work

- HBAO/HDAO contain dormant zero-default macro violations; SSR has one locally
  unsafe helper test. These are not active in the current iOS profile and must
  gain real harness entries before being enabled or changed.
- Advanced Options and Controls need one device capture/test. Inventory/PDA/map
  need logging of the exact retail XML selected by `ui_style_default`; focus
  auto-scroll and viewport clipping are still open.
- CI still needs one remote artifact run plus a deliberately failing remote
  shader/varying canary. Device validation must also prove that normal launch is
  free of autonomous readback/polling and that diagnostic captures are fresh.

## 2026-07-21 — independent review corrections and offline UI extension

An independent read-only review found four defects before handoff:

- dSYM matrix values had initially landed on `deps`, while `engine-build` read
  them. They now live on the engine matrix; only the device row requests
  `dwarf-with-dsym`.
- `dwTimeGlobal` freezes in paused menus. Diagnostic capture now schedules and
  stamps with `dwTimeContinual`, so Options captures continue to advance.
- The release initially waited on only engine+shader and used cancellable
  workflow concurrency. It now waits on smoke, engine, shader and isolated
  LuaJIT jobs; only publication is serialized, and an in-flight IPA/apps.json
  replacement is never cancelled.
- If the first metadata pull failed, `shot.sh` could accept an old token. It now
  treats the first later token as a baseline and requires another generation,
  waiting up to twelve seconds.

Additional hardening pins glslang 16.4.0 to commit
`168d452a4f460d24b588fed08477a81c44ee27a1`. A successful full local gate now
writes a stamp; the default installer refuses a local app when relevant source,
gamedata, CMake or shader-check inputs are newer than that stamp.

The dense-UI follow-up was also prepared without the phone. All `CUIXml::Load`
overloads log the requested name and actual VFS path only in diagnostic mode.
Drag/drop inventory lists scroll an off-screen controller-focused cell into
view, and the gold focus overlay reconstructs clipping for drag/drop, list and
scroll ancestors. The final full gate rebuilt 1,854 translation units and passed
279/279, 2/2, 6/6 plus the numeric contract, 137/137 and arm64. Device validation
of those three UI behaviors remains pending.

The earlier symbol-enabled validation build proved a 590,419,597-byte
`__debug_info` section, matching UUID and valid 292,020,812-byte archive. The
later ordinary local Release link necessarily received a new UUID, so its stale
validation dSYM was moved out of `bin`; CI creates and checks a fresh dSYM from
the same clean device build it packages.

## 2026-07-24 — M3 Pro/Xcode 26.6 migration and iOS 16.4 revalidation

### Scope and host

- Source revision remained `43dadb509f49` on `ios-port`; the inherited dirty
  worktree was preserved.
- New host: Apple M3 Pro, 36 GiB RAM, macOS 26.5.2 (25F84), Xcode 26.6
  (17F113), iPhoneOS/iPhoneSimulator SDK 26.5, AppleClang 21, CMake 4.4.0,
  glslang 16.4.0 and Ninja 1.13.2.
- The migrated shell initially selected x86_64 Homebrew programs from
  `/usr/local`, producing `bad CPU type`. The obsolete Intel Homebrew
  initialization was removed from `.zprofile`, and `build_check.sh` now
  prepends `/opt/homebrew/bin` on arm64.
- Product deployment target was corrected and enforced as iOS 16.4 throughout
  CMake defaults, smoke/provisioning projects, CI, metadata and documentation.

### Clean build evidence

- Clean device and simulator smoke projects passed as arm64 with SDK 26.5 and
  `minos 16.4`.
- Device and simulator dependency superbuilds completed from source. Each
  prefix contains arm64 SDL2, OpenAL Soft, Ogg, Vorbis, VorbisFile, Theora and
  LZO archives, and their nested caches use deployment target 16.4.
- Isolated LuaJIT checks passed for `OS64` and `SIMULATORARM64`. `minilua` and
  `buildvm` are macOS arm64 host tools; both target archives are arm64.
- The first device engine build exposed a cross-build bug: the target's iOS
  `CMAKE_OSX_DEPLOYMENT_TARGET=16.4` was forwarded to LuaJIT's macOS host-tool
  CMake, causing an invalid `-mmacosx-version-min=16.4`. Removing that forwarding
  repaired the host/target boundary.
- The complete simulator engine built successfully for platform
  `IOSSIMULATOR`, arm64, minOS 16.4 and SDK 26.5. After the final CMake-symbol
  change, the simulator was regenerated with `GCC_GENERATE_DEBUGGING_SYMBOLS=NO`
  and a representative `xrCore` rebuild passed.
- The final clean device gate compiled 1,854 translation units and passed
  279/279 shader stages, 2/2 low-settings stages, 6/6 SSAO branches, the SSAO
  numeric macro contract and 137/137 links.

### dSYM correction

- Xcode 26.6 generated a target-level
  `GCC_GENERATE_DEBUGGING_SYMBOLS=NO`, overriding the cache-level CI request.
  Applying the requested Xcode debug attributes only to `xr_3da` created a
  technically valid but incomplete dSYM with only 51,469 bytes of
  `__debug_info`.
- The final fix walks the complete generated CMake target graph and applies the
  explicit device/simulator debug choice to every compiled target. A clean
  device build produced an 880 MB dSYM containing 590,443,673 bytes of
  `__debug_info`.
- The app and dSYM UUID are both
  `92E88454-67D1-3689-8AC9-F5AED83B4828`. The final Mach-O is arm64,
  `platform IOS`, `minos 16.4`, SDK 26.5. Its Info.plist reports iPhone/iPad
  families and iOS 16.4.
- The final app target now exposes a matching Xcode product bundle identifier
  and target device family, removing Xcode 26.6's generated-plist warnings.

### Device validation

- The paid-team Apple Development identity has a private key. The profile is
  for Team `RMJWWPF379` and application identifier
  `RMJWWPF379.io.github.tryk016.openxray.RMJWWPF379`; it expires
  2027-07-19.
- Xcode 26.6 sees the paired, wired iPhone 15 Pro Max on iOS 26.6 with Developer
  Mode enabled. Both its hardware UDID and CoreDevice identifier resolve to the
  same device.
- The clean build signed, installed over the existing bundle and launched
  without uninstalling or replacing the data container. Autoload and
  `keypress_on_start 0` were restored.
- Diagnostic mode produced a fresh native 1864×860 frame from the active level
  showing world geometry, textures, weapon and HUD. A following normal install
  and launch explicitly restored `ios_diagnostics 0`; the final process remained
  running.
- The fresh runtime log grew through level script initialization and weather
  setup without a fatal, assertion or crash. The only `failed` entry was the
  expected unused GameSpy ATLAS initialization.

### Remaining findings

- The dependency prefix contains OpenAL Soft, but the application cache and
  final link still choose Apple's SDK `OpenAL.framework`. IOS-P2-002 remains
  open.
- CMake 4.4 warns that the vendored iOS toolchain's old policy compatibility is
  deprecated. LuaJIT's configure probe also prints its existing
  `MACOSX_DEPLOYMENT_TARGET` Makefile warning although the generated host tools
  were proven macOS arm64. Neither warning blocks the validated builds.
- Rendering, multi-save/level, dense UI, lifecycle, memory and performance work
  remain separate product-validation tasks; the migration test does not close
  them.

### Gate hardening after the symbol proof

The earlier CI check accepted any nonzero `__debug_info`, which would have
accepted the observed 51,469-byte final-target-only dSYM. The local full gate
and Actions now require at least 100 MiB plus an exact app/dSYM UUID match. The
clean 590,443,673-byte reference passes with a large safety margin.

## 2026-07-24 — project cleanup and pre-macOS 27 checkpoint

### Cleanup boundary

- Scope is limited to `/Users/patryk/openxray`. Intel Homebrew, other projects
  and installed applications are outside the cleanup.
- A briefly started global ARM-tool migration was stopped when scope was
  clarified. Its added Homebrew formulae/cask, dependencies and configuration
  directories were removed again; the prior ARM Homebrew leaves list was
  restored.
- Removed ignored caches named for iOS 15, Xcode 26.4 migration, partial
  dependency builds and the failed LuaJIT host-target experiment.
- Removed one-shot `env-smoketest-*` and `env-luajit-*` validation trees after
  their evidence had been recorded, plus empty `tools/` and obsolete local
  `.claude` permissions.
- Kept both active ARM dependency prefixes/superbuilds, device and simulator
  engine trees, the final app and its matching dSYM for post-update comparison.

### Documentation cleanup

- Kept all seven intentional iOS documents. The dated audit remains an
  immutable superseded snapshot, the controller prior-art file remains an active
  design reference, and the Metal RFC remains explicitly deferred.
- Removed the tracked repository-root `apps.json`: CI generates release metadata
  from the packaged IPA, while that unused snapshot still described a
  pre-renderer crash build.
- Reduced completed implementation detail in the active plan, added explicit
  evidence/acceptance contracts and corrected stale Theora/memory wording in
  the deferred Metal RFC.
- Added IOS-P1-009 with measurable macOS 27 build, GPU-capture and XCUITest
  acceptance criteria.
- Corrected README links. The journal remains append-only; despite the old
  header saying “Newest entry first”, current entries are appended at EOF. The
  canonical status remains `iOS-Port.md`, not the 2026-07-19 “Current facts”
  preface above.

### Recovery contract

- Local checkpoint tag: `ios-pre-macos27-2026-07-24`.
- Portable backup:
  `/Users/patryk/openxray-backups/openxray-ios-pre-macos27-2026-07-24.bundle`.
- Unsigned app plus matching full-dSYM archive:
  `/Users/patryk/openxray-backups/openxray-ios-artifacts-pre-macos27-2026-07-24.zip`.
  Installation still applies the stable bundle identifier, profile and
  signature to a temporary copy.
- This checkpoint does not prove that macOS 27 can capture the translated GLES
  workload. That must be tested after the update; XCUITest automation also
  remains implementation work.

### Additional project-only cleanup

- Removed the old `openxray-handoff` startup prompt and generated workflow
  scripts because they encoded the superseded Intel/iOS 15 workflow. Preserved
  `device-backup-2026-07-19` with saves, configuration and boot log.
- Removed the historical local iOS 15 IPA, shader-check Python cache, temporary
  CMake migration logs and Finder metadata.
- The local SDL README named near the top of this journal is not present because
  SDL is fetched by the dependency superbuild. Use the linked upstream SDL2
  document; do not treat the missing local path as a checkout defect.

### Final validation before checkpoint

- The complete local gate passed after cleanup: 279/279 shader stages, 2/2
  low-settings stages, 6/6 SSAO branches, the numeric macro contract, 137/137
  shader links and an arm64 device build. Fourteen translation units rebuilt.
- The dSYM still contains 590,443,673 bytes of `__debug_info`; its UUID
  `92E88454-67D1-3689-8AC9-F5AED83B4828` matches the app. The Mach-O remains
  arm64, platform iOS, minimum OS 16.4 and SDK 26.5.
- Final libraries in both active prefixes contain only arm64 slices. Intel-named
  files seen below the build trees are unlinked architecture samples shipped in
  LZO source and Xcode compiler-identification scratch files, not migrated
  products.
- No device reinstall was needed for this documentation/cleanup slice. The last
  installed build remains the previously validated normal-mode build, and its
  data container was not touched.

### Independent pre-checkpoint hardening

- Review found that the full-gate stamp could still be written when the device
  cache did not request debug symbols. `build_check.sh` now fails unless
  `GCC_GENERATE_DEBUGGING_SYMBOLS=YES`, then always checks the 100 MiB
  `__debug_info` floor and exact app/dSYM UUID before touching the stamp.
- The installer no longer chooses the first Apple Development identity. It
  decodes `DeveloperCertificates` from the selected paid-team profile and uses
  only a matching keychain fingerprint with a private key.
- Freshness checking now covers all `src`, `res`, `Externals`, CMake and
  `.gitmodules` inputs, plus the iOS app metadata/assets and gate scripts.
  Device-control, provisioning-stub and smoketest-only edits do not invalidate
  an otherwise current engine artifact.
- `install_device.sh --renew` proved that the current profile has a matching
  signing identity. The hardened full gate then passed 279/279, 2/2, 6/6, the
  numeric SSAO contract, 137/137 and a zero-unit incremental arm64 build; the
  full dSYM size and matching UUID remained unchanged.

## 2026-07-24 — macOS 27 and Xcode 27 requalification

### Proven environment and cache recovery

- The updated host is an Apple M3 Pro with 36 GiB, macOS 27.0 beta
  (`26A5388g`), Xcode 27.0 beta (`27A5228h`), iPhoneOS/iPhoneSimulator SDK
  27.0, AppleClang 21.0.0 and CMake 4.4.0. Xcode first-launch setup is complete.
- Xcode sees the paired iPhone 15 Pro Max on iOS 26.6 and the installed
  iPhone 17 simulator runtime. The downloaded Metal toolchain reports status
  `installed`; `metal`, `metallib` and the `Metal System Trace` template are
  available.
- Nested dependency and LuaJIT host-tool caches still referenced the removed
  Xcode 26.5 path under `/Applications/Xcode.app`. Only generated cache state
  was discarded; the host-tool directories were first quarantined and removed
  after the new builds passed. No source change was required.
- Fresh device and simulator smoke tests passed as arm64, platform IOS and
  IOSSIMULATOR, minimum OS 16.4 and SDK 27.0. Device and simulator dependency
  superbuilds rebuilt all seven expected arm64 archives.
- Isolated LuaJIT device/simulator checks passed with macOS arm64 host tools and
  iOS arm64 target archives. AppleClang 21 adds non-fatal warnings in the
  vendored LuaJIT and luabind sources.
- The complete simulator engine built successfully as arm64,
  `platform IOSSIMULATOR`, `minos 16.4`, SDK 27.0.

### Full gate, signing and device evidence

- The clean device gate rebuilt 1,854 translation units and passed 279/279
  shader stages, 2/2 low-settings stages, 6/6 SSAO resource branches, the SSAO
  numeric macro contract and 137/137 shader links.
- The final Mach-O is arm64, `platform IOS`, `minos 16.4`, SDK 27.0. Its dSYM
  contains 513,353,999 `__debug_info` bytes; app and dSYM share UUID
  `66122874-7481-3540-9C4F-8C171313E766`.
- `install_device.sh --renew` validated the paid-team profile and matching
  private key. The app then signed, installed over
  `io.github.tryk016.openxray.RMJWWPF379` without uninstalling and launched from
  the existing data container.
- Diagnostic mode produced fresh native 1864×860 loading and gameplay frames.
  The gameplay frame showed the loaded world, textures, HUD and weapon. A final
  ordinary install/launch restored `ios_diagnostics 0`.

### GPU tooling and remaining work

- A ten-second whole-device `Metal System Trace` recorded successfully and
  saved a 142 MB trace. Its table of contents includes `xr_3da`; export produced
  47,766 `metal-gpu-execution-points`, proving that Xcode can inspect the
  translated GLES-on-Metal workload.
- `xctrace --attach` could not resolve the same process by name or PID even
  though Xcode 27 `devicectl device info processes` listed it. Whole-device
  recording is the working capture path. Trace finalization/export also emits
  non-fatal overlapping-dylib timeline warnings in this beta.
- Plain `lipo` is no longer exposed in the macOS 27 shell path; `xcrun lipo`
  works and was used for artifact verification.
- XCUITest Main Menu → Options automation remains unimplemented and is the only
  unfinished acceptance branch of IOS-P1-009. Rendering correctness across
  additional saves/levels, dense UI, lifecycle, memory and performance remain
  separate validation work.

## 2026-07-24 — iOS product graphics profiles and simplified menus

### Product/UI change

- Added an iOS-only construction-time filter for both known Multiplayer button
  names. Filtering happens before menu-item allocation, so repository fallback
  XML and retail XML in the device container follow the same product contract.
- Replaced the editable 932×430 `vid_mode` combo with a read-only
  `Device.dwWidth` × `Device.dwHeight` value. The supported device therefore
  reports the real 1864×860 render target/drawable without requesting another
  scale or restart.
- Replaced renderer, window mode, legacy five-level `_preset` and Advanced
  graphics controls with three modes: Performance, Optimal and Quality.
  Gamma, contrast and brightness remain directly adjustable.

### Profile architecture

- All modes keep the native 1864×860 resolution and current shader/resource
  topology. Performance applies a low runtime tier with a 60 FPS target;
  Quality applies the full runtime tier at 30 FPS; Optimal starts balanced at
  30 FPS.
- Optimal measures frame work before limiter sleep and uses two-second p90
  windows. It lowers one tier after two windows over 31 ms or one over 40 ms,
  and raises only after eight windows below 24 ms. Resume warmup plus 8/20
  second downgrade/upgrade cooldowns prevent oscillation.
- Adaptation changes only `rs_vis_distance`, `r__geometry_lod`,
  `r2_ls_squality` and `r2_slight_fade`. Low Power Mode or serious/critical
  thermal state forces the performance tier at 30 FPS; fair thermal state caps
  the controller at balanced.
- Textures, SSAO, shader macros, render targets, MSAA, VSync and postprocess
  features are not switched at runtime. Audit also corrected a stale project
  claim: GLES currently forces MSAA off, so inherited `r3_msaa 2x` config text
  is not evidence of active multisampling.

### Verification and evidence boundary

- Added a deterministic C++ policy test plus a static iOS UI contract gate.
  They verify downgrade/upgrade/cooldown/suspension behavior, exactly three
  tokens, no legacy preset/Advanced exposure, native read-only resolution and
  the runtime-safe knob allowlist.
- The first arm64 iPhoneOS engine gate passed with 69 recompiled translation
  units. A complete arm64 iPhoneSimulator build then linked successfully
  against SDK 27.0 with minimum iOS 16.4.
- Device and simulator targets share the same final `bin` product path. The
  full gate and installer now reject any payload whose Mach-O platform is not
  `IOS` or whose minimum OS is not 16.4, preventing a later simulator build
  from being mistaken for an installable device artifact.
- After the simulator build, a forced device relink and complete gate passed:
  policy/UI gates, 279/279 shader stages, 2/2 low-settings stages, 6/6 SSAO
  branches, the numeric macro contract, 137/137 links and arm64 engine. The
  final app is `platform IOS`, minOS 16.4, SDK 27.0; its 513,358,781-byte
  `__debug_info` and app share UUID
  `A8883D4D-13B4-330B-BFBA-F7C9EEB8BBE2`.
- No phone was used for this slice. Menu appearance, profile persistence,
  runtime transitions, frame pacing, thermal response and the proposed tier
  values remain explicitly untested on device.

## 2026-07-26 — agent context and handoff structure

### Problem and decision

- The former root instruction could be read as requiring every agent to load
  the complete specification, Plan, Resume, 179,765-byte Journal and
  42,923-byte Metal RFC before changing the port.
- The source documents totalled approximately 74,000 model tokens when loaded
  together. The Journal alone represented roughly 45,000, despite normally
  containing only a few relevant historical sections for a task.
- Markdown remains the source of truth. Converting handoffs to JSON/JSONL would
  repeat keys, increase token cost and reduce human readability. JSONL is
  reserved for a future generated evidence/performance stream only when a tool
  consumes it.

### Structure change

- `AGENTS.md` now requires a bounded bootstrap: full Resume, relevant active
  task, relevant canonical section, then targeted `rg`/bounded reads from the
  Journal. Backlog and Metal RFC are loaded only for their explicit workflows.
- The active Plan now contains exactly five ordered tasks:
  IOS-P0-003, IOS-P1-005, IOS-P1-006, IOS-P1-002 and IOS-P1-003.
- Deferred P1/P2, distribution and renderer-decision items retain their IDs,
  evidence levels and acceptance criteria in `iOS-Port-Backlog.md`. Their
  presence does not authorize implementation.
- Resume was reduced from 228 lines to a concise current checkpoint, commands,
  contracts, proven/build-only boundary, next slice and safety rules.
- The prior 417-line Codex session log was preserved verbatim under
  `.Codex/archive/`; the active session log now contains only recent operational
  state.

### Evidence boundary

- This was documentation/routing work only. Engine, shader, UI resource and
  build artifacts were not changed, so the previous complete gate remains the
  latest code validation.
- Link, heading, task-ID and context-size checks are the acceptance evidence for
  this slice. No phone was required.

## 2026-07-26 — autonomous profile, UI, lifecycle and memory device pass

### Scope and safety

- Used one device owner throughout. Every engine change passed
  `./misc/ios/build_check.sh` before installation.
- Installed only over `io.github.tryk016.openxray.RMJWWPF379`; the retail asset
  and save container was never removed.
- Diagnostic readback was used only for deterministic UI/render evidence.
  Normal mode was restored after testing. At the end the installed build has
  `ios_diagnostics 0`, Graphics Profile `Optimal`, and no game process is
  running.

### Proven profile and presentation behavior

- Main Menu and Options are readable at a native 1864×860 capture. Multiplayer
  is absent. Video contains exactly Performance, Optimal and Quality, read-only
  1864×860, gamma, contrast and brightness.
- Performance, Optimal and Quality each selected their documented initial
  tier/target in the device log. Performance and Quality world captures plus
  the final Optimal world frame retained complete geometry, textures, HUD and
  correct global lighting without the former lit-circle defect.
- Five controlled foreground cycles increased the app's activation/deactivation
  counters by exactly 5/5 except for the final active state (10 activations,
  9 deactivations). The exact post-cycle frame remained 1864×860 and correct.
  This is diagnostic-mode lifecycle evidence; normal-mode cycles and audio
  interruption remain open.
- A 20-second normal-mode Optimal Activity Monitor trace sampled `xr_3da`
  21 times. CPU ranged 33.14–77.81% with 55.66% mean. Physical footprint was
  3,194.94–3,199.72 MiB with 3,197.81 MiB mean; resident memory averaged
  627.23 MiB and compressed memory averaged 2,578.80 MiB. The static-camera
  duration is too short to be a performance target or memory budget.

### Proven UI and deterministic input behavior

- Ordinary inventory and the core PDA/map pages are complete and readable in
  native captures. Dense overfilled-inventory focus/scroll/clipping remains
  untested.
- The retail PDA load still reports missing
  `ui_ingame2_pda_buttons_background_e` and `ui_pda2_fr_*` faction-frame and
  delimiter textures. Basic PDA/map captures do not exercise all of those
  fraction-war surfaces, so this remains a real UI gap.
- `misc/ios/input.sh` now accepts `tap x y` in logical 932×430 coordinates.
  Same-frame, one-frame, two-frame and four-frame move/click sequences were
  rejected by device behavior: the first event only established focus.
  Waiting 500 ms in continual time after the cursor move, then refreshing the
  pointer and sending press/release, opened Options with one command. The
  device log recorded `move (230,276) frame=496` followed by `click frame=516`.
- The fixed `autoinput.txt` filename still has a cable copy/delete race. A
  transient command failure was observed; this harness issue is not claimed
  fixed.
- iPhone Mirroring accepted macOS keyboard input: Escape closed the PDA and
  Home Screen plus `devicectl` launch supported controlled lifecycle cycles.
  Coordinate mouse injection did not reach the game. Mirroring requires the
  physical phone to be locked.

### Thermal constraint bug and correction

- Independent review found that `CanMeasure()` included
  `psIOSDiagnostics == 0` before Low Power/thermal handling. Diagnostics
  therefore suspended not only p90 adaptation, but also the canonical
  Performance constraint.
- Moved Low Power, serious/critical and fair thermal handling before
  `CanMeasure()`. Diagnostics/inactive/paused state now suspends only p90
  measurement. A second review reported no findings.
- Device evidence resolved the formerly untested branch. With
  `ios_diagnostics 1`, the log selected Optimal/balanced and immediately
  changed to Optimal/performance while the device reported `serious thermal`.
- Constraint telemetry now distinguishes `low power mode`, `serious thermal`,
  `critical thermal`, and combined Low Power plus thermal states. This changes
  only the logged reason, not tier policy. Independent review covered all
  combinations and reported no findings.

### User-reported error and memory evidence

- During the final repeated diagnostic launch, the user saw an error and the
  process disappeared. The app log contains no `FATAL`, assertion or GL error.
  It ends after normal level synchronization and repeated 1864×860 diagnostic
  shots.
- That run logged `serious thermal`, process heap 2,396,656 K and
  `phys_footprint 3,251,907 K`. System crash-log inventory contained
  contemporaneous Jetsam events with `largestProcess: xr_3da` and
  `vm-compressor-thrashing`; the latest inspected reports used the preceding
  build UUID and do not prove the exact termination reason of the user's final
  popup. Thermal plus compressor pressure is therefore a strong inference, not
  a closed crash diagnosis.
- To avoid reheating the phone, normal mode was restored by copying only the
  existing `user.ltx` back with `ios_diagnostics 0`; the game was not
  relaunched.

### Final build evidence and remaining boundary

- Final full gate: graphics-policy PASS, UI-contract PASS, 279/279 shader
  stages, 2/2 low-settings stages, 6/6 SSAO branches plus value-macro PASS,
  137/137 links and one rebuilt arm64 translation unit.
- Final artifact is platform IOS, minimum OS 16.4, SDK 27.0. App/full-dSYM UUID
  is `04DB2BAB-241F-3E95-BC36-9C69C337E97A`; full dSYM `__debug_info` is
  513,360,029 bytes.
- The final telemetry build installed successfully and reached the loaded level.
  The last accepted render capture,
  `/tmp/openxray-ios-test-20260726/44-final-profile-order-regression.png`, is
  1864×860 with SHA-256
  `3162c2fa9c227dc82692604a69ba0a78a6718a8e01d8895b651b6f31329b83a0`
  and comes from the immediately preceding UUID
  `5BD2156E-29D8-3371-80A3-23E0959BF49F`; the only subsequent source change was
  constraint-reason logging.
- Still unproven: Optimal measured downgrade/recovery/upgrade, Low Power
  separately from serious thermal, same-path normal-mode frame pacing, another
  outdoor save, indoor/portal start, save/reload, level transition, dense
  inventory focus, faction-war PDA, normal lifecycle/audio interruption,
  low-memory recovery and a 30-minute memory budget run.

## 2026-07-26 — content-addressed gates and FastDevice iteration build

### Objective and measured baseline

The user requested the previously proposed iteration-time work, then explicitly
limited this slice to local work without using the phone. The historical
approximately 38-second full-gate estimate was remeasured rather than assumed:
on the current M3 Pro/Xcode 27 host, sequential shader compilation took 4.88
seconds and ten CPU/memory-bounded workers took 1.53 seconds. A cached default
Release gate now takes 4.33 seconds; the final uncached full gate took 5.52
seconds. These are host-side gate timings, not runtime performance evidence.

### Implemented build contracts

- `glsl_es_check.py` now validates shaders through an ordered ten-worker pool,
  bounded by host or cgroup memory. Output and pass counts remain deterministic.
- Shader compile/link output is cached by content digest. Keys cover shader and
  renderer inputs, gate scripts, expected counts, Python/host identity, the
  resolved glslang executable and its macOS dylib closure. Hash failures and
  source changes during a gate are fatal; cache publication is atomic.
- `build_check.sh` distinguishes a cached default device gate from `--full`.
  Full mode deliberately reruns all shader compile/link work and is the pre-push
  contract. Partial `--shaders` and `--engine` modes never write install stamps.
- Existing CMake trees are refreshed only when a content hash of all
  `CMakeLists.txt`, `.cmake` and `.in` inputs, the controlling build script,
  broad typed cache values, CMake/Xcode versions or build context changes. Build
  context includes Git HEAD/branch, local date and CMake-visible CI variables.
  The symbol-complete tree explicitly forces and verifies
  `XRAY_IOS_FAST_DEVICE=OFF`.
- Device/full stamps bind the complete source manifest, Mach-O UUID, platform,
  minimum OS and complete relative-path app-bundle hash. A later ordinary gate
  removes an older full stamp when source, UUID or bundle content differs.
- `build_fast_device.sh` configures a separate
  `build/ios-engine-fastdevice-iphoneos` tree and writes
  `bin/aarch64/FastDevice/xr_3da.app`. It uses Release `-O3/NDEBUG` semantics,
  explicitly omits LTO and full dSYM generation, and never installs or launches.
- Both build paths run `sync_app_resources.sh`, so `res/gamedata` and
  `res/fsgame.ltx` reach the app bundle even when no engine target relinks.
- `install_device.sh --fast` selects only a valid FastDevice stamp. Both normal
  and FastDevice paths verify source hash and UUID, copy the app, then verify the
  copied full-bundle hash before any signing or device action.

### Local build and negative-test evidence

- The first cold FastDevice tree compiled 1,855 translation units in 227.01
  seconds. It initially failed only the post-build path assertion because Xcode
  appended `/Release`; an empty generator expression now keeps the intended
  `FastDevice/xr_3da.app` path. The corrected incremental run took 9.76 seconds.
- The final FastDevice no-op cache hit took 5.11 seconds. Its 75 MiB arm64
  executable bundle reports platform IOS, minOS 16.4 and SDK 27.0, UUID
  `96F29EA3-886F-3527-9E91-C1854A66D421`; no FastDevice dSYM exists. The
  symbol-complete Release app is 88 MiB and its dSYM is 739 MiB.
- Current Xcode build settings and binary inspection prove FastDevice uses
  `-O3`, `NDEBUG`, no debug info, no `LLVM_LTO`/`-flto` and no dSYM. The current
  symbol-complete Release tree also has no active LTO because its Xcode 27 IPO
  probe reports unsupported;
  the prior thin-LTO cost assumption was false for this environment.
- A temporary resource-only file reached the FastDevice app with zero C++
  recompiles and was removed from the bundle by the next `rsync --delete`.
- Relative-root bundle hashes matched across two copied logical trees. A
  deliberate FastDevice bundle mutation changed the digest and resource sync
  restored it. A root-only Release bundle mutation caused the next default gate
  to invalidate the otherwise matching full stamp; the probe was removed and a
  new full gate restored the final stamp.
- Temporary `.cmake` and `.in` inputs changed the CMake configuration digest;
  deleting them restored it. A changed CI environment changed the build-context
  digest. Temporary shader/renderer/source probes also produced the expected
  cache misses and stale-stamp rejection.
- Bash syntax, Python parsing, ShellCheck (apart from two pre-existing
  informational `SC2012` notices) and `git diff --check` pass. Three read-only
  review rounds drove fixes for cache atomicity, TOCTOU, dynamic dependencies,
  complete configure inputs, stale stamps, full-bundle authentication and
  Fast/Release mode isolation. The final review reported no P1/P2 findings.

### Final evidence boundary

The final symbol-complete gate passed graphics policy, UI contract, 279/279
shader stages, 2/2 low-settings stages, 6/6 SSAO branches plus value-macro
contract, 137/137 links and the arm64 iPhoneOS 16.4 engine. Release app and dSYM
remain UUID `04DB2BAB-241F-3E95-BC36-9C69C337E97A`; `__debug_info` remains
513,360,029 bytes. No install, launch, Mirroring, screenshot or other phone
action occurred. FastDevice installation and runtime behavior therefore remain
untested. Nothing was committed or pushed.

### Diagnostic-input header fan-out reduction

The remaining local-only optimization moved exactly five cable-diagnostic
fields (`nextPoll`, `holdUntil`, `holdKey`, `tapMoveFrame`, `tapPending`) out of
`CInput` and into an iOS-only file-local state in `xr_input.cpp`. Normal touch
position and active-finger state remain per-instance in `xr_input.h`.

Reference audit found no use of the five fields outside `xr_input.cpp`.
OpenXRay creates one `CInput` instance for the process, so the file-local state
matches the current iOS application contract. It would not preserve independent
state for overlapping `CInput` instances; supporting that hypothetical case
would require a larger sidecar map and is outside the iOS-only product scope.

The compile-cost discriminator was decisive:

- removing the fields from `xr_input.h` rebuilt 1,383 FastDevice translation
  units and took 199.42 seconds;
- adding a temporary field only to the file-local `.cpp` state rebuilt one unit
  and took 6.30 seconds;
- removing that temporary field again rebuilt one unit and took 6.33 seconds;
- the final symbol-complete Release gate rebuilt the same 1,383 units, took
  344.50 seconds including full dSYM work, and passed every gate.

The final Release artifact is arm64 platform IOS, minOS 16.4, SDK 27.0. App and
dSYM UUID are `1080C791-9890-3C5E-8A45-0B15BC24543C`; `__debug_info` contains
513,359,537 bytes. FastDevice returned to the probe-free source state with UUID
`5E43D1A6-F019-308F-B2A6-2D1865C5AA6C`.

### Resource-override boundary

`CLocatorAPI` currently copies the bundled `fsgame.ltx` and complete engine
`gamedata` overlay into Documents on every launch. A pre-launch direct copy of
XML, Lua or shaders to `Documents/gamedata` would therefore be overwritten.
The next safe design is a bundle-resource version marker plus an explicit
restore path; it must be device-validated before direct Documents sync becomes
an advertised workflow. This slice deliberately stopped at the proven
bundle-level `sync_app_resources.sh` path.

No install, launch, simulator, Mirroring or phone action occurred. Diagnostic
press/hold/tap timing remains behaviorally unchanged by code inspection and
local compile/link evidence, but the refactor itself is not runtime-proven.
Nothing was committed or pushed.

## 2026-08-01 — local lifecycle, LOWMEMORY and simulator reliability slice

### Scope and hypothesis

The user authorized autonomous local work but explicitly excluded the physical
phone. The slice targeted three device risks that could still be closed from
code and host evidence: lifecycle events racing a rendered frame, an
unimplemented low-memory response despite the observed approximately 3.2 GB
footprint, and profile/UI state being overwritten by legacy config paths.
Phone runtime correctness and a measured memory reduction remained outside the
claim boundary.

### Implemented contracts

- SDL lifecycle callbacks now publish to a single atomic event inbox. The main
  thread drains activity, persistence and low-memory work from one snapshot;
  `TryBeginFrame()` establishes a total order between a frame and a concurrent
  background transition. Persistence is requested together with inactivity,
  saved before deactivation and never performed from the SDL callback.
- iOS deactivation stops scheduling/input and explicitly detaches the EAGL
  context. Reactivation must successfully restore the primary context before
  focus, input and scheduling resume; failed restoration remains durable and is
  retried rather than rendering through an invalid context. The drawable and
  engine size are refreshed after a successful restore.
- `SDL_APP_LOWMEMORY` now records Mach current/peak footprint telemetry and
  performs allocator compaction on the main thread. GPU work waits for an
  active foreground primary context and no frame in progress. Each notification
  may evict at most 256 MiB of file-backed textures unused for 300 frames.
- Texture accounting records uploaded storage, including complete decoded RGBA8
  mip chains for DXT sources. Eviction clears storage and lazy reloads on next
  use. Raw-handle consumers such as environment/colormap aliases, UI and ImGui
  exports pin their source wrappers so the bounded batch cannot invalidate an
  untracked live alias.
- A dedicated `ResourcesLowMemoryEvict` path leaves the legacy renderer eviction
  API unchanged. Profile selection is applied before each frame and is
  synchronously reasserted after `cfg_load`, closing the window in which a
  following `vid_restart` could consume legacy values.
- PDA investigation proved that the reported faction-frame names were probes of
  alternative XML topologies, not missing retail textures. Authored FrameLine
  and static forms are now attempted first and the nine-slice fallback is only
  used when required.

### Review and local evidence

- Added deterministic host tests for lifecycle ordering, decoded texture
  accounting/eviction selection, graphics-profile policy and UI/config ordering.
  Three review passes first found five P1 issues, then three remaining P1
  issues; accepted fixes covered frame/lifecycle ordering, failed-context retry,
  exact renderer result propagation, raw texture-handle lifetime and immediate
  config reassert. The final focused review reported no P0/P1 findings.
- Final FastDevice validation passed all policy/UI gates, 279/279 shader stages,
  2/2 low-settings stages, 6/6 SSAO branches plus value-macro contract and
  137/137 links. Its UUID is
  `BCC55AFC-69E7-39D9-94D2-1CEFE83148E6`.
- The exact final source built for arm64 iOS Simulator, platform
  `IOSSIMULATOR`, minOS 16.4 and SDK 27.0. On iOS 26.5 it launched through
  AVAudioSession, OpenAL, input and filesystem initialization, cached 776 bundled
  files and stopped at the expected absent retail
  `Documents/gamedata/configs/system.ltx` boundary. The simulator process was
  terminated after inspection.
- A separate iOS 27 Simulator probe traps in UIKit before SDL with
  `NoSceneLifecycleAdoption`; SDL 2.32.10 has no scene lifecycle integration.
  `IOS-P1-010` records a deferred UIScene migration because changing the launch
  ownership model without physical-device lifecycle/save validation is unsafe.
- Final uncached full gate passed every policy/UI/shader/link contract and
  rebuilt 1,383 arm64 iPhoneOS translation units. The platform IOS, minOS 16.4,
  SDK 27.0 app and full dSYM share UUID
  `BB5691C2-BB4A-311A-AE3C-CE0D352C66AE`; dSYM `__debug_info` is 513,393,184
  bytes.

### Evidence boundary and state

No phone, install, Mirroring, capture or device-container action occurred. The
new lifecycle recovery, real low-memory notification, physical-footprint drop,
texture lazy reload in a retail level and PDA fallback remain device-untested.
The prior phone artifact/container and stopped-process state are unchanged.
Nothing was committed or pushed.

## 2026-08-01 — lifecycle input and persistence follow-up

Two read-only audits were run after the preceding local reliability slice.
They found two concrete lifecycle defects that did not require the phone to
fix.

- Direct touch bypasses SDL's mouse bitset, and `CInput::OnAppDeactivate()`
  cleared local state without guaranteeing receiver release callbacks. In
  addition, the level, main menu and editor overrides did not call the base
  `IInputReceiver` release helper. Deactivation now sends one synthetic
  `MOUSE_1` release only for touch-only state, re-reads `CurrentIR()` after that
  re-entrant callback, dispatches receiver-wide keyboard/mouse/controller
  releases, clears the active finger and diagnostic hold/tap state, and flushes
  stale queued input while retaining the logical cursor position.
- SDL queues focus/minimize window events before
  `SDL_APP_WILLENTERBACKGROUND`. The old loop could therefore deactivate before
  processing the atomic persistence request. The inbox is now drained
  immediately after SDL pumps events and before window-event dispatch;
  `ApplyActivityOrdered` is shared by production and host test code and enforces
  `persist -> deactivate`. The later drain/frame permit remains to consume
  events published during dispatch.
- The touch review found one P1 after the first patch: the synthetic release
  could mutate the receiver stack, invalidating a cached pointer. Re-reading
  `CurrentIR()` closed it; follow-up review reported no P0/P1. Independent
  review of the persistence patch also reported no P0/P1.
- The audio audit found no additional local P1 in the interruption state
  machine. It confirmed that the current artifact links deprecated system
  `OpenAL.framework`; selecting the already-built OpenAL Soft archive remains
  the separate deferred `IOS-P2-002` backend task and was not mixed into this
  lifecycle change.
- Final FastDevice passed all policy/UI/shader/link contracts and rebuilt one
  unit after the review fix. UUID:
  `FB44464E-0FFB-3F99-B652-E7C944F1EFD4`.
- The exact final arm64 iOS Simulator build (minOS 16.4, SDK 27.0) again launched
  on iOS 26.5 through AVAudioSession, OpenAL, input and 776-file filesystem
  initialization, then reached the expected missing-retail-`system.ltx`
  boundary. The process was terminated.
- Final uncached full gate passed the lifecycle, memory, profile, UI, 279/279,
  2/2, 6/6 plus value-macro and 137/137 contracts, rebuilding five iPhoneOS
  units. App/full-dSYM UUID is
  `35F1BD5F-410A-3773-87EF-27B5D8E8BB25`; `__debug_info` is 513,393,286 bytes.

No phone, device installation/container action, Mirroring, capture, commit or
push occurred. Held-input cancellation, save ordering, audio interruption and
context recovery remain device-untested.

## 2026-08-02 — external working-set review closure

### Scope and confirmed defects

An external read-only review covered the complete uncommitted iOS working set.
Every reported item was then rechecked against current code and split between
confirmed defects and rejected hypotheses. No physical-phone action was in
scope.

- The outer iOS loop had no pacing when `TryBeginFrame()` rejected a frame.
  Background or pending lifecycle work could therefore busy-spin until UIKit
  suspended the process. The skipped-frame path now drains once and sleeps
  10 ms; its host test enforces drain-before-backoff ordering.
- The broad claim that every resume leaked background wall time was false:
  normal single-player deactivation already freezes both `CTimer_paused`
  clocks. A real gap remained when a saved `rs_always_active=1` bypassed that
  pause, and a failed final context check could unpause callbacks before a
  retry. iOS now ignores that desktop policy, removes it from Options and rolls
  every activation callback back when final context validation fails.
- Optimal profile measurements used scaled integer milliseconds. Work and
  start-to-start elapsed time now come from unscaled `TimerMM` nanoseconds;
  sub-millisecond samples are retained and a lifecycle-sized gap restores the
  five-second warmup.
- A repeated `CTexture::Load()` incorrectly marked every existing surface as
  low-memory pinned. It now preserves the established ownership/pin state, so
  ordinary level textures remain valid eviction candidates.
- The artifact hash now includes all three mandatory policy tests. Empty or
  malformed tap coordinates get controlled validation, and the 300-frame
  eviction comment records both 60 and 30 FPS durations.
- The p90 empty-window, OpenGL `surface_get` constness and faction-war fallback
  reports were rejected after tracing their reachable call sites and retail
  asset topology; no speculative change was made.

### Review and validation

- Standalone graphics, lifecycle and texture-memory policy tests, the UI
  contract, shell validation, artifact-input manifest and `git diff --check`
  passed.
- Two final independent read-only reviews returned APPROVE with no P0/P1/P2.
- FastDevice passed 279/279 shader stages, 2/2 low-settings, 6/6 SSAO branches
  plus value-macro contract and 137/137 links. Final UUID:
  `BCF77E6D-2F7E-333D-BD02-986A09FF58EC`.
- The exact source built as arm64 `IOSSIMULATOR`, minOS 16.4, SDK 27.0 and ran
  on iOS 26.5 through AVAudioSession, OpenAL, input and 776 cached files to the
  expected missing-retail-`system.ltx` boundary. The simulator process was
  terminated. Its launch identifier is the base
  `io.github.tryk016.openxray`, distinct from the signed device identifier.
- The final uncached iPhoneOS gate rebuilt 21 translation units and passed all
  contracts. App and full dSYM share UUID
  `F7AF56B7-06F8-3D0A-A7BC-63A61DEF86F7`; `__debug_info` is 513,394,351 bytes.

No phone, device install/container action, Mirroring, capture, commit or push
occurred. Busy-spin elimination, always-active clock isolation, failed-restore
rollback, real Optimal adaptation and LOWMEMORY reduction still need physical
device evidence.

## 2026-08-03 — reliability device proof and Options XML crash closure

### Device and automation boundary

The physical iPhone 15 Pro Max ran iOS 26.6 and remained on Team
`RMJWWPF379` with bundle identifier
`io.github.tryk016.openxray.RMJWWPF379`; installation was in place and did not
erase the retail container. A minimal XCTest/XCUIAutomation host and test bundle
were signed and installed, but Xcode timed out while enabling automation mode
and then requested interactive authorization. No password or passcode was
guessed. The reusable runner remains provisional until its generated scheme and
first-device authorization are automated.

`build_fast_device.sh` passed graphics, lifecycle, texture-memory and UI policy
gates, cached 279/279 shader compilation, 2/2 low-settings, 6/6 SSAO plus value
macro and 137/137 links. It rebuilt 14 units before the first install. The
matching artifact retained UUID `BCF77E6D-2F7E-333D-BD02-986A09FF58EC` and was
installed with diagnostics without deleting the data container.

### Options defect found and fixed

Inventory and the pause menu rendered at 1864×860. Opening Options then stopped
fresh captures and logged a fatal `XML node not found` for
`video_adv:cap_always_active` in `ui_mm_opt_ios_16.xml`, reached from
`ui_mm_opt_video_adv.script`. The simplified iOS UI hides Advanced, but the
shared Lua constructor still initializes its controls.

The iOS XML now supplies `cap_always_active` and `check_always_active` as hidden
compatibility nodes without an `options_item`; desktop `rs_always_active`
therefore remains neither exposed nor writable on iOS. The UI contract enforces
both node presence and absence of a binding. The contract, `git diff --check`
and FastDevice gate passed; no translation unit rebuilt because this was a
resource/checker change. A fresh in-place installation then rendered Video,
Sound, Game and Controls, including `Optimal` and read-only `1864×860`, with no
new fatal entry.

### Lifecycle and LOWMEMORY measurements

Four scripted Safari-background/OpenXRay-foreground cycles on the current
reliability build retained PID 32231. Every cycle logged `app deactivate`,
`app activate` and `foreground drawable 1864x860 (engine 1864x860)`; fresh world
or PDA frames followed. This proves the diagnostic background path and current
context restore, not lock-screen, normal-mode, held-input or audio behavior.

`devicectl device process sendMemoryWarning` reported an `NSPOSIXErrorDomain`
ENOENT even though the current engine log proved delivery. At the foreground
safe point the first useful batch measured:

- before: physical footprint 3,262,723 KiB, uploaded textures 2,220,790 KiB;
- selection: 47 stale surfaces from 929 candidates, 262,143 KiB released against
  the 262,144 KiB budget;
- after: physical footprint 2,998,051 KiB, uploaded textures 1,958,646 KiB.

Thus uploaded storage fell by 262,144 KiB within rounding and current physical
footprint fell by 264,672 KiB. Fresh 1864×860 captures then proved complete PDA,
world/HUD and inventory surfaces, followed by a five-second forward walk and a
background/foreground cycle. No fatal, assertion or black frame followed. A
second immediate `sendMemoryWarning` returned the same tool error without a new
engine event, so repeated-warning/coalescing behavior remains unproven.

The final game process was stopped after evidence collection to preserve phone
battery. No app/container uninstall, certificate action, commit or push was
performed. The full symbol-complete gate is still required before publication.

## 2026-08-03 — phone-free XCUITest runner and review closure

### Reproducible local runner

The provisional XCTest/XCUIAutomation experiment was converted into a
repeatable tool under `misc/ios/ui_automation` without accessing the phone.
CMake generates the Xcode project, and `prepare_scheme.py` derives the host and
test buildable references from generated schemes before adding the testable and
macro-expansion relationships idempotently. Two unit tests cover repeated
patching and rejection of a missing target.

`run.sh --build` now performs a clean generic-device `build-for-testing`, then
strictly verifies both the host and nested runner signatures. Clean rebuilding
is intentional: review reproduced an incremental Xcode 27 path that re-signed
the nested `.xctest` after the containing Runner app, leaving the outer
signature invalid even though `xcodebuild` returned success. The runner also
serializes one owner per build directory, rejects extra arguments and existing
result bundles, checks for a local Apple Development identity and reserves all
device access for explicit `--test`. Test teardown terminates OpenXRay even
after an assertion failure.

Xcode 27's XCTest/XCUIAutomation binaries require iOS 17.0, so that minimum is
isolated to the test bundle. `AutomationHost` and the OpenXRay product remain at
iOS 16.4. The generated `.xctestrun` references the test bundle, runner and
target app correctly. First-device authorization and deterministic Main Menu to
Options navigation remain unproven because `--test` was not run.

### Follow-up review and final gate

Two read-only reviews found and closed five concrete gaps: a shared-build-tree
race, a test that could leave the game running, ignored extra runner arguments,
undocumented signing prerequisites and a static Options contract that checked
only XML. The contract now also proves that shared Advanced Lua constructs both
hidden always-active compatibility nodes.

The lifecycle review also found one surviving direct read of
`rs_always_active` at the end of precache. iOS now routes that startup-pause
decision through `ShouldBypassPauseForAlwaysActive`, making the saved desktop
flag fully inert as already required by the lifecycle policy test and canonical
contract.

Python unit tests, UI contract, `bash -n`, ShellCheck and `git diff --check`
passed. The final uncached full gate passed graphics/lifecycle/texture/UI
policies, 279/279 shader stages, 2/2 low-settings, 6/6 SSAO branches plus the
value-macro contract, 137/137 links and the arm64 iPhoneOS build. One
translation unit rebuilt. App and full dSYM share UUID
`DEBA5EDC-F455-32A9-A767-A04848A20614`; dSYM `__debug_info` is 513,394,539
bytes.

No phone, device installation, launch, Mirroring, container, certificate,
commit or push action occurred. The previously stopped game remains untouched.

## 2026-08-03 — final static-contract review correction

Follow-up review showed that the first Lua and startup-pause checks could be
satisfied by expected text left only in comments. This corrects the preceding
section's review-closure claim: those two P2 test weaknesses were still open at
that checkpoint even though the production implementations were correct.

The checker now removes Lua/C++ comments, requires active anchored Advanced-Lua
calls, isolates `CRenderDevice::RenderEnd()` and matches the complete iOS/helper
plus desktop/direct assignment block before accepting the final
`bypassStartupPause` condition. Controlled negative mutations with the expected
names only in comments are both rejected. Follow-up read-only review returned
APPROVE with no P0/P1/P2.

The uncached full gate was repeated after the checker change. It passed every
policy/UI/shader/link contract and rebuilt zero engine units; app/dSYM UUID
remains `DEBA5EDC-F455-32A9-A767-A04848A20614` with 513,394,539 debug-info
bytes. No phone or external state was accessed.

## 2026-08-04 — first-device XCUITest authorization and three Options runs

The iPhone 15 Pro Max on iOS 26.6 was available for a bounded ten-minute test
window. No game installation, container, save, certificate or profile change
was made. The first explicit `run.sh --test` completed Xcode's device
authorization and passed its launch/foreground assertion (`1/1`, no warnings).
Its teardown terminated OpenXRay, confirmed by an empty process query.

The runner was then extended to retain before/after XCUIScreen attachments and
perform three independent launches. XCUITest exports the landscape-right
OpenGL app as a portrait-native screenshot, so the first two calibration runs
hit Credits rather than Options; those runs were inspected and deliberately
not counted as acceptance evidence. Moving the calibrated tap one main-menu row
up produced the intended result.

The final test completed in 47.922 seconds with `1/1` test methods passed and
six attachments. Visual inspection of all three `attempt-N-after` images proved
the Video Options panel, `Optimal` profile and read-only `1864×860` value on
every launch. The result bundle is
`/tmp/OpenXRayUIAutomation-options-3x-confirm.xcresult`. This closes the three
consecutive Main Menu to Options branch of IOS-P1-009; the repeatable
whole-device Metal System Trace requirement had already passed on this host.

Teardown terminated the game after the third attempt and the final process
query again found no OpenXRay process. After the user ended the phone window,
only exported local PNGs and documentation were read. No further device command
was issued. Nothing was committed or pushed.

## 2026-08-04 — bounded device tooling and lifecycle-oracle preparation

### Phone-free implementation and proof

Cable input now has a per-request UUID and atomic ACK. Key ACK follows dispatch;
tap ACK follows its complete move/press/release sequence. Diagnostic autoinput is
separate from framebuffer readback, so repeatable routes can run with
`ios_diagnostics 0` and `ios_autoinput 1`. Pending triggers are removed at both
lifecycle boundaries and tombstoned before launch. An active synthetic hold
retains its UUID; the old generic receiver marker was replaced by a marker after
`CLevel` actually dispatches release to the current entity.

`held_input_lifecycle_test.sh` prepares a device oracle that starts a 30-second
W hold, backgrounds the game, injects a deliberately stale request while the
game is suspended, foregrounds it and accepts only a continuous log proving
entity release, UUID-correlated cancellation, drawable recovery and no replay.
Positive and replay-negative host fixtures pass. This behavior is not yet
device-proven.

The Activity Monitor parser now rejects non-finite timestamps/PIDs/footprints,
too few post-warmup samples and excessive adjacent sample gaps. A synthetic
two-sample, 1,800-second trace correctly fails. The 30-minute baseline therefore
requires at least 1,500 samples and a maximum five-second gap in addition to its
duration, PID, footprint and growth limits. `traverse.sh` now reports only
accepted commands; without actor position/sector evidence it does not claim a
completed traversal.

All repository device tools now acquire the shared iPhone lease immediately
before their first device command, reuse an outer lease only through its exact
token and release only ownership they acquired. External device commands run
under process-group wall-clock timeouts. Isolated free/busy, exact-token,
wrong-owner, timeout, SIGTERM cleanup and busy-before-device checks pass.
Worst-case polling remains inside each requested lease. A follow-up read-only
review returned no P0/P1. The signed generic-device XCUITest build also passes.

The final uncached Release gate passed graphics/lifecycle/texture/UI policies,
279/279 shader stages, 2/2 low-settings, 6/6 SSAO plus value-macro, 137/137 links
and rebuilt three translation units. App and full dSYM share UUID
`D8A842FD-A5ED-3AA6-B1AA-FFFCE67FCA9B`; dSYM `__debug_info` is 513,394,826 bytes.

### Bounded phone attempt and evidence boundary

A five-minute OpenXRay lease was acquired only immediately before installing
FastDevice UUID `9982376B-3039-3B76-A681-611A2FDE28E0`. Signing and in-place
installation under the stable bundle identifier succeeded, preserving the
retail data container. The following `devicectl` read of `user.ltx` did not
return, so the command was interrupted before launch and the exact-token lease
was released immediately. No runtime, lifecycle, input, visual or save result
is claimed from that attempt. Timeout enforcement was added and locally proved
before any retry. The coordinator later reported an active OpenGothic lease, so
no further phone command was issued. Nothing was committed or pushed.

## 2026-08-08 — held-input evidence correction and reliable lifecycle/audio harness

### Corrected device evidence from the bounded 2026-08-04 run

The later held-input retry did reach the device and supersedes the earlier
"not yet device-proven" status in this Journal. One continuous engine log
accepted UUID `a4b36864-2482-47d6-83ce-cd268f697a6c` as a 30,000 ms W hold
(scancode 26), dispatched keyboard release to the current entity, cancelled the
same UUID/scancode at lifecycle transition, restored the 1864x860 drawable and
discarded the suspended trigger. No second press/hold for that request appeared
after cancellation. This proves diagnostic-key cancellation and stale-trigger
non-replay; it does not prove held touch or lock-screen behavior.

The subsequent combined XCUITest produced five Home-state assertions and one
Siri-state assertion because neither API displaced OpenXRay as the harness
assumed. Only one new deactivate/activate/drawable triplet and no ordered audio
interruption pair appeared. That run is rejected as lifecycle/audio evidence;
the failure belonged to the harness, not to a demonstrated engine regression.

### Phone-free correction and validation

The lifecycle scenario now performs five fail-fast switches through Safari,
requiring Safari foreground, OpenXRay background/suspended without termination,
then OpenXRay foreground and a recovery screenshot. The audio scenario uses a
non-mixable `Playback` AVAudioSession plus looping nonzero PCM in the XCTest
runner while OpenXRay remains foreground. Engine begin-interruption diagnostics
now include `wasSuspended=0/1` without changing behavior.

The shell oracle is factored into a reusable source and has twelve durable
positive/negative fixtures. It ignores complete suspension-origin pairs,
requires the target foreground-audio pair after five lifecycle groups in the
combined mode, rejects deactivation between target begin/end and cannot pass on
suspended-only evidence. The UI checker now strips C++ and Lua comments, scopes
checks to active functions/branches, treats the iOS product macro as true and
rejects nested `#if 0` or outer `#else` markers. Its twelve mutation fixtures
pass.

Local validation passed `prepare_scheme` 2/2, UI fixtures 12/12, log-oracle
fixtures 12/12, real UI contract, Bash/ShellCheck, signed generic-device
XCUITest build and AVFAudio linkage. FastDevice passed all policy/UI gates,
279/279 shaders, 2/2 low settings, 6/6 SSAO plus value-macro and 137/137 links;
UUID is `A13C4DCA-971F-3BA8-B8DB-7290E2B12C44`. The final uncached Release gate
passed the same contracts with app/dSYM UUID
`9ECD525A-FC14-3D68-ADFD-7BBB086A2C5A` and 513,394,876 bytes of `__debug_info`.
Independent final review returned `APPROVE — brak P0/P1/P2`.

No phone, installation, container, certificate, commit or push action occurred
in this correction. Five real Safari cycles, one foreground audio interruption,
lock/unlock and held touch remain device-pending.

## 2026-08-08 — Activity Monitor parser regression contract

The phone-free 30-minute-soak parser now rejects non-finite or negative
timestamps/footprints, non-positive or fractional PIDs, duplicate schema
columns/IDs, malformed rows and invalid, dangling or cyclic shared XML
references. Optional CPU sentinel and non-finite values remain ignorable rather
than invalidating otherwise usable memory samples. Valid rows are sorted before
warmup and gap/growth analysis; the CLI retains exit 0 for PASS, 1 for unmet
budgets/no usable samples and 2 for malformed input or output I/O failure.

Twenty subprocess fixtures cover shared refs, stable JSON, unordered input,
partial/all warmup, no matching process, schema/ref failures, multi-PID
continuity, sample/duration/gap limits and alias, footprint/growth limits,
numeric validation, CPU omission and JSON write failure. The suite is part of
`build_check.sh` and both parser and tests are gate-hash inputs.

FastDevice and the final uncached Release gate passed the parser alongside all
existing policies, twelve UI fixtures, twelve lifecycle/audio-oracle fixtures,
279/279 shaders, 2/2 low settings, 6/6 SSAO plus value-macro and 137/137 links.
No engine unit rebuilt; FastDevice UUID remains
`A13C4DCA-971F-3BA8-B8DB-7290E2B12C44`, while Release app/dSYM remains
`9ECD525A-FC14-3D68-ADFD-7BBB086A2C5A` with 513,394,876 bytes of `__debug_info`.
Final review returned `APPROVE — brak P0/P1/P2`.

No phone, trace, install, commit or push occurred. This closes parser
trustworthiness only; the real 1,700-second/1,500-sample device budget remains
open.

## 2026-08-08 — graphics-profile policy eligibility contract

The deterministic Optimal-profile policy suite now covers strict boundaries at
24, 31 and 40 ms, complete Quality-to-Performance-to-Quality traversal,
neutral-window reset, cooldown boundaries, invalid timing, suspend/warmup,
30/60/120 Hz, uneven cadence and the bounded 512-sample window. Strict compiler
warnings and ASan/UBSan pass.

The suite exposed a real policy defect: fast windows accumulated while
`allowUpgrade` was false, so lifting a thermal or power constraint could reuse
blocked headroom. The first correction cleared completed blocked windows, but
independent review found that one mixed false-to-true window could still count.
The final policy marks a complete window ineligible if any admitted sample was
blocked, clears prior fast hysteresis while blocked and still permits slow
windows to downgrade. Reset and Suspend restore eligibility. Mixed-window
positive and downgrade-negative fixtures prove the corrected boundary.

FastDevice passed with UUID `259BB7C3-9EDB-327F-BCD3-17958E19B648`. The final
uncached Release gate passed all local policy/UI/parser contracts, 279/279
shader stages, 2/2 low settings, 6/6 SSAO plus value-macro and 137/137 links.
App and full dSYM share UUID `249506E5-1364-3725-987B-9B5B53D17319`;
`__debug_info` is 513,394,901 bytes. Independent corrected-state review returned
`APPROVE — brak P0/P1/P2`.

No phone, install, runtime capture, container, certificate, commit or push
action occurred. Actual pacing, thermals and measured transitions remain
device-pending.

## 2026-08-08 — numeric shader-macro regression ledger

The offline shader checker now treats `SUN_QUALITY`, `SSR_QUALITY`,
`SSAO_QUALITY`, `SSAO_OPT_DATA` and `MSAA_SAMPLES` as an exact numeric-feature
manifest. It requires five complete `COMMON_H`-guarded zero fallbacks, applies
backslash-newline splicing before comment removal and rejects new presence uses.
The legacy ledger freezes eight presence tests across SSR/HBAO/HDAO sources and
one existing `#undef SSAO_QUALITY` in `combine_1.ps`; additions, removals,
changes or relocation fail until explicitly reviewed.

Thirty subprocess mutations cover active SSAO/SSR/MSAA/sun uses, missing,
wrong, duplicate, nested and inactive fallbacks, comments and multiline
directives, intentional presence macros and both debt ledgers. The contract is
executed before the shader cache and belongs to compile/link and artifact hash
inputs. Several independent reviews found and closed broad `common.h` guard,
inactive-parent, `#undef`-scope and comment-splicing false passes. Final review
returned `APPROVE — brak P0/P1/P2`.

No shader, emitter or runtime behavior changed. HBAO, HDAO, alternate SSR and
MSAA remain disabled/deferred; this is a static regression boundary, not device
rendering evidence.

## 2026-08-08 — deterministic startup-sector fallback policy

The existing iOS nearest-floor fallback was extracted without changing its CDB
callback or search behavior. The pure policy preserves exact-query bypass,
seven radii, eight independently asserted directions, the 0.70710678 diagonal,
unchanged `y`, first valid hit and no-hit invalid metadata. A separate pure
commit policy owns notification and `last_sector_id`: invalid results cannot
notify or replace the previous sector, unchanged valid results do not notify,
and changed results notify before assignment.

Strict warning and ASan/UBSan host tests pass. The review rejected a fragile
source-text guard assertion, so it was removed rather than expanded into a C++
preprocessor parser. The final direct policy tests and iOS caller received
`APPROVE — brak P0/P1/P2`. CDB, portal detection, full prefetch and non-iOS
behavior were not changed.

FastDevice passed all current contracts and rebuilt one translation unit; UUID
is `FF0304BE-A014-3DE5-AE81-36C825248495`. The full uncached Release gate passed
the sector/profile/lifecycle/texture policies, twelve UI fixtures, twelve
lifecycle/audio log fixtures, twenty Activity Monitor fixtures, thirty macro
mutations, 279/279 shader stages, 2/2 low settings, 6/6 SSAO plus value-macro and
137/137 links. App and full dSYM share UUID
`3EBCCFF7-45FC-3B4C-9438-EF190721CF73`; `__debug_info` is 513,395,574 bytes.

No phone, install, launch, container, certificate, commit or push action
occurred. Additional outdoor/indoor/portal saves, reload and level transition
remain device-pending.

## 2026-08-08 — stable inventory focus geometry and exact nested clipping

The phone-free audit of IOS-P1-006 first rejected the existing extraction. Its
top-first `floor`/bottom `ceil` delta could alternate by one pixel for a
fractional cell near viewport height and necessarily oscillated for a cell
taller than the viewport, causing repeated `OnScrollV` and cursor warps. The
focus overlay also reconstructed a `CUIScrollView` from its full absolute rect,
while the real draw scissor removes `m_upIndent` and `m_downIndent`; retail XML
contains nonzero top indents.

`RequestedVerticalScroll` now recovers invariant content-space item edges and
computes the complete feasible integer-scroll interval. It retains an already
valid position, selects the nearest bound when the interval is feasible and
uses deterministic top alignment when no integer can expose the whole cell.
Non-finite geometry retains the current scroll and out-of-range finite geometry
saturates before conversion. Multi-update tests apply the returned scroll to
the item rect and prove a fixed point after normal and externally clamped
scrollbar movement.

`CUIScrollView::GetDrawClipRect` now owns the exact absolute rect plus vertical
indent contraction. Both `CUIScrollView::Draw` and the iOS focus overlay consume
that helper; drag-drop parents retain `GetClientArea` and list parents retain
their absolute rect. Nested clipping still matches `Frect::intersection`,
including legal zero-extent edge contact.

Strict warning and ASan/UBSan policy runs pass. The UI checker now requires
exact viewport/item and clip-field mappings, the assigned `AddClip` result,
immediate invisible return, shared ScrollView clip consumption, scissor draw
order and active iOS code. Thirty-two positive/negative fixtures cover comments,
inactive preprocessor branches, runtime `if(false)`/`if(0)`, detached results,
swapped fields, scroll/warp order, lost indents and divergent scissor input.

FastDevice passed every current policy/parser/UI contract, 279/279 shader
stages, low-settings 2/2, SSAO 6/6 plus value/numeric macro contracts and
137/137 links; 1,068 translation units rebuilt and UUID is
`493EFBB7-A613-3DD7-B906-065EDCB04BE8`. The full uncached Release gate rebuilt
1,068 units and passed the same contracts. App and full dSYM share UUID
`72D6778E-D1AB-370B-B83A-67B823B94B83`; `__debug_info` is 513,399,145 bytes.
Final independent review returned `APPROVE — brak P0/P1/P2`.

No phone, Simulator, install, launch, container, signing, commit or push action
occurred. This closes the local geometry mechanism only; visual auto-scroll and
focus clipping in a dense overfilled inventory remain device-pending.

## 2026-08-08 — isolated retail iOS 26.5 Simulator checkpoint

The phone-free workflow now creates a unique external work root, copies the
current dirty source tree and Simulator dependency prefix without `.git`,
`.Codex`, `build*` or `bin`, relocates copied pkg-config metadata and builds only
inside that snapshot. It requires an exact arm64 `IOSSIMULATOR` Mach-O with
minOS 16.4, the iOS 26.5 runtime and a dedicated iPhone 15 Pro Max Simulator.
It validates the external backup manifest before use, rejects links and unsafe
paths, stages only resources, levels, localization, patches and opt-in saves,
then compares every staged file after runtime. Repository state, retail backup,
device/FastDevice build trees, bundles, dependency prefix and five gate stamps
are fully manifested before the run and must remain unchanged.

The first complete run reached the textured main menu but exposed an evidence
defect: `simctl launch --console` forwards signals to the application, so the
harness's own `process.terminate()` appended a post-screenshot `FATAL` with
exit code 3. The corrected launcher uses `--stdout`/`--stderr`, accepts only the
documented single `bundle: PID` result and observes that exact PID immediately,
before and after the screenshot and throughout a bounded stability interval.
It never signals the game from Python; shell cleanup owns only the dedicated
Simulator. Thirty-one deterministic positive/negative tests cover isolation,
manifests, malformed launch output, immediate and deferred process death,
screenshot failure, runtime archive mutation and cleanup failure.

The corrected real run at
`/Users/patryk/openxray-handoff/simulator-work-20260808-200113-29007` passed. It
verified the 789-file, 4,761,053,330-byte retail backup and all seven required
`resources.db0-4`/`levels.db0-1` hashes, mounted 12 archives, loaded
`gamedata/configs/system.ltx`, emitted a later `Starting engine...`, retained a
live process through the screenshot and rendered the full main menu. The engine
reported a 1864x860 drawable and decoded both menu videos. Corrected logs contain
no `FATAL`, termination exit or stack trace. The dedicated Simulator UUID
`3A9BD583-C18F-4F6A-B8A8-C74CA6716901` was deleted and no `OpenXRay Retail`
device remains.

FastDevice and the final full uncached Release gate passed the 31 Simulator
fixtures, all existing policy/parser/UI contracts, 279/279 shader stages, 2/2
low settings, 6/6 SSAO branches plus numeric macros and 137/137 links. No engine
translation unit rebuilt. FastDevice UUID remains
`493EFBB7-A613-3DD7-B906-065EDCB04BE8`; Release/dSYM UUID remains
`72D6778E-D1AB-370B-B83A-67B823B94B83` with 513,399,145 bytes of `__debug_info`.
Sol xhigh returned `APPROVE — brak P0/P1/P2`.

No phone, device lease, `devicectl`, signing, install, commit or push action
occurred in this runtime slice. The screenshot proves only the iOS 26.5 Apple
Software Renderer retail/menu boundary; physical-device rendering, gameplay,
HUD, dense inventory and PDA acceptance remain unchanged.

## 2026-08-08 — Simulator saved-game autoload and Locator path-length correction

The first 300-second exact-save Simulator run disproved archive corruption but
did not complete the runtime oracle: it mounted 12 archives and cached 39,270
files, loaded `mobile user - beginning of the game`, accepted the client and
loaded Zaton HOM. It timed out before `End of synchronization`/`after_load`;
the process was still live and working. Cleanup and the backup-integrity checks
passed. This was partial progress, not a runtime checkpoint.

The missing motion-file failure had a concrete shared Apple cause. In
`CLocatorAPI::Register`, `string256` could not represent a Simulator path of
exactly 256 bytes without its NUL. `xr_strcpy_s` returned `ERANGE` and cleared
the destination, allowing long archive entries to collapse to an empty lookup
key. The physical-app root is shorter and did not trigger the boundary. The
correction uses `string_path`, rejects an empty source name and checks the copy
result with fail-fast reporting. It deliberately has no Simulator-only branch.
The prior broken cache had 24,075 entries; after the correction it has 39,270.

`test_locator_registration_contract.py` provides eight mutation-backed host
checks. The retail workflow suite has 41 checks after naming the runtime
boundary `saved_game_sync_complete`; FastDevice passed with UUID
`470F9977-D7B8-355C-837B-63569A7F49B1`. The new Locator contract is included in
artifact inputs, so the matching final `build_check.sh --full` was intentionally
left open rather than inherited from an earlier gate.

The extended real run in
`/Users/patryk/openxray-handoff/simulator-work-20260808-220209-88863` passed the
saved-game oracle: 39,270 files/12 archives, the exact selected save, client
acceptance, Zaton HOM, `End of synchronization A[1] R[1]`, and
`iOS memory after_load phys_current=3575125 K phys_peak=3588309 K`. The selected
save remained 631,235 bytes with SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`; protected
inputs were unchanged and the dedicated Simulator was deleted. The 45-entry
difference from the 39,315-entry physical cache is expected: fresh allowlisted
staging includes only the selected `_appdata_` save and intentionally excludes
device-generated cache, configuration and diagnostics. It is not a retail
archive deficit.

The retained screenshot shows a loading screen, not a presented world. This
proves exact saved-game load through synchronization and `after_load` under the
Apple Software Renderer only. It does not prove rendered pixels, UI
correctness, a presented 3D world, physical-device rendering or iPhone
performance. No phone, device lease, `devicectl`, signing, install, commit or
push action occurred.

The subsequent final uncached gate passed all host contracts, 279/279 shader
stages, 2/2 low-settings variants, 6/6 SSAO branches plus value macros and
137/137 links. One translation unit rebuilt. Release and full dSYM share UUID
`21A8EC49-8ED7-3494-B47A-6E8588DF6913`; `__debug_info` is 513,400,299 bytes.
Independent final review remains open at this evidence point.

## 2026-08-08 — documentation correction after Locator/autoload review

The subsequent final review did not approve the checkpoint: it reported two P1
and two P2 findings. This append-only correction fixes the P2 record only; the
P1 code/test work, a repeated final gate and a new final review remain open.

The current full-gate artifact is not the older `72D6778E-D1AB-370B-B83A-67B823B94B83`
artifact mentioned in earlier entries. The later pre-P1-follow-up Release/full-dSYM
stamp is `21A8EC49-8ED7-3494-B47A-6E8588DF6913` with 513,400,299 bytes of
`__debug_info`.

The extended run's raw `runtime-proof.txt` retains the historical
`saved_game_world_ready` label because the rename occurred after that run. The
current workflow code and tests use `saved_game_sync_complete`; the raw evidence
was not rewritten. Its retained screenshot remains loading-only, hence does not
prove a presented world, pixels, UI, device rendering or performance.

The exact 45-entry difference between physical 39,315 and staged 39,270 is 34
`bin` files, seven `_appdata_` files, three diagnostic files and the separate
`gamedata/shaders/gl/hud_default.s` overlay. This still excludes a deficit in
the 12 retail archives, but the HUD overlay must not be folded into a generic
cache/configuration explanation.

## 2026-08-09 — Locator/autoload P1 hardening follow-up

This entry preserves the earlier rejected-review facts. The first final Sol
xhigh review reported two P1 and two P2 findings; the P2 documentation was then
corrected. Sol medium pre-review subsequently found a post-finalization gap plus
mutable-save semantics weakness, followed by a symlink-resolution bypass. Both
follow-up series were corrected. The latest Sol medium verdict is
`PRE-REVIEW CLEAR — brak P0/P1/P2`; this is not final approval, and formal Sol
xhigh re-review remains open.

Both original P1 mechanisms are now hardened. `large-files-sha256.tsv` is a
mandatory parsed manifest whose paths, sizes and SHA-256 values must agree with
`files.tsv`; every allowlisted large file is protected at preflight, staging
and post-runtime. The selected mutable large save is the only exception and
must remain a nonempty regular file. The log oracle is now two-stage:
`launch-proof` writes a fresh snapshot manifest but no `runtime-proof.txt`;
after successful `simctl terminate`, `finalize-log` operates without path
resolution and verifies `lstat`, `O_NOFOLLOW`, pre/post `fstat` and final
`lstat`, inode identity, captured-prefix size and SHA-256, rotation, truncation,
rewrite, symlink substitution, the complete required sequence and failure
markers. Only then may it write `runtime-proof.txt` and `report.txt`.

The hardened host suite passes 54/54: the main independent run took 67.306
seconds and the full-gate run 68.359 seconds. The Locator suite remains 8/8.
FastDevice passed with zero rebuilt translation units and unchanged UUID
`470F9977-D7B8-355C-837B-63569A7F49B1`.

The new real run at
`/Users/patryk/openxray-handoff/simulator-work-20260808-230615-67616` reports
PASS with `runtime_boundary=saved_game_sync_complete`, level `zaton`, 39,270
cached files and 12 archives, the exact save, client acceptance and HOM load.
`End of synchronization A[1] R[1]` is at line 641; `after_load` is at line 645
with `phys_current=3588549 K` and `phys_peak=3593573 K`. The post-stop snapshot
is version 1, 27,244 bytes, SHA-256
`b05f714c7efac4bcfa17a877ea19964b0eaf684bc6a8a18bbc790156f22fe937`.
The selected save remains 631,235 bytes and SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`.
Protected inputs remain unchanged; the dedicated Simulator beginning `FA337`
was deleted and its directory is absent.

The repeated `build_check.sh --full` passed 54/54 plus every host contract,
279/279 shader stages, 2/2 low-settings stages, 6/6 SSAO branches plus value
checks, 137/137 links and zero rebuilt translation units. Release and full dSYM
remain UUID `21A8EC49-8ED7-3494-B47A-6E8588DF6913`; `__debug_info` remains
513,400,299 bytes and source stamp `4b79402e…` matches the current tree.

The retained screenshot is still loading-only: this does not prove a presented
world, pixels, UI correctness, physical-device rendering or performance. No
phone, `devicectl`, device lease, signing, install, commit or push was used.

## 2026-08-09 — Locator/autoload naming and source-stamp correction

The formal Sol xhigh review of the hardened checkpoint found one P2 and no P0,
P1 or other P2: the `4b79402e…` source stamp recorded in current documentation
became stale after two naming-only test changes. The identifiers changed from
`world_proof` to `sync_complete_proof` and from `dynamic_world_markers` to
`ordered_sync_complete_markers`. The suite remains 54/54 PASS; these changes do
not alter behavior and require no new real Simulator run.

The current full stamp was independently recomputed and matches:

- `source_sha256=543342523068fd3d2edc02f3ee31e95a3d4537488aa7c305a99f99a6319f414e`;
- `app_uuid=21A8EC49-8ED7-3494-B47A-6E8588DF6913`;
- `bundle_sha256=4275267b15bd0689a30145bca3b2dd9974b2211ce8bd4b95a0b461dfe19804b7`;
- `minos=16.4`, `shader_cache=forced-off`;
- dSYM `__debug_info=513400299` bytes.

This documentation corrects that sole P2, but the repeated formal verdict is
still pending and no approval is claimed. The runtime boundary remains
`saved_game_sync_complete`; the retained evidence remains loading-only and does
not prove a presented world, pixels, UI correctness, physical-device rendering
or performance. No build, Simulator, phone, commit or push was used for this
documentation correction.

## 2026-08-09 — LocatorAPI/autoload final closeout

After the source-stamp documentation correction, the repeated Sol xhigh verdict
is exactly `APPROVE — brak P0/P1/P2`. This closes the local LocatorAPI/autoload
checkpoint. The next phone-free UI slice will emit semantic state only after
`DoRenderDialogs()` and prove `world -> inventory -> world -> pda_tasks ->
world -> pda_map -> world` in an isolated Simulator. That future result can
prove navigation and render-path execution, not pixels, physical-device
rendering or performance. No build, Simulator, phone, commit or push was used
for this documentation closeout.

## 2026-08-09 — semantic UI navigation / CoP PDA map-hotkey closeout

The final Sol xhigh verdict is exactly `APPROVE — brak P0/P1/P2`. The marker is
iOS/autoinput-only and is emitted after `DoRenderDialogs()` only on an actual
semantic transition, with exact PID, sequence, frame and state. This is a
semantic-ui-navigation checkpoint only; it is not pixel, readability,
performance or physical-device proof.

The first semantic run showed that the previous five-second per-step timeout
was too short because inventory parsing exceeded it. The harness was corrected
to 60 seconds for each of seven steps plus 30 seconds overhead (450 seconds)
and now refreshes its log on failure. The second run proved the `I, I, P,
Escape` prefix but `M` reopened `pda_tasks`. That was the retail topology, not a
marker defect: CoP retail has no standalone `eptMap`; `eptTasks` is the combined
Tasks/Map surface. This explicitly corrects the older future prediction of a
separate `pda_map` state. `Show_MapWnd(true)` prefers standalone `eptMap` where
another topology provides one, otherwise `eptTasks` and synchronizes the active
dialog/tab; `false` or neither is a no-op.

The final real isolated iOS 26.5 Simulator run in
`/Users/patryk/openxray-handoff/simulator-work-20260809-013601-65931` is PASS;
Simulator `410EA3BC-23FC-4F4C-843D-CF4438704652` was deleted. PID `72114`
records `1/34/world`, `2/92/inventory`, `3/94/world`, `4/96/pda_tasks`,
`5/98/other`, `6/100/world`, `7/102/pda_tasks`, `8/104/world` for controller
`I, I, P, E, Escape, M, Escape`. The deliberate `E` proves `M` does not inherit
previous Tasks state. Sync was 337327 ms; after load physical footprint was
3559173 K and textures 2206009 K. The save remains 631235 bytes, SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`; protected
inputs are unchanged and cleanup deleted the dedicated Simulator. Binding JSON
records `final_log_sha256=638434c1b8b745d7d7002237eed356144b893e823ddbce28e571b8f34dba9d96`
and exact scope `semantic-ui-navigation-only`.

Host gates passed: marker 15/15, PDA map-hotkey mutation contract 6/6,
navigation 28/28, retail isolation 61/61, shaders 279/279, low 2/2, SSAO 6/6,
numeric macros 30/30 and links 137/137. FastDevice PASS UUID is
`8C52017C-119B-3800-A9E5-8335EB9E3574`. The final uncached
`build_check.sh --full` PASS rebuilt two TUs; Release/dSYM UUID
`B2ACA614-EC54-30A1-9607-9DD84AEB00BC`, dSYM `__debug_info` 513401386 bytes,
`source_sha256=4319ce67726cf8ecafc9992329d5f8c4ab66cfce811d7f79700f5d70d44b3b82`,
`bundle_sha256=7828329ff93ec4665bb82c9a4c1874cd2ef998250ff9e3d9ec630d878b8aa6e9`,
platform `IOS`, minOS 16.4, shader cache forced off. After this run only the
aggregate scope wording and its host assertion in `report.txt` changed to the
approved exact scope. The final reviewer decided no real rerun was necessary:
binding JSON already had that exact scope and the runtime mechanism was
unchanged. No physical phone, device lease, `devicectl`, signing, install,
commit or push occurred.

## 2026-08-09 — IOS-P2-002 OpenAL Soft local technical approval

IOS-P2-002 now intentionally selects the project-owned static OpenAL Soft
1.25.2 archive from the exact device or Simulator dependency prefix. The local
provider contract requires the exact CMake, archive, header and strong
AudioToolbox/CoreFoundation/CoreAudio framework relationship. It rejects Apple
OpenAL, dylibs, `-lopenal`, `-latomic`, alternate or duplicate archives,
forwarded linker spellings and response files. The runtime record is exactly
vendor `OpenAL Community`, renderer `OpenAL Soft`, version
`1.1 ALSOFT 1.25.2`, with extension, pause and resume support `1/1/1`.

The initial whole-checkpoint review found a P1 scene-replacement/ABA resume
failure and two P2s: linker-form bypasses and omission of the interruption test
from the gate hash. The fixes replaced global interruption state with a
per-Core exact-scene registry, hardened the fail-closed linker parser and added
the policy test to the artifact inputs. The initial `3dd…` stamps were thereby
invalidated. The corrected code review and final technical-evidence review each
ended exactly `APPROVE — brak P0/P1/P2`.

Local policy tests cover empty begin, destroyed A followed by B including ABA
address reuse, surviving and pre-paused scenes, and duplicate begin/end; strict
warnings and ASan/UBSan pass. FastDevice then passed with 1,485 translation
units, UUID `C7FDF518-AF64-31C7-92E4-3FA8C3B9C977`, source SHA-256
`bfc46e7f3c53b62cbf7e6157378fad589790f9cc4fe40b7f9a2b133e38e73345` and bundle
SHA-256 `5c0a6ebc5f730adaf14e4381e7cb4aff17ff0dd939ec985958a49b0a3d9d9e8b`.

The isolated iOS 26.5 Simulator run in
`/Users/patryk/openxray-handoff/simulator-work-20260809-113846-78853` passed:
it reached the menu after `system.ltx`, preserved protected inputs, emitted the
exact provider line and retained screenshot `a77f17ca…`. Dedicated Simulator
`67FD36F1-7888-4C9A-8851-3AB745A7A283` was deleted. This proves provider/menu
only, not a physical interruption.

The final uncached full gate passed with 1,485 translation units, 279/279
shader stages, low 2/2, SSAO 6/6, numeric macros 30/30, links 137/137 and
retail isolation 62/62. Release/full-dSYM UUID is
`54A5DF59-2A42-3676-BC22-94F01F96A724`; `__debug_info` is 513404887 bytes;
source SHA-256 is
`bfc46e7f3c53b62cbf7e6157378fad589790f9cc4fe40b7f9a2b133e38e73345`; bundle
SHA-256 is `53431d3d67f59a0e9a4ae1465db8100ab24c7c39113b4f1a853795dac10a365d`;
provider SHA-256 is
`86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914`.
Platform is IOS, minOS 16.4 and shader cache is forced off. No source edits
followed that technical evidence.

No phone, lease, `devicectl`, signing, install, commit or push occurred in this
checkpoint. Physical iPhone audio-interruption recovery remains pending under
IOS-P1-002 and IOS-P2-002.

## 2026-08-09 — IOS-P1-010 UIKit UIScene local/Simulator closeout

The repository now applies a hash-pinned, fail-closed SDL2 2.32.10 UIKit
scene-lifecycle backport. The manifest declares one scene with
`UIApplicationSupportsMultipleScenes=false`; `SDLUIKitSceneDelegate` owns the
four active/background transitions; `SDL_main` starts once; and iOS 13+ window
creation uses a connected `UIWindowScene`.

The first iOS 26.5 retail run in
`/Users/patryk/openxray-handoff/simulator-work-20260809-131233-70026` did not
establish a product regression. Its oracle sent the Safari and OpenXRay
foreground commands without a confirmed background handshake, then its Boolean
parser rejected the valid transitional prefix containing only `deactivate`.
The harness was corrected to a fail-closed two-phase protocol anchored to the
pre-cycle `activate`: wait for exactly one later `deactivate`, only then request
OpenXRay foreground, then require exactly one later `activate` for the same
PID. The deterministic oracle passed 74/74, including split polling, delayed
events, wrong PID, duplicate/order and timeout mutations.

The corrected isolated retail runs passed on iOS 26.5 in
`/Users/patryk/openxray-handoff/simulator-work-20260809-135908-18426` (PID
25991) and iOS 27.0 in
`/Users/patryk/openxray-handoff/simulator-work-20260809-140608-26903` (PID
35981). Each recorded one engine start, one menu frame and ordered lifecycle
markers `1 activate`, `2 deactivate`, `3 activate`; protected inputs were
unchanged and cleanup deleted the dedicated Simulator.

The authoritative uncached full gate rebuilt four TUs and passed SDL scene 7/7,
lifecycle marker 10/10, retail oracle 74/74, shaders 279/279, low 2/2, SSAO
6/6, numeric macros 30/30 and links 137/137. It records platform IOS, minOS
16.4, forced-off shader cache, 513405098-byte `__debug_info`,
`source_sha256=00270cdb45ad8bc0c95b7770151a4d0b9443977c2c4c26d122bb5b4ca688dcd3`,
app UUID `341855E0-0560-3C8B-9D7E-115F941DCAA6`, bundle SHA-256
`48fef93d74af38d769de6742ce95bb00ca23532f14d7db2b3b72ae3dc0f064fa` and OpenAL
SHA-256 `86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914`.
The final Sol xhigh verdict is exactly `APPROVE — brak P0/P1/P2`.

This closes only the local/Simulator UIScene checkpoint: it proves bootstrap,
the code-level rendered-menu boundary and one same-PID recovery cycle. It does
not prove pixels/readability, physical device, performance, audio interruption
or multi-cycle soak. No phone, lease, `devicectl`, signing, install, commit or
push occurred.

## 2026-08-09 — IOS-P0-003 startup-sector v1 evidence-oracle local closeout

The new iOS startup-sector v1 evidence state covers both `level_load` and
`QuickLoad` epochs. Its terminal classifications are exactly `exact`,
`fallback`, `retained`, `unresolved` and `recovered`. The state is two-phase:
`Prepare`, fully validate/build the payload, `CommitPrepared`, `Msg`, then
`FlushLog`. An invalid payload remains pending for retry and does not consume
state.

For QuickLoad, `retained`/`none` can be emitted only after a post-epoch
`CCameraManager::ApplyDevice` generation. `retained` means only that the prior
valid `last_sector_id` was used after an authoritative camera application; it
does not mean fresh sector detection or prove that the new save/location is
correct.

The oracle CLI mandatorily requires `--expected-pid`, `--after-epoch` and
repeated `--expect-trigger`. It enforces exact anchored epoch suffix/count/order
and rejects a missing final outcome, gaps, returns, extras, wrong trigger,
malformed near-prefix, impossible fallback geometry and unsafe/symlink I/O. New
output uses `O_NOFOLLOW|O_EXCL`, mode `0600`, never overwrites or deletes another
path, and a failed write may leave its own fresh partial output fail-closed.
JSON `PASS` means only that the expected marker stream is structurally complete.
`classification=unresolved` is a failed device startup test; even
`exact`/`fallback`/`retained` markers are sector evidence, not visual or pixel
proof.

Host evidence passed: parser 17/17; source-mutation contract 9/9; strict C++
policy PASS; ASan/UBSan PASS. The partial engine gate rebuilt 1,383 TUs; full
dSYM `__debug_info` is 513539650 and UUID is
`8D623468-0A50-3055-9516-3EA703818076` (log
`/Users/patryk/openxray-handoff/local-gates/ios-p0-003-engine-20260809.log`).

Independent Sol xhigh implementation review first rejected the slice with 3 P1
and 3 P2 findings. Those findings were fixed. A later review rejected 1 P1 and
2 P2 findings; those corrections were also applied. The final independent code
re-review returned exactly `APPROVE — brak P0/P1/P2`.

The final full uncached gate passed after the Python corrections. It rebuilt 0
TUs because the matching C++ engine already existed; retail 74/74, numeric
macros 30/30, low 2/2, SSAO 6/6, shader contract 279/279 and links 137/137 all
passed. Platform is `IOS`, minOS 16.4 and shader cache is forced off. The
authoritative full stamp is:

```text
source_sha256=b00f2aec6cdfd1ab97a7e403a251cd86982a1df6b650fea58f5b7269f85fb02e
app_uuid=8D623468-0A50-3055-9516-3EA703818076
bundle_sha256=c214839a7d3e418533a0fdfeefda19ee4da7f06fbd5589db1ee3adfae1c2bc99
openal_provider=OpenALSoft-1.25.2-static
openal_sha256=86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914
__debug_info=513539650
log=/Users/patryk/openxray-handoff/local-gates/ios-p0-003-full-20260809.log
```

The main worker independently recomputed the current gate hash and it exactly
matched the stamp; scoped `git diff --check` passed. This does not close
IOS-P0-003: future short phone tests first record their completed baseline epoch
before each exact expected batch and separately require resolved classification
plus an objective world frame. Another outdoor save, indoor/portal,
QuickLoad/transition and device visual proof remain pending. No phone, lease,
`devicectl`, Simulator, install, signing, commit or push occurred.

## 2026-08-09 — IOS-P2-004 local installer/preflight closeout

The installer now keeps Team `RMJWWPF379` and bundle
`io.github.tryk016.openxray.RMJWWPF379` fixed while accepting `--device` as
CLI > environment > default and an all-or-nothing absolute `--app`/`--stamp`
pair (CLI pair > environment pair). IPA, positional payloads, mixed/incomplete
pairs and `--fast` plus a custom pair fail closed. Gate stamps and every profile
candidate are private descriptor-relative snapshots; app directories are checked
before and after copy for symlinks, then the exact bundle hash is verified.

`--preflight` performs no renewal, lease or device command. It validates the
existing profile/keychain identity and all stamp, base/final identifier, team
and entitlement invariants, then signs and verifies a temporary app copy. The
hermetic contract passed 12/12; ShellCheck for both scripts, `bash -n`, no-write
compile and scoped diff check passed. After the explicit
`signed_team_identifier=""` lint correction, delta Sol xhigh review again
returned exactly `APPROVE — brak P0/P1/P2`.

One wrapper full-gate result was discarded because its zsh wrapper used readonly
`status`. The later `9edb...` rerun/preflight were superseded by that lint-only
source change and are not current. The authoritative rerun is
`/Users/patryk/openxray-handoff/local-gates/ios-p2-004-full-final-20260809.log`
(SHA-256 `359487733f1942549ccfc8419c3ba79b86827dd0582ef44715c55d385d08d62a`,
19,725 bytes): 0 TUs; retail 74/74, installer 12/12, numeric 30/30, low 2/2,
SSAO 6/6, shaders 279/279 and links 137/137; debug-info 513539650 and UUID
`8D623468-0A50-3055-9516-3EA703818076`. Its current source hash is
`f7ff08d783ea42772dd9b74184dde8d0cbb8e4075f4900137c5f158db1a71361`.

The final real no-device preflight log is
`/Users/patryk/openxray-handoff/local-gates/ios-p2-004-preflight-final-20260809.log`
(SHA-256 `2ef10f170e4cf418b78ebd5a928777f0d63699848eecbca52b9d4f8385a35128`,
456 bytes). It used an existing real profile/keychain identity; the temporary
app met the Designated Requirement, the nonexistent lock sentinel stayed absent,
WORK was cleaned and the source bundle hash remained unchanged. No lease,
`devicectl`, `xcodebuild`, renewal, install, launch, container action, phone or
Simulator was used. This closes only local tooling acceptance, not an install or
any on-device behavior.

## 2026-08-09 — IOS-P2-004 final-review dependency correction

Independent final closeout review found one P1: the artifact stamp covered
`install_device.sh` but not the production `device_lease.sh` helper or its
`command_timeout.py` wrapper. It also found two documentation P2s: the active
task Definition of Done unconditionally required a device even for host-only
tooling, and canonical open work still described the now-fixed installer as
configurable/team-aware work.

Both production helper paths are now `IOS_ARTIFACT_INPUTS`. The installer
contract's thirteenth hermetic test statically requires both inputs and mutates
each one in an isolated artifact tree; each mutation changes the
`ios-device-artifact-v2` digest and restoring the bytes restores the baseline.
The full contract passed 13/13. The Plan now requires device evidence only for
claims about installation, runtime, rendering or device behavior; host-only
tooling may close on a real preflight with its device boundary explicit. The
canonical open-work row now records the actual CI supply-chain debt.

The authoritative rerun is
`/Users/patryk/openxray-handoff/local-gates/ios-p2-004-full-reviewfix-20260809.log`
(SHA-256 `1b572e98948ba3b18cd8196801f33cd78b0f5d3daf0fb52eef6c581e1d123e41`,
19,881 bytes). It exited zero after retail 74/74, installer 13/13, numeric
macros 30/30, low 2/2, SSAO 6/6, shaders 279/279 and links 137/137; zero C++
translation units rebuilt, `__debug_info` remains 513,539,650 bytes and UUID
remains `8D623468-0A50-3055-9516-3EA703818076`. The current source hash and
full stamp both equal
`2d1cdfe589623819de98c6c9ac08465ddabc72b6ebabb154ade248a3b3191def`.

The corrected real preflight is
`/Users/patryk/openxray-handoff/local-gates/ios-p2-004-preflight-reviewfix-20260809.log`
(SHA-256 `eb23ec1d98d49daed8f0eee57e264b7d43734b1df5d07c3aea209d0c7557fa97`,
456 bytes). It exited zero, used the existing profile/keychain identity, left
the nonexistent lock sentinel absent, cleaned its private WORK directory and
left the source bundle at SHA-256
`c214839a7d3e418533a0fdfeefda19ee4da7f06fbd5589db1ee3adfae1c2bc99`.
No lease, `devicectl`, `xcodebuild`, renewal, install, launch, container action,
phone or Simulator was used. Independent final re-review is still required
before this correction closes.

## 2026-08-09 — IOS-P2-004 corrected final approval

After the dependency, mutation-test and documentation corrections above, the
independent Sol xhigh re-reviewed the complete current diff and evidence. Its
verdict was exactly `APPROVE — brak P0/P1/P2`. This closes the host-only
IOS-P2-004 tooling checkpoint; physical installation, container preservation
and runtime behavior were not exercised or claimed.

## 2026-08-09 — IOS-P2-005 local CI provenance closeout

The temporary phone-free IOS-P2-005 slice pinned all 12 external action uses in
`.github/workflows/ios.yml` to reviewed commits while retaining release comments:
checkout `11d5960a326750d5838078e36cf38b85af677262` (v4.4.0), upload-artifact
`ea165f8d65b6e75b540449e92b4886f43607fa02` (v4.6.2), download-artifact
`d3f86a106a0bac45b974a628896c90dbdf5c8093` (v4.3.0) and cache
`0057852bfaa89a56745cba8c7296529d2fc39830` (v4.3.0). GitHub reported each
commit signature as verified. Official Actions documentation established that
only a full commit SHA is immutable, `cache-hit == 'true'` denotes an exact
primary-key hit and `hashFiles` needs explicit missing-file protection.

The dependency job now rejects missing or symlinked members of an exact
seven-file input list, hashes that list and records an ordered runner/Xcode/SDK/
clang/CMake/Python/Make fingerprint. Its cache key also fixes branch, runner,
SDK/platform, iOS 16.4, bitcode off, Release and Unix Makefiles. There are no
restore keys, split restore/save actions, cross-OS mode or `always()` escape.
The build runs only when the exact primary key misses; the exact seven-library
arm64 verifier and prefix upload remain unconditional on hit and miss.

`misc/ios/test_ios_ci_contract.py` is stdlib-only. It checks action identities
and counts, trigger/permission boundaries, cache/source/toolchain contracts,
critical scripts, step/job fields and compile-only Simulator intent. Four
adversarial Sol xhigh rounds found YAML alternate spellings, hidden failure
semantics, forged hashes, cache options, weakened executable payloads, trigger/
matrix gaps and shell-obfuscated Simulator runtime. The final correction keeps
those semantic diagnostics and additionally binds the complete reviewed
workflow using `sha256-v1-exact-utf8-bytes`, including comments and line endings.
Its authoritative workflow digest is
`47885fe333f741eb5e1438c3bc5a5de7c1ee552d8c6635bce8317727e01c389b`.

Independent local evidence passed: no-write Python compile; 79/79 positive and
mutation tests; `actionlint 1.7.12`; full and untracked-file whitespace checks;
the extracted input script; and the extracted Xcode 27/iPhoneOS 27 toolchain
fingerprint script. The latter produced
`74536eb14f0fc6ca11bb6e4e9b24462f4593cdc597c213d6f3ce574e3f7102e5`.
The final Sol xhigh verdict was exactly `APPROVE — brak P0/P1/P2`.

The workflow and validator are intentionally outside the device-artifact input
set. Recomputed source hash still exactly matches the existing full stamp:
`2d1cdfe589623819de98c6c9ac08465ddabc72b6ebabb154ade248a3b3191def`.
Therefore no redundant app build was run. No phone, lease, `devicectl`,
Simulator, signing, installation, launch, runtime test, commit or push occurred.

This closes only local provenance/drift acceptance. Full IOS-P2-005 remains in
the Backlog until one clean remote miss and one exact remote hit reproduce the
device and Simulator artifacts. The default `ios-port` branch is unprotected,
so no cache-poisoning-resistance claim is made. IOS-P1-002 returns to the active
Plan.

## 2026-08-09 — IOS-P2-006 local BC fallback contract closeout

With the active P0/P1 items waiting on physical-device evidence, Sol medium
ranked the remaining phone-free work and selected the active BC/DXT fallback
over dormant feature macros and an unproven environment-alias cache risk. Sol
xhigh approved a behavior-preserving contract slice before implementation.

The former file-local BC1/2/3/4/5 decoder in `glTexture.cpp` is now the pure,
header-only `ios_bc_texture_codec.h`; `ios_bc_gli_format.h` is the sole mapping
from supported GLI formats. `BlockBytes(Kind)` is the single block-size source.
The decoder rejects null, zero, truncated, undersized and overflowed inputs.
Runtime callers pass the actual `texture.size(level)`; the 3D path validates the
whole level before advancing per-slice pointers.

`texture_bc_fallback_test.cpp` covers exact BC1 four-color and transparent
three-color modes, BC2 alpha, both BC3 alpha branches, BC4 `RRR1`, BC5 `RG01`,
1x1/3x5/5x3/multiblock edges and malformed bounds. It also loads and decodes
real GLI fixtures for DXT1 UNORM, DXT1 sRGB, DXT5 sRGB, BC4 and BC5. The strict
and ASan/UBSan variants are fail-closed members of `build_check.sh` and clean
their temporary binaries through the existing EXIT trap.

The rendered contract is deliberately unchanged: BC1/2/3 sources tagged sRGB
still allocate `GL_RGBA8`, GLI source swizzles remain ignored, BC4 expands to
`RRR1` and BC5 to `RG01`. Earlier iPhone evidence already proved DXT channel
order for representative terrain and sky; this slice does not reopen that
finding and does not claim that the transfer function is colorimetrically
correct. Choosing `GL_RGBA8` versus `GL_SRGB8_ALPHA8` and explicit swizzles
remains IOS-P2-006 device work requiring numeric probes and reference frames.

The first final review rejected three P2s: computed instead of actual source
sizes, synthetic GLI objects instead of real files, and duplicated block-size
definitions. Terra xhigh corrected all three. Strict and sanitized focused
tests then passed. An intermediate full invocation hit an unrelated retail
harness race and wrote no stamp; after its isolated rerun passed, the fresh full
gate completed with 279/279 shaders, low 2/2, SSAO 6/6, numeric 30/30, 137/137
links and one rebuilt translation unit.

The authoritative stamp is dated `2026-08-09T22:21:30+0100`: source
`e081251226b5358a7b521174564f80073e62f1cbfc44ba31436938074c6d2817`,
UUID `44D1A9FA-F8AF-3CD5-AC97-386F0D2999D2`, bundle
`38ab03ef4a7417f16ed93acb653e772d536ba8ddb099e099f784e06eb9547f6e`,
platform IOS, minOS 16.4 and shader cache forced off. Final Sol xhigh re-review
returned exactly `APPROVE — brak P0/P1/P2`.

No phone, lease, `devicectl`, Simulator, signing, installation, launch, runtime
test, commit or push occurred. The local contract is complete; the visual/color
acceptance remains in the Backlog.

## 2026-08-09 — IOS-P2-006 artifact-hash and evidence correction

The documentation closeout review found two P1s. The new BC contract test was
executed by `build_check.sh` but absent from `IOS_ARTIFACT_INPUTS`, so changing
the test alone would not invalidate a prior device-artifact stamp. Canonical
current facts also still named the superseded IOS-P2-004 stamp.

`misc/ios/texture_bc_fallback_test.cpp` is now an explicit artifact input. The
existing hermetic installer contract requires it alongside the lease/timeout
dependencies, mutates each copied file, proves the digest changes, restores the
original bytes and proves an exact return to the baseline digest. No-write
Python compilation passed 2/2 and the complete installer contract remains
13/13.

The fresh full gate passed BC strict/sanitized, retail 74/74, installer 13/13,
numeric macros 30/30, low 2/2, SSAO 6/6, shaders 279/279 and links 137/137. Its
stamp is dated `2026-08-09T22:35:29+0100`: source
`02d9ca8d88910cb4ff485e3fe9a0fb378c1e9baa8eb4a66e0c28cca8a76d64f2`,
UUID `44D1A9FA-F8AF-3CD5-AC97-386F0D2999D2`, bundle
`38ab03ef4a7417f16ed93acb653e772d536ba8ddb099e099f784e06eb9547f6e`,
platform IOS, minOS 16.4 and shader cache forced off. Recomputing the current
artifact hash returns the stamped source hash exactly. The app UUID and bundle
remain those produced by the preceding one-TU codec build; current
`__debug_info` is 513,541,671 bytes with the matching UUID.

Evidence correction: the preceding entry's phrase "unrelated retail harness
race" was not backed by a retained exact error or log and is withdrawn. The
only retained facts are that one intermediate invocation wrote no fresh stamp,
the focused retail contract later passed, and the subsequent complete full gate
passed. No cause is claimed.

Canonical and Resume stamps now describe this current artifact. Final Sol
xhigh re-review of the corrected production dependency and documentation is
still required before closeout.

No phone, lease, `devicectl`, Simulator, signing, installation, launch, runtime
test, commit or push occurred.

## 2026-08-09 — IOS-P2-006 corrected final approval

After the artifact-input mutation coverage, fresh full stamp, canonical stamp
update and append-only evidence correction above, the independent Sol xhigh
reviewed the complete corrected production and documentation state. Its verdict
was exactly `APPROVE — brak P0/P1/P2`.

This closes the local IOS-P2-006 codec/contract checkpoint only. The choice of
sRGB storage and explicit source swizzles, numeric texture probes and iPhone
reference frames remain deferred acceptance work. No phone, Simulator, install,
signing, runtime, commit or push occurred.

## 2026-08-09 — stamped Release device smoke after IOS-P2-006

The stamped Release payload build 10045 was installed under the preserved Team
ID `RMJWWPF379` and bundle identifier
`io.github.tryk016.openxray.RMJWWPF379`, then launched in diagnostics/autoinput
mode on the shared iPhone 15 Pro Max. The unattended load reached gameplay and
fresh engine framebuffer captures were produced at the native `1864x860`
resolution.

The first gameplay frame remained visibly dark across terrain and vegetation.
A file-driven 12-second forward request was acknowledged by the engine and the
subsequent frame showed a changed camera position and materially lighter nearby
terrain, while distant vegetation still read as dark silhouettes. This is a
runtime reproduction consistent with the previously reported movement-linked
transition, but this single sequence does not isolate travelled distance from
game time or weather and therefore does not close the lighting checkpoint.

Pause-menu, inventory and Video-options frames were also captured. The pause
menu was readable and contained no Multiplayer item; inventory item, equipment,
portrait, slot and HUD textures were present; Video options displayed quality
profile `Optimal` and resolution `1864x860`. These are bounded visual smoke
results, not complete interaction or texture-correctness acceptance. Diagnostics
readback was enabled, so no performance conclusion is drawn.

The OpenXRay process was terminated after capture. The exact-token device lease
was released successfully and the shared lock returned `free`. No source,
signing identity, Team ID or bundle ID was changed. No explicit save/container
deletion or retail-data mutation command was issued; the normal same-identifier
app update and diagnostic control-file writes were the only intended device
mutations. No before/after container manifest was collected.

## 2026-08-09 — consolidated commit and post-commit full gate

The accumulated, individually reviewed iOS slices were consolidated in commit
`27d966ddc8a571955abdb861839ece5d3b77289a` (`ios: consolidate playable
port checkpoint`). No push occurred.

Because the artifact digest intentionally includes Git HEAD, the pre-commit
stamp became obsolete at commit time. A fresh `./misc/ios/build_check.sh --full`
then rebuilt 68 translation units and passed the strict/sanitized BC contract,
retail 74/74, installer 13/13, local CI 79/79, numeric macros 30/30, low 2/2,
SSAO 6/6, shaders 279/279 and links 137/137. The dSYM contains 513,541,671
`__debug_info` bytes and UUID `545F8958-0B1F-3D85-BDF2-758CF9553A16`, matching
the app. The authoritative stamp is source
`449dd3f9d6bcd540d1ed6739fa111219f7c9d95fddcc0eed8529e10326a28da6`, bundle
`eeacc4b2d878f62ca70a33cf51e9014b2f20de20a28a64e50096879467c68351`,
OpenAL Soft 1.25.2 static SHA-256
`86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914`,
platform IOS, minOS 16.4 and shader cache forced off. An independent recompute
matched the source hash exactly, and the host-only installer preflight passed.

This post-commit artifact was not installed or run on a phone. The immediately
preceding device smoke used the prior stamped build-10045 artifact, so its visual
evidence must not be attributed to this new UUID. No Simulator, phone command,
lease, install or push occurred during the post-commit gate verification.

## 2026-08-10 — capture-state v2 and controlled lighting A/B local closeout

Commit `90e9d3c3e6de12c86be682875784d2032bcbc238` adds an iOS-only
capture-state v2 producer and host evidence pipeline for the unresolved later
dark-frame smoke. One frozen canonical JSON snapshot carries process/session,
sequence, PID/frame/time, scene, camera, world epoch/sector, final environment
and diagnostic-input state. The same token is embedded in the PPM and PNG.
Publication is fail-closed: stale GL errors are drained and logged, the read FBO
and `glReadPixels` result are checked, the RGBA allocation is zeroed, writes are
temporary-plus-rename and no JSON is published for a failed image.

`shot.sh` now reads metadata-before, PPM and metadata-after, accepts only
byte-identical metadata and a matching token, and refuses stale, partial,
overshot or pre-existing evidence. `lighting_ab_capture.sh` holds one exact
8-minute lease, renews the same token before B/input/C and captures A, stationary
B=A+3, a UUID-correlated released 12 s W request, then C=B+3. The verifier
requires one PID/session/level/epoch, controlled 15 s arms, stationary A-B,
forward B-C, camera/time/weather/sector constraints and no active/intervening
A-B diagnostic request. It gates only position-modified `ambient.rgb` and
`hemi.rgb`; nonlinear hemi alpha, sun and sun direction are retained as
explicit non-gating observations. Its success token proves correlation controls
only and does not classify pixels or establish a movement/streaming cause.

The first Sol xhigh review rejected four P1s: intervening stationary input,
uncontrolled final lighting, publish-on-readback-error and a lease shorter than
the bounded batch. Re-review rejected an over-strict constant-light model and
an active-hold loophole; a later review rejected linear gating of nonlinear sun
motion. Each finding received a focused mutation/adversarial test. The corrected
complete code/test/tooling diff received exactly
`APPROVE — brak P0/P1/P2`.

Post-commit `./misc/ios/build_check.sh --full` rebuilt 69 translation units and
passed capture evidence 16/16, host mocks 8/8, source mutation 2/2, strict and
ASan/UBSan C++ tests, retail 74/74, installer 13/13, CI 79/79, numeric 30/30,
low 2/2, SSAO 6/6, shaders 279/279 and links 137/137. Stamp:
`source_sha256=ff2c33c77f1fb0aca0a6b2ec9d49661ea41ded30ce44ffe28469167055b57f02`,
UUID `226890F0-CF42-302B-AA5F-3092CB5E4AF3`, bundle
`620f3b98983fe10184b31a232dbf564bf256b6d68e7073f1f547ff6e0181ae1a`,
platform IOS, minOS 16.4, forced-off shader cache and dSYM `__debug_info`
513,888,155 bytes.

No phone, shared lease, `devicectl`, Simulator, signing, installation, launch,
runtime capture or push occurred. Physical capture values, image comparison and
the underlying cause remain open.

## 2026-08-10 — isolated iOS 27 capture-v2 closeout exposes stationary sector gap

This is a subsequent checkpoint, not a rewrite of the preceding capture-state
v2 entry. The device producer/A-B baseline remains commit `90e9d3c3e`; the new
locally reviewed host extension adds one isolated Simulator
single-capture mode across exactly five files:
`misc/ios/retail_simulator.sh`, `misc/ios/retail_simulator_guard.py`,
`misc/ios/lighting_ab_evidence.py`, `misc/ios/test_retail_simulator.py` and
`misc/ios/test_lighting_ab_evidence.py`.

`--capture-v2` requires `--with-saves` plus `--autoload-save`, conflicts with UI
navigation and is restricted to iOS 27.0. Ordinary normal and UI-navigation
modes retain diagnostics/autoinput `0/0` and `0/1`; capture-v2 uses `1/0`. The
runtime guard owns the `saved_game_sync_complete` boundary, exact PID/liveness
and one original launch deadline. T0 is a stable metadata watermark at that
boundary or an explicit absent watermark, T1 is the first stable sidecar newer
than T0, and T2 is a stable metadata-before/PPM/metadata-after pair newer than
T1 in the same session/PID. The evidence parser is the sole capture grammar and
secure snapshot owner. Outputs are new-only and proof-last; report publication
is ordered after Simulator deletion and all final capture/log/save/staged/
protected-input guards.

Focused static checks pass, parser coverage is 31/31 and runner coverage is
84/84. The final Sol xhigh verdict is exactly
`APPROVE — brak P0/P1/P2`. This closes the host implementation only.

The first real isolated workroot was
`/Users/patryk/openxray-handoff/simulator-work-20260810-015703-14748`.
Synchronization completed; T0 was a stable `loading` sequence 24, T1 was
`loading` sequence 25, and the first stable post-T1 candidate was `loading`
sequence 26. Treating that valid transitional candidate as fatal was too
strict. The correction makes live `loading`, paused gameplay and gameplay with
no environment retryable with CLI 75. Malformed/schema-invalid evidence, wrong
PID/session, dimensions, period, input, menu scene, wrong non-null level,
epoch 0 and nonadvancing frame/continual time remain fatal with CLI 1. The
post-stop full-set verifier escalates every retry classification to fatal.

The corrected real workroot was
`/Users/patryk/openxray-handoff/simulator-work-20260810-024814-86404`.
Its build succeeded and the retail save reached Zaton synchronization and
`after_load`. T0 was `loading` sequence 23, T1 was `loading` sequence 24, and
stable candidates through sequence 88 remained `loading` under the one
600-second launch deadline. The startup-sector marker was:

```text
pid=92531 epoch=1 frame=35 level=zaton trigger=level_load
status=unresolved method=none sector=4294967295
probe=camera radius=0
```

Code inspection identifies the uncovered control flow. `CRender::Calculate`
invokes exact detection and the nearest-floor fallback only when
`Device.vCameraPositionSaved` differs from `Device.vCameraPosition`. The
stationary Simulator load has equal positions and enters the no-detection
branch, which records `unresolved`/`none`; it never attempts the fallback. This
does not invalidate the existing iPhone evidence: when invoked on the affected
Zaton spawn, the fallback finds sector 115 at 8 m and restores the world. The
new fact is that the stationary initial path can bypass that proven mechanism.

The 600-second run timed out fail-closed. Both dedicated Simulators were
deleted. No T2 `capture.json`, `capture.ppm`, `capture-proof.json`, `report.txt`
or `.report.pending` exists. The last successful protected-input and
source-snapshot guards were pre-launch; the timeout prevented the post-runtime
log, save, staged-data and protected-input guards from running. There was no
phone, shared lease, `devicectl`, signing or installation.

Evidence scope is exactly
`iOS-27.0-Simulator-Apple-Software-Renderer-only`. These runs prove the host
ordering/readiness behavior and the stationary sector-branch gap. They do not
prove a successful runtime capture, pixels, readability, physical-iPhone
behavior, performance or a lighting cause. IOS-P0-003 remains active. Its next
local action is to make the stationary initial epoch attempt exact/fallback
once without weakening QuickLoad barriers, retained-sector semantics or
invalid-sector commit protection, then rerun capture-v2 in a fresh workroot.

## 2026-08-10 — capture-v2 host checkpoint committed and full-gated

Commit `362bf625d59a5219857382afb8a5f23ccbf54fbe` records the reviewed
five-file capture-v2 host implementation, its tests and the current-state
documentation. The post-commit full uncached gate passed: retail isolation
84/84, numeric macros 30/30, low 2/2, SSAO branches 6/6, shader contract
279/279 and links 137/137, plus all established installer, CI, lifecycle,
memory, audio, UI and BC contracts. The symbol-complete arm64 iPhoneOS build
recompiled 68 translation units; platform is IOS, minOS 16.4 and shader cache
is forced off.

```text
source_sha256=33c9efabda5cfdb99939440199b87331fac3ee8deb106383ca8fb6112a6e9c06
app_uuid=7297E9B6-1DBE-348D-8BAE-A153A19F781E
bundle_sha256=0e0ba58332a97b6d04fa458a50fd82006ebd6cecbe1f6f1182f8dabf453b6ff3
openal_provider=OpenALSoft-1.25.2-static
openal_sha256=86dd63597bac2f3e3e8dae7be8bbbc84a35d3aa492e2cd23ffca6363e97c4914
__debug_info=513888155
```

The gate was host-only: no phone, lease, `devicectl`, signing, installation or
launch occurred. It validates the committed tooling and artifact, not a real
T2 publication; the stationary Simulator sector branch remains IOS-P0-003.

## 2026-08-10 — docs provenance and matching-stamp boundary

Documentation commit `86ed974d6` recorded the preceding code-artifact gate. A
second full uncached gate on that docs commit also passed, including retail
84/84, numeric 30/30, low 2/2, SSAO 6/6, shader contract 279/279, links 137/137
and the 68-TU symbol-complete build. Its generated stamp was:

```text
source_sha256=8c121ae86840118e90e639d0145b79e7fb7764d5c6b51d3a166b6d2264d05e2b
app_uuid=3D91E69C-9170-37E0-96B6-1F3269E12C0A
bundle_sha256=d1daad61ac97523dbb71c1f67d7b6ac810b4237399977ac1a3e7a4d89d400768
```

This append itself advances the documentation-only HEAD, and documentation is
part of the source hash. The values above are therefore historical evidence for
`86ed974d6`, not an install stamp for the later docs-only HEAD. A fresh full
gate is mandatory before install or push. No phone, lease, install or push
occurred.

## 2026-08-10 — stationary LevelLoad branch corrected and Simulator capture published

The stationary startup gap is closed locally. A pure `ShouldDetectSector`
policy keeps real camera movement unconditional and permits one equal-camera
`level_load` exact/fallback attempt only while startup evidence is awaiting,
`last_sector_id` is invalid, no report is pending and a dedicated post-epoch
camera-generation barrier has passed. LevelLoad rejects retained/none outcomes;
QuickLoad keeps its independent barrier and no-detection semantics. Barriers
and pending evidence are reset at level load/unload and QuickLoad boundaries,
and a stored LevelLoad observation disarms its barrier before report retry.

Focused local evidence passed: startup oracle 17/17, source-marker contract
10/10, strict C++ policy, ASan/UBSan with macOS-supported leak detection off,
`git diff --check`, and the complete FastDevice gate after rebuilding 249
translation units. FastDevice UUID is
`020185E6-48B6-3A4B-8B1C-CC8ED72EE27B`.

Fresh isolated workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-041339-82601` passed on
iOS 27.0. PID 90549, epoch 1, frame 35 emitted `level_load resolved/exact`,
sector 115, camera/probe `(256.240,21.468,550.824)`, radius 0, without input or
movement. The saved-game boundary and stable capture used one session with
T0/T1/T2 sequences 54/55/56, frames 89/91/92 and advancing continual time;
T2 was unpaused Zaton gameplay at 1864x860 with no diagnostic input. The five
capture-v2 artifacts and `report.txt` were published only after all final
guards passed, protected inputs remained unchanged and dedicated Simulator
`1AF2F8F5-63A9-417A-9406-6B2DFE0882AC` was deleted.

Scope is `iOS-27.0-Simulator-Apple-Software-Renderer-only`: this proves the
stationary control-flow and publication contract, not pixels, readability,
physical-iPhone behavior, performance or the cause of the later dark frame.
IOS-P0-003 remains open for another outdoor save, indoor/portal, QuickLoad,
transition and controlled phone lighting evidence. No phone, shared lease,
`devicectl`, signing, installation or push occurred.

## 2026-08-10 — delayed lifecycle fixture race removed without weakening guards

The post-commit full gate for `399b7fdbb` first exposed one intermittent failure
in the positive delayed foreground-cycle fixture: an asynchronous 30 ms writer
could append a lifecycle marker between the stable reader's metadata samples,
so the production guard correctly rejected `runtime log identity or size
changed while being read`. The exact test passed alone, but an independent
sequential stress reproduced the failure at iteration 7. This was a fixture
scheduling defect, not grounds to retry or weaken the production read.

The test-only correction changes `misc/ios/test_retail_simulator.py`. Separate
`deactivate` and `activate` payloads now advance deterministically through
`pending -> armed -> released`. The delayed-only `ps` mock executes absolute
`/bin/ps` with the original arguments, forwards its stdout/stderr/status, and
moves at most one state only after exact argument, positive PID, real stdout and
payload-PID validation. Marker append is synchronous between guard snapshots.
The production runner and guard, including inode, size, mtime, rotation,
truncation, prefix-rewrite and symlink checks, are unchanged.

Evidence passed: Python compile, focused positive test, seven death/late-fatal/
second-engine/rotation/truncation/symlink adversarial tests, `git diff --check`,
50/50 sequential workflows, 200/200 eight-way parallel workflows and the full
retail suite 84/84. FastDevice then passed all established local contracts,
retail 84/84, numeric macros 30/30, low 2/2, SSAO 6/6 and shader links 137/137;
68 translation units rebuilt and UUID is
`B19A8309-D20F-3743-9452-33DC054DF305`.

The following full uncached gate also passed: retail 84/84, numeric 30/30, low
2/2, SSAO 6/6, shaders 279/279 and links 137/137. The symbol-complete Release
reused its matching engine objects (0 TUs), retained arm64 iOS 16.4 and produced
dSYM `__debug_info` 513889052 bytes with UUID
`A94C72FB-B192-34CA-9B5B-3B563839DDC3`.

This closes only deterministic host-fixture reliability. It adds no Simulator
runtime, pixel, iPhone, lifecycle, performance or audio evidence. No phone,
lease, `devicectl`, signing, installation, commit or push occurred.

## 2026-08-10 — opt-in native iOS 27 Simulator UI-capture evidence

The new host-only `--ui-captures` mode is intentionally coupled to
`--ui-navigation`, iOS 27, diagnostics and autoinput. The semantic marker is
emitted after `DoRenderDialogs()`. The controller path uses `I, I, P, E, Escape,
M, Escape` and requires the seven post-baseline states `inventory`, `world`,
`pda_tasks`, `other`, `world`, `pda_tasks`, `world`. Captures are required at
steps 1, 3, 4 and 6 respectively: inventory, combined CoP Tasks/Map,
other/Stats, combined CoP Tasks/Map.

The evidence mechanism accepts a stable metadata/PPM pair only when exact
session, PID, token, frame, dimensions and input ordering agree. The PPM is
authoritative; the PNG is generated as its exact derivative. Acceptance order
is `accepted.frame <= marker.frame < released.frame <= capture.frame`, so an
input release in `FrameMove` may share the rendered capture frame but cannot
precede a marker from the same frame. Process-stop revalidation precedes the
new-only manifest and report publication. The live runtime-log reader admits a
monotonic append while the process is alive, but fails closed on a symlink or
nonregular file, inode change, shrink, rewrite, truncation or any read race.

The initial workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-082352-68297` failed
closed after 6/7 transitions: the strict immutable reader saw an ordinary live
append. It published no manifest or report and its dedicated Simulator was
deleted. This was a host-harness boundary, not evidence of a game failure; the
bounded append-only reader above replaced immutable reading only for live
runtime observation. The earlier capture ordering issue was corrected to the
explicit inequality above.

Two independent iOS 27 Apple Software Renderer runs then passed. Workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-085137-86609` used PID
92589; Simulator `064FCEBA-4D4B-445C-B25F-C0C22B262519` was deleted. Its
manifest SHA-256 is
`7274f1b111e48be3e5febad225c3bb151392750bf63b52715f2bb6ebdc29148d`.
Workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-090751-93548` used PID
99569; Simulator `A5DF823B-917C-4E0D-8E0B-533F29C5C636` was deleted. Its
manifest SHA-256 is
`7c609c49fbfd483e046bbce6f9fbc67a2884c1d0435b3ce9a8a1ee5f8034b2ca`.
Each report records 7/7 semantic transitions, 4/4 native 1864x860 captures,
post-stop revalidation and unchanged protected inputs.

Focused host tests were capture evidence 15/15, navigation 34/34 and retail
isolation 92/92. The main chat inspected all eight resulting PNGs: complete
inventory, area map/tasks, Stats and area map/tasks in each run. This is native
file-level Simulator visual evidence for those surfaces. It is not iPhone,
readability, color or performance proof, and it does not close the remaining
overfilled-inventory, faction-war or other listed UI surface tests under
IOS-P1-006. No phone, shared lease, `devicectl`, signing, installation, commit
or push occurred. The final Sol xhigh verdict is exactly
`APPROVE — brak P0/P1/P2`.

## 2026-08-10 — normal QuickSave/QuickLoad Simulator evidence published

IOS-P0-003 now has one real normal-path QuickLoad packet without phone access.
The opt-in iOS 27 runner generates an isolated config with F5 QuickSave and F9
QuickLoad, maps them to SDL scancodes 62/66 and drives the existing file input
path. Its oracle requires unique UUID/ACK/press/release records, exact logical
`Player - quicksave` save/load messages, the lowercase physical
`player - quicksave.scop`, one PID, an anchored terminal `quick_load` epoch and
three advancing native 1864x860 gameplay captures.

Fail-closed development runs separated four harness defects from game behavior:
an empty Simulator username produced ` - quicksave.scop`; LocatorAPI lowercased
the physical filename; synchronous save/load could complete before the
post-dispatch press log; and the general autoload finalizer rejected the one
intentional second load. The iOS username now falls back to `Player` only after
sanitization yields empty, preserving nonempty device names. The general
finalizer has a narrowly named canonical-marker exception; the dedicated oracle
still exclusively proves F9, UUID/scancode and epoch semantics. A later run
timed out at 120 seconds while valid world loading continued, so the final run
used the existing bounded 600-second runtime budget. No failed run published
`report.txt`; every owned Simulator was deleted.

Sol medium pre-review then found two publication P1s. Finalization now binds and
revalidates the private QuickSave copy by inode, size and SHA-256. The manifest
is evidence-only and cannot claim PASS; `report.txt` is the sole authoritative
PASS artifact and its hardlink is the final publication commit point. Tests
cover private-copy content/inode replacement and interruption before the final
report link. The full retail suite passes 97/97, the dedicated oracle 22/22,
the static input/username contract 3/3, Python compilation, shell syntax and
`git diff --check`.

Final workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-134537-64428` passed a
clean arm64 iOS 27 Simulator build with deployment target 16.4. PID 73664 kept
epoch 1 `level_load/exact` (frame 35) and produced epoch 2
`quick_load/retained` (frame 122). B0/B1/C captures were unpaused Zaton gameplay
at 1864x860 on frames 90/94/182. The original save remained 631235 bytes;
live and private QuickSave copies were each 649947 bytes with SHA-256
`f49e87d54c1c9c8aee656bce941f8aabdcee76f0e3e77746108cda1a5489dd96`.
Manifest SHA-256 is
`858668039a9c1856e93f80ac694309053396a8be7ecc211005f46557fe700778`.
All post-stop guards passed, protected inputs were unchanged, Simulator
`3EADFEB4-67E2-4AF4-B2F5-5410FDBBC84A` was deleted, then `report.txt` was
published. Scope is Apple Software Renderer save/load and sector control flow,
not iPhone, pixel quality, readability, lighting correctness or performance.
No phone, lease, `devicectl`, signing, install or push occurred.

Final Sol xhigh review of the complete uncommitted slice found one further P1
and two P2s. Finalization had trusted the capture-key set, and successful
publication retained mutable pending hardlinks; the Resume also blurred the
older UI approval with this slice. The correction requires exactly B0/B1/C,
re-runs each semantic capture contract against sector/input ordering, removes
both pending links after publication and scopes the older approval explicitly.
The dedicated oracle now passes 22/22. Workroot `134537-64428` remains valid
normal F5/F9 runtime evidence, but predates these final host hardenings; a fresh
run is required before calling the current publication implementation
real-Simulator proven.

## 2026-08-10 — deterministic active-gate capsule and private gate logs

OpenXRay now boots Codex work from a checked, local capsule rather than loading
the full iOS documentation set into conversation context. The generated cache
is ignored, mode 0600, source/dirty/hash bound and fail-closed; the final real
snapshot is 27,322 bytes with the explicit `ceil(UTF-8 bytes/4)` estimate 6,831.
Focused contracts pass 7/7, including newest exact task-ID Journal selection,
symlink rejection, stale tracked/untracked content and pair rollback.

Project config leaves only Context7 and iOS Simulator enabled (7/9 MCP servers
disabled), with a one-process Cloudflare override proving 6/9 then restoration
to 7/9. New OpenXRay tasks have memory disabled. The CLI `code` profile excludes
documents, PDF, spreadsheets, presentations, template, Sites and Visualize;
its prompt contains none of those skills or Memory. Desktop/profile and memory
still require a new-task smoke and are not inferred from this already-open task.

The allowlisted logged runner serializes each build tree, preserves exit and
signal status, hashes through the reserved log descriptor and writes private
raw/metadata/inner logs. One real full run passed with exit 0: retail 97/97,
numeric macros 30/30, shader compile 279/279, links 137/137 and 69 rebuilt TUs.
Its log/metadata hashes and existing stamp snapshot revalidated; all private
file modes passed. Final post-run hardening passes logger 9/9 and was not used
to claim a second full build. Final Sol xhigh verdict is exactly
`APPROVE — brak P0/P1/P2`.

Scope is host tooling/configuration only: no phone, lease, Simulator, signing,
install, commit or push. Existing user-config credentials were not copied or
printed; separate rotation/storage migration remains pending.

## 2026-08-10 — host-tooling checkpoint correction and closeout

The active-gate contract is now fully fail-closed: focused tests pass 9/9,
the generated capsule estimate must be within 6,000..9,000 tokens, the runtime
directory is mode 0700 and its capsule/manifest artifacts are mode 0600. The
logged-gate runner contracts also pass 9/9.

FastDevice PASS is recorded by
`gate-1786376080683616000-11527-0`: retail 97/97, numeric macros 30/30,
links 137/137, 1,856 translation units and UUID
`21A5256F-0EB0-3AA6-90BD-E1D7B719D603`. Full PASS is recorded by
`gate-1786376829438790000-35386-0`: retail 97/97, numeric macros 30/30,
shader stages 279/279, links 137/137, 14 translation units, arm64, minimum iOS
16.4, SDK 27.0 and UUID `EBF46B07-0F07-30E2-B6F0-4EB8B7B3FE6B`. Each gate bound
an identical source state before and after execution. Final Sol xhigh verdict:
`APPROVE — brak P0/P1/P2`.

This evidence applies strictly to the pre-append hashed source state. This
append advances that state and therefore stales install stamps; a new full gate
is required before any install or push. No phone, Simulator, install, launch,
lease, commit or push occurred. A later Codex CLI verification exposed the
Cloudflare and Context7 credentials in this task's private tool transcript, but
not in the repository or gate logs. Rotation is required; no credential values
are recorded here.

## 2026-08-10 — active-gate tail correction

Final Sol documentation review found a P1 in the bounded session-log selection:
the prior implementation retained the beginning of the final window and could
omit the newest handoff state. `tail_clip` now retains complete newest lines and
adds an explicit earlier-lines marker. Focused active-gate regression coverage
is 10/10; logged-runner coverage remains 9/9. The real capsule generated before
this documentation append was 27,279 bytes with a 6,820-token estimate and
contained the latest full-gate, stale-stamp and credential-rotation status.
Sol xhigh re-review: `APPROVE — brak P0/P1/P2`.

No phone, Simulator, install, launch, lease, commit or push occurred. The
existing Fast/full evidence remains bound to the pre-document/pre-tail source
state; run a new full gate before any install or push. No credential values are
recorded.

## 2026-08-10 — install-stamp attribution correction

Documentation and session-log appends change the logged runner's full git-state
binding, but those documents are not `IOS_ARTIFACT_INPUTS` and did not by
themselves invalidate the install stamp. The later changes to
`misc/ios/active_gate.py` and `misc/ios/test_active_gate.py`, both artifact
inputs, made the Fast/full install stamps stale. A new full gate remains
required before install or push.

## 2026-08-10 — IOS-P0-003 fresh hardened Simulator QuickLoad publication

Fresh phone-free workroot
`/Users/patryk/openxray-handoff/simulator-work-20260810-164020-71268` closes the
previous “fresh hardened publication pending” state for the Simulator portion of
IOS-P0-003. `report.txt` is the sole PASS artifact (SHA-256
`27f89f6793ec2d0f0ee6831add123ae3e54b9d83eaaa37d4326a62d052f35cb9`); manifest
SHA-256 is `7d45f49bbe15727a1813971b958fe8c19261b32cc3426a7bfd69dffbdd418896`.

The clean build has one arm64 `IOSSIMULATOR` slice, minOS 16.4 and SDK 27.0.
PID 77340 recorded epoch 1 `level_load/exact` at frame 35, then epoch 2
`quick_load/retained` by method `retained` at frame 122. The hardened semantic
set is exactly B0/B1/C: native 1864x860 gameplay frames 89/94/182 and tokens
54/58/118. Normal F5/F9 dispatch is bound to scancodes 62/66. Live/private
QuickSave copies have distinct inodes 22482673/22482715 and identical 649473 B
SHA-256 `eb82993bc9eb70ef64ef7830669328470fb556da69a82883a41ffc9dda5c43fe`.
The original save remains unchanged at 631235 B with SHA-256
`7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`.

Both pending aliases are absent, protected inputs are unchanged and dedicated
Simulator `278AD2E8-7116-448F-8474-670945489415` was deleted before report
publication. Final Sol xhigh verdict: `APPROVE — brak P0/P1/P2`.

This is evidence of normal F5/F9 control flow on Apple Software Renderer only.
It is not evidence for iPhone behavior, pixels, readability, lighting,
performance or other content. IOS-P0-003 stays active for physical and wider
content acceptance. No phone, lease, `devicectl`, device install, commit or push
occurred. Existing device-install stamps remain stale because of prior
`active_gate` artifact-input changes; a fresh full gate is required before
install or push.
