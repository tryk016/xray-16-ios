# OpenXRay iOS — Agent Working Agreement

This file applies to the whole repository. The active project is the iOS port on
the `ios-port` branch. The product scope is iOS-only: desktop compatibility is
not an acceptance criterion and must not delay a better iOS implementation.

## Project objective

Ship a stable, playable Call of Pripyat 1.6.02 build for arm64 iOS 16.4+ while
keeping the retail resource and gameplay contracts intact.

The current renderer is native OpenGL ES 3.0 through Apple's GL-on-Metal
compatibility implementation. ANGLE and a native Metal renderer are research
options, not active migrations.

## Context bootstrap and source-of-truth hierarchy

Do not load every iOS document in full. Start each task with the smallest
context that can safely answer it:

1. Read `doc/iOS-Port-Resume.md` completely.
2. Read the current focus and the relevant task in `doc/iOS-Port-Plan.md`.
3. Read only the relevant current-facts section of `doc/iOS-Port.md`.
4. Search `doc/iOS-Port-Journal.md` with `rg` using the task ID, subsystem and
   exact error text; then read only the bounded matching section.
5. Read `doc/iOS-Port-Backlog.md` only when promoting, reprioritizing or
   investigating deferred work.
6. Read `doc/iOS-Metal-Plan-Draft.md` only for an explicitly approved renderer
   decision or migration task.
7. Read only the recent tail of `.Codex/session-log.md` when operational history
   beyond the Resume is actually needed.

Document precedence is independent from loading order:

1. `doc/iOS-Port.md` — canonical current product and architecture contract.
2. `doc/iOS-Port-Plan.md` — active prioritized work and acceptance criteria.
3. `doc/iOS-Port-Resume.md` — concise operational handoff.
4. `doc/iOS-Port-Backlog.md` — deferred work, not authorized active scope.
5. `doc/iOS-Port-Journal.md` — append-only historical evidence.
6. `doc/iOS-Metal-Plan-Draft.md` — deferred RFC.

If loaded documents disagree, the earlier item in the precedence list wins.
Correct the stale current-state document in the same slice; correct historical
evidence with a new Journal entry.

## Mandatory development loop

1. Start with `git status --short`; preserve unrelated user changes.
2. State the hypothesis and the observable that can prove or refute it.
3. Make one smallest self-contained change.
4. Run `./misc/ios/build_fast_device.sh` for the normal local iteration, or
   `./misc/ios/build_check.sh` when a symbol-complete Release artifact is needed.
5. Run `./misc/ios/build_check.sh --full` after the final commit and before push
   of engine or shader changes; partial modes are never install stamps.
6. Use `./misc/ios/install_device.sh --fast --launch` for FastDevice validation
   or the non-fast form for the symbol-complete Release artifact.
7. Use `misc/ios/input.sh` and `misc/ios/shot.sh` for repeatable camera paths
   and visual evidence.
8. Record proven facts separately from inference.
9. Update `doc/iOS-Port-Journal.md` and `.Codex/session-log.md` when a slice
   changes project state.

Never install a build after a failed or stale matching gate. Do not pipe either
build script through a command that hides its exit status. Only one worker may
own the device or a given iOS build tree at a time.

### Shared physical iPhone lease

OpenXRay and OpenGothic share one physical iPhone. Do not reserve it for a whole
session. Acquire the shortest practical lease immediately before the first
physical-device command in a concrete batch and announce the result:

```bash
LOCK=/Users/patryk/.codex/device-coordination/ios-device-lock.sh
LEASE="$($LOCK acquire openxray 5)" # size this to the actual device batch
TOKEN="$(sed -n 's/.*"token":"\([^"]*\)".*/\1/p' <<< "$LEASE")"
```

Exit code 3 means another project owns the phone: do not issue any device,
Mirroring, install, launch, log, screenshot or UI-automation command. Continue
phone-free work and check `$LOCK status` later. Renew before long device work
with `$LOCK renew-token openxray "$TOKEN" 20`. Pass the exact token to nested
test tools and always run `$LOCK release-token openxray "$TOKEN"`; a stale
cleanup must never release a newer OpenXRay task's lease. Announce that the
phone is free after PASS, failure, cancellation or user timeout.
Repository device tools enforce this just-in-time rule and bound external
commands with timeouts; an outer batch may reuse only its exact exported token.
Generic-device builds and simulators do not require the lease. Never bypass or
delete another owner's live lease; expiration handles abandoned work.

### Mandatory four-level agent routing

Use this routing automatically for all OpenXRay iOS work:

- `gpt-5.6-terra`, `reasoning_effort=high`: default execution worker for Bash,
  Python, parsers, build/test automation, XML/Lua/UI, mechanical C++, gates,
  evidence collection and documentation.
- `gpt-5.6-terra`, `reasoning_effort=xhigh`: difficult multi-module C++,
  renderer/shaders, lifecycle/synchronization, memory ownership/eviction,
  complex XCUITest harnesses and graphics-profile telemetry.
- `gpt-5.6-sol`, `reasoning_effort=medium`: read-only triage, code research,
  hypotheses, risk analysis, test planning and optional pre-review. It cannot
  close a checkpoint, make the final architectural decision or replace the
  final reviewer.
- `gpt-5.6-sol`, `reasoning_effort=xhigh`: mandatory final reviewer, architect
  and decision-maker. Sol xhigh is always the final gate.

For every substantial slice, state in commentary what stays with the main chat,
what Terra high and Terra xhigh receive, whether Sol medium is analyzing, when
Sol xhigh decides/reviews, and the observable acceptance criterion. Do not
delegate work shorter than agent startup cost. Give each editing worker an
exclusive file scope, remind it not to revert concurrent work, keep one owner
per build tree and close agents after collecting their result.

Required sequence:

1. Classify the task and define its observable/acceptance criterion.
2. Use Sol medium for preliminary analysis when useful.
3. Use Sol xhigh for risky or architectural design decisions.
4. Assign the approved implementation to Terra high or Terra xhigh.
5. Let Terra run deterministic tests and collect exact evidence.
6. Optionally use a separate Sol medium pre-review.
7. Have a separate Sol xhigh inspect the complete diff, mechanism, tests,
   evidence boundary, device requirement and acceptance criteria.
8. Terra fixes findings; Sol xhigh re-reviews the corrected state.
9. Only after `APPROVE — brak P0/P1/P2` may current-state documentation close
   the checkpoint.

Every production slice and documentation/status closeout requires Sol xhigh.
Terra never approves its own work, and a worker cannot be its only reviewer.
Final review findings must cite concrete lines and P0/P1/P2 severity or use the
exact approval wording above. A green build alone never proves runtime.

Routing by subsystem:

- lease/timeout/parser: Terra high, optional Sol medium, Sol xhigh review;
- menu/inventory/PDA/XML/Lua: Terra high, Sol xhigh review;
- complex lifecycle/audio harness or profile telemetry: Terra xhigh, optional
  Sol medium evidence analysis, Sol xhigh decision and final review;
- renderer/shader with known cause: Terra xhigh then Sol xhigh;
- unknown renderer fault: Sol medium hypotheses, Sol xhigh direction, Terra
  xhigh implementation, Sol xhigh review;
- lifecycle races/atomics: Sol xhigh design, Terra xhigh implementation, Sol
  xhigh review;
- memory/jetsam/ownership: Sol medium facts, Sol xhigh mechanism, Terra xhigh
  implementation, Sol xhigh review;
- 30-minute soak: Terra high execution, Sol medium data organization, Sol xhigh
  interpretation;
- signing/container/saves and ANGLE/Metal/native-renderer decisions: Sol xhigh
  decides before mutation.

## Current safety constraints

- Do not restore full level prefetch blindly. It previously produced an
  approximately 1.6 GB memory spike and jetsam termination.
- Keep the two resolved startup defects separate. Missing static geometry was
  caused by an invalid camera sector when vertical collision rays missed the
  spawn floor; nearby-sector fallback is its proven fix. Global darkness with a
  correctly lit circle near the player was a separate GLSL feature-macro bug in
  `ssao.ps`; value-based `SSAO_QUALITY`/`SSAO_OPT_DATA` tests are its proven fix.
  Do not replace either fix with a streaming, brightness, ANGLE, or Metal
  workaround.
- Do not revoke Apple certificates as a signing fix.
- Keep Team ID `RMJWWPF379` and bundle identifier
  `io.github.tryk016.openxray.RMJWWPF379` for local device installs. Changing the
  installed identifier creates a different data container.
- Preserve the device's retail game assets and saves. Do not uninstall or erase
  the app container unless explicitly authorized.
- Keep diagnostic frame readback and file-driven input isolated from performance
  conclusions; they currently perturb the runtime.

## Validation contracts

- Shader compile gate: `279/279`.
- Low-settings shader profile: `2/2`.
- SSAO resource-branch profile: `6/6`; value-macro contract must pass.
- Numeric feature-macro contract: `30/30`; five zero fallbacks, eight presence
  debts and one `#undef` debt must match exactly.
- Shader link gate: `137/137`.
- Device target: arm64, iOS 16.4+.
- Device stamps bind source content, Mach-O UUID and complete app-bundle content.
- Baseline Lua runtime: LuaJIT interpreter mode (`LUAJIT_DISABLE_JIT=ON`).
- Primary on-device input: MFi/Bluetooth controller.
- Touch baseline: menu pointer/tap only; a full virtual gamepad is not complete.

Claims about rendering correctness require an on-device frame or numeric probe.
Claims about streaming require correlation with distance, sector/portal state, or
a named resource event. A clean GL error log is not proof that a feature rendered.

## Documentation rules

- `iOS-Port.md` contains current facts only.
- `iOS-Port-Plan.md` contains only the current focus and at most five active
  tasks.
- `iOS-Port-Backlog.md` contains deferred work. Moving an item into the active
  Plan is an explicit prioritization decision.
- `iOS-Port-Resume.md` must remain short enough to read completely before the
  first command; target at most 150 lines.
- `iOS-Port-Journal.md` is append-only. Correct prior entries with a newer entry;
  do not silently rewrite historical evidence.
- `.Codex/session-log.md` is a compact recent operational log, not a duplicate
  canonical specification or full Journal.
- Dated audits are immutable snapshots and must be marked superseded when their
  findings are no longer current.
- Every open item must have a priority, evidence level, and acceptance criterion.

## File editing and git

Use focused patches. Do not reformat unrelated code or overwrite user changes.
Do not use destructive git commands. Do not commit or push unless the user asks
for publication or the active task explicitly includes it.
