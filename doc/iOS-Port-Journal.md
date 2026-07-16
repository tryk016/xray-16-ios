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
