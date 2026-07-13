# OpenXRay — iOS port

Status of the effort to bring the OpenXRay (xray-16) engine to iOS. This is a fan
project; you must own a legal copy of S.T.A.L.K.E.R. to supply game assets.

## Bottom line

**Feasibility: hard but not impractical.** The platform / build / scripting layers are
in unusually good shape for a mobile port:

- SDL2 already abstracts the window/input/GL layer and supports iOS natively.
- ARM64 + NEON is a first-class, already-supported architecture (`sse2neon`).
- A static single-binary build path already exists (`BUILD_SHARED_LIBS=OFF`,
  `XRAY_STATIC_BUILD`), which is what iOS requires (no `dlopen` of external dylibs).
- CI already cross-builds **macOS arm64** — iOS is also arm64, so a large part of the
  Apple toolchain story is proven.
- LuaJIT already degrades to interpreter mode on iOS.

The work is dominated by **one very large workstream** (the renderer, Phase 4) and a
second sizeable one (touch controls + UI, Phase 5). Booting to the main menu is a
realistic near-term proof; a fully playable build is a multi-month effort.

## Build model (important)

**iOS binaries cannot be built on Windows.** The iPhoneOS SDK, clang sysroot and
`codesign` are macOS-only. The dev machine here is Windows, so the workflow is:

> Author every change on Windows → push → **build on GitHub Actions macOS runners**
> (they carry Xcode). The repo is public, so macOS CI minutes are free and unlimited.

CMake is driven with the vendored [leetal ios-cmake](https://github.com/leetal/ios-cmake)
toolchain (`cmake/toolchains/ios.toolchain.cmake`), selecting `-DPLATFORM=SIMULATORARM64`
(simulator) or `-DPLATFORM=OS64` (device).

## Distribution model

Target is **personal sideloading via SideStore / AltStore**, not the App Store (which
would reject a S.T.A.L.K.E.R. engine on IP grounds and forbids JIT). Sideloaded apps
carry `get-task-allow`, which also unlocks optional JIT (see below).

### LuaJIT / JIT

- **Baseline: LuaJIT in interpreter mode** — always works, no user setup, and LuaJIT's
  interpreter is hand-written assembly (fast). This is the default.
- **Optional: full JIT** for users who enable it per-launch via SideStore's JIT feature
  (StikDebug / JitStreamer, iOS 17.4+). Requires a LuaJIT build with JIT force-enabled
  for iOS; JIT is re-enabled each cold start and is broken/limited on iOS 26+.
- The engine must never hard-crash if RWX allocation fails — fall back to interpreter.

## Phased plan

| Phase | Goal | Status |
|------|------|--------|
| **1. Toolchain + CI** | A trivial iOS target compiles/links on CI, proving the Windows→macOS-CI→iOS pipeline. | 🚧 in progress |
| **2. Deps + link** | Every C dependency cross-builds for iphoneos/simulator; whole engine links into one static app binary (need not boot). | ⬜ |
| **3. Boot to window** | App launches via `SDL_main` under `CADisplayLink`, survives background/foreground/rotation, resolves sandbox paths, reaches engine init / console with a cleared frame. | ⬜ |
| **4. Renderer (GLES 3.0 / ANGLE-on-Metal)** | Desktop GL 4.1 + ~286 GLSL-410 shaders → GLES 3.0; DXT/BC textures → ASTC. Main menu first, then in-game scenes. **The dominant workstream.** | ⬜ |
| **5. Touch, UI, playability** | Touch / virtual-gamepad input (+ first-class MFi controller), UI adaptation, frame pacing, on-device signing. | ⬜ |

### Phase 1 deliverables (this branch)

- `cmake/toolchains/ios.toolchain.cmake` — vendored leetal iOS toolchain.
- `misc/ios/smoketest/` — engine-free target that asserts it built for arm64 iOS.
- `.github/workflows/ios.yml` — macOS CI building the smoke test for simulator + device.
- `src/Common/Platform.hpp` — new `XR_PLATFORM_APPLE_IOS` / `XR_PLATFORM_APPLE_MACOS`
  distinction via `<TargetConditionals.h>` (no behavior change on macOS).

## Key file references (from codebase analysis)

Renderer (Phase 4, the hard part):
- `src/Layers/xrRenderPC_GL/glHW.cpp` — requests GL Core 4.1 context (needs GLES 3.0 ES profile).
- `src/Layers/xrRenderPC_GL/rgl_shaders.cpp` — prepends `#version 410`; central place to emit `#version 300 es` + precision.
- `res/gamedata/shaders/gl/` — ~286 GLSL shaders + shared shim `common.h`.
- `src/Layers/xrRenderPC_GL/glTexture.cpp` — DDS/DXT upload via `gli` (needs ASTC path).

Lifecycle / entry (Phase 3):
- `src/xrEngine/x_ray.cpp` `CApplication::Run` — blocking main loop; refactor to a tick under `SDL_iPhoneSetAnimationCallback`.
- `src/xrEngine/Device.cpp` — `Sleep()` frame limiter (bypass on iOS).
- `src/xrCore/LocatorAPI.cpp`, `xrCore.cpp` — path setup (bundle read-only + `SDL_GetPrefPath` writable).

Input (Phase 5):
- `src/xrEngine/xr_input.cpp` `CInput::OnFrame` — add `SDL_FINGER*` / gesture source; MFi controller code already present.

## Open decisions (defaults chosen; tell us to change)

1. **Rendering strategy** → default **ANGLE-on-Metal** (GLES 3.0 translated to Metal; Apple's supported forward path). Alternatives: native EAGL GLES 3.0 (simplest, deprecated) or native Metal (largest effort). Shapes Phase 4.
2. **Bring-up order** → **simulator first** (no signing), then device via SideStore.
3. **Min iOS target** → **15.0**, arm64 only.
4. **Which STALKER game + assets** → needed by Phase 4 for real rendering tests. Undecided.
5. **Definition of done** → phase-by-phase; "boots to main menu" is the first meaningful milestone.
