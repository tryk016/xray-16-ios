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
  - **4.9 (A2) — slot-based varying names for ES monolithic linking.** Device build 10021 (family C)
    compiled shaders on Apple ES but failed at LINK: `Output of vertex shader 'v2p_TL_Tex0' not read
    by fragment shader` — ES links varyings by NAME, desktop SSO by LOCATION, so paired VS/FS use
    different names on purpose. Fix: renamed EVERY vs→fs varying to a slot-encoded name
    `xrvary<location>` (TEXCOORD0→xrvary8, COLOR→xrvary0) in both `out`/`in` decls + `main()` uses
    (304 names, 82 files) so name==slot ⇒ VS-out and FS-in at the same slot match by name on ES and by
    location on desktop. New offline tool `misc/ios/shadercheck/link_check.py` checks the ES varying
    interface over blender pairs (glslang's own linker is too lenient) — **40/49 pairs link clean now
    (from ~0)**, incl. boot-critical accum_sun_mask. `find_include` made case-insensitive (fixes CI
    under-count vs local).
  - **4.10 — boot-log + soft-fail (2026-07-16 evening).** Device confirmed **A2 works** (engine
    marched past the old fatal to the next C++ blender pair). New debug channel:
    **`Documents/xr_boot.log`** — every log line mirrored to a plain file, fflush per line, works
    even when boot fatals before CreateLog (the "no log file" mystery = the log only flushed on
    fatal/exit AND the fatal happened before the log opened; also `idevicesyslog` can't see a 3rd-
    party os_log, so the file mirror is THE channel). **Soft-fail:** a failed monolithic link no
    longer kills boot (`_LinkPP` skips constant-parse on program 0 and logs
    `! Pass '<vs|ps>' failed to link — pass disabled`; `CBackend::Render` skips draws with `pp==0`).
    One run now lists ALL broken pairs. `link_check.py` also scans C++ blenders (`r_Pass` in
    src/Layers/xrRender/blenders) = the boot-critical passes: **121/137 clean, 16 broken = 3 roots**.
  - **4.11+4.12 (2026-07-16 evening):** soft-fail run showed the engine reaches the MAIN LOOP
    (Lua loads, intro audio plays). Fixed on top: **MRT `layout(location=N)`** on all 18
    SV_Target0/1/2 outs (ES requires it; was the sky2/combine/particle root), gate switched to the
    **real device define set** (SUN_SHAFTS/SSR/DOF/parallax paths — fixed everything it exposed;
    gate 265/279 on real paths), and **CPU DXT→RGBA8 decode** in glTexture.cpp (BC1-5, all
    mips/faces; Apple ES has no S3TC — this was the black-screen root) + gli PROFILE_ES30 for
    non-DXT (no GL_BGRA on ES). **Build 10021058 verdict: ZERO shader errors on device.**
  - **🎉 2026-07-17 MILESTONE (build 10022063): THE MAIN MENU RENDERS ON THE iPHONE.** Full
    crash-ladder day (each .ips one step deeper, all in the journal): 4.13 skip intros +
    decoder error scoping → 4.14 glMapBuffer→glMapBufferRange (menu-bg video crash) + the
    **float4 varying ABI** (A2 finished; link_check **137/137**) → 4.15 ES-absent GL call sweep
    (glGetTexLevelParameteriv font crash — dims now from texture_load; glPolygonMode;
    glDrawBuffer) → 4.16 **the draw call itself was NULL** (glDrawElementsBaseVertex is ES 3.2;
    classic-path base-vertex fallback + latent VAO/pointer bug fix) → 4.17 **iOS has no FBO 0**:
    Present now blits to SDL's uikit view framebuffer at drawable size. Screenshot: menu list +
    fonts + version label perfect; two green quads = the two .ogm VIDEO textures (logo + animated
    background) — the unported D3D-wrapper video path, known and non-blocking.
  - **🎉 2026-07-17 (later): touch works + "New Game" reaches the 3D world.** Phase 5.1 (build
    10022064) made the menu operable (tap = absolute cursor + click). Tapping New Game loads
    **Zaton** fully (6464 spawns, ALL gameplay Lua, HOM/portal/objspace caches, geometry) and
    crashed on the FIRST 3D frame. Lua/luabind on arm64 = solid (hidden-wall fear dead).
    Slices since: **4.18** stub FS for depth-only shadow passes (853 null-FS link failures);
    **4.19** the crash = **occlusion queries** (ES has no GL_SAMPLES_PASSED / glGetQueryObjectiv
    — forced R_occlusion off on iOS) + sampler border-color/LOD-bias/clamp-border ES guards;
    **4.20** family D gate-clean (SKIN_NONE NORMAL float3; gate **279/279**, link_check 137/137).
  - **12-agent AUDIT done → [iOS-Port-Audit-2026-07-17.md](iOS-Port-Audit-2026-07-17.md).**
    Read it — it verified the plan is still valid and ranked the remaining walls with file:line.
  - **🏆 2026-07-18 (build 10022072): THE GAME IS PLAYABLE.** In Zaton, AK + arms
    (skinning works), HUD/missions, fired rounds, autosave OK. phys_footprint 3.1 GB —
    survived ~300 MB under jetsam thanks to 4.25's prefetch skip. Ladder: 4.22 float RT
    32F->16F (EXT_color_buffer_float absent) -> 4.23 DXT mip-skip 1024px -> 4.24 never
    self-minimize on deactivate -> 4.25 jetsam OOM fix (skip Prefetch; JetsamEvent files
    are where these kills log, never xr_3da .ips). Details in the journal.
  - **🎮 2026-07-18 (later): FULL GAME LOOP ON A BLUETOOTH PAD — zero new code.** The
    engine's built-in default pad scheme (xr_level_controller.cpp:972) matches the console
    remaster layout; SDL game-controller init + hot-plug were already live. Inventory,
    weapon switching, pause menu, save and clean exit all confirmed on device. In-game
    memory: heap 1.43 GB, textures 324 MB (mip-skip works); 3.1 GB only at load peak.
    Phase 5 is DONE for pad players.
  - **Phase 6 (2026-07-18 evening, slices 6.1-6.4, builds 073-075):** user priority =
    graphics -> memory -> settings -> virtual pad LAST. Landed: legacy-format fallback +
    3D DXT (water volume) + env lighting log + **settings persistence (user.ltx CONFIRMED
    loading)**; the 129 texture "failures" were ONE bug — vector GL_TEXTURE_SWIZZLE_RGBA
    is desktop-only (per-channel now; BGRA R/B-swap fixed, options highlight likely same
    root); black sky = VTF texelFetch(s_tonemap) in the VERTEX stage returns 0 on ES
    monolithic — sky2/clouds forced to the non-VTF path under GL_ES. Full ladder in the
    journal.
  - **Slice 6.7 (2026-07-18 late): device test of 079 → 5-track worker-pool.** 079
    CONFIRMED on device: audio resumes after Siri, phantom 0x500s gone (129→0). Still
    broken: sky, menu-text ghosting, options highlight, Game Mode banner. Five
    detective+adversarial-verifier tracks (12 patches): **sky = bVTF gate never binds
    the cubemaps on iOS** (desktop glad FLAGS are 0 under gladLoadGLES2 — new gotcha
    class; also why 6.4's shader fix was a no-op) → bind unconditionally; **ghosting =
    nothing clears the single persistent presented texture** → iOS ClearRT/ClearZB at
    OnFrameBegin + rt_Generic_0 clear in RenderMenu (trade-off: pause backdrop black);
    **Game Mode = GCSupportsGameMode deprecated at iOS 18.6** → LSSupportsGameMode
    added (banner needs ≥5 min since last close; check Settings → Game Mode);
    **colors = DXT decoder PROVEN spec-correct** (Python transcription test; screenshot
    re-check shows colors actually fine — perceived wrongness is lighting/sky) + avg-RGBA
    diag; **focus highlight = warp-skip hypothesis DISPROVEN** (hover is polled
    per-frame, not event-driven) → 6 `* iOS diag:` log points bracket the whole path.
    Full detail in the journal (slice 6.7).
  - **Slices 6.7 + 6.8 — DEVICE-TESTED on build `1.6.02.10023080`. Three of four
    confirmed fixed:** **sky + clouds now render** (the unconditional cubemap bind was
    the whole story); **menu text no longer stacks** (per-frame ClearRT/ClearZB +
    rt_Generic_0 clear killed the ghosting on the single persistent presented texture);
    **colours are correct and the DXT decoder is exonerated for good** — the on-device
    avg-RGBA diag proves channel order in both directions (`detail_grnd_grass`
    `124 114 98` R>G>B warm earth; `sky_19_cube` `156 165 184` B-dominant blue;
    `sky_20_cube` `149 117 86` R-dominant sunset), so the earlier "wrong colours" was
    the missing sky/lighting, not an R/B swap. **Still open: the options focus
    highlight — but the log PROVES the logic works and is merely invisible**
    (`valuable=14`, `focusNav` resolves a candidate, `SetFocused 'CUIButton'
    cursor=(566,320)` warps correctly, `cursorVis=1` throughout). 6.8's
    erase-iterator UB fix in `CUIFocusSystem::Update` is in and caused no regression.
  - **Slice 6.10 (CURRENT) — the white world is SOLVED and FIXED; Track A stripped.**
    Build 10024082's timeline proved the mechanism outright: `f_luminance_adapt` is not an
    exposure value, it is the **lerp weight** of the 1x1 exposure feedback texture
    (`MiddleGray.w` → `bloom_luminance_3.ps:52 rvalue = lerp(scale_prev, scale, w)`), so
    **weight 0 strands the exposure forever** rather than pausing it. The intro sequencer
    pauses the device, `Device.fTimeDelta` is pinned at 0, and the log shows the weight
    decaying `0.0179 → 0.0003 → 0.0000` across the intro (`dt=0.0000`, `intro=2/1`) and
    snapping back to `0.0148 → 0.0167` the frame the clock is released (`t=8.14`,
    `intro=0`, `dt=0.0166`) — which is exactly when the picture heals by eye.
    `0.5 * 0.9^31.6 = 0.0179` pins the first sample: only ~32 frames had ever run, all with
    `dt == 0`, from the one-shot `f_luminance_adapt = 0.5f` seed. FIX (iOS-guarded, `#else`
    byte-identical): drive the update from `fTimeDeltaReal` **and floor the weight at
    `_max(f, 0.015f)`**. The floor is the load-bearing half — `fTimeDeltaReal` is NOT a live
    wall clock during the intro either (`CTimer::Start()` early-returns while paused; the log
    shows `dtr` frozen at `0.2272` x6 and `0.0028` x2), it is merely guaranteed nonzero.
    0.015 is ~1.1 s and sits below the 0.0167 steady-state weight at 60 fps, so it is inert
    during normal play. The entire Track A timeline is **removed** (it cost a 20-60 ms hitch
    once a second for 25 s after every level load).
  - **Slice 6.10 — Track B: leading hypothesis KILLED, replaced.** The "hud\crosshair draws
    nothing, see the missing in-game crosshair" argument is **false**: the stock crosshair is
    only drawn while holding a `use_crosshair` weapon (`HUDTarget.cpp:53,266`,
    `Actor.cpp:1136,1157`) and is *supposed* to be absent otherwise. The always-drawn centre
    mark is instead a **textured** dot via `hud\cursor` + `ui\cursor` — the same textured-UI
    path as the missing `CUICursor` sprite. So **two independent textured-UI draws are missing
    while untextured and font draws are visible**, and a texture load/bind failure on the
    `ui\cursor` family is now the best explanation. The 6.9 present probe is also **void**: it
    sampled the sprite's outermost corner texel, transparent on an arrow cursor. Track B
    diagnostics are therefore KEPT and sharpened: the cursor's own
    `GetBaseTextureResolution` is now logged (`texOk`/`texRes` — a `0` or `0x0` answers it
    outright), the present probe samples **three** points in both Y orientations, and a
    `CGameFont` `>` `<` bracket affordance is drawn alongside the shader bars as an A/B
    (font paints on device; the ui_shader path is unproven). The DXT avg-RGBA diag in
    `glTexture.cpp` is kept too — it now sits on the prime suspect's path.
  - **Slice 6.10 — STANDALONE FINDING: the game does NOT render at native resolution.**
    `CHW::Present` blits a **932x430 point** source to the **2796x1290 pixel** drawable with
    `GL_NEAREST` (`glHW.cpp:309-323, 371-374`) — a 3x nearest-neighbour upscale of every
    frame. That is the whole explanation for the soft, blocky device screenshots. Not fixed
    here; it is a deliberate quality/performance decision that deserves its own slice.
  - **Slice 6.9 (previous build) — two diagnostic tracks, all iOS-guarded.** **Track B:**
    a yellow/cyan blinking focus frame drawn around the focused widget at the end of
    `CDialogHolder::DoRenderDialogs` (covers main menu AND in-game pause) using the
    `hud\crosshair` shader, which has no sampler stage so the ES unbound-sampler
    failure mode cannot apply; plus a ~1 Hz `CUICursor::OnRender` log (reached /
    visible / pos / shader `inited()`) and a ~1 Hz two-pixel `glReadPixels` probe out
    of `pFB` in `CHW::Present` at both Y orientations. **Track A:** a one-line-per-second
    timeline for the first 25 s after each level load, emitted from the combine_1 block,
    sampling albedo / accumulator / combined scene / sky / exposure through a private
    read-only FBO, alongside dt-vs-dtr (proves pause), intro state, fog, sun/hemi/ambient
    and weather weights. **Track A is diagnostic only — no fix ships in it.**
  - **Slice 6.11 (CURRENT, awaiting device test) — the main-menu GREEN QUADS explained and
    patched.** The green was a **fingerprint, not a colour bug**: `yuv2rgb.ps:19`'s constant bias
    `_S = (-0.86961, +0.53076, -1.0786)` maps a (0,0,0) sample to exactly RGB(0,135,0), so a
    *uniform* quad in that colour proves shader/geometry/blend all work and only the texture
    CONTENT is missing. Root cause: the OGM create sequence
    (`glSH_Texture.cpp:236`) ended with ONE undrained `glGetError()`; GL errors are **sticky** and
    `CHK_GL` is a no-op in release, so it read a stale error from an earlier path, logged
    `Invalid video stream`, deleted the decoder and zeroed `pSurface` — after which `PostLoad()`
    picked `apply_normal`, which binds texture 0, and on ES an unbound sampler returns (0,0,0,1)
    with no error. **The long-standing "unported D3D-wrapper video path" note was WRONG and cost
    a wasted investigation** — that is the DX11 implementation, never compiled on iOS; xrRenderGL's
    Theora path was already ES-correct. Fixes: drain + per-stage error checks (PBO / storage /
    clear) naming the failing stage; **non-destructive failure** — on genuine failure substitute a
    1x1 immutable RGBA8 surface holding YUV black (Y=16,U=V=128) instead of `pSurface = 0`, since
    `dxUIRender::UpdateShaderName` has already swapped the element to `hud\movie` and cannot be
    un-swapped; full-surface YUV-black clear at creation (closes the pow2-storage-vs-real-upload
    margin latent bug); one-shot "first frame decoded" log per video texture. **Green is now
    structurally impossible — worst case is black.** Also removed the invalid
    `D3DSAMP_MAXMIPLEVEL` sampler call in `glState.cpp` — but note it was verified **DEAD CODE**
    (no `SetSAMP` call site emits it), so it is a cleanup only and will **not** change the log's
    0x500 population; the true upstream source of the stale error is **still unidentified**.
    **Safety:** `allow_game_intro()` is now iOS-`false` like `allow_intro()` — with video working,
    leaving it open would re-arm the intro path the app was previously KILLED on.
    **Blast radius:** tutorial/PDA video and the sleep-dialog static also go live and are NOT
    gated by either intro flag.
  - **BUILD TO TEST: `1.6.02.10024084` = commit `287138b15` = slices 6.10 + 6.11.**
    CI green (run 29688122705, all jobs incl. shader gate). **Verified live in SideStore:** the
    `ios-dev` release's `OpenXRay.ipa` has `updatedAt=2026-07-19T13:20:30Z`, matching that run's
    publish job finishing at `13:20:31Z` — so the served IPA really is this commit, containing
    BOTH the tonemap-adaptation fix (white world) and the video-texture fix (green quads).
    Slice 6.10 alone was `1.6.02.10024083`; do NOT test that one, it lacks 6.11.
  - **ENVIRONMENT CHANGE (2026-07-19): the project moved from Windows to a MacBook**
    (Intel i9 8-core, 32 GB, macOS Tahoe 26.3.1, Xcode installed). This means **iOS builds now
    run LOCALLY** — see the CI recipe in `.github/workflows/ios.yml`, reproduced in the setup
    script under `~/openxray-handoff/`. The single most valuable consequence: **agents can now
    COMPILE before claiming a patch is ready.** Until this point every patch in this port was
    verified by reading code only ("cannot compile — verified by reading"), with syntax errors
    surfacing ~20 min later in CI. Update agent briefs accordingly: build locally, then push.
    Keep GitHub Actions running as the clean-room check and the producer of the SideStore `.ipa`.
    Also worth establishing and journalling once: direct install to the device from Xcode over
    cable, live log via Console.app instead of exporting `Documents/xr_boot.log`, and Instruments
    for the queued 3.1 GB load-peak work. Note Apple removed the OpenGL ES frame debugger from
    Xcode, so do not count on GPU frame capture for this GL app.
  - **NEXT STEP (resume here):**
    0a. **UPDATE SIDESTORE FIRST.** As of 2026-07-19 the device still has
       **`1.6.02.10024082`** (slice 6.9) installed — verified via
       `devicectl device info apps`. The build to test is **`1.6.02.10024084`**.
       Testing before updating would "reproduce" both the white world and the green
       quads against a binary that predates their fixes. Confirm the version on the
       device after updating, don't assume.
    0. **DEVICE TEST 6.11 (video textures).** Boot the build, look at the **main menu**:
       the animated `.ogm` background and the logo. Then pull `Documents/xr_boot.log` and grep
       for `iOS video` and `! OpenGL:`. Read the result by this table:
       - **video plays** ⇒ 6.11 complete; the stale error was the whole story.
       - **black quad + `* iOS video: '<name>' first frame decoded WxH ...`** ⇒ the decoder works;
         the next suspect is the upload/swizzle path, not creation.
       - **black quad + NO such line** ⇒ the decoder never advances; look at
         `CTheoraSurface::Update` / libtheora on device.
       - **any `! OpenGL: 0x<err>: video <stage> ... failed`** ⇒ a REAL failure, now correctly
         attributed to a named stage. If it is the *storage* stage, check the reported `_w`/`_h`:
         a zero or absurd dimension from the Theora header would mean the failure was always
         genuine and the sticky-error theory was wrong end to end.
       - **green quad still** ⇒ the build does not contain the `glSH_Texture.cpp` edits.
       Also confirm intros are still skipped (boot goes straight to the menu, no ~40 s wait) —
       that is the safety guard working.
    1. **Run the 6.10 build on device and do BOTH of these in one session**, then pull
       `Documents/xr_boot.log`:
       a. **Load Zaton and watch the first ~10 seconds of the intro.** The white/washed-out
          world should now be **gone from the very first visible frame** — correct exposure
          throughout the intro, and **no "snap" to correct brightness** when the intro
          releases input and you take the first step. Also confirm the once-per-second
          hitch for 25 s after every level load is gone (Track A was removed).
       b. **Enter Options or the Save dialog and navigate with the D-Pad.** NOT the main
          menu — the log proves the main menu reports `focused='none' valuable=0`, so
          there the frame legitimately draws nothing and its absence is not a bug.
          Report, separately: (i) do the **white `>` `<` brackets** appear either side of
          the focused control? (ii) do the **blinking yellow/cyan bars** appear? (iii) is
          the mouse cursor still invisible?
    2. What each answer means:
       - **brackets yes, bars no** ⇒ the failure is confined to the ui_shader draw path,
         not to draw ordering or end-of-frame state. Next slice targets that shader.
       - **both yes** ⇒ the frame simply never reached a focused widget before; done.
       - **neither** ⇒ nothing queued in `DoRenderDialogs` reaches the screen; that is a
         different and bigger problem.
    3. What to look for in the returned log:
       - `* iOS diag: CUICursor::OnRender reached ... texOk=? texRes=?x?` — **this is the
         decisive line.** `texOk=0` or `texRes=0x0` means the cursor's base texture is not
         resident, which would explain BOTH the missing cursor and the missing in-game
         centre dot in one stroke, and points the next slice straight at the
         `ui\cursor` / `ui\ui_ani_cursor` load path (CPU DXT decode or `.seq` rebind).
       - `* iOS diag: presentProbe ... A0/A1/A2 | B0/B1/B2` — six texels now, three points
         in each Y orientation. **Any one** non-background sample proves the cursor
         rasterises into `pFB` and is lost after Present. All six background ⇒ the draw
         produced nothing. Whichever side is plausible also settles the FBO Y convention.
       - `* iOS diag: focusFrame '<widget>' ui=... px=... clr=... font=1` — confirms the
         frame code ran and where it thinks the widget is.
    4. Then: fix the cursor per whatever the texture probe says, and **strip every
       remaining iOS diagnostic** (cursor log, present probe, focus-frame diag, the
       `ui_focus.cpp` / `xr_input.cpp` focus logs, the `glTexture.cpp` DXT avg-RGBA diag)
       — the glReadPixels probe costs a 20-60 ms hitch on each sampling frame and must
       not reach a release build.
    4b. Standalone, its own slice: **render at native resolution.** Every frame is
       currently a 3x `GL_NEAREST` upscale from 932x430 points to the 2796x1290 drawable
       (`glHW.cpp:309-323, 371-374`). Decide deliberately: full native, a 2x middle
       ground, or keep the upscale but at least switch to `GL_LINEAR`. Related hazard
       for the same slice: `CBackend::ClearRT`/`ClearZB`
       (`glR_Backend_Runtime.h:51-86`) issue `glClear` without touching
       `GL_SCISSOR_TEST`, and `set_Scissor` is uncached — a UI scissor left enabled at
       frame end would silently shrink our per-frame iOS clears.
    4. Memory (load peak 3.1 GB): per-phase phys_footprint now logged — find the spike
       phase; candidates: FS file cache trim after load, ASTC transcode (Plan 4.9).
    5. Polish backlog: proper ES vertex-sampler binding (restore VTF); ES occlusion
       (ANY_SAMPLES_PASSED); video textures patched in 6.11 (no wrapper was ever needed —
       pending device confirmation); OpenAL → static openal-soft link; LotZ-style weapon
       wheel; gyro aim.
    6. **Virtual touch pad — LAST (user's explicit order), after all of the above.**
    Tools (updated 2026-07-19, slice 6.12 — macOS):
    **`./misc/ios/build_check.sh` is the mandatory pre-push gate** (~38 s: shader
    compile gate + shader link gate + incremental arm64 build; `--shaders` /
    `--engine` to narrow). It wraps
    `python3 misc/ios/shadercheck/glsl_es_check.py --glslang /usr/local/bin/glslangValidator`
    (expect 279/279) and `python3 misc/ios/shadercheck/link_check.py` (expect 137/137).
    glslang comes from `brew install glslang` (16.4.0 — the version the gates were
    tuned against; `tools/glslang/` shipped empty in the move).
    Debug channel = `Documents/xr_boot.log`, now pullable over the cable in ~2 s
    (see below) instead of exported by hand.

## On-device testing (the loop)

**Cable access (new 2026-07-19, slice 6.12 — full detail + caveats in the journal).**
Device must be **unlocked**. `DEV=088D4462-3B95-582F-8998-167D65A0CBD6`; read the
bundle id from `xcrun devicectl device info apps --device $DEV` (SideStore appends a
per-install suffix, currently `io.github.tryk016.openxray.RMJWWPF379`).

- **Pull the log — no more File Sharing export:**
  `xcrun devicectl device copy from --device $DEV --domain-type appDataContainer
  --domain-identifier <bundleid> --source Documents/xr_boot.log --destination ./xr_boot.log`
- **Launch over cable** (works without any signing identity):
  `xcrun devicectl device process launch --device $DEV <bundleid>`
- **Live engine log:** `pymobiledevice3 syslog live --match xr_3da` (venv install).
  `idevicesyslog` genuinely cannot do this; `log stream` no longer supports remote
  devices; `log` is a zsh builtin, use `/usr/bin/log`.
- **Instruments / memory profiling:** attach by **PID** (`--attach xr_3da` fails —
  the process is named `OpenXRay`). Templates incl. `Allocations`, `Game Memory`.
- **Install over cable is NOT available** — no code-signing identity / Apple ID on
  this Mac. Add one in Xcode → Settings → Accounts (free account is enough) to
  unblock. Until then SideStore stays the install path.
- Ignore the `Failed to load provisioning parameter list ... No provider was found`
  banner on every `devicectl` call; it is a side effect of having no signing account.

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
