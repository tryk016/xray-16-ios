# iOS port — resume guide (start of Phase 2d)

Quick-start context to continue the iOS port after a break. Full detail:
[iOS-Port.md](iOS-Port.md) (overview) and [iOS-Port-Plan.md](iOS-Port-Plan.md) (58-task plan).
**Per-slice history (what was done / how / every error → root cause → fix) and the
list of authoritative references lives in [iOS-Port-Journal.md](iOS-Port-Journal.md) —
read it to avoid re-deriving, and append an entry there after each slice.**

## Where we are (2026-07-13)

Branch **`ios-port`** (HEAD `8acf37228`+), all green on CI (`.github/workflows/ios.yml`,
macOS runners only — Windows can't build iOS). Per-slice history + every error→fix is
in [iOS-Port-Journal.md](iOS-Port-Journal.md).

- **Phase 1 ✅** toolchain (`cmake/toolchains/ios.toolchain.cmake`), smoke test, CI, `.ipa`.
- **Phase 2 deps ✅ (7/7)** — every dependency cross-builds static for **both** iOS SDKs
  (OS64 device + SIMULATORARM64 simulator), all verified arm64:
  - SDL2 2.32.10, OpenAL Soft 1.25.2, libogg 1.3.6, libvorbis 1.3.7, lzo 2.10 →
    `cmake/ios/deps/CMakeLists.txt` (ExternalProject superbuild → one prefix).
  - libtheora 1.1.1 → injected CMake at `cmake/ios/theora/` (upstream is autotools-only).
  - LuaJIT → fixes in `Externals/LuaJIT-proj/CMakeLists.txt` (all `if (CMAKE_SYSTEM_NAME
    STREQUAL "iOS")`-guarded) + standalone check `cmake/ios/luajit-check/`.
- **Phase 2d Slice 1 ✅** — the *whole engine* configures + generates for iOS (device+sim)
  via one guard var `XRAY_PLATFORM_IOS` in `cmake/XRay.Build.cmake`; CI job `engine-build`
  (`needs: deps`, `-G Xcode`). Deps resolve from the prefix.
- **Phase 2d Slice 3 ✅** — all six Externals static libs compile arm64 for both iOS SDKs.
  Solved LuaJIT-under-Xcode (host tools single-config generator; `lj_vm.S` → `LANGUAGE ASM`).
- **Phase 2d Slice 4 ✅** — the *whole engine* compiles arm64 for both iOS SDKs.
- **Phase 2e ✅ (PHASE 2 COMPLETE 🎉)** — the whole engine LINKS into one arm64 iOS Mach-O
  (`bin/aarch64/Release/xr_3da.app/xr_3da`, zero unresolved symbols, both SDKs). Link closed
  with two fixes: disable JPEG on iOS (host `libjpeg.dylib` mismatch); build GameSpy for iOS
  (xrGame references it — compiled clean). glad GL renderer, OpenAL (SDK framework), pthread/dl
  all resolved unchanged. CI `engine-build`: configure → Externals → support → core → link.
- **Phase 3 (IN PROGRESS) — boots on a real device.** The app launches (SDL_main routes the
  entry through UIApplicationMain), installs + sideloads as a full iOS bundle, and the engine
  FS runs from writable `$HOME/Documents`.
- **Phase 4 (IN PROGRESS — the shader port; HEAD `488cc8c47`, 2026-07-14).** The renderer runs:
  on device the log reports **`OpenGL ES 3.0 Metal` / `GLSL ES 3.00`** (Apple's own GL-on-Metal —
  native ES 3.0 is Metal-backed, no ANGLE needed to *run*). The engine boots all the way through
  FS (CoP `.db` mounted), sound, textures (2736 .thm), to shader compilation. Slices landed:
  - **4.1** GLES 3.0 context (`glHW.cpp`: ES profile + `gladLoadGLES2`) — fixed the launch SIGKILL.
  - **4.6a** guard `glBindFragDataLocation` on ES (`ShaderResourceTraits.h`) — fixed the `_LinkPP` SIGKILL.
  - **CI shader gate** (`misc/ios/shadercheck/glsl_es_check.py` + `shader-check` job): assembles
    each of the ~286 GL shaders as `#version 300 es` and runs `glslangValidator`. ~2-min loop,
    non-strict/green. THE fix-verification tool — use it, don't round-trip the device per shader.
  - Shader-source ES fixes, all `#ifdef GL_ES` (glslang + the driver auto-predefine `GL_ES`),
    gate now at **148/286**: **4.4a** precision prelude + **4.4b** float-literal shims (lmodel/clip)
    in `gl/common.h`; **4.5a** `gl_PerVertex` wrapped `#ifndef GL_ES`; **4.5b (A1)** `VARYING(loc)`
    macro strips `layout(location=)` off vs→fs varyings; **4.3a** default option macros (SUN/SSR/
    MSAA_QUALITY=0). **4.3** engine emits `#version 300 es` on iOS (`rgl_shaders.cpp`).
  - **4.5c (2026-07-14, HEAD `488cc8c47`) — the FIRST real on-device ES compile error.** The
    definitive build finally delivered the port to the device; the dumped shader source carries all
    our fixes, and the first shader compiled (`accum_sun_mask_nomsaa.ps`, deferred path) failed with
    **`gl_FragCoord may not be redeclared`**. Every fragment shader's `iostructs/p_*.h` does, under
    `GBUFFER_OPTIMIZATION`, `in vec4 gl_FragCoord;` (also `in int gl_SampleID;` under MSAA) — a
    redeclaration of a **built-in** that desktop tolerates but GLSL ES 3.00 forbids. FIX: wrap every
    such redeclaration in `#ifndef GL_ES` (desktop verbatim, ES drops it; all *usages* untouched) —
    **32 across 24 files** (iostructs p_*.h + depth_downs/copy/copy_p.ps). The **gate had missed it**
    because it only defined `SMAP_size`, not `GBUFFER_OPTIMIZATION`, so the `#ifdef` block was cut →
    added `GBUFFER_OPTIMIZATION=1` to the gate's `BASE_DEFINES` (shader-check job stays green). The
    downstream `FATAL unsupported uniform` (`glR_constants.cpp:20`) is a **cascade** of the failed
    link (parse ran on a broken program), NOT a 2nd bug — should vanish once the fragment compiles
    (every uniform our shaders declare is a handled type: `float*/mat4/mat4x3/sampler*`).
  - **CRITICAL infra fix (`LocatorAPI.cpp`):** the iOS seed copied bundled `gamedata` into
    Documents only on FIRST launch, so every shader fix shipped in the `.app` but never reached
    the device (it reads shaders from `Documents/gamedata`). Now **refreshes fsgame.ltx + gamedata
    from the bundle every launch** (CoP `.db` archives + `_appdata_` untouched). Confirmed via the
    device log: pre-fix shader source had none of our edits.
  - **`#version 410` is confirmed unsupported** on the ES 3.0 context (driver error at `0:1`,
    independent of the stale-cache issue) → exactly what 4.3 fixes.
  - **4.8 — local glslang + family C grind, gate 117→265/279 (95%).** Downloaded the official
    Khronos `glslangValidator` (16.4.0) to `tools/glslang/` (gitignored) so **the gate runs LOCALLY
    in seconds** — no CI round-trip per fix. Helpers in scratchpad: `diag.py` (assemble like the gate
    + local glslang + show the offending source line; `--list` = first-error per failing shader),
    `fix_intlit.py` (batch the safe int-literal→float edits, prints before→after). Family C is
    GLSL ES 3.00 **int/float strictness** (no implicit int→float in operators/args/assign; desktop
    GLSL 1.20+ has it — that's why these compile on desktop). Errors chain per shader, so the method
    is: drive one representative to green by fixing the **shared headers** it pulls, which clears
    every shader on that chain. Fixed: `gather.ps` (`float*int2`, int LOD), `shadow.h`/`sload.h`/
    `hmodel.h`/`lmodel.h` int-literals, `accum_volumetric.ps` `1-`→`1.0-`; **moved `VARYING()` into
    `shared/common.h`** (the `stub_notransform_*.vs` include only common_iostructs→shared, not
    gl/common.h); guarded `gl_ClipDistance` write under `#ifndef GL_ES` (`v_volumetric.h`); renamed
    the space-typo file `accum_volumetric_sun_normal .ps` + `#unfdef`→`#undef`. Gate now also defines
    `USE_HWSMAP`/`USE_HWSMAP_PCF` (device uses HW shadows) to compile the real path.
    Then int-literal batches (skin.h `v.N.w*255`/`1-w0-w1`/`v.ind[i]*255` ≈14 shaders each,
    shared/watermove.h, cloudconfig.h `(2*0.05)`→`(2.0*…)`), structural fixes (`p_flat_atoc.h`
    forward-declared `_main(p_bumped)` not `p_flat`; `v.uv` int2→`float2(v.uv)`), macro defaults
    (fxaa FXAA_360/PS3, common.h SSAO_QUALITY/SSAO_OPT_DATA), and gate accuracy (skip include-only
    + dead files, define USE_HWSMAP/SKIN_NONE). Method: dedup the *first-error source line* across all
    failures → 2–18 unique expressions cover 30–90 shaders, almost all in shared headers.
  - **NEXT STEP (resume here):** two tracks.
    (a) **Device test** — SideStore build carrying gl_FragCoord (4.5c) + the whole family-C batch
    (4.8) is live (a new build pushes with parts 5–8). Once green, **SideStore Update → run → send
    `Documents/xray_*.log`** to see how far the engine marches on Apple's ES compiler and whether the
    monolithic program **LINKS** (the deferred **A2** varying-name problem — `v2p_*` out vs `p_*` in
    differ, ES matches by name — is the next unknown; the `unsupported uniform` fatal should be gone).
    (b) **Family D: skinning vertex-format (the only offline item left, 14 shaders).** Every remaining
    gate failure is a **model vertex shader** doing `I.N = v_model_N` where the NORMAL attribute is
    `float4` (packs a skin index/weight in .w) but `v_model.N` is `float3` → strict ES rejects vec4→vec3.
    It's variant-dependent (struct N is float3 for v_model/skinned_0/1, float4 for skinned_2/3/4; the
    attribute is float3 only under SKIN_0), so no single-line fix — needs a variant-aware change to the
    v_model_*.h headers (or the skin.h structs / vertex-format) **verified on-device**, not rushed,
    because it touches how the engine binds model vertex buffers. Run the gate locally:
    `python misc/ios/shadercheck/glsl_es_check.py --glslang ./tools/glslang/glslangValidator.exe`
    (`scratchpad/diag.py <shader>` shows the offending line). Then A2 (adopt ANGLE/ES-3.1-SSO only if
    A2 linking is painful — does NOT help family C/D). **FSR 1.0 = Plan Phase 6.** Build ≈30 min.

## On-device testing (the loop)

- **Install:** SideStore source
  `https://github.com/tryk016/xray-16-ios/releases/download/ios-dev/apps.json` (rolling
  `ios-dev` pre-release; CI republishes each build; the source version is READ FROM THE .ipa's
  CFBundleShortVersionString so it always matches — AltStore **v2** format with `versions[]` +
  `minOSVersion`). Add the source once → Update in SideStore for each new build.
- **Engine log (MAIN diagnostic now — for black-screen / runs-but-wrong, not crashes):** the
  engine writes `xray_*.log` to `$logs$`, now **`$fs_root$` = the OpenXRay Documents root**
  (was `_appdata_/logs/`), so it's one tap in File Sharing: `On My iPhone → OpenXRay →
  xray_*.log`. Pull it and read — it shows FS mount, GPU/GL version, per-shader compile results,
  link errors, how far boot got. This is how the black-screen (shaders not compiling) was found.
- **gamedata refresh:** the app now recopies bundled `fsgame.ltx` + `gamedata` (shaders/configs)
  from the `.app` into Documents on **every** launch, so a new build's shader fixes actually ship.
  First launch after an update is a bit slower (the copy). CoP `.db` archives + `_appdata_` kept.
- **Seeing errors:** the engine shows FATAL errors as **on-screen dialogs** (read + report —
  this is the main loop, no USB needed). For **hard crashes** (app vanishes, no dialog) pull
  the crash report with the wrapper `powershell C:\openxray\tools\get-xray-crash.ps1` — it
  runs idevicecrashreport, keeps ONLY `xr_3da-*.ips` in `C:\openxray\ios-crashes`, and drops
  all the device noise (SiriSearchFeedback/Jetsam/other apps), printing the newest report's
  name. (`idevicecrashreport` itself has no name filter; raw form is
  `idevicecrashreport -e -k C:\openxray\ios-crashes`. libimobiledevice + these scripts live
  in `C:\openxray\tools\`, gitignored; use `powershell` not `pwsh`.) Then say "czytaj crash".
  NOTE: `idevicesyslog` does NOT capture app `os_log` on iOS 15+ (only legacy daemon noise) —
  don't rely on it; `pip install pymobiledevice3` is the future upgrade for unified-log capture.
  NOTE: `JetsamEvent-*.ips` next to a crash is unrelated OS memory-pressure noise, not our kill.
- **CoP gamedata (user owns it; packed `.db` archives).** From the retail CoP install
  (`C:\Program Files (x86)\bitComposer Games\S.T.A.L.K.E.R. - Call of Pripyat`) the user copied
  into the app's Documents via iTunes/Apple Devices **File Sharing** (`On My iPhone → OpenXRay`):
  `resources/` (2.8 GB — `configs.db` has system.ltx, + textures/meshes/shaders), `localization/`
  (679 MB), `patches/` (34 MB). NOT yet copied: `levels/` (854 MB, needed to load a level);
  skipped: `mp/` (multiplayer). The bundled `fsgame.ltx`'s `$arch_dir_*$` entries mount these
  and the engine reads them directly. Documents also holds the engine-seeded `_appdata_`
  (writable logs/saves), `fsgame.ltx`, and `gamedata` (OpenXRay overlay) from first launch.

## The deps prefix (input to the engine build)

CI job `deps` produces a prefix per SDK (uploaded as `ios-deps-iphoneos` /
`ios-deps-iphonesimulator`, ~3.2 MB). Reproduce:

```
cmake -S cmake/ios/deps -B build/ios-deps-<sdk> \
  -DIOS_TOOLCHAIN_FILE=$PWD/cmake/toolchains/ios.toolchain.cmake \
  -DPLATFORM=OS64|SIMULATORARM64 -DDEPLOYMENT_TARGET=15.0 -DENABLE_BITCODE=OFF \
  -DCMAKE_INSTALL_PREFIX=$PWD/build/ios-prefix-<sdk>
cmake --build build/ios-deps-<sdk> --parallel
```
Prefix layout: `lib/{libSDL2,libopenal,libogg,libvorbis,libvorbisfile,libtheora,liblzo2}.a`
+ `include/{SDL2,AL,ogg,vorbis,theora,lzo}` + CMake configs (SDL2Config.cmake, OggConfig…).
**Note:** LuaJIT is NOT in this prefix — the engine builds it itself from
`Externals/LuaJIT-proj` (already iOS-fixed). jpeg-turbo and mimalloc were intentionally
skipped (both optional; see below).

## Phase 2d — iOS branch in the engine CMake (next up)

Goal: `cmake -B build` for the whole engine configures + compiles for iOS, finding the
deps at the prefix, static, one binary. Concrete steps:

1. **`cmake/XRay.Compiler.GNULike.cmake` lines 143-152** (`if (NOT WIN32)` block):
   the `find_package(...)` calls run for all non-Windows. For iOS, point them at the
   deps prefix via `-DCMAKE_FIND_ROOT_PATH=<prefix>` / `-DCMAKE_PREFIX_PATH=<prefix>` on
   the configure line (leetal FIND_ROOT modes = BOTH, so this works — same as the deps
   superbuild). `find_package(JPEG)` (line 146) and `find_package(mimalloc ...)` (151)
   are NOT `REQUIRED` → absent is fine; mimalloc-absent auto-selects `MEMORY_ALLOCATOR=
   standard` (lines 155-159). SDL2/OpenAL/Ogg/Vorbis/Theora/LZO are REQUIRED and present.
2. **Force static:** configure with `-DBUILD_SHARED_LIBS=OFF` on iOS so `XRAY_STATIC_BUILD`
   activates and every module links into one binary (kills the dlopen concern).
3. **LuaJIT interpreter mode — CRITICAL:** the engine's iOS config MUST set
   `-DLUAJIT_DISABLE_JIT=ON` (or set it in an iOS branch). Without it the build fails with
   `undeclared identifier 'lj_cf_jit_util_trace*'` (host buildvm emits JIT libdefs that
   iOS's interpreter-only `lib_jit.c` doesn't define). This is exactly what
   `cmake/ios/luajit-check` proved.
4. **pthread/dl:** `src/xrCore/CMakeLists.txt:457` (`pthread`) and `:471` (`dl`), and
   `Externals/imgui-proj/CMakeLists.txt:29` (`dl`). On iOS these resolve via libSystem /
   the SDK's `libdl.tbd`, so they *may* just work — try first, guard with
   `NOT XR_PLATFORM_APPLE_IOS` only if the link complains.
5. **Confirm disabled on iOS:** AGS_SDK, GameSpy, DiscordGameSDK, RenderDoc, and the
   DirectX renderers (xrRenderDX11 / xrRenderPC_R4 / xrRender_R2) — already non-Windows
   gated; verify they stay out. `Externals/CMakeLists.txt` also builds luabind, xrLuaFix,
   OPCODE, ode (physics), imgui-proj — these must compile for iOS too.
6. **Renderer link tension (watch out):** the engine target `xr_3da` links `xrRender_GL`,
   which uses desktop GL via glad. iOS has no desktop GL — full renderer port is Phase 4.
   For a Phase 2e *link*, either (a) get xrRender_GL to compile/link against a GLES/ANGLE
   loader (start of Phase 4), or (b) temporarily link a stub so the rest of the engine
   links first. Decide when we get there; don't assume xrRender_GL compiles unchanged.

## Phase 2e — link the whole engine

Add an iOS engine build to `ios.yml`: build the deps prefix, then
`cmake -B build -DCMAKE_TOOLCHAIN_FILE=... -DPLATFORM=... -DDEPLOYMENT_TARGET=15.0
-DENABLE_BITCODE=OFF -DBUILD_SHARED_LIBS=OFF -DLUAJIT_DISABLE_JIT=ON
-DCMAKE_FIND_ROOT_PATH=<prefix> -DCMAKE_PREFIX_PATH=<prefix>` then `cmake --build`.
Exit criterion: one static iOS Mach-O with zero unresolved symbols.

## Recurring gotchas (already solved once — expect them again in engine deps)

- **CMake 4.x** on runners rejects `cmake_minimum_required(VERSION < 3.5)` → pass
  `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` (used for vorbis/lzo).
- **Xcode 26.5 clang** promotes new warnings to errors (e.g. openal's
  `-Werror=function-effects` → pre-seeded `HAVE_WFUNCTION_EFFECTS=OFF`). Watch for other
  `-Werror=…` in submodules.
- **Host vs target:** any tool that must run at build time (like LuaJIT's minilua/buildvm)
  must be built native (`--target=<host>-apple-macos`, `-DCMAKE_OSX_SYSROOT=macosx`), not
  for iOS.

## Workflow reminders

- Only the `iOS` workflow runs on `ios-port` (cibuild/stylecheck disabled for the branch).
  Repo is public → macOS CI is free. Docs-only commits skip CI (`paths-ignore`).
- Work in small committed slices; end each green. Iterate on CI via
  `gh run watch <id> --exit-status` and `gh run view <id> --log-failed`.
