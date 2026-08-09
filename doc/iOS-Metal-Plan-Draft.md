# iOS native Metal renderer — deferred RFC

**Status: DEFERRED RFC.** Originally synthesized 2026-07-17 and synchronized with
the project contract on 2026-07-24; routing was updated on 2026-07-26. This is
not the active renderer plan. Numbering remains provisional (`M0…M7`). The
start gate is maintained in [iOS-Port-Backlog.md](iOS-Port-Backlog.md); the
canonical current renderer is documented in [iOS-Port.md](iOS-Port.md).

---

## 1. Why, and why later

The ES path works — menu renders, Zaton loads — but it pays a permanent impedance tax:
Apple's GLES-on-Metal is a frozen, deprecated compatibility layer (ES 3.0 ceiling — no SSO,
no BC textures, no desktop occlusion queries, `glDrawElementsBaseVertex` / `glMapBuffer` /
border-clamp all absent), and essentially every Phase 4 slice was spent working *around* it.

A native Metal backend removes the translation layer entirely:

- restores location-based stage linking (the whole A2 varying saga disappears by construction);
- three ES impedance areas are **native features**: float color-buffer
  renderability (the current ES deferred path works on the test device, while
  Metal makes the format contract explicit), occlusion queries (visibility
  result buffers), and base-vertex draws (native on the iOS 16.4 floor);
- real GPU tooling: Metal HUD, Xcode `.gputrace` captures, API validation layer;
- TBDR headroom the GL layer can't reach: load/store actions, memoryless G-buffer,
  MetalFX upscaling.

It is also **~2.5× the ES-path effort** (see §8). The ES path is now playable and
serves as an on-device visual reference. The apparent far-field streaming defect
was later split into two bugs and fixed: camera-sector recovery restored static
geometry, and value-based SSAO feature-macro tests restored global ambient
lighting. Neither required ANGLE or Metal.

Start this RFC only after cross-content startup validation, memory budget,
lifecycle, and repeatable ES performance baseline are complete.

**Ground rules.** One slice is self-contained and leaves the tree buildable.
Changes compile locally on macOS, pass the GitHub Actions clean-room build, and
are smoke-tested on device through `misc/ios/install_device.sh`. The ES path
stays the default renderer until M7; every Metal slice must leave ES working.
`Documents/xr_boot.log`, numeric probes, and captured reference frames are the
debug evidence.

---

## 2. Codebase reality: where the backend plugs in

### 2.1 The compile-twice pattern

The renderer is compiled per-backend from mostly-shared source. One CMake target = one
backend module:

- `xrRender_R4` = `USE_DX11` + `RENDER_NAMESPACE=render_r4`
- `xrRender_GL` = `USE_OGL` + `RENDER_NAMESPACE=render_gl`

Both pull the *same* files from `src/Layers/xrRender/` (visuals, resource manager, backend
state cache, blenders, deferred dsgraph) and `src/Layers/xrRender_R2/` (the deferred
renderer logic), then add an API-specific HAL (`xrRenderGL/` vs `xrRenderDX11/`) and an
API-specific shell target (`xrRenderPC_GL/` vs `xrRenderPC_R4/`).

The Metal backend follows the same pattern:

- **`src/Layers/xrRenderMetal/`** (HAL) + **`src/Layers/xrRenderPC_Metal/`** (shell)
- target `xrRender_Metal`, `RENDER_NAMESPACE=render_metal`, define `USE_METAL`,
  links `Metal.framework` + `QuartzCore.framework`
- module registers mode `renderer_metal` (id 7)

Template to mirror: `src/Layers/xrRenderPC_GL/CMakeLists.txt`.

**Registration flow:** `src/xr_3da/entry_point.cpp` `s_render_modules` (currently a
hard-coded `std::array<RendererModule*, 2>` — widening it touches `EngineAPI.h` too) →
`CEngineAPI::CreateRendererList` → each module's `ObtainSupportedModes()` (HW probe) →
`SelectRenderer()` reads the `renderer` console var → `SetupEnv(mode)` publishes
`GEnv.Render / RenderFactory / DU / UIRender`. A Metal module implements the same 4
`RendererModule` virtuals (`xrRender_GL.cpp` is the model), plus a declaration in
`src/Include/xrRender/xrRender.h`.

**Shared-core branching:** the shared code contains ~330 `#if defined(USE_OGL)/defined(USE_DX11)`
sites total (≈134 in `xrRender/`, ≈60 in `xrRender_R2/`, rest in headers). The architecture
audit found the *load-bearing* subset is ~12 shared dispatch files that must gain a third
`#elif defined(USE_METAL)` arm (`R_Backend.h`, `R_Backend_Runtime.h`, `r_constants.h`,
`BufferUtils.h`, `SH_RT.h`, `SH_Texture.h`, `QueryHelper.h`, `r__occlusion.h`,
`r__sync_point.cpp`, `ShaderResourceTraits.h`, `Texture.cpp`, `xrRender_console.cpp`);
the bulk (dsgraph, blenders, detail manager, skinning, particles) should compile unchanged.
**M0.1 measures the real number** — treat "12 files" as the hypothesis, "330 sites" as the
worst case.

### 2.2 Subsystem inventory (what must be built)

Scope = effort relative to the GL counterpart, accounting for API-model mismatch.

| # | Subsystem | GL counterpart | Metal piece | Scope |
|---|---|---|---|---|
| 1 | Device/context/swapchain (`CHW`) | `xrRenderGL/glHW.cpp/.h` | `MTLDevice` + queue + `CAMetalLayer` via `SDL_WINDOW_METAL` / `SDL_Metal_CreateView` | Large |
| 2 | Present / frame boundary | `glHW.cpp` (`Present/BeginScene/EndScene`) | drawable acquire (late) + blit pass + commit; frame semaphore ring | Medium |
| 3 | Backend command recorder (`CBackend`) | `R_Backend*.cpp` + `glR_Backend_Runtime.h` | `mtlR_Backend_Runtime.h`: lazy-encoder pass manager + draw flush | Large |
| 4 | Draw submission + topology | `glR_Backend_Runtime.h` | `drawIndexedPrimitives` (native baseVertex); fan expansion IB | Medium |
| 5 | Vertex/index/stream buffers | `BufferUtils.h` + `glBufferUtils.cpp` | private static buffers + staging; shared-storage dynamic rings | Medium |
| 6 | Vertex declarations | `glBufferUtils.cpp` (`ConvertVertexDeclaration`) | `SDeclaration` → `MTLVertexDescriptor` (baked into PSO) | Medium |
| 7 | Textures: upload/formats/bind | `glTexture.cpp` + `glTextureUtils.cpp` + `glSH_Texture.cpp` | `MTLTexture` upload; BC tiers (§3.6); video path | Large |
| 8 | Render-target surface (`CRT`) | `SH_RT.h` + `glSH_RT.cpp` | `MTLTexture` with renderTarget usage; `D24S8` → `Depth32Float_Stencil8` | Medium |
| 9 | Deferred G-buffer container | `xrRenderPC_GL/gl_rendertarget*.{h,cpp}` | `mtl_rendertarget*` copies (DX11-style duplication) | Medium |
| 10 | State objects | `glState.cpp/.h` + `glStateUtils.cpp` | SState split 3 ways: PSO key / DS-state cache / sampler cache (§3.3) | Large |
| 11 | Shader compile/link pipeline | `rgl_shaders.cpp` + `ShaderResourceTraits.h` | translator (glslang + SPIRV-Cross) + `SProgram` pairing + 3-level cache | Large |
| 12 | Constants: reflection + upload | `glr_constants.cpp` + `glr_constants_cache.h` | reflection blob → `R_constant_table`; per-frame constant ring (§3.5) | Large |
| 13 | Resource-manager factory glue | `glResourceManager_Resources.cpp` | `_CreateVS/_CreatePS/_LinkPP` equivalents | Medium |
| 14 | HW caps probe | `glHWCaps.cpp` + `HWCaps.h` | `MTLGPUFamily` mapping | Small |
| 15 | Occlusion queries | `QueryHelper.h` | visibility result buffers, deferred get (§3.7) | Small |
| 16 | Blender→state recorder | `Blender_Recorder_GL.cpp` | Metal arm | Small/Medium |
| 17 | Type shim | `CommonTypes.h` + `Common/d3d9compat.hpp` | opaque C++ wrapper handles (no ObjC in shared headers) | Medium |
| 18 | Detail/grass instancing | `glDetailManager_VS.cpp` | constant-array instancing | Small |
| 19 | Screenshot readback | `glr_screenshot.cpp` | blit → shared buffer → `getBytes` | Small |
| 20 | Scripting bindings stub | `glResourceManager_Scripting.cpp` | stub | Small |
| 21 | HW test probe | `r2_test_hw.cpp` | `MTLCreateSystemDefaultDevice != nil` + family check | Small |
| 22 | Module/factory/console reg | `xrRender_GL.cpp` + `entry_point.cpp` + `EngineAPI` | `xrRender_Metal.cpp`, array widening | Small |
| 23 | Shader corpus (MSL) | `res/gamedata/shaders/gl/` (286 stage files: 87 `.vs` + 199 `.ps`; 359 files incl. includes) | translation pipeline, not hand-authoring (§3.4) | Very Large |
| 24 | Shared-core `USE_METAL` branching | ~330 `#if` sites / ~12 load-bearing files | third arm where needed | Medium–Large |

**Reused unchanged** (API-neutral, already sit on `CBackend`/`RCache`): all `dx*Render.*`
helpers (`dxUIRender`, `dxFontRender`, `dxDebugRender`, `dxRainRender`,
`dxEnvironmentRender`, …), visuals (`FVisual`/`FSkinned`/`FTreeVisual`/…), `ModelPool`,
`DetailManager`, `WallmarksEngine`, HOM, light DB, and the entire `r__dsgraph_*` + `r2_*`
deferred pipeline. That is the payoff of the namespaced-module design.

### 2.3 Top 5 coupling points (the hard parts)

1. **D3D9 type vocabulary saturating the shared core** (`D3DFORMAT`, `D3DRS_*`,
   `D3DSAMP_*`, `D3DVERTEXELEMENT9`, via `Common/d3d9compat.hpp`). The Metal backend keeps
   speaking D3D9 enums at its leaves, exactly like GL does — no seam redesign.
2. **Constant model:** runtime `glGetActiveUniform` reflection + per-uniform `glUniform*`
   pushes vs Metal's buffer-only binding. Needs a reflection source (SPIRV-Cross blob) and
   a constant-ring redesign of `r_constants_cache` (§3.5).
3. **Piecemeal mutable state vs baked PSOs.** `CBackend`/`glState` apply blend/depth/cull
   lazily per draw; Metal bakes shader × blend × vertex-layout × RT-formats into immutable
   `MTLRenderPipelineState`. Needs the PSO cache keyed at draw-flush time (§3.3).
4. **Shader toolchain:** 286-file GLSL-ES corpus + bespoke `#include`/`#define`
   preprocessor + two link models. Solved by reusing everything above the compile call and
   translating (§3.4).
5. **Context/present lifecycle:** `IRender::Create/Reset(SDL_Window*)`, `RenderContext` /
   `MakeContextCurrent` / `ScopedContext` (GL-context semantics), engine FBO blitted to
   SDL's UIKit framebuffer. Metal slots `CAMetalLayer` + command-buffer/encoder lifecycle
   into the same `Begin/End/Present` boundary; context calls become no-ops.
   Note: the R4/DX11 path's deferred-command-list model (`CBackend::submit()`) maps more
   naturally onto Metal than GL's immediate model — v1 stays single-immediate-context
   (GL-shaped), but parallel encoder recording per R4's shape is a future option (§6 Q6).

The render-DLL boundary is clean: **`src/xrEngine` contains zero direct GL calls.** What
crosses it: `SDL_Window*` in `Create/Reset`, the `BackendAPI` enum (needs a `Metal` value;
essentially unused downstream), `GEnv`, and `ObtainRequiredWindowFlags` (inject
`SDL_WINDOW_METAL` instead of `SDL_WINDOW_OPENGL`).

---

## 3. Architecture decisions

### 3.1 Orientation & conventions

**The Metal backend runs in D3D orientation natively.** Metal matches D3D9 conventions
(NDC z in [0,1], top-left origin) — the engine's matrices and shader depth math are
D3D-native, so the GL backend's scattered y-inversions (scissor flip, Present blit flip,
screenshot flip) disappear. Translate shaders with vertex-y flip **OFF**; audit the few
shaders with explicit GL-compensation `1-uv.y` during bring-up. Winding: keep
`frontFacingWinding = .clockwise` to match D3D9 `D3DCULL` semantics as blenders record them.

### 3.2 Device / present layer

One `MTLDevice`, one `MTLCommandQueue`, one `CAMetalLayer` — in `mtlHW` implementing the
same `CHW` interface as `glHW.h`. SDL integration: `SDL_WINDOW_METAL`,
`SDL_Metal_CreateView` → `SDL_Metal_GetLayer` (SDL ≥ 2.0.12; deps build 2.32.10).
Layer config: `BGRA8Unorm` (not sRGB — engine does its own gamma), `framebufferOnly = YES`,
`maximumDrawableCount = 3`, `drawableSize` from `SDL_Metal_GetDrawableSize` (the retina
sizing lesson from the ES Present work, done right this time). The existing
`SDL_iPhoneSetAnimationCallback` loop paces frames.

**Frame ring:** `kFramesInFlight = 2` (bump to 3 only if GPU-bound bubbles appear) with a
`dispatch_semaphore_t`. `BeginScene` waits the semaphore, advances the frame index, resets
that frame's transient arenas (dynamic VB/IB ring region, constant ring region, visibility
slots), creates the frame's single `MTLCommandBuffer`. `Present`: end encoder, acquire the
drawable **late** (only for the final blit pass), encode fullscreen-triangle copy
(final RT → drawable; also carries the render-scale), `presentDrawable`,
`addCompletedHandler` (signals semaphore, stamps fence for occlusion/ring reclaim),
`commit`. The engine keeps rendering into its own RT exactly as today (`pFB` model);
the drawable is touched by exactly one pass — the TBDR-friendly shape.

No device-loss on iOS. On `OnAppDeactivate` stop encoding (committing GPU work while
backgrounded terminates the app).

### 3.3 D3D9 state machine → PSOs + render passes

**SState splits three ways:**
- *Blend block + color write mask* → **into the PSO key** (`set_ColorWriteEnable` is called
  dynamically, so the mask must be in the key).
- *Depth/stencil block* → `MTLDepthStencilState` cache; dynamic `set_Z/set_ZFunc/set_Stencil`
  mutate a current key, object looked up at draw flush; `stencil_ref` is encoder-dynamic.
- *Sampler array* → `MTLSamplerState` cache, bound by stage index.

**True dynamic encoder state:** cull mode, viewport + depth range, scissor (D3D
orientation, clamped to RT size — Metal validates), shadow-pass depth bias, fill mode
(`setTriangleFillMode(.lines)` — wireframe comes back, ES lost it with `glPolygonMode`).

**PSO cache.** Key ≈ 128 bits + two pointers: `{vsFunction, psFunction, SDeclaration id,
blend factors/ops, colorMask, colorFormat[0..2], depthFormat, sampleCount}` — attachment
formats come from the pass manager's pending pass, computed at draw flush. Miss path builds
the descriptor and creates **synchronously** on first-ever encounter, feeding every
descriptor into a `MTLBinaryArchive` persisted under
`Documents/_appdata_/shaders_cache_metal/`; on boot, pre-warm asynchronously from the
previous run's key list during the loading screen. First run compiles once; later runs are
hitchless.

**Render passes vs `u_setrt` — the lazy-encoder RenderPassManager** (the core impedance
piece): `set_RT/set_ZB/set_pass_targets` only mutate a pending attachment record and mark
the encoder stale; the actual `MTLRenderCommandEncoder` is ended/created at the next
draw/clear. Defaults `load = .load`, `store = .store` (semantically identical to GL FBO —
the correctness baseline). `ClearRT/ClearZB` with no draws yet fold into
`loadAction = .clear` (covers virtually all engine clears, which follow `u_setrt`);
clear-after-draws = end + reopen (correct, rare); scissored clears = clear-quad draw.
`storeAction = .dontCare` and memoryless G-buffer are **M7 optimizations**, not v1.

**Draw flush** (`CBackend::Render`): ensure encoder (reopen if stale, rebind per-pass
persistent state) → resolve PSO if inputs changed → DS object if dirty → flush constants →
`drawIndexedPrimitives(..., baseVertex:baseV)`. Base vertex is native on the iOS 16.4 floor —
the ES attrib-repointing workaround (slice 4.16) dies. Preserve 4.10 soft-fail semantics:
a pass whose program failed renders nothing but never kills the frame.

**Triangle fans** (`dxUIRender`, `FLOD`, debug): Metal has no fan primitive — one shared
static fan-expansion index buffer, routed inside the backend, invisible to callers.
Point list: unused on r2 path — assert.

### 3.4 Shader strategy (the key decision)

**Recommended: SPIRV-Cross route.** The validated GLSL-ES corpus → glslang → SPIR-V →
SPIRV-Cross → MSL, executed **on device at shader-load time** with a layered disk cache,
plus an offline CI mirror. Why:

1. The ES 3.00 corpus is the project's hardest-won asset — slices 4.3–4.20 made it
   gate-clean (279/279) and link-clean (137/137) including varying-slot normalization,
   MRT locations, precision and int/float strictness. Translation consumes exactly that
   corpus and the exact runtime permutation machinery in `rgl_shaders.cpp`
   (everything above the compile call is reused verbatim).
2. glslang is already the project's reference front-end (`misc/ios/shadercheck`) —
   corpus-vs-toolchain mismatch is a solved problem here.
3. Hand-written MSL: ~286 files × a live permutation space with no oracle — months of work,
   forks on every upstream shader change. Escape hatch only.
4. HLSL→DXC route: DXC front-ends SM6-era HLSL; the original `shaders/r2` corpus is D3D9
   SM2/3 — you'd port the HLSL forward first (bigger than the finished ES port) and abandon
   its fixes.

On-device MSL compilation (`newLibraryWithSource`) is a sandbox-friendly system service —
no JIT/RWX constraints apply.

**Pipeline detail** (`mtlShaderTranslator`): assemble source exactly as today (options
prelude + include expansion, translator prelude replacing `#version 300 es`) → **mechanical
uniform-block wrap pass** (Vulkan-dialect SPIR-V forbids loose uniforms: hoist every loose
`uniform` into one `layout(std140, binding=0) uniform xr_locals {...}` per stage — same
trick ANGLE-class translators use; corpus untouched) → glslang (Vulkan target; explicit
`layout(location)`s and `xrvary` slots pass through) → SPIRV-Cross `CompilerMSL` (iOS, MSL
2.3+; texture unit N → `[[texture(N)]]/[[sampler(N)]]` preserving the engine's stage model
1:1; `xr_locals` → `[[buffer(1)]]`; `[[stage_in]]` at `[[buffer(0)]]`) → MSL + compact
reflection blob `{name, class, array size, byte offset, block size}` + texture stages.

**Program pairing:** the ES port already forced monolithic VS+FS pairing with
slot-consistent varyings, so it carries over: `SProgram = {vs, ps (or depth-only stub —
port of `GLDepthOnlyStubFS`), merged R_constant_table}`, created where `_LinkPP` runs
today. **Caches:** L1 = translated MSL + reflection blob keyed like the existing GL binary
cache (renderer/version + source CRC + options hash) — skips glslang/SPIRV-Cross entirely
on later launches; L2 = OS shader cache absorbing `newLibraryWithSource`; L3 = the PSO
binary archive (§3.3). First boot ≈ 300 programs translated+compiled on the loading screen.

**CI mirror — gate first:** extend `misc/ios/shadercheck` with `msl_check.py` on the macOS
runner: same assembly + define sets as the ES gate, then glslang → `spirv-cross --msl` →
`xcrun -sdk iphoneos metal -c`. The corpus must be green offline **before** the first
device build — the method that made the ES port converge.

### 3.5 Constants: fixed slots + per-frame ring (no argument buffers)

Argument buffers solve bindless problems the D3D9 model doesn't have; the engine's churn is
per-draw uniform writes → linear upload ring is the right tool.
`R_constant_table::parse` for `USE_METAL` reads the reflection blob instead of
`glGetActiveUniform`; loads become `{offset, class}` per stage (a constant destined for
both stages fills both `C->vs` and `C->ps` loads — `r_constants.h` already models exactly
this D3D9 shape). `R_constants::set/seta` write into per-stage CPU shadow blocks with a
dirty flag; `flush()` at draw time allocates block size (256-aligned) from the frame's
shared `MTLBuffer` ring, memcpys, binds at `[[buffer(1)]]`. The skinning array
(`sbones_array[234]`, ~11 KB float3x4) exceeds the `setBytes` limit — so there is **one**
code path: always the ring. Matrix transpose handling stays in the cache layer as today.

### 3.6 Resources

**Vertex/index buffers.** Static: `StorageModePrivate` + staging upload (the `BufferUtils`
staging abstraction is already shaped for this). Dynamic streams (`R_DStreams`,
DISCARD/NOOVERWRITE — today's unsynchronized-map hazard, the 4.14 crash family): one
shared-storage `MTLBuffer` per stream sized `todaySize × kFramesInFlight` used as a ring;
`Lock` returns `contents + offset` directly (unified memory), `Unlock` is a no-op, DISCARD
wrap advances into the next frame's region, the frame semaphore guarantees safety.
**This deletes the whole unsynchronized-map hazard class.**

**Textures / BCn.** CoP assets are BC1/2/3/4/5; on Apple GPUs ASTC is universal but BCn is
optional (`supportsBCTextureCompression`, API iOS 16.4+, availability-guarded; present on
newest A/M chips). Three tiers:
- **v1 (bring-up):** port the existing CPU BC→RGBA8 decoder to `MTLTexture` —
  pixel-identical to today, known ~1.6 GB inflation, correctness only.
- **v2 (the real path):** offline gamedata transcode — BC1/2/3 → ASTC 4x4 (6x6 albedo),
  BC4 → `EAC_R11Unorm`, BC5 (normals) → `EAC_RG11Unorm`. Benefits the ES path too.
- **v3 (opportunistic):** direct BCn upload where supported (zero transcode on
  A17-class/M-class — including the project's test device).

Upload: staging shared buffer → blit encoder → **private** `MTLTexture` (enables lossless
bandwidth compression on TBDR); tiny UI textures may use `replaceRegion`. Cube maps
supported; gli swizzle metadata → `MTLTextureSwizzleChannels`.

**sRGB:** store and sample Unorm/linear-as-stored, no `_srgb` views, layer non-sRGB —
the D3D9 pipeline does its own gamma (avoids double-correction).

**Render targets.** `CRT::create` → `{usage: renderTarget|shaderRead, storage: private}`.
`D3DFMT_D24S8` → `Depth32Float_Stencil8` (no D24S8 on Apple GPUs). RGBA16F/RG16F/R16F/R32F
are guaranteed color-renderable and blendable on Apple families — the
`EXT_color_buffer_float` wall does not exist here. (R32F *linear filtering* is
family-dependent; the LUM chain reads 1×1 with point sampling — no impact.) RT ping-pong =
alternating pass descriptors. Debug-build validation: no bound pass attachment
simultaneously bound as shader texture (Metal forbids the feedback loops GL half-tolerates).

### 3.7 Awkward corners

- **Occlusion queries** (the 4.19 crash; `R_occlusion` force-disabled on iOS today):
  visibility result buffers are a clean fit for `QueryHelper.h`. Per frame one
  `visibilityResultBuffer` set on every pass descriptor; `occq_begin` allocates an 8-byte
  slot + `setVisibilityResultMode(.counting)` (samples-passed semantics, A9+); results
  valid in the frame's completed-handler; `occq_get` returns not-ready until then — matches
  the engine's existing deferred polling model (D3D `GetData` S_FALSE shape).
  **Re-enables occlusion on iOS for the first time.**
- **No sync readback:** screenshot = blit final RT → shared buffer + `waitUntilCompleted`
  (user-initiated hitch, fine). `r__sync_point` is subsumed by the frame semaphore.
- **Samplers:** no runtime LOD bias (same status as ES); border color only on newer
  families — keep the 4.19 clamp-to-edge behavior. `[[clip_distance]]` exists in MSL;
  keep ES-parity guards for v1, optional re-enable later (sunshafts).
- **Video textures:** port the dynamic-update path using the working ES Theora
  implementation as a device reference (M2.6 stretch).
- **ES workarounds that die here:** base-vertex emulation (4.16), depth-only null-FS hack
  (4.18 — Metal allows a legitimately nil fragment function; alpha-test vegetation shadows
  get a real discard FS), FBO-0/UIKit-framebuffer hack (4.17), DXT CPU decode
  (tiered out in §3.6), monolithic-link varying-name fragility, `#version` prelude
  fragility, GL depth range.

### 3.8 Module layout

Hygiene rule both reports agree on: **no Objective-C types in shared headers** — shared
`xrRender/*.cpp` stay pure C++; handles exposed to them are opaque C++ wrapper structs;
all `id<MTL...>` usage lives inside the Metal layer. (Whether that layer is ObjC++ `.mm`
or metal-cpp `.cpp` is open question Q4.)

```
src/Layers/xrRenderMetal/            (HAL — mirrors xrRenderGL file-for-file)
  mtlHW.h/.mm                        CHW: device/queue/CAMetalLayer, frame ring, Present
  mtlCommonTypes.h                   opaque handle wrappers for shared code
  mtlRenderPass.h/.mm                lazy-encoder pass manager, clear folding, rebinds
  mtlPipelineCache.h/.mm             PSO cache, DS-state cache, MTLBinaryArchive warm
  mtlState.h/.mm                     SState backend (blend/DS descriptors, sampler array)
  mtlR_Backend_Runtime.h             USE_METAL inline CBackend (draw flush, fans, dynamic state)
  mtlBufferUtils.h/.mm               static private + staging; R_DStreams rings
  mtlVertexFormat.h/.mm              SDeclaration → MTLVertexDescriptor
  mtlTexture*, mtlSH_RT.mm, mtlSH_Texture.mm
  mtlShaderTranslator.h/.mm          wrap-pass + glslang + SPIRV-Cross + reflection + L1 cache
  mtlResourceManager_Resources.mm    _CreateVS/_CreatePS/_LinkPP → SProgram
  mtlr_constants.mm, mtlr_constants_cache.h
  mtlQuery.h/.mm, mtlScreenshot.mm, mtlDebug.mm (encoder labels, MTLCaptureManager)

src/Layers/xrRenderPC_Metal/         (shell target xrRender_Metal)
  stdafx.h, rmtl_shaders.cpp         (shader_compile front-end reused verbatim)
  mtl_rendertarget.h + mtl_rendertarget_*.cpp   (gl_rendertarget* copies, handles swapped)
  r2_R_sun.cpp, r2_test_hw.cpp, xrRender_Metal.cpp

New Externals (static, iOS builds): glslang, SPIRV-Cross.
```

During bring-up, select per-configure (`XRAY_METAL=ON`) so the working ES build remains the
on-device regression reference — A/B capability preserved until parity.

---

## 4. Milestone ladder (visible checkpoints)

| Milestone | You can see | Maps to |
|---|---|---|
| **M0** | `renderer_metal` selectable; boots to a solid clear color, survives bg/fg | skeleton |
| **M1** | hard-coded triangle / fullscreen gradient through the real `CBackend` path | shaders + PSOs |
| **M2** | **main menu renders under Metal** (textures, fonts, UI) | 2D path |
| **M3** | Zaton static geometry in 3D (flat/albedo, then combined) | G-buffer + combine |
| **M4** | NPCs/hands animate; particles, details, wallmarks | skinning + lights |
| **M5** | sun shadows; occlusion culling ON (first time on iOS) | shadows + queries |
| **M6** | bloom/tonemap/SSAO/distortion — comparable to ES side-by-side | post chain |
| **M7** | parity screenshots + stable frame rate; ES-retirement decision | parity + perf |

---

## 5. Phases and slices

Each slice: **goal — scope — gate**. Gates are CI green plus, where stated, an on-device
observation.

### M0 — Skeleton: selectable backend, clear color (est. 5 slices)

- **M0.1 Target scaffold.** `xrRender_Metal` CMake target: `xrRenderPC_Metal/` +
  `xrRenderMetal/` with all-stub HAL; compiles the full shared source list under
  `RENDER_NAMESPACE=render_metal`, `USE_METAL`. Language decision (Q4) lands here; the real
  count of shared-core `#if` sites needing a `USE_METAL` arm gets measured here.
  Gate: iOS CI green both SDKs, ES target byte-identical.
- **M0.2 Module registration.** `MetalRendererModule` (`renderer_metal`, id 7) modeled on
  `xrRender_GL.cpp`; `mtl_test_hw` = `MTLCreateSystemDefaultDevice` + GPU-family check;
  wire into `entry_point.cpp` (array widening; ES stays default). Gate: device boot log
  enumerates both renderers, ES still runs.
- **M0.3 Device + swapchain.** `CHW`: device, queue, `SDL_WINDOW_METAL` +
  `SDL_Metal_CreateView` → `CAMetalLayer` (retina drawable sizing); `Present()` =
  clear-pass encoder + `presentDrawable` + commit. Gate: with `renderer renderer_metal`
  forced, **device shows a solid clear color** and survives background/foreground.
- **M0.4 Soft-fail backend.** `CBackend` stubs log-and-skip draws (Phase 4 soft-fail
  philosophy) so the whole engine — resource manager, blenders, UI — boots under Metal
  without a single real draw. Gate: `xr_boot.log` reaches the main loop under Metal, clear
  color still presents, zero crashes.
- **M0.5 CI job + caps.** `metal-build` leg in `ios.yml`; `CHWCaps` from `MTLGPUFamily`;
  stat overlay works. Gate: CI green; boot log prints real GPU caps.

### M1 — Shader translation pipeline + first triangle (est. 7 slices)

- **M1.1 Offline translation gate FIRST.** `misc/ios/shadercheck/msl_check.py` CI job:
  assemble each shader exactly like the engine (reuse `glsl_es_check.py` machinery) →
  glslang → SPIR-V → SPIRV-Cross → MSL → `xcrun -sdk iphoneos metal` compile on the macOS
  runner. Gate: a baseline `N/279` exists before any engine code depends on translation
  (the Phase 4 gate-first playbook).
- **M1.2 Grind the gate green.** Fix translation failures (uniform-block wrap pass,
  clip-space epilogue, interface blocks, reserved names) in the harness or shader source.
  Gate: at/near 279/279 + the 137 VS/FS pairs cross-compile as matched pairs.
- **M1.3 Vendor the translator into the app.** glslang + SPIRV-Cross built for iOS in
  `cmake/ios/deps`; `rmtl_shaders.cpp` keeps the engine's runtime define-preamble assembly
  → MSL → `newLibraryWithSource`, with the L1 MSL cache keyed by source hash. Gate: CI
  green; device boot log shows shaders translating + caching (count, timings).
- **M1.4 Constant tables.** PSOs with reflection option; populate `R_constant_table` from
  the reflection blob; per-frame constant ring upload. Gate: boot log dumps a parsed
  constant table for a known shader matching the GL path's.
- **M1.5 Vertex declarations + streams.** `ConvertVertexDeclaration` →
  `MTLVertexDescriptor` (all `D3DVERTEXELEMENT9` types incl. skinning `short4`/`UBYTE4`/
  D3DCOLOR); VB/IB creation; `R_DStreams` rings with frame synchronization. Gate: CI green
  + log assert of decl→descriptor mapping for every FVF registered at boot.
- **M1.6 PSO + state objects.** `mtlState` captures recorded D3D9-style state; lazy PSO
  cache keyed by {program, decl, RT formats, blend, mask}; DS + sampler caches. Gate: boot
  under Metal builds >0 PSOs with zero validation-layer errors.
- **M1.7 Triangle.** Un-stub the draw path for one hand-fed fullscreen pass. Gate:
  **triangle/gradient visible on device**.

### M2 — Textures + 2D path: the menu (est. 6 slices)

- **M2.1 Texture upload.** `mtlTexture`/`SH_Texture`: 2D + cube + mips; reuse the software
  BC→RGBA8 decoder verbatim (tier v1 — correctness first). Gate: textured quad on device;
  texture count matches ES boot.
- **M2.2 Render-pass tracker.** The core impedance piece (§3.3): lazy encoder, clear
  folding, per-pass rebinds. Gate: CI green; log reports pass count/frame, no validation
  errors.
- **M2.3 UI + font draw path.** Dynamic-VB UI stream (`dxUIRender`, `dxFontRender`) +
  fan expansion + sampler states. Gate: **console text / UI rectangles visible on device**.
- **M2.4 Main menu.** Whatever falls out of the first full menu attempt (crash-ladder slice
  by design — budgeted). Gate: **main menu renders and is operable via the Phase 5 touch
  path**.
- **M2.5 Screenshot/readback.** `mtlScreenshot`: final RT → shared buffer → PNG in
  Documents — the visual debug channel for everything after. Gate: on-demand screenshot
  matches what the user sees.
- **M2.6 (stretch) Video textures.** Dynamic-update texture for Theora, matched
  against the working ES reference. Gate: menu video plays (or explicitly
  deferred).

### M3 — Static level geometry: G-buffer + combine (est. 7 slices)

- **M3.1 RT build-out.** `mtl_rendertarget` equivalents: full RT inventory (RGBA8,
  RGBA16F, R32F, `Depth32Float_Stencil8`) validated against `MTLGPUFamily`. Gate: boot log
  lists every RT with format + size, no fallbacks.
- **M3.2 MRT G-buffer pass.** Deferred base pass as one encoder: position/normal/albedo
  MRT + depth. Gate: G-buffer channel dumped via M2.5 shows recognizable Zaton geometry
  (also validates orientation/depth conventions before any lighting work).
- **M3.3 Static visual draw.** dsgraph normal-pass with native base-vertex draws. Gate:
  **flat/albedo world visible on device**.
- **M3.4 Combine phase.** Hemi + sun-static approximation first. Gate: lit-looking world
  screenshot.
- **M3.5 Details + trees.** `DetailManager` instanced path + tree wave benders. Gate:
  grass/vegetation visible.
- **M3.6 Sky + env.** Skybox, environment blending. Gate: sky correct at different day
  times (screenshot pair).
- **M3.7 Stabilization.** Crash-ladder budget slice for the first real level walk. Gate:
  several minutes walking Zaton exterior, no crash, clean boot log.

### M4 — Lights, skinning, particles (est. 7 slices)

- **M4.1 Stencil light masking.** Two-sided stencil ops via DS states + pass tracker
  keeping depth/stencil attached across accumulation. Gate: light-volume masks visibly
  correct (debug tint screenshot).
- **M4.2 Point/spot accumulation.** Accumulator phases + geometry volumes. Gate: lamps
  light the world on device.
- **M4.3 Skinned formats.** Family-D vertex formats (1–4 weights, `short4`/byte indices)
  through `MTLVertexDescriptor` + `sbones` constant-ring upload; CPU-skin fallback kept as
  diagnostic switch. Gate: **NPCs + first-person hands render animated**.
- **M4.4 Particles + wallmarks.** Dynamic-VB particles, `dxWallMarkArray`, distortion
  sources rendered (effect applied in M6). Gate: campfire/anomaly particles visible.
- **M4.5 LOD + progressive meshes.** `FLOD` (fan expansion) / `FProgressive` index-range
  draws. Gate: no popping walking toward a building.
- **M4.6 Dynamic lights polish.** Bumped materials, specular. Gate: side-by-side vs ES of
  the same scene is "close".
- **M4.7 Stabilization.** Budget slice. Gate: a firefight runs without artifacts or crash.

### M5 — Sun, shadows, occlusion (est. 6 slices)

- **M5.1 SMAP depth-only passes.** Shadow RTs + depth-only pipelines (legitimately nil
  fragment function — the 4.18 stub dies); alpha-test vegetation shadows get a real
  discard FS. Gate: SMAP debug dump shows depth content.
- **M5.2 Sun cascades.** Cascade selection + compare samplers (PCF via
  `compareFunction`; border-clamp natively supported). Gate: **sun shadows on device**.
- **M5.3 Volumetrics.** Sunshafts, optionally with real `[[clip_distance]]`. Gate:
  sunshafts screenshot parity vs ES reference.
- **M5.4 Occlusion queries.** Visibility result buffers behind `QueryHelper.h` (§3.7);
  `r__occlusion` enabled under Metal only. Gate: `R_occlusion` ON: no crash, lights/flares
  cull correctly — the first working occlusion on iOS.
- **M5.5 Rain + minmax SM.** Rain rendering; minmax counterpart or a documented skip.
  Gate: rain weather renders.
- **M5.6 Stabilization.** Budget slice. Gate: full day-night + weather cycle on device.

### M6 — Post-processing chain (est. 5 slices)

- **M6.1 Bloom + luminance.** Incl. the tiny-RT chain with completed-handler /
  `MTLSharedEvent` sync. Gate: exposure adapts walking indoors→outdoors.
- **M6.2 Combine-2 + tonemap + gamma.** `xr_effgamma` in-shader (no hardware ramp on iOS).
  Gate: brightness/contrast/gamma sliders visibly work.
- **M6.3 SSAO + DOF.** Quality-tiered. Gate: screenshot A/B vs ES.
- **M6.4 Distortion.** Heat-haze/anomaly pass. Gate: anomaly distortion visible.
- **M6.5 Upscaling.** Render-scale infra wired to Metal — FSR 1.0 port or MetalFX spatial
  (Q7). Gate: render-scale cvar changes resolution with sharp UI at native res.

### M7 — Parity audit + performance (est. 7 slices)

- **M7.1 Parity harness.** Fixed-camera screenshot set (menu, Zaton day/night/rain,
  firefight) captured on both renderers via M2.5. Gate: diff sheet produced; gaps
  enumerated in the journal.
- **M7.2–M7.3 Parity fixes.** Grind the diff list (typical suspects: gamma, fog,
  alpha-test edges, depth-range artifacts). Gate: user calls the image "same or better"
  per scene.
- **M7.4 Pass/encoder optimization.** Merge passes, correct load/store actions,
  memoryless G-buffer on TBDR. Gate: pass count/frame down; no visual change.
- **M7.5 Memory.** BC tier v2 (offline ASTC/EAC transcode; benefits ES too) +
  tier v3 (native BC where supported), with a budget that prevents recurrence
  of the measured ~1.6 GB transient full-prefetch spike. Gate: peak memory
  materially down; big-level load survives.
- **M7.6 Frame pacing + HUD.** Frame ring tuning, `presentAfterMinimumDuration`; Metal HUD
  + engine stats; optional programmatic `.gputrace` dump. Gate: stable paced FPS from
  device.
- **M7.7 Decision slice.** Default renderer flips to Metal on iOS; document the ES
  retire/keep decision (Q2); delete or fence ES-only hacks accordingly. Gate: fresh
  install boots Metal by default; docs updated.

---

## 6. Open questions (flag now; decide at the marked phase entry)

1. **Shader translation route** *(decide at M1 entry)* — **default: glslang + SPIRV-Cross
   on device at load time with L1 MSL cache + identical offline CI gate** (§3.4).
   Alt A: full offline pre-translation on CI (viable — the iOS define set proved
   essentially fixed — and removes the on-device translator dependency; costs define-matrix
   enumeration). Alt B: hand-written MSL as a targeted escape hatch only.
2. **Beside or replace the ES path** *(decide at M7.7; until then: beside)* — **default:
   Metal as a third `RendererModule`, runtime-selectable, ES as fallback and parity
   reference through M7.** Costs binary size + the `s_render_modules` widening. Retire ES
   only after parity + a few stable releases.
3. **Minimum iOS / GPU family** *(decide at M0)* — **default: keep the iOS 16.4 floor;
   baseline `MTLGPUFamilyApple6` (A13); treat native BC (iOS 16.4+ API) and MetalFX
   (iOS 16+) as gated fast paths.**
4. **Implementation language** *(decide at M0.1)* — the two reports differ: the phased plan
   recommends **metal-cpp** (single-language and easy to audit) with ObjC++ only in
   the CAMetalLayer/SDL seam; the architecture design assumes **ObjC++ `.mm`** throughout
   the HAL with C++ wrapper handles in headers. Both satisfy the shared-header hygiene
   rule. Default: **metal-cpp first**; fall back to ObjC++ if metal-cpp friction shows up
   in CI (interop with `SDL_Metal_GetLayer` is the seam either way).
5. **Keep the D3D9-style `CBackend` contract** *(decide at M0.1)* — **default: keep it,
   mirror the GL backend file-for-file.** Redesigning the seam touches every backend —
   out of scope for a port branch.
6. **Single immediate context vs R4-style command lists** *(revisit at M7.4)* — v1 is
   single-immediate (GL-shaped). Metal's command buffers map naturally onto R4's
   multi-context `submit()` model; parallel encoder recording is a possible perf phase,
   not a bring-up concern.
7. **Upscaling tech for M6.5** *(decide at M6)* — FSR 1.0 port (works on the iOS 16.4
   floor) vs MetalFX spatial.

---

## 7. Risk register

| Risk | Why it bites | Mitigation |
|---|---|---|
| **Shader translation fidelity** | SPIRV-Cross output may diverge from what Apple's GL-on-Metal did silently: clip-space conventions, precision defaults, texture-origin — wrong-image bugs, not crashes | Gate-first (M1.1) with `xcrun metal` compile of all 279; uniform-block wrap + y-flip policy fixed in the translator; validate orientation/depth with the M3.2 G-buffer dump before lighting; ES on the same device is the per-scene reference image |
| **D3D9 state-machine impedance** | Ad-hoc state mutation + RT retargeting vs immutable PSOs and explicit passes → PSO-explosion stutter, encoder churn | State capture + lazy hashed PSO cache (DX11 backend is prior art); dedicated pass-tracker slice (M2.2) with pass-count telemetry from day one; naive pass breaks first, optimize in M7.4; `MTLBinaryArchive` warm from previous run |
| **Occlusion queries** | Results only post-completion; naive same-frame `Get` stalls; this code never ran on iOS | Ship M0–M4 with `R_occlusion` off (status quo); M5.4 implements against the engine's existing deferred-polling contract with one-frame latency; force-off cvar stays as permanent fallback |
| **BC textures / memory** | No BC on most A-chips; RGBA8 inflation already ~1.6 GB on Zaton → jetsam | Tier v1 decoder for correctness phases; M7.5 lands offline ASTC/EAC transcode (helps ES too) + native-BC gated path; track peak-resident in boot log every phase |
| **Metal build and device-loop latency** | A native backend adds shader translation, PSO creation and device-only validation to each slice | Compile locally on macOS; keep the translator and pure logic independently testable; one unknown per slice; use boot-log-first probes and scripted device captures |
| **Constant-layout mismatch** | MSL/std140 packing vs the layout `R_constant_table` assumes → silently wrong lighting math | Populate tables from the SPIRV-Cross reflection blob at build (never assume offsets); CI cross-checks reflection against the GL introspection dump per shader |
| **TBDR pass-structure perf** | Deferred pipeline ping-pongs RTs; naive encoder-per-change wrecks a tile-based GPU even when correct | Pass-count telemetry from M2.2; M7.4 dedicated optimization slice (merge, load/store, memoryless); Metal HUD numbers close the loop |

---

## 8. Effort estimate (in slices; ES Phase 4 ≈ 20 slices is the calibration unit)

| Phase | Scope | Est. slices |
|---|---|:---:|
| M0 | skeleton, selectable, clear color | 5 |
| M1 | translation pipeline, constants, PSOs, triangle | 7 |
| M2 | textures, pass tracker, UI, **menu** | 6 |
| M3 | G-buffer, static world, combine | 7 |
| M4 | lights, skinning, particles | 7 |
| M5 | sun, shadows, occlusion | 6 |
| M6 | post-processing chain | 5 |
| M7 | parity + performance + default flip | 7 |
| **Total** | | **~50 (range 42–58)** |

Calibration honesty: Phase 4's ~20 slices *reused the entire working GL backend* and mostly
patched shaders and call sites; this plan **rebuilds the ~8.7k-LOC backend layer**
(`xrRenderGL` + `xrRenderPC_GL` inventory) plus a translation toolchain — 2–2.5× is the
floor. Stabilization slices (M2.4 / M3.7 / M4.7 / M5.6) are budgeted because the
crash-ladder pattern (one wall per device run) is empirically how this port progresses.

---

## 9. Synthesis notes — where the three reports disagreed

1. **Naming:** one report used `xrRenderMTL`/`USE_MTL`/`render_mtl`, two used
   `xrRenderMetal`/`USE_METAL`/`render_metal`. This draft standardizes on the latter.
2. **Shared-core branching scope:** the codebase map counted ~330 `#if USE_OGL/USE_DX11`
   sites across the shared core; the architecture audit concluded only ~12 shared dispatch
   files need a `USE_METAL` arm and the rest compiles unchanged. Both can be true (most
   sites live in per-API leaves or resolve through those headers). Resolution: M0.1
   measures it; plan carries the ~12-file hypothesis with the 330-site worst case.
3. **Implementation language:** metal-cpp (plan) vs ObjC++ `.mm` (design). Kept as open
   question Q4 with metal-cpp as default.
4. **Frames in flight:** "triple buffering" (plan) vs `kFramesInFlight = 2` +
   `maximumDrawableCount = 3` (design). Design's concrete numbers win; bump only on
   evidence.
5. **`CBackend` context model:** the map recommends following R4's deferred command-list
   shape (natural fit for Metal); the design specifies a single immediate context with a
   lazy encoder for v1. Resolution: v1 single-context, R4-shape parallel encoding recorded
   as Q6 / M7-era option.

Sources for Metal capability claims: Apple Metal Feature Set Tables,
`supportsBCTextureCompression` documentation, "Optimizing texture data" (Apple),
"Explore GPU advancements in M3 and A17 Pro" (Tech Talk).
