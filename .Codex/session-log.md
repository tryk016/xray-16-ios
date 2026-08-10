# Codex session log

This is the compact recent operational log. Older entries are preserved in
[archive/session-log-through-2026-07-24.md](archive/session-log-through-2026-07-24.md).
Canonical facts, active tasks and evidence remain in the iOS documents.

## 2026-07-26 — context bootstrap optimization

- Preserved the existing dirty graphics-profile/menu implementation and did not
  touch engine or resource code.
- Replaced the instruction to read every iOS document with a targeted bootstrap:
  full Resume, relevant active task, relevant canonical section, then bounded
  Journal search.
- Limited the active Plan to five ordered tasks.
- Added `doc/iOS-Port-Backlog.md` for deferred P1/P2, distribution and renderer
  decision work. Backlog entries are not authorized active scope.
- Reduced the operational Resume to current environment, contracts, evidence
  boundary, first commands, next slice and safety rules.
- Kept Markdown as the human/agent source of truth. JSONL remains reserved for a
  future generated performance/evidence stream when tooling consumes it.

## 2026-07-26 — device validation and thermal constraint

- Installed and exercised the simplified menu plus all three graphics profiles
  without deleting the app container. Multiplayer is absent; Options reports
  read-only 1864×860; core menu/HUD/inventory/PDA/map captures are readable.
- Added deterministic `input.sh tap x y`; device evidence requires a 500 ms
  focus delay between move and click. The fixed trigger filename still has a
  cable copy/delete race.
- Five diagnostic foreground cycles retained a correct 1864×860 frame. Normal
  lifecycle, audio interruption and low-memory recovery remain open.
- Moved Low Power/thermal handling before the diagnostic measurement gate.
  Device log proved Optimal changed from balanced to performance under
  `serious thermal` with `ios_diagnostics 1`. Constraint logs now identify the
  exact Low Power/thermal cause. Two independent read-only reviews found no
  defects.
- A short normal Optimal trace stayed near 3.20 GB physical footprint.
  Repeated diagnostic launches later reached 3,251,907 K with
  `serious thermal`; contemporaneous Jetsam inventory reports name `xr_3da` as
  the largest process during VM-compressor thrashing. The exact final popup
  termination remains inferred because no matching final crash report appeared.
- Final full gate passed 279/279, 2/2, 6/6 plus value-macro PASS, 137/137 and
  arm64 iPhoneOS build. UUID:
  `04DB2BAB-241F-3E95-BC36-9C69C337E97A`.
- Final device state: this artifact is installed, `ios_diagnostics 0`, profile
  Optimal, game stopped. Let the phone cool before the next long run.

## 2026-07-26 — local gate and FastDevice optimization

- Added deterministic ten-worker shader validation plus content-addressed,
  atomic compile/link caches. Default Release gate is 4.33 s cached; final
  uncached full gate is 5.52 s on the current M3 Pro.
- Added content-hashed CMake regeneration. It catches source-list/toolchain
  changes without paying configure cost on every iteration and forces the
  symbol-complete tree to remain outside FastDevice mode.
- Added a separate Release-semantics FastDevice tree with no LTO or full dSYM,
  synchronized resource-only updates and `install_device.sh --fast`.
  Final cached no-op is 5.11 s; app is 75 MiB, arm64 IOS, minOS 16.4.
- Device stamps now authenticate source content, Mach-O UUID and the copied
  complete app bundle. Negative tests proved resource sync, bundle-tamper
  rejection, stale full-stamp invalidation and CMake-input invalidation.
- Full local gate passed 279/279, 2/2, 6/6 plus value-macro PASS, 137/137 and
  symbol-complete Release UUID `04DB2BAB-241F-3E95-BC36-9C69C337E97A`.
- No phone, install, launch, capture, commit or push was used in this slice.

## 2026-07-26 — diagnostic header fan-out reduction

- Moved five cable-diagnostic hold/poll/tap fields from `CInput` into iOS-only
  file-local state; normal touch state remains per-instance.
- The one-time header layout change rebuilt 1,383 FastDevice units in 199.42 s.
  A temporary `.cpp`-only state field and its removal each rebuilt one unit in
  6.30/6.33 s, proving future diagnostic fields avoid the header fan-out.
- Final full Release gate rebuilt 1,383 units and passed all contracts in
  344.50 s including dSYM work. UUID is
  `1080C791-9890-3C5E-8A45-0B15BC24543C`; `__debug_info` is 513,359,537 bytes.
- Direct Documents resource injection remains deferred: `CLocatorAPI` refreshes
  bundled gamedata on every launch. A safe marker/restore design needs phone
  validation; current resource-only iteration stays bundle-level.
- No phone, simulator, install, launch, capture, commit or push was used.

## 2026-08-01 — local lifecycle and low-memory closure

- Added an atomic lifecycle/frame permit, main-thread persistence ordering and
  explicit EAGL detach/rebind with failed-context retry before engine activity.
- Moved the first lifecycle drain ahead of SDL window events so `cfg_save`
  precedes deactivation. Deactivation now releases held keyboard/mouse/controller,
  direct touch and diagnostic input, clears active-finger state and drops stale
  queued input while retaining cursor position.
- Added Mach current/peak telemetry, immediate CPU trim and foreground-only,
  256 MiB bounded stale-texture eviction with decoded upload accounting, lazy
  reload and pinning for raw environment/UI/ImGui handles.
- Reasserted graphics profiles synchronously after config load and changed PDA
  frame fallback ordering after proving the warnings were alternate-topology
  probes rather than absent retail assets.
- The main reliability slice and both follow-up lifecycle fixes ended review
  with no P0/P1 findings. Policy/UI tests and all shader/link gates pass.
- The final arm64 iOS 26.5 Simulator build reached AVAudioSession, OpenAL, input
  and 776-file filesystem initialization, then the expected missing-retail-data
  boundary. iOS 27's pre-SDL `NoSceneLifecycleAdoption` trap is deferred as
  `IOS-P1-010`; both simulator app processes are stopped.
- Final full iPhoneOS 16.4 gate rebuilt the five affected units. App/full-dSYM
  UUID is `35F1BD5F-410A-3773-87EF-27B5D8E8BB25`; `__debug_info` is
  513,393,286 bytes. FastDevice UUID is
  `FB44464E-0FFB-3F99-B652-E7C944F1EFD4`.
- No phone, device install/container action, commit or push occurred. Lifecycle,
  LOWMEMORY reduction/lazy reload and PDA visuals still require device proof.

## 2026-08-02 — external review closure

- Verified every finding from an external full-working-set review. Fixed the
  background busy-spin, iOS `rs_always_active` clock gap, failed-activation
  rollback, scaled/integer adaptive timing, repeated-Load texture pinning, two
  missing gate-hash inputs and tap validation; rejected three unreachable or
  asset-inconsistent reports.
- Two final independent reviews returned APPROVE without P0/P1/P2. FastDevice,
  arm64 Simulator and full iPhoneOS gates passed all policy/UI, 279/279,
  2/2, 6/6 plus macro and 137/137 contracts.
- iOS 26.5 Simulator reached the expected missing retail `system.ltx` boundary
  and was terminated. Final FastDevice UUID is
  `BCF77E6D-2F7E-333D-BD02-986A09FF58EC`; full app/dSYM UUID is
  `F7AF56B7-06F8-3D0A-A7BC-63A61DEF86F7` with 513,394,351 debug-info bytes.
- No phone, install, device-container action, Mirroring, commit or push occurred.

## 2026-08-03 — phone reliability and UI validation

- Built and installed matching FastDevice UUID
  `BCF77E6D-2F7E-333D-BD02-986A09FF58EC` in place under the stable paid-team
  identifier; the retail container was preserved.
- XCUITest bundles signed and launched, but first-device automation enabling
  timed out at interactive authorization. No credential was guessed.
- Device testing found Options fatal on missing
  `video_adv:cap_always_active`. Added unbound hidden iOS compatibility nodes
  and a static contract; FastDevice passed and all four Options tabs rendered.
- Four diagnostic background/foreground cycles retained PID, correct frames and
  the 1864×860 drawable.
- A real LOWMEMORY event evicted 47 surfaces/262,143 KiB and reduced physical
  footprint from 3,262,723 to 2,998,051 KiB. PDA, world, inventory, walking and
  a later foreground cycle rendered correctly.
- Game process stopped after testing. No uninstall, commit or push; full gate
  remains required before publication.

## 2026-08-03 — local XCUITest and review closure

- Made the CMake/Xcode XCUITest project and scheme reproducible; clean generic
  `build-for-testing` passes twice with strict host/runner signature checks.
- Added build-directory locking, exact argument/result validation, signing
  preflight and test teardown that terminates OpenXRay. Device access remains
  exclusive to explicit `--test`; no phone action occurred.
- Xcode 27 requires iOS 17 for the test bundle only; product and automation host
  remain iOS 16.4.
- Review extended the Options contract through shared Advanced Lua and routed
  the final precache `rs_always_active` read through the inert iOS lifecycle
  policy.
- Final full gate passed all policy/UI, 279/279, 2/2, 6/6 plus macro and
  137/137 contracts. App/dSYM UUID is
  `DEBA5EDC-F455-32A9-A767-A04848A20614`; debug-info is 513,394,539 bytes.
- No install, launch, Mirroring, container, certificate, commit or push action.
- Follow-up review found two comment-only false-positive paths in the new static
  checker. Comment stripping, active-call matching and scoped `RenderEnd()`
  policy matching now reject both controlled negative mutations; final review
  is APPROVE with no P0/P1/P2.
- Repeated full gate passed with zero rebuilt units and the same
  `DEBA5EDC-F455-32A9-A767-A04848A20614` UUID.

## 2026-08-04 — XCUITest phone authorization and Options 3x

- First explicit XCUITest device run completed authorization and passed 1/1;
  teardown left no OpenXRay process.
- Added before/after XCUIScreen evidence and calibrated the rotated XCUITest
  coordinate space. Two Credits calibration runs were rejected as evidence.
- Final 47.922-second run launched independently three times; all three after
  captures visibly show Video Options, Optimal and read-only 1864×860.
- Final xcresult: `/tmp/OpenXRayUIAutomation-options-3x-confirm.xcresult`.
- Game stopped and process absence confirmed before the user ended phone time.
  No install, container/signing mutation, commit or push occurred.

## 2026-08-04 — shared physical-iPhone lease

- Added the global coordinator at
  `/Users/patryk/.codex/device-coordination/ios-device-lock.sh` and documented
  it in both OpenXRay and OpenGothic `AGENTS.md` files.
- OpenXRay owner is `openxray`; physical-device work must acquire, renew and
  release the shared lease while announcing busy/free state in commentary.
- Atomic exclusion, wrong-owner rejection and owner renew/release passed in an
  isolated state directory. Generic-device builds and simulators need no lease.
- Direct cross-task messaging tools were unavailable in this session; the
  shared lease and event log are therefore the authoritative coordination path.

## 2026-08-04 — overnight local reliability and bounded device tooling

- Split `ios_autoinput` from frame readback; added UUID/atomic ACK, lifecycle
  trigger discard, UUID-correlated hold cancellation and entity-level release.
- Added five-cycle+Siri XCUITest scenarios and a held-input/stale-replay device
  oracle. Generic-device build and host positive/negative fixtures pass; these
  new lifecycle scenarios remain device-unproven.
- Hardened the 30-minute trace parser with minimum sample count, max adjacent
  gap and non-finite rejection. Route ACKs are documented as command acceptance,
  not actor-movement proof.
- Device scripts now use just-in-time exact-token leases plus bounded
  process-group timeouts. Isolated ownership/race/timeout tests and final review
  found no P0/P1.
- One bounded install preserved the retail container and installed FastDevice
  UUID `9982376B-3039-3B76-A681-611A2FDE28E0`; pre-launch `user.ltx` copy hung,
  was interrupted, and the token was released. No runtime result is claimed.
- Full uncached Release gate passed 279/279, 2/2, 6/6 plus macro and 137/137;
  app/dSYM UUID is `D8A842FD-A5ED-3AA6-B1AA-FFFCE67FCA9B`, debug-info
  513,394,826 bytes. Nothing committed or pushed.

## 2026-08-08 — local lifecycle/audio and UI-oracle correction

- Corrected the stale handoff: the 2026-08-04 held diagnostic W run device-proved
  entity release, UUID cancellation and no stale replay; held touch remains open.
- Rejected the Home/Siri reliability run as a harness failure. Replaced it with
  five fail-fast Safari switches and foreground non-mixable AVFAudio playback.
- Added `wasSuspended` diagnostics, twelve durable log-oracle fixtures and
  twelve mutation-based UI fixtures covering comments and inactive branches.
- Signed generic XCUITest build, FastDevice and full uncached Release passed;
  final Release UUID is `9ECD525A-FC14-3D68-ADFD-7BBB086A2C5A`, debug-info
  513,394,876 bytes. Sol xhigh: `APPROVE — brak P0/P1/P2`.
- No phone, install, container/signing mutation, commit or push. Corrected
  Safari/audio runtime, lock/unlock and held touch remain device-pending.
- Added 20 Activity Monitor parser fixtures and strict timestamp, footprint,
  PID, schema/reference and exit-class validation. FastDevice/full gates and
  Sol xhigh passed; the real 30-minute device budget remains open.

## 2026-08-08 — Optimal profile policy eligibility

- Expanded the host policy suite across strict thresholds, tier traversal,
  cooldowns, gaps/warmup, 30/60/120 Hz and uneven cadence; warnings and
  ASan/UBSan pass.
- Fixed blocked headroom leaking into a later upgrade, including mixed
  false-to-true windows; blocked slow windows still downgrade.
- FastDevice UUID is `259BB7C3-9EDB-327F-BCD3-17958E19B648`; final Release and
  dSYM UUID is `249506E5-1364-3725-987B-9B5B53D17319`, debug-info 513,394,901
  bytes. Sol xhigh: `APPROVE — brak P0/P1/P2`.
- No phone/install/runtime/commit/push action; measured pacing and thermal
  transitions remain device-pending.

## 2026-08-08 — shader macro and sector fallback contracts

- Added a five-macro numeric shader manifest with 30 mutation tests, five exact
  zero fallbacks and frozen debt of eight presence uses plus one `#undef`.
- Extracted the iOS 7×8 nearest-floor search and valid-sector commit into pure
  policies; strict warnings and ASan/UBSan pass without changing CDB/prefetch.
- Multiple Sol xhigh reviews closed guard, inactive-parent, undef, comment-phase
  and source-parser false passes. Both slices ended with
  `APPROVE — brak P0/P1/P2`.
- FastDevice rebuilt one unit, UUID `FF0304BE-A014-3DE5-AE81-36C825248495`.
  Full uncached Release app/dSYM UUID is
  `3EBCCFF7-45FC-3B4C-9438-EF190721CF73`, debug-info 513,395,574 bytes.
- No phone/install/launch/container/signing/commit/push action. Cross-content
  sector validation and all remaining runtime evidence stay device-pending.

## 2026-08-08 — stable inventory focus geometry

- Replaced oscillating fractional focus-scroll deltas with a content-space
  feasible integer interval and deterministic top alignment when no full fit
  exists; multi-update and external-clamp fixed points pass strict/ASan/UBSan.
- Shared the exact indented `CUIScrollView` draw clip with the iOS focus overlay
  and expanded the UI checker to 32 fixtures total: 1 positive baseline + 31
  negative mutations, with no known dead-code, detached-result, mapping or
  scissor-consumption false pass.
- FastDevice rebuilt 1,068 units, UUID
  `493EFBB7-A613-3DD7-B906-065EDCB04BE8`. Full uncached Release rebuilt 1,068;
  app/dSYM UUID is `72D6778E-D1AB-370B-B83A-67B823B94B83`, debug-info
  513,399,145 bytes. Sol xhigh: `APPROVE — brak P0/P1/P2`.
- No phone/Simulator/install/runtime/commit/push action. Dense inventory visual
  focus/clipping remains device-pending.

## 2026-08-08 — isolated retail Simulator smoke test

- Added a fail-closed, external-work-root iOS 26.5 retail Simulator workflow
  with exact backup/staging/protected-input manifests and 31 mocked fixtures.
- First real menu run exposed `--console` forwarding the harness SIGTERM into
  the game; switched to redirected output and exact-PID liveness checks.
- Corrected run reached the fully textured menu with 12 archives, `system.ltx`,
  `Starting engine...`, clean logs and a live post-screenshot process; its
  dedicated Simulator was deleted and protected inputs stayed unchanged.
- FastDevice and full uncached Release gates pass without engine recompilation;
  UUIDs remain `493EFBB7-A613-3DD7-B906-065EDCB04BE8` and
  `72D6778E-D1AB-370B-B83A-67B823B94B83`. Sol xhigh approved with no P0/P1/P2.
- No phone/device/signing/commit/push action. Simulator menu proof does not
  replace physical-device rendering, gameplay, HUD or dense-UI evidence.

## 2026-08-08 — Simulator autoload / Locator checkpoint

- Located the real autoload failure: a 256-byte Simulator path overflowed the
  `string256` destination's NUL capacity in `CLocatorAPI::Register`; `ERANGE`
  cleared the buffer and long archive entries became empty-key lookups. Shared
  `string_path` plus explicit source/copy checks restores 39,270 cached files
  from 24,075, without a Simulator-only branch.
- Locator mutation contract is 8/8; retail workflow is 41/41 with
  `saved_game_sync_complete`; FastDevice passed as
  `470F9977-D7B8-355C-837B-63569A7F49B1`. The new contract is an artifact input;
  the final uncached gate passed as Release/dSYM
  `21A8EC49-8ED7-3494-B47A-6E8588DF6913` with 513,400,299-byte `__debug_info`.
- Extended isolated run loaded `mobile user - beginning of the game`, reached
  Zaton HOM, `End of synchronization A[1] R[1]` and `after_load`; selected save
  SHA-256 and protected inputs stayed unchanged, and its dedicated Simulator
  was deleted. The retained capture is loading-only: no presented-world, pixel,
  UI, device-rendering or performance claim follows.
- No phone/device lease/devicectl/signing/install/commit/push action.
- Independent final review remains open.

## 2026-08-08 — Locator/autoload documentation correction

- Final review did not approve the checkpoint: 2 P1 and 2 P2 findings. This
  update corrects P2 facts only; P1 fixes, a repeated full gate and new review
  remain open.
- Current pre-P1-follow-up Release/dSYM UUID is
  `21A8EC49-8ED7-3494-B47A-6E8588DF6913`; `__debug_info` is 513,400,299 bytes.
  The raw extended-run `runtime-proof.txt` still says pre-rename
  `saved_game_world_ready`; current code/tests use `saved_game_sync_complete`.
- The 45-entry 39,315-to-39,270 delta is 34 `bin`, 7 `_appdata_`, 3 diagnostic
  files and the separate `gamedata/shaders/gl/hud_default.s` overlay, not a
  deficit in the 12 retail archives. The retained capture remains loading-only.

## 2026-08-09 — Locator/autoload P1 hardening

- Both original P1s are fixed: mandatory `large-files-sha256.tsv` parse,
  files-manifest size/SHA consistency and three-phase protection cover all
  allowlisted large files; only the selected mutable large save may differ and
  it must remain a nonempty regular file.
- The two-stage log oracle writes only a fresh snapshot at launch, then after a
  successful stop performs no-follow descriptor/inode/prefix/rotation/rewrite/
  symlink/full-sequence validation before producing runtime proof and report.
- Review history remains explicit: final Sol xhigh found 2 P1/2 P2; P2 docs
  were fixed; two Sol medium follow-ups found and drove fixes for post-final
  save semantics and symlink resolution. Latest result is
  `PRE-REVIEW CLEAR — brak P0/P1/P2`, not final approval.
- Host workflow is 54/54 (67.306 s independent, 68.359 s full gate); Locator is
  8/8. FastDevice passes at 0 TU with UUID
  `470F9977-D7B8-355C-837B-63569A7F49B1`.
- Real run `simulator-work-20260808-230615-67616` reports PASS:
  `saved_game_sync_complete`, Zaton, 39,270 files/12 archives, sync line 641,
  `after_load` line 645 at 3,588,549/3,593,573 K current/peak. Post-stop snapshot
  v1 is 27,244 bytes, SHA-256 `b05f714c7efac4bcfa17a877ea19964b0eaf684bc6a8a18bbc790156f22fe937`.
  The 631,235-byte save keeps SHA-256 `7ff0b12ee5d0a39b7a9595d7cc491cd63a32dfc2ce276e5302f74a4cdf7214cc`;
  protected inputs are unchanged and the dedicated Simulator is absent.
- Repeated full gate passed every host/shader/link contract at 0 TU. Release/
  dSYM remains `21A8EC49-8ED7-3494-B47A-6E8588DF6913`, debug-info 513,400,299
  bytes. Current matched stamp is source
  `543342523068fd3d2edc02f3ee31e95a3d4537488aa7c305a99f99a6319f414e`, bundle
  `4275267b15bd0689a30145bca3b2dd9974b2211ce8bd4b95a0b461dfe19804b7`, minOS
  16.4 and forced-off shader cache.
- Loading screenshot only; no world/pixel/UI/device/performance proof. No phone,
  `devicectl`, lease, signing, install, commit or push.

## 2026-08-09 — Locator/autoload final-review documentation P2

- Formal Sol xhigh found only stale source-stamp documentation, with no P0/P1
  or other P2. After correction the exact repeat verdict is
  `APPROVE — brak P0/P1/P2`; LocatorAPI/autoload is locally closed.
- Naming-only test changes `world_proof` to `sync_complete_proof` and
  `dynamic_world_markers` to `ordered_sync_complete_markers` retain 54/54 PASS,
  change no behavior and require no new real run.
- Independently reproduced full stamp: source
  `543342523068fd3d2edc02f3ee31e95a3d4537488aa7c305a99f99a6319f414e`, app UUID
  `21A8EC49-8ED7-3494-B47A-6E8588DF6913`, bundle
  `4275267b15bd0689a30145bca3b2dd9974b2211ce8bd4b95a0b461dfe19804b7`, minOS
  16.4, forced-off shader cache and 513,400,299-byte dSYM `__debug_info`.
- Boundary remains `saved_game_sync_complete`, loading-only; no world/pixel/UI/
  device/performance proof. No build, Simulator, phone, commit or push.
- Next phone-free slice: semantic marker after `DoRenderDialogs()` and isolated
  Simulator sequence `world -> inventory -> world -> pda_tasks -> world ->
  pda_map -> world`; navigation/render-path evidence only, not pixel/device proof.

## 2026-08-09 — semantic UI navigation / CoP PDA map-hotkey closeout

- Sol xhigh: `APPROVE — brak P0/P1/P2`. Final isolated iOS 26.5 Simulator run
  `simulator-work-20260809-013601-65931` PASS; deleted Simulator
  `410EA3BC-23FC-4F4C-843D-CF4438704652`; PID 72114 marker sequence proves
  `I, I, P, E, Escape, M, Escape` as world/inventory/world/pda_tasks/other/
  world/pda_tasks/world. CoP `eptTasks` is combined Tasks/Map; no standalone
  retail `eptMap`; scope is semantic-ui-navigation-only.
- Gates: marker 15/15, PDA map-hotkey 6/6, navigation 28/28, retail isolation
  61/61; shaders 279/279, low 2/2, SSAO 6/6, numeric 30/30, links 137/137.
  FastDevice `8C52017C-119B-3800-A9E5-8335EB9E3574`; full gate two TUs,
  Release/dSYM `B2ACA614-EC54-30A1-9607-9DD84AEB00BC`.
- Save/protected inputs unchanged; cleanup passed. No phone, lease, devicectl,
  signing, install, commit or push. Pixel/readability/performance/device proof
  remains open.

## 2026-08-09 — IOS-P2-002 local OpenAL Soft checkpoint

- iOS now uses the exact static OpenAL Soft 1.25.2 prefix archive with strong
  AudioToolbox/CoreFoundation/CoreAudio; Apple OpenAL, dynamic/alternate/
  duplicate/forwarded or response-file link forms fail closed.
- The per-Core interruption registry covers empty begin, scene replacement/ABA,
  surviving/pre-paused scenes and duplicate begin/end. Strict, ASan/UBSan and
  final Sol xhigh technical reviews pass: `APPROVE — brak P0/P1/P2`.
- Fresh FastDevice: 1,485 TUs, UUID `C7FDF518-AF64-31C7-92E4-3FA8C3B9C977`.
  Fresh isolated iOS 26.5 Simulator provider/menu run PASS in
  `simulator-work-20260809-113846-78853`; deleted Simulator
  `67FD36F1-7888-4C9A-8851-3AB745A7A283`; protected inputs unchanged.
- Fresh uncached full gate: 1,485 TUs; 279/279, low 2/2, SSAO 6/6, numeric
  30/30, links 137/137, retail 62/62; Release/dSYM
  `54A5DF59-2A42-3676-BC22-94F01F96A724`, debug-info 513404887 bytes.
- No phone, lease, `devicectl`, signing, install, commit or push. The iPhone
  interruption recovery proof remains pending under IOS-P1-002/IOS-P2-002.

## 2026-08-09 — IOS-P1-010 UIScene local/Simulator complete

- Hash-pinned, fail-closed SDL2 2.32.10 UIScene backport: one scene,
  `SDLUIKitSceneDelegate`, one `SDL_main`, connected iOS 13+ `UIWindowScene`
  and scene-owned four-transition lifecycle.
- Initial iOS 26.5 work `simulator-work-20260809-131233-70026` exposed a
  harness race, not a product regression: no background handshake and a parser
  rejected the valid one-`deactivate` prefix. Corrected two-phase oracle: 74/74.
- Retail PASS: iOS 26.5
  `/Users/patryk/openxray-handoff/simulator-work-20260809-135908-18426`, PID
  25991; iOS 27.0
  `/Users/patryk/openxray-handoff/simulator-work-20260809-140608-26903`, PID
  35981. Both have one menu marker, seq 1/2/3 activate/deactivate/activate,
  protected inputs unchanged and deleted dedicated Simulator.
- Full authoritative gate rebuilt 4 TUs: SDL scene 7/7, lifecycle 10/10,
  retail 74/74, shaders 279/279, low 2/2, SSAO 6/6, numeric 30/30, links
  137/137; source `00270cdb…`, UUID `341855E0-0560-3C8B-9D7E-115F941DCAA6`.
  Final Sol xhigh: `APPROVE — brak P0/P1/P2`.
- Next boundary: physical-device lifecycle acceptance under IOS-P1-010/
  IOS-P1-002; no pixel/readability, device, performance, audio or soak claim.
  No phone, lease, `devicectl`, signing, install, commit or push.

## 2026-08-09 — IOS-P0-003 startup-sector v1 evidence-oracle local closeout

- v1 covers `level_load`/`QuickLoad` epochs and exact/fallback/retained/
  unresolved/recovered outcomes. QuickLoad retained/none requires a post-epoch
  `CCameraManager::ApplyDevice` generation; retained is prior valid-sector use,
  not fresh detection, save/location correctness or visual proof.
- Two-phase evidence is Prepare -> fully validate/build -> CommitPrepared -> Msg
  -> FlushLog; invalid payload remains pending. Mandatory oracle inputs are
  expected PID, after-epoch and repeated expected triggers; JSON PASS is
  structural only and unresolved fails a device startup test.
- Host PASS: parser 17/17, source mutation 9/9, strict C++ policy, ASan/UBSan;
  independent Sol xhigh implementation re-review: `APPROVE — brak P0/P1/P2`.
  Partial engine gate rebuilt 1,383 TUs; full gate rebuilt 0 matching TUs after
  Python corrections and passed retail 74/74, numeric 30/30, low 2/2, SSAO 6/6,
  shaders 279/279 and links 137/137. Current UUID `8D623468-0A50-3055-9516-3EA703818076`.
- Next phone boundary: completed baseline epoch before every exact expected
  batch, then separately resolved classification and an objective world frame.
  Another outdoor, indoor/portal, QuickLoad/transition and device visual proof
  remain pending. No phone, lease, `devicectl`, Simulator, install, signing,
  commit or push.

## 2026-08-09 — IOS-P2-004 installer/preflight local closeout

- Closed host-only installer hardening: fixed Team/bundle ID, CLI/env/default
  device selection, atomic custom app/stamp pairs, descriptor-safe private
  snapshots and a no-device/no-renew temporary-signing `--preflight`.
- Hermetic installer contract 12/12, ShellCheck, `bash -n`, no-write compile,
  diff check and delta Sol xhigh approval passed. The rejected wrapper run and
  superseded `9edb...` stamp are historical only.
- Final full gate: 0 TUs; retail 74/74, installer 12/12, numeric 30/30, low
  2/2, SSAO 6/6, shaders 279/279, links 137/137; source `f7ff08d7...`, UUID
  `8D623468-0A50-3055-9516-3EA703818076`. Real preflight used no lease,
  device command, renewal, install, launch, container, phone or Simulator.

## 2026-08-09 — IOS-P2-004 final-review correction

- Final closeout review found that the gate hash omitted the installer's lease
  and timeout dependencies. Both are now hashed; a new mutation test proves
  each changes the artifact digest. Installer contract: 13/13.
- The Plan now permits host-only tooling closeout only against its declared
  preflight scope; canonical open work no longer misstates fixed Team/bundle
  identity as unfinished configurability.
- Authoritative full gate: 0 TUs; retail 74/74, installer 13/13, numeric 30/30,
  low 2/2, SSAO 6/6, shaders 279/279, links 137/137; source `2d1cdfe5...`, UUID
  `8D623468-0A50-3055-9516-3EA703818076`. Real preflight again used no lease,
  device command, renewal, install, launch, container, phone or Simulator.
- Corrected complete-diff Sol xhigh review: `APPROVE — brak P0/P1/P2`.

## 2026-08-09 — IOS-P2-005 local CI provenance

- Pinned 12 action uses to reviewed SHAs; dependency cache now uses one exact
  source/toolchain key, no restore keys, exact-hit-only build skip and
  unconditional seven-library verification/upload.
- The fail-closed workflow contract retains semantic checks and pins exact
  workflow bytes at SHA-256 `47885fe3...`; four rejected adversarial rounds
  were corrected before final Sol xhigh `APPROVE — brak P0/P1/P2`.
- Independent PASS: Python compile, 79/79 mutations, actionlint, whitespace,
  extracted input and Xcode 27 toolchain scripts. Toolchain fingerprint
  `74536eb1...`; current device artifact hash remains `2d1cdfe5...`.
- Local slice only: no remote miss/hit, app build, Simulator, phone, signing,
  install, runtime, commit or push. `ios-port` remains unprotected. P2-005 is
  backlogged remote-pending; physical IOS-P1-002 is active again.

## 2026-08-09 — IOS-P2-006 local BC fallback contract

- Extracted the active BC1-BC5 decoder into one bounds-checked pure codec and
  one GLI mapping; runtime now validates actual level bytes. Current rendering
  remains `GL_RGBA8`, ignored GLI swizzle, BC4 `RRR1`, BC5 `RG01`.
- Strict and ASan/UBSan synthetic coverage plus five real DDS/KTX fixtures pass.
  First Sol review found three P2; all were corrected and final re-review was
  `APPROVE — brak P0/P1/P2`.
- Full uncached gate: 279/279 shaders, 137/137 links, one rebuilt TU; source
  `e0812512...`, UUID `44D1A9FA-F8AF-3CD5-AC97-386F0D2999D2`, bundle
  `38ab03ef...`, iOS 16.4, shader cache forced off.
- No phone, Simulator, install, signing, runtime, commit or push. sRGB/swizzle
  visual acceptance remains device-pending in IOS-P2-006.

## 2026-08-09 — IOS-P2-006 stamp-dependency correction

- Final docs review found the BC test missing from `IOS_ARTIFACT_INPUTS` and a
  stale canonical stamp. The test is now hashed and covered by the existing
  mutate/change/restore contract; installer tests remain 13/13.
- Fresh full gate: BC strict/sanitized, 279/279 shaders, 137/137 links; source
  `02d9ca8d...`, UUID `44D1A9FA-F8AF-3CD5-AC97-386F0D2999D2`, bundle
  `38ab03ef...`, iOS 16.4, shader cache forced off. Current hash matches stamp.
- Unsupported "retail harness race" attribution is withdrawn; only the missing
  intermediate stamp and later focused/full PASS are retained facts.
- No phone, Simulator, install, signing, runtime, commit or push.
- Corrected complete-state Sol xhigh review: `APPROVE — brak P0/P1/P2`; local
  BC codec/contract is closed, visual sRGB/swizzle acceptance remains deferred.

## 2026-08-09 — device visual smoke for stamped build 10045

- Same-Team/same-bundle Release install and diagnostics launch passed; a fresh
  native `1864x860` gameplay frame was captured after unattended loading.
- A 12 s acknowledged forward input correlated with lighter nearby terrain,
  while distant vegetation remained dark; no stationary/time control was run,
  so the lighting checkpoint remains open.
- Pause menu was readable with Multiplayer absent; inventory textures were
  present; Video options showed `Optimal` and `1864x860`.
- App terminated after testing; exact-token lease released and shared lock
  returned `free`. No performance claim, commit or push.

## 2026-08-09 — consolidated commit and post-commit gate

- Commit `27d966ddc8a5` consolidates the reviewed iOS checkpoint; not pushed.
- Post-commit full gate rebuilt 68 TUs and passed BC strict/sanitized, retail
  74/74, installer 13/13, CI 79/79, numeric 30/30, low 2/2, SSAO 6/6,
  shaders 279/279 and links 137/137.
- Current stamp: source `449dd3f9…`, UUID `545F8958-0B1F-3D85-BDF2-758CF9553A16`,
  bundle `eeacc4b2…`, iOS 16.4, forced-off shader cache; dSYM `__debug_info`
  513,541,671 bytes. Current hash and no-device signing preflight pass.
- The post-commit artifact is not device-tested; the preceding visual smoke
  belongs to the prior stamped artifact. No phone, lease, Simulator or push.

## 2026-08-10 — capture-state v2 local checkpoint

- Commit `90e9d3c3e` adds token-bound canonical JSON/PPM/PNG capture state and a
  one-lease A/A+3/A+6 stationary-vs-forward evidence batch.
- Offline controls pass 16/16 parser/evidence, 8/8 mocked host tools, 2/2
  producer mutations and strict/sanitized C++; final Sol xhigh:
  `APPROVE — brak P0/P1/P2`.
- Post-commit full gate rebuilt 69 TUs; source `ff2c33c7…`, UUID
  `226890F0-CF42-302B-AA5F-3092CB5E4AF3`, bundle `620f3b98…`, dSYM
  `__debug_info` 513,888,155 bytes; all established gates passed.
- Local-only: no phone/lease/devicectl/Simulator/sign/install/launch. The first
  physical A/B packet, visual interpretation and dark-frame cause remain open.

## 2026-08-10 — isolated capture-v2 host closeout and runtime blocker

- Five-file Simulator capture-v2 host implementation is locally reviewed: final
  static PASS, parser 31/31, runner 84/84; final Sol xhigh exactly
  `APPROVE — brak P0/P1/P2`.
- Workroot `015703-14748` exposed the over-strict post-T1 loading classification;
  valid live readiness now retries 75, invariants remain fatal 1, and post-stop
  retry remains fatal.
- Workroot `024814-86404` built and reached Zaton sync/after_load: T0 loading
  seq23, T1 loading seq24, then loading through seq88 under one 600 s deadline.
  PID 92531 epoch1/frame35 marker was `level_load unresolved/none`, invalid
  sector, same camera/probe and radius0.
- `CRender::Calculate` bypasses exact/fallback when saved/current camera are
  equal; the stationary Simulator therefore enters no-detection. IOS-P0-003
  remains active; the device-proven fallback remains valid and distinct.
- Timeout failed closed: both Simulators deleted and no T2 capture/proof/report.
  The last successful protected-input/source-snapshot guards were pre-launch;
  post-runtime log/save/staged/protected guards did not run. Scope is Apple
  Software Renderer only; no phone/lease/devicectl/sign/install/pixel/
  performance/cause claim and no push.

## 2026-08-10 — capture-v2 commit and post-commit full gate

- Commit `362bf625d` records the reviewed capture-v2 host checkpoint.
- Full uncached gate PASS: retail 84/84, numeric 30/30, low 2/2, SSAO 6/6,
  shaders 279/279, links 137/137 and all established host contracts; 68 TUs.
- Stamp: source `33c9efab...`, UUID `7297E9B6-1DBE-348D-8BAE-A153A19F781E`,
  bundle `0e0ba583...`, iOS 16.4, dSYM `__debug_info` 513,888,155 bytes.
- Host-only gate; no phone/lease/devicectl/sign/install/launch/push. Successful
  T2 publication remains blocked by active IOS-P0-003 stationary-sector work.

## 2026-08-10 — docs-only stamp provenance

- Docs commit `86ed974d6` then passed another full uncached gate: retail 84/84,
  shader contracts and 68-TU build; source `8c121ae8...`, UUID
  `3D91E69C-9170-37E0-96B6-1F3269E12C0A`, bundle `d1daad61...`.
- This docs append advances hashed HEAD again. Treat that stamp as historical
  evidence for `86ed974d6`; rerun the full gate before install or push.
- No phone, lease, devicectl, install or push.

## 2026-08-10 — stationary LevelLoad local closeout

- Added a dedicated one-shot post-camera `level_load` sector barrier while
  preserving unconditional movement and separate QuickLoad semantics.
- PASS: startup oracle 17/17, marker contract 10/10, strict/sanitized C++,
  diff check and FastDevice (249 TUs, UUID `020185E6-48B6-3A4B-8B1C-CC8ED72EE27B`).
- iOS 27 workroot `041339-82601` PASS: PID 90549 `resolved/exact` sector 115;
  T0/T1/T2 54/55/56, frames 89/91/92, gameplay 1864x860, no input.
- Five capture artifacts and report published after all guards; protected
  inputs unchanged and dedicated Simulator deleted. Apple Software Renderer
  control-flow evidence only; no phone/lease/devicectl/sign/install/push.

## 2026-08-10 — deterministic delayed lifecycle fixture

- Reproduced the existing async-writer race at stress iteration 7; production
  stable-read failure was correct and remains unchanged.
- Test-only PID-validated `pending -> armed -> released` handshake replaces both
  30 ms writers and proves separate deactivate/activate polling.
- PASS: 50/50 serial, 200/200 P8, adversarial 7/7, retail 84/84, diff check and
  FastDevice (68 TUs, UUID `B19A8309-D20F-3743-9452-33DC054DF305`).
- Full uncached gate PASS: retail 84/84, numeric 30/30, shaders 279/279, links
  137/137, Release 0 TUs, UUID `A94C72FB-B192-34CA-9B5B-3B563839DDC3`.
- Host-fixture reliability only; no phone/lease/devicectl/Simulator/sign/install/
  commit/push and no physical lifecycle or pixel claim.

## 2026-08-10 — native iOS 27 Simulator UI-capture evidence

- Opt-in `--ui-captures` requires `--ui-navigation`, iOS 27 and diagnostics/
  autoinput. It binds stable metadata/authoritative PPM/exact PNG to semantic
  input ordering, then revalidates after process stop before manifest/report.
- PASS workroots `085137-86609` and `090751-93548`: both 7/7 semantic states,
  4/4 native 1864x860 captures, protected inputs unchanged and dedicated
  Simulators deleted. Focused tests: capture 15/15, navigation 34/34, retail
  92/92. Eight PNGs visually show inventory, area map/tasks, Stats, map/tasks.
- The first `082352-68297` run failed closed at 6/7 when an immutable log read
  met normal append; bounded append-only live reading and strict capture order
  `accepted <= marker < released <= capture` correct that harness boundary.
- Scope is Apple Software Renderer file-level Simulator evidence, not iPhone,
  readability, color or performance proof. Final Sol xhigh verdict is
  `APPROVE — brak P0/P1/P2`; no phone, lease, devicectl, signing, install or
  push occurred.
