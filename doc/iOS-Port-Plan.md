# OpenXRay iOS port — detailed implementation plan

The exhaustive, task-by-task checklist for porting the OpenXRay (xray-16) engine to iOS. It complements [iOS-Port.md](iOS-Port.md) (the high-level overview): read that first for feasibility, build model and distribution, then use this file to execute.

> This project is a fan effort; you must own a legal copy of S.T.A.L.K.E.R. to supply game assets. iOS binaries are built on GitHub Actions macOS runners (the Windows dev machine cannot build iOS) and distributed via SideStore sideloading, not the App Store.

## Current status — 2026-07-13

**Phase 1 is complete and green on CI.** The `ios-port` branch builds on GitHub Actions
macOS runners for both the arm64 **simulator** (compile check) and the arm64 **device**,
and the device leg produces an unsigned, SideStore-ready `.ipa` artifact
(`OpenXRay-ios-smoketest-ipa`). Only the `iOS` workflow runs on this branch — the heavy
desktop matrix and StyleCheck are disabled here. Some task statuses below were captured
against the branch HEAD before the final green run; treat the CI-verified device build and
`.ipa` packaging as done. **Phase 2 in progress** via the `cmake/ios/deps` superbuild
(CI job `deps`, artifacts `ios-deps-iphoneos` / `ios-deps-iphonesimulator`):
- **2a ✅** SDL2 2.32.10 (static, both SDKs).
- **2b ✅** libogg 1.3.6 + libvorbis 1.3.7 + lzo 2.10 (static, both SDKs; all verified arm64).
  Note: macOS runners ship CMake 4.x, which rejects `cmake_minimum_required(<3.5)`; the
  superbuild passes `CMAKE_POLICY_VERSION_MINIMUM=3.5` for older deps (vorbis, lzo).
- **theora ✅** libtheora 1.1.1 via an injected CMake build (`cmake/ios/theora`) over the
  autotools-only source — portable no-asm source set; engine links only `Theora::Theora`.
- **OpenAL ✅** OpenAL Soft 1.25.2 static (CoreAudio backend). Xcode 26.5's clang trips
  `-Werror=function-effects`; the superbuild pre-seeds `HAVE_WFUNCTION_EFFECTS=OFF`.
- **LuaJIT ✅** cross-compiled for iOS (both SDKs) via `Externals/LuaJIT-proj` fixes +
  the `cmake/ios/luajit-check` driver. Host codegen tools (minilua/buildvm) build natively
  on macOS (`--target=<host>-apple-macos`, `-DCMAKE_OSX_SYSROOT=macosx`) while the target
  lib targets iphoneos; interpreter mode (`LUAJIT_DISABLE_JIT=ON`) keeps the arch probe,
  host tools and target sources consistent. All iOS-guarded — no change to other platforms.
- **7/7 deps done** (SDL2, OpenAL, ogg, vorbis, theora, lzo2, LuaJIT — all arm64, both SDKs).

**Phase 2 engine bring-up (2d/2e) — the WHOLE ENGINE COMPILES for iOS.** One guard var
`XRAY_PLATFORM_IOS` (`cmake/XRay.Build.cmake`) forces static + LuaJIT interpreter and gates
off GameSpy/mimalloc/packaging/install. The CI job `engine-build` (`-G Xcode`, `needs: deps`)
configures → compiles all Externals → all 12 support libs (clean first try) → xrEngine +
xrRender_GL (clean) → xrGame (2 fixes). New gotcha class **Xcode generator vs build tooling**
(TESTARCH sysroot, `CMAKE_DEFAULT_BUILD_TYPE`, LuaJIT host-tool generator, `lj_vm.S`→ASM).
**Phase 2 COMPLETE (14/14) 🎉** — the whole engine links into one arm64 iOS Mach-O
(`bin/aarch64/Release/xr_3da.app/xr_3da`, zero unresolved symbols, both SDKs). Link
closed with only two fixes: disable JPEG on iOS (host `libjpeg.dylib` mismatch) and build
GameSpy for iOS (xrGame references it; it compiled clean). Everything else — deps, all
engine libs, the glad GL renderer, OpenAL (iOS SDK framework), pthread/dl — resolved with
no changes. **Next: Phase 3** — boot to a window with the iOS app lifecycle (SDL_main/UIKit,
per-frame tick, sandbox paths, real MACOSX_BUNDLE app target). Full per-slice history +
every error→fix: [iOS-Port-Journal.md](iOS-Port-Journal.md).

Controller/touch design for Phase 5 draws on the user's OpenGothic iOS work — see
[iOS-Controller-Prior-Art.md](iOS-Controller-Prior-Art.md).

**Target game: Call of Pripyat 1.6.02.** OpenXRay builds one `xr_3da` binary; the game
variant is runtime-selected via `fsgame.ltx` + gamedata, so no build-target change is
needed — the CoP decision drives which gamedata we bundle and test with (Phase 4). Shadow
of Chernobyl is not yet playable on OpenXRay and is not a target.

## How to read a task

Every task carries: **Goal** (why it exists) · **Steps** (concrete ordered sub-steps: exact symbols, CMake vars, flags, `#ifdef` guards) · **Files** (repo-relative, with line refs where known) · **Acceptance** (verifiable done-criteria) · **Risks** (easy-to-miss gotchas). Status: ✅ done · 🟡 partial · ⬜ todo. Effort: trivial / low / medium / high / very-high.

## Phases at a glance

| # | Phase | Tasks | Done | Effort focus |
|---|-------|:-----:|:----:|--------------|
| 1 | [iOS toolchain + CI pipeline](#phase-1) | 10 | 6/10 | mostly low/medium |
| 2 | [Cross-build dependencies + link full engine (static)](#phase-2) | 14 | 14/14 | 4× high+ |
| 3 | [Boot to a window with iOS app lifecycle](#phase-3) | 11 | 1/11 | 3× high+ |
| 4 | [GLES 3.0 / ANGLE-on-Metal renderer + shaders + textures](#phase-4) | 13 | 1/13 | 5× high+ |
| 5 | [Touch / controls, UI adaptation, playability](#phase-5) | 10 | 0/10 | 5× high+ |
| 6 | [Performance & polish (post-playable)](#phase-6) | 2 | 0/2 | medium/high |

## Contents

- **Phase 1 — iOS toolchain + CI pipeline**
  - ✅ 1.1 Vendor the leetal ios-cmake cross-compile toolchain
  - ✅ 1.2 Engine-free iOS smoke-test target
  - ✅ 1.3 Platform macro: XR_PLATFORM_APPLE_IOS distinction
  - ✅ 1.4 iOS CI workflow on macOS runners (simulator + device)
  - 🟡 1.5 Confirm and harden the OS64 (arm64 device) build
  - ✅ 1.6 Package an unsigned, SideStore-ready .ipa artifact
  - 🟡 1.7 Pin CI action versions / supply-chain hardening
  - ✅ 1.8 Disable the heavy desktop CI (cibuild.yml + StyleCheck) on ios-port
  - 🟡 1.9 Document build, artifact fetch, and sideload procedure
  - ⬜ 1.10 End-to-end CI verification & branch gardening
- **Phase 2 — Cross-build dependencies + link full engine (static)**
  - ✅ 2.1 Choose and implement the third-party deps acquisition/superbuild strategy for iOS
  - ✅ 2.2 Cross-build SDL2 for iphoneos + simulator and expose SDL2::SDL2
  - ✅ 2.3 Cross-build OpenAL for iOS and expose OpenAL::OpenAL
  - ✅ 2.4 Cross-build libogg / libvorbis / libtheora and satisfy the repo's Find modules
  - ✅ 2.5 Cross-build lzo2 and satisfy FindLZO (LZO::LZO)
  - ✅ 2.6 Optionally cross-build jpeg-turbo (JPEG::JPEG) or cleanly disable JPEG on iOS — disabled on iOS (host libjpeg.dylib broke the link; JPEG optional, no-jpeg source path)
  - ✅ 2.7 Force static build + standard allocator on iOS (BUILD_SHARED_LIBS=OFF, MEMORY_ALLOCATOR=standard, skip mimalloc)
  - ✅ 2.8 Add an iOS branch to XRay.Compiler.GNULike.cmake that finds deps from CMAKE_FIND_ROOT_PATH and applies iOS-correct flags
  - ✅ 2.9 Fix Externals/LuaJIT-proj for iOS cross-compile: sysroot, host-tool decoupling, arch detection, JIT-optional variant (+ Xcode-generator: host tools single-config, lj_vm.S LANGUAGE ASM)
  - ✅ 2.10 Apple/iOS-guard the bare pthread and dl link entries (xrCore, imgui-proj) — not needed: both resolve via libSystem; the link closed with no guards
  - ✅ 2.11 Audit AGS_SDK / GameSpy / DiscordGameSDK / RenderDoc / DX11 R4 — AGS/Discord/RenderDoc/DX11 stay out; GameSpy is instead BUILT for iOS (xrGame references it; POSIX SDK compiled clean) so the link closes
  - ✅ 2.12 Minimal source/compile fixes to close the iOS link — xrGame: xr_string varargs `.c_str()`, system()/xdg-open iOS guards; JPEG + GameSpy resolved
  - ✅ 2.13 Link the whole engine + game into one static Mach-O — `bin/aarch64/Release/xr_3da.app/xr_3da` is a Mach-O arm64 executable, zero unresolved symbols (proper MACOSX_BUNDLE Info.plist/entitlements/gamedata is Phase 3.9)
  - ✅ 2.14 Extend ios.yml CI to build deps + full engine for both SDKs and verify the single Mach-O — `engine-build` job: configure → Externals → support libs → core libs → link xr_3da, both SDKs
- **Phase 3 — Boot to a window with iOS app lifecycle**
  - ✅ 3.1 Route the entry point through SDL2main / SDL_main on iOS
  - ⬜ 3.2 Refactor CApplication::Run's blocking loop into a per-frame tick under SDL_iPhoneSetAnimationCallback
  - ⬜ 3.3 Bypass the Sleep()-based frame limiter and busy-waits on iOS
  - ⬜ 3.4 Wire iOS lifecycle (background/foreground/terminate/low-memory) via SDL_AddEventWatch into seqAppActivate/Deactivate + audio pause
  - ⬜ 3.5 Add an iOS single-fullscreen-window branch in Device_Initialize.cpp
  - ⬜ 3.6 Neutralize video-mode enumeration / windowed handling and treat SIZE_CHANGED as rotation
  - 🟡 3.7 Resolve sandbox paths — fsgame.ltx + base gamedata seeded from the bundle into writable Documents on first launch (LocatorAPI iOS branch); full CoP gamedata layout / split read-only vs writable still to refine
  - ⬜ 3.8 Replace the second-window software splash with a LaunchScreen storyboard
  - 🟡 3.9 Package xr_3da as a MACOSX_BUNDLE iOS app — custom Info.plist (bundle id, MinimumOSVersion, UIDeviceFamily, UIFileSharingEnabled), app icon, base gamedata bundled; entitlements/launch-storyboard/full-gamedata still to add
  - 🟡 3.10 Extend ios.yml CI to build the real xr_3da app — done: packages the real app .ipa + publishes to `ios-dev` release with a SideStore source; still to do: retire the smoketest job
  - 🟡 3.11 Request a GLES 3.0 context on iOS so the engine presents a cleared frame (renderer seam with Phase 4) — context request flipped to ES 3.0 + gladLoadGLES2 (Slice 4.1); cleared-frame proof pending
- **Phase 4 — GLES 3.0 / ANGLE-on-Metal renderer + shaders + textures**
  - ✅ 4.1 Wire xrRender_GL into the iOS build — target links from Phase 2 (USE_OGL); GLES loader (gladLoadGLES2, merged glad) wired in glHW.cpp; native EAGL for now, ANGLE deferred
  - 🟡 4.2 Create a GLES 3.0 context: SetPrimaryAttributes ES profile + gladLoadGLES2 done (fixes the launch SIGKILL in xrRender_test_hw); ANGLE/EAGL selection + fallback still to add
  - 🟡 4.3 Central shader front-end: emit '#version 300 es' + precision, force the monolithic non-separable path — tooling done first: offline GLSL-ES gate `misc/ios/shadercheck/glsl_es_check.py` + CI job `shader-check` (assembles each shader as ES 3.00 and runs glslangValidator; informational until the tree compiles). Front-end emission change still to do.
  - ⬜ 4.4 Rewrite the shared shims common.h and common_samplers.h for GLSL ES 3.00
  - ⬜ 4.5 Regenerate the 81 iostructs/ headers: name-matched varyings, drop gl_PerVertex, layout(location) fragment outputs
  - 🟡 4.6 Replace glBindFragDataLocation and reconcile the compile/link traits for GLES monolithic programs — glBindFragDataLocation guarded out on ES (Slice 4.6a, fixes the _LinkPP SIGKILL); layout(location) outputs in shaders + link-trait reconcile still to do
  - ⬜ 4.7 Replace desktop-only GL calls in the runtime (draw, buffers, formats, polygon mode, screenshot)
  - ⬜ 4.8 HW caps, extension gating, and shader-binary cache for GLES
  - ⬜ 4.9 Texture pipeline: gli ES30 profile + DXT/BC->ASTC offline transcode + runtime RGBA fallback
  - ⬜ 4.10 Validate the deferred G-buffer under ES (float/half color attachments, FBO completeness, MRT)
  - ⬜ 4.11 Configure the ImGui OpenGL3 backend for GLES3 (console/debug/UI overlay)
  - ⬜ 4.12 Renderer module registration and shader-tree packaging for iOS
  - ⬜ 4.13 Bring-up validation: main menu first, then a test level
- **Phase 5 — Touch / controls, UI adaptation, playability**
  - ⬜ 5.1 Touch input core: CTouchInput module wired into CInput::OnFrame reading SDL_FINGER*/SDL_MULTIGESTURE
  - ⬜ 5.2 Gameplay virtual stick (move) + right-drag look synthesized through ControllerState/IInputReceiver
  - ⬜ 5.3 On-screen action buttons (fire/aim/use/reload/jump/crouch/weapon-switch) mapped to game actions
  - ⬜ 5.4 On-screen controls rendering, contextual visibility, layout config + tuning cvars
  - ⬜ 5.5 Menu / inventory / list UI touch adaptation (tap, drag-scroll, drag-drop) keeping controller nav first-class
  - ⬜ 5.6 Soft keyboard / text input routing (console, chat, save names, MP name) via SDL_StartTextInput
  - ⬜ 5.7 MFi / Bluetooth controller first-class on iOS (SDL GameController + Info.plist + haptics)
  - ⬜ 5.8 App lifecycle, frame pacing, thermals & LOWMEMORY: no GL while backgrounded, CADisplayLink pacing
  - ⬜ 5.9 iOS application bundle target: Info.plist, entitlements (get-task-allow), launch storyboard, resource bundling
  - ⬜ 5.10 On-device signing path, optional LuaJIT-JIT enablement with interpreter fallback, and .ipa packaging for SideStore
- **Phase 6 — Performance & polish (post-playable)**
  - ⬜ 6.1 Dynamic render-scale infrastructure: render the 3D scene into an off-screen color target at a configurable fraction of native resolution, then present it to the backbuffer through a full-screen pass. Prerequisite for any upscaler; add a `r__render_scale` cvar. High-DPI iOS panels make native-res rendering very expensive, so this alone is a big perf lever.
  - ⬜ 6.2 **FSR 1.0 upscaling**: port AMD FidelityFX Super Resolution 1.0 (EASU edge-adaptive upscale + RCAS sharpening) to GLSL ES 3.00 as the present pass over the render-scale target. FSR 1.0 is spatial (no motion vectors / no temporal history), which suits the deferred GL renderer and mobile — much simpler to integrate than FSR2/temporal. Expose render-scale + sharpness cvars; validate through the same `shader-check` gate. Quality/perf win to hit a playable framerate on device.

---

<a id="phase-1"></a>

## Phase 1 — iOS toolchain + CI pipeline

> **Phase goal.** Prove and lock down the Windows-authored -> macOS-CI -> iOS build pipeline end to end, with zero engine code. A trivial arm64 iOS target must cross-compile and link on GitHub Actions macOS runners for both the arm64 simulator (SIMULATORARM64, fast compile sanity) and the arm64 device (OS64), the device leg must emit an unsigned, SideStore-ready .ipa artifact, CI actions must be pinned, the heavy desktop matrix (cibuild.yml) and StyleCheck must not consume minutes on this branch, and the build/fetch/sideload procedure must be documented. A red build here indicts the toolchain/SDK/CI wiring alone, never the engine. Most of this is already implemented on the current HEAD (1feccc641); the residual work is CI-green confirmation of the device leg, iOS bundle-installability review, optional supply-chain hardening, and the operator documentation.

### ✅ 1.1 Vendor the leetal ios-cmake cross-compile toolchain

`status: done` · `effort: low`

**Goal.** Give CMake a single, self-contained toolchain file that turns a macOS runner into an iOS cross-compiler (arm64 device + arm64 simulator), so no per-developer Xcode project or SDK path wrangling is needed. This is the foundation every later iOS build depends on.

**Steps**

1. Drop the upstream leetal ios.toolchain.cmake verbatim under cmake/toolchains/ (already committed in e81bedbb8) so it is version-controlled and reproducible rather than fetched at build time.
2. Confirm the file is consumed only via -DCMAKE_TOOLCHAIN_FILE=<abs path>/cmake/toolchains/ios.toolchain.cmake and never included by the engine's own CMake.
3. Note the knobs the pipeline relies on: PLATFORM (OS64 | SIMULATORARM64), DEPLOYMENT_TARGET, ENABLE_BITCODE, and that the toolchain sets CMAKE_SYSTEM_NAME=iOS (line 769) which makes CMake 3.14+ generate an iOS-flavoured bundle Info.plist.
4. Record that code signing is globally disabled by the toolchain (lines 825-826), so device builds link without a provisioning profile / DEVELOPMENT_TEAM.

**Files**

- `cmake/toolchains/ios.toolchain.cmake (1177 lines; header credits leetal/gerstrong/cristea, BSD-licensed)`
- `cmake/toolchains/ios.toolchain.cmake:168-170 (valid PLATFORM enum incl. OS64, SIMULATORARM64)`
- `cmake/toolchains/ios.toolchain.cmake:266-295 (DEPLOYMENT_TARGET default logic; iOS default 13.0)`
- `cmake/toolchains/ios.toolchain.cmake:322-332 (OS64 -> arm64-apple-ios triple), :399-405 (SIMULATORARM64 -> arm64-apple-ios-simulator triple)`
- `cmake/toolchains/ios.toolchain.cmake:653-657 (ENABLE_BITCODE default OFF), :769 (CMAKE_SYSTEM_NAME iOS -> CMake uses iOS bundle Info.plist template)`
- `cmake/toolchains/ios.toolchain.cmake:825-826 (CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED/ALLOWED = NO globally)`

**Acceptance**

- [ ] File exists at cmake/toolchains/ios.toolchain.cmake and is tracked by git.
- [ ] Passing -DCMAKE_TOOLCHAIN_FILE=.../ios.toolchain.cmake -DPLATFORM=OS64 configures with SDK_NAME=iphoneos; -DPLATFORM=SIMULATORARM64 configures with SDK_NAME=iphonesimulator.
- [ ] No network fetch of the toolchain at configure time.

**Risks / easy to miss**

- Toolchain is third-party BSD-licensed; keep the original copyright header intact for attribution.
- Its default DEPLOYMENT_TARGET (13.0) differs from the project's chosen 15.0 — always pass -DDEPLOYMENT_TARGET explicitly (see 1.4) so the default never silently applies.
- Do NOT let the root CMake (add_subdirectory(misc)) or any engine CMakeLists include this toolchain as a normal module — it is a -DCMAKE_TOOLCHAIN_FILE input only.

### ✅ 1.2 Engine-free iOS smoke-test target

`status: done` · `effort: low`

**Goal.** Provide a standalone CMake project that compiles a single main.cpp asserting (at compile time) that it built for an Apple iOS arm64 target, and produces a .app bundle. Because it has ZERO engine dependencies, a failure unambiguously indicts the toolchain/SDK/CI rather than the port, which is the entire point of Phase 1.

**Steps**

1. Keep the project as a separate top-level CMake project (its own project() call) so it can be configured directly from misc/ios/smoketest without pulling in Externals/src/res.
2. Assert platform correctness at compile time via #error guards on __APPLE__, TARGET_OS_IPHONE, and arm64 so a mis-targeted toolchain fails to compile rather than silently producing a wrong-arch binary.
3. Build as MACOSX_BUNDLE so the output is ios_smoketest.app (an installable bundle shape), matching what later phases and the .ipa packaging expect.
4. Belt-and-suspenders: also set CODE_SIGNING_ALLOWED/REQUIRED = NO on the target (in addition to the toolchain's global setting) so the target is self-describing.
5. Emit a runtime printf so a future on-device/simulator run visibly confirms execution.

**Files**

- `misc/ios/smoketest/CMakeLists.txt (cmake_minimum_required 3.23; project OpenXRayIOSSmokeTest; add_executable ios_smoketest MACOSX_BUNDLE main.cpp)`
- `misc/ios/smoketest/CMakeLists.txt:22 (MACOSX_BUNDLE_GUI_IDENTIFIER io.github.openxray.iossmoketest)`
- `misc/ios/smoketest/CMakeLists.txt:28-29 (XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED/REQUIRED = NO on the target)`
- `misc/ios/smoketest/main.cpp:11 (#include <TargetConditionals.h>)`
- `misc/ios/smoketest/main.cpp:14-22 (#error guards: __APPLE__, TARGET_OS_IPHONE, __aarch64__/__arm64__)`
- `misc/ios/smoketest/main.cpp:27-31 (TARGET_OS_SIMULATOR vs device print)`

**Acceptance**

- [ ] cmake -B build -G Xcode -DCMAKE_TOOLCHAIN_FILE=.../ios.toolchain.cmake -DPLATFORM=SIMULATORARM64 -DDEPLOYMENT_TARGET=15.0 -DENABLE_BITCODE=OFF followed by cmake --build build --config Release yields build/Release-iphonesimulator/ios_smoketest.app.
- [ ] Same with -DPLATFORM=OS64 yields build/Release-iphoneos/ios_smoketest.app.
- [ ] lipo -info on the binary reports arm64; a macOS/x86 or arm64-desktop toolchain fails the #error guards.
- [ ] Configuring the project does NOT configure Externals/, src/, or res/.

**Risks / easy to miss**

- The CMake-auto-generated iOS Info.plist may lack MinimumOSVersion / UIDeviceFamily / CFBundleSupportedPlatforms=[iPhoneOS] required for real on-device install — acceptable for a pipeline proof, but see 1.5/1.6 before claiming true installability.
- Must stay engine-free forever; adding any engine include here defeats the isolation guarantee.
- Keep files LF/UTF-8/final-newline (see cross-cutting note on stylecheck) so a future merge to dev passes StyleCheck even though it is disabled on this branch.

### ✅ 1.3 Platform macro: XR_PLATFORM_APPLE_IOS distinction

`status: done` · `effort: trivial`

**Goal.** Give the engine a compile-time way to tell iOS (device/simulator) apart from macOS and tvOS under the existing XR_PLATFORM_APPLE umbrella, using <TargetConditionals.h>, without changing any existing macOS behavior. Every later phase's #ifdef guards hang off this macro.

**Steps**

1. Inside the existing #elif defined(__APPLE__) block, #include <TargetConditionals.h> and branch on TARGET_OS_IOS / TARGET_OS_TV / else(macOS) to define XR_PLATFORM_APPLE_IOS / XR_PLATFORM_APPLE_TVOS / XR_PLATFORM_APPLE_MACOS respectively.
2. Keep XR_PLATFORM_APPLE and XR_PLATFORM_POSIX defined for all Apple targets so existing Apple/POSIX code paths are unchanged.
3. Set _XRAY_PLATFORM_MARKER to 'iOS' / 'iOS Simulator' / 'Apple' so the build banner (XRAY_BUILD_CONFIGURATION2) self-identifies.
4. Continue including Common/PlatformApple.inl for all Apple sub-platforms (no new .inl needed in Phase 1; iOS-specific overrides land in later phases).

**Files**

- `src/Common/Platform.hpp:28-45 (Apple branch: XR_PLATFORM_APPLE + XR_PLATFORM_POSIX, includes <TargetConditionals.h>)`
- `src/Common/Platform.hpp:32-38 (TARGET_OS_IOS -> XR_PLATFORM_APPLE_IOS; TARGET_OS_SIMULATOR -> marker 'iOS Simulator')`
- `src/Common/Platform.hpp:39-41 (TARGET_OS_TV -> XR_PLATFORM_APPLE_TVOS)`
- `src/Common/Platform.hpp:43-44 (else -> XR_PLATFORM_APPLE_MACOS, marker 'Apple')`
- `src/Common/Platform.hpp:63-65 (XR_ARCHITECTURE_ARM64 for __aarch64__)`
- `src/Common/Platform.hpp:97-98 (still includes Common/PlatformApple.inl for all Apple)`

**Acceptance**

- [ ] Building for iOS defines XR_PLATFORM_APPLE_IOS and, on the simulator, keeps behavior identical except the marker string.
- [ ] macOS builds still resolve to XR_PLATFORM_APPLE_MACOS with the unchanged 'Apple' marker — no behavior change (confirmed: the desktop macOS build is unaffected).
- [ ] No new mandatory .inl file is required to compile.

**Risks / easy to miss**

- TARGET_OS_IPHONE is 1 for both iOS and tvOS; the code correctly keys iOS off TARGET_OS_IOS (not TARGET_OS_IPHONE) — do not regress this when extending.
- Order matters: check TARGET_OS_IOS/TV before the macOS else-branch.
- Later phases will want an iOS-specific PlatformApple split; do not prematurely fork the .inl in Phase 1.

### ✅ 1.4 iOS CI workflow on macOS runners (simulator + device)

`status: done` · `effort: medium`

**Goal.** Automate the whole pipeline on GitHub Actions: on every push to ios-port (and manual dispatch), configure+build the smoke test with the leetal toolchain for both SIMULATORARM64 and OS64 on macos-latest, and inspect the resulting Mach-O. This is the load-bearing workflow the entire port rides on since no iOS build is possible on the Windows dev machine.

**Steps**

1. Gate the workflow to push on ios-port + workflow_dispatch (deliberately no pull_request here in Phase 1; the smoke test is cheap and push-driven).
2. Use a matrix with two legs sharing one job body; carry per-leg sdk and a package_ipa boolean so only the device leg produces an artifact (see 1.6).
3. Emit toolchain versions first (sw_vers/xcodebuild/cmake) so runner-image drift is visible in logs when a future build breaks.
4. Configure with -G Xcode and the vendored toolchain, passing PLATFORM from the matrix, DEPLOYMENT_TARGET=15.0 (override the toolchain's 13.0 default), ENABLE_BITCODE=OFF, --log-level=VERBOSE.
5. Build Release; then inspect the produced .app binary with file / lipo -info / vtool -show-build to prove arch=arm64 and the correct load-command platform (iphoneos vs iphonesimulator).
6. Set timeout-minutes and fail-fast:false so one leg failing still reports the other.

**Files**

- `.github/workflows/ios.yml:17-21 (on: push branches ios-port + workflow_dispatch)`
- `.github/workflows/ios.yml:24-39 (job 'ios', runs-on macos-latest, matrix: device=OS64/iphoneos/package_ipa:true, simulator=SIMULATORARM64/iphonesimulator/package_ipa:false)`
- `.github/workflows/ios.yml:42 (actions/checkout@v4)`
- `.github/workflows/ios.yml:44-48 (Show toolchain versions: sw_vers, xcodebuild -version, cmake --version)`
- `.github/workflows/ios.yml:50-58 (Configure: -G Xcode, toolchain file, PLATFORM, DEPLOYMENT_TARGET=15.0, ENABLE_BITCODE=OFF, --log-level=VERBOSE)`
- `.github/workflows/ios.yml:60-62 (Build: cmake --build build --config Release)`
- `.github/workflows/ios.yml:64-73 (Inspect binary: find .app, file/lipo -info/vtool -show-build)`

**Acceptance**

- [ ] A push to ios-port triggers the 'iOS' workflow with two matrix legs (device (.ipa) and simulator (compile check)).
- [ ] Both legs reach the Build step; the simulator leg is confirmed green (per seed) and the device leg is expected green (confirm in 1.5).
- [ ] Inspect step prints lipo -info => arm64 and a valid LC_BUILD_VERSION for the respective platform.
- [ ] cibuild.yml/stylecheck.yml do NOT run on the same push (see 1.8).

**Risks / easy to miss**

- macos-latest image periodically bumps Xcode/SDK; the DEPLOYMENT_TARGET pin prevents min-version surprises but a hard SDK change can still break configure — the version-echo step is the diagnostic.
- -G Xcode is required for proper iOS bundle/plist generation; do not switch to the Ninja/Makefile generator for iOS.
- Simulator .app is not device-installable — never try to package it as an .ipa (guarded by package_ipa).

### 🟡 1.5 Confirm and harden the OS64 (arm64 device) build

`status: partial` · `effort: low`

**Goal.** Prove the device leg actually links to a valid unsigned arm64 iphoneos Mach-O in CI (not just the simulator), and confirm the bundle is well-formed enough to be SideStore-installable. This is the one item that can only be verified on macOS CI, never locally on Windows.

**Steps**

1. Push the branch (or workflow_dispatch) and open the 'iOS' run; confirm the device (.ipa) leg's Configure step reports SDK_NAME=iphoneos and an arm64-apple-ios15.0 triple.
2. Confirm Build succeeds with no 'code signing is required' / 'no provisioning profile' error (the toolchain's lines 825-826 plus the target's CODE_SIGNING_ALLOWED=NO should prevent it).
3. In Inspect, verify file => Mach-O arm64, lipo -info => arm64, and vtool -show-build => platform IOS (minos 15.0) NOT IOSSIMULATOR.
4. Harden the Inspect step (optional): grep the vtool/lipo output and fail the job if the platform is not IOS or the arch is not arm64, so a regression to a wrong target is caught automatically.
5. Review the generated Info.plist inside ios_smoketest.app (plutil -p) for CFBundleExecutable, CFBundleIdentifier, MinimumOSVersion, CFBundleSupportedPlatforms=[iPhoneOS], UIDeviceFamily; if any required-for-install key is missing, add a custom Info.plist (see 1.6 risk) — for a headless smoke test this is advisory, but it de-risks the first real installable build in Phase 3.

**Files**

- `.github/workflows/ios.yml:32-35 (device matrix leg: OS64 / iphoneos / package_ipa:true)`
- `.github/workflows/ios.yml:64-73 (Inspect binary step)`
- `cmake/toolchains/ios.toolchain.cmake:825-826 (global code-signing disabled -> device links without a provisioning profile)`
- `misc/ios/smoketest/CMakeLists.txt:28-29 (target-level signing disabled)`

**Acceptance**

- [ ] The device leg is green on macos-latest.
- [ ] vtool -show-build on the device binary shows platform IOS (not IOSSIMULATOR), minos 15.0, arm64.
- [ ] No signing/provisioning error appears in the Build log.
- [ ] Record the passing run URL in the docs (1.9) as the proof-of-pipeline milestone.

**Risks / easy to miss**

- A future runner may flip default signing behavior; the two-layer signing-disable (toolchain + target) is intentional redundancy — keep both.
- Missing MinimumOSVersion/UIDeviceFamily/CFBundleSupportedPlatforms in the auto-plist can make SideStore refuse the .ipa even though the binary is valid; verify with plutil before declaring the .ipa truly installable.
- Can only be confirmed on CI — do not mark fully done until a real macOS run is green.

### ✅ 1.6 Package an unsigned, SideStore-ready .ipa artifact

`status: done` · `effort: low`

**Goal.** Turn the device .app into a downloadable, unsigned .ipa (Payload/<App>.app zipped) and upload it as a CI artifact, so the operator can pull one file and sideload it via SideStore/AltStore (which re-sign on-device with the user's Apple ID and grant get-task-allow). Unsigned is exactly what these sideload tools expect.

**Steps**

1. Gate the packaging + upload steps on the matrix package_ipa flag so only OS64 (device) produces an .ipa; the simulator leg never does.
2. Locate the built .app via find build -maxdepth 4 -name 'ios_smoketest.app'; recreate a clean Payload/ dir; copy the .app into Payload/ (an .ipa is just a zip whose top-level entry is Payload/<App>.app).
3. zip -qr the Payload tree to OpenXRay-smoketest-unsigned.ipa; dump unzip -l so the log shows the archive layout.
4. Upload with actions/upload-artifact@v4, a stable artifact name (OpenXRay-ios-smoketest-ipa) and if-no-files-found: error so a packaging regression fails loudly.
5. Do NOT codesign, do NOT embed a provisioning profile, do NOT set DEVELOPMENT_TEAM — SideStore signs at install time.

**Files**

- `.github/workflows/ios.yml:75-86 (Package unsigned .ipa: gated on matrix.package_ipa; build Payload/, cp the .app in, zip -qr OpenXRay-smoketest-unsigned.ipa Payload, unzip -l to log contents)`
- `.github/workflows/ios.yml:88-94 (Upload .ipa: actions/upload-artifact@v4, name OpenXRay-ios-smoketest-ipa, if-no-files-found: error)`

**Acceptance**

- [ ] The device leg uploads exactly one artifact OpenXRay-ios-smoketest-ipa containing Payload/ios_smoketest.app/....
- [ ] unzip -l in the log shows the Payload/<App>.app structure.
- [ ] The simulator leg uploads no .ipa.
- [ ] Downloading the artifact yields a .ipa that SideStore accepts for install (installability gated on the Info.plist review in 1.5).

**Risks / easy to miss**

- An .ipa whose top-level dir is not exactly Payload/ will be rejected by installers — keep the Payload/ layout precise.
- macOS 'zip' preserves symlinks/permissions; the smoke test .app is flat so this is fine, but real app bundles later need -y or ditto to preserve symlinked frameworks.
- If the bundle Info.plist lacks install-required keys (see 1.5) SideStore may refuse it despite a correct zip — treat Info.plist correctness as part of 'SideStore-ready'.
- Artifact retention defaults to 90 days; note this in docs so operators don't rely on old runs.

### 🟡 1.7 Pin CI action versions / supply-chain hardening

`status: partial` · `effort: trivial`

**Goal.** Stop the iOS workflow from floating on mutable action refs (@main), so a third-party action update cannot silently change or break the pipeline. Pin at least to a major release tag; optionally to a full commit SHA for reproducibility.

**Steps**

1. Confirm ios.yml pins both actions it uses to @v4 (already done): actions/checkout@v4 and actions/upload-artifact@v4.
2. Decide policy: v-major tag (current) is adequate for a personal fork; for stronger supply-chain guarantees, repin to full 40-char commit SHAs with a trailing '# vX.Y.Z' comment.
3. Leave cibuild.yml/stylecheck.yml @main refs untouched in Phase 1 (they don't run on ios-port); note that they must be pinned before the port ever merges to dev.
4. Document the chosen pinning policy in doc/iOS-Port.md so future workflow edits follow it.

**Files**

- `.github/workflows/ios.yml:42 (actions/checkout@v4)`
- `.github/workflows/ios.yml:90 (actions/upload-artifact@v4)`
- `.github/workflows/cibuild.yml:24,161,251,312 (actions/checkout@main), :44,50,56,218 (actions/upload-artifact@main), :176 (actions/cache@main), :28 (microsoft/setup-msbuild@main) — disabled on this branch, out of Phase-1 scope`
- `doc/iOS-Port.md (record the pinning policy)`

**Acceptance**

- [ ] No @main (mutable) ref appears in .github/workflows/ios.yml.
- [ ] Every action ios.yml uses resolves to a fixed tag (or SHA).
- [ ] Pinning policy is written down for future iOS workflow steps (e.g. when Phase 2 adds submodule checkout / caching actions).

**Risks / easy to miss**

- A pure v-major tag can still move within the major line; only a full SHA is truly immutable — choose consciously.
- When Phase 2 adds actions/checkout with submodules and a cache action, apply the same pin policy then.
- Don't 'fix' cibuild/stylecheck @main refs on this branch — that would create merge noise for changes unrelated to iOS.

### ✅ 1.8 Disable the heavy desktop CI (cibuild.yml + StyleCheck) on ios-port

`status: done` · `effort: trivial`

**Goal.** Ensure that pushing to ios-port only spends CI on the tiny iOS smoke test, not the full Windows/Linux/BSD/macOS/flatpak matrix or the style/encoding/clang-format checks, which are irrelevant to a toolchain-proof branch and slow/expensive.

**Steps**

1. Add 'ios-port' to the push branches-ignore list in both cibuild.yml and stylecheck.yml (already committed in 1feccc641) with an explanatory comment pointing to ios.yml.
2. Leave the pull_request trigger intact so that, if/when an ios-port -> dev PR is opened, the full desktop build and StyleCheck still run as a merge gate (style is enforced at PR time, not per push).
3. Verify via the Actions tab that an ios-port push spawns only the 'iOS' workflow: mirror is skipped by its repo guard on the fork, labeler is PR-only, cibuild/stylecheck are branch-excluded.

**Files**

- `.github/workflows/cibuild.yml:4-11 (on: push -> branches-ignore: 'dependabot/*' AND 'ios-port'; pull_request; workflow_dispatch)`
- `.github/workflows/stylecheck.yml:4-10 (on: push -> branches-ignore: 'dependabot/*' AND 'ios-port'; pull_request; workflow_dispatch)`
- `.github/workflows/mirror.yml:7 (if: github.repository == 'OpenXRay/xray-16' -> no-op on the personal fork)`
- `.github/workflows/labeler.yml:3-6 (pull_request_target / pull_request_review only -> not triggered by ios-port pushes)`

**Acceptance**

- [ ] A push to ios-port shows exactly one workflow run ('iOS') in the Actions tab.
- [ ] cibuild.yml and stylecheck.yml list 'ios-port' under push.branches-ignore.
- [ ] A hypothetical PR from ios-port to dev would still trigger cibuild + stylecheck (pull_request trigger preserved).

**Risks / easy to miss**

- pull_request events are NOT branch-filtered here, so opening a PR (even draft) from ios-port will fan out the whole desktop matrix — expected, but be aware before opening one prematurely.
- Because StyleCheck won't run per-push, new iOS files can silently drift from LF/UTF-8/tab rules and only fail at PR time — keep them clean now (cross-cutting note).
- branches-ignore is push-only; delete/other events are unaffected (irrelevant here).

### 🟡 1.9 Document build, artifact fetch, and sideload procedure

`status: partial` · `effort: low`

**Goal.** Give the operator (and future contributors) a written, reproducible recipe: how to trigger the iOS build from a Windows machine, where to find and download the unsigned .ipa, and how to sideload it via SideStore — plus the pinned CI/branch policy. Without this, the pipeline is a black box only the author understands.

**Steps**

1. Add a 'Building & fetching artifacts' section to doc/iOS-Port.md: (a) push to ios-port OR use the Actions tab 'iOS' workflow -> 'Run workflow' (workflow_dispatch) to trigger a build entirely from a browser on Windows.
2. Document fetching: open the completed 'iOS' run -> Artifacts -> download OpenXRay-ios-smoketest-ipa (note the 90-day retention default).
3. Document sideloading: install SideStore/AltStore on the device, import the unsigned .ipa; SideStore re-signs with the user's Apple ID and grants get-task-allow (the same entitlement that later unlocks optional LuaJIT JIT).
4. State the pipeline invariants: iOS binaries build ONLY on macOS CI (never on Windows); min iOS 15.0, arm64 only; ENABLE_BITCODE=OFF; simulator leg is a compile-only sanity check and produces no .ipa.
5. Record the CI/branch policy: ios.yml is the only workflow on this branch; actions are pinned to @v4; cibuild/stylecheck are branch-excluded but still gate PRs to dev.
6. Optionally paste the first green device-run URL (from 1.5) as the proof-of-pipeline milestone.

**Files**

- `doc/iOS-Port.md (90 lines; has feasibility, build model, distribution model, phased plan, key file refs, open decisions — but currently NO 'how to build / fetch artifacts / sideload' section; grep found only passing SideStore mentions)`
- `doc/iOS-Port.md:60-66 (Phase 1 deliverables list — extend with the run/fetch how-to)`
- `README.md (optionally cross-link the iOS doc)`

**Acceptance**

- [ ] doc/iOS-Port.md contains an explicit, step-by-step 'how to trigger the build, download the .ipa, and sideload' section.
- [ ] A reader on Windows with no prior context can trigger a build and obtain an installable .ipa by following the doc.
- [ ] The min-target (15.0/arm64), bitcode-off, and macOS-CI-only invariants are written down.
- [ ] The doc notes artifact retention and the pinning/branch-exclusion policy.

**Risks / easy to miss**

- Keep the doc in sync as the workflow evolves in Phase 2+ (submodule checkout, real engine link) — stale build docs are worse than none.
- Don't over-promise on-device runnability for the smoke test — it has no UI; it proves the pipeline, not a launchable app (that is Phase 3).
- Sideload steps depend on iOS version / SideStore feature availability (JIT via StikDebug is iOS 17.4+ and degraded on iOS 26+) — phrase as 'baseline install works everywhere; JIT is optional/version-gated'.

### ⬜ 1.10 End-to-end CI verification & branch gardening

`status: todo` · `effort: low`

**Goal.** Close Phase 1 by verifying the complete pipeline on a real macOS run and confirming the branch is quiet (only the iOS workflow spends minutes). This is the phase's definition-of-done gate that ties tasks 1.4-1.9 together.

**Steps**

1. Trigger a run (push or workflow_dispatch) and confirm BOTH matrix legs are green: simulator (compile check) and device (.ipa).
2. Download OpenXRay-ios-smoketest-ipa and verify locally (unzip -l) that it contains Payload/ios_smoketest.app with an arm64 iphoneos binary (from the Inspect log).
3. Confirm the same push produced NO cibuild/stylecheck runs (Actions tab shows only 'iOS').
4. Capture the run URL and note it in doc/iOS-Port.md as the Phase 1 completion marker; flip the phase table row in the doc from 'in progress' to 'done'.
5. (Optional) Add the arch/platform assertion to the Inspect step (from 1.5) so future regressions auto-fail rather than needing a human to read logs.

**Files**

- `.github/workflows/ios.yml (whole file — the artifact of record)`
- `doc/iOS-Port.md (record the passing run URL / milestone)`

**Acceptance**

- [ ] One green 'iOS' run with both legs passing and the .ipa artifact present.
- [ ] No cibuild/stylecheck run on the same push.
- [ ] doc/iOS-Port.md links the passing run and marks Phase 1 done.
- [ ] The .ipa's internal layout is verified (Payload/<App>.app, arm64, iphoneos).

**Risks / easy to miss**

- This is the only task that genuinely cannot be done from Windows — it requires observing a real GitHub Actions macOS run; budget for CI turnaround.
- Green build != installable app; installability is bounded by the Info.plist review (1.5/1.6). Keep the DoD honest: 'pipeline proven + unsigned .ipa produced', not 'runs on device'.
- Runner-image drift can turn a previously-green run red later; the version-echo (1.4) is the first thing to check.

### Phase 1 cross-cutting notes

- Build locality is absolute: iOS binaries build ONLY on macOS CI (GitHub Actions macos-latest). The dev machine is Windows; there is NO local iOS build. Every change is validated only after push — design steps so failures surface in CI logs (version echoes, verbose configure, explicit Inspect assertions) rather than needing an interactive Mac.
- Ground-truth volatility during authoring: HEAD moved from e81bedbb8 to 1feccc641 mid-analysis (a second iOS commit landed), which made the editor's file Reads briefly stale. Always re-verify workflow/file state with `git show HEAD:<path>` before trusting a cached Read on this actively-committed branch.
- Isolation invariant: misc/ios/smoketest is a standalone CMake project and MUST NOT be pulled into the engine build — misc/CMakeLists.txt does not (and must not) add_subdirectory(ios). Keep it engine-free so a red iOS build unambiguously means toolchain/SDK/CI, never the port.
- Signing is intentionally disabled twice: globally in the toolchain (ios.toolchain.cmake:825-826) and on the target (smoketest CMakeLists:28-29). Device builds therefore link unsigned with no provisioning profile / DEVELOPMENT_TEAM. This is correct for SideStore, which re-signs on-device and grants get-task-allow — the same entitlement that later unlocks optional LuaJIT JIT. Do not add signing in Phase 1.
- Only the device leg (OS64) yields an installable .ipa. The simulator leg (SIMULATORARM64) is a fast compile sanity check; its .app cannot be installed on a device and must never be packaged as an .ipa (guarded by the package_ipa matrix flag).
- Platform pins to keep consistent everywhere: min iOS 15.0, arm64-only, ENABLE_BITCODE=OFF (bitcode is deprecated/removed by Apple). Always pass -DDEPLOYMENT_TARGET=15.0 explicitly because the leetal toolchain's default is 13.0.
- -G Xcode is mandatory for iOS: it drives proper .app bundle + iOS Info.plist generation and code-signing attribute handling. Do not switch the iOS legs to Ninja/Makefile generators.
- 'SideStore-ready' = valid unsigned .ipa AND a bundle Info.plist carrying the install-required keys (CFBundleExecutable, CFBundleIdentifier, MinimumOSVersion, CFBundleSupportedPlatforms=[iPhoneOS], UIDeviceFamily). CMake with CMAKE_SYSTEM_NAME=iOS (toolchain:769) emits an iOS-flavoured plist, but verify with `plutil -p` and add a custom Info.plist if keys are missing before claiming true installability (becomes load-bearing once a launchable app exists in Phase 3).
- StyleCheck is disabled per-push on ios-port but still gates PRs to dev, and .editorconfig enforces UTF-8 + final newline (and 4-space indent, 120-col for C/C++). Keep every new iOS file (workflow YAML, smoketest sources, docs) LF / UTF-8 / no trailing whitespace / final newline so a future merge to dev passes StyleCheck.
- Action pinning policy applies going forward: ios.yml is pinned to @v4; when Phase 2 adds submodule checkout (actions/checkout with submodules: recursive) and caching, pin those the same way. Leave cibuild.yml/stylecheck.yml @main refs alone on this branch (they don't run here) but pin them before the port merges to dev.
- On an ios-port push, only the 'iOS' workflow should run: cibuild/stylecheck exclude the branch via push.branches-ignore, mirror.yml is guarded by `if: github.repository == 'OpenXRay/xray-16'` (skips on the personal fork), and labeler.yml is PR-event-only. Opening a PR from ios-port, however, WILL fan out the full desktop matrix + StyleCheck (pull_request triggers are not branch-filtered) — that is intended as the eventual merge gate, so don't open one prematurely.
- Artifact retention defaults to 90 days; the unsigned .ipa (artifact name OpenXRay-ios-smoketest-ipa) is not permanent. Document re-triggering (push or workflow_dispatch) so operators can regenerate it, and keep the artifact name stable so download instructions don't rot.
- Definition of done for Phase 1 is 'the Windows->macOS-CI->iOS pipeline is proven and produces an unsigned .ipa', NOT 'the app runs on a device'. The smoke test is headless (a printf + compile-time #error guards); booting to a window is Phase 3.

---

<a id="phase-2"></a>

## Phase 2 — Cross-build dependencies + link full engine (static)

> **Phase goal.** Make every third-party C dependency cross-compile for both iphoneos (OS64) and iphonesimulator (SIMULATORARM64) arm64, and make the entire OpenXRay engine + game link into ONE static Mach-O inside an app bundle on the macOS GitHub Actions runners. The binary need not boot or render (rendering is Phase 4); success = a clean configure + build + link with all engine symbols present in a single arm64 iOS Mach-O for both SDKs. This requires forcing BUILD_SHARED_LIBS=OFF (activating XRAY_STATIC_BUILD, removing the dlopen concern), skipping mimalloc, fixing the LuaJIT cross-build (sysroot + host-tool decoupling), Apple/iOS-guarding the bare pthread/dl link entries, confirming the Windows/desktop-only externals (AGS_SDK, GameSpy, DiscordGameSDK, RenderDoc, DX11 R4) stay out of the iOS link, and pointing find_package at the cross-built deps under CMAKE_FIND_ROOT_PATH instead of Homebrew.

### ⬜ 2.1 Choose and implement the third-party deps acquisition/superbuild strategy for iOS

`status: todo` · `effort: high`

**Goal.** Provide a single, CI-reproducible mechanism that produces static libogg/libvorbis/libtheora/lzo2/SDL2/OpenAL (and optional jpeg-turbo) for iphoneos-arm64 AND iphonesimulator-arm64, installed into per-SDK prefixes that the engine's find_package can locate. Everything downstream (2.2-2.6) plugs into this. Without a deterministic dep story the engine link is unreproducible.

**Steps**

1. Decide the mechanism. Recommended: a standalone shell superbuild (misc/ios/deps/build-deps.sh) that loops over the two SDKs and invokes each dep's own CMake with -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/ios.toolchain.cmake -DPLATFORM=OS64|SIMULATORARM64 -DDEPLOYMENT_TARGET=15.0 -DENABLE_BITCODE=OFF -DBUILD_SHARED_LIBS=OFF -DCMAKE_INSTALL_PREFIX=<prefix>/<sdk>, and 'cmake --install'. Rationale: identical toolchain to the engine, no autotools/host-triplet friction, cacheable in CI. Alternatives to note in the decision comment: (a) ExternalProject_Add inside a top-level superbuild (couples dep builds to engine configure, slower iteration, harder to cache); (b) vcpkg with the arm64-ios triplet + a custom device/simulator triplet (brings sdl2, libogg, libvorbis, libtheora, openal-soft, liblzo, libjpeg-turbo, but vorbis/theora ports and the simulator-arm64 triplet need validation).
2. Define two install prefixes, e.g. $PWD/build-ios/deps/iphoneos and .../iphonesimulator. Each contains include/ + lib/ with the static .a files and the CMake package configs / pkgconfig the engine will consume.
3. Pin exact upstream versions/tags per dep (record in the script header) so CI is reproducible: libogg, libvorbis, libtheora, lzo-2.10, SDL release-2.30.x (>=2.0.18 required by GNULike.cmake:144), openal-soft 1.23.x, libjpeg-turbo 3.x (optional).
4. Ensure ENABLE_BITCODE=OFF everywhere (App Store bitcode is dead; SideStore does not want it) and CMAKE_OSX_DEPLOYMENT_TARGET=15.0 to match the engine (doc/iOS-Port.md open-decision 3).
5. Add a CI cache keyed on (sdk, dep versions, toolchain sha) around the deps prefix so only the ~20-min engine build reruns on most pushes.
6. Emit a single CMake cache-init file (e.g. misc/ios/deps/<sdk>-deps.cmake) that sets CMAKE_PREFIX_PATH / *_ROOT / OGGDIR / VORBISDIR / THEORADIR / LZO_ROOT_DIR / SDL2_DIR / OPENALDIR so the engine configure is one -C flag.

**Files**

- `misc/ios/deps/ (new: superbuild script or ExternalProject CMake)`
- `misc/ios/deps/build-deps.sh (new)`
- `.github/workflows/ios.yml:50-62 (configure/build steps that must gain a deps stage)`
- `cmake/toolchains/ios.toolchain.cmake:1110-1176 (CMAKE_FIND_ROOT_PATH / IGNORE_PATH behavior deps must respect)`

**Acceptance**

- [ ] Running build-deps.sh on a macOS runner produces, for BOTH sdks, static libogg.a/libvorbis*.a/libtheora*.a/liblzo2.a/libSDL2.a/libopenal.a (+ headers + package configs) under the per-sdk prefix.
- [ ] The prefixes sit on paths that survive the toolchain's CMAKE_IGNORE_PATH (i.e. NOT under /usr/local or /opt/homebrew) and are discoverable via CMAKE_FIND_ROOT_PATH/CMAKE_PREFIX_PATH.
- [ ] A trivial find_package(Ogg REQUIRED) etc. test configure against the prefix succeeds under the iOS toolchain.

**Risks / easy to miss**

- Autotools-based deps (openal-soft is CMake; libogg/vorbis/theora ship both autotools and CMake — use their CMake) can mis-detect host vs target if not driven through the leetal toolchain; always pass the toolchain file, never rely on ./configure host triples.
- Simulator-arm64 and device-arm64 both report arm64 to lipo; mixing their prefixes yields link-time 'building for iOS-simulator but linking against iOS' errors. Keep prefixes strictly per-SDK.
- vcpkg's stock arm64-ios triplet targets device; a simulator triplet must be authored — easy to forget and silently get a device slice in the simulator build.
- CMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH (toolchain line ~1146) means find_package can still see host CMake package registries; pin with explicit *_DIR to avoid pulling a macOS/Homebrew config.

### ⬜ 2.2 Cross-build SDL2 for iphoneos + simulator and expose SDL2::SDL2

`status: todo` · `effort: medium`

**Goal.** SDL2 is the window/input/GL abstraction the engine hard-depends on (find_package(SDL2 2.0.18 REQUIRED) at GNULike.cmake:144; linked by xrCore:459 and xrEngine:434). It must exist as a static arm64 iOS lib exporting the SDL2::SDL2 target so xrCore/xrEngine link.

**Steps**

1. Build SDL2 from its CMake with -DSDL_STATIC=ON -DSDL_SHARED=OFF -DSDL2_DISABLE_INSTALL=OFF through the iOS toolchain for each SDK; SDL2 has first-class iOS support (UIKit video driver, CoreAudio, CoreMotion, MFi/GameController).
2. Install so that SDL2Config.cmake / sdl2-config lands in the prefix and the SDL2::SDL2 imported target (and SDL2::SDL2-static) is produced. Confirm the engine references SDL2::SDL2 — verify whether SDL2 static exposes SDL2::SDL2 or only SDL2::SDL2-static and add an ALIAS if needed so GNULike.cmake:144's expectation holds.
3. Ensure the iOS SDL2 links its required system frameworks (UIKit, Foundation, CoreGraphics, QuartzCore, CoreAudio, AudioToolbox, AVFoundation, GameController, CoreMotion, Metal, OpenGLES) via its interface usage requirements, so the engine final link inherits them.
4. Do NOT build SDL2main here as a separate concern yet — but note SDL2 on iOS supplies SDL_UIKitAppDelegate + an SDL_main shim; the app target (2.13) will need SDL2::SDL2main or the SDL_main entry. Record which library carries main().
5. Keep SDL headers include dir on the interface so #include <SDL.h>/<SDL_loadso.h> (used in ModuleLookup.cpp) resolve.

**Files**

- `misc/ios/deps/build-deps.sh (SDL2 stanza)`
- `cmake/XRay.Compiler.GNULike.cmake:144 (find_package(SDL2 2.0.18 REQUIRED))`
- `src/xrCore/CMakeLists.txt:459`
- `src/xrEngine/CMakeLists.txt:434`

**Acceptance**

- [ ] find_package(SDL2 2.0.18 REQUIRED) succeeds under both iOS SDKs and yields a usable SDL2::SDL2 (or aliased) target.
- [ ] libSDL2.a is a static arm64 slice for the correct SDK (verified with lipo -info / vtool -show-build).
- [ ] A minimal TU that #include <SDL.h> and calls SDL_Init compiles and links against the prefix.

**Risks / easy to miss**

- SDL2 static target name differs between versions (SDL2::SDL2 vs SDL2::SDL2-static); the engine expects SDL2::SDL2 — a missing alias breaks the link at both xrCore and xrEngine.
- SDL_loadso on iOS: SDL_LoadObject exists but iOS forbids loading external dylibs; ModuleLookup uses it but XRAY_STATIC_BUILD means it should never be called at runtime — ensure static build path doesn't require it.
- SDL2main pulls a UIKit app delegate and its own main(); colliding with the engine's own main() in entry_point.cpp will cause duplicate-symbol or wrong entry — resolved in 2.13, but flag it now.
- The system frameworks SDL needs must propagate transitively; if SDL's static config omits them, the engine link fails with hundreds of undefined ObjC/framework symbols.

### ⬜ 2.3 Cross-build OpenAL for iOS and expose OpenAL::OpenAL

`status: todo` · `effort: medium`

**Goal.** The sound stack (xrSound, xrEngine) links OpenAL::OpenAL (xrSound/CMakeLists.txt:134, xrEngine/CMakeLists.txt:431) and find_package(OpenAL REQUIRED) at GNULike.cmake:145. iOS has no system OpenAL framework (Apple deprecated/removed it), so openal-soft must be cross-built as a static lib and mapped to the OpenAL::OpenAL target CMake's FindOpenAL expects.

**Steps**

1. Build openal-soft with -DLIBTYPE=STATIC -DALSOFT_UTILS=OFF -DALSOFT_EXAMPLES=OFF -DALSOFT_TESTS=OFF through the iOS toolchain per SDK; it supports the CoreAudio backend on iOS.
2. Decide the target-name bridge: CMake's stock FindOpenAL provides OpenAL::OpenAL from OPENAL_INCLUDE_DIR/OPENAL_LIBRARY. Either (a) install openal-soft so FindOpenAL finds libopenal.a + AL/ headers (set OPENALDIR to the prefix), or (b) rely on openal-soft's own OpenALConfig.cmake and add an ALIAS OpenAL::OpenAL if the exported name differs.
3. Ensure the openal-soft interface carries its iOS system framework deps (CoreAudio, AudioToolbox, AudioUnit, AVFAudio) so the final engine link resolves them.
4. Confirm the EAX/effects code in xrSound (SoundRender_EffectsA_EAX.cpp) compiles against openal-soft's AL/efx.h (efx is provided by openal-soft, unlike Apple's old framework).

**Files**

- `misc/ios/deps/build-deps.sh (openal-soft stanza)`
- `cmake/XRay.Compiler.GNULike.cmake:145 (find_package(OpenAL REQUIRED))`
- `src/xrSound/CMakeLists.txt:134`
- `src/xrEngine/CMakeLists.txt:431`

**Acceptance**

- [ ] find_package(OpenAL REQUIRED) succeeds under both iOS SDKs and OpenAL::OpenAL resolves to the static openal-soft lib.
- [ ] xrSound and xrEngine compile+link against it (efx.h symbols present).
- [ ] libopenal.a is a static arm64 iOS slice for the correct SDK.

**Risks / easy to miss**

- A left-over reference to Apple's system OpenAL.framework would link on macOS but fail on iOS (framework absent) — make sure the iOS branch never does find_library(OpenAL) against system frameworks.
- openal-soft's CoreAudio backend needs AVFAudio/AudioSession setup at runtime (Phase 3); irrelevant to linking but note it.
- Target name mismatch (OpenAL::OpenAL vs OpenAL::al) breaks two link sites; add alias.

### ⬜ 2.4 Cross-build libogg / libvorbis / libtheora and satisfy the repo's Find modules

`status: todo` · `effort: medium`

**Goal.** Vorbis (xrSound: Ogg::Ogg, Vorbis::Vorbis, Vorbis::VorbisFile at xrSound/CMakeLists.txt:135-137) decodes game audio; Theora (xrEngine: Ogg::Ogg, Theora::Theora at xrEngine/CMakeLists.txt:432-433) plays intro/cutscene videos. The repo's own FindOgg/FindVorbis/FindTheora modules must locate the cross-built static libs and construct the imported targets.

**Steps**

1. Build libogg first (dependency of both), then libvorbis (needs ogg), then libtheora (needs ogg + vorbis), all static via CMake through the iOS toolchain per SDK. libtheora upstream is autotools-heavy — prefer a maintained CMake port or the xiph theora CMakeLists; if using autotools, drive with the toolchain's compiler/sysroot exported and --host=arm-apple-darwin.
2. Install headers as ogg/ogg.h, vorbis/codec.h + vorbis/vorbisfile.h, theora/theora.h so the Find modules' find_path NAMES match (FindOgg looks for ogg/ogg.h; FindVorbis vorbis/codec.h; FindTheora theora/theora.h).
3. Provide component libs the Find modules expect: vorbis, vorbisenc, vorbisfile (FindVorbis.cmake:60-65) and theora/theoradec/theoraenc (FindTheora.cmake:60-65). If the upstream ships a combined libtheora only, ensure THEORADEC/THEORAENC resolve — the .framework special-case (FindTheora.cmake:72-76,93-98) will NOT trigger for a plain prefix, so the module will look for separate theoradec/theoraenc; make sure those exist or patch the module.
4. Set env/cache OGGDIR, VORBISDIR, THEORADIR (the Find modules honor $ENV{OGGDIR} etc., FindOgg.cmake:34, FindVorbis.cmake:49, FindTheora.cmake:49) OR rely on PATH_SUFFIXES include/lib under CMAKE_FIND_ROOT_PATH.
5. Verify select_library_configurations picks the release .a (no debug suffix) so OGG/VORBIS/THEORA_LIBRARY are set and *_FOUND becomes TRUE.

**Files**

- `misc/ios/deps/build-deps.sh (ogg/vorbis/theora stanzas)`
- `cmake/FindOgg.cmake`
- `cmake/FindVorbis.cmake:67-96 (framework special-case + component libs)`
- `cmake/FindTheora.cmake:67-98`
- `cmake/XRay.Compiler.GNULike.cmake:147-149 (find_package Ogg/Vorbis/Theora REQUIRED)`
- `src/xrSound/CMakeLists.txt:135-137`
- `src/xrEngine/CMakeLists.txt:432-433`

**Acceptance**

- [ ] find_package(Ogg/Vorbis/Theora REQUIRED) all succeed under both iOS SDKs; Ogg::Ogg, Vorbis::Vorbis, Vorbis::VorbisFile, Theora::Theora imported targets exist.
- [ ] xrSound links vorbis+vorbisfile+ogg; xrEngine links theora+ogg; both compile against the installed headers.
- [ ] Static arm64 iOS slices for all three libs for the correct SDK.

**Risks / easy to miss**

- FindTheora/FindVorbis .framework detection (a macOS convenience) is dead weight on iOS and can misfire if THEORA_INCLUDE_DIR resolves oddly; a plain include prefix takes the 3-component path expecting theoradec/theoraenc as separate libs — libtheora often only builds libtheora + libtheoraenc + libtheoradec, verify all three install.
- libtheora is the most fragile to cross-build (old build system, x86 asm guarded by config); ensure the arm build disables x86 asm and encoder asm.
- Header layout mismatch (e.g. vorbis/vorbisfile.h missing) makes VORBISFILE_FOUND false and drops the target silently, surfacing only as an undefined ov_* symbol at engine link.

### ⬜ 2.5 Cross-build lzo2 and satisfy FindLZO (LZO::LZO)

`status: todo` · `effort: low`

**Goal.** xrCore's compression path (Compression/lzo_compressor.cpp, rt_compressor*.cpp) links LZO::LZO (xrCore/CMakeLists.txt:465) via find_package(LZO REQUIRED) (GNULike.cmake:150). lzo2 must be a static arm64 iOS lib discoverable by cmake/FindLZO.cmake.

**Steps**

1. Build lzo-2.10 static through the iOS toolchain per SDK (lzo has a plain, portable C build; no asm on arm64). If lzo ships only autotools, a tiny hand-written CMakeLists compiling src/*.c into liblzo2.a is acceptable and simplest to cross-compile.
2. Install headers under include/lzo/ (lzo/lzo1x.h) and liblzo2.a under lib/ so FindLZO's find_path(lzo/lzo1x.h) and find_library(NAMES lzo2) hit; set LZO_ROOT_DIR to the prefix (FindLZO honors it, FindLZO.cmake:27-34).
3. Confirm LZO::LZO imported target is created (FindLZO.cmake:62-66).

**Files**

- `misc/ios/deps/build-deps.sh (lzo stanza)`
- `cmake/FindLZO.cmake:36-50 (find_path lzo/lzo1x.h, find_library lzo2)`
- `cmake/XRay.Compiler.GNULike.cmake:150`
- `src/xrCore/CMakeLists.txt:465`

**Acceptance**

- [ ] find_package(LZO REQUIRED) succeeds; LZO::LZO points at the static liblzo2.a.
- [ ] xrCore compiles+links the lzo compressor TUs.
- [ ] liblzo2.a is arm64 iOS for the correct SDK.

**Risks / easy to miss**

- FindLZO hardcodes /usr/local into its search (_lzo_SEARCH_DIRS, FindLZO.cmake:31-34) — under the iOS toolchain /usr/local is in CMAKE_IGNORE_PATH, so rely on LZO_ROOT_DIR, not the default path.
- Library name must be exactly lzo2 (not lzo) to match NAMES lzo2.

### ⬜ 2.6 Optionally cross-build jpeg-turbo (JPEG::JPEG) or cleanly disable JPEG on iOS

`status: todo` · `effort: low`

**Goal.** JPEG is optional (find_package(JPEG) with no REQUIRED at GNULike.cmake:146; linked conditionally at xrCore/CMakeLists.txt:464 via $<$<BOOL:${JPEG_FOUND}>:JPEG::JPEG>; used by Media/ImageJPEG.cpp). Either provide libjpeg-turbo so screenshots/JPEG image loading work, or confirm the JPEG_FOUND=FALSE path compiles so the link is not blocked.

**Steps**

1. Preferred: build libjpeg-turbo 3.x static via its CMake with -DENABLE_SHARED=OFF -DENABLE_STATIC=ON through the iOS toolchain (NEON SIMD is supported on arm64, keep it on). Install so CMake's stock FindJPEG produces JPEG::JPEG.
2. If skipping: verify ImageJPEG.cpp and any include of jpeglib.h are guarded so a JPEG_FOUND=FALSE build compiles (check whether xrCore always compiles ImageJPEG.cpp regardless of JPEG_FOUND — it is listed unconditionally in xrCore sources at CMakeLists.txt:334, so a missing jpeglib.h header would break the compile even though the LINK is conditional). If unguarded, either always ship jpeg-turbo (simplest) or add a compile guard.
3. Decide and record: recommend shipping jpeg-turbo to avoid touching source guards.

**Files**

- `misc/ios/deps/build-deps.sh (optional jpeg-turbo stanza)`
- `cmake/XRay.Compiler.GNULike.cmake:146 (find_package(JPEG))`
- `src/xrCore/CMakeLists.txt:464`
- `src/xrCore/Media/ImageJPEG.cpp`

**Acceptance**

- [ ] Either find_package(JPEG) succeeds and JPEG::JPEG links, OR the build compiles cleanly with JPEG absent (ImageJPEG.cpp guarded).
- [ ] No unresolved jpeg_* symbols at engine link.

**Risks / easy to miss**

- Media/ImageJPEG.cpp is compiled unconditionally (xrCore/CMakeLists.txt:334) but only LINKED against JPEG conditionally — a header-present/lib-absent or header-absent situation is the classic trap. Verify the include is inside a JPEG_FOUND guard or just always provide the lib.
- libjpeg-turbo needs NASM only for x86; arm64 uses its own SIMD — do not disable SIMD unnecessarily.

### ⬜ 2.7 Force static build + standard allocator on iOS (BUILD_SHARED_LIBS=OFF, MEMORY_ALLOCATOR=standard, skip mimalloc)

`status: todo` · `effort: low`

**Goal.** iOS forbids loading external dylibs, so the whole engine must be static, which activates XRAY_STATIC_BUILD (src/CMakeLists.txt:3) and the direct-symbol module model. mimalloc must be skipped (not available / not needed) and the allocator forced to standard (USE_PURE_ALLOC) so xrCore compiles without USE_MIMALLOC.

**Steps**

1. In the iOS configuration path (either the toolchain-detected APPLE_IOS branch you add in GNULike.cmake, or the deps cache-init file), force set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE) for iOS. Note PreProjectInit.cmake only sets BUILD_SHARED_LIBS_DEFAULT_VALUE OFF for the ReleaseMasterGold config; for iOS make it unconditional regardless of CMAKE_BUILD_TYPE.
2. Guard the mimalloc find_package (GNULike.cmake:151) so it is not attempted on iOS (or simply never provide mimalloc in the deps prefix and ensure MEMORY_ALLOCATOR defaults to 'standard' at GNULike.cmake:157-158 rather than hitting the FATAL_ERROR at :162-163).
3. Confirm the resulting XRAY_STATIC_BUILD define flows (src/CMakeLists.txt:3 keys on NOT BUILD_SHARED_LIBS) and that XRAPI_API etc. become empty (Include/xrAPI/xrAPI.h:3-11) so no dllimport/visibility attributes remain.
4. The Externals/mimalloc/include path on xrCore's include dirs (xrCore/CMakeLists.txt:452) is harmless if the submodule is absent as long as USE_MIMALLOC is off and no mimalloc header is included; verify xrMemory.cpp only includes mimalloc under USE_MIMALLOC.
5. Ensure every engine add_library() without explicit STATIC/SHARED honors BUILD_SHARED_LIBS=OFF → static (they do: xrCore etc. use bare add_library); xrMiscMath/xrImGui are already explicit STATIC.

**Files**

- `CMakeLists.txt:15,28-31 (BUILD_SHARED_LIBS option / default)`
- `cmake/PreProjectInit.cmake:27-31 (BUILD_SHARED_LIBS_DEFAULT_VALUE)`
- `cmake/XRay.Compiler.GNULike.cmake:151,155-166 (mimalloc find + MEMORY_ALLOCATOR)`
- `src/CMakeLists.txt:3 (XRAY_STATIC_BUILD define)`
- `src/xrCore/CMakeLists.txt:463,490-494 (mimalloc link + USE_MIMALLOC/USE_PURE_ALLOC)`
- `src/xrCore/CMakeLists.txt:452 (Externals/mimalloc/include on include path)`

**Acceptance**

- [ ] Configure under the iOS toolchain reports BUILD_SHARED_LIBS: OFF and 'Using standard memory allocator' (GNULike.cmake:166), never the mimalloc FATAL_ERROR.
- [ ] XRAY_STATIC_BUILD is defined for all engine targets; XRAPI_API/ENGINE_API expand to nothing.
- [ ] No install(TARGETS ... LIBRARY) rules fire (all guarded by BUILD_SHARED_LIBS) and every xr* lib is a static .a.

**Risks / easy to miss**

- If BUILD_SHARED_LIBS is only defaulted (option) not forced, a stray -DBUILD_SHARED_LIBS=ON or the multi-config default could silently produce dylibs that iOS rejects.
- USE_PURE_ALLOC vs USE_MIMALLOC: xrMemory must have a working standard path; verify no code assumes mimalloc is present (e.g. mi_malloc calls outside USE_MIMALLOC).
- IPO/LTO is auto-enabled for Release/ReleaseMasterGold (XRay.Build.cmake:30-33); with static libs + Xcode generator, LTO across many static libs can blow up link time or memory on CI — consider disabling LTO for the iOS bring-up to keep the 20-min CI budget.

### ⬜ 2.8 Add an iOS branch to XRay.Compiler.GNULike.cmake that finds deps from CMAKE_FIND_ROOT_PATH and applies iOS-correct flags

`status: todo` · `effort: medium`

**Goal.** GNULike.cmake is the shared Clang/GCC config; today its 'NOT WIN32' block (lines 143-152) find_packages SDL2/OpenAL/JPEG/Ogg/Vorbis/Theora/LZO/mimalloc assuming a Linux/Homebrew layout, and its link/flag logic (-Wl,-undefined,error at :82, -msse3 fallback at :129-132) needs iOS-awareness. Add an explicit iOS branch so deps resolve from the cross-built prefixes and flags are arm64/iOS-correct.

**Steps**

1. Add a detection for iOS. The leetal toolchain does not define CMAKE_SYSTEM_NAME as generic 'iOS' consistently across versions, but it sets PLATFORM/APPLE and CMAKE_OSX_SYSROOT to an iphoneos* SDK; simplest robust test: if (APPLE AND (PLATFORM MATCHES "OS64|SIMULATOR" OR CMAKE_OSX_SYSROOT MATCHES "iPhone")). Prefer keying off a variable the toolchain exports (leetal sets PLATFORM). Set an internal XRAY_IOS flag.
2. For iOS, keep find_package(SDL2/OpenAL/Ogg/Vorbis/Theora/LZO) REQUIRED but ensure they resolve through CMAKE_FIND_ROOT_PATH/CMAKE_PREFIX_PATH set by the deps prefix (task 2.1), and skip find_package(mimalloc) entirely so mimalloc_FOUND stays false → MEMORY_ALLOCATOR=standard.
3. Guard the SIMD else-branch: currently non-ARM/PPC/E2K falls to -mfpmath=sse -msse3 (:129-132). arm64 sets PROJECT_PLATFORM_ARM64 (:106-107) and takes the empty ARM64 branch (:118-119) — confirm iOS arm64 lands there and NOT in the sse branch. (It should, since CMAKE_SYSTEM_PROCESSOR is arm64/aarch64 under the toolchain — verify the toolchain sets it; if it sets 'arm64' the STREQUAL at :106 matches.)
4. Review the undefined-symbol policy: APPLE already uses -Wl,-undefined,error (:82) which is correct and desirable for catching missing symbols in the static link; keep it, but be ready to see it fire loudly during bring-up (that is the point).
5. Do NOT apply -mfpu=neon (that is 32-bit ARM only, :116-117); arm64 has NEON unconditionally.
6. Ensure CCACHE logic (:29-34) and ld selection (XRAY_LINKER, :135-137) don't force a linker unavailable on the Xcode toolchain; leave default ld64.

**Files**

- `cmake/XRay.Compiler.GNULike.cmake:81-85 (undefined-symbol link opt)`
- `cmake/XRay.Compiler.GNULike.cmake:106-133 (arch → SIMD flags)`
- `cmake/XRay.Compiler.GNULike.cmake:143-166 (find_package block + allocator)`
- `cmake/toolchains/ios.toolchain.cmake:1110-1176 (find-root behavior to honor)`

**Acceptance**

- [ ] Configuring the engine under the iOS toolchain runs the find_package block, resolves all six deps from the cross-built prefixes, and does not attempt Homebrew or /usr/local.
- [ ] arm64 iOS build applies no -msse3/-mfpmath=sse and no -mfpu=neon; NEON is implicit.
- [ ] MEMORY_ALLOCATOR resolves to standard without FATAL_ERROR.

**Risks / easy to miss**

- Relying on CMAKE_SYSTEM_NAME STREQUAL 'iOS' is brittle across leetal versions; some set it to 'Darwin'. Key on PLATFORM or CMAKE_OSX_SYSROOT instead.
- The 'NOT WIN32' find block currently runs for macOS too; adding an iOS branch must not regress the (now-disabled but still-present) macOS desktop path.
- If CMAKE_SYSTEM_PROCESSOR is 'aarch64' vs 'arm64' the STREQUAL checks at :106 must cover both (they do), but the bin output folder path (XRay.Build.cmake:7 uses CMAKE_SYSTEM_PROCESSOR) will differ — cosmetic only.
- find_package(JPEG) unguarded REQUIRED-ness: it is not REQUIRED (:146) so absence is fine, but see task 2.6 compile trap.

### ⬜ 2.9 Fix Externals/LuaJIT-proj for iOS cross-compile: sysroot, host-tool decoupling, arch detection, JIT-optional variant

`status: todo` · `effort: very-high`

**Goal.** LuaJIT is the scripting VM (xrScriptEngine → xrLuaJIT). Cross-compiling it is the single hardest dep because it builds HOST tools (minilua, buildvm) that must RUN on the macOS runner to emit the TARGET arm64 lj_vm.S + generated headers. The current CMakeLists hardcodes the macOS SDK (line 18), leaks SDKROOT into the host tool sub-builds (lines 66-67), and detects target arch by invoking the compiler without iOS flags (line 141) — all of which break under the iOS toolchain.

**Steps**

1. Remove/guard the hardcoded CMAKE_OSX_SYSROOT at line 18. It force-overrides the iOS toolchain's iphoneos sysroot for the TARGET xrLuaJIT lib, which would compile LuaJIT against the macOS SDK. On iOS, the TARGET must use the toolchain-provided CMAKE_OSX_SYSROOT (iphoneos/simulator).
2. Decouple the HOST tools from the iOS target. The nested add_custom_command cmake invocations (lines 332-346 for minilua, 365-378 for buildvm) do NOT pass -DCMAKE_TOOLCHAIN_FILE, so absent env pollution they build for the host — GOOD. But lines 66-67 set ENV{SDKROOT}=${CMAKE_OSX_SYSROOT} (now iphoneos) and ENV{MACOSX_DEPLOYMENT_TARGET}; that env leaks into the nested builds and forces the host tools to target iOS, producing binaries that cannot execute on the runner. Fix: on iOS, do NOT export the iOS SDKROOT globally; instead pass an explicit host SDKROOT (macosx) to the minilua/buildvm sub-builds (e.g. add -DCMAKE_OSX_SYSROOT=macosx and -DCMAKE_OSX_ARCHITECTURES=<host arch> to their cmake command lines, and clear SDKROOT in their environment).
3. Fix arch detection (line 141): execute_process(${CMAKE_C_COMPILER} ${CCOPTIONS} -E lj_arch.h -dM). Under the iOS toolchain, -isysroot/-arch/-mios-version-min are NOT in CCOPTIONS (CMake injects them separately), so this preprocess runs against the HOST and may fail to define LJ_TARGET_ARM64/TARGET_OS_IPHONE. Append the iOS target flags explicitly for the detection: -isysroot ${CMAKE_OSX_SYSROOT} -arch arm64 -m(ios|ios-simulator)-version-min=${DEPLOYMENT_TARGET}. This ensures TARGET_LJARCH=arm64 and the iOS-specific dynasm defines are chosen for the TARGET.
4. Verify the machasm path: APPLE branch sets LJVM_MODE=machasm (line 216) which is correct for Mach-O arm64. The '-D IOS' dynasm flag is only added for 32-bit arm on APPLE (lines 286-289); arm64 does not need it, so no change there, but confirm arm64+machasm emits a valid iOS lj_vm.S.
5. Keep interpreter-mode baseline: do NOT set LUAJIT_DISABLE_JIT for the default iOS build — LuaJIT's interpreter is hand-written arm64 assembly and is what we ship; the JIT compiler is compiled-in but simply won't successfully allocate RWX at runtime on iOS (runtime fallback is Phase 3). The buildvm-generated lj_vm.S contains the interpreter regardless.
6. Add the JIT-optional build flavor as a CMake cache option (e.g. XRAY_LUAJIT_ENABLE_JIT) that, when a user builds for SideStore+StikDebug, keeps JIT paths hot; when off it can define LUAJIT_DISABLE_JIT to shrink the VM. Default: JIT compiled-in (so the same binary can opt into JIT at runtime). Wire it to XCFLAGS (lines 100-135).
7. Set BUILD_SHARED_LIBS OFF for LuaJIT on iOS (the option at line 21 defaults ON) so xrLuaJIT is a static lib folded into the engine; the WIN32 -shared/out-implib path (lines 190-193) is already skipped on non-Windows but confirm the else/APPLE path produces a static archive under BUILD_SHARED_LIBS=OFF.
8. Guard the LUA_USE_DLOPEN option (line 45) for iOS: dlopen of Lua C modules is meaningless/forbidden on iOS. Set LUA_USE_DLOPEN OFF for iOS so lib_package uses the static path and 'dl' is not linked (line 574).
9. Confirm the 'make clean' execute_process (lines 71-74) is harmless on the runner (make exists) or replace with a CMake-native clean to avoid a host-tool assumption.

**Files**

- `Externals/LuaJIT-proj/CMakeLists.txt:18 (hardcoded CMAKE_OSX_SYSROOT=MacOSX.sdk)`
- `Externals/LuaJIT-proj/CMakeLists.txt:65-68 (ENV{SDKROOT}/ENV{MACOSX_DEPLOYMENT_TARGET})`
- `Externals/LuaJIT-proj/CMakeLists.txt:140-145 (lj_arch.h -E arch detection)`
- `Externals/LuaJIT-proj/CMakeLists.txt:200-219 (APPLE LJVM_MODE=machasm branch)`
- `Externals/LuaJIT-proj/CMakeLists.txt:282-292 (DASM_FLAGS incl. IOS flag for arm)`
- `Externals/LuaJIT-proj/CMakeLists.txt:329-388 (nested cmake sub-builds for minilua/buildvm)`
- `Externals/LuaJIT-proj/CMakeLists.txt:108-110 (LUAJIT_DISABLE_JIT option)`
- `Externals/LuaJIT-proj/HostBuildTools/minilua/CMakeLists.txt`
- `Externals/LuaJIT-proj/HostBuildTools/buildvm/CMakeLists.txt`

**Acceptance**

- [ ] Under both iOS SDKs, minilua and buildvm build as HOST-native executables (macOS arm64/x86_64) that RUN during the build (verify by successful generation of buildvm_arch.h, lj_vm.S, lj_bcdef.h, lj_ffdef.h, lj_libdef.h, lj_recdef.h, lj_folddef.h, jit/vmdef.lua).
- [ ] The TARGET xrLuaJIT static lib is arm64 iOS (lipo/vtool confirm iOS slice) and links into the engine.
- [ ] lj_arch.h detection selects TARGET_LJARCH=arm64 and the iOS-appropriate dynasm/machasm output.
- [ ] A default iOS build keeps JIT compiled-in (interpreter guaranteed to run); the optional LUAJIT_DISABLE_JIT/XRAY_LUAJIT_ENABLE_JIT switch is honored.
- [ ] No 'dl' is linked into xrLuaJIT on iOS.

**Risks / easy to miss**

- The nested cmake host-tool builds are the crux: if the iOS SDKROOT/arch leaks, buildvm compiles for iOS and cannot execute on the runner → the custom command 'runs' an iOS binary and fails cryptically ('bad CPU type' / cannot execute). Explicitly pin host SDK+arch for those sub-builds.
- On an Apple-silicon runner the host arch is arm64, same as the target — this MASKS a broken decoupling (buildvm might accidentally be an iOS arm64 binary that still won't run because it's built for iphoneos, or a macOS arm64 binary that runs fine). Test on the actual runner and check with vtool -show-build that the host tools are platform=MACOS, not IOS.
- set_source_files_properties(lj_vm.S PROPERTIES LANGUAGE CXX) at line 424 forces the assembler through the C++ driver; ensure the iOS clang++ assembles the Mach-O arm64 .S with the right -arch/-isysroot (it should via CMake managed flags, but the .S must not inherit host flags).
- LTO/IPO interacting with the hand-written asm object can miscompile; keep xrLuaJIT out of LTO (it already sets UNITY_BUILD OFF; also exclude from INTERPROCEDURAL_OPTIMIZATION).
- DEPLOYMENT_TARGET/PLATFORM variable names differ between the deps invocation and this subproject; thread the min-version through consistently.
- The APPLE branch FATAL_ERRORs if CMAKE_OSX_DEPLOYMENT_TARGET is empty (lines 201-203) — ensure it is set for iOS (the toolchain sets DEPLOYMENT_TARGET; map it).

### ⬜ 2.10 Apple/iOS-guard the bare pthread and dl link entries (xrCore, imgui-proj)

`status: todo` · `effort: low`

**Goal.** Several targets link the bare library names 'pthread' and 'dl' which become -lpthread/-ldl. iOS has no libpthread.dylib or libdl.dylib to link by name (both are folded into libSystem), so these entries fail the link. Guard them so Apple/iOS does not receive them.

**Steps**

1. xrCore pthread (line 457): on Apple, pthread is in libSystem; linking 'pthread' by name works on macOS (compat symlink) but there is NO libpthread on iOS. Replace the bare 'pthread' with a guarded entry: use find_package(Threads) + Threads::Threads (which on Apple resolves to no extra lib / -pthread compile flag), or wrap the bare pthread in $<$<NOT:$<PLATFORM_ID:Apple>>:pthread>. Prefer Threads::Threads for portability.
2. xrCore dl (lines 468-473): the guard 'NOT OpenBSD AND NOT NetBSD AND NOT WIN32 AND NOT HAIKU' now TRUE for iOS, adding -ldl which fails on iOS. Extend the guard with 'AND NOT APPLE' (macOS also folds dl into libSystem, and CMake historically links dl fine there, but iOS does not — safest to exclude all APPLE). Verify macOS desktop (now disabled on this branch) is unaffected since desktop macOS build is dropped for ios-port.
3. imgui-proj dl (lines 26-31): same guard; add 'AND NOT APPLE' (or key on NOT XR_PLATFORM_APPLE). imgui only needs dl for its (unused-on-iOS) dynamic loading; the static GL3 backend doesn't require it.
4. Search for any other bare 'pthread'/'dl'/'rt'/'m' link entries across src and Externals that the 'NOT WIN32' pattern would now feed to iOS (grep for target_link_libraries with pthread|dl|rt) and guard consistently.

**Files**

- `src/xrCore/CMakeLists.txt:455-457 (PUBLIC pthread)`
- `src/xrCore/CMakeLists.txt:468-473 (dl on NOT OpenBSD/NetBSD/WIN32/HAIKU — now matches iOS)`
- `Externals/imgui-proj/CMakeLists.txt:26-31 (dl on NOT OpenBSD/NetBSD/WIN32/HAIKU)`

**Acceptance**

- [ ] No -lpthread or -ldl appears in the iOS link command for any engine or Externals target (inspect the Xcode link line / compile_commands).
- [ ] xrCore and xrImGui link cleanly under both iOS SDKs.
- [ ] macOS/Linux/BSD/Haiku link paths are unchanged (guards only add NOT APPLE / use Threads::Threads).

**Risks / easy to miss**

- A single missed bare 'dl'/'pthread' anywhere in the transitive graph fails the whole static app link with 'library not found for -ldl'.
- Threads::Threads requires find_package(Threads); on Apple it sets the -pthread compile flag harmlessly but do not also pass bare pthread or you double up.
- LuaJIT also links dl conditionally (Externals/LuaJIT-proj/CMakeLists.txt:574) — handled in 2.9 via LUA_USE_DLOPEN OFF; make sure the guard there and here are consistent.

### ⬜ 2.11 Confirm AGS_SDK / GameSpy / DiscordGameSDK / RenderDoc / DX11 R4 stay out of the iOS link (Apple non-macOS audit)

`status: todo` · `effort: medium`

**Goal.** These components are Windows/desktop-only and must not enter the iOS static link. The seed says they are 'already non-Windows-gated', but iOS is a NEW non-Windows platform, so every 'NOT WIN32' gate must be re-checked: some gates that meant 'Linux/BSD' now also admit iOS and could drag in code that assumes Linux, or (for GameSpy) a submodule that must still provide a linkable stub.

**Steps**

1. DX11 R4 renderer: confirmed gated — Layers/CMakeLists.txt:2-4 only adds xrRenderPC_R4 on WIN32, and entry_point.cpp:27-29 only references render_r4::GetRendererModule() under XR_PLATFORM_WINDOWS. No action beyond confirming iOS never enters this branch.
2. AGS_SDK (AMD GPU services) and RenderDoc: verify they are referenced ONLY under WIN32 in src (grep found no src CMake references, so they are likely included only via headers under a WIN32/DX path). Confirm no iOS TU includes amd_ags.h or renderdoc_app.h. RenderDoc submodule is under Externals/renderdoc (dir exists) but not add_subdirectory'd — confirm it is header-only + WIN32-gated in code.
3. DiscordGameSDK: confirm it is not add_subdirectory'd in Externals/CMakeLists.txt (it is not) and not linked by any engine target on iOS. Grep src for discord includes under non-WIN32.
4. GameSpy is the real risk: Externals/CMakeLists.txt:15 add_subdirectory(GameSpy) is UNCONDITIONAL, xrGameSpy is built unconditionally (src/CMakeLists.txt:19) and xrGame links it (xrGame/CMakeLists.txt:2496). On Linux this works because the GameSpy submodule's CMakeLists provides a GameSpy-oxr target (with internal WIN32 gating of platform bits) and PlatformApple.inl even '#define _LINUX // for GameSpy'. For iOS: (a) ensure the GameSpy submodule is checked out on CI (it is a submodule; the ios.yml checkout must recurse submodules), (b) verify GameSpy compiles for iOS arm64 (it is portable C sockets code; it built for macOS/Linux) — if not, decide to gate xrGameSpy out for iOS and stub the multiplayer entry points xrGame expects, or keep it (multiplayer is not a Phase-2/near-term goal).
5. Decision to record: simplest low-risk path is to KEEP GameSpy in the link if it cross-compiles (it is just BSD sockets + the _LINUX shim), avoiding surgery on xrGame's link graph. Only gate it out if it blocks the link. If gating out, add an XR_PLATFORM_APPLE_IOS guard around add_subdirectory(GameSpy)/xrGameSpy and provide null implementations for the symbols xrGame references.
6. Re-audit all 'NOT WIN32' / 'PLATFORM_ID:Linux' branches in Externals and src CMake for ones that now admit iOS incorrectly (e.g. execinfo at xrCore/CMakeLists.txt:458 is BSD-only via generator expr — fine; but any Linux-only lib added under a broad NOT WIN32 needs an explicit NOT APPLE or platform check).

**Files**

- `src/Layers/CMakeLists.txt:1-7 (xrRenderPC_R4 only if WIN32 — good; xrRenderPC_GL always)`
- `Externals/CMakeLists.txt:15-16 (add_subdirectory(GameSpy), imgui-proj)`
- `src/xrGameSpy/CMakeLists.txt (links GameSpy-oxr)`
- `src/xrGame/CMakeLists.txt:2496 (xrGame links xrGameSpy)`
- `src/CMakeLists.txt:19 (add_subdirectory(xrGameSpy) unconditional)`
- `.gitmodules:9-14 (GameSpy, AGS_SDK submodules)`

**Acceptance**

- [ ] No DX11/R4, AGS, RenderDoc, or Discord symbols/headers enter any iOS TU or the final link.
- [ ] Either GameSpy cross-compiles for iOS arm64 and links, OR it is cleanly gated out with xrGame's references satisfied by stubs — the engine link closes either way.
- [ ] ios.yml checkout recurses submodules so GameSpy/AGS/gli/sse2neon/imgui/luabind/LuaJIT are present.

**Risks / easy to miss**

- The GameSpy submodule is NOT checked out on the Windows dev box (git submodule status shows '-'); its CMakeLists could contain a Windows-only assumption that only surfaces on the iOS runner. Must validate on CI.
- PlatformApple.inl's '#define _LINUX' (line ~32) is a blunt instrument that makes GameSpy think it is on Linux; on iOS this may pull Linux-specific socket options (e.g. MSG_NOSIGNAL, SO_* differences) that differ on Darwin — watch for compile errors in GameSpy socket code.
- If xrGameSpy is gated out, xrGame (xrGame/CMakeLists.txt:2496) and possibly xrNetServer will have undefined references to GameSpy wrapper symbols — need matching stubs, which is more work than keeping it.
- actions/checkout without submodules:recursive silently yields empty Externals dirs and a confusing add_subdirectory failure.

### ⬜ 2.12 Minimal source/compile fixes to close the iOS link (PlatformApple.inl, StackTrace, entry point, GL renderer glad)

`status: todo` · `effort: high`

**Goal.** Get every engine TU to COMPILE for iOS arm64 so the static libs are producible. This is not the full port — only the minimal, central compile-blockers that stop the archive from building. Runtime correctness (renderer, lifecycle) is Phases 3-4.

**Steps**

1. Verify <xlocale.h> (PlatformApple.inl:28) is present in the iOS SDK — it exists but is deprecated; if it errors, switch to <locale.h>/<xlocale.h> guarded by availability. Low-risk, but a hard compile stop if wrong.
2. GL renderer link feasibility: xrRender_GL uses the vendored glad loader (sdk/include/glad/gl.c + gl.h) which declares desktop-GL function pointers as its OWN globals — it does NOT include a platform GL SDK header, so the renderer should COMPILE and LINK for iOS with all glXXX symbols resolved internally to glad (they are just null pointers at runtime). Confirm glad/gl.c has no __APPLE__/dlopen/GLX/WGL platform assumptions that break iOS compile (grep showed none). This means the full engine INCLUDING the renderer can link in Phase 2, deferring GLES correctness to Phase 4.
3. If any renderer TU fails to compile on iOS for a non-runtime reason (e.g. a desktop-only GL enum missing from glad, or SDL_opengl.h inclusion), either add the missing glad decl or #ifdef the offending line under XR_PLATFORM_APPLE_IOS. Do the MINIMUM to compile; do not attempt GLES conversion (Phase 4).
4. Contingency (record as fallback, do not do preemptively): if the GL renderer proves too costly to even compile, wire a temporary null RendererModule stub so the app target links without xrRender_GL, and revisit in Phase 4. Prefer NOT to, since glad likely lets it link as-is.
5. entry_point.cpp: the non-Windows main() (lines 79-137) is a plain int main(argc,argv). On iOS with SDL2, the real entry is SDL's UIKit main shim which calls SDL_main. Decide the entry strategy now (full lifecycle is Phase 3): for Phase 2 linking, ensure exactly ONE main() exists — either rename the engine main to SDL_main and let SDL2main provide the UIKit main(), or keep main() and don't link SDL2main. Duplicate-main is the classic iOS link failure.
6. StackTrace.cpp: check its Apple path uses backtrace()/backtrace_symbols()/dladdr from <execinfo.h>/<dlfcn.h> (present on iOS) and does not require the BSD execinfo lib (that lib is only linked for FreeBSD/OpenBSD/NetBSD at xrCore/CMakeLists.txt:458). Ensure no compile-time dependency on a Linux-only symbol.
7. Do a first full configure+build on CI and triage the compile errors centrally: fix shared headers (Platform*.inl, stdafx, xr_types) rather than per-file where possible (the seed prefers central/systemic changes).

**Files**

- `src/Common/PlatformApple.inl:28 (<xlocale.h>), :32 (#define _LINUX)`
- `src/xrCore/Debug/StackTrace.cpp (backtrace/dladdr on Apple)`
- `src/xr_3da/entry_point.cpp:79-137 (main() / SDL_main on iOS)`
- `src/Layers/xrRenderGL/glHW.cpp (desktop GL context + KHR_debug)`
- `sdk/include/glad/gl.c / gl.h (self-contained desktop-GL function pointers)`
- `src/xrCore/xrCore.h:24 (XRAY_STATIC_BUILD marker)`

**Acceptance**

- [ ] Every engine static lib (xrCore, xrCDB, xrEngine, xrGame, xrSound, xrScriptEngine, xrNetServer, xrPhysics, xrParticles, xrAICore, xrUICore, xrMaterialSystem, xrMiscMath, xrRender_GL, xrGameSpy, xrAPI, and Externals xrLuaJIT/xrLuabind/xrLuaFix/OPCODE/ode/xrImGui) compiles to a .a for both iOS SDKs.
- [ ] Exactly one program entry point exists in the final link (no duplicate main/SDL_main).
- [ ] No Linux-only symbol or header breaks an iOS compile.

**Risks / easy to miss**

- The GL renderer is the biggest unknown for COMPILE (not runtime): glHW.cpp references GL_DEBUG_SEVERITY_* and GLDEBUGPROC (desktop KHR_debug) — glad desktop headers declare these so it should compile, but confirm on CI.
- SDL2main / main() duplication is subtle: SDL on iOS #defines main to SDL_main via a header macro; if xr_3da includes SDL headers in the TU defining main(), the symbol gets renamed unexpectedly. Control this deliberately.
- Fixing compile errors file-by-file risks sprawling into hundreds of edits; keep fixes in shared headers and guard on XR_PLATFORM_APPLE_IOS to avoid touching many TUs (aligns with the 'central over per-file' constraint).
- PURE_DYNAMIC_CAST / PURE_ALLOC and exception settings differ per config (ReleaseMasterGold disables exceptions, XRay.Configurations/Build); a compile issue may only appear in the ReleaseMasterGold config CI uses — build the same config CI will ship.

### ⬜ 2.13 Create the iOS application-bundle target that links the whole engine + game into one static Mach-O

`status: todo` · `effort: high`

**Goal.** Produce the single MACOSX_BUNDLE executable that statically links every engine + game + Externals lib into one arm64 iOS Mach-O with an Info.plist — the concrete Phase-2 deliverable. Replaces the desktop xr_3da exe topology for iOS while reusing entry_point.cpp.

**Steps**

1. Author an iOS app target. Two options: (a) add an APPLE_IOS branch inside src/xr_3da/CMakeLists.txt that makes xr_3da a MACOSX_BUNDLE with the iOS link set and an Info.plist; or (b) a dedicated misc/ios app CMakeLists that reuses entry_point.cpp and links the engine. Prefer (a) to reuse the existing target/entry wiring, guarded by the iOS detection flag.
2. Set MACOSX_BUNDLE on the target and supply MACOSX_BUNDLE_GUI_IDENTIFIER (e.g. io.github.openxray), BUNDLE_NAME, BUNDLE_VERSION, SHORT_VERSION_STRING, and an Info.plist template with UIRequiredDeviceCapabilities=arm64/metal, UILaunchStoryboardName or a launch screen, supported orientations, and (for sideload/JIT) note get-task-allow comes from the SideStore re-sign, not from us. Mirror the smoketest's CODE_SIGNING_ALLOWED/REQUIRED=NO for unsigned CI output.
3. Link the full graph: xrCore, xrAPI, xrEngine, xrRender_GL, xrGame — the same as the desktop exe (xr_3da/CMakeLists.txt:27-34). Transitively this pulls xrSound, xrScriptEngine, xrNetServer, xrCDB, xrPhysics, xrParticles, xrAICore, xrUICore, xrMaterialSystem, xrMiscMath, xrGameSpy, xrImGui, xrLuaJIT, xrLuabind, xrLuaFix, OPCODE, ode, and the deps (SDL2, OpenAL, ogg/vorbis/theora, lzo). Confirm the direct-symbol module refs in entry_point.cpp (render_gl::GetRendererModule, &xrGame) force those archives in.
4. Guard against dead-stripping of self-registering TUs: the static libs contain global-ctor registrations (script exporters, class factories). With ld64 and static archives, objects with no referenced symbol can be dropped, silently removing script/class registration. Ensure whole-archive inclusion where needed — either use -force_load on the archives that self-register (xrGame, xrEngine, xrScriptEngine, renderer) or CMake's $<LINK_LIBRARY:WHOLE_ARCHIVE,...>. This is a very common static-link-on-Apple pitfall.
5. Add the iOS system frameworks the engine needs at the app link (inherited from SDL/OpenAL interface, but add explicitly if missing): UIKit, Foundation, CoreGraphics, QuartzCore, Metal, OpenGLES, GameController, CoreMotion, AudioToolbox, AVFoundation, CoreAudio, CoreHaptics, ImageIO.
6. Resolve the main() entry: link SDL2main (or the SDL_main shim) so SDL provides the UIKit main() that calls into the engine's SDL_main, OR keep the engine main() and provide a minimal UIApplicationMain wrapper — pick one, consistent with task 2.12. For Phase 2 (need not boot) the simplest linkable choice is fine.
7. Bundle nothing heavy: Phase 2 need not include game assets; a minimal bundle that links is the goal. Asset/resource staging is Phase 3+.
8. Produce the .app in the Xcode build tree exactly like the smoketest so the existing ios.yml 'Inspect binary' (lipo/vtool) and 'Package unsigned .ipa' steps can be reused/adapted.

**Files**

- `src/xr_3da/CMakeLists.txt:1-42 (desktop exe: entry_point.cpp + resources, links xrCore/xrAPI/xrEngine/xrRender_GL/xrGame)`
- `src/xr_3da/entry_point.cpp (shared entry)`
- `misc/ios/ (new app target CMake + Info.plist)`
- `cmake/toolchains/ios.toolchain.cmake (MACOSX_BUNDLE handling)`
- `misc/ios/smoketest/CMakeLists.txt (reference for bundle props/signing attrs)`

**Acceptance**

- [ ] cmake --build produces <app>.app/<binary> that is a single arm64 Mach-O for both iphoneos and iphonesimulator (lipo -info confirms arm64; vtool -show-build confirms platform IOS / IOSSIMULATOR).
- [ ] The link succeeds with zero unresolved symbols (the APPLE -Wl,-undefined,error policy is satisfied).
- [ ] nm/symbol inspection shows engine symbols (e.g. from xrGame, xrEngine, xrRender_GL) present in the binary, proving the whole engine linked in, not just a stub.
- [ ] No duplicate-main and no dead-stripped self-registration (spot-check a known script-exporter symbol is retained).

**Risks / easy to miss**

- Self-registration dead-stripping is the top risk: without -force_load/WHOLE_ARCHIVE, the binary links but is missing class factories/script bindings — invisible in Phase 2 (need not boot) but will bite in Phase 3. Add whole-archive now for the registering libs.
- Ordering of static libs matters for ld (though ld64 is more forgiving than GNU ld); circular deps between engine libs (xrEngine<->xrRender via interfaces) may need repeated libs or WHOLE_ARCHIVE.
- Info.plist minimum keys: a missing CFBundleExecutable/identifier makes the .app malformed and the ipa packaging step fail even though the binary linked.
- The desktop xr_3da target also pulls resource.rc/.ico/.bmp (Windows resources, xr_3da/CMakeLists.txt:6-13) — exclude those from the iOS target.
- LTO across the whole static graph at link can exceed the 20-min CI budget or the runner's memory; consider disabling INTERPROCEDURAL_OPTIMIZATION for the app target during bring-up.

### ⬜ 2.14 Extend ios.yml CI to build deps + full engine for both SDKs and verify the single Mach-O

`status: todo` · `effort: medium`

**Goal.** Turn the Phase-2 work into an enforced, reproducible CI gate: the macOS runner builds the deps (with caching), configures the engine with the iOS toolchain + deps prefix, builds the app target for iphoneos and iphonesimulator, and verifies one arm64 iOS Mach-O containing the engine. This is the only place the work can be validated (Windows dev box cannot build iOS).

**Steps**

1. Add submodule checkout: change actions/checkout to fetch submodules recursively (LuaJIT, luabind, GameSpy, gli, sse2neon, imgui, xrLuaFix are required for the engine build; the smoketest didn't need them).
2. Add a 'Build deps' job/step that runs misc/ios/deps/build-deps.sh for the matrix SDK, wrapped in actions/cache keyed on (sdk, dep-versions, toolchain sha). Keep the two SDKs as the existing matrix (OS64 + SIMULATORARM64) established in ios.yml:31-39.
3. Add an engine configure step (separate from the smoketest working-directory) that runs from the repo root: cmake -B build-ios -G Xcode -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/ios.toolchain.cmake -DPLATFORM=${matrix.platform} -DDEPLOYMENT_TARGET=15.0 -DENABLE_BITCODE=OFF -DBUILD_SHARED_LIBS=OFF -DXRAY_USE_LUAJIT=ON -C misc/ios/deps/<sdk>-deps.cmake (the deps cache-init from 2.1) --log-level=VERBOSE.
4. Add the engine build step: cmake --build build-ios --config ReleaseMasterGold (match the shipping config; note LTO caveat) or Release for faster bring-up — decide and document. Keep timeout-minutes generous but within runner limits; raise from 20 if needed.
5. Keep the Phase-1 smoketest job as a fast toolchain canary (own job) so a red engine build is distinguishable from a broken toolchain (per the ios.yml header comment intent).
6. Adapt the 'Inspect binary' step to the engine app: find <app>.app, run file/lipo -info/vtool -show-build on the engine binary, and add a symbol check (nm | grep for a known engine symbol) to prove the engine actually linked in (not an empty stub).
7. For the device (OS64) matrix entry, reuse the unsigned .ipa packaging (ios.yml:75-94) to produce an artifact; for the simulator entry keep it compile/link-only.
8. Make the engine build REQUIRED (fail-fast per matrix already false at ios.yml:29) so regressions on ios-port are caught.

**Files**

- `.github/workflows/ios.yml:17-95 (whole workflow; currently smoketest-only)`
- `misc/ios/deps/build-deps.sh (from 2.1)`
- `cmake/toolchains/ios.toolchain.cmake`

**Acceptance**

- [ ] A push to ios-port runs: submodules checkout → deps build (cached) → engine configure → engine build → binary inspection, green for BOTH iphoneos and iphonesimulator.
- [ ] The device job uploads an unsigned .ipa artifact containing the engine app bundle.
- [ ] The binary-inspection step confirms arm64 + platform IOS/IOSSIMULATOR and the presence of engine symbols.
- [ ] The smoketest canary job still runs and passes independently.

**Risks / easy to miss**

- 20-minute timeout (ios.yml:27) is tight for a from-scratch engine + deps build; rely on ccache/dep cache and possibly split deps vs engine into dependent jobs, or raise the timeout.
- Dep cache key must include the SDK and toolchain sha or a stale simulator/device prefix gets reused and produces wrong-slice link errors.
- actions/upload-artifact and .ipa packaging expect a well-formed bundle; a link-success-but-malformed-plist will fail packaging (couple with 2.13's Info.plist).
- Building the ReleaseMasterGold config (exceptions off, MASTER_GOLD) may expose compile errors not seen in Release; pick the config deliberately and keep it consistent with the intended shipping config.
- Free macOS CI minutes are ample for public repos, but concurrent matrix + long LTO links can still queue; keep the graph lean during bring-up.

### Phase 2 cross-cutting notes

- iOS binaries build ONLY on macOS CI (GitHub Actions macos-latest). Nothing in this phase can be compiled/verified on the Windows dev box — every change is authored blind and validated by pushing to the ios-port branch and reading the ios.yml run. Keep CI turnaround tight and expect several red runs per task.
- The leetal toolchain (cmake/toolchains/ios.toolchain.cmake) already: sets CMAKE_OSX_SYSROOT to the iphoneos/iphonesimulator SDK, sets CMAKE_OSX_ARCHITECTURES=arm64, appends the SDK to CMAKE_FIND_ROOT_PATH (line ~1112), and sets CMAKE_IGNORE_PATH to exclude /usr/local/lib and /opt/homebrew (line ~1113) so Homebrew libs are NOT accidentally found. Deps must therefore be installed into a prefix that is on CMAKE_FIND_ROOT_PATH / CMAKE_PREFIX_PATH, per-SDK (device libs and simulator libs are NOT interchangeable — different sysroot, different arch slice even though both are arm64).
- CMake passes -isysroot and -arch as managed flags derived from CMAKE_OSX_SYSROOT/CMAKE_OSX_ARCHITECTURES; they are NOT in CMAKE_C_FLAGS. Any place that shells out to the compiler manually (LuaJIT lj_arch.h detection at Externals/LuaJIT-proj/CMakeLists.txt:141) will silently use the HOST arch/SDK unless you append -isysroot/-arch/-mios-version-min yourself.
- Device (OS64) and simulator (SIMULATORARM64) are two independent build trees. Everything (deps + engine) must be built twice, once per SDK. Never lipo device+simulator arm64 slices into one fat lib — they collide; keep them in separate prefixes/build dirs.
- Static build changes the plugin model: with XRAY_STATIC_BUILD there is no dlopen — the renderer and game modules are referenced by direct symbol (entry_point.cpp: xray::render::render_gl::GetRendererModule(), &xrGame). Confirm every module that was a runtime .dll/.so becomes a static lib pulled into the final link; missing whole-archive inclusion can silently drop self-registering translation units.
- Apple + iOS is XR_PLATFORM_APPLE + XR_PLATFORM_POSIX but NOT Linux/BSD. Many CMake guards use 'NOT WIN32' or 'NOT OpenBSD/NetBSD' which now MATCH iOS and can pull Linux-only link entries (dl, pthread, execinfo, mimalloc, SDL2 system package). Audit every 'NOT WIN32' branch for iOS correctness, don't assume APPLE is already handled just because macOS built.
- PlatformApple.inl currently does '#define _LINUX // for GameSpy' and includes <xlocale.h>; that macro plus the Windows-ish shims are shared by macOS and iOS. Any per-iOS behavioral fork must key on XR_PLATFORM_APPLE_IOS (already defined in Platform.hpp:33), never on plain __APPLE__.
- Keep the Phase 1 smoketest target and its ios.yml job working as a toolchain canary; add the engine build as ADDITIONAL matrix entries/jobs rather than replacing the smoketest, so a red engine build is distinguishable from a broken toolchain.
- MEMORY_ALLOCATOR must resolve to 'standard' on iOS (USE_PURE_ALLOC, not USE_MIMALLOC). mimalloc is never add_subdirectory'd (Externals/CMakeLists.txt does not add it) — it only comes from find_package(mimalloc); simply don't provide it for iOS and ensure the FATAL_ERROR path (GNULike.cmake:162) is not hit.
- LuaJIT baseline for iOS is interpreter mode which ALWAYS works once lj_vm.S is emitted for arm64 by a HOST-native buildvm. The optional JIT variant is a separate build flavor; do not let the JIT-optional work block the interpreter link. Runtime JIT fallback (RWX mmap failure) is a Phase 3 concern, not Phase 2.

---

<a id="phase-3"></a>

## Phase 3 — Boot to a window with iOS app lifecycle

> **Phase goal.** Make the linked static engine binary (Phase 2 output) actually launch and stay alive as an iOS app: entry runs through SDL2main's SDL_main → SDL_UIKitRunApp → UIApplicationMain; the blocking desktop main loop becomes a per-frame tick driven by CADisplayLink via SDL_iPhoneSetAnimationCallback; the app survives background/foreground/rotation/low-memory and terminates cleanly; the filesystem resolves read-only gamedata from the app bundle and writable state from the iOS sandbox (SDL_GetPrefPath); and the engine reaches init/console presenting a cleared backbuffer. No scene rendering, shaders, textures, or touch input yet (those are Phases 4-5). All iOS behavior is gated behind XR_PLATFORM_APPLE_IOS with zero behavior change on macOS/Linux/Windows, and everything is validated only on the GitHub Actions macOS runner (the Windows dev box cannot build iOS).

### ⬜ 3.1 Route the entry point through SDL2main / SDL_main on iOS

`status: todo` · `effort: low`

**Goal.** iOS apps must boot via UIApplicationMain. SDL provides this only if libSDL2main supplies the real main() and the engine's entry is renamed to SDL_main by including <SDL_main.h>. Without this the binary has a bare main() that never creates a UIApplication, so no window, run loop, or lifecycle events ever exist.

**Steps**

1. In entry_point.cpp, above the `#else`/`int main` block (currently line 80-131), add `#if defined(XR_PLATFORM_APPLE_IOS)\n#include <SDL_main.h>\n#endif` so that on iOS `main` is textually replaced by `SDL_main` (SDL defines SDL_MAIN_NEEDED for iOS).
2. Keep the POSIX `int main(int argc, char* argv[])` signature — with SDL_main.h it becomes `int SDL_main(int argc, char* argv[])`; SDL's own main() (from SDL2main) calls SDL_UIKitRunApp and invokes it on the main thread after didFinishLaunchingWithOptions.
3. Guard the arg-concatenation branch (entry_point.cpp:88-109): on iOS argc is effectively 1 (SDL passes only the program name), so wrap the whole `if(argc>1){...}else{...}` such that iOS always calls `entry_point("")` — i.e. `#if defined(XR_PLATFORM_APPLE_IOS) result = entry_point(""); #else <existing arg concat> #endif`. This removes reliance on real command-line args that iOS never provides and avoids the xr_malloc/strcat dance.
4. In src/xr_3da/CMakeLists.txt target_link_libraries(xr_3da ...), add SDL2main for iOS: `if(XR_PLATFORM_APPLE_IOS or PLATFORM matches OS64/SIMULATOR) target_link_libraries(xr_3da PRIVATE SDL2::SDL2main)`. Prefer keying off a CMake variable set by the toolchain (e.g. a new XRAY_IOS cache/interface) rather than compiler macros, since CMake can't see XR_PLATFORM_APPLE_IOS. SDL2main must be linked with whole-archive semantics on some setups so its main() is not dead-stripped.
5. Verify SDL was cross-built WITH the SDL2main static lib for iphoneos/simulator in Phase 2; if Phase 2 built only SDL2::SDL2, add the SDL2main target there.

**Files**

- `src/xr_3da/entry_point.cpp:1-131 (add SDL_main.h include; guard/drop the argc/argv concat branch on iOS)`
- `src/xr_3da/CMakeLists.txt:27-34 (link SDL2main on iOS)`
- `src/Common/Platform.hpp:32-38 (XR_PLATFORM_APPLE_IOS reference)`

**Acceptance**

- [ ] Simulator CI job compiles and links xr_3da with an SDL_main symbol (verify with `nm` that _SDL_main exists and libSDL2main's _main is present).
- [ ] On a simulator run the app reaches the CApplication constructor (log line 'Initializing Engine...' or the build-info banner appears in device console), proving UIApplicationMain launched and called SDL_main.
- [ ] macOS/Linux/Windows builds are byte-for-byte unaffected (SDL_main.h not included there).

**Risks / easy to miss**

- SDL2main's main() can be dead-stripped by LTO/linker (CMAKE_INTERPROCEDURAL_OPTIMIZATION is ON for Release, XRay.Build.cmake:31); may need -force_load / $<LINK_LIBRARY:WHOLE_ARCHIVE,...> or KEEP.
- Including SDL_main.h in a .cpp that also declares WinMain/other mains can double-define; keep it strictly iOS-guarded.
- If Phase 2 defined SDL_MAIN_HANDLED anywhere globally, the `#define main SDL_main` is suppressed and the app silently won't boot — grep for SDL_MAIN_HANDLED.

### ⬜ 3.2 Refactor CApplication::Run's blocking loop into a per-frame tick under SDL_iPhoneSetAnimationCallback

`status: todo` · `effort: medium`

**Goal.** iOS wants the frame loop driven by CADisplayLink, not a busy while-loop that starves the UIKit run loop (breaking touch, animations, and lifecycle). Extract the loop body into a reusable tick, and on iOS register it as the animation callback then return, letting SDL keep the app alive.

**Steps**

1. Extract the body of the `while (!SDL_QuitRequested())` loop (x_ray.cpp:374-436) verbatim into a new private member `void CApplication::LoopFrame()` declared in x_ray.h. It contains: FrameMarkStart, the SDL_PeepEvents(SDL_WINDOWEVENT range) drain, the activate/deactivate switch, `Device.OnWindowActivate`, `Device.ProcessFrame()`, `UpdateDiscordStatus()`, FrameMarkEnd.
2. IMPORTANT: LoopFrame must pump events itself. The old while-condition `SDL_QuitRequested()` (x_ray.cpp:372) internally called SDL_PumpEvents; with the loop gone, add an explicit `SDL_PumpEvents();` at the top of LoopFrame and a quit check via `if (SDL_HasEvent(SDL_QUIT)) { <request shutdown>; }` (see quit handling in step 5).
3. Add a static thunk matching SDL's callback signature `void (*)(void*)`: `static void CApplication::AnimationCallback(void* param){ static_cast<CApplication*>(param)->LoopFrame(); }` declared in x_ray.h.
4. Rewrite Run(): keep `HideSplash(); Device.Run();` prologue (367-370). Then branch: `#if defined(XR_PLATFORM_APPLE_IOS)` call `SDL_iPhoneSetAnimationCallback(Device.m_sdlWnd, 1, &CApplication::AnimationCallback, this);` and `return 0;` (do NOT call Device.Shutdown() here — shutdown happens on SDL_APP_TERMINATING, see Task 3.4). `#else` keep the existing `while(!SDL_QuitRequested()) LoopFrame();` then `Device.Shutdown(); return 0;` `#endif`. Include <SDL_system.h> for SDL_iPhoneSetAnimationCallback (iOS-only header).
5. iOS quit path: because Run() returns while the app lives, termination is driven by SDL_APP_TERMINATING (Task 3.4) which must call Device.Shutdown() and the CApplication destructor sequence. Ensure LoopFrame tolerates being called after a quit was requested (guard against double-shutdown).
6. interval argument = 1 (fire every display refresh; ProMotion 120Hz or standard 60Hz). Do not try to hit a fixed 60 with the engine's own limiter — Task 3.3 removes it.
7. Keep MAX_WINDOW_EVENTS peep logic intact — it still works inside the tick since SDL_PumpEvents now runs first.

**Files**

- `src/xrEngine/x_ray.cpp:367-442 (CApplication::Run and its loop body)`
- `src/xrEngine/x_ray.h:18-48 (add LoopFrame + static animation callback declarations)`
- `src/xrEngine/Device.cpp:399-422 (CRenderDevice::Run, called before loop)`

**Acceptance**

- [ ] On simulator, after boot the app renders repeatedly (LoopFrame invoked ~display-refresh times/sec — instrument with a frame counter Msg every 60 frames).
- [ ] The app remains responsive: a tap or home-button press is delivered (proves the UIKit run loop is not starved).
- [ ] Desktop builds still use the while-loop path and behave identically (no regression in Windows/Linux frame loop).
- [ ] Run() returns promptly on iOS instead of blocking (verify via a log line immediately after SDL_iPhoneSetAnimationCallback).

**Risks / easy to miss**

- Forgetting SDL_PumpEvents in the tick → the SDL event queue never fills, input and lifecycle events stall.
- SDL_iPhoneSetAnimationCallback requires a valid window; must be called AFTER Device.Initialize() created m_sdlWnd (it is — Run() is called after the ctor).
- Calling Device.Shutdown()/returning nonzero on iOS from Run() would tear down the engine while UIApplication is still alive → crash. Shutdown must move to the TERMINATING handler.
- If exceptions escape LoopFrame they now propagate up through the CADisplayLink selector (Objective-C frame) — undefined; wrap LoopFrame body in the same try/catch the desktop main() uses, or ensure noexcept boundaries.

### ⬜ 3.3 Bypass the Sleep()-based frame limiter and busy-waits on iOS

`status: todo` · `effort: trivial`

**Goal.** CADisplayLink already paces the tick to the display; the engine's own millisecond Sleep() calls run on the main/UI thread and would stall the run loop, drop frames, and risk watchdog kills. Every Sleep on the render path must be compiled out on iOS.

**Steps**

1. In ProcessFrame (Device.cpp:290-299) wrap the `updateDelta`/`if (frameTime < updateDelta) Sleep(...)` block in `#if !defined(XR_PLATFORM_APPLE_IOS)` so the frame is never throttled by Sleep on iOS (CADisplayLink is the clock).
2. Wrap the trailing `if (!b_is_Active) Sleep(1);` (Device.cpp:301-302) in the same guard — when backgrounded the animation callback should ideally be paused/no-op (Task 3.4), not sleeping the UI thread.
3. In BeforeFrame (Device.cpp:164-168), on iOS replace `Sleep(100); return false;` with just `return false;` under a guard — the CADisplayLink will re-invoke next frame; do not sleep the UI thread while the device is not yet ready.
4. In RenderBegin (Device.cpp:44-48) DeviceState::Lost, guard the `Sleep(33)` out on iOS and just `return false;` (GLES contexts on iOS are not 'lost' the way D3D9 is; this path is mostly dead on GL but must not sleep).
5. Leave the dedicated-server branch (Device.cpp:292-293) untouched — dedicated server never runs on iOS.

**Files**

- `src/xrEngine/Device.cpp:290-303 (ProcessFrame FPS limiter + Sleep(1) when inactive)`
- `src/xrEngine/Device.cpp:160-168 (BeforeFrame Sleep(100) when !b_is_Ready)`
- `src/xrEngine/Device.cpp:44-47 (RenderBegin Sleep(33) on DeviceState::Lost)`

**Acceptance**

- [ ] No `Sleep(`/`usleep(` remains reachable on the iOS per-frame path (grep Device.cpp under the iOS guards).
- [ ] Frame cadence on iOS follows the display refresh (no artificial cap to ps_fps_limit_in_menu=60 via Sleep).
- [ ] Desktop builds keep all Sleep-based limiting unchanged.

**Risks / easy to miss**

- Removing the limiter uncaps GPU work — acceptable at Phase 3 (clear only) but note battery/thermal for later; the real fix is CADisplayLink preferredFramesPerSecond, not Sleep.
- BeforeFrame returning false without any yield could spin if b_is_Ready stays false, but since we return to the animation callback (not a tight loop), the run loop still breathes — verify b_is_Ready becomes true after Device.Create().
- ps_fps_limit / ps_fps_limit_in_menu (Device.cpp:26-27) stay defined; leaving them referenced under the guard is fine but don't delete (used on desktop).

### ⬜ 3.4 Wire iOS lifecycle (background/foreground/terminate/low-memory) via SDL_AddEventWatch into seqAppActivate/Deactivate + audio pause

`status: todo` · `effort: high`

**Goal.** iOS delivers app-state transitions synchronously and expects the app to immediately stop drawing and release the GPU on backgrounding, and to pause audio. The engine already has seqAppActivate/seqAppDeactivate signals used by the renderer, input, and persistent game; hook the iOS SDL_APP_* events into them (synchronously, before they hit the deferred queue) and pause OpenAL. Also handle graceful termination (Run() returned, so shutdown must happen here) and memory pressure.

**Steps**

1. Add a static event-watch callback: `static int CApplication::LifecycleWatch(void* userdata, SDL_Event* e)` declared in x_ray.h. Register it in the CApplication ctor on iOS after SDL_Init (x_ray.cpp:216-222): `#if defined(XR_PLATFORM_APPLE_IOS) SDL_AddEventWatch(&CApplication::LifecycleWatch, this); #endif`. The watch fires from inside SDL_PumpEvents on the thread that pumps (the main thread), synchronously, which is required because iOS's UIApplicationDelegate callbacks (willResignActive/didEnterBackground) run synchronously and SDL relays them there.
2. In LifecycleWatch, switch on e->type: SDL_APP_WILLENTERBACKGROUND, SDL_APP_DIDENTERBACKGROUND, SDL_APP_WILLENTERFOREGROUND, SDL_APP_DIDENTERFOREGROUND, SDL_APP_TERMINATING, SDL_APP_LOWMEMORY. Return 1 (keep events) except optionally consuming APP_* (return 0) so they don't also sit in the queue.
3. WILLENTERBACKGROUND: set a hard app-level flag `g_appInBackground = true` (new ENGINE_API bool) BEFORE the OS suspends; call `Device.OnWindowActivate(Device.m_sdlWnd, false)` which runs seqAppDeactivate (Device.cpp:585) — this deactivates renderer HW (glHW registers on seqAppDeactivate, glHW.cpp:43), input, and game persistent. Pause audio here (see step 6). Everything in this handler must complete synchronously; no deferral.
4. DIDENTERBACKGROUND: assert no more rendering will occur — the animation callback / DoRender must early-return while g_appInBackground (gate in DoRender at Device.cpp:236 with `if (g_appInBackground) return;` or before RenderBegin). This is the point after which GL calls are fatal.
5. WILLENTERFOREGROUND / DIDENTERFOREGROUND: on DIDENTERFOREGROUND clear g_appInBackground and call `Device.OnWindowActivate(Device.m_sdlWnd, true)` → seqAppActivate (Device.cpp:579) reactivates renderer/input, resume audio. Reset the frame timer (Device.cpp:406-416 logic) if large time gap to avoid a huge fTimeDelta spike.
6. Audio pause/resume: on background call `GEnv.Sound->pause_emitters(true)` and `GEnv.Sound->set_master_volume(0.f)` (interfaces at Sound.h:225,227; impls SoundRender_Core.cpp:88, SoundRender_CoreA.cpp:122). On foreground reverse it. Best-effort: also consider alcMakeContextCurrent(nullptr) on background to fully quiesce the OpenAL device (SoundRender_CoreA.cpp:140 already does this on destroy). Guard all against GEnv.Sound==nullptr.
7. SDL_APP_TERMINATING: this is the ONLY clean-shutdown path on iOS (Run() already returned). Trigger the same teardown the desktop does after its loop: `Device.Shutdown()` (seqAppEnd) then let the CApplication destructor run — but since the process is being killed, at minimum call Console->Execute("cfg_save")-equivalent and flush logs. Practically: set SDL_QUIT / call the shutdown subset that persists user.ltx and closes the log (destroyConsole path, x_ray.cpp:187-193). iOS gives ~5s; keep it short.
8. SDL_APP_LOWMEMORY: free reclaimable caches — call `Memory.mem_compact()` (used at Device.cpp:84) and drop any renderer texture/model caches that are safe to rebuild; log a warning. Do not crash.
9. Register g_appInBackground as ENGINE_API in Device.h/defines.h so DoRender and the animation tick can read it.

**Files**

- `src/xrEngine/x_ray.cpp:206-311 (CApplication ctor — register SDL_AddEventWatch on iOS)`
- `src/xrEngine/x_ray.cpp:313-365 (dtor / shutdown sequence to invoke on TERMINATING)`
- `src/xrEngine/Device.cpp:550-589 (OnWindowActivate → seqAppActivate/Deactivate + b_is_Active gating)`
- `src/xrEngine/Device.cpp:226-259 (DoRender — must be gated when backgrounded)`
- `src/xrEngine/Device.cpp:424-428 (Shutdown → seqAppEnd)`
- `src/xrSound/SoundRender_Core.cpp:88-95 (pause_emitters)`
- `src/xrSound/SoundRender_CoreA.cpp:122-140 (set_master_volume / alcMakeContextCurrent)`

**Acceptance**

- [ ] Pressing Home on device/simulator: within one run-loop turn the app stops issuing GL (no draws after DIDENTERBACKGROUND — verify no GL error / no crash on resume), audio goes silent.
- [ ] Returning to the app resumes rendering and audio with no crash and no giant time-delta hitch.
- [ ] Swiping the app away (terminate) flushes user.ltx/log without hanging (app exits within the iOS grace window).
- [ ] A simulated memory warning (Simulator > Features > Simulate Memory Warning) does not crash and triggers mem_compact.
- [ ] seqAppActivate/seqAppDeactivate observers (glHW, xr_input, IGame_Persistent) receive exactly one deactivate on background and one activate on foreground.

**Risks / easy to miss**

- THE classic iOS killer: any EAGL/GL/Metal call after DIDENTERBACKGROUND → immediate termination. The g_appInBackground gate must cover DoRender AND any SwapWindow AND ImGui platform-window updates (Device.cpp:216-224 UpdateViewports).
- SDL_AddEventWatch runs on the pumping thread and must be re-entrancy/lock aware — seqAppDeactivate.Process() touches renderer/task scheduler; ensure it's safe to call from within SDL_PumpEvents (it is, since the desktop already calls OnWindowActivate from the same thread that pumps).
- TaskScheduler->Pause(true) happens inside OnWindowActivate deactivate (Device.cpp:586); make sure worker threads are actually paused before iOS suspends or they may be killed mid-task.
- pause_emitters underflow (SoundRender_Core guards) — double-pause on repeated background events; use the g_appInBackground flag to make transitions idempotent.
- SDL may deliver DIDENTERFOREGROUND before the GL context is safe to touch on some iOS versions; do GL re-init lazily on the first foreground frame, not inside the watch.
- app_inactive_time bookkeeping (Device.cpp:430-431,580,584) assumes focus events; ensure the iOS activate/deactivate feeds the same accounting so timers don't drift.

### ⬜ 3.5 Add an iOS single-fullscreen-window branch in Device_Initialize.cpp

`status: todo` · `effort: low`

**Goal.** The desktop window setup (borderless+resizable, 640x480, hit-test drag, min-size, custom icon, ImGui secondary-viewport registration) is meaningless or harmful on iOS, which has exactly one fullscreen, non-resizable, system-managed window. Create that window correctly so SDL_GL_CreateContext and the run loop have a valid drawable.

**Steps**

1. In CRenderDevice::Initialize (Device_Initialize.cpp:47-82), add `#if defined(XR_PLATFORM_APPLE_IOS)` producing flags = `SDL_WINDOW_FULLSCREEN | SDL_WINDOW_ALLOW_HIGHDPI | SDL_WINDOW_HIDDEN` (keep HIDDEN; Device.Run() shows it later, Device.cpp:418-421). Then `GEnv.Render->ObtainRequiredWindowFlags(flags)` still runs to OR-in SDL_WINDOW_OPENGL (see Task 3.11). Do NOT set BORDERLESS/RESIZABLE on iOS.
2. Create the window at the native screen resolution: query `SDL_DisplayMode dm; SDL_GetDesktopDisplayMode(0,&dm);` and `SDL_CreateWindow(title, SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, dm.w, dm.h, flags)`; or create at 0x0 with SDL_WINDOW_FULLSCREEN_DESKTOP and read back the drawable size. Prefer letting SDL pick fullscreen size — pass the display bounds.
3. Guard out the desktop-only calls on iOS: SDL_SetWindowHitTest (line 76), SDL_SetWindowMinimumSize (line 77), ExtractAndSetWindowIcon (line 79). Keep xrDebug::SetWindowHandler(this) and TracySetProgramName.
4. Force fullscreen device mode so downstream code doesn't try windowed logic: set `psDeviceMode.WindowStyle = rsFullscreen;` (defines.h:51) and psDeviceMode.Monitor = 0 early on iOS (before UpdateWindowProps runs in Device.Create(), Device_create.cpp:40).
5. In SetSDLSettings (Device_Initialize.cpp:22-37) the existing `#ifdef SDL_HINT_*` guards are already safe (no-op if the hint doesn't exist on iOS); optionally add SDL_HINT_ORIENTATIONS to constrain allowed orientations (defer full rotation policy to Task 3.6 / Info.plist).
6. Under IMGUI_ENABLE_VIEWPORTS (line 84-103), ensure iOS does not attempt to register/raise secondary viewports — either compile IMGUI_ENABLE_VIEWPORTS off for iOS in Phase 2/here, or guard the cocoa/win PlatformHandleRaw branch (it already has no iOS case, which is fine).
7. Retina/backing scale: use SDL_GL_GetDrawableSize (not SDL_GetWindowSize) when computing dwWidth/dwHeight so the framebuffer matches the physical pixels; feed that into Device.Create (Device_create.cpp:41 passes dwWidth/dwHeight).

**Files**

- `src/xrEngine/Device_Initialize.cpp:40-109 (CRenderDevice::Initialize window creation)`
- `src/xrEngine/Device_Initialize.cpp:20-37 (SetSDLSettings hints)`
- `src/xrEngine/Device_Initialize.cpp:84-103 (IMGUI_ENABLE_VIEWPORTS main-viewport registration)`
- `src/xrEngine/defines.h:46-62 (WindowStyle enum / DeviceMode)`

**Acceptance**

- [ ] SDL_CreateWindow succeeds on simulator and returns a fullscreen window sized to the simulated device's screen (log the WxH).
- [ ] No hit-test/min-size/icon SDL calls execute on iOS (they are legitimately no-ops or skipped — verify no SDL errors logged).
- [ ] psDeviceMode.WindowStyle == rsFullscreen at the time UpdateWindowProps first runs.
- [ ] SDL_GL_GetDrawableSize returns 2x/3x the point size on a retina simulator, and dwWidth/dwHeight reflect pixels.

**Risks / easy to miss**

- Creating the window at 640x480 (the desktop default at line 73) then resizing to fullscreen causes an extra Reset; create at the right size up front.
- SDL_WINDOW_ALLOW_HIGHDPI is required for correct retina framebuffer; without it the GL viewport is 1x and everything renders in a corner.
- On iOS SDL ignores position args; using SDL_WINDOWPOS_CENTERED (as desktop line 73 does elsewhere) is harmless but rely on FULLSCREEN.
- Icon extraction (ExtractAndSetWindowIcon) reads embedded Windows resources; must be guarded or it pulls in resource.rc paths that don't exist on iOS.

### ⬜ 3.6 Neutralize video-mode enumeration / windowed handling and treat SIZE_CHANGED as rotation

`status: todo` · `effort: medium`

**Goal.** Device_mode.cpp assumes a desktop multi-monitor, mode-switching world (SetWindowFullscreen exclusive, SetWindowSize, GetClosestDisplayMode, GetNumDisplayModes). On iOS there is one display, no user-selectable modes, and no exclusive fullscreen; the only 'resolution change' is a device rotation that arrives as SDL_WINDOWEVENT_SIZE_CHANGED and must rebuild the viewport via Reset().

**Steps**

1. In UpdateWindowProps (Device_mode.cpp:109-166), add an early iOS branch that skips all SDL_SetWindowFullscreen/SetWindowSize/SetWindowDisplayMode calls (lines 118-158) — the window is already system-fullscreen. Just compute dwWidth/dwHeight from SDL_GL_GetDrawableSize, call UpdateWindowRects() (line 160) and set ImGui io.DisplaySize (line 164). No exclusive mode-set (SDL_WINDOW_FULLSCREEN at line 149 fails/does nothing on iOS).
2. In SelectResolution (Device_mode.cpp:185-238), on iOS set psDeviceMode.Width/Height from the current display bounds (SDL_GetDisplayBounds / drawable size) and return before the GetClosestDisplayMode logic (lines 202-235), which is desktop-only.
3. FillVideoModes (Device_mode.cpp:55) / FillResolutionsForMonitor: SDL_GetNumDisplayModes may return few/odd modes on iOS. It's called from CApplication ctor (x_ray.cpp:267). Keep it (harmless) but ensure R_ASSERT3(modeCount>0) at line 12 doesn't fire on iOS — if it can return 0, guard to synthesize a single mode from the current display. Verify on simulator; add a fallback that pushes one token equal to the native resolution.
4. Rotation handling: in ProcessEvent (Device.cpp:359-378) the SDL_WINDOWEVENT_SIZE_CHANGED case already updates psDeviceMode.Width/Height and calls Reset(). Confirm this fires on iOS rotation (SDL posts SIZE_CHANGED with swapped w/h). Ensure the guard `if (Width==data1 && Height==data2) break;` (line 365-367) correctly detects the swap and proceeds to Reset(). This is the intended 'treat SIZE_CHANGED as rotation' behavior — keep it, just make sure the iOS path reaches it (it's not iOS-gated, good).
5. SDL_DISPLAYEVENT_ORIENTATION (Device.cpp:315): on iOS this may also arrive; the current handler calls CleanupVideoModes/FillVideoModes then Reset()/UpdateWindowProps. Ensure that path is safe on iOS (FillVideoModes must not assert — see step 3).
6. Decide orientation policy: constrain supported orientations in Info.plist (UISupportedInterfaceOrientations, Task 3.9) and/or SDL_HINT_ORIENTATIONS. For Phase 3, landscape-only is simplest (STALKER UI is landscape) and reduces rotation churn; document the choice.

**Files**

- `src/xrEngine/Device_mode.cpp:109-166 (UpdateWindowProps)`
- `src/xrEngine/Device_mode.cpp:185-238 (SelectResolution)`
- `src/xrEngine/Device_mode.cpp:9-26 (FillResolutionsForMonitor)`
- `src/xrEngine/Device.cpp:305-397 (ProcessEvent — DISPLAYEVENT/WINDOWEVENT SIZE_CHANGED)`

**Acceptance**

- [ ] Rotating the simulator triggers exactly one SIZE_CHANGED → Reset() and the viewport/backbuffer resize to the new orientation without crash (cleared frame still fills the screen).
- [ ] No R_ASSERT3 in FillVideoModes/FillResolutionsForMonitor fires on iOS.
- [ ] UpdateWindowProps performs no SDL_SetWindowFullscreen/SetWindowDisplayMode on iOS (verify no SDL warnings).
- [ ] dwWidth/dwHeight and ImGui DisplaySize track the current orientation's drawable size.

**Risks / easy to miss**

- Reset() (declared Device.h:196) rebuilds the render device; on GL/iOS it must recreate the GLES surface, not a D3D swapchain — ensure the GL Reset path is a no-op-or-recreate that doesn't destroy the shared EAGL context. This overlaps Phase 4; for Phase 3 keep Reset minimal (just re-clear at new size).
- If orientation is locked in Info.plist, SIZE_CHANGED never fires — fine, but then don't depend on it for initial sizing; size from drawable size at create time.
- SDL on iOS sometimes reports display bounds in points, drawable in pixels — mixing them yields half-size rendering. Standardize on SDL_GL_GetDrawableSize for the framebuffer.
- GetClosestDisplayMode/SetWindowDisplayMode on iOS can silently no-op or error; leaving them reachable pollutes logs and may mis-set psDeviceMode.

### ⬜ 3.7 Resolve sandbox paths: read-only gamedata from the bundle, writable state to SDL_GetPrefPath

`status: todo` · `effort: high`

**Goal.** The iOS app bundle (Resources) is read-only; the app may only write inside its sandbox (Documents/Library via SDL_GetPrefPath). The current FS setup points both read ($fs_root$/gamedata) and write ($fs_root$/_appdata_, logs, saves, screenshots) at one root, and the POSIX setup_fs_path does Linux-only chdir/mkdir/symlink into /usr/share — none of which works in the iOS sandbox. Split read vs write roots and strip the Linux fallback.

**Steps**

1. xrCore.cpp: the POSIX branch (202-213) already sets Core.ApplicationPath = SDL_GetBasePath(); on iOS SDL_GetBasePath returns the bundle's resource dir (…/YourApp.app/) — read-only. Keep this for iOS but ensure the `if(!base_path)` SDL_GetPrefPath fallback (204-212) is NOT taken on iOS (base_path is always valid). WorkingPath via getcwd (232) is meaningless in the sandbox; on iOS set WorkingPath = ApplicationPath (or the prefpath) instead of getcwd.
2. Add an iOS branch in setup_fs_path (LocatorAPI.cpp:817-904): set `full_current_directory` = SDL_GetBasePath() (bundle) as the read-only $fs_root$. Do NOT run realpath on a relative fs_path, and DO NOT execute the chdir(pref_path)/symlink/mkdir block (lines 853-889) — that is Linux-desktop-only (it symlinks into CMAKE_INSTALL_FULL_DATAROOTDIR/openxray). Guard the entire `else` block (836-894) with `#if !defined(XR_PLATFORM_APPLE_IOS)`.
3. Compute the writable root once: `char* pref = SDL_GetPrefPath("OpenXRay", "STALKER");` (iOS maps this into ~/Library/Application Support/ inside the sandbox). Store it (e.g. a member or Core field) for redirecting write aliases.
4. Redirect writable aliases to the prefpath after fsgame.ltx is parsed. Reuse the existing -overlaypath mechanism (LocatorAPI.cpp:1041-1055): on iOS, after the ltx loop (right after R_ASSERT(path_exist("$app_data_root$")) at line 1031), call `get_path("$app_data_root$")->_set_root(pref)`, same for `$logs$`, `$screenshots$`, `$game_saves$`, `$downloads$`, then `rescan_path(...)` for the recursive ones. This keeps gamedata reads in the bundle while user writes land in the sandbox. Alternatively ship a dedicated iOS fsgame.ltx whose write aliases use a new `$app_data_root$` root — but the programmatic _set_root avoids a second config to maintain.
5. Ensure the app_data dirs exist under prefpath: SDL_GetPrefPath creates the base; create logs/, savedgames/, screenshots/ subdirs with _mkdir (PlatformApple.inl maps _mkdir->mkdir(S_IRWXU)) — but only under the prefpath, never under the bundle.
6. Handle flScanAppRoot / CMAKE_INSTALL_FULL_DATAROOTDIR: xrCore.cpp:306-309 sets flScanAppRoot when ApplicationPath != CMAKE_INSTALL_FULL_DATAROOTDIR. On iOS this comparison is meaningless (no /usr/share); ensure flScanAppRoot scanning the bundle root is harmless or guard it. CMAKE_INSTALL_FULL_DATAROOTDIR must still be defined for the iOS build (it's referenced at LocatorAPI.cpp:854 too, now guarded out).
7. Case sensitivity: confirm PlatformApple.inl keeps `xr_fs_strlwr` as do_nothing so lookups are case-preserving; document that packaged gamedata directory/file case must match config references exactly (e.g. gamedata/configs/system.ltx). This is enforced at packaging (Task 3.9), not code.

**Files**

- `src/xrCore/xrCore.cpp:202-235 (ApplicationPath/WorkingPath on POSIX)`
- `src/xrCore/LocatorAPI.cpp:817-904 (setup_fs_path — $fs_root$, chdir/mkdir/symlink block 853-889)`
- `src/xrCore/LocatorAPI.cpp:940-1062 (_initialize; $app_data_root$ assert 1031; -overlaypath root override 1041-1055)`
- `res/fsgame.ltx:1-30 (path alias table; may ship an iOS variant)`
- `src/Common/PlatformApple.inl (xr_fs_strlwr no-op — case sensitivity)`

**Acceptance**

- [ ] On simulator, $fs_root$ resolves to …/xr_3da.app/ and reading $game_config$/system.ltx from the bundle succeeds (InitSettings at x_ray.cpp:126 does not CHECK_OR_EXIT-fail).
- [ ] Writes (log file, user.ltx cfg_save, a savegame, a screenshot) land under the SDL_GetPrefPath sandbox dir and succeed (no EACCES/EROFS).
- [ ] No chdir/mkdir/symlink into system dirs is attempted on iOS (verify via strace-equivalent / no errno spam).
- [ ] R_ASSERT(path_exist("$app_data_root$")) (LocatorAPI.cpp:1031) passes and $app_data_root$ points into the sandbox.
- [ ] A file whose on-disk case differs from its config reference is correctly NOT found (confirms case-sensitivity expectation), guiding packaging.

**Risks / easy to miss**

- The bundle path from SDL_GetBasePath has a trailing slash and is read-only; any code that tries to create/copy into $fs_root$ (e.g. flBuildCopy, copy_file_to_build LocatorAPI.cpp:1478-1486) will fail — ensure -build/-ebuild flags are off on iOS.
- SDL_GetPrefPath org/app must be stable across versions or user saves 'move'; pick and freeze the org/app strings now.
- CMAKE_INSTALL_FULL_DATAROOTDIR is injected as a compile define (used at LocatorAPI.cpp:854, xrCore.cpp:307); if undefined for iOS the build breaks — define it (even to a dummy) in the iOS CMake path.
- APFS case-sensitivity: STALKER assets historically have inconsistent case; a case-insensitive dev macOS may hide bugs that only appear on the case-sensitive device — always test on device or a case-sensitive volume.
- get_path returns nullptr if an alias is absent in the shipped fsgame.ltx (e.g. $downloads$ line is cut off in res/fsgame.ltx) — null-check before _set_root.
- _set_root + rescan_path must run BEFORE CreateLog (LocatorAPI.cpp:1060) or the log tries to open under the read-only bundle and fails silently.

### ⬜ 3.8 Replace the second-window software splash with a LaunchScreen storyboard

`status: todo` · `effort: low`

**Goal.** The startup splash spins up a SECOND SDL borderless always-on-top window and a splash thread blitting a bitmap (x_ray.cpp:444-501). iOS is single-window and shows its launch image via a LaunchScreen storyboard declared in Info.plist; the software splash must be compiled out on iOS and replaced by the storyboard.

**Steps**

1. Guard the ShowSplash call in the ctor (x_ray.cpp:230-234) with `#if !defined(XR_PLATFORM_APPLE_IOS)` so no second SDL window is created on iOS.
2. Guard HideSplash() in Run() (x_ray.cpp:369) likewise, or make ShowSplash/HideSplash bodies early-return on iOS (`if(...) return;`). Simplest: wrap the bodies of ShowSplash (444), SplashProc (472), HideSplash (487) with an iOS early return so m_window/m_surface/m_splash_thread stay null and the rest of the class is unaffected.
3. Author res/ios/LaunchScreen.storyboard: a minimal storyboard with a full-screen view, a centered UIImageView (the OpenXRay/STALKER splash art) and a background color. Base it on the imgui example storyboard already in the tree. Keep it static (no launch-time code).
4. Add a launch image asset (or reuse res/…/splash art) referenced by the storyboard; include it in the bundle Resources (Task 3.9 CMake).
5. Wire it in Info.plist via `UILaunchStoryboardName = LaunchScreen` (Task 3.9). iOS shows the storyboard automatically from process launch until the first frame is presented — bridging the gap the software splash used to cover during Core.Initialize/Engine.Initialize.
6. Ensure the splash thread (Threading::RunThread, x_ray.cpp:466) and Sleep(SPLASH_FRAMERATE) loop (483) never start on iOS (covered by the early return) — a background thread blitting to a nonexistent window would crash.

**Files**

- `src/xrEngine/x_ray.cpp:230-234 (ctor ShowSplash call)`
- `src/xrEngine/x_ray.cpp:369 (Run HideSplash call)`
- `src/xrEngine/x_ray.cpp:444-501 (ShowSplash/SplashProc/HideSplash bodies)`
- `src/xrEngine/x_ray.h:20-37 (splash members)`
- `NEW res/ios/LaunchScreen.storyboard (reference Externals/imgui/examples/example_apple_metal/iOS/LaunchScreen.storyboard as a template)`
- `NEW res/ios/Info.plist.in (UILaunchStoryboardName key — see Task 3.9)`

**Acceptance**

- [ ] On launch, iOS displays the LaunchScreen storyboard art immediately, then transitions to the engine's cleared frame once the first LoopFrame presents.
- [ ] No second SDL_Window and no splash thread are created on iOS (verify m_window == nullptr throughout).
- [ ] Desktop builds still show the software splash unchanged.
- [ ] The storyboard is present in the built .app bundle and referenced by Info.plist.

**Risks / easy to miss**

- If UILaunchStoryboardName is missing, iOS shows a black launch screen (works but ugly) — not fatal for Phase 3 but include the storyboard.
- Storyboard must be compiled (ibtool) by Xcode; when driving CMake via the Xcode generator this is automatic if the .storyboard is a bundle Resource — verify it's added as a RESOURCE, not a plain file.
- Leftover splash members (m_window/m_surface/m_splash_thread in x_ray.h) are fine unused, but don't reference them in iOS teardown paths.
- The launch image resolution should cover common device sizes; a single centered image on a solid background scales acceptably.

### ⬜ 3.9 Package xr_3da as a MACOSX_BUNDLE iOS app target with Info.plist, entitlements, embedded gamedata, and signing config

`status: todo` · `effort: high`

**Goal.** iOS requires the executable inside a .app bundle with an Info.plist (bundle id, exec name, launch storyboard, required capabilities, orientations) and, for sideload/JIT/debug, an entitlements file carrying get-task-allow. Read-only gamedata must be embedded in the bundle Resources so setup_fs_path (Task 3.7) can find it. This turns the linked binary into an installable/sideloadable app.

**Steps**

1. In src/xr_3da/CMakeLists.txt, on iOS make the target a bundle: `add_executable(xr_3da MACOSX_BUNDLE)` (guard with the iOS CMake variable). Follow the working pattern in misc/ios/smoketest/CMakeLists.txt:19-31.
2. Set target properties on iOS: MACOSX_BUNDLE_GUI_IDENTIFIER (e.g. io.github.openxray), MACOSX_BUNDLE_BUNDLE_NAME, MACOSX_BUNDLE_BUNDLE_VERSION, MACOSX_BUNDLE_SHORT_VERSION_STRING (from PROJECT_VERSION), MACOSX_BUNDLE_INFO_PLIST pointing at the configured res/ios/Info.plist.in, XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO / CODE_SIGNING_REQUIRED=NO (unsigned .ipa for SideStore, matching ios.yml intent), XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY (1,2 for iPhone+iPad), and the deployment target (15.0, matching ios.yml DEPLOYMENT_TARGET).
3. Author res/ios/Info.plist.in with: CFBundleExecutable=$(EXECUTABLE_NAME), CFBundleIdentifier, CFBundleName, CFBundleShortVersionString/CFBundleVersion (CMake-substituted), UILaunchStoryboardName=LaunchScreen, UIRequiredDeviceCapabilities=[arm64, metal] (metal because ANGLE-on-Metal is the default renderer), UISupportedInterfaceOrientations (landscape per Task 3.6 decision), UIStatusBarHidden=true, UIViewControllerBasedStatusBarAppearance=false, and (optional) UIFileSharingEnabled/LSSupportsOpeningDocumentsInPlace to let users drop gamedata into Documents.
4. Author res/ios/OpenXRay.entitlements with `<key>get-task-allow</key><true/>` (needed for SideStore debug attach and the optional LuaJIT full-JIT path in a later phase). Wire via XCODE_ATTRIBUTE_CODE_SIGN_ENTITLEMENTS = path (even though signing is off in CI, SideStore re-signs on-device and honors the embedded entitlements).
5. Embed gamedata + fsgame.ltx into the bundle Resources so $fs_root$ (bundle) contains gamedata/ (Task 3.7 reads it). Use MACOSX_PACKAGE_LOCATION=Resources on the res/gamedata files, or a post-build copy, or set_source_files_properties(... PROPERTIES MACOSX_PACKAGE_LOCATION Resources). Mirror res/CMakeLists.txt:3-5 semantics into the bundle. Add LaunchScreen.storyboard as a RESOURCE.
6. Link SDL2main on iOS here (or confirm Task 3.1 did): target_link_libraries(xr_3da PRIVATE SDL2::SDL2main) under the iOS guard, with whole-archive if needed.
7. Provide app icons (AppIcon set) — optional for Phase 3 but required for a clean install; can stub with a placeholder Asset Catalog or CFBundleIcons entries. Note as follow-up if skipped.
8. Define a CMake variable (e.g. set(XRAY_IOS TRUE) in the toolchain or a top-level check on PLATFORM/CMAKE_SYSTEM_NAME STREQUAL iOS) so all these `if(iOS)` guards and Task 3.1/3.5 CMake branches have something to test — CMake cannot read the C++ XR_PLATFORM_APPLE_IOS macro.

**Files**

- `src/xr_3da/CMakeLists.txt:1-43 (make target a MACOSX_BUNDLE on iOS; set properties; embed resources; link SDL2main)`
- `NEW res/ios/Info.plist.in (bundle metadata template)`
- `NEW res/ios/OpenXRay.entitlements (get-task-allow)`
- `res/ios/LaunchScreen.storyboard (from Task 3.8)`
- `misc/ios/smoketest/CMakeLists.txt:17-31 (reference pattern already in-repo for MACOSX_BUNDLE + code-signing attrs)`
- `res/CMakeLists.txt:1-6 (gamedata install pattern to mirror into the bundle)`

**Acceptance**

- [ ] CI (device job) produces xr_3da.app containing: the arm64 Mach-O executable, Info.plist with the correct keys, embedded LaunchScreen.storyboardc, an entitlements-derived get-task-allow (verify with `codesign -d --entitlements` after SideStore signs, or check the embedded .entitlements), and a gamedata/ tree under Resources.
- [ ] The .ipa (zip of Payload/xr_3da.app) is produced by the packaging step and installs via SideStore.
- [ ] `vtool -show-build`/`lipo -info` confirm arm64 iphoneos (device) and arm64 iphonesimulator (sim), mirroring the smoketest inspection step.
- [ ] On device, the app icon/launch screen appear and the app launches to a cleared frame.

**Risks / easy to miss**

- MACOSX_BUNDLE_INFO_PLIST + a manual CFBundle* property set can conflict; if using a custom Info.plist.in, don't also rely on the auto-generated one — pick the template.
- Embedding a full gamedata tree (can be GBs) bloats the .ipa; for Phase 3 embed only the minimal configs/shaders needed to reach the console, and plan on-device asset import (UIFileSharingEnabled) for the full set in Phase 4/5.
- get-task-allow with a distribution signature is invalid; it's only valid with development/sideload signing — fine for SideStore, but never ship this to TestFlight/App Store (out of scope anyway).
- TARGETED_DEVICE_FAMILY and UIRequiredDeviceCapabilities mismatches cause install-time rejection; metal capability excludes very old devices (acceptable — min iOS 15/arm64).
- CMake Xcode generator is required for proper bundle/plist/entitlement handling (ios.yml already uses -G Xcode); the Makefile/Ninja generators mishandle iOS bundles.
- If SDL2main is stripped (LTO) the bundle launches to a bare main and dies — same risk as Task 3.1; validate the symbol survives in the packaged binary.

### ⬜ 3.10 Extend ios.yml CI to configure and build the real xr_3da app (replace the smoketest)

`status: todo` · `effort: medium`

**Goal.** Phase 1's ios.yml builds only misc/ios/smoketest. Phase 3 needs CI to configure the top-level CMake with the leetal toolchain and build the actual xr_3da bundle for simulator (fast compile gate) and device (unsigned .ipa), since the Windows dev box can't build iOS at all. This is the only way to verify every Phase 3 change.

**Steps**

1. Change the Configure step (ios.yml:50-58) working-directory from misc/ios/smoketest to the repo root and point cmake -B build at the top-level CMakeLists, keeping -G Xcode, -DCMAKE_TOOLCHAIN_FILE=…/ios.toolchain.cmake, -DPLATFORM=${matrix.platform}, -DDEPLOYMENT_TARGET=15.0, -DENABLE_BITCODE=OFF, and add -DBUILD_SHARED_LIBS=OFF (static single-binary is required on iOS) plus any XRAY_* options (e.g. -DXRAY_USE_LUAJIT=ON interpreter baseline).
2. Ensure submodules are checked out (actions/checkout with submodules: recursive) — the engine needs LuaJIT/luabind/imgui/gli/sse2neon submodules (.gitmodules) which the smoketest didn't.
3. Update the Build step to `cmake --build build --config Release --target xr_3da` (and its deps).
4. Update Inspect/Package steps (ios.yml:64-93) to find xr_3da.app instead of ios_smoketest.app; keep the lipo/vtool inspection and the Payload-zip → .ipa packaging for the device job.
5. Keep the two-job matrix: SIMULATORARM64 (compile check, no ipa) and OS64 (device, package_ipa=true). The simulator job is the primary per-commit gate for Phase 3 correctness.
6. Bump timeout-minutes (currently 20) — a full engine build is far larger than the smoketest; expect 40-90 min; enable ccache/actions cache for the build dir and CMake deps to keep it tractable.
7. Gate: this task depends on Phase 2 (all deps cross-build and the engine links). Mark partial until Phase 2 lands; wire the workflow now but expect link failures until Phase 2 is complete.

**Files**

- `.github/workflows/ios.yml:41-94 (configure/build/package steps currently rooted at misc/ios/smoketest)`
- `CMakeLists.txt:1-27 (top-level; must configure cleanly under the ios toolchain)`
- `cmake/toolchains/ios.toolchain.cmake (vendored leetal toolchain from Phase 1)`

**Acceptance**

- [ ] ios.yml simulator job configures the top-level project with the ios toolchain and compiles xr_3da (or fails only on genuinely unported code, not on missing toolchain/config).
- [ ] Device job produces an unsigned OpenXRay .ipa artifact containing xr_3da.app.
- [ ] The workflow still runs only on the ios-port branch (push filter unchanged).
- [ ] Build completes within the (raised) timeout with caching.

**Risks / easy to miss**

- Full engine build time may blow past CI limits without caching; add actions/cache for the CMake build dir and consider a warm dep cache.
- The Xcode generator + LTO + static build can surface link-order issues (SDL2main, whole-archive) not seen in the smoketest.
- Submodule fetch is mandatory — the smoketest didn't need it, so checkout config must change or the build fails immediately.
- -DBUILD_SHARED_LIBS=OFF must propagate; if any subproject defaults to shared, dlopen won't exist on iOS and it breaks.
- Simulator and device may diverge (e.g. GLES/ANGLE availability); a green simulator job doesn't guarantee device — inspect both.

### ⬜ 3.11 Request a GLES 3.0 context on iOS so the engine presents a cleared frame (renderer seam with Phase 4)

`status: todo` · `effort: medium`

**Goal.** The Phase 3 done-criterion is 'reach engine init/console with a cleared frame'. That requires a live GL context. glHW.cpp currently asks SDL for desktop GL Core 4.1, which cannot be created on iOS. Flip the context request to GLES 3.0 (ES profile) on iOS — just enough for SDL_GL_CreateContext to succeed and glClear+SwapWindow to show a cleared backbuffer. All shader/texture/scene porting stays in Phase 4.

**Steps**

1. In ObtainRequiredWindowFlags (glHW.cpp:181-197), add an iOS branch: keep `windowFlags |= SDL_WINDOW_OPENGL` (line 181), keep RGBA8/depth24/stencil8/doublebuffer attributes (185-192), but set `SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_ES)` and MAJOR=3, MINOR=0 instead of CORE / 4.1 (183, 196-197). This selects GLES 3.0.
2. Decide EAGL (native GLES) vs ANGLE-on-Metal for Phase 3 bring-up: the fastest path to a cleared frame is native EAGL GLES3 (SDL's iOS GL backend), which needs no extra deps. ANGLE-on-Metal (the project default renderer) is heavier to wire and belongs to Phase 4; for Phase 3 the cleared-frame proof can use native EAGL and be swapped to ANGLE in Phase 4. Document the choice; guard so ANGLE can slot in later.
3. Verify glad/GL loader: gladLoadGL (glHW.cpp:110) loads desktop GL symbols; for GLES you need a GLES loader (gladLoadGLES2 or SDL_GL_GetProcAddress against a GLES header). For a mere glClear this can be minimal — ensure at least glClearColor/glClear/SDL_GL_SwapWindow resolve. Full GLES symbol coverage is Phase 4.
4. Add a minimal clear path: ensure Device.Create()→GEnv.Render->Create (Device_create.cpp:41) can complete without loading shaders.xr (Device_create.cpp:47-48 calls OnDeviceCreate(shaders.xr)). For Phase 3, guard/stub the shader-dependent creation on iOS so RenderBegin/Clear/RenderEnd (Device.cpp:34-108) can run a glClear and SDL_GL_SwapWindow, presenting the cleared backbuffer. Keep this stub explicitly Phase-4-TODO.
5. vsync: SDL_GL_SetSwapInterval(-1) then 1 (glHW.cpp:28-29) — on iOS adaptive vsync (-1) is unsupported; ensure the fallback to 1 works, and note it composes with CADisplayLink (don't double-throttle).
6. Confirm SwapWindow (glHW.cpp:250) is called each frame from RenderEnd so the cleared frame actually reaches the screen.

**Files**

- `src/Layers/xrRenderGL/glHW.cpp:181-197 (ObtainRequiredWindowFlags: SDL_WINDOW_OPENGL + GL attribute selection, currently CORE 4.1)`
- `src/Layers/xrRenderGL/glHW.cpp:88-115 (SDL_GL_CreateContext + gladLoad)`
- `src/Layers/xrRenderGL/glHW.cpp:20-33 (SwapInterval / vsync)`
- `src/Layers/xrRenderGL/glHW.cpp:203-250 (MakeCurrent / SwapWindow)`

**Acceptance**

- [ ] SDL_GL_CreateContext succeeds on simulator/device with a GLES 3.0 context (log the GL_VERSION string — should report 'OpenGL ES 3.0' or ANGLE equivalent).
- [ ] The app presents a solid cleared color filling the fullscreen window (not black-by-default) — proving context, swap, and the run loop all work end to end.
- [ ] No desktop-GL-only enum/entrypoint is required just to clear (any that are get stubbed/guarded with a Phase-4 TODO).
- [ ] Desktop GL builds still request Core 4.1 unchanged.

**Risks / easy to miss**

- This is the single deliberate overlap with Phase 4; scope-creep risk is high. Hold the line: context + glClear + swap only. Any shader/FBO/texture work is Phase 4.
- SDL iOS GL backend may require the window to be created with the GL flag BEFORE context creation (order matters) — ObtainRequiredWindowFlags runs during Device_Initialize (Device_Initialize.cpp:51) which is correct, but verify on iOS.
- glad desktop-GL loader will fail to resolve many symbols under GLES; if the renderer's Create() blindly calls desktop-only entrypoints it will crash before the first clear — the stub in step 4 is essential.
- ANGLE-on-Metal (project default) needs libEGL/libGLESv2 from ANGLE embedded and SDL_HINT_VIDEO_EGL / SDL egl driver wiring; deferring to native EAGL for Phase 3 avoids that, but ensure the code path is switchable so Phase 4 can adopt ANGLE without another refactor.
- Depth24/stencil8 combo may not be a valid GLES renderbuffer format on all iOS GPUs; if context creation fails, fall back to DEPTH24+STENCIL8 packed or DEPTH16.
- If OnDeviceCreate(shaders.xr) is stubbed too aggressively, the console (which needs a font/shader to draw text) won't render — 'reach console' may mean 'console logic initialized' rather than 'console text drawn'; clarify the acceptance with the team (cleared frame is the hard requirement; visible console text may slip to Phase 4).

### Phase 3 cross-cutting notes

- GATING MACRO DISCIPLINE: use XR_PLATFORM_APPLE_IOS for all new iOS code (defined in src/Common/Platform.hpp:33 via TargetConditionals TARGET_OS_IOS). CRITICAL PITFALL: iOS ALSO satisfies XR_PLATFORM_APPLE (Platform.hpp:29), XR_PLATFORM_POSIX (Platform.hpp:30) and includes PlatformApple.inl, so every existing `#elif defined(XR_PLATFORM_POSIX)` / `#ifdef XR_PLATFORM_APPLE` branch (e.g. LocatorAPI.cpp:169,330,494,726; xrCore.cpp:202-262) is currently a Linux/macOS path that iOS will silently fall into. Audit and add an earlier `#if defined(XR_PLATFORM_APPLE_IOS)` branch before those, or guard the Linux-only bits (chdir/mkdir/symlink/getcwd/getpwuid) out on iOS.
- MAIN THREAD == UI THREAD == RENDER THREAD on iOS. Never Sleep()/block it (PlatformApple.inl maps Sleep->usleep). Blocking it stalls CADisplayLink, touch delivery and the watchdog. Absolutely NO OpenGL/EAGL/Metal calls after SDL_APP_DIDENTERBACKGROUND is delivered — iOS kills the app (0x8BADF00D) if you draw while backgrounded. This is why lifecycle uses SDL_AddEventWatch (synchronous, fired inside SDL_PumpEvents before the event is queued) rather than the deferred SDL_PeepEvents queue that x_ray.cpp:379 currently drains.
- SDL2main is MANDATORY on iOS: libSDL2main provides the real main() that calls SDL_UIKitRunApp(argc,argv,SDL_main) → UIApplicationMain; the engine's entry becomes SDL_main only if <SDL_main.h> is included (it does `#define main SDL_main` when SDL_MAIN_NEEDED). On Linux/macOS SDL_main is a no-op, so the include must be iOS-guarded or verified harmless. Currently NOTHING links SDL2main (src/xrEngine/CMakeLists.txt:434 links only SDL2::SDL2).
- When SDL_main returns on iOS with an animation callback registered, SDL keeps the process alive under UIApplicationMain — so CApplication::Run() must NOT loop on iOS; it registers the callback and returns. Corollary: the tick must call SDL_PumpEvents() itself, because the removed `while(!SDL_QuitRequested())` (x_ray.cpp:372) was what pumped the SDL event queue each iteration.
- iOS filesystem is CASE-SENSITIVE (unlike default macOS APFS). PlatformApple.inl already defines `xr_fs_strlwr` as a no-op (do_nothing), so LocatorAPI does not lowercase lookups on Apple — therefore the on-disk case of packaged gamedata must EXACTLY match the case used in configs/scripts, or files won't be found. This is a packaging/asset concern, not a code fix, but it will bite silently.
- Build/verify loop: NO local iOS build possible (Windows dev box). Every change is compiled on the macOS CI runner via .github/workflows/ios.yml. Keep the simulator (SIMULATORARM64) job as the fast compile-sanity gate; the device (OS64) job produces the unsigned .ipa for SideStore. TARGET_OS_SIMULATOR yields the distinct "iOS Simulator" marker (Platform.hpp:34-38).
- Set the get-task-allow entitlement now (Task 3.9). It is required for SideStore sideload debugging and is the gate for the optional LuaJIT full-JIT path in a later phase; harmless to include for interpreter-only baseline.
- The 'cleared frame' deliverable requires a working GL context. glHW.cpp requests desktop GL Core 4.1 (glHW.cpp:183,196-197) which will FAIL on iOS. Task 3.11 flips that to a GLES 3.0 ES-profile request just far enough to glClear+SwapWindow. This is the single seam shared with Phase 4 — keep Phase 3 to context-creates-and-clears; defer the 286 shaders / DXT->ASTC / scene render to Phase 4.
- Do NOT create a second SDL window anywhere on iOS. The software splash (x_ray.cpp:444-501) and any ImGui secondary viewports assume multi-window; iOS is single-window. Splash becomes a LaunchScreen storyboard (Task 3.8); IMGUI_ENABLE_VIEWPORTS multi-window paths (Device_Initialize.cpp:84-103) should stay disabled on iOS.

---

<a id="phase-4"></a>

## Phase 4 — GLES 3.0 / ANGLE-on-Metal renderer + shaders + textures

> **Phase goal.** Port the desktop OpenGL 4.1 Core renderer (library xrRender_GL, USE_OGL, RENDER_NAMESPACE=render_gl) and its ~286 runtime GLSL-410 shaders under res/gamedata/shaders/gl/ to OpenGL ES 3.0 (GLSL ES 3.00) so the engine renders on iOS arm64. Default backend is ANGLE-on-Metal (EGL/libGLESv2), with native EAGL GLES3 (SDL's built-in iOS GL driver) as the pragmatic first-pixels path and runtime fallback. The dominant subsystem change is centralizing the shader front-end (version/precision emission in rgl_shaders.cpp, the shared shims common.h/common_samplers.h, and regenerating the 81 iostructs/ headers to drop separable-program varyings) so we do NOT hand-edit hundreds of shader files, plus a DXT/BC->ASTC texture path (Apple GPUs have no S3TC). Milestones: main menu renders first, then a test level. Every change must remain dual-target — desktop GL 4.1 (Windows/Linux/macOS) must still compile and run — gated by XR_PLATFORM_APPLE_IOS and/or runtime GLAD_GL_ES_VERSION_3_0 booleans.

### ⬜ 4.1 Wire xrRender_GL into the iOS build (CMake target, USE_OGL, GLES loader, ANGLE libs)

`status: todo` · `effort: high`

**Goal.** Get the GL renderer library and its dependencies compiling and linking into the single static iOS app binary, with the GLES3 function loader and (for the default path) ANGLE's libEGL/libGLESv2 available. Nothing renders yet; this is the plumbing that later tasks build on.

**Steps**

1. Confirm the top-level CMake includes the xrRenderPC_GL subdirectory for Apple/iOS (currently non-Windows registers renderer_r3 using this same lib). Add xrRender_GL to the iOS link set; ensure BUILD_SHARED_LIBS=OFF (PREFIX '' set_target_properties stays, but it links statically into the app).
2. Verify xrRender_GL's link deps (xrAPI, xrCDB, xrCore, xrEngine, xrMaterialSystem, xrParticles, xrScriptEngine, xrImGui) all build for iphoneos/simulator arm64 (Phase 2 dependency).
3. Decide GLES driver acquisition: (a) DEFAULT ANGLE — add prebuilt ANGLE static/dynamic libEGL + libGLESv2 for iOS arm64 (device + simulator slices) under misc/ios/angle/; for a sideloaded single-binary app prefer static .a or embed the .frameworks in the bundle (dylibs are allowed with get-task-allow but complicate signing). (b) FALLBACK native EAGL — no external lib; SDL's UIKit driver provides GLES3 via EAGLContext.
4. Add a CMake option XR_IOS_GLES_BACKEND = ANGLE|EAGL (default ANGLE) that selects link libs and a compile define (XR_IOS_USE_ANGLE) consumed by glHW.cpp.
5. Change the runtime loader: in CHW::CreateDevice (glHW.cpp:107-118) gladLoadGL(...) loads DESKTOP entrypoints. Under XR_PLATFORM_APPLE_IOS call gladLoadGLES2(reinterpret_cast<GLADloadfunc>(SDL_GL_GetProcAddress)) instead, so ANGLE/EAGL GLES entrypoints resolve. Keep gladLoadGL for desktop.
6. Ensure SDL is built with GLES support for iOS (SDL_VIDEO_OPENGL_ES2/ES3, SDL_VIDEO_RENDER_OGL_ES enabled). For ANGLE, SDL must be able to create an EGL context — see task 4.2 for the SDL/ANGLE nuance; for EAGL, SDL's uikitopengl driver works unmodified.
7. Confirm gli include dirs (Externals/gli, Externals/gli/external) resolve under the iOS toolchain; gli is header-only so no separate link.
8. Build target for SIMULATORARM64 and OS64 on macOS CI; success = xrRender_GL.a compiles and the app links (it need not run correctly yet).

**Files**

- `src/Layers/xrRenderPC_GL/CMakeLists.txt (defines xrRender_GL, USE_OGL, RENDER_NAMESPACE=render_gl; pulls sdk/include/glad/gl.c at :386)`
- `CMakeLists.txt (root; renderer/library selection, BUILD_SHARED_LIBS=OFF for iOS)`
- `cmake/toolchains/ios.toolchain.cmake (leetal, already vendored)`
- `sdk/include/glad/gl.h + gl.c (merged GL4.6/GLES loader, has gladLoadGLES2)`
- `Externals/gli (header-only, used by glTexture.cpp)`
- `misc/ios/ (add ANGLE prebuilt libs + app bundle wiring here)`

**Acceptance**

- [ ] xrRender_GL compiles for iphoneos and iphonesimulator arm64 with USE_OGL and no desktop-only-symbol link errors.
- [ ] gladLoadGLES2 is called on iOS; the app links against ANGLE (default) or resolves EAGL GLES3 via SDL (fallback) with no unresolved GL symbols.
- [ ] ios.yml (or its Phase-4 successor) builds the full app, not just the smoketest, through the link step.
- [ ] CMake option XR_IOS_GLES_BACKEND switches backends without source edits.

**Risks / easy to miss**

- ANGLE on iOS is EGL-based; SDL's iOS video driver is UIKit/EAGL and does NOT natively route through EGL — naive linking of ANGLE will not make SDL_GL_CreateContext use it. This is resolved in 4.2; do not assume 'link ANGLE => done'.
- gladLoadGL vs gladLoadGLES2 load different function-pointer sets from the SAME global tables; calling the desktop loader on an ES context silently leaves ES-only pointers null. Must switch loaders per-platform.
- Static-linking ANGLE (libGLESv2/libEGL) may pull in heavy Metal/spirv translation deps; prefer the vendored Metal backend build. Simulator vs device slices must both be present (lipo/xcframework).
- Sideload signing: embedded dylibs/frameworks need to be signed & bundled; a static ANGLE avoids per-dylib signing but ANGLE upstream primarily ships dynamic. Confirm what SideStore accepts.

### ⬜ 4.2 Create a GLES 3.0 context: SetPrimaryAttributes ES profile + ANGLE/EAGL selection + fallback

`status: todo` · `effort: high`

**Goal.** Make CHW request and obtain an OpenGL ES 3.0 context on iOS (ANGLE-on-Metal by default, native EAGL GLES3 as fallback), replacing the hard-coded GL Core 4.1 request, and adapt debug-output usage that ES lacks.

**Steps**

1. In SetPrimaryAttributes, under XR_PLATFORM_APPLE_IOS set SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_ES) and CONTEXT_MAJOR_VERSION=3, CONTEXT_MINOR_VERSION=0 (instead of CORE / 4 / 1 at lines 183,196-197). Keep the RGBA8/depth24/stencil8/doublebuffer attribs (185-192) — all valid on ES3.
2. ANGLE selection: for the default backend, ANGLE's EGL must back the SDL context. Practical options, pick one and implement: (A) Use SDL_HINT_VIDEO_X11_FORCE_EGL-style path is N/A on iOS; instead build/patch SDL so its uikit GL path can create an ANGLE EGLContext on a CAMetalLayer-backed UIView (SDL 2.28+ has some ANGLE plumbing on other platforms). (B) Simpler: keep SDL only for windowing/input and create the GL context yourself via ANGLE EGL (eglGetDisplay on the UIView layer, eglCreateContext ES3, eglMakeCurrent), bypassing SDL_GL_CreateContext — then MakeContextCurrent/Present call egl* instead of SDL_GL_*. Abstract behind CHW so desktop keeps SDL_GL_*.
3. EAGL fallback: if XR_IOS_GLES_BACKEND==EAGL (or ANGLE init fails at runtime), use the plain SDL path (SDL creates an EAGLContext with kEAGLRenderingAPIOpenGLES3). This requires NO egl code — SDL_GL_CreateContext just works with the ES3 attributes above. Implement a runtime try-ANGLE-then-EAGL fallback so the app never hard-fails to get a context.
4. Present() (glHW.cpp:237-252): the default-FBO blit + SDL_GL_SwapWindow works for EAGL; for the self-managed ANGLE path, swap via eglSwapBuffers. Note EAGL has no real default framebuffer 0 — the presentable is a renderbuffer FBO SDL manages; the existing pFB blit-to-0 in Present may need the SDL-provided framebuffer id (SDL_GL_GetDrawableSize / the uikit window framebuffer). Verify glBindFramebuffer(...,0) actually targets the presentable on iOS.
5. Debug output (glHW.cpp:124-130): glDebugMessageCallback / GL_DEBUG_OUTPUT is desktop KHR_debug; on ES it is KHR_debug extension (glDebugMessageCallbackKHR) and may be absent under ANGLE. Guard with a runtime check (if (glDebugMessageCallback) already exists — keep it, it no-ops when null) and only enable under DEBUG.
6. Adjust r2_test_hw.cpp: the hidden 1x1 probe window must also request ES3; on iOS a hidden GL window may be invalid — consider skipping the probe on iOS and returning TRUE (the module already force-selects renderer on non-Windows), or make the probe use the real window.
7. Log the obtained GL_VERSION/GL_RENDERER/GL_SHADING_LANGUAGE_VERSION (already done at glHW.cpp:137-143) to confirm 'OpenGL ES 3.0 (ANGLE ... Metal ...)' at runtime.

**Files**

- `src/Layers/xrRenderGL/glHW.cpp (SetPrimaryAttributes :179-199 sets PROFILE_CORE + major 4 minor 1 at :183,196-197; CreateDevice :75-150; debug callback :124-130; Present :237-252)`
- `src/Layers/xrRenderGL/glHW.h (CHW decl, m_context/m_window)`
- `src/Layers/xrRenderPC_GL/r2_test_hw.cpp (sdl_window_test_helper uses SetPrimaryAttributes for HW probe)`

**Acceptance**

- [ ] On device/simulator the log shows an OpenGL ES 3.0 context (renderer string shows ANGLE Metal for the default path, or Apple GPU for EAGL).
- [ ] glGetError is clean immediately after context creation and gladLoadGLES2 populates ES3 entrypoints (e.g. glGenFramebuffers non-null).
- [ ] If ANGLE fails, the app automatically falls back to EAGL GLES3 and still gets a context (no crash).
- [ ] A cleared color frame is presented to the screen (ties into Phase 3 boot-to-window).

**Risks / easy to miss**

- The SDL-vs-ANGLE context ownership decision is the crux: bypassing SDL_GL_CreateContext means CHW::MakeContextCurrent/GetCurrentContext/Present must be re-plumbed to egl*/CAMetalLayer for iOS while staying SDL_GL_* on desktop — get the abstraction boundary right or every call site breaks.
- EAGL on iOS has no window-system default framebuffer; code that binds FBO 0 as 'the screen' (Present, phase_flip) may render to nothing. SDL exposes the on-screen FBO differently — must fetch it.
- Requesting ES3 but the driver granting ES2 (older ANGLE/sim) silently breaks GLSL ES 3.00 shaders — assert major>=3 after creation.
- EAGL is deprecated by Apple (may warn/removed on future iOS); that is exactly why ANGLE is the default — keep EAGL strictly as fallback.

### ⬜ 4.3 Central shader front-end: emit '#version 300 es' + precision, force the monolithic non-separable path

`status: todo` · `effort: medium`

**Goal.** Change the single place that assembles every shader's source so ES builds get GLSL ES 3.00 with mandatory precision qualifiers instead of '#version 410' + ARB_separate_shader_objects, and force the engine down the already-present monolithic (non-separable) link path. This is the highest-leverage change — it reconfigures all ~286 shaders without touching them individually.

**Steps**

1. In CRender::shader_compile, replace the two options.add lines (227-228) with a platform switch: on ES emit options.add("#version 300 es"); then a precision preamble block, e.g. options.add("precision highp float;"), options.add("precision highp int;"), plus sampler precisions ('precision highp sampler2D; precision highp sampler3D; precision highp samplerCube; precision lowp sampler2DShadow;' as needed) and 'precision highp int;'. On desktop keep '#version 410' + the ARB_separate_shader_objects extension. Note: the '#version' MUST be the first non-comment token, and options[] entries are concatenated in order — ensure the version line is emitted at index 0 before the '// name' comment (c_name at 238-239).
2. Precision must differ per stage: fragment shaders REQUIRE an explicit default float precision (highp is safest for the deferred math; mediump risks banding in G-buffer/lighting). Vertex shaders default to highp but declaring it is harmless. Since shader_compile does not know stage here directly (it is passed pTarget), branch on pTarget[0]=='p' vs 'v' to tune precision if needed; simplest correct default: highp float + highp int for all stages on ES.
3. Force monolithic linking: ensure GLAD_GL_ARB_separate_shader_objects is FALSE on iOS so create_shader returns raw shaders ({'s',shader}) and _LinkPP (glResourceManager_Resources.cpp:104-123) takes the GLLinkMonolithicProgram branch (:114). ANGLE GLES3 does not expose ARB_separate_shader_objects (only EXT_separate_shader_objects with limitations), so GLAD should already report it false — but assert/force it: under XR_PLATFORM_APPLE_IOS treat the flag as false everywhere it gates behavior.
4. Because monolithic linking nulls pass.ps/vs/gs after link (glResourceManager_Resources.cpp:117-119) and re-parses constants against the whole program (RC_dest_all :115), verify the constant-table parse path (result->constants.parse) works with a linked program handle, not a separable program — it already branches on GetShaderDest per stage; confirm RC_dest_all path.
5. Keep the debug '#pragma optimize' lines (230-236) — they are ignored by GLSL ES compilers (harmless) but confirm ANGLE does not error on unknown pragmas; if it does, drop them on ES.
6. Relax the shader-cache gating (see task 4.8) so it does not require ARB_get_program_binary+separable; for initial bring-up, on ES skip the binary cache entirely (always compile) to avoid the separable-only code at 496-507/557-583.
7. Leave SM_4_1 disabled (already #ifndef XR_PLATFORM_APPLE at 415-420) and do not enable USE_MINMAX_SM/MSAA.

**Files**

- `src/Layers/xrRenderPC_GL/rgl_shaders.cpp (options.add("#version 410") :227 and "#extension GL_ARB_separate_shader_objects : enable" :228; SM_4_1 apple guard :415-420; shader-cache gating :496-510,557; sh_name/options builders)`
- `src/Layers/xrRenderGL/glHW.cpp (where GLAD_GL_ARB_separate_shader_objects effectively becomes false on ES — verify GLAD sets it false under ANGLE; if not, force it off on iOS)`
- `src/Layers/xrRender/ShaderResourceTraits.h (GLCompileShader branch on !GLAD_GL_ARB_separate_shader_objects :61-62 returns raw shader)`

**Acceptance**

- [ ] A dumped/logged shader source for any .vs/.ps on iOS begins with '#version 300 es' followed by precision defaults, then the #define option block, then the shader body.
- [ ] Fragment shaders compile without the ES error 'No precision specified for (float)'.
- [ ] At runtime GLAD_GL_ARB_separate_shader_objects is false on iOS and _LinkPP takes the GLLinkMonolithicProgram branch (verified by log/break).
- [ ] Desktop GL build is byte-for-byte unchanged in the shader preamble (still #version 410 + separable).

**Risks / easy to miss**

- '#version 300 es' MUST be the literal first line — any of the option-array entries emitted before it (the sh_name comment, defines) will make the ES compiler reject the whole shader. Audit the m_sources assembly order in shader_sources_manager::apply_options (rgl_shaders.cpp:183-195) which prepends options[] before the file body.
- highp is only guaranteed in fragment shaders on ES3.0 hardware that reports GL_FRAGMENT_PRECISION_HIGH (all real iOS GPUs do, ANGLE-Metal does); still, declaring highp sampler defaults can trip older ANGLE — validate.
- Monolithic linking matches varyings by name — this is INERT until task 4.5 renames varyings; expect link-time 'varying v2p_* not declared in fragment shader' errors between 4.3 and 4.5. Land 4.4/4.5 together with 4.3.
- #extension GL_ARB_separate_shader_objects on ES is invalid; ensure it is not emitted on iOS.

### ⬜ 4.4 Rewrite the shared shims common.h and common_samplers.h for GLSL ES 3.00

`status: todo` · `effort: medium`

**Goal.** Fix the two shared headers that every shader #includes so their HLSL->GLSL compatibility macros, sampler declarations, texture swizzles and built-in redeclarations are valid under GLSL ES 3.00 (no separable-program constructs, ES-legal sampler precision, no desktop-only intrinsics).

**Steps**

1. common_samplers.h: the macros 'Texture2D = uniform sampler2D' etc (:4-8) are fine on ES, but each combined sampler on ES may need a precision qualifier. Rather than per-declaration, rely on the global 'precision highp sampler2D;' emitted in 4.3; verify Texture2DMS (sampler2DMS, :6) only appears under USE_MSAA (it does, :19-23/50-56/64-70) so it never compiles on ES3.0 (MSAA off) — keep guarded.
2. common_samplers.h: Texture2DShadow -> sampler2DShadow (:8) is ES3.0-core; ensure the shadow sampler uses lowp/highp per ES rules and that comparison samplers get correct precision (declare 'precision highp sampler2DShadow;' if the global default is insufficient).
3. common.h: the HLSL-compat function overloads mul()/sincos() (:29-39) are plain GLSL and legal on ES3.00 (function overloading is allowed). Verify no implicit int->float that ES rejects (e.g. mul(int a, vec4 b) at :29 casts explicitly — good).
4. common.h: tex2Dlod/tex2Dproj/tex3D/texCUBE macros (:45-49) map to texture/textureLod/textureProj — all ES3.00 core. asuint/asfloat -> floatBitsToUint/uintBitsToFloat (:50-51) are ES3.00 core. saturate/clip/mask (:43-52) are fine. Confirm 'clip(x): if (x<0) discard' compiles (discard is ES-legal).
5. common.h semantic #defines (COLOR..TEXCOORD7 = 0..15, :55-76): these become the layout(location=N) values on VS INPUT attributes (legal on ES) and, in today's iostructs, on varyings (ILLEGAL on ES). After task 4.5 removes varying layouts, these #defines are used only for VS attribute inputs and PS fragment outputs — keep them; they must stay consistent with VertexUsageList in glBufferUtils.cpp (POSITION=3, NORMAL=5, TANGENT=4, BINORMAL=6, COLOR=0, TEXCOORD0=8...).
6. common.h loose uniforms (m_WVP, m_V, m_P, timers, fog_*, L_* etc, :82-122): plain uniforms are ES3.00-legal; keep as-is (do NOT convert to UBOs this phase). Ensure matrix types (float4x4->mat4, float3x4->mat4x3 via :22-27) are ES-legal — mat4x3 is ES3.00 core.
7. Add an ES-only shim if any intrinsic used by common_functions.h is desktop-only (e.g. textureGatherOffset in gather/fxaa/ssao_hdao) — confirm those .ps are only reachable under SM_4_1/HDAO/minmax which stay OFF; if any is unconditionally included, wrap in #ifdef guards.
8. Because both files use include-guards (SHARED_COMMON_H, common_samplers_h_included) and are pulled via the shader_sources_manager recursive #include loader (rgl_shaders.cpp:141-181), no build-system change is needed — edits take effect at runtime shader compile.

**Files**

- `res/gamedata/shaders/gl/shared/common.h (macros/uniforms :1-142; mul() overloads :29-38; tex2D/tex2Dlod :45-47; semantic location #defines COLOR..TEXCOORD7 :55-76)`
- `res/gamedata/shaders/gl/common_samplers.h (Texture2D/3D/MS/Cube/Shadow macros :4-8; sampler decls :18-74)`
- `res/gamedata/shaders/gl/common_iostructs.h (:1-4 includes shared/common.h)`
- `res/gamedata/shaders/gl/common.h and common_functions.h (top-level includers — verify no ES-illegal constructs like textureGatherOffset outside disabled paths)`

**Acceptance**

- [ ] A trivial ES shader that only #includes shared/common.h + common_samplers.h compiles clean under ANGLE GLES3 (no 'unsupported qualifier', 'no precision', or 'sampler as function argument' errors).
- [ ] No layout(location) remains on anything that becomes a varying after 4.5; VS-input and frag-output locations still resolve to the common.h semantic values.
- [ ] Desktop GL still compiles these headers (the ES-specific changes are additive/guarded).

**Risks / easy to miss**

- Sampler precision on ES: mismatched precision between a sampler declared here and its use in a shader body can cause 'precision mismatch' link errors on some ANGLE versions — standardize on highp for color samplers, and be deliberate about shadow samplers.
- These shims are shared by BOTH desktop and iOS at runtime (same asset tree). Any ES-only syntax added unconditionally will break the desktop GL 4.1 compile. Use the #version already-selected context: ES-illegal-on-desktop constructs are rare, but do not add 'precision' lines here (they belong in the central preamble 4.3, and 'precision' is invalid in desktop GLSL 410).
- gli texture swizzle interacts with the .r-swizzle convention noted in common_samplers/glTextureUtils (L8->GL_R8 with shader .r swizzle) — keep swizzle semantics consistent with task 4.9.

### ⬜ 4.5 Regenerate the 81 iostructs/ headers: name-matched varyings, drop gl_PerVertex, layout(location) fragment outputs

`status: todo` · `effort: very-high`

**Goal.** Rewrite the per-shader I/O glue headers (res/gamedata/shaders/gl/iostructs/*.h) so vertex->fragment communication is GLSL-ES-3.00-legal: remove the separable-program 'out gl_PerVertex' redeclaration, remove layout(location=...) from all varyings, rename VS-output varyings to MATCH the PS-input varying names (ES links varyings by name), keep layout(location) only on VS attribute inputs, and add layout(location=N) to fragment SV_Target outputs (replacing glBindFragDataLocation). This is the single largest content change of the phase.

**Steps**

1. Establish the transformation rules (apply uniformly, ideally via a script since 81 files share structure): RULE-1 delete every 'out gl_PerVertex { vec4 gl_Position; };' line (present in all VS iostructs, e.g. v_static_flat.h:2, v_dumb.h:2) — on ES gl_Position is implicitly declared and redeclaration is illegal. RULE-2 On VS attribute INPUTS keep 'layout(location = <SEMANTIC>) in ...' (legal on ES; <SEMANTIC> from common.h). RULE-3 On the VS->PS varyings, delete the 'layout(location = TEXCOORDn)' qualifier from BOTH the VS 'out' and PS 'in' declarations. RULE-4 Rename varyings so VS-out identifier == PS-in identifier. Today VS emits v2p_<name> (e.g. v2p_flat_tcdh, v_static_flat.h:23) and PS reads p_<name> (p_flat_tcdh, p_flat.h:15) — unify to a single agreed name per interpolant (e.g. keep the VS 'v2p_flat_*' names and rename PS inputs to match, or introduce neutral names). Consistency across the matched .vs/.ps pair is what matters. RULE-5 On PS fragment OUTPUTS add explicit 'layout(location = 0) out vec4 SV_Target0;', location=1 for SV_Target1, location=2 for SV_Target2 (p_flat.h:2-6 and every deferred PS) — this replaces glBindFragDataLocation (task 4.6).
2. Map the varying locations that were carried by layout(location=TEXCOORDn) into declaration ORDER + name matching. Since ES links by name, exact locations no longer matter, but the VS and PS must declare the SAME set of varyings under the SAME #ifdef conditions (USE_R2_STATIC_SUN, USE_LM_HEMI, USE_TDETAIL, GBUFFER_OPTIMIZATION, etc). Verify each conditional block in the VS iostruct has a mirror in the PS iostruct so no unmatched varying survives (mismatch => link error or silent garbage).
3. Handle gl_FragCoord/gl_SampleMask: p_flat.h:8-12 uses 'in vec4 gl_FragCoord' (guarded by MSAA_ALPHATEST_DX10_1_ATOC) and 'out int gl_SampleMask[]' (guarded by EXTEND_F_DEFFER). Both are MSAA paths kept OFF; ensure the guards remain so they never compile on ES3.0. Do NOT redeclare gl_FragCoord on ES (it is built-in) — drop that redeclaration or keep it strictly under the disabled MSAA macro.
4. f_deffer struct (common_iostructs.h:303-322) maps to SV_Target0/1/2 (or 0/1 under GBUFFER_OPTIMIZATION). Confirm the PS main() writes SV_Target0..N (p_flat.h:53-58) and the layout(location=N) outs added in RULE-5 correspond exactly to MRT attachment order used by the G-buffer FBO (task 4.10) and glDrawBuffers.
5. Write a generator/transform script (python) that parses each iostructs/*.h and applies RULE-1..5 deterministically, since the 81 files are mechanically similar; hand-fix the handful of irregular ones (accum/volumetric, sky, clouds, particle). Regenerate rather than hand-edit 81 files to avoid drift. Keep the desktop path working: since desktop uses separable programs, name-matching is harmless there, BUT removing layout(location) from varyings could break desktop separable linking (which relies on locations). Guard varying declarations: emit layout(location=...) ONLY when a separable macro is defined (e.g. #ifdef XRAY_SEPARABLE_SHADERS ... #else name-matched ... #endif), or accept that the monolithic path is now used on desktop too (simpler — desktop GL 4.1 also supports monolithic linking; consider forcing monolithic everywhere to keep ONE shader variant). Decide: forcing monolithic on desktop as well removes the dual-variant burden — recommended.
6. If forcing monolithic on ALL platforms (recommended): then gl_PerVertex redeclaration and varying layouts can be removed unconditionally, and desktop uses GLLinkMonolithicProgram too — this dramatically simplifies the iostructs (one form) and is the lowest-risk long-term. Validate desktop GL 4.1 still renders after the switch.
7. Test incrementally with the shaders needed for the MAIN MENU first (2D/UI: stub_default, ui/font shaders, p_TL/v_TL from common_iostructs.h:41-67, screen_set, postpr), then deferred scene shaders (flat/bumped/accum/combine) for the test level.

**Files**

- `res/gamedata/shaders/gl/iostructs/ (81 files; e.g. v_static_flat.h, v_dumb.h, p_flat.h, p_bumped.h, p_accum.h, v_combine.h/p_combine.h, v_build.h/p_build.h, v_filter.h/p_filter.h, v_postpr.h/p_postpr.h, p_aa_aa*.h, v_shadow*.h/p_shadow*.h, v_model_*.h, v_detail.h, p_particle*.h, v_sky.h, v_clouds.h, ...)`
- `res/gamedata/shaders/gl/common_iostructs.h (the struct definitions v_*, v2p_*, p_*, f_deffer :1-471 — the 'contract' the iostructs marshal to/from)`
- `res/gamedata/shaders/gl/iostructs/p_flat.h (canonical PS example: SV_Target0/1/2 outs :2-6, layout(location=TEXCOORD*) in varyings :15-26, main() unpack :34-63)`
- `res/gamedata/shaders/gl/iostructs/v_static_flat.h (canonical VS example: out gl_PerVertex :2, layout(location=NORMAL/...) in attribs :10-20, layout(location=TEXCOORD*) out varyings :23-34, main() :38-65)`

**Acceptance**

- [ ] Every .vs/.ps pair links as a monolithic program on ES with no 'varying X not declared'/'gl_PerVertex redeclared'/'layout qualifier not allowed on varying' errors.
- [ ] Fragment outputs use explicit layout(location=0/1/2) matching MRT order; no glBindFragDataLocation is needed for correct output routing.
- [ ] Menu shaders compile+link first; then flat/bumped/accum/combine deferred shaders.
- [ ] (If monolithic-everywhere chosen) desktop GL 4.1 still renders correctly with the regenerated iostructs.
- [ ] The regeneration is script-driven and reproducible, not 81 manual edits.

**Risks / easy to miss**

- Varying NAME mismatch is silent-failure-prone: if a VS out and PS in end up with different names or different #ifdef guarding, the linker either errors or (worse) leaves an input undefined -> garbage/NaN in G-buffer. Diff each pair's varying set under every macro combination.
- Interpolation qualifiers: integer varyings on ES MUST be 'flat' (e.g. any 'out int'/'out uint' interpolants) or linking/compile fails. Audit for non-float varyings (mask, material ids) and add 'flat'.
- Removing layout(location) from varyings while KEEPING it on VS inputs and PS outputs is a precise distinction — a script that strips all layout() will break attribute binding (which the C++ side maps via VertexUsageList) and MRT output routing.
- common_iostructs.h struct field order encodes the interface; if the iostructs marshalling (I.field = varying / out = O.field) drifts from the struct, shaders compile but render wrong.
- 81 files × multiple macro permutations = large test surface; without a real STALKER asset set (open decision #4) some shader variants can only be smoke-compiled, not visually validated.

### ⬜ 4.6 Replace glBindFragDataLocation and reconcile the compile/link traits for GLES monolithic programs

`status: todo` · `effort: medium`

**Goal.** Remove the desktop-only glBindFragDataLocation calls from the C++ shader pipeline (ES routes fragment outputs via the layout(location) added in 4.5) and make GLCompileShader/GLUseBinary/GLLinkMonolithicProgram correct for the ES monolithic path.

**Steps**

1. In GLLinkMonolithicProgram (ShaderResourceTraits.h:119-152) remove/guard the four glBindFragDataLocation calls (:133-136). On ES, fragment output locations come from the shader's layout(location=N) (added in 4.5), so BindFragDataLocation is both unavailable and unnecessary. Under desktop, either keep them (if still separable) or, if going monolithic-everywhere, rely on layout(location) there too and drop the binds. glBindFragDataLocation must be BEFORE glLinkProgram to have any effect — since we now set locations in-shader, delete the calls (guard with #ifndef XR_PLATFORM_APPLE_IOS if you keep desktop binds, or delete outright if shaders always carry explicit locations).
2. GLCompileShader (:44-90): the '!GLAD_GL_ARB_separate_shader_objects' early-return at :61-62 returns {'s', shader} (raw shader object) — this is the ES path and is correct. Ensure the separable branch (:64-89, which creates a per-stage program and binds frag data) is never taken on ES (it is gated by GLAD_GL_ARB_separate_shader_objects which is false on ANGLE).
3. GLUseBinary (:92-117) is the program-binary cache load path and is separable-only (creates GL_PROGRAM_SEPARABLE program, binds frag data). For ES initial bring-up the binary cache is disabled (task 4.8), so GLUseBinary is not called; still, guard its glBindFragDataLocation (:102-105) so it compiles clean and, when monolithic caching is added later, does not call the missing function.
4. GLGeneratePipeline (:154-164) uses glGenProgramPipelines/glUseProgramStages/glValidateProgramPipeline (separable, ARB) — never invoked on ES (separable=false). Leave as-is but ensure it is not referenced from the ES path.
5. Confirm _LinkPP monolithic branch (glResourceManager_Resources.cpp:113-120): after GLLinkMonolithicProgram it parses constants against the whole program (RC_dest_all) and nulls pass.ps/vs/gs. Verify pass.gs is null for all content (no geometry shaders) so glAttachShader(program, gs) at :131-132 is skipped (gs==0).
6. GL_GEOMETRY_SHADER references: GLCompileShader<GL_GEOMETRY_SHADER> (SGS trait) and GL_GEOMETRY_SHADER_BIT (GLGeneratePipeline) compile because the GLAD merged header defines the enums; they are never executed on ES (no .gs content). Leave compiling, dead at runtime.
7. Verify glGetShaderiv(GL_SHADER_SOURCE_LENGTH)/glGetShaderSource used in show_compile_errors (:25-27) exist on ES3.0 (they do) for the error dump path — important for debugging the 81 shaders.

**Files**

- `src/Layers/xrRender/ShaderResourceTraits.h (GLCompileShader :44-90 with glBindFragDataLocation :73-76 in the separable branch; GLUseBinary :92-117 with binds :102-105; GLLinkMonolithicProgram :119-152 with binds :133-136; GLGeneratePipeline :154-164)`
- `src/Layers/xrRenderGL/glResourceManager_Resources.cpp (_LinkPP :104-123 chooses generate-pipeline vs monolithic based on GLAD_GL_ARB_separate_shader_objects)`
- `src/Layers/xrRenderPC_GL/rgl_shaders.cpp (create_shader wrappers :18-55; program-binary cache :493-584)`

**Acceptance**

- [ ] No glBindFragDataLocation is called at runtime on iOS (verified by symbol not-resolved-safe / guarded out).
- [ ] Monolithic link path produces working programs whose fragment outputs land in the correct MRT slots purely from layout(location).
- [ ] show_compile_errors dumps ES compiler logs for failed shaders (critical for iterating on the 81 iostructs).
- [ ] Desktop GL build unaffected (or intentionally also monolithic per 4.5 decision).

**Risks / easy to miss**

- If layout(location) frag outputs (4.5) and the removal of glBindFragDataLocation (here) are not landed together, either the binds call missing functions (crash/GL error) or outputs route to wrong attachments (swapped G-buffer channels).
- glBindFragDataLocation binds by NAME ('SV_Target0'...) at link time; the in-shader layout must use the SAME location integers the C++/FBO side expects (0=position/albedo, 1=normal, 2=color per f_deffer) — cross-check with task 4.10 attachment order.
- The constants.parse(RC_dest_all) monolithic reflection must find uniform locations by name across the linked program; confirm glGetUniformLocation-based reflection (not separable program interfaces) is what the ES path uses.

### ⬜ 4.7 Replace desktop-only GL calls in the runtime (draw, buffers, formats, polygon mode, screenshot)

`status: todo` · `effort: medium`

**Goal.** Eliminate GL entrypoints that do not exist in OpenGL ES 3.0 core from the hot runtime paths: emulate glDrawElementsBaseVertex, drop glPolygonMode, convert glDrawBuffer->glDrawBuffers, glMapBuffer->glMapBufferRange, per-channel texture swizzle, and BGRA/GL_RGB CPU-format assumptions.

**Steps**

1. glDrawElementsBaseVertex (glR_Backend_Runtime.h:329): NOT in GLES3.0 core (it is ES3.2 / OES_draw_elements_base_vertex / EXT_draw_elements_base_vertex). At runtime prefer the extension if ANGLE exposes it (check GLAD_GL_OES_draw_elements_base_vertex / GLAD_GL_EXT_draw_elements_base_vertex and call glDrawElementsBaseVertexOES/EXT). Fallback emulation when absent: bake baseV into the vertex attribute binding — before the draw, re-point the vertex buffer with an offset of baseV*stride via glBindVertexBuffer(bindingindex, vbo, baseV*stride, stride) (needs ARB_vertex_attrib_binding-style separate binding, which ES3.0 does NOT have) OR re-issue glVertexAttribPointer with a byte offset of baseV*stride for each attribute, then call plain glDrawElements(Topology, count, GL_UNSIGNED_SHORT, startI*2). Simplest robust path: add baseV to indices at buffer build time is not possible (shared IB), so offset the vertex pointer. Encapsulate as CBackend::Render so both desktop (base-vertex) and ES (offset) coexist.
2. Note index type is GL_UNSIGNED_SHORT (16-bit) — fine on ES3.0. Verify no path needs >65535 vertices per draw with base-vertex offsetting (the offset approach shifts the attribute base, so indices stay 16-bit relative — correct).
3. glPolygonMode: remove/guard the wireframe debug in r2_R_render.cpp:173-175,196-199 under #ifndef XR_PLATFORM_APPLE_IOS (or a generic 'has polygon mode' runtime check). In glR_Backend_Runtime.h SetFillMode (:458) guard the glPolygonMode call; ConvertFillMode (glStateUtils.cpp:8-22) can stay but its result is only used by the guarded call. Wireframe is a non-shipping debug feature on iOS.
4. glDrawBuffer (glSH_RT.cpp:91, in resolve_into MSAA resolve): replace with glDrawBuffers(1, {GL_COLOR_ATTACHMENT1}) — glDrawBuffer (singular) is desktop-only; glDrawBuffers (plural) is ES3.0 core. This whole resolve_into is MSAA-only (kept off) but must compile/run-clean; the code already calls glDrawBuffers at :99 so just remove the singular :91 call or convert it.
5. glMapBuffer (glSH_Texture.cpp:90, Theora video decode into PBO): replace with glMapBufferRange(GL_PIXEL_UNPACK_BUFFER, 0, size, GL_MAP_WRITE_BIT|GL_MAP_INVALIDATE_BUFFER_BIT) which is ES3.0 core, then glUnmapBuffer. Or bypass PBO entirely and glBufferSubData/glTexSubImage2D from CPU memory. Video is not needed for main-menu milestone; low priority but must compile.
6. GL_BGRA (glSH_Texture.cpp:96): ES3.0 has no GL_BGRA external format (only via EXT_texture_format_BGRA8888/APPLE_texture_format_BGRA8888). The Theora frame is decoded BGRA; either swap R/B on CPU during DecompressFrame to produce RGBA and upload GL_RGBA, or upload as RGBA8 storage with GL_RGBA and swizzle. Convert the source to RGBA.
7. GL_TEXTURE_SWIZZLE_RGBA (glTexture.cpp:128): the single-call combined swizzle is desktop-only; ES3.0 supports the four individual GL_TEXTURE_SWIZZLE_R/G/B/A. Replace the one glTexParameteriv(GL_TEXTURE_SWIZZLE_RGBA, &Swizzles[0]) with four glTexParameteri(target, GL_TEXTURE_SWIZZLE_R, Swizzles[0]) ... _A, Swizzles[3]. Keep the greyscale-alpha-font skip (EXTERNAL_RED guard :127).
8. glReadPixels GL_RGB (glr_screenshot.cpp:36): ES3.0 glReadPixels only guarantees GL_RGBA/GL_UNSIGNED_BYTE plus one impl-defined combo; GL_RGB/UNSIGNED_BYTE may be rejected. Read GL_RGBA and drop alpha into the screenshot buffer, or query GL_IMPLEMENTATION_COLOR_READ_FORMAT/TYPE. Screenshots are non-critical; guard/adjust so it does not error.
9. glBufferUtils.cpp ConvertVertexDeclaration (:174-178) uses glVertexAttribFormat/glVertexAttribBinding under GLAD_GL_ARB_vertex_attrib_binding — ES3.0 lacks vertex_attrib_binding, so that branch is skipped and SetVertexDeclaration's glVertexAttribPointer path (:156-165) is used. Verify the ES path consistently uses glVertexAttribPointer (which needs the buffer bound at call time) and that VAOs (glGenVertexArrays :49 in _CreateDecl) are used correctly — VAOs are ES3.0 core.

**Files**

- `src/Layers/xrRenderGL/glR_Backend_Runtime.h (Render() glDrawElementsBaseVertex :329; glDrawArrays :342; SetFillMode glPolygonMode :458)`
- `src/Layers/xrRenderGL/glSH_RT.cpp (resolve_into glDrawBuffer(GL_COLOR_ATTACHMENT1) :91; MSAA tex :52-57)`
- `src/Layers/xrRenderGL/glSH_Texture.cpp (glMapBuffer :90; GL_BGRA glTexSubImage2D :96 — Theora video path)`
- `src/Layers/xrRenderGL/glTexture.cpp (GL_TEXTURE_SWIZZLE_RGBA :128)`
- `src/Layers/xrRenderGL/glStateUtils.cpp (ConvertFillMode :8-22)`
- `src/Layers/xrRender_R2/r2_R_render.cpp (glPolygonMode wireframe :174,198)`
- `src/Layers/xrRenderGL/glr_screenshot.cpp (glReadPixels GL_RGB :36)`
- `src/Layers/xrRenderGL/glBufferUtils.cpp (glVertexAttribFormat/Binding under ARB_vertex_attrib_binding :174-178; glMapBufferRange already used :399,448)`

**Acceptance**

- [ ] No GLES-absent entrypoint is invoked at runtime on iOS (verified by a clean run with GL error checking / ANGLE validation layer): no glPolygonMode, glDrawBuffer(singular), glMapBuffer, GL_BGRA, GL_TEXTURE_SWIZZLE_RGBA, or unguarded glDrawElementsBaseVertex.
- [ ] Base-vertex draws render geometry correctly (via extension or vertex-offset emulation) — a textured mesh appears in the right place.
- [ ] Texture channel swizzles (L8 red-replicate, fonts) look correct with the per-channel swizzle calls.
- [ ] Desktop GL still uses the native fast paths (base-vertex, combined swizzle) via guards.

**Risks / easy to miss**

- The base-vertex EMULATION is the trickiest: re-issuing glVertexAttribPointer with per-attribute byte offsets must happen for EVERY enabled attribute of the current declaration and must be undone/reset for subsequent draws — easy to corrupt attribute state. Prefer the OES/EXT extension if ANGLE-Metal exposes it (it commonly does) and keep emulation as fallback only.
- glMapBufferRange with UNSYNCHRONIZED (LOCKFLAGS in glBufferUtils :10-11) on ANGLE can behave differently than desktop; validate dynamic stream buffers (UI, particles) do not tear/stall.
- GL_TEXTURE_SWIZZLE relies on gli providing swizzle values for the ES30 profile (task 4.9) — ensure format.Swizzles is populated for ES translation.
- Screenshot/video are easy to forget because they are not on the menu path, but an unguarded call there will still throw GL errors that pollute debugging.

### ⬜ 4.8 HW caps, extension gating, and shader-binary cache for GLES

`status: todo` · `effort: medium`

**Goal.** Make CHWCaps/feature detection report ES-appropriate capabilities, gate desktop-only extension checks to their GLES equivalents (especially the shader-binary program cache and float-renderable formats), and disable the separable-only cache for initial bring-up.

**Steps**

1. glHWCaps::Update (glHWCaps.cpp): the caps are mostly hardcoded (raster_major=4, MRT_count=4, etc). Review each against ES3.0 minimums: MRT count — ES3.0 guarantees GL_MAX_DRAW_BUFFERS>=4 and GL_MAX_COLOR_ATTACHMENTS>=4 (query them rather than hardcode 4; Apple GPUs support 8). bVTF (:31): vertex texture fetch is ES3.0 core — set true (query GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS>0). Guard the GLAD_GL_VERSION_3_0 test to also accept GLAD_GL_ES_VERSION_3_0.
2. VTF/CTI unit queries in glHW.cpp:134-135 (GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS, GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS) are ES3.0 core — keep, but validate the deferred renderer's sampler demand (common_samplers.h declares ~20 samplers across phases) fits ES3.0's guaranteed GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS (>=32 on ES3.0) and per-stage GL_MAX_TEXTURE_IMAGE_UNITS (>=16) — combine phase uses many; count worst-case bound samplers per program.
3. ComputeShadersSupported stays false (glHW.cpp:146) — ES3.0 has no compute (3.1+). Fine; HDAO-CS and other compute paths are already disabled.
4. Float-renderable formats: ES3.0 core does NOT allow rendering to RGBA16F/RGBA32F/R16F color attachments without EXT_color_buffer_float (and half-float via EXT_color_buffer_half_float). At init, query GLAD_GL_EXT_color_buffer_float / GLAD_GL_EXT_color_buffer_half_float and store on Caps; the G-buffer (task 4.10) depends on these. ANGLE-Metal exposes EXT_color_buffer_float. Log a fatal/clear message if absent.
5. Shader-binary cache (rgl_shaders.cpp:496,510,557): the gate is GLAD_GL_ARB_get_program_binary && GLAD_GL_ARB_separate_shader_objects — both false on ES. For initial bring-up, on iOS take the 'always compile, no cache' branch (skip the whole binary read at 510-542 and write at 557-583). Later, add an ES cache using GLES3.0 core glGetProgramBinary/glProgramBinary (available when GL_NUM_PROGRAM_BINARY_FORMATS>0) against the MONOLITHIC program handle (currently caching is separable-only and monolithic caching is a TODO in GLLinkMonolithicProgram:126-127). Relax the gate to (GLAD_GL_ES_VERSION_3_0 || (ARB_get_program_binary && ARB_separate_shader_objects)) once monolithic cache write/read is implemented.
6. Because ES shader compile of ~286 programs at first launch is SLOW, the program-binary cache matters for real devices — schedule the monolithic-cache implementation as a follow-up within this task but after first-light. Cache key must include GL_RENDERER/GL_VERSION/GL_SHADING_LANGUAGE_VERSION (already written :569-571) plus the source CRC (getFileCrc32 :505) so an ANGLE update invalidates it.
7. raster.dwMRT_count / b_MRT_mixdepth (:41-43) and swizzle/caps flags feed the rendertarget setup; ensure they match what ES actually allows (mixed-format MRT with a shared depth is fine on ES3.0).

**Files**

- `src/Layers/xrRenderGL/glHWCaps.cpp (hardcoded caps; bVTF gate GLAD_GL_VERSION_3_0||ARB_texture_float :31; MRT_count=4 :41; useCombinedSamplers :73)`
- `src/Layers/xrRenderGL/glHW.cpp (CreateDevice caps queries :133-146; ComputeShadersSupported=false :146; VTF units :134)`
- `src/Layers/xrRenderPC_GL/rgl_shaders.cpp (cache gating GLAD_GL_ARB_get_program_binary && GLAD_GL_ARB_separate_shader_objects :496,510,557)`
- `src/Layers/xrRender/HWCaps.h (CHWCaps struct)`

**Acceptance**

- [ ] Caps report ES3.0-truthful values (MRT>=4 queried, VTF on, compute off) and log EXT_color_buffer_float presence.
- [ ] On iOS the shader path compiles all programs without touching the separable-only binary cache (no crash, correct rendering), and (follow-up) a monolithic binary cache measurably cuts second-launch shader build time.
- [ ] Extension gates use GLAD_GL_ES_VERSION_3_0 where they previously used GLAD_GL_VERSION_3_0/ARB_*.
- [ ] Clear diagnostic if a required ES extension (color_buffer_float) is missing.

**Risks / easy to miss**

- Hardcoded caps that are true on desktop 4.1 but false on ES (e.g. assuming get_program_binary+separable) cause either wrong code paths or missing cache; audit each GLAD_GL_ARB_*/VERSION_* reference in the GL layer for an ES fallback.
- First-run shader compilation of hundreds of programs on a phone can take many seconds and may trip watchdog/ANLE recompiles — the binary cache is not merely an optimization on mobile, it is close to required for acceptable UX; do not defer it indefinitely.
- EXT_color_buffer_float present but a SPECIFIC format (e.g. R11F_G11F_B10F vs RGBA16F) not color-renderable — must FBO-completeness-check per format, not just per-extension (ties to 4.10).

### ⬜ 4.9 Texture pipeline: gli ES30 profile + DXT/BC->ASTC offline transcode + runtime RGBA fallback

`status: todo` · `effort: very-high`

**Goal.** Make texture loading work on Apple GPUs, which have NO S3TC/BC support. Switch gli's format translation to the ES30 profile, build an OFFLINE DXT/BC->ASTC transcode pipeline to repackage game textures, and add a runtime decompress-to-RGBA8 fallback so early bring-up works even before assets are transcoded.

**Steps**

1. Switch gli profile: in glTexture.cpp:116 use gli::gl GL(gli::gl::PROFILE_ES30) on iOS (keep PROFILE_GL33 on desktop) so GL.translate returns ES-legal internal/external/type/swizzle. Verify gli's ES30 tables map the incoming DDS formats; DXT/BC will map to S3TC enums that ES cannot accept — hence the transcode/fallback below.
2. Runtime RGBA fallback (for FIRST-LIGHT, before any transcoding): when gli reports gli::is_compressed(texture.format()) AND the compressed format is S3TC/BC (unsupported on Apple), decompress on the CPU to RGBA8 and upload via glTexStorage2D(GL_RGBA8)+glTexSubImage2D(GL_RGBA,GL_UNSIGNED_BYTE). Use gli's own decompression or a small BC1/BC2/BC3 decoder (bcdec.h). This is slow and memory-heavy but unblocks rendering with the original assets. Gate behind a cvar/define so it can be turned off once ASTC assets exist.
3. Offline transcode pipeline (the PRODUCTION path): build a tool that walks the game's .dds textures, decodes DXT1/DXT3/DXT5 (and any BC) to RGBA, and encodes to ASTC (e.g. 6x6 or 4x4 LDR) via astcenc, writing KTX2 or DDS-with-ASTC (or raw .dds replaced by .ktx). Preserve mip chains, cube faces, and the color-vs-normal distinction (normals want higher-quality/uncorrelated ASTC or keep as RG). Package the transcoded tree into the iOS bundle/sandbox. gli can LOAD KTX/ASTC (PROFILE_KTX/ES30), so the runtime just uploads via the existing glCompressedTexSubImage2D path with the ASTC internal format.
4. Extend glTextureUtils.cpp format table (:14-100) if any explicit D3DFORMAT->GLenum mapping is needed for ASTC or for the RGBA fallback (the current table has no DXT entries — they are handled by gli, not this table; but RT/format conversions for ConvertTextureFormat may need ASTC-adjacent additions).
5. Per-channel swizzle (from task 4.7): glTexture.cpp:128 combined swizzle -> four GL_TEXTURE_SWIZZLE_R/G/B/A calls, using gli's format.Swizzles under the ES30 profile.
6. glTexStorage2D/3D and glCompressedTexSubImage2D/3D (glTexture.cpp:137-215) are ES3.0 core — keep. Validate that texture.levels()/extent from gli produce ES-legal mip sizes for ASTC (ASTC block constraints on small mips) — some tiny mips may need clamping or the ASTC encoder must handle them.
7. Cube maps: gli TARGET_CUBE path (sub_target GL_TEXTURE_CUBE_MAP_POSITIVE_X+face :169-171) is ES3.0 core; ensure ASTC cube uploads work. TARGET_3D (material lookup s_material sampler3D) — ensure the 3D texture format is ES-renderable/uploadable (likely uncompressed).
8. Decide transcode granularity to keep app size sane; ASTC 6x6 is a good size/quality tradeoff for diffuse, 4x4 or 5x5 for normals/detail. Document required encoder settings.
9. Milestone sequencing: menu textures (UI atlases, fonts — often uncompressed or DXT5) first via RGBA fallback; then bulk level textures via ASTC once the pipeline runs.

**Files**

- `src/Layers/xrRenderGL/glTexture.cpp (gli::gl GL(PROFILE_GL33) :116; format=GL.translate :118; glTexStorage2D/3D :137-149; glCompressedTexSubImage2D/3D :180-215; glTexSubImage2D/3D :193-227; swizzle :128)`
- `src/Layers/xrRenderGL/glTextureUtils.cpp (D3DFORMAT->GLenum table :14-100 — DXT entries are all commented out :52-56)`
- `Externals/gli/gli/gl.hpp (profile enum PROFILE_ES30 :324; is_compressed/format translate; ASTC support present)`
- `(new) offline tool under src/utils/ or misc/ios/ for DDS(DXT)->ASTC/KTX repackaging`
- `res/gamedata/textures (the asset tree to transcode; sourced from the user's legal STALKER copy)`

**Acceptance**

- [ ] With PROFILE_ES30 + RGBA fallback, original DXT game textures load and display correctly on device/simulator (no GL_INVALID_ENUM/format errors, no black textures).
- [ ] The offline tool converts a sample texture set to ASTC/KTX and the runtime uploads them via the compressed path; visual parity with desktop is acceptable.
- [ ] L8/greyscale/font swizzles render correctly with per-channel swizzle.
- [ ] App can boot the menu with textures using either path (fallback or ASTC) selectable at build/runtime.

**Risks / easy to miss**

- Apple GPUs (all iOS) do NOT support S3TC/BC at all — there is no runtime shortcut; either decompress (slow/large) or transcode (pipeline work). Both are required: fallback for bring-up, ASTC for shipping.
- CPU BC decompression at load time multiplies VRAM/RAM (RGBA8 is 4-8x the size of DXT) and load time — fine for a few menu textures, unacceptable for a full level; do not ship the fallback.
- ASTC of NORMAL maps is quality-sensitive (ASTC is perceptual/LDR) — naive encoding wrecks lighting; may need per-texture encoder profiles or keep normals as uncompressed RG/RGBA16.
- gli's ES30 translate tables and ASTC/KTX loading must actually cover the formats present in STALKER DDS files; verify with real assets (blocked on asset-availability open decision).
- Mip and non-power-of-two constraints differ; ASTC block sizes vs small mip levels can produce encoder or upload errors.
- Asset repackaging changes the on-disk texture tree — the FS path logic (FS.exist $game_textures$ .dds in glTexture.cpp:76-101) must find the new extension (.ktx/.dds-astc); update the loader/extension resolution accordingly.

### ⬜ 4.10 Validate the deferred G-buffer under ES (float/half color attachments, FBO completeness, MRT)

`status: todo` · `effort: high`

**Goal.** Ensure the R3/deferred renderer's G-buffer, accumulation, shadow, and post-process render targets are ES3.0-renderable: float/half formats gated on EXT_color_buffer_float, correct MRT attachment order matching the layout(location) fragment outputs, and runtime FBO-completeness checks with graceful format fallback.

**Steps**

1. Enumerate the deferred RTs and their formats from the R2/R3 rendertarget setup (position/normal/color G-buffer, accumulator, generic, bloom, luminance, SSAO, shadow map depth). Cross-check each requested D3DFORMAT against glTextureUtils.cpp mapping: RGBA16F/RG16F/R16F/R32F/RGBA32F (:76-81) — these require EXT_color_buffer_float (RGBA16F/R16F also via EXT_color_buffer_half_float) to be COLOR-RENDERABLE on ES3.0.
2. At CRT::create (glSH_RT.cpp:20-66) after glTexStorage2D, attach to a scratch FBO and call glCheckFramebufferStatus; if not GL_FRAMEBUFFER_COMPLETE, log the format and fall back (e.g. RGBA32F->RGBA16F, or half->float) or reduce G-buffer precision. Implement a format-negotiation helper used by all RT allocations.
3. MRT ordering: the deferred geometry pass writes SV_Target0/1/2 (f_deffer -> position, Ne/normal, C/color; or position,C under GBUFFER_OPTIMIZATION, common_iostructs.h:303-322). The layout(location=0/1/2) added in task 4.5 MUST match the glDrawBuffers/attachment order set when binding the G-buffer FBO. Audit set_RT/phase_scene_begin (r2/gl rendertarget code) so attachment N == location N.
4. Depth-stencil: GL_DEPTH24_STENCIL8 (glTextureUtils.cpp:60-61) is ES3.0 core and depth-texture sampling (s_position/s_normal use the depth for reconstruction; shadow maps use sampler2DShadow) is supported. Verify shadow map depth textures use a depth-renderable + sampler2DShadow-compatible format (DEPTH_COMPONENT24/32F) and GL_TEXTURE_COMPARE_MODE is set (ES3.0 core).
5. MSAA RTs (glSH_RT.cpp:52-57, glTexImage2DMultisample + GL_TEXTURE_2D_MULTISAMPLE): these are ES3.1+, so with SampleCount==1 the else-branch (glTexStorage2D) is taken — confirm the deferred setup never requests SampleCount>1 on iOS (o.msaa off). Guard the multisample branch so it compiles but is never hit on ES3.0.
6. resolve_into (glSH_RT.cpp:88-103) does a glBlitFramebuffer color resolve — glBlitFramebuffer is ES3.0 core (used already in Present, glHW.cpp:244). Fix the glDrawBuffer(singular) at :91 (task 4.7). This path is MSAA-only; keep off.
7. Verify the combine/post phases sample the G-buffer via the s_position/s_normal/s_diffuse/s_accumulator samplers (common_samplers.h:49-70) as regular textures (not multisample, since USE_MSAA off) — the non-MSAA declarations (:53-56,67-69) apply.
8. Test order: get the CLEARED default framebuffer presenting (Phase 3), then the menu (which may use a simpler RT path / straight-to-backbuffer), then enable the full deferred chain for the test level, checking FBO completeness at each RT creation.

**Files**

- `src/Layers/xrRenderGL/glSH_RT.cpp (CRT::create :20-66 uses ConvertTextureFormat + glTexStorage2D :59; MSAA glTexImage2DMultisample :56; resolve_into :88-103)`
- `src/Layers/xrRenderGL/glTextureUtils.cpp (D3DFORMAT->GLenum: RG16F/RGBA16F/R32F/RGBA32F :76-81; D24S8 :60-61)`
- `src/Layers/xrRender_R2/r2_rendertarget*.cpp and gl_rendertarget_*.cpp (RT allocation: build_textures, phase_combine, accumulator, ssao, smap, bloom, luminance)`
- `src/Layers/xrRender_R2/r2_types.h (RT format choices)`
- `src/Layers/xrRenderGL/glHW.cpp (Caps.fTarget/fDepth :90-91)`

**Acceptance**

- [ ] Every deferred RT reports GL_FRAMEBUFFER_COMPLETE on device/simulator, or falls back to a supported format with a logged notice.
- [ ] G-buffer MRT outputs land in the correct textures (position/normal/albedo not swapped) — verified visually or via a debug view.
- [ ] No float-render errors: EXT_color_buffer_float is present (logged) and the chosen formats are color-renderable.
- [ ] Shadow maps sample correctly (sampler2DShadow comparison works) with MSAA/min-max disabled.
- [ ] The test level renders a recognizable frame.

**Risks / easy to miss**

- Simulator GLES/ANGLE float-render support historically lags device; a format complete on device may fail on simulator (or vice versa) — check both and prefer the negotiation helper over hardcoded formats.
- If layout(location) MRT order (4.5) and FBO attachment order here disagree, the G-buffer silently corrupts (normals in albedo slot) — this is a classic hard-to-see bug; add a debug RT visualizer early.
- RGBA32F G-buffer may be complete but bandwidth-crushing on a mobile TBDR GPU; consider RGBA16F/packed formats for perf even if 32F is 'supported'.
- ES3.0 has no GL_RED/GL_RG renderable guarantees for some float variants; R32F/RG16F color attachments specifically need checking.
- Apple TBDR strongly prefers not reloading G-buffer between passes; the desktop deferred structure (write G-buffer, then sample it in lighting) forces store/load and can be slow — acceptable for correctness milestone, a known perf risk for later.

### ⬜ 4.11 Configure the ImGui OpenGL3 backend for GLES3 (console/debug/UI overlay)

`status: todo` · `effort: low`

**Goal.** Ensure the Dear ImGui OpenGL3 backend (used for the console and debug UI, and possibly menu overlays) initializes in GLES3 mode with the right GLSL version and loader, since it renders through the same context and must not conflict with GLAD or issue desktop-only calls.

**Steps**

1. The imgui backend already auto-selects IMGUI_IMPL_OPENGL_ES3 on Apple iOS/TV (imgui_impl_opengl3.cpp:155,167) and, when ES3, includes the system GLES headers and defaults glsl_version to '#version 300 es'. Confirm the engine compiles the backend with the correct branch on iOS — it keys off (defined(__APPLE__) && TARGET_OS_IOS), which is already true; no explicit define may be needed, but set IMGUI_IMPL_OPENGL_ES3 explicitly in the iOS build to be safe.
2. Loader conflict: imgui's default IMGL3W loader (imgui_impl_opengl3_loader.h) vs the engine's GLAD. On ES3 the backend uses the platform GLES headers directly (no imgl3w), avoiding conflict — verify. If a custom-loader define is needed, set IMGUI_IMPL_OPENGL_LOADER_CUSTOM and route to GLAD's function pointers, or ensure only one loader is active in the translation unit.
3. ImGui_ImplOpenGL3_Init() is called with no glsl_version (dxImGuiRender.cpp:83); on ES3 the backend picks '#version 300 es' — fine. If it does not, pass '#version 300 es' explicitly on iOS.
4. The backend guards glPolygonMode (HasPolygonMode, :722) and glDrawElementsBaseVertex (IMGUI_IMPL_OPENGL_MAY_HAVE_* macros, :677) behind desktop-only feature macros that are off for ES3 — confirm those macros evaluate false on iOS so imgui itself does not call the missing entrypoints.
5. Verify imgui creates/uses VAOs and its own shader compatible with ES3 (it does). Ensure it does not fight the engine's GL state (the engine already restores state around imgui in desktop; keep parity).

**Files**

- `src/Layers/xrRender/dxImGuiRender.cpp (ImGui_ImplOpenGL3_Init() :83)`
- `Externals/imgui/backends/imgui_impl_opengl3.cpp (ES3 auto-detect :155,166-172; glsl_version default '#version 300 es' on ES3 :353,397; glPolygonMode save/restore :486,722; glDrawElementsBaseVertex :677)`
- `Externals/imgui/backends/imgui_impl_opengl3_loader.h (imgl3w loader — may clash with GLAD)`

**Acceptance**

- [ ] The in-engine console and any ImGui debug UI render on device/simulator in the ES3 context without GL errors.
- [ ] imgui does not invoke glPolygonMode/glDrawElementsBaseVertex/other desktop-only calls on iOS.
- [ ] No symbol/loader conflict between imgui's loader and GLAD.
- [ ] '#version 300 es' is used for imgui's shaders.

**Risks / easy to miss**

- Two GL loaders (imgl3w + GLAD) in one process can double-define or leave one uninitialized; the ES3 path should avoid imgl3w but verify per compiler.
- imgui state save/restore assumes some desktop caps; on ES a save of a nonexistent state (e.g. GL_POLYGON_MODE query) would error — the version-guarded code should skip it, but test.
- The console is the primary on-device debugging surface for the whole port — if imgui is broken you lose your main diagnostic; prioritize it early alongside the cleared-frame milestone.

### ⬜ 4.12 Renderer module registration and shader-tree packaging for iOS

`status: todo` · `effort: low`

**Goal.** Confirm the GL renderer module is selected on iOS, the correct shader tree ('gl\\') and transcoded textures are packaged and found in the sandbox, and the HW probe does not spuriously reject iOS.

**Steps**

1. Confirm the non-Windows branch of ObtainSupportedModes (xrRender_GL.cpp:41-46) registers renderer_r3 (id 4) and that this is what iOS uses; the shader path for this renderer is 'gl\\' (r2.h:404). No change needed unless a distinct iOS mode id is desired.
2. CheckGameRequirements (:51-60) does FS.exist('$game_shaders$', getShaderPath()) — ensure the 'gl' shader tree is packaged into the app bundle and that '$game_shaders$' resolves to the read-only bundle path on iOS (Phase 3 path work). If shaders are missing the renderer silently refuses.
3. xrRender_test_hw / sdl_window_test_helper (r2_test_hw.cpp): creates a hidden 1x1 GL window to probe. On iOS a hidden GL window may be unsupported; either skip the probe under XR_PLATFORM_APPLE_IOS and return TRUE, or reuse the real window. Ensure the probe requests ES3 (it uses SetPrimaryAttributes, so it inherits the ES fix from 4.2).
4. Path separators: getShaderPath returns 'gl\\' (backslash). The shader include loader (rgl_shaders.cpp:166-169) converts '/' to '\\'; on iOS the FS layer must accept backslash-style virtual paths or normalize them. Verify FS.update_path handles this on POSIX/iOS (it does on other POSIX targets, but confirm).
5. Packaging: add res/gamedata/shaders/gl and the transcoded textures to the iOS bundle resources (CMake RESOURCE / bundle copy, or a data pack). Ensure they are copied into the .app and, if needed, extracted to the writable prefs dir on first run.
6. SetupEnv (:62-96) sets ps_r2_advanced_pp=true for r3/rgl modes — the advanced deferred path. For the very first menu milestone consider whether a simpler mode reduces surface area, but the level milestone needs advanced_pp.

**Files**

- `src/Layers/xrRenderPC_GL/xrRender_GL.cpp (RGLRendererModule::ObtainSupportedModes :33-49 registers renderer_r3 id 4 on non-Windows :44; CheckGameRequirements verifies $game_shaders$ :51-60; SetupEnv :62-96)`
- `src/Layers/xrRender_R2/r2.h (getShaderPath 'gl\\' :404, 'r3\\' :400)`
- `src/Layers/xrRenderPC_GL/r2_test_hw.cpp (xrRender_test_hw probe)`
- `res/gamedata/shaders/gl/ (asset tree to bundle)`
- `(Phase 3 overlap) src/xrCore/LocatorAPI.cpp (bundle read-only + SDL_GetPrefPath writable path setup)`

**Acceptance**

- [ ] On iOS the engine selects the GL renderer (renderer_r3) and CheckGameRequirements passes (shaders found in bundle).
- [ ] The HW probe does not falsely fail on iOS.
- [ ] 'gl\\' shader paths and transcoded textures resolve correctly from the read-only bundle / writable sandbox.
- [ ] The renderer initializes far enough to begin compiling shaders.

**Risks / easy to miss**

- If '$game_shaders$' does not resolve (Phase 3 path setup incomplete) the renderer refuses with '~ No shaders found' and it looks like a renderer bug — verify path wiring first.
- Backslash shader subpaths on POSIX/iOS can fail FS lookups if not normalized.
- The hidden probe window can hang or fail on iOS and block startup; handle explicitly.
- Bundling the full shader+texture tree affects app size and first-run extraction time.

### ⬜ 4.13 Bring-up validation: main menu first, then a test level

`status: todo` · `effort: medium`

**Goal.** Drive the two phase milestones end-to-end on device/simulator, using incremental, observable checkpoints so regressions are caught per subsystem rather than at the end.

**Steps**

1. Checkpoint A (cleared frame): with 4.1-4.2 done, present a solid clear color every frame (glClear + swap). Confirms context, loader, and Present work. Overlaps Phase 3.
2. Checkpoint B (imgui/console): with 4.11, render the ImGui console over the clear. Gives on-device logging for everything after.
3. Checkpoint C (menu 2D shaders): compile+link the minimal shader set the main menu needs (stub_default, UI/font: p_TL/v_TL, screen_set, postpr) via the 4.3-4.6 pipeline; load menu textures via the RGBA fallback (4.9). Milestone: main menu renders and is interactable (input is Phase 5, but visuals must be correct).
4. Checkpoint D (single mesh): render one textured deferred mesh to validate the G-buffer MRT + combine path (4.5 varyings, 4.7 base-vertex, 4.10 FBO) before a full level.
5. Checkpoint E (test level): load a level and run the full deferred chain (geometry -> accum/lights -> combine -> post). Validate shadows (min-max/MSAA off), fog, and that no shader in the level fails to compile/link. Milestone: test level renders.
6. At each checkpoint enable ANGLE/GL error checking (CHK_GL everywhere + ANGLE validation) and dump any failing shader source via show_compile_errors (ShaderResourceTraits.h:8-42).
7. Track which of the 199 .ps / 87 .vs actually get exercised by menu vs level; prioritize fixing those. Non-exercised shaders can be smoke-compiled offline via a headless ANGLE to catch ES errors without the game.
8. Compare against the desktop GL 4.1 build as the reference image where an asset set exists.

**Files**

- `src/Layers/xrRenderGL/glHW.cpp (Present/clear path)`
- `src/Layers/xrRender/dxImGuiRender.cpp (console for on-device logging)`
- `src/Layers/xrRender_R2/r2_R_render.cpp (main scene render entry)`
- `res/gamedata/shaders/gl/* (menu vs level shader subsets)`
- `the full xrRender_GL + shader + texture stack from tasks 4.1-4.12`

**Acceptance**

- [ ] Milestone 1: main menu renders correctly on simulator and device (ANGLE default; EAGL fallback also verified).
- [ ] Milestone 2: a test level renders a recognizable frame with the deferred pipeline (lighting/shadows on, MSAA/min-max off).
- [ ] No unhandled GL/ANGLE errors during menu or level rendering.
- [ ] Every shader used by the menu and the test level compiles and links; failures are logged with source, not silent.

**Risks / easy to miss**

- Without a real STALKER asset set (open decision #4) the level milestone cannot be visually validated — mitigate by smoke-compiling all shaders headlessly and using any available free/test map.
- A single un-regenerated iostruct or un-transcoded texture can break a whole scene; the incremental checkpoints exist to localize such failures.
- ANGLE-on-Metal perf on a TBDR GPU with a store/load-heavy deferred renderer may be poor even when correct — correctness is the phase goal; perf/frame-pacing is Phase 5.
- Simulator and device can diverge (formats, ANGLE behavior); validate on both, prefer device for final sign-off.

### Phase 4 cross-cutting notes

- DUAL-TARGET INVARIANT: xrRender_GL builds for desktop GL 4.1 AND iOS GLES3 from the SAME sources. Never delete a desktop path; gate ES divergences behind XR_PLATFORM_APPLE_IOS (compile-time) or the runtime booleans GLAD_GL_ES_VERSION_3_0 / !GLAD_GL_ARB_separate_shader_objects. Breaking the Windows/Linux/macOS GL build is a regression.
- The GLAD loader at sdk/include/glad/gl.h + gl.c is a MERGED generator output (api='gl:compatibility=4.6,gles1:common=1.0,gles2=3.2'). It already declares gladLoadGLES2() (gl.h ~17814) and the runtime feature booleans GLAD_GL_ES_VERSION_3_0 (gl.h:6210) and GLAD_GL_ES_VERSION_3_2 (6214). So desktop-only symbols (glPolygonMode, glDrawElementsBaseVertex gl.h:12208, glBindFragDataLocation, glMapBuffer, glTexImage2DMultisample) still COMPILE on iOS — the danger is calling them at RUNTIME under ANGLE where they are absent/no-ops. Guard the calls, not just the symbols.
- GOOD NEWS discovered by grep: there are ZERO geometry shaders (.gs) in res/gamedata/shaders/gl (87 .vs, 199 .ps, 57 .s scripts, 101 .h). The SGS/GL_GEOMETRY_SHADER code paths exist but are never fed content, so GLES 3.0's lack of geometry shaders is a non-issue for assets; keep the code compiling but it is dead on iOS.
- Shaders ship as game ASSETS (res/gamedata/shaders/gl/*), compiled at runtime by CRender::shader_compile in rgl_shaders.cpp — not by the C++ build. Editing them does NOT require macOS CI; but they must be packaged into the iOS app bundle / synced to the sandbox and found via getShaderPath()=='gl\\' (r2.h:404). The active renderer on non-Windows is registered as renderer_r3 / id 4 (xrRender_GL.cpp:44), still using the 'gl\\' shader tree.
- Shaders have NO uniform blocks / UBOs — common.h uses loose global uniforms (m_WVP, m_V, timers, ...). The r_constants system binds them by individual uniform location. This is ES3.0-compatible; do not 'modernize' to std140 UBOs in this phase.
- GLES 3.0 GLSL rule that drives the whole shader effort: layout(location=...) is legal ONLY on vertex-shader inputs and fragment-shader outputs — NOT on vertex->fragment varyings. Desktop relies on ARB_separate_shader_objects to give varyings explicit locations; on ES we must match varyings BY NAME across stages, which forces regenerating the iostructs so VS-out names equal PS-in names (today they differ: v2p_flat_* vs p_flat_*).
- Keep MSAA (o.msaa), min-max shadow maps (o.minmax_sm / USE_MINMAX_SM), and gather/gatherTextureOffset (SM_4_1) DISABLED for initial bring-up. SM_4_1 is already #ifndef XR_PLATFORM_APPLE-guarded in rgl_shaders.cpp:415-420. sampler2DMS + glTexImage2DMultisample are GLES 3.1+, so MSAA render targets must stay off on 3.0.
- Wrap new GL calls in CHK_GL(...) (defined in glHW.h) for error logging, matching existing style. Use ZoneScoped/Msg/Log as elsewhere. Namespace everything in xray::render::RENDER_NAMESPACE.
- Bring-up order per project decision: SIMULATORARM64 first (no signing), then OS64 device via SideStore. Apple simulator GLES/ANGLE has quirks (some float-render formats) — validate FBO completeness at runtime rather than assuming.
- Depth format: glHW sets Caps.fDepth=D3DFMT_D24S8 -> GL_DEPTH24_STENCIL8 (ES3.0 core, OK). Caps.fTarget=D3DFMT_A8R8G8B8 maps to GL_RGBA8 already (glTextureUtils:18); the D3D 'A8R8G8B8' naming is cosmetic, storage is RGBA8 — no byte-order work needed for RTs, only for CPU-side BGRA sources (Theora, screenshots).
- Everything in Phase 4 is status=todo: the only Apple-related pre-existing code is the SM_4_1 disable guard and a couple XR_PLATFORM_APPLE #ifdefs in glSH_RT.cpp (GL_MAX_TEXTURE_SIZE vs GL_MAX_FRAMEBUFFER_WIDTH); no GLES port work has landed on ios-port yet.

---

<a id="phase-5"></a>

## Phase 5 — Touch / controls, UI adaptation, playability

> **Phase goal.** Turn the booting, rendering iOS build (Phases 3–4) into something a person can actually play on a phone. Add a touch input source to the event-source-agnostic IInputReceiver/ControllerState pipeline (virtual left-stick move, right-drag look, on-screen action buttons) without regressing the already-first-class MFi/Bluetooth controller path; adapt the keyboard/mouse-centric UI, inventory and console for finger targets and the iOS soft keyboard; make the app lifecycle correct and comfortable (no GL while backgrounded, CADisplayLink-based frame pacing, thermal/FPS caps, LOWMEMORY handling); and finish the distribution story — an iOS app-bundle CMake target with Info.plist + get-task-allow entitlement, optional LuaJIT-JIT enablement with a safe interpreter fallback, and an .ipa packaged in ios.yml for SideStore sideload. Definition of playable: obtain a level, then move/look/shoot and navigate menus end-to-end on a physical device.

### ⬜ 5.1 Touch input core: CTouchInput module wired into CInput::OnFrame reading SDL_FINGER*/SDL_MULTIGESTURE

`status: todo` · `effort: high`

**Goal.** Create the single central place that consumes iOS touch events and turns them into engine input, so every later task (sticks, buttons, look, UI, keyboard) plugs into one well-defined module instead of scattering SDL_FINGER handling across the codebase.

**Steps**

1. Create class CTouchInput (Touch.h/.cpp) owning: a std::vector/array of active TouchPoint{ SDL_FingerID id; Fvector2 startNorm, curNorm, prevNorm; u32 startTimeMs; enum Role{None,LeftStick,Look,Button,UI} role; int buttonId; } plus layout rectangles (in normalized 0..1 screen space) for the left virtual stick, the right look region, and each on-screen button.
2. Add a public entry point CTouchInput::Update(IInputReceiver* target) that CInput::OnFrame calls once per frame with cbStack.back(); it SDL_PeepEvents()es SDL_FINGERDOWN/SDL_FINGERUP/SDL_FINGERMOTION and SDL_MULTIGESTURE out of the queue (own range, so ControllerUpdate/KeyUpdate/MouseUpdate at xr_input.cpp:773-775 don't also see them).
3. In CInput::OnFrame (xr_input.cpp:761), inside the `if (Device.dwPrecacheFrame == 0 && !Device.IsAnselActive)` block, add — under #ifdef XR_PLATFORM_APPLE_IOS — a call to the touch module (e.g. `touch.Update(cbStack.back())`) alongside the existing ControllerUpdate/KeyUpdate/MouseUpdate. Keep it OUTSIDE ControllerUpdate's controller-gated early-return (xr_input.cpp:391-400) so touch works with no MFi controller attached.
4. Add a CTouchInput member to CInput (xr_input.h private section ~:180) constructed in the CInput ctor (xr_input.cpp:62). On non-Apple platforms compile it as an empty stub (either #ifdef the member out, or make Update() a no-op) so desktop builds are unaffected.
5. In Device_Initialize.cpp SetSDLSettings() (line ~20), under #ifdef XR_PLATFORM_APPLE_IOS set SDL_SetHint(SDL_HINT_TOUCH_MOUSE_EVENTS, "0") so raw touches don't double as mouse events, and SDL_SetHint(SDL_HINT_MOUSE_TOUCH_EVENTS, "0"). The module will re-synthesize mouse only where wanted (menus).
6. Force non-exclusive input on iOS: in the CInput ctor and GrabInput (xr_input.cpp:629-644) guard the SDL_SetRelativeMouseMode / SDL_SetWindowGrab calls with `#ifndef XR_PLATFORM_APPLE_IOS` (relative mode is unsupported on iOS and would break drag-look). Default exclusiveInput=false on iOS.
7. Decide the touch source model: introduce a `bool CInput::IsTouchAvailable()` (SDL_GetNumTouchDevices()>0) and a helper on the module to report whether a gameplay UI (level) or a menu is the current receiver, so 5.2/5.3 (gameplay) vs 5.5 (menu) branch cleanly. Convert normalized coords to pixels using Device.dwWidth/dwHeight and account for orientation.
8. Add Touch.cpp/Touch.h to src/xrEngine/CMakeLists.txt under target_sources_grouped NAME "Interfaces\\Input" (lines 261-269).

**Files**

- `src/xrEngine/Touch.h (new)`
- `src/xrEngine/Touch.cpp (new)`
- `src/xrEngine/xr_input.h:159 (InputType enum), :180-210 (CInput private members)`
- `src/xrEngine/xr_input.cpp:761 (CInput::OnFrame), :332 (ControllerUpdate), :142 (SetCurrentInputType), :62 (ctor), :629 (GrabInput)`
- `src/xrEngine/Device_Initialize.cpp:20 (SetSDLSettings — SDL hints)`
- `src/xrEngine/CMakeLists.txt:261-269 (Interfaces\Input source group)`

**Acceptance**

- [ ] iOS build compiles with the new module; desktop builds unchanged and still link (module is a no-op off-Apple).
- [ ] On a device, SDL_FINGERDOWN/UP/MOTION events are consumed by CTouchInput and are NOT also delivered as spurious SDL_MOUSEMOTION to MouseUpdate (verified via a temporary log of event counts).
- [ ] With no controller connected, touch code still executes every frame (proven by an on-screen debug overlay dot tracking the primary finger).

**Risks / easy to miss**

- ControllerUpdate/KeyUpdate/MouseUpdate use SDL_PeepEvents with explicit min/max event ranges; SDL_FINGER*/SDL_MULTIGESTURE fall in a different range — make sure your PeepEvents range doesn't overlap theirs or you'll steal/duplicate events.
- If SDL_HINT_TOUCH_MOUSE_EVENTS isn't disabled, menus may 'work' via emulation but gameplay look will jump — set the hint before SDL_Init/window creation for it to take effect.
- fingerId is only unique among currently-down fingers; do not assume monotonic ids across gestures.

### ⬜ 5.2 Gameplay virtual stick (move) + right-drag look synthesized through ControllerState/IInputReceiver

`status: todo` · `effort: medium`

**Goal.** Make the player able to walk and aim with thumbs by feeding the exact ControllerAxisState the real analog stick feeds, so existing move/look logic (deadzones, sensitivity, invert, sprint threshold) is reused verbatim.

**Steps**

1. Left virtual stick: when a finger goes down in the left-stick rect, record startNorm as the stick center (floating/relative stick), track curNorm each FINGERMOTION. Compute a Fvector2 offset (cur-start), scale to a unit vector, apply the SAME inner/outer deadzone normalization as CInput::ControllerUpdate's applyStickDeadZone lambda (xr_input.cpp:470-485) — or better, reuse psControllerStickInnerDeadZone/OuterDeadZone so behavior matches a real stick.
2. Emit stick as a controller axis: on first move call target->IR_OnControllerPress(XR_CONTROLLER_AXIS_LEFT, state); while held each frame call IR_OnControllerHold(XR_CONTROLLER_AXIS_LEFT, state); on finger up call IR_OnControllerRelease(XR_CONTROLLER_AXIS_LEFT, {}). CLevel::IR_OnControllerHold (Level_input.cpp:625) maps XR_CONTROLLER_AXIS_LEFT→GetBindedAction→kMOVE_AROUND→CActor::IR_OnControllerHold (ActorInput.cpp:533) which already sets mcFwd/mcBack/mcLStrafe/mcRStrafe/mcSprint from state. Sprint falls out for free (state.y<-0.95f, ActorInput.cpp:442/553).
3. Build ControllerAxisState with the {Fvector2, magnitude} ctor (xr_input.h:92) so magnitude drives the sprint/accel logic; clamp magnitude to 0..1.
4. Right-drag look: for a finger that goes down in the right region and is NOT on a button, on each FINGERMOTION compute pixel delta (dx,dy) = (cur-prev)*Device.dwWidth/Height, and call target->IR_OnMouseMove(dx, dy). CLevel::IR_OnMouseMove (Level_input.cpp:80) already routes to CActor::IR_OnMouseMove (ActorInput.cpp:349) which applies psMouseSens/invert — so touch look reuses the mouse-look pipeline and the player's sensitivity settings. (Alternative: synthesize XR_CONTROLLER_AXIS_RIGHT for a fixed-position look stick — keep the mouse-delta drag as default per the seed's 'right-drag look'.)
5. Multi-touch: allow the left stick, the look drag, and a fire button to be three simultaneous fingers. Route each fingerId to its Role at DOWN time and keep that role until UP (a finger that started as look never becomes the stick even if it slides into the left rect).
6. Add a per-frame 'stick released but no motion this frame' path: if the stick finger is held but stationary, still emit IR_OnControllerHold with the last state so held-forward keeps moving (the game expects Hold each frame; see the Hold-vs-Press distinction in CInput at xr_input.cpp:501-517).
7. Respect Device.Paused() — CLevel already gates on it; nothing extra needed, but ensure you stop emitting stick/look while a modal dialog is the top receiver (5.5 handles that by role=UI).

**Files**

- `src/xrEngine/Touch.cpp (new — synthesis logic)`
- `src/xrEngine/xr_input.h:72-143 (ControllerAxisState / ControllerState / axis enum)`
- `src/xrEngine/IInputReceiver.h:38-42 (IR_OnController* virtuals)`
- `src/xrGame/Level_input.cpp:552-659 (CLevel::IR_OnControllerPress/Hold/Release route axis via GetBindedAction), :80-108 (IR_OnMouseMove)`
- `src/xrGame/ActorInput.cpp:335-347 (OnAxisMove), :372-454 (kLOOK_AROUND/kMOVE_AROUND handling), :486-565 (IR_OnControllerHold)`

**Acceptance**

- [ ] On device in a loaded level: left thumb walks/strafes in all directions; pushing fully forward sprints; right thumb drags to aim; both work simultaneously with a fire tap.
- [ ] Move/look honor the user's gamepad deadzone/sensitivity and mouse invert cvars (changing gamepad_stick_inner_deadzone / psMouseInvert changes touch behavior identically to a real stick/mouse).
- [ ] Releasing the stick immediately stops movement (mstate clears).

**Risks / easy to miss**

- cam look via IR_OnControllerHold(kLOOK_AROUND) is scaled by Device.fTimeDeltaReal (ActorInput.cpp:517) — a positional drag through the mouse path (IR_OnMouseMove) is frame-independent and feels better; don't mix the two for look or sensitivity will be inconsistent.
- OnAxisMove for the stick isn't used for move (move is discrete mstate flags), but for look it is — verify you didn't accidentally route the move stick to kLOOK_AROUND.
- If you forget to call IR_OnControllerRelease on finger-up, movement latches on (actor keeps walking).

### ⬜ 5.3 On-screen action buttons (fire/aim/use/reload/jump/crouch/weapon-switch) mapped to game actions

`status: todo` · `effort: medium`

**Goal.** Give the player every essential verb as a tappable control, mapped through the existing keybind table so it obeys user rebinds and works in SP and MP.

**Steps**

1. Define a TouchButton table: { EGameActions action; Frect rectNorm; bool isHold; cpstr caption; } for at least kWPN_FIRE, kWPN_ZOOM (aim), kUSE, kWPN_RELOAD, kJUMP, kCROUCH, kNEXT_SLOT, kPREV_SLOT, kINVENTORY, kQUIT(pause/back). Keep it data-driven so 5.4 can load positions from config.
2. On FINGERDOWN inside a button rect: resolve dik = GetActionDik(action) (xr_level_controller.h:260). If dik==SDL_SCANCODE_UNKNOWN for that action, fall back to synthesizing the action id directly through a controller-button press. Call target->IR_OnKeyboardPress(dik). Mark the finger Role=Button, buttonId=index.
3. For hold-style buttons (fire, aim, crouch-hold): each frame the finger stays down, call IR_OnKeyboardHold(dik) (mirrors CInput::KeyUpdate's per-frame Hold at xr_input.cpp:321-323, which drives auto-fire and continuous crouch). On FINGERUP call IR_OnKeyboardRelease(dik).
4. For tap-style buttons (use, reload, weapon switch, inventory): emit Press on DOWN and Release on UP (single shot). kUSE release clears pickup mode (ActorInput.cpp:240) so always pair press+release.
5. Route through the CURRENT receiver (cbStack.back()), not directly to CActor: this makes buttons work whether the level, a vehicle holder (m_holder path in ActorInput.cpp:79/398), or a spectator is active, and makes MP fire (special-cased in ActorInput.cpp:56/384) work.
6. Fire correctness: CActor::IR_OnKeyboardPress(kWPN_FIRE) has MP lookout gating (ActorInput.cpp:58) — because you go through the normal keyboard path this is handled for free; do not shortcut it.
7. Guard against double-trigger: a finger that started on a button must not also be interpreted as the look drag (role locked at DOWN, per 5.1/5.2). Buttons take priority over the look region when rects overlap.

**Files**

- `src/xrEngine/Touch.cpp/.h (button hit-test + synthesis)`
- `src/xrEngine/xr_level_controller.h:12-199 (EGameActions: kWPN_FIRE, kWPN_ZOOM, kUSE, kWPN_RELOAD, kJUMP, kCROUCH, kNEXT_SLOT/kPREV_SLOT, kINVENTORY, kQUIT), :260-261 (GetActionDik/GetBindedAction)`
- `src/xrGame/Level_input.cpp:122 (CLevel::IR_OnKeyboardPress), :445 (Release), :477 (Hold)`
- `src/xrGame/ActorInput.cpp:37 (CActor::IR_OnKeyboardPress fire path)`

**Acceptance**

- [ ] Tapping FIRE shoots (full-auto while held); AIM toggles/holds ADS; USE picks up/interacts; RELOAD reloads; JUMP/CROUCH work; weapon-switch cycles slots; INVENTORY opens the actor menu; PAUSE/BACK opens the menu.
- [ ] Rebinding an action in the keybind UI changes what the on-screen button does (proves it goes through GetActionDik, not a hardcoded scancode).
- [ ] Fire button works in both SP and MP (no assert on the MP lookout path).

**Risks / easy to miss**

- Some actions (kMOVE_AROUND/kLOOK_AROUND) are gamepad-axis actions with no keyboard dik — never route a button to those; buttons are discrete actions only.
- kUSE special path: CActor::IR_OnKeyboardPress skips m_holder for kUSE (ActorInput.cpp:79) — going through the real path preserves that; don't reimplement ActorUse().
- Holding a hold-button while opening a dialog could latch fire; on receiver change (iCapture at xr_input.cpp:705) synthesize releases for all held touch buttons.

### ⬜ 5.4 On-screen controls rendering, contextual visibility, layout config + tuning cvars

`status: todo` · `effort: high`

**Goal.** Draw the virtual gamepad only when it makes sense (in-game, no active controller, not in a modal/inventory/cutscene), let the user tune size/opacity/deadzone, and persist a layout — without wiring up new texture assets or touching hundreds of UI files.

**Steps**

1. Render the overlay using ImGui's foreground/background draw list (ImGui::GetBackgroundDrawList()->AddCircle/AddCircleFilled/AddText) inside the existing ImGui frame — the engine already builds an ImGui frame each tick (device.cpp FrameMove ImGui::NewFrame at :476, ImGui::Render at device.cpp:246). This needs NO game texture assets and renders through whatever backend (ANGLE/GLES) Phase 4 selects. Draw: left stick base+thumb (relative to current finger), right-look hint ring, and each button as a filled circle with a glyph/caption.
2. Draw only when appropriate: expose a predicate CTouchInput::ShouldShowGameControls() = (touch available) && (current receiver is the level/gameplay) && (!Device.Paused()) && (!inventory/pda/dialog open) && (!pInput->IsControllerAvailable() OR no controller input recently). Hook 'inventory/dialog open' off CurrentGameUI()->TopInputReceiver() (used at Level_input.cpp:175) — expose a tiny engine-visible query or a bool the game sets on dialog open/close.
3. Auto-hide when an MFi controller becomes active: reuse CInput::IsCurrentInputTypeController() (xr_input.h:254) — when a real controller sends events, hide touch controls and stop synthesizing (dovetails with Task 5.7). When a touch happens, show them again (mirror psControllerCursorAutohideTime autohide semantics).
4. Register tuning cvars in xr_ioc_cmd.cpp right after the gamepad block (line 840), following CMD3/CMD4: touch_enable (Mask/bool), touch_opacity (Float 0..1), touch_scale (Float 0.5..2), touch_left_deadzone (Float), touch_look_sens (Float), touch_show_when_controller (Mask). Declare the backing ps* variables in IInputReceiver.h alongside psController* (lines 49-58) and define them in xr_input.cpp (near :43-53).
5. Layout: store button/stick rects in normalized coords with sane defaults for a phone (stick bottom-left, fire bottom-right, jump/crouch/use/reload/aim clustered right, weapon-switch top-right, pause top-left). Optionally load overrides from res/gamedata/configs/touch_default.ltx via pSettings so modders/users can reposition without recompiling. Respect safe-area insets (notch/home-indicator) — inset the outermost controls.
6. Make hit rects larger than the drawn glyph (finger-friendly ~44pt minimum) and keep draw vs hit rect separate so visuals stay clean while targets stay forgiving.
7. Recompute pixel layout on SDL_WINDOWEVENT_SIZE_CHANGED / orientation change (Device.cpp ProcessEvent:359, DISPLAYEVENT_ORIENTATION:315) so rotating the device re-lays-out controls.

**Files**

- `src/xrEngine/Touch.cpp (render), src/xrEngine/device.cpp:226-259 (DoRender — ImGui overlay is drawn here via m_imgui_render->Render)`
- `src/xrEngine/Device_imgui.cpp (ImGui integration)`
- `src/xrEngine/xr_ioc_cmd.cpp:830-840 (register touch_* cvars next to gamepad_* ones)`
- `src/xrEngine/IInputReceiver.h:45-58 (ps* extern cvars pattern)`
- `res/gamedata/configs/touch_default.ltx (new default layout, optional)`

**Acceptance**

- [ ] Controls are visible and usable in-game, hidden in the main menu, inventory, PDA, dialogs, and while paused.
- [ ] Connecting an MFi controller hides the touch overlay within ~1s; touching the screen brings it back.
- [ ] touch_opacity/touch_scale/touch_enable change the overlay live and persist across restarts (written to user.ltx).
- [ ] Rotating the device / changing resolution re-lays-out controls without overlapping the notch or home indicator.

**Risks / easy to miss**

- If the overlay is drawn via the game's own xrUICore instead of ImGui, you must wire texture atlases and it renders per-UI-state — the ImGui background draw list is the low-asset, always-available central path; prefer it.
- ImGui overlay must be draw-only: don't let ImGui capture the touch as a window interaction (use draw lists, not ImGui::Button, or the input goes to ImGui not the game).
- Safe-area insets on iOS require querying the UIWindow safeAreaInsets (via SDL_GetWindowBordersSize is not it) — may need a tiny Obj-C shim or SDL_metal/UIKit call; account for it or controls hide under the notch.

### ⬜ 5.5 Menu / inventory / list UI touch adaptation (tap, drag-scroll, drag-drop) keeping controller nav first-class

`status: todo` · `effort: high`

**Goal.** Let the player drive menus, options, the load/new-game flow and the inventory with a finger, so the 'navigate menus' and 'obtain a level' parts of playable actually work — while not regressing keyboard/mouse or MFi controller navigation.

**Steps**

1. Menu tap = mouse click: when the current receiver is a menu/dialog (not the level), route a touch DOWN/UP to IR_OnMousePress(MOUSE_1)/IR_OnMouseRelease(MOUSE_1) at the finger position, and move the UI cursor there first via IR_OnMouseMove or by setting absolute pos. UICursor already supports absolute positioning through iGetAsyncMousePos/m_bound_to_system_cursor (UICursor.cpp:108-113); ensure that path is taken on iOS (it is, since exclusive/relative mode is off per 5.1).
2. Position the cursor at the finger before the click: because iOS has no hover, on FINGERDOWN set the UI cursor to the finger's absolute position (add an engine call to place the cursor, or synthesize an absolute IR_OnMouseMove) so the click lands where the finger is, not where the cursor last was.
3. Drag-scroll for lists (inventory grid, level list, options scrollboxes): translate a finger drag over a scrollable widget into scroll wheel events (IR_OnMouseWheel, routed at Level_input.cpp:46 / UI IR_UIOnMouseWheel) or into the widget's scrollbar drag. Use SDL_MULTIGESTURE only if you want pinch; single-finger vertical drag over a list = scroll.
4. Inventory drag-drop: UICellItem/UIDragDropReferenceList already implement press-move-release drag with the mouse. Since touch synthesizes MOUSE_1 down + moves + up, drag-drop should work once the cursor follows the finger — verify item pickup/drop and add a small drag threshold so a tap isn't misread as a micro-drag.
5. Keep MFi controller UI nav working: the UI already handles controller navigation via IR_UIOnControllerPress/Hold and the kUI_* contextual actions (xr_level_controller.h:145-173) and CUIDialogWnd::OnControllerAction (UIDialogWnd.cpp:29). Do NOT remove or shadow that path — touch and controller nav must coexist; only ensure the on-screen touch overlay is suppressed in menus (5.4) so it doesn't cover buttons.
6. New-game/obtain-a-level flow: confirm the main menu (UIMMShniaga) 'New Game'/'Load' buttons and the level/slot lists are reachable and confirmable by tap end-to-end, since that is the literal first half of the playable definition.
7. Add an on-screen way to reach the console/quit that doesn't need a keyboard (a PAUSE/menu button from 5.3 → main_menu), because kCONSOLE/kQUIT are keyboard-bound.

**Files**

- `src/xrUICore/Cursor/UICursor.cpp:90-117 (SetUICursorPosition/UpdateCursorPosition)`
- `src/xrGame/ui/UIDialogWnd.cpp:20-50, src/xrGame/UIDialogHolder.cpp (controller/mouse routing)`
- `src/xrGame/ui/UIActorMenu*.cpp, src/xrGame/ui/UICellItem.cpp, UIDragDropReferenceList (inventory drag-drop)`
- `src/xrGame/ui/UIMMShniaga.cpp (main menu buttons), UIMapList (level/new-game list)`
- `src/xrGame/Level_input.cpp:80-108 (IR_OnMouseMove→UI), :46-74 (wheel)`

**Acceptance**

- [ ] From cold start on device: tap through main menu → New Game (or Load) → spawn into a level using only touch.
- [ ] Inventory: open actor menu, drag an item between slots, drop it, close — all by finger.
- [ ] Long option/keybind lists scroll by dragging; buttons and checkboxes toggle by tap.
- [ ] A paired MFi controller can still navigate every menu (touch changes didn't break controller UI nav).

**Risks / easy to miss**

- UICursor.UpdateCursorPosition branches on IsExclusiveMode()/m_bound_to_system_cursor (UICursor.cpp:102) — if exclusive/relative mode isn't forced off on iOS (5.1), the cursor uses relative deltas and menu taps land in the wrong place.
- Tap-vs-drag ambiguity: without a movement threshold, every tap becomes a tiny drag and drag-drop mis-fires; pick ~10px threshold.
- Some dialogs WorkInPause (UIDialogWnd.cpp:46) — make sure menu touch routing doesn't get gated by Device.Paused() the way gameplay input is.

### ⬜ 5.6 Soft keyboard / text input routing (console, chat, save names, MP name) via SDL_StartTextInput

`status: todo` · `effort: medium`

**Goal.** Let the player type where the game needs text (console commands to obtain a level, save names, multiplayer name/chat) using the iOS on-screen keyboard, since there's no physical keyboard.

**Steps**

1. Verify the text pipeline end to end on iOS: EnableTextInput() (xr_input.cpp:666) already calls SDL_StartTextInput() which raises the iOS soft keyboard; SDL_TEXTINPUT events are dispatched to cbStack.back()->IR_OnTextInput (xr_input.cpp:309-313) → CLevel/Console/line editor. Confirm each text field actually calls pInput->EnableTextInput() on focus and DisableTextInput() on blur (grep the UI edit widgets and the console show path).
2. Console on touch: the console is normally opened by kCONSOLE (grave key). Provide a touch route to open it (debug button, or a menu entry) and confirm that when the console shows it calls EnableTextInput so the soft keyboard appears, and that typed characters land in the input line (IR_OnTextInput → text_editor).
3. Keyboard show/hide + layout: when the iOS keyboard is up it covers the bottom of the screen. Use SDL_SetTextInputRect() to tell iOS where the text field is so it scrolls into view, and ensure the console/edit box is drawn above the keyboard (offset by keyboard height — obtainable via SDL or a UIKit notification shim).
4. Return/submit + dismiss: map the software Return to submit (the line editor already handles Enter), and provide a way to dismiss the keyboard (tap outside / a Done button → DisableTextInput → SDL_StopTextInput).
5. textInputCounter correctness: EnableTextInput/DisableTextInput are ref-counted (xr_input.cpp:668/679). Make sure paired calls are balanced when a dialog opens the keyboard then another steals focus, or the keyboard sticks/leaks; the frame-skip guard at KeyUpdate:291-312 relies on textInputCounter changing on target switch.
6. MP name/chat and save-name dialogs: confirm those edit boxes bring up the keyboard and accept UTF-8 (IR_OnTextInput takes pcstr UTF-8; StringFromUTF8 is used elsewhere).

**Files**

- `src/xrEngine/xr_input.cpp:666-693 (EnableTextInput/DisableTextInput/IsTextInputEnabled — already call SDL_StartTextInput/StopTextInput), :309-313 (SDL_TEXTINPUT dispatch)`
- `src/xrEngine/XR_IOConsole.cpp:414 (IR_OnTextInput), src/xrEngine/line_edit_control.cpp (line editor)`
- `src/xrGame/Level_input.cpp:536 (IR_OnTextInput routing)`
- `src/xrGame/ui/UIChatWnd.cpp, src/xrUICore text edit widgets (EnableTextInput call sites)`

**Acceptance**

- [ ] Opening the console on device shows the iOS keyboard; typing a command (e.g. to load/obtain a level) works and Enter executes it.
- [ ] The active text field is not hidden behind the keyboard (SDL_SetTextInputRect scrolls it into view).
- [ ] Save-name and MP-name/chat fields accept typed text and dismiss the keyboard on submit; no stuck keyboard after closing a dialog.

**Risks / easy to miss**

- If a widget shows a caret but never calls EnableTextInput, no keyboard appears on iOS — the desktop path relies on always-on hardware keyboard, so missing EnableTextInput calls are latent bugs only iOS exposes.
- Unbalanced Enable/Disable leaves SDL in text-input mode (keyboard won't dismiss) or drops characters (textInputCounter mismatch → the skip at KeyUpdate:310 eats input).
- Keyboard height/inset needs UIKit info SDL may not surface directly; a small Obj-C notification observer may be required.

### ⬜ 5.7 MFi / Bluetooth controller first-class on iOS (SDL GameController + Info.plist + haptics)

`status: todo` · `effort: medium`

**Goal.** Keep hardware controllers a premium path on iOS: full stick/trigger/button/gyro support (already coded) plus rumble, controller-aware UI, and the Info.plist keys iOS needs to recognize game controllers.

**Steps**

1. Confirm SDL is initialized with SDL_INIT_GAMECONTROLLER (and SDL_INIT_HAPTIC/SENSOR) on iOS so SDL_NumJoysticks()/SDL_IsGameController() (used in CInput ctor xr_input.cpp:95) actually enumerate MFi/DualShock/Xbox BT pads. The existing ControllerUpdate already handles add/remove/axes/buttons/gyro — no game-logic change needed.
2. Add the required Info.plist keys (implemented in Task 5.9 but tracked here): GCSupportsControllerUserInteraction=YES, GCSupportsMultipleMicroGamepads (if desired), and GCSupportedGameControllers as appropriate; UIApplicationSupportsIndirectInputEvents=YES so pointer/hover and controller-driven UI events flow.
3. Rumble: CInput::Feedback (xr_input.cpp:804) calls SDL_GameControllerRumble / SDL_GameControllerRumbleTriggers. Verify SDL's iOS backend maps these to Core Haptics for MFi controllers that support it; gate gracefully if unsupported (SDL returns <0 → ignore).
4. Gyro aim: OpenController enables SDL_SENSOR_GYRO (xr_input.cpp:129-131) and ControllerUpdate feeds IR_OnControllerAttitudeChange (xr_input.cpp:446-456 → CActor:567). Confirm iOS delivers SDL_CONTROLLERSENSORUPDATE for controllers with gyro (DualSense/DualShock); leave psControllerSensorSens cvars as-is.
5. Controller-vs-touch arbitration: when a controller sends events, SetCurrentInputType(Controller) runs (xr_input.cpp:397); use IsCurrentInputTypeController() to suppress the touch overlay (Task 5.4) and stop touch synthesis, and vice-versa. Keep the last-used input source authoritative.
6. Verify hot-plug: connecting/disconnecting a BT controller mid-game is handled by SDL_CONTROLLERDEVICEADDED/REMOVED (xr_input.cpp:370-381) — smoke-test on device.

**Files**

- `src/xrEngine/xr_input.cpp:95-133 (OpenController/SDL_GameControllerOpen + gyro), :332-518 (ControllerUpdate), :804-834 (Feedback/rumble)`
- `src/xrEngine/xr_input.h:251-255 (IsControllerAvailable/IsCurrentInputTypeController)`
- `misc/ios/<app>/Info.plist (Task 5.9 — GC keys)`
- `SDL init flags (SDL_INIT_GAMECONTROLLER / SDL_INIT_HAPTIC) wherever SDL_Init is called`

**Acceptance**

- [ ] A paired MFi/Bluetooth controller drives move/look/fire/menus on device with correct deadzones and invert (identical to desktop controller behavior).
- [ ] Rumble fires on weapon/impact events where the desktop build rumbles (or silently no-ops if the pad lacks haptics).
- [ ] Connecting a controller hides the touch overlay; disconnecting restores it.
- [ ] Controllers are recognized by iOS (Info.plist GC keys present) and survive background/foreground.

**Risks / easy to miss**

- Without GCSupportsControllerUserInteraction/UIApplicationSupportsIndirectInputEvents in Info.plist, iOS may intercept controller input for its own UI or not deliver it to SDL.
- SDL haptics on iOS requires the controller's Core Haptics engine; SDL_GameControllerRumble may return success but do nothing on some pads — don't assert on the return.
- SDL_INIT_SENSOR vs per-controller sensor enable: gyro needs the sensor subsystem AND SDL_GameControllerSetSensorEnabled (already called) — both must succeed.

### ⬜ 5.8 App lifecycle, frame pacing, thermals & LOWMEMORY: no GL while backgrounded, CADisplayLink pacing

`status: todo` · `effort: high`

**Goal.** Make the app a well-behaved iOS citizen so it isn't killed by the GPU watchdog or the OS: never touch GL while suspended, pace frames off the display link instead of a busy Sleep, cap FPS for battery/thermals, and free memory on pressure.

**Steps**

1. Register an SDL event watch EARLY (SDL_AddEventWatch, e.g. in CApplication ctor or Device Initialize) that reacts SYNCHRONOUSLY to SDL_APP_WILLENTERBACKGROUND: set a global 'backgrounded' flag, call GEnv.Render to glFinish/flush, and set Device.b_is_Active=false via OnWindowActivate(m_sdlWnd,false). This MUST happen in the watch callback, not the normal pump (x_ray.cpp:372), because iOS suspends the app right after and SDL_PeepEvents won't run in time.
2. Handle SDL_APP_DIDENTERBACKGROUND (stop all rendering; ensure no GL calls until foreground), SDL_APP_WILLENTERFOREGROUND / SDL_APP_DIDENTERFOREGROUND (clear the flag, OnWindowActivate(true), possibly re-create/rebind the GL/EAGL surface if iOS discarded it), SDL_APP_TERMINATING (clean shutdown → Device.Shutdown), and SDL_APP_LOWMEMORY (see memory step).
3. Gate rendering on the backgrounded flag AND b_is_Active: DoRender already checks b_is_Active before RenderBegin (device.cpp:236) — additionally short-circuit ProcessFrame/DoRender when backgrounded so not even ImGui/UpdateViewports runs GL. Add an assert/log if RenderBegin is reached while backgrounded to catch regressions.
4. Frame pacing on iOS: the busy-wait Sleep(updateDelta-frameTime) in ProcessFrame (device.cpp:298-299) wastes battery and fights the display link. Under #ifdef XR_PLATFORM_APPLE_IOS, drop the manual Sleep and let SDL_iPhoneSetAnimationCallback (set up in Phase 3) drive tick cadence; set the CADisplayLink interval from a target-FPS cvar (30/60/120). Keep the desktop Sleep path unchanged.
5. Thermals/battery: default the iOS FPS cap conservatively (e.g. 60, or 30 in menu) reusing ps_fps_limit / ps_fps_limit_in_menu (xr_ioc_cmd.cpp:764-765). Optionally lower the cap when ProcessInfo thermalState is Serious/Critical (small Obj-C shim reading NSProcessInfo.thermalState) — expose as a cvar-driven policy.
6. Also handle the device-lost Sleep(33) (device.cpp:46) and precache Sleep(100) so they don't spin while backgrounded.
7. Memory under LOWMEMORY: on SDL_APP_LOWMEMORY, drop non-essential caches — call the same paths used after precache (ResourcesDestroyNecessaryTextures / Memory.mem_compact, seen at device.cpp:83-84), flush sound caches, and free the touch overlay's transient state. Log mem_usage before/after.
8. Verify the GL context / default framebuffer is re-validated on foreground (iOS may invalidate the drawable); coordinate with the Phase 4 GL/ANGLE surface owner.

**Files**

- `src/xrEngine/x_ray.cpp:367-442 (CApplication::Run main loop — SDL_APP_* handling / event watch)`
- `src/xrEngine/device.cpp:261-303 (ProcessFrame Sleep pacing), :226-259 (DoRender b_is_Active guard :236), :550-589 (OnWindowActivate → seqAppActivate/Deactivate), :34-60 (RenderBegin), :46 (Sleep(33) device-lost)`
- `src/xrEngine/xr_ioc_cmd.cpp:764-765 (ps_fps_limit / ps_fps_limit_in_menu)`
- `src/xrEngine/Device_create.cpp / wherever SDL window+GL context live`

**Acceptance**

- [ ] Backgrounding the app (home/app-switcher) then returning does NOT crash (no 0x8badf00d GPU watchdog kill); a log shows rendering stopped on WILLENTERBACKGROUND and resumed on DIDENTERFOREGROUND.
- [ ] No GL/EAGL call executes while backgrounded (instrumented assert never trips over many background/foreground cycles).
- [ ] On device the app holds a steady capped framerate without a busy-wait spinning a core (battery/thermal sane); changing the FPS cap cvar changes cadence.
- [ ] A simulated memory warning frees caches (mem_usage drops in the log) and the app keeps running.

**Risks / easy to miss**

- The single most common iOS port crash: issuing GL after WILLENTERBACKGROUND. The event-watch (synchronous) path is mandatory — relying on the normal SDL pump WILL crash intermittently.
- seqAppActivate/Deactivate (device.cpp:579/585) also pause the TaskScheduler and sound; make sure background/foreground drives them exactly once (b_is_InFocus guard at :573) or you double-pause.
- Re-entering foreground may require rebuilding the EAGL/ANGLE surface — if Phase 4 caches the framebuffer, a stale drawable will render black or crash.
- Dropping the Sleep entirely without CADisplayLink pacing (if Phase 3 tick isn't in place yet) will free-run the CPU — keep a fallback cap.

### ⬜ 5.9 iOS application bundle target: Info.plist, entitlements (get-task-allow), launch storyboard, resource bundling

`status: todo` · `effort: medium`

**Goal.** Produce a real, installable OpenXRay .app for iOS (not the engine-free smoketest): correct orientation/fullscreen/status-bar behavior, controller & indirect-input keys, a launch screen, sandbox-correct resources, and the get-task-allow entitlement SideStore/JIT require.

**Steps**

1. Create an iOS app-bundle CMake target (MACOSX_BUNDLE) that links the full engine/game static libs into one binary (the static single-binary path already exists: BUILD_SHARED_LIBS=OFF/XRAY_STATIC_BUILD per doc/iOS-Port.md). Model target properties on misc/ios/smoketest/CMakeLists.txt but flip signing on for device.
2. Author Info.plist.in with: CFBundleIdentifier (e.g. io.github.openxray), CFBundleShortVersionString/Version, LSRequiresIPhoneOS=YES, UIRequiredDeviceCapabilities=arm64, MinimumOSVersion=15.0. Orientation: UISupportedInterfaceOrientations = Landscape Left+Right (game is landscape); UIRequiresFullScreen=YES. Status bar: UIStatusBarHidden=YES, UIViewControllerBasedStatusBarAppearance=NO. Home indicator: prefersHomeIndicatorAutoHidden via the view controller if possible.
3. Add controller/input keys: GCSupportsControllerUserInteraction=YES, UIApplicationSupportsIndirectInputEvents=YES (Task 5.7). Add ITSAppUsesNonExemptEncryption=NO to avoid export-compliance prompts. Add UILaunchStoryboardName pointing at LaunchScreen.storyboard.
4. Create OpenXRay.entitlements with get-task-allow=YES (required: sideloaded apps carry it; also gates optional JIT in Task 5.10). Wire it via XCODE_ATTRIBUTE_CODE_SIGN_ENTITLEMENTS.
5. Provide a minimal LaunchScreen.storyboard and AppIcon set (asset catalog) so the app launches without a black/placeholder screen and passes bundle validation.
6. Resource/sandbox paths: the engine reads assets from a read-only bundle and writes to a writable dir. Ensure game data is either bundled as Resources or expected under Documents; confirm LocatorAPI path setup (doc references src/xrCore/LocatorAPI.cpp, SDL_GetPrefPath writable + bundle read-only) resolves on device. (Path plumbing is Phase 3; here just make the bundle lay resources out where that code expects, and mark large data as not-backed-up if needed.)
7. Set XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY (1,2 for iPhone+iPad), ENABLE_BITCODE=OFF (already in ios.yml:57), and a deployment target of 15.0 (ios.yml:56).

**Files**

- `misc/ios/OpenXRay/CMakeLists.txt (new app target) or extend misc/ios/smoketest/CMakeLists.txt`
- `misc/ios/OpenXRay/Info.plist.in (new)`
- `misc/ios/OpenXRay/OpenXRay.entitlements (new)`
- `misc/ios/OpenXRay/LaunchScreen.storyboard (new) + AppIcon assets`
- `cmake/toolchains/ios.toolchain.cmake (already vendored, Phase 1)`
- `src/ top-level CMakeLists / xray_re app exe target (link the engine into the bundle)`

**Acceptance**

- [ ] CI produces an OpenXRay.app bundle (not just smoketest) whose binary is arm64 iOS (verified by the existing lipo/vtool inspect step pattern in ios.yml:64-73).
- [ ] Info.plist has correct landscape/fullscreen/status-bar keys and the GC/indirect-input keys; the app launches to a landscape full-screen surface with a launch screen.
- [ ] The entitlements file contains get-task-allow; the bundle validates as an installable structure.
- [ ] Game data resolves from the bundle/sandbox on device (engine finds fsgame paths and boots).

**Risks / easy to miss**

- Orientation mismatch (portrait Info.plist vs landscape render) yields a rotated/letterboxed view; SDL respects Info.plist orientations, so set them correctly.
- Missing LaunchScreen.storyboard makes iOS render the app at a legacy small resolution (no full native resolution) — this silently caps your render size.
- Static link of the whole engine into one bundle can hit duplicate-symbol / -ObjC / --whole-archive issues; may need XCODE linker flags. The single-binary path is proven for macOS CI but iOS linker specifics differ.
- Large bundled game assets can trip iCloud-backup and App-thinning; mark appropriately.

### ⬜ 5.10 On-device signing path, optional LuaJIT-JIT enablement with interpreter fallback, and .ipa packaging for SideStore

`status: todo` · `effort: high`

**Goal.** Ship a distributable artifact: an unsigned/dev-signed .ipa that SideStore re-signs on device with get-task-allow, plus an optional full-JIT LuaJIT path (for StikDebug/JIT-enabled installs) that never hard-crashes and falls back to the always-working interpreter.

**Steps**

1. Extend ios.yml to build & package the REAL app (Task 5.9) into an unsigned .ipa: reuse the existing Payload/<App>.app zip recipe (ios.yml:75-87) but point APP at OpenXRay.app. Unsigned is exactly what SideStore/AltStore want (they re-sign on-device with the user's Apple ID) — the header comment at ios.yml:8-9 already states this.
2. Keep the device build unsigned in CI (XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO for the CI path) but ensure the entitlements (get-task-allow) travel with the bundle so SideStore's re-sign preserves them. Document the SideStore install steps in doc/iOS-Port.md.
3. LuaJIT baseline: keep interpreter mode as default (LuaJIT's interpreter is hand-written asm; per doc/iOS-Port.md it 'always works'). Do NOT set LUAJIT_DISABLE_JIT unconditionally — instead build a JIT-capable LuaJIT for iOS and gate JIT at RUNTIME.
4. Optional full JIT: LuaJIT on iOS needs W^X-aware mcode allocation. Verify/patch Externals/LuaJIT/src/lj_mcode.c to use MAP_JIT + pthread_jit_write_protect_np (Apple's toggle) and mmap(PROT_READ|PROT_WRITE|PROT_EXEC) via the iOS path. Provide a config/cvar to enable JIT only when the process has get-task-allow AND a JIT provider (StikDebug/JitStreamer) has enabled RWX.
5. Runtime fallback so it NEVER hard-crashes: at LuaJIT init in xrScriptEngine, attempt to enable JIT (luaJIT_setmode LUAJIT_MODE_ON). Wrap the first trace/mcode allocation so that if RWX allocation fails (no JIT entitlement/provider), catch it and call luaJIT_setmode(...LUAJIT_MODE_OFF) to run as pure interpreter — log 'JIT unavailable, running interpreter' rather than aborting. Test both: JIT-enabled install and plain sideload.
6. Note iOS-version caveats (doc/iOS-Port.md:47-48): JIT is re-enabled per cold start and is broken/limited on iOS 26+ — make interpreter the safe default and JIT strictly opt-in.
7. Add a CI sanity step: confirm the .ipa contains the entitlements and a valid arm64 binary, and (optionally) run a headless simulator smoke of the interpreter path (simulator can't JIT, which is exactly the fallback case to validate).

**Files**

- `.github/workflows/ios.yml:32-94 (matrix + package/upload .ipa steps)`
- `misc/ios/OpenXRay/OpenXRay.entitlements (get-task-allow)`
- `Externals/LuaJIT-proj/CMakeLists.txt:30 (LUAJIT_DISABLE_JIT option), :108-110`
- `Externals/LuaJIT/src/lj_arch.h, lj_mcode.c (iOS MAP_JIT / pthread_jit_write_protect_np / mprotect RWX)`
- `src/xrScriptEngine/* (LuaJIT init — runtime JIT on/off + fallback)`

**Acceptance**

- [ ] ios.yml uploads an OpenXRay device .ipa artifact containing OpenXRay.app with get-task-allow entitlement; the simulator job still passes as a compile/interpreter check.
- [ ] The app runs scripts correctly in interpreter mode with NO JIT entitlement (plain SideStore install) — no crash, game logic works.
- [ ] With a JIT provider enabled, JIT turns on; if RWX allocation fails, the engine logs a fallback and continues in interpreter mode instead of crashing.
- [ ] doc/iOS-Port.md documents the SideStore sideload + optional JIT-enable steps.

**Risks / easy to miss**

- Interpreter-only must be rock solid: many STALKER scripts run hot — validate performance is acceptable without JIT before relying on it.
- lj_mcode.c W^X on iOS: forgetting pthread_jit_write_protect_np(false) before writing mcode / true after → EXC_BAD_ACCESS the instant a trace compiles; this is the classic iOS-JIT crash.
- get-task-allow present in the bundle but stripped by re-sign would silently disable JIT — verify SideStore preserves it.
- Do not enable JIT by default; iOS 17.4+ JIT via StikDebug is per-launch and fragile, and iOS 26+ limits it — a JIT-default build will crash for most sideloaders.
- App Store is explicitly NOT the target (IP + JIT rules); keep distribution docs SideStore-only to avoid confusion.

### Phase 5 cross-cutting notes

- ALL Phase 5 items are status=todo. Only Phase 1 (leetal toolchain cmake/toolchains/ios.toolchain.cmake, misc/ios/smoketest, .github/workflows/ios.yml, and the XR_PLATFORM_APPLE_IOS macro in src/Common/Platform.hpp:32) is done on ios-port. Nothing touch/UI/lifecycle exists yet — there are ZERO SDL_FINGER*/SDL_MULTIGESTURE references in src/ (only in vendored imgui).
- Guard every iOS-only code path with #ifdef XR_PLATFORM_APPLE_IOS (defined in src/Common/Platform.hpp). Prefer central/systemic hooks (CInput::OnFrame, CLevel/CMainMenu IInputReceiver dispatch, Device lifecycle) over editing hundreds of per-widget UI files.
- Event-source-agnostic is the whole design pattern: the game NEVER learns input came from touch. Touch code synthesizes the SAME calls the real devices make on cbStack.back(): IR_OnControllerPress/Hold/Release(XR_CONTROLLER_AXIS_LEFT/RIGHT, ControllerAxisState) for sticks, IR_OnMouseMove(dx,dy)/IR_OnMousePress for look & UI, IR_OnKeyboardPress/Release(dik) for buttons. Reuse GetActionDik()/GetBindedAction() (xr_level_controller.h:260-261) so touch obeys the user's existing keybinds/deadzones/invert settings.
- On iOS SDL by default synthesizes mouse events from touches (SDL_HINT_TOUCH_MOUSE_EVENTS) with which==SDL_TOUCH_MOUSEID, and relative-mouse/global-mouse is unavailable (SDL_HAS_CAPTURE_AND_GLOBAL_MOUSE==0, see xr_input.h:9). Decide the policy ONCE and centrally: set SDL_HINT_TOUCH_MOUSE_EVENTS="0" in Device_Initialize.cpp SetSDLSettings() and drive everything from SDL_FINGER* yourself, OR keep emulation for menus only. Also force exclusiveInput=false / never call SDL_SetRelativeMouseMode(true) on iOS (CInput ctor / GrabInput at xr_input.cpp:629) — relative mode is a no-op there and breaks look.
- SDL_FINGER* coordinates are NORMALIZED 0..1 (event.tfinger.x/y, dx/dy), NOT pixels. Multiply by Device.dwWidth/dwHeight (or m_rcWindowClient) and account for device orientation and safe-area insets. event.tfinger.fingerId is the persistent touch id you must track to route a finger to the stick vs a button vs look for its whole lifetime.
- iOS background/foreground events (SDL_APP_WILLENTERBACKGROUND / SDL_APP_DIDENTERBACKGROUND / SDL_APP_WILLENTERFOREGROUND / SDL_APP_DIDENTERFOREGROUND / SDL_APP_LOWMEMORY / SDL_APP_TERMINATING) are delivered SYNCHRONOUSLY through an SDL_AddEventWatch/SDL_SetEventFilter callback during the UIApplication delegate call — the normal SDL_PeepEvents pump in CApplication::Run (x_ray.cpp:372) runs too late (the app is already suspended). You MUST register an event watch to glFinish + stop rendering on WILLENTERBACKGROUND. Making ANY GL/EAGL call while backgrounded is a guaranteed GPU-watchdog kill (0x8badf00d).
- currentInputType is a two-value enum (KeyboardMouse, Controller) at xr_input.h:159. Touch is a third source: either add InputType::Touch or (simpler) treat touch as its own overlay that runs unconditionally in OnFrame and does NOT depend on a real SDL_GameController being connected. Note ControllerUpdate() (xr_input.cpp:391-400) early-returns when no controller is present, so touch synthesis MUST live outside that controller-gated block.
- Keep the desktop build byte-for-byte unchanged: no behavior change on Windows/Linux/macOS. Every new .cpp added to a target's CMakeLists must compile (even if it is a no-op) on all platforms, or be gated so it is only added on Apple. Add new files to src/xrEngine/CMakeLists.txt under the "Interfaces\\Input" group (lines 261-269).
- New touch tuning cvars belong next to the existing gamepad cvars in src/xrEngine/xr_ioc_cmd.cpp:830-840 (gamepad_stick_sens_x etc.), following the CMD3/CMD4 pattern, so they persist via user.ltx like every other setting.
- SDL2 is a find_package dependency (cmake/XRay.Compiler.GNULike.cmake:144 requires >=2.0.18), not vendored. Building SDL2 for iphoneos/simulator is a Phase 2 concern, but Phase 5 assumes an SDL2 with the iOS UIKit video driver, SDL_iPhoneSetAnimationCallback, SDL touch + GameController + haptics. Verify the pinned SDL version actually has these before relying on them.
- Physical-device verification is the acceptance gate for the whole phase and can only happen after a SideStore install of a signed build — it cannot be done in CI or on the Windows dev box. Sequence device testing after Task 5.9/5.10 land.

---

