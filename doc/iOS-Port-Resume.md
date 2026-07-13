# iOS port — resume guide (start of Phase 2d)

Quick-start context to continue the iOS port after a break. Full detail:
[iOS-Port.md](iOS-Port.md) (overview) and [iOS-Port-Plan.md](iOS-Port-Plan.md) (58-task plan).
**Per-slice history (what was done / how / every error → root cause → fix) and the
list of authoritative references lives in [iOS-Port-Journal.md](iOS-Port-Journal.md) —
read it to avoid re-deriving, and append an entry there after each slice.**

## Where we are (2026-07-13)

Branch **`ios-port`** (HEAD `82b76ecfa`), all green on CI (`.github/workflows/ios.yml`,
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
  via one guard var `XRAY_PLATFORM_IOS` in `cmake/XRay.Build.cmake`; new `engine-configure`
  CI job (`needs: deps`, `-G Xcode`). Deps resolve from the prefix. **Next: Slice 3** —
  compile the Externals static libs (add `IMGUI_IMPL_OPENGL_ES3`), then Slice 4 (engine libs).

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
