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

## Source-of-truth hierarchy

Read these in order before changing the iOS port:

1. `doc/iOS-Port.md` — canonical product and architecture contract.
2. `doc/iOS-Port-Plan.md` — active prioritized roadmap and acceptance criteria.
3. `doc/iOS-Port-Resume.md` — short operational handoff.
4. `doc/iOS-Port-Journal.md` — append-only evidence and historical decisions.
5. `doc/iOS-Metal-Plan-Draft.md` — deferred RFC, not the active renderer plan.

If documents disagree, the earlier item in this list wins. Correct the stale
document in the same slice.

## Mandatory development loop

1. Start with `git status --short`; preserve unrelated user changes.
2. State the hypothesis and the observable that can prove or refute it.
3. Make one smallest self-contained change.
4. Run `./misc/ios/build_check.sh` for engine or shader changes.
5. Use `./misc/ios/install_device.sh --launch` for device validation.
6. Use `misc/ios/input.sh` and `misc/ios/shot.sh` for repeatable camera paths
   and visual evidence.
7. Record proven facts separately from inference.
8. Update `doc/iOS-Port-Journal.md` and `.Codex/session-log.md` when a slice
   changes project state.

Never install a build after a failed gate. Do not pipe `build_check.sh` through a
command that hides its exit status. Only one worker may own the device and the
shared iOS build tree at a time.

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
- Shader link gate: `137/137`.
- Device target: arm64, iOS 16.4+.
- Baseline Lua runtime: LuaJIT interpreter mode (`LUAJIT_DISABLE_JIT=ON`).
- Primary on-device input: MFi/Bluetooth controller.
- Touch baseline: menu pointer/tap only; a full virtual gamepad is not complete.

Claims about rendering correctness require an on-device frame or numeric probe.
Claims about streaming require correlation with distance, sector/portal state, or
a named resource event. A clean GL error log is not proof that a feature rendered.

## Documentation rules

- `iOS-Port.md` contains current facts only.
- `iOS-Port-Plan.md` contains active and future work only.
- `iOS-Port-Resume.md` must remain short enough to read before the first command.
- `iOS-Port-Journal.md` is append-only. Correct prior entries with a newer entry;
  do not silently rewrite historical evidence.
- Dated audits are immutable snapshots and must be marked superseded when their
  findings are no longer current.
- Every open item must have a priority, evidence level, and acceptance criterion.

## File editing and git

Use focused patches. Do not reformat unrelated code or overwrite user changes.
Do not use destructive git commands. Do not commit or push unless the user asks
for publication or the active task explicitly includes it.
