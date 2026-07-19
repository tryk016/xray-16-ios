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
- **Desktop glad FLAGS are 0 under `gladLoadGLES2`** — not just the function pointers.
  Any `HW.Caps` capability derived from `GLAD_GL_VERSION_*` / `GLAD_GL_ARB_*` (e.g.
  `bVTF` from `GLAD_GL_VERSION_3_0 || GLAD_GL_ARB_texture_float`) is **silently false
  on iOS**, and every feature it gates silently disappears with no GL error (the
  black-sky bug: the bVTF gate skipped binding the sky cubemaps entirely). When a
  feature "does nothing" on iOS with a clean log, grep what its code path is gated on.

---

## Journal

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
  texture); menu background video doesn't create (D3D-wrapper CreateTexture → ES formats);
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
  (D3D-wrapper `CreateTexture(A8R8G8B8)`, still unsupported on ES) spin GL errors the whole time
  and can't render; they're also the prime suspect zone for the kill.
- **NEXT SESSION, in order:**
  1. **Pull crash reports FIRST** (`idevicecrashreport` staging; look for today's `xr_3da-*.ips`)
     — the termination reason (watchdog/jetsam/GPU) decides everything downstream.
  2. **Skip the intro movies on iOS** — they can't render (video-texture path unported), they
     cost 40s per boot iteration, and they bracket the kill. Find the sequence trigger: grep
     configs/scripts for `bitcomposer`/`intro` (CUISequencer in xrGame plays logo movies), guard
     it out on iOS. This alone may reach the menu.
  3. **Harden the DXT decoder's error scoping** (drain before, per-stage checks) so the next log
     tells the truth about texture uploads.
  4. Then: menu render test → video-texture D3D-wrapper port (in-game PDA/TV need it later) →
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
- **Known follow-ups:** Theora/AVI video textures use the D3D-wrapper `CreateTexture(A8R8G8B8)`
  and still fail on ES (`Invalid video stream`, non-fatal — intro movies stay audio-only; menu
  doesn't depend on them). A2-tail effect pairs (accum_sun↔2uv, distort↔particle, lplanes) are
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
