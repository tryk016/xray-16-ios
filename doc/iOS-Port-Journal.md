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
2. Commit with a message that states **what + why + how any error was fixed**.
3. Push to `ios-port` → the `iOS` workflow validates on **GitHub macOS runners**
   (Windows cannot build iOS; CI is the *only* validator).
4. Read the CI result; fix red as its own micro-slice. **End every slice green.**
5. Add a journal entry here. Docs-only commits skip CI (`paths-ignore`), so
   journalling is free.

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

---

## Journal

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
