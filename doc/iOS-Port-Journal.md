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
